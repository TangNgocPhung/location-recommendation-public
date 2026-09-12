"""Ghi nguồn gốc của từng rating.

Revision ID: 0009_rating_provenance
Revises: 0008_nullable_rating

Sau migration 0008, `pois.rating` phân biệt được "chưa ai đánh giá" (NULL) với
"điểm thấp". Nhưng vẫn còn một câu hỏi mà hội đồng chắc chắn sẽ hỏi và hiện
database không trả lời được: **điểm này ở đâu ra?**

Ba nguồn có bản chất khác hẳn nhau:

- `seed`   — 28 POI gõ tay trong `0002_seed_demo_data`. Là dữ liệu bịa, chỉ để
             demo, KHÔNG được dùng làm bằng chứng trong báo cáo.
- `google` — lấy từ Google Places API. Là đánh giá thật của người dùng thật,
             nhưng là dữ liệu của bên thứ ba.
- `user`   — người dùng chính ứng dụng này chấm, gộp từ bảng `poi_reviews`.
             Đây mới là nguồn mà kiến trúc hướng tới.

Không tách được ba nguồn này thì mọi phát biểu kiểu "tín hiệu rating đóng góp
X% vào nDCG" đều mơ hồ: không rõ đang nói về dữ liệu bịa hay dữ liệu thật.

`google_place_id` để riêng một cột vì hai lý do. Thứ nhất, nó là khóa để làm
mới dữ liệu mà không phải dò tìm lại (việc dò tìm theo tên + toạ độ là bước
dễ khớp nhầm nhất). Thứ hai, trong điều khoản của Google, place ID là trường
được phép lưu lâu dài, khác với nội dung hiển thị như điểm số và số lượt đánh
giá vốn bị giới hạn thời gian lưu — `rating_fetched_at` tồn tại để biết bản
ghi đã cũ bao lâu và khi nào cần làm mới.
"""

from __future__ import annotations

from alembic import op

revision = "0009_rating_provenance"
down_revision = "0008_nullable_rating"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE pois
            ADD COLUMN IF NOT EXISTS rating_source TEXT,
            ADD COLUMN IF NOT EXISTS rating_fetched_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS google_place_id TEXT
        """
    )
    op.execute(
        """
        ALTER TABLE pois DROP CONSTRAINT IF EXISTS pois_rating_source_check;
        ALTER TABLE pois ADD CONSTRAINT pois_rating_source_check
        CHECK (rating_source IS NULL OR rating_source IN ('seed', 'google', 'user'))
        """
    )
    # Rating duy nhất đang tồn tại là của 28 POI seed; đánh dấu đúng bản chất.
    op.execute(
        "UPDATE pois SET rating_source = 'seed' WHERE rating IS NOT NULL AND source = 'seed'"
    )
    # Một POI chỉ ứng với một place ID; trùng nghĩa là bước dò tìm đã khớp hai
    # POI khác nhau vào cùng một địa điểm Google — phải chặn ở tầng schema chứ
    # không dựa vào script nhớ kiểm tra.
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS pois_google_place_id_key
        ON pois (google_place_id) WHERE google_place_id IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS pois_rating_source_idx
        ON pois (rating_source) WHERE rating_source IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS pois_rating_source_idx")
    op.execute("DROP INDEX IF EXISTS pois_google_place_id_key")
    op.execute("ALTER TABLE pois DROP CONSTRAINT IF EXISTS pois_rating_source_check")
    op.execute(
        """
        ALTER TABLE pois
            DROP COLUMN IF EXISTS rating_source,
            DROP COLUMN IF EXISTS rating_fetched_at,
            DROP COLUMN IF EXISTS google_place_id
        """
    )
