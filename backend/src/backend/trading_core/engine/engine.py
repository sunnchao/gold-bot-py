"""Strategy 引擎(镜像 packages/trading-core/src/engine/engine.ts)。

1:1 移植:createStrategyEngine → create_strategy_engine、analyze → analyze、
validateStrategyName → validate_strategy_name;dict 键保持 TS 原样(camelCase)。
analyze 把 replay 的 run_replay 输出映射为 StrategyDecision,并始终
canProduceLiveCommands=False(只读审计)。
"""

from __future__ import annotations

from typing import Any

from backend.shared_contracts import is_ea_strategy_name
from backend.trading_core.replay.replay import run_replay

# ------------------------------------------------------------------ 类型别名

StrategyBar = dict[str, Any]
"""镜像 StrategyBar:time?/open/high/low/close/volume?。"""

StrategyInput = dict[str, Any]
"""镜像 StrategyInput:accountId/symbol/price/bars/aiResult?/ai_result?/smc?。"""

StrategySignal = dict[str, Any]
"""镜像 StrategySignal:strategy/side/entry/stopLoss/tp1/tp2?/score。"""

StrategyLog = dict[str, Any]
"""镜像 StrategyLog:level/strategy/message。"""

StrategyDecision = dict[str, Any]
"""镜像 StrategyDecision:decision/signal/logs/canProduceLiveCommands。"""

StrategyEngine = dict[str, Any]
"""镜像 StrategyEngine:{ analyze, validateStrategyName }。"""


def create_strategy_engine() -> StrategyEngine:
    """镜像 createStrategyEngine():返回 analyze + validateStrategyName 的实现绑定。"""
    return {
        "analyze": analyze,
        "validateStrategyName": validate_strategy_name,
    }


def analyze(_input: StrategyInput) -> StrategyDecision:
    """镜像 analyze():调用 runReplay 并把结果映射为 StrategyDecision。

    ai_result 优先级镜像 TS 的 `_input.ai_result ?? _input.aiResult`(null 时回退);
    replay 无信号时 decision='no_signal'、signal=None;日志 level/strategy/msg 映射为
    level/strategy/message。
    """
    replay = run_replay(
        {
            "account_id": _input.get("accountId"),
            "symbol": _input.get("symbol"),
            "current_price": _input.get("price"),
            "bars": _input.get("bars"),
            "ai_result": _input.get("ai_result") if _input.get("ai_result") is not None else _input.get("aiResult"),
            "smc": _input.get("smc"),
        }
    )
    logs = [
        {"level": entry["level"], "strategy": entry["strategy"], "message": entry["msg"]}
        for entry in replay["logs"]
    ]
    signal = replay["signal"]
    if signal is not None:
        return {
            "decision": "signal",
            "signal": {
                "strategy": validate_strategy_name(signal["strategy"]),
                "side": signal["side"],
                "entry": signal["entry"],
                "stopLoss": signal["stop_loss"],
                "tp1": signal["tp1"],
                "tp2": signal.get("tp2"),
                "score": signal["score"],
            },
            "logs": logs,
            "canProduceLiveCommands": False,
        }

    return {
        "decision": "no_signal",
        "signal": None,
        "logs": logs,
        "canProduceLiveCommands": False,
    }


def validate_strategy_name(value: str) -> str:
    """镜像 validateStrategyName():非 EA 策略名抛错,合法则原样返回。"""
    if not is_ea_strategy_name(value):
        raise ValueError(f"{value} is not an EA strategy name")
    return value
