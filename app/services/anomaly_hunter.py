import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.repositories.anomaly_repo import AnomalyRepository
from app.repositories.scheduler_cycle_repo import SchedulerCycleRepository
from app.schemas.anomaly import (
    AnomalyHunterAnalysisResult,
    AnomalyHunterReport,
    AnomalyHypothesisCreate,
    AnomalyHypothesisItem,
    AnomalyObservationCreate,
)
from app.schemas.scheduler import SchedulerCycleResult


class AnomalyHunter:
    """Read-only market and pipeline anomaly hunter."""

    def __init__(
        self,
        *,
        settings: Settings,
        anomaly_repository: AnomalyRepository,
        scheduler_cycle_repository: SchedulerCycleRepository,
    ) -> None:
        self.settings = settings
        self.anomaly_repository = anomaly_repository
        self.scheduler_cycle_repository = scheduler_cycle_repository

    async def observe_cycle(self, *, result: SchedulerCycleResult) -> int:
        """Persist read-only observations from one scheduler cycle."""
        if not self.settings.anomaly_hunter_enabled:
            return 0

        observed_at = _parse_datetime(result.finished_at) or datetime.now(UTC)
        observations = build_cycle_observations(result=result, observed_at=observed_at)
        if not observations:
            return 0
        await self.anomaly_repository.create_observations(observations)
        return len(observations)

    async def analyze_recent(self, *, window_hours: int = 6) -> AnomalyHunterAnalysisResult:
        """Analyze accumulated observations and persist hypotheses."""
        now = datetime.now(UTC)
        since = now - timedelta(hours=max(window_hours, 1))
        observations = await self.anomaly_repository.list_observations_since(since=since)
        cycles = await self.scheduler_cycle_repository.list_since(since=since)
        hypothesis_payloads = build_anomaly_hypotheses(
            generated_at=now,
            window_start=since,
            window_end=now,
            observations=observations,
            cycles=cycles,
        )
        rows = await self.anomaly_repository.create_hypotheses(hypothesis_payloads)
        return AnomalyHunterAnalysisResult(
            generated_at=now.isoformat(),
            window_start=since.isoformat(),
            window_end=now.isoformat(),
            observations_analyzed=len(observations),
            hypotheses_created=len(rows),
            hypotheses=[_hypothesis_to_item(row) for row in rows],
        )

    async def build_report(self, *, window_hours: int = 24) -> AnomalyHunterReport:
        """Build a daily-style operator report from recent hypotheses."""
        now = datetime.now(UTC)
        since = now - timedelta(hours=max(window_hours, 1))
        observations_count = await self.anomaly_repository.count_observations_since(since=since)
        hypotheses = await self.anomaly_repository.list_hypotheses_since(since=since, limit=10)
        notes = []
        if observations_count == 0:
            notes.append("no_anomaly_observations")
        if not hypotheses:
            notes.append("no_anomaly_hypotheses")
        return AnomalyHunterReport(
            generated_at=now.isoformat(),
            window_hours=window_hours,
            observations_count=observations_count,
            hypotheses_count=len(hypotheses),
            top_hypotheses=[_hypothesis_to_item(row) for row in hypotheses],
            notes=notes,
        )

    async def should_analyze_now(self) -> bool:
        latest = await self.anomaly_repository.latest_hypothesis_generated_at()
        if latest is None:
            return True
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        interval = timedelta(hours=max(self.settings.anomaly_hunter_analysis_interval_hours, 1))
        return datetime.now(UTC) - latest >= interval


def build_cycle_observations(
    *,
    result: SchedulerCycleResult,
    observed_at: datetime,
) -> list[AnomalyObservationCreate]:
    """Create observation payloads from one cycle result."""
    observations = [
        AnomalyObservationCreate(
            cycle_id=result.cycle_id,
            observed_at=observed_at.isoformat(),
            observation_type="cycle_summary",
            subject_type="cycle",
            subject_id=result.cycle_id,
            severity="INFO",
            score=10.0,
            title="Scheduler cycle observed",
            details={
                "inserted_news_count": result.inserted_news_count,
                "pending_news_count": result.pending_news_count,
                "processed_news_count": result.processed_news_count,
                "actionable_signal_count": result.actionable_signal_count,
                "approved_signal_count": result.approved_signal_count,
                "opened_position_count": result.opened_position_count,
                "closed_position_count": result.closed_position_count,
                "error_count": result.error_count,
            },
        )
    ]
    if result.error_count > 0:
        observations.append(
            AnomalyObservationCreate(
                cycle_id=result.cycle_id,
                observed_at=observed_at.isoformat(),
                observation_type="cycle_error",
                subject_type="cycle",
                subject_id=result.cycle_id,
                severity="WARN",
                score=min(95.0, 45.0 + result.error_count * 10.0),
                title="Scheduler cycle had item errors",
                details={"error_count": result.error_count},
            )
        )

    for item in result.item_results:
        if item.market_candidate_count == 0 and item.analysis_id is not None:
            observations.append(
                AnomalyObservationCreate(
                    cycle_id=result.cycle_id,
                    observed_at=observed_at.isoformat(),
                    observation_type="market_matching_dead_zone",
                    subject_type="analysis",
                    subject_id=str(item.analysis_id),
                    severity="INFO",
                    score=35.0,
                    title="Analysis produced no market candidates",
                    details={"news_item_id": item.news_item_id},
                )
            )
        if item.actionable_signal_count > 0 and item.approved_signal_count == 0:
            observations.append(
                AnomalyObservationCreate(
                    cycle_id=result.cycle_id,
                    observed_at=observed_at.isoformat(),
                    observation_type="risk_bottleneck",
                    subject_type="analysis",
                    subject_id=str(item.analysis_id or item.news_item_id),
                    severity="INFO",
                    score=45.0,
                    title="Actionable signals were blocked by risk",
                    details={
                        "news_item_id": item.news_item_id,
                        "actionable_signal_count": item.actionable_signal_count,
                        "blocked_signal_count": item.blocked_signal_count,
                    },
                )
            )
        if item.opened_position_count > 0:
            observations.append(
                AnomalyObservationCreate(
                    cycle_id=result.cycle_id,
                    observed_at=observed_at.isoformat(),
                    observation_type="opportunity_flow",
                    subject_type="analysis",
                    subject_id=str(item.analysis_id or item.news_item_id),
                    severity="INFO",
                    score=55.0,
                    title="Pipeline found an approved opportunity",
                    details={
                        "news_item_id": item.news_item_id,
                        "opened_position_count": item.opened_position_count,
                        "opened_trade_ids": item.opened_trade_ids,
                    },
                )
            )

    if result.closed_position_count > 0:
        observations.append(
            AnomalyObservationCreate(
                cycle_id=result.cycle_id,
                observed_at=observed_at.isoformat(),
                observation_type="position_exit_flow",
                subject_type="cycle",
                subject_id=result.cycle_id,
                severity="INFO",
                score=50.0,
                title="Paper positions closed during cycle",
                details={
                    "closed_position_count": result.closed_position_count,
                    "closed_trade_ids": result.closed_trade_ids,
                },
            )
        )

    return observations


def build_anomaly_hypotheses(
    *,
    generated_at: datetime,
    window_start: datetime,
    window_end: datetime,
    observations,
    cycles,
) -> list[AnomalyHypothesisCreate]:
    """Turn recent observations into analyst-style hypotheses."""
    observation_counts = Counter(row.observation_type for row in observations)
    failed_cycles = [
        cycle
        for cycle in cycles
        if cycle.status == "FAILED" or int(cycle.error_count or 0) > 0
    ]
    hypotheses: list[AnomalyHypothesisCreate] = []

    if failed_cycles:
        score = min(100.0, 55.0 + len(failed_cycles) * 4.0)
        hypotheses.append(
            _hypothesis(
                generated_at=generated_at,
                window_start=window_start,
                window_end=window_end,
                hypothesis_type="pipeline_instability",
                score=score,
                title="Pipeline instability may be hiding or distorting edge",
                rationale=(
                    f"{len(failed_cycles)} cycles in the window had failed status or item errors. "
                    "Anomaly Hunter treats this as a data quality problem before strategy tuning."
                ),
                evidence={
                    "failed_or_error_cycles": len(failed_cycles),
                    "recent_errors": [
                        str(getattr(cycle, "error", "") or "")[:240]
                        for cycle in failed_cycles[-5:]
                        if getattr(cycle, "error", None)
                    ],
                },
            )
        )

    dead_zone_count = observation_counts["market_matching_dead_zone"]
    if dead_zone_count >= 3:
        hypotheses.append(
            _hypothesis(
                generated_at=generated_at,
                window_start=window_start,
                window_end=window_end,
                hypothesis_type="market_matching_dead_zone",
                score=min(90.0, 45.0 + dead_zone_count * 5.0),
                title="News flow is producing themes with no matched market",
                rationale=(
                    f"{dead_zone_count} analyses produced zero market candidates. "
                    "This can reveal missed query normalization, a new market category, or hype with no tradable venue."
                ),
                evidence={"dead_zone_observations": dead_zone_count},
            )
        )

    risk_bottleneck_count = observation_counts["risk_bottleneck"]
    opportunity_count = observation_counts["opportunity_flow"]
    if risk_bottleneck_count >= 2 and opportunity_count == 0:
        hypotheses.append(
            _hypothesis(
                generated_at=generated_at,
                window_start=window_start,
                window_end=window_end,
                hypothesis_type="risk_bottleneck",
                score=min(85.0, 50.0 + risk_bottleneck_count * 6.0),
                title="Actionable ideas are being stopped after signal generation",
                rationale=(
                    f"{risk_bottleneck_count} actionable batches were blocked and no positions opened. "
                    "Review blockers before changing thresholds; this may expose an overly strict guard or weak market quality."
                ),
                evidence={
                    "risk_bottleneck_observations": risk_bottleneck_count,
                    "opportunity_observations": opportunity_count,
                },
            )
        )

    if opportunity_count >= 2:
        hypotheses.append(
            _hypothesis(
                generated_at=generated_at,
                window_start=window_start,
                window_end=window_end,
                hypothesis_type="opportunity_cluster",
                score=min(80.0, 45.0 + opportunity_count * 7.0),
                title="Multiple approved opportunities appeared in the same window",
                rationale=(
                    f"{opportunity_count} observations opened positions. "
                    "This is a candidate cluster for manual theme review, not an automatic reason to increase size."
                ),
                evidence={"opportunity_observations": opportunity_count},
            )
        )

    exit_count = observation_counts["position_exit_flow"]
    if exit_count >= 2:
        hypotheses.append(
            _hypothesis(
                generated_at=generated_at,
                window_start=window_start,
                window_end=window_end,
                hypothesis_type="exit_cluster",
                score=min(80.0, 45.0 + exit_count * 8.0),
                title="Several paper positions exited in the same window",
                rationale=(
                    f"{exit_count} cycles closed positions. Review whether exits were thesis breaks, max hold, or price moves."
                ),
                evidence={"exit_observations": exit_count},
            )
        )

    hypotheses.sort(key=lambda item: item.score, reverse=True)
    return hypotheses


async def run_anomaly_hunter_observe_cycle(
    session: AsyncSession,
    settings: Settings,
    *,
    result: SchedulerCycleResult,
) -> int:
    service = AnomalyHunter(
        settings=settings,
        anomaly_repository=AnomalyRepository(session),
        scheduler_cycle_repository=SchedulerCycleRepository(session),
    )
    return await service.observe_cycle(result=result)


async def run_anomaly_hunter_analysis(
    session: AsyncSession,
    settings: Settings,
    *,
    window_hours: int = 6,
) -> AnomalyHunterAnalysisResult:
    service = AnomalyHunter(
        settings=settings,
        anomaly_repository=AnomalyRepository(session),
        scheduler_cycle_repository=SchedulerCycleRepository(session),
    )
    return await service.analyze_recent(window_hours=window_hours)


async def run_anomaly_hunter_report(
    session: AsyncSession,
    settings: Settings,
    *,
    window_hours: int = 24,
) -> AnomalyHunterReport:
    service = AnomalyHunter(
        settings=settings,
        anomaly_repository=AnomalyRepository(session),
        scheduler_cycle_repository=SchedulerCycleRepository(session),
    )
    return await service.build_report(window_hours=window_hours)


def _hypothesis(
    *,
    generated_at: datetime,
    window_start: datetime,
    window_end: datetime,
    hypothesis_type: str,
    score: float,
    title: str,
    rationale: str,
    evidence: dict[str, object],
) -> AnomalyHypothesisCreate:
    return AnomalyHypothesisCreate(
        generated_at=generated_at.isoformat(),
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
        hypothesis_type=hypothesis_type,
        status="OPEN",
        score=round(score, 2),
        title=title,
        rationale=rationale,
        evidence=evidence,
    )


def _hypothesis_to_item(row) -> AnomalyHypothesisItem:
    return AnomalyHypothesisItem(
        id=row.id,
        generated_at=row.generated_at.isoformat(),
        window_start=row.window_start.isoformat(),
        window_end=row.window_end.isoformat(),
        hypothesis_type=row.hypothesis_type,
        status=row.status,
        score=float(row.score),
        title=row.title,
        rationale=row.rationale,
        evidence=row.evidence or {},
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Anomaly Hunter tasks.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    analyze_parser = subparsers.add_parser("analyze", help="Analyze recent observations.")
    analyze_parser.add_argument("--hours", type=int, default=6)
    report_parser = subparsers.add_parser("report", help="Build recent anomaly report.")
    report_parser.add_argument("--hours", type=int, default=24)
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    async with AsyncSessionLocal() as session:
        if args.command == "analyze":
            result = await run_anomaly_hunter_analysis(
                session,
                settings,
                window_hours=max(args.hours, 1),
            )
        else:
            result = await run_anomaly_hunter_report(
                session,
                settings,
                window_hours=max(args.hours, 1),
            )
        print(result.model_dump_json())


if __name__ == "__main__":
    asyncio.run(_main())
