"""Phase 3 scenarios, run end-to-end against fake DeepSeek/ElevenLabs."""
import asyncio

import pytest

from salesbot.orchestrator import ActionError
from tests.conftest import wait_until


@pytest.fixture
async def orch(make_orch):
    o = make_orch()
    o.start_workers()
    yield o
    await o.shutdown()


async def test_approval_workflow(orch):
    await orch.start()
    c = await orch.ingest_comment("aisyah", "berapa harga?", "simulator")
    assert c["status"] == "queued"
    await wait_until(lambda: orch.pending)
    rid = next(iter(orch.pending))
    assert orch.db.get_reply(rid)["status"] == "pending"
    assert not orch.bus.of("speak")                         # nothing spoken without approval
    await orch.approve(rid, "Hai Aisyah! Baju ini RM39 sahaja.")
    await wait_until(lambda: orch.db.get_reply(rid)["status"] == "spoken")
    speak = orch.bus.of("speak")[-1]
    assert speak["text"] == "Hai Aisyah! Baju ini RM39 sahaja." and speak["product"]["name"] == "Baju Kurung Moden"
    assert orch.db.get_comment(c["id"])["status"] == "answered"


async def test_reject(orch):
    await orch.start()
    await orch.ingest_comment("a", "how much?")
    await wait_until(lambda: orch.pending)
    rid = next(iter(orch.pending))
    await orch.reject(rid)
    assert orch.db.get_reply(rid)["status"] == "rejected"
    with pytest.raises(ActionError):
        await orch.approve(rid)


async def test_blocked_and_paused_comments(orch):
    assert (await orch.ingest_comment("a", "hello there"))["reason"] == "paused"
    await orch.start()
    bad = await orch.ingest_comment("a", "bodoh live")
    assert bad["status"] == "blocked" and bad["reason"] == "profanity"
    rude_name = await orch.ingest_comment("pukimak99", "nice")
    assert rude_name["user"] == "viewer"


async def test_concurrent_comments_never_overlap_speech(orch, fake):
    await orch.set_mode("auto")
    await orch.start()
    n = [0]

    def reply(body):
        n[0] += 1
        return {"reply": f"Reply number {n[0]} for you!", "product_id": 1, "intent": "question",
                "emotion": "happy", "needs_human": False}
    fake.reply_fn = reply
    comments = [f"question {i}?" for i in range(25)]
    results = await asyncio.gather(*(orch.ingest_comment(f"user{i}", t, "simulator") for i, t in enumerate(comments)))
    assert all(r["status"] == "queued" for r in results)
    # max_pending=3 means the AI never runs far ahead of the voice
    peak = 0
    for _ in range(300):
        peak = max(peak, orch._pending_count())
        if len(orch.spoken_log) >= 6:
            break
        await asyncio.sleep(0.01)
    assert peak <= 3
    await wait_until(lambda: len(orch.spoken_log) >= 6)
    log = sorted(orch.spoken_log, key=lambda x: x[1])
    for (_, s1, e1), (_, s2, e2) in zip(log, log[1:]):
        assert s2 >= e1, "two lines were spoken at the same time"


async def test_duplicates_and_cooldown_skipped(make_orch):
    o = make_orch(user_cooldown_seconds=20)
    o.start_workers()
    await o.start()
    await o.pause()   # keep items in queue
    o.running = True
    a = await o.ingest_comment("x", "berapa harga?")
    b = await o.ingest_comment("y", "Berapa harga?")
    c = await o.ingest_comment("x", "ada stok?")
    assert a["status"] == "queued" and b["reason"] == "duplicate" and c["reason"] == "cooldown"
    await o.shutdown()


async def test_emergency_stop(orch, fake):
    await orch.set_mode("auto")
    orch.settings.set("obs_enabled", True)
    orch.duration_scale = 1.0                                 # make speech long enough to interrupt
    await orch.start()
    for i in range(5):
        await orch.ingest_comment(f"u{i}", f"harga {i}?")
    await wait_until(lambda: orch.current is not None)
    speaking = orch.current.reply_id
    await orch.emergency_stop()
    await wait_until(lambda: orch.current is None, timeout=2)
    assert orch.db.get_reply(speaking)["status"] == "interrupted"
    assert len(orch.comments) == 0 and len(orch.speech) == 0 and not orch.pending
    assert orch.bus.of("stop_audio")
    await asyncio.sleep(0.05)
    assert orch.obs.scenes == ["AI Salesperson - BRB"]
    # nothing else gets through while stopped
    assert (await orch.ingest_comment("z", "price?"))["reason"] == "emergency_stop"
    with pytest.raises(ActionError):
        await orch.start()
    with pytest.raises(ActionError):
        await orch.say("hello")
    spoken_before = len(orch.bus.of("speak"))
    await asyncio.sleep(0.3)
    assert len(orch.bus.of("speak")) == spoken_before
    await orch.resume()
    await asyncio.sleep(0.05)
    assert orch.obs.scenes[-1] == "AI Salesperson"
    await orch.start()
    assert orch.running


async def test_stop_discards_in_flight_ai_reply(orch, fake):
    await orch.set_mode("auto")
    await orch.start()
    fake.delay = 0.3
    await orch.ingest_comment("a", "price?")
    await wait_until(lambda: orch.generating == 1)
    await orch.emergency_stop()
    await asyncio.sleep(0.5)
    assert not orch.bus.of("speak") and not orch.pending and len(orch.speech) == 0


async def test_human_takeover(orch, fake):
    await orch.set_mode("auto")
    await orch.start()
    await orch.set_takeover(True)
    c = await orch.ingest_comment("a", "price?")
    assert c["reason"] == "takeover"
    rid = await orch.say("Hi everyone, this is the real me speaking!")
    await wait_until(lambda: orch.db.get_reply(rid)["status"] == "spoken")
    assert orch.bus.of("speak")[-1]["kind"] == "takeover"
    assert not fake.deepseek_calls
    await orch.set_takeover(False)
    assert (await orch.ingest_comment("b", "how much?"))["status"] == "queued"


async def test_auto_mode_still_holds_risky_lines(orch, fake):
    await orch.set_mode("auto")
    await orch.start()
    fake.reply = {"reply": "Special price RM5 now!", "product_id": 1, "intent": "question", "emotion": "happy"}
    await orch.ingest_comment("a", "discount?")
    await wait_until(lambda: orch.pending)
    d = next(iter(orch.pending.values()))
    assert "price_mismatch" in d.flags
    assert any(a["level"] == "warning" for a in orch.bus.of("alert"))


async def test_product_intro_and_featured(orch, fake):
    await orch.set_mode("auto")
    fake.reply = {"reply": "Baju Kurung Moden, cotton and comfy, now RM39! Tap the yellow basket!",
                  "product_id": 1, "intent": "other", "emotion": "excited"}
    rid = await orch.introduce_product(1)
    assert orch.settings.get("featured_product_id") == 1
    await wait_until(lambda: orch.db.get_reply(rid)["status"] == "spoken")
    assert orch.bus.of("product")[-1]["product"]["live_promo_price_rm"] == "39"


async def test_overlay_ack_finishes_speech_early(make_orch):
    o = make_orch()
    o._players = lambda: 1
    o.duration_scale = 1.0
    o.start_workers()
    rid = await o.say("A fairly long sentence that would take a few seconds to speak aloud on stream")
    await wait_until(lambda: o.current is not None)
    o.speech_done(rid)
    await wait_until(lambda: o.db.get_reply(rid)["status"] == "spoken", timeout=1)
    await o.shutdown()


async def test_idle_pitch_when_chat_quiet(make_orch, fake):
    o = make_orch(idle_enabled=True, idle_seconds=10, mode="auto", featured_product_id=1)
    o.start_workers()
    await o.start()
    o.last_activity -= 11
    await wait_until(lambda: any(m["kind"] == "idle" for m in o.bus.of("speak")), timeout=3)
    await o.shutdown()


async def test_tts_failure_is_reported_not_fatal(make_orch):
    o = make_orch(tts_engine="elevenlabs")      # no key
    o.start_workers()
    rid = await o.say("hello")
    await wait_until(lambda: o.db.get_reply(rid)["status"] == "failed")
    assert any("Voice failed" in a["message"] for a in o.bus.of("alert"))
    rid2 = await o.say("still alive")
    await wait_until(lambda: o.db.get_reply(rid2)["status"] == "failed")
    await o.shutdown()
