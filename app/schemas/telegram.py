from pydantic import BaseModel, ConfigDict, Field


class TelegramChat(BaseModel):
    """Telegram chat object (minimal subset)."""

    model_config = ConfigDict(extra="ignore")

    id: int


class TelegramUser(BaseModel):
    """Telegram user object (minimal subset)."""

    model_config = ConfigDict(extra="ignore")

    id: int


class TelegramMessage(BaseModel):
    """Telegram message object (minimal subset)."""

    model_config = ConfigDict(extra="ignore")

    message_id: int | None = None
    chat: TelegramChat
    text: str | None = None


class TelegramCallbackQuery(BaseModel):
    """Telegram callback query object (minimal subset)."""

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True,
    )

    id: str
    from_user: TelegramUser = Field(alias="from")
    data: str | None = None
    message: TelegramMessage | None = None


class TelegramUpdate(BaseModel):
    """Telegram update payload accepted by webhook endpoint."""

    model_config = ConfigDict(extra="ignore")

    update_id: int | None = None
    message: TelegramMessage | None = None
    callback_query: TelegramCallbackQuery | None = None
