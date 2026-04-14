from datetime import datetime
from pydantic import BaseModel, Field


class VerificationLayer(BaseModel):
    """Result of one verification layer check."""

    status: str = "ok"  # ok, warning, error
    message: str | None = None
    last_verified_at: datetime = Field(default_factory=datetime.utcnow)
    details: dict[str, object] = Field(default_factory=dict)


class SystemVerificationReport(BaseModel):
    """Full 4-layer system verification report."""

    generated_at: datetime = Field(default_factory=datetime.utcnow)
    overall_status: str = "ok"

    layer_a_server: VerificationLayer = Field(description="Server is alive")
    layer_b_pipeline: VerificationLayer = Field(description="Pipeline is alive")
    layer_c_data: VerificationLayer = Field(description="Data is being saved")
    layer_d_automation: VerificationLayer = Field(description="Automation is working")
