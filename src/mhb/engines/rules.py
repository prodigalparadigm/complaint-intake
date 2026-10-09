"""Deterministic interpretation engine.

Pattern-and-lexicon based. Chosen as the default for three reasons that matter
to this client specifically:

  * It runs with no network call, so the PII question ("does complaint text
    leave our environment?") has the answer "no, not even redacted" on day one.
  * Every output traces to a named rule id, so Compliance can audit a decision
    by reading a config file rather than trusting a probability.
  * It costs nothing per complaint, so the backlog argument does not depend on
    a per-unit inference price the client has not budgeted.

Its ceiling is real and stated plainly in docs/EVIDENCE.md: it reads keywords,
not meaning, and it will miss a politely-worded regulatory complaint that never
uses a trigger word. That gap is the argument for the `claude` engine later —
after the client has answered the egress question.
"""
from __future__ import annotations

import re

from ..models import DIMENSIONS, RegulatoryFinding, Signal
from . import RegHit

MONEY = re.compile(r"\$\s?([0-9][0-9,]*(?:\.[0-9]{2})?)")
_TERM_CACHE: dict[str, "re.Pattern"] = {}

FUNDS_INACCESSIBLE = (
    "frozen", "froze my account", "locked out of my account", "can't access my money",
    "cannot access my money", "account was closed", "hold on my deposit",
    "money is not available", "funds unavailable", "can't get to my money",
)

VULN_ACUTE = (
    "rent is due", "can't pay my rent", "cannot pay my rent", "eviction",
    "can't buy groceries", "cannot buy groceries", "can't buy food", "no food",
    "can't afford my medication", "cannot afford my medication", "out of my medication",
    "my utilities", "shut off my power", "shut off notice", "car will be repossessed",
    "nothing to feed", "no money for gas to get to work",
)
VULN_STATUS = (
    "fixed income", "social security", "disability check", "retired", "i'm 78",
    "i am 78", "i'm 81", "i am 81", "elderly", "my late husband", "my late wife",
    "passed away", "recently widowed", "in hospice", "cancer treatment",
    "lost my job", "unemployed", "single mother", "veteran", "ssi", "ssdi",
)
VULN_MILD = ("struggling", "tight month", "hardship", "paycheck to paycheck", "stressed about money")

RECUR = {
    3: ("fourth time", "fifth time", "four times", "five times", "every month this happens",
        "nobody has ever called me back", "no one has ever called me back"),
    2: ("third time", "three times", "twice now and again", "i have written twice",
        "still no answer after two"),
    1: ("second time", "called again", "writing again", "following up on my complaint",
        "as i said in my last", "already reported this"),
}

DURATION = {
    3: ("six months", "seven months", "eight months", "a year now", "since january",
        "since february", "since march", "over two months"),
    2: ("two months", "six weeks", "a month and a half", "since june", "since july",
        "five weeks", "four weeks", "last month"),
    1: ("two weeks", "ten days", "a week and a half", "eight days", "last week"),
}

ESCALATION = {
    # Level 3 is reserved for a party with jurisdiction or a megaphone. A
    # lawyer or the BBB is level 2: serious, but not a regulator.
    3: ("cfpb", "consumer financial protection", "attorney general", "occ",
        "comptroller of the currency", "fdic", "news station", "local news",
        "reporter", "channel 7"),
    2: ("my lawyer", "my attorney", "legal action", "sue you", "suing", "lawsuit",
        "small claims", "legal counsel", "better business bureau", "bbb"),
    1: ("closing my accounts", "closing my account", "moving my money", "leaving the bank",
        "thirty years of banking", "never banking with you", "done with this bank",
        "tell everyone i know"),
}


def _find(text: str, phrases) -> str | None:
    for p in phrases:
        if p in text:
            return p
    return None


class RulesEngine:
    name = "rules"
    version = "1.0.0"

    def __init__(self, cfg):
        self.cfg = cfg
        self.reg = cfg.regulatory
        self.routing = cfg.routing
        self._negations = [re.compile(p, re.I) for p in self.reg["negation_guards"]["patterns"]]
        self._doc_guards = [
            (re.compile(g["pattern"], re.I), set(g["downgrades"]))
            for g in self.reg.get("document_scope_guards", {}).get("guards", [])
        ]

    # ------------------------------------------------------------ regulatory

    def _negated(self, text: str, idx: int) -> bool:
        """A trigger inside a negating construction is downgraded, not dropped.
        Window is the 90 characters before the hit — enough for 'I am not
        saying this was unauthorized' and short enough to avoid swallowing a
        separate sentence."""
        window = text[max(0, idx - 90): idx + 10]
        return any(rx.search(window) for rx in self._negations)

    def detect_regulatory(self, text: str) -> list[RegHit]:
        low = text.lower()
        # Categories the customer explicitly disclaimed anywhere in the text.
        disclaimed: set[str] = set()
        for rx, cats in self._doc_guards:
            if rx.search(low):
                disclaimed |= cats

        hits: list[RegHit] = []
        for cat in self.reg["categories"]:
            for trig in cat["triggers"]:
                idx = low.find(trig)
                if idx == -1:
                    continue
                if cat["id"] in disclaimed:
                    strength = "weak"
                elif self._negated(low, idx):
                    strength = "weak"
                else:
                    strength = "strong"
                hits.append(RegHit(cat["id"], trig, strength))
        return hits

    # --------------------------------------------------------------- signals

    def extract_signals(
        self, text: str, metadata: dict, regulatory: list[RegulatoryFinding]
    ) -> list[Signal]:
        low = text.lower()
        out: list[Signal] = []

        # financial_harm -------------------------------------------------
        amounts = [float(m.replace(",", "")) for m in MONEY.findall(text)]
        top = max(amounts) if amounts else 0.0
        inacc = _find(low, FUNDS_INACCESSIBLE)
        if inacc:
            out.append(Signal("financial_harm", 3, "FH-INACCESSIBLE", f"funds inaccessible: '{inacc}'"))
        elif top > 1000:
            out.append(Signal("financial_harm", 3, "FH-AMT-HIGH", f"${top:,.2f} at stake"))
        elif top >= 100:
            out.append(Signal("financial_harm", 2, "FH-AMT-MID", f"${top:,.2f} at stake"))
        elif top > 0:
            out.append(Signal("financial_harm", 1, "FH-AMT-LOW", f"${top:,.2f} at stake"))
        else:
            out.append(Signal("financial_harm", 0, "FH-NONE", "no dollar amount stated"))

        # vulnerability --------------------------------------------------
        if (h := _find(low, VULN_ACUTE)):
            out.append(Signal("vulnerability", 3, "VU-ACUTE", f"essential obligation at risk: '{h}'"))
        elif (h := _find(low, VULN_STATUS)):
            out.append(Signal("vulnerability", 2, "VU-STATUS", f"vulnerability indicator: '{h}'"))
        elif (h := _find(low, VULN_MILD)):
            out.append(Signal("vulnerability", 1, "VU-MILD", f"hardship language: '{h}'"))
        else:
            out.append(Signal("vulnerability", 0, "VU-NONE", "no vulnerability signal"))

        # regulatory_exposure (derived from the regulatory agent's output) --
        strong = [r for r in regulatory if r.strength == "strong"]
        if strong:
            lvl = max(r.exposure for r in strong)
            cites = ", ".join(sorted({r.category_id for r in strong}))
            out.append(Signal("regulatory_exposure", min(3, lvl), "RE-STRONG", f"strong hits: {cites}"))
        elif regulatory:
            out.append(Signal("regulatory_exposure", 1, "RE-WEAK",
                              f"weak/negated hits only: {', '.join(sorted({r.category_id for r in regulatory}))}"))
        else:
            out.append(Signal("regulatory_exposure", 0, "RE-NONE", "no regulatory trigger"))

        # recurrence ------------------------------------------------------
        prior = int(metadata.get("prior_contacts", 0) or 0)
        lvl, ev = 0, "first contact on record"
        if prior >= 3:
            lvl, ev = 3, f"{prior} prior contacts on record"
        elif prior == 2:
            lvl, ev = 2, "2 prior contacts on record"
        elif prior == 1:
            lvl, ev = 1, "1 prior contact on record"
        for cand, phrases in sorted(RECUR.items(), reverse=True):
            if cand > lvl and (h := _find(low, phrases)):
                lvl, ev = cand, f"customer states repetition: '{h}'"
                break
        out.append(Signal("recurrence", lvl, "RC-COUNT", ev))

        # duration_unresolved ---------------------------------------------
        days = metadata.get("days_open")
        lvl, ev = 0, "under a week or unstated"
        if isinstance(days, (int, float)):
            if days > 42:
                lvl, ev = 3, f"open {int(days)} days"
            elif days > 14:
                lvl, ev = 2, f"open {int(days)} days"
            elif days > 7:
                lvl, ev = 1, f"open {int(days)} days"
        for cand, phrases in sorted(DURATION.items(), reverse=True):
            if cand > lvl and (h := _find(low, phrases)):
                lvl, ev = cand, f"customer states elapsed time: '{h}'"
                break
        out.append(Signal("duration_unresolved", lvl, "DU-ELAPSED", ev))

        # escalation_signals ----------------------------------------------
        lvl, ev = 0, "no escalation language"
        for cand, phrases in sorted(ESCALATION.items(), reverse=True):
            if (h := _find(low, phrases)):
                lvl, ev = cand, f"escalation language: '{h}'"
                break
        out.append(Signal("escalation_signals", lvl, "ES-LANG", ev))

        # relationship_value ----------------------------------------------
        tenure = float(metadata.get("tenure_years", 0) or 0)
        products = int(metadata.get("product_count", 0) or 0)
        if metadata.get("relationship_flag"):
            out.append(Signal("relationship_value", 3, "RV-FLAG", "relationship flagged in CRM"))
        elif tenure >= 10 or products >= 3:
            out.append(Signal("relationship_value", 2, "RV-DEEP", f"{tenure:g}y tenure, {products} products"))
        elif tenure > 0:
            out.append(Signal("relationship_value", 1, "RV-STD", f"{tenure:g}y tenure"))
        else:
            out.append(Signal("relationship_value", 0, "RV-UNKNOWN", "tenure unknown"))

        assert {s.dimension for s in out} == set(DIMENSIONS), "every dimension must be scored"
        return out

    # --------------------------------------------------------------- routing

    def score_departments(self, text: str) -> list[tuple[str, float, str]]:
        low = text.lower()
        scored: list[tuple[str, float, str]] = []
        for dept in self.routing["departments"]:
            score, ev = 0.0, []
            for term in dept["terms"]:
                n = len(self._term_rx(term).findall(low))
                if n:
                    # Longer terms are more diagnostic: "mobile deposit" beats "card".
                    weight = 1.0 + 0.25 * len(term.split())
                    score += weight * min(n, 3)
                    ev.append(term)
            if score:
                scored.append((dept["id"], score, ", ".join(ev[:4])))
        scored.sort(key=lambda t: -t[1])
        return scored


    @staticmethod
    def _term_rx(term: str):
        """Word-boundary matcher, memoised.

        Plain substring counting routed a loan complaint to Digital Banking
        because "applied" contains "app", and routed branch complaints to
        Deposit Operations because "transfer" contains "nsf". Both were silent:
        the confidence score looked healthy and the evidence string said "app".
        """
        rx = _TERM_CACHE.get(term)
        if rx is None:
            rx = re.compile(r"(?<![a-z0-9])" + re.escape(term.strip()) + r"(?![a-z0-9])")
            _TERM_CACHE[term] = rx
        return rx

    # -------------------------------------------------------------- summary

    def summarize_issue(self, text: str) -> str:
        """A short, quotable noun phrase for the draft's {{ISSUE_SUMMARY}} slot.

        Extractive on purpose: it reuses the customer's own first clause rather
        than paraphrasing. A paraphrase is a new assertion about what the
        customer said, and an acknowledgement letter is the wrong place to make
        a new assertion."""
        first = re.split(r"(?<=[.!?])\s", text.strip())
        for sent in first:
            s = sent.strip().rstrip(".!?")
            if 20 <= len(s) <= 180:
                s = s[0].lower() + s[1:] if s[:1].isupper() and not s.startswith("I ") else s
                return s
        s = text.strip().replace("\n", " ")[:140]
        return s or "the issue you raised"
