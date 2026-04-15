import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.logging_utils import log_event


T = TypeVar("T")


async def retry_async(
    operation: Callable[[], Awaitable[T]],
    *,
    logger: logging.Logger,
    provider: str,
    operation_name: str,
    max_attempts: int,
    base_delay_seconds: float,
    is_retryable: Callable[[Exception], bool],
    context: dict[str, object] | None = None,
) -> T:
    """Run one async operation with simple exponential backoff."""
    attempts = max(max_attempts, 1)
    delay_base = max(base_delay_seconds, 0.0)
    extra_context = context or {}

    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            should_retry = attempt < attempts and is_retryable(exc)
            if not should_retry:
                raise

            delay_seconds = round(delay_base * (2 ** (attempt - 1)), 2)
            log_event(
                logger,
                "external_call_retry_scheduled",
                provider=provider,
                operation=operation_name,
                attempt=attempt,
                max_attempts=attempts,
                delay_seconds=delay_seconds,
                error=str(exc),
                **extra_context,
            )
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

    raise RuntimeError("retry_async exhausted without returning or re-raising")
