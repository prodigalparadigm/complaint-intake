"""Intake agent — three channels into one record.

The three sources do not look alike. Email arrives with headers and a signature
block. The web form arrives as labelled fields. A call-center note is an
analyst's shorthand written while a customer was talking, and it carries the
metadata the other two lack (the rep already knows the tenure and the prior
contacts). Normalising here means every downstream agent sees one shape, and
the channel-specific quirks are fixed in one file.
"""
from __future__ import annotations

import hashlib
import re

from ..models import Case, RawComplaint
from .base import Agent

SIGNATURE = re.compile(
    r"\n\s*(?:--+|__+|sent from my|best regards|regards,|thanks,|thank you,|sincerely,)\b.*",
    re.I | re.S,
)
QUOTED_REPLY = re.compile(r"\n\s*(?:On .{0,60}wrote:|>.*|-{3,}\s*Original Message)", re.S)
FORM_FIELD = re.compile(r"^\s*([A-Za-z][A-Za-z /_]{2,30})\s*:\s*(.*)$")

# Call-center shorthand. Expanded so that both the keyword rules and a model
# read the same text a human rep meant.
SHORTHAND = {
    r"\bcx\b": "customer", r"\bcust\b": "customer", r"\bclmd\b": "claimed",
    r"\bunauth\b": "unauthorized", r"\bOD\b": "overdraft", r"\bODs\b": "overdrafts",
    r"\bNSF\b": "insufficient funds fee", r"\bxfer\b": "transfer",
    r"\bacct\b": "account", r"\bstmt\b": "statement", r"\btxn\b": "transaction",
    r"\btxns\b": "transactions", r"\bmgr\b": "manager", r"\bbr\b": "branch",
    r"\bCB\b": "call back", r"\bw/\b": "with", r"\bre:\b": "regarding",
    r"\bcc\b": "credit card", r"\bdc\b": "debit card", r"\bESC\b": "escalated",
    r"\brefd\b": "refused", r"\bfrst\b": "frustrated", r"\bdisp\b": "dispute",
}


class IntakeAgent(Agent):
    name = "intake"
    version = "1.1.0"

    def normalize(self, raw: RawComplaint) -> tuple[str, dict]:
        text, notes = raw.text, []

        if raw.channel == "email":
            text = QUOTED_REPLY.split(text)[0]
            text = SIGNATURE.sub("", text)
            notes.append("stripped signature block and quoted reply history")

        elif raw.channel == "web_form":
            fields, body = {}, []
            for line in text.splitlines():
                m = FORM_FIELD.match(line)
                if m and len(m.group(2)) < 120:
                    fields[m.group(1).strip().lower()] = m.group(2).strip()
                else:
                    body.append(line)
            narrative = "\n".join(body).strip()
            # The free-text box is the complaint; the labelled fields are context.
            text = narrative or " ".join(f"{k}: {v}" for k, v in fields.items())
            if fields:
                notes.append(f"parsed {len(fields)} form fields: {', '.join(sorted(fields))}")
            for key, meta_key in (("account type", "account_type"), ("branch", "branch")):
                if key in fields:
                    raw.metadata.setdefault(meta_key, fields[key])

        elif raw.channel == "call_note":
            for pat, full in SHORTHAND.items():
                text = re.sub(pat, full, text)
            notes.append("expanded call-center shorthand")

        else:
            notes.append(f"UNKNOWN CHANNEL {raw.channel!r} — forced to human review per policy")

        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text, {"notes": notes}

    def build_case(self, raw: RawComplaint) -> tuple[Case, str, dict]:
        text, info = self.normalize(raw)
        case_id = "MHB-" + _case_suffix(f"{raw.complaint_id}:{raw.received_at}")
        case = Case(
            case_id=case_id,
            complaint_id=raw.complaint_id,
            channel=raw.channel,
            received_at=raw.received_at,
            content_hash=raw.content_hash(),
            state="INGESTED",
        )
        case.human = {
            "status": "unassigned",
            "approver": None,
            "approved_at": None,
            "edits": None,
            "unknown_channel": raw.channel not in ("email", "web_form", "call_note"),
        }
        return case, text, info

    def handle(self, case: Case, *, raw: RawComplaint, normalized: str, info: dict):
        return (
            f"normalized {raw.channel} complaint ({len(raw.text)} -> {len(normalized)} chars)",
            1.0,
            {
                "channel": raw.channel,
                "original_chars": len(raw.text),
                "normalized_chars": len(normalized),
                "transformations": info["notes"],
                "metadata_keys": sorted(raw.metadata),
            },
        )

# Case id alphabet. Crockford-style, with I/O/0/1 removed so an id can be read
# over the phone without ambiguity, and with the first character forced to a
# letter. The forced letter is not cosmetic: an all-digit id like "MHB-16251160"
# is indistinguishable from an account number, and the compliance lint rule that
# looks for bare digit runs in customer-facing text correctly flagged it.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def _case_suffix(seed: str, length: int = 8) -> str:
    digest = hashlib.sha256(seed.encode()).digest()
    out = [_LETTERS[digest[0] % len(_LETTERS)]]
    out += [_ALPHABET[b % len(_ALPHABET)] for b in digest[1:length]]
    return "".join(out)
