# Spatial Knowledge Graph (Neo4j) — Tầng 2

Đồ thị tri thức không gian bổ sung một kênh candidate cho gợi ý: thay vì chỉ
dựa trên khoảng cách/từ khóa, nó khai thác **hành vi đồng-click** để sinh gợi ý
kiểu *"người có hành vi tương tự cũng thích"* và *"địa điểm liên quan"*.

Đây là nguồn candidate **bổ sung, có fallback**: khi Neo4j chưa cấu hình hoặc
lỗi, gợi ý vẫn chạy bằng session-affinity + PostGIS như trước (circuit breaker
như `app.search`). Mã nguồn: [`backend/app/graph/`](../backend/app/graph/).

## Mô hình đồ thị

```
(:Session {id})-[:CLICKED {weight}]->(:Poi {id, name, category, district})
(:Poi)-[:BELONGS_TO]->(:Category {name})
(:Poi)-[:LOCATED_IN]->(:District {name})
(:Poi)-[:SIMILAR_TO {weight}]->(:Poi)      // suy ra từ đồng-click cùng phiên
```

| Module | Trách nhiệm |
|---|---|
| `client.py` | Driver Neo4j, `graph_available()` + circuit breaker (import trễ). |
| `cypher.py` | Câu Cypher đọc thuần (test được, tham số hóa). |
| `sync.py` | Dựng/đồng bộ đồ thị từ PostGIS + `ingestion_events`. |
| `recommend.py` | `also_liked_ids` (collaborative) và `related_ids` (SIMILAR_TO). |

## Sinh candidate

- **"Cũng thích"** (`ALSO_LIKED`): tìm các phiên khác từng click trùng POI với
  phiên hiện tại, rồi lấy POI khác mà họ click — collaborative filtering trên
  đồ thị đồng-click, xếp theo số phiên chung.
- **"Liên quan"** (`RELATED_POIS`): hàng xóm `SIMILAR_TO` của một tập hạt giống.

`SIMILAR_TO` được dựng bằng Cypher: hai POI được cùng một phiên click thì nối
với nhau, trọng số = số phiên chung (`--min-shared` mặc định 1 cho đồ án nhỏ,
nên đặt ≥2 khi có nhiều dữ liệu).

## Ảnh hưởng tới xếp hạng

`GET /api/v1/recommendations` lấy `graph_candidate_ids(session_id)` và truyền
`graph_boost` vào `ranking.rerank`; POI được đồ thị gợi ý nhận thêm
`w_graph = 0.10` và cờ `graphRecommended=true` (đổi lý do gợi ý thành *"Người có
hành vi tương tự cũng thích địa điểm này"*). Phản hồi có `graphRecommendations`
= số POI đến từ đồ thị.

## Đồng bộ đồ thị

`graph-sync` (profile `data`) đọc POI + click từ PostGIS và dựng lại đồ thị:

```powershell
docker compose --env-file config/development.env --profile data run --rm --build graph-sync
```

Chạy trực tiếp: `python -m app.graph.sync --recreate [--min-shared 2]`.

## Fallback

- `GRAPH_BACKEND=auto` (mặc định): dùng Neo4j khi kết nối được, tự bỏ qua khi
  chưa cấu hình / lỗi / chưa đồng bộ (đồ thị rỗng → không có gợi ý graph).
- `GRAPH_BACKEND=off`: tắt hẳn.
- Neo4j chiếm ~0.8 GB RAM (heap 512m + pagecache 256m); giảm trong compose nếu
  máy yếu, hoặc đặt `GRAPH_BACKEND=off`.
