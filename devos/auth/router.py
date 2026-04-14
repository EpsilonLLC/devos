from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from devos.auth.dependencies import get_current_user
from devos.auth.models import User
from devos.auth.schemas import (
    LoginRequest,
    LoginResponse,
    SessionResponse,
    SignupRequest,
    SignupResponse,
    UserResponse,
)
from devos.auth.service import AuthService
from devos.core.database import get_db

_COOKIE_NAME = "session_token"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days in seconds

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/signup", response_model=SignupResponse)
async def signup(
    body: SignupRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> SignupResponse:
    service = AuthService(db)
    user, raw_token = await service.signup(body)

    response.set_cookie(
        key=_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=_COOKIE_MAX_AGE,
    )

    return SignupResponse(
        user=UserResponse.model_validate(user),
        token=raw_token,
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    service = AuthService(db)
    user, raw_token = await service.login(body)

    response.set_cookie(
        key=_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=_COOKIE_MAX_AGE,
    )

    return LoginResponse(
        user=UserResponse.model_validate(user),
        token=raw_token,
    )


@router.get("/session", response_model=SessionResponse)
async def get_session(
    current_user: User = Depends(get_current_user),
) -> SessionResponse:
    return SessionResponse(user=UserResponse.model_validate(current_user))
