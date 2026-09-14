#!/usr/bin/env bash
# Kiểm tra TRƯỚC khi triển khai thật. Mục đích là để những lỗi thầm lặng nổ ra ở
# đây — trong 5 giây — thay vì sau 90 phút build hoặc sau khi site đã lên với
# Postgres phơi ra internet.
#
#   bash scripts/preflight_production.sh
#
# Mã thoát khác 0 = KHÔNG được triển khai.
set -uo pipefail

ENV_FILE="${ENV_FILE:-config/production.env}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker-compose.yml -f deploy/docker-compose.prod.yml)
fail=0
warn=0

ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
bad()  { printf '  \033[31mFAIL\033[0m %s\n' "$1"; fail=$((fail+1)); }
note() { printf '  \033[33mWARN\033[0m %s\n' "$1"; warn=$((warn+1)); }

echo "== 1. Tệp cấu hình =="
if [ -f "$ENV_FILE" ]; then
  ok "$ENV_FILE tồn tại"
else
  bad "$ENV_FILE KHÔNG tồn tại — chép từ config/production.env.example"
  echo; echo "Dừng: không có gì để kiểm tra tiếp."; exit 1
fi

# Placeholder còn sót là lỗi hay gặp nhất và hậu quả nặng: Caddy sẽ xin chứng
# chỉ cho một tên không phải của bạn và bị Let's Encrypt giới hạn tần suất.
if grep -q 'REPLACE_ME' "$ENV_FILE"; then
  bad "còn chuỗi REPLACE_ME — sửa PUBLIC_HOST / PUBLIC_URL / ALLOWED_ORIGINS thành IP thật"
else
  ok "không còn placeholder REPLACE_ME"
fi

for key in POSTGRES_PASSWORD NEO4J_PASSWORD PUBLIC_HOST PUBLIC_URL OSRM_IMAGE; do
  value="$(grep -E "^${key}=" "$ENV_FILE" | head -1 | cut -d= -f2-)"
  [ -n "$value" ] && ok "$key đã đặt" || bad "$key rỗng hoặc thiếu"
done

echo
echo "== 2. Kiến trúc máy =="
arch="$(uname -m)"
if [ "$arch" = "aarch64" ] || [ "$arch" = "arm64" ]; then
  ok "arm64 ($arch) — đúng loại máy cho lớp phủ prod"
else
  note "máy này là $arch, không phải arm64. Lớp phủ prod thay image sang bản arm64;"
  note "chạy trên amd64 vẫn được nhưng image OSRM phải dựng lại cho amd64."
fi

echo
echo "== 3. Cổng phơi ra ngoài =="
# Danh sách `ports` khi gộp nhiều file compose là NỐI THÊM chứ không thay thế,
# nên cách duy nhất đáng tin là đọc kết quả GỘP, không đọc từng file.
merged="$("${COMPOSE[@]}" config 2>/dev/null)"
if [ -z "$merged" ]; then
  bad "docker compose config thất bại — chạy lệnh đó trực tiếp để xem lỗi"
else
  # Mỗi cổng publish không có host_ip nghĩa là nghe trên 0.0.0.0.
  exposed="$(printf '%s\n' "$merged" | awk '
    /host_ip:/        { ip = $2 }
    /published:/      { gsub(/"/, "", $2); if (ip == "") print $2; ip = "" }
  ')"
  unexpected="$(printf '%s\n' "$exposed" | grep -vE '^(80|443)?$' || true)"
  if [ -z "$unexpected" ]; then
    ok "chỉ 80/443 ra internet, mọi cổng khác nghe 127.0.0.1"
  else
    bad "cổng phơi ra 0.0.0.0 ngoài 80/443: $(echo $unexpected | tr '\n' ' ')"
    bad "  -> thêm tiền tố 127.0.0.1: vào biến *_PORT tương ứng trong $ENV_FILE"
  fi
fi

echo
echo "== 4. Image OSRM arm64 =="
osrm_image="$(grep -E '^OSRM_IMAGE=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
if docker image inspect "$osrm_image" >/dev/null 2>&1; then
  ok "$osrm_image đã có trên máy"
  image_ver="$(docker run --rm "$osrm_image" osrm-routed --version 2>/dev/null | head -1 | tr -d '\r')"
  ok "  phiên bản binary: ${image_ver:-không đọc được}"
else
  bad "$osrm_image chưa có — chạy: bash deploy/build-osrm-image-arm64.sh"
  image_ver=""
fi

echo
echo "== 5. Đồ thị định tuyến =="
# File .osrm gắn chặt với phiên bản binary sinh ra nó. Lệch phiên bản thì
# osrm-routed thoát ngay khi khởi động với thông báo khó hiểu về "file version",
# và ba container định tuyến chết im lặng trong khi phần còn lại vẫn chạy.
missing=0
for graph in hcm hcm-foot hcm-motorbike; do
  if [ -f "osrm/${graph}.osrm" ]; then
    ok "osrm/${graph}.osrm có ($(du -h "osrm/${graph}.osrm" | cut -f1))"
  else
    bad "thiếu osrm/${graph}.osrm"
    missing=$((missing+1))
  fi
done
if [ "$missing" -eq 0 ] && [ -n "$image_ver" ]; then
  note "KHÔNG có cách tự động kiểm tra đồ thị khớp phiên bản binary."
  note "  Đồ thị dựng trên máy dev dùng osrm/osrm-backend:latest = v5.26.0."
  note "  Nếu image ở trên KHÔNG phải v5.26.0 thì phải dựng lại đồ thị bằng"
  note "  chính image đó: OSRM_IMAGE=$osrm_image bash scripts/build_osrm.sh (và _foot, _motorbike)."
fi

echo
echo "== 6. Thư mục dữ liệu =="
[ -f deploy/Caddyfile ] && ok "deploy/Caddyfile có" || bad "thiếu deploy/Caddyfile"
[ -f backend/app/ltr/model.txt ] && ok "mô hình LTR có trong repo (sẽ vào image lúc build)" \
  || note "thiếu backend/app/ltr/model.txt — LTR sẽ tắt im lặng, /api/v1/ltr/status trả available:false"

echo
if [ "$fail" -gt 0 ]; then
  printf '\033[31m%d lỗi\033[0m, %d cảnh báo. KHÔNG triển khai cho tới khi hết lỗi.\n' "$fail" "$warn"
  exit 1
fi
printf '\033[32mKhông có lỗi\033[0m, %d cảnh báo. Có thể triển khai.\n' "$warn"
