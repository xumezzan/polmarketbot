import logging

import httpx

from app.config import Settings
from app.logging_utils import log_event
from app.schemas.historical_prices import BatchPriceHistoryResult, PriceHistoryPoint
from app.services.retry_utils import retry_async


logger = logging.getLogger(__name__)


class HistoricalPriceClientError(Exception):
    """Raised when historical price retrieval fails."""


class ClobHistoricalPriceClient:
    """Thin adapter over Polymarket CLOB batch price history."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def fetch_batch_prices_history(
        self,
        *,
        market_ids: list[str],
        start_ts: int | None = None,
        end_ts: int | None = None,
        interval: str = "1h",
        fidelity: int = 1,
    ) -> BatchPriceHistoryResult:
        if not market_ids:
            return BatchPriceHistoryResult()

        if len(market_ids) > 20:
            raise HistoricalPriceClientError("CLOB batch-prices-history accepts at most 20 markets.")

        url = f"{self.settings.clob_api_base_url.rstrip('/')}/batch-prices-history"
        payload: dict[str, object] = {
            "markets": market_ids,
            "interval": interval,
            "fidelity": fidelity,
        }
        if start_ts is not None:
            payload["start_ts"] = start_ts
        if end_ts is not None:
            payload["end_ts"] = end_ts

        async with httpx.AsyncClient(timeout=self.settings.gamma_request_timeout_seconds) as client:
            async def _request_once() -> httpx.Response:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                return response

            try:
                response = await retry_async(
                    _request_once,
                    logger=logger,
                    provider="polymarket_clob",
                    operation_name="batch_prices_history",
                    max_attempts=self.settings.gamma_retry_max_attempts,
                    base_delay_seconds=self.settings.gamma_retry_base_delay_seconds,
                    is_retryable=_is_retryable_historical_price_exception,
                    context={"market_count": len(market_ids), "interval": interval},
                )
            except httpx.HTTPError as exc:
                log_event(
                    logger,
                    "clob_batch_prices_history_failed",
                    provider="polymarket_clob",
                    market_count=len(market_ids),
                    error=str(exc),
                )
                raise HistoricalPriceClientError(f"CLOB batch-prices-history failed: {exc}") from exc

        raw_history = response.json().get("history")
        if not isinstance(raw_history, dict):
            raise HistoricalPriceClientError("CLOB batch-prices-history returned invalid payload.")

        normalized: dict[str, list[PriceHistoryPoint]] = {}
        for market_id, items in raw_history.items():
            if not isinstance(items, list):
                continue

            normalized[str(market_id)] = [
                PriceHistoryPoint(
                    timestamp=int(item.get("t") or item.get("timestamp") or 0),
                    price=float(item.get("p") or item.get("price") or 0.0),
                )
                for item in items
                if isinstance(item, dict)
            ]

        return BatchPriceHistoryResult(history=normalized)


def _is_retryable_historical_price_exception(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code
        return status_code == 429 or 500 <= status_code <= 599

    return isinstance(exc, httpx.TransportError)
