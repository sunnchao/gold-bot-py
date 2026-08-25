"""镜像 apps/app-agent/src/tools/goldbot-api.ts。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import TypeAdapter

from backend.agents.types.schemas import GoldbotPayloadSchema, PendingSignalSchema
from backend.agents.utils.logger import get_logger

__all__ = ["GoldbotAPI", "GoldbotApiService"]

FETCH_TIMEOUT_S = 30.0
RETRY_ATTEMPTS = 3

_PendingAdapter: TypeAdapter[PendingSignalSchema | list[PendingSignalSchema]] = TypeAdapter(
    PendingSignalSchema | list[PendingSignalSchema]
)
_SymbolsAdapter: TypeAdapter[list[str]] = TypeAdapter(list[str])


class GoldbotAPI:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: Any = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._transport = transport
        self._client = client

    async def fetch_analysis_payload(self, account_id: str, symbol: str) -> dict[str, Any]:
        raw = await self._request(f"/api/v2/analysis_payload/{quote(account_id, safe='')}/{quote(symbol, safe='')}")
        return GoldbotPayloadSchema.model_validate(raw).model_dump()

    async def fetch_pending_signal(self, account_id: str, symbol: str) -> dict[str, Any] | None:
        try:
            raw = await self._request(f"/api/pending_signal/{quote(account_id, safe='')}/{quote(symbol, safe='')}")
            parsed = _PendingAdapter.validate_python(raw)
        except Exception as error:
            err_msg = str(error)
            if "404" in err_msg or "204" in err_msg:
                get_logger().debug({"accountId": account_id, "symbol": symbol}, "No pending signal found")
                return None
            raise
        if isinstance(parsed, list):
            first = parsed[0] if parsed else None
            return None if first is None else first.model_dump()
        return parsed.model_dump()

    async def fetch_account_symbols(self, account_id: str) -> dict[str, list[str]]:
        raw = await self._request(f"/api/ai_symbols/{quote(account_id, safe='')}")
        return {"symbols": _SymbolsAdapter.validate_python(raw)}

    async def fetch_accounts(self) -> list[dict[str, str]]:
        raw = await self._request("/api/v1/accounts")
        payload = raw if isinstance(raw, dict) else {}
        accounts = payload.get("accounts")
        if not isinstance(accounts, list):
            return []
        discovered: list[dict[str, str]] = []
        for item in accounts:
            if not isinstance(item, dict):
                continue
            account_id = item.get("account_id")
            if isinstance(account_id, str) and account_id.strip():
                discovered.append({"account_id": account_id.strip()})
        return discovered

    async def post_ai_result(self, account_id: str, symbol: str, result: Any) -> None:
        payload = result.model_dump() if hasattr(result, "model_dump") else result
        await self._request(
            f"/api/v2/ai_result/{quote(account_id, safe='')}/{quote(symbol, safe='')}",
            method="POST",
            json_body=payload,
        )

    async def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        json_body: Any | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        logger = get_logger()
        last_error: Exception | None = None
        for attempt in range(1, RETRY_ATTEMPTS + 2):
            try:
                logger.debug({"url": url, "method": method}, "GoldbotAPI request")
                async with self._client_ctx() as client:
                    response = await client.request(
                        method,
                        url,
                        json=json_body,
                        headers={
                            "Content-Type": "application/json",
                            "X-API-Token": self.token,
                        },
                        timeout=FETCH_TIMEOUT_S,
                    )
                if not response.is_success:
                    body = response.text or "no body"
                    raise RuntimeError(f"GoldbotAPI {method} {path} failed: {response.status_code} {body}")
                return response.json()
            except Exception as error:
                last_error = error
                retries_left = RETRY_ATTEMPTS + 1 - attempt
                if retries_left <= 0:
                    raise
                logger.warn(
                    {"attempt": attempt, "retriesLeft": retries_left, "path": path},
                    "GoldbotAPI request failed, retrying...",
                )
        assert last_error is not None
        raise last_error

    @asynccontextmanager
    async def _client_ctx(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(transport=self._transport) as client:
            yield client


class GoldbotApiService(GoldbotAPI):
    def __init__(self, config: Any, **kwargs: Any) -> None:
        goldbot = _goldbot_config(config)
        super().__init__(goldbot["apiUrl"], goldbot["apiToken"], **kwargs)


def _goldbot_config(config: Any) -> dict[str, str]:
    if hasattr(config, "goldbot"):
        goldbot = config.goldbot
        if callable(goldbot):
            goldbot = goldbot()
        if isinstance(goldbot, dict):
            return goldbot
    return {"apiUrl": "", "apiToken": ""}
