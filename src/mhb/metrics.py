"""Two analyses the stakeholders will actually ask for.

`calibration_report` is for the ops head and the two senior analysts: where do
the rival definitions of "serious" actually diverge, how often, and on which
complaints. It converts an argument into an agenda.

`throughput_model` is for the ops head alone: does this touch the backlog. It
is a model, not a measurement, and it labels its own inputs as assumptions
because the honest answer is that one number — how long an analyst takes to
review a pre-triaged case with a draft attached — has to come from a pilot,
not from us.
"""
from __future__ import annotations

from . import store

BANDS = ["S1", "S2", "S3", "S4"]


def calibration_report(cases: list[dict] | None = None) -> dict:
    cases = cases if cases is not None else store.all_cases()
    matrix = {a: {b: 0 for b in BANDS} for a in BANDS}
    disagreements, flagged = [], []

    for c in cases:
        sev, alt = c.get("severity"), c.get("severity_alt")
        if not sev or not alt:
            continue
        matrix[sev["band"]][alt["band"]] += 1
        if sev["band"] != alt["band"]:
            gap = abs(BANDS.index(sev["band"]) - BANDS.index(alt["band"]))
            row = {
                "case_id": c["case_id"],
                "archetype": c.get("archetype", ""),
                "band_a": sev["band"],
                "band_b": alt["band"],
                "gap": gap,
                "driver": _driver(sev, alt, c),
                "excerpt": (c.get("redaction") or {}).get("redacted_text", "")[:160],
            }
            disagreements.append(row)
            if c.get("calibration_flag"):
                flagged.append(row)

    n = sum(sum(r.values()) for r in matrix.values())
    agree = sum(matrix[b][b] for b in BANDS)
    disagreements.sort(key=lambda r: (-r["gap"], r["case_id"]))

    return {
        "n": n,
        "agree": agree,
        "agreement_rate": round(agree / n, 4) if n else 0.0,
        "disagreement_count": len(disagreements),
        "hard_flag_count": len(flagged),
        "matrix": matrix,
        "disagreements": disagreements,
        "worklist": disagreements[:15],
        "band_counts_a": {b: sum(matrix[b].values()) for b in BANDS},
        "band_counts_b": {b: sum(matrix[a][b] for a in BANDS) for b in BANDS},
    }


def _driver(sev: dict, alt: dict, case: dict) -> str:
    """Why the two rubrics parted company on this case. Reads the dimension
    levels and names the one that the two weight most differently."""
    levels = sev.get("dimension_levels", {})
    a_ov = {o["id"] for o in sev.get("overrides_applied", []) if o["from"] != o["to"]}
    b_ov = {o["id"] for o in alt.get("overrides_applied", []) if o["from"] != o["to"]}
    if b_ov - a_ov:
        return f"Rubric B override fired ({', '.join(sorted(b_ov - a_ov))})"
    if a_ov - b_ov:
        return f"Rubric A override fired ({', '.join(sorted(a_ov - b_ov))})"
    # A weights harm 3.0/2.5 vs B 1.0/1.0; B weights exposure 3.5 and
    # escalation 3.0 vs A 1.5/1.0.
    harm = levels.get("financial_harm", 0) + levels.get("vulnerability", 0)
    expo = levels.get("regulatory_exposure", 0) + levels.get("escalation_signals", 0)
    if expo > harm:
        return "regulatory/escalation signal present, financial harm low — B escalates, A does not"
    if harm > expo:
        return "financial or personal harm high, regulatory exposure low — A escalates, B does not"
    return "near a band cutoff under both rubrics"


def throughput_model(policy: dict, measured: dict | None = None) -> dict:
    """Backlog arithmetic.

    measured: optional dict of observed values that override the assumptions,
    e.g. {"assisted_minutes_per_complaint_routine": 6.4} after a pilot week.
    """
    t = dict(policy["throughput_assumptions"])
    if measured:
        t.update(measured)

    demand_per_day = t["complaints_per_month"] / t["working_days_per_month"]
    analyst_minutes = t["analysts"] * t["productive_minutes_per_analyst_day"]

    baseline_capacity = analyst_minutes / t["baseline_minutes_per_complaint"]
    esc = t["escalated_share_estimate"]
    assisted_minutes = (
        (1 - esc) * t["assisted_minutes_per_complaint_routine"]
        + esc * t["assisted_minutes_per_complaint_escalated"]
    )
    assisted_capacity = analyst_minutes / assisted_minutes

    backlog_now = t["current_backlog_days"] * demand_per_day
    baseline_net = baseline_capacity - demand_per_day
    assisted_net = assisted_capacity - demand_per_day
    drain_days = (backlog_now / assisted_net) if assisted_net > 0 else None

    # Sensitivity: the one number the client must measure. The range must run
    # PAST the breakeven point — a table that only shows the scenarios where
    # the plan works is marketing, not analysis.
    sensitivity = []
    for routine in (4, 5, 6, 8, 10, 12, 14, 16, 18, 20):
        am = (1 - esc) * routine + esc * t["assisted_minutes_per_complaint_escalated"]
        cap = analyst_minutes / am
        net = cap - demand_per_day
        sensitivity.append({
            "assisted_routine_minutes": routine,
            "capacity_per_day": round(cap, 1),
            "net_per_day": round(net, 1),
            "drains_backlog": net > 0,
            "drain_days": round(backlog_now / net, 1) if net > 0 else None,
        })

    return {
        "demand_per_day": round(demand_per_day, 1),
        "analyst_minutes_per_day": analyst_minutes,
        "baseline": {
            "minutes_per_complaint": t["baseline_minutes_per_complaint"],
            "capacity_per_day": round(baseline_capacity, 1),
            "net_per_day": round(baseline_net, 1),
            "verdict": "backlog grows" if baseline_net < 0 else "backlog stable",
        },
        "assisted": {
            "minutes_per_complaint": round(assisted_minutes, 2),
            "capacity_per_day": round(assisted_capacity, 1),
            "net_per_day": round(assisted_net, 1),
            "verdict": "backlog drains" if assisted_net > 0 else "backlog still grows",
        },
        "backlog_complaints_now": round(backlog_now),
        "estimated_drain_days": round(drain_days, 1) if drain_days else None,
        "breakeven_minutes_per_complaint": round(analyst_minutes / demand_per_day, 1),
        "sensitivity": sensitivity,
        "assumption_warning": (
            "Every input is an assumption supplied by us, not a measurement from Meridian Harbor. "
            "The load-bearing one is assisted_minutes_per_complaint_routine. Breakeven is "
            f"{round(analyst_minutes / demand_per_day, 1)} minutes per complaint — above that the "
            "backlog keeps growing no matter what the pipeline does. A two-week pilot measuring "
            "actual review time replaces this whole model."
        ),
    }
