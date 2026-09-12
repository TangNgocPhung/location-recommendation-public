"""Log the exact feature vector shown to the user for each (request, POI).

Revision ID: 0013_ranking_snapshots
Revises: 0012_poi_photos_and_contact

`ltr/dataset.py` (nguồn nhãn `click`) tự ghi rõ khiếm khuyết của chính nó:
"Không có ảnh chụp đặc trưng tại thời điểm hiển thị... phải tính lại đặc trưng
ở hiện tại, nên các tín hiệu phụ thuộc thời gian (recency, trending, is_open,
hour_of_day) KHÔNG phải giá trị người dùng thực sự đã thấy."

Bảng này là phần còn thiếu: ghi `ltr.features.feature_row()` — ĐÚNG hàm mà
`ltr_model.score()` dùng lúc phục vụ — ngay tại thời điểm request trả kết quả,
trước khi bất kỳ tín hiệu phụ thuộc thời gian nào (popularity, regionCtr,
graph affinity, user preference) kịp đổi khác. Nhãn (`poi_click`/
`navigation_start`) ghép vào sau, offline, không cần tính lại feature.

Ghi ở server (trong `api.contextual_search`, sau khi có `rank` cuối cùng),
không phải client, để feature dùng huấn luyện không thể bị client giả mạo.
"""

from __future__ import annotations

from alembic import op


revision = "0013_ranking_snapshots"
down_revision = "0012_poi_photos_and_contact"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS ranking_snapshots (
            request_id UUID NOT NULL,
            poi_id TEXT NOT NULL,
            rank INTEGER NOT NULL,
            session_id UUID,
            retrieval_backend TEXT NOT NULL,
            ranker_used TEXT NOT NULL,
            features JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (request_id, poi_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ranking_snapshots_created_idx "
        "ON ranking_snapshots (created_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ranking_snapshots")
