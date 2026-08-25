"""Position-poll job processor (mirror of position-poll.processor.ts).

Iterates every (account, symbol) pair and runs the workflow with skipFeishu=True,
counting how many symbols produced a posted (non-hold) signal.
"""

from __future__ import annotations

from typing import Any, Protocol, TypedDict


class PositionPollJobResult(TypedDict):
    analyzed: int
    posted: int
    skipped: int
    total: int


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class PositionPollConfigLike(Protocol):
    accounts: list[Any]


class WorkflowLike(Protocol):
    async def run(
        self,
        account_id: str,
        symbols: list[str],
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class PositionPollProcessor:
    def __init__(self, config: PositionPollConfigLike, workflow: WorkflowLike) -> None:
        self.config = config
        self.workflow = workflow

    async def process(self, job: Any) -> PositionPollJobResult:
        analyzed = 0
        posted = 0
        skipped = 0

        for account in self.config.accounts:
            account_id = _get(account, "id", "")
            for symbol in list(_get(account, "symbols") or []):
                analyzed += 1
                result = await self.workflow.run(
                    account_id, [symbol], {"skipFeishu": True}
                )
                final_signal = result.get("finalSignal")
                if final_signal is None or _get(final_signal, "exit_suggestion") == "hold":
                    skipped += 1
                    continue
                posted += 1

        total = sum(len(list(_get(account, "symbols") or [])) for account in self.config.accounts)
        return {
            "analyzed": analyzed,
            "posted": posted,
            "skipped": skipped,
            "total": total,
        }
