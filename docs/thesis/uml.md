# Sơ đồ UML

Bốn sơ đồ dưới đây được dựng từ code thật, không vẽ thứ chưa tồn tại. Mỗi sơ đồ
ghi rõ nguồn đối chiếu để người đọc kiểm tra được.

Cú pháp Mermaid — GitHub, VS Code và Artifact đều render trực tiếp, không cần cài
thêm công cụ. Để xuất ảnh chèn vào bản Word, dùng https://mermaid.live rồi tải PNG/SVG.

---

## 1. Sơ đồ Use Case

Nguồn: các endpoint trong `backend/app/api.py` và bảng chức năng ở `docs/layer-3.md`.

Chỉ vẽ những chức năng đã có endpoint thật. "Viết đánh giá" được đánh dấu riêng
vì phía tiêu thụ (ranking, feature store, knowledge graph) đã sẵn sàng nhưng
phía phát sự kiện chưa có giao diện — xem bước A3 trong lộ trình.

```mermaid
graph LR
    U(("Người dùng"))
    subgraph HeThong["Hệ thống Nearby"]
        UC1["Tìm kiếm địa điểm<br/>theo từ khoá + vị trí"]
        UC2["Xem địa điểm gần đây"]
        UC3["Lọc theo loại địa điểm"]
        UC4["Xem địa điểm đang thịnh hành"]
        UC5["Nhận gợi ý cá nhân hoá"]
        UC6["Bắt đầu chỉ đường"]
        UC7["Viết đánh giá<br/>(chưa có giao diện)"]
        UC8["Hiểu địa danh trong câu<br/>'gần Bến Thành'"]
        UC9["Ghi nhận tương tác"]
    end
    OSM[("OpenStreetMap<br/>Overpass API")]

    U --> UC1
    U --> UC2
    U --> UC3
    U --> UC4
    U --> UC5
    U --> UC6
    U -.-> UC7
    UC1 -. include .-> UC8
    UC1 -. include .-> UC9
    UC2 -. include .-> UC9
    UC5 -. extend .-> UC1
    UC1 --> OSM

    classDef chuaCo stroke-dasharray: 5 5,fill:#fff4e6
    class UC7 chuaCo
```

| Use case | Endpoint | Vị trí |
|---|---|---|
| Tìm kiếm địa điểm | `POST /api/v1/search` | `api.py` — `contextual_search` |
| Xem địa điểm gần đây | `GET /api/pois/nearby` | `api.py` — `nearby_pois` |
| Lọc theo loại địa điểm | `GET /api/v1/categories` | `api.py` — `list_categories` |
| Xem thịnh hành | `GET /api/v1/trending` | `api.py` — `trending` |
| Gợi ý cá nhân hoá | `GET /api/v1/recommendations` | `api.py` — `recommendations` |
| Ghi nhận tương tác | `POST /api/v1/events/batch` | `api.py` — nhận `EventBatch` |
| Viết đánh giá | *chưa có* | loại `review` đã được `models.py` chấp nhận |

---

## 2. Sơ đồ tuần tự — `POST /api/v1/search`

Nguồn: `docs/layer-1.md`, `backend/app/api.py`, `backend/app/search/retrieval.py`,
`backend/app/spatio_temporal.py`, `backend/app/ranking.py`.

Điểm đáng chú ý về mặt thiết kế: nhánh `alt` ở giữa là circuit breaker thật
(`backend/app/search/client.py`). Khi OpenSearch không khả dụng, hệ thống rơi về
PostGIS và **vẫn trả kết quả**, chỉ đổi trường `retrievalBackend` trong phản hồi.

```mermaid
sequenceDiagram
    autonumber
    actor Client as Trình duyệt
    participant NGINX as NGINX Gateway
    participant API as FastAPI
    participant GAZ as Gazetteer<br/>(PostGIS)
    participant OS as OpenSearch
    participant PG as PostGIS
    participant ENR as Spatio-Temporal<br/>Enricher
    participant RANK as Ranking Engine

    Client->>NGINX: POST /api/v1/search
    NGINX->>NGINX: gắn X-Request-ID, rate limit
    NGINX->>API: chuyển tiếp
    API->>GAZ: parse_location("cà phê gần Bến Thành")
    GAZ-->>API: subject="cà phê", tâm=(10.772, 106.698)

    alt OpenSearch khả dụng
        API->>OS: BM25 (không dấu + fuzzy)
        API->>OS: geo_distance
        API->>OS: k-NN trên embedding
        OS-->>API: 3 danh sách ID
        API->>API: Reciprocal Rank Fusion (k=60)
        API->>PG: hydrate_candidates(ids)
        PG-->>API: POI đầy đủ thuộc tính
    else OpenSearch hỏng hoặc chưa dựng chỉ mục
        API->>PG: pg_trgm + ST_DWithin
        PG-->>API: ứng viên (retrievalBackend="postgis")
    end

    API->>ENR: enrich_candidates(ứng viên)
    ENR->>PG: đếm tương tác cửa sổ 15p/1h/24h
    ENR-->>API: openNow, contextScore, recencyScore, ETA
    API->>RANK: rerank(9 tín hiệu có trọng số)
    RANK->>RANK: diversify (tối đa 2 cùng loại liên tiếp)
    RANK-->>API: danh sách đã xếp hạng
    API->>API: gán rank 0..n-1
    API-->>NGINX: JSON + requestId + retrievalBackend
    NGINX-->>Client: 200 OK
    Client->>API: POST /api/v1/events/batch<br/>(lô impression cho toàn bộ kết quả)
```

---

## 3. Sơ đồ ERD

Nguồn: `0001_initial_schema.py` và `0003_enrich_poi_model.py`. Chỉ liệt kê cột
tiêu biểu; số cột đầy đủ ghi trong ngoặc.

Lưu ý về quan hệ: `ingestion_events.poi_id` là `TEXT` và **không có khoá ngoại**
tới `pois` — cố ý, vì log sự kiện phải nhận được cả POI đã bị xoá và cả id rác từ
client mà không làm hỏng luồng ghi. Đổi lại, mọi truy vấn thống kê đều phải
`JOIN pois` và chấp nhận việc bỏ qua âm thầm các id không khớp.

```mermaid
erDiagram
    pois ||--o{ poi_source_records : "gộp từ nhiều nguồn"
    pois ||--o{ poi_reviews : "được đánh giá"
    pois ||--o{ pois : "canonical_id (gộp trùng)"
    search_sessions ||--o{ ingestion_events : "phát sinh"
    poi_import_runs ||--o{ poi_source_records : "sinh ra trong lần nhập"

    pois {
        UUID id PK
        TEXT name
        TEXT category
        GEOGRAPHY location "POINT 4326, GiST"
        NUMERIC rating
        INTEGER review_count
        JSONB opening_hours
        TEXT district
        TEXT h3_r7 "và h3_r8, h3_r9"
        REAL_ARRAY embedding "64 chiều"
        TEXT embedding_model
        UUID canonical_id FK
        TEXT dedupe_fingerprint
    }

    ingestion_events {
        UUID id PK
        TEXT event_type "7 loại, có CHECK"
        UUID session_id
        TIMESTAMPTZ occurred_at
        TEXT poi_id "không có FK, cố ý"
        INTEGER dwell_ms
        JSONB metadata "request_id, query, rank"
        TEXT processing_status
    }

    poi_reviews {
        UUID id PK
        UUID poi_id FK
        SMALLINT rating "1..5"
        TEXT body
        TEXT source "human hoặc synthetic"
        INTEGER helpful_count
    }

    poi_source_records {
        BIGSERIAL id PK
        UUID canonical_poi_id FK
        TEXT source "osm, seed"
        TEXT source_id
        JSONB raw_payload
    }

    search_sessions {
        UUID session_id PK
        TIMESTAMPTZ first_seen_at
        GEOGRAPHY last_location
        INTEGER event_count
        INTEGER search_count
    }

    geo_aliases {
        BIGSERIAL id PK
        TEXT alias "không dấu"
        TEXT canonical_name
        GEOGRAPHY location
        SMALLINT priority
    }

    user_preferences {
        TEXT user_id PK
        TEXT_ARRAY preferred_categories
        GEOGRAPHY home_location
        INTEGER max_distance_meters
    }

    geofence_subscriptions {
        UUID id PK
        GEOGRAPHY center
        INTEGER radius_meters
        BOOLEAN is_active
    }

    poi_import_runs {
        UUID id PK
        TEXT source
        TEXT status
        INTEGER inserted_count
        INTEGER deduplicated_count
    }
```

---

## 4. Sơ đồ lớp — gói `backend/app/search/`

Nguồn: các module thật trong `backend/app/search/`. Gói này viết theo kiểu
module-hàm chứ không phải lớp, nên sơ đồ dùng `<<module>>` để phản ánh đúng cấu
trúc code thay vì bịa ra các lớp không tồn tại.

```mermaid
classDiagram
    direction LR

    class client {
        <<module>>
        +search_backend() str
        +is_search_configured() bool
        +get_client() Any
        +ping(client) bool
        +search_available() bool
        +reset_client_cache()
        -_breaker_open_until float
    }

    class query {
        <<module>>
        +bm25_body(...) dict
        +geo_body(...) dict
        +vector_body(embedding, ...) dict
        +extract_ranked_ids(response) list
        -_geo_filter(lat, lng, radius)
        -_category_filter(category)
    }

    class fusion {
        <<module>>
        +reciprocal_rank_fusion(channels, k) list
        +fused_channels(channels, limit) list
    }

    class retrieval {
        <<module>>
        +multi_channel_candidates(...) list
        -_search_ids(client, body)
        -_trending_ids(limit)
    }

    class enrichment {
        <<module>>
        +hydrate_candidates(ids, ...) list
        -_finalize(row)
    }

    class index {
        <<module>>
        +index_settings() dict
        +index_mappings() dict
        +ensure_index(client) bool
        +build_document(row) dict
        +bulk_actions(rows) list
        +index_document(client, row)
        +delete_document(client, poi_id)
        -_coerce_embedding(row)
    }

    class reindex {
        <<module>>
        +reindex(recreate, batch_size) int
        +main()
        -_iter_pois(database_url, batch_size)
    }

    retrieval ..> client : lấy client, kiểm tra breaker
    retrieval ..> query : dựng 3 truy vấn
    retrieval ..> fusion : gộp bằng RRF
    retrieval ..> enrichment : nạp thuộc tính từ PostGIS
    reindex ..> index : dựng document + bulk
    reindex ..> client : ghi vào OpenSearch
    index ..> query : dùng chung định nghĩa trường
```

**Luồng đọc sơ đồ**: `retrieval` là điểm vào. Nó hỏi `client` xem OpenSearch có
dùng được không (circuit breaker), nhờ `query` dựng ba thân truy vấn, gọi
OpenSearch ba lần, đưa ba danh sách ID cho `fusion` gộp lại bằng Reciprocal Rank
Fusion, rồi nhờ `enrichment` nạp thuộc tính đầy đủ từ PostGIS. Nhánh ghi
(`reindex` → `index`) chạy độc lập, thường là một job riêng.
