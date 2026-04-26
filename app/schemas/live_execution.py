from pydantic import BaseModel, Field


class ExecutionIntentPayload(BaseModel):
    """Deterministic payload snapshot for one shadow/live order."""

    asset_id: str
    market_id: str
    side: str
    target_size_usd: float
    shares: float
    requested_price: float
    max_acceptable_price: float
    order_type: str
    client_order_id: str


class ExecutionIntentRecord(BaseModel):
    """Persisted execution intent summary."""

    intent_id: int
    signal_id: int
    market_id: str
    side: str
    token_id: str
    execution_mode: str
    status: str
    target_size_usd: float
    shares: float
    requested_price: float
    max_acceptable_price: float
    client_order_id: str
    payload: ExecutionIntentPayload
    exchange_order_id: str | None = None
    error: str | None = None
    created_at: str
    executed_at: str | None = None


class ShadowExecutionResult(BaseModel):
    """Result of phase-2 execution simulation."""

    intent: ExecutionIntentRecord
    audit_trail: list[str] = Field(default_factory=list)


class LiveOrderResult(BaseModel):
    """Normalized live order placement or status result."""

    intent: ExecutionIntentRecord
    live_order_id: int | None = None
    live_position_id: int | None = None
    order_status: str
    exchange_order_id: str | None = None
    raw_response: dict[str, object] = Field(default_factory=dict)


class ReconciliationResult(BaseModel):
    """Phase-4 reconciliation output."""

    status: str
    mismatch_count: int = 0
    details: dict[str, object] = Field(default_factory=dict)
    error: str | None = None
