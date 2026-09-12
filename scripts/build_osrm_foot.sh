#!/usr/bin/env bash
# Dựng đồ thị định tuyến OSRM cho hồ sơ ĐI BỘ (Phase 12.7).
#
# Dùng lại chính "hcm.osm.pbf" đã cắt sẵn bởi build_osrm.sh — không tải/cắt lại
# từ đầu. Một ảnh osrm/osrm-backend chỉ phục vụ MỘT đồ thị mỗi lần chạy
# (osrm-routed nhận đúng 1 file .osrm), nên hồ sơ "car" (đã có, tên "hcm.osrm")
# và hồ sơ "foot" phải là hai đồ thị RIÊNG, đặt tên khác nhau ("hcm-foot.osrm"),
# để chạy hai container osrm-routed song song trên hai cổng khác nhau — xem
# service "osrm-foot" trong docker-compose.yml.
#
# Chạy MỘT LẦN, sau khi đã có ./osrm/hcm.osm.pbf (tức đã chạy build_osrm.sh):
#
#   bash scripts/build_osrm_foot.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$ROOT/osrm"
IMAGE="osrm/osrm-backend:latest"
PROFILE="/opt/foot.lua"

cd "$DATA_DIR"

if [ ! -f hcm.osm.pbf ]; then
  echo "Thiếu osrm/hcm.osm.pbf — chạy 'bash scripts/build_osrm.sh' (hồ sơ car) trước." >&2
  exit 1
fi

if [ ! -f hcm-foot.osm.pbf ]; then
  echo "== Sao chép hcm.osm.pbf -> hcm-foot.osm.pbf (osrm-extract ghi đè cạnh file input) =="
  cp hcm.osm.pbf hcm-foot.osm.pbf
else
  echo "== Đã có hcm-foot.osm.pbf, bỏ qua bước sao chép =="
fi

echo "== Dựng đồ thị định tuyến ĐI BỘ (extract → partition → customize) =="
docker run --rm -v "$DATA_DIR:/data" "$IMAGE" osrm-extract -p "$PROFILE" /data/hcm-foot.osm.pbf
docker run --rm -v "$DATA_DIR:/data" "$IMAGE" osrm-partition /data/hcm-foot.osrm
docker run --rm -v "$DATA_DIR:/data" "$IMAGE" osrm-customize /data/hcm-foot.osrm

echo
echo "== Xong. Bật dịch vụ: =="
echo "   docker compose -p nearby-dev up -d osrm-foot"
echo "   curl 'http://localhost:5002/route/v1/foot/106.7009,10.7757;106.7018,10.7784?overview=false'"
