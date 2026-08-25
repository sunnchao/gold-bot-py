# syntax=docker/dockerfile:1.7
# Build from repo root:
#   docker build -f backend/Dockerfile -t gold-bot-backend .
#
# 前端静态资源打进 /opt/dashboard，并镜像 TS releaseRoot/apps/app-web/dist 布局。
#
# 缓存策略：依赖安装（npm ci / uv sync）与源码复制分成独立阶段，源码变更不会
# 使依赖层失效；配合 CI 的 type=gha 层缓存实现重复构建的快速复用。

# --- Frontend: install deps (cached separately from source) ---
FROM node:22-alpine AS frontend-deps
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

# --- Frontend: build (reuses deps layer) ---
FROM node:22-alpine AS frontend-build
WORKDIR /app
COPY frontend/ ./
COPY --from=frontend-deps /app/node_modules ./node_modules
RUN npm run build && test -f /app/dist/index.html

# --- Python: install deps into venv (cached separately from source) ---
FROM python:3.12-slim AS python-deps
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    UV_CACHE_DIR=/root/.cache/uv
WORKDIR /code
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    pip install --no-cache-dir uv \
 && uv sync --frozen --no-dev --no-install-project

# --- Final backend image ---
FROM python:3.12-slim AS backend
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    UV_CACHE_DIR=/root/.cache/uv
WORKDIR /code
# reuse prebuilt venv (heavy dep layer is cached across builds)
COPY --from=python-deps /opt/venv /opt/venv
# lockfile layer: changes only when dependencies change
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
# source layer: changes most often, isolated from dep install
COPY backend/src ./src
# uv 随项目 venv 不会一并拷贝，需在最终阶段单独安装（体积极小）
# install only the project (deps already present in venv; fast)
RUN pip install --no-cache-dir uv \
 && uv sync --frozen --no-dev
COPY --from=frontend-build /app/dist /opt/dashboard
COPY --from=frontend-build /app/dist /code/apps/app-web/dist
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
