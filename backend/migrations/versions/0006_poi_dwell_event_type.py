"""Split poi_impression into real impressions and a new poi_dwell event.

Revision ID: 0006_poi_dwell_event_type
Revises: 0005_recompute_embeddings_v2

Trước bước này, frontend chỉ bắn `poi_impression` khi người dùng RỜI một POI đã
click, kèm `dwell_ms`. Nghĩa là impression là tập con của click, nên CTR trong
feature store tiến tới 1.0 và được cộng thẳng vào điểm xếp hạng.

Migration tách hai khái niệm: `poi_impression` trở thành "đã được hiển thị",
`poi_dwell` mang thời gian ở lại. Các hàng cũ có `dwell_ms` đều do luồng dwell
sinh ra nên được chuyển sang loại mới; nhờ vậy dữ liệu lịch sử không còn bơm
click vào tử số CTR mà không có mẫu số tương ứng.

Hàng `poi_impression` cũ mà `dwell_ms IS NULL` (chỉ có thể đến từ gọi API thủ
công, vì frontend luôn gửi dwell_ms) được GIỮ NGUYÊN là impression — chúng vô
hại với cả hai cách hiểu và không vi phạm ràng buộc mới.
"""

from __future__ import annotations

from alembic import op


revision = "0006_poi_dwell_event_type"
down_revision = "0005_recompute_embeddings_v2"
branch_labels = None
depends_on = None

OLD_TYPES = ("search", "location_ping", "poi_impression", "poi_click", "navigation_start", "review")
NEW_TYPES = OLD_TYPES[:3] + ("poi_dwell",) + OLD_TYPES[3:]

# CHECK ở 0001 được viết inline và KHÔNG đặt tên, nên tên thật do PostgreSQL tự
# sinh. Không đoán tên: tra pg_constraint tìm đúng ràng buộc CHECK nào nhắc tới
# cột event_type rồi drop theo tên tìm được.
#
# Câu SQL dưới đây CỐ Ý không chứa ký tự '%' nào: psycopg dùng paramstyle
# pyformat, nên '%' trong câu lệnh có thể bị hiểu là placeholder và làm migration
# đổ ngay khi chạy. Vì vậy dùng strpos() thay ILIKE '...' và nối chuỗi với
# quote_ident() thay format().
_DROP_EVENT_TYPE_CHECK = """
DO $$
DECLARE target_name TEXT;
BEGIN
    FOR target_name IN
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        WHERE rel.relname = 'ingestion_events'
          AND con.contype = 'c'
          AND strpos(pg_get_constraintdef(con.oid), 'event_type') > 0
    LOOP
        EXECUTE 'ALTER TABLE ingestion_events DROP CONSTRAINT ' || quote_ident(target_name);
    END LOOP;
END $$;
"""


def _add_check(types: tuple[str, ...]) -> str:
    values = ", ".join(f"'{value}'" for value in types)
    return (
        "ALTER TABLE ingestion_events ADD CONSTRAINT ingestion_events_event_type_check "
        f"CHECK (event_type IN ({values}))"
    )


def upgrade() -> None:
    # Thứ tự bắt buộc: phải drop CHECK trước, vì 'poi_dwell' chưa hợp lệ với
    # ràng buộc cũ nên UPDATE sẽ bị từ chối.
    op.execute(_DROP_EVENT_TYPE_CHECK)
    op.execute(
        """
        UPDATE ingestion_events
        SET event_type = 'poi_dwell'
        WHERE event_type = 'poi_impression' AND dwell_ms IS NOT NULL
        """
    )
    op.execute(_add_check(NEW_TYPES))


def downgrade() -> None:
    op.execute(_DROP_EVENT_TYPE_CHECK)
    op.execute("UPDATE ingestion_events SET event_type = 'poi_impression' WHERE event_type = 'poi_dwell'")
    op.execute(_add_check(OLD_TYPES))
