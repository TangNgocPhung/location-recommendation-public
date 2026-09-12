# Phase 3 — Kiểm toán Feature Vector trước bước xếp hạng

**Ngày đo:** 2026-09-11 · **Dữ liệu:** Postgres `nearby_dev` đang chạy (3010 POI, 1249 sự kiện)

Tài liệu này KHÔNG dựa vào sơ đồ kiến trúc hay suy đoán. Mỗi dòng đều có trích
dẫn `file:line` và, nơi nào liên quan tới dữ liệu, kèm số đếm truy vấn trực tiếp
từ database.

## 0. Đường đi thực tế của một POI

Trình tự đúng như trong `ranking.rank_pois_detailed`
(`backend/app/ranking.py:360-397`):

```
retrieve_candidates()      # OpenSearch đa kênh + RRF, tự rơi về PostGIS
  |
apply_trending_boost()     # + trendingScore          (ranking.py:280)
  |
enrich_candidates()        # + openNow, closesIn, opensIn, timeContext,
  |                        #   etaMinutes, popularityWindows, recencyScore,
  |                        #   contextScore            (spatio_temporal.py:157)
attach_region_ctr()        # + regionCtr              (features/serving.py:13)
  |
rerank()                   # to hop tuyen tinh 9 tin hieu (ranking.py:152)
  |
diversify / price / distance / sponsored               (ranking.py:390-394)
```

Feature vector cuối cùng đi vào `rerank()` gồm **9 tín hiệu có trọng số** cộng
với `category_boost`. Đây là danh sách đầy đủ, không có tín hiệu nào khác.

## 1. Bảng kiểm toán

Chú giải: ✅ tính thật từ dữ liệu · ⚠️ có chạy nhưng là heuristic/thiếu dữ liệu · ❌ chưa triển khai

| # | Feature | Trọng số | Trạng thái | Bằng chứng | Thực tế đo được |
|---|---|---:|:---:|---|---|
| 1 | `distance` | 0.24 | ✅ | `ST_Distance(geography)` `ranking.py:76` | Chính xác trên ellipsoid. Decay `exp(-d/1500)` `ranking.py:164` |
| 2 | `is_open` | *(trong context)* | ✅ | `opening_status()` `spatio_temporal.py:171` | Thuật toán đúng, **nhưng 2505/3010 POI (83.2%) không có `opening_hours`** → trả `None` → tính trung tính 0.5 |
| 3 | `time_of_day` | *(trong context)* | ✅ | `time_context()` `spatio_temporal.py:78` | Đồng hồ hệ thống + `ZoneInfo`, 5 buổi, cờ cuối tuần |
| 4 | `category_time_match` | *(trong context)* | ⚠️ | `CATEGORY_TIME_AFFINITY` `spatio_temporal.py:55` | **Dict gõ tay 12 loại.** Không học từ dữ liệu. Loại ngoài danh sách = trung tính |
| 5 | `contextScore` | 0.10 | ⚠️ | `_context_score()` `spatio_temporal.py:137` | Tổ hợp `0.6×giờ mở + 0.4×thời điểm`, hai hệ số gõ tay |
| 6 | `popularity` | 0.08 | ❌ | cột `pois.popularity_score` | **2982/3010 (99.07%) = 0.** Chỉ 28 POI seed có giá trị, gõ tay trong migration. Không job nào cập nhật |
| 7 | `rating` | 0.12 | ❌ | cột `pois.rating` | **2982/3010 (99.07%) = 0.** 28 giá trị còn lại gõ tay ở `0002_seed_demo_data.py:20`. Bảng `poi_reviews` **rỗng (0 dòng)**. Không có đường ghi nào |
| 8 | `recency` | 0.12 | ✅ | `windowed_popularity()` `spatio_temporal.py:107` | Thật: đếm event 15p/1h/24h, decay `3.0/1.5/0.5`. Cố ý loại `poi_impression` để tránh vòng lặp tự củng cố `spatio_temporal.py:38-47` |
| 9 | `trending` | 0.08 | ⚠️ | Redis ZSET `ranking.py:280` | Có 37 POI trong `nearby:trending:pois`. Nhưng **toàn cục chứ không theo hexagon**, và bộ đếm **không suy giảm** |
| 10 | `ctr` | 0.08 | ❌ | `attach_region_ctr()` `features/serving.py:13` | **Luôn = 0.** Registry đọc khóa `feat:v2:region_ctr:*` (`registry.py:42`) nhưng Redis chỉ có khóa `feat:v1:*` cũ → miss 100%. Ngoài ra 2804/3010 POI (93.2%) không có `district` |
| 11 | `graph` | 0.10 | ❌ | `/api/v1/search` `api.py:145` | **Không bao giờ được truyền trên đường tìm kiếm.** `rank_pois_detailed` có tham số `graph_boost` nhưng `contextual_search` gọi mà không truyền. Chỉ `/api/v1/recommendations` (`api.py:203`) dùng |
| 12 | `user preference` | *(category_boost)* | ❌ | cùng chỗ trên | Cùng lý do #11. Bảng `user_preferences` cũng **rỗng (0 dòng)** |
| 13 | `text` | 0.26 | ⚠️ | xem mục 2 | **Hai thang đo khác nhau tùy backend** — xem phân tích riêng bên dưới |
| 14 | `ETA` | *(chỉ hiển thị)* | ⚠️ | `eta_minutes()` `spatio_temporal.py:97` | Đường chim bay ÷ vận tốc cố định (đi bộ 4.8, xe máy 22, ô tô 26 km/h). Không routing. **Không vào công thức xếp hạng** |
| 15 | `weather` | — | ❌ | — | `grep -i weather backend/app` → **0 kết quả**. Không tồn tại |
| 16 | `traffic` | — | ❌ | — | `grep -i traffic backend/app` → **0 kết quả**. Không tồn tại |

### Tổng kết

- **Thật sự hoạt động (✅):** 4/16 — distance, is_open, time_of_day, recency
- **Heuristic hoặc thiếu dữ liệu (⚠️):** 5/16 — category_time_match, contextScore, trending, text, ETA
- **Không đóng góp gì (❌):** 7/16 — popularity, rating, ctr, graph, user preference, weather, traffic

Tức là **trong 9 tín hiệu có trọng số, 4 tín hiệu (rating 0.12 + popularity 0.08
+ ctr 0.08 + graph 0.10 = 0.38, chiếm 32% tổng trọng số 1.18) hiện đang là hằng
số 0 hoặc gần như vậy** với 99% POI.

## 2. Phát hiện quan trọng nhất — `textScore` không cùng đơn vị giữa hai backend

Hệ thống có hai đường truy xuất và `textScore` mang ý nghĩa **hoàn toàn khác nhau**:

| Backend | `textScore` là gì | Miền giá trị |
|---|---|---|
| PostGIS (dự phòng) | `pg_trgm similarity()` giữa truy vấn và name/description/category_label (`ranking.py:79-85`) | Độ tương đồng trigram thật, [0,1] |
| OpenSearch (chính) | **Điểm RRF hợp nhất** chia cho max trong tập ứng viên (`enrichment.py:97`) | Luôn có đúng một ứng viên = 1.0 |

Hai hệ quả:

1. **Ứng viên hạng 1 của RRF luôn nhận trọn 0.26 điểm**, bất kể nó khớp văn bản
   tốt hay dở — vì chuẩn hóa theo max.
2. **Đếm trùng tín hiệu.** RRF hợp nhất 4 kênh: bm25, vector, **geo**, **trending**
   (`retrieval.py:110`). Nghĩa là geo và trending đã nằm sẵn trong `textScore`,
   rồi lại được cộng lần nữa qua `w_spatial = 0.24` và `w_trending = 0.08`.

Đây là lời giải thích trực tiếp cho nghịch lý trong ablation gần nhất: **B0
(PostGIS thuần) đạt nDCG 0.9291 còn B3 (hệ thống đầy đủ) chỉ 0.4720**. Hệ thống
"đầy đủ" tệ hơn baseline vì nó cộng geo hai lần rồi thay tín hiệu text thật
bằng một điểm thứ hạng đã chuẩn hóa.

## 3. Trả lời câu hỏi về `rating`

> *"rating hãy lấy từ cái app thật chứ sao bịa"*

Đúng, và hạ tầng đã có sẵn 3/5 mảnh:

| Mảnh | Trạng thái |
|---|---|
| `POST /api/v1/events/batch` nhận event `review` kèm rating 1–5 | ✅ `api.py:239`, `models.py:35` |
| Lưu vào `ingestion_events` | ✅ `ingestion.py:31` |
| Bảng `poi_reviews` đã thiết kế đầy đủ | ✅ `0003_enrich_poi_model.py:201` — **nhưng 0 dòng** |
| Giao diện để người dùng chấm điểm | ❌ `'review'` có trong union type (`telemetry.ts:10`) nhưng **không màn hình nào phát sự kiện này** |
| Job tổng hợp `poi_reviews` → `pois.rating` | ❌ không tồn tại |

Cần phân biệt hai vấn đề khác nhau:

- **Rating của 28 POI seed là bịa** → không dùng làm bằng chứng được.
- **Rating của 2982 POI thật = 0** → đây là **lỗi**, không phải thiếu dữ liệu.
  Schema đặt `NOT NULL DEFAULT 0` (`0001_initial_schema.py:31`) nên "chưa có ai
  đánh giá" bị mã hóa thành "điểm 0/5" — tức là *tệ nhất có thể*. Với
  `w_rating = 0.12`, mỗi POI seed được cộng không công `0.12 × 4.5/5 ≈ 0.108`
  so với mọi POI thật. **POI demo luôn thắng POI thật, bất kể truy vấn.**

OpenStreetMap không có trường rating nên nguồn ngoài cũng không lấp được. Hướng
xử lý đưa vào Phase 4–5: chuyển `rating` sang cho phép `NULL`, thêm cờ
`has_rating`, và để LightGBM tự học nhánh missing thay vì ép về 0.

## 4. Việc phải làm trước khi sang Phase 4

1. Sửa `rating`: cho phép NULL, ngừng coi 0 là "điểm kém". *(bắt buộc — đang bóp méo mọi số đo)*
2. Sửa `textScore` của đường OpenSearch: tách `bm25Score` thật ra khỏi `fusionScore`. *(bắt buộc — nguyên nhân B3 < B0)*
3. Materialize lại `region_ctr` sang khóa `v2`, hoặc tắt hẳn `w_ctr` và ghi rõ lý do.
4. Truyền `graph_boost` + `category_boost` vào `/api/v1/search`, hoặc đặt hai trọng số về 0.
5. Ghi nhận `weather`/`traffic` là "hướng phát triển", không vẽ vào sơ đồ hệ thống hiện tại.
