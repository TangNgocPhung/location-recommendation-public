"""Add sponsored placement window to POIs.

Revision ID: 0007_sponsored_placements
Revises: 0006_poi_dwell_event_type

Một cột thời hạn thay vì cờ boolean: quảng cáo luôn có hạn, và cột hạn cho phép
hết hạn tự động mà không cần job dọn dẹp. NULL nghĩa là không tài trợ.

Ba ràng buộc đạo đức được cài cứng ở tầng ứng dụng (`ranking.insert_sponsored`),
không phải ở đây, và cố ý không cho cấu hình:
1. Địa điểm tài trợ vẫn phải qua bộ lọc địa lý — chỉ đổi vị trí trong tập kết
   quả đã truy xuất, không bao giờ chèn thêm POI mới.
2. Không bao giờ chiếm vị trí số 1.
3. Luôn kèm cờ ``sponsored`` để giao diện hiển thị nhãn "Tài trợ".
"""

from __future__ import annotations

from alembic import op


revision = "0007_sponsored_placements"
down_revision = "0006_poi_dwell_event_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pois ADD COLUMN IF NOT EXISTS sponsored_until TIMESTAMPTZ")
    # Chỉ mục một phần: đại đa số POI không tài trợ nên đánh chỉ mục toàn bảng
    # là lãng phí.
    op.execute(
        "CREATE INDEX IF NOT EXISTS pois_sponsored_until_idx "
        "ON pois (sponsored_until) WHERE sponsored_until IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS pois_sponsored_until_idx")
    op.execute("ALTER TABLE pois DROP COLUMN IF EXISTS sponsored_until")
