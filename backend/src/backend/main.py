import os
from typing import Literal

import httpx
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.agents.runtime import create_agent_workflow
from backend.api.app import create_api_app
from backend.core.config import load_gold_bot_env
from backend.core.database import create_store_from_env, ensure_store_schema
from backend.notifications import FeishuNotifier
from backend.services.sop import AlertRouter, PatrolService
from backend.trading_core.riskgate.economic_calendar import load_economic_calendar


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


runtime_env = load_gold_bot_env()
store = create_store_from_env(runtime_env)
agent_env = dict(os.environ)
agent_token = (agent_env.get("GOLDBOT_API_TOKEN") or runtime_env.GB_ADMIN_TOKEN).strip()
if agent_token:
    agent_env["GOLDBOT_API_TOKEN"] = agent_token
pi_http_client = httpx.AsyncClient(trust_env=False)
agent_workflow = create_agent_workflow(agent_env, pi_http_client=pi_http_client)


async def trigger_llm_analysis(account_id: str, symbol: str, timeframe: str, bar_time: str) -> None:
    await agent_workflow.run(
        account_id,
        [symbol],
        {"triggerTimeframe": timeframe, "barCloseTime": bar_time},
    )


feishu_notifier = FeishuNotifier(
    webhook_url=runtime_env.GB_FEISHU_WEBHOOK_URL,
    secret=runtime_env.GB_FEISHU_SECRET,
    log=print,
)
sop_patrol: PatrolService | None = None
if runtime_env.GB_SOP_PATROL_ENABLED:
    sop_execution_mode: Literal["advisory_only", "integrated"] = (
        "integrated" if runtime_env.GB_SOP_EXECUTION_MODE == "integrated" else "advisory_only"
    )
    sop_patrol = PatrolService(
        store=store,
        workflow_service=agent_workflow,
        alert_router=AlertRouter(feishu=feishu_notifier),
        execution_mode=sop_execution_mode,
        htf_review_minutes=runtime_env.GB_SOP_HTF_REVIEW_MINUTES,
        move_trigger_pct=runtime_env.GB_SOP_MOVE_TRIGGER_PCT,
        m15_always_run_entry=runtime_env.GB_SOP_M15_ALWAYS_RUN_ENTRY,
        active_account_ttl_seconds=runtime_env.GB_SOP_ACTIVE_ACCOUNT_TTL_SECONDS,
    )


async def trigger_patrol_on_bar_close(account_id: str, symbol: str, timeframe: str, bar_time: str) -> None:
    if sop_patrol is None:
        await trigger_llm_analysis(account_id, symbol, timeframe, bar_time)
        return
    ttype = "m15_close" if timeframe.strip().upper() == "M15" else f"{timeframe.strip().lower()}_close"
    await sop_patrol.run_patrol(account_id, symbol, trigger_type=ttype)


app_options: dict[str, object] = {
    "store": store,
    "llm_analysis_trigger": trigger_llm_analysis,
    "patrol_trigger": trigger_patrol_on_bar_close if sop_patrol is not None else None,
    "sop_patrol": sop_patrol,
    "sop_patrol_enabled": runtime_env.GB_SOP_PATROL_ENABLED,
    "sop_execution_mode": runtime_env.GB_SOP_EXECUTION_MODE,
    "sop_patrol_interval_seconds": runtime_env.GB_SOP_PATROL_INTERVAL_SECONDS,
    "sop_active_account_ttl_seconds": runtime_env.GB_SOP_ACTIVE_ACCOUNT_TTL_SECONDS,
    "feishu": feishu_notifier,
}
if agent_token:
    app_options.update(
        {
            "valid_tokens": {agent_token},
            "token_accounts": {agent_token: set()},
            "admin_tokens": {agent_token},
        }
    )
app = create_api_app(app_options)
app.router.add_event_handler("shutdown", pi_http_client.aclose)
app.router.add_event_handler("shutdown", app.state.scheduler.stop)


@app.on_event("startup")
async def initialize_persistence() -> None:
    load_economic_calendar()
    await ensure_store_schema(store)
    await app.state.scheduler.start(
        position_review=False,
        sop_patrol=runtime_env.GB_SOP_PATROL_ENABLED,
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="backend")


def run() -> None:
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
