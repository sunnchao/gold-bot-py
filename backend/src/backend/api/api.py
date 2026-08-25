from fastapi import APIRouter

from backend.api.routes.logs import router as logs_router
from backend.api.routes.users import router as users_router

router = APIRouter()
router.include_router(users_router)
router.include_router(logs_router)
