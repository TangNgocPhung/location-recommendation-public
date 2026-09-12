# ML Feature Store — Tầng 2

Khối "ML Feature Store" trong sơ đồ được hiện thực **gọn nhẹ bằng Postgres +
Redis** thay vì cài Feast/Hopsworks: với một mô hình chạy trên laptop, Feast
thêm registry + job materialize + dependency mà không cải thiện chất lượng gợi
ý. Bản gọn nhẹ vẫn thể hiện đầy đủ ba tính chất cốt lõi của feature store.

| Tính chất | Cách hiện thực |
|---|---|
| **Offline store** (huấn luyện) | Postgres — `app/features/offline.py` tính từ `ingestion_events` + `pois` |
| **Online store** (serving độ trễ thấp) | Redis hash, khóa `feat:<version>:<view>:<entity>` |
| **Chống training/serving skew** | `app/features/registry.py` — định nghĩa feature **có version**, cả hai phía cùng tham chiếu |

Mã nguồn: [`backend/app/features/`](../backend/app/features/).

## Ba feature theo sơ đồ

| Feature view | Entity | Nội dung |
|---|---|---|
| `user_profile` v1 | `session_id` | **User Profile Vector**: `affinity` (category → trọng số chuẩn hóa), `top_category`, `pref_price_level`, `event_count` |
| `region_ctr` v2 | `region` = `"<quận>\|<category>"` | **Historical CTR theo Vùng**: `clicks`, `impressions`, `ctr` |
| `poi_embedding` `hashing-v2-64` | `poi_id` | **POI Embeddings** (đã tính lúc ingest, phơi ra dưới đúng version) |

Version của `poi_embedding` **trùng** `poi_features.EMBEDDING_MODEL` — có unit
test khóa ràng buộc này để embedding lúc train và lúc serve không lệch.

## CTR được làm mượt

CTR thô dễ sai khi ít dữ liệu (1 click / 1 impression → 100%). Ta dùng prior
Beta(1, 9) ≈ CTR nền 0.1:

```
ctr = (clicks + 1) / (impressions + 1 + 9)
```

> **Số liệu cũ đã bị gỡ (bước A2).** Con số "48 impression / 36 click →
> `ctr = 0.833`" từng được ghi ở đây như bằng chứng công thức chạy đúng. Thực ra
> nó là bằng chứng của một lỗi: tỉ lệ 75% bất khả thi với impression đúng nghĩa,
> và nó cao như vậy vì `poi_impression` khi đó chỉ được bắn khi người dùng RỜI
> một POI đã click — tức impression là tập con của click. Phải đo lại sau khi
> chạy migration `0006` rồi materialize lại feature store.

Hai lớp bảo vệ đã thêm cùng bước A2:

- `smoothed_ctr` kẹp trần 1.0. Công thức thô vượt 1.0 khi `clicks >= impressions + 10`
  (40 click / 0 impression cho 4.1), và giá trị đó được nhân trọng số 0.08 rồi
  cộng thẳng vào điểm xếp hạng.
- `_REGION_CTR_SQL` chỉ đếm sự kiện từ impression THẬT đầu tiên trở đi (nhận
  diện qua `metadata.request_id`). Không có mốc cắt này thì tử số là click lịch
  sử còn mẫu số là impression mới — hai thang đo khác nhau.

## Materialize

Job đẩy feature offline → online (`feature-store`, profile `data`):

```powershell
docker compose --env-file config/development.env --profile data run --rm --build feature-store
```

Chạy trực tiếp: `python -m app.features.materialize [--views user_profile,region_ctr]`.
Feature online có TTL 7 ngày để tự hết hạn nếu ngừng materialize.

## Dùng lúc serving

- `ranking.rank_pois` gọi `attach_region_ctr` → mỗi candidate có `regionCtr`,
  vào công thức xếp hạng với trọng số `w_ctr = 0.08`.
- `/api/v1/recommendations` đọc `user_profile` từ online store
  (`profileSource: "feature-store"`); nếu phiên **chưa** materialize thì tự tính
  trực tiếp từ Postgres (`profileSource: "postgres"`) — đã kiểm chứng.
- `/api/v1/features/status` trả version, entity, số bản ghi và `materializedAt`
  của từng view để theo dõi độ tươi.

## Bước sau

Các feature này là đầu vào trực tiếp cho **LTR** ở Tầng 3: tập huấn luyện sẽ
đọc từ offline store, mô hình lúc serving đọc từ online store dưới **cùng tên và
cùng version** — đúng lý do feature store tồn tại.
