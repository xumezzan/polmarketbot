from pydantic import BaseModel, Field


class AnomalyObservationCreate(BaseModel):
    """Observation payload captured by Anomaly Hunter."""

    cycle_id: str | None = None
    observed_at: str
    observation_type: str
    subject_type: str | None = None
    subject_id: str | None = None
    severity: str
    score: float
    title: str
    details: dict[str, object] = Field(default_factory=dict)


class AnomalyHypothesisCreate(BaseModel):
    """Hypothesis payload generated from recent observations."""

    generated_at: str
    window_start: str
    window_end: str
    hypothesis_type: str
    status: str = "OPEN"
    score: float
    title: str
    rationale: str
    evidence: dict[str, object] = Field(default_factory=dict)


class AnomalyObservationItem(BaseModel):
    """Read model for one anomaly observation."""

    id: int
    cycle_id: str | None = None
    observed_at: str
    observation_type: str
    subject_type: str | None = None
    subject_id: str | None = None
    severity: str
    score: float
    title: str
    details: dict[str, object] = Field(default_factory=dict)


class AnomalyHypothesisItem(BaseModel):
    """Read model for one anomaly hypothesis."""

    id: int
    generated_at: str
    window_start: str
    window_end: str
    hypothesis_type: str
    status: str
    score: float
    title: str
    rationale: str
    evidence: dict[str, object] = Field(default_factory=dict)


class AnomalyHunterAnalysisResult(BaseModel):
    """Result of one 6h anomaly analysis pass."""

    generated_at: str
    window_start: str
    window_end: str
    observations_analyzed: int = 0
    hypotheses_created: int = 0
    hypotheses: list[AnomalyHypothesisItem] = Field(default_factory=list)


class AnomalyHunterReport(BaseModel):
    """Operator-facing report for Anomaly Hunter hypotheses."""

    generated_at: str
    window_hours: int
    observations_count: int = 0
    hypotheses_count: int = 0
    top_hypotheses: list[AnomalyHypothesisItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
