"""Ảnh địa điểm (Wikimedia Commons) + hai trường liên hệ vốn đã có sẵn trong dữ liệu OSM.

Revision ID: 0012_poi_photos_and_contact
Revises: 0011_geofence_runtime

**website/phone.** Hai trường này nằm sẵn trong `poi_source_records.raw_payload`
từ lần nhập OSM đầu tiên (300 thẻ `website`, 375 thẻ `phone` trên 3.000 bản ghi)
nhưng chưa bao giờ được nâng lên thành cột của `pois`, nên trang chi tiết không
có gì để hiện. Đọc thẳng từ JSONB mỗi lần mở một địa điểm thì phải quét bảng
nguồn — rẻ hơn nhiều khi nâng lên cột và backfill một lần.

Chỉ nhận `website` bắt đầu bằng `http://` hoặc `https://`. Dữ liệu OSM do người
nhập nên trường này có cả `www.abc.vn`, số điện thoại gõ nhầm ô, và cả chuỗi
rỗng. Nhét nguyên xi vào `<a href>` thì trình duyệt hiểu `www.abc.vn` là đường
dẫn TƯƠNG ĐỐI và người dùng bị đưa tới một trang 404 của chính ứng dụng.

**poi_photos.** OSM không có ảnh; Wikimedia Commons có, và đó là nguồn duy nhất
gọi được ở đây (không có GOOGLE_MAPS_API_KEY, Static Maps của MapTiler trả 403).
Cột `confidence` là phần quan trọng nhất của bảng này — xem chú thích ngay tại
chỗ tạo bảng.

**poi_photo_fetches.** Bảng chỉ có bốn cột nhưng thiếu nó là hỏng: đại đa số POI
KHÔNG có ảnh nào, và nếu chỉ cache qua sự tồn tại của dòng trong `poi_photos`
thì mỗi lần mở một quán không ảnh là một lần gọi lại Wikimedia. Wikimedia có
giới hạn tần suất thật (gọi dồn dập trả HTTP 429), nên "kết quả rỗng" phải là
một sự kiện được ghi lại chứ không phải sự vắng mặt của dữ liệu.
"""

from __future__ import annotations

from alembic import op

revision = "0012_poi_photos_and_contact"
down_revision = "0011_geofence_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE pois
            ADD COLUMN IF NOT EXISTS website TEXT,
            ADD COLUMN IF NOT EXISTS phone TEXT
        """
    )

    # Backfill từ thẻ OSM gốc.
    #
    # Gộp theo POI thay vì lấy bản ghi nguồn mới nhất: một POI có thể có nhiều
    # bản ghi nguồn, và bản mới nhất chưa chắc là bản có thẻ. `ARRAY_AGG ...
    # FILTER (WHERE ... IS NOT NULL)` lấy giá trị KHÔNG RỖNG mới nhất, tức
    # không đánh mất số điện thoại chỉ vì lần đồng bộ gần đây không kèm nó.
    op.execute(
        """
        WITH tag_values AS (
            SELECT
                r.canonical_poi_id AS poi_id,
                r.last_seen_at,
                COALESCE(
                    NULLIF(TRIM(r.raw_payload->'tags'->>'website'), ''),
                    NULLIF(TRIM(r.raw_payload->'tags'->>'contact:website'), '')
                ) AS website,
                COALESCE(
                    NULLIF(TRIM(r.raw_payload->'tags'->>'phone'), ''),
                    NULLIF(TRIM(r.raw_payload->'tags'->>'contact:phone'), '')
                ) AS phone
            FROM poi_source_records r
            WHERE r.raw_payload ? 'tags'
        ),
        picked AS (
            SELECT
                poi_id,
                (ARRAY_AGG(website ORDER BY last_seen_at DESC)
                    FILTER (WHERE website IS NOT NULL))[1] AS website,
                (ARRAY_AGG(phone ORDER BY last_seen_at DESC)
                    FILTER (WHERE phone IS NOT NULL))[1] AS phone
            FROM tag_values
            GROUP BY poi_id
        )
        UPDATE pois p
        SET website = CASE WHEN picked.website ~* '^https?://' THEN picked.website END,
            phone = picked.phone
        FROM picked
        WHERE p.id = picked.poi_id
          AND (picked.website IS NOT NULL OR picked.phone IS NOT NULL)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS poi_photos (
            id BIGSERIAL PRIMARY KEY,
            poi_id UUID NOT NULL REFERENCES pois(id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            url TEXT NOT NULL,
            thumb_url TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            title TEXT,
            confidence TEXT NOT NULL CHECK (confidence IN ('place', 'area')),
            distance_meters DOUBLE PRECISION,
            license TEXT,
            attribution TEXT,
            source_url TEXT,
            position SMALLINT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (poi_id, source, source_ref)
        )
        """
    )
    # `confidence` là ràng buộc ĐẠO ĐỨC chứ không phải siêu dữ liệu trang trí,
    # nên nó là CHECK ở tầng schema chứ không phải quy ước ở tầng ứng dụng:
    #
    #   'place' = ảnh của CHÍNH địa điểm, suy ra từ thẻ OSM image /
    #             wikimedia_commons / wikidata(P18). Đếm thật trên database
    #             này: 7 POI mang thẻ ảnh, 2 trong số đó trỏ ra ngoài Wikimedia
    #             nên bị từ chối — còn 5 POI, tức 0,17%.
    #   'area'  = ảnh chụp QUANH ĐÓ, tìm bằng geosearch theo toạ độ. Thử 25 POI
    #             thì 24 có ảnh trong 120-300 m, nhưng phần lớn là ảnh con phố,
    #             ảnh ô tô, ảnh logo — KHÔNG phải ảnh của quán.
    #
    # Trộn hai loại này lại là nói dối người dùng, nên `distance_meters` đi kèm
    # để giao diện ghi được "Ảnh khu vực · cách N m".
    op.execute(
        "CREATE INDEX IF NOT EXISTS poi_photos_poi_position_idx "
        "ON poi_photos (poi_id, position)"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS poi_photo_fetches (
            poi_id UUID PRIMARY KEY REFERENCES pois(id) ON DELETE CASCADE,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            photo_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL CHECK (status IN ('ready', 'empty'))
        )
        """
    )
    # CỐ Ý không có trạng thái 'unavailable' ở đây. "Chưa gọi được Wikimedia"
    # không phải một kết quả dò, nên nó không được phép nằm trong cache — ghi nó
    # xuống là biến một lần mất mạng thành "địa điểm này không có ảnh" suốt 30
    # ngày sau. Tầng ứng dụng trả 'unavailable' mà KHÔNG ghi dòng nào.


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS poi_photo_fetches")
    op.execute("DROP INDEX IF EXISTS poi_photos_poi_position_idx")
    op.execute("DROP TABLE IF EXISTS poi_photos")
    op.execute(
        """
        ALTER TABLE pois
            DROP COLUMN IF EXISTS phone,
            DROP COLUMN IF EXISTS website
        """
    )
