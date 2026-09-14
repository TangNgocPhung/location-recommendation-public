#!/usr/bin/env bash
# Đưa bản dev đang chạy ra một URL công khai bằng Cloudflare Tunnel.
#
#   bash scripts/tunnel_public.sh            # bật
#   bash scripts/tunnel_public.sh --stop     # tắt, trả lại cấu hình localhost
#
# KHÔNG cần tài khoản Cloudflare, không cần thẻ, không cần tên miền. Đổi lại:
#   - Site chỉ sống khi máy này bật và stack nearby-dev đang chạy.
#   - URL là ngẫu nhiên và ĐỔI mỗi lần chạy lại script. Muốn URL cố định thì
#     phải có tài khoản Cloudflare kèm một tên miền.
#
# Thứ tự các bước là bắt buộc: phải có URL TRƯỚC rồi mới dựng lại frontend,
# vì frontend nhúng URL API vào bundle trình duyệt lúc build.
set -uo pipefail

PROJECT=nearby-dev
NETWORK="${PROJECT}_default"
TUNNEL_NAME=nearby-tunnel
COMPOSE_BASE=(docker compose --env-file config/development.env --env-file .env -p "$PROJECT")

compose_dev()    { POSTGRES_PORT=5433 "${COMPOSE_BASE[@]}" "$@"; }
compose_tunnel() { POSTGRES_PORT=5433 PUBLIC_URL="$1" "${COMPOSE_BASE[@]}" -f docker-compose.yml -f deploy/docker-compose.tunnel.yml "${@:2}"; }

if [ "${1:-}" = "--stop" ]; then
  echo "== Tắt tunnel =="
  docker rm -f "$TUNNEL_NAME" >/dev/null 2>&1 && echo "   đã gỡ container $TUNNEL_NAME" || echo "   không có tunnel nào đang chạy"
  echo "== Trả frontend/backend về cấu hình localhost =="
  compose_dev up -d --force-recreate --no-deps frontend backend
  echo "   Xong. Site local: http://localhost:8081"
  exit 0
fi

if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
  echo "LỖI: không thấy mạng $NETWORK — stack nearby-dev chưa chạy." >&2
  echo "Chạy trước: POSTGRES_PORT=5433 docker compose --env-file config/development.env --env-file .env -p $PROJECT up -d" >&2
  exit 1
fi

echo "== 1. Mở Cloudflare Tunnel tới gateway =="
docker rm -f "$TUNNEL_NAME" >/dev/null 2>&1
docker run -d --name "$TUNNEL_NAME" --network "$NETWORK" --restart unless-stopped \
  cloudflare/cloudflared:latest tunnel --no-autoupdate --url http://gateway:8080 >/dev/null || {
    echo "LỖI: không khởi động được cloudflared." >&2; exit 1; }

echo "== 2. Chờ Cloudflare cấp URL =="
PUBLIC_URL=""
for _ in $(seq 1 40); do
  PUBLIC_URL="$(docker logs "$TUNNEL_NAME" 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | head -1)"
  [ -n "$PUBLIC_URL" ] && break
  sleep 2
done
if [ -z "$PUBLIC_URL" ]; then
  echo "LỖI: không lấy được URL sau 80 giây. Log cloudflared:" >&2
  docker logs "$TUNNEL_NAME" 2>&1 | tail -20 >&2
  exit 1
fi
echo "   $PUBLIC_URL"

echo "== 3. Dựng lại frontend theo URL đó (~60 giây, pnpm build chạy lúc khởi động) =="
# backend recreate cùng lúc để nhận ALLOWED_ORIGINS và TRUSTED_PROXY_HOPS mới.
compose_tunnel "$PUBLIC_URL" up -d --force-recreate --no-deps backend frontend || exit 1

echo "== 4. Chờ frontend sẵn sàng =="
for _ in $(seq 1 60); do
  status="$(docker inspect -f '{{.State.Health.Status}}' "${PROJECT}-frontend-1" 2>/dev/null)"
  [ "$status" = "healthy" ] && break
  sleep 3
done
[ "$status" = "healthy" ] || echo "   CẢNH BÁO: frontend chưa healthy (trạng thái: ${status:-?})"

echo "== 5. Kiểm tra từ ngoài internet =="
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 "$PUBLIC_URL/")"
echo "   GET $PUBLIC_URL/ -> $code"

echo
echo "========================================================"
echo "  $PUBLIC_URL"
echo "========================================================"
echo "Site chỉ sống khi máy này bật và Docker chạy."
echo "Tắt và trả về localhost:  bash scripts/tunnel_public.sh --stop"
