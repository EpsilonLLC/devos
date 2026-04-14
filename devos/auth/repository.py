import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from devos.auth.models import Session, Tenant, User
from devos.core.utils import utcnow


class AuthRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create_tenant(self) -> Tenant:
        now = utcnow()
        tenant = Tenant(id=uuid.uuid4(), created_at=now, updated_at=now)
        self._db.add(tenant)
        await self._db.flush()
        return tenant

    async def create_user(
        self,
        *,
        tenant_id: uuid.UUID,
        email: str,
        hashed_password: str,
    ) -> User:
        now = utcnow()
        user = User(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            email=email,
            hashed_password=hashed_password,
            created_at=now,
            updated_at=now,
        )
        self._db.add(user)
        await self._db.flush()
        return user

    async def create_session(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: datetime | None,
    ) -> Session:
        now = utcnow()
        session = Session(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            created_at=now,
            updated_at=now,
        )
        self._db.add(session)
        await self._db.flush()
        return session

    async def get_active_user_by_email(self, email: str) -> User | None:
        result = await self._db.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_active_session_by_token_hash(self, token_hash: str) -> Session | None:
        result = await self._db.execute(
            select(Session).where(
                Session.token_hash == token_hash,
                Session.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_user_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._db.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()
