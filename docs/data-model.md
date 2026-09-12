# Mô hình dữ liệu POI — Bước 2

Migration `0003_enrich_poi_model` mở rộng một POI từ bản ghi hiển thị cơ bản
thành thực thể có thể hợp nhất từ nhiều nguồn.

## Trường chính

- Giờ hoạt động: `opening_hours` giữ cả chuỗi nguồn và cấu trúc đã parse,
  `timezone`, `open_now` là cache; API tính lại `openNow` tại thời điểm trả kết quả.
- Thuộc tính: `price_level` dùng thang `0..4` (`0` là chưa rõ), `amenities`
  là JSONB, `tags` là mảng có GIN index, cùng `brand`, `district`, `city`.
- Nguồn gốc: `source`, `source_id`, `canonical_id`, `updated_at` và bảng
  `poi_source_records` lưu lineage/raw payload của từng nguồn.
- Không gian: `h3_r7`, `h3_r8`, `h3_r9` hỗ trợ gom cụm ở ba mức; PostGIS
  vẫn là nguồn sự thật cho tọa độ và truy vấn khoảng cách chính xác.
- Vector: `embedding REAL[64]` là hashing embedding tất định từ tên, mô tả,
  tags; `embedding_model` ghi phiên bản để có thể tái tạo/nâng cấp.

Các bảng `poi_reviews`, `user_preferences`, `geofence_subscriptions` đã có
ràng buộc, khóa ngoại và spatial index cần thiết. `poi_import_runs` lưu kết quả
mỗi lần nhập dữ liệu để theo dõi và chạy lại an toàn.

## Chuẩn hóa và loại trùng

Importer thực hiện theo thứ tự:

1. Chuẩn hóa Unicode, bỏ dấu phục vụ so khớp, chuẩn hóa category và thuộc tính OSM.
2. Nhận diện lại chính nguồn bằng `(source, source_type, source_id)`.
3. Nếu là nguồn mới, tìm POI cùng category trong bán kính 75 m và tên giống
   ít nhất `0.84`; bản ghi nguồn được gắn vào POI canonical thay vì tạo bản sao.
4. Khi chạy lại, thuộc tính mới được merge, lineage và `last_seen_at` được cập nhật.

## Nhập dữ liệu OpenStreetMap

Phạm vi mặc định `10.70,106.60,10.90,106.82` bao phủ nhiều quận TP.HCM và
lọc các nhóm ăn uống, y tế, giáo dục, mua sắm, văn hóa, công viên, thể thao,
lưu trú. Chạy:

```powershell
docker compose --env-file config/development.env --profile data run --rm --build osm-import
```

Giới hạn có thể đổi trong `config/development.env` bằng `OSM_MAX_POIS` và
`OSM_BBOX`. Để nhập từ snapshot đã tải sẵn:

```powershell
docker compose --env-file config/development.env --profile data run --rm osm-import `
  python -m scripts.import_osm_pois --input /data/osm.json --max-pois 3000
```

Dữ liệu lấy từ OpenStreetMap qua Overpass API [12]. Giấy phép là ODbL v1.0 [13]
— một giấy phép **share-alike**, nghĩa là hai nghĩa vụ chứ không phải một:

1. **Ghi công.** Bản đồ trong giao diện đã hiển thị attribution
   (`frontend/components/location-explorer.tsx`, nguồn raster OpenStreetMap).
2. **Chia sẻ lại.** Nếu phân phối cơ sở dữ liệu phái sinh, phải phát hành dưới
   cùng giấy phép. Nghĩa vụ này chỉ phát sinh khi phân phối; dùng nội bộ cho đồ
   án thì chưa, nhưng phải nêu rõ trong báo cáo.

Chi tiết nguồn dẫn ở [docs/thesis/references.md](thesis/references.md).

Có thể chạy lại chuẩn hóa tên quận từ raw lineage mà không gọi mạng:

```powershell
docker compose --env-file config/development.env --profile data run --rm --build osm-import `
  python -m scripts.import_osm_pois --refresh-existing
```

## Kiểm tra độ phủ

`GET /api/v1/data/status` trả tổng POI, số POI từ OpenStreetMap, số quận và
độ phủ H3/embedding/opening hours, cùng kết quả import gần nhất. API tìm kiếm
không trả vector thô; nó chỉ trả `embeddingModel` và các thuộc tính hiển thị.
