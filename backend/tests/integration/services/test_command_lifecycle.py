"""命令生命周期集成测试(1:1 镜像 src/services/command-lifecycle/service.spec.ts)。"""

from __future__ import annotations

from backend.persistence.store import create_in_memory_store
from backend.services.command_lifecycle.index import CommandLifecycleService
from backend.services.shadow.index import ShadowService


async def test_keeps_candidates_shadow_only_when_account_not_in_cutover_mode() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode("90011087", "shadow")
    shadow = ShadowService(store, lambda: "2026-04-13T08:00:00.000Z")
    service = CommandLifecycleService(store, "oracle", shadow)

    stored = await service.accept_candidate(
        "90011087",
        {
            "command_id": "shadow_cmd",
            "action": "SIGNAL",
            "source": "ai_result",
            "symbol": "XAUUSD",
            "strategy": "ai_signal",
        },
    )

    assert stored["status"] == "shadow_only"
    assert await store.poll_commands("90011087") == []
    assert await store.list_shadow_comparisons() == [
        {
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "protocol_ok": True,
            "signal_drift": False,
            "command_drift": False,
            "oracle_compared": False,
            "source": "ai_result",
            "created_at": stored["created_at"],
        }
    ]


async def test_queues_candidates_only_for_cutover_accounts() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode("90011087", "cutover")
    service = CommandLifecycleService(store)

    stored = await service.accept_candidate(
        "90011087",
        {
            "command_id": "cutover_cmd",
            "action": "SIGNAL",
            "source": "ai_result",
            "symbol": "XAUUSD",
            "strategy": "ai_signal",
        },
    )

    assert stored["status"] == "queued"
    assert len(await store.poll_commands("90011087")) == 1
    comparisons = await store.list_shadow_comparisons()
    assert len(comparisons) == 1
    assert comparisons[0]["account_id"] == "90011087"
    assert comparisons[0]["symbol"] == "XAUUSD"
    assert comparisons[0]["oracle_compared"] is False
    assert comparisons[0]["source"] == "ai_result"


async def test_records_ai_approve_commands_under_ai_result_shadow_source() -> None:
    store = create_in_memory_store()
    await store.set_runtime_mode("90011087", "shadow")
    shadow = ShadowService(store, lambda: "2026-04-13T08:00:00.000Z")
    service = CommandLifecycleService(store, "oracle", shadow)

    stored = await service.accept_candidate(
        "90011087",
        {
            "command_id": "ai_pending_90011087_XAUUSD_buy",
            "action": "SIGNAL",
            "source": "ai_approve",
            "symbol": "XAUUSD",
            "strategy": "ai_signal",
        },
    )

    assert stored["source"] == "ai_approve"
    snapshot = await store.get_latest_shadow_snapshot("90011087", "XAUUSD", "ai_result")
    assert snapshot is not None
    assert snapshot["account_id"] == "90011087"
    assert snapshot["symbol"] == "XAUUSD"
    assert snapshot["source"] == "ai_result"
    assert snapshot["command"]["source"] == "ai_approve"
    comparisons = await store.list_shadow_comparisons()
    assert len(comparisons) == 1
    assert comparisons[0]["account_id"] == "90011087"
    assert comparisons[0]["symbol"] == "XAUUSD"
    assert comparisons[0]["source"] == "ai_result"


async def test_uses_configured_shadow_default_when_no_explicit_runtime_mode_stored() -> None:
    store = create_in_memory_store()
    service = CommandLifecycleService(store, "shadow")

    stored = await service.accept_candidate(
        "90011087",
        {
            "command_id": "default_shadow_cmd",
            "action": "SIGNAL",
            "source": "ai_result",
            "symbol": "XAUUSD",
            "strategy": "ai_signal",
        },
    )

    assert stored["status"] == "shadow_only"
    assert await store.poll_commands("90011087") == []


async def test_uses_configured_cutover_default_when_no_explicit_runtime_mode_stored() -> None:
    store = create_in_memory_store()
    service = CommandLifecycleService(store, "cutover")

    stored = await service.accept_candidate(
        "90011087",
        {
            "command_id": "default_cutover_cmd",
            "action": "SIGNAL",
            "source": "live_strategy",
            "symbol": "XAUUSD",
            "strategy": "pullback",
        },
    )

    assert stored["status"] == "queued"
    polled = await store.poll_commands("90011087")
    assert len(polled) == 1
    assert polled[0]["command_id"] == "default_cutover_cmd"
    assert polled[0]["action"] == "SIGNAL"
