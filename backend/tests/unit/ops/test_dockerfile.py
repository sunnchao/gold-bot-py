"""镜像 apps/app-server/src/dockerfile.spec.ts:生产镜像必须能托管 dashboard SPA。"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]


def test_backend_image_copies_and_ships_static_dashboard() -> None:
    dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY --from=frontend-build /app/dist" in dockerfile
    assert "/opt/dashboard" in dockerfile
    assert "uvicorn" in dockerfile


def test_frontend_image_builds_static_export() -> None:
    dockerfile = (REPO_ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert "npm run build" in dockerfile or "vite build" in dockerfile
    assert "nginx" in dockerfile.lower()
    assert "try_files" not in dockerfile  # nginx.conf 单独挂载
    nginx = (REPO_ROOT / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    assert "try_files $uri $uri/ /index.html" in nginx
