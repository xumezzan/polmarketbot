from app.schemas.adaptive import (
    HistoricalRegimeMemory,
    MarketRegimeInput,
    ModelPerformanceSnapshot,
    ModuleConfidenceSignal,
    SelfImprovementEvidence,
    SelfImprovementProposal,
    StrategyMemorySnapshot,
    TimeframeRegimeSignal,
)
from app.services.adaptive_trading import (
    REGIME_LOW_LIQUIDITY,
    REGIME_PANIC,
    REGIME_RANGE,
    REGIME_TREND,
    STRATEGY_GRID,
    STRATEGY_MARKET_MAKING,
    STRATEGY_MEAN_REVERSION,
    STRATEGY_SENTIMENT,
    STRATEGY_TREND,
    analyze_meta_learning,
    assess_multi_timeframe_regime,
    build_adaptive_system_decision,
    build_strategy_policy,
    detect_market_structure_shift,
    detect_market_regime,
    evaluate_self_improvement_governance,
    score_policy_confidence,
    search_similar_regime_memories,
)
from tests.helpers import build_test_settings


def _weight(policy, strategy: str) -> float:
    for allocation in policy.allocations:
        if allocation.strategy == strategy:
            return allocation.weight
    raise AssertionError(f"Missing strategy allocation: {strategy}")


def _allocation_weight(allocations, strategy: str) -> float:
    for allocation in allocations:
        if allocation.strategy == strategy:
            return allocation.weight
    raise AssertionError(f"Missing strategy allocation: {strategy}")


def test_detect_market_regime_identifies_trend_market() -> None:
    settings = build_test_settings()

    assessment = detect_market_regime(
        settings=settings,
        diagnostics=MarketRegimeInput(
            adx=38,
            realized_volatility_percentile=0.45,
            liquidity_score=0.90,
            volume_zscore=1.7,
            hurst_exponent=0.62,
        ),
    )

    assert assessment.primary_regime == REGIME_TREND
    assert assessment.kill_switch is False
    assert assessment.risk_size_multiplier == 1.0
    assert assessment.scorecard[REGIME_TREND] >= 0.65


def test_strategy_policy_allocates_to_trend_and_keeps_cash_buffer() -> None:
    settings = build_test_settings(strategy_orchestrator_min_cash_allocation=0.20)

    policy = build_strategy_policy(
        settings=settings,
        diagnostics=MarketRegimeInput(
            adx=42,
            realized_volatility_percentile=0.40,
            liquidity_score=0.95,
            volume_zscore=2.1,
        ),
    )

    assert policy.assessment.primary_regime == REGIME_TREND
    assert policy.cash_weight == 0.20
    assert _weight(policy, STRATEGY_TREND) > 0.55
    assert _weight(policy, STRATEGY_GRID) == 0.0
    assert policy.max_position_size_multiplier == 1.0


def test_strategy_policy_turns_panic_into_full_cash_kill_switch() -> None:
    settings = build_test_settings()

    policy = build_strategy_policy(
        settings=settings,
        diagnostics=MarketRegimeInput(
            adx=18,
            atr_percentile=0.95,
            realized_volatility_percentile=0.92,
            liquidity_score=0.20,
            sentiment_panic_score=0.96,
            spread_bps=700,
        ),
    )

    assert policy.assessment.primary_regime == REGIME_PANIC
    assert policy.assessment.kill_switch is True
    assert policy.cash_weight == 1.0
    assert policy.max_position_size_multiplier == 0.0
    assert all(allocation.enabled is False for allocation in policy.allocations)


def test_strategy_policy_prefers_mean_reversion_and_grid_in_range_market() -> None:
    settings = build_test_settings(strategy_orchestrator_min_cash_allocation=0.20)

    policy = build_strategy_policy(
        settings=settings,
        diagnostics=MarketRegimeInput(
            adx=12,
            realized_volatility_percentile=0.16,
            liquidity_score=0.88,
            mean_reversion_score=0.68,
        ),
    )

    assert policy.assessment.primary_regime == REGIME_RANGE
    assert _weight(policy, STRATEGY_MEAN_REVERSION) > _weight(policy, STRATEGY_TREND)
    assert _weight(policy, STRATEGY_GRID) > 0.20
    assert policy.max_position_size_multiplier == 0.75


def test_low_liquidity_overrides_trend_and_reduces_size() -> None:
    settings = build_test_settings()

    policy = build_strategy_policy(
        settings=settings,
        diagnostics=MarketRegimeInput(
            adx=44,
            realized_volatility_percentile=0.40,
            liquidity_score=0.15,
            sentiment_hype_score=0.35,
        ),
    )

    assert policy.assessment.primary_regime == REGIME_LOW_LIQUIDITY
    assert policy.cash_weight == 0.80
    assert _weight(policy, STRATEGY_SENTIMENT) == 0.20
    assert _weight(policy, STRATEGY_TREND) == 0.0
    assert policy.max_position_size_multiplier == 0.25


def test_multi_timeframe_layer_reports_alignment_and_conflicts() -> None:
    settings = build_test_settings()

    assessment = assess_multi_timeframe_regime(
        settings=settings,
        timeframe_signals=[
            TimeframeRegimeSignal(
                timeframe="5m",
                weight=1.0,
                diagnostics=MarketRegimeInput(
                    adx=40,
                    realized_volatility_percentile=0.45,
                    liquidity_score=0.90,
                ),
            ),
            TimeframeRegimeSignal(
                timeframe="1h",
                weight=2.0,
                diagnostics=MarketRegimeInput(
                    adx=37,
                    realized_volatility_percentile=0.42,
                    liquidity_score=0.92,
                ),
            ),
            TimeframeRegimeSignal(
                timeframe="1d",
                weight=1.0,
                diagnostics=MarketRegimeInput(
                    adx=10,
                    realized_volatility_percentile=0.14,
                    liquidity_score=0.90,
                    mean_reversion_score=0.65,
                ),
            ),
        ],
    )

    assert assessment.dominant_regime == REGIME_TREND
    assert assessment.alignment_score == 0.75
    assert assessment.conflicts == ["1d:RANGE"]


def test_full_adaptive_decision_stays_in_shadow_until_shadow_sample_is_large() -> None:
    settings = build_test_settings(
        confidence_min_live_score=0.70,
        shadow_testing_min_observations=30,
    )

    decision = build_adaptive_system_decision(
        settings=settings,
        timeframe_signals=[
            TimeframeRegimeSignal(
                timeframe="15m",
                diagnostics=MarketRegimeInput(
                    adx=42,
                    realized_volatility_percentile=0.40,
                    liquidity_score=0.95,
                    volume_zscore=1.8,
                ),
            ),
            TimeframeRegimeSignal(
                timeframe="1h",
                diagnostics=MarketRegimeInput(
                    adx=39,
                    realized_volatility_percentile=0.38,
                    liquidity_score=0.95,
                    volume_zscore=1.6,
                ),
            ),
        ],
        strategy_memory=[
            StrategyMemorySnapshot(
                strategy=STRATEGY_TREND,
                observations=60,
                win_rate=0.62,
                sharpe=1.4,
                max_drawdown=0.08,
                confidence=0.78,
                recent_pnl=12.0,
            )
        ],
        shadow_observation_count=12,
        live_trading_requested=True,
    )

    assert decision.timeframe_assessment.dominant_regime == REGIME_TREND
    assert decision.confidence.score >= settings.confidence_min_live_score
    assert decision.deployment.mode == "SHADOW"
    assert decision.deployment.allow_live is False
    assert "shadow_observations_too_low:12<30" in decision.deployment.reasons


def test_memory_layer_deweights_underperforming_strategy_and_moves_weight_to_cash() -> None:
    settings = build_test_settings(
        ai_memory_min_observations=20,
        confidence_min_live_score=0.70,
    )

    decision = build_adaptive_system_decision(
        settings=settings,
        timeframe_signals=[
            TimeframeRegimeSignal(
                timeframe="1h",
                diagnostics=MarketRegimeInput(
                    adx=36,
                    realized_volatility_percentile=0.42,
                    liquidity_score=0.90,
                    volume_zscore=1.3,
                ),
            )
        ],
        strategy_memory=[
            StrategyMemorySnapshot(
                strategy=STRATEGY_TREND,
                observations=50,
                win_rate=0.28,
                sharpe=-0.6,
                max_drawdown=0.22,
                confidence=0.30,
                recent_pnl=-8.0,
            )
        ],
        shadow_observation_count=40,
        live_trading_requested=False,
    )

    base_trend_weight = _weight(decision.strategy_policy, STRATEGY_TREND)
    adjusted_trend_weight = _allocation_weight(decision.adjusted_allocations, STRATEGY_TREND)

    assert adjusted_trend_weight < base_trend_weight
    assert decision.cash_weight > decision.strategy_policy.cash_weight
    assert f"{STRATEGY_TREND}:deweighted_by_memory" in decision.memory_notes


def test_full_adaptive_decision_blocks_live_during_panic() -> None:
    settings = build_test_settings()

    decision = build_adaptive_system_decision(
        settings=settings,
        timeframe_signals=[
            TimeframeRegimeSignal(
                timeframe="5m",
                diagnostics=MarketRegimeInput(
                    atr_percentile=0.95,
                    realized_volatility_percentile=0.95,
                    liquidity_score=0.10,
                    sentiment_panic_score=0.98,
                    spread_bps=850,
                ),
            )
        ],
        shadow_observation_count=100,
        live_trading_requested=True,
    )

    assert decision.strategy_policy.assessment.primary_regime == REGIME_PANIC
    assert decision.deployment.mode == "BLOCKED"
    assert decision.deployment.allow_live is False
    assert decision.cash_weight == 1.0


def test_meta_learning_detects_strategy_performance_decay() -> None:
    settings = build_test_settings(
        meta_learning_win_rate_drop_threshold=0.12,
        meta_learning_sharpe_drop_threshold=0.40,
        meta_learning_drawdown_increase_threshold=0.08,
        meta_learning_accuracy_drop_threshold=0.08,
    )

    report = analyze_meta_learning(
        settings=settings,
        current_diagnostics=MarketRegimeInput(liquidity_score=0.90),
        performance_snapshots=[
            ModelPerformanceSnapshot(
                entity_id=STRATEGY_TREND,
                entity_type="strategy",
                observations=80,
                baseline_win_rate=0.62,
                current_win_rate=0.41,
                baseline_sharpe=1.35,
                current_sharpe=0.35,
                baseline_drawdown=0.06,
                current_drawdown=0.19,
                baseline_prediction_accuracy=0.66,
                current_prediction_accuracy=0.52,
            )
        ],
    )

    assert report.performance_decays[0].entity_id == STRATEGY_TREND
    assert report.performance_decays[0].severity in {"WARNING", "CRITICAL"}
    assert report.performance_decays[0].weight_multiplier < 1.0
    assert "performance_decay_detected" in report.notes


def test_market_structure_shift_detects_news_liquidity_spread_and_correlation() -> None:
    settings = build_test_settings(
        market_structure_shift_threshold=0.60,
        market_structure_liquidity_drop_threshold=0.30,
        market_structure_spread_widening_bps=250,
        market_structure_correlation_shift_threshold=0.65,
    )

    shift = detect_market_structure_shift(
        settings=settings,
        current_diagnostics=MarketRegimeInput(
            liquidity_score=0.20,
            spread_bps=320,
            correlation_shift_score=0.72,
            realized_volatility_percentile=0.76,
            news_intensity_score=0.82,
        ),
    )

    assert shift.detected is True
    assert shift.shift_score >= 0.72
    assert "liquidity_dried_up" in shift.shift_types
    assert "spread_widened" in shift.shift_types
    assert "correlation_breakdown" in shift.shift_types
    assert "news_driven_market" in shift.shift_types
    assert shift.risk_multiplier < 1.0


def test_full_adaptive_decision_applies_performance_decay_weight_reduction() -> None:
    settings = build_test_settings(
        ai_memory_min_observations=20,
        meta_learning_decay_reduce_threshold=0.25,
        meta_learning_decay_disable_threshold=0.90,
    )

    decision = build_adaptive_system_decision(
        settings=settings,
        timeframe_signals=[
            TimeframeRegimeSignal(
                timeframe="1h",
                diagnostics=MarketRegimeInput(
                    adx=40,
                    realized_volatility_percentile=0.42,
                    liquidity_score=0.88,
                    volume_zscore=1.5,
                ),
            )
        ],
        strategy_memory=[
            StrategyMemorySnapshot(
                strategy=STRATEGY_TREND,
                observations=80,
                win_rate=0.58,
                sharpe=1.1,
                max_drawdown=0.08,
                confidence=0.70,
                recent_pnl=6.0,
            )
        ],
        performance_snapshots=[
            ModelPerformanceSnapshot(
                entity_id=STRATEGY_TREND,
                entity_type="strategy",
                observations=80,
                baseline_win_rate=0.65,
                current_win_rate=0.42,
                baseline_sharpe=1.4,
                current_sharpe=0.2,
                baseline_drawdown=0.05,
                current_drawdown=0.18,
            )
        ],
        shadow_observation_count=50,
        live_trading_requested=False,
    )

    base_trend_weight = _weight(decision.strategy_policy, STRATEGY_TREND)
    adjusted_trend_weight = _allocation_weight(decision.adjusted_allocations, STRATEGY_TREND)

    assert decision.meta_learning is not None
    assert decision.meta_learning.performance_decays
    assert adjusted_trend_weight < base_trend_weight
    assert f"{STRATEGY_TREND}:reduced_by_performance_decay" in decision.memory_notes


def test_confidence_scoring_aggregates_modules_and_returns_position_action() -> None:
    settings = build_test_settings(
        confidence_full_position_threshold=0.80,
        confidence_reduced_position_threshold=0.60,
    )
    diagnostics = MarketRegimeInput(
        adx=42,
        realized_volatility_percentile=0.22,
        liquidity_score=0.94,
        volume_zscore=1.1,
    )
    timeframe_assessment = assess_multi_timeframe_regime(
        settings=settings,
        timeframe_signals=[TimeframeRegimeSignal(timeframe="1h", diagnostics=diagnostics)],
    )
    strategy_policy = build_strategy_policy(settings=settings, diagnostics=diagnostics)

    confidence = score_policy_confidence(
        settings=settings,
        timeframe_assessment=timeframe_assessment,
        strategy_policy=strategy_policy,
        diagnostics=diagnostics,
        strategy_memory=[
            StrategyMemorySnapshot(
                strategy=STRATEGY_TREND,
                observations=80,
                win_rate=0.65,
                sharpe=1.3,
                max_drawdown=0.06,
                confidence=0.82,
            )
        ],
        module_confidences=[
            ModuleConfidenceSignal(module=STRATEGY_TREND, signal="LONG", confidence=0.88, uncertainty=0.10),
            ModuleConfidenceSignal(module=STRATEGY_MEAN_REVERSION, signal="WAIT", confidence=0.61, uncertainty=0.25),
            ModuleConfidenceSignal(module=STRATEGY_SENTIMENT, signal="WEAK", confidence=0.34, uncertainty=0.60),
            ModuleConfidenceSignal(module="statistical_arbitrage", signal="LONG", confidence=0.77, uncertainty=0.20),
        ],
    )

    assert confidence.module_scores[STRATEGY_TREND] == 0.88
    assert confidence.score >= settings.confidence_reduced_position_threshold
    assert confidence.action in {"FULL_POSITION", "REDUCED_POSITION"}
    assert confidence.position_size_multiplier > 0.0


def test_multi_timeframe_fusion_understands_micro_panic_and_macro_breakout() -> None:
    settings = build_test_settings()

    assessment = assess_multi_timeframe_regime(
        settings=settings,
        timeframe_signals=[
            TimeframeRegimeSignal(
                timeframe="5m",
                diagnostics=MarketRegimeInput(
                    realized_volatility_percentile=0.95,
                    liquidity_score=0.24,
                    sentiment_panic_score=0.96,
                ),
            ),
            TimeframeRegimeSignal(
                timeframe="1h",
                diagnostics=MarketRegimeInput(adx=18, realized_volatility_percentile=0.45, liquidity_score=0.82),
            ),
            TimeframeRegimeSignal(
                timeframe="daily",
                diagnostics=MarketRegimeInput(adx=39, realized_volatility_percentile=0.35, liquidity_score=0.90),
            ),
            TimeframeRegimeSignal(
                timeframe="weekly",
                diagnostics=MarketRegimeInput(
                    adx=35,
                    breakout_score=0.88,
                    realized_volatility_percentile=0.40,
                    liquidity_score=0.90,
                ),
            ),
        ],
    )

    assert assessment.short_term_panic is True
    assert assessment.micro_trend == "PANIC"
    assert assessment.macro_trend == "BREAKOUT"
    assert assessment.long_term_direction == "MACRO_BREAKOUT"
    assert assessment.unified_market_state == "SHORT_TERM_PANIC_WITH_BULLISH_MACRO"
    assert len(assessment.state_vector) == 12


def test_memory_similarity_search_returns_historical_regime_cautions() -> None:
    settings = build_test_settings(ai_memory_similarity_threshold=0.70)
    current = [0.3, 0.9, 0.9, 0.2, 0.8, 0.6, 0.7, 0.95, 0.8, 0.2, 0.1, 0.4]

    result = search_similar_regime_memories(
        settings=settings,
        current_state_vector=current,
        memories=[
            HistoricalRegimeMemory(
                memory_id="flash-crash",
                label="Flash crash",
                state_vector=[0.25, 0.95, 0.88, 0.18, 0.82, 0.55, 0.72, 0.92, 0.75, 0.15, 0.10, 0.35],
                successful_strategies=[STRATEGY_TREND],
                failed_strategies=[STRATEGY_MARKET_MAKING],
                dangerous_patterns=["spread_explosion", "liquidity_collapse"],
            ),
            HistoricalRegimeMemory(
                memory_id="calm-range",
                label="Calm range",
                state_vector=[0.1, 0.1, 0.1, 0.9, 0.05, 0.1, 0.1, 0.0, 0.1, 0.0, 0.8, 0.45],
            ),
        ],
    )

    assert result.matches[0].memory_id == "flash-crash"
    assert result.best_similarity >= 0.90
    assert "liquidity_collapse" in result.caution_flags
    assert "spread_explosion" in result.caution_flags


def test_governance_denies_critical_risk_changes() -> None:
    settings = build_test_settings()

    decision = evaluate_self_improvement_governance(
        settings=settings,
        proposal=SelfImprovementProposal(
            proposal_id="danger",
            change_type="risk_override",
            target="risk_engine",
            requested_capital_fraction=1.0,
            disables_risk_engine=True,
            disables_kill_switch=True,
            changes_global_risk_limits=True,
        ),
        evidence=SelfImprovementEvidence(
            sandbox_passed=True,
            paper_trading_passed=True,
            out_of_sample_score=0.90,
            walk_forward_score=0.90,
            monte_carlo_score=0.90,
            shadow_score=0.90,
            monitoring_approved=True,
            sandbox_approved=True,
        ),
        max_allowed_leverage=2.0,
    )

    assert decision.approved is False
    assert decision.mode == "DENIED"
    assert "cannot_disable_risk_engine" in decision.blockers
    assert "cannot_disable_kill_switch" in decision.blockers
    assert "cannot_change_global_risk_limits" in decision.blockers


def test_governance_allows_only_staged_rollout_after_all_validations() -> None:
    settings = build_test_settings(
        staged_rollout_initial_capital_fraction=0.01,
        staged_rollout_max_initial_capital_fraction=0.05,
    )

    decision = evaluate_self_improvement_governance(
        settings=settings,
        proposal=SelfImprovementProposal(
            proposal_id="model-v2",
            change_type="model_upgrade",
            target="trend_following_model",
            requested_capital_fraction=0.25,
        ),
        evidence=SelfImprovementEvidence(
            sandbox_passed=True,
            paper_trading_passed=True,
            out_of_sample_score=0.80,
            walk_forward_score=0.78,
            monte_carlo_score=0.82,
            shadow_score=0.76,
            monitoring_approved=True,
            sandbox_approved=True,
        ),
        max_allowed_leverage=2.0,
    )

    assert decision.approved is True
    assert decision.mode == "STAGED_ROLLOUT"
    assert decision.max_capital_fraction == 0.01
    assert "start_at_1_percent_then_5_10_25" in decision.required_next_steps
