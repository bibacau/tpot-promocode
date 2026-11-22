"""Pluggable avatar drivers.

* ``overlay``      — built-in 2D avatar drawn by the OBS overlay page (default; nothing to install).
* ``vtube_studio`` — Live2D via the VTube Studio public WebSocket API (mouth + emotion hotkeys).
* ``none``         — no avatar (captions and product card only).

To add a new avatar later, subclass ``AvatarDriver`` and register it in ``make_driver``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Callable

import websockets

log = logging.getLogger("salesbot.avatar")


class AvatarDriver:
    name = "none"

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def set_mouth(self, value: float) -> None: ...
    async def set_emotion(self, emotion: str) -> None: ...
    async def idle(self) -> None: ...

    def status(self) -> dict:
        return {"driver": self.name, "connected": True, "last_error": None}


class OverlayAvatar(AvatarDriver):
    """The overlay page animates itself from the audio; emotions are broadcast to it."""
    name = "overlay"

    def __init__(self, broadcast: Callable[[dict], Any]) -> None:
        self._broadcast = broadcast

    async def set_emotion(self, emotion: str) -> None:
        await self._broadcast({"type": "emotion", "emotion": emotion})

    async def idle(self) -> None:
        await self._broadcast({"type": "emotion", "emotion": "neutral"})


class VTubeStudioAvatar(AvatarDriver):
    """VTube Studio plugin API. First connection shows an 'Allow plugin' popup in VTube Studio."""
    name = "vtube_studio"
    PLUGIN = {"pluginName": "TikTok Shop AI Salesperson", "pluginDeveloper": "salesbot"}

    def __init__(self, get_setting: Callable[[str], Any], set_setting: Callable[[str, Any], None]) -> None:
        self._get, self._set = get_setting, set_setting
        self.ws = None
        self.connected = False
        self.last_error: str | None = None
        self._lock = asyncio.Lock()

    def status(self) -> dict:
        return {"driver": self.name, "connected": self.connected, "last_error": self.last_error}

    async def _call(self, mtype: str, data: dict | None = None) -> dict:
        msg = {"apiName": "VTubeStudioPublicAPI", "apiVersion": "1.0", "requestID": uuid.uuid4().hex[:12],
               "messageType": mtype, "data": data or {}}
        await self.ws.send(json.dumps(msg))
        res = json.loads(await asyncio.wait_for(self.ws.recv(), 60 if mtype == "AuthenticationTokenRequest" else 5))
        if res.get("messageType") == "APIError":
            raise RuntimeError(res.get("data", {}).get("message", "VTube Studio API error"))
        return res.get("data", {})

    async def connect(self) -> None:
        async with self._lock:
            if self.connected:
                return
            try:
                self.ws = await websockets.connect(f"ws://{self._get('vts_host')}:{int(self._get('vts_port'))}")
                token = self._get("vts_token")
                if not token:
                    token = (await self._call("AuthenticationTokenRequest", self.PLUGIN))["authenticationToken"]
                    self._set("vts_token", token)
                ok = await self._call("AuthenticationRequest", {**self.PLUGIN, "authenticationToken": token})
                if not ok.get("authenticated"):
                    self._set("vts_token", "")
                    raise RuntimeError("VTube Studio did not accept the plugin token; try again and click Allow")
                self.connected, self.last_error = True, None
            except Exception as e:  # noqa: BLE001
                self.connected, self.last_error = False, str(e) or type(e).__name__
                raise

    async def disconnect(self) -> None:
        if self.ws is not None:
            await self.ws.close()
        self.connected = False

    async def set_mouth(self, value: float) -> None:
        if not self.connected:
            return
        try:
            async with self._lock:
                await self._call("InjectParameterDataRequest", {"parameterValues": [
                    {"id": self._get("vts_mouth_param") or "MouthOpen", "value": max(0.0, min(1.0, value))}]})
        except Exception as e:  # noqa: BLE001
            self.connected, self.last_error = False, str(e)

    async def set_emotion(self, emotion: str) -> None:
        if not self.connected:
            return
        try:
            hotkeys = json.loads(self._get("vts_emotion_hotkeys") or "{}")
        except ValueError:
            hotkeys = {}
        hk = hotkeys.get(emotion)
        if hk:
            try:
                async with self._lock:
                    await self._call("HotkeyTriggerRequest", {"hotkeyID": hk})
            except Exception as e:  # noqa: BLE001
                self.last_error = str(e)

    async def idle(self) -> None:
        await self.set_mouth(0.0)


def make_driver(kind: str, get_setting, set_setting, broadcast) -> AvatarDriver:
    if kind == "vtube_studio":
        return VTubeStudioAvatar(get_setting, set_setting)
    if kind == "overlay":
        return OverlayAvatar(broadcast)
    return AvatarDriver()
