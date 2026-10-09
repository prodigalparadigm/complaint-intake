"""Config loading.

Every contestable judgment in this system is a file in config/, loaded here,
and stamped onto each decision by its ruleset id. When the ops head asks "why
was this a two?", the answer is a rubric id and a weight table she can read,
not a model version.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"


def _load(name: str) -> dict:
    return json.loads((CONFIG / name).read_text())


@dataclass
class Config:
    policy: dict
    redaction: dict
    regulatory: dict
    routing: dict
    rubric_primary: dict
    rubric_secondary: dict
    templates: dict
    lint: dict

    @property
    def ruleset_ids(self) -> list[str]:
        return sorted(
            d["ruleset_id"] if "ruleset_id" in d else d["rubric_id"]
            for d in (
                self.policy if "ruleset_id" in self.policy else {"ruleset_id": self.policy["policy_id"]},
                self.redaction, self.regulatory, self.routing,
                self.rubric_primary, self.rubric_secondary, self.templates, self.lint,
            )
        )

    def rubric(self, which: str) -> dict:
        return self.rubric_primary if which.upper() == "A" else self.rubric_secondary


def load(primary: str = "A") -> Config:
    a, b = _load("severity_rubric.A.json"), _load("severity_rubric.B.json")
    if primary.upper() == "B":
        a, b = b, a
    return Config(
        policy=_load("policy.json"),
        redaction=_load("redaction_patterns.json"),
        regulatory=_load("regulatory_rules.json"),
        routing=_load("routing_map.json"),
        rubric_primary=a,
        rubric_secondary=b,
        templates=_load("response_templates.json"),
        lint=_load("draft_lint.json"),
    )
