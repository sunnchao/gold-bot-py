"""镜像 packages/shared-contracts/src/fixture.spec.ts。"""

from __future__ import annotations

import json
from pathlib import Path

from backend.shared_contracts import parse_oracle_fixture

FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "fixtures"


def _read_fixture_files(group: str) -> list[dict]:
    directory = FIXTURE_ROOT / group
    names = sorted(name for name in directory.iterdir() if name.suffix == ".json")
    return [json.loads(path.read_text(encoding="utf-8")) for path in names]


def test_parses_every_ea_route_fixture() -> None:
    fixtures = [parse_oracle_fixture(value) for value in _read_fixture_files("earoutes")]
    assert len(fixtures) == 7
    assert sorted(fixture["fixture"] for fixture in fixtures) == [
        "ea-bars",
        "ea-heartbeat",
        "ea-order-result",
        "ea-poll",
        "ea-positions",
        "ea-register",
        "ea-tick",
    ]


def test_parses_every_admin_and_ai_fixture() -> None:
    fixtures = [parse_oracle_fixture(value) for value in _read_fixture_files("admin")]
    assert len(fixtures) == 11
    assert sorted(fixture["fixture"] for fixture in fixtures) == [
        "admin-accounts",
        "admin-ai-result",
        "admin-ai-result-v2-trade-plan",
        "admin-ai-symbols",
        "admin-analysis-payload",
        "admin-analysis-payload-v2",
        "admin-audit",
        "admin-events-stream-sample",
        "admin-overview",
        "admin-pending-signal",
        "admin-symbols",
    ]
