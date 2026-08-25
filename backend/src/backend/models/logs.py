from pydantic import BaseModel


class Logs(BaseModel):
    id: int
    user_id: int
    action: str
    timestamp: str
    description: str
