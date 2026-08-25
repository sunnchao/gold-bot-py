import os
from typing import Literal

import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend.agents.runtime import create_agent_workflow
from backend.api.app import create_api_app
from backend.core.config import load_gold_bot_env
from backend.core.database import create_store_from_env
from backend.notifications import FeishuNotifier


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str


runtime_env = load_gold_bot_env()
store = create_store_from_env(runtime_env)
agent_env = dict(os.environ)
agent_token = (agent_env.get("GOLDBOT_API_TOKEN") or runtime_env.GB_ADMIN_TOKEN).strip()
if agent_token:
    agent_env["GOLDBOT_API_TOKEN"] = agent_token
agent_workflow = create_agent_workflow(agent_env)


async def trigger_llm_analysis(account_id: str, symbol: str, timeframe: str, bar_time: str) -> None:
    await agent_workflow.run(
        account_id,
        [symbol],
        {"triggerTimeframe": timeframe, "barCloseTime": bar_time},
    )


app_options: dict[str, object] = {
    "store": store,
    "llm_analysis_trigger": trigger_llm_analysis,
    "feishu": FeishuNotifier(
        webhook_url=runtime_env.GB_FEISHU_WEBHOOK_URL,
        secret=runtime_env.GB_FEISHU_SECRET,
        log=print,
    ),
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
