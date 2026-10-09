"""Severity agent — and the honest answer to "what does serious mean?"

Nobody at Meridian Harbor could define "serious", and the two senior analysts
disagreed about it in front of us. We did not resolve that by picking one. A
system that quietly encodes one analyst's instincts and presents the output as
objective is how you get a triage model nobody trusts and everybody overrides.

So this agent does something different. It separates:

  * OBSERVATION — the seven dimensions, scored 0-3 with quoted evidence. Both
    analysts agree these are the things that matter. This part is automatable.
  * VALUATION — the weights, band cutoffs and override rules that turn those
    observations into "serious". This part is a policy artifact the bank owns,
    and it is contested.

Then it runs BOTH rubrics on every complaint, reports the primary band, and
raises `calibration_flag` wherever the two disagree by enough bands to change
what an analyst would do. Those flagged cases are the agenda for the one-hour
meeting that actually settles the definition — concrete complaints where the
two readings diverge, instead of an abstract argument about the word.

The deliverable here is not a severity score. It is a measured disagreement
rate and a worklist. See `mhb calibrate`.
"""
from __future__ import annotations

from ..models import Case, SeverityAssessment, Signal
from .base import Agent

BAND_ORDER = ["S1", "S2", "S3", "S4"]


def _band_index(band: str) -> int:
    return BAND_ORDER.index(band)


class SeverityAgent(Agent):
    name = "severity"
    version = "1.2.0"

    def ruleset_ids(self):
        return [self.cfg.rubric_primary["rubric_id"], self.cfg.rubric_secondary["rubric_id"]]

    # ---------------------------------------------------------------- scoring

    def apply_rubric(self, rubric: dict, signals: list[Signal]) -> SeverityAssessment:
        levels = {s.dimension: s.level for s in signals}
        evidence = {s.dimension: s.evidence for s in signals}

        raw, max_possible, rationale = 0.0, 0.0, []
        for dim, spec in rubric["dimensions"].items():
            w = float(spec["weight"])
            lvl = int(levels.get(dim, 0))
            raw += w * lvl
            max_possible += w * 3
            if lvl:
                rationale.append(
                    f"{dim}={lvl} x w{w:g} = {w * lvl:.1f}  ({evidence.get(dim, '')})"
                )
        normalized = raw / max_possible if max_possible else 0.0

        band = BAND_ORDER[-1]
        band_label = ""
        for b in rubric["bands"]:
            if normalized >= b["min_normalized"]:
                band, band_label = b["id"], b["label"]
                break

        applied = []
        for ov in rubric.get("overrides", []):
            cond = ov["when"]
            if cond.startswith("dimension:"):
                expr = cond.split(":", 1)[1]
                dim, _, threshold = expr.partition(">=")
                if levels.get(dim.strip(), 0) >= int(threshold):
                    forced = ov["force_min_band"]
                    if _band_index(forced) < _band_index(band):
                        applied.append(
                            {"id": ov["id"], "from": band, "to": forced, "reason": ov["reason"]}
                        )
                        band = forced
                        band_label = next(
                            (b["label"] for b in rubric["bands"] if b["id"] == forced), ""
                        )
                    else:
                        applied.append(
                            {"id": ov["id"], "from": band, "to": band,
                             "reason": ov["reason"] + " (already at or above this band)"}
                        )

        # Confidence reflects how close the score sits to a band edge. A case
        # at 0.479 against a 0.480 cutoff is a coin flip and should say so
        # rather than present a band as settled.
        #
        # The slope matters. An earlier version used 0.55 + 4.0 * margin, which
        # saturated at the 0.95 ceiling for any case more than 0.1 away from a
        # cutoff — so nearly every case reported maximum confidence and the
        # number carried no information. 2.0 keeps the scale spread across the
        # range the band widths actually occupy.
        cutoffs = [b["min_normalized"] for b in rubric["bands"]]
        margin = min((abs(normalized - c) for c in cutoffs if c > 0), default=0.5)
        confidence = round(0.50 + min(0.45, 2.0 * margin), 2)
        if applied and any(a["from"] != a["to"] for a in applied):
            # A categorical override is a rule, not a score. It is genuinely
            # more certain than anything the weighted sum can express.
            confidence = max(confidence, 0.92)

        return SeverityAssessment(
            rubric_id=rubric["rubric_id"],
            rubric_label=rubric["label"],
            dimension_levels=levels,
            raw_score=round(raw, 2),
            normalized=round(normalized, 4),
            band=band,
            band_label=band_label,
            overrides_applied=applied,
            confidence=confidence,
            rationale=rationale,
        )

    # ----------------------------------------------------------------- handle

    def handle(self, case: Case):
        signals = self.engine.extract_signals(
            case.redacted_text, case.human.get("metadata", {}) or {}, case.regulatory
        )
        case.signals = signals

        primary = self.apply_rubric(self.cfg.rubric_primary, signals)
        secondary = self.apply_rubric(self.cfg.rubric_secondary, signals)
        case.severity = primary
        case.severity_alt = secondary

        gap = abs(_band_index(primary.band) - _band_index(secondary.band))
        threshold = self.cfg.policy["escalation"]["rubric_disagreement_bands_forces_calibration_flag"]
        case.calibration_flag = gap >= threshold
        if case.calibration_flag:
            case.calibration_note = (
                f"Rubric A says {primary.band}, Rubric B says {secondary.band}. "
                "The two senior analysts would triage this complaint differently. "
                "Needs a human decision, and belongs on the calibration agenda."
            )
        elif gap == 1:
            case.calibration_note = (
                f"Minor divergence: A={primary.band}, B={secondary.band}. Within tolerance."
            )

        case.state = "TRIAGED"
        return (
            f"{primary.band} ({primary.rubric_id}, n={primary.normalized:.2f}); "
            f"alt {secondary.band} ({secondary.rubric_id}); "
            f"{'CALIBRATION FLAG' if case.calibration_flag else 'rubrics agree'}",
            primary.confidence,
            {
                "primary": {"rubric": primary.rubric_id, "band": primary.band,
                            "normalized": primary.normalized, "overrides": primary.overrides_applied},
                "secondary": {"rubric": secondary.rubric_id, "band": secondary.band,
                              "normalized": secondary.normalized, "overrides": secondary.overrides_applied},
                "band_gap": gap,
                "calibration_flag": case.calibration_flag,
                "dimension_levels": primary.dimension_levels,
                "evidence": {s.dimension: s.evidence for s in signals},
            },
        )
