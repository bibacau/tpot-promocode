"""FastAPI app: local dashboard, OBS overlay, REST API and WebSockets.

Binds to 127.0.0.1 only. Mutating requests need the X-Salesbot header (blocks
cross-site requests from other websites) and the Host header must be local
(blocks DNS-rebinding attacks).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import __version__
from .avatar import AvatarDriver, make_driver
from .db import Database
from .engine import SalesEngine
from .hub import Client, Hub
from .llm import DeepSeekClient, LLMError
from .obs import OBSClient, OBSError
from .orchestrator import ActionError, Orchestrator
from .settings import DEFAULTS, SECRET_KEYS, Settings
from .simulator import Simulator
from .tts import TTS, TTSError

log = logging.getLogger("salesbot")
STATIC = Path(__file__).parent / "static"
LOCAL_HOSTS = {"127.0.0.1", "localhost", "testserver"}


class SettingsIn(BaseModel):
    values: dict[str, Any]


class TextIn(BaseModel):
    text: str = ""
    confirm: bool = False


class CommentIn(BaseModel):
    user: str = "viewer"
    text: str


class ModeIn(BaseModel):
    mode: str


class OnIn(BaseModel):
    on: bool


class FeaturedIn(BaseModel):
    product_id: int | None = None


class CountIn(BaseModel):
    n: int = 10
    per_minute: int = 12
    include_abuse: bool = True


class ApproveIn(BaseModel):
    text: str | None = None


class SceneIn(BaseModel):
    name: str


class CsvIn(BaseModel):
    csv: str


def create_app(data_dir: str | Path = "data", *, http_transport: httpx.AsyncBaseTransport | None = None,
               obs_connect=None, duration_scale: float = 1.0, background: bool = True) -> FastAPI:
    data_dir = Path(data_dir)
    db = Database(data_dir / "salesbot.sqlite3" if str(data_dir) != ":memory:" else ":memory:")
    settings = Settings(db)
    hub = Hub()
    llm = DeepSeekClient(settings.get, transport=http_transport)
    tts = TTS(settings.get, (data_dir if str(data_dir) != ":memory:" else Path("data")) / "audio",
              transport=http_transport)
    engine = SalesEngine(llm, settings.get, lambda: db.list_products(active_only=True))
    obs = OBSClient(settings.get, connect=obs_connect)
    orch = Orchestrator(db, settings, engine, tts, hub.broadcast, hub.players, obs=obs,
                        duration_scale=duration_scale)

    def build_avatar() -> AvatarDriver:
        return make_driver(settings.get("avatar_driver"), settings.get, settings.set, hub.broadcast)

    orch.avatar = build_avatar()
    sim = Simulator(orch.ingest_comment)

    async def reconnect_loop() -> None:
        while True:
            await asyncio.sleep(5)
            if settings.get("obs_enabled") and not obs.connected:
                with contextlib.suppress(Exception):
                    await obs.connect()
            av = orch.avatar
            if av.name == "vtube_studio" and not av.status()["connected"] and settings.get("vts_token"):
                with contextlib.suppress(Exception):
                    await av.connect()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        orch.start_workers()
        bg = asyncio.create_task(reconnect_loop()) if background else None
        yield
        sim.stop()
        if bg:
            bg.cancel()
        await orch.shutdown()
        await obs.disconnect()
        db.close()

    app = FastAPI(title="AI Salesperson", version=__version__, lifespan=lifespan)
    app.state.db, app.state.settings, app.state.orch, app.state.hub = db, settings, orch, hub
    app.state.sim, app.state.llm, app.state.tts, app.state.obs = sim, llm, tts, obs

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in LOCAL_HOSTS:
            return JSONResponse({"detail": "Local access only"}, status_code=403)
        if request.method in ("POST", "PUT", "DELETE", "PATCH") and request.headers.get("x-salesbot") != "1":
            return JSONResponse({"detail": "Missing X-Salesbot header"}, status_code=403)
        return await call_next(request)

    def ok(**kw):
        return {"ok": True, **kw}

    async def act(coro):
        try:
            return await coro
        except ActionError as e:
            raise HTTPException(409, str(e)) from e

    # ── pages ──
    nocache = {"Cache-Control": "no-store"}

    @app.get("/")
    async def dashboard():
        return FileResponse(STATIC / "dashboard.html", headers=nocache)

    @app.get("/overlay")
    async def overlay():
        return FileResponse(STATIC / "overlay.html", headers=nocache)

    @app.get("/overlay/brb")
    async def overlay_brb():
        return FileResponse(STATIC / "brb.html", headers=nocache)

    @app.get("/audio/{name}")
    async def audio(name: str):
        p = (tts.audio_dir / name).resolve()
        if p.parent != tts.audio_dir.resolve() or not p.is_file() or p.suffix != ".mp3":
            raise HTTPException(404)
        return FileResponse(p, media_type="audio/mpeg")

    @app.get("/favicon.ico")
    async def favicon():
        return Response(status_code=204)

    # ── websocket ──
    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        origin = ws.headers.get("origin")
        host = (ws.headers.get("host") or "").split(":")[0]
        if host not in LOCAL_HOSTS or (origin and origin.split("//")[-1].split(":")[0] not in LOCAL_HOSTS):
            await ws.close(code=1008)
            return
        await ws.accept()
        role = ws.query_params.get("role", "dashboard")
        client = Client(ws, role, plays_audio=(role == "overlay" and ws.query_params.get("audio", "1") == "1"))
        hub.add(client)
        await ws.send_text(json.dumps({"type": "hello", "state": orch.state(),
                                       "product": orch._card(settings.get("featured_product_id")),
                                       "avatar_driver": settings.get("avatar_driver"),
                                       "persona_name": settings.get("persona_name")}))
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                t = msg.get("type")
                if t == "speech_done" and client.plays_audio:
                    orch.speech_done(int(msg.get("id", 0)))
                elif t == "mouth":
                    await orch.avatar.set_mouth(float(msg.get("v", 0)))
        except (WebSocketDisconnect, RuntimeError, ValueError):
            pass
        finally:
            hub.remove(client)

    # ── status & settings ──
    @app.get("/api/state")
    async def get_state():
        return {
            "version": __version__, "state": orch.state(),
            "services": {
                "deepseek": llm.status(), "voice": tts.status(), "obs": obs.status(),
                "avatar": orch.avatar.status(),
                "overlay": {"connected": hub.overlays(), "audio_players": hub.players()},
                "simulator": {"active": sim.active, "per_minute": sim.rate_per_min},
                "tiktok": {"connected": False, "note": "Phase 4 — not connected"},
            },
        }

    @app.get("/api/settings")
    async def get_settings():
        return settings.public()

    @app.post("/api/settings")
    async def post_settings(body: SettingsIn):
        unknown = [k for k in body.values if k not in DEFAULTS and k not in SECRET_KEYS]
        if unknown:
            raise HTTPException(400, f"Unknown settings: {unknown}")
        old_driver = settings.get("avatar_driver")
        try:
            settings.update(body.values)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e)) from e
        if settings.get("avatar_driver") != old_driver:
            with contextlib.suppress(Exception):
                await orch.avatar.disconnect()
            orch.avatar = build_avatar()
            await hub.broadcast({"type": "avatar_driver", "driver": settings.get("avatar_driver")})
        if any(k in body.values for k in ("obs_host", "obs_port", "obs_password")):
            await obs.disconnect()
        if "persona_name" in body.values:
            await hub.broadcast({"type": "persona", "name": settings.get("persona_name")})
        await orch.push_state()
        return settings.public()

    # ── service tests ──
    @app.post("/api/test/deepseek")
    async def test_deepseek():
        try:
            text = await llm.chat([{"role": "user", "content": "Reply with exactly: DeepSeek connected."}],
                                  max_tokens=20, temperature=0, bypass_breaker=True)
        except LLMError as e:
            raise HTTPException(400, str(e)) from e
        return ok(reply=text.strip())

    @app.post("/api/test/voice")
    async def test_voice(body: TextIn):
        if orch.stopped:
            raise HTTPException(409, "Emergency stop is active. Press Resume first.")
        if tts.engine == "elevenlabs" and not body.confirm:
            raise HTTPException(402, "This uses ElevenLabs credits. Confirm to continue.")
        text = body.text.strip() or f"Hai semua! I'm {settings.get('persona_name')}, welcome to our live!"
        rid = await orch.say(text)
        return ok(reply_id=rid)

    @app.get("/api/voices")
    async def voices():
        try:
            return {"voices": await tts.list_voices()}
        except (TTSError, httpx.HTTPError) as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/obs/connect")
    async def obs_connect_ep():
        try:
            await obs.disconnect()
            await obs.connect()
            return ok(scenes=await obs.scene_names(), status=obs.status())
        except (OBSError, asyncio.TimeoutError) as e:
            raise HTTPException(400, str(e) or "OBS did not answer") from e

    @app.post("/api/obs/setup")
    async def obs_setup(request: Request):
        base = f"http://127.0.0.1:{request.url.port or 8765}"
        try:
            return ok(actions=await obs.setup_scenes(base), scenes=await obs.scene_names())
        except (OBSError, asyncio.TimeoutError) as e:
            raise HTTPException(400, str(e) or "OBS did not answer") from e

    @app.post("/api/obs/scene")
    async def obs_scene(body: SceneIn):
        try:
            await obs.set_scene(body.name)
        except (OBSError, asyncio.TimeoutError) as e:
            raise HTTPException(400, str(e) or "OBS did not answer") from e
        return ok()

    @app.get("/api/obs/status")
    async def obs_status_ep():
        try:
            st = await obs.stream_status()
            return ok(live=st.get("outputActive"), scenes=await obs.scene_names())
        except (OBSError, asyncio.TimeoutError) as e:
            raise HTTPException(400, str(e) or "OBS did not answer") from e

    @app.post("/api/avatar/connect")
    async def avatar_connect():
        try:
            await orch.avatar.connect()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e) or "Could not connect to the avatar") from e
        return ok(status=orch.avatar.status())

    @app.post("/api/avatar/test")
    async def avatar_test():
        for emo in ("happy", "excited", "thinking", "sorry", "neutral"):
            await orch.avatar.set_emotion(emo)
            for v in (0.8, 0.2, 0.9, 0.0):
                await orch.avatar.set_mouth(v)
                await asyncio.sleep(0.08)
            await asyncio.sleep(0.4)
        await orch.avatar.idle()
        return ok(status=orch.avatar.status())

    # ── products ──
    @app.get("/api/products")
    async def products():
        return {"products": db.list_products()}

    @app.post("/api/products")
    async def add_product(body: dict):
        try:
            return db.add_product(body)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e)) from e

    @app.put("/api/products/{pid}")
    async def update_product(pid: int, body: dict):
        try:
            p = db.update_product(pid, body)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e)) from e
        if not p:
            raise HTTPException(404, "Product not found")
        if pid == settings.get("featured_product_id"):
            await hub.broadcast({"type": "product", "product": orch._card(pid)})
        return p

    @app.delete("/api/products/{pid}")
    async def delete_product(pid: int):
        if not db.delete_product(pid):
            raise HTTPException(404, "Product not found")
        if pid == settings.get("featured_product_id"):
            await orch.set_featured(None)
        return ok()

    @app.post("/api/products/import")
    async def import_products(body: CsvIn):
        try:
            return ok(imported=db.import_products_csv(body.csv))
        except (ValueError, KeyError) as e:
            raise HTTPException(400, str(e)) from e

    # ── live controls ──
    @app.post("/api/control/{action}")
    async def control(action: str):
        actions = {"start": orch.start, "pause": orch.pause, "end": orch.end_session,
                   "stop": orch.emergency_stop, "resume": orch.resume}
        if action not in actions:
            raise HTTPException(404)
        if action == "stop":
            sim.stop()
        await act(actions[action]())
        return ok(state=orch.state())

    @app.post("/api/mode")
    async def set_mode(body: ModeIn):
        if body.mode not in ("approve", "auto"):
            raise HTTPException(400, "mode must be approve or auto")
        await orch.set_mode(body.mode)
        return ok(state=orch.state())

    @app.post("/api/takeover")
    async def takeover(body: OnIn):
        await orch.set_takeover(body.on)
        return ok(state=orch.state())

    @app.post("/api/say")
    async def say(body: TextIn):
        return ok(reply_id=await act(orch.say(body.text)))

    @app.post("/api/featured")
    async def featured(body: FeaturedIn):
        await act(orch.set_featured(body.product_id))
        return ok(state=orch.state())

    @app.post("/api/introduce/{pid}")
    async def introduce(pid: int):
        return ok(reply_id=await act(orch.introduce_product(pid)))

    @app.post("/api/approve/{rid}")
    async def approve(rid: int, body: ApproveIn | None = None):
        await act(orch.approve(rid, body.text if body else None))
        return ok()

    @app.post("/api/reject/{rid}")
    async def reject(rid: int):
        await act(orch.reject(rid))
        return ok()

    @app.post("/api/comments")
    async def comment(body: CommentIn):
        return await orch.ingest_comment(body.user, body.text, "manual")

    # ── simulator ──
    @app.post("/api/simulator/burst")
    async def sim_burst(body: CountIn):
        return ok(comments=await sim.burst(max(1, min(body.n, 100)), body.include_abuse))

    @app.post("/api/simulator/start")
    async def sim_start(body: CountIn):
        sim.start(body.per_minute)
        return ok(active=True)

    @app.post("/api/simulator/stop")
    async def sim_stop():
        sim.stop()
        return ok(active=False)

    # ── history ──
    @app.get("/api/sessions")
    async def sessions():
        return {"sessions": db.list_sessions()}

    @app.get("/api/history")
    async def history(session_id: int | None = None):
        return db.history(session_id)

    return app


def main() -> None:
    import threading
    import webbrowser

    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    port = int(os.environ.get("SALESBOT_PORT", "8765"))
    data_dir = Path(os.environ.get("SALESBOT_DATA", Path.cwd() / "data"))
    url = f"http://127.0.0.1:{port}/"
    print(f"\n  AI Salesperson dashboard: {url}\n  OBS overlay URL:          {url}overlay\n")
    if os.environ.get("SALESBOT_NO_BROWSER") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(data_dir), host="127.0.0.1", port=port, log_level="warning")
