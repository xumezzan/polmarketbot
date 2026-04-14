from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.analysis import Analysis
from app.models.enums import VerdictDirection
from app.schemas.verdict import Verdict


class AnalysisRepository:
    """Persistence helper for LLM analysis results."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _with_context(self) -> sa.Select[tuple[Analysis]]:
        return sa.select(Analysis).options(
            selectinload(Analysis.news_item),
            selectinload(Analysis.signals),
        )

    async def _save_snapshot(
        self,
        *,
        analysis_id: int,
        snapshot_name: str,
        snapshot: dict[str, object],
    ) -> Analysis:
        analysis = await self.get_by_id(analysis_id)
        if analysis is None:
            raise ValueError(f"Analysis {analysis_id} not found.")

        raw_response = dict(analysis.raw_response or {})
        snapshots = dict(raw_response.get("snapshots") or {})
        snapshots[snapshot_name] = snapshot
        raw_response["snapshots"] = snapshots

        analysis.raw_response = raw_response
        await self.session.commit()
        await self.session.refresh(analysis)
        return analysis

    async def get_by_id(self, analysis_id: int) -> Analysis | None:
        """Return one analysis by primary key."""
        stmt = sa.select(Analysis).where(Analysis.id == analysis_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_latest(self) -> Analysis | None:
        """Return the latest stored analysis."""
        stmt = sa.select(Analysis).order_by(Analysis.id.desc()).limit(1)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def count(self) -> int:
        """Return total number of analysis rows."""
        stmt = sa.select(sa.func.count()).select_from(Analysis)
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_created_since(self, *, since: datetime) -> int:
        """Return analyses count created since a timestamp."""
        stmt = (
            sa.select(sa.func.count())
            .select_from(Analysis)
            .where(Analysis.created_at >= since)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_with_context(
        self,
        *,
        since: datetime | None = None,
    ) -> list[Analysis]:
        """Return analyses with linked news/signals for analytics and reporting."""
        stmt = self._with_context().order_by(Analysis.id)
        if since is not None:
            stmt = stmt.where(Analysis.created_at >= since)
        return list((await self.session.execute(stmt)).scalars().all())

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
        return await self._save_snapshot(
            analysis_id=analysis_id,
            snapshot_name="market_matching",
            snapshot=snapshot,
        )

    async def save_signal_engine_snapshot(
        self,
        *,
        analysis_id: int,
        snapshot: dict[str, object],
    ) -> Analysis:
        """Store the latest signal engine snapshot inside raw_response JSONB."""
        return await self._save_snapshot(
            analysis_id=analysis_id,
            snapshot_name="signal_engine",
            snapshot=snapshot,
        )

    async def save_risk_engine_decision(
        self,
        *,
        analysis_id: int,
        decision: dict[str, object],
    ) -> Analysis:
        """Append or replace one risk decision inside raw_response JSONB."""
        analysis = await self.get_by_id(analysis_id)
        if analysis is None:
            raise ValueError(f"Analysis {analysis_id} not found.")

        raw_response = dict(analysis.raw_response or {})
        snapshots = dict(raw_response.get("snapshots") or {})
        risk_snapshot = dict(snapshots.get("risk_engine") or {})
        decisions = list(risk_snapshot.get("decisions") or [])

        filtered_decisions = [
            item for item in decisions if item.get("signal_id") != decision.get("signal_id")
        ]
        filtered_decisions.append(decision)

        risk_snapshot["updated_at"] = decision.get("evaluated_at")
        risk_snapshot["decisions"] = filtered_decisions
        snapshots["risk_engine"] = risk_snapshot
        raw_response["snapshots"] = snapshots

        analysis.raw_response = raw_response
        await self.session.commit()
        await self.session.refresh(analysis)
        return analysis

    async def save_paper_trader_action(
        self,
        *,
        analysis_id: int,
        action: dict[str, object],
    ) -> Analysis:
        """Append one open/close action to the paper trader snapshot."""
        analysis = await self.get_by_id(analysis_id)
        if analysis is None:
            raise ValueError(f"Analysis {analysis_id} not found.")

        raw_response = dict(analysis.raw_response or {})
        snapshots = dict(raw_response.get("snapshots") or {})
        paper_snapshot = dict(snapshots.get("paper_trader") or {})
        actions = list(paper_snapshot.get("actions") or [])
        actions.append(action)

        paper_snapshot["updated_at"] = action.get("action_at")
        paper_snapshot["actions"] = actions
        snapshots["paper_trader"] = paper_snapshot
        raw_response["snapshots"] = snapshots

        analysis.raw_response = raw_response
        await self.session.commit()
        await self.session.refresh(analysis)
        return analysis
