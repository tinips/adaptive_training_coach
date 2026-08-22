"""Owner-scoped persistence for revocable mobile sync credentials."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import MobileSyncCredential


class MobileSyncCredentialRepository:
    """Read and update one athlete's mobile credential within a caller transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(
        self,
        *,
        user_id: uuid.UUID,
        for_update: bool = False,
    ) -> MobileSyncCredential | None:
        """Return an athlete-owned credential, optionally locked for mutation."""

        statement = select(MobileSyncCredential).where(
            MobileSyncCredential.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        credential: MobileSyncCredential | None = await self._session.scalar(statement)
        return credential

    async def get_for_pairing_code_hash(
        self,
        *,
        pairing_code_hash: str,
        for_update: bool = False,
    ) -> MobileSyncCredential | None:
        """Resolve a one-time code without accepting a user identifier from iOS."""

        statement = select(MobileSyncCredential).where(
            MobileSyncCredential.pairing_code_hash == pairing_code_hash,
        )
        if for_update:
            statement = statement.with_for_update()
        credential: MobileSyncCredential | None = await self._session.scalar(statement)
        return credential

    async def get_for_active_device_token_hash(
        self,
        *,
        device_token_hash: str,
        for_update: bool = False,
    ) -> MobileSyncCredential | None:
        """Resolve only a non-revoked device credential by its opaque token hash."""

        statement = select(MobileSyncCredential).where(
            MobileSyncCredential.device_token_hash == device_token_hash,
            MobileSyncCredential.revoked_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        credential: MobileSyncCredential | None = await self._session.scalar(statement)
        return credential

    async def issue_pairing_code(
        self,
        *,
        user_id: uuid.UUID,
        pairing_code_hash: str,
        expires_at: datetime,
    ) -> MobileSyncCredential:
        """Create or replace the pending one-time pairing code for an athlete."""

        credential = await self.get_for_user(user_id=user_id, for_update=True)
        if credential is None:
            credential = MobileSyncCredential(
                user_id=user_id,
                pairing_code_hash=pairing_code_hash,
                pairing_code_expires_at=expires_at,
            )
            try:
                async with self._session.begin_nested():
                    self._session.add(credential)
                    await self._session.flush()
            except IntegrityError:
                # Two Telegram updates can issue a code before either has
                # created the one-per-user row. The database is the authority;
                # after the savepoint rolls back, update the winner instead.
                credential = await self.get_for_user(user_id=user_id, for_update=True)
                if credential is None:
                    raise
                credential.pairing_code_hash = pairing_code_hash
                credential.pairing_code_expires_at = expires_at
        else:
            credential.pairing_code_hash = pairing_code_hash
            credential.pairing_code_expires_at = expires_at
        await self._session.flush()
        return credential


__all__ = ["MobileSyncCredentialRepository"]
