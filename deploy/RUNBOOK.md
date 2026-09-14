# Triển khai Nearby lên máy chủ công khai

Viết ngày 2026-09-14. Mọi con số trong này đã đo trên máy dev, không chép từ tài
liệu cũ.

Nearby **không phải site tĩnh**. Nó là 11 container: Postgres+PostGIS, OpenSearch,
Redis, Neo4j, backend Python, frontend, gateway nginx, 3 máy chủ định tuyến OSRM,
và Caddy lo TLS. Không có dịch vụ hosting thường nào nhận khối này — cần một VPS.

Trước mỗi lần triển khai, chạy:

```bash
bash scripts/preflight_production.sh
```

Thoát khác 0 = chưa được triển khai. Script đó kiểm tra đúng những thứ hay hỏng
thầm lặng, kể cả việc đọc cấu hình GỘP để chắc chắn không cổng nào ngoài 80/443
phơi ra internet.

---

## 0. Thứ bạn phải tự làm (tôi không làm thay được)

1. **Tạo tài khoản Oracle Cloud** và dựng một máy Ampere A1. Hạn mức Always Free
   bị Oracle cắt một nửa từ 15/06/2026, không thông báo: nay là **2 OCPU / 12 GB
   RAM** (trước là 4 OCPU / 24 GB). 12 GB vẫn dư — stack đo được dùng ~3,6 GB lúc
   rảnh — nhưng 2 nhân làm việc biên dịch OSRM từ nguồn lâu hơn đáng kể. Việc
   tạo tài khoản cần số điện thoại và thẻ của bạn (thẻ chỉ để xác minh).
2. Chọn ảnh **Ubuntu 24.04 (aarch64)**.
3. Lưu khóa SSH khi tạo máy — Oracle chỉ cho tải một lần.

Mức dùng thật, đo bằng `docker stats` trên máy dev khi stack đứng yên: tổng
**~3,6 GB** — OpenSearch 1,4 GB, Neo4j 527 MB, ba tiến trình OSRM ~1,2 GB, còn lại
là Postgres, backend, frontend, gateway. Cộng thêm lúc build image và bộ nhớ đệm
cho 2,6 GB đồ thị mmap thì 8 GB là mức an toàn, 4 GB sẽ chật.

### Mở cổng 80/443 — phải làm ở HAI nơi

Đây là chỗ hay mất thời gian nhất với Oracle Cloud.

1. **Security List** của VCN: thêm Ingress 0.0.0.0/0 cho TCP 80 và 443.
2. **iptables trong máy**: ảnh Ubuntu của Oracle có sẵn luật REJECT ở cuối chuỗi
   INPUT. Chỉ mở Security List thôi thì gói tin vẫn bị chính máy chặn:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

---

## 1. Cài Docker trên máy chủ

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"    # đăng xuất rồi vào lại
```

## 2. Lấy mã nguồn

```bash
git clone <URL repo của bạn> nearby && cd nearby
```

## 3. Chuyển cấu hình bí mật sang

`config/production.env` chứa mật khẩu và khóa MapTiler nên **đã bị .gitignore bỏ
qua** — nó không đi theo `git clone`. Chép thẳng từ máy dev:

```bash
scp config/production.env ubuntu@<IP>:~/nearby/config/production.env
```

## 4. Điền tên miền thật

Sửa 3 dòng trong `config/production.env`, thay `REPLACE_ME` bằng IP máy chủ với
dấu chấm đổi thành gạch nối:

```
PUBLIC_HOST=152-67-1-2.sslip.io
PUBLIC_URL=https://152-67-1-2.sslip.io
ALLOWED_ORIGINS=https://152-67-1-2.sslip.io
```

`sslip.io` tự phân giải tên đó về đúng IP và Let's Encrypt cấp chứng chỉ bình
thường — không cần mua tên miền. Có domain riêng thì trỏ A record về IP rồi điền
thẳng domain.

Đặt luôn `ACME_EMAIL` nếu muốn nhận cảnh báo chứng chỉ sắp hết hạn.

---

## 5. Định tuyến OSRM — quyết định một lần, ảnh hưởng cả buổi

OSRM **không publish image arm64**. Phải dựng từ nguồn. Nhưng có một cái bẫy
phiên bản:

| | Phiên bản |
|---|---|
| Đồ thị trong `osrm/` hiện có (dựng trên máy dev) | **v5.26.0** (`osrm/osrm-backend:latest` trên Docker Hub) |
| Mặc định của `deploy/build-osrm-image-arm64.sh` | **v26.9.0** (tag mới nhất của dự án) |

File `.osrm` khóa chặt theo phiên bản binary sinh ra nó. Lệch phiên bản thì
`osrm-routed` thoát ngay lúc khởi động, ba container định tuyến chết im lặng,
còn phần site vẫn chạy — bạn chỉ phát hiện khi bấm "Chỉ đường" và không ra gì.

### Cách A — khớp với đồ thị đã có (khuyến nghị)

Giữ nguyên mọi thứ đã kiểm chứng chạy được trên máy dev, kể cả hồ sơ Lua xe máy.

```bash
# Trên MÁY CHỦ (arm64), khoảng 60-90 phút:
OSRM_REF=v5.26.0 IMAGE_TAG=nearby/osrm:arm64 bash deploy/build-osrm-image-arm64.sh
```

Rồi chuyển 2,6 GB đồ thị từ máy dev sang:

```bash
# Trên MÁY DEV:
rsync -avz --progress osrm/ ubuntu@<IP>:~/nearby/osrm/
```

### Cách B — dựng lại đồ thị trên máy chủ

Không phải tải lên 2,6 GB, nhưng dùng dòng v26.x mà hồ sơ `osrm/motorbike.lua`
của repo chưa từng được kiểm thử trên đó.

```bash
bash deploy/build-osrm-image-arm64.sh                      # 60-90 phút
export OSRM_IMAGE=nearby/osrm:arm64
bash scripts/build_osrm.sh                                 # tải ~350 MB pbf Việt Nam
bash scripts/build_osrm_foot.sh
bash scripts/build_osrm_motorbike.sh
```

Cả ba script đọc `OSRM_IMAGE`, nên đồ thị và máy chủ định tuyến chắc chắn cùng
phiên bản binary.

---

## 6. Dựng database và chạy migration

```bash
cd ~/nearby
COMPOSE="docker compose --env-file config/production.env -f docker-compose.yml -f deploy/docker-compose.prod.yml"
$COMPOSE up -d database redis opensearch neo4j
$COMPOSE run --rm migrate
```

Trên database MỚI, `alembic upgrade head` chạy sạch tới `0014_district_boundaries`
(head hiện tại của repo — chuỗi 0012 -> 0013 -> 0014 liền mạch).

> **Nếu mang dữ liệu từ máy dev sang, dùng dump CHỈ DỮ LIỆU.** Dump toàn phần
> mang theo cả bảng `alembic_version`, đè lên trạng thái migration mà máy chủ
> vừa chạy đúng:
>
> ```bash
> # Trên máy dev
> docker exec nearby-dev-database-1 pg_dump -U nearby_dev -d nearby_dev \
>   --data-only --exclude-table=alembic_version > nearby-data.sql
> ```

## 7. Nhập dữ liệu địa điểm

Cách sạch nhất là để máy chủ tự lấy từ OpenStreetMap:

```bash
$COMPOSE --profile data run --rm osm-import
$COMPOSE --profile data run --rm search-index
$COMPOSE --profile data run --rm graph-sync
$COMPOSE --profile data run --rm feature-store
```

`OSM_BBOX` trong `config/production.env` đã đặt `10.20,106.00,11.40,107.40`, khớp
vùng phủ của ba đồ thị OSRM (dựng với `105.95,10.15,107.45,11.45`). Máy dev với
bbox này có **15.988 POI**.

> `production.env.example` vẫn ghi bbox cũ `10.70,106.60,10.90,106.82` — hẹp hơn
> nhiều, nhập theo đó sẽ mất khoảng 7 nghìn POI ngoài trung tâm. File
> `config/production.env` tôi sinh ra đã sửa.

## 8. Bật toàn bộ site

```bash
bash scripts/preflight_production.sh      # phải sạch lỗi
$COMPOSE up -d
```

Caddy sẽ tự xin chứng chỉ Let's Encrypt cho `PUBLIC_HOST` trong vài chục giây.

---

## 9. Kiểm chứng — đo, đừng đoán

```bash
curl -sI  https://<PUBLIC_HOST>/            | head -1     # mong đợi 200
curl -s   https://<PUBLIC_HOST>/api/v1/ltr/status         # available phải là true
curl -s -G https://<PUBLIC_HOST>/api/v1/recommendations \
     --data-urlencode lat=10.7757 --data-urlencode lng=106.7009 --data-urlencode limit=3
```

`ltr/status` phải trả `available: true` và `featureCount: 29`. Nếu trả `false`
hoặc `28`, image backend đã dựng từ mã cũ — `$COMPOSE build backend` rồi
`$COMPOSE up -d --no-deps backend`, sau đó chạy lại `search-index`.

Kiểm định tuyến thật (không phải deep-link Google Maps) — trong UI bấm một địa
điểm rồi bấm **Chỉ đường**; phải hiện quãng đường và thời gian, và bản đồ vẽ
tuyến. Thiếu đồ thị thì backend lặng lẽ lùi về deep-link.

### Đèn báo sai đã biết

- Container `osrm-foot` / `osrm` thỉnh thoảng hiện **unhealthy** khi máy tải
  nhưng vẫn định tuyến đúng. Kiểm bằng cách gọi thẳng, đừng tin đèn:
  `curl -s 'http://127.0.0.1:5002/route/v1/foot/106.7009,10.7757;106.7018,10.7784?overview=false'`
  — trả `"code":"Ok"` là ổn.
- Đồ thị lớn: `osrm-routed` cần 1-2 phút nạp sau khi container báo "Up" mới trả
  lời. Chờ log "running and waiting for requests".

---

## 10. Dừng / gỡ

```bash
$COMPOSE down              # dừng, GIỮ dữ liệu
$COMPOSE down -v           # xóa cả volume — mất POI, chỉ mục, đồ thị KG
```
