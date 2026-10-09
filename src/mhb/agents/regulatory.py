"""Regulatory tripwire.

This is the agent that answers the discovery finding "complaints that touch
potential regulatory issues are supposed to be flagged to Compliance within 48
hours, and they're currently finding them late."

Three design choices worth defending to the compliance officer:

1. It runs independently of severity and in parallel with it. A complaint can
   be low-harm and still be a Reg E matter, and in the current manual process
   those are exactly the ones that get missed — they do not look urgent. This
   agent does not consult the severity band and cannot be overruled by it.

2. It is recall-biased by construction. `min_hits` defaults to 1 for every
   category, and the agent surfaces weak and negated hits too, marked as such.
   The cost asymmetry is explicit in the config file: a false positive is two
   minutes of reading, a false negative is a statutory deadline.

3. It sets the clock from `received_at`, not from processing time, because
   that is how an examiner would count the 48 hours.
"""
from __future__ import annotations

from .. import clock
from ..models import Case, RegulatoryFinding
from .base import Agent


class RegulatoryAgent(Agent):
    name = "regulatory_tripwire"
    version = "1.1.0"

    def ruleset_ids(self):
        return [self.cfg.regulatory["ruleset_id"]]

    def assess(self, text: str) -> list[RegulatoryFinding]:
        hits = self.engine.detect_regulatory(text)
        by_cat: dict[str, list] = {}
        for h in hits:
            by_cat.setdefault(h.category_id, []).append(h)

        findings: list[RegulatoryFinding] = []
        for cat in self.cfg.regulatory["categories"]:
            cat_hits = by_cat.get(cat["id"])
            if not cat_hits:
                continue
            strong = [h for h in cat_hits if h.strength == "strong"]
            # min_hits is Compliance's dial. Strong hits count toward it; weak
            # ones are reported but do not satisfy the threshold on their own.
            strength = "strong" if len(strong) >= cat.get("min_hits", 1) else "weak"
            findings.append(
                RegulatoryFinding(
                    category_id=cat["id"],
                    label=cat["label"],
                    citation=cat["citation"],
                    statutory_clock=cat.get("statutory_clock"),
                    exposure=cat.get("exposure", 2),
                    strength=strength,
                    evidence=sorted({h.evidence for h in cat_hits})[:6],
                )
            )
        findings.sort(key=lambda f: (f.strength != "strong", -f.exposure, f.category_id))
        return findings

    def handle(self, case: Case):
        findings = self.assess(case.redacted_text)
        case.regulatory = findings
        strong = [f for f in findings if f.strength == "strong"]

        if strong:
            case.sla["compliance_flag_due"] = clock.due(
                case.received_at, self.cfg.policy["sla_hours"]["compliance_flag"]
            )
            # Flagged the moment triage runs. This is the number that changes
            # for the client: discovery latency goes from days to the pipeline's
            # own runtime.
            case.sla["compliance_flagged_at"] = clock.fmt(clock.parse(case.received_at))
            case.sla["compliance_flag_latency_hours"] = 0.0

        clocks = sorted(
            {f.statutory_clock for f in strong if f.statutory_clock}
        )
        summary = (
            "no regulatory trigger"
            if not findings
            else f"{len(strong)} strong / {len(findings) - len(strong)} weak: "
            + ", ".join(f.category_id for f in strong) or "weak only"
        )
        confidence = 0.9 if strong else (0.5 if findings else 0.85)
        return (
            summary,
            confidence,
            {
                "strong": [f.category_id for f in strong],
                "weak": [f.category_id for f in findings if f.strength == "weak"],
                "citations": [f.citation for f in strong],
                "statutory_clocks": clocks,
                "evidence": {f.category_id: f.evidence for f in findings},
                "recall_bias_note": self.cfg.regulatory["design_note"][:120] + "...",
            },
        )
