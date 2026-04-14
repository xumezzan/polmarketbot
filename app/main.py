import logging

from fastapi import Depends, FastAPI, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db_session
from app.logging_utils import log_event
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.news_repo import NewsRepository
from app.repositories.operator_state_repo import OperatorStateRepository
from app.repositories.runtime_flag_repo import RuntimeFlagRepository
from app.repositories.signal_repo import SignalRepository
from app.repositories.trade_repo import TradeRepository
from app.runtime_flags import RUNTIME_FLAG_PAPER_TRADING_KILL_SWITCH
from app.schemas.admin import (
    AdminPaperStatsResponse,
    AdminStatusResponse,
    KillSwitchStatus,
    OpenPositionsResponse,
    RecentSignalsResponse,
)
from app.services.alerting import AlertingService, build_alert_client
from app.services.operator import OperatorService


settings = get_settings()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name)


def _build_operator_service(session: AsyncSession) -> OperatorService:
    return OperatorService(
        news_repository=NewsRepository(session),
        analysis_repository=AnalysisRepository(session),
        signal_repository=SignalRepository(session),
        trade_repository=TradeRepository(session),
        runtime_flag_repository=RuntimeFlagRepository(session),
        operator_state_repository=OperatorStateRepository(session),
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


async def _set_kill_switch(
    *,
    session: AsyncSession,
    enabled: bool,
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
            source="admin_api",
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
