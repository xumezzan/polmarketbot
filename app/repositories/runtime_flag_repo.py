from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.runtime_flag import RuntimeFlag


class RuntimeFlagRepository:
    """Persistence helper for runtime boolean flags."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, *, key: str) -> RuntimeFlag | None:
        stmt = sa.select(RuntimeFlag).where(RuntimeFlag.key == key)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_bool(self, *, key: str, default: bool = False) -> bool:
        flag = await self.get(key=key)
        if flag is None:
            return default
        return bool(flag.bool_value)

    async def get_status(
        self,
        *,
        key: str,
        default: bool = False,
    ) -> tuple[bool, datetime | None]:
        flag = await self.get(key=key)
        if flag is None:
            return default, None
        return bool(flag.bool_value), flag.updated_at

    async def set_bool(self, *, key: str, value: bool) -> RuntimeFlag:
        flag = await self.get(key=key)
        now = datetime.now(UTC)

        if flag is None:
            flag = RuntimeFlag(
                key=key,
                bool_value=value,
                updated_at=now,
            )
            self.session.add(flag)
        else:
            flag.bool_value = value
            flag.updated_at = now

        await self.session.commit()
        await self.session.refresh(flag)
        return flag

    async def get_text(self, *, key: str) -> str | None:
        """Return the stored text_value or None if the key does not exist."""
        flag = await self.get(key=key)
        if flag is None:
            return None
        return flag.text_value

    async def set_text(self, *, key: str, value: str | None) -> RuntimeFlag:
        """Upsert a text_value for the given key."""
        flag = await self.get(key=key)
        now = datetime.now(UTC)

        if flag is None:
            flag = RuntimeFlag(
                key=key,
                bool_value=False,
                text_value=value,
                updated_at=now,
            )
            self.session.add(flag)
        else:
            flag.text_value = value
            flag.updated_at = now

        await self.session.commit()
        await self.session.refresh(flag)
        return flag

