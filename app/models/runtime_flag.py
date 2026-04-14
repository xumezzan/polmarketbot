from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RuntimeFlag(Base):
    """Small key/value table for runtime kill switches and feature flags."""

    __tablename__ = "runtime_flags"

    key: Mapped[str] = mapped_column(sa.String(100), primary_key=True)
    bool_value: Mapped[bool] = mapped_column(
        sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )

