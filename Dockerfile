# ---------- 前端构建 ----------
FROM node:22-alpine AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---------- 后端运行 ----------
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime
WORKDIR /app/backend

# 先装依赖再拷源码，充分利用层缓存
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen

COPY backend/ ./
COPY --from=frontend-build /build/dist /app/frontend/dist

ENV RADAR_DATA_DIR=/data \
    RADAR_FRONTEND_DIST_DIR=/app/frontend/dist \
    UV_COMPILE_BYTECODE=1

VOLUME /data
EXPOSE 8300

CMD ["uv", "run", "--no-sync", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8300"]
