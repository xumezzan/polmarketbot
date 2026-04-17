from pydantic import BaseModel, Field


class ForecastObservationSyncResult(BaseModel):
    """Summary of one resolved-signal observation sync pass."""

    evaluated_signals: int = 0
    synced_observations: int = 0
    unresolved_signals: int = 0
    skipped_signals: int = 0
    synced_signal_ids: list[int] = Field(default_factory=list)
