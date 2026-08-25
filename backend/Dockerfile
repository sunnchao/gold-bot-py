# Build from repo root:
#   docker build -f backend/Dockerfile -t gold-bot-backend .
#
# 前端静态资源打进 /opt/dashboard,并镜像 TS releaseRoot/apps/app-web/dist 布局。

FROM node:22-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build && test -f /app/dist/index.html

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /code

COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

COPY backend/src ./src
COPY --from=frontend /app/dist /opt/dashboard
COPY --from=frontend /app/dist /code/apps/app-web/dist

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
