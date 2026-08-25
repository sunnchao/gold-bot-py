"""仲裁服务集成测试(1:1 镜像 apps/app-server/src/services/arbitration/service.spec.ts)。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.persistence.store import create_in_memory_store
from backend.services.arbitration.index import ArbitrationManager, default_arbitration_config


def make_signal(overrides: dict | None = None) -> dict:
    signal = {
        "side": "buy",
        "entry": 2350,
        "stop_loss": 2340,
        "tp1": 2360,
        "tp2": 2370,
        "score": 9,
        "strategy": "pullback",
        "atr": 5,
        "scale_in_parent_ticket": 0,
        "weighted_avg_entry": 0,
        "unified_sl": 0,
        "scale_in_count": 0,
    }
    if overrides:
        signal.update(overrides)
    return signal


def dt(ms: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)


class AbortSignal:
    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


async def test_exposes_default_config_matching_go_defaults() -> None:
    cfg = default_arbitration_config()
    assert cfg["max_wait_ms"] == 30_000
    assert cfg["timeout_auto_pass_score"] == 8
    assert cfg["poll_interval_ms"] == 1_000
    assert cfg["pending_signal_ttl_ms"] == 5 * 60 * 1_000


async def test_auto_passes_on_timeout_when_score_above_threshold() -> None:
    store = create_in_memory_store()
    sleeps: list[int] = []
    now_ms = 0

    async def sleep(ms: int) -> None:
        sleeps.append(ms)
        nonlocal now_ms
        now_ms += 15

    manager = ArbitrationManager(
        store=store,
        config={"max_wait_ms": 30, "poll_interval_ms": 10, "timeout_auto_pass_score": 8},
        sleep=sleep,
        now=lambda: dt(now_ms),
    )

    verdict = await manager.submit_signal("90011087", "XAUUSD", make_signal({"score": 9}))
    assert verdict["execute"] is True
    assert verdict["reason"] == "timeout_auto_pass"
    assert verdict["result"]["status"] == "timeout"
    assert len(sleeps) > 0


async def test_abandons_on_timeout_when_score_below_threshold() -> None:
    store = create_in_memory_store()
    now_ms = 0

    async def sleep(ms: int) -> None:
        nonlocal now_ms
        now_ms += 15

    manager = ArbitrationManager(
        store=store,
        config={"max_wait_ms": 25, "poll_interval_ms": 10, "timeout_auto_pass_score": 8},
        sleep=sleep,
        now=lambda: dt(now_ms),
    )

    verdict = await manager.submit_signal("90011087", "XAUUSD", make_signal({"score": 5}))
    assert verdict["execute"] is False
    assert verdict["reason"] == "timeout_abandoned"


async def test_returns_approved_when_admin_updates_result_before_timeout() -> None:
    store = create_in_memory_store()
    signal_id = 0

    async def sleep(ms: int) -> None:
        nonlocal signal_id
        # 首次 poll 时批准 signal
        if signal_id == 0:
            pending = await store.get_pending_signals("90011087", "XAUUSD")
            signal_id = pending[0].get("id", 0)
            await store.update_pending_signal_arbitration(signal_id, "approved", "manual_review")

    manager = ArbitrationManager(
        store=store,
        config={"max_wait_ms": 10_000, "poll_interval_ms": 5, "timeout_auto_pass_score": 8},
        sleep=sleep,
        now=lambda: dt(0),
    )

    verdict = await manager.submit_signal("90011087", "XAUUSD", make_signal({"score": 9}))
    assert verdict["execute"] is True
    assert verdict["reason"] == "manual_review"
    assert verdict["result"]["status"] == "approved"


async def test_returns_rejected_when_admin_rejects_before_timeout() -> None:
    store = create_in_memory_store()
    approved = False

    async def sleep(ms: int) -> None:
        nonlocal approved
        if not approved:
            approved = True
            pending = await store.get_pending_signals("90011087", "XAUUSD")
            signal_id = pending[0].get("id", 0)
            await store.update_pending_signal_arbitration(signal_id, "rejected", "too_risky")

    manager = ArbitrationManager(
        store=store,
        config={"max_wait_ms": 10_000, "poll_interval_ms": 5, "timeout_auto_pass_score": 8},
        sleep=sleep,
        now=lambda: dt(0),
    )

    verdict = await manager.submit_signal("90011087", "XAUUSD", make_signal({"score": 9}))
    assert verdict["execute"] is False
    assert verdict["reason"] == "too_risky"
    assert verdict["result"]["status"] == "rejected"


async def test_saves_pending_signal_with_5min_expiration_and_side_score_strategy() -> None:
    store = create_in_memory_store()
    now_ms = 0

    async def sleep(ms: int) -> None:
        nonlocal now_ms
        now_ms += 10

    manager = ArbitrationManager(
        store=store,
        config={"max_wait_ms": 5, "poll_interval_ms": 5, "timeout_auto_pass_score": 8},
        sleep=sleep,
        now=lambda: dt(now_ms),
    )

    await manager.submit_signal(
        "90011087", "XAUUSD", make_signal({"side": "sell", "score": 10, "strategy": "momentum_scalp"})
    )
    pending = await store.get_pending_signals("90011087", "XAUUSD")
    assert len(pending) == 1
    signal = pending[0]
    assert str(signal["status"]) == "pending"
    assert str(signal["side"]) == "sell"
    assert signal["score"] == 10
    assert str(signal["strategy"]) == "momentum_scalp"
    assert str(signal["created_at"]) == "1970-01-01T00:00:00.000Z"
    # Go 默认 5 分钟过期
    assert str(signal["expires_at"]) == "1970-01-01T00:05:00.000Z"
    indicators = str(signal["indicators"])
    assert '"side":"sell"' in indicators
    assert '"strategy":"momentum_scalp"' in indicators
    assert '"score":10' in indicators


async def test_serializes_all_strategies_when_present() -> None:
    store = create_in_memory_store()
    now_ms = 0

    async def sleep(ms: int) -> None:
        nonlocal now_ms
        now_ms += 10

    manager = ArbitrationManager(
        store=store,
        config={"max_wait_ms": 5, "poll_interval_ms": 5, "timeout_auto_pass_score": 8},
        sleep=sleep,
        now=lambda: dt(now_ms),
    )

    await manager.submit_signal("90011087", "XAUUSD", make_signal({"all_strategies": ["pullback", "momentum_scalp"]}))
    pending = await store.get_pending_signals("90011087", "XAUUSD")
    assert '"all_strategies":["pullback","momentum_scalp"]' in str(pending[0]["indicators"])


async def test_delegates_expire_stale_signals_get_pending_signals_update_arbitration_result_to_store() -> None:
    store = create_in_memory_store()

    async def sleep(ms: int) -> None:
        pass

    manager = ArbitrationManager(
        store=store,
        config={"max_wait_ms": 5, "poll_interval_ms": 5, "timeout_auto_pass_score": 8},
        sleep=sleep,
        now=lambda: dt(0),
    )

    await store.save_pending_signal(
        {
            "account_id": "90011087",
            "symbol": "XAUUSD",
            "status": "pending",
            "created_at": "1970-01-01T00:00:00.000Z",
            "expires_at": "1970-01-01T00:00:00.000Z",
        }
    )
    pending = await manager.get_pending_signals("90011087", "XAUUSD")
    assert len(pending) == 1
    signal_id = int(pending[0]["id"])
    assert await manager.update_arbitration_result(signal_id, "approved", "manual") is True
    expired = await manager.expire_stale_signals()
    assert expired == 0


async def test_respects_abort_signal_cancellation() -> None:
    store = create_in_memory_store()
    controller = AbortSignal()

    async def sleep(ms: int) -> None:
        controller.abort()

    manager = ArbitrationManager(
        store=store,
        signal=lambda: controller,
        config={"max_wait_ms": 10_000, "poll_interval_ms": 5, "timeout_auto_pass_score": 8},
        sleep=sleep,
        now=lambda: dt(0),
    )

    verdict = await manager.submit_signal("90011087", "XAUUSD", make_signal({"score": 9}))
    assert verdict["result"]["status"] == "timeout"
    assert verdict["result"]["reason"] == "context_cancelled"
    assert verdict["execute"] is True  # score 9 >= 8 自动放行


async def test_tracks_active_pending_signals_during_submit() -> None:
    store = create_in_memory_store()
    active_during_wait = -1
    now_ms = 0
    holder: dict[str, ArbitrationManager] = {}

    async def sleep(ms: int) -> None:
        nonlocal active_during_wait, now_ms
        active_during_wait = holder["manager"].active_count()
        now_ms += 10

    holder["manager"] = ArbitrationManager(
        store=store,
        config={"max_wait_ms": 5, "poll_interval_ms": 5, "timeout_auto_pass_score": 8},
        sleep=sleep,
        now=lambda: dt(now_ms),
    )

    await holder["manager"].submit_signal("90011087", "XAUUSD", make_signal({"score": 9}))
    assert active_during_wait == 1
    assert holder["manager"].active_count() == 0


async def test_uses_provided_log_callback() -> None:
    store = create_in_memory_store()
    logs: list[str] = []
    now_ms = 0

    async def sleep(ms: int) -> None:
        nonlocal now_ms
        now_ms += 10

    manager = ArbitrationManager(
        store=store,
        config={"max_wait_ms": 5, "poll_interval_ms": 5, "timeout_auto_pass_score": 8},
        sleep=sleep,
        now=lambda: dt(now_ms),
        log=logs.append,
    )

    await manager.submit_signal("90011087", "XAUUSD", make_signal({"score": 9}))
    assert any("submit" in entry for entry in logs)
    assert any("timeout auto-pass" in entry for entry in logs)
