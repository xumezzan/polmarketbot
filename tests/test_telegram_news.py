from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.main import _format_recent_news_message


@pytest.mark.asyncio
async def test_format_recent_news_message_handles_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeNewsRepository:
        def __init__(self, session) -> None:
            pass

        async def list_recent_news(self, *, limit: int = 5):
            return []

    monkeypatch.setattr("app.main.NewsRepository", FakeNewsRepository)

    message = await _format_recent_news_message(session=object())

    assert message == "<b>📰 Последние новости</b>\nНовостей пока нет."


@pytest.mark.asyncio
async def test_format_recent_news_message_escapes_html(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeNewsRepository:
        def __init__(self, session) -> None:
            pass

        async def list_recent_news(self, *, limit: int = 5):
            return [
                SimpleNamespace(
                    title="BTC <moves> & markets",
                    source="Wire <A>",
                    url="https://example.com/news?a=1&b=2",
                    published_at=datetime(2026, 4, 27, 9, 0, tzinfo=timezone.utc),
                )
            ]

    monkeypatch.setattr("app.main.NewsRepository", FakeNewsRepository)

    message = await _format_recent_news_message(session=object())

    assert "BTC &lt;moves&gt; &amp; markets" in message
    assert "Wire &lt;A&gt;" in message
    assert "https://example.com/news?a=1&amp;b=2" in message
    assert "2026-04-27T09:00:00+00:00" in message
