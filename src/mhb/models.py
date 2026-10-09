"""Canonical records for the complaint-intake pipeline.

Two rules shape everything in this module:

1. `RawComplaint.text` may contain PII. Nothing downstream of `RedactionResult`
   is allowed to hold it. The `Case` object therefore never carries raw text —
   only `redacted_text` plus a vault id that resolves locally.
2. Every automated judgment is a `Decision`: it carries the rule or rubric
   version that produced it, the evidence it stood on, and a confidence. An
   assessment without provenance is not auditable, and Compliance has already
   rejected one system it could not audit.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

CHANNELS = ("email", "web_form", "call_note")

# Case lifecycle. The only transition into a SENT state is performed by
# mhb.review.approve(), which requires a named human approver.
STATES = (
    "INGESTED",
    "REDACTED",
    "TRIAGED",
    "ROUTED",
    "DRAFTED",
    "PENDING_HUMAN_APPROVAL",
    "APPROVED_FOR_SEND",
    "RETURNED_FOR_REWORK",
    "BLOCKED_QA",
)
TERMINAL_HUMAN_STATES = ("APPROVED_FOR_SEND", "RETURNED_FOR_REWORK")

DIMENSIONS = (
    "financial_harm",
    "vulnerability",
    "regulatory_exposure",
    "recurrence",
    "duration_unresolved",
    "escalation_signals",
    "relationship_value",
)


def _asdict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {k: _asdict(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _asdict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_asdict(v) for v in obj]
    return obj


@dataclass
class RawComplaint:
    """As received, before anything touches it. PII-bearing."""

    complaint_id: str
    channel: str
    received_at: str
    text: str
    subject: str = ""
    customer_ref: str = ""
    metadata: dict = field(default_factory=dict)

    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]


@dataclass
class PIIFinding:
    rule_id: str
    placeholder: str
    start: int
    end: int
    original: str  # vault-only; never serialized into a Case


@dataclass
class RedactionResult:
    redacted_text: str
    redacted_subject: str
    findings: list[PIIFinding]
    vault_id: str
    residual_risk_notes: list[str] = field(default_factory=list)

    def public(self) -> dict:
        """Everything except the original values — safe to persist in the case file."""
        return {
            "vault_id": self.vault_id,
            "redacted_text": self.redacted_text,
            "redacted_subject": self.redacted_subject,
            "pii_count": len(self.findings),
            "pii_kinds": sorted({f.rule_id for f in self.findings}),
            "placeholders": sorted({f.placeholder for f in self.findings}),
            "residual_risk_notes": self.residual_risk_notes,
        }


@dataclass
class Signal:
    """One observable fact about a complaint. Observation is shared ground;
    what it is *worth* is the rubric's business, not the detector's."""

    dimension: str
    level: int
    rule_id: str
    evidence: str


@dataclass
class RegulatoryFinding:
    category_id: str
    label: str
    citation: str
    statutory_clock: str | None
    exposure: int
    strength: str  # "strong" | "weak"
    evidence: list[str]


@dataclass
class SeverityAssessment:
    rubric_id: str
    rubric_label: str
    dimension_levels: dict
    raw_score: float
    normalized: float
    band: str
    band_label: str
    overrides_applied: list[dict]
    confidence: float
    rationale: list[str]


@dataclass
class RoutingDecision:
    department: str
    department_label: str
    queue_email: str
    confidence: float
    alternates: list[dict]
    reason: str
    tiebreaker_applied: str | None = None


@dataclass
class DraftResponse:
    template_id: str
    subject: str
    body: str
    placeholders_used: list[str]
    lint_findings: list[dict]
    confidence: float


@dataclass
class QAResult:
    passed: bool
    blocks: list[dict]
    warnings: list[dict]
    checks_run: list[str]


@dataclass
class Decision:
    """Audit envelope. One per agent invocation."""

    agent: str
    agent_version: str
    engine: str
    ruleset_ids: list[str]
    at: str
    duration_ms: float
    confidence: float | None
    summary: str
    payload: dict


@dataclass
class Case:
    case_id: str
    complaint_id: str
    channel: str
    received_at: str
    content_hash: str
    state: str = "INGESTED"

    redaction: dict | None = None
    signals: list[Signal] = field(default_factory=list)
    regulatory: list[RegulatoryFinding] = field(default_factory=list)
    severity: SeverityAssessment | None = None
    severity_alt: SeverityAssessment | None = None
    calibration_flag: bool = False
    calibration_note: str = ""
    routing: RoutingDecision | None = None
    draft: DraftResponse | None = None
    qa: QAResult | None = None

    sla: dict = field(default_factory=dict)
    decisions: list[Decision] = field(default_factory=list)
    human: dict = field(default_factory=dict)

    @property
    def redacted_text(self) -> str:
        return (self.redaction or {}).get("redacted_text", "")

    @property
    def regulatory_ids(self) -> list[str]:
        return [r.category_id for r in self.regulatory if r.strength == "strong"]

    def to_dict(self) -> dict:
        return _asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)
