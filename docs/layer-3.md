# Tầng 3 — Ranking engine đa tín hiệu & cá nhân hóa

Tầng 1 đã thu thập và lưu bền interaction events; `stream-worker` đã tính
trending POI/query và cập nhật session profile vào Redis, nhưng trước bản
nâng cấp này không có gì tiêu thụ dữ liệu đó. Tầng 3 đóng vòng lặp: lấy
candidate từ PostGIS, làm giàu bằng tín hiệu Redis thời gian thực, re-rank đa
tín hiệu, rồi áp một luật đa dạng hóa — tương ứng với "Multi-Channel
Candidate Retrieval → Spatio-Temporal Enricher → LTR & Neural Re-ranking →
Diversity & Business Rules" trong kiến trúc tổng thể, chạy gọn trong
`backend/app/ranking.py` thay vì Elasticsearch/mô hình LTR riêng.

## Pipeline

1. **Candidate retrieval** (`fetch_candidates`): PostGIS trả POI trong bán
   kính (`ST_DWithin`) kèm `distance_meters` (`ST_Distance`) và `text_score`
   — độ tương đồng trigram (`pg_trgm`) lớn nhất giữa từ khóa và
   `name`/`description`/`category_label`, mặc định `1.0` khi không có từ
   khóa. Lọc thêm theo `category` nếu có. Không có từ khóa hoặc category,
   pipeline vẫn hoạt động — mọi tham số đều tùy chọn.
2. **Enrichment thời gian thực** (`apply_trending_boost`): `ZMSCORE` hàng
   loạt trên `nearby:trending:pois` (Redis Sorted Set do `stream_worker.py`
   ghi từ `poi_click`/`navigation_start`/`review`), chuẩn hóa bằng
   `score / (score + 10)` để không có POI nào chiếm toàn bộ trọng số.
3. **Re-ranking** (`rerank`): điểm cuối là tổng trọng số của text relevance,
   spatial decay dạng hàm mũ (`exp(-distance / 1500m)`), rating, popularity
   tĩnh và trending — cộng thêm `category_boost` tùy chọn (dùng cho cá nhân
   hóa, xem bên dưới).
4. **Diversity** (`diversify`): giới hạn tối đa 2 kết quả liên tiếp cùng
   category trong danh sách cuối, tránh top-N bị một category áp đảo dù điểm
   số cao.

Áp dụng cho cả `GET /api/pois/nearby` và `POST /api/v1/search`.

## Cá nhân hóa — `GET /api/v1/recommendations`

Không dùng feature store hay mô hình học máy riêng — tính "category
affinity" trực tiếp từ `ingestion_events` của một `session_id`:

- Trọng số sự kiện giống hệt trending POI (`poi_click`=1, `navigation_start`=3,
  `review`=5) để tín hiệu nhất quán giữa trending toàn cục và cá nhân hóa
  theo session.
- Chuẩn hóa affinity về `[0, 0.2]` rồi truyền vào `rerank` như
  `category_boost` — không thay thế các tín hiệu khác, chỉ nghiêng nhẹ kết
  quả về category người dùng hay tương tác.
- Session chưa có lịch sử (`cold start`) → `affinity` rỗng → endpoint vẫn trả
  kết quả bình thường (rating/popularity/trending), không lỗi, `personalized:
  false`.
- Mỗi kết quả có field `reason` để UI hiển thị minh bạch vì sao được gợi ý.

## Endpoint mới

| Endpoint | Mô tả |
|---|---|
| `GET /api/v1/categories` | Danh sách category có trong `pois`, kèm số lượng — phục vụ filter chip. |
| `GET /api/v1/trending?limit=` | Top POI và top từ khóa đang trending, đọc trực tiếp từ Redis Sorted Sets. |
| `GET /api/v1/recommendations?session_id=&lat=&lng=&radius=&limit=` | Gợi ý cá nhân hóa theo lịch sử session, có cold-start fallback. |

## Ánh xạ production

| Bản local | Khi mở rộng production |
|---|---|
| PostGIS `ST_DWithin` + `pg_trgm` similarity | OpenSearch/Elasticsearch (BM25 + H3 geo-fence) |
| Redis Sorted Set trending, tính trong Python | Feature Store (Feast/Hopsworks) + streaming aggregation (Flink) |
| Trọng số tuyến tính viết tay trong `rerank` | LTR/Neural re-ranking model (LightGBM, TF-Ranking...) |
| `fetch_category_affinity` truy vấn trực tiếp Postgres | User profile vector trong ML Feature Store, cập nhật gần thời gian thực |
