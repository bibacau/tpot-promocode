"""Simulated TikTok LIVE viewers (idea from teddytravolta's simulator mode, MIT).

Lets the whole pipeline be tested without connecting TikTok.
"""
from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable

NAMES = ["aisyah_92", "kelvin.lim", "nurul_shop", "danish88", "meiling", "farah.z", "arif_my",
         "sitikhadijah", "jasonteh", "priya.k", "hafiz_kl", "wei_jie", "amirah", "raj_88"]

COMMENTS = [
    "Hi kak!", "hello Aiko", "salam semua", "你好",
    "berapa harga?", "how much is this?", "ada stok lagi?", "still have stock?",
    "boleh COD tak?", "size M ada?", "warna lain ada?", "free shipping ka?",
    "nak beli satu", "how to buy?", "the first one quality ok?", "这个多少钱?",
    "ok cantik", "wahh nice", "suitable for kids?", "sis, delivery berapa hari?",
    "my order haven't arrive yet", "can give discount?", "is this original?",
]

ABUSIVE = ["bodoh punya live", "follow me for free followers", "whatsapp 0123456789 for cheap",
           "visit www.scam-shop.xyz"]


def random_comment(rng: random.Random, include_abuse: bool = False) -> tuple[str, str]:
    pool = COMMENTS + (ABUSIVE if include_abuse else [])
    return rng.choice(NAMES), rng.choice(pool)


class Simulator:
    def __init__(self, ingest: Callable[[str, str, str], Awaitable[dict]], seed: int | None = None) -> None:
        self._ingest = ingest
        self.rng = random.Random(seed)
        self.task: asyncio.Task | None = None
        self.rate_per_min = 0

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()

    async def burst(self, n: int, include_abuse: bool = True, gap: float = 0.0) -> list[dict]:
        out = []
        for _ in range(n):
            user, text = random_comment(self.rng, include_abuse)
            out.append(await self._ingest(user, text, "simulator"))
            if gap:
                await asyncio.sleep(gap)
        return out

    def start(self, per_minute: int = 12) -> None:
        self.stop()
        self.rate_per_min = max(1, min(per_minute, 120))
        self.task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self.task:
            self.task.cancel()
        self.task = None
        self.rate_per_min = 0

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.rng.expovariate(self.rate_per_min / 60))
            user, text = random_comment(self.rng, include_abuse=self.rng.random() < 0.1)
            await self._ingest(user, text, "simulator")
