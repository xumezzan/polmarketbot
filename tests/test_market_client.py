from app.schemas.market import GammaMarket
from app.services.market_client import (
    KeywordMarketRanker,
    extract_market_domain_anchor_tokens,
    filter_markets_by_query_domain,
    infer_market_contract_type,
    is_market_domain_compatible,
    market_contract_compatibility,
    normalize_market_query,
)
from tests.helpers import build_test_settings


def test_normalize_market_query_condenses_all_time_high_queries() -> None:
    assert (
        normalize_market_query("Will Bitcoin reach a new all-time high in 2026?")
        == "bitcoin all time high"
    )


def test_normalize_market_query_extracts_price_targets() -> None:
    assert normalize_market_query("Bitcoin price prediction $125,000") == "bitcoin 125k"


def test_normalize_market_query_maps_operational_queries_to_tradeable_terms() -> None:
    assert (
        normalize_market_query("U.S. government bitcoin seizure impact")
        == "bitcoin government transfer"
    )
    assert normalize_market_query("Bitcoin tax implications") == "bitcoin tax"
    assert normalize_market_query("Bitcoin quantum resistance upgrades") == "bitcoin quantum"


def test_normalize_market_query_maps_strategy_to_microstrategy() -> None:
    assert (
        normalize_market_query("Strategy buys 3,273 bitcoin as it nears 1 million target")
        == "microstrategy bitcoin buy"
    )


def test_normalize_market_query_keeps_non_bitcoin_assets_compact() -> None:
    assert normalize_market_query("Ethereum transaction volume increase") == "ethereum volume"
    assert normalize_market_query("XRP price prediction") == "xrp"
    assert normalize_market_query("CLARITY Act approval") == "clarity act crypto"
    assert normalize_market_query("clarty act senate action 2026") == "clarity act crypto"


def test_extract_market_domain_anchor_tokens_prefers_specific_assets() -> None:
    assert extract_market_domain_anchor_tokens("bitcoin all time high") == {"bitcoin", "btc"}
    assert "fed" in extract_market_domain_anchor_tokens("fed rate cuts 2026")


def test_extract_market_domain_anchor_tokens_ignores_generic_market_terms() -> None:
    assert extract_market_domain_anchor_tokens("cftc prediction market regulation") == {"cftc"}
    assert extract_market_domain_anchor_tokens("wisconsin prediction market lawsuit") == {
        "wisconsin"
    }
    assert extract_market_domain_anchor_tokens("polymarket lawsuit") == set()


def test_extract_market_domain_anchor_tokens_keeps_stablecoin_queries_specific() -> None:
    assert extract_market_domain_anchor_tokens("stablecoin reserve manager") == {
        "stablecoin",
        "stablecoins",
    }


def test_filter_markets_by_query_domain_discards_irrelevant_2026_markets() -> None:
    markets = [
        GammaMarket.model_validate(
            {
                "id": "btc",
                "question": "Will Bitcoin hit $150k by June 30, 2026?",
                "slug": "will-bitcoin-hit-150k-by-june-30-2026",
            }
        ),
        GammaMarket.model_validate(
            {
                "id": "knicks",
                "question": "Will the New York Knicks win the 2026 NBA Finals?",
                "slug": "will-the-new-york-knicks-win-the-2026-nba-finals",
            }
        ),
        GammaMarket.model_validate(
            {
                "id": "governor",
                "question": "Will the Republicans win the New York governor race in 2026?",
                "slug": "will-the-republicans-win-the-new-york-governor-race-in-2026",
            }
        ),
    ]

    filtered = filter_markets_by_query_domain(
        markets=markets,
        query_text="bitcoin all time high",
    )

    assert [market.id for market in filtered] == ["btc"]


def test_filter_markets_by_query_domain_keeps_macro_markets_for_fed_queries() -> None:
    markets = [
        GammaMarket.model_validate(
            {
                "id": "fed",
                "question": "Will the Fed cut rates in June 2026?",
                "slug": "fed-cut-rates-june-2026",
            }
        ),
        GammaMarket.model_validate(
            {
                "id": "sports",
                "question": "Will New Zealand win the 2026 FIFA World Cup?",
                "slug": "new-zealand-win-2026-fifa-world-cup",
            }
        ),
    ]

    filtered = filter_markets_by_query_domain(
        markets=markets,
        query_text="fed rate cuts 2026",
    )

    assert [market.id for market in filtered] == ["fed"]


def test_filter_markets_by_query_domain_ignores_generic_description_matches() -> None:
    markets = [
        GammaMarket.model_validate(
            {
                "id": "senate",
                "question": "Will the Democratic Party control the Senate after 2026?",
                "slug": "democratic-party-control-senate-2026",
                "description": "This market resolves after a federal election.",
            }
        ),
        GammaMarket.model_validate(
            {
                "id": "fed",
                "question": "Will the Fed cut rates in June 2026?",
                "slug": "fed-cut-rates-june-2026",
            }
        ),
    ]

    filtered = filter_markets_by_query_domain(
        markets=markets,
        query_text="fed rate cuts",
    )

    assert [market.id for market in filtered] == ["fed"]


def test_filter_markets_by_query_domain_returns_empty_for_platform_only_query() -> None:
    markets = [
        GammaMarket.model_validate(
            {
                "id": "any",
                "question": "Will Bitcoin hit $150k by December 31, 2026?",
                "slug": "will-bitcoin-hit-150k-by-december-31-2026",
            }
        )
    ]

    assert filter_markets_by_query_domain(markets=markets, query_text="polymarket lawsuit") == []


def test_filter_markets_by_query_domain_avoids_legal_query_geography_matches() -> None:
    markets = [
        GammaMarket.model_validate(
            {
                "id": "wisconsin-governor",
                "question": "Will the Democrats win the Wisconsin governor race in 2026?",
                "slug": "democrats-win-wisconsin-governor-race-2026",
            }
        )
    ]

    assert (
        filter_markets_by_query_domain(
            markets=markets,
            query_text="wisconsin market lawsuit",
        )
        == []
    )


def test_market_domain_compatibility_rejects_breach_false_friends() -> None:
    data_center = GammaMarket.model_validate(
        {
            "id": "ai-data-center",
            "question": "AI data center moratorium passed before 2027?",
            "slug": "ai-data-center-moratorium-before-2027",
        }
    )
    sports_team = GammaMarket.model_validate(
        {
            "id": "boston-breach",
            "question": "Will Boston Breach finish in the top 4 of the CDL Regular Season?",
            "slug": "boston-breach-top-4-cdl-regular-season",
        }
    )

    assert (
        is_market_domain_compatible(
            query_text="polymarket data breach 2026",
            market=data_center,
        )
        is False
    )
    assert (
        is_market_domain_compatible(
            query_text="polymarket data breach 2026",
            market=sports_team,
        )
        is False
    )


def test_market_domain_compatibility_requires_atm_for_crypto_atm_queries() -> None:
    liquidation = GammaMarket.model_validate(
        {
            "id": "crypto-liquidation",
            "question": "Record crypto liquidation in 2026?",
            "slug": "record-crypto-liquidation-in-2026",
        }
    )
    atm_market = GammaMarket.model_validate(
        {
            "id": "crypto-atm",
            "question": "Will Canada ban crypto ATMs before 2027?",
            "slug": "canada-ban-crypto-atms-before-2027",
        }
    )

    assert (
        is_market_domain_compatible(
            query_text="canada crypto atm ban 2026",
            market=liquidation,
        )
        is False
    )
    assert (
        is_market_domain_compatible(
            query_text="canada crypto atm ban 2026",
            market=atm_market,
        )
        is True
    )


def test_market_domain_compatibility_rejects_wrong_act_for_clarity_queries() -> None:
    insurrection = GammaMarket.model_validate(
        {
            "id": "insurrection-act",
            "question": "Insurrection Act invoked by December 31?",
            "slug": "insurrection-act-invoked-by-december-31",
        }
    )

    assert (
        is_market_domain_compatible(
            query_text="clarity act senate action 2026",
            market=insurrection,
        )
        is False
    )


def test_keyword_ranker_does_not_exact_match_single_asset_queries() -> None:
    ranker = KeywordMarketRanker(build_test_settings(market_match_min_score=0.0))
    analysis = type(
        "AnalysisStub",
        (),
        {
            "id": 1,
            "news_item_id": 1,
            "market_query": "Bitcoin price prediction",
        },
    )()
    markets = [
        GammaMarket.model_validate(
            {
                "id": "btc-150k",
                "question": "Will Bitcoin hit $150k by December 31, 2026?",
                "slug": "will-bitcoin-hit-150k-by-december-31-2026",
            }
        )
    ]

    candidates = ranker.rank(analysis=analysis, markets=markets)

    assert candidates[0].score_breakdown["exact_match"] == 0.0


def test_infer_market_contract_type_separates_price_targets_from_performance() -> None:
    assert infer_market_contract_type("bitcoin 100k december 2026") == "price_target"
    assert (
        infer_market_contract_type("Will Bitcoin have the best performance in 2026?")
        == "relative_performance"
    )
    assert (
        infer_market_contract_type("Bitcoin all time high by September 30, 2026?")
        == "all_time_high"
    )


def test_market_contract_compatibility_rejects_wrong_bitcoin_contract_type() -> None:
    market = GammaMarket.model_validate(
        {
            "id": "btc-performance",
            "question": "Will Bitcoin have the best performance in 2026?",
            "slug": "will-bitcoin-have-the-best-performance-in-2026",
        }
    )

    assert (
        market_contract_compatibility(
            query_text="bitcoin 100k december 2026",
            market=market,
        )
        == 0.0
    )


def test_market_contract_compatibility_rejects_purchase_query_for_sale_market() -> None:
    market = GammaMarket.model_validate(
        {
            "id": "mstr-sell",
            "question": "MicroStrategy sells any Bitcoin by June 30, 2026?",
            "slug": "microstrategy-sells-any-bitcoin-by-june-30-2026",
        }
    )

    assert (
        market_contract_compatibility(
            query_text="microstrategy bitcoin buy",
            market=market,
        )
        == 0.0
    )


def test_keyword_ranker_skips_incompatible_contract_types() -> None:
    ranker = KeywordMarketRanker(build_test_settings(market_match_min_score=0.0))
    analysis = type(
        "AnalysisStub",
        (),
        {
            "id": 1,
            "news_item_id": 1,
            "market_query": "bitcoin 100k december 2026",
        },
    )()
    markets = [
        GammaMarket.model_validate(
            {
                "id": "btc-performance",
                "question": "Will Bitcoin have the best performance in 2026?",
                "slug": "will-bitcoin-have-the-best-performance-in-2026",
            }
        ),
        GammaMarket.model_validate(
            {
                "id": "btc-ath",
                "question": "Bitcoin all time high by September 30, 2026?",
                "slug": "bitcoin-all-time-high-by-september-30-2026",
            }
        ),
        GammaMarket.model_validate(
            {
                "id": "btc-100k",
                "question": "Will Bitcoin reach $100,000 by December 31, 2026?",
                "slug": "will-bitcoin-reach-100k-by-december-31-2026",
            }
        ),
    ]

    candidates = ranker.rank(analysis=analysis, markets=markets)

    assert [candidate.market_id for candidate in candidates] == ["btc-100k"]
