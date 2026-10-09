"""Persistence, split along the PII boundary.

Three stores, deliberately separate directories:

  out/cases/      de-identified case files. These are what the pipeline, the
                  review console and the eval harness read. Safe to copy to a
                  laptop, email to a vendor, or hand to an auditor.
  vault/          placeholder -> original-value maps. Never leaves the host,
                  gitignored, and only `rehydrate()` reads it.
  out/audit.jsonl append-only decision log.

Keeping these apart is what lets us make a falsifiable claim to Compliance:
"the case file physically does not contain an account number." A test asserts
it (tests/test_redaction.py::test_case_files_contain_no_vault_values).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterator

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"
CASES = OUT / "cases"
VAULT = ROOT / "vault"
AUDIT = OUT / "audit.jsonl"


def ensure_dirs() -> None:
    for d in (OUT, CASES, VAULT):
        d.mkdir(parents=True, exist_ok=True)
    # Best-effort tightening. On a real deployment this is a separate volume
    # with its own access policy; here it at least signals intent.
    try:
        os.chmod(VAULT, 0o700)
    except OSError:
        pass


# ---------------------------------------------------------------- cases


def save_case(case_dict: dict) -> Path:
    ensure_dirs()
    p = CASES / f"{case_dict['case_id']}.json"
    p.write_text(json.dumps(case_dict, indent=2, sort_keys=True))
    return p


def load_case(case_id: str) -> dict:
    return json.loads((CASES / f"{case_id}.json").read_text())


def iter_cases() -> Iterator[dict]:
    if not CASES.exists():
        return
    for p in sorted(CASES.glob("*.json")):
        yield json.loads(p.read_text())


def all_cases() -> list[dict]:
    return list(iter_cases())


# ---------------------------------------------------------------- vault


def write_vault(vault_id: str, mapping: dict[str, str]) -> Path:
    ensure_dirs()
    p = VAULT / f"{vault_id}.json"
    p.write_text(json.dumps(mapping, indent=2, sort_keys=True))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return p


def read_vault(vault_id: str) -> dict[str, str]:
    p = VAULT / f"{vault_id}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def rehydrate(text: str, vault_id: str) -> str:
    """Put the real values back. Called at exactly one moment in the whole
    system: when an approved letter is rendered for sending, inside the bank's
    own network. Never called before a model request."""
    for placeholder, original in sorted(
        read_vault(vault_id).items(), key=lambda kv: -len(kv[0])
    ):
        text = text.replace(placeholder, original)
    return text


def all_vault_values() -> set[str]:
    """Used by the leak detector and its test."""
    vals: set[str] = set()
    if not VAULT.exists():
        return vals
    for p in VAULT.glob("*.json"):
        vals.update(json.loads(p.read_text()).values())
    return {v for v in vals if len(str(v)) >= 4}


# ---------------------------------------------------------------- audit


def audit(event: dict) -> None:
    ensure_dirs()
    event = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **event}
    with AUDIT.open("a") as fh:
        fh.write(json.dumps(event, sort_keys=True) + "\n")


def read_audit() -> list[dict]:
    if not AUDIT.exists():
        return []
    return [json.loads(l) for l in AUDIT.read_text().splitlines() if l.strip()]


def reset() -> None:
    """Clear generated state. Does not touch config/ or data/."""
    import shutil

    for d in (CASES, VAULT):
        if d.exists():
            shutil.rmtree(d)
    if AUDIT.exists():
        AUDIT.unlink()
    ensure_dirs()
