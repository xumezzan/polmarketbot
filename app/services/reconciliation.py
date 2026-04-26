from datetime import UTC, datetime

from app.models.enums import ReconciliationStatus
from app.repositories.reconciliation_repo import ReconciliationRepository
from app.repositories.runtime_flag_repo import RuntimeFlagRepository
from app.runtime_flags import RUNTIME_FLAG_LIVE_CIRCUIT_BREAKER
from app.schemas.live_execution import ReconciliationResult
from app.services.live_execution import reconcile_live_state


class ReconciliationService:
    """Phase-4 reconciliation plus circuit-breaker toggling."""

    def __init__(
        self,
        *,
        reconciliation_repository: ReconciliationRepository,
        runtime_flag_repository: RuntimeFlagRepository,
    ) -> None:
        self.reconciliation_repository = reconciliation_repository
        self.runtime_flag_repository = runtime_flag_repository

    async def run(self, *, session, settings) -> ReconciliationResult:
        if settings.execution_mode.lower().strip() != "live" or not settings.clob_private_key:
            await self.runtime_flag_repository.set_bool(
                key=RUNTIME_FLAG_LIVE_CIRCUIT_BREAKER,
                value=False,
            )
            return ReconciliationResult(
                status="PASSED",
                mismatch_count=0,
                details={"skipped": True, "reason": "live_execution_not_configured"},
            )

        run = await self.reconciliation_repository.create_started(
            started_at=datetime.now(UTC)
        )
        result = await reconcile_live_state(session, settings)
        status = ReconciliationStatus(result.status)
        await self.reconciliation_repository.finish(
            run=run,
            status=status,
            mismatch_count=result.mismatch_count,
            details=result.details,
            error=result.error,
        )
        await self.runtime_flag_repository.set_bool(
            key=RUNTIME_FLAG_LIVE_CIRCUIT_BREAKER,
            value=status != ReconciliationStatus.PASSED,
        )
        return result
