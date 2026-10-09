"""PII leak detector.

Independent of the redaction agent on purpose. The redactor's own opinion that
it succeeded is worth very little; this scans the finished artifact against the
vault's actual values and reports what it finds. It is wired into QA (so a leak
blocks the case) and into the eval harness (so a leak fails the build).
"""
from __future__ import annotations

import json
import re

from . import store

# Values too short or too generic to be meaningful evidence of a leak.
MIN_LEN = 4
GENERIC = re.compile(r"^(?:19|20)\d{2}$")


def scan(text: str, vault_values: set[str] | None = None) -> list[dict]:
    values = vault_values if vault_values is not None else store.all_vault_values()
    hits = []
    for v in values:
        s = str(v)
        if len(s) < MIN_LEN or GENERIC.match(s):
            continue
        if s in text:
            hits.append({"value_fingerprint": f"{s[:2]}***{s[-2:]}", "length": len(s)})
    return hits


def scan_case(case_dict: dict, vault_values: set[str] | None = None) -> list[dict]:
    """Scan the entire serialized case — not just the draft. A case file is
    what gets exported, reported on and shown in the console, so the whole
    object is the surface that matters."""
    return scan(json.dumps(case_dict), vault_values)
