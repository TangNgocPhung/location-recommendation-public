# B14 — Trang chi tiết địa điểm: hai endpoint, và bài toán ảnh không có lời giải đẹp

**Ngày:** 2026-09-12 · Tiếp nối [B3 · B6 · B12 · B13](b3-b6-b12-b13.md)

## Tóm tắt một đoạn

Ô **"Tầng 4 – hiển thị chi tiết địa điểm"** trong sơ đồ kiến trúc trước bước này
được lấp bằng một thẻ overlay năm dòng, và backend **chưa hề có endpoint chi
tiết**. B14 thêm hai endpoint: một cái đọc thuần Postgres và phải nhanh, một cái
đi ra Wikimedia Commons và có thể chậm. Phần khó của bước này không phải code mà
là **ảnh**: dữ liệu OSM gần như không có ảnh, mọi nguồn ảnh còn lại đều vướng
tiền hoặc vướng giấy phép, và nguồn duy nhất dùng được trả về **ảnh đúng khu
vực nhưng thường không phải ảnh của quán**. Toàn bộ thiết kế hai mức
`confidence` trong tài liệu này sinh ra từ chỗ đó.

---

## 1. Vì sao bước này

Cái đang chạy dưới ô "chi tiết địa điểm" là một thẻ overlay trong
[`frontend/components/location-explorer.tsx`](../../frontend/components/location-explorer.tsx):
tên, loại, địa chỉ, khoảng cách, một nút chỉ đường. Năm dòng — và **cả năm đều
đã nằm sẵn trong kết quả tìm kiếm**. Bấm vào một POI không lấy thêm một byte nào
từ máy chủ.

Lý do rất đơn giản: liệt kê route trong [`backend/app/api.py`](../../backend/app/api.py)
trước bước này có 17 endpoint, **không có cái nào là `GET /api/v1/pois/{id}`**.

Hệ quả cần nói thẳng trong báo cáo: mọi thứ database đã lưu cho từng địa điểm —
giờ mở cửa theo thứ trong tuần, website, điện thoại, thương hiệu, tiện ích, H3 ở
ba độ phân giải, nguồn dữ liệu và thời điểm cập nhật, đánh giá và bình luận —
**chưa từng có đường ra giao diện**. Schema thiết kế cho chúng đã tồn tại từ
migration 0003; phần hiển thị thì không.

---

## 2. Hợp đồng hai endpoint

### A) `GET /api/v1/pois/{poi_id}?lat=<float>&lng=<float>`

Chỉ đọc Postgres, **không gọi mạng ra ngoài**. `lat`/`lng` tuỳ chọn; thiếu thì
`distanceMeters` và `etaMinutes` trả `null` chứ không trả 0.

```jsonc
{
  "id", "name", "description", "category", "categoryLabel", "address",
  "district"|null, "city", "countryCode", "latitude", "longitude", "brand"|null,
  "website"|null, "phone"|null,
  "rating": number|null, "reviewCount": int, "ratingSource": string|null,
  "popularityScore": number, "priceLevel": 0..4,
  "tags": string[], "amenities": object, "sponsored": bool,
  "timezone": "Asia/Ho_Chi_Minh",
  "openingHours": { "raw", "parseStatus", "periods", "alwaysOpen" },
  "openingStatus": { "openNow": bool|null, "closesInMinutes": int|null,
                     "opensInMinutes": int|null },
  "weekHours": [ { "weekday": 0..6, "label": "Thứ Hai".."Chủ Nhật",
                   "isToday": bool, "closed": bool, "unknown": bool,
                   "intervals": [ { "opens": "06:00", "closes": "22:00" } ] } ],
  "distanceMeters": number|null,
  "etaMinutes": { "walk": int, "motorbike": int, "car": int }|null,
  "popularityWindows": { "w15": int, "w1h": int, "w24h": int },
  "reviewSummary": { "count": int, "average": number|null,
                     "histogram": { "1": int, "2": int, "3": int, "4": int, "5": int } },
  "reviews": [ { "id", "authorName"|null, "rating", "title"|null, "body",
                 "language", "source", "helpfulCount", "createdAt" } ],
  "similar": [ { "id", "name", "categoryLabel", "address", "rating"|null,
                 "reviewCount", "distanceMeters", "latitude", "longitude",
                 "reason" } ],
  "provenance": { "source", "sourceId"|null, "updatedAt",
                  "h3": { "r7", "r8", "r9" }, "embeddingModel"|null }
}
```

Lỗi:

| Mã | Thân |
|---|---|
| 400 | `{"detail": "poi_id phải là UUID"}` — kiểm bằng `app.geofence.is_uuid` |
| 404 | `{"detail": "Không có địa điểm này"}` |

Kiểm UUID **trước khi chạm database** không phải để đẹp mã lỗi: `poi_id` đi
thẳng vào một tham số truy vấn kiểu `uuid`, nên một chuỗi rác sẽ làm psycopg ném
`DataError` và FastAPI trả 500. Một 500 ở đây nói "máy chủ hỏng" trong khi sự
thật là "người dùng gõ sai đường dẫn".

### B) `GET /api/v1/pois/{poi_id}/photos?limit=8`

```jsonc
{
  "poiId": "...",
  "status": "ready" | "empty" | "unavailable",
  "fetchedAt": "ISO8601"|null,
  "photos": [ {
      "id", "url", "thumbUrl", "width": int|null, "height": int|null,
      "title": "tên file trên Commons",
      "confidence": "place" | "area",
      "distanceMeters": number|null,
      "source": "wikimedia",
      "sourceUrl": "https://commons.wikimedia.org/wiki/File:...",
      // `license` không bao giờ null: thiếu giấy phép thì ảnh bị loại từ tầng
      // dò, không ra tới đây (mục 4.5). `attribution` null chỉ với CC0/PD.
      "license": "CC BY-SA 4.0",
      "attribution": "tên tác giả"|null
  } ]
}
```

### Vì sao tách làm hai endpoint

Endpoint A đọc Postgres và phải nhanh; endpoint B **có thể** đi ra Wikimedia
Commons, mà Commons bắt buộc giãn ≥ 1 giây giữa hai lần gọi (xem mục 4). Gộp
chung thì toàn bộ trang chi tiết bị giữ lại theo nhịp của phần chậm nhất, và một
lần Commons hỏng sẽ làm **trang chi tiết trắng** thay vì chỉ thiếu ảnh.

Giao diện gọi **song song** hai endpoint: phần chữ hiện ngay, khung ảnh tự lấp
sau. Đây cũng là lý do trạng thái ảnh nằm ở trường `status` của B chứ không phải
suy ra từ việc mảng `photos` rỗng.

### Ba trạng thái, và vì sao không được gộp

| `status` | Nghĩa | Giao diện nói gì |
|---|---|---|
| `ready` | Đã dò, có ảnh | hiện ảnh |
| `empty` | **Đã dò xong, thật sự không có ảnh nào** | "Chưa có ảnh cho địa điểm này" |
| `unavailable` | **Chưa dò được** — mạng hỏng, rate limit, hoặc `photos_enabled = false` | "Chưa tải được ảnh" |

Đây đúng là quy tắc `NULL ≠ 0` mà migration `0008_nullable_rating` đã phải dọn
một lần cho `rating`: *"không có ảnh"* và *"chưa lấy được ảnh"* là hai sự thật
khác nhau. Gộp chúng lại thì một lần tắt cấu hình sẽ được báo cáo như một phát
hiện về dữ liệu.

### `weekHours` — 7 phần tử, và `closed` khác `unknown`

`weekHours` luôn có **đúng 7 phần tử, Thứ Hai trước**, kể cả khi POI không có
`opening_hours`. Lý do là giao diện: một mảng dài thay đổi theo dữ liệu sẽ làm
bảng giờ mở cửa nhảy số dòng giữa các POI.

Hai cờ tách bạch, không được để một cờ gánh cả hai việc:

- `closed: true` — **biết** rằng hôm đó đóng cửa (`opening_hours` có ghi).
- `unknown: true` — **không biết**, vì POI không có dữ liệu giờ mở cửa.

Với độ phủ `opening_hours` chỉ 17% (mục 3), phần lớn POI sẽ là `unknown` ở cả 7
ngày. Nếu hiển thị chúng như "đóng cửa" thì đồ án đang khẳng định 83% địa điểm
của TP.HCM đóng cửa cả tuần.

---

## 3. Độ phủ dữ liệu thật

Đếm trực tiếp trên `poi_source_records.raw_payload->'tags'` — thẻ OSM gốc, giữ
nguyên văn — của **3.000 bản ghi nguồn OpenStreetMap**:

| Thẻ OSM | Số bản ghi | Tỉ lệ | Đi vào trường nào |
|---|---:|---:|---|
| `brand` | 821 | 27,4% | `brand` |
| `opening_hours` | 509 | 17,0% | `openingHours`, `openingStatus`, `weekHours` |
| `phone` | 375 | 12,5% | `phone` |
| `website` | 300 | 10,0% | `website` |
| `wikidata` | 5 | 0,17% | ảnh `confidence: "place"` (qua P18) |
| `wikimedia_commons` | 4 | 0,13% | ảnh `confidence: "place"` |
| `contact:phone` | 4 | 0,13% | `phone` (dự phòng) |
| `image` | **2** | **0,07%** | ảnh `confidence: "place"` |
| `contact:website` | 1 | 0,03% | `website` (dự phòng) |

Thêm hai con số nữa của toàn bảng `pois` (3.010 dòng):

- **28 POI có `rating`** — toàn bộ là seed demo, gõ tay trong migration 0002.
- **2.982 POI có `rating_source` NULL**, tức 99,07% địa điểm trong hệ thống
  **chưa ai đánh giá**.

Ba điều bảng này quyết định, và cả ba đều nên đưa vào báo cáo:

1. **Trang chi tiết phải chịu được trường trống ở mọi ô.** Trường hợp phổ biến
   nhất của một POI trong database này là: không giờ mở cửa, không điện thoại,
   không website, không đánh giá, không ảnh. Đó là mặc định, không phải ngoại lệ.
2. **`reviewCount = 0` phải hiển thị là "chưa có đánh giá"**, không phải 0 sao.
3. **Ảnh `confidence: "place"` chỉ có ở 5 POI.** Cộng dồn số THẺ (2 `image` +
   4 `wikimedia_commons` + 5 `wikidata`) ra 11, nhưng đó là cận trên vì giả
   định ba tập không giao nhau. Đếm lại theo POI trên chính database này:

   ```sql
   SELECT count(*) FILTER (WHERE tags ?| ARRAY['image','wikimedia_commons','wikidata'])
   FROM (SELECT DISTINCT ON (canonical_poi_id) canonical_poi_id,
                raw_payload->'tags' AS tags
         FROM poi_source_records WHERE raw_payload ? 'tags'
         ORDER BY canonical_poi_id, last_seen_at DESC) s;
   -- 7
   ```

   **7 POI** mang ít nhất một thẻ ảnh — bốn trường đại học/khách sạn mang cùng
   lúc `wikimedia_commons` và `wikidata`, nên phép cộng đếm trùng. Trong 7 POI
   đó, 2 POI (`Inspirée Vintage`, `Lẩu cá đuối Hai Béo`) có thẻ `image` trỏ tới
   `sites.google.com` và `encrypted-tbn0.gstatic.com`, bị `_ALLOWED_IMAGE_HOSTS`
   từ chối vì thẻ OSM ai cũng sửa được và nhận URL tuỳ ý là giao quyền quyết
   định nội dung trang cho người lạ.

   **Còn lại 5 POI, tức 0,17%**, thật sự hiển thị được ảnh `place`.

> **Một chênh lệch nhỏ cần nói rõ để không bị hỏi vặn.** Kiểm toán Phase 3 đếm
> trên bảng `pois` được 505 POI có `opening_hours`; bảng trên đếm trên
> `poi_source_records` được 509. Không mâu thuẫn: một POI canonical có thể gộp
> nhiều bản ghi nguồn (importer gắn các địa điểm cùng loại, tên tương tự, cách
> nhau ≤ 75 m về một POI). Đếm trên `poi_source_records` là **đếm bản ghi
> nguồn**; đếm trên `pois` là **đếm địa điểm**.

---

## 4. Vấn đề ảnh

Đây là phần dài nhất của tài liệu, vì nó là phần sẽ bị hỏi.

### 4.1 Bốn nguồn đã cân nhắc

| Nguồn | Kết luận | Lý do |
|---|---|---|
| **Thẻ ảnh trong OSM** | Không đủ | 2 thẻ `image` trên 3.000 bản ghi. OpenStreetMap là cơ sở dữ liệu **bản đồ**, không phải kho ảnh — thiếu ảnh là đúng bản chất của nó, không phải lỗi import. |
| **Google Places Photos** | Không dùng | Cần API key gắn tài khoản thanh toán, tính phí **theo từng truy vấn** — 3.010 POI là 3.010 lượt tính tiền. Hệ thống hiện **không có `GOOGLE_MAPS_API_KEY`**. Thêm nữa, điều khoản của Google giới hạn việc lưu trữ lâu dài nội dung Places, nên không thể cache ảnh vào database của đồ án (lý do này đã ghi trong [`app/poi_ratings.py`](../../backend/app/poi_ratings.py)). |
| **MapTiler Static Maps** | Không dùng được | Key MapTiler của đồ án có sẵn (đang dùng cho nền bản đồ), nhưng gọi Static Maps API trả **HTTP 403** — gói hiện tại không mở API đó. Và kể cả có chạy, ảnh vệ tinh/ảnh bản đồ **không phải ảnh của địa điểm**; đưa nó vào khung ảnh là nói dối bằng hình. |
| **Wikimedia Commons** | **Chọn** | Miễn phí, không cần key, không cần tài khoản thanh toán. Giấy phép CC ghi rõ từng file và **lấy được qua API**, nên ghi công đúng luật được. Có `geosearch` theo toạ độ — thứ mà ba nguồn trên không có ở dạng miễn phí. |

### 4.2 Ràng buộc kỹ thuật của Commons

Gọi được từ trong container backend (đã thử, HTTP 200), **nhưng có rate limit**:
gọi dồn dập trả **HTTP 429**. Hai điều kiện bắt buộc:

- Giãn **≥ 1 giây** giữa hai lần gọi.
- Gửi `User-Agent` mô tả rõ, đúng chuỗi:
  `NearbyPOI/0.1 (https://github.com/TangNgocPhung/location-recommendation)`

Chuỗi này **không chứa email cá nhân** và không được thêm vào. Chính sách của
Wikimedia khuyến nghị một cách liên hệ trong User-Agent, và URL kho mã đáp ứng
được yêu cầu đó mà không phát tán địa chỉ riêng của một sinh viên ra mọi access
log trên đường đi.

Giới hạn 1 giây/lần gọi là lý do endpoint B **phải tách khỏi A** và là lý do có
script pre-warm (mục 6): dò ảnh cho 3.010 POI theo đúng nhịp này mất khoảng 50
phút — không thể làm trong một request.

### 4.3 Kết quả đo thật: geosearch tìm đúng chỗ, sai vật

Thử `geosearch` trên **25 POI ngẫu nhiên**:

| Chỉ số | Kết quả |
|---|---|
| POI có ít nhất một ảnh trong bán kính | **24/25** |
| Khoảng cách điển hình | **120–300 m** |
| Ảnh thật sự là ảnh của địa điểm đó | **phần thiểu số** |

Nghe qua thì 24/25 là một con số đẹp. Nhìn vào từng ảnh thì không:

| POI | Ảnh Commons trả về |
|---|---|
| **Quán Phở Hoa** | ảnh một chiếc **ô tô Hongqi H9** |
| **Trà Sữa Happy** | **logo công ty ATS Water Technology** |

Phần lớn 24 kết quả còn lại cùng một dạng: ảnh con phố, ảnh phương tiện, ảnh
biển hiệu của một doanh nghiệp khác cùng dãy nhà. Điều này hợp lý và đoán trước
được — `geosearch` hỏi *"có ảnh nào được gắn toạ độ gần đây không"*, nó **không
hỏi** *"có ảnh nào chụp quán này không"*. Ở một thành phố mật độ cao như TP.HCM,
"gần đây" trong vòng 200 m có thể là hàng trăm mặt tiền khác nhau.

### 4.4 Hai mức `confidence` — và vì sao không được bỏ

Đây là lý do trực tiếp sinh ra trường `confidence`:

- **`place`** — ảnh **của chính địa điểm**, suy từ thẻ OSM `image`,
  `wikimedia_commons`, hoặc `wikidata` → P18. Đếm thật: **5 POI** trong toàn bộ
  database, tức 0,17% (xem mục 3). Đây là thứ duy nhất được phép hiển thị như
  "ảnh của quán".
- **`area`** — ảnh **chụp quanh đó**, tìm bằng `geosearch` theo toạ độ. Phủ gần
  hết, nhưng như mục 4.3 cho thấy, phần lớn không phải ảnh của quán.

**Giao diện bắt buộc ghi nhãn `"Ảnh khu vực · cách N m"`** cho mọi ảnh
`confidence: "area"`, kèm `distanceMeters` thật. Không phải một tinh chỉnh giao
diện — đây là điều kiện để hệ thống không nói dối.

Bỏ nhãn đó đi thì trang chi tiết của "Quán Phở Hoa" hiển thị một chiếc ô tô ở
đúng vị trí mà mọi ứng dụng bản đồ khác đặt ảnh mặt tiền quán. Người dùng không
có cách nào biết đó không phải ảnh của quán. Và đó chính xác là **hiển thị một
ảnh suy đoán như thể nó là sự thật** — điều mà quy ước số 2 của kho mã này cấm.

Một cách nói gọn cho hội đồng:

> Hệ thống **không có** nguồn ảnh đúng cho 99,83% địa điểm. Thay vì che chỗ
> trống đó bằng ảnh gần đúng, hệ thống hiển thị ảnh gần đúng **kèm nhãn nói rõ
> nó chỉ là ảnh khu vực và cách bao nhiêu mét**. Người xem giữ được quyền tự
> đánh giá.

### 4.5 Nghĩa vụ ghi công

Ảnh trên Commons là ảnh **có giấy phép**, không phải ảnh tự do tuyệt đối. Phần
lớn là CC BY hoặc CC BY-SA, và cả hai đều **bắt buộc ghi tên tác giả**. Vì vậy
`license` và `attribution` là trường của hợp đồng API chứ không phải thông tin
thêm thắt, và `sourceUrl` trỏ thẳng về trang file trên Commons để người xem kiểm
được.

Trường hợp API không trả về được giấy phép hoặc tác giả, hai trường này là
`null` — và ảnh **không hiển thị**. Hiển thị một ảnh CC mà không ghi công là vi
phạm giấy phép; hiển thị nó với dòng ghi công trống là vi phạm có ghi lại. Chốt
chặn đặt ở tầng dò ảnh, tức chỗ duy nhất ghi vào cache `poi_photos`: lọc ở giao
diện thì ảnh vẫn nằm trong database và vẫn ra theo API.

**Một ngoại lệ, vì giấy phép nói vậy chứ không phải vì tiện.** Ảnh **CC0 /
Public domain** không kèm nghĩa vụ ghi công, nên thiếu `attribution` vẫn được
hiển thị — miễn là `license` nói rõ nó là CC0/PD. Thiếu `license` thì loại,
không có ngoại lệ: không đọc được giấy phép nghĩa là không biết mình được phép
gì, và đoán ở đây là đoán thay cho nghĩa vụ pháp lý của người khác.

**Cái giá phải nói thẳng.** Một file Commons có thể hợp pháp mà `extmetadata`
vẫn thiếu `Artist`; nếu giấy phép của nó là CC BY/CC BY-SA thì chốt chặn này
loại luôn, dù về mặt pháp lý nó chỉ thiếu metadata chứ không thiếu quyền. Nghĩa
là độ phủ ảnh `area` thấp hơn số ảnh Commons thật sự có quanh POI. Đây là đánh
đổi có chủ ý — mất một phần độ phủ để không phải giải thích một vi phạm giấy
phép trước hội đồng — chứ không phải bỏ sót.

---

## 5. Hạn chế còn lại và hướng mở rộng

Bốn thứ chưa làm được, xếp theo mức khả thi:

1. **Mapillary.** Ảnh chụp đường phố có toạ độ và có hướng nhìn, giấy phép CC
   BY-SA, API **miễn phí nhưng cần token**. Đây là hướng tốt nhất cho lớp
   `area`: có hướng camera thì lọc được ảnh *đang nhìn về phía* POI thay vì chỉ
   *ở gần* POI — đúng chỗ mà `geosearch` của Commons thất bại. Chưa làm vì cần
   đăng ký token và thêm một nguồn nữa vào đường ống dò ảnh.
2. **Google Places Photos.** Chất lượng cao nhất và là ảnh đúng của địa điểm,
   nhưng tính tiền theo truy vấn và **điều khoản cấm lưu trữ lâu dài**, nên
   không dùng được cho một đồ án cần chạy lại nhiều lần lúc chấm.
3. **Người dùng tự tải ảnh lên.** Lời giải đúng về lâu dài, nhưng cần
   **xác thực người dùng** (hệ thống hiện chỉ có `session_id` ẩn danh), **lưu
   trữ đối tượng**, và **kiểm duyệt nội dung**. Cả ba đều chưa có.
4. **Phân loại ảnh `area` bằng thị giác máy tính** (ảnh này có mặt tiền quán
   không?). Đúng về nguyên tắc nhưng chi phí cao hơn nhiều so với giá trị nó
   mang lại cho 3.010 POI — và vẫn không sinh ra được ảnh cho POI không có ảnh nào.

Ngoài ảnh:

- **`similar` đang dựa trên khoảng cách + cùng category**, kèm `reason` nói rõ
  cơ sở. Chưa dùng embedding ngữ nghĩa vì embedding hiện vẫn là *hashing trick*
  (`hashing-v2-64`), không mang ngữ nghĩa — đã ghi ở [Phase 4 → 8](phase-4-8-ltr.md#6-phase-8--thang-ablation-cộng-dồn).
- **`reviews` gần như luôn rỗng**: bảng `poi_reviews` chỉ có dữ liệu seed.
  `reviewSummary.average` là `null` chứ không phải 0 khi không có đánh giá nào.

---

## 6. Cách chạy

### Pre-warm ảnh

Dò trước và lưu lại, để lúc demo không ai phải chờ Commons. Nhịp gọi bắt buộc
≥ 1 giây (mục 4.2), nên script chạy chậm **có chủ ý** — đừng giảm `--sleep`
xuống dưới 1,0 để "cho nhanh", kết quả sẽ là một chuỗi HTTP 429 và một tập ảnh
thủng lỗ chỗ.

```powershell
docker compose -p nearby-dev run --rm --no-deps -v "${PWD}/backend/app:/app/app" -v "${PWD}/backend/scripts:/app/scripts" -v "${PWD}/backend/results:/app/results" backend python scripts/enrich_photos.py --limit 200 --sleep 1.1
```

Ba chi tiết của lệnh này không được bỏ:

- **Một dòng, không có `\`.** Máy chạy đồ án là Windows/PowerShell, mà PowerShell
  không hiểu dấu `\` nối dòng kiểu bash — dán lệnh nhiều dòng vào là gãy ngay ở
  dòng đầu.
- **Mount `backend/results`.** Script ghi toàn bộ kết quả ra
  `results/poi_photos_<ts>.json`, gồm cả POI không lấy được ảnh kèm lý do. Thiếu
  mount thì file đó chết theo container `--rm`, mà đúng phần thất bại mới là
  phần phân biệt được "đã hỏi, không có ảnh" với "chưa hỏi được".
- **Chạy `--limit 3 --dry-run` trước.** Gọi Wikimedia thật nhưng không ghi
  database, đủ để thấy bộ lọc có đang chọn đúng nhóm POI không trước khi bỏ ra
  một lượt dài.

Chạy cho toàn bộ 3.010 POI mất khoảng **50 phút** ở nhịp 1 giây. Script chạy lặp
lại được: POI đã dò rồi thì bỏ qua.

### Tắt hẳn phần ảnh

`photos_enabled` trong [`app/config.py`](../../backend/app/config.py), cùng kiểu
với `weather_enabled` và `traffic_enabled`:

```bash
PHOTOS_ENABLED=false
```

Tắt thì endpoint B trả `status: "unavailable"` với `photos: []` — **không phải**
`"empty"`. Phân biệt này là cốt lõi: `empty` là một phát hiện về dữ liệu,
`unavailable` là một trạng thái của hệ thống. Đặt nhầm thì một lần tắt cấu hình
sẽ đi vào báo cáo dưới dạng "địa điểm này không có ảnh".

Hai lý do thật để tắt: **đo độ trễ sạch** (endpoint B là lần gọi mạng ngoài duy
nhất của trang chi tiết) và **chạy demo offline** khi mạng hội trường không tin
được.
