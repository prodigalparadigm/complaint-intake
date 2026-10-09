"""Drafting agent — writes the acknowledgement, and nothing else.

The compliance officer killed the last vendor pilot over customer-facing text.
Taking that seriously meant narrowing the job rather than solving it better:

  * The agent SELECTS a compliance-approved template and FILLS slots. It does
    not compose prose. The only sentences that can reach a customer are the
    ones sitting in config/response_templates.json, readable as flat text by
    someone who does not read Python.
  * The {{ISSUE_SUMMARY}} slot is extractive — the customer's own clause. A
    paraphrase would be a new assertion about what the customer said, which is
    the wrong thing to put in an acknowledgement letter.
  * It drafts an ACKNOWLEDGEMENT only. It never states an outcome, a refund, a
    fault finding, or a resolution date. Those are the analyst's to write once
    the investigation is done, and the lint rules block the vocabulary.
  * {{CUSTOMER_NAME}} is left unfilled on purpose. The draft is de-identified
    even in the review queue; the name is merged in at send time from the
    system of record.

What this is worth to the client: it is not "AI writes our letters". It is
"the blank page and the boilerplate are already done, the case number is
already right, and the analyst starts from a draft instead of a cursor."
"""
from __future__ import annotations

import re

from .. import clock
from ..models import Case, DraftResponse
from .base import Agent


class DraftingAgent(Agent):
    name = "drafting"
    version = "1.2.0"

    def ruleset_ids(self):
        return [self.cfg.templates["ruleset_id"], self.cfg.lint["ruleset_id"]]

    # ------------------------------------------------------- template choice

    def select_template(self, case: Case) -> dict:
        strong = set(case.regulatory_ids)
        levels = (case.severity.dimension_levels if case.severity else {}) or {}
        default = None
        for t in self.cfg.templates["templates"]:
            rule = t["applies_to"]
            if rule.get("default"):
                default = t
                continue
            if set(rule.get("regulatory_any", [])) & strong:
                return t
            md = rule.get("min_dimension") or {}
            if md and all(levels.get(k, 0) >= v for k, v in md.items()):
                return t
        return default

    # ------------------------------------------------------------- slot fill

    def _next_step(self, case: Case) -> str:
        band = case.severity.band if case.severity else "S4"
        resolve_due = case.sla.get("resolve_due")
        when = resolve_due[:10] if resolve_due else "the date shown in your case record"
        if band == "S1":
            return (
                f"Because of what you have described, your case has been placed with a named "
                f"owner rather than a general queue, and you should expect to hear from that "
                f"person directly. We are working to {when}."
            )
        if band == "S2":
            return f"We are working to {when} and will write to you by then, even if only to tell you where the review stands."
        return f"We are working to {when}. If you need an update before then, reply to this message with your case number."

    def issue_topic(self, case: Case) -> str:
        """What the letter says the complaint is about.

        A compliance-approved topic phrase, chosen by the strongest regulatory
        finding and falling back to the owning department. Deliberately NOT the
        customer's own sentence spliced into the template: that produced broken
        grammar and pushed redaction placeholders into the letter. The analyst
        still sees the customer's own words — `analyst_summary` below carries
        the extractive version into the console, where it belongs."""
        topics = self.cfg.templates["issue_topics"]
        for cat in sorted(
            (f for f in case.regulatory if f.strength == "strong"),
            key=lambda f: -f.exposure,
        ):
            if cat.category_id in topics["by_regulatory"]:
                return topics["by_regulatory"][cat.category_id]
        if case.routing:
            return topics["by_department"].get(case.routing.department, topics["fallback"])
        return topics["fallback"]

    def fill(self, case: Case, template: dict) -> tuple[str, str, list[str]]:
        issue = self.issue_topic(case)
        amounts = re.findall(r"\$\s?[0-9][0-9,]*(?:\.[0-9]{2})?", case.redacted_text)
        values = {
            "{{CASE_ID}}": case.case_id,
            "{{ISSUE_SUMMARY}}": issue,
            "{{DEPARTMENT_LABEL}}": case.routing.department_label if case.routing else "Customer Service",
            "{{NEXT_STEP}}": self._next_step(case),
            "{{AMOUNT}}": amounts[0] if amounts else "",
            "{{DEADLINE_DATE}}": (case.sla.get("resolve_due") or "")[:10],
        }
        subject, body = template["subject"], template["body"]
        used = []
        for slot, val in values.items():
            if slot in subject or slot in body:
                used.append(slot)
            subject = subject.replace(slot, str(val))
            body = body.replace(slot, str(val))
        body = re.sub(r"\n{3,}", "\n\n", body)
        return subject, body, used

    # ------------------------------------------------------------------ lint

    def lint(self, subject: str, body: str) -> list[dict]:
        text = f"{subject}\n{body}"
        deferred = set(self.cfg.templates.get("deferred_slots", []))
        out: list[dict] = []
        for severity_key in ("block", "warn"):
            for rule in self.cfg.lint[severity_key]:
                for m in re.finditer(rule["pattern"], text, re.I | re.M):
                    matched = m.group(0)
                    if rule["id"] == "L-UNFILLED" and matched in deferred:
                        continue  # resolved at send time, by design
                    if rule["id"] == "L-PII" and matched in case_safe_digits(text, m.start()):
                        continue
                    out.append(
                        {
                            "rule": rule["id"],
                            "severity": severity_key,
                            "why": rule["why"],
                            "matched": matched[:60],
                        }
                    )
                    break  # one finding per rule is enough to act on
        return out

    def handle(self, case: Case):
        template = self.select_template(case)
        subject, body, used = self.fill(case, template)
        findings = self.lint(subject, body)
        # Analyst-facing only: the customer's own opening clause, so the
        # reviewer sees what was said without opening the full complaint.
        case.human["analyst_summary"] = self.engine.summarize_issue(case.redacted_text)
        blocks = [f for f in findings if f["severity"] == "block"]
        confidence = round(max(0.2, 0.9 - 0.25 * len(blocks) - 0.05 * (len(findings) - len(blocks))), 2)

        case.draft = DraftResponse(
            template_id=template["id"],
            subject=subject,
            body=body,
            placeholders_used=used,
            lint_findings=findings,
            confidence=confidence,
        )
        case.state = "DRAFTED"
        return (
            f"template {template['id']}; {len(blocks)} block / "
            f"{len(findings) - len(blocks)} warn lint finding(s)",
            confidence,
            {
                "template_id": template["id"],
                "slots_filled": used,
                "deferred_slots": self.cfg.templates.get("deferred_slots", []),
                "lint": findings,
                "chars": len(body),
            },
        )


def case_safe_digits(text: str, pos: int) -> set[str]:
    """The L-PII rule looks for bare long digit runs in customer-facing text.
    A case id like MHB-6A3F1C02 contains no such run, but a filled date or a
    dollar amount could; this keeps the rule from firing on them."""
    window = text[max(0, pos - 2) : pos + 24]
    out = set()
    m = re.search(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{2})?)", window)
    if m:
        out.add(re.sub(r"\D", "", m.group(1)))
    return out
