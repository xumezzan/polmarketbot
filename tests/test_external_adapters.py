from types import SimpleNamespace

import httpx
import pytest

from app.schemas.verdict import Verdict
from app.services.llm_analyzer import OpenAILLMClient
from app.services.market_client import GammaPolymarketClient
from app.services.news_fetcher import NewsApiClient
from tests.helpers import build_test_settings


class _FakeRuntimeFlagRepository:
    def __init__(self) -> None:
        self.values: dict[str, str | None] = {}

    async def get_text(self, *, key: str) -> str | None:
        return self.values.get(key)

    async def set_text(self, *, key: str, value: str | None):
        self.values[key] = value
        return None


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
                usage=SimpleNamespace(
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                ),
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
    usage = raw_response["usage"]
    assert usage["prompt_tokens"] == 100
    assert usage["completion_tokens"] == 50
    assert usage["total_tokens"] == 150
    assert usage["estimated_cost_usd"] == pytest.approx(0.0003)
    assert captured["response_format"] is Verdict
    assert captured["model"] == "gpt-5.4-mini"


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


@pytest.mark.asyncio
async def test_gamma_market_client_fetches_single_market_by_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_log: list[dict[str, object]] = []
    market_payload = {
        "id": "stub-btc-100k",
        "question": "Will Bitcoin reach $100,000 by December 31, 2026?",
        "slug": "bitcoin-100k-by-end-of-2026",
        "conditionId": "cond-btc-100k",
        "liquidity": "245000.5",
        "volume": "925000.2",
        "bestBid": 0.58,
        "bestAsk": 0.60,
        "lastTradePrice": 0.59,
        "active": False,
        "closed": True,
        "resolutionSource": "oracle",
        "outcomes": "[\"Yes\", \"No\"]",
        "outcomePrices": "[\"1.0\", \"0.0\"]",
        "clobTokenIds": "[\"btc100k-yes\", \"btc100k-no\"]",
        "feeSchedule": {"rate": 400, "exponent": 4, "takerOnly": True, "rebateRate": 0},
        "feesEnabled": True,
        "events": [],
    }

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self.payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, params: dict[str, object] | None = None):
            request_log.append({"url": url, "params": params})
            return FakeResponse(market_payload)

    monkeypatch.setattr("app.services.market_client.httpx.AsyncClient", FakeAsyncClient)

    client = GammaPolymarketClient(build_test_settings(market_fetch_mode="gamma"))
    market = await client.fetch_market("stub-btc-100k")

    assert market is not None
    assert market.id == "stub-btc-100k"
    assert market.closed is True
    assert market.effective_taker_fee_rate == pytest.approx(0.04)
    assert request_log[0]["url"].endswith("/markets/stub-btc-100k")


@pytest.mark.asyncio
async def test_news_api_client_retries_rate_limit_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_log: list[dict[str, object]] = []
    article_payload = {
        "status": "ok",
        "totalResults": 1,
        "articles": [
            {
                "source": {"id": "stub-1", "name": "Stub Crypto Wire"},
                "author": "Bot Tester",
                "title": "Bitcoin jumps on ETF optimism",
                "description": "Short summary",
                "url": "https://example.com/bitcoin-etf",
                "publishedAt": "2026-04-15T09:00:00Z",
                "content": "Bitcoin jumped after ETF optimism returned.",
            }
        ],
    }

    class FakeResponse:
        def __init__(self, payload, status_code: int = 200, url: str = "https://example.com") -> None:
            self.payload = payload
            self.status_code = status_code
            self._url = url
            self.text = '{"status":"error","code":"rateLimited"}' if status_code == 429 else ""

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = httpx.Request("GET", self._url)
                response = httpx.Response(self.status_code, request=request, text=self.text)
                raise httpx.HTTPStatusError(
                    f"status={self.status_code}",
                    request=request,
                    response=response,
                )

        def json(self):
            return self.payload

    class FakeAsyncClient:
        calls = 0

        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, params: dict[str, object] | None = None, headers=None):
            request_log.append({"url": url, "params": params or {}, "headers": headers or {}})
            FakeAsyncClient.calls += 1
            if FakeAsyncClient.calls == 1:
                return FakeResponse({}, status_code=429, url=url)
            return FakeResponse(article_payload, status_code=200, url=url)

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.services.news_fetcher.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.news_fetcher.asyncio.sleep", fake_sleep)
    client = NewsApiClient(
        build_test_settings(
            news_fetch_mode="newsapi",
            news_api_key="test-news-key",
            news_retry_max_attempts=2,
            news_retry_base_delay_seconds=0,
        ),
        runtime_flag_repository=_FakeRuntimeFlagRepository(),
    )
    articles = await client.fetch_latest()

    assert len(articles) == 1
    assert articles[0].title == "Bitcoin jumps on ETF optimism"
    assert len(request_log) == 2


@pytest.mark.asyncio
async def test_gamma_market_client_retries_rate_limit_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        def __init__(self, payload, status_code: int = 200, url: str = "https://example.com") -> None:
            self.payload = payload
            self.status_code = status_code
            self._url = url

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = httpx.Request("GET", self._url)
                response = httpx.Response(self.status_code, request=request)
                raise httpx.HTTPStatusError(
                    f"status={self.status_code}",
                    request=request,
                    response=response,
                )

        def json(self):
            return self.payload

    class FakeAsyncClient:
        calls = 0

        def __init__(self, *args, **kwargs) -> None:
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, params: dict[str, object] | None = None):
            request_log.append({"url": url, "params": params or {}})
            FakeAsyncClient.calls += 1
            if FakeAsyncClient.calls == 1:
                return FakeResponse({}, status_code=429, url=url)
            if FakeAsyncClient.calls == 2:
                return FakeResponse(market_payload, status_code=200, url=url)
            return FakeResponse([], status_code=200, url=url)

    async def fake_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("app.services.market_client.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("app.services.market_client.asyncio.sleep", fake_sleep)

    client = GammaPolymarketClient(
        build_test_settings(
            market_fetch_mode="gamma",
            gamma_markets_page_size=1,
            gamma_markets_max_pages=2,
            gamma_retry_max_attempts=2,
            gamma_retry_base_delay_seconds=0,
        )
    )
    markets = await client.fetch_markets()

    assert len(markets) == 1
    assert markets[0].id == "stub-btc-100k"
    assert len(request_log) == 3
