"""Minimal OBS WebSocket v5 client (OBS 28+ has the server built in).

Written directly against the public obs-websocket 5.x protocol so the project
avoids a GPL dependency. Behaviour borrowed from teddytravolta's obs.js (MIT):
OBS being closed never breaks the rest of the app; it reconnects in the background.

Safety: this client refuses requests that would start a stream or recording.
The app never goes live on its own — you press "Start Streaming" in OBS yourself.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import uuid
from typing import Any, Callable

import websockets

log = logging.getLogger("salesbot.obs")

FORBIDDEN_REQUESTS = {
    "StartStream", "ToggleStream", "StartRecord", "ToggleRecord", "StartVirtualCam",
    "ToggleVirtualCam", "SetStreamServiceSettings", "StartReplayBuffer",
}


class OBSError(Exception):
    pass


def auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class OBSClient:
    def __init__(self, get_setting: Callable[[str], Any], connect: Callable | None = None) -> None:
        self._get = get_setting
        self._connect = connect or websockets.connect
        self.ws = None
        self.connected = False
        self.last_error: str | None = None
        self.obs_version: str | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict:
        return {"enabled": bool(self._get("obs_enabled")), "connected": self.connected,
                "version": self.obs_version, "last_error": self.last_error}

    async def connect(self) -> None:
        async with self._lock:
            if self.connected:
                return
            url = f"ws://{self._get('obs_host')}:{int(self._get('obs_port'))}"
            try:
                ws = await asyncio.wait_for(self._connect(url, max_size=8 * 2**20), 5)
                hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if hello.get("op") != 0:
                    raise OBSError("Unexpected first message from OBS")
                ident: dict[str, Any] = {"rpcVersion": 1, "eventSubscriptions": 0}
                auth = hello["d"].get("authentication")
                if auth:
                    pw = self._get("obs_password") or ""
                    if not pw:
                        raise OBSError("OBS requires a password (set it in Setup)")
                    ident["authentication"] = auth_string(pw, auth["salt"], auth["challenge"])
                await ws.send(json.dumps({"op": 1, "d": ident}))
                ok = json.loads(await asyncio.wait_for(ws.recv(), 5))
                if ok.get("op") != 2:
                    raise OBSError("OBS rejected the connection (wrong password?)")
                self.obs_version = hello["d"].get("obsWebSocketVersion")
            except Exception as e:  # noqa: BLE001
                self.connected = False
                self.last_error = str(e) or type(e).__name__
                if "4009" in self.last_error:
                    self.last_error = "OBS rejected the password"
                raise OBSError(self.last_error) from e
            self.ws = ws
            self.connected = True
            self.last_error = None
            self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self) -> None:
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if msg.get("op") == 7:
                    fut = self._pending.pop(msg["d"].get("requestId"), None)
                    if fut and not fut.done():
                        fut.set_result(msg["d"])
        except Exception as e:  # noqa: BLE001
            self.last_error = f"connection lost: {e}"
        finally:
            self.connected = False
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(OBSError("OBS disconnected"))
            self._pending.clear()

    async def disconnect(self) -> None:
        if self.ws is not None:
            try:
                await self.ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._reader:
            self._reader.cancel()
        self.connected = False

    async def request(self, rtype: str, data: dict | None = None, timeout: float = 5) -> dict:
        if rtype in FORBIDDEN_REQUESTS:
            raise OBSError(f"Blocked for safety: {rtype}. Start streaming manually in OBS.")
        if not self.connected:
            await self.connect()
        rid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        payload: dict[str, Any] = {"requestType": rtype, "requestId": rid}
        if data:
            payload["requestData"] = data
        await self.ws.send(json.dumps({"op": 6, "d": payload}))
        res = await asyncio.wait_for(fut, timeout)
        st = res.get("requestStatus", {})
        if not st.get("result"):
            raise OBSError(f"{rtype} failed: {st.get('comment') or st.get('code')}")
        return res.get("responseData") or {}

    # ── high-level helpers ──
    async def scene_names(self) -> list[str]:
        data = await self.request("GetSceneList")
        return [s["sceneName"] for s in data.get("scenes", [])]

    async def set_scene(self, name: str) -> None:
        await self.request("SetCurrentProgramScene", {"sceneName": name})

    async def stream_status(self) -> dict:
        return await self.request("GetStreamStatus")

    async def setup_scenes(self, base_url: str) -> list[str]:
        """Create the main and BRB scenes with browser sources (idempotent). Returns actions taken."""
        done: list[str] = []
        scenes = await self.scene_names()
        inputs = {i["inputName"] for i in (await self.request("GetInputList")).get("inputs", [])}
        plan = [
            (self._get("obs_main_scene"), "Salesbot Overlay", f"{base_url}/overlay"),
            (self._get("obs_brb_scene"), "Salesbot BRB", f"{base_url}/overlay/brb"),
        ]
        for scene, source, url in plan:
            if scene not in scenes:
                await self.request("CreateScene", {"sceneName": scene})
                done.append(f"created scene '{scene}'")
            if source not in inputs:
                await self.request("CreateInput", {
                    "sceneName": scene, "inputName": source, "inputKind": "browser_source",
                    "inputSettings": {"url": url, "width": 1080, "height": 1920, "fps": 30,
                                      "reroute_audio": True, "shutdown": False},
                    "sceneItemEnabled": True,
                })
                done.append(f"added browser source '{source}' to '{scene}'")
        return done or ["already set up"]
