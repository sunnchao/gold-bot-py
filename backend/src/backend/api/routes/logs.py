from fastapi import APIRouter

from backend.models.logs import Logs

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("/")
async def get_logs() -> list[Logs]:
    return []


@router.post("/")
async def create_log(log: Logs) -> Logs:
    return log
