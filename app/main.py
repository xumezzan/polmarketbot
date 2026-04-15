import logging
from datetime import UTC, datetime, timedelta
from html import escape

from fastapi import Depends, FastAPI, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db_session
from app.logging_utils import log_event
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.news_repo import NewsRepository
from app.repositories.operator_state_repo import OperatorStateRepository
from app.repositories.runtime_flag_repo import RuntimeFlagRepository
from app.repositories.scheduler_cycle_repo import SchedulerCycleRepository
from app.repositories.signal_repo import SignalRepository
from app.repositories.trade_repo import TradeRepository
from app.runtime_flags import RUNTIME_FLAG_PAPER_TRADING_KILL_SWITCH
from app.schemas.admin import (
    AdminPaperStatsResponse,
    AdminStatusResponse,
    KillSwitchStatus,
    OpenPositionsResponse,
    SignalAuditResponse,
    RecentSignalsResponse,
)
from app.services.alerting import AlertingService, build_alert_client, get_alerting_runtime_status
from app.services.monitor import MonitorService
from app.services.operator import OperatorService
from app.schemas.monitor import SystemVerificationReport
from app.schemas.telegram import TelegramUpdate
from app.services.telegram_bot import TelegramBotService


settings = get_settings()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)


def _build_operator_service(session: AsyncSession) -> OperatorService:
    return OperatorService(
        settings=settings,
        news_repository=NewsRepository(session),
        analysis_repository=AnalysisRepository(session),
        signal_repository=SignalRepository(session),
        trade_repository=TradeRepository(session),
        runtime_flag_repository=RuntimeFlagRepository(session),
        operator_state_repository=OperatorStateRepository(session),
        scheduler_cycle_repository=SchedulerCycleRepository(session),
    )


@app.on_event("startup")
async def app_startup() -> None:
    """Log a compact startup validation snapshot for operator visibility."""
    alerting_status = get_alerting_runtime_status(settings)
    log_event(
        logger,
        "app_startup_completed",
        app_name=settings.app_name,
        app_env=settings.app_env,
        alert_mode=alerting_status["mode"],
        alerting_status=alerting_status["status"],
        alerting_enabled=alerting_status["enabled"],
        alerting_reason=alerting_status["reason"] or None,
        telegram_enabled=settings.telegram_enabled,
    )


@app.get("/health")
async def health_check() -> dict[str, str]:
    """Simple liveness check for Docker and external monitoring."""
    return {"status": "ok"}


@app.get("/admin/status", response_model=AdminStatusResponse)
async def admin_status(
    session: AsyncSession = Depends(get_db_session),
) -> AdminStatusResponse:
    """Return compact operator status snapshot."""
    service = _build_operator_service(session)
    return await service.get_status()


@app.get("/admin/signals/recent", response_model=RecentSignalsResponse)
async def admin_recent_signals(
    limit: int = Query(
        default=settings.operator_recent_signals_default_limit,
        ge=1,
        le=settings.operator_recent_signals_max_limit,
    ),
    session: AsyncSession = Depends(get_db_session),
) -> RecentSignalsResponse:
    """Return latest signal rows for quick operator review."""
    service = _build_operator_service(session)
    return await service.get_recent_signals(limit=limit)


@app.get("/admin/signals/audit", response_model=SignalAuditResponse)
async def admin_signal_audit(
    limit: int = Query(
        default=settings.operator_recent_signals_default_limit,
        ge=1,
        le=settings.operator_recent_signals_max_limit,
    ),
    session: AsyncSession = Depends(get_db_session),
) -> SignalAuditResponse:
    """Return detailed news->market->risk audit rows for recent signals."""
    service = _build_operator_service(session)
    return await service.get_signal_audit(limit=limit)


@app.get("/admin/positions/open", response_model=OpenPositionsResponse)
async def admin_open_positions(
    session: AsyncSession = Depends(get_db_session),
) -> OpenPositionsResponse:
    """Return currently open paper positions."""
    service = _build_operator_service(session)
    return await service.get_open_positions()


@app.get("/admin/paper/stats", response_model=AdminPaperStatsResponse)
async def admin_paper_stats(
    session: AsyncSession = Depends(get_db_session),
) -> AdminPaperStatsResponse:
    """Return compact paper-trading statistics."""
    service = _build_operator_service(session)
    return await service.get_paper_stats()


@app.get("/admin/verify", response_model=SystemVerificationReport)
async def admin_verify(
    session: AsyncSession = Depends(get_db_session),
) -> SystemVerificationReport:
    """Run full 4-layer system verification and return report."""
    monitor = MonitorService(
        settings=settings,
        operator_state_repository=OperatorStateRepository(session),
        scheduler_cycle_repository=SchedulerCycleRepository(session),
        news_repository=NewsRepository(session),
        trade_repository=TradeRepository(session),
    )
    return await monitor.run_full_verification()


@app.get("/admin/kill-switch/status", response_model=KillSwitchStatus)
async def kill_switch_status(
    session: AsyncSession = Depends(get_db_session),
) -> KillSwitchStatus:
    """Return paper-trading kill switch status."""
    repository = RuntimeFlagRepository(session)
    enabled, updated_at = await repository.get_status(
        key=RUNTIME_FLAG_PAPER_TRADING_KILL_SWITCH,
        default=False,
    )
    return KillSwitchStatus(
        enabled=enabled,
        key=RUNTIME_FLAG_PAPER_TRADING_KILL_SWITCH,
        updated_at=updated_at.isoformat() if updated_at is not None else None,
    )


@app.post("/admin/kill-switch/on", response_model=KillSwitchStatus)
async def kill_switch_on(
    session: AsyncSession = Depends(get_db_session),
) -> KillSwitchStatus:
    """Enable kill switch: block opening new paper trades."""
    return await _set_kill_switch(session=session, enabled=True)


@app.post("/admin/kill-switch/off", response_model=KillSwitchStatus)
async def kill_switch_off(
    session: AsyncSession = Depends(get_db_session),
) -> KillSwitchStatus:
    """Disable kill switch: allow opening new paper trades."""
    return await _set_kill_switch(session=session, enabled=False)


@app.post("/telegram/webhook")
async def telegram_webhook(
    update: TelegramUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, bool]:
    """
    Receive Telegram updates and execute minimal operator commands.

    Webhook should never raise to Telegram even if processing fails.
    """
    if not settings.telegram_enabled:
        return {"ok": True}

    bot_service = TelegramBotService(settings=settings)
    try:
        if update.message is not None:
            await _handle_telegram_message(
                session=session,
                bot_service=bot_service,
                update=update,
            )
        elif update.callback_query is not None:
            await _handle_telegram_callback(
                session=session,
                bot_service=bot_service,
                update=update,
            )
        else:
            log_event(logger, "telegram_update_ignored", reason="unsupported_update_type")
    except Exception as exc:
        log_event(logger, "telegram_webhook_failed", error=str(exc))
        chat_id = bot_service.extract_chat_id(update)
        if chat_id is not None and bot_service.is_owner_chat(chat_id):
            await bot_service.send_message(
                chat_id=chat_id,
                text="Ошибка обработки команды. Проверьте логи API.",
            )
    return {"ok": True}


async def _set_kill_switch(
    *,
    session: AsyncSession,
    enabled: bool,
    source: str = "admin_api",
) -> KillSwitchStatus:
    repository = RuntimeFlagRepository(session)
    flag = await repository.set_bool(
        key=RUNTIME_FLAG_PAPER_TRADING_KILL_SWITCH,
        value=enabled,
    )

    try:
        alerting_service = AlertingService(
            settings=settings,
            client=build_alert_client(settings),
        )
        await alerting_service.send_kill_switch_changed(
            enabled=enabled,
            changed_at=flag.updated_at.isoformat(),
            source=source,
        )
    except Exception as exc:
        # Alert delivery issues should never block kill-switch state updates.
        log_event(
            logger,
            "kill_switch_alert_failed",
            enabled=enabled,
            error=str(exc),
        )

    return KillSwitchStatus(
        enabled=bool(flag.bool_value),
        key=flag.key,
        updated_at=flag.updated_at.isoformat(),
    )


async def _handle_telegram_message(
    *,
    session: AsyncSession,
    bot_service: TelegramBotService,
    update: TelegramUpdate,
) -> None:
    message = update.message
    if message is None:
        return

    chat_id = message.chat.id
    if not bot_service.is_owner_chat(chat_id):
        log_event(logger, "telegram_update_ignored", reason="unauthorized_chat", chat_id=chat_id)
        return

    text = (message.text or "").strip()
    log_event(logger, "telegram_message_received", chat_id=chat_id, text=text)

    if text in {"/start", "/menu"}:
        await bot_service.send_main_menu(chat_id=chat_id)
        return

    await bot_service.send_message(
        chat_id=chat_id,
        text="Используйте /start для открытия меню управления.",
    )


async def _handle_telegram_callback(
    *,
    session: AsyncSession,
    bot_service: TelegramBotService,
    update: TelegramUpdate,
) -> None:
    callback = update.callback_query
    if callback is None:
        return

    chat_id = callback.message.chat.id if callback.message is not None else callback.from_user.id
    callback_id = callback.id
    data = (callback.data or "").strip()

    if not bot_service.is_owner_chat(chat_id):
        log_event(
            logger,
            "telegram_update_ignored",
            reason="unauthorized_chat",
            chat_id=chat_id,
            callback_data=data,
        )
        await bot_service.answer_callback_query(
            callback_query_id=callback_id,
            text="Access denied",
        )
        return

    log_event(
        logger,
        "telegram_callback_received",
        chat_id=chat_id,
        callback_data=data,
    )

    if data == "status":
        status = await _build_operator_service(session).get_status()
        await bot_service.send_message(
            chat_id=chat_id,
            text=_format_status_message(status),
        )
    elif data == "trades":
        await bot_service.send_message(
            chat_id=chat_id,
            text=await _format_recent_trades_message(session=session),
        )
    elif data == "pnl":
        await bot_service.send_message(
            chat_id=chat_id,
            text=await _format_pnl_message(session=session),
        )
    elif data == "kill_on":
        await _set_kill_switch(
            session=session,
            enabled=True,
            source="telegram_bot",
        )
        await bot_service.send_message(chat_id=chat_id, text="Kill switch включён 🛑")
    elif data == "kill_off":
        await _set_kill_switch(
            session=session,
            enabled=False,
            source="telegram_bot",
        )
        await bot_service.send_message(chat_id=chat_id, text="Kill switch выключен ▶️")
    else:
        await bot_service.send_message(
            chat_id=chat_id,
            text=f"Неизвестная команда callback: <code>{escape(data)}</code>",
        )

    await bot_service.answer_callback_query(callback_query_id=callback_id, text="OK")


def _format_status_message(status: AdminStatusResponse) -> str:
    """Build compact operator status text for Telegram."""
    pipeline_alive = False
    if status.last_scheduler_cycle_finished_at:
        try:
            finished_at = datetime.fromisoformat(
                status.last_scheduler_cycle_finished_at.replace("Z", "+00:00")
            )
            if finished_at.tzinfo is None:
                finished_at = finished_at.replace(tzinfo=UTC)
            now = datetime.now(UTC)
            max_stale = max(settings.scheduler_interval_minutes * 3, 15.0)
            pipeline_alive = (now - finished_at) <= timedelta(minutes=max_stale)
        except ValueError:
            pipeline_alive = False

    pipeline_text = "жив ✅" if pipeline_alive else "нет новых циклов ⚠️"
    kill_switch_text = "ON 🛑" if status.kill_switch_enabled else "OFF ▶️"
    last_cycle = status.last_scheduler_cycle_finished_at or "n/a"
    cooldown_lines = []
    for provider, payload in status.provider_cooldowns.items():
        remaining_seconds = payload.get("remaining_seconds")
        if isinstance(remaining_seconds, (int, float)):
            cooldown_lines.append(
                f"Cooldown {provider}: <code>{int(remaining_seconds)}s</code>"
            )

    lines = [
        "<b>📊 Статус системы</b>",
        f"API: <b>жив ✅</b>",
        f"Pipeline: <b>{pipeline_text}</b>",
        f"Последний цикл: <code>{escape(last_cycle)}</code>",
        f"Новости (24ч): <code>{status.inserted_news_24h}</code>",
        f"Сигналы (24ч): <code>{status.signals_count_24h}</code>",
        f"Открытые позиции: <code>{status.open_positions_count}</code>",
        f"Kill switch: <b>{kill_switch_text}</b>",
    ]
    lines.extend(cooldown_lines)
    return "\n".join(lines)


async def _format_recent_trades_message(*, session: AsyncSession) -> str:
    """Build Telegram text for the latest paper trades."""
    trades = await TradeRepository(session).list_recent_trades(limit=5)
    if not trades:
        return "<b>📈 Последние сделки</b>\nСделок пока нет."

    lines = ["<b>📈 Последние 5 сделок</b>"]
    for trade in trades:
        side = trade.side.value
        entry = float(trade.entry_price)
        exit_price = "-" if trade.exit_price is None else f"{float(trade.exit_price):.4f}"
        pnl = "-" if trade.pnl is None else f"{float(trade.pnl):+.4f}"
        status = trade.status.value
        lines.append(
            (
                f"#{trade.id} {status} | {side} | "
                f"entry={entry:.4f} | exit={exit_price} | pnl={pnl}"
            )
        )
    return "\n".join(lines)


async def _format_pnl_message(*, session: AsyncSession) -> str:
    """Build compact paper-trading statistics message for Telegram."""
    stats = await TradeRepository(session).get_trade_statistics()
    total_trades = int(stats.get("total_trades", 0))
    win_rate = float(stats.get("win_rate", 0.0)) * 100
    realized_pnl = float(stats.get("total_pnl", 0.0))
    open_positions = int(stats.get("open_positions", 0))
    return "\n".join(
        [
            "<b>📉 PnL summary</b>",
            f"Всего сделок: <code>{total_trades}</code>",
            f"Win rate: <code>{win_rate:.2f}%</code>",
            f"Realized PnL: <code>{realized_pnl:+.4f}</code>",
            f"Open positions: <code>{open_positions}</code>",
        ]
    )
