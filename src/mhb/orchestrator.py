"""Pipeline orchestration.

Deliberately a plain sequential function rather than a model deciding which
agent to call next. For a regulated intake process that is a feature, not a
limitation: the order is auditable, the same complaint produces the same path
every time, and a failure is attributable to one step. The multi-agent value
here comes from specialised, independently testable and independently tunable
steps — not from letting a planner improvise over a compliance process.

Ordering constraints that are real:
  intake     -> everything (normalisation)
  redaction  -> everything downstream (the PII boundary)
  regulatory -> severity (feeds the regulatory_exposure dimension)
             -> routing  (ownership tiebreakers)
  severity   -> clocks   (resolve SLA depends on the band)
  clocks     -> drafting ({{NEXT_STEP}} quotes the date)
  qa         -> last, and can veto
"""
from __future__ import annotations

from . import clock, store
from .agents import (
    DraftingAgent, IntakeAgent, QAAgent, RedactionAgent,
    RegulatoryAgent, RoutingAgent, SeverityAgent,
)
from .engines import get_engine
from .models import Case, RawComplaint

# Structured context the pipeline will carry. Allowlisted: anything not on
# this list stays out of the case file, so a future CRM integration cannot
# quietly widen what the case object holds.
METADATA_ALLOW = (
    "prior_contacts", "days_open", "tenure_years", "product_count",
    "relationship_flag", "account_type", "branch", "channel",
)


class Pipeline:
    def __init__(self, cfg, engine_name: str = "rules"):
        self.cfg = cfg
        self.engine = get_engine(engine_name, cfg)
        self.intake = IntakeAgent(cfg, self.engine)
        self.redaction = RedactionAgent(cfg, self.engine)
        self.regulatory = RegulatoryAgent(cfg, self.engine)
        self.severity = SeverityAgent(cfg, self.engine)
        self.routing = RoutingAgent(cfg, self.engine)
        self.drafting = DraftingAgent(cfg, self.engine)
        self.qa = QAAgent(cfg, self.engine)

    def run(self, raw: RawComplaint, persist: bool = True) -> Case:
        case, normalized, info = self.intake.build_case(raw)
        case.human["metadata"] = {
            k: v for k, v in (raw.metadata or {}).items() if k in METADATA_ALLOW
        }
        case.human["metadata"]["channel"] = raw.channel

        self.intake(case, raw=raw, normalized=normalized, info=info)
        self.redaction(case, text=normalized, subject=raw.subject)
        self.regulatory(case)
        self.severity(case)
        self.routing(case)

        # SLA clocks, preserving anything the regulatory agent already set.
        computed = clock.build_clocks(
            case.received_at,
            case.severity.band if case.severity else "S4",
            bool(case.regulatory_ids),
            self.cfg.policy,
        )
        case.sla = {**computed, **case.sla}

        self.drafting(case)
        self.qa(case)

        if persist:
            store.save_case(case.to_dict())
            store.audit({
                "event": "pipeline_run",
                "case_id": case.case_id,
                "channel": case.channel,
                "engine": self.engine.name,
                "state": case.state,
                "band": case.severity.band if case.severity else None,
                "band_alt": case.severity_alt.band if case.severity_alt else None,
                "calibration_flag": case.calibration_flag,
                "department": case.routing.department if case.routing else None,
                "regulatory": case.regulatory_ids,
                "qa_passed": case.qa.passed if case.qa else None,
                "total_ms": round(sum(d.duration_ms for d in case.decisions), 2),
            })
        return case

    def run_many(self, raws: list[RawComplaint], persist: bool = True) -> list[Case]:
        return [self.run(r, persist=persist) for r in raws]
