from datetime import UTC, datetime, timedelta
from collections import Counter

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scheduler_cycle import SchedulerCycle


class SchedulerCycleRepository:
    """Persistence helper for scheduler cycle history."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_cycle_id(self, *, cycle_id: str) -> SchedulerCycle | None:
        stmt = sa.select(SchedulerCycle).where(SchedulerCycle.cycle_id == cycle_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create_started(
        self,
        *,
        cycle_id: str,
        started_at: datetime,
        source_mode: str,
        llm_mode: str,
        fetch_mode: str,
    ) -> SchedulerCycle:
        cycle = await self.get_by_cycle_id(cycle_id=cycle_id)
        if cycle is None:
            cycle = SchedulerCycle(
                cycle_id=cycle_id,
                status="STARTED",
                source_mode=source_mode,
                llm_mode=llm_mode,
                fetch_mode=fetch_mode,
                started_at=started_at,
            )
            self.session.add(cycle)
        else:
            cycle.status = "STARTED"
            cycle.source_mode = source_mode
            cycle.llm_mode = llm_mode
            cycle.fetch_mode = fetch_mode
            cycle.started_at = started_at
            cycle.finished_at = None
            cycle.error = None
            cycle.error_count = None

        await self.session.commit()
        await self.session.refresh(cycle)
        return cycle

    async def mark_completed(
        self,
        *,
        cycle_id: str,
        finished_at: datetime,
        fetched_news_count: int,
        inserted_news_count: int,
        pending_news_count: int,
        processed_news_count: int,
        actionable_signal_count: int,
        approved_signal_count: int,
        opened_position_count: int,
        auto_close_evaluated_count: int,
        closed_position_count: int,
        error_count: int,
    ) -> SchedulerCycle:
        cycle = await self.get_by_cycle_id(cycle_id=cycle_id)
        if cycle is None:
            cycle = SchedulerCycle(
                cycle_id=cycle_id,
                status="COMPLETED",
                source_mode="unknown",
                llm_mode="unknown",
                fetch_mode="unknown",
                started_at=finished_at,
            )
            self.session.add(cycle)

        cycle.status = "COMPLETED"
        cycle.finished_at = finished_at
        cycle.fetched_news_count = fetched_news_count
        cycle.inserted_news_count = inserted_news_count
        cycle.pending_news_count = pending_news_count
        cycle.processed_news_count = processed_news_count
        cycle.actionable_signal_count = actionable_signal_count
        cycle.approved_signal_count = approved_signal_count
        cycle.opened_position_count = opened_position_count
        cycle.auto_close_evaluated_count = auto_close_evaluated_count
        cycle.closed_position_count = closed_position_count
        cycle.error_count = error_count
        cycle.error = None
        await self.session.commit()
        await self.session.refresh(cycle)
        return cycle

    async def mark_failed(
        self,
        *,
        cycle_id: str,
        finished_at: datetime,
        error: str,
    ) -> SchedulerCycle:
        cycle = await self.get_by_cycle_id(cycle_id=cycle_id)
        if cycle is None:
            cycle = SchedulerCycle(
                cycle_id=cycle_id,
                status="FAILED",
                source_mode="unknown",
                llm_mode="unknown",
                fetch_mode="unknown",
                started_at=finished_at,
            )
            self.session.add(cycle)

        cycle.status = "FAILED"
        cycle.finished_at = finished_at
        cycle.error_count = int(cycle.error_count or 0) + 1
        cycle.error = error[:2000]
        await self.session.commit()
        await self.session.refresh(cycle)
        return cycle

    async def sum_fetched_news_since(self, *, since: datetime) -> int:
        stmt = sa.select(sa.func.coalesce(sa.func.sum(SchedulerCycle.fetched_news_count), 0)).where(
            SchedulerCycle.started_at >= since
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_cycles_since(self, *, since: datetime) -> int:
        stmt = (
            sa.select(sa.func.count())
            .select_from(SchedulerCycle)
            .where(SchedulerCycle.started_at >= since)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_failed_cycles_since(self, *, since: datetime) -> int:
        stmt = (
            sa.select(sa.func.count())
            .select_from(SchedulerCycle)
            .where(
                SchedulerCycle.started_at >= since,
                SchedulerCycle.status == "FAILED",
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def sum_actionable_signals_since(self, *, since: datetime) -> int:
        stmt = sa.select(
            sa.func.coalesce(sa.func.sum(SchedulerCycle.actionable_signal_count), 0)
        ).where(SchedulerCycle.started_at >= since)
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_recent(self, *, limit: int = 20) -> list[SchedulerCycle]:
        stmt = sa.select(SchedulerCycle).order_by(SchedulerCycle.started_at.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def list_since(self, *, since: datetime) -> list[SchedulerCycle]:
        stmt = (
            sa.select(SchedulerCycle)
            .where(SchedulerCycle.started_at >= since)
            .order_by(SchedulerCycle.started_at)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def get_latest(self) -> SchedulerCycle | None:
        stmt = sa.select(SchedulerCycle).order_by(SchedulerCycle.started_at.desc()).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_latest_finished(self) -> SchedulerCycle | None:
        stmt = (
            sa.select(SchedulerCycle)
            .where(SchedulerCycle.finished_at.is_not(None))
            .order_by(SchedulerCycle.started_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_latest_completed(self) -> SchedulerCycle | None:
        stmt = (
            sa.select(SchedulerCycle)
            .where(SchedulerCycle.status == "COMPLETED")
            .order_by(SchedulerCycle.started_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count_consecutive_failed_cycles(self, *, limit: int = 20) -> int:
        cycles = await self.list_recent(limit=limit)
        count = 0
        for cycle in cycles:
            if cycle.status == "STARTED":
                continue
            if cycle.status != "FAILED":
                break
            count += 1
        return count

    async def count_consecutive_idle_cycles(self, *, limit: int = 20) -> int:
        cycles = await self.list_recent(limit=limit)
        count = 0
        for cycle in cycles:
            if cycle.status == "STARTED":
                continue
            if not is_idle_scheduler_cycle(cycle):
                break
            count += 1
        return count

    async def get_provider_failure_counts_since(
        self,
        *,
        since: datetime,
        limit: int = 5,
    ) -> list[tuple[str, int]]:
        stmt = (
            sa.select(SchedulerCycle.error)
            .where(
                SchedulerCycle.started_at >= since,
                SchedulerCycle.status == "FAILED",
                SchedulerCycle.error.is_not(None),
            )
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        counter = Counter(classify_cycle_error_provider(error) for error in rows)
        return counter.most_common(limit)

    async def get_active_provider_cooldowns(
        self,
        *,
        now: datetime,
        newsapi_cooldown_minutes: int,
        limit: int = 50,
    ) -> list[tuple[str, datetime, int, str]]:
        cycles = await self.list_recent(limit=limit)
        cooldowns: list[tuple[str, datetime, int, str]] = []

        newsapi_cooldown = infer_newsapi_rate_limit_cooldown(
            cycles=cycles,
            now=now,
            cooldown_minutes=newsapi_cooldown_minutes,
        )
        if newsapi_cooldown is not None:
            cooldown_until, remaining_seconds = newsapi_cooldown
            cooldowns.append(
                ("newsapi", cooldown_until, remaining_seconds, "rate_limit_inferred")
            )

        return cooldowns


def classify_cycle_error_provider(error: str | None) -> str:
    text = (error or "").lower()
    if "newsapi" in text:
        return "newsapi"
    if "openai" in text or "llm" in text:
        return "openai"
    if "gamma" in text or "polymarket" in text:
        return "polymarket_gamma"
    return "unknown"


def is_rate_limited_cycle_error(error: str | None) -> bool:
    text = (error or "").lower()
    return (
        "429" in text
        or "too many requests" in text
        or "ratelimited" in text
        or "rate limit" in text
    )


def infer_newsapi_rate_limit_cooldown(
    *,
    cycles,
    now: datetime,
    cooldown_minutes: int,
) -> tuple[datetime, int] | None:
    duration_minutes = max(cooldown_minutes, 1)

    for cycle in cycles:
        if classify_cycle_error_provider(getattr(cycle, "error", None)) != "newsapi":
            continue
        if not is_rate_limited_cycle_error(getattr(cycle, "error", None)):
            continue

        reference_at = getattr(cycle, "finished_at", None) or getattr(cycle, "started_at", None)
        if reference_at is None:
            continue
        if reference_at.tzinfo is None:
            reference_at = reference_at.replace(tzinfo=UTC)

        cooldown_until = reference_at + timedelta(minutes=duration_minutes)
        remaining_seconds = int((cooldown_until - now).total_seconds())
        if remaining_seconds > 0:
            return cooldown_until, remaining_seconds

        return None

    return None


def is_idle_scheduler_cycle(cycle: SchedulerCycle) -> bool:
    if cycle.status != "COMPLETED":
        return False

    return (
        int(cycle.inserted_news_count or 0) == 0
        and int(cycle.processed_news_count or 0) == 0
        and int(cycle.actionable_signal_count or 0) == 0
        and int(cycle.opened_position_count or 0) == 0
    )
