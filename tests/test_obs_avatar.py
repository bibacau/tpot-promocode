import asyncio
import base64
import hashlib
import json

import pytest

from salesbot.obs import OBSClient, OBSError, auth_string


def test_auth_string_matches_protocol_spec():
    pw, salt, ch = "supersecret", "lM1GncleQOaCu9lT1yeUZhFYnqhsLLP1G5lAGo3ixaI=", "+IxH4CnCiqpX1rM9scsNynZzbOe4KhDeYcTNS3PDaeY="
    secret = base64.b64encode(hashlib.sha256((pw + salt).encode()).digest()).decode()
    expect = base64.b64encode(hashlib.sha256((secret + ch).encode()).digest()).decode()
    assert auth_string(pw, salt, ch) == expect


class FakeOBSServer:
    """Speaks just enough obs-websocket v5 for the client."""

    def __init__(self, password="pw"):
        self.password = password
        self.salt, self.challenge = "salt123", "chal456"
        self.requests = []
        self.scenes = ["Scene"]
        self.inputs = []
        self.out = asyncio.Queue()
        self.closed = False

    async def connect(self, url, **kw):
        await self.out.put({"op": 0, "d": {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1,
                                           "authentication": {"salt": self.salt, "challenge": self.challenge}}})
        return self

    async def recv(self):
        return json.dumps(await self.out.get())

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.closed:
            raise StopAsyncIteration
        return await self.recv()

    async def close(self):
        self.closed = True

    async def send(self, raw):
        msg = json.loads(raw)
        if msg["op"] == 1:
            if msg["d"].get("authentication") != auth_string(self.password, self.salt, self.challenge):
                raise ConnectionError("4009 authentication failed")
            await self.out.put({"op": 2, "d": {"negotiatedRpcVersion": 1}})
            return
        d = msg["d"]
        self.requests.append(d)
        t, data = d["requestType"], d.get("requestData", {})
        resp = {}
        if t == "GetSceneList":
            resp = {"scenes": [{"sceneName": s} for s in self.scenes]}
        elif t == "GetInputList":
            resp = {"inputs": [{"inputName": i} for i in self.inputs]}
        elif t == "CreateScene":
            self.scenes.append(data["sceneName"])
        elif t == "CreateInput":
            self.inputs.append(data["inputName"])
        await self.out.put({"op": 7, "d": {"requestType": t, "requestId": d["requestId"],
                                           "requestStatus": {"result": True, "code": 100}, "responseData": resp}})


def settings(**over):
    base = {"obs_host": "127.0.0.1", "obs_port": 4455, "obs_password": "pw", "obs_enabled": True,
            "obs_main_scene": "AI Salesperson", "obs_brb_scene": "AI Salesperson - BRB"}
    base.update(over)
    return base.get


async def test_connect_setup_and_switch_scene():
    srv = FakeOBSServer()
    c = OBSClient(settings(), connect=srv.connect)
    await c.connect()
    assert c.connected and c.obs_version == "5.5.0"
    actions = await c.setup_scenes("http://127.0.0.1:8765")
    assert "created scene 'AI Salesperson'" in actions
    created = [r for r in srv.requests if r["requestType"] == "CreateInput"]
    assert created[0]["requestData"]["inputSettings"]["url"] == "http://127.0.0.1:8765/overlay"
    assert created[0]["requestData"]["inputSettings"]["reroute_audio"] is True
    assert await c.setup_scenes("http://127.0.0.1:8765") == ["already set up"]
    await c.set_scene("AI Salesperson - BRB")
    assert srv.requests[-1]["requestData"] == {"sceneName": "AI Salesperson - BRB"}
    await c.disconnect()


async def test_wrong_password():
    srv = FakeOBSServer(password="right")
    c = OBSClient(settings(obs_password="wrong"), connect=srv.connect)
    with pytest.raises(OBSError, match="password"):
        await c.connect()
    assert not c.connected


@pytest.mark.parametrize("req", ["StartStream", "ToggleStream", "StartRecord"])
async def test_never_starts_a_stream(req):
    srv = FakeOBSServer()
    c = OBSClient(settings(), connect=srv.connect)
    await c.connect()
    with pytest.raises(OBSError, match="Blocked for safety"):
        await c.request(req)
    assert not any(r["requestType"] == req for r in srv.requests)
    await c.disconnect()


async def test_overlay_avatar_broadcasts_emotions():
    from salesbot.avatar import make_driver
    sent = []

    async def bc(m):
        sent.append(m)
    d = make_driver("overlay", lambda k: None, lambda k, v: None, bc)
    await d.set_emotion("excited")
    await d.idle()
    assert sent == [{"type": "emotion", "emotion": "excited"}, {"type": "emotion", "emotion": "neutral"}]
    assert make_driver("none", None, None, bc).status()["driver"] == "none"
