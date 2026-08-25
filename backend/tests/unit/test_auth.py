"""鉴权中间件测试(镜像 shared-contracts/src/endpoint.spec.ts + middleware/auth.ts 语义)。"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from backend.api.middleware.auth import (
    authorize_api_account,
    authorize_route_account,
    extract_auth_token,
    require_admin_route,
    require_route_token,
)


class TestExtractAuthToken:
    def test_header_token(self) -> None:
        assert extract_auth_token({"X-API-Token": "tok-1"}, "/poll") == "tok-1"

    def test_header_token_case_insensitive(self) -> None:
        assert extract_auth_token({"x-api-token": "tok-1"}, "/poll") == "tok-1"

    def test_api_key_header_fallback(self) -> None:
        assert extract_auth_token({"X-API-Key": "tok-2"}, "/poll") == "tok-2"
        # X-API-Token 优先于 X-API-Key
        assert extract_auth_token({"X-API-Key": "a", "X-API-Token": "b"}, "/poll") == "b"

    def test_query_token(self) -> None:
        assert extract_auth_token({}, "/api/v1/events/stream?token=tok-3") == "tok-3"
        assert extract_auth_token({}, "/poll?token=") is None
        assert extract_auth_token({"X-API-Key": "tok-2"}, "/poll?token=tok-3") == "tok-2"

    def test_no_token(self) -> None:
        assert extract_auth_token({}, "/poll") is None

    def test_matches_ea_compat_endpoint_behavior(self) -> None:
        # 与 shared-contracts endpoint.spec 的边界对齐:空 header 值视为无 token
        assert extract_auth_token({"X-API-Token": ""}, "/poll") is None


class TestRequireRouteToken:
    def test_valid_token_passes(self) -> None:
        assert require_route_token({"tok-1"}, {"X-API-Token": "tok-1"}, "/poll") == "tok-1"

    @pytest.mark.parametrize(
        "headers,url",
        [
            ({}, "/poll"),
            ({"X-API-Token": "bad"}, "/poll"),
            ({"X-API-Token": "bad"}, "/poll?token=tok-1"),
        ],
    )
    def test_invalid_token_raises_401(self, headers, url) -> None:
        with pytest.raises(HTTPException) as exc:
            require_route_token({"tok-1"}, headers, url)
        assert exc.value.status_code == 401

    def test_null_valid_tokens_always_rejects(self) -> None:
        # 镜像 TS:validTokens == null → 总是 401(即使带了 token)
        with pytest.raises(HTTPException) as exc:
            require_route_token(None, {"X-API-Token": "anything"}, "/poll")
        assert exc.value.status_code == 401


class TestRequireAdminRoute:
    def test_admin_ok(self) -> None:
        assert require_admin_route({"admin"}, {"admin"}, {"X-API-Token": "admin"}, "/api/v1/overview") == "admin"

    def test_regular_token_forbidden(self) -> None:
        with pytest.raises(HTTPException) as exc:
            require_admin_route({"tok-1", "admin"}, {"admin"}, {"X-API-Token": "tok-1"}, "/api/v1/overview")
        assert exc.value.status_code == 403
        assert exc.value.detail == "admin only"

    def test_no_token_401_before_403(self) -> None:
        with pytest.raises(HTTPException) as exc:
            require_admin_route({"admin"}, {"admin"}, {}, "/api/v1/overview")
        assert exc.value.status_code == 401


class TestAuthorize:
    def test_route_account_auto_binds_empty_token(self) -> None:
        token_accounts: dict[str, set[str]] = {"tok-1": set()}
        assert authorize_route_account(token_accounts, "tok-1", "acc-1", set()) is True
        assert token_accounts["tok-1"] == {"acc-1"}

    def test_route_account_admin_bypass(self) -> None:
        assert authorize_route_account({"tok-1": {"acc-2"}}, "tok-admin", "acc-1", {"tok-admin"}) is True

    def test_route_account_requires_binding(self) -> None:
        assert authorize_route_account({"tok-1": {"acc-2"}}, "tok-1", "acc-1", set()) is False
        assert authorize_route_account({"tok-1": {"acc-2"}}, "tok-1", "acc-2", set()) is True

    def test_route_account_null_map_allows_all(self) -> None:
        assert authorize_route_account(None, "whatever", "acc-1", set()) is True

    def test_api_account_no_auto_bind(self) -> None:
        token_accounts: dict[str, set[str]] = {"tok-1": set()}
        assert authorize_api_account(token_accounts, "tok-1", "acc-1", set()) is False

    def test_api_account_admin_bypass(self) -> None:
        assert authorize_api_account({"tok-1": {"acc-2"}}, "tok-admin", "acc-1", {"tok-admin"}) is True

    def test_api_account_bound_ok(self) -> None:
        assert authorize_api_account({"tok-1": {"acc-1"}}, "tok-1", "acc-1", set()) is True
        assert authorize_api_account({"tok-1": {"acc-1"}}, "tok-1", "acc-2", set()) is False
