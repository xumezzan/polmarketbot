from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable

from app.config import Settings
from app.models.enums import MarketSide, VerdictDirection
from app.schemas.market import GammaMarket, MarketCandidate


@dataclass(frozen=True)
class CalibrationPoint:
    """One resolved forecast used for probability calibration."""

    raw_probability: float
    outcome_value: float


@dataclass(frozen=True)
class CalibrationResult:
    """Calibrated probability plus diagnostics."""

    raw_probability: float
    calibrated_probability: float
    sample_count: int
    empirical_rate: float | None


@dataclass(frozen=True)
class EdgeEstimate:
    """Execution-aware signal inputs."""

    reference_market_price: float
    execution_price: float
    raw_probability: float
    calibrated_probability: float
    raw_edge: float
    net_edge: float
    estimated_fee_rate: float
    estimated_fee_per_share: float
    market_consensus_weight: float


@dataclass(frozen=True)
class MarketResolution:
    """Resolved market outcome derived from a market snapshot."""

    outcome_label: str
    yes_outcome_value: float
    resolved_at: datetime
    resolution_source: str | None


def calculate_brier_score(*, probability: float, outcome_value: float) -> float:
    """Return the Brier score for one binary forecast."""
    return round((probability - outcome_value) ** 2, 6)


def calibrate_probability(
    *,
    settings: Settings,
    raw_probability: float,
    history: Iterable[CalibrationPoint],
) -> CalibrationResult:
    """Calibrate one probability from resolved history using simple bucket smoothing."""
    if not settings.signal_calibration_enabled:
        return CalibrationResult(
            raw_probability=round(raw_probability, 4),
            calibrated_probability=round(raw_probability, 4),
            sample_count=0,
            empirical_rate=None,
        )

    bucket_size = max(min(settings.signal_calibration_bucket_size, 1.0), 0.01)
    bucket_center = round(round(raw_probability / bucket_size) * bucket_size, 4)
    lower = max(bucket_center - (bucket_size / 2), 0.0)
    upper = min(bucket_center + (bucket_size / 2), 1.0)

    matching = [
        point
        for point in history
        if lower <= point.raw_probability <= upper
    ]
    sample_count = len(matching)

    if sample_count == 0:
        return CalibrationResult(
            raw_probability=round(raw_probability, 4),
            calibrated_probability=round(raw_probability, 4),
            sample_count=0,
            empirical_rate=None,
        )

    empirical_rate = sum(point.outcome_value for point in matching) / sample_count
    if sample_count < settings.signal_calibration_min_samples:
        return CalibrationResult(
            raw_probability=round(raw_probability, 4),
            calibrated_probability=round(raw_probability, 4),
            sample_count=sample_count,
            empirical_rate=round(empirical_rate, 4),
        )

    calibrated_probability = (
        (
            raw_probability * settings.signal_calibration_prior_strength
            + empirical_rate * sample_count
        )
        / (settings.signal_calibration_prior_strength + sample_count)
    )
    return CalibrationResult(
        raw_probability=round(raw_probability, 4),
        calibrated_probability=round(calibrated_probability, 4),
        sample_count=sample_count,
        empirical_rate=round(empirical_rate, 4),
    )


def build_execution_edge(
    *,
    settings: Settings,
    direction: str,
    candidate: MarketCandidate,
    reference_market_price: float,
    raw_probability: float,
    calibrated_probability: float,
) -> EdgeEstimate:
    """Return execution-aware edge after spread, fees, and market-consensus shrinkage."""
    side = _direction_to_side(direction)
    execution_price = select_entry_market_price(
        side=side.value,
        yes_price=candidate.yes_price,
        no_price=candidate.no_price,
        best_bid=candidate.best_bid,
        best_ask=candidate.best_ask,
        last_trade_price=candidate.last_trade_price,
    )
    fee_rate = float(candidate.effective_taker_fee_rate or 0.0)
    fee_per_share = round(fee_rate * execution_price * (1 - execution_price), 6)
    market_consensus_weight = _market_consensus_weight(
        settings=settings,
        liquidity=float(candidate.liquidity or 0.0),
    )
    consensus_adjusted_probability = (
        calibrated_probability
        + (reference_market_price - calibrated_probability) * market_consensus_weight
    )
    liquidity_penalty = _liquidity_penalty(
        settings=settings,
        liquidity=float(candidate.liquidity or 0.0),
        size_usd=settings.risk_max_trade_size_usd,
    )
    raw_edge = round(raw_probability - reference_market_price, 4)
    net_edge = round(
        consensus_adjusted_probability - execution_price - fee_per_share - liquidity_penalty,
        4,
    )
    return EdgeEstimate(
        reference_market_price=round(reference_market_price, 4),
        execution_price=round(execution_price, 4),
        raw_probability=round(raw_probability, 4),
        calibrated_probability=round(consensus_adjusted_probability, 4),
        raw_edge=raw_edge,
        net_edge=net_edge,
        estimated_fee_rate=round(fee_rate, 6),
        estimated_fee_per_share=fee_per_share,
        market_consensus_weight=round(market_consensus_weight, 4),
    )


def select_entry_market_price(
    *,
    side: str,
    yes_price: float | None,
    no_price: float | None,
    best_bid: float | None,
    best_ask: float | None,
    last_trade_price: float | None,
) -> float:
    """Return a conservative immediately executable entry price."""
    normalized_side = side.upper()

    if normalized_side == MarketSide.YES.value:
        if best_ask is not None:
            return round(best_ask, 4)
        if yes_price is not None:
            return round(yes_price, 4)
        if last_trade_price is not None:
            return round(last_trade_price, 4)
        raise ValueError("No usable YES-side execution price available.")

    if normalized_side == MarketSide.NO.value:
        no_ask = None
        if best_bid is not None:
            no_ask = round(1 - best_bid, 4)
        if no_ask is not None:
            return no_ask
        if no_price is not None:
            return round(no_price, 4)
        if yes_price is not None:
            return round(1 - yes_price, 4)
        if last_trade_price is not None:
            return round(1 - last_trade_price, 4)
        raise ValueError("No usable NO-side execution price available.")

    raise ValueError(f"Unsupported side: {side}")


def resolve_market_resolution(market: GammaMarket) -> MarketResolution | None:
    """Return settlement information when a market snapshot looks resolved."""
    yes_price = market.yes_price
    no_price = market.no_price
    if yes_price is None:
        return None

    if market.closed is not True and market.active is not False:
        return None

    if abs(yes_price - 1.0) <= 0.02 and (no_price is None or abs(no_price - 0.0) <= 0.02):
        return MarketResolution(
            outcome_label="YES",
            yes_outcome_value=1.0,
            resolved_at=market.end_date or datetime.now(UTC),
            resolution_source=market.resolution_source,
        )

    if abs(yes_price - 0.0) <= 0.02 and (no_price is None or abs(no_price - 1.0) <= 0.02):
        return MarketResolution(
            outcome_label="NO",
            yes_outcome_value=0.0,
            resolved_at=market.end_date or datetime.now(UTC),
            resolution_source=market.resolution_source,
        )

    if abs(yes_price - 0.5) <= 0.02 and (no_price is None or abs(no_price - 0.5) <= 0.02):
        return MarketResolution(
            outcome_label="HALF",
            yes_outcome_value=0.5,
            resolved_at=market.end_date or datetime.now(UTC),
            resolution_source=market.resolution_source,
        )

    return None


def estimate_openai_cost_usd(
    *,
    settings: Settings,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Return estimated OpenAI request cost in USD from configured token pricing."""
    input_cost = (prompt_tokens / 1_000_000) * settings.openai_input_cost_per_1m_tokens
    output_cost = (completion_tokens / 1_000_000) * settings.openai_output_cost_per_1m_tokens
    return round(input_cost + output_cost, 6)


def _direction_to_side(direction: str) -> MarketSide:
    if direction == VerdictDirection.YES.value:
        return MarketSide.YES
    if direction == VerdictDirection.NO.value:
        return MarketSide.NO
    raise ValueError(f"Unsupported direction for entry price: {direction}")


def _liquidity_penalty(
    *,
    settings: Settings,
    liquidity: float,
    size_usd: float,
) -> float:
    if liquidity <= 0 or size_usd <= 0:
        return round(settings.signal_liquidity_penalty_cap, 4)

    penalty = (size_usd / liquidity) * settings.signal_liquidity_penalty_factor
    return round(min(penalty, settings.signal_liquidity_penalty_cap), 4)


def _market_consensus_weight(
    *,
    settings: Settings,
    liquidity: float,
) -> float:
    if liquidity <= 0 or settings.signal_market_consensus_liquidity_cap <= 0:
        return 0.0

    return min(
        liquidity / settings.signal_market_consensus_liquidity_cap,
        1.0,
    ) * settings.signal_market_consensus_max_weight
