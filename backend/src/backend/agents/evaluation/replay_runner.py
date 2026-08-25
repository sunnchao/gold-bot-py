"""Replay fixture capture / metrics(镜像 apps/app-agent/src/evaluation/replay-runner.ts)。"""

from __future__ import annotations

import math
import re
from collections.abc import Awaitable, Callable
from typing import Any

REPLAY_FIXTURE_SCHEMA_VERSION = "replay_fixture.v1"
REPLAY_REPORT_SCHEMA_VERSION = "replay_report.v1"
REDACTED = "[REDACTED]"

_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|authorization|bearer|token|secret|password|passwd|webhook|cookie|signature|sign)$",
    re.IGNORECASE,
)


def _redact_string(value: str) -> str:
    value = re.sub(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", f"Bearer {REDACTED}", value, flags=re.IGNORECASE)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{6,}", REDACTED, value)
    value = re.sub(
        r"\b((?:api[_-]?key|token|secret|password|authorization)=)[^\s&\"']+",
        lambda m: f"{m.group(1)}{REDACTED}",
        value,
        flags=re.IGNORECASE,
    )
    return value


def _redact_unknown(value: Any, seen: set[int]) -> Any:
    if isinstance(value, str):
        return _redact_string(value)
    if value is None or not isinstance(value, (list, dict)):
        return value
    object_id = id(value)
    if object_id in seen:
        return "[Circular]"
    seen.add(object_id)
    if isinstance(value, list):
        return [_redact_unknown(entry, seen) for entry in value]
    redacted: dict[str, Any] = {}
    for key, entry in value.items():
        redacted[key] = (
            REDACTED if _SECRET_KEY_PATTERN.search(key) else _redact_unknown(entry, seen)
        )
    return redacted


def redact_secrets(value: Any) -> Any:
    return _redact_unknown(value, set())


def capture_replay_fixture(input_: dict[str, Any]) -> dict[str, Any]:
    fixture: dict[str, Any] = {
        "schema_version": REPLAY_FIXTURE_SCHEMA_VERSION,
        "fixture_id": input_["fixtureId"],
        "captured_at": input_.get("capturedAt") or _now_iso(),
        "account_id": input_["accountId"],
        "symbol": input_["symbol"],
        "analysis_payload": redact_secrets(input_["analysisPayload"]),
        "llm_responses": [
            redact_secrets(
                {
                    "agent": response.get("agent"),
                    "model": response.get("model"),
                    "prompt": response.get("prompt"),
                    "system_prompt": response.get("systemPrompt"),
                    "raw_response": response.get("rawResponse"),
                    "parsed_output": response.get("parsedOutput"),
                    "parse_error": response.get("parseError"),
                }
            )
            for response in input_.get("llmResponses", [])
        ],
        "parsed_outputs": redact_secrets(input_.get("parsedOutputs") or {}),
    }
    if input_.get("pendingSignal") is not None:
        fixture["pending_signal"] = redact_secrets(input_["pendingSignal"])
    if input_.get("finalSignal") is not None:
        fixture["final_signal"] = redact_secrets(input_["finalSignal"])
    if input_.get("finalTradePlan") is not None:
        fixture["final_trade_plan"] = redact_secrets(input_["finalTradePlan"])
    return fixture


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _parse_failures_from_responses(responses: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for response in responses:
        if response.get("parse_error") or response.get("parsed_output") is None:
            if response.get("parse_error"):
                failures.append(f"{response.get('agent')}: {response['parse_error']}")
            else:
                failures.append(str(response.get("agent")))
    return failures


def fixture_to_candidate_result(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "fixture_id": fixture["fixture_id"],
        "account_id": fixture["account_id"],
        "symbol": fixture["symbol"],
        "source": "fixture",
        "parsed_outputs": fixture.get("parsed_outputs") or {},
        "raw_llm_responses": fixture.get("llm_responses"),
        "final_signal": fixture.get("final_signal"),
        "trade_plan": fixture.get("final_trade_plan"),
        "parse_failures": _parse_failures_from_responses(fixture.get("llm_responses") or []),
    }


ReplayCandidateRunner = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


async def replay_fixture(fixture: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    if options.get("allowLiveLlm"):
        live_runner = options.get("liveRunner")
        if not live_runner:
            raise ValueError("allowLiveLlm requires a liveRunner")
        return await live_runner(fixture)
    return fixture_to_candidate_result(fixture)


def _by_fixture_id(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {result["fixture_id"]: result for result in results}


def _direction_of(result: dict[str, Any]) -> str | None:
    trade_plan = result.get("trade_plan") or {}
    final_signal = result.get("final_signal") or {}
    arbitration = final_signal.get("arbitration") or {}
    return (
        trade_plan.get("side")
        or arbitration.get("direction")
        or final_signal.get("bias")
    )


def _mode_of(result: dict[str, Any]) -> str | None:
    trade_plan = result.get("trade_plan") or {}
    final_signal = result.get("final_signal") or {}
    arbitration = final_signal.get("arbitration") or {}
    return trade_plan.get("mode") or arbitration.get("action")


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _stop_loss_of(result: dict[str, Any]) -> float | None:
    trade_plan = result.get("trade_plan") or {}
    final_signal = result.get("final_signal") or {}
    return _number(trade_plan.get("stop_loss") if "stop_loss" in trade_plan else final_signal.get("suggested_sl"))


def _max_lots_of(result: dict[str, Any]) -> float | None:
    trade_plan = result.get("trade_plan") or {}
    final_signal = result.get("final_signal") or {}
    return _number(trade_plan.get("max_lots") if "max_lots" in trade_plan else final_signal.get("max_position_size"))


def _round(value: float) -> float:
    return float(f"{value:.6f}")


def _rate(count: int, total: int) -> float:
    return 0.0 if total == 0 else _round(count / total)


def _average(values: list[float]) -> float:
    if len(values) == 0:
        return 0.0
    return _round(sum(values) / len(values))


def compute_replay_metrics(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options = options or {}
    baseline_by_id = _by_fixture_id(baseline)
    stop_loss_tolerance = float(options.get("stopLossTolerance") or 0)
    max_lots_tolerance = float(options.get("maxLotsTolerance") or 0)

    compared_fixtures = 0
    direction_drift_count = 0
    mode_drift_count = 0
    stop_loss_drift_count = 0
    max_lots_drift_count = 0
    stop_loss_deltas: list[float] = []
    max_lots_deltas: list[float] = []

    for candidate_result in candidate:
        baseline_result = baseline_by_id.get(candidate_result["fixture_id"])
        if baseline_result is None:
            continue
        compared_fixtures += 1

        baseline_direction = _direction_of(baseline_result)
        candidate_direction = _direction_of(candidate_result)
        if (
            baseline_direction is not None
            and candidate_direction is not None
            and baseline_direction != candidate_direction
        ):
            direction_drift_count += 1

        baseline_mode = _mode_of(baseline_result)
        candidate_mode = _mode_of(candidate_result)
        if baseline_mode is not None and candidate_mode is not None and baseline_mode != candidate_mode:
            mode_drift_count += 1

        baseline_stop_loss = _stop_loss_of(baseline_result)
        candidate_stop_loss = _stop_loss_of(candidate_result)
        if baseline_stop_loss is not None and candidate_stop_loss is not None:
            delta = _round(abs(candidate_stop_loss - baseline_stop_loss))
            stop_loss_deltas.append(delta)
            if delta > stop_loss_tolerance:
                stop_loss_drift_count += 1

        baseline_max_lots = _max_lots_of(baseline_result)
        candidate_max_lots = _max_lots_of(candidate_result)
        if baseline_max_lots is not None and candidate_max_lots is not None:
            delta = _round(abs(candidate_max_lots - baseline_max_lots))
            max_lots_deltas.append(delta)
            if delta > max_lots_tolerance:
                max_lots_drift_count += 1

    parse_failure_count = sum(1 for result in candidate if len(result.get("parse_failures") or []) > 0)
    total_fixtures = len(candidate)

    return {
        "total_fixtures": total_fixtures,
        "compared_fixtures": compared_fixtures,
        "parse_failure_count": parse_failure_count,
        "parse_failure_rate": _rate(parse_failure_count, total_fixtures),
        "direction_drift_count": direction_drift_count,
        "direction_drift_rate": _rate(direction_drift_count, compared_fixtures),
        "mode_drift_count": mode_drift_count,
        "mode_drift_rate": _rate(mode_drift_count, compared_fixtures),
        "stop_loss_drift_count": stop_loss_drift_count,
        "stop_loss_drift_rate": _rate(stop_loss_drift_count, compared_fixtures),
        "stop_loss_average_abs_delta": _average(stop_loss_deltas),
        "stop_loss_max_abs_delta": max(stop_loss_deltas) if stop_loss_deltas else 0,
        "max_lots_drift_count": max_lots_drift_count,
        "max_lots_drift_rate": _rate(max_lots_drift_count, compared_fixtures),
        "max_lots_average_abs_delta": _average(max_lots_deltas),
        "max_lots_max_abs_delta": max(max_lots_deltas) if max_lots_deltas else 0,
    }


async def replay_fixtures(fixtures: list[dict[str, Any]], options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = options or {}
    baseline_results = [await replay_fixture(fixture) for fixture in fixtures]
    candidate_runner = options.get("candidateRunner")
    candidate_results = (
        [await candidate_runner(fixture) for fixture in fixtures]
        if candidate_runner
        else baseline_results
    )
    return {
        "schema_version": REPLAY_REPORT_SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "fixture_count": len(fixtures),
        "baseline_results": baseline_results,
        "candidate_results": candidate_results,
        "metrics": compute_replay_metrics(baseline_results, candidate_results, options.get("metrics")),
    }


class ReplayEvaluationService:
    def capture_fixture(self, input_: dict[str, Any]) -> dict[str, Any]:
        return capture_replay_fixture(input_)

    async def replay_fixture(self, fixture: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
        return await replay_fixture(fixture, options)

    async def replay_fixtures(
        self, fixtures: list[dict[str, Any]], options: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return await replay_fixtures(fixtures, options)

    def compute_metrics(
        self,
        baseline: list[dict[str, Any]],
        candidate: list[dict[str, Any]],
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return compute_replay_metrics(baseline, candidate, options)
