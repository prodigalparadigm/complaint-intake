"""Claude interpretation engine — optional, and off by default.

What it is for: the deterministic engine reads keywords. A customer who writes
"I was told the account had no monthly charge and then I found one" has made a
Reg DD complaint without using any word in the trigger list. A model reads that
correctly. That is the entire case for this engine.

What it is NOT allowed to do, enforced structurally rather than by instruction:

  * It never sees raw complaint text. `build_request()` takes redacted text,
    and `_egress_guard()` re-scans the finished payload against the PII vault
    and raises before any socket is opened. A redaction bug becomes a crash,
    not a disclosure.
  * It returns interpretation only — candidate regulatory categories, dimension
    levels with verbatim evidence, department vocabulary scores, and an
    extractive summary. Severity bands, SLA clocks, routing ownership and the
    human-approval gate are computed by `mhb.agents` from config, identically
    to the rules engine.

Status in this prototype: WRITTEN, NOT EXERCISED. The build host had no API
credentials, so no number in docs/EVIDENCE.md comes from this path. The
offline parts are tested — `build_request` and `_egress_guard` are pure and
tests/test_egress.py asserts the outbound payload carries no PII. Turning this
engine on is a client decision, not an engineering one: see docs/DECISIONS.md
Q1, which asks whether redacted text may leave the building at all.

Requires `anthropic>=0.104` (for `output_config`) and credentials via
ANTHROPIC_API_KEY or `ant auth login`.
"""
from __future__ import annotations

import json
import re

from ..models import DIMENSIONS, RegulatoryFinding, Signal
from . import RegHit

MODEL = "claude-opus-5"

SYSTEM = """You are a complaint-reading assistant inside a US regional bank's \
complaint intake pipeline. You read a single customer complaint and report what \
it says. You do not decide what the bank does about it.

Rules you must follow:

1. The text has already been de-identified. Placeholders like [ACCT_1], \
[NAME_2] or [PHONE_1] stand in for real values. Treat them as opaque. Never \
guess what is behind one, and never ask for it.
2. Every `evidence` field must be a VERBATIM span copied from the complaint. \
If you cannot quote it, the level is 0 and the evidence is "not stated".
3. Report what the customer asserts, not whether you believe it. "The customer \
says the charge was unauthorized" is in scope. "The charge was probably \
legitimate" is not.
4. For regulatory categories, favour recall. Listing a category that turns out \
not to apply costs a compliance analyst two minutes. Missing one costs a \
statutory deadline. If a category is plausibly in play, list it.
5. Do not write anything addressed to the customer. You are not drafting a reply."""

LEVEL_GUIDE = {
    "financial_harm": "0 no money at stake; 1 under $100 or already refunded; 2 $100-$1000 unresolved; 3 over $1000 unresolved or funds inaccessible",
    "vulnerability": "0 none; 1 mild hardship language; 2 fixed income/elderly/disability/bereavement; 3 cannot meet an essential obligation now (rent, food, medication, utilities)",
    "regulatory_exposure": "0 none; 1 adjacent to a regulated practice; 2 regulated practice implicated; 3 a statutory clock is running",
    "recurrence": "0 first contact; 1 second; 2 third; 3 fourth or more, or a prior complaint went unresolved",
    "duration_unresolved": "0 under a week; 1 one to two weeks; 2 two to six weeks; 3 over six weeks",
    "escalation_signals": "0 none; 1 frustration or threat to leave; 2 attorney or BBB mentioned; 3 a regulator or the media has been contacted",
    "relationship_value": "0 unknown; 1 standard; 2 long tenure or multi-product; 3 flagged relationship",
}


def _schema(category_ids: list[str], department_ids: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "issue_summary": {
                "type": "string",
                "description": "One extractive clause, under 160 characters, reusing the customer's own words, describing what the complaint is about. No paraphrase.",
            },
            "regulatory_candidates": {
                "type": "array",
                "description": "Every regulated practice plausibly in play. Favour recall.",
                "items": {
                    "type": "object",
                    "properties": {
                        "category_id": {"type": "string", "enum": category_ids},
                        "evidence": {"type": "string", "description": "Verbatim span from the complaint."},
                        "strength": {"type": "string", "enum": ["strong", "weak"],
                                     "description": "weak if the trigger appears inside a negation or hypothetical."},
                    },
                    "required": ["category_id", "evidence", "strength"],
                    "additionalProperties": False,
                },
            },
            "dimensions": {
                "type": "object",
                "properties": {
                    d: {
                        "type": "object",
                        "properties": {
                            "level": {"type": "integer", "minimum": 0, "maximum": 3},
                            "evidence": {"type": "string"},
                        },
                        "required": ["level", "evidence"],
                        "additionalProperties": False,
                    }
                    for d in DIMENSIONS
                },
                "required": list(DIMENSIONS),
                "additionalProperties": False,
            },
            "department_scores": {
                "type": "array",
                "description": "Departments whose subject matter the complaint touches, strongest first.",
                "items": {
                    "type": "object",
                    "properties": {
                        "department_id": {"type": "string", "enum": department_ids},
                        "score": {"type": "number", "minimum": 0, "maximum": 1},
                        "evidence": {"type": "string"},
                    },
                    "required": ["department_id", "score", "evidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["issue_summary", "regulatory_candidates", "dimensions", "department_scores"],
        "additionalProperties": False,
    }


class EgressViolation(RuntimeError):
    """Raised when a payload bound for the API contains a value from the PII
    vault. This is a bug-stops-the-line control, not a warning."""


class ClaudeEngine:
    name = "claude"
    version = "1.0.0"

    def __init__(self, cfg, client=None, model: str = MODEL, effort: str = "medium"):
        self.cfg = cfg
        self.model = model
        self.effort = effort
        self._client = client
        self._cache: dict[str, dict] = {}
        self._category_ids = [c["id"] for c in cfg.regulatory["categories"]]
        self._department_ids = [d["id"] for d in cfg.routing["departments"]]

    # ------------------------------------------------------- request shaping

    def build_request(self, text: str, metadata: dict) -> dict:
        """Pure. Returns the exact kwargs that would be sent. Tested offline."""
        context = {
            "channel": metadata.get("channel", "unknown"),
            "prior_contacts_on_record": metadata.get("prior_contacts", 0),
            "days_open": metadata.get("days_open"),
            "tenure_years": metadata.get("tenure_years"),
            "product_count": metadata.get("product_count"),
        }
        guide = "\n".join(f"  {k}: {v}" for k, v in LEVEL_GUIDE.items())
        user = (
            "Structured account context (already de-identified):\n"
            f"{json.dumps(context, indent=2)}\n\n"
            "Dimension level definitions — use these exactly:\n"
            f"{guide}\n\n"
            "De-identified complaint text:\n"
            "<complaint>\n"
            f"{text}\n"
            "</complaint>"
        )
        return {
            "model": self.model,
            "max_tokens": 4096,
            "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": self.effort,
                "format": {
                    "type": "json_schema",
                    "schema": _schema(self._category_ids, self._department_ids),
                },
            },
            "messages": [{"role": "user", "content": user}],
        }

    # --------------------------------------------------------- egress guard

    @staticmethod
    def _egress_guard(request: dict, forbidden: set[str]) -> None:
        blob = json.dumps(request)
        for value in forbidden:
            v = str(value)
            if len(v) < 4:
                continue
            if v in blob:
                raise EgressViolation(
                    f"refusing to send: payload contains a vault value "
                    f"({v[:2]}***{v[-2:]}, {len(v)} chars). Redaction did not hold."
                )

    # ---------------------------------------------------------------- client

    def _ensure_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("engine 'claude' needs: pip install 'anthropic>=0.104'") from exc
            self._client = anthropic.Anthropic()
        return self._client

    def _interpret(self, text: str, metadata: dict) -> dict:
        key = f"{hash(text)}:{sorted(metadata.items())!r}"
        if key in self._cache:
            return self._cache[key]

        from .. import store

        request = self.build_request(text, metadata)
        self._egress_guard(request, store.all_vault_values())

        response = self._ensure_client().messages.create(**request)
        if getattr(response, "stop_reason", None) == "refusal":
            raise RuntimeError(
                "model declined to read this complaint "
                f"({getattr(response, 'stop_details', None)}); route to a human analyst"
            )
        raw = next((b.text for b in response.content if b.type == "text"), "{}")
        parsed = json.loads(raw)
        self._cache[key] = parsed
        return parsed

    # ------------------------------------------------------- Engine protocol

    def detect_regulatory(self, text: str) -> list[RegHit]:
        data = self._interpret(text, {})
        return [
            RegHit(c["category_id"], c["evidence"], c.get("strength", "strong"))
            for c in data.get("regulatory_candidates", [])
            if c["category_id"] in self._category_ids
        ]

    def extract_signals(
        self, text: str, metadata: dict, regulatory: list[RegulatoryFinding]
    ) -> list[Signal]:
        data = self._interpret(text, metadata)
        dims = data.get("dimensions", {})
        out: list[Signal] = []
        for d in DIMENSIONS:
            got = dims.get(d) or {}
            level = max(0, min(3, int(got.get("level", 0))))
            out.append(Signal(d, level, f"CL-{d.upper()}", str(got.get("evidence", "not stated"))[:300]))
        # The regulatory_exposure dimension is reconciled with what the
        # regulatory agent actually accepted, so the model cannot talk the
        # severity score up past Compliance's own threshold.
        strong = [r for r in regulatory if r.strength == "strong"]
        ceiling = max((r.exposure for r in strong), default=0)
        for s in out:
            if s.dimension == "regulatory_exposure" and s.level > ceiling:
                s.level = ceiling
                s.evidence += f" [capped to accepted exposure {ceiling}]"
        return out

    def score_departments(self, text: str) -> list[tuple[str, float, str]]:
        data = self._interpret(text, {})
        scored = [
            (d["department_id"], float(d["score"]) * 10.0, str(d.get("evidence", ""))[:120])
            for d in data.get("department_scores", [])
            if d["department_id"] in self._department_ids
        ]
        scored.sort(key=lambda t: -t[1])
        return scored

    def summarize_issue(self, text: str) -> str:
        s = str(self._interpret(text, {}).get("issue_summary", "")).strip()
        s = re.sub(r"\s+", " ", s)
        return s[:180] or "the issue you raised"
