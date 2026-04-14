from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from devos.auth.models import User
from devos.auth.service import AuthService
from devos.core.database import get_db
from devos.core.exceptions import Http401Error

_BEARER_PREFIX = "Bearer "


def _extract_token(request: Request) -> str | None:
    # Prefer the HTTP-only session cookie
    token = request.cookies.get("session_token")
    if token:
        return token

    # Fall back to Authorization header for direct API clients
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith(_BEARER_PREFIX):
        return auth_header[len(_BEARER_PREFIX):]

    return None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    raw_token = _extract_token(request)
    if not raw_token:
        raise Http401Error("Missing or invalid token")

    service = AuthService(db)
    return await service.validate_session(raw_token)
