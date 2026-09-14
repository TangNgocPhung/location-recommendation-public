#!/usr/bin/env bash
# Dựng đồ thị định tuyến OSRM cho hồ sơ XE MÁY.
#
# Dùng lại "hcm.osm.pbf" đã cắt sẵn bởi build_osrm.sh — không tải/cắt lại. Một
# osrm-routed chỉ phục vụ ĐÚNG MỘT đồ thị, nên xe máy phải là đồ thị riêng
# ("hcm-motorbike.osrm") chạy trên cổng riêng — xem service `osrm-motorbike`
# trong docker-compose.yml, cùng cách làm như `osrm-foot`.
#
# Hồ sơ Lua nằm ở osrm/motorbike.lua (kế thừa /opt/car.lua, xem ghi chú trong
# file đó). File .lua ĐƯỢC commit vì nó là mã nguồn; chỉ dữ liệu .pbf/.osrm*
# mới bị .gitignore bỏ qua.
#
# Chạy MỘT LẦN, sau khi đã có ./osrm/hcm.osm.pbf:
#
#   MSYS_NO_PATHCONV=1 bash scripts/build_osrm_motorbike.sh
#
# (MSYS_NO_PATHCONV=1 là bắt buộc trên Git Bash/MSYS: nếu không, "/data" trong
# tham số docker bị dịch thành đường dẫn Windows và container không tìm thấy
# file — lỗi khó đoán vì thông báo chỉ nói "file không tồn tại".)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$ROOT/osrm"
IMAGE="${OSRM_IMAGE:-osrm/osrm-backend:latest}"
PROFILE="/data/motorbike.lua"

cd "$DATA_DIR"

if [ ! -s hcm.osm.pbf ]; then
  echo "Thiếu (hoặc rỗng) osrm/hcm.osm.pbf — chạy 'bash scripts/build_osrm.sh' trước." >&2
  exit 1
fi

if [ ! -f motorbike.lua ]; then
  echo "Thiếu osrm/motorbike.lua — file này phải có trong repo." >&2
  exit 1
fi

# `-s` chứ không `-f`: lần build hỏng có thể để lại file RỖNG, và kiểm tra bằng
# -f sẽ coi như đã xong rồi bỏ qua bước sao chép — osrm-extract sau đó báo
# "0 nodes" mà không nói vì sao. Đây là lỗi đã gặp thật lúc dựng hồ sơ car.
if [ ! -s hcm-motorbike.osm.pbf ]; then
  echo "== Sao chép hcm.osm.pbf -> hcm-motorbike.osm.pbf (osrm-extract ghi cạnh file input) =="
  cp hcm.osm.pbf hcm-motorbike.osm.pbf
else
  echo "== Đã có hcm-motorbike.osm.pbf, bỏ qua bước sao chép =="
fi

echo "== Dựng đồ thị XE MÁY (extract → partition → customize) =="
docker run --rm -v "$DATA_DIR:/data" "$IMAGE" osrm-extract -p "$PROFILE" /data/hcm-motorbike.osm.pbf
docker run --rm -v "$DATA_DIR:/data" "$IMAGE" osrm-partition /data/hcm-motorbike.osrm
docker run --rm -v "$DATA_DIR:/data" "$IMAGE" osrm-customize /data/hcm-motorbike.osrm

echo
echo "== Xong. Bật dịch vụ: =="
echo "   docker compose -p nearby-dev up -d osrm-motorbike"
echo "   curl 'http://localhost:5003/route/v1/driving/106.7009,10.7757;106.7018,10.7784?overview=false'"
