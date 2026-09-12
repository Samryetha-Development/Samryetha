from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.authentication.service import register
from app.common.database import get_db

router = APIRouter(prefix="/api/auth", tags=["authentication"])


class RegisterInput(BaseModel):
    username: str
    email: str
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)


@router.post("/register", status_code=201)
async def register_endpoint(body: RegisterInput, db: AsyncSession = Depends(get_db)) -> dict:
    user = await register(db, body.username, body.email, body.password, body.display_name)
    return {"id": str(user.id), "display_name": user.display_name}
