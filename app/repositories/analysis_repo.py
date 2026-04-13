import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analysis import Analysis
from app.models.enums import VerdictDirection
from app.schemas.verdict import Verdict


class AnalysisRepository:
    """Persistence helper for LLM analysis results."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, analysis_id: int) -> Analysis | None:
        """Return one analysis by primary key."""
        stmt = sa.select(Analysis).where(Analysis.id == analysis_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_latest(self) -> Analysis | None:
        """Return the latest stored analysis."""
        stmt = sa.select(Analysis).order_by(Analysis.id.desc()).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_news_item_id(self, news_item_id: int) -> Analysis | None:
        """Return the latest analysis for a news item if one exists."""
        stmt = (
            sa.select(Analysis)
            .where(Analysis.news_item_id == news_item_id)
            .order_by(Analysis.id.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create(
        self,
        *,
        news_item_id: int,
        verdict: Verdict,
        raw_response: dict[str, object] | None,
    ) -> Analysis:
        """Insert a new analysis row and commit it."""
        analysis = Analysis(
            news_item_id=news_item_id,
            relevance=verdict.relevance,
            confidence=verdict.confidence,
            direction=VerdictDirection(verdict.direction),
            fair_probability=verdict.fair_probability,
            market_query=verdict.market_query,
            reason=verdict.reason,
            raw_response=raw_response,
        )
        self.session.add(analysis)
        await self.session.commit()
        await self.session.refresh(analysis)
        return analysis

    async def save_market_matching_snapshot(
        self,
        *,
        analysis_id: int,
        snapshot: dict[str, object],
    ) -> Analysis:
        """Store the latest market matching snapshot inside raw_response JSONB."""
        analysis = await self.get_by_id(analysis_id)
        if analysis is None:
            raise ValueError(f"Analysis {analysis_id} not found.")

        raw_response = dict(analysis.raw_response or {})
        snapshots = dict(raw_response.get("snapshots") or {})
        snapshots["market_matching"] = snapshot
        raw_response["snapshots"] = snapshots

        analysis.raw_response = raw_response
        await self.session.commit()
        await self.session.refresh(analysis)
        return analysis
