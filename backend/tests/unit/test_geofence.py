"""Kiểm thử Proximity Notification Service (lộ trình B13b).

Phần chạm PostGIS được kiểm ở tầng integration; ở đây tập trung vào những chỗ
sai được mà không báo lỗi:

- Khung SSE sai định dạng → trình duyệt im lặng không nhận sự kiện nào.
- Không lọc theo phiên → người này nhận thông báo vị trí của người kia.
- Đọc stream từ đầu → tab vừa mở dội lại toàn bộ thông báo cũ.
- Chống báo trùng nằm ở Python thay vì trong SQL.
"""

from __future__ import annotations

import json

import pytest

from app import geofence


class FakeRedis:
    """Chỉ đủ lệnh cho geofence: xadd và xread."""

    def __init__(self, batches: list | None = None) -> None:
        self.added: list[tuple[str, dict]] = []
        self.batches = list(batches or [])
        self.xread_args: list = []

    def xadd(self, stream: str, fields: dict, maxlen: int | None = None, approximate=True):
        self.added.append((stream, fields))
        return f"id-{len(self.added)}"

    def xread(self, streams: dict, count: int = 10, block: int = 0):
        self.xread_args.append(dict(streams))
        return self.batches.pop(0) if self.batches else []


def notification(session_id: str = "s-1", hit_id: str = "h-1") -> dict:
    return {
        "hitId": hit_id,
        "sessionId": session_id,
        "subscriptionId": "sub-1",
        "poiId": "poi-1",
        "title": "Phở Nhà Mình",
        "distanceMeters": 120.4,
    }


def entry(payload: dict, entry_id: str = "1-0"):
    return (
        geofence.NOTIFICATION_STREAM,
        [(entry_id, {"payload": json.dumps(payload, ensure_ascii=False)})],
    )


# --- publish ------------------------------------------------------------------


def test_publish_day_dung_so_thong_bao_vao_stream() -> None:
    client = FakeRedis()
    assert geofence.publish(client, [notification(), notification(hit_id="h-2")]) == 2
    assert all(stream == geofence.NOTIFICATION_STREAM for stream, _ in client.added)


def test_publish_khong_no_khi_redis_hong() -> None:
    class RedisHong:
        def xadd(self, *args, **kwargs):
            raise OSError("mat ket noi")

    assert geofence.publish(RedisHong(), [notification()]) == 0


def test_publish_giu_nguyen_tieng_viet_co_dau() -> None:
    """ensure_ascii=False. Nếu escape thì tiêu đề thông báo hiện ra dạng \\u1ec9."""
    client = FakeRedis()
    geofence.publish(client, [notification()])
    assert "Phở Nhà Mình" in client.added[0][1]["payload"]


# --- kênh SSE -----------------------------------------------------------------


def test_khung_sse_dung_dinh_dang(monkeypatch) -> None:
    """Sai định dạng khung là lỗi im lặng điển hình: server vẫn 200, trình duyệt
    vẫn kết nối, chỉ là onmessage không bao giờ chạy."""
    monkeypatch.setattr(geofence, "mark_notified", lambda ids: len(ids))
    client = FakeRedis([[entry(notification("s-1"))]])
    monkeypatch.setattr(geofence, "STREAM_TTL_SECONDS", 0.2)

    khung = list(geofence.stream_notifications("s-1", client=client))
    su_kien = [k for k in khung if k.startswith("event: proximity")]

    assert len(su_kien) == 1
    assert su_kien[0].startswith("event: proximity\ndata: ")
    assert su_kien[0].endswith("\n\n")
    payload = json.loads(su_kien[0].split("data: ", 1)[1].strip())
    assert payload["title"] == "Phở Nhà Mình"


def test_khong_gui_thong_bao_cua_phien_khac(monkeypatch) -> None:
    """Stream là kênh chung. Không lọc thì đây là rò rỉ vị trí giữa người dùng."""
    monkeypatch.setattr(geofence, "mark_notified", lambda ids: len(ids))
    monkeypatch.setattr(geofence, "STREAM_TTL_SECONDS", 0.2)
    client = FakeRedis([[entry(notification("phien-khac"))]])

    khung = list(geofence.stream_notifications("phien-cua-toi", client=client))

    assert not any(k.startswith("event: proximity") for k in khung)


def test_bat_dau_tu_cuoi_stream_chu_khong_phai_tu_dau(monkeypatch) -> None:
    """Đọc từ "0" thì một tab vừa mở sẽ dội lại toàn bộ thông báo cũ."""
    monkeypatch.setattr(geofence, "STREAM_TTL_SECONDS", 0.2)
    client = FakeRedis([])

    list(geofence.stream_notifications("s-1", client=client))

    assert client.xread_args[0][geofence.NOTIFICATION_STREAM] == "$"


def test_khong_co_su_kien_thi_van_gui_nhip_tim(monkeypatch) -> None:
    """Im lặng quá lâu thì proxy đóng kết nối."""
    monkeypatch.setattr(geofence, "STREAM_TTL_SECONDS", 0.2)
    client = FakeRedis([])

    khung = list(geofence.stream_notifications("s-1", client=client))

    assert any(k.startswith(":") for k in khung)


def test_redis_chet_thi_bao_loi_roi_dong(monkeypatch) -> None:
    """Ngoại lệ lọt ra khỏi generator của StreamingResponse sẽ cắt cụt response
    sau khi header đã gửi — client thấy kết nối đứt giữa chừng, không thấy lỗi."""
    def khong_ket_noi_duoc(*args, **kwargs):
        raise OSError("Redis khong chay")

    monkeypatch.setattr(geofence.redis.Redis, "from_url", khong_ket_noi_duoc)

    khung = list(geofence.stream_notifications("s-1", client=None))

    assert khung and khung[0].startswith("event: error")


def test_danh_dau_da_gui_cho_dung_nhung_hit_da_day_di(monkeypatch) -> None:
    """notified_at tách "đã phát hiện" khỏi "đã báo cho người dùng"."""
    da_danh_dau: list[list[str]] = []
    monkeypatch.setattr(geofence, "mark_notified", lambda ids: da_danh_dau.append(list(ids)))
    monkeypatch.setattr(geofence, "STREAM_TTL_SECONDS", 0.2)
    client = FakeRedis(
        [[(geofence.NOTIFICATION_STREAM, [
            ("1-0", {"payload": json.dumps(notification("s-1", "h-1"))}),
            ("1-1", {"payload": json.dumps(notification("phien-khac", "h-2"))}),
        ])]]
    )

    list(geofence.stream_notifications("s-1", client=client))

    assert da_danh_dau[0] == ["h-1"]


# --- SQL: những ràng buộc phải nằm TRONG câu lệnh ------------------------------


def test_chong_bao_trung_nam_trong_sql_chu_khong_phai_python() -> None:
    """Lọc ở Python thì hai ping xử lý gần nhau vẫn lọt cả hai."""
    assert "NOT EXISTS" in geofence._MATCH_SQL
    assert "geofence_hits" in geofence._MATCH_SQL
    assert "cooldown_minutes" in geofence._MATCH_SQL


def test_truy_van_dung_st_dwithin_de_an_duoc_gist_index() -> None:
    """ST_Distance(...) < r không dùng được index; ST_DWithin thì có."""
    assert "ST_DWithin" in geofence._MATCH_SQL


def test_moi_truy_van_deu_rang_buoc_theo_phien() -> None:
    """Thiếu session_id ở bất kỳ câu nào là một lỗ rò dữ liệu giữa người dùng."""
    for sql in (geofence._MATCH_SQL, geofence._LIST_SQL):
        assert "session_id" in sql


def test_tam_vung_lay_tu_pois_khong_nhan_toa_do_client() -> None:
    """Client gửi toạ độ thì một lỗi giao diện sẽ đặt vùng nhắc ở sai chỗ."""
    assert "FROM pois p" in geofence._INSERT_SQL
    assert "p.location" in geofence._INSERT_SQL


def test_ban_kinh_bi_kep_ve_khoang_hop_le() -> None:
    from app.models import GeofenceRequest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GeofenceRequest(poi_id="10000000-0000-0000-0000-000000000002", radius_meters=10)
    with pytest.raises(ValidationError):
        GeofenceRequest(poi_id="10000000-0000-0000-0000-000000000002", radius_meters=99_999)

    hop_le = GeofenceRequest(poi_id="10000000-0000-0000-0000-000000000002")
    assert hop_le.radius_meters == 300
    assert hop_le.cooldown_minutes == 30


def test_is_uuid() -> None:
    assert geofence.is_uuid("10000000-0000-0000-0000-000000000002") is True
    assert geofence.is_uuid("khong-phai-uuid") is False
    assert geofence.is_uuid(None) is False
    assert geofence.is_uuid("") is False


def test_socket_timeout_phai_dai_hon_thoi_gian_xread_chan() -> None:
    """Lỗi đã dính thật lần chạy đầu: socket_timeout 5 giây trong khi XREAD chặn
    15 giây, nên mọi kết nối SSE chết sau đúng 5 giây với "Timeout reading from
    socket" rồi kết nối lại vô tận. Nhìn từ trình duyệt thì không khác gì server
    chập chờn, và log chỉ có một dòng cảnh báo lặp lại."""
    assert geofence.STREAM_SOCKET_TIMEOUT > geofence.HEARTBEAT_SECONDS


def test_moi_ket_noi_deu_tu_dong_sau_mot_khoang_huu_han() -> None:
    """Không có trần này thì mỗi tab bỏ quên giữ một luồng vĩnh viễn trong
    threadpool của FastAPI, và server hết luồng trước khi hết bộ nhớ."""
    assert 0 < geofence.STREAM_TTL_SECONDS <= 3600
