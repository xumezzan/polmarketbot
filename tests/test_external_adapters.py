from types import SimpleNamespace

import pytest

from app.schemas.verdict import Verdict
from app.services.llm_analyzer import OpenAILLMClient
from app.services.market_client import GammaPolymarketClient
from tests.helpers import build_test_settings


@pytest.mark.asyncio
async def test_openai_llm_client_parses_structured_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    verdict = Verdict(
        relevance=0.88,
        confidence=0.77,
        direction="YES",
        fair_probability=0.66,
        market_query="bitcoin price",
        reason="A mocked structured verdict for testing the OpenAI adapter.",
    )

    class FakeCompletions:
        async def parse(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                _request_id="req_test_123",
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            parsed=verdict,
                            content="mocked response",
                        )
                    )
                ],
            )

    class FakeAsyncOpenAI:
        def __init__(self, *args, **kwargs) -> None:
            self.beta = SimpleNamespace(
                chat=SimpleNamespace(completions=FakeCompletions())
            )

    monkeypatch.setattr("app.services.llm_analyzer.AsyncOpenAI", FakeAsyncOpenAI)

    client = OpenAILLMClient(build_test_settings(openai_api_key="test-key", llm_mode="openai"))
    news_item = SimpleNamespace(
        id=123,
        source="stub",
        published_at="2026-04-13T09:00:00Z",
        title="Bitcoin rally continues",
        url="https://example.com/bitcoin-rally",
        content="Bitcoin and crypto sentiment improve after a large treasury disclosure.",
    )

    parsed_verdict, raw_response = await client.analyze_news_item(news_item)

    assert parsed_verdict == verdict
    assert raw_response is not None
    assert raw_response["provider"] == "openai"
    assert raw_response["request_id"] == "req_test_123"
    assert captured["response_format"] is Verdict
    assert captured["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_gamma_market_client_parses_mocked_api_response(monkeypatch: pytest.MonkeyPatch) -> None:
    request_log: list[dict[str, object]] = []
    market_payload = [
        {
            "id": "stub-btc-100k",
            "question": "Will Bitcoin reach $100,000 by December 31, 2026?",
            "slug": "bitcoin-100k-by-end-of-2026",
            "conditionId": "cond-btc-100k",
            "liquidity": "245000.5",
            "volume": "925000.2",
            "bestBid": 0.58,
            "bestAsk": 0.60,
            "lastTradePrice": 0.59,
            "active": True,
            "closed": False,
            "archived": False,
            "enableOrderBook": True,
            "outcomes": "[\"Yes\", \"No\"]",
            "outcomePrices": "[\"0.59\", \"0.41\"]",
            "clobTokenIds": "[\"btc100k-yes\", \"btc100k-no\"]",
            "events": [
                {
                    "id": "event-btc-price",
                    "slug": "bitcoin-price-targets",
                    "title": "Bitcoin price targets",
                }
            ],
        }
    ]

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self.payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, params: dict[str, object] | None = None):
            request_log.append({"url": url, "params": params or {}})
            self.calls += 1
            if self.calls == 1:
                return FakeResponse(market_payload)
            return FakeResponse([])

    monkeypatch.setattr("app.services.market_client.httpx.AsyncClient", FakeAsyncClient)

    client = GammaPolymarketClient(
        build_test_settings(
            market_fetch_mode="gamma",
            gamma_markets_page_size=1,
            gamma_markets_max_pages=2,
        )
    )
    markets = await client.fetch_markets()

    assert len(markets) == 1
    assert markets[0].id == "stub-btc-100k"
    assert markets[0].yes_price == pytest.approx(0.59)
    assert markets[0].no_price == pytest.approx(0.41)
    assert markets[0].event_slug == "bitcoin-price-targets"
    assert request_log[0]["params"] == {
        "limit": 1,
        "offset": 0,
        "active": "true",
        "closed": "false",
    }
