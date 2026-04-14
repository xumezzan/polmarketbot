import argparse
import asyncio
import html
import logging
from typing import Protocol

import httpx

from app.config import Settings, get_settings
from app.logging_utils import configure_logging, log_event
from app.schemas.daily_report import DailyReport
from app.schemas.alert import AlertDispatchResult, AlertMessage
from app.schemas.scheduler import PipelineItemResult, SchedulerCycleResult
from app.schemas.trade import PaperTradeCloseResult, PaperTradeOpenResult


logger = logging.getLogger(__name__)


class AlertingError(Exception):
    """Raised when alert delivery fails."""


class AlertClientProtocol(Protocol):
    """Common contract for alert adapters."""

    async def send(self, alert: AlertMessage) -> AlertDispatchResult:
        """Deliver one alert message."""


class NoopAlertClient:
    """Local fake alert provider used by default."""

    async def send(self, alert: AlertMessage) -> AlertDispatchResult:
        return AlertDispatchResult(
            event=alert.event,
            mode="noop",
            delivered=False,
            error="alert_mode=noop",
        )


class TelegramAlertClient:
    """Thin adapter over Telegram Bot API sendMessage."""

    def __init__(self, settings: Settings) -> None:
        if not settings.telegram_bot_token:
            raise AlertingError("TELEGRAM_BOT_TOKEN is required when ALERT_MODE=telegram")
        if not settings.telegram_chat_id:
            raise AlertingError("TELEGRAM_CHAT_ID is required when ALERT_MODE=telegram")

        self.settings = settings

    async def send(self, alert: AlertMessage) -> AlertDispatchResult:
        url = (
            f"{self.settings.telegram_api_base_url.rstrip('/')}"
            f"/bot{self.settings.telegram_bot_token}/sendMessage"
        )
        payload = {
            "chat_id": self.settings.telegram_chat_id,
            "text": alert.text,
            "parse_mode": "HTML",
            "disable_notification": self.settings.telegram_disable_notification,
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.telegram_request_timeout_seconds
            ) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AlertingError(f"Telegram sendMessage request failed: {exc}") from exc

        response_payload = response.json()
        if not response_payload.get("ok"):
            description = response_payload.get("description", "Telegram returned ok=false")
            raise AlertingError(f"Telegram sendMessage failed: {description}")

        result = response_payload.get("result") or {}
        return AlertDispatchResult(
            event=alert.event,
            mode="telegram",
            delivered=True,
            provider_message_id=result.get("message_id"),
            provider_chat_id=str(result.get("chat", {}).get("id", self.settings.telegram_chat_id)),
        )


class AlertingService:
    """Build and send optional operational alerts."""

    def __init__(self, *, settings: Settings, client: AlertClientProtocol) -> None:
        self.settings = settings
        self.client = client

    async def send_trade_opened(
        self,
        *,
        cycle_id: str,
        trade: PaperTradeOpenResult,
    ) -> AlertDispatchResult:
        if not self.settings.alert_on_trade_opened:
            return self._skipped_result("paper_trade_opened_alert", "alert_on_trade_opened=false")

        alert = AlertMessage(
            event="paper_trade_opened_alert",
            level="INFO",
            title="Paper Trade Opened",
            text="\n".join(
                [
                    "<b>🚀 Paper Trade Opened</b>",
                    f"cycle_id=<code>{html.escape(cycle_id)}</code>",
                    f"news_id=<code>{trade.news_item_id}</code>",
                    f"signal_id=<code>{trade.signal_id}</code>",
                    f"market=<b>{html.escape(trade.market_id)}</b>",
                    f"side=<b>{html.escape(trade.side)}</b>",
                    f"entry_price=<code>{trade.entry_price:.4f}</code>",
                    f"size_usd=<code>{trade.size_usd:.2f}</code>",
                ]
            ),
            context={
                "cycle_id": cycle_id,
                "news_item_id": trade.news_item_id,
                "signal_id": trade.signal_id,
                "market_id": trade.market_id,
                "trade_id": trade.trade_id,
            },
        )
        return await self._deliver(alert)

    async def send_trade_closed(
        self,
        *,
        cycle_id: str | None = None,
        trade: object,  # Supporting PaperTradeCloseResult
    ) -> AlertDispatchResult:
        if not self.settings.alert_on_trade_closed:
            return self._skipped_result("paper_trade_closed_alert", "alert_on_trade_closed=false")

        alert = AlertMessage(
            event="paper_trade_closed_alert",
            level="INFO",
            title="Paper Trade Closed",
            text="\n".join(
                [
                    "<b>Paper Trade Closed</b>",
                    f"cycle_id=<code>{html.escape(cycle_id)}</code>",
                    f"news_id=<code>{trade.news_item_id}</code>",
                    f"signal_id=<code>{trade.signal_id}</code>",
                    f"market_id=<code>{html.escape(trade.market_id)}</code>",
                    f"side=<b>{html.escape(trade.side)}</b>",
                    f"entry_price=<code>{trade.entry_price:.4f}</code>",
                    f"exit_price=<code>{trade.exit_price:.4f}</code>",
                    f"pnl=<code>{trade.pnl:.4f}</code>",
                    (
                        f"close_reason=<code>{html.escape(trade.close_reason)}</code>"
                        if trade.close_reason
                        else "close_reason=<code>manual</code>"
                    ),
                ]
            ),
            context={
                "cycle_id": cycle_id,
                "news_item_id": trade.news_item_id,
                "signal_id": trade.signal_id,
                "market_id": trade.market_id,
                "trade_id": trade.trade_id,
                "position_id": trade.position_id,
            },
        )
        return await self._deliver(alert)

    async def send_scheduler_item_failure(
        self,
        *,
        cycle_id: str,
        item_result: PipelineItemResult,
    ) -> AlertDispatchResult:
        if not self.settings.alert_on_scheduler_error:
            return self._skipped_result(
                "scheduler_item_failed_alert",
                "alert_on_scheduler_error=false",
            )

        error_text = "; ".join(item_result.errors)[:1000]
        alert = AlertMessage(
            event="scheduler_item_failed_alert",
            level="ERROR",
            title="Scheduler Item Failed",
            text="\n".join(
                [
                    "<b>Scheduler Item Failed</b>",
                    f"cycle_id=<code>{html.escape(cycle_id)}</code>",
                    f"news_id=<code>{item_result.news_item_id}</code>",
                    f"analysis_id=<code>{item_result.analysis_id}</code>",
                    f"errors=<code>{html.escape(error_text)}</code>",
                ]
            ),
            context={
                "cycle_id": cycle_id,
                "news_item_id": item_result.news_item_id,
                "analysis_id": item_result.analysis_id,
            },
        )
        return await self._deliver(alert)

    async def send_cycle_summary(self, result: SchedulerCycleResult) -> AlertDispatchResult:
        if not self.settings.alert_on_cycle_summary:
            return self._skipped_result("scheduler_cycle_summary_alert", "alert_on_cycle_summary=false")

        alert = AlertMessage(
            event="scheduler_cycle_summary_alert",
            level="INFO",
            title="Scheduler Cycle Summary",
            text="\n".join(
                [
                    "<b>Scheduler Cycle Summary</b>",
                    f"cycle_id=<code>{html.escape(result.cycle_id)}</code>",
                    f"processed_news=<code>{result.processed_news_count}</code>",
                    f"actionable_signals=<code>{result.actionable_signal_count}</code>",
                    f"approved_signals=<code>{result.approved_signal_count}</code>",
                    f"opened_positions=<code>{result.opened_position_count}</code>",
                    f"errors=<code>{result.error_count}</code>",
                ]
            ),
            context={
                "cycle_id": result.cycle_id,
                "processed_news_count": result.processed_news_count,
                "opened_position_count": result.opened_position_count,
                "error_count": result.error_count,
            },
        )
        return await self._deliver(alert)

    async def send_daily_report(self, *, report: DailyReport) -> AlertDispatchResult:
        if not self.settings.alert_on_daily_report:
            return self._skipped_result("daily_report_alert", "alert_on_daily_report=false")

        fetched_text = (
            str(report.fetched_news_24h)
            if report.fetched_news_24h is not None
            else "n/a (not persisted)"
        )
        unrealized_text = (
            f"{report.unrealized_pnl:.4f}"
            if report.unrealized_pnl is not None
            else "n/a"
        )
        blocker_lines = (
            [f"- {html.escape(item.reason)}: <code>{item.count}</code>" for item in report.top_blockers]
            if report.top_blockers
            else ["- none"]
        )
        note_lines = (
            [f"- {html.escape(note)}" for note in report.notes]
            if report.notes
            else ["- none"]
        )

        alert = AlertMessage(
            event="daily_report_alert",
            level="INFO",
            title="Daily Report",
            text="\n".join(
                [
                    "<b>Daily Report (last 24h)</b>",
                    f"generated_at=<code>{html.escape(report.generated_at)}</code>",
                    f"window_start=<code>{html.escape(report.window_start)}</code>",
                    f"window_end=<code>{html.escape(report.window_end)}</code>",
                    "",
                    f"fetched_news=<code>{fetched_text}</code>",
                    f"inserted_news=<code>{report.inserted_news_24h}</code>",
                    f"analyses=<code>{report.analyses_count_24h}</code>",
                    f"signals=<code>{report.signals_count_24h}</code>",
                    f"approved_signals=<code>{report.approved_signals_count_24h}</code>",
                    f"opened_trades=<code>{report.opened_paper_trades_24h}</code>",
                    f"closed_trades=<code>{report.closed_paper_trades_24h}</code>",
                    f"open_positions=<code>{report.open_positions_count}</code>",
                    f"realized_pnl=<code>{report.realized_pnl_24h:.4f}</code>",
                    (
                        "unrealized_pnl="
                        f"<code>{unrealized_text}</code> "
                        f"(valued={report.unrealized_positions_valued}/{report.unrealized_positions_total})"
                    ),
                    "",
                    "<b>Top blockers</b>",
                    *blocker_lines,
                    "",
                    "<b>Notes</b>",
                    *note_lines,
                ]
            ),
            context={
                "generated_at": report.generated_at,
                "inserted_news_24h": report.inserted_news_24h,
                "analyses_count_24h": report.analyses_count_24h,
                "signals_count_24h": report.signals_count_24h,
                "approved_signals_count_24h": report.approved_signals_count_24h,
                "opened_paper_trades_24h": report.opened_paper_trades_24h,
                "closed_paper_trades_24h": report.closed_paper_trades_24h,
            },
        )
        return await self._deliver(alert)

    async def send_system_error(
        self,
        *,
        component: str,
        error: str,
        cycle_id: str | None = None,
        cycle_number: int | None = None,
    ) -> AlertDispatchResult:
        if not self.settings.alert_on_scheduler_error:
            return self._skipped_result(
                "system_error_alert",
                "alert_on_scheduler_error=false",
            )

        cycle_id_line = (
            f"cycle_id=<code>{html.escape(cycle_id)}</code>"
            if cycle_id is not None
            else "cycle_id=<code>n/a</code>"
        )
        cycle_number_line = (
            f"cycle_number=<code>{cycle_number}</code>"
            if cycle_number is not None
            else "cycle_number=<code>n/a</code>"
        )
        alert = AlertMessage(
            event="system_error_alert",
            level="ERROR",
            title="System Error",
            text="\n".join(
                [
                    "<b>System Error</b>",
                    f"component=<code>{html.escape(component)}</code>",
                    cycle_id_line,
                    cycle_number_line,
                    f"error=<code>{html.escape(error[:1000])}</code>",
                ]
            ),
            context={
                "component": component,
                "cycle_id": cycle_id,
                "cycle_number": cycle_number,
            },
        )
        return await self._deliver(alert)

    async def send_kill_switch_changed(
        self,
        *,
        enabled: bool,
        changed_at: str,
        source: str,
    ) -> AlertDispatchResult:
        event_name = "kill_switch_on_alert" if enabled else "kill_switch_off_alert"
        state = "ON" if enabled else "OFF"
        alert = AlertMessage(
            event=event_name,
            level="WARNING" if enabled else "INFO",
            title="Paper Trading Kill Switch Changed",
            text="\n".join(
                [
                    "<b>Paper Trading Kill Switch Changed</b>",
                    f"state=<b>{state}</b>",
                    f"changed_at=<code>{html.escape(changed_at)}</code>",
                    f"source=<code>{html.escape(source)}</code>",
                ]
            ),
            context={
                "enabled": enabled,
                "changed_at": changed_at,
                "source": source,
            },
        )
        return await self._deliver(alert)

    async def send_risk_limit_reached(
        self,
        *,
        cycle_id: str,
        daily_exposure: float,
        limit: float,
    ) -> AlertDispatchResult:
        alert = AlertMessage(
            event="risk_limit_reached_alert",
            level="WARNING",
            title="Daily Risk Limit Reached",
            text="\n".join(
                [
                    "<b>⚠️ Daily Risk Limit Reached</b>",
                    f"cycle_id=<code>{html.escape(cycle_id)}</code>",
                    f"current_exposure=<code>{daily_exposure:.2f} USD</code>",
                    f"limit=<code>{limit:.2f} USD</code>",
                    "<i>Trading suspended until tomorrow.</i>",
                ]
            ),
            context={
                "cycle_id": cycle_id,
                "daily_exposure": daily_exposure,
                "limit": limit,
            },
        )
        return await self._deliver(alert)

    async def send_test_message(self, *, message: str) -> AlertDispatchResult:
        alert = AlertMessage(
            event="manual_test_alert",
            level="INFO",
            title="Manual Test Alert",
            text=f"<b>Manual Test Alert</b>\n<code>{html.escape(message)}</code>",
            context={"message": message},
        )
        return await self._deliver(alert)

    async def _deliver(self, alert: AlertMessage) -> AlertDispatchResult:
        try:
            result = await self.client.send(alert)
        except Exception as exc:
            log_event(
                logger,
                "alert_delivery_failed",
                event_name=alert.event,
                level=alert.level,
                error=str(exc),
                **alert.context,
            )
            return AlertDispatchResult(
                event=alert.event,
                mode=self.settings.alert_mode.lower(),
                delivered=False,
                error=str(exc),
            )

        log_event(
            logger,
            "alert_delivery_completed",
            event_name=alert.event,
            mode=result.mode,
            delivered=result.delivered,
            provider_message_id=result.provider_message_id,
            provider_chat_id=result.provider_chat_id,
            error=result.error,
            **alert.context,
        )
        return result

    def _skipped_result(self, event: str, reason: str) -> AlertDispatchResult:
        result = AlertDispatchResult(
            event=event,
            mode=self.settings.alert_mode.lower(),
            delivered=False,
            error=reason,
        )
        log_event(
            logger,
            "alert_delivery_skipped",
            event_name=event,
            mode=result.mode,
            reason=reason,
        )
        return result


def build_alert_client(settings: Settings) -> AlertClientProtocol:
    """Return noop or Telegram alert adapter."""
    mode = settings.alert_mode.lower()

    if mode == "noop":
        return NoopAlertClient()

    if mode == "telegram":
        return TelegramAlertClient(settings)

    raise ValueError("Unsupported ALERT_MODE. Expected 'noop' or 'telegram'.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send a test operational alert.")
    parser.add_argument(
        "--message",
        type=str,
        required=True,
        help="Test alert message text.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    service = AlertingService(settings=settings, client=build_alert_client(settings))
    result = await service.send_test_message(message=args.message)
    print(result.model_dump_json())


if __name__ == "__main__":
    asyncio.run(_main())
