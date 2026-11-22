import time

import pytest
from fastapi.testclient import TestClient

from salesbot.server import create_app

H = {"X-Salesbot": "1"}


@pytest.fixture
def client(tmp_path, transport):
    app = create_app(tmp_path, http_transport=transport, duration_scale=0.02, background=False)
    with TestClient(app) as c:
        yield c


def test_pages_served(client):
    for path in ("/", "/overlay", "/overlay/brb"):
        r = client.get(path)
        assert r.status_code == 200 and "<html" in r.text


def test_mutations_need_header_and_local_host(client):
    assert client.post("/api/control/start").status_code == 403
    assert client.get("/api/state", headers={"host": "evil.example.com"}).status_code == 403
    assert client.post("/api/control/start", headers=H).status_code == 200


def test_secrets_never_returned(client):
    r = client.post("/api/settings", headers=H, json={"values": {"deepseek_api_key": "sk-abcdef123456"}})
    assert r.status_code == 200
    assert "sk-abcdef123456" not in r.text
    assert r.json()["deepseek_api_key"] == {"set": True, "hint": "…3456"}
    assert "sk-abcdef123456" not in client.get("/api/settings").text
    assert client.post("/api/settings", headers=H, json={"values": {"nope": 1}}).status_code == 400
    assert client.post("/api/settings", headers=H, json={"values": {"mode": "yolo"}}).status_code == 400


def test_products_crud_and_csv(client):
    r = client.post("/api/products", headers=H, json={"name": "Serum", "price": "49.90", "stock_status": "low_stock"})
    assert r.status_code == 200 and r.json()["price"] == 49.9
    pid = r.json()["id"]
    assert client.post("/api/products", headers=H, json={"name": ""}).status_code == 400
    assert client.post("/api/products", headers=H, json={"name": "x", "price": -1}).status_code == 400
    assert client.put(f"/api/products/{pid}", headers=H, json={"promo_price": 39.9}).json()["promo_price"] == 39.9
    csv = "name,price,promo_price,stock_status,features\nLipstick,29,19,in_stock,Matte\nMask,15,,out_of_stock,\n"
    assert client.post("/api/products/import", headers=H, json={"csv": csv}).json()["imported"] == 2
    assert len(client.get("/api/products").json()["products"]) == 3
    assert client.delete(f"/api/products/{pid}", headers=H).status_code == 200


def test_deepseek_test_endpoint(client, fake):
    assert client.post("/api/test/deepseek", headers=H).status_code == 400   # no key yet
    client.post("/api/settings", headers=H, json={"values": {"deepseek_api_key": "sk-test"}})
    fake.reply = "DeepSeek connected."
    r = client.post("/api/test/deepseek", headers=H)
    assert r.status_code == 200 and r.json()["reply"] == "DeepSeek connected."


def test_elevenlabs_voice_test_requires_confirmation(client, fake):
    client.post("/api/settings", headers=H, json={"values": {
        "tts_engine": "elevenlabs", "elevenlabs_api_key": "el-key", "elevenlabs_voice_id": "v1"}})
    r = client.post("/api/test/voice", headers=H, json={"text": "hi"})
    assert r.status_code == 402 and not fake.eleven_calls
    r = client.post("/api/test/voice", headers=H, json={"text": "hi", "confirm": True})
    assert r.status_code == 200
    deadline = time.time() + 3
    while time.time() < deadline and not fake.eleven_calls:
        time.sleep(0.02)
    assert fake.eleven_calls
    assert client.get("/api/voices").json()["voices"][0]["voice_id"] == "v1"


def test_websocket_flow_and_emergency_stop(client):
    with client.websocket_connect("/ws?role=overlay&audio=1") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["avatar_driver"] == "overlay"
        client.post("/api/say", headers=H, json={"text": "Hai semua!"})
        while True:
            m = ws.receive_json()
            if m["type"] == "speak":
                break
        assert m["text"] == "Hai semua!" and m["engine"] == "browser"
        ws.send_json({"type": "speech_done", "id": m["id"]})
        while ws.receive_json()["type"] != "speak_end":
            pass
        client.post("/api/control/stop", headers=H)
        types = set()
        while "stop_audio" not in types:
            types.add(ws.receive_json()["type"])
    st = client.get("/api/state").json()["state"]
    assert st["stopped"] and not st["running"]
    assert client.post("/api/control/start", headers=H).status_code == 409
    assert client.post("/api/control/resume", headers=H).status_code == 200


def test_websocket_rejects_foreign_origin(client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"origin": "https://evil.example.com"}) as ws:
            ws.receive_json()


def test_simulator_and_history(client):
    client.post("/api/control/start", headers=H)
    r = client.post("/api/simulator/burst", headers=H, json={"n": 8}).json()
    assert len(r["comments"]) == 8
    sid = client.get("/api/state").json()["state"]["session_id"]
    h = client.get(f"/api/history?session_id={sid}").json()
    assert len(h["comments"]) == 8
    assert client.get("/api/sessions").json()["sessions"][0]["comment_count"] == 8


def test_audio_path_traversal_blocked(client):
    assert client.get("/audio/..%2Fsalesbot.sqlite3").status_code == 404
    assert client.get("/audio/nothing.mp3").status_code == 404
