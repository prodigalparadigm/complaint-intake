"""Loading the synthetic corpus into RawComplaint records."""
from __future__ import annotations

import json
from pathlib import Path

from .models import RawComplaint

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "data" / "synthetic" / "complaints.jsonl"


def load(limit: int | None = None, path: Path | None = None) -> list[dict]:
    p = path or CORPUS
    if not p.exists():
        raise FileNotFoundError(f"{p} missing — run: python3 data/generate.py")
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    return rows[:limit] if limit else rows


def to_raw(row: dict) -> RawComplaint:
    return RawComplaint(
        complaint_id=row["complaint_id"],
        channel=row["channel"],
        received_at=row["received_at"],
        text=row["text"],
        subject=row.get("subject", ""),
        customer_ref=row.get("customer_ref", ""),
        metadata=dict(row.get("metadata", {})),
    )
