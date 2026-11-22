import asyncio
import json

import httpx
import pytest

from salesbot.db import Database
from salesbot.engine import SalesEngine
from salesbot.llm import DeepSeekClient
from salesbot.orchestrator import Orchestrator
from salesbot.settings import Settings
from salesbot.tts import TTS


class FakeAPIs:
    """httpx MockTransport standing in for DeepSeek and ElevenLabs (no network, no cost)."""

    def __init__(self):
        self.deepseek_calls = []
        self.eleven_calls = []
        self.reply = {"reply": "Hai! Baju ini RM39 sahaja, tekan bakul kuning ya!", "product_id": 1,
                      "intent": "question", "emotion": "happy", "needs_human": False}
        self.reply_fn = None
        self.fail_deepseek = False
        self.delay = 0.0
        self.mp3_bytes = b"\xff\xfb" + b"\0" * 1600   # 1602 bytes ~= 0.1 s at 128 kbps

    async def handler(self, request: httpx.Request) -> httpx.Response:
        if self.delay:
            await asyncio.sleep(self.delay)
        if request.url.host == "api.deepseek.com":
            body = json.loads(request.content)
            self.deepseek_calls.append(body)
            if self.fail_deepseek:
                return httpx.Response(503, text="overloaded")
            data = self.reply_fn(body) if self.reply_fn else self.reply
            content = data if isinstance(data, str) else json.dumps(data)
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
        if request.url.host == "api.elevenlabs.io":
            self.eleven_calls.append(request)
            if request.method == "GET":
                return httpx.Response(200, json={"voices": [{"voice_id": "v1", "name": "Aiko", "labels": {}}]})
            if request.headers.get("xi-api-key") != "el-key":
                return httpx.Response(401, json={"detail": "bad key"})
            return httpx.Response(200, content=self.mp3_bytes, headers={"content-type": "audio/mpeg"})
        return httpx.Response(404)


@pytest.fixture
def fake():
    return FakeAPIs()


@pytest.fixture
def transport(fake):
    return httpx.MockTransport(fake.handler)


class Bus:
    def __init__(self):
        self.messages = []

    async def __call__(self, msg):
        self.messages.append(msg)

    def of(self, t):
        return [m for m in self.messages if m["type"] == t]


class FakeOBS:
    def __init__(self):
        self.scenes = []

    async def set_scene(self, name):
        self.scenes.append(name)

    def status(self):
        return {"enabled": True, "connected": True}


@pytest.fixture
def make_orch(tmp_path, transport):
    created = []

    def _make(**settings_over):
        db = Database(":memory:")
        s = Settings(db)
        s.update({"deepseek_api_key": "sk-test", "user_cooldown_seconds": 0, "idle_enabled": False,
                  **settings_over})
        db.add_product({"name": "Baju Kurung Moden", "price": 59, "promo_price": 39, "stock_status": "in_stock",
                        "features": "Cotton, saiz S-XL"})
        db.add_product({"name": "Tudung Bawal", "price": 25, "stock_status": "out_of_stock"})
        llm = DeepSeekClient(s.get, transport=transport, pause_after_failure=0)
        tts = TTS(s.get, tmp_path / "audio", transport=transport)
        engine = SalesEngine(llm, s.get, lambda: db.list_products(active_only=True))
        bus = Bus()
        o = Orchestrator(db, s, engine, tts, bus, lambda: 0, obs=FakeOBS(), duration_scale=0.02)
        o.bus = bus
        created.append(o)
        return o

    yield _make


async def wait_until(pred, timeout=5.0, step=0.01):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if pred():
            return True
        await asyncio.sleep(step)
    raise AssertionError("condition not met in time")
