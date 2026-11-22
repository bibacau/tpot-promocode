from salesbot.queues import CommentQueue, QueuedComment, SpeechItem, SpeechQueue, comment_priority


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(cooldown=0, max_age=90, size=30):
    clock = Clock()
    return CommentQueue(lambda: cooldown, lambda: max_age, max_size=size, clock=clock), clock


def test_priority_classification():
    assert comment_priority("berapa harga?") == 2
    assert comment_priority("how much") == 2
    assert comment_priority("hi") == 0
    assert comment_priority("salam") == 0
    assert comment_priority("cantiknya") == 1


def test_buying_questions_answered_first():
    q, _ = make()
    q.push(QueuedComment(1, "a", "hello", 0))
    q.push(QueuedComment(2, "b", "cantik", 1))
    q.push(QueuedComment(3, "c", "berapa harga?", 2))
    assert [q.pop()[0].id for _ in range(3)] == [3, 2, 1]


def test_dedupe_and_cooldown():
    q, clock = make(cooldown=20)
    assert q.push(QueuedComment(1, "a", "Berapa harga?", 2))[0]
    assert q.push(QueuedComment(2, "b", "berapa harga?", 2))[1] == "duplicate"
    assert q.push(QueuedComment(3, "a", "ada stok?", 2))[1] == "cooldown"
    clock.t += 31
    assert q.push(QueuedComment(4, "b", "berapa harga?", 2))[0]


def test_stale_comments_expire():
    q, clock = make(max_age=90)
    q.push(QueuedComment(1, "a", "old question?", 2))
    clock.t += 91
    q.push(QueuedComment(2, "b", "new question?", 2))
    best, expired = q.pop()
    assert best.id == 2 and [e.id for e in expired] == [1]


def test_full_queue_evicts_lowest_priority():
    q, clock = make(size=2)
    q.push(QueuedComment(1, "a", "hi", 0))
    q.push(QueuedComment(2, "b", "nice", 1))
    ok, why, evicted = q.push(QueuedComment(3, "c", "price?", 2))
    assert ok and evicted.id == 1
    ok, why, _ = q.push(QueuedComment(4, "d", "hello", 0))
    assert not ok and why == "queue_full"


def test_speech_queue_order():
    s = SpeechQueue()
    s.push(SpeechItem(1, "idle", "idle"))
    s.push(SpeechItem(2, "reply", "reply"))
    s.push(SpeechItem(3, "human", "takeover"))
    s.push(SpeechItem(4, "reply2", "reply"))
    assert [s.pop().reply_id for _ in range(4)] == [3, 2, 4, 1]
