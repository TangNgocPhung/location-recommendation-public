"""ML Feature Store gọn nhẹ (Tầng 2).

Thể hiện đúng vai trò "Feature Store" trong sơ đồ mà không cần Feast/Hopsworks:
Postgres là **offline store** (lịch sử, dùng để huấn luyện), Redis là **online
store** (độ trễ thấp, dùng để serving), với **định nghĩa feature có version**
dùng chung cho cả hai để tránh training/serving skew.

Feature theo hình:
- ``user_profile``  — vector hồ sơ người dùng theo phiên (category affinity,
  mức giá ưa thích, số sự kiện).
- ``region_ctr``    — CTR lịch sử theo (quận, category).
- ``poi_embedding`` — embedding POI (đã có sẵn trong bảng ``pois``).

- ``registry``    — định nghĩa feature view + version (thuần, test được).
- ``offline``     — tính feature từ Postgres (nguồn sự thật để huấn luyện).
- ``online``      — đọc/ghi online store trên Redis, có fallback.
- ``materialize`` — job đẩy feature offline -> online.
"""
