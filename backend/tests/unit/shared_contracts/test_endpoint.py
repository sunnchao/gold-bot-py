"""镜像 packages/shared-contracts/src/endpoint.spec.ts。"""

from __future__ import annotations

from backend.shared_contracts import EA_COMPAT_ENDPOINTS, extract_auth_token, is_ea_compat_endpoint


def test_freezes_the_legacy_ea_route_list() -> None:
    assert list(EA_COMPAT_ENDPOINTS) == [
        "/register",
        "/heartbeat",
        "/tick",
        "/bars",
        "/positions",
        "/poll",
        "/order_result",
    ]


def test_rejects_unknown_endpoint_values() -> None:
    assert is_ea_compat_endpoint("/register") is True
    assert is_ea_compat_endpoint("/api/analysis_payload/90011087") is False
    assert is_ea_compat_endpoint("/orders") is False


def test_extracts_auth_tokens_with_go_compatible_priority() -> None:
    assert (
        extract_auth_token(
            {"x-api-token": "primary", "x-api-key": "secondary"},
            "/poll?token=query",
        )
        == "primary"
    )
    assert extract_auth_token({"X-API-Key": "secondary"}, "/poll?token=query") == "secondary"
    assert extract_auth_token({}, "/poll?token=query") == "query"
    assert extract_auth_token({}, "/poll") is None
