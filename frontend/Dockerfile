FROM node:22-slim

WORKDIR /app

# Cài đúng pnpm version mà project yêu cầu
RUN npm install -g pnpm@11.19.0

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./

# Cài dependencies và cho phép build scripts
RUN --mount=type=cache,id=nearby-pnpm,target=/root/.local/share/pnpm/store \
    pnpm install --frozen-lockfile

# Rebuild các package native cần thiết
RUN pnpm rebuild workerd esbuild sharp

COPY . .

EXPOSE 3000

# Vinext's development runner can lose its internal Workers connection inside
# Docker and then return HTTP 500 indefinitely. Build with the runtime env (so
# VITE_* values are embedded) and serve the generated Worker instead.
CMD ["sh", "-c", "pnpm build && exec pnpm exec wrangler dev --config dist/server/wrangler.json --ip 0.0.0.0 --port 3000"]
