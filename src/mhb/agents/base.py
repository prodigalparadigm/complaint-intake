"""Agent base.

Each agent is a narrow, independently testable step that records its own work.
The wrapper exists so that no agent can run without leaving an audit entry —
`Case.decisions` is built by the base class, not by the agent author.
"""
from __future__ import annotations

import time
from datetime import datetime

from ..models import Case, Decision


class Agent:
    name = "agent"
    version = "0.0.0"

    def __init__(self, cfg, engine=None):
        self.cfg = cfg
        self.engine = engine

    # Subclasses implement this and return (summary, confidence, payload).
    def handle(self, case: Case, **kw):  # pragma: no cover - interface
        raise NotImplementedError

    def ruleset_ids(self) -> list[str]:
        return []

    def __call__(self, case: Case, **kw) -> Case:
        started = time.perf_counter()
        summary, confidence, payload = self.handle(case, **kw)
        case.decisions.append(
            Decision(
                agent=self.name,
                agent_version=self.version,
                engine=getattr(self.engine, "name", "n/a"),
                ruleset_ids=self.ruleset_ids(),
                at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                confidence=confidence,
                summary=summary,
                payload=payload,
            )
        )
        return case
