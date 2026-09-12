"""Dead-letter queue và XAUTOCLAIM cho stream worker (bước B9).

Trước đây nhánh `except` chỉ `logger.exception` rồi bỏ qua: không XACK, không
đếm số lần thử. Một sự kiện hỏng vĩnh viễn nằm mãi trong Pending Entries List,
còn `ingestion_events` giữ nguyên trạng thái 'queued' nên `requeue_pending` (chỉ
quét 'pending') không bao giờ lấy lại. Sự kiện biến mất im lặng.

Test dùng Redis giả nên chạy được mà không cần hạ tầng.
"""

import pytest

from app import stream_worker


class FakeRedis:
    def __init__(self, times_delivered: int = 1, autoclaim: list | None = None):
        self._times_delivered = times_delivered
        self._autoclaim = autoclaim or []
        self.acked: list[str] = []
        self.dlq: list[dict] = []

    def xack(self, stream, group, message_id):
        self.acked.append(message_id)
        return 1

    def xadd(self, stream, payload, maxlen=None, approximate=True):
        self.dlq.append({"stream": stream, **payload})
        return "1-1"

    def xpending_range(self, stream, group, min, max, count):
        return [{"message_id": min, "times_delivered": self._times_delivered}]

    def xautoclaim(self, stream, group, consumer, min_idle_time, count):
        return ("0-0", self._autoclaim, [])


@pytest.fixture
def no_db(monkeypatch):
    """Chặn mọi lần chạm Postgres; ghi lại các lần đánh dấu hỏng."""
    calls: list[tuple] = []
    monkeypatch.setattr(stream_worker, "mark_failed", lambda eid, reason: calls.append((eid, reason)))
    return calls


def _boom(*_args, **_kwargs):
    raise ValueError("payload hỏng")


def test_successful_event_is_acked(monkeypatch, no_db):
    monkeypatch.setattr(stream_worker, "process_event", lambda *_: None)
    client = FakeRedis()

    assert stream_worker.handle_message(client, "5-1", {"event_id": "e1"}) is True
    assert client.acked == ["5-1"]
    assert client.dlq == []


def test_first_failure_keeps_message_in_pending_list(monkeypatch, no_db):
    """Chưa hết lượt thì KHÔNG được XACK, nếu không sự kiện mất luôn."""
    monkeypatch.setattr(stream_worker, "process_event", _boom)
    client = FakeRedis(times_delivered=1)

    assert stream_worker.handle_message(client, "5-1", {"event_id": "e1"}) is False
    assert client.acked == []
    assert client.dlq == []
    assert no_db == []


def test_event_goes_to_dlq_after_max_attempts(monkeypatch, no_db):
    monkeypatch.setattr(stream_worker, "process_event", _boom)
    client = FakeRedis(times_delivered=stream_worker.MAX_DELIVERY_ATTEMPTS)

    assert stream_worker.handle_message(client, "5-1", {"event_id": "e1"}) is True
    # Đã XACK: giải phóng consumer group thay vì chặn nó mãi.
    assert client.acked == ["5-1"]
    # Nội dung gốc được giữ lại kèm lý do, để còn điều tra được.
    assert len(client.dlq) == 1
    assert client.dlq[0]["stream"] == stream_worker.DLQ_STREAM
    assert client.dlq[0]["event_id"] == "e1"
    assert "payload hỏng" in client.dlq[0]["dlq_reason"]
    assert client.dlq[0]["dlq_source_id"] == "5-1"
    # Và trạng thái trong Postgres chuyển sang 'failed' kèm lý do.
    assert no_db == [("e1", "ValueError: payload hỏng")]


def test_dlq_write_failure_keeps_message_unacked(monkeypatch, no_db):
    """Không ghi được DLQ thì tuyệt đối không XACK — thà thử lại còn hơn mất."""
    import redis

    monkeypatch.setattr(stream_worker, "process_event", _boom)
    client = FakeRedis(times_delivered=stream_worker.MAX_DELIVERY_ATTEMPTS)

    def failing_xadd(*_args, **_kwargs):
        raise redis.RedisError("Redis đầy")

    monkeypatch.setattr(client, "xadd", failing_xadd)
    stream_worker.handle_message(client, "5-1", {"event_id": "e1"})

    assert client.acked == []
    assert no_db == []


def test_reclaim_picks_up_messages_from_a_dead_consumer(monkeypatch, no_db):
    """XREADGROUP '>' chỉ trả message MỚI, nên message của consumer đã chết
    không worker nào đọc được nếu không XAUTOCLAIM."""
    processed: list[dict] = []
    monkeypatch.setattr(stream_worker, "process_event", lambda _c, fields: processed.append(fields))
    client = FakeRedis(autoclaim=[("5-1", {"event_id": "e1"}), ("5-2", {"event_id": "e2"})])

    assert stream_worker.reclaim_stale(client) == 2
    assert [event["event_id"] for event in processed] == ["e1", "e2"]
    assert client.acked == ["5-1", "5-2"]


def test_reclaim_acks_tombstones(monkeypatch, no_db):
    """Message đã bị xoá khỏi stream nhưng còn trong PEL: XACK để dọn, đừng xử lý."""
    monkeypatch.setattr(stream_worker, "process_event", _boom)
    client = FakeRedis(autoclaim=[("5-9", None)])

    stream_worker.reclaim_stale(client)
    assert client.acked == ["5-9"]
    assert client.dlq == []


def test_reclaim_survives_missing_consumer_group(monkeypatch, no_db):
    import redis

    class Broken(FakeRedis):
        def xautoclaim(self, *_args, **_kwargs):
            raise redis.ResponseError("NOGROUP")

    assert stream_worker.reclaim_stale(Broken()) == 0
