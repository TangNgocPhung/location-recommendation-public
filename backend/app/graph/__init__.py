"""Spatial Knowledge Graph (Neo4j) — Tầng 2.

Đồ thị tri thức không gian bổ sung cho truy xuất: các cạnh
``(:Session)-[:CLICKED]->(:Poi)``, ``(:Poi)-[:BELONGS_TO]->(:Category)``,
``(:Poi)-[:LOCATED_IN]->(:District)`` và ``(:Poi)-[:SIMILAR_TO]->(:Poi)`` (suy
ra từ đồng-click trong cùng phiên) cho phép sinh candidate kiểu "người có hành
vi tương tự cũng thích" và "địa điểm liên quan".

Đây là nguồn candidate *bổ sung*, có fallback: khi Neo4j chưa cấu hình hoặc
lỗi, các gợi ý vẫn chạy bằng đường session-affinity/PostGIS như trước.

- ``client``    — driver Neo4j, kiểm tra khả dụng + circuit breaker (import trễ).
- ``cypher``    — các hàm thuần dựng câu Cypher (test được, không cần Neo4j).
- ``sync``      — dựng/đồng bộ đồ thị từ PostGIS + ingestion_events.
- ``recommend`` — truy vấn "cũng thích" (collaborative) và "liên quan".
"""
