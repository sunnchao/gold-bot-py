"""EA 版本/下载 + 服务端点契约(镜像 app.ts eaVersionResponse/eaVersionCheckResponse/eaDownloadResponse)。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_api_app
from backend.persistence.store import create_in_memory_store

pytestmark = pytest.mark.contract

ROUTE_TOKEN = "route-token"
MT4_DIR = "apps/app-mt/mt4_ea"
MT5_DIR = "apps/app-mt/mt5_ea"


def release_root_with(tmp_path: Path, version: str = "1.2.3", build: int = 45, changelog: str = "fixes") -> Path:
    (tmp_path / MT4_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / MT4_DIR / "version.json").write_text(
        json.dumps({"version": version, "build": build, "changelog": changelog}),
        encoding="utf-8",
    )
    return tmp_path


def make_app(release_root: str | Path, **options) -> TestClient:
    store = options.pop("store", None) or create_in_memory_store()
    return TestClient(create_api_app({"store": store, "release_root": release_root, **options}))


async def test_ea_version_public_and_reports_build(tmp_path: Path) -> None:
    client = make_app(release_root_with(tmp_path))
    response = client.get("/api/ea/version")
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "version": "1.2.3", "build": 45, "changelog": "fixes"}

    client_mt5 = make_app(release_root_with(tmp_path))
    response_mt5 = client_mt5.get("/api/ea/version?platform=mt5")
    # mt5 目录不存在 → fallback
    assert response_mt5.status_code == 200
    assert response_mt5.json()["version"] == "0.0.0"
    assert response_mt5.json()["build"] == 0


async def test_missing_version_file_uses_fallback(tmp_path: Path) -> None:
    client = make_app(tmp_path)
    response = client.get("/api/ea/version")
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "version": "0.0.0", "build": 0, "changelog": ""}


async def test_malformed_version_json_reports_decode_error(tmp_path: Path) -> None:
    (tmp_path / MT4_DIR).mkdir(parents=True)
    (tmp_path / MT4_DIR / "version.json").write_text("not json", encoding="utf-8")
    client = make_app(tmp_path)
    response = client.get("/api/ea/version")
    assert response.status_code == 500
    assert response.json()["message"].startswith("decode EA version file:")


async def test_version_file_ignores_type_mismatches(tmp_path: Path) -> None:
    (tmp_path / MT4_DIR).mkdir(parents=True)
    (tmp_path / MT4_DIR / "version.json").write_text(
        json.dumps({"version": 123, "build": "45", "changelog": 42}),
        encoding="utf-8",
    )
    client = make_app(tmp_path)
    response = client.get("/api/ea/version")
    assert response.status_code == 200
    assert response.json() == {"status": "OK", "version": "0.0.0", "build": 0, "changelog": ""}


async def test_version_check_requires_token(tmp_path: Path) -> None:
    client = make_app(release_root_with(tmp_path), valid_tokens={ROUTE_TOKEN})
    no_token = client.get("/version_check")
    assert no_token.status_code == 401
    assert no_token.json() == {"status": "ERROR", "message": "invalid token"}

    ok = client.get("/version_check", headers={"X-API-Token": ROUTE_TOKEN})
    assert ok.status_code == 200
    assert ok.json() == {"latest_version": "1.2.3", "latest_build": 45, "force_update": False}


async def test_download_requires_token_and_returns_file_bytes(tmp_path: Path) -> None:
    root = release_root_with(tmp_path)
    (root / MT4_DIR / "GoldBolt_Client.mq4").write_text("// EA source", encoding="utf-8")
    client = make_app(root, valid_tokens={ROUTE_TOKEN})

    no_token = client.get("/api/ea/download")
    assert no_token.status_code == 401

    ok = client.get("/api/ea/download", headers={"X-API-Token": ROUTE_TOKEN})
    assert ok.status_code == 200
    assert ok.content == b"// EA source"
    assert ok.headers.get("content-disposition", "").startswith('attachment; filename="GoldBolt_Client.mq4"')


async def test_download_missing_file_returns_404(tmp_path: Path) -> None:
    client = make_app(release_root_with(tmp_path), valid_tokens={ROUTE_TOKEN})
    response = client.get("/api/ea/download", headers={"X-API-Token": ROUTE_TOKEN})
    assert response.status_code == 404
    assert response.json() == {"status": "ERROR", "message": "file not found"}


async def test_contracts_endpoint_lists_ea_endpoints() -> None:
    client = make_app("/tmp")
    response = client.get("/__contracts")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "OK"
    assert body["phase"] == 1
    assert body["ea_endpoints"] == [
        "/register",
        "/heartbeat",
        "/tick",
        "/bars",
        "/positions",
        "/poll",
        "/order_result",
    ]
    assert body["persistence"] == {"writesLiveCommands": False}


async def test_healthz_and_metrics_endpoints() -> None:
    client = make_app("/tmp")
    healthz = client.get("/healthz")
    assert healthz.status_code == 200
    assert healthz.text == "ok"

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "text/plain" in metrics.headers.get("content-type", "")
    assert "goldbot_" in metrics.text


async def test_shadow_metrics_qualification_and_comparisons() -> None:
    client = make_app("/tmp")

    metrics = client.get("/shadow/metrics")
    assert metrics.status_code == 200
    body = metrics.json()
    assert body["status"] == "OK"
    assert "report" in body
    assert "totals" in body
    assert body["totals"]["comparisons"] == 0

    qualification = client.get("/shadow/qualification")
    assert qualification.status_code == 200
    assert "summary" in qualification.json()

    invalid = client.post("/shadow/comparisons", json={})
    assert invalid.status_code == 400
    assert invalid.json() == {"status": "ERROR", "message": "invalid shadow comparison payload"}

    bad_json = client.post("/shadow/comparisons", data="not-json", headers={"Content-Type": "text/plain"})
    assert bad_json.status_code == 400
    assert bad_json.json() == {"status": "ERROR", "message": "invalid JSON"}

    missing_snapshot = client.post(
        "/shadow/comparisons",
        json={
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "source": "ea_analysis",
            "oracle": {"signal": {"action": "none"}, "command": None},
        },
    )
    assert missing_snapshot.status_code == 404
    assert missing_snapshot.json()["message"] == "shadow runtime snapshot not found"

    valid = client.post(
        "/shadow/comparisons",
        json={
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "source": "ea_analysis",
            "protocol_ok": True,
            "node": {"signal": {"action": "none"}, "command": None},
            "oracle": {"signal": {"action": "none"}, "command": None},
        },
    )
    assert valid.status_code == 200
    assert valid.json()["status"] == "OK"
    comparison = valid.json()["comparison"]
    assert comparison["protocol_ok"] is True
    assert comparison["source"] == "ea_analysis"
