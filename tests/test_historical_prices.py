import pytest

from app.services.historical_prices import ClobHistoricalPriceClient
from tests.helpers import build_test_settings


@pytest.mark.asyncio
async def test_clob_historical_price_client_parses_batch_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_log: list[dict[str, object]] = []
    payload = {
        "history": {
            "yes-token-1": [
                {"t": 1713200000, "p": 0.61},
                {"timestamp": 1713203600, "price": 0.64},
            ]
        }
    }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return payload

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url: str, json: dict[str, object]):
            request_log.append({"url": url, "json": json})
            return FakeResponse()

    monkeypatch.setattr("app.services.historical_prices.httpx.AsyncClient", FakeAsyncClient)

    client = ClobHistoricalPriceClient(build_test_settings())
    result = await client.fetch_batch_prices_history(
        market_ids=["yes-token-1"],
        start_ts=1713200000,
        end_ts=1713207200,
        interval="1h",
        fidelity=15,
    )

    assert request_log[0]["url"].endswith("/batch-prices-history")
    assert request_log[0]["json"] == {
        "markets": ["yes-token-1"],
        "start_ts": 1713200000,
        "end_ts": 1713207200,
        "interval": "1h",
        "fidelity": 15,
    }
    assert len(result.history["yes-token-1"]) == 2
    assert result.history["yes-token-1"][0].timestamp == 1713200000
    assert result.history["yes-token-1"][1].price == pytest.approx(0.64)
