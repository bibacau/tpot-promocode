"""Docker support: env defaults, allowed hosts, host-side screen agent."""
import importlib
import io
import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest
from PIL import Image

from salesbot.db import Database
from salesbot.screen_agent import load_or_create_token, make_handler
from salesbot.screen_reader import OcrLine, remote_grab, remote_monitors
from salesbot.settings import Settings

H = {"X-Salesbot": "1"}


def test_env_defaults_are_used_but_saved_values_win(monkeypatch):
    monkeypatch.setenv("SALESBOT_DEFAULT_OBS_HOST", "host.docker.internal")
    monkeypatch.setenv("SALESBOT_DEFAULT_OBS_PORT", "4460")
    monkeypatch.setenv("SALESBOT_DEFAULT_OBS_ENABLED", "true")
    monkeypatch.setenv("SALESBOT_DEFAULT_VTS_PORT", "not-a-number")
    s = Settings(Database(":memory:"))
    assert s.get("obs_host") == "host.docker.internal"
    assert s.get("obs_port") == 4460 and s.get("obs_enabled") is True
    assert s.get("vts_port") == 8001                      # bad env value falls back to built-in default
    s.set("obs_host", "192.168.1.20")
    assert s.get("obs_host") == "192.168.1.20"
    assert s.public()["obs_host"] == "192.168.1.20"


def test_extra_allowed_hosts(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import salesbot.server as server
    try:
        monkeypatch.setenv("SALESBOT_ALLOWED_HOSTS", "salesbot.local, 192.168.1.50")
        importlib.reload(server)
        with TestClient(server.create_app(tmp_path, background=False)) as c:
            assert c.get("/api/state", headers={"host": "salesbot.local:8765"}).status_code == 200
            assert c.get("/api/state", headers={"host": "192.168.1.50"}).status_code == 200
            assert c.get("/api/state", headers={"host": "evil.example.com"}).status_code == 403
    finally:
        monkeypatch.delenv("SALESBOT_ALLOWED_HOSTS")
        importlib.reload(server)


@pytest.fixture
def agent(tmp_path):
    token = load_or_create_token(tmp_path)
    calls = []

    def grab(monitor, region):
        calls.append((monitor, region))
        if monitor != 1:
            raise ValueError("Monitor 2 not found (1 available)")
        w, h = (region["width"], region["height"]) if region else (1920, 1080)
        return Image.new("RGB", (w, h), (24, 24, 30))

    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
        token, grab, lambda: [{"index": 1, "left": 0, "top": 0, "width": 1920, "height": 1080}]))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", token, calls, tmp_path
    srv.shutdown()


def test_token_created_once(tmp_path):
    t1 = load_or_create_token(tmp_path)
    assert len(t1) >= 32 and load_or_create_token(tmp_path) == t1
    assert (tmp_path / "screen_agent_token").read_text() == t1


def test_agent_security(agent):
    url, token, calls, _ = agent
    assert httpx.get(f"{url}/monitors").status_code == 401
    assert httpx.get(f"{url}/monitors", headers={"X-Agent-Token": "wrong"}).status_code == 401
    r = httpx.get(f"{url}/monitors", headers={"X-Agent-Token": token, "Origin": "https://evil.example"})
    assert r.status_code == 403                                    # browsers can't use it
    assert httpx.get(f"{url}/grab", params={"left": 0, "top": 0, "width": 0, "height": 5},
                     headers={"X-Agent-Token": token}).status_code == 400
    assert not calls


def test_agent_grab_and_monitors(agent):
    url, token, calls, _ = agent
    img = remote_grab(url, token, 1, {"left": 10, "top": 20, "width": 300, "height": 400})
    assert img.size == (300, 400)
    assert calls[-1] == (1, {"left": 10, "top": 20, "width": 300, "height": 400})
    assert remote_monitors(url, token)[0]["width"] == 1920
    with pytest.raises(RuntimeError, match="Monitor 2 not found"):
        remote_grab(url, token, 2, None)
    with pytest.raises(PermissionError, match="token"):
        remote_grab(url, "wrong", 1, None)


def test_agent_unreachable_message():
    with pytest.raises(ConnectionError, match="screen_agent.bat"):
        remote_grab("http://127.0.0.1:1", "t", 1, None)


def test_app_captures_through_agent(agent, transport):
    from fastapi.testclient import TestClient
    from salesbot.server import create_app
    url, token, calls, data_dir = agent

    def ocr(img):
        assert img.size == (400, 700)
        return [OcrLine("aisyah", 50, 10, 20), OcrLine("berapa harga?", 200, 10, 20)]

    with TestClient(create_app(data_dir, http_transport=transport, background=False, screen_ocr=ocr)) as c:
        # agent URL set, token read automatically from <data>/screen_agent_token
        c.post("/api/settings", headers=H, json={"values": {
            "screen_agent_url": url, "screen_region": {"left": 1500, "top": 200, "width": 400, "height": 700}}})
        m = c.post("/api/screen/monitors", headers=H).json()
        assert m["error"] is None and m["monitors"][0]["width"] == 1920
        snap = c.post("/api/screen/snapshot", headers=H, json={"monitor": 1}).json()
        assert snap["width"] == 1920
        t = c.post("/api/screen/test", headers=H).json()
        assert t["comments"] == [{"user": "aisyah", "text": "berapa harga?"}]
        assert calls[-1] == (1, {"left": 1500, "top": 200, "width": 400, "height": 700})
        # without a token file the dashboard gets a hint instead of an error page
        (data_dir / "screen_agent_token").unlink()
        m = c.post("/api/screen/monitors", headers=H)
        assert m.status_code == 200 and m.json()["monitors"] == [] and "screen_agent.bat" in m.json()["error"]
        assert c.post("/api/screen/test", headers=H).status_code == 400


def test_local_monitor_error_is_soft(tmp_path, transport):
    from fastapi.testclient import TestClient
    from salesbot.server import create_app

    def no_display():
        raise RuntimeError("no display")
    with TestClient(create_app(tmp_path, http_transport=transport, background=False, screen_monitors=no_display)) as c:
        r = c.post("/api/screen/monitors", headers=H)
        assert r.status_code == 200 and r.json() == {"ok": True, "monitors": [], "error": "no display"}
