"""Human review — the only door to a customer.

Everything the pipeline produces is a proposal. This module is where a named
person accepts or rejects it, and it is the single place in the codebase that:

  * writes the state APPROVED_FOR_SEND, and
  * calls store.rehydrate() to put real customer values back into a letter.

Both are guarded. `approve()` refuses a case QA blocked, refuses an anonymous
approver, and records the analyst's edits as a diff so that the drafting agent
can be measured on how much rewriting it actually saves — the one metric that
tells the ops head whether this is working.
"""
from __future__ import annotations

import difflib
import re
from datetime import datetime

from . import store


class ApprovalError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def approve(case_id: str, approver: str, edited_body: str | None = None,
            edited_subject: str | None = None) -> dict:
    case = store.load_case(case_id)

    if not approver or not approver.strip():
        raise ApprovalError("approval requires a named approver; anonymous approval is not permitted")
    if case["state"] == "BLOCKED_QA":
        raise ApprovalError(
            f"case {case_id} was blocked by QA and cannot be approved as drafted: "
            + "; ".join(b["detail"] for b in case["qa"]["blocks"])
        )
    if case["state"] != "PENDING_HUMAN_APPROVAL":
        raise ApprovalError(
            f"case {case_id} is in state {case['state']}, not PENDING_HUMAN_APPROVAL"
        )

    original_body = case["draft"]["body"]
    original_subject = case["draft"]["subject"]
    final_body = edited_body if edited_body is not None else original_body
    final_subject = edited_subject if edited_subject is not None else original_subject

    diff = list(difflib.unified_diff(
        original_body.splitlines(), final_body.splitlines(),
        fromfile="agent_draft", tofile="analyst_final", lineterm="", n=0,
    ))
    changed_lines = sum(1 for l in diff if l.startswith(("+", "-")) and not l.startswith(("+++", "---")))
    total_lines = max(1, len(original_body.splitlines()))

    case["state"] = "APPROVED_FOR_SEND"
    case["human"].update({
        "status": "approved",
        "approver": approver,
        "approved_at": _now(),
        "edits": {
            "edited": final_body != original_body or final_subject != original_subject,
            "changed_lines": changed_lines,
            "edit_ratio": round(changed_lines / total_lines, 3),
            "diff": diff[:60],
        },
        "final_subject": final_subject,
        "final_body": final_body,
    })
    store.save_case(case)
    store.audit({"event": "human_approval", "case_id": case_id, "approver": approver,
                 "edited": case["human"]["edits"]["edited"],
                 "edit_ratio": case["human"]["edits"]["edit_ratio"]})
    return case


def reject(case_id: str, approver: str, note: str) -> dict:
    case = store.load_case(case_id)
    if not approver or not approver.strip():
        raise ApprovalError("rejection requires a named reviewer")
    case["state"] = "RETURNED_FOR_REWORK"
    case["human"].update({"status": "returned", "approver": approver,
                          "approved_at": None, "rework_note": note})
    store.save_case(case)
    store.audit({"event": "human_rejection", "case_id": case_id,
                 "approver": approver, "note": note})
    return case


# Slots the pipeline deliberately leaves empty, resolved only here. In
# production {{CUSTOMER_NAME}} comes from the system of record, looked up by
# account number inside the bank's network. This prototype has no core banking
# system, so it resolves from the local vault when redaction captured a name
# and falls back to a neutral salutation when it did not — rather than printing
# a letter addressed to a template variable.
SALUTATION_FALLBACK = "Customer"


def _fill_deferred_slots(letter: str, case: dict) -> tuple[str, list[str]]:
    vault = store.read_vault(case["redaction"]["vault_id"])
    name = next(
        (v for k, v in sorted(vault.items()) if k.startswith("[NAME_")), None
    )
    notes = []
    if name is None:
        name = SALUTATION_FALLBACK
        notes.append(
            "customer name not present in the complaint text; in production this "
            "is resolved from the system of record by account number"
        )
    letter = letter.replace("{{CUSTOMER_NAME}}", name)
    letter = letter.replace("{{ANALYST_NAME}}", case["human"].get("approver") or "")
    return letter, notes


def render_for_send(case_id: str) -> str:
    """Produce the letter exactly as the customer would receive it.

    The only rehydration point in the system, and the only place the deferred
    slots are filled. It refuses unless a human has approved, which is why no
    agent can reach real customer data by calling it — and it refuses again,
    after rendering, if any slot is still unfilled, so a letter addressed to
    "{{CUSTOMER_NAME}}" cannot leave this function.
    """
    case = store.load_case(case_id)
    if case["state"] != "APPROVED_FOR_SEND":
        raise ApprovalError(
            f"refusing to render case {case_id}: state is {case['state']}, "
            "and rehydration of customer data requires an approved case"
        )
    body = case["human"].get("final_body") or case["draft"]["body"]
    subject = case["human"].get("final_subject") or case["draft"]["subject"]
    vault_id = case["redaction"]["vault_id"]

    letter = f"Subject: {subject}\n\n{body}"
    letter = store.rehydrate(letter, vault_id)
    letter, notes = _fill_deferred_slots(letter, case)

    leftover = re.findall(r"\{\{[A-Z_]+\}\}|\[[A-Z]+_\d+\]", letter)
    if leftover:
        raise ApprovalError(
            f"refusing to send case {case_id}: the rendered letter still contains "
            f"unresolved placeholder(s) {sorted(set(leftover))}"
        )

    store.audit({"event": "render_for_send", "case_id": case_id,
                 "approver": case["human"].get("approver"),
                 "resolution_notes": notes})
    return letter


# --------------------------------------------------------------------- queues


def queues() -> dict:
    """The four worklists the review console shows."""
    cases = store.all_cases()
    out = {
        "compliance_48h": [], "approval": [], "rework": [],
        "calibration": [], "done": [],
    }
    for c in cases:
        if c.get("sla", {}).get("compliance_flag_due") and c["state"] not in (
            "APPROVED_FOR_SEND", "RETURNED_FOR_REWORK"
        ):
            out["compliance_48h"].append(c)
        if c["state"] == "PENDING_HUMAN_APPROVAL":
            out["approval"].append(c)
        elif c["state"] == "BLOCKED_QA":
            out["rework"].append(c)
        elif c["state"] in ("APPROVED_FOR_SEND", "RETURNED_FOR_REWORK"):
            out["done"].append(c)
        if c.get("calibration_flag"):
            out["calibration"].append(c)

    band_rank = {"S1": 0, "S2": 1, "S3": 2, "S4": 3}
    for key in out:
        out[key].sort(key=lambda c: (
            band_rank.get((c.get("severity") or {}).get("band", "S4"), 3),
            c["received_at"],
        ))
    return out
