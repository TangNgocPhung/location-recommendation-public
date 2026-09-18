# Nearby — Location Recommendation MVP

MVP tìm kiếm và xếp hạng địa điểm theo vị trí tại TP.HCM.

## Giới thiệu

**Trường Đại học Sư phạm Thành phố Hồ Chí Minh** — Khoa Công nghệ thông tin

- Giảng viên hướng dẫn: TS.GVC. Nguyễn Quốc Huy
- Học viên thực hiện:
  1. Tăng Ngọc Phụng — KHMT836027
  2. Hoàng Châu Ngọc Phương — KHMT836028
  3. Lê Thị Mai Len — KHMT836015

Trên giao diện, nút **Giới thiệu** ở thanh trên cùng mở lại thông tin này; hộp
giới thiệu tự hiện ở lần truy cập đầu tiên của mỗi trình duyệt.

## Thành phần

- `frontend/`: React/Vinext, MapLibre và giao diện danh sách POI.
- `backend/`: FastAPI, geo-parser, event ingestion và stream worker.
- `gateway/`: NGINX edge router, request ID và rate limiting.
- `backend/migrations/`: Alembic migrations cho PostGIS, gazetteer, event log và dữ liệu mẫu.
- `docs/layer-1.md`: thiết kế chi tiết, event contract và luồng xử lý tầng 1.
- `docs/quality-gates.md`: ngưỡng latency, event lag, relevance và cách đo.
- `docs/data-model.md`: mô hình POI giàu thuộc tính, H3/vector và pipeline OSM.
- `docs/layer-4-retrieval.md`: truy xuất đa kênh OpenSearch (BM25/geo/vector) và RRF.
- `docs/layer-5-context.md`: làm giàu ngữ cảnh không gian–thời gian trước ranking.
- `docs/layer-2-graph.md`: Spatial Knowledge Graph (Neo4j) và gợi ý collaborative.
- `docs/layer-2-features.md`: ML Feature Store (Postgres offline + Redis online).
- `backend/app/search/`: gói truy xuất đa kênh và indexing worker.
- `backend/app/graph/`: Knowledge Graph (Neo4j) — đồng bộ, gợi ý "cũng thích".
- `backend/app/features/`: feature store — registry có version, offline/online, materialize.
- `backend/app/spatio_temporal.py`: enricher giờ mở, thời điểm, popularity theo cửa sổ, ETA.
- `backend/app/poi_detail.py`: chi tiết một POI — giờ mở cửa đủ 7 ngày, ETA, đánh giá, địa điểm tương tự.
- `backend/app/photos.py`: ảnh Wikimedia Commons cho POI, hai mức tin cậy `place`/`area`.
- `docker-compose.yml`: migration, frontend, gateway, API, worker, Redis, OpenSearch và PostGIS.

## Chạy toàn bộ bằng Docker

Hãy mở Docker Desktop và chờ engine sẵn sàng, sau đó chạy:

```bash
docker compose --env-file config/development.env up --build --wait
```

Service `migrate` tự chạy `alembic upgrade head` trước khi API và worker khởi
động. Các file dưới `config/` tách biệt development, test và production;
`config/production.env` bị Git ignore và không được phép dùng mật khẩu mẫu.

Sau khi các service sẵn sàng:

- Giao diện đầy đủ qua gateway: http://localhost:8081
- Giao diện trực tiếp: http://localhost:3001
- API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

## Chạy riêng giao diện

Giao diện có dữ liệu dự phòng nên vẫn dùng được khi backend chưa chạy:

```bash
cd frontend
pnpm install
pnpm dev
```

Mở http://localhost:3000. Khi API ở `http://localhost:8000` sẵn sàng,
giao diện sẽ tự động dùng dữ liệu từ PostGIS.

## Migration và kiểm thử

Tạo migration mới từ thư mục `backend/`:

```bash
alembic revision -m "describe change"
alembic upgrade head
```

Chạy unit tests không cần Docker:

```bash
cd backend
python -m pip install -r requirements-dev.txt
pytest tests/unit
```

Integration và E2E dùng toàn bộ stack:

```powershell
$env:RUN_INTEGRATION = "1"
$env:API_BASE_URL = "http://localhost:8000"
pytest tests/integration
$env:RUN_E2E = "1"
$env:GATEWAY_BASE_URL = "http://localhost:8081"
pytest tests/e2e
```

CI chạy frontend lint/build, backend unit tests, kiểm tra Docker Compose,
integration, E2E và relevance gate. Chi tiết các ngưỡng và benchmark nằm ở
[docs/quality-gates.md](docs/quality-gates.md).

## API chính

```http
GET /api/pois/nearby?lat=10.7757&lng=106.7009&radius=3000&q=cà%20phê
```

Ứng viên được truy xuất đa kênh qua OpenSearch (BM25 không dấu + fuzzy, geo,
vector k-NN) gộp bằng Reciprocal Rank Fusion, tự rơi về PostGIS (`pg_trgm`) khi
OpenSearch không khả dụng — xem [docs/layer-4-retrieval.md](docs/layer-4-retrieval.md).
Ứng viên được làm giàu ngữ cảnh không gian–thời gian (đang mở/sắp đóng, thời
điểm trong ngày, popularity theo cửa sổ 15p/1h/24h, ETA) — xem
[docs/layer-5-context.md](docs/layer-5-context.md). Sau đó ranking engine đa tín
hiệu re-rank theo relevance, spatial decay hàm mũ, rating, popularity, trending
thời gian thực và các tín hiệu ngữ cảnh — xem [docs/layer-3.md](docs/layer-3.md).

Endpoint khác:

- `GET /api/v1/categories` — danh sách category cho filter chip.
- `GET /api/v1/trending` — POI và từ khóa đang được quan tâm.
- `GET /api/v1/recommendations` — gợi ý cá nhân hóa theo lịch sử session và
  Knowledge Graph ("người có hành vi tương tự cũng thích", xem
  [docs/layer-2-graph.md](docs/layer-2-graph.md)).
- `GET /api/v1/data/status` — độ phủ dữ liệu và trạng thái import gần nhất.
- `GET /api/v1/features/status` — version/độ tươi của ML Feature Store.
- `GET /api/v1/pois/{poi_id}?lat=&lng=` — chi tiết một địa điểm: giờ mở cửa đủ 7
  ngày, trạng thái đang mở, khoảng cách và ETA (chỉ khi truyền `lat`/`lng`),
  đánh giá, địa điểm tương tự và nguồn gốc dữ liệu. Chỉ đọc Postgres, không gọi
  mạng ra ngoài.
- `GET /api/v1/pois/{poi_id}/photos?limit=8` — ảnh của địa điểm, tách riêng vì
  có thể phải gọi Wikimedia Commons (chậm). Xem mục *Ảnh địa điểm* bên dưới.

## Dữ liệu POI thực

Migration mới bổ sung giờ mở cửa, mức giá, tiện ích, tags, thương hiệu,
lineage đa nguồn, H3 ở resolution 7/8/9 và embedding 64 chiều. Các bảng review,
user preference và geofence subscription cũng được quản lý bằng Alembic.

Nhập tối đa 3.000 POI OpenStreetMap trong bbox TP.HCM mặc định:

```powershell
docker compose --env-file config/development.env --profile data run --rm --build osm-import
```

Importer có thể chạy lặp lại: cùng source ID sẽ được cập nhật; địa điểm cùng
category, tên tương tự và cách nhau tối đa 75 m sẽ được gắn về một POI canonical.
Chi tiết schema, giấy phép dữ liệu và cách dùng snapshot nằm tại
[docs/data-model.md](docs/data-model.md).

Sau khi nhập/đổi POI, dựng lại chỉ mục OpenSearch để bật truy xuất đa kênh:

```powershell
docker compose --env-file config/development.env --profile data run --rm --build search-index
```

Nếu bỏ qua bước này (hoặc tắt OpenSearch), API vẫn chạy bình thường nhờ tự
fallback về PostGIS. Chi tiết ở [docs/layer-4-retrieval.md](docs/layer-4-retrieval.md).

> **Bắt buộc dựng lại chỉ mục sau migration `0005`.** Migration này tính lại
> `pois.embedding` theo mô hình `hashing-v2-64` (sửa lỗi vector triệt tiêu về 0).
> Chỉ mục OpenSearch vẫn giữ vector `v1` cho tới khi chạy `search-index`, mà
> vector v1 không so sánh được với vector v2 sinh lúc truy vấn — bỏ qua bước này
> thì kênh Vector ANN trả kết quả sai ở mọi truy vấn.

Kiểm tra hệ thống đang dùng đường nào: `POST /api/v1/search` trả trường
`retrievalBackend` — `opensearch` là truy xuất đa kênh, `postgis` nghĩa là đã rơi
về đường dự phòng (thường do quên dựng chỉ mục).

> **Bắt buộc materialize lại sau migration `0006`.** Migration này tách
> `poi_impression` (đã được hiển thị) khỏi `poi_dwell` (thời gian ở lại), và
> feature view `region_ctr` lên `v2` để giá trị CTR hỏng cũ không lẫn với giá trị
> mới trong Redis. Chưa chạy lại materialize thì mọi vùng đọc ra `ctr = 0.0`.

Kiểm chứng impression đã ghi đúng (tiêu chí hoàn thành của bước A2) — câu này
phải trả về cả dòng **negative**, tức POI đã hiển thị nhưng không được click.
Dùng `EXISTS` chứ không `LEFT JOIN`: người dùng bấm lại đúng POI đang chọn vẫn
sinh thêm một `poi_click`, nên `LEFT JOIN` sẽ nhân đôi dòng positive và làm CTR
tính từ câu này cao hơn thực tế.

```sql
SELECT i.poi_id,
       (i.metadata->>'rank')::int AS rank,
       EXISTS (
           SELECT 1 FROM ingestion_events c
           WHERE c.event_type = 'poi_click'
             AND c.poi_id = i.poi_id
             AND c.metadata->>'request_id' = i.metadata->>'request_id'
       ) AS clicked
FROM ingestion_events i
WHERE i.event_type = 'poi_impression'
  AND i.metadata->>'request_id' IS NOT NULL
ORDER BY i.occurred_at DESC, (i.metadata->>'rank')::int
LIMIT 50;
```

Đồng bộ Knowledge Graph (Neo4j) để bật gợi ý "người có hành vi tương tự cũng thích":

```powershell
docker compose --env-file config/development.env --profile data run --rm --build graph-sync
```

Không có Neo4j thì gợi ý vẫn chạy bằng session-affinity. Chi tiết ở
[docs/layer-2-graph.md](docs/layer-2-graph.md).

Materialize ML Feature Store (user profile, CTR theo vùng, POI embeddings) từ
Postgres sang Redis:

```powershell
docker compose --env-file config/development.env --profile data run --rm --build feature-store
```

Xem độ tươi tại `GET /api/v1/features/status`. Chi tiết ở
[docs/layer-2-features.md](docs/layer-2-features.md).

## Ảnh địa điểm (Wikimedia Commons)

OpenStreetMap gần như không có ảnh: trong 3.000 bản ghi nguồn OSM chỉ có 2 thẻ
`image`, 4 `wikimedia_commons` và 5 `wikidata`. Google Places Photos cần key
tính tiền theo truy vấn và MapTiler Static Maps trả 403, nên nguồn ảnh duy nhất
dùng được là **Wikimedia Commons** — miễn phí, không cần key, giấy phép CC lấy
được qua API.

`GET /api/v1/pois/{poi_id}/photos` trả mỗi ảnh kèm `confidence`:

- `place` — ảnh **của chính địa điểm**, suy từ thẻ OSM. Đếm thật chỉ **5/3010**
  POI có (7 POI mang thẻ ảnh, 2 trong đó trỏ ra ngoài Wikimedia nên bị từ chối).
- `area` — ảnh **chụp quanh đó**, tìm bằng Commons geosearch theo toạ độ. Phủ
  gần hết, nhưng phần lớn là ảnh đường phố/phương tiện/logo chứ không phải ảnh
  của quán. Giao diện **bắt buộc** ghi nhãn `Ảnh khu vực · cách N m`.

Trường `status` phân biệt `empty` (đã dò, thật sự không có ảnh) với
`unavailable` (chưa dò được: mạng, rate limit, hoặc `PHOTOS_ENABLED=false`).
Hai thứ này không được gộp — xem lý do ở
[docs/thesis/b14-poi-detail.md](docs/thesis/b14-poi-detail.md).

**Nghĩa vụ ghi công.** Ảnh Commons phần lớn là CC BY hoặc CC BY-SA, và cả hai
đều bắt buộc ghi tên tác giả. Vì vậy `license`, `attribution` và `sourceUrl` là
trường bắt buộc của hợp đồng API; thiếu giấy phép hoặc tác giả thì ảnh **không
được hiển thị**. Ngoại lệ duy nhất là **CC0 / Public domain**: giấy phép đó
không đòi ghi công, nên ảnh thiếu `attribution` vẫn hiện. Thiếu `license` thì
loại trong mọi trường hợp — không đọc được giấy phép nghĩa là không biết mình
được phép làm gì với tấm ảnh đó.

Commons có rate limit (gọi dồn dập trả HTTP 429): phải giãn ≥ 1 giây giữa hai
lần gọi và gửi User-Agent mô tả rõ. Pre-warm trước khi demo:

```powershell
docker compose -p nearby-dev run --rm --no-deps -v "${PWD}/backend/app:/app/app" -v "${PWD}/backend/scripts:/app/scripts" -v "${PWD}/backend/results:/app/results" backend python scripts/enrich_photos.py --limit 200 --sleep 1.1
```

Lệnh viết trên một dòng vì PowerShell không hiểu dấu `\` nối dòng kiểu bash.
Mount `backend/results` vì script ghi toàn bộ kết quả ra
`results/poi_photos_<ts>.json`, kể cả POI không lấy được ảnh kèm lý do; thiếu
mount thì file đó chết theo container `--rm`, mà phần thất bại mới là phần cần
đọc. Thử `--limit 3 --dry-run` trước — gọi Wikimedia thật nhưng không ghi
database.

## Tầng 1 hiện tại

- Web client thu thập từ khóa, GPS có consent và interaction events.
- NGINX edge router định tuyến, gắn request ID và giới hạn tốc độ.
- Geo-parser hiểu truy vấn như `cà phê gần Bến Thành`.
- Reverse geocoding tìm địa chỉ/POI gần tọa độ.
- PostgreSQL lưu event bền vững; Redis Streams nhận event thời gian thực.
- Stream worker cập nhật session, trending query/POI và Redis GEO.
- Giao diện có bảng điều khiển trạng thái tầng 1 theo thời gian thực.

Chi tiết: [docs/layer-1.md](docs/layer-1.md).

## Tầng 3 hiện tại

- Ranking engine đa tín hiệu tiêu thụ trực tiếp dữ liệu trending mà tầng 1
  đã tính nhưng trước đây không ai dùng.
- Cá nhân hóa cold-start-safe dựa trên lịch sử session, không cần feature
  store hay mô hình ML riêng.
- Filter theo category, đa dạng hóa kết quả theo category.

Chi tiết: [docs/layer-3.md](docs/layer-3.md).

## Phạm vi toàn hệ thống

MVP đã có luồng dọc hoàn chỉnh từ client ingestion đến PostGIS, truy xuất đa
kênh OpenSearch, Spatial Knowledge Graph (Neo4j), ML Feature Store, ranking
engine, API và bản đồ. Redis Streams + Python worker hiện là lát cắt chạy local
tương đương vai trò Kafka/Flink; Feature Store dùng Postgres + Redis thay vì
Feast (xem lý do ở docs). LTR/mô hình neural re-ranking thật sự là bước kế tiếp.

Ánh xạ với kiến trúc 4 tầng:

1. Thu thập: vị trí và từ khóa từ giao diện web.
2. Lưu trữ: PostgreSQL/PostGIS, GiST + GIN, H3, vector, dữ liệu POI đa nguồn,
   OpenSearch (chỉ mục truy xuất), Neo4j (Spatial Knowledge Graph) và ML Feature
   Store (Postgres offline + Redis online).
3. Truy xuất/xếp hạng: OpenSearch đa kênh (BM25/geo/vector) + RRF, fallback
   PostGIS `ST_DWithin`; spatial decay, trending Redis, cá nhân hóa theo session.
4. Trình bày: bản đồ MapLibre với clustering, danh sách xếp hạng, filter
   category, trending, gợi ý cá nhân hóa, dark mode.
