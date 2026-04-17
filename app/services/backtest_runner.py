import argparse
import asyncio
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging
from app.models.enums import SignalStatus, VerdictDirection
from app.repositories.signal_repo import SignalRepository
from app.schemas.backtest import (
    BacktestBucket,
    BacktestRow,
    BacktestRunResult,
    BacktestSummary,
)
from app.schemas.historical_prices import BatchPriceHistoryResult, PriceHistoryPoint
from app.schemas.market import MarketCandidate
from app.services.forecasting import calculate_brier_score, resolve_market_resolution
from app.services.historical_prices import ClobHistoricalPriceClient
from app.services.market_client import MarketClientProtocol, build_market_client


class HistoricalPriceClientProtocol(Protocol):
    """Contract for fetching batch token price history."""

    async def fetch_batch_prices_history(
        self,
        *,
        market_ids: list[str],
        start_ts: int | None = None,
        end_ts: int | None = None,
        interval: str = "1h",
        fidelity: int = 1,
    ) -> BatchPriceHistoryResult:
        """Return historical price series keyed by token id."""


class BacktestRunnerError(Exception):
    """Raised when a signal replay backtest cannot be completed."""


class BacktestRunner:
    """Read-only backtest runner over stored signals plus historical prices."""

    def __init__(
        self,
        *,
        settings: Settings,
        signal_repository: SignalRepository,
        market_client: MarketClientProtocol,
        historical_price_client: HistoricalPriceClientProtocol,
    ) -> None:
        self.settings = settings
        self.signal_repository = signal_repository
        self.market_client = market_client
        self.historical_price_client = historical_price_client

    async def run_signal_replay(
        self,
        *,
        since: datetime,
        until: datetime,
        signal_statuses: list[SignalStatus] | None = None,
        entry_lag_minutes: int = 5,
        interval: str = "1h",
        include_unresolved: bool = False,
    ) -> BacktestRunResult:
        signals = await self.signal_repository.list_created_between(
            since=_ensure_utc(since),
            until=_ensure_utc(until),
            signal_statuses=signal_statuses,
        )
        prepared: list[dict[str, object]] = []
        rows: list[BacktestRow] = []
        counts = {
            "direction_none_skipped_count": 0,
            "missing_candidate_count": 0,
            "missing_token_count": 0,
            "missing_history_count": 0,
            "unresolved_count": 0,
        }

        for signal in signals:
            analysis = signal.analysis
            if analysis is None:
                counts["missing_candidate_count"] += 1
                continue

            direction = _enum_value(analysis.direction)
            if direction == VerdictDirection.NONE.value:
                counts["direction_none_skipped_count"] += 1
                rows.append(
                    self._build_skipped_row(
                        signal=signal,
                        direction=direction,
                        skip_reason="direction_none",
                    )
                )
                continue

            candidate = self._load_candidate(signal)
            if candidate is None:
                counts["missing_candidate_count"] += 1
                rows.append(
                    self._build_skipped_row(
                        signal=signal,
                        direction=direction,
                        skip_reason="candidate_snapshot_not_found",
                    )
                )
                continue

            token_id = _select_token_id(candidate=candidate, direction=direction)
            if token_id is None:
                counts["missing_token_count"] += 1
                rows.append(
                    self._build_skipped_row(
                        signal=signal,
                        direction=direction,
                        skip_reason="entry_token_id_missing",
                    )
                )
                continue

            prepared.append(
                {
                    "signal": signal,
                    "direction": direction,
                    "candidate": candidate,
                    "token_id": token_id,
                    "target_entry_at": _ensure_utc(signal.created_at)
                    + timedelta(minutes=max(entry_lag_minutes, 0)),
                }
            )

        history_map = await self._load_history(
            prepared=prepared,
            until=_ensure_utc(until),
            interval=interval,
        )
        resolution_map = await self._load_resolutions(prepared=prepared)

        for item in prepared:
            signal = item["signal"]
            direction = str(item["direction"])
            token_id = str(item["token_id"])
            entry_points = history_map.get(token_id) or []
            target_entry_at = item["target_entry_at"]
            assert isinstance(target_entry_at, datetime)
            entry_price = _select_entry_price(
                points=entry_points,
                target_ts=int(target_entry_at.timestamp()),
            )
            if entry_price is None:
                counts["missing_history_count"] += 1
                rows.append(
                    self._build_skipped_row(
                        signal=signal,
                        direction=direction,
                        skip_reason="historical_entry_price_missing",
                        token_id=token_id,
                    )
                )
                continue

            resolution = resolution_map.get(signal.market_id)
            if resolution is None:
                counts["unresolved_count"] += 1
                if include_unresolved:
                    rows.append(
                        BacktestRow(
                            signal_id=signal.id,
                            analysis_id=signal.analysis_id,
                            market_id=signal.market_id,
                            created_at=_ensure_utc(signal.created_at).isoformat(),
                            signal_status=_enum_value(signal.signal_status),
                            direction=direction,
                            raw_probability=_raw_probability(signal),
                            calibrated_probability=float(signal.fair_probability),
                            stored_net_edge=float(signal.edge),
                            token_id=token_id,
                            entry_price_historical=entry_price,
                            skip_reason="market_unresolved",
                        )
                    )
                continue

            outcome_value = _side_aligned_outcome_value(
                direction=direction,
                yes_outcome_value=resolution.yes_outcome_value,
            )
            raw_probability = _raw_probability(signal)
            calibrated_probability = float(signal.fair_probability)
            raw_brier = calculate_brier_score(
                probability=raw_probability,
                outcome_value=outcome_value,
            )
            calibrated_brier = calculate_brier_score(
                probability=calibrated_probability,
                outcome_value=outcome_value,
            )
            realized_edge = round(outcome_value - entry_price, 4)
            rows.append(
                BacktestRow(
                    signal_id=signal.id,
                    analysis_id=signal.analysis_id,
                    market_id=signal.market_id,
                    created_at=_ensure_utc(signal.created_at).isoformat(),
                    signal_status=_enum_value(signal.signal_status),
                    direction=direction,
                    raw_probability=raw_probability,
                    calibrated_probability=calibrated_probability,
                    stored_net_edge=round(float(signal.edge), 4),
                    token_id=token_id,
                    entry_price_historical=entry_price,
                    resolution_outcome=resolution.outcome_label,
                    outcome_value=round(outcome_value, 4),
                    realized_edge=realized_edge,
                    realized_pnl_per_share=realized_edge,
                    raw_brier=raw_brier,
                    calibrated_brier=calibrated_brier,
                    hit=realized_edge > 0,
                )
            )

        resolved_rows = [row for row in rows if row.outcome_value is not None]
        summary = BacktestSummary(
            signals_total=len(signals),
            direction_none_skipped_count=counts["direction_none_skipped_count"],
            missing_candidate_count=counts["missing_candidate_count"],
            missing_token_count=counts["missing_token_count"],
            missing_history_count=counts["missing_history_count"],
            unresolved_count=counts["unresolved_count"],
            signals_scored=len(resolved_rows),
            resolved_count=len(resolved_rows),
            win_rate=round(
                sum(1 for row in resolved_rows if row.hit) / len(resolved_rows),
                4,
            )
            if resolved_rows
            else 0.0,
            avg_predicted_net_edge=_round_mean(row.stored_net_edge for row in resolved_rows),
            avg_realized_edge=_round_mean(row.realized_edge for row in resolved_rows),
            avg_raw_brier=_round_mean(row.raw_brier for row in resolved_rows),
            avg_calibrated_brier=_round_mean(row.calibrated_brier for row in resolved_rows),
        )
        buckets = self._build_buckets(resolved_rows)

        return BacktestRunResult(
            generated_at=datetime.now(UTC).isoformat(),
            mode="signal-replay",
            window_start=_ensure_utc(since).isoformat(),
            window_end=_ensure_utc(until).isoformat(),
            entry_lag_minutes=max(entry_lag_minutes, 0),
            interval=interval,
            signal_status_filter=_signal_status_filter_label(signal_statuses),
            summary=summary,
            buckets=buckets,
            rows=rows,
        )

    async def _load_history(
        self,
        *,
        prepared: list[dict[str, object]],
        until: datetime,
        interval: str,
    ) -> dict[str, list[PriceHistoryPoint]]:
        token_ids = sorted(
            {
                str(item["token_id"])
                for item in prepared
                if item.get("token_id") is not None
            }
        )
        if not token_ids:
            return {}

        target_times = [
            int(item["target_entry_at"].timestamp())
            for item in prepared
            if item.get("target_entry_at") is not None
        ]
        start_ts = min(target_times) if target_times else None
        end_ts = max(
            int(until.timestamp()),
            max(target_times) + _interval_to_seconds(interval),
        ) if target_times else int(until.timestamp())

        history_map: dict[str, list[PriceHistoryPoint]] = {}
        for chunk in _chunked(token_ids, 20):
            result = await self.historical_price_client.fetch_batch_prices_history(
                market_ids=chunk,
                start_ts=start_ts,
                end_ts=end_ts,
                interval=interval,
                fidelity=1,
            )
            for token_id, points in result.history.items():
                history_map[token_id] = sorted(points, key=lambda point: point.timestamp)

        return history_map

    async def _load_resolutions(
        self,
        *,
        prepared: list[dict[str, object]],
    ) -> dict[str, object]:
        market_ids = sorted(
            {
                signal.market_id
                for item in prepared
                if (signal := item.get("signal")) is not None
                and item.get("token_id") is not None
            }
        )
        if not market_ids:
            return {}

        markets = await asyncio.gather(
            *[self.market_client.fetch_market(market_id) for market_id in market_ids]
        )
        resolutions: dict[str, object] = {}
        for market_id, market in zip(market_ids, markets, strict=False):
            if market is None:
                continue
            resolution = resolve_market_resolution(market)
            if resolution is not None:
                resolutions[market_id] = resolution
        return resolutions

    def _load_candidate(self, signal) -> MarketCandidate | None:
        analysis = signal.analysis
        raw_response = analysis.raw_response or {}
        snapshots = raw_response.get("snapshots") or {}
        signal_snapshot = snapshots.get("signal_engine") or {}
        signal_items = signal_snapshot.get("signals") or []

        for item in signal_items:
            if item.get("signal_id") == signal.id and item.get("candidate") is not None:
                return MarketCandidate.model_validate(item["candidate"])

        market_snapshot = snapshots.get("market_matching") or {}
        for item in market_snapshot.get("candidates") or []:
            if item.get("market_id") == signal.market_id:
                return MarketCandidate.model_validate(item)
        return None

    def _build_buckets(self, rows: list[BacktestRow]) -> list[BacktestBucket]:
        bucket_size = max(min(self.settings.signal_calibration_bucket_size, 1.0), 0.01)
        grouped: dict[str, list[BacktestRow]] = defaultdict(list)

        for row in rows:
            bucket_key = _bucket_label(row.raw_probability, bucket_size=bucket_size)
            grouped[bucket_key].append(row)

        result: list[BacktestBucket] = []
        for bucket_key in sorted(grouped):
            bucket_rows = grouped[bucket_key]
            result.append(
                BacktestBucket(
                    bucket=bucket_key,
                    n=len(bucket_rows),
                    avg_raw_probability=round(mean(row.raw_probability for row in bucket_rows), 4),
                    avg_calibrated_probability=round(
                        mean(row.calibrated_probability for row in bucket_rows),
                        4,
                    ),
                    empirical_rate=round(mean(float(row.outcome_value or 0.0) for row in bucket_rows), 4),
                    raw_brier=round(mean(float(row.raw_brier or 0.0) for row in bucket_rows), 6),
                    calibrated_brier=round(
                        mean(float(row.calibrated_brier or 0.0) for row in bucket_rows),
                        6,
                    ),
                )
            )
        return result

    def _build_skipped_row(
        self,
        *,
        signal,
        direction: str,
        skip_reason: str,
        token_id: str | None = None,
    ) -> BacktestRow:
        return BacktestRow(
            signal_id=signal.id,
            analysis_id=signal.analysis_id,
            market_id=signal.market_id,
            created_at=_ensure_utc(signal.created_at).isoformat(),
            signal_status=_enum_value(signal.signal_status),
            direction=direction,
            raw_probability=_raw_probability(signal),
            calibrated_probability=float(signal.fair_probability),
            stored_net_edge=round(float(signal.edge), 4),
            token_id=token_id,
            skip_reason=skip_reason,
        )


async def run_signal_replay_backtest(
    session: AsyncSession,
    settings: Settings,
    *,
    since: datetime,
    until: datetime,
    signal_statuses: list[SignalStatus] | None = None,
    entry_lag_minutes: int = 5,
    interval: str = "1h",
    include_unresolved: bool = False,
) -> BacktestRunResult:
    """Convenience entrypoint for one signal-replay backtest."""
    runner = BacktestRunner(
        settings=settings,
        signal_repository=SignalRepository(session),
        market_client=build_market_client(settings),
        historical_price_client=ClobHistoricalPriceClient(settings),
    )
    return await runner.run_signal_replay(
        since=since,
        until=until,
        signal_statuses=signal_statuses,
        entry_lag_minutes=entry_lag_minutes,
        interval=interval,
        include_unresolved=include_unresolved,
    )


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _select_entry_price(
    *,
    points: list[PriceHistoryPoint],
    target_ts: int,
) -> float | None:
    for point in points:
        if point.timestamp >= target_ts:
            return round(point.price, 4)
    return None


def _select_token_id(
    *,
    candidate: MarketCandidate,
    direction: str,
) -> str | None:
    if direction == VerdictDirection.YES.value:
        return candidate.yes_token_id
    if direction == VerdictDirection.NO.value:
        return candidate.no_token_id
    return None


def _side_aligned_outcome_value(
    *,
    direction: str,
    yes_outcome_value: float,
) -> float:
    if direction == VerdictDirection.YES.value:
        return round(yes_outcome_value, 4)
    if direction == VerdictDirection.NO.value:
        return round(1 - yes_outcome_value, 4)
    raise BacktestRunnerError(f"Unsupported direction for outcome alignment: {direction}")


def _raw_probability(signal) -> float:
    raw_probability = getattr(signal, "raw_fair_probability", None)
    if raw_probability is None:
        raw_probability = signal.fair_probability
    return round(float(raw_probability), 4)


def _signal_status_filter_label(signal_statuses: list[SignalStatus] | None) -> str:
    if not signal_statuses:
        return "all"
    return ",".join(status.value for status in signal_statuses)


def _bucket_label(probability: float, *, bucket_size: float) -> str:
    bucket_center = round(round(probability / bucket_size) * bucket_size, 4)
    lower = max(bucket_center - (bucket_size / 2), 0.0)
    upper = min(bucket_center + (bucket_size / 2), 1.0)
    return f"{lower:.2f}-{upper:.2f}"


def _round_mean(values) -> float | None:
    normalized = [float(value) for value in values if value is not None]
    if not normalized:
        return None
    return round(mean(normalized), 6)


def _interval_to_seconds(interval: str) -> int:
    mapping = {
        "1m": 60,
        "1h": 3600,
        "6h": 21600,
        "1d": 86400,
        "1w": 604800,
        "all": 3600,
        "max": 3600,
    }
    if interval not in mapping:
        raise BacktestRunnerError(f"Unsupported interval: {interval}")
    return mapping[interval]


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    return _ensure_utc(parsed)


def _parse_signal_status_filter(value: str) -> list[SignalStatus] | None:
    normalized = value.strip().lower()
    if not normalized or normalized == "all":
        return None

    parsed: list[SignalStatus] = []
    for item in normalized.split(","):
        candidate = item.strip().upper()
        parsed.append(SignalStatus(candidate))
    return parsed


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run signal-replay backtest over stored signals.")
    parser.add_argument("--from", dest="from_ts", required=True, help="Inclusive start timestamp in ISO8601.")
    parser.add_argument("--to", dest="to_ts", required=True, help="Inclusive end timestamp in ISO8601.")
    parser.add_argument(
        "--signal-status",
        default="actionable",
        help="Signal status filter: actionable, watchlist, rejected, or comma-separated list. Use all for no filter.",
    )
    parser.add_argument(
        "--entry-lag-minutes",
        type=int,
        default=5,
        help="Delay after signal.created_at before selecting the historical entry price.",
    )
    parser.add_argument(
        "--interval",
        default="1h",
        choices=["1m", "1h", "6h", "1d", "1w", "all", "max"],
        help="Historical price interval passed to CLOB batch-prices-history.",
    )
    parser.add_argument(
        "--include-unresolved",
        action="store_true",
        help="Include unresolved markets in rows with skip_reason=market_unresolved.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)

    async with AsyncSessionLocal() as session:
        result = await run_signal_replay_backtest(
            session,
            settings,
            since=_parse_datetime(args.from_ts),
            until=_parse_datetime(args.to_ts),
            signal_statuses=_parse_signal_status_filter(args.signal_status),
            entry_lag_minutes=max(args.entry_lag_minutes, 0),
            interval=args.interval,
            include_unresolved=bool(args.include_unresolved),
        )
        print(result.model_dump_json())


if __name__ == "__main__":
    asyncio.run(_main())
