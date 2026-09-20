from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication.service import register
from app.common.client_ip import client_ip
from app.common.database import get_db
from app.common.ratelimit import check as check_rate_limit

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class RegisterInput(BaseModel):
    username: str
    email: str
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)


@router.post("/register", status_code=201)
async def register_endpoint(body: RegisterInput, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    host = client_ip(request) or "unknown"
    await check_rate_limit(f"register:{host}", 10)
    user = await register(db, body.username, body.email, body.password, body.display_name)
    return {"id": str(user.id), "display_name": user.display_name}
