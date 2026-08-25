"""Replay fixture 覆盖率(镜像 packages/trading-core/src/replay/coverage.ts)。

遍历 fixture 根目录下的 `*_snapshot.json` / `*_expected.json` 配对文件,逐一运行
run_replay 并用 stableStringify 归一化对比 signal / logs / position_commands。
"""

from __future__ import annotations

import json
import os
from typing import Any

__all__ = [
    "ReplayCoverageSummary",
    "ReplayFixturePair",
    "compute_replay_coverage",
    "list_replay_fixture_pairs",
]

ReplayCoverageSummary = dict[str, int]
"""镜像 ReplayCoverageSummary:total / validated。"""

ReplayFixturePair = dict[str, str]
"""镜像 ReplayFixturePair:snapshot / expected(均为文件名)。"""

_ExpectedShape = dict[str, Any]


def list_replay_fixture_pairs(fixture_root: str) -> list[ReplayFixturePair]:
    """镜像 listReplayFixturePairs:按 `_snapshot.json` 后缀配对同名 `_expected.json`。"""
    files = os.listdir(fixture_root)
    snapshot_files = [name for name in files if name.endswith("_snapshot.json")]
    pairs: list[ReplayFixturePair] = []

    for snapshot_file in snapshot_files:
        base = snapshot_file.replace("_snapshot.json", "")
        expected_file = f"{base}_expected.json"
        if expected_file in files:
            pairs.append({"snapshot": snapshot_file, "expected": expected_file})

    return pairs


def compute_replay_coverage(fixture_root: str) -> ReplayCoverageSummary:
    """镜像 computeReplayCoverage:统计全部 fixture 对中信号/日志/命令全等的数量。

    run_replay 惰性导入(镜像 backend.trading_core.replay.replay 引擎,尚在移植):
    replay.py 落地前本模块仍可正常导入。
    """
    from backend.trading_core.replay.replay import run_replay  # noqa: PLC0415

    pairs = list_replay_fixture_pairs(fixture_root)
    validated = 0

    for pair in pairs:
        snapshot_path = os.path.join(fixture_root, pair["snapshot"])
        expected_path = os.path.join(fixture_root, pair["expected"])
        with open(snapshot_path, encoding="utf-8") as fh:
            snapshot = json.load(fh)
        with open(expected_path, encoding="utf-8") as fh:
            expected: _ExpectedShape = json.load(fh)

        result = run_replay(snapshot)
        signal_match = _stable_stringify(result["signal"]) == _stable_stringify(expected.get("signal"))
        logs_match = _stable_stringify(result["logs"]) == _stable_stringify(expected.get("logs"))
        commands_match = _stable_stringify(result["position_commands"]) == _stable_stringify(
            expected.get("position_commands")
        )

        if signal_match and logs_match and commands_match:
            validated += 1

    return {"total": len(pairs), "validated": validated}


def _stable_stringify(value: Any) -> str:
    """镜像 stableStringify:归一化后 JSON 序列化(键排序)。"""
    return json.dumps(_normalize(value), ensure_ascii=False)


def _normalize(value: Any) -> Any:
    if isinstance(value, list):
        return [_normalize(entry) for entry in value]
    if isinstance(value, dict):
        return {key: _normalize(value[key]) for key in sorted(value)}
    return value  # TS `value ?? null`:Python 无 undefined,None 即 null,原样保留
