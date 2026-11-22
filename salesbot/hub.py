"""WebSocket fan-out to the dashboard and OBS overlay pages."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from fastapi import WebSocket


@dataclass(eq=False)
class Client:
    ws: WebSocket
    role: str            # dashboard | overlay
    plays_audio: bool


class Hub:
    def __init__(self) -> None:
        self.clients: set[Client] = set()

    def add(self, c: Client) -> None:
        self.clients.add(c)

    def remove(self, c: Client) -> None:
        self.clients.discard(c)

    def players(self) -> int:
        return sum(1 for c in self.clients if c.plays_audio)

    def overlays(self) -> int:
        return sum(1 for c in self.clients if c.role == "overlay")

    async def broadcast(self, msg: dict) -> None:
        data = json.dumps(msg, default=str)
        dead = []
        for c in list(self.clients):
            try:
                await asyncio.wait_for(c.ws.send_text(data), 2)
            except Exception:  # noqa: BLE001
                dead.append(c)
        for c in dead:
            self.clients.discard(c)
