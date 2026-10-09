"""QA supervisor — the gate.

This agent exists because of one sentence in discovery: the compliance officer
will not accept any customer-facing text going out without human sign-off, and
she is the one who killed the last vendor pilot.

The weak answer to that is "we'll add an approval step." The answer here is
that no code path exists from an agent to a customer. The pipeline's terminal
state is PENDING_HUMAN_APPROVAL. The only function that produces
APPROVED_FOR_SEND is `mhb.review.approve()`, it requires a named approver, and
it refuses to run on a case this agent blocked. tests/test_gates.py attempts
the bypass four different ways and asserts each one fails.

QA runs last and can veto. A case it blocks still reaches a human — it goes to
the rework queue instead of the approval queue. Nothing is dropped silently.
"""
from __future__ import annotations

from .. import leak
from ..models import Case, QAResult
from .base import Agent


class QAAgent(Agent):
    name = "qa_supervisor"
    version = "1.2.0"

    def ruleset_ids(self):
        return [self.cfg.policy["policy_id"]]

    def handle(self, case: Case):
        blocks: list[dict] = []
        warnings: list[dict] = []
        checks: list[str] = []
        gates = self.cfg.policy["gates"]
        thresholds = self.cfg.policy["confidence_thresholds"]

        def check(cid: str):
            checks.append(cid)

        # --- 1. the send gate ------------------------------------------------
        check("QA-GATE-NO-AUTOSEND")
        if gates["agents_may_send_customer_text"]:
            blocks.append({"check": "QA-GATE-NO-AUTOSEND", "detail":
                           "policy.gates.agents_may_send_customer_text is true — "
                           "refusing to run a pipeline that could send unreviewed text"})
        if case.state == "APPROVED_FOR_SEND" or case.human.get("approver"):
            blocks.append({"check": "QA-GATE-NO-AUTOSEND", "detail":
                           "case arrived at QA already marked approved — approval must "
                           "come from mhb.review.approve(), after QA, by a named human"})

        # --- 2. PII leak -----------------------------------------------------
        check("QA-PII-LEAK")
        found = leak.scan_case(case.to_dict())
        if found:
            blocks.append({"check": "QA-PII-LEAK", "detail":
                           f"{len(found)} vault value(s) present in the case file: {found}"})

        # --- 3. customer-facing text lint ------------------------------------
        check("QA-DRAFT-LINT")
        if case.draft:
            for f in case.draft.lint_findings:
                target = blocks if f["severity"] == "block" else warnings
                target.append({"check": "QA-DRAFT-LINT", "detail":
                               f"{f['rule']}: {f['why']} (matched {f['matched']!r})"})
        else:
            blocks.append({"check": "QA-DRAFT-LINT", "detail": "no draft produced"})

        # --- 4. the 48-hour regulatory clock ---------------------------------
        check("QA-REG-CLOCK")
        if case.regulatory_ids and not case.sla.get("compliance_flag_due"):
            blocks.append({"check": "QA-REG-CLOCK", "detail":
                           f"strong regulatory finding(s) {case.regulatory_ids} but no "
                           "48-hour compliance clock was set"})
        if case.regulatory_ids and self.cfg.policy["escalation"]["regulatory_hit_forces_compliance_queue"]:
            warnings.append({"check": "QA-REG-CLOCK", "detail":
                             "routed to the Compliance queue in parallel with the owning "
                             f"department ({', '.join(case.regulatory_ids)})"})

        # --- 5. completeness -------------------------------------------------
        check("QA-COMPLETE")
        for field in ("redaction", "severity", "routing", "draft"):
            if getattr(case, field) is None:
                blocks.append({"check": "QA-COMPLETE", "detail": f"missing {field}"})

        # --- 6. confidence floors --------------------------------------------
        check("QA-CONFIDENCE")
        if case.routing and case.routing.confidence < thresholds["routing_autoaccept"]:
            warnings.append({"check": "QA-CONFIDENCE", "detail":
                             f"routing confidence {case.routing.confidence:.2f} < "
                             f"{thresholds['routing_autoaccept']} — analyst must confirm the department"})
        if case.severity and case.severity.confidence < thresholds["severity_autoaccept"]:
            warnings.append({"check": "QA-CONFIDENCE", "detail":
                             f"severity confidence {case.severity.confidence:.2f} < "
                             f"{thresholds['severity_autoaccept']} — score sits near a band edge"})
        if case.draft and case.draft.confidence < thresholds["draft_autoattach"]:
            warnings.append({"check": "QA-CONFIDENCE", "detail":
                             f"draft confidence {case.draft.confidence:.2f} — "
                             "attached for reference, not as a starting point"})

        # --- 7. channel ------------------------------------------------------
        check("QA-CHANNEL")
        if case.human.get("unknown_channel"):
            if self.cfg.policy["escalation"]["unknown_channel_forces_human"]:
                blocks.append({"check": "QA-CHANNEL", "detail":
                               f"unrecognised channel {case.channel!r} — normalisation unverified"})

        # --- 8. calibration --------------------------------------------------
        check("QA-CALIBRATION")
        if case.calibration_flag:
            warnings.append({"check": "QA-CALIBRATION", "detail": case.calibration_note})

        # --- 9. case id in the letter ----------------------------------------
        check("QA-CASEREF")
        if case.draft and case.case_id not in case.draft.body:
            blocks.append({"check": "QA-CASEREF", "detail":
                           "draft does not quote its own case id — a customer reply "
                           "would not be matchable to the case"})

        passed = not blocks
        case.qa = QAResult(passed=passed, blocks=blocks, warnings=warnings, checks_run=checks)
        case.state = "PENDING_HUMAN_APPROVAL" if passed else "BLOCKED_QA"
        case.human["status"] = "awaiting_approval" if passed else "awaiting_rework"

        return (
            ("PASS" if passed else "BLOCKED") + f" — {len(blocks)} block, {len(warnings)} warn, "
            f"{len(checks)} checks",
            1.0 if passed else 0.0,
            {"passed": passed, "blocks": blocks, "warnings": warnings, "checks_run": checks,
             "terminal_state": case.state,
             "gate_assertion": "no transition to APPROVED_FOR_SEND exists in the pipeline"},
        )
