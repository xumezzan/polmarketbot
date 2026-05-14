from __future__ import annotations

from app.config import Settings
from app.schemas.adaptive import (
    AdaptiveSystemDecision,
    ConfidenceScore,
    DeploymentDecision,
    GovernanceDecision,
    HistoricalRegimeMemory,
    MarketRegimeAssessment,
    MarketRegimeInput,
    MarketStructureShiftAssessment,
    MemorySearchResult,
    MetaLearningReport,
    ModelPerformanceSnapshot,
    ModuleConfidenceSignal,
    MultiTimeframeAssessment,
    PerformanceDecaySignal,
    SelfImprovementEvidence,
    SelfImprovementProposal,
    SimilarRegimeMatch,
    StrategyAllocation,
    StrategyMemorySnapshot,
    StrategyPolicy,
    TimeframeRegimeSignal,
)


REGIME_TREND = "TREND"
REGIME_RANGE = "RANGE"
REGIME_PANIC = "PANIC"
REGIME_HIGH_VOLATILITY = "HIGH_VOLATILITY"
REGIME_LOW_LIQUIDITY = "LOW_LIQUIDITY"
REGIME_NEWS_DRIVEN = "NEWS_DRIVEN"
REGIME_MEAN_REVERSION = "MEAN_REVERSION"
REGIME_BREAKOUT = "BREAKOUT"

STRATEGY_TREND = "trend_following"
STRATEGY_MEAN_REVERSION = "mean_reversion"
STRATEGY_STAT_ARB = "statistical_arbitrage"
STRATEGY_GRID = "grid"
STRATEGY_MARKET_MAKING = "market_making"
STRATEGY_SENTIMENT = "sentiment_ai"


def build_adaptive_system_decision(
    *,
    settings: Settings,
    timeframe_signals: list[TimeframeRegimeSignal],
    strategy_memory: list[StrategyMemorySnapshot] | None = None,
    module_confidences: list[ModuleConfidenceSignal] | None = None,
    historical_memories: list[HistoricalRegimeMemory] | None = None,
    performance_snapshots: list[ModelPerformanceSnapshot] | None = None,
    previous_diagnostics: MarketRegimeInput | None = None,
    shadow_observation_count: int = 0,
    live_trading_requested: bool = False,
) -> AdaptiveSystemDecision:
    """Build the full adaptive decision from features through deployment gate."""
    timeframe_assessment = assess_multi_timeframe_regime(
        settings=settings,
        timeframe_signals=timeframe_signals,
    )
    aggregated_diagnostics = aggregate_timeframe_diagnostics(timeframe_signals)
    strategy_policy = build_strategy_policy(
        settings=settings,
        diagnostics=aggregated_diagnostics,
    )
    confidence = score_policy_confidence(
        settings=settings,
        timeframe_assessment=timeframe_assessment,
        strategy_policy=strategy_policy,
        diagnostics=aggregated_diagnostics,
        strategy_memory=strategy_memory or [],
        module_confidences=module_confidences or [],
    )
    meta_learning = analyze_meta_learning(
        settings=settings,
        current_diagnostics=aggregated_diagnostics,
        previous_diagnostics=previous_diagnostics,
        performance_snapshots=performance_snapshots or [],
    )
    memory_search = search_similar_regime_memories(
        settings=settings,
        current_state_vector=timeframe_assessment.state_vector,
        memories=historical_memories or [],
    )
    adjusted_allocations, cash_weight, memory_notes = apply_meta_learning_memory(
        settings=settings,
        allocations=strategy_policy.allocations,
        base_cash_weight=strategy_policy.cash_weight,
        confidence=confidence,
        strategy_memory=strategy_memory or [],
        meta_learning=meta_learning,
    )
    deployment = decide_deployment_mode(
        settings=settings,
        confidence=confidence,
        strategy_policy=strategy_policy,
        shadow_observation_count=shadow_observation_count,
        live_trading_requested=live_trading_requested,
    )

    return AdaptiveSystemDecision(
        timeframe_assessment=timeframe_assessment,
        strategy_policy=strategy_policy,
        confidence=confidence,
        meta_learning=meta_learning,
        memory_search=memory_search,
        deployment=deployment,
        adjusted_allocations=adjusted_allocations,
        cash_weight=cash_weight,
        max_position_size_multiplier=strategy_policy.max_position_size_multiplier,
        memory_notes=memory_notes,
    )


def analyze_meta_learning(
    *,
    settings: Settings,
    current_diagnostics: MarketRegimeInput,
    performance_snapshots: list[ModelPerformanceSnapshot],
    previous_diagnostics: MarketRegimeInput | None = None,
) -> MetaLearningReport:
    """Analyze model decay and market structure shifts before allocation."""
    if not settings.meta_learning_enabled:
        return MetaLearningReport(
            performance_decays=[],
            market_structure_shift=MarketStructureShiftAssessment(
                detected=False,
                shift_score=0.0,
                risk_multiplier=1.0,
                reasons=["meta_learning_disabled"],
            ),
            global_weight_multiplier=1.0,
            notes=["meta_learning_disabled"],
        )

    decays = [
        _analyze_performance_decay(settings=settings, snapshot=snapshot)
        for snapshot in performance_snapshots
    ]
    decays = [decay for decay in decays if decay.decay_score > 0.0]
    shift = detect_market_structure_shift(
        settings=settings,
        current_diagnostics=current_diagnostics,
        previous_diagnostics=previous_diagnostics,
    )
    strongest_decay = max((decay.decay_score for decay in decays), default=0.0)
    decay_multiplier = 1.0
    if strongest_decay >= settings.meta_learning_decay_disable_threshold:
        decay_multiplier = 0.70
    elif strongest_decay >= settings.meta_learning_decay_reduce_threshold:
        decay_multiplier = 0.85

    global_weight_multiplier = min(decay_multiplier, shift.risk_multiplier)
    disabled_entities = [
        decay.entity_id
        for decay in decays
        if decay.severity == "CRITICAL"
    ]
    notes = []
    if decays:
        notes.append("performance_decay_detected")
    if shift.detected:
        notes.append("market_structure_shift_detected")

    return MetaLearningReport(
        performance_decays=decays,
        market_structure_shift=shift,
        global_weight_multiplier=round(global_weight_multiplier, 4),
        disabled_entities=disabled_entities,
        notes=notes,
    )


def detect_market_structure_shift(
    *,
    settings: Settings,
    current_diagnostics: MarketRegimeInput,
    previous_diagnostics: MarketRegimeInput | None = None,
) -> MarketStructureShiftAssessment:
    """Detect liquidity, spread, correlation, volatility, and news-driven shifts."""
    shift_components: list[float] = []
    shift_types: list[str] = []
    reasons: list[str] = []

    liquidity_score = current_diagnostics.liquidity_score
    if (
        liquidity_score is not None
        and liquidity_score <= settings.market_structure_liquidity_drop_threshold
    ):
        pressure = 1.0 - liquidity_score
        shift_components.append(pressure)
        shift_types.append("liquidity_dried_up")
        reasons.append(
            "liquidity_score_low:"
            f"{liquidity_score:.4f}<="
            f"{settings.market_structure_liquidity_drop_threshold:.4f}"
        )

    spread_bps = current_diagnostics.spread_bps
    if spread_bps is not None and spread_bps >= settings.market_structure_spread_widening_bps:
        pressure = _clamp(spread_bps / 500.0)
        shift_components.append(pressure)
        shift_types.append("spread_widened")
        reasons.append(
            "spread_widened:"
            f"{spread_bps:.2f}>={settings.market_structure_spread_widening_bps:.2f}"
        )

    correlation_shift = current_diagnostics.correlation_shift_score
    if (
        correlation_shift is not None
        and correlation_shift >= settings.market_structure_correlation_shift_threshold
    ):
        shift_components.append(correlation_shift)
        shift_types.append("correlation_breakdown")
        reasons.append(
            "correlation_shift_high:"
            f"{correlation_shift:.4f}>="
            f"{settings.market_structure_correlation_shift_threshold:.4f}"
        )

    volatility = max(
        current_diagnostics.atr_percentile or 0.0,
        current_diagnostics.realized_volatility_percentile or 0.0,
    )
    if volatility >= settings.market_structure_volatility_shift_threshold:
        shift_components.append(volatility)
        shift_types.append("volatility_regime_change")
        reasons.append(
            "volatility_shift_high:"
            f"{volatility:.4f}>={settings.market_structure_volatility_shift_threshold:.4f}"
        )

    news_intensity = current_diagnostics.news_intensity_score or 0.0
    if news_intensity >= settings.market_regime_news_driven_threshold:
        shift_components.append(news_intensity)
        shift_types.append("news_driven_market")
        reasons.append(
            "news_intensity_high:"
            f"{news_intensity:.4f}>={settings.market_regime_news_driven_threshold:.4f}"
        )

    if previous_diagnostics is not None:
        _append_previous_diagnostic_shifts(
            settings=settings,
            current=current_diagnostics,
            previous=previous_diagnostics,
            shift_components=shift_components,
            shift_types=shift_types,
            reasons=reasons,
        )

    shift_score = max(shift_components, default=0.0)
    detected = shift_score >= settings.market_structure_shift_threshold
    risk_multiplier = 1.0
    if detected:
        risk_multiplier = max(0.25, 1.0 - shift_score * 0.50)

    return MarketStructureShiftAssessment(
        detected=detected,
        shift_score=round(shift_score, 4),
        shift_types=shift_types,
        risk_multiplier=round(risk_multiplier, 4),
        reasons=reasons,
    )


def search_similar_regime_memories(
    *,
    settings: Settings,
    current_state_vector: list[float],
    memories: list[HistoricalRegimeMemory],
) -> MemorySearchResult:
    """Return historical regimes similar to the current fused market state."""
    matches: list[SimilarRegimeMatch] = []
    if not current_state_vector:
        return MemorySearchResult(caution_flags=["missing_current_state_vector"])

    for memory in memories:
        similarity = _cosine_similarity(current_state_vector, memory.state_vector)
        if similarity < settings.ai_memory_similarity_threshold:
            continue
        matches.append(
            SimilarRegimeMatch(
                memory_id=memory.memory_id,
                label=memory.label,
                similarity=round(similarity, 4),
                successful_strategies=memory.successful_strategies,
                failed_strategies=memory.failed_strategies,
                dangerous_patterns=memory.dangerous_patterns,
            )
        )

    matches.sort(key=lambda item: item.similarity, reverse=True)
    caution_flags = sorted(
        {
            pattern
            for match in matches
            for pattern in match.dangerous_patterns
        }
    )
    return MemorySearchResult(
        matches=matches[:5],
        best_similarity=matches[0].similarity if matches else 0.0,
        caution_flags=caution_flags,
    )


def evaluate_self_improvement_governance(
    *,
    settings: Settings,
    proposal: SelfImprovementProposal,
    evidence: SelfImprovementEvidence,
    max_allowed_leverage: float,
) -> GovernanceDecision:
    """Gate autonomous model changes behind validation and hard risk rules."""
    blockers: list[str] = []
    required_next_steps: list[str] = []

    if proposal.disables_risk_engine:
        blockers.append("cannot_disable_risk_engine")
    if proposal.disables_kill_switch:
        blockers.append("cannot_disable_kill_switch")
    if proposal.changes_global_risk_limits:
        blockers.append("cannot_change_global_risk_limits")
    if proposal.increases_leverage and (
        proposal.requested_leverage is None
        or proposal.requested_leverage > max_allowed_leverage
    ):
        blockers.append("leverage_above_limit")

    if not evidence.sandbox_passed:
        required_next_steps.append("run_sandbox_validation")
    if not evidence.sandbox_approved:
        required_next_steps.append("sandbox_approval_required")
    if not evidence.paper_trading_passed:
        required_next_steps.append("run_paper_trading_validation")
    if (
        evidence.out_of_sample_score is None
        or evidence.out_of_sample_score < settings.self_improvement_min_oos_score
    ):
        required_next_steps.append("out_of_sample_validation_required")
    if (
        evidence.walk_forward_score is None
        or evidence.walk_forward_score < settings.self_improvement_min_walk_forward_score
    ):
        required_next_steps.append("walk_forward_validation_required")
    if (
        evidence.monte_carlo_score is None
        or evidence.monte_carlo_score < settings.self_improvement_min_monte_carlo_score
    ):
        required_next_steps.append("monte_carlo_stress_test_required")
    if (
        evidence.shadow_score is None
        or evidence.shadow_score < settings.self_improvement_min_shadow_score
    ):
        required_next_steps.append("shadow_deployment_required")
    if not evidence.monitoring_approved:
        required_next_steps.append("monitoring_approval_required")

    max_capital_fraction = min(
        proposal.requested_capital_fraction,
        settings.staged_rollout_initial_capital_fraction,
        settings.staged_rollout_max_initial_capital_fraction,
    )

    if blockers:
        return GovernanceDecision(
            approved=False,
            mode="DENIED",
            max_capital_fraction=0.0,
            blockers=blockers,
            required_next_steps=required_next_steps,
        )
    if required_next_steps:
        return GovernanceDecision(
            approved=False,
            mode="SANDBOX_OR_SHADOW_ONLY",
            max_capital_fraction=0.0,
            blockers=[],
            required_next_steps=required_next_steps,
        )
    return GovernanceDecision(
        approved=True,
        mode="STAGED_ROLLOUT",
        max_capital_fraction=round(max_capital_fraction, 4),
        blockers=[],
        required_next_steps=["start_at_1_percent_then_5_10_25"],
    )


def assess_multi_timeframe_regime(
    *,
    settings: Settings,
    timeframe_signals: list[TimeframeRegimeSignal],
) -> MultiTimeframeAssessment:
    """Detect regime consensus across multiple timeframes."""
    if not timeframe_signals:
        neutral = TimeframeRegimeSignal(timeframe="default", diagnostics=MarketRegimeInput())
        timeframe_signals = [neutral]

    regime_weights: dict[str, float] = {}
    timeframe_regimes: dict[str, str] = {}
    total_weight = 0.0
    for signal in timeframe_signals:
        assessment = detect_market_regime(settings=settings, diagnostics=signal.diagnostics)
        weight = max(signal.weight, 0.0)
        timeframe_regimes[signal.timeframe] = assessment.primary_regime
        regime_weights[assessment.primary_regime] = (
            regime_weights.get(assessment.primary_regime, 0.0) + weight
        )
        total_weight += weight

    if total_weight <= 0.0:
        total_weight = float(len(timeframe_signals))
        regime_weights = {}
        for regime in timeframe_regimes.values():
            regime_weights[regime] = regime_weights.get(regime, 0.0) + 1.0

    dominant_regime, dominant_weight = max(
        regime_weights.items(),
        key=lambda item: item[1],
    )
    alignment_score = round(dominant_weight / total_weight, 4)
    conflicts = [
        f"{timeframe}:{regime}"
        for timeframe, regime in timeframe_regimes.items()
        if regime != dominant_regime
    ]
    intelligence = _build_timeframe_intelligence(
        timeframe_signals=timeframe_signals,
        timeframe_regimes=timeframe_regimes,
        dominant_regime=dominant_regime,
        alignment_score=alignment_score,
    )

    return MultiTimeframeAssessment(
        dominant_regime=dominant_regime,
        alignment_score=alignment_score,
        timeframe_regimes=timeframe_regimes,
        conflicts=conflicts,
        unified_market_state=intelligence["unified_market_state"],
        micro_trend=intelligence["micro_trend"],
        macro_trend=intelligence["macro_trend"],
        short_term_panic=bool(intelligence["short_term_panic"]),
        long_term_direction=intelligence["long_term_direction"],
        market_noise=float(intelligence["market_noise"]),
        structural_movement=bool(intelligence["structural_movement"]),
        state_vector=list(intelligence["state_vector"]),
    )


def aggregate_timeframe_diagnostics(
    timeframe_signals: list[TimeframeRegimeSignal],
) -> MarketRegimeInput:
    """Build one weighted feature snapshot from multiple timeframes."""
    if not timeframe_signals:
        return MarketRegimeInput()

    fields = MarketRegimeInput.model_fields.keys()
    values: dict[str, float] = {}
    for field in fields:
        total = 0.0
        weight_total = 0.0
        for signal in timeframe_signals:
            raw_value = getattr(signal.diagnostics, field)
            if raw_value is None:
                continue
            weight = max(signal.weight, 0.0)
            total += float(raw_value) * weight
            weight_total += weight
        if weight_total > 0.0:
            values[field] = round(total / weight_total, 6)

    return MarketRegimeInput(**values)


def score_policy_confidence(
    *,
    settings: Settings,
    timeframe_assessment: MultiTimeframeAssessment,
    strategy_policy: StrategyPolicy,
    diagnostics: MarketRegimeInput,
    strategy_memory: list[StrategyMemorySnapshot],
    module_confidences: list[ModuleConfidenceSignal] | None = None,
) -> ConfidenceScore:
    """Score whether the current adaptive policy is reliable enough to deploy."""
    liquidity = diagnostics.liquidity_score if diagnostics.liquidity_score is not None else 0.50
    regime_confidence = strategy_policy.assessment.confidence
    memory_quality = _memory_quality(settings=settings, strategy_memory=strategy_memory)
    risk_posture = 1.0 - strategy_policy.cash_weight
    module_confidence, module_uncertainty, module_scores = aggregate_module_confidences(
        module_confidences or [],
        fallback_confidence=regime_confidence,
    )
    volatility = max(
        diagnostics.atr_percentile or 0.0,
        diagnostics.realized_volatility_percentile or 0.0,
    )
    volatility_stability = 1.0 - volatility

    components = {
        "regime_confidence": regime_confidence,
        "timeframe_alignment": timeframe_assessment.alignment_score,
        "liquidity": liquidity,
        "memory_quality": memory_quality,
        "risk_posture": risk_posture,
        "module_confidence": module_confidence,
        "module_certainty": 1.0 - module_uncertainty,
        "volatility_stability": volatility_stability,
    }
    score = (
        components["module_confidence"] * 0.25
        + components["regime_confidence"] * 0.20
        + components["timeframe_alignment"] * 0.20
        + components["liquidity"] * 0.10
        + components["memory_quality"] * 0.10
        + components["module_certainty"] * 0.10
        + components["volatility_stability"] * 0.05
    )
    uncertainty = _clamp(
        module_uncertainty * 0.50
        + (1.0 - timeframe_assessment.alignment_score) * 0.30
        + volatility * 0.20
    )
    action, position_size_multiplier = _confidence_action(
        settings=settings,
        final_confidence=score,
        uncertainty=uncertainty,
        base_position_multiplier=strategy_policy.max_position_size_multiplier,
    )
    blockers: list[str] = []
    if timeframe_assessment.alignment_score < settings.confidence_min_timeframe_alignment:
        blockers.append(
            "timeframe_alignment_too_low:"
            f"{timeframe_assessment.alignment_score:.4f}<"
            f"{settings.confidence_min_timeframe_alignment:.4f}"
        )
    if strategy_policy.assessment.kill_switch:
        blockers.append("regime_kill_switch_active")
    if uncertainty > settings.confidence_max_uncertainty_for_live:
        blockers.append(
            "uncertainty_too_high:"
            f"{uncertainty:.4f}>{settings.confidence_max_uncertainty_for_live:.4f}"
        )
    if score < settings.confidence_min_shadow_score:
        blockers.append(
            f"confidence_below_shadow:{score:.4f}<{settings.confidence_min_shadow_score:.4f}"
        )

    return ConfidenceScore(
        score=round(score, 4),
        uncertainty=round(uncertainty, 4),
        action=action,
        position_size_multiplier=round(position_size_multiplier, 4),
        module_scores=module_scores,
        components={key: round(value, 4) for key, value in components.items()},
        blockers=blockers,
    )


def aggregate_module_confidences(
    module_confidences: list[ModuleConfidenceSignal],
    *,
    fallback_confidence: float,
) -> tuple[float, float, dict[str, float]]:
    """Aggregate module signal confidence and uncertainty."""
    if not module_confidences:
        return _clamp(fallback_confidence), 0.50, {}

    weighted_confidence = 0.0
    weighted_uncertainty = 0.0
    total_weight = 0.0
    module_scores: dict[str, float] = {}
    for item in module_confidences:
        weight = max(item.weight, 0.0)
        weighted_confidence += item.confidence * weight
        weighted_uncertainty += item.uncertainty * weight
        total_weight += weight
        module_scores[item.module] = round(item.confidence, 4)

    if total_weight <= 0.0:
        return _clamp(fallback_confidence), 0.50, module_scores
    return (
        round(_clamp(weighted_confidence / total_weight), 4),
        round(_clamp(weighted_uncertainty / total_weight), 4),
        module_scores,
    )


def apply_meta_learning_memory(
    *,
    settings: Settings,
    allocations: list[StrategyAllocation],
    base_cash_weight: float,
    confidence: ConfidenceScore,
    strategy_memory: list[StrategyMemorySnapshot],
    meta_learning: MetaLearningReport | None = None,
) -> tuple[list[StrategyAllocation], float, list[str]]:
    """Adjust strategy weights using recent memory without inventing new trades."""
    if not settings.meta_learning_enabled:
        return allocations, base_cash_weight, ["meta_learning_disabled"]

    memory_by_strategy = {item.strategy: item for item in strategy_memory}
    decay_by_strategy = {
        decay.entity_id: decay
        for decay in (meta_learning.performance_decays if meta_learning is not None else [])
        if decay.entity_type == "strategy"
    }
    adjusted_raw: dict[str, float] = {}
    notes: list[str] = []
    freed_weight = 0.0

    for allocation in allocations:
        memory = memory_by_strategy.get(allocation.strategy)
        multiplier = _strategy_memory_multiplier(settings=settings, memory=memory)
        decay = decay_by_strategy.get(allocation.strategy)
        if decay is not None:
            multiplier = min(multiplier, decay.weight_multiplier)
        adjusted_weight = allocation.weight * multiplier
        adjusted_raw[allocation.strategy] = adjusted_weight
        freed_weight += max(allocation.weight - adjusted_weight, 0.0)
        if memory is None:
            notes.append(f"{allocation.strategy}:no_memory_neutralized")
        elif memory.observations < settings.ai_memory_min_observations:
            notes.append(f"{allocation.strategy}:insufficient_memory")
        elif multiplier < 1.0:
            notes.append(f"{allocation.strategy}:deweighted_by_memory")
        elif multiplier > 1.0:
            notes.append(f"{allocation.strategy}:boosted_by_memory")
        if decay is not None:
            notes.append(f"{allocation.strategy}:reduced_by_performance_decay")

    if confidence.score < settings.confidence_min_live_score:
        confidence_penalty = min(0.25, settings.confidence_min_live_score - confidence.score)
        for strategy in list(adjusted_raw):
            reduction = adjusted_raw[strategy] * confidence_penalty
            adjusted_raw[strategy] -= reduction
            freed_weight += reduction
        notes.append("allocations_deweighted_by_confidence")

    if meta_learning is not None and meta_learning.global_weight_multiplier < 1.0:
        for strategy in list(adjusted_raw):
            reduction = adjusted_raw[strategy] * (1.0 - meta_learning.global_weight_multiplier)
            adjusted_raw[strategy] -= reduction
            freed_weight += reduction
        notes.append("allocations_deweighted_by_meta_learning")

    cash_weight = _clamp(base_cash_weight + freed_weight)
    normalized, cash_weight = _normalize_strategy_weights(
        strategy_weights=adjusted_raw,
        cash_weight=cash_weight,
    )
    adjusted_allocations = [
        StrategyAllocation(
            strategy=allocation.strategy,
            enabled=normalized[allocation.strategy] > 0.0 and allocation.enabled,
            weight=round(normalized[allocation.strategy], 4),
            reason=allocation.reason,
        )
        for allocation in allocations
    ]
    return adjusted_allocations, round(cash_weight, 4), notes


def decide_deployment_mode(
    *,
    settings: Settings,
    confidence: ConfidenceScore,
    strategy_policy: StrategyPolicy,
    shadow_observation_count: int,
    live_trading_requested: bool,
) -> DeploymentDecision:
    """Gate live deployment behind confidence, shadow data, and kill switches."""
    reasons = list(confidence.blockers)
    if strategy_policy.assessment.kill_switch:
        return DeploymentDecision(
            mode="BLOCKED",
            allow_live=False,
            reasons=reasons or ["regime_kill_switch_active"],
        )
    if confidence.score < settings.confidence_min_shadow_score:
        return DeploymentDecision(
            mode="BLOCKED",
            allow_live=False,
            reasons=reasons,
        )
    if shadow_observation_count < settings.shadow_testing_min_observations:
        reasons.append(
            "shadow_observations_too_low:"
            f"{shadow_observation_count}<{settings.shadow_testing_min_observations}"
        )
        return DeploymentDecision(mode="SHADOW", allow_live=False, reasons=reasons)
    if not live_trading_requested:
        return DeploymentDecision(
            mode="SHADOW",
            allow_live=False,
            reasons=reasons + ["live_trading_not_requested"],
        )
    if confidence.score < settings.confidence_min_live_score:
        reasons.append(
            f"confidence_below_live:{confidence.score:.4f}<"
            f"{settings.confidence_min_live_score:.4f}"
        )
        return DeploymentDecision(mode="SHADOW", allow_live=False, reasons=reasons)
    return DeploymentDecision(mode="LIVE_READY", allow_live=True, reasons=reasons)


def detect_market_regime(
    *,
    settings: Settings,
    diagnostics: MarketRegimeInput,
) -> MarketRegimeAssessment:
    """Convert normalized diagnostics into an explainable market regime."""
    scorecard = _build_scorecard(settings=settings, diagnostics=diagnostics)
    primary_regime = _select_primary_regime(settings=settings, scorecard=scorecard)
    secondary_regimes = [
        regime
        for regime, score in sorted(scorecard.items(), key=lambda item: item[1], reverse=True)
        if regime != primary_regime and score >= 0.55
    ][:3]
    confidence = round(scorecard[primary_regime], 4)
    kill_switch = (
        settings.adaptive_disable_on_panic
        and scorecard[REGIME_PANIC] >= settings.market_regime_panic_threshold
    )
    risk_size_multiplier = _risk_size_multiplier(
        settings=settings,
        primary_regime=primary_regime,
        kill_switch=kill_switch,
    )

    return MarketRegimeAssessment(
        primary_regime=primary_regime,
        secondary_regimes=secondary_regimes,
        confidence=confidence,
        risk_size_multiplier=risk_size_multiplier,
        kill_switch=kill_switch,
        reasons=_build_regime_reasons(
            settings=settings,
            diagnostics=diagnostics,
            scorecard=scorecard,
            primary_regime=primary_regime,
            kill_switch=kill_switch,
        ),
        scorecard={key: round(value, 4) for key, value in scorecard.items()},
    )


def build_strategy_policy(
    *,
    settings: Settings,
    diagnostics: MarketRegimeInput,
) -> StrategyPolicy:
    """Return strategy weights and risk posture for one market snapshot."""
    assessment = detect_market_regime(settings=settings, diagnostics=diagnostics)
    raw_weights = _base_strategy_weights(assessment.primary_regime)
    notes: list[str] = []

    if _strong_trend_detected(settings=settings, diagnostics=diagnostics, assessment=assessment):
        if raw_weights.get(STRATEGY_GRID, 0.0) > 0:
            notes.append("grid_disabled_by_trend_kill_switch")
        raw_weights[STRATEGY_GRID] = 0.0

    if assessment.kill_switch:
        raw_weights = {strategy: 0.0 for strategy in _all_strategies()}
        cash_weight = settings.strategy_orchestrator_panic_cash_allocation
        notes.append("panic_kill_switch_active")
    else:
        cash_weight = raw_weights.pop("cash", settings.strategy_orchestrator_min_cash_allocation)
        cash_weight = max(cash_weight, settings.strategy_orchestrator_min_cash_allocation)

    normalized_weights, cash_weight = _normalize_strategy_weights(
        strategy_weights=raw_weights,
        cash_weight=cash_weight,
    )

    allocations = [
        StrategyAllocation(
            strategy=strategy,
            enabled=weight > 0.0 and not assessment.kill_switch,
            weight=round(weight, 4),
            reason=_strategy_reason(strategy=strategy, assessment=assessment, weight=weight),
        )
        for strategy, weight in normalized_weights.items()
    ]

    return StrategyPolicy(
        assessment=assessment,
        allocations=allocations,
        cash_weight=round(cash_weight, 4),
        max_position_size_multiplier=assessment.risk_size_multiplier,
        notes=notes,
    )


def _build_scorecard(
    *,
    settings: Settings,
    diagnostics: MarketRegimeInput,
) -> dict[str, float]:
    adx_score = _ratio(diagnostics.adx, settings.market_regime_adx_trend_threshold)
    high_volatility = max(
        diagnostics.atr_percentile or 0.0,
        diagnostics.realized_volatility_percentile or 0.0,
    )
    low_volatility = 1.0 - high_volatility
    low_liquidity = max(
        1.0 - (diagnostics.liquidity_score if diagnostics.liquidity_score is not None else 1.0),
        _spread_pressure(diagnostics.spread_bps),
    )
    volume_pressure = _zscore_pressure(diagnostics.volume_zscore)
    trend_persistence = _trend_persistence(diagnostics.hurst_exponent)
    news_pressure = max(
        diagnostics.news_intensity_score or 0.0,
        diagnostics.sentiment_hype_score or 0.0,
    )

    trend = _clamp(max(adx_score, trend_persistence) * 0.7 + volume_pressure * 0.3)
    breakout = _clamp(
        (diagnostics.breakout_score or 0.0) * 0.60
        + adx_score * 0.25
        + volume_pressure * 0.15
    )
    range_score = _clamp(
        low_volatility * 0.55
        + (1.0 - adx_score) * 0.30
        + (diagnostics.mean_reversion_score or 0.0) * 0.15
    )
    mean_reversion = _clamp(
        (diagnostics.mean_reversion_score or 0.0) * 0.60
        + range_score * 0.40
    )
    panic = _clamp(
        (diagnostics.sentiment_panic_score or 0.0) * 0.55
        + high_volatility * 0.30
        + low_liquidity * 0.15
    )
    news_driven = _clamp(
        news_pressure * 0.70
        + (diagnostics.correlation_shift_score or 0.0) * 0.20
        + volume_pressure * 0.10
    )

    return {
        REGIME_TREND: trend,
        REGIME_RANGE: range_score,
        REGIME_PANIC: panic,
        REGIME_HIGH_VOLATILITY: high_volatility,
        REGIME_LOW_LIQUIDITY: low_liquidity,
        REGIME_NEWS_DRIVEN: news_driven,
        REGIME_MEAN_REVERSION: mean_reversion,
        REGIME_BREAKOUT: breakout,
    }


def _select_primary_regime(
    *,
    settings: Settings,
    scorecard: dict[str, float],
) -> str:
    if scorecard[REGIME_PANIC] >= settings.market_regime_panic_threshold:
        return REGIME_PANIC
    if scorecard[REGIME_LOW_LIQUIDITY] >= settings.market_regime_low_liquidity_threshold:
        return REGIME_LOW_LIQUIDITY
    if scorecard[REGIME_HIGH_VOLATILITY] >= settings.market_regime_high_volatility_threshold:
        return REGIME_HIGH_VOLATILITY
    if scorecard[REGIME_NEWS_DRIVEN] >= settings.market_regime_news_driven_threshold:
        return REGIME_NEWS_DRIVEN
    if scorecard[REGIME_BREAKOUT] >= settings.market_regime_breakout_threshold:
        return REGIME_BREAKOUT
    if scorecard[REGIME_TREND] >= 0.65:
        return REGIME_TREND
    if (
        scorecard[REGIME_RANGE] >= 0.65
        or scorecard[REGIME_HIGH_VOLATILITY] <= settings.market_regime_low_volatility_threshold
    ):
        return REGIME_RANGE
    if scorecard[REGIME_MEAN_REVERSION] >= 0.60:
        return REGIME_MEAN_REVERSION
    return REGIME_RANGE


def _risk_size_multiplier(
    *,
    settings: Settings,
    primary_regime: str,
    kill_switch: bool,
) -> float:
    if kill_switch:
        return settings.risk_regime_panic_size_multiplier
    if primary_regime == REGIME_PANIC:
        return settings.risk_regime_panic_size_multiplier
    if primary_regime == REGIME_LOW_LIQUIDITY:
        return settings.risk_regime_low_liquidity_size_multiplier
    if primary_regime == REGIME_HIGH_VOLATILITY:
        return settings.risk_regime_high_vol_size_multiplier
    if primary_regime in {REGIME_RANGE, REGIME_MEAN_REVERSION}:
        return settings.risk_regime_range_size_multiplier
    return settings.risk_regime_normal_size_multiplier


def _base_strategy_weights(primary_regime: str) -> dict[str, float]:
    if primary_regime == REGIME_TREND:
        return {
            STRATEGY_TREND: 0.60,
            STRATEGY_MEAN_REVERSION: 0.05,
            STRATEGY_STAT_ARB: 0.10,
            STRATEGY_GRID: 0.00,
            STRATEGY_MARKET_MAKING: 0.05,
            STRATEGY_SENTIMENT: 0.05,
            "cash": 0.15,
        }
    if primary_regime == REGIME_BREAKOUT:
        return {
            STRATEGY_TREND: 0.50,
            STRATEGY_MEAN_REVERSION: 0.00,
            STRATEGY_STAT_ARB: 0.10,
            STRATEGY_GRID: 0.00,
            STRATEGY_MARKET_MAKING: 0.05,
            STRATEGY_SENTIMENT: 0.15,
            "cash": 0.20,
        }
    if primary_regime == REGIME_RANGE:
        return {
            STRATEGY_TREND: 0.05,
            STRATEGY_MEAN_REVERSION: 0.35,
            STRATEGY_STAT_ARB: 0.10,
            STRATEGY_GRID: 0.25,
            STRATEGY_MARKET_MAKING: 0.15,
            STRATEGY_SENTIMENT: 0.00,
            "cash": 0.10,
        }
    if primary_regime == REGIME_MEAN_REVERSION:
        return {
            STRATEGY_TREND: 0.00,
            STRATEGY_MEAN_REVERSION: 0.45,
            STRATEGY_STAT_ARB: 0.15,
            STRATEGY_GRID: 0.15,
            STRATEGY_MARKET_MAKING: 0.05,
            STRATEGY_SENTIMENT: 0.00,
            "cash": 0.20,
        }
    if primary_regime == REGIME_NEWS_DRIVEN:
        return {
            STRATEGY_TREND: 0.20,
            STRATEGY_MEAN_REVERSION: 0.05,
            STRATEGY_STAT_ARB: 0.05,
            STRATEGY_GRID: 0.00,
            STRATEGY_MARKET_MAKING: 0.00,
            STRATEGY_SENTIMENT: 0.45,
            "cash": 0.25,
        }
    if primary_regime == REGIME_HIGH_VOLATILITY:
        return {
            STRATEGY_TREND: 0.20,
            STRATEGY_MEAN_REVERSION: 0.00,
            STRATEGY_STAT_ARB: 0.10,
            STRATEGY_GRID: 0.00,
            STRATEGY_MARKET_MAKING: 0.00,
            STRATEGY_SENTIMENT: 0.15,
            "cash": 0.55,
        }
    if primary_regime == REGIME_LOW_LIQUIDITY:
        return {
            STRATEGY_TREND: 0.00,
            STRATEGY_MEAN_REVERSION: 0.00,
            STRATEGY_STAT_ARB: 0.00,
            STRATEGY_GRID: 0.00,
            STRATEGY_MARKET_MAKING: 0.00,
            STRATEGY_SENTIMENT: 0.20,
            "cash": 0.80,
        }
    return {strategy: 0.0 for strategy in [*_all_strategies(), "cash"]}


def _normalize_strategy_weights(
    *,
    strategy_weights: dict[str, float],
    cash_weight: float,
) -> tuple[dict[str, float], float]:
    cash_weight = _clamp(cash_weight)
    strategy_total = sum(max(value, 0.0) for value in strategy_weights.values())
    remaining = max(1.0 - cash_weight, 0.0)
    if strategy_total <= 0.0:
        return {strategy: 0.0 for strategy in _all_strategies()}, 1.0

    normalized = {
        strategy: max(weight, 0.0) / strategy_total * remaining
        for strategy, weight in strategy_weights.items()
        if strategy in _all_strategies()
    }
    for strategy in _all_strategies():
        normalized.setdefault(strategy, 0.0)
    return normalized, cash_weight


def _build_regime_reasons(
    *,
    settings: Settings,
    diagnostics: MarketRegimeInput,
    scorecard: dict[str, float],
    primary_regime: str,
    kill_switch: bool,
) -> list[str]:
    reasons = [f"primary_regime={primary_regime}:{scorecard[primary_regime]:.4f}"]
    if diagnostics.adx is not None:
        reasons.append(
            f"adx={diagnostics.adx:.2f},trend_threshold={settings.market_regime_adx_trend_threshold:.2f}"
        )
    if diagnostics.realized_volatility_percentile is not None:
        reasons.append(f"realized_volatility_percentile={diagnostics.realized_volatility_percentile:.4f}")
    if diagnostics.atr_percentile is not None:
        reasons.append(f"atr_percentile={diagnostics.atr_percentile:.4f}")
    if diagnostics.liquidity_score is not None:
        reasons.append(f"liquidity_score={diagnostics.liquidity_score:.4f}")
    if diagnostics.sentiment_panic_score is not None:
        reasons.append(f"sentiment_panic_score={diagnostics.sentiment_panic_score:.4f}")
    if kill_switch:
        reasons.append("kill_switch=panic_threshold_reached")
    return reasons


def _build_timeframe_intelligence(
    *,
    timeframe_signals: list[TimeframeRegimeSignal],
    timeframe_regimes: dict[str, str],
    dominant_regime: str,
    alignment_score: float,
) -> dict[str, object]:
    short_regimes = _regimes_for_timeframes(
        timeframe_regimes=timeframe_regimes,
        names={"1m", "5m", "15m"},
    )
    long_regimes = _regimes_for_timeframes(
        timeframe_regimes=timeframe_regimes,
        names={"1h", "4h", "1d", "daily", "1w", "weekly"},
    )
    macro_regimes = _regimes_for_timeframes(
        timeframe_regimes=timeframe_regimes,
        names={"1d", "daily", "1w", "weekly"},
    )
    micro_trend = _trend_label(short_regimes)
    macro_trend = _trend_label(macro_regimes or long_regimes)
    short_term_panic = REGIME_PANIC in short_regimes
    long_term_direction = _direction_label(macro_regimes or long_regimes)
    market_noise = _market_noise(timeframe_signals=timeframe_signals, alignment_score=alignment_score)
    structural_movement = any(
        regime in {REGIME_TREND, REGIME_BREAKOUT, REGIME_NEWS_DRIVEN}
        for regime in macro_regimes
    )
    if short_term_panic and macro_trend in {"BULLISH", "BREAKOUT"}:
        unified_market_state = "SHORT_TERM_PANIC_WITH_BULLISH_MACRO"
    elif dominant_regime == REGIME_RANGE and market_noise > 0.55:
        unified_market_state = "NOISY_RANGE"
    elif structural_movement:
        unified_market_state = f"STRUCTURAL_{dominant_regime}"
    else:
        unified_market_state = dominant_regime

    return {
        "unified_market_state": unified_market_state,
        "micro_trend": micro_trend,
        "macro_trend": macro_trend,
        "short_term_panic": short_term_panic,
        "long_term_direction": long_term_direction,
        "market_noise": round(market_noise, 4),
        "structural_movement": structural_movement,
        "state_vector": _market_state_vector(timeframe_signals=timeframe_signals),
    }


def _regimes_for_timeframes(
    *,
    timeframe_regimes: dict[str, str],
    names: set[str],
) -> list[str]:
    normalized_names = {item.lower() for item in names}
    return [
        regime
        for timeframe, regime in timeframe_regimes.items()
        if timeframe.lower() in normalized_names
    ]


def _trend_label(regimes: list[str]) -> str:
    if not regimes:
        return "UNKNOWN"
    if REGIME_PANIC in regimes:
        return "PANIC"
    if REGIME_BREAKOUT in regimes:
        return "BREAKOUT"
    if REGIME_TREND in regimes:
        return "BULLISH"
    if REGIME_RANGE in regimes or REGIME_MEAN_REVERSION in regimes:
        return "SIDEWAYS"
    return "RISK_OFF"


def _direction_label(regimes: list[str]) -> str:
    if not regimes:
        return "UNKNOWN"
    if REGIME_BREAKOUT in regimes:
        return "MACRO_BREAKOUT"
    if REGIME_TREND in regimes:
        return "MACRO_TREND"
    if REGIME_RANGE in regimes:
        return "MACRO_RANGE"
    if REGIME_PANIC in regimes or REGIME_HIGH_VOLATILITY in regimes:
        return "MACRO_STRESS"
    return "MIXED"


def _market_noise(
    *,
    timeframe_signals: list[TimeframeRegimeSignal],
    alignment_score: float,
) -> float:
    if not timeframe_signals:
        return 0.0
    volatility_values = [
        max(
            signal.diagnostics.atr_percentile or 0.0,
            signal.diagnostics.realized_volatility_percentile or 0.0,
        )
        for signal in timeframe_signals
    ]
    avg_volatility = sum(volatility_values) / len(volatility_values)
    return _clamp(avg_volatility * 0.60 + (1.0 - alignment_score) * 0.40)


def _market_state_vector(
    *,
    timeframe_signals: list[TimeframeRegimeSignal],
) -> list[float]:
    diagnostics = aggregate_timeframe_diagnostics(timeframe_signals)
    return [
        round((diagnostics.adx or 0.0) / 100.0, 4),
        round(diagnostics.atr_percentile or 0.0, 4),
        round(diagnostics.realized_volatility_percentile or 0.0, 4),
        round(diagnostics.liquidity_score if diagnostics.liquidity_score is not None else 0.5, 4),
        round(_spread_pressure(diagnostics.spread_bps), 4),
        round(_zscore_pressure(diagnostics.volume_zscore), 4),
        round(diagnostics.correlation_shift_score or 0.0, 4),
        round(diagnostics.sentiment_panic_score or 0.0, 4),
        round(diagnostics.news_intensity_score or 0.0, 4),
        round(diagnostics.breakout_score or 0.0, 4),
        round(diagnostics.mean_reversion_score or 0.0, 4),
        round(diagnostics.hurst_exponent or 0.0, 4),
    ]


def _confidence_action(
    *,
    settings: Settings,
    final_confidence: float,
    uncertainty: float,
    base_position_multiplier: float,
) -> tuple[str, float]:
    if final_confidence >= settings.confidence_full_position_threshold and uncertainty <= 0.30:
        return "FULL_POSITION", base_position_multiplier
    if final_confidence >= settings.confidence_reduced_position_threshold:
        uncertainty_penalty = max(0.25, 1.0 - uncertainty)
        return "REDUCED_POSITION", base_position_multiplier * 0.50 * uncertainty_penalty
    return "AVOID_TRADE", 0.0


def _analyze_performance_decay(
    *,
    settings: Settings,
    snapshot: ModelPerformanceSnapshot,
) -> PerformanceDecaySignal:
    reasons: list[str] = []
    components: list[float] = []

    win_rate_drop = _drop(snapshot.baseline_win_rate, snapshot.current_win_rate)
    if win_rate_drop >= settings.meta_learning_win_rate_drop_threshold:
        components.append(_clamp(win_rate_drop / 0.40))
        reasons.append(
            "win_rate_drop:"
            f"{win_rate_drop:.4f}>={settings.meta_learning_win_rate_drop_threshold:.4f}"
        )

    sharpe_drop = _drop(snapshot.baseline_sharpe, snapshot.current_sharpe)
    if sharpe_drop >= settings.meta_learning_sharpe_drop_threshold:
        components.append(_clamp(sharpe_drop / 2.0))
        reasons.append(
            "sharpe_drop:"
            f"{sharpe_drop:.4f}>={settings.meta_learning_sharpe_drop_threshold:.4f}"
        )

    drawdown_increase = _increase(snapshot.baseline_drawdown, snapshot.current_drawdown)
    if drawdown_increase >= settings.meta_learning_drawdown_increase_threshold:
        components.append(_clamp(drawdown_increase / 0.35))
        reasons.append(
            "drawdown_increase:"
            f"{drawdown_increase:.4f}>="
            f"{settings.meta_learning_drawdown_increase_threshold:.4f}"
        )

    volatility_increase = _relative_increase(
        snapshot.baseline_volatility,
        snapshot.current_volatility,
    )
    if volatility_increase >= settings.meta_learning_volatility_increase_threshold:
        components.append(_clamp(volatility_increase / 1.0))
        reasons.append(
            "volatility_increase:"
            f"{volatility_increase:.4f}>="
            f"{settings.meta_learning_volatility_increase_threshold:.4f}"
        )

    accuracy_drop = _drop(
        snapshot.baseline_prediction_accuracy,
        snapshot.current_prediction_accuracy,
    )
    if accuracy_drop >= settings.meta_learning_accuracy_drop_threshold:
        components.append(_clamp(accuracy_drop / 0.30))
        reasons.append(
            "prediction_accuracy_drop:"
            f"{accuracy_drop:.4f}>={settings.meta_learning_accuracy_drop_threshold:.4f}"
        )

    sample_score = _clamp(snapshot.observations / max(settings.ai_memory_min_observations, 1))
    decay_score = max(components, default=0.0) * sample_score
    if decay_score >= settings.meta_learning_decay_disable_threshold:
        severity = "CRITICAL"
        weight_multiplier = 0.0
    elif decay_score >= settings.meta_learning_decay_reduce_threshold:
        severity = "WARNING"
        weight_multiplier = max(0.25, 1.0 - decay_score)
    elif decay_score > 0.0:
        severity = "INFO"
        weight_multiplier = 0.85
    else:
        severity = "OK"
        weight_multiplier = 1.0

    return PerformanceDecaySignal(
        entity_id=snapshot.entity_id,
        entity_type=snapshot.entity_type,
        decay_score=round(decay_score, 4),
        severity=severity,
        weight_multiplier=round(weight_multiplier, 4),
        reasons=reasons,
    )


def _append_previous_diagnostic_shifts(
    *,
    settings: Settings,
    current: MarketRegimeInput,
    previous: MarketRegimeInput,
    shift_components: list[float],
    shift_types: list[str],
    reasons: list[str],
) -> None:
    if current.liquidity_score is not None and previous.liquidity_score is not None:
        liquidity_drop = previous.liquidity_score - current.liquidity_score
        if liquidity_drop >= settings.market_structure_liquidity_drop_threshold:
            shift_components.append(_clamp(liquidity_drop))
            shift_types.append("liquidity_drop")
            reasons.append(f"liquidity_drop:{liquidity_drop:.4f}")

    if current.spread_bps is not None and previous.spread_bps is not None:
        spread_increase = current.spread_bps - previous.spread_bps
        if spread_increase >= settings.market_structure_spread_widening_bps:
            shift_components.append(_clamp(spread_increase / 500.0))
            shift_types.append("spread_expansion")
            reasons.append(f"spread_expansion_bps:{spread_increase:.2f}")

    current_volatility = max(
        current.atr_percentile or 0.0,
        current.realized_volatility_percentile or 0.0,
    )
    previous_volatility = max(
        previous.atr_percentile or 0.0,
        previous.realized_volatility_percentile or 0.0,
    )
    volatility_delta = current_volatility - previous_volatility
    if volatility_delta >= settings.market_structure_volatility_shift_threshold:
        shift_components.append(_clamp(volatility_delta))
        shift_types.append("volatility_jump")
        reasons.append(f"volatility_jump:{volatility_delta:.4f}")


def _strategy_reason(
    *,
    strategy: str,
    assessment: MarketRegimeAssessment,
    weight: float,
) -> str:
    if assessment.kill_switch:
        return "disabled_by_kill_switch"
    if weight <= 0.0:
        return f"disabled_for_{assessment.primary_regime.lower()}"
    return f"allocated_for_{assessment.primary_regime.lower()}"


def _memory_quality(
    *,
    settings: Settings,
    strategy_memory: list[StrategyMemorySnapshot],
) -> float:
    if not strategy_memory:
        return 0.50

    quality_scores = [
        _strategy_memory_quality(settings=settings, memory=memory)
        for memory in strategy_memory
    ]
    return _clamp(sum(quality_scores) / len(quality_scores))


def _strategy_memory_multiplier(
    *,
    settings: Settings,
    memory: StrategyMemorySnapshot | None,
) -> float:
    if memory is None:
        return 0.85
    if memory.observations < settings.ai_memory_min_observations:
        return 0.80

    quality = _strategy_memory_quality(settings=settings, memory=memory)
    if quality < settings.ai_memory_min_strategy_confidence:
        return 0.50
    if quality >= 0.75:
        return 1.20
    if quality >= 0.60:
        return 1.05
    return 0.90


def _strategy_memory_quality(
    *,
    settings: Settings,
    memory: StrategyMemorySnapshot,
) -> float:
    if memory.observations <= 0:
        return 0.0

    win_rate = memory.win_rate if memory.win_rate is not None else 0.50
    confidence = memory.confidence if memory.confidence is not None else 0.50
    sharpe_score = _clamp(((memory.sharpe or 0.0) + 1.0) / 3.0)
    drawdown_score = 1.0 - _clamp((memory.max_drawdown or 0.0) / 0.35)
    sample_score = _clamp(memory.observations / max(settings.ai_memory_min_observations, 1))
    pnl_score = 0.55 if (memory.recent_pnl or 0.0) >= 0.0 else 0.35

    return _clamp(
        win_rate * 0.30
        + confidence * 0.25
        + sharpe_score * 0.20
        + drawdown_score * 0.10
        + sample_score * 0.10
        + pnl_score * 0.05
    )


def _strong_trend_detected(
    *,
    settings: Settings,
    diagnostics: MarketRegimeInput,
    assessment: MarketRegimeAssessment,
) -> bool:
    return (
        assessment.primary_regime in {REGIME_TREND, REGIME_BREAKOUT}
        or REGIME_TREND in assessment.secondary_regimes
        or (diagnostics.adx or 0.0) >= settings.market_regime_adx_trend_threshold
    )


def _all_strategies() -> list[str]:
    return [
        STRATEGY_TREND,
        STRATEGY_MEAN_REVERSION,
        STRATEGY_STAT_ARB,
        STRATEGY_GRID,
        STRATEGY_MARKET_MAKING,
        STRATEGY_SENTIMENT,
    ]


def _ratio(value: float | None, denominator: float) -> float:
    if value is None or denominator <= 0.0:
        return 0.0
    return _clamp(value / denominator)


def _spread_pressure(spread_bps: float | None) -> float:
    if spread_bps is None:
        return 0.0
    return _clamp(spread_bps / 500.0)


def _zscore_pressure(volume_zscore: float | None) -> float:
    if volume_zscore is None:
        return 0.0
    return _clamp(abs(volume_zscore) / 3.0)


def _trend_persistence(hurst_exponent: float | None) -> float:
    if hurst_exponent is None or hurst_exponent <= 0.50:
        return 0.0
    return _clamp((hurst_exponent - 0.50) / 0.25)


def _clamp(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _drop(baseline: float | None, current: float | None) -> float:
    if baseline is None or current is None:
        return 0.0
    return max(float(baseline) - float(current), 0.0)


def _increase(baseline: float | None, current: float | None) -> float:
    if baseline is None or current is None:
        return 0.0
    return max(float(current) - float(baseline), 0.0)


def _relative_increase(baseline: float | None, current: float | None) -> float:
    if baseline is None or current is None or baseline <= 0.0:
        return 0.0
    return max((float(current) - float(baseline)) / float(baseline), 0.0)


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return _clamp(dot / (left_norm * right_norm))
