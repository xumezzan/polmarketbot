import argparse
import asyncio
import logging
from datetime import UTC, datetime
from typing import Protocol

try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AsyncOpenAI,
        OpenAIError,
    )
except ImportError:  # pragma: no cover - exercised only in stripped test envs
    class OpenAIError(Exception):
        """Fallback base error when the OpenAI SDK is unavailable."""

    class APIConnectionError(OpenAIError):
        """Fallback connection error."""

    class APITimeoutError(OpenAIError):
        """Fallback timeout error."""

    class APIStatusError(OpenAIError):
        """Fallback HTTP status error."""

        status_code: int | None = None
        request_id: str | None = None

    AsyncOpenAI = None
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import AsyncSessionLocal
from app.logging_utils import configure_logging, log_event
from app.models.analysis import Analysis
from app.models.news import NewsItem
from app.repositories.analysis_repo import AnalysisRepository
from app.repositories.news_repo import NewsRepository
from app.schemas.verdict import AnalysisRunResult, Verdict
from app.services.forecasting import estimate_openai_cost_usd
from app.services.retry_utils import retry_async


logger = logging.getLogger(__name__)

_BTC_POSITIVE_HINTS = (
    "etf",
    "treasury",
    "adoption",
    "inflow",
    "product",
    "rally",
    "rallies",
    "surge",
    "surges",
    "gain",
    "gains",
    "bull",
    "record",
    "buy",
    "buys",
    "launch",
    "approval",
)
_BTC_AMBIGUOUS_HINTS = (
    "tremble",
    "trembles",
    "drift",
    "drifts",
    "fall",
    "falls",
    "drop",
    "drops",
    "selloff",
    "volatility",
    "risk",
)
_FED_HINTS = (
    "federal reserve",
    "fed chair",
    "powell",
    "rate cut",
    "rate cuts",
    "interest rate",
    "interest rates",
)


class LLMAnalysisError(Exception):
    """Raised when the LLM analysis step fails."""


class LLMAuthenticationError(LLMAnalysisError):
    """Raised when OpenAI authentication or configuration is invalid."""


class LLMClientProtocol(Protocol):
    """Interface for a structured verdict provider."""

    async def analyze_news_item(self, news_item: NewsItem) -> tuple[Verdict, dict[str, object] | None]:
        """Return a verdict and optional raw response payload."""


class StubLLMClient:
    """Deterministic fake analyzer for local end-to-end testing."""

    async def analyze_news_item(self, news_item: NewsItem) -> tuple[Verdict, dict[str, object] | None]:
        title = (news_item.title or "").lower()
        content = (news_item.content or "").lower()
        text = f"{title}\n{content}"

        has_bitcoin = any(token in text for token in ("bitcoin", "btc"))
        has_crypto = "crypto" in text
        has_etf = "etf" in text
        has_fed = _contains_any(text, _FED_HINTS) or "fed" in text
        references_bitcoin_market = has_bitcoin or has_etf or has_crypto

        if references_bitcoin_market and any(
            token in text for token in ("$1m", "1m", "one million", "gta vi")
        ):
            verdict = Verdict(
                relevance=0.88,
                confidence=0.82,
                direction="YES",
                fair_probability=0.64,
                market_query="bitcoin 1m gta vi",
                reason=(
                    "The article directly references an extreme bitcoin upside target, so "
                    "the stub maps it to a specific long-dated bitcoin milestone market."
                ),
            )
        elif references_bitcoin_market and _contains_any(text, _BTC_POSITIVE_HINTS):
            verdict = Verdict(
                relevance=0.87,
                confidence=0.80,
                direction="YES",
                fair_probability=0.67,
                market_query="bitcoin 150k june 2026",
                reason=(
                    "The article looks like a bullish bitcoin catalyst, so the stub maps "
                    "it to a concrete upside target instead of a vague bitcoin price query."
                ),
            )
        elif references_bitcoin_market and _contains_any(text, _BTC_AMBIGUOUS_HINTS):
            verdict = Verdict(
                relevance=0.62,
                confidence=0.66,
                direction="NONE",
                fair_probability=0.50,
                market_query="bitcoin 150k june 2026",
                reason=(
                    "The article is about bitcoin but the directional edge is weak, so the "
                    "stub keeps a neutral verdict while still pointing to a matchable market."
                ),
            )
        elif has_fed and not references_bitcoin_market:
            verdict = Verdict(
                relevance=0.61,
                confidence=0.70,
                direction="NONE",
                fair_probability=0.50,
                market_query="fed rate cuts 2026",
                reason=(
                    "The article matters for macro sentiment, but it still looks too "
                    "indirect for a clean binary trade in the stub path."
                ),
            )
        elif references_bitcoin_market:
            verdict = Verdict(
                relevance=0.55,
                confidence=0.60,
                direction="NONE",
                fair_probability=0.50,
                market_query="bitcoin 150k june 2026",
                reason=(
                    "The article touches crypto, but the catalyst is too fuzzy for a "
                    "directional trade, so the stub stays neutral on a concrete market."
                ),
            )
        elif has_fed:
            verdict = Verdict(
                relevance=0.56,
                confidence=0.64,
                direction="NONE",
                fair_probability=0.50,
                market_query="fed rate cuts 2026",
                reason=(
                    "The article looks macro-relevant, so the stub maps it to a rate-cut "
                    "market while keeping the direction neutral."
                ),
            )
        else:
            verdict = Verdict(
                relevance=0.35,
                confidence=0.40,
                direction="NONE",
                fair_probability=0.50,
                market_query="general news",
                reason=(
                    "The article does not provide a clear, tradable event for a binary "
                    "prediction market."
                ),
            )

        raw_response = {
            "provider": "stub",
            "generated_at": datetime.now(UTC).isoformat(),
            "verdict": verdict.model_dump(mode="json"),
        }
        return verdict, raw_response


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    """Return True when the input contains any of the configured hint phrases."""
    return any(pattern in text for pattern in patterns)


class OpenAILLMClient:
    """
    OpenAI adapter for structured verdict generation.

    Note:
        The installed SDK version in this repo is `openai==1.51.0`, which supports
        `beta.chat.completions.parse(...)` with a Pydantic model. Newer OpenAI docs
        often recommend the Responses API, but this implementation intentionally uses
        the current repo's compatible SDK surface instead of inventing unavailable methods.
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.openai_api_key:
            raise LLMAuthenticationError("OPENAI_API_KEY is required when LLM_MODE=openai")
        if AsyncOpenAI is None:
            raise LLMAuthenticationError("openai package is required when LLM_MODE=openai")

        self.settings = settings
        self.client = AsyncOpenAI(
            api_key=settings.openai_api_key,
            timeout=settings.openai_timeout_seconds,
        )

    async def analyze_news_item(self, news_item: NewsItem) -> tuple[Verdict, dict[str, object] | None]:
        prompt = self._build_user_prompt(news_item)

        async def _request_once():
            return await self.client.beta.chat.completions.parse(
                model=self.settings.openai_model,
                temperature=self.settings.openai_temperature,
                max_completion_tokens=self.settings.openai_max_completion_tokens,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You analyze news for a Polymarket paper-trading bot. "
                            "Return only a structured verdict that follows the schema. "
                            "LLM is an advisor, not the final decision-maker. "
                            "Be conservative. If the article is weak or ambiguous, use "
                            "direction=NONE and fair_probability near 0.50."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format=Verdict,
            )

        try:
            completion = await retry_async(
                _request_once,
                logger=logger,
                provider="openai",
                operation_name="structured_verdict",
                max_attempts=self.settings.openai_retry_max_attempts,
                base_delay_seconds=self.settings.openai_retry_base_delay_seconds,
                is_retryable=_is_retryable_openai_exception,
                context={
                    "model": self.settings.openai_model,
                    "news_item_id": news_item.id,
                },
            )
        except (APITimeoutError, APIConnectionError, APIStatusError, OpenAIError) as exc:
            request_id = getattr(exc, "request_id", None)
            status_code = getattr(exc, "status_code", None)
            log_event(
                logger,
                "llm_openai_request_failed",
                provider="openai",
                model=self.settings.openai_model,
                news_item_id=news_item.id,
                error=str(exc),
                request_id=request_id,
                status_code=status_code,
            )
            if status_code in {401, 403}:
                raise LLMAuthenticationError(f"OpenAI authentication failed: {exc}") from exc
            raise LLMAnalysisError(f"OpenAI analysis failed: {exc}") from exc

        message = completion.choices[0].message
        parsed_verdict = message.parsed

        if parsed_verdict is None:
            log_event(
                logger,
                "llm_openai_parse_failed",
                provider="openai",
                model=self.settings.openai_model,
                news_item_id=news_item.id,
            )
            raise LLMAnalysisError("OpenAI returned no parsed verdict.")

        raw_response = {
            "provider": "openai",
            "model": self.settings.openai_model,
            "request_id": getattr(completion, "_request_id", None),
            "message_content": message.content,
            "usage": _extract_openai_usage(completion=completion, settings=self.settings),
            "verdict": parsed_verdict.model_dump(mode="json"),
        }
        return parsed_verdict, raw_response

    def _build_user_prompt(self, news_item: NewsItem) -> str:
        content = (news_item.content or "")[: self.settings.llm_max_content_chars]

        return (
            "Analyze the following news item for a Polymarket news trading bot.\n\n"
            "Only produce a directional YES/NO verdict when the news maps to a concrete, "
            "currently plausible binary prediction market. Prefer specific market_query "
            "phrases that include the entity, measurable outcome, and timeframe when known "
            "(for example: 'bitcoin 150k june 2026', 'fed rate cuts 2026', "
            "'trump crypto tax 2027').\n\n"
            "Use direction=NONE, fair_probability=0.50, and a low confidence when the article "
            "is broad industry commentary, conference/speaker news, product thought leadership, "
            "security speculation, vague AI/crypto impact, or price movement without a clear "
            "market catalyst. For direction=NONE, market_query should be either a concrete "
            "market to monitor or 'general news'; do not use vague queries like "
            "'crypto impact', 'AI agents in crypto payments', or 'crypto security AI impact'.\n\n"
            "Do not infer a trade only from general sentiment. A good verdict should answer: "
            "which binary market would this affect, which side, and why now?\n\n"
            "Return a verdict with these meanings:\n"
            "- relevance: how relevant this news is for prediction markets, 0 to 1\n"
            "- confidence: how confident you are in your interpretation, 0 to 1\n"
            "- direction: YES, NO, or NONE\n"
            "- fair_probability: estimated fair probability for the best matching binary market, 0 to 1\n"
            "- market_query: short search query to find the matching Polymarket market\n"
            "- reason: short explanation in plain English\n\n"
            f"Source: {news_item.source}\n"
            f"Published at: {news_item.published_at}\n"
            f"Title: {news_item.title}\n"
            f"URL: {news_item.url}\n"
            f"Content: {content}\n"
        )


def _is_retryable_openai_exception(exc: Exception) -> bool:
    if isinstance(exc, (APITimeoutError, APIConnectionError)):
        return True

    if isinstance(exc, APIStatusError):
        status_code = getattr(exc, "status_code", None)
        return status_code == 429 or (status_code is not None and status_code >= 500)

    return False


class FallbackLLMClient:
    """Use OpenAI when available and fall back to stub analysis on auth/config errors."""

    def __init__(
        self,
        *,
        primary: LLMClientProtocol | None,
        fallback: LLMClientProtocol,
        primary_provider: str,
        initial_error: Exception | None = None,
    ) -> None:
        self.primary = primary
        self.fallback = fallback
        self.primary_provider = primary_provider
        self.initial_error = initial_error

    async def analyze_news_item(self, news_item: NewsItem) -> tuple[Verdict, dict[str, object] | None]:
        if self.primary is None:
            return await self._run_fallback(news_item=news_item, reason=str(self.initial_error))

        try:
            return await self.primary.analyze_news_item(news_item)
        except LLMAuthenticationError as exc:
            return await self._run_fallback(news_item=news_item, reason=str(exc))

    async def _run_fallback(
        self,
        *,
        news_item: NewsItem,
        reason: str,
    ) -> tuple[Verdict, dict[str, object] | None]:
        log_event(
            logger,
            "llm_fallback_activated",
            news_item_id=news_item.id,
            primary_provider=self.primary_provider,
            fallback_provider="stub",
            reason=reason,
        )
        verdict, raw_response = await self.fallback.analyze_news_item(news_item)
        payload = dict(raw_response or {})
        payload["fallback"] = {
            "from_provider": self.primary_provider,
            "to_provider": "stub",
            "reason": reason,
            "activated_at": datetime.now(UTC).isoformat(),
        }
        return verdict, payload


def build_llm_client(settings: Settings) -> LLMClientProtocol:
    """Return either a stub analyzer or the real OpenAI client."""
    mode = settings.llm_mode.lower()

    if mode == "stub":
        return StubLLMClient()

    if mode == "openai":
        fallback_mode = settings.llm_openai_fallback_mode.lower()
        try:
            primary = OpenAILLMClient(settings)
        except LLMAuthenticationError as exc:
            if fallback_mode == "stub":
                log_event(
                    logger,
                    "llm_primary_client_unavailable",
                    primary_provider="openai",
                    fallback_provider="stub",
                    reason=str(exc),
                )
                return FallbackLLMClient(
                    primary=None,
                    fallback=StubLLMClient(),
                    primary_provider="openai",
                    initial_error=exc,
                )
            raise

        if fallback_mode == "stub":
            return FallbackLLMClient(
                primary=primary,
                fallback=StubLLMClient(),
                primary_provider="openai",
            )
        return primary

    raise ValueError("Unsupported LLM_MODE. Expected 'stub' or 'openai'.")


def _extract_openai_usage(
    *,
    completion,
    settings: Settings,
) -> dict[str, int | float] | None:
    usage = getattr(completion, "usage", None)
    if usage is None:
        return None

    prompt_tokens = _coerce_int(getattr(usage, "prompt_tokens", None))
    completion_tokens = _coerce_int(getattr(usage, "completion_tokens", None))
    total_tokens = _coerce_int(getattr(usage, "total_tokens", None))
    if prompt_tokens is None or completion_tokens is None or total_tokens is None:
        return None

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": estimate_openai_cost_usd(
            settings=settings,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    }


def _coerce_int(value: object) -> int | None:
    if value is None:
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class LLMAnalyzerService:
    """Loads a news item, gets a verdict, and stores it in analyses."""

    def __init__(
        self,
        *,
        client: LLMClientProtocol,
        news_repository: NewsRepository,
        analysis_repository: AnalysisRepository,
    ) -> None:
        self.client = client
        self.news_repository = news_repository
        self.analysis_repository = analysis_repository

    async def analyze_one(
        self,
        *,
        news_item_id: int | None = None,
        force: bool = False,
    ) -> AnalysisRunResult:
        news_item = await self._load_news_item(news_item_id)

        if news_item is None:
            raise LLMAnalysisError("No news item found to analyze.")

        existing = await self.analysis_repository.get_by_news_item_id(news_item.id)
        if existing is not None and not force:
            verdict = self._analysis_to_verdict(existing)
            log_event(
                logger,
                "llm_analysis_reused",
                news_item_id=news_item.id,
                analysis_id=existing.id,
            )
            return AnalysisRunResult(
                news_item_id=news_item.id,
                analysis_id=existing.id,
                created_new=False,
                verdict=verdict,
            )

        await self._enforce_daily_budget()
        verdict, raw_response = await self.client.analyze_news_item(news_item)
        usage = self._extract_usage(raw_response)
        analysis = await self.analysis_repository.create(
            news_item_id=news_item.id,
            verdict=verdict,
            raw_response=raw_response,
            llm_provider=self._extract_raw_field(raw_response, "provider"),
            llm_model=self._extract_raw_field(raw_response, "model"),
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
            estimated_cost_usd=usage["estimated_cost_usd"],
        )

        log_event(
            logger,
            "llm_analysis_completed",
            news_item_id=news_item.id,
            analysis_id=analysis.id,
            direction=verdict.direction,
            relevance=verdict.relevance,
            confidence=verdict.confidence,
            fair_probability=verdict.fair_probability,
        )
        return AnalysisRunResult(
            news_item_id=news_item.id,
            analysis_id=analysis.id,
            created_new=True,
            verdict=verdict,
        )

    async def _enforce_daily_budget(self) -> None:
        if not isinstance(self.client, OpenAILLMClient):
            return

        if self.client.settings.openai_daily_budget_usd <= 0:
            return

        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        spent = await self.analysis_repository.sum_estimated_cost_since(since=day_start)
        if spent >= self.client.settings.openai_daily_budget_usd:
            raise LLMAnalysisError(
                "OpenAI daily budget exceeded: "
                f"{spent:.6f}>={self.client.settings.openai_daily_budget_usd:.6f}"
            )

    async def _load_news_item(self, news_item_id: int | None) -> NewsItem | None:
        if news_item_id is not None:
            return await self.news_repository.get_by_id(news_item_id)
        return await self.news_repository.get_latest()

    def _analysis_to_verdict(self, analysis: Analysis) -> Verdict:
        return Verdict(
            relevance=float(analysis.relevance),
            confidence=float(analysis.confidence),
            direction=analysis.direction.value,
            fair_probability=float(analysis.fair_probability),
            market_query=analysis.market_query,
            reason=analysis.reason,
        )

    def _extract_usage(
        self,
        raw_response: dict[str, object] | None,
    ) -> dict[str, int | float | None]:
        usage = (raw_response or {}).get("usage")
        if not isinstance(usage, dict):
            return {
                "prompt_tokens": None,
                "completion_tokens": None,
                "total_tokens": None,
                "estimated_cost_usd": None,
            }

        return {
            "prompt_tokens": _coerce_int(usage.get("prompt_tokens")),
            "completion_tokens": _coerce_int(usage.get("completion_tokens")),
            "total_tokens": _coerce_int(usage.get("total_tokens")),
            "estimated_cost_usd": (
                float(usage["estimated_cost_usd"])
                if usage.get("estimated_cost_usd") is not None
                else None
            ),
        }

    def _extract_raw_field(
        self,
        raw_response: dict[str, object] | None,
        field_name: str,
    ) -> str | None:
        value = (raw_response or {}).get(field_name)
        return str(value) if value is not None else None


async def run_llm_analysis(
    session: AsyncSession,
    settings: Settings,
    *,
    news_item_id: int | None = None,
    force: bool = False,
) -> AnalysisRunResult:
    """Convenience entrypoint for a single analysis run."""
    service = LLMAnalyzerService(
        client=build_llm_client(settings),
        news_repository=NewsRepository(session),
        analysis_repository=AnalysisRepository(session),
    )
    return await service.analyze_one(news_item_id=news_item_id, force=force)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze one news item with an LLM.")
    parser.add_argument("--news-id", type=int, default=None, help="Analyze a specific news_items.id")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Create a new analysis even if one already exists for the news item.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)

    async with AsyncSessionLocal() as session:
        result = await run_llm_analysis(
            session,
            settings,
            news_item_id=args.news_id,
            force=args.force,
        )
        print(result.model_dump_json())


if __name__ == "__main__":
    asyncio.run(_main())
