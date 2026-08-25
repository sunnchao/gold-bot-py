"""trading-core 包入口脚手架(镜像 packages/trading-core/src/index.spec.ts)。"""

from __future__ import annotations

from backend.trading_core.index import evaluate_risk_gate, trading_core_status


def test_declares_that_live_command_production_is_disabled() -> None:
    assert trading_core_status["canProduceLiveCommands"] is False


def test_exports_the_riskgate_evaluator_from_the_package_entrypoint() -> None:
    assert callable(evaluate_risk_gate)
