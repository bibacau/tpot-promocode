"""Comment queue and speech queue.

Design ported from teddytravolta's queue.js (MIT): one utterance at a time so
audio never overlaps, priority ordering, and dropping stale items instead of
answering a comment from minutes ago.
"""
from __future__ import annotations

import itertools
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from .moderation import normalise

BUY_HINTS = re.compile(
    r"\?|\b(price|how much|berapa|harga|stock|stok|ada lagi|size|saiz|colou?r|warna|buy|beli|order|"
    r"discount|diskaun|promo|cod|ship|pos|postage|free shipping|voucher|baju|available|still have|"
    r"多少|价钱|价格|有货|怎么买)",
    re.I,
)
GREETING = re.compile(r"^\s*(hi+|hello+|hai+|helo+|hey+|salam|assalamualaikum|你好|selamat \w+)\W*$", re.I)


def comment_priority(text: str) -> int:
    """Higher = answered first. 2 = buying signal, 1 = normal, 0 = bare greeting."""
    if BUY_HINTS.search(text):
        return 2
    if GREETING.match(text):
        return 0
    return 1


@dataclass
class QueuedComment:
    id: int
    user: str
    text: str
    priority: int
    ts: float = field(default_factory=time.monotonic)


class CommentQueue:
    def __init__(self, cooldown: Callable[[], float], max_age: Callable[[], float],
                 max_size: int = 30, dedupe_window: float = 30.0, clock: Callable[[], float] = time.monotonic) -> None:
        self._cooldown, self._max_age = cooldown, max_age
        self.max_size, self.dedupe_window, self._clock = max_size, dedupe_window, clock
        self.items: list[QueuedComment] = []
        self._last_by_user: dict[str, float] = {}
        self._recent_text: dict[str, float] = {}

    def __len__(self) -> int:
        return len(self.items)

    def push(self, c: QueuedComment) -> tuple[bool, str | None, QueuedComment | None]:
        """Returns (accepted, reject_reason, evicted_item)."""
        now = self._clock()
        c.ts = now
        key = normalise(c.text).strip()
        if now - self._recent_text.get(key, -1e9) < self.dedupe_window:
            return False, "duplicate", None
        if now - self._last_by_user.get(c.user.lower(), -1e9) < self._cooldown():
            return False, "cooldown", None
        evicted = None
        if len(self.items) >= self.max_size:
            worst = min(self.items, key=lambda x: (x.priority, -x.ts))
            if worst.priority > c.priority:
                return False, "queue_full", None
            self.items.remove(worst)
            evicted = worst
        self.items.append(c)
        self._recent_text[key] = now
        self._last_by_user[c.user.lower()] = now
        if len(self._recent_text) > 500:
            self._recent_text = {k: t for k, t in self._recent_text.items() if now - t < self.dedupe_window}
        return True, None, evicted

    def pop(self) -> tuple[QueuedComment | None, list[QueuedComment]]:
        """Return (best fresh comment or None, expired comments removed)."""
        now = self._clock()
        expired = [c for c in self.items if now - c.ts > self._max_age()]
        self.items = [c for c in self.items if c not in expired]
        if not self.items:
            return None, expired
        best = max(self.items, key=lambda x: (x.priority, -x.ts))
        self.items.remove(best)
        return best, expired

    def clear(self) -> list[QueuedComment]:
        out, self.items = self.items, []
        return out


SPEECH_PRIORITY = {"takeover": 0, "reply": 1, "intro": 2, "idle": 3, "test": 1}
_seq = itertools.count()


@dataclass
class SpeechItem:
    reply_id: int
    text: str
    kind: str
    emotion: str = "happy"
    product_id: int | None = None
    seq: int = field(default_factory=lambda: next(_seq))

    @property
    def order(self) -> tuple[int, int]:
        return SPEECH_PRIORITY.get(self.kind, 2), self.seq


class SpeechQueue:
    def __init__(self) -> None:
        self.items: list[SpeechItem] = []

    def __len__(self) -> int:
        return len(self.items)

    def push(self, item: SpeechItem) -> None:
        self.items.append(item)
        self.items.sort(key=lambda i: i.order)

    def pop(self) -> SpeechItem | None:
        return self.items.pop(0) if self.items else None

    def remove_where(self, pred: Callable[[SpeechItem], bool]) -> list[SpeechItem]:
        gone = [i for i in self.items if pred(i)]
        self.items = [i for i in self.items if not pred(i)]
        return gone
