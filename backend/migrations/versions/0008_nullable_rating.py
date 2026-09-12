"""Rating: NULL nghĩa là "chưa ai đánh giá", không phải "0/5 điểm".

Revision ID: 0008_nullable_rating
Revises: 0007_sponsored_placements

Trước migration này, `pois.rating` là `NUMERIC(2,1) NOT NULL DEFAULT 0`. Hệ quả
đo được trên dữ liệu thật: 2982/3010 POI (99.07%) nhập từ OpenStreetMap có
rating = 0, vì `poi_import` không ghi cột này còn schema thì ép một giá trị mặc
định. Chỉ 28 POI seed có rating khác 0 — và chúng được gõ tay trong
`0002_seed_demo_data`.

Trong công thức xếp hạng, số hạng `w_rating * (rating / 5.0)` với w = 0.12 nghĩa
là mỗi POI seed được cộng không công ~0.108 điểm so với MỌI POI thật. Nói cách
khác, dữ liệu demo luôn xếp trên dữ liệu thật bất kể truy vấn là gì — đây là lỗi
chứ không phải lựa chọn thiết kế.

Cách sửa: phân biệt "chưa có dữ liệu" (NULL) với "điểm thấp" (giá trị nhỏ).
- `rating` cho phép NULL, ràng buộc đổi thành `NULL OR BETWEEN 0 AND 5`.
- Mọi rating = 0 hiện có chuyển thành NULL (không POI nào thực sự được chấm 0).
- `review_count` và `popularity_score` giữ nguyên NOT NULL: với chúng, 0 là giá
  trị ĐÚNG nghĩa ("chưa có lượt đánh giá nào", "chưa có tương tác nào"), khác
  hẳn rating.

Tầng ứng dụng phải xử lý NULL tường minh: baseline tuyến tính bỏ qua số hạng
rating khi thiếu và chuẩn hóa lại trọng số, còn LightGBM nhận thẳng NaN và tự
học nhánh missing.
"""

from __future__ import annotations

from alembic import op

revision = "0008_nullable_rating"
down_revision = "0007_sponsored_placements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pois ALTER COLUMN rating DROP NOT NULL")
    op.execute("ALTER TABLE pois ALTER COLUMN rating DROP DEFAULT")
    op.execute("ALTER TABLE pois DROP CONSTRAINT IF EXISTS pois_rating_check")
    op.execute(
        """
        ALTER TABLE pois ADD CONSTRAINT pois_rating_check
        CHECK (rating IS NULL OR (rating BETWEEN 0 AND 5))
        """
    )
    # Không POI nào thực sự bị chấm 0/5; mọi số 0 đang có đều là giá trị mặc
    # định của schema cũ, tức là "không biết".
    op.execute("UPDATE pois SET rating = NULL WHERE rating = 0")


def downgrade() -> None:
    op.execute("UPDATE pois SET rating = 0 WHERE rating IS NULL")
    op.execute("ALTER TABLE pois DROP CONSTRAINT IF EXISTS pois_rating_check")
    op.execute(
        """
        ALTER TABLE pois ADD CONSTRAINT pois_rating_check
        CHECK (rating BETWEEN 0 AND 5)
        """
    )
    op.execute("ALTER TABLE pois ALTER COLUMN rating SET DEFAULT 0")
    op.execute("ALTER TABLE pois ALTER COLUMN rating SET NOT NULL")
