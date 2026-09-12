"""Recompute POI embeddings with the fixed hashing scheme (v1 -> v2).

Revision ID: 0005_recompute_embeddings_v2
Revises: 0004_normalize_poi_districts

Ở v1, hai slot băm của một token đều lấy modulo cùng `dimension` nên có thể
trùng nhau; khi trùng mà hai dấu ngược nhau thì token tự triệt tiêu. Truy vấn
một từ dính trường hợp đó cho vector toàn 0 (đo thực tế 0,78% từ đơn, trong đó
có "phở"), OpenSearch từ chối k-NN trên vector 0 và kênh Vector ANN chết âm
thầm. v2 buộc slot thứ hai lệch khỏi slot thứ nhất.

Vector v1 đã lưu không so sánh được với vector v2 sinh lúc truy vấn, nên toàn
bộ hàng còn mang model cũ phải được tính lại — nếu không, kênh k-NN sẽ sai ở
MỌI truy vấn chứ không riêng các từ hỏng.

Thuật toán băm được chép vào chính migration này thay vì import từ
`app.poi_features`. Migration phải đứng yên theo thời gian: bản 0003 import hàm
của app nên khi hàm đó đổi, kết quả của một migration đã chạy cũng đổi theo —
đúng cái bẫy đã tạo ra lỗi này.
"""

from __future__ import annotations

import hashlib
import json
import math

import sqlalchemy as sa
from alembic import op

from app.poi_features import normalize_text


revision = "0005_recompute_embeddings_v2"
down_revision = "0004_normalize_poi_districts"
branch_labels = None
depends_on = None

DIMENSION = 64
MODEL_V1 = "hashing-v1-64"
MODEL_V2 = "hashing-v2-64"


def _tokens(name: str | None, description: str | None, tags: list[str] | None) -> list[str]:
    joined = " ".join(part or "" for part in (name, description, " ".join(tags or [])))
    return normalize_text(joined).split()


def _finalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    return [round(value / norm, 8) for value in vector] if norm else vector


def _embedding_v1(name, description, tags) -> list[float]:
    vector = [0.0] * DIMENSION
    for token in _tokens(name, description, tags):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        for offset in (0, 4):
            index = int.from_bytes(digest[offset : offset + 4], "big") % DIMENSION
            sign = 1.0 if digest[offset + 8] & 1 else -1.0
            vector[index] += sign
    return _finalize(vector)


def _embedding_v2(name, description, tags) -> list[float]:
    vector = [0.0] * DIMENSION
    for token in _tokens(name, description, tags):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        first = int.from_bytes(digest[0:4], "big") % DIMENSION
        second = (int.from_bytes(digest[4:8], "big") % (DIMENSION - 1) + first + 1) % DIMENSION
        for offset, index in ((0, first), (4, second)):
            sign = 1.0 if digest[offset + 8] & 1 else -1.0
            vector[index] += sign
    return _finalize(vector)


_SELECT = sa.text(
    """
    SELECT id::text AS id, name, description, tags
    FROM pois
    WHERE embedding_model IS DISTINCT FROM :target
    """
)

_UPDATE = sa.text(
    """
    UPDATE pois SET
        embedding = ARRAY(
            SELECT value::real
            FROM jsonb_array_elements_text(CAST(:embedding_json AS jsonb)) AS value
        ),
        embedding_model = :embedding_model,
        updated_at = NOW()
    WHERE id = CAST(:id AS uuid)
    """
)


def _rewrite(target_model: str, encoder) -> None:
    connection = op.get_bind()
    rows = connection.execute(_SELECT, {"target": target_model}).mappings().all()
    payloads = [
        {
            "id": row["id"],
            "embedding_json": json.dumps(
                encoder(row["name"], row["description"], list(row["tags"] or []))
            ),
            "embedding_model": target_model,
        }
        for row in rows
    ]
    if payloads:
        connection.execute(_UPDATE, payloads)


def upgrade() -> None:
    _rewrite(MODEL_V2, _embedding_v2)


def downgrade() -> None:
    _rewrite(MODEL_V1, _embedding_v1)
