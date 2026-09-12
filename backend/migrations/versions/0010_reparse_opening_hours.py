"""Phân tích lại giờ mở cửa đã lưu sau khi parser nhận thêm hai dạng.

Revision ID: 0010_reparse_opening_hours
Revises: 0009_rating_provenance

`app/opening_hours.py` trước đây bắt buộc phải có tiền tố ngày, nên hai dạng
rất phổ biến trong dữ liệu OSM bị đánh dấu `unsupported`:

- `06:00-22:00`  — khoảng giờ trần, trong cú pháp OSM nghĩa là áp dụng mọi ngày
- `06:00-24:00`  — `24:00` là nửa đêm cuối ngày, không phải giờ thứ 24

Đo trên database trước migration: 73/505 POI có `opening_hours` rơi vào nhóm
này. Với chúng, `is_open_now` trả `None`, `contextScore` tụt về mức trung tính
0.5, và đặc trưng `is_open` của mô hình LTR mất thêm độ phủ — vốn đã chỉ 11%.

Parser đã sửa, nhưng các bản ghi cũ vẫn giữ kết quả phân tích cũ trong cột
jsonb. Migration này đọc lại `opening_hours->>'raw'` và chạy lại parser MỚI
trên đúng chuỗi nguồn đó. Không mất mát gì: `raw` luôn được giữ nguyên từ đầu,
chính là lý do nó tồn tại.
"""

from __future__ import annotations

import json

from alembic import op

# Import trực tiếp từ ứng dụng thay vì chép logic sang đây: chép sang nghĩa là
# migration và runtime sẽ trôi ra khỏi nhau ngay lần sửa parser tiếp theo.
from app.opening_hours import parse_opening_hours

revision = "0010_reparse_opening_hours"
down_revision = "0009_rating_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.exec_driver_sql(
        """
        SELECT id::text AS id, opening_hours->>'raw' AS raw
        FROM pois
        WHERE opening_hours ? 'raw'
          AND opening_hours->>'parseStatus' = 'unsupported'
        """
    ).fetchall()

    fixed = 0
    for row in rows:
        parsed = parse_opening_hours(row.raw)
        if parsed.get("parseStatus") != "parsed" and not parsed.get("alwaysOpen"):
            continue  # vẫn không hiểu được; giữ nguyên `raw`, không đoán bừa
        connection.exec_driver_sql(
            "UPDATE pois SET opening_hours = %s::jsonb WHERE id = %s::uuid",
            (json.dumps(parsed, ensure_ascii=False), row.id),
        )
        fixed += 1

    print(f"  phan tich lai duoc {fixed}/{len(rows)} lich mo cua")


def downgrade() -> None:
    # Không đảo ngược: migration chỉ làm giàu kết quả phân tích từ `raw` vốn
    # luôn được giữ. Muốn quay lại thì hạ parser xuống rồi chạy lại.
    pass
