import argparse
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models.analysis import Analysis
from app.models.news import NewsItem
from app.models.scheduler_cycle import SchedulerCycle
from app.models.signal import Signal
from app.models.trade import PaperTrade


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print an aggregated pipeline diagnostics report."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Lookback window in hours.",
    )
    parser.add_argument(
        "--examples",
        type=int,
        default=5,
        help="Maximum example rows per section.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    now = datetime.now(UTC)
    since = now - timedelta(hours=args.hours)

    async with AsyncSessionLocal() as session:
        cycles = list(
            (
                await session.execute(
                    sa.select(SchedulerCycle)
                    .where(SchedulerCycle.started_at >= since)
                    .order_by(SchedulerCycle.started_at)
                )
            )
            .scalars()
            .all()
        )
        analyses = list(
            (
                await session.execute(
                    sa.select(Analysis)
                    .options(selectinload(Analysis.news_item), selectinload(Analysis.signals))
                    .where(Analysis.created_at >= since)
                    .order_by(Analysis.id.desc())
                )
            )
            .scalars()
            .all()
        )
        signals = list(
            (
                await session.execute(
                    sa.select(Signal)
                    .options(
                        selectinload(Signal.analysis).selectinload(Analysis.news_item)
                    )
                    .where(Signal.created_at >= since)
                    .order_by(Signal.id.desc())
                )
            )
            .scalars()
            .all()
        )
        trades = list(
            (
                await session.execute(
                    sa.select(PaperTrade)
                    .where(PaperTrade.created_at >= since)
                    .order_by(PaperTrade.id.desc())
                )
            )
            .scalars()
            .all()
        )
        inserted_news_count = int(
            (
                await session.execute(
                    sa.select(sa.func.count())
                    .select_from(NewsItem)
                    .where(NewsItem.created_at >= since)
                )
            ).scalar_one()
        )

    _print_report(
        generated_at=now,
        since=since,
        cycles=cycles,
        analyses=analyses,
        signals=signals,
        trades=trades,
        inserted_news_count=inserted_news_count,
        examples_limit=args.examples,
    )


def _print_report(
    *,
    generated_at: datetime,
    since: datetime,
    cycles: list[SchedulerCycle],
    analyses: list[Analysis],
    signals: list[Signal],
    trades: list[PaperTrade],
    inserted_news_count: int,
    examples_limit: int,
) -> None:
    completed_cycles = [cycle for cycle in cycles if cycle.status == "COMPLETED"]
    failed_cycles = [cycle for cycle in cycles if cycle.status == "FAILED"]
    status_counts = Counter(signal.signal_status.value for signal in signals)
    direction_counts = Counter(analysis.direction.value for analysis in analyses)
    signal_reason_counts = Counter(_classify_signal_explanation(signal.explanation) for signal in signals)
    risk_blockers = Counter()
    no_candidate_count = 0
    weak_candidate_count = 0
    candidate_counts = Counter()

    for analysis in analyses:
        market_snapshot = _market_matching_snapshot(analysis.raw_response)
        candidate_count = _safe_int(market_snapshot.get("candidate_count"))
        if candidate_count is not None:
            candidate_counts[candidate_count] += 1
            if candidate_count == 0:
                no_candidate_count += 1
        candidates = market_snapshot.get("candidates") or []
        if candidates:
            top_score = _safe_float(candidates[0].get("match_score"))
            if top_score is not None and top_score < 0.35:
                weak_candidate_count += 1

        for decision in _risk_decisions(analysis.raw_response):
            for blocker in decision.get("blockers") or []:
                risk_blockers[str(blocker).split(":", 1)[0]] += 1

    print("PIPELINE DIAGNOSTICS")
    print(f"generated_at: {_fmt_dt(generated_at)}")
    print(f"window_start: {_fmt_dt(since)}")
    print()
    print("FUNNEL")
    print(f"cycles: {len(cycles)} completed={len(completed_cycles)} failed={len(failed_cycles)}")
    print(f"fetched_news: {sum(int(cycle.fetched_news_count or 0) for cycle in completed_cycles)}")
    print(f"inserted_news: {inserted_news_count}")
    print(f"analyses: {len(analyses)}")
    print(f"signals: {len(signals)}")
    print(f"paper_trades_created: {len(trades)}")
    print()
    print("SIGNALS")
    _print_counter(status_counts)
    print()
    print("LLM DIRECTIONS")
    _print_counter(direction_counts)
    print()
    print("SIGNAL REASONS")
    _print_counter(signal_reason_counts)
    print()
    print("MARKET MATCHING")
    print(f"analyses_without_candidates: {no_candidate_count}")
    print(f"analyses_with_weak_top_candidate_lt_0.35: {weak_candidate_count}")
    if candidate_counts:
        print("candidate_count_distribution:")
        for count, total in sorted(candidate_counts.items()):
            print(f"- {count}: {total}")
    print()
    print("RISK BLOCKERS")
    if risk_blockers:
        _print_counter(risk_blockers)
    else:
        print("- none")
    print()
    _print_next_actions(
        analyses_count=len(analyses),
        signals_count=len(signals),
        trades_count=len(trades),
        status_counts=status_counts,
        direction_counts=direction_counts,
        signal_reason_counts=signal_reason_counts,
        no_candidate_count=no_candidate_count,
        weak_candidate_count=weak_candidate_count,
        risk_blockers=risk_blockers,
    )
    print()
    _print_examples(
        title="WATCHLIST EXAMPLES",
        signals=[signal for signal in signals if signal.signal_status.value == "WATCHLIST"],
        limit=examples_limit,
    )
    _print_examples(
        title="REJECTED EXAMPLES",
        signals=[signal for signal in signals if signal.signal_status.value == "REJECTED"],
        limit=examples_limit,
    )
    if failed_cycles:
        print("FAILED CYCLES")
        for cycle in failed_cycles[-examples_limit:]:
            print(f"- {cycle.cycle_id}: {cycle.error}")


def _print_examples(*, title: str, signals: list[Signal], limit: int) -> None:
    print(title)
    if not signals:
        print("- none")
        print()
        return
    for signal in signals[:limit]:
        analysis = signal.analysis
        news = analysis.news_item if analysis is not None else None
        market_snapshot = _market_matching_snapshot(analysis.raw_response if analysis else None)
        candidates = market_snapshot.get("candidates") or []
        top_score = _safe_float(candidates[0].get("match_score")) if candidates else None
        print(f"- signal_id={signal.id} status={signal.signal_status.value} edge={float(signal.edge):.4f}")
        if news is not None:
            print(f"  news={news.title}")
        if analysis is not None:
            print(
                "  "
                f"query={analysis.market_query} direction={analysis.direction.value} "
                f"confidence={float(analysis.confidence):.4f} relevance={float(analysis.relevance):.4f}"
            )
        print(f"  market={signal.market_question}")
        if top_score is not None:
            print(f"  top_match_score={top_score:.4f}")
        print(f"  reason={signal.explanation[:220]}")
    print()


def _print_counter(counter: Counter[str]) -> None:
    if not counter:
        print("- none")
        return
    for key, count in counter.most_common():
        print(f"- {key}: {count}")


def _print_next_actions(
    *,
    analyses_count: int,
    signals_count: int,
    trades_count: int,
    status_counts: Counter[str],
    direction_counts: Counter[str],
    signal_reason_counts: Counter[str],
    no_candidate_count: int,
    weak_candidate_count: int,
    risk_blockers: Counter[str],
) -> None:
    print("NEXT ACTIONS")
    actions: list[str] = []

    if analyses_count == 0:
        actions.append("No analyses in window: improve ingestion freshness or wait for new news.")
    if signals_count == 0 and analyses_count > 0:
        actions.append("Analyses exist but no signals: inspect market matching snapshots.")
    if direction_counts["NONE"] > max(direction_counts["YES"] + direction_counts["NO"], 1):
        actions.append(
            "direction=NONE dominates: improve LLM prompt/news filters before loosening risk."
        )
    if no_candidate_count:
        actions.append(
            "Some analyses have zero candidates: add query normalization/domain rules for those topics."
        )
    if weak_candidate_count:
        actions.append(
            "Weak top matches remain: audit examples and tighten matching before accepting more trades."
        )
    if status_counts["WATCHLIST"]:
        actions.append(
            "Watchlist exists: manually inspect examples; only then consider threshold calibration."
        )
    if risk_blockers:
        actions.append(
            "Risk blockers exist: fix the most common blocker category before changing position size."
        )
    if trades_count == 0 and status_counts["ACTIONABLE"] == 0:
        actions.append(
            "No trades and no actionable signals: do not relax risk yet; improve upstream signal quality."
        )
    if signal_reason_counts["direction_none"] and weak_candidate_count:
        actions.append(
            "direction=NONE plus weak matches suggests broad crypto/news queries are still too vague."
        )

    if not actions:
        actions.append("No obvious bottleneck in this window; collect more cycles before changing logic.")

    for action in actions:
        print(f"- {action}")


def _classify_signal_explanation(explanation: str) -> str:
    lowered = explanation.lower()
    if "direction=none" in lowered:
        return "direction_none"
    if "market match is weak" in lowered:
        return "weak_market_match"
    if "not above watchlist threshold" in lowered:
        return "edge_below_watchlist_threshold"
    if "watchlist" in lowered:
        return "watchlist_thresholds_not_met"
    if "actionable" in lowered:
        return "actionable"
    return "other"


def _market_matching_snapshot(raw_response: dict[str, Any] | None) -> dict[str, Any]:
    snapshots = (raw_response or {}).get("snapshots") or {}
    market_matching = snapshots.get("market_matching") or {}
    return market_matching if isinstance(market_matching, dict) else {}


def _risk_decisions(raw_response: dict[str, Any] | None) -> list[dict[str, Any]]:
    snapshots = (raw_response or {}).get("snapshots") or {}
    risk_engine = snapshots.get("risk_engine") or {}
    decisions = risk_engine.get("decisions") or []
    return [decision for decision in decisions if isinstance(decision, dict)]


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fmt_dt(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


if __name__ == "__main__":
    asyncio.run(_main())
