"""shared-contracts 常量与帮助函数(镜像 packages/shared-contracts/src/endpoint.ts + strategy.ts)。

放 EA_STRATEGY_NAMES / EA_COMPAT_ENDPOINTS 常量与其帮助函数;
其它共享常量已存在于 backend/src/backend/persistence/records.py,不在此重复。
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

# ---------------------------------------------------------------------------
# EA 兼容端点(与 shared-contracts/src/endpoint.ts 逐字一致)
# ---------------------------------------------------------------------------

EA_COMPAT_ENDPOINTS = (
    "/register",
    "/heartbeat",
    "/tick",
    "/bars",
    "/positions",
    "/poll",
    "/order_result",
)
_EA_COMPAT_ENDPOINT_SET = frozenset(EA_COMPAT_ENDPOINTS)


def is_ea_compat_endpoint(value: str) -> bool:
    """镜像 isEaCompatEndpoint。"""
    return value in _EA_COMPAT_ENDPOINT_SET


def extract_auth_token(headers: dict[str, Any], url: str) -> str | None:
    """镜像 extractAuthToken:X-API-Token → X-API-Key → ?token=。"""
    return _first_header(headers, "X-API-Token") or _first_header(headers, "X-API-Key") or _query_token(url)


def _first_header(headers: dict[str, Any], name: str) -> str | None:
    lower_name = name.lower()
    for key, value in headers.items():
        if key.lower() != lower_name:
            continue
        token = value[0] if isinstance(value, list) else value
        if isinstance(token, str) and len(token) > 0:
            return token
        return None
    return None


def _query_token(url: str) -> str | None:
    token = parse_qs(urlsplit(url).query).get("token")
    return token[0] if token and len(token[0]) > 0 else None


# ---------------------------------------------------------------------------
# EA 策略名(与 shared-contracts/src/strategy.ts 逐字一致,顺序不变)
# ---------------------------------------------------------------------------

EA_STRATEGY_NAMES = (
    "pullback",
    "breakout_retest",
    "divergence",
    "breakout_pyramid",
    "counter_pullback",
    "scale_in",
    "range",
    "momentum_scalp",
    "ai_signal",
)
EaStrategyName = Literal[
    "pullback",
    "breakout_retest",
    "divergence",
    "breakout_pyramid",
    "counter_pullback",
    "scale_in",
    "range",
    "momentum_scalp",
    "ai_signal",
]

_STRATEGY_NAME_SET = frozenset(EA_STRATEGY_NAMES)


def is_ea_strategy_name(value: str) -> bool:
    """镜像 isEaStrategyName:是否为 EA 认可的策略名。"""
    return value in _STRATEGY_NAME_SET


def parse_oracle_fixture(value: object) -> dict[str, Any]:
    """镜像 parseOracleFixture:校验 oracle 夹具形状。"""
    fixture = _expect_record(value, "fixture")
    parsed: dict[str, Any] = {
        "fixture": _expect_string(fixture.get("fixture"), "fixture.fixture"),
        "oracle": _parse_oracle(fixture.get("oracle")),
    }
    if "request" in fixture:
        parsed["request"] = _parse_request(fixture.get("request"), "fixture.request")
    if "response" in fixture:
        parsed["response"] = _parse_response(fixture.get("response"), "fixture.response")
    if "cases" in fixture:
        parsed["cases"] = [
            _parse_case(item, f"fixture.cases[{index}]")
            for index, item in enumerate(_expect_array(fixture.get("cases"), "fixture.cases"))
        ]
    if "normalization" in fixture:
        parsed["normalization"] = _expect_record(fixture.get("normalization"), "fixture.normalization")
    if parsed.get("cases") is None and (parsed.get("request") is None or parsed.get("response") is None):
        raise ValueError("fixture must include request/response or cases")
    return parsed


def _parse_oracle(value: object) -> dict[str, str]:
    oracle = _expect_record(value, "fixture.oracle")
    return {
        "source": _expect_string(oracle.get("source"), "fixture.oracle.source"),
        "head": _expect_string(oracle.get("head"), "fixture.oracle.head"),
    }


def _parse_case(value: object, path: str) -> dict[str, Any]:
    fixture_case = _expect_record(value, path)
    return {
        "name": _expect_string(fixture_case.get("name"), f"{path}.name"),
        "request": _parse_request(fixture_case.get("request"), f"{path}.request"),
        "response": _parse_response(fixture_case.get("response"), f"{path}.response"),
    }


def _parse_request(value: object, path: str) -> dict[str, Any]:
    request = _expect_record(value, path)
    parsed: dict[str, Any] = {
        "method": _expect_string(request.get("method"), f"{path}.method"),
        "path": _expect_string(request.get("path"), f"{path}.path"),
    }
    if "headers" in request:
        parsed["headers"] = _expect_record(request.get("headers"), f"{path}.headers")
    if "body" in request:
        parsed["body"] = request.get("body")
    return parsed


def _parse_response(value: object, path: str) -> dict[str, Any]:
    response = _expect_record(value, path)
    parsed: dict[str, Any] = {
        "status_code": _expect_number(response.get("status_code"), f"{path}.status_code"),
    }
    if "headers" in response:
        parsed["headers"] = _expect_record(response.get("headers"), f"{path}.headers")
    if "body" in response:
        parsed["body"] = response.get("body")
    if "body_ref" in response:
        parsed["body_ref"] = _expect_string(response.get("body_ref"), f"{path}.body_ref")
    if "frames" in response:
        parsed["frames"] = [
            _expect_string(frame, f"{path}.frames[{index}]")
            for index, frame in enumerate(_expect_array(response.get("frames"), f"{path}.frames"))
        ]
    return parsed


def _expect_record(value: object, path: str) -> dict[str, Any]:
    if value is None or not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _expect_string(value: object, path: str) -> str:
    if not isinstance(value, str) or len(value) == 0:
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _expect_number(value: object, path: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        raise ValueError(f"{path} must be a finite number")
    return value


def _expect_array(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    return value


__all__ = [
    "EA_COMPAT_ENDPOINTS",
    "EA_STRATEGY_NAMES",
    "EaStrategyName",
    "extract_auth_token",
    "is_ea_compat_endpoint",
    "is_ea_strategy_name",
    "parse_oracle_fixture",
]
