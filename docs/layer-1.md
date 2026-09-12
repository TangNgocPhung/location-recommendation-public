# Tầng 1 — Client, định vị và ingestion

Tầng 1 nhận dữ liệu từ Web App, chuẩn hóa ngữ cảnh vị trí và đưa interaction
events vào một luồng xử lý có thể phục hồi. Mục tiêu của bản local là giữ đúng
hợp đồng dữ liệu của kiến trúc production nhưng đủ nhẹ để chạy trên một máy.

## Luồng đồng bộ: tìm kiếm theo ngữ cảnh

1. Web App tạo một `session_id` UUID và lưu trong `localStorage`.
2. Người dùng nhập từ khóa và, nếu đồng ý, cấp GPS qua Geolocation API.
3. NGINX nhận request, gắn `X-Request-ID`, giới hạn tốc độ và chuyển `/api/*`
   đến FastAPI.
4. Geo-parser tách chủ đề và địa danh, ví dụ:
   `cà phê gần Bến Thành` → chủ đề `cà phê`, địa danh `Bến Thành`.
5. Gazetteer PostGIS + `pg_trgm` tìm tọa độ phù hợp và confidence score.
6. Search service dùng tọa độ đã parse hoặc GPS làm tâm truy vấn PostGIS.
7. Response trả cả `requestId`, `searchCenter`, `parsedLocation` và danh sách POI.

API chính:

- `POST /api/v1/search`: tìm kiếm có geo-parsing.
- `POST /api/v1/geocode/parse`: phân tích địa danh trong văn bản.
- `GET /api/v1/geocode/reverse`: tọa độ → địa chỉ/POI gần nhất.
- `GET /api/pois/nearby`: API tương thích với MVP cũ.

## Luồng bất đồng bộ: interaction events

Client gom tối đa 50 events mỗi batch và gửi đến
`POST /api/v1/events/batch`. Các loại event hiện có:

- `search`: từ khóa, bán kính và nguồn giao diện.
- `location_ping`: GPS + độ chính xác; bắt buộc `location_consent=true`.
- `poi_impression`: POI đã được HIỂN THỊ trong kết quả tìm kiếm; một lô cho toàn
  bộ kết quả sau mỗi lần tìm, `metadata = {request_id, query, rank}`.
- `poi_dwell`: thời gian ở lại một POI đã click (`dwell_ms`).
- `poi_click`: POI được chọn từ danh sách hoặc bản đồ.
- `navigation_start`: bắt đầu chỉ đường.
- `review`: rating 1–5.

Mỗi batch đi qua hai bước:

1. Ghi bền vững vào `ingestion_events` trong PostgreSQL. Event ID là khóa
   idempotency nên gửi lại không tạo bản ghi trùng.
2. Đưa event mới vào Redis Stream `nearby:events:v1`. Nếu Redis tạm thời lỗi,
   event giữ trạng thái `pending`; worker sẽ tự đưa lại vào stream.

`stream-worker` dùng consumer group `realtime-scoring`, cập nhật:

- session gần nhất trong Redis và bảng `search_sessions`;
- vị trí session trong Redis GEO;
- trending query và trending POI trong Redis Sorted Sets;
- trạng thái event từ `queued` sang `processed`.

Theo dõi bằng `GET /api/v1/ingestion/status?session_id=<uuid>`.

## Quyền riêng tư và an toàn

- Client chỉ gửi GPS sau khi trình duyệt cấp quyền.
- Event có tọa độ bị API từ chối nếu thiếu `location_consent=true`.
- Payload giới hạn 50 events và 256 KB tại NGINX.
- Session header phải là UUID.
- NGINX và FastAPI đều có rate limit; response có request ID để truy vết.
- Không ghi token, cookie hay nội dung nhạy cảm vào metadata.

## Ánh xạ production

| Bản local | Khi mở rộng production |
|---|---|
| NGINX edge router | Kong/NGINX Ingress/API Gateway có TLS và distributed rate limit |
| Gazetteer PostGIS + pg_trgm | Pelias/Photon/Nominatim hoặc nhà cung cấp geocoding |
| Redis Streams | Kafka/Pulsar với schema registry và nhiều partition |
| Python stream worker | Flink/Kafka Streams với event-time watermark |
| PostgreSQL event log | Data lake/warehouse + retention policy |

Redis Streams được dùng làm Kafka-compatible design slice về mặt hợp đồng event,
consumer group, retry và trạng thái xử lý. Khi thay bằng Kafka, client và API
contract không cần đổi.
