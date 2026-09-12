# Truy xuất đa kênh với OpenSearch — Bước 3-4

Tầng truy xuất tách khỏi ranking: OpenSearch cung cấp nhiều kênh ứng viên, gộp
bằng Reciprocal Rank Fusion, rồi hydrate từ PostGIS trước khi re-rank. PostGIS
vẫn là **nguồn dữ liệu chuẩn**; OpenSearch chỉ là lớp chỉ mục và có thể vắng
mặt — khi đó hệ thống tự rơi về truy xuất PostGIS thuần.

## Kiến trúc

```
                 ┌────────── OpenSearch (nếu khả dụng) ──────────┐
query, lat/lng → │ BM25 (không dấu+fuzzy) · Geo · Vector k-NN     │→ RRF fusion
                 └───────────────────────────────────────────────┘      │
       Redis trending ────────────────────────────────────────────────► │
                                                                          ▼
                                              top ~300 poi_id  →  Hydrate từ PostGIS
                                                                          ▼
                                          rerank đa tín hiệu → diversify → kết quả

   OpenSearch lỗi/chưa cấu hình  →  fetch_candidates (PostGIS: pg_trgm + PostGIS)
```

Mã nguồn nằm trong `backend/app/search/`:

| Module         | Trách nhiệm |
|----------------|-------------|
| `client.py`    | Kết nối OpenSearch, kiểm tra khả dụng (import trễ, nuốt lỗi). |
| `index.py`     | Mapping, analyzer `vi_folded` (bỏ dấu), `knn_vector`, build document. |
| `query.py`     | Hàm thuần dựng truy vấn BM25 [1] / geo / vector k-NN [4]. |
| `fusion.py`    | Reciprocal Rank Fusion [2]. |
| `retrieval.py` | Chạy các kênh, gộp, hydrate; trả `None` để fallback. |
| `enrichment.py`| Hydrate poi_id từ PostGIS, áp lại lọc bán kính/category. |
| `reindex.py`   | Indexing worker bản batch: đồng bộ PostGIS → OpenSearch. |

## Các kênh ứng viên

- **BM25** (`multi_match`, `fuzziness: AUTO`) trên `name`/`category_label`/
  `tags`/`brand`/`description`. Analyzer `vi_folded` = `lowercase` +
  `asciifolding`, nên "cà phê", "ca phe", "Cà Phê" đều khớp; fuzzy chịu lỗi gõ.
- **Geo** — `geo_distance` trong bán kính, sắp theo khoảng cách. Đây là kênh
  recall phổ quát; geo rỗng nghĩa là chỉ mục chưa dựng ⇒ hệ thống fallback.
- **Vector k-NN** — `knn_vector` 64 chiều (cosine) trên cùng embedding tất định
  `hashing-v1-64` dùng cho POI, cho tìm kiếm ngữ nghĩa. Có thể tắt bằng
  `OPENSEARCH_KNN_ENABLED=false`.
- **Trending** — poi_id nóng đọc từ Redis (`nearby:trending:pois`).

## Reciprocal Rank Fusion

Mỗi kênh đóng góp theo *thứ hạng*, không cần chuẩn hóa điểm khác thang. Đây là
Reciprocal Rank Fusion [2]:

```
score(doc) = Σ_kênh  weight_kênh / (k + rank_kênh(doc))      k = 60
```

Hằng số `k = 60` lấy từ bài gốc [2]; tác giả chọn giá trị này theo thực nghiệm để
hạ bớt ảnh hưởng của vài thứ hạng đầu. Xem [docs/thesis/references.md](thesis/references.md).

Trọng số mặc định: BM25 1.0 · vector 0.9 · geo 0.6 · trending 0.5. Điểm fusion
được chuẩn hóa về `[0,1]` và dùng làm tín hiệu `text` trong re-rank hiện có, nên
`rerank`/`diversify` không đổi.

## Đồng bộ chỉ mục

`search-index` (profile `data`) đọc toàn bộ POI từ PostGIS và bulk-index:

```powershell
docker compose --env-file config/development.env --profile data run --rm --build search-index
```

Thêm `--recreate` (mặc định trong compose) để xóa và dựng lại chỉ mục khi đổi
mapping. Có thể chạy trực tiếp: `python -m app.search.reindex --recreate`.

## Fallback và độ bền

- `SEARCH_BACKEND=auto` (mặc định): dùng OpenSearch khi kết nối được, tự rơi về
  PostGIS khi lỗi kết nối, chỉ mục rỗng, hoặc kênh geo không trả kết quả.
- `SEARCH_BACKEND=postgis`: tắt hẳn OpenSearch (hành vi trước Bước 3-4).
- `SEARCH_BACKEND=opensearch`: vẫn fallback khi lỗi, nhưng không tắt chủ động.
- k-NN lỗi (khác version) chỉ làm mất kênh vector; BM25 + geo vẫn chạy.

Kết quả mỗi ứng viên kèm `fusionScore` và `retrievalChannels` để giải thích và
phục vụ observability. `retrieve_candidates` trả thêm backend ("opensearch"/
"postgis") đã dùng.
