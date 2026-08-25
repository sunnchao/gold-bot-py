"""镜像 apps/app-server/src/app.spec.ts dashboard SPA 静态托管用例。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.api.app import create_api_app
from backend.persistence.store import create_in_memory_store


def test_serves_dashboard_static_files_with_spa_fallbacks(tmp_path: Path) -> None:
    dist = tmp_path / "apps" / "app-web" / "dist"
    (dist / "accounts" / "__dynamic__").mkdir(parents=True)
    (dist / "index.html").write_text("<main>dashboard shell</main>", encoding="utf-8")
    (dist / "accounts" / "__dynamic__" / "index.html").write_text("<main>account detail</main>", encoding="utf-8")

    app = create_api_app({"store": create_in_memory_store(), "release_root": tmp_path})
    client = TestClient(app)

    root = client.get("/")
    spa = client.get("/audit")
    account = client.get("/accounts/90011087")

    assert root.status_code == 200
    assert "text/html" in root.headers["content-type"]
    assert root.text == "<main>dashboard shell</main>"
    assert spa.status_code == 200
    assert spa.text == "<main>dashboard shell</main>"
    assert account.status_code == 200
    assert account.text == "<main>account detail</main>"


def test_unknown_path_stays_json_404_when_dashboard_dist_is_absent() -> None:
    app = create_api_app({"store": create_in_memory_store(), "release_root": "/tmp/gold-bot-no-dashboard"})
    client = TestClient(app)
    response = client.get("/not-found")
    assert response.status_code == 404
    assert response.json() == {"status": "ERROR", "message": "not found"}
