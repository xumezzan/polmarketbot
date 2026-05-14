from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class MarketRegimeInput(BaseModel):
    """Normalized market diagnostics used by the adaptive policy layer."""

    adx: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    atr_percentile: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    realized_volatility_percentile: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    liquidity_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    spread_bps: Optional[float] = Field(default=None, ge=0.0)
    volume_zscore: Optional[float] = None
    correlation_shift_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    sentiment_panic_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    sentiment_hype_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    news_intensity_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    breakout_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    mean_reversion_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    hurst_exponent: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class MarketRegimeAssessment(BaseModel):
    """Detected market state plus risk posture."""

    primary_regime: str
    secondary_regimes: list[str] = Field(default_factory=list)
    confidence: float
    risk_size_multiplier: float
    kill_switch: bool = False
    reasons: list[str] = Field(default_factory=list)
    scorecard: dict[str, float] = Field(default_factory=dict)


class StrategyAllocation(BaseModel):
    """One strategy allocation decision from the orchestrator."""

    strategy: str
    enabled: bool
    weight: float
    reason: str


class StrategyPolicy(BaseModel):
    """Full adaptive policy for one market snapshot."""

    assessment: MarketRegimeAssessment
    allocations: list[StrategyAllocation] = Field(default_factory=list)
    cash_weight: float
    max_position_size_multiplier: float
    notes: list[str] = Field(default_factory=list)


class TimeframeRegimeSignal(BaseModel):
    """One timeframe's feature snapshot for regime aggregation."""

    timeframe: str
    diagnostics: MarketRegimeInput
    weight: float = Field(default=1.0, ge=0.0)


class MultiTimeframeAssessment(BaseModel):
    """Regime consensus across multiple horizons."""

    dominant_regime: str
    alignment_score: float
    timeframe_regimes: dict[str, str] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)
    unified_market_state: str = "UNKNOWN"
    micro_trend: str = "UNKNOWN"
    macro_trend: str = "UNKNOWN"
    short_term_panic: bool = False
    long_term_direction: str = "UNKNOWN"
    market_noise: float = 0.0
    structural_movement: bool = False
    state_vector: list[float] = Field(default_factory=list)


class StrategyMemorySnapshot(BaseModel):
    """Recent strategy performance used by the meta-learning layer."""

    strategy: str
    observations: int = Field(default=0, ge=0)
    win_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    sharpe: Optional[float] = None
    max_drawdown: Optional[float] = Field(default=None, ge=0.0)
    recent_pnl: Optional[float] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class ModuleConfidenceSignal(BaseModel):
    """Confidence output from one strategy/model module."""

    module: str
    signal: str
    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    regime: Optional[str] = None
    weight: float = Field(default=1.0, ge=0.0)
    reason: Optional[str] = None


class ModelPerformanceSnapshot(BaseModel):
    """Baseline-vs-current model or strategy performance."""

    entity_id: str
    entity_type: str = "strategy"
    observations: int = Field(default=0, ge=0)
    baseline_win_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    current_win_rate: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    baseline_sharpe: Optional[float] = None
    current_sharpe: Optional[float] = None
    baseline_drawdown: Optional[float] = Field(default=None, ge=0.0)
    current_drawdown: Optional[float] = Field(default=None, ge=0.0)
    baseline_volatility: Optional[float] = Field(default=None, ge=0.0)
    current_volatility: Optional[float] = Field(default=None, ge=0.0)
    baseline_prediction_accuracy: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    current_prediction_accuracy: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class PerformanceDecaySignal(BaseModel):
    """One detected model or strategy degradation signal."""

    entity_id: str
    entity_type: str
    decay_score: float
    severity: str
    weight_multiplier: float
    reasons: list[str] = Field(default_factory=list)


class MarketStructureShiftAssessment(BaseModel):
    """Detected market structure change that should reduce risk."""

    detected: bool
    shift_score: float
    shift_types: list[str] = Field(default_factory=list)
    risk_multiplier: float
    reasons: list[str] = Field(default_factory=list)


class MetaLearningReport(BaseModel):
    """Meta-learning analysis of performance decay and market shifts."""

    performance_decays: list[PerformanceDecaySignal] = Field(default_factory=list)
    market_structure_shift: MarketStructureShiftAssessment
    global_weight_multiplier: float
    disabled_entities: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class HistoricalRegimeMemory(BaseModel):
    """Known historical regime used for similarity search."""

    memory_id: str
    label: str
    state_vector: list[float] = Field(default_factory=list)
    regimes: list[str] = Field(default_factory=list)
    successful_strategies: list[str] = Field(default_factory=list)
    failed_strategies: list[str] = Field(default_factory=list)
    dangerous_patterns: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class SimilarRegimeMatch(BaseModel):
    """One similar historical market state."""

    memory_id: str
    label: str
    similarity: float
    successful_strategies: list[str] = Field(default_factory=list)
    failed_strategies: list[str] = Field(default_factory=list)
    dangerous_patterns: list[str] = Field(default_factory=list)


class MemorySearchResult(BaseModel):
    """Result of asking whether this market was seen before."""

    matches: list[SimilarRegimeMatch] = Field(default_factory=list)
    best_similarity: float = 0.0
    caution_flags: list[str] = Field(default_factory=list)


class SelfImprovementProposal(BaseModel):
    """Proposed model/strategy change awaiting governance approval."""

    proposal_id: str
    change_type: str
    target: str
    requested_capital_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    disables_risk_engine: bool = False
    increases_leverage: bool = False
    requested_leverage: Optional[float] = Field(default=None, ge=0.0)
    disables_kill_switch: bool = False
    changes_global_risk_limits: bool = False


class SelfImprovementEvidence(BaseModel):
    """Validation evidence required before production rollout."""

    sandbox_passed: bool = False
    paper_trading_passed: bool = False
    out_of_sample_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    walk_forward_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    monte_carlo_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    shadow_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    monitoring_approved: bool = False
    sandbox_approved: bool = False


class GovernanceDecision(BaseModel):
    """Safety decision for autonomous adaptation proposals."""

    approved: bool
    mode: str
    max_capital_fraction: float
    blockers: list[str] = Field(default_factory=list)
    required_next_steps: list[str] = Field(default_factory=list)


class ConfidenceScore(BaseModel):
    """Composite confidence score before deployment gating."""

    score: float
    uncertainty: float = 0.0
    action: str = "AVOID_TRADE"
    position_size_multiplier: float = 0.0
    module_scores: dict[str, float] = Field(default_factory=dict)
    components: dict[str, float] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)


class DeploymentDecision(BaseModel):
    """Shadow/live deployment gate decision."""

    mode: str
    allow_live: bool
    reasons: list[str] = Field(default_factory=list)


class AdaptiveSystemDecision(BaseModel):
    """Full policy decision across regime, confidence, memory, and deployment."""

    timeframe_assessment: MultiTimeframeAssessment
    strategy_policy: StrategyPolicy
    confidence: ConfidenceScore
    meta_learning: Optional[MetaLearningReport] = None
    memory_search: Optional[MemorySearchResult] = None
    deployment: DeploymentDecision
    adjusted_allocations: list[StrategyAllocation] = Field(default_factory=list)
    cash_weight: float
    max_position_size_multiplier: float
    memory_notes: list[str] = Field(default_factory=list)
