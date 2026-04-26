from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import Analysis
from app.models.news import NewsItem
from app.schemas.news import NormalizedNewsItem


class NewsRepository:
    """Persistence helper for normalized news rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_many(self, items: Sequence[NormalizedNewsItem]) -> tuple[int, int]:
        """
        Save only unique news rows.

        Returns:
            inserted_count, skipped_count
        """
        if not items:
            return 0, 0

        urls = {item.url for item in items}
        content_hashes = {item.content_hash for item in items}

        stmt = sa.select(NewsItem.url, NewsItem.content_hash).where(
            sa.or_(
                NewsItem.url.in_(urls),
                NewsItem.content_hash.in_(content_hashes),
            )
        )
        existing_rows = (await self.session.execute(stmt)).all()

        existing_urls = {row[0] for row in existing_rows}
        existing_hashes = {row[1] for row in existing_rows}

        inserted_count = 0
        skipped_count = 0

        for item in items:
            if item.url in existing_urls or item.content_hash in existing_hashes:
                skipped_count += 1
                continue

            self.session.add(
                NewsItem(
                    source=item.source,
                    title=item.title,
                    url=item.url,
                    content=item.content,
                    published_at=item.published_at,
                    content_hash=item.content_hash,
                    raw_payload=item.raw_payload,
                )
            )
            inserted_count += 1
            existing_urls.add(item.url)
            existing_hashes.add(item.content_hash)

        await self.session.commit()
        return inserted_count, skipped_count

    async def count(self) -> int:
        """Return total number of stored news rows."""
        stmt = sa.select(sa.func.count()).select_from(NewsItem)
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_created_since(self, *, since: datetime) -> int:
        """Return number of news rows inserted since a timestamp."""
        stmt = (
            sa.select(sa.func.count())
            .select_from(NewsItem)
            .where(NewsItem.created_at >= since)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def get_by_id(self, news_item_id: int) -> NewsItem | None:
        """Return one news item by primary key."""
        stmt = sa.select(NewsItem).where(NewsItem.id == news_item_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_latest(self) -> NewsItem | None:
        """Return the newest stored news item by id."""
        stmt = sa.select(NewsItem).order_by(NewsItem.id.desc()).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_without_analysis(self, *, limit: int | None = None) -> list[NewsItem]:
        """Return news items that have not gone through LLM analysis yet."""
        analysis_exists = (
            sa.select(sa.literal(1))
            .select_from(Analysis)
            .where(Analysis.news_item_id == NewsItem.id)
        )
        stmt = (
            sa.select(NewsItem)
            .where(~sa.exists(analysis_exists))
            .order_by(NewsItem.published_at.desc().nullslast(), NewsItem.id.desc())
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    async def count_stale_without_analysis(self, *, cutoff: datetime) -> int:
        """Return unanalyzed news rows older than the provided cutoff timestamp."""
        analysis_exists = (
            sa.select(sa.literal(1))
            .select_from(Analysis)
            .where(Analysis.news_item_id == NewsItem.id)
        )
        stmt = (
            sa.select(sa.func.count())
            .select_from(NewsItem)
            .where(
                ~sa.exists(analysis_exists),
                NewsItem.published_at.is_not(None),
                NewsItem.published_at < cutoff,
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def delete_stale_without_analysis(self, *, cutoff: datetime) -> int:
        """Delete unanalyzed news rows older than the provided cutoff timestamp."""
        analysis_exists = (
            sa.select(sa.literal(1))
            .select_from(Analysis)
            .where(Analysis.news_item_id == NewsItem.id)
        )
        stmt = (
            sa.delete(NewsItem)
            .where(
                ~sa.exists(analysis_exists),
                NewsItem.published_at.is_not(None),
                NewsItem.published_at < cutoff,
            )
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        return int(result.rowcount or 0)
