from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.authentication.mfa_routes import router as mfa_router
from app.authentication.routes import router as authentication_router
from app.common.config import get_settings
from app.common.errors import ApiError, api_error_handler
from app.identity.routes import router as identity_router
from app.oauth.routes import router as oauth_router
from app.sessions.routes import router as sessions_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title="Lako Auth", version="0.1.0", lifespan=lifespan)
app.add_exception_handler(ApiError, api_error_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
)
app.include_router(identity_router)
app.include_router(authentication_router)
app.include_router(mfa_router)
app.include_router(sessions_router)
app.include_router(oauth_router)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
