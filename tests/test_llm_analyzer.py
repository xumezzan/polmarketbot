import asyncio
from types import SimpleNamespace

from app.services.llm_analyzer import (
    LLMAuthenticationError,
    StubLLMClient,
    build_llm_client,
)
from tests.helpers import build_test_settings


def test_stub_llm_maps_bullish_bitcoin_etf_news_to_specific_market() -> None:
    news_item = SimpleNamespace(
        title="Goldman Sachs files for its first bitcoin ETF product",
        content="The filing is seen as another bullish catalyst for bitcoin adoption and ETF inflows.",
    )

    verdict, raw_response = asyncio.run(StubLLMClient().analyze_news_item(news_item))

    assert verdict.direction == "YES"
    assert verdict.market_query == "bitcoin 150k june 2026"
    assert verdict.fair_probability == 0.67
    assert raw_response is not None
    assert raw_response["provider"] == "stub"


def test_stub_llm_maps_fed_macro_news_to_neutral_rate_cut_query() -> None:
    news_item = SimpleNamespace(
        title="How Trump Is Sabotaging Himself on the Federal Reserve",
        content="The dispute centers on pressure around interest rates and the Fed chair.",
    )

    verdict, _ = asyncio.run(StubLLMClient().analyze_news_item(news_item))

    assert verdict.direction == "NONE"
    assert verdict.market_query == "fed rate cuts 2026"
    assert verdict.fair_probability == 0.50


def test_stub_llm_maps_bitcoin_million_target_to_gta_vi_market() -> None:
    news_item = SimpleNamespace(
        title="Will bitcoin hit $1M before GTA VI? Traders debate the odds",
        content="A new rally has revived discussion around whether BTC can reach one million first.",
    )

    verdict, _ = asyncio.run(StubLLMClient().analyze_news_item(news_item))

    assert verdict.direction == "YES"
    assert verdict.market_query == "bitcoin 1m gta vi"
    assert verdict.fair_probability == 0.64


def test_build_llm_client_falls_back_to_stub_when_openai_key_is_missing() -> None:
    client = build_llm_client(build_test_settings(llm_mode="openai", openai_api_key=""))
    news_item = SimpleNamespace(
        id=1,
        title="Bitcoin ETF inflows continue",
        content="Large ETF inflows are seen as bullish for bitcoin.",
    )

    verdict, raw_response = asyncio.run(client.analyze_news_item(news_item))

    assert verdict.direction == "YES"
    assert raw_response is not None
    assert raw_response["provider"] == "stub"
    assert raw_response["fallback"]["from_provider"] == "openai"


def test_build_llm_client_falls_back_to_stub_on_openai_auth_error(
    monkeypatch,
) -> None:
    class FakeOpenAIClient:
        def __init__(self, settings) -> None:
            self.settings = settings

        async def analyze_news_item(self, news_item):
            raise LLMAuthenticationError("OpenAI authentication failed: invalid key")

    monkeypatch.setattr("app.services.llm_analyzer.OpenAILLMClient", FakeOpenAIClient)

    client = build_llm_client(build_test_settings(llm_mode="openai", openai_api_key="test-key"))
    news_item = SimpleNamespace(
        id=2,
        title="Bitcoin adoption grows",
        content="A new adoption catalyst could lift sentiment.",
    )

    verdict, raw_response = asyncio.run(client.analyze_news_item(news_item))

    assert verdict.direction == "YES"
    assert raw_response is not None
    assert raw_response["provider"] == "stub"
    assert raw_response["fallback"]["reason"] == "OpenAI authentication failed: invalid key"
