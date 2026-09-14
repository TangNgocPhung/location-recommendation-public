#!/usr/bin/env bash
# Dựng image OSRM cho arm64 (Oracle Cloud Ampere A1).
#
# LÝ DO TỒN TẠI: dự án OSRM KHÔNG publish image arm64 lên Docker Hub. Đã kiểm
# chứng bằng Docker Registry API: `osrm/osrm-backend:latest`, `v5.25.0` và
# `v5.24.0` đều là manifest v2 ĐƠN kiến trúc với "architecture":"amd64", không
# có manifest list. Kéo image đó về Ampere A1 sẽ chết ở "exec format error".
#
# Nhưng SOURCE thì dựng được: docker/Dockerfile-debian của upstream đọc
# TARGETARCH rồi chọn vcpkg triplet arm64-linux tương ứng. Nên script này KHÔNG
# tự viết Dockerfile — nó clone đúng một tag rồi gọi Dockerfile của chính dự án
# OSRM. Tự viết lại sẽ lệch dependency (upstream dùng vcpkg + ninja, không dùng
# libboost-dev của Debian) và hỏng theo những cách khó chẩn đoán.
#
#   bash deploy/build-osrm-image-arm64.sh
#
# CHẠY NATIVE TRÊN MÁY ARM, không build qua qemu trên máy amd64: biên dịch C++
# của OSRM qua emulation mất nhiều giờ. Trên 4 vCPU Ampere, tính khoảng 60-90
# phút cho lần đầu.
set -euo pipefail

# Phiên bản OSRM đã chuyển sang dòng v26.x; v5.25.0 trên Docker Hub là ảnh cũ.
# Ghim một tag cụ thể chứ không dùng nhánh mặc định: file đồ thị .osrm gắn chặt
# với phiên bản binary sinh ra nó, nên image và ba script build_osrm*.sh phải
# dùng CÙNG một phiên bản.
OSRM_REF="${OSRM_REF:-v26.9.0}"
IMAGE_TAG="${IMAGE_TAG:-nearby/osrm:arm64}"
WORKDIR="${OSRM_BUILD_DIR:-/tmp/osrm-src}"

arch="$(uname -m)"
if [ "$arch" != "aarch64" ] && [ "$arch" != "arm64" ]; then
  echo "CẢNH BÁO: máy này là $arch, không phải arm64." >&2
  echo "Nếu vẫn muốn chạy (build qua emulation, rất chậm), đặt FORCE=1." >&2
  [ "${FORCE:-0}" = "1" ] || exit 1
fi

echo "== Clone osrm-backend $OSRM_REF =="
rm -rf "$WORKDIR"
git clone --depth 1 --branch "$OSRM_REF" https://github.com/Project-OSRM/osrm-backend.git "$WORKDIR"

echo "== Build image $IMAGE_TAG (60-90 phút, đừng ngắt) =="
# DOCKER_TAG không chứa "-debug"/"-assertions" nên Dockerfile chọn Release.
DOCKER_BUILDKIT=1 docker build \
  -f "$WORKDIR/docker/Dockerfile-debian" \
  --build-arg DOCKER_TAG="$OSRM_REF" \
  -t "$IMAGE_TAG" \
  "$WORKDIR"

echo
echo "== Kiểm tra image chạy được và có đủ hồ sơ Lua =="
# osrm/motorbike.lua nạp '/opt/car.lua' tường minh và thêm '/opt/?.lua' vào
# package.path để require('lib/...') tìm thấy. Thiếu một trong ba thứ này thì
# build_osrm_motorbike.sh chết ở require đầu tiên, nên kiểm ngay tại đây thay
# vì để lộ ra sau 40 phút dựng đồ thị.
docker run --rm "$IMAGE_TAG" osrm-routed --help >/dev/null
docker run --rm "$IMAGE_TAG" sh -c 'test -f /opt/car.lua && test -f /opt/foot.lua && test -d /opt/lib'
echo "   /opt/car.lua, /opt/foot.lua, /opt/lib  OK"

echo
echo "== Xong. Dựng ba đồ thị bằng chính image này: =="
echo "   export OSRM_IMAGE=$IMAGE_TAG"
echo "   bash scripts/build_osrm.sh"
echo "   bash scripts/build_osrm_foot.sh"
echo "   bash scripts/build_osrm_motorbike.sh"
