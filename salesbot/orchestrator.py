"""The live show controller: comments → AI → (human approval) → speech → avatar/overlay.

States
  running   AI answers comments / makes idle pitches. Paused = AI stops generating.
  mode      'approve' (every AI line waits for a human) or 'auto'.
  takeover  Human is in control: AI generation stops, only human-typed lines are spoken.
  stopped   EMERGENCY STOP: silence immediately, all queues cleared, BRB scene. Needs Resume.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

from . import moderation
from .avatar import AvatarDriver
from .db import Database
from .engine import Draft, SalesEngine, product_facts
from .llm import LLMError
from .obs import OBSClient
from .queues import CommentQueue, QueuedComment, SpeechItem, SpeechQueue, comment_priority
from .settings import Settings
from .tts import TTS, TTSError

log = logging.getLogger("salesbot")


class ActionError(Exception):
    pass


class Orchestrator:
    def __init__(self, db: Database, settings: Settings, engine: SalesEngine, tts: TTS,
                 broadcast: Callable[[dict], Awaitable[None]], players_connected: Callable[[], int],
                 obs: OBSClient | None = None, avatar: AvatarDriver | None = None,
                 duration_scale: float = 1.0) -> None:
        self.db, self.settings, self.engine, self.tts = db, settings, engine, tts
        self._broadcast, self._players = broadcast, players_connected
        self.obs, self.avatar = obs, avatar or AvatarDriver()
        self.duration_scale = duration_scale
        g = settings.get
        self.comments = CommentQueue(lambda: float(g("user_cooldown_seconds")),
                                     lambda: float(g("comment_max_age_seconds")))
        self.speech = SpeechQueue()
        self.running = False
        self.takeover = False
        self.stopped = False
        self.session_id: int | None = None
        self.epoch = 0                       # bumped on stop/takeover: in-flight AI results are discarded
        self.generating = 0
        self.pending: dict[int, Draft] = {}  # reply_id -> draft awaiting approval
        self.current: SpeechItem | None = None
        self.last_activity = time.monotonic()
        self.spoken_log: list[tuple[int, float, float]] = []   # (reply_id, start, end) for overlap tests
        self._done_events: dict[int, asyncio.Event] = {}
        self._interrupt = asyncio.Event()
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task] = []

    # ── lifecycle ──
    def start_workers(self) -> None:
        if not self._tasks:
            self._tasks = [asyncio.create_task(self._ai_worker(), name="ai"),
                           asyncio.create_task(self._speech_worker(), name="speech"),
                           asyncio.create_task(self._idle_worker(), name="idle")]

    async def shutdown(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self.session_id:
            self.db.end_session(self.session_id)

    def _poke(self) -> None:
        self._wake.set()

    async def _sleep_or_wake(self, timeout: float = 0.5) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    async def emit(self, type_: str, **data: Any) -> None:
        try:
            await self._broadcast({"type": type_, **data})
        except Exception:  # noqa: BLE001
            log.exception("broadcast failed")

    def state(self) -> dict:
        return {
            "running": self.running, "takeover": self.takeover, "stopped": self.stopped,
            "mode": self.settings.get("mode"), "stream_mode": self.settings.get("stream_mode"),
            "session_id": self.session_id,
            "featured_product_id": self.settings.get("featured_product_id"),
            "comment_queue": len(self.comments), "speech_queue": len(self.speech),
            "generating": self.generating,
            "speaking": ({"reply_id": self.current.reply_id, "text": self.current.text, "kind": self.current.kind}
                         if self.current else None),
            "pending": [self._pending_view(rid) for rid in self.pending],
        }

    def _pending_view(self, rid: int) -> dict:
        d = self.pending[rid]
        r = self.db.get_reply(rid) or {}
        c = self.db.get_comment(r["comment_id"]) if r.get("comment_id") else None
        return {"reply_id": rid, "text": d.text, "kind": d.kind, "product_id": d.product_id,
                "comment": ({"user": c["user"], "text": c["text"]} if c else None), **d.meta()}

    async def push_state(self) -> None:
        await self.emit("state", state=self.state())

    # ── controls ──
    async def start(self) -> None:
        if self.stopped:
            raise ActionError("Emergency stop is active. Press Resume first.")
        if self.session_id is None:
            self.session_id = self.db.start_session()
        self.running = True
        self.last_activity = time.monotonic()
        self._poke()
        await self.push_state()

    async def pause(self) -> None:
        self.running = False
        await self.push_state()

    async def end_session(self) -> None:
        self.running = False
        if self.session_id:
            self.db.end_session(self.session_id)
        self.session_id = None
        await self.push_state()

    async def set_mode(self, mode: str) -> None:
        self.settings.set("mode", mode)
        await self.push_state()

    @property
    def casual(self) -> bool:
        return self.settings.get("stream_mode") == "casual"

    async def set_stream_mode(self, mode: str) -> None:
        """Switch between 'selling' and 'casual' (works mid-stream; products and settings are kept)."""
        if mode not in ("selling", "casual"):
            raise ActionError("stream_mode must be 'selling' or 'casual'")
        if mode == self.settings.get("stream_mode"):
            await self.push_state()
            return
        self.settings.set("stream_mode", mode)
        # Lines written for the other mode are stale: discard in-flight AI work and queued AI speech.
        # The line currently being spoken finishes; human (takeover) lines are kept.
        self.epoch += 1
        for item in self.speech.remove_where(lambda i: i.kind != "takeover"):
            self.db.set_reply_status(item.reply_id, "dropped")
        for rid in [r for r, d in self.pending.items() if d.kind in ("intro", "idle")]:
            self.pending.pop(rid)
            self.db.set_reply_status(rid, "dropped")
        self.last_activity = time.monotonic()
        await self.emit("stream_mode", mode=mode,
                        product=None if mode == "casual" else self._card(self.settings.get("featured_product_id")))
        self._poke()
        await self.push_state()

    async def set_featured(self, pid: int | None) -> None:
        if pid is not None and not self.db.get_product(pid):
            raise ActionError("Product not found")
        self.settings.set("featured_product_id", pid)
        await self.emit("product", product=self._card(pid))
        await self.push_state()

    def _card(self, pid: int | None) -> dict | None:
        if self.casual:
            return None
        p = self.db.get_product(pid) if pid else None
        return product_facts(p) if p else None

    async def emergency_stop(self) -> None:
        self.stopped = True
        self.running = False
        self.epoch += 1
        for c in self.comments.clear():
            self.db.set_comment_status(c.id, "dropped", "emergency_stop")
        for s in self.speech.remove_where(lambda _: True):
            self.db.set_reply_status(s.reply_id, "dropped")
        for rid in list(self.pending):
            self.db.set_reply_status(rid, "dropped")
        self.pending.clear()
        self._interrupt.set()
        await self.emit("stop_audio")
        await self._safe(self.avatar.idle())
        if self.obs and self.settings.get("obs_enabled") and self.settings.get("obs_switch_on_stop"):
            asyncio.create_task(self._safe(self.obs.set_scene(self.settings.get("obs_brb_scene"))))
        await self.emit("alert", level="danger", message="EMERGENCY STOP — AI silenced, queues cleared.")
        await self.push_state()

    async def resume(self) -> None:
        self.stopped = False
        self._interrupt.clear()
        if self.obs and self.settings.get("obs_enabled") and self.settings.get("obs_switch_on_stop"):
            asyncio.create_task(self._safe(self.obs.set_scene(self.settings.get("obs_main_scene"))))
        await self.push_state()

    async def set_takeover(self, on: bool) -> None:
        self.takeover = on
        if on:
            self.epoch += 1
            for s in self.speech.remove_where(lambda i: i.kind != "takeover"):
                self.db.set_reply_status(s.reply_id, "dropped")
            if self.current and self.current.kind != "takeover":
                self._interrupt.set()
                await self.emit("stop_audio")
        self._poke()
        await self.push_state()

    async def say(self, text: str) -> int:
        """Human types a line; the avatar speaks it next (ahead of AI lines)."""
        if self.stopped:
            raise ActionError("Emergency stop is active. Press Resume first.")
        text = moderation.clean_comment(text, 500)
        if not text:
            raise ActionError("Nothing to say")
        rid = self.db.add_reply(self.session_id, None, "takeover", text, None, "queued")
        self.speech.push(SpeechItem(rid, text, "takeover", emotion="happy"))
        self._poke()
        await self.push_state()
        return rid

    async def approve(self, rid: int, text: str | None = None) -> None:
        draft = self.pending.pop(rid, None)
        if draft is None:
            raise ActionError("This line is no longer waiting for approval")
        if self.stopped:
            self.db.set_reply_status(rid, "dropped")
            raise ActionError("Emergency stop is active")
        if text is not None and text.strip():
            draft.text = moderation.clean_comment(text, 500)
        self.db.set_reply_status(rid, "approved", draft.text)
        self.speech.push(SpeechItem(rid, draft.text, draft.kind, draft.emotion, draft.product_id))
        self._poke()
        await self.push_state()

    async def reject(self, rid: int) -> None:
        if self.pending.pop(rid, None) is None:
            raise ActionError("This line is no longer waiting for approval")
        self.db.set_reply_status(rid, "rejected")
        self._poke()
        await self.push_state()

    async def introduce_product(self, pid: int) -> int:
        if self.stopped:
            raise ActionError("Emergency stop is active. Press Resume first.")
        if self.casual:
            raise ActionError("Casual chat stream: product introductions are off. Switch Stream to Selling first.")
        p = self.db.get_product(pid)
        if not p:
            raise ActionError("Product not found")
        await self.set_featured(pid)
        epoch = self.epoch
        self.generating += 1
        try:
            draft = await self.engine.introduce_product(p, "intro")
        finally:
            self.generating -= 1
        if epoch != self.epoch:
            raise ActionError("Cancelled by stop/takeover")
        return await self._route(draft, None)

    # ── comments ──
    async def ingest_comment(self, user: str, text: str, source: str = "manual") -> dict:
        text = moderation.clean_comment(text)
        user = moderation.safe_username(user, "viewer")
        reason = moderation.check(text)
        status = "blocked" if reason else "received"
        cid = self.db.add_comment(self.session_id, user, text, source, status, reason)
        evicted = None
        if not reason:
            if self.stopped or not self.running:
                status, reason = "not_queued", ("emergency_stop" if self.stopped else "paused")
            elif self.takeover:
                status, reason = "not_queued", "takeover"
            else:
                ok, why, evicted = self.comments.push(QueuedComment(cid, user, text, comment_priority(text)))
                status, reason = ("queued", None) if ok else ("skipped", why)
            self.db.set_comment_status(cid, status, reason)
        if evicted:
            self.db.set_comment_status(evicted.id, "skipped", "queue_full")
            await self.emit("comment_status", id=evicted.id, status="skipped", reason="queue_full")
        self.last_activity = time.monotonic()
        out = {"id": cid, "user": user, "text": text, "source": source, "status": status, "reason": reason}
        await self.emit("comment", comment=out)
        self._poke()
        return out

    def _pending_count(self) -> int:
        return len(self.speech) + len(self.pending) + self.generating

    async def _route(self, draft: Draft, comment_id: int | None) -> int:
        """Send a draft to the approval queue or straight to the speech queue."""
        needs_review = (self.settings.get("mode") != "auto") or draft.needs_human or bool(draft.flags)
        status = "pending" if needs_review else "queued"
        rid = self.db.add_reply(self.session_id, comment_id, draft.kind, draft.text, draft.product_id,
                                status, draft.meta())
        if needs_review:
            self.pending[rid] = draft
            if draft.needs_human or draft.flags:
                await self.emit("alert", level="warning",
                                message=f"Needs your review: {', '.join(draft.flags) or draft.intent}")
        else:
            self.speech.push(SpeechItem(rid, draft.text, draft.kind, draft.emotion, draft.product_id))
        self._poke()
        await self.push_state()
        return rid

    # ── workers ──
    def _ai_may_work(self) -> bool:
        return self.running and not self.stopped and not self.takeover

    async def _ai_worker(self) -> None:
        while True:
            await self._sleep_or_wake()
            while self._ai_may_work() and len(self.comments) and \
                    self._pending_count() < int(self.settings.get("max_pending")):
                c, expired = self.comments.pop()
                for e in expired:
                    self.db.set_comment_status(e.id, "expired", "too_old")
                    await self.emit("comment_status", id=e.id, status="expired", reason="too_old")
                if c is None:
                    break
                await self._answer(c)

    async def _answer(self, c: QueuedComment) -> None:
        epoch = self.epoch
        self.db.set_comment_status(c.id, "answering")
        await self.emit("comment_status", id=c.id, status="answering")
        self.generating += 1
        try:
            draft = await self.engine.reply_to_comment(c.user, c.text)
        except LLMError as e:
            self.db.set_comment_status(c.id, "ai_failed", str(e)[:200])
            await self.emit("comment_status", id=c.id, status="ai_failed", reason=str(e)[:200])
            await self.emit("alert", level="warning", message=f"AI unavailable: {e}")
            return
        finally:
            self.generating -= 1
        if epoch != self.epoch:
            self.db.set_comment_status(c.id, "dropped", "cancelled")
            return
        self.db.set_comment_status(c.id, "answered")
        await self.emit("comment_status", id=c.id, status="answered")
        await self._route(draft, c.id)

    async def _speech_worker(self) -> None:
        while True:
            await self._sleep_or_wake()
            while not self.stopped and len(self.speech):
                item = self.speech.pop()
                if item:
                    await self._speak(item)

    async def _speak(self, item: SpeechItem) -> None:
        epoch = self.epoch
        self._interrupt.clear()
        self.db.set_reply_status(item.reply_id, "speaking")
        try:
            audio = await self.tts.synthesize(item.text)
        except TTSError as e:
            self.db.set_reply_status(item.reply_id, "failed")
            await self.emit("alert", level="warning", message=f"Voice failed: {e}")
            return
        if epoch != self.epoch and item.kind != "takeover":
            self.db.set_reply_status(item.reply_id, "dropped")
            return
        if self.stopped:
            self.db.set_reply_status(item.reply_id, "dropped")
            return
        self.current = item
        done = asyncio.Event()
        self._done_events[item.reply_id] = done
        start = time.monotonic()
        await self._safe(self.avatar.set_emotion(item.emotion))
        await self.emit("speak", id=item.reply_id, text=item.text, kind=item.kind, engine=audio.engine,
                        audio_url=audio.audio_url, duration=audio.duration, emotion=item.emotion,
                        product=self._card(item.product_id or self.settings.get("featured_product_id")))
        await self.push_state()
        scaled = audio.duration * self.duration_scale
        timeout = scaled * 1.5 + 3 * self.duration_scale if self._players() else scaled
        waiters = [asyncio.create_task(done.wait()), asyncio.create_task(self._interrupt.wait())]
        _, rest = await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        for t in rest:
            t.cancel()
        interrupted = self._interrupt.is_set()
        self._done_events.pop(item.reply_id, None)
        self.spoken_log.append((item.reply_id, start, time.monotonic()))
        self.db.set_reply_status(item.reply_id, "interrupted" if interrupted else "spoken")
        self.current = None
        self.last_activity = time.monotonic()
        await self._safe(self.avatar.idle())
        await self.emit("speak_end", id=item.reply_id, interrupted=interrupted)
        self._poke()
        await self.push_state()

    def speech_done(self, reply_id: int) -> None:
        """Called when the overlay reports the audio finished playing."""
        ev = self._done_events.get(reply_id)
        if ev:
            ev.set()

    async def _idle_worker(self) -> None:
        while True:
            await asyncio.sleep(1)
            g = self.settings.get
            casual = self.casual
            pid = g("featured_product_id")
            if not (self._ai_may_work() and g("idle_enabled") and (casual or pid)) or self.current \
                    or self._pending_count() or len(self.comments):
                continue
            if time.monotonic() - self.last_activity < float(g("idle_seconds")):
                continue
            p = None if casual else self.db.get_product(pid)
            if not casual and not p:
                continue
            self.last_activity = time.monotonic()
            epoch = self.epoch
            self.generating += 1
            try:
                draft = await (self.engine.chat_starter() if casual else self.engine.introduce_product(p, "idle"))
            finally:
                self.generating -= 1
            if epoch == self.epoch and self._ai_may_work():
                await self._route(draft, None)

    @staticmethod
    async def _safe(coro) -> None:
        try:
            await asyncio.wait_for(coro, 5)
        except Exception as e:  # noqa: BLE001
            log.warning("ignored error: %s", e)
