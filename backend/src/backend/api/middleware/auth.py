"""鉴权中间件(镜像 gold-bot apps/app-server/src/middleware/auth.ts + shared-contracts extractAuthToken)。

FastAPI 侧错误通过 HTTPException 抛出(401/403);纯函数部分与 TS 语义逐字一致。
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException

HeaderMap = dict[str, str]


def first_header(headers: HeaderMap, name: str) -> str | None:
    lower_name = name.lower()
    for key, value in headers.items():
        if key.lower() != lower_name:
            continue
        if isinstance(value, str) and len(value) > 0:
            return value
    return None


def query_token(url: str) -> str | None:
    token = parse_qs(urlsplit(url).query).get("token")
    return token[0] if token and len(token[0]) > 0 else None


def extract_auth_token(headers: HeaderMap, url: str) -> str | None:
    """X-API-Token / X-API-Key / ?token= 任一方式(大小写不敏感)。"""
    other = {k: v for k, v in headers.items() if k.lower() in ("x-api-token", "x-api-key")}
    return first_header(other, "X-API-Token") or first_header(other, "X-API-Key") or query_token(url)


def authorize_route_account(
    token_accounts: dict[str, set[str]] | None,
    token: str | None,
    account_id: str,
    admin_tokens: set[str],
) -> bool:
    """路由级账户授权;首次使用的空账户 token 自动绑定(镜像 authorizeRouteAccount)。"""
    if token_accounts is None:
        return True
    if token is None:
        return False
    if token in admin_tokens:
        return True
    accounts = token_accounts.get(token)
    if accounts is None or len(accounts) == 0:
        token_accounts[token] = {account_id}
        return True
    return account_id in accounts


def authorize_api_account(
    token_accounts: dict[str, set[str]] | None,
    token: str | None,
    account_id: str,
    admin_tokens: set[str],
) -> bool:
    """API 级账户授权(不自动绑定;镜像 authorizeApiAccount)。"""
    if token_accounts is None:
        return True
    if token is None:
        return False
    if token in admin_tokens:
        return True
    return token in token_accounts and account_id in token_accounts[token]


def require_route_token(valid_tokens: set[str] | None, headers: HeaderMap, url: str) -> str:
    """未带/无效 token 抛 401(镜像 requireRouteToken;valid_tokens 为空时一律 401)。"""
    token = extract_auth_token(headers, url)
    if token is None or valid_tokens is None or token not in valid_tokens:
        raise HTTPException(status_code=401, detail="invalid token")
    return token


def require_admin_route(valid_tokens: set[str] | None, admin_tokens: set[str], headers: HeaderMap, url: str) -> str:
    """非 admin 抛 403(镜像 requireAdminRoute)。"""
    token = require_route_token(valid_tokens, headers, url)
    if token not in admin_tokens:
        raise HTTPException(status_code=403, detail="admin only")
    return token
