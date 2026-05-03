from app.services.risk_engine import evaluate_risk_case
from tests.helpers import build_test_settings


def test_risk_engine_allows_clean_actionable_signal() -> None:
    settings = build_test_settings()

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=45,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.55,
        existing_open_position=False,
        daily_exposure_used_usd=20.0,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is True
    assert result.blockers == []
    assert result.approved_size_usd == 50.0


def test_risk_engine_blocks_stale_duplicate_and_daily_limit() -> None:
    settings = build_test_settings(risk_max_news_age_minutes=360)

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=720,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.55,
        existing_open_position=True,
        daily_exposure_used_usd=250.0,
    )

    assert result.allow is False
    assert "news_too_old:720>360" in result.blockers
    assert "duplicate_market_position_exists" in result.blockers
    assert "daily_limit_reached:250.00>=250.00" in result.blockers
    assert result.approved_size_usd == 0.0


def test_risk_engine_allows_existing_position_when_stronger_duplicate_is_approved() -> None:
    settings = build_test_settings(
        risk_allow_stronger_duplicate_position=True,
        risk_max_daily_exposure_usd=250.0,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=45,
        liquidity=200000.0,
        edge=0.24,
        match_score=0.55,
        existing_open_position=True,
        duplicate_position_allowed=True,
        daily_exposure_used_usd=20.0,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is True
    assert "duplicate_market_position_exists" not in result.blockers
    assert result.approved_size_usd == 50.0


def test_risk_engine_still_blocks_existing_position_without_stronger_duplicate_override() -> None:
    settings = build_test_settings(risk_allow_stronger_duplicate_position=True)

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=45,
        liquidity=200000.0,
        edge=0.24,
        match_score=0.55,
        existing_open_position=True,
        duplicate_position_allowed=False,
        daily_exposure_used_usd=20.0,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is False
    assert "duplicate_market_position_exists" in result.blockers


def test_risk_engine_blocks_low_liquidity_and_priced_in_signal() -> None:
    settings = build_test_settings()

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=10,
        liquidity=1000.0,
        edge=0.02,
        match_score=0.55,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
    )

    assert result.allow is False
    assert "liquidity_too_low:1000.00<10000.00" in result.blockers
    assert "priced_in_or_converged:0.0200<=0.0300" in result.blockers
    assert result.approved_size_usd == 0.0


def test_risk_engine_allows_older_signal_inside_extended_window_with_smaller_size() -> None:
    settings = build_test_settings(
        risk_enable_extended_news_age_window=True,
        risk_extended_max_news_age_minutes=1800,
        risk_extended_news_age_size_multiplier=0.5,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=1476,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.55,
        existing_open_position=False,
        daily_exposure_used_usd=20.0,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is True
    assert result.blockers == []
    assert result.approved_size_usd == 25.0


def test_risk_engine_still_blocks_signal_beyond_extended_window() -> None:
    settings = build_test_settings(
        risk_enable_extended_news_age_window=True,
        risk_extended_max_news_age_minutes=1800,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=2000,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.55,
        existing_open_position=False,
        daily_exposure_used_usd=20.0,
    )

    assert result.allow is False
    assert "news_too_old:2000>1800" in result.blockers
    assert result.approved_size_usd == 0.0


def test_risk_engine_blocks_weak_market_match() -> None:
    settings = build_test_settings(risk_min_match_score=0.35)

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.24,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
    )

    assert result.allow is False
    assert "match_score_too_low:0.2400<0.3500" in result.blockers
    assert result.approved_size_usd == 0.0


def test_risk_engine_blocks_low_causality_and_non_whitelisted_news() -> None:
    settings = build_test_settings()

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        causality_score=0.30,
        event_category="OTHER",
        news_quality="LOW",
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.10,
        match_score=0.55,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
        query_text="Trump tariffs analysis",
        market_question="Will Trump win the 2028 election?",
    )

    assert result.allow is False
    assert "causality_below_threshold:0.3000<0.7000" in result.blockers
    assert "event_category_not_allowed:OTHER not in COURT_DECISION,ELECTION,POLITICIAN_HEALTH,WAR_CONFLICT" in result.blockers
    assert "news_quality_not_allowed:LOW not in CONFIRMED_EVENT,OFFICIAL_STATEMENT" in result.blockers


def test_risk_engine_blocks_indirect_market_event_match() -> None:
    settings = build_test_settings()

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        causality_score=0.90,
        event_category="COURT_DECISION",
        news_quality="CONFIRMED_EVENT",
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.10,
        match_score=0.55,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
        query_text="Trump court conviction",
        market_question="Will Trump win the 2028 election?",
    )

    assert result.allow is False
    assert "direct_market_event_match_missing" in result.blockers


def test_risk_engine_blocks_after_two_trades_per_day() -> None:
    settings = build_test_settings(risk_max_trades_per_day=2)

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        causality_score=0.90,
        event_category="ELECTION",
        news_quality="CONFIRMED_EVENT",
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.10,
        match_score=0.55,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
        daily_trade_count=2,
        query_text="Senate election winner",
        market_question="Will Republicans win the Senate election?",
    )

    assert result.allow is False
    assert "daily_trade_count_limit_reached:2>=2" in result.blockers


def test_risk_engine_allows_meaningful_query_market_overlap_for_short_queries() -> None:
    settings = build_test_settings(
        risk_min_query_market_token_overlap=2,
        risk_min_query_market_overlap_token_length=5,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is True
    assert result.blockers == []


def test_risk_engine_blocks_query_market_overlap_with_only_weak_name_match() -> None:
    settings = build_test_settings(
        risk_min_query_market_token_overlap=2,
        risk_min_query_market_overlap_token_length=5,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
        query_text="Will Eric Swalwell resign from Congress?",
        market_question="Will Eric Trump win the 2028 Republican presidential nomination?",
    )

    assert result.allow is False
    assert "query_market_overlap_too_low:count=1,max_len=4" in result.blockers


def test_risk_engine_blocks_generic_overlap_without_anchor_match() -> None:
    settings = build_test_settings(
        risk_min_query_market_token_overlap=2,
        risk_min_query_market_overlap_token_length=5,
        risk_min_anchor_entity_overlap=1,
        risk_anchor_entity_max_tokens=2,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
        query_text="Will Eric Swalwell resign from Congress?",
        market_question="Will Trump resign by December 31, 2026?",
    )

    assert result.allow is False
    assert (
        "anchor_entity_overlap_too_low:anchors=congress,swalwell,count=0"
        in result.blockers
    )


def test_risk_engine_blocks_ceasefire_market_with_wrong_country_anchor() -> None:
    settings = build_test_settings(
        risk_min_query_market_token_overlap=2,
        risk_min_query_market_overlap_token_length=5,
        risk_min_anchor_entity_overlap=1,
        risk_anchor_entity_max_tokens=2,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
        query_text="U.S.-Iran Ceasefire",
        market_question="Russia x Ukraine ceasefire by end of 2026?",
    )

    assert result.allow is False
    assert "anchor_entity_overlap_too_low:anchors=iran,count=0" in result.blockers


def test_risk_engine_allows_short_symbol_anchor_match() -> None:
    settings = build_test_settings(
        risk_min_query_market_token_overlap=2,
        risk_min_query_market_overlap_token_length=5,
        risk_min_anchor_entity_overlap=1,
        risk_anchor_entity_max_tokens=2,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
        query_text="XRP price prediction",
        market_question="Will XRP hit $3 by June 30, 2026?",
    )

    assert result.allow is True
    assert result.blockers == []


def test_risk_engine_blocks_second_trade_for_same_analysis() -> None:
    settings = build_test_settings(
        risk_max_trades_per_analysis=1,
        risk_min_query_market_token_overlap=2,
        risk_min_query_market_overlap_token_length=5,
        risk_min_anchor_entity_overlap=1,
        risk_anchor_entity_max_tokens=2,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
        analysis_trade_count=1,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is False
    assert "analysis_trade_limit_reached:1>=1" in result.blockers


def test_risk_engine_blocks_entity_open_position_limit() -> None:
    settings = build_test_settings(
        risk_max_open_positions_per_entity=1,
        risk_max_entity_open_exposure_usd=50.0,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        entity_key="bitcoin",
        entity_open_positions_count=1,
        entity_open_exposure_used_usd=25.0,
        daily_exposure_used_usd=0.0,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is False
    assert "entity_open_position_limit_reached:bitcoin:1>=1" in result.blockers


def test_risk_engine_blocks_entity_open_exposure_limit() -> None:
    settings = build_test_settings(
        risk_max_open_positions_per_entity=2,
        risk_max_entity_open_exposure_usd=50.0,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        entity_key="bitcoin",
        entity_open_positions_count=1,
        entity_open_exposure_used_usd=50.0,
        daily_exposure_used_usd=0.0,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is False
    assert "entity_open_exposure_limit_reached:bitcoin:50.00>=50.00" in result.blockers


def test_risk_engine_caps_approved_size_by_entity_remaining_exposure() -> None:
    settings = build_test_settings(
        risk_max_open_positions_per_entity=2,
        risk_max_entity_open_exposure_usd=30.0,
        risk_max_trade_size_usd=50.0,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        entity_key="bitcoin",
        entity_open_positions_count=0,
        entity_open_exposure_used_usd=12.0,
        daily_exposure_used_usd=0.0,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is True
    assert result.approved_size_usd == 18.0


def test_risk_engine_blocks_wide_bid_ask_spread() -> None:
    settings = build_test_settings(
        risk_max_bid_ask_spread=0.03,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        bid_ask_spread=0.05,
        daily_exposure_used_usd=0.0,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is False
    assert "spread_too_wide:0.0500>0.0300" in result.blockers


def test_risk_engine_blocks_yes_entry_slippage() -> None:
    settings = build_test_settings(
        risk_max_bid_ask_spread=0.03,
        risk_max_yes_entry_slippage=0.02,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        bid_ask_spread=0.01,
        yes_entry_slippage=0.03,
        daily_exposure_used_usd=0.0,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is False
    assert "yes_entry_slippage_too_high:0.0300>0.0200" in result.blockers


def test_risk_engine_allows_no_side_without_yes_slippage_check() -> None:
    settings = build_test_settings(
        risk_max_bid_ask_spread=0.03,
        risk_max_yes_entry_slippage=0.02,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        bid_ask_spread=0.01,
        yes_entry_slippage=None,
        daily_exposure_used_usd=0.0,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is True


def test_risk_engine_blocks_ambiguous_top_candidates() -> None:
    settings = build_test_settings(
        risk_min_query_market_token_overlap=2,
        risk_min_query_market_overlap_token_length=5,
        risk_min_anchor_entity_overlap=1,
        risk_anchor_entity_max_tokens=2,
        risk_min_top_candidate_score_delta=0.05,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
        top_candidate_score_delta=0.03,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is False
    assert "ambiguous_market_match:0.0300<0.0500" in result.blockers


def test_risk_engine_allows_clear_top_candidate_gap() -> None:
    settings = build_test_settings(
        risk_min_query_market_token_overlap=2,
        risk_min_query_market_overlap_token_length=5,
        risk_min_anchor_entity_overlap=1,
        risk_anchor_entity_max_tokens=2,
        risk_min_top_candidate_score_delta=0.05,
    )

    result = evaluate_risk_case(
        settings=settings,
        signal_status="ACTIONABLE",
        confidence=0.79,
        relevance=0.86,
        news_age_minutes=30,
        liquidity=200000.0,
        edge=0.08,
        match_score=0.42,
        existing_open_position=False,
        daily_exposure_used_usd=0.0,
        top_candidate_score_delta=0.09,
        query_text="Bitcoin price prediction",
        market_question="Will Bitcoin hit $150k by June 30, 2026?",
    )

    assert result.allow is True
    assert result.blockers == []
