import argparse
import asyncio
import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import AsyncSessionLocal
from app.repositories.forecast_observation_repo import ForecastObservationRepository
from app.schemas.trade import (
    ForecastCalibrationBreakdown,
    ForecastCalibrationBucket,
    ForecastCalibrationReport,
)
from app.services.forecasting import calculate_brier_score


def calculate_log_loss(*, probability: float, outcome_value: float) -> float:
    """Return binary log loss with probability clamping for numerical stability."""
    probability = min(max(float(probability), 1e-6), 1 - 1e-6)
    outcome_value = float(outcome_value)
    return round(
        -(
            outcome_value * math.log(probability)
            + (1 - outcome_value) * math.log(1 - probability)
        ),
        6,
    )


def build_forecast_calibration_report(
    *,
    generated_at: str,
    window_days: int | None,
    rows: list[dict[str, Any]],
    bucket_size: float = 0.10,
) -> ForecastCalibrationReport:
    """Build reliability and scoring metrics from normalized resolved forecasts."""
    if not rows:
        return ForecastCalibrationReport(
            generated_at=generated_at,
            window_days=window_days,
            notes=["no_resolved_observations"],
        )

    normalized_bucket_size = max(min(bucket_size, 1.0), 0.01)
    enriched = [_enrich_row(row) for row in rows]
    buckets = _build_buckets(enriched, bucket_size=normalized_bucket_size)
    by_source = _build_breakdowns(enriched, key_name="source", limit=10)
    by_model = _build_breakdowns(enriched, key_name="model", limit=10)
    by_topic = _build_breakdowns(enriched, key_name="topic", limit=10)
    weighted_calibration_error = (
        sum(bucket.calibration_error * bucket.count for bucket in buckets)
        / sum(bucket.count for bucket in buckets)
        if buckets
        else 0.0
    )

    notes = []
    if len(enriched) < 30:
        notes.append(f"need_more_resolved_observations:{len(enriched)}<30")
    if len(enriched) < 50:
        notes.append(f"preferred_resolved_observations:{len(enriched)}<50")

    return ForecastCalibrationReport(
        generated_at=generated_at,
        window_days=window_days,
        resolved_observations=len(enriched),
        avg_raw_probability=_mean(row["raw_probability"] for row in enriched),
        avg_calibrated_probability=_mean(row["calibrated_probability"] for row in enriched),
        actual_frequency=_mean(row["outcome_value"] for row in enriched),
        avg_raw_brier=_mean(row["raw_brier"] for row in enriched),
        avg_calibrated_brier=_mean(row["calibrated_brier"] for row in enriched),
        avg_raw_log_loss=_mean(row["raw_log_loss"] for row in enriched),
        avg_calibrated_log_loss=_mean(row["calibrated_log_loss"] for row in enriched),
        weighted_calibration_error=round(weighted_calibration_error, 6),
        buckets=buckets,
        by_source=by_source,
        by_model=by_model,
        by_topic=by_topic,
        notes=notes,
    )


class ForecastCalibrationReportService:
    """Build calibration reports from resolved forecast observations."""

    def __init__(self, *, observation_repository: ForecastObservationRepository) -> None:
        self.observation_repository = observation_repository

    async def build_report(
        self,
        *,
        window_days: int | None = 30,
        bucket_size: float = 0.10,
    ) -> ForecastCalibrationReport:
        generated_at = datetime.now(UTC)
        since = None if window_days is None else generated_at - timedelta(days=window_days)
        rows = await self.observation_repository.list_with_analysis_context(since=since)
        normalized_rows = [
            {
                "raw_probability": float(observation.raw_probability),
                "calibrated_probability": float(observation.calibrated_probability),
                "outcome_value": float(observation.outcome_value),
                "source": getattr(news, "source", None) or "unknown",
                "model": observation.model or "unknown",
                "topic": getattr(analysis, "market_query", None) or observation.market_id,
            }
            for observation, analysis, news in rows
        ]
        return build_forecast_calibration_report(
            generated_at=generated_at.isoformat(),
            window_days=window_days,
            rows=normalized_rows,
            bucket_size=bucket_size,
        )


async def get_forecast_calibration_report(
    *,
    window_days: int | None = 30,
    bucket_size: float = 0.10,
) -> ForecastCalibrationReport:
    """Convenience entrypoint for CLI callers."""
    async with AsyncSessionLocal() as session:
        service = ForecastCalibrationReportService(
            observation_repository=ForecastObservationRepository(session)
        )
        return await service.build_report(window_days=window_days, bucket_size=bucket_size)


def _enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    raw_probability = float(row["raw_probability"])
    calibrated_probability = float(row["calibrated_probability"])
    outcome_value = float(row["outcome_value"])
    return {
        **row,
        "raw_probability": raw_probability,
        "calibrated_probability": calibrated_probability,
        "outcome_value": outcome_value,
        "raw_brier": calculate_brier_score(
            probability=raw_probability,
            outcome_value=outcome_value,
        ),
        "calibrated_brier": calculate_brier_score(
            probability=calibrated_probability,
            outcome_value=outcome_value,
        ),
        "raw_log_loss": calculate_log_loss(
            probability=raw_probability,
            outcome_value=outcome_value,
        ),
        "calibrated_log_loss": calculate_log_loss(
            probability=calibrated_probability,
            outcome_value=outcome_value,
        ),
        "source": str(row.get("source") or "unknown"),
        "model": str(row.get("model") or "unknown"),
        "topic": str(row.get("topic") or "unknown"),
    }


def _build_buckets(
    rows: list[dict[str, Any]],
    *,
    bucket_size: float,
) -> list[ForecastCalibrationBucket]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    max_index = math.ceil(1 / bucket_size) - 1
    for row in rows:
        index = min(
            math.floor((float(row["calibrated_probability"]) + 1e-9) / bucket_size),
            max_index,
        )
        grouped[index].append(row)

    result = []
    for index in sorted(grouped):
        bucket_rows = grouped[index]
        lower = round(index * bucket_size, 2)
        upper = round(min(lower + bucket_size, 1.0), 2)
        avg_calibrated = _mean(row["calibrated_probability"] for row in bucket_rows)
        actual_frequency = _mean(row["outcome_value"] for row in bucket_rows)
        result.append(
            ForecastCalibrationBucket(
                bucket=f"{lower:.2f}-{upper:.2f}",
                count=len(bucket_rows),
                avg_raw_probability=_mean(row["raw_probability"] for row in bucket_rows),
                avg_calibrated_probability=avg_calibrated,
                actual_frequency=actual_frequency,
                calibration_error=round(abs(avg_calibrated - actual_frequency), 6),
                avg_raw_brier=_mean(row["raw_brier"] for row in bucket_rows),
                avg_calibrated_brier=_mean(row["calibrated_brier"] for row in bucket_rows),
                avg_raw_log_loss=_mean(row["raw_log_loss"] for row in bucket_rows),
                avg_calibrated_log_loss=_mean(row["calibrated_log_loss"] for row in bucket_rows),
            )
        )
    return result


def _build_breakdowns(
    rows: list[dict[str, Any]],
    *,
    key_name: str,
    limit: int,
) -> list[ForecastCalibrationBreakdown]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(key_name) or "unknown")].append(row)

    result = []
    for key, group_rows in grouped.items():
        avg_calibrated = _mean(row["calibrated_probability"] for row in group_rows)
        actual_frequency = _mean(row["outcome_value"] for row in group_rows)
        result.append(
            ForecastCalibrationBreakdown(
                key=key,
                count=len(group_rows),
                avg_calibrated_probability=avg_calibrated,
                actual_frequency=actual_frequency,
                calibration_error=round(abs(avg_calibrated - actual_frequency), 6),
                avg_calibrated_brier=_mean(row["calibrated_brier"] for row in group_rows),
                avg_calibrated_log_loss=_mean(row["calibrated_log_loss"] for row in group_rows),
            )
        )

    result.sort(key=lambda item: (item.count, -item.calibration_error), reverse=True)
    return result[:limit]


def _mean(values) -> float:
    items = [float(value) for value in values]
    return round(sum(items) / len(items), 6) if items else 0.0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build forecast calibration report.")
    parser.add_argument("--days", type=int, default=30, help="Resolved observation window in days.")
    parser.add_argument("--bucket-size", type=float, default=0.10, help="Reliability bucket size.")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    report = await get_forecast_calibration_report(
        window_days=args.days,
        bucket_size=args.bucket_size,
    )
    print(report.model_dump_json())


if __name__ == "__main__":
    asyncio.run(_main())
