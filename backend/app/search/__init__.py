"""Tầng truy xuất đa kênh (Bước 3-4).

Gói này tách rõ trách nhiệm theo sơ đồ kiến trúc:

- ``client``     — kết nối OpenSearch, kiểm tra khả dụng (lazy import).
- ``index``      — mapping, analyzer tiếng Việt/không dấu, build document.
- ``query``      — các hàm thuần dựng truy vấn BM25 / geo / vector.
- ``fusion``     — Reciprocal Rank Fusion gộp nhiều kênh.
- ``retrieval``  — chạy các kênh, gộp và trả danh sách ứng viên.
- ``enrichment`` — hydrate ứng viên từ PostGIS (nguồn dữ liệu chuẩn).
- ``reindex``    — đồng bộ toàn bộ POI từ PostGIS sang OpenSearch.

PostGIS luôn là nguồn sự thật; OpenSearch chỉ là chỉ mục truy xuất và có thể
vắng mặt — khi đó ``ranking.rank_pois`` tự rơi về đường PostGIS thuần.
"""
