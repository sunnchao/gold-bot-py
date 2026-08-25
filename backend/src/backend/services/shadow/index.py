"""影子校验服务(1:1 镜像 apps/app-server/src/services/shadow/service.ts)。

ShadowService:metrics() / qualification() 聚合 shadow 对比并生成 cutover 报告,
record_runtime_snapshot 保存运行时快照,record_oracle_comparison 用 node(优先)或
最新运行时快照与 oracle 载荷对比,漂移判定用键排序的稳定 JSON 序列化
(mirror stableStringify/normalize);快照缺失时抛 "shadow runtime snapshot not found"。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from backend.observability.shadow_report import ReplayCoverageSummary, build_shadow_report
from backend.persistence.records import EaRecord
from backend.persistence.store import EaStore

__all__ = ["ShadowService", "create_shadow_service"]


def _default_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ShadowService:
    """镜像 TS ShadowService(store, nowIso, replayCoverageProvider?)。"""

    def __init__(
        self,
        store: EaStore,
        now_iso: Callable[[], str] | None = None,
        replay_coverage_provider: Callable[[], ReplayCoverageSummary | None] | None = None,
    ) -> None:
        self._store = store
        self._now_iso = now_iso if now_iso is not None else _default_now_iso
        self._replay_coverage_provider = replay_coverage_provider

    async def metrics(self) -> dict[str, Any]:
        comparisons = await self._store.list_shadow_comparisons()
        totals = await self._store.summarize_shadow_comparisons()
        replay_coverage = self._replay_coverage_provider() if self._replay_coverage_provider is not None else None
        return {
            "status": "OK",
            "generated_at": self._now_iso(),
            "report": build_shadow_report(comparisons, replay_coverage),
            "totals": {
                "comparisons": totals.get("comparisons", 0),
                "protocol_errors": totals.get("protocol_errors", 0),
                "signal_drifts": totals.get("signal_drifts", 0),
                "command_drifts": totals.get("command_drifts", 0),
            },
        }

    async def qualification(self) -> dict[str, Any]:
        metrics = await self.metrics()
        return {**metrics, "summary": metrics["report"]["checks"]}

    async def record_runtime_snapshot(self, snapshot_input: EaRecord) -> None:
        snapshot: EaRecord = {
            "account_id": snapshot_input["account_id"],
            "symbol": snapshot_input["symbol"],
            "source": snapshot_input["source"],
            "signal": snapshot_input.get("signal"),
            "command": snapshot_input.get("command"),
            "created_at": (
                snapshot_input.get("created_at")
                if snapshot_input.get("created_at") is not None
                else self._now_iso()
            ),
        }
        await self._store.save_shadow_snapshot(snapshot)

    async def record_oracle_comparison(self, comparison_input: EaRecord) -> EaRecord:
        runtime_snapshot = comparison_input.get("node")
        if runtime_snapshot is None:
            runtime_snapshot = await self._store.get_latest_shadow_snapshot(
                str(comparison_input["account_id"]),
                str(comparison_input["symbol"]),
                str(comparison_input["source"]),
            )
        if runtime_snapshot is None:
            raise RuntimeError("shadow runtime snapshot not found")
        oracle = comparison_input["oracle"]
        protocol_ok = comparison_input.get("protocol_ok")
        created_at = comparison_input.get("created_at")
        comparison: EaRecord = {
            "account_id": comparison_input["account_id"],
            "symbol": comparison_input["symbol"],
            "protocol_ok": True if protocol_ok is None else bool(protocol_ok),
            "signal_drift": _has_drift(runtime_snapshot.get("signal"), oracle.get("signal")),
            "command_drift": _has_drift(runtime_snapshot.get("command"), oracle.get("command")),
            "oracle_compared": True,
            "source": comparison_input["source"],
            "created_at": created_at if created_at is not None else self._now_iso(),
        }
        await self._store.record_shadow_comparison(comparison)
        return comparison


def _has_drift(left: Any, right: Any) -> bool:
    return _stable_stringify(left) != _stable_stringify(right)


def _stable_stringify(value: Any) -> str:
    return json.dumps(_normalize(value), ensure_ascii=False, separators=(",", ":"))


def _normalize(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize(entry) for entry in value]
    if isinstance(value, dict):
        return {key: _normalize(value[key]) for key in sorted(value.keys())}
    if value is None:
        return None
    return value


def create_shadow_service(options: dict[str, Any] | None = None) -> ShadowService:
    """工厂:coordinator 用 options dict 组装(store/now_iso/replay_coverage_provider)。"""
    opts = options or {}
    return ShadowService(opts["store"], opts.get("now_iso"), opts.get("replay_coverage_provider"))
