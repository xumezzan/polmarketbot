from pydantic import BaseModel, Field


class AlertMessage(BaseModel):
    """Normalized alert payload before delivery."""

    event: str
    level: str
    title: str
    text: str
    context: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class AlertDispatchResult(BaseModel):
    """Result of one alert delivery attempt."""

    event: str
    mode: str
    delivered: bool
    provider_message_id: int | None = None
    provider_chat_id: str | None = None
    error: str | None = None
