"""持久层记录类型与枚举常量(镜像 gold-bot packages/persistence/src/**)。

迁移自:
- shared-contracts/src/runtime.ts(runtimeModes / commandStatuses / commandSources)
- shared-contracts/src/endpoint.ts(EA_COMPAT_ENDPOINTS)
- persistence/src/commands.ts / decisions.ts / shadow.ts / tokens.ts / runtime-state.ts
- persistence/src/index.ts(ClosedTrade / ClosedTradeStats / PositionStateRecord / EaRecord)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

# 常量(与 shared-contracts/src/runtime.ts 逐字一致)
# ---------------------------------------------------------------------------
# 常量(与 shared-contracts/src/runtime.ts 逐字一致)
# ---------------------------------------------------------------------------

RUNTIME_MODES = ("oracle", "shadow", "cutover", "rollback")
RuntimeMode = Literal["oracle", "shadow", "cutover", "rollback"]

COMMAND_STATUSES = (
    "draft",
    "shadow_only",
    "queued",
    "delivered",
    "acked",
    "rejected",
    "failed",
    "superseded",
)
CommandStatus = Literal["draft", "shadow_only", "queued", "delivered", "acked", "rejected", "failed", "superseded"]

COMMAND_SOURCES = (
    "ea_analysis",
    "live_strategy",
    "position_review",
    "ai_stop_loss",
    "ai_result",
    "ai_risk_alert",
    "ai_approve",
    "position_manager",
)
CommandSource = Literal[
    "ea_analysis",
    "live_strategy",
    "position_review",
    "ai_stop_loss",
    "ai_result",
    "ai_risk_alert",
    "ai_approve",
    "position_manager",
]

EA_COMPAT_ENDPOINTS = (
    "/register",
    "/heartbeat",
    "/tick",
    "/bars",
    "/positions",
    "/poll",
    "/order_result",
)

DEFAULT_SYMBOL = "XAUUSD"
BE_TRIGGER_ATR_DEFAULT = 1.5

# ---------------------------------------------------------------------------
# 通用记录类型
# ---------------------------------------------------------------------------
EaRecord = dict[str, Any]
"""宽松的 JS 风格 record:EA 载荷 / 快照 / 分析结果等统一载体。"""
EaCommand = dict[str, Any]
"""发给 EA 的命令(镜像 toEaCommand 输出):command_id, action, 及其它策略字段。"""
CommandCandidate = dict[str, Any]
"""候选命令(镜像 CommandCandidate):command_id, action, source, symbol 等。"""
StoredCommand = dict[str, Any]
"""存储的命令记录(镜像 StoredCommand)字段含:
account_id, command_id, action, source, status, created_at, delivered_at,
acked_at, failed_at, result, ticket, error_text。"""


class PositionStateRecord(TypedDict, total=False):
    ticket: int
    tp1_hit: bool
    tp2_hit: bool
    max_profit_atr: float
    be_moved: bool
    be_trigger_atr: float
    best_sl: float
    open_time: str
    last_modify_time: str
    add_on_count: int
    last_add_on_time: str
    last_add_on_price: float
    group_id: str
    group_avg_entry: float
    group_best_sl: float
    trailing_closed: bool


class ClosedTrade(TypedDict):
    account_id: str
    ticket: int
    magic: int
    symbol: str
    strategy: str
    side: str
    open_price: float
    close_price: float
    lots: float
    profit: float
    open_time: str
    close_time: str
    duration_min: int


class ClosedTradeStats(TypedDict):
    strategy: str
    total: int
    wins: int
    losses: int
    win_rate: float
    total_profit: float
    avg_profit: float
    avg_win: float
    avg_loss: float
    expectancy: float
    avg_duration_min: float


DecisionStage = Literal[
    "candidate_signal",
    "ai_result",
    "risk_gate",
    "command_enqueued",
    "command_delivered",
    "order_result",
]
DecisionStatus = Literal["pending", "accepted", "rejected", "clamped", "delivered", "acked", "failed"]


class DecisionEvent(TypedDict):
    id: int
    decision_id: str
    account_id: str
    symbol: str
    stage: DecisionStage
    status: DecisionStatus
    reason_codes: list[str]
    summary: dict[str, Any]
    related_command_id: str
    created_at: str


class DecisionEventInput(TypedDict, total=False):
    decision_id: str
    account_id: str
    symbol: str
    stage: DecisionStage
    status: DecisionStatus
    reason_codes: list[str]
    summary: dict[str, Any]
    related_command_id: str
    created_at: str


class DecisionEventFilter(TypedDict, total=False):
    account_id: str
    symbol: str
    status: str
    limit: int


ShadowSource = Literal["ea_analysis", "position_review", "ai_result"]


class ShadowComparison(TypedDict):
    account_id: str
    symbol: str
    protocol_ok: bool
    signal_drift: bool
    command_drift: bool
    oracle_compared: bool
    source: ShadowSource
    created_at: str


class ShadowComparisonFilter(TypedDict, total=False):
    account_id: str
    symbol: str
    source: ShadowSource
    protocol_ok: bool
    signal_drift: bool
    command_drift: bool
    oracle_compared: bool
    created_at_gte: str
    created_at_lte: str


class ShadowComparisonSummary(TypedDict):
    comparisons: int
    protocol_errors: int
    signal_drifts: int
    command_drifts: int
    oracle_compared: int
    first_created_at: str
    last_created_at: str


class ShadowRuntimeSnapshot(TypedDict):
    account_id: str
    symbol: str
    source: ShadowSource
    signal: Any
    command: Any
    created_at: str

"""存储的 API 令牌记录(镜像 StoredApiToken):token, name, accounts, is_admin, created_at。"""
StoredApiToken = dict[str, Any]
"""存储的 API 令牌记录(镜像 StoredApiToken):token, name, accounts, is_admin, created_at。"""
"""令牌写入输入(镜像 StoredApiTokenInput):token, name, accounts, is_admin, created_at。"""
StoredApiTokenInput = dict[str, Any]
"""令牌写入输入(镜像 StoredApiTokenInput):token, name, accounts, is_admin, created_at。"""


class RuntimeStateRecord(TypedDict):
    account_id: str
    mode: RuntimeMode
    cutover_enabled: bool
    updated_at: str


    """/__contracts 端点暴露的持久层状态(镜像 persistenceStatus)。"""
class PersistenceStatus:
    """/__contracts 端点暴露的持久层状态(镜像 persistenceStatus)。"""

    writes_live_commands: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"writesLiveCommands": self.writes_live_commands}


@dataclass
class Migration:
    version: int
    name: str
    sql: str


@dataclass
class BootstrapResult:
    admin_tokens_seeded: int = field(default=0)
    legacy_tokens_imported: int = field(default=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adminTokensSeeded": self.admin_tokens_seeded,
            "legacyTokensImported": self.legacy_tokens_imported,
        }


@dataclass
class LegacyTokenRecord:
    token: str
    name: str = ""
    is_admin: bool = False
    accounts: list[str] = field(default_factory=list)


def is_runtime_mode(value: str) -> bool:
    return value in RUNTIME_MODES


def is_command_status(value: str) -> bool:
    return value in COMMAND_STATUSES


def is_command_source(value: str) -> bool:
    return value in COMMAND_SOURCES
