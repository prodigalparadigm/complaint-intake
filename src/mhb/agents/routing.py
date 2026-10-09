"""Routing agent — which department owns this.

Two inputs, in priority order:

  1. Ownership rules from the ops head (config/routing_map.json tiebreakers).
     "Reg E disputes are worked by Cards & Disputes regardless of channel" is a
     management decision about who owns work. No amount of text similarity
     should be able to override it, so these are applied first and recorded.
  2. Subject-matter vocabulary, scored by the engine.

Confidence is the margin between the top department and the runner-up, not the
absolute score. A complaint that mentions both "overdraft fee" and "mobile app
crashed" genuinely has two owners, and the right behaviour is to say so and let
a human choose rather than to pick the higher number silently.
"""
from __future__ import annotations

from ..models import Case, RoutingDecision
from .base import Agent


class RoutingAgent(Agent):
    name = "routing"
    version = "1.1.0"

    def ruleset_ids(self):
        return [self.cfg.routing["ruleset_id"]]

    def _dept(self, dept_id: str) -> dict:
        for d in self.cfg.routing["departments"]:
            if d["id"] == dept_id:
                return d
        raise KeyError(dept_id)

    def decide(self, text: str, regulatory_ids: list[str]) -> RoutingDecision:
        scored = self.engine.score_departments(text)

        # 1. Ownership tiebreakers. Management's rules win — but when a rule
        # sends a case somewhere the text does not point, say so instead of
        # reporting a confident answer.
        for rule in self.cfg.routing["tiebreakers"]["rules"]:
            if rule["if_regulatory"] in regulatory_ids:
                d = self._dept(rule["then_department"])
                alts = [
                    {"department": i, "score": round(s, 2), "evidence": e}
                    for i, s, e in scored[:3]
                    if i != d["id"]
                ]
                reason = f"ownership rule: {rule['reason']}"
                confidence = 0.95
                conflict = None
                if scored:
                    top_id, top_score, top_ev = scored[0]
                    own_score = next((s for i, s, _ in scored if i == d["id"]), 0.0)
                    if top_id != d["id"] and top_score > max(own_score * 1.5, 2.0):
                        conflict = top_id
                        confidence = 0.55
                        reason += (
                            f" — BUT the complaint's own vocabulary points to {top_id} "
                            f"({top_ev}; {top_score:.1f} vs {own_score:.1f}). The rule as written "
                            "may be too broad for this case; an analyst should confirm."
                        )
                return RoutingDecision(
                    department=d["id"],
                    department_label=d["label"],
                    queue_email=d["queue_email"],
                    confidence=confidence,
                    alternates=alts,
                    reason=reason,
                    tiebreaker_applied=rule["if_regulatory"]
                    + (f" [conflict:{conflict}]" if conflict else ""),
                )

        # 2. Vocabulary scoring.
        if not scored:
            d = self._dept(self.cfg.routing["fallback"])
            return RoutingDecision(
                department=d["id"],
                department_label=d["label"],
                queue_email=d["queue_email"],
                confidence=0.25,
                alternates=[],
                reason="no departmental vocabulary matched — fallback queue, needs a human read",
            )

        top_id, top_score, top_ev = scored[0]
        second = scored[1][1] if len(scored) > 1 else 0.0
        margin = (top_score - second) / top_score if top_score else 0.0
        confidence = round(min(0.95, 0.45 + 0.5 * margin), 2)

        d = self._dept(top_id)
        return RoutingDecision(
            department=d["id"],
            department_label=d["label"],
            queue_email=d["queue_email"],
            confidence=confidence,
            alternates=[
                {"department": i, "score": round(s, 2), "evidence": e} for i, s, e in scored[1:4]
            ],
            reason=f"vocabulary match on: {top_ev}"
            + (
                f" (close call — {scored[1][0]} scored {second:.1f} vs {top_score:.1f})"
                if margin < 0.25 and len(scored) > 1
                else ""
            ),
        )

    def handle(self, case: Case):
        decision = self.decide(case.redacted_text, case.regulatory_ids)
        case.routing = decision
        case.state = "ROUTED"
        threshold = self.cfg.policy["confidence_thresholds"]["routing_autoaccept"]
        return (
            f"{decision.department} @ {decision.confidence:.2f}"
            + ("" if decision.confidence >= threshold else "  [BELOW THRESHOLD — human picks]"),
            decision.confidence,
            {
                "department": decision.department,
                "confidence": decision.confidence,
                "threshold": threshold,
                "below_threshold": decision.confidence < threshold,
                "alternates": decision.alternates,
                "reason": decision.reason,
                "tiebreaker_applied": decision.tiebreaker_applied,
            },
        )
