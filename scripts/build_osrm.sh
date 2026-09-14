#!/usr/bin/env bash
# Dựng dữ liệu định tuyến OSRM cho TP.HCM (lộ trình B16).
#
# Chạy MỘT LẦN trước khi bật service `osrm` trong docker-compose. Không đưa các
# bước này vào lúc khởi động container: osrm-extract mất vài phút và ngốn RAM,
# chạy nó mỗi lần `up` sẽ làm cả stack không lên nổi trên máy yếu.
#
#   bash scripts/build_osrm.sh
#
# Kết quả nằm trong ./osrm/ và bị .gitignore bỏ qua — riêng file Việt Nam đã
# 328 MB, và đồ thị .osrm phụ thuộc phiên bản OSRM nên commit chúng chỉ tổ làm
# người khác chạy nhầm đồ thị của phiên bản khác.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$ROOT/osrm"
# Cắt rộng hơn OSM_BBOX của phần nhập POI (10.20,106.00,11.40,107.40 — xem
# config/development.env) một chút: tuyến đường có thể vòng ra ngoài hộp chứa
# hai đầu mút, và cắt sát quá thì OSRM trả "NoRoute" cho những cặp điểm nằm gần
# rìa. Lưu ý thứ tự tọa độ của osmium là west,south,east,north (lon trước lat),
# NGƯỢC với OSM_BBOX của phần nhập POI (south,west,north,east).
BBOX="${OSRM_BBOX:-105.95,10.15,107.45,11.45}"
SOURCE_URL="${OSRM_SOURCE_URL:-https://download.geofabrik.de/asia/vietnam-latest.osm.pbf}"
# Hồ sơ ô tô. OSRM dựng sẵn car/bicycle/foot trong /opt của ảnh chính thức.
#
# Xe máy ở TP.HCM đi được nhiều đường mà ô tô không đi được, nên thời gian tính
# ra là CẬN TRÊN cho xe máy. Muốn sát hơn thì cần một hồ sơ Lua riêng; phải ghi
# rõ điều này trong báo cáo thay vì để người đọc tưởng đó là thời gian xe máy.
PROFILE="${OSRM_PROFILE:-/opt/car.lua}"
IMAGE="${OSRM_IMAGE:-osrm/osrm-backend:latest}"

mkdir -p "$DATA_DIR"
cd "$DATA_DIR"

if [ ! -f vietnam-latest.osm.pbf ]; then
  echo "== Tải dữ liệu OSM Việt Nam (khoảng 328 MB) =="
  curl -L --fail -o vietnam-latest.osm.pbf "$SOURCE_URL"
else
  echo "== Đã có vietnam-latest.osm.pbf, bỏ qua bước tải =="
fi

if [ ! -f hcm.osm.pbf ]; then
  echo "== Cắt vùng TP.HCM ($BBOX) =="
  # Cắt trước khi dựng đồ thị là bắt buộc trên máy ít RAM: osrm-extract trên
  # toàn Việt Nam cần vài GB, trên riêng TP.HCM thì vài trăm MB.
  docker run --rm -v "$DATA_DIR:/data" debian:bookworm-slim sh -c "
    apt-get update -qq >/dev/null && apt-get install -y -qq osmium-tool >/dev/null
    osmium extract --bbox $BBOX --set-bounds -o /data/hcm.osm.pbf --overwrite /data/vietnam-latest.osm.pbf
  "
else
  echo "== Đã có hcm.osm.pbf, bỏ qua bước cắt =="
fi

echo "== Dựng đồ thị định tuyến (extract → partition → customize) =="
# Thuật toán MLD: customize lại nhanh khi đổi trọng số, hợp với việc thử nghiệm.
docker run --rm -v "$DATA_DIR:/data" "$IMAGE" osrm-extract -p "$PROFILE" /data/hcm.osm.pbf
docker run --rm -v "$DATA_DIR:/data" "$IMAGE" osrm-partition /data/hcm.osrm
docker run --rm -v "$DATA_DIR:/data" "$IMAGE" osrm-customize /data/hcm.osrm

echo
echo "== Xong. Bật dịch vụ: =="
echo "   docker compose -p nearby-dev up -d osrm"
echo "   curl 'http://localhost:5001/route/v1/driving/106.7009,10.7757;106.7018,10.7784?overview=false'"
