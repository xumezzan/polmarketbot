from app.schemas.market import GammaMarket
from app.services.market_client import (
    extract_market_domain_anchor_tokens,
    filter_markets_by_query_domain,
    normalize_market_query,
)


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


def test_normalize_market_query_keeps_non_bitcoin_assets_compact() -> None:
    assert normalize_market_query("Ethereum transaction volume increase") == "ethereum volume"
    assert normalize_market_query("XRP price prediction") == "xrp"
    assert normalize_market_query("CLARITY Act approval") == "clarity act crypto"


def test_extract_market_domain_anchor_tokens_prefers_specific_assets() -> None:
    assert extract_market_domain_anchor_tokens("bitcoin all time high") == {"bitcoin", "btc"}
    assert "fed" in extract_market_domain_anchor_tokens("fed rate cuts 2026")


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
