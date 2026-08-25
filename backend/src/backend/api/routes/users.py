from fastapi import APIRouter

from backend.models.users import User

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/")
async def get_users() -> list[User]:
    return []


@router.post("/")
async def create_user(user: User) -> User:
    return user
