"""Agent 分析结果查询(镜像 apps/app-agent/src/results/results.controller.ts)。

limit 语义:absent → 10;parseInt 失败/NaN/<1/>100 → BadRequest。
"""

from __future__ import annotations

from typing import Any


class ResultsError(Exception):
    """镜像 NestJS BadRequestException。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def get_results(
    store: Any,
    account_id: str,
    symbol: str,
    limit_param: str | None = None,
) -> dict[str, Any]:
    limit = 10 if limit_param is None else _parse_int(limit_param)
    if limit is None or limit < 1 or limit > 100:
        raise ResultsError("limit must be between 1 and 100")

    results = store.get_recent_results(account_id, symbol, limit)
    return {
        "accountId": account_id,
        "symbol": symbol,
        "count": len(results),
        "results": results,
    }


def _parse_int(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
