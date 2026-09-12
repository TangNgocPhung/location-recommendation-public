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

CMD ["pnpm", "exec", "vinext", "dev", "--hostname", "0.0.0.0"]