"""AI approve 服务出口(镜像 apps/app-server/src/services/ai-approve 的 rules/gate/command)。

模块级函数与 TS 一一对应:rules 的 *_ai_approve_* 规则、gate 的
evaluate_ai_approve_pending_gate / create_ai_approve_cooldown、command 的
build_ai_approve_command_candidate。另导出组合门面类 AiApproveGate / AiApproveCommand
与 create_* 工厂,供协调器(arbitration/scheduler)按需注入 store/metrics/时间源。
"""

from __future__ import annotations

from backend.services.ai_approve.command import (
    AiApproveCommand,
    AIApproveCommandInput,
    build_ai_approve_command_candidate,
    create_ai_approve_command,
)
from backend.services.ai_approve.gate import (
    AI_APPROVE_COOLDOWN_MS,
    AI_APPROVE_MAX_DAILY_SIGNALS_PER_SYMBOL,
    AIApproveCooldown,
    AiApproveGate,
    create_ai_approve_cooldown,
    create_ai_approve_gate,
    evaluate_ai_approve_pending_gate,
)
from backend.services.ai_approve.rules import (
    AI_APPROVE_MIN_RR,
    calc_ai_approve_lots,
    first_positive_ai_approve_take_profit,
    pick_ai_approve_entry_price,
    primary_ai_approve_take_profit,
    resolve_ai_approve_executable_take_profits,
    resolve_ai_approve_order_intent,
    round2,
    validate_ai_approve_protection_direction,
)

__all__ = [
    "AI_APPROVE_COOLDOWN_MS",
    "AI_APPROVE_MAX_DAILY_SIGNALS_PER_SYMBOL",
    "AI_APPROVE_MIN_RR",
    "AIApproveCommandInput",
    "AIApproveCooldown",
    "AiApproveCommand",
    "AiApproveGate",
    "build_ai_approve_command_candidate",
    "calc_ai_approve_lots",
    "create_ai_approve_command",
    "create_ai_approve_cooldown",
    "create_ai_approve_gate",
    "evaluate_ai_approve_pending_gate",
    "first_positive_ai_approve_take_profit",
    "pick_ai_approve_entry_price",
    "primary_ai_approve_take_profit",
    "resolve_ai_approve_executable_take_profits",
    "resolve_ai_approve_order_intent",
    "round2",
    "validate_ai_approve_protection_direction",
]
