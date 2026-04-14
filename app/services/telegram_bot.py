import logging
from typing import Any

import httpx

from app.config import Settings
from app.logging_utils import log_event
from app.schemas.telegram import TelegramUpdate


logger = logging.getLogger(__name__)


class TelegramBotService:
    """Minimal Telegram bot adapter for webhook-driven operator control."""

    def __init__(self, *, settings: Settings) -> None:
        self.settings = settings

    def is_enabled(self) -> bool:
        return bool(self.settings.telegram_enabled)

    def is_owner_chat(self, chat_id: int) -> bool:
        owner_chat_id = self.settings.telegram_chat_id.strip()
        if not owner_chat_id:
            return False
        return str(chat_id) == owner_chat_id

    def extract_chat_id(self, update: TelegramUpdate) -> int | None:
        if update.message is not None:
            return update.message.chat.id
        if update.callback_query is not None and update.callback_query.message is not None:
            return update.callback_query.message.chat.id
        return None

    async def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        if not self.is_enabled():
            log_event(logger, "telegram_send_skipped", reason="telegram_enabled=false")
            return False
        if not self.settings.telegram_bot_token:
            log_event(logger, "telegram_send_skipped", reason="telegram_bot_token_missing")
            return False

        url = (
            f"{self.settings.telegram_api_base_url.rstrip('/')}"
            f"/bot{self.settings.telegram_bot_token}/sendMessage"
        )
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": self.settings.telegram_disable_notification,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.telegram_request_timeout_seconds
            ) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
            response_payload = response.json()
            if not response_payload.get("ok"):
                log_event(
                    logger,
                    "telegram_send_failed",
                    reason=response_payload.get("description", "ok=false"),
                )
                return False
        except Exception as exc:
            log_event(logger, "telegram_send_failed", error=str(exc))
            return False

        log_event(logger, "telegram_send_completed", chat_id=chat_id)
        return True

    async def answer_callback_query(
        self,
        *,
        callback_query_id: str,
        text: str | None = None,
    ) -> bool:
        if not self.is_enabled():
            return False
        if not self.settings.telegram_bot_token:
            return False

        url = (
            f"{self.settings.telegram_api_base_url.rstrip('/')}"
            f"/bot{self.settings.telegram_bot_token}/answerCallbackQuery"
        )
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.telegram_request_timeout_seconds
            ) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
            response_payload = response.json()
            if not response_payload.get("ok"):
                return False
        except Exception as exc:
            log_event(logger, "telegram_answer_callback_failed", error=str(exc))
            return False

        return True

    async def send_main_menu(self, *, chat_id: int) -> bool:
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "📊 Статус", "callback_data": "status"},
                    {"text": "📈 Сделки", "callback_data": "trades"},
                ],
                [
                    {"text": "📉 PnL", "callback_data": "pnl"},
                ],
                [
                    {"text": "🛑 Stop bot", "callback_data": "kill_on"},
                    {"text": "▶️ Start bot", "callback_data": "kill_off"},
                ],
            ]
        }
        return await self.send_message(
            chat_id=chat_id,
            text="<b>Operator control</b>\nВыберите действие:",
            reply_markup=reply_markup,
        )
