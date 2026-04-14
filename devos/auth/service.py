import hashlib
import secrets
from datetime import timedelta

from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession

from devos.auth.models import User
from devos.auth.repository import AuthRepository
from devos.auth.schemas import LoginRequest, SignupRequest
from devos.core.exceptions import Http401Error, Http409Error, Http500Error
from devos.core.utils import utcnow

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_EXPIRE_HOURS = 168  # 7 days


def _generate_token() -> str:
    return secrets.token_urlsafe(32)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def _hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def _verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


class AuthService:
    def __init__(self, db: AsyncSession) -> None:
        self._repo = AuthRepository(db)

    async def signup(self, request: SignupRequest) -> tuple[User, str]:
        existing = await self._repo.get_active_user_by_email(request.email)
        if existing is not None:
            raise Http409Error(
                "An account with this email already exists. Please log in.",
                code="EMAIL_ALREADY_EXISTS",
                detail={"field": "email"},
            )

        hashed_password = _hash_password(request.password)

        try:
            tenant = await self._repo.create_tenant()
            user = await self._repo.create_user(
                tenant_id=tenant.id,
                email=request.email,
                hashed_password=hashed_password,
            )
        except Exception as exc:
            raise Http500Error("Something went wrong. Please try again.") from exc

        raw_token = _generate_token()
        token_hash = _hash_token(raw_token)
        expires_at = utcnow() + timedelta(hours=SESSION_EXPIRE_HOURS)

        try:
            await self._repo.create_session(
                tenant_id=tenant.id,
                user_id=user.id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        except Exception as exc:
            raise Http500Error("Something went wrong. Please try again.") from exc

        return user, raw_token

    async def login(self, request: LoginRequest) -> tuple[User, str]:
        user = await self._repo.get_active_user_by_email(request.email)
        # Constant-time check: always verify even if user is None to prevent timing attacks
        dummy_hash = "$2b$12$" + "x" * 53
        stored_hash = user.hashed_password if user is not None else dummy_hash
        password_ok = _verify_password(request.password, stored_hash)

        if user is None or not password_ok:
            raise Http401Error(
                "Invalid email or password.",
                code="INVALID_CREDENTIALS",
                detail={"field": "credentials"},
            )

        raw_token = _generate_token()
        token_hash = _hash_token(raw_token)
        expires_at = utcnow() + timedelta(hours=SESSION_EXPIRE_HOURS)

        try:
            await self._repo.create_session(
                tenant_id=user.tenant_id,
                user_id=user.id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        except Exception as exc:
            raise Http500Error("Something went wrong. Please try again.") from exc

        return user, raw_token

    async def validate_session(self, raw_token: str) -> User:
        token_hash = _hash_token(raw_token)
        session = await self._repo.get_active_session_by_token_hash(token_hash)

        if session is None:
            raise Http401Error("Missing or invalid token")

        if session.expires_at is not None and session.expires_at < utcnow():
            raise Http401Error("Session has expired")

        user = await self._repo.get_active_user_by_id(session.user_id)
        if user is None:
            raise Http401Error("Missing or invalid token")

        return user
