"""Redaction agent — the hard boundary.

Policy at Meridian Harbor is that no un-redacted complaint text leaves their
environment. This agent is where that policy becomes a property of the code
rather than a promise in a deck:

  * It runs second, immediately after normalisation, before any other agent
    sees the text. No code path reaches severity, routing or drafting with raw
    text in hand.
  * Original values go to `vault/`, a separate gitignored directory. The case
    file gets placeholders only.
  * It reports what it is unsure about. `residual_risk_notes` carries the
    things that look like identifiers but did not match a rule — unusual name
    forms, long digit runs in odd formats. Those notes are surfaced to the
    analyst in the review console. A redactor that silently believes it caught
    everything is more dangerous than one that admits a gap.

Known limits, stated here because they belong in the code and not only in the
deck: person names without a title are detected weakly, and a customer who
writes their account number as "four two one nine, seven seven" in a call note
will not be caught by a digit pattern. Both are in docs/EVIDENCE.md with
measured numbers.
"""
from __future__ import annotations

import hashlib
import re

from .. import store
from ..models import Case, PIIFinding, RedactionResult
from .base import Agent

# Capitalised bigrams that are probably a person. Used only to raise a
# residual-risk note, never to redact — the false-positive rate on bank names,
# branch names and product names is far too high to act on.
NAME_SHAPE = re.compile(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b")
NAME_ALLOW = {
    "Meridian Harbor", "Harbor Bank", "Customer Service", "Branch Manager",
    "Online Banking", "Mobile Deposit", "Direct Deposit", "Credit Card",
    "Debit Card", "Savings Account", "Checking Account", "Bill Pay",
    "Better Business", "Business Bureau", "Attorney General", "Social Security",
    "Consumer Financial", "Financial Protection", "Protection Bureau",
    "Main Street", "Last Week", "Next Day", "Thank You", "Dear Sir",
}
DIGIT_RUN = re.compile(r"\b[\d][\d\s\-.]{7,}[\d]\b")


def luhn(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total, parity = 0, len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def aba(s: str) -> bool:
    d = [int(c) for c in s if c.isdigit()]
    if len(d) != 9:
        return False
    checksum = (
        3 * (d[0] + d[3] + d[6]) + 7 * (d[1] + d[4] + d[7]) + (d[2] + d[5] + d[8])
    )
    return checksum % 10 == 0


VALIDATORS = {"luhn": luhn, "aba": aba, None: lambda s: True}


class RedactionAgent(Agent):
    name = "redaction"
    version = "1.2.0"

    def __init__(self, cfg, engine=None):
        super().__init__(cfg, engine)
        self.rules = [
            {**r, "rx": re.compile(r["pattern"], re.I if r["id"] != "PERSON" else 0)}
            for r in cfg.redaction["rules"]
        ]

    def ruleset_ids(self):
        return [self.cfg.redaction["ruleset_id"]]

    def redact(self, text: str, subject: str, case_id: str) -> RedactionResult:
        combined = f"{subject}\n\x00\n{text}" if subject else text

        # Collect candidates in rule order; first rule to claim a span wins, so
        # a 16-digit PAN is tokenised as a card and not chopped up by the
        # bare-account rule that follows it.
        claimed: list[tuple[int, int]] = []
        findings: list[PIIFinding] = []
        counters: dict[str, int] = {}
        seen: dict[str, str] = {}

        def overlaps(a: int, b: int) -> bool:
            return any(a < e and b > s for s, e in claimed)

        for rule in self.rules:
            validate = VALIDATORS[rule.get("validator")]
            group = rule.get("group", 0)
            for m in rule["rx"].finditer(combined):
                start, end = m.span(group)
                value = m.group(group)
                if not value or overlaps(start, end):
                    continue
                if not validate(value):
                    continue
                token = rule["token"]
                if value in seen:
                    placeholder = seen[value]
                else:
                    counters[token] = counters.get(token, 0) + 1
                    placeholder = f"[{token}_{counters[token]}]"
                    seen[value] = placeholder
                claimed.append((start, end))
                findings.append(PIIFinding(rule["id"], placeholder, start, end, value))

        for f in sorted(findings, key=lambda f: -f.start):
            combined = combined[: f.start] + f.placeholder + combined[f.end :]

        if subject:
            red_subject, _, red_text = combined.partition("\n\x00\n")
        else:
            red_subject, red_text = "", combined

        vault_id = f"{case_id}-{hashlib.sha256(case_id.encode()).hexdigest()[:6]}"
        store.write_vault(vault_id, {f.placeholder: f.original for f in findings})

        return RedactionResult(
            redacted_text=red_text,
            redacted_subject=red_subject,
            findings=findings,
            vault_id=vault_id,
            residual_risk_notes=self._residual_risks(red_text),
        )

    def _residual_risks(self, redacted: str) -> list[str]:
        notes = []
        names = {
            n for n in NAME_SHAPE.findall(redacted)
            if n not in NAME_ALLOW and not any(a in n for a in NAME_ALLOW)
        }
        if names:
            notes.append(
                "Possible person or place name not covered by a redaction rule: "
                + ", ".join(sorted(names)[:5])
                + " — analyst should confirm before this text is shared outside Retail Ops."
            )
        runs = [r.strip() for r in DIGIT_RUN.findall(redacted)]
        if runs:
            notes.append(
                f"Long digit run(s) surviving redaction ({len(runs)}): "
                + ", ".join(runs[:3])
                + " — may be an identifier in an unexpected format."
            )
        if re.search(r"\b(?:four|five|six|seven|eight|nine|zero|one|two|three)\s+(?:four|five|six|seven|eight|nine|zero|one|two|three)\s+(?:four|five|six|seven|eight|nine|zero|one|two|three)\b", redacted, re.I):
            notes.append(
                "Digits appear to be spelled out in words — digit patterns cannot "
                "catch this. Known gap; see docs/EVIDENCE.md."
            )
        return notes

    def handle(self, case: Case, *, text: str, subject: str):
        result = self.redact(text, subject, case.case_id)
        case.redaction = result.public()
        case.state = "REDACTED"
        kinds = ", ".join(result.public()["pii_kinds"]) or "none"
        return (
            f"redacted {len(result.findings)} identifier(s) [{kinds}]; "
            f"{len(result.residual_risk_notes)} residual-risk note(s)",
            1.0,
            {
                "pii_count": len(result.findings),
                "pii_kinds": result.public()["pii_kinds"],
                "vault_id": result.vault_id,
                "residual_risk_notes": result.residual_risk_notes,
                "boundary_assertion": "downstream agents receive redacted_text only",
            },
        )
