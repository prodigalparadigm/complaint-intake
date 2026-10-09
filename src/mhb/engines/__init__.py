"""Interpretation engines.

An engine answers questions *about language*: what does this complaint say,
which regulated practice does it touch, which department's vocabulary does it
use. An engine never decides what the bank does about the answer — severity
banding, SLA clocks, routing ownership, template choice and the human-approval
gate all live in `mhb.agents` and run identically whichever engine is loaded.

That split is deliberate. It means swapping `rules` for `claude` can change how
well the system *reads*, but cannot change what the system is *allowed to do*.

Two engines ship:

  rules   deterministic, offline, zero-cost, fully inspectable. The default,
          and the one every number in docs/EVIDENCE.md was produced with.
  claude  Claude via the Messages API, for the interpretation steps only, and
          only ever on redacted text. Not exercised in this prototype: there
          were no API credentials on the build host, and more to the point it
          should not be switched on until the client has decided whether any
          text at all may leave the building. See docs/DECISIONS.md Q1.
"""
from __future__ import annotations

from typing import Protocol

from ..models import RegulatoryFinding, Signal


class RegHit:
    """A candidate regulatory match. The engine proposes; the regulatory agent
    decides whether the candidate clears Compliance's threshold."""

    __slots__ = ("category_id", "evidence", "strength")

    def __init__(self, category_id: str, evidence: str, strength: str = "strong"):
        self.category_id = category_id
        self.evidence = evidence
        self.strength = strength

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"RegHit({self.category_id!r}, {self.strength!r}, {self.evidence!r})"


class Engine(Protocol):
    name: str
    version: str

    def detect_regulatory(self, text: str) -> list[RegHit]: ...

    def extract_signals(
        self, text: str, metadata: dict, regulatory: list[RegulatoryFinding]
    ) -> list[Signal]: ...

    def score_departments(self, text: str) -> list[tuple[str, float, str]]: ...

    def summarize_issue(self, text: str) -> str: ...


def get_engine(name: str, cfg) -> Engine:
    if name == "rules":
        from .rules import RulesEngine

        return RulesEngine(cfg)
    if name == "claude":
        from .claude import ClaudeEngine

        return ClaudeEngine(cfg)
    raise ValueError(f"unknown engine {name!r} (choose: rules, claude)")
