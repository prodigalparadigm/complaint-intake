"""Command line interface.

    python3 -m mhb generate              regenerate the synthetic corpus
    python3 -m mhb run [--limit N]       run the pipeline over the corpus
    python3 -m mhb queues                the four worklists
    python3 -m mhb show CASE_ID          one case, with its full audit trail
    python3 -m mhb approve CASE_ID --approver "Name" [--edit FILE]
    python3 -m mhb reject  CASE_ID --approver "Name" --note "..."
    python3 -m mhb render  CASE_ID       the letter as the customer sees it
    python3 -m mhb calibrate             where the two rubrics disagree
    python3 -m mhb simulate              backlog arithmetic
    python3 -m mhb console               build the HTML review console
    python3 -m mhb reset                 clear generated cases, vault, audit
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import config, corpus, metrics, review, store
from .orchestrator import Pipeline

BAR = "─" * 78


def _p(s=""):
    print(s)


def cmd_generate(args):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "data"))
    import generate

    generate.main()


def cmd_run(args):
    cfg = config.load(args.rubric)
    if args.fresh:
        store.reset()
    pipe = Pipeline(cfg, args.engine)
    rows = corpus.load(args.limit)
    _p(f"Running {len(rows)} complaints — engine={args.engine}, primary rubric={args.rubric}")
    _p(BAR)
    cases = []
    for row in rows:
        case = pipe.run(corpus.to_raw(row))
        # Only the archetype label rides along, for the console's grouping.
        # Gold labels must NOT be written here: they contain the raw injected
        # identifier values, and attaching them put real PII back into every
        # case file. The independent leak scanner caught it; the eval harness
        # now joins gold from data/synthetic/ on complaint_id instead.
        d = store.load_case(case.case_id)
        d["archetype"] = row.get("archetype", "")
        store.save_case(d)
        cases.append(case)
        if args.verbose:
            _p(f"{case.case_id}  {case.channel:<10} {case.severity.band} "
               f"(alt {case.severity_alt.band}) {case.routing.department:<20} "
               f"reg={','.join(case.regulatory_ids) or '-':<28} {case.state}")

    states, bands, flagged, blocked = {}, {}, 0, 0
    reg_count = 0
    for c in cases:
        states[c.state] = states.get(c.state, 0) + 1
        bands[c.severity.band] = bands.get(c.severity.band, 0) + 1
        flagged += bool(c.calibration_flag)
        blocked += c.state == "BLOCKED_QA"
        reg_count += bool(c.regulatory_ids)
    total_ms = sum(sum(d.duration_ms for d in c.decisions) for c in cases)

    _p(BAR)
    _p(f"states              {states}")
    _p(f"severity (rubric {args.rubric}) {dict(sorted(bands.items()))}")
    _p(f"regulatory flagged  {reg_count}/{len(cases)}  (all within 0.0h of receipt)")
    _p(f"calibration flags   {flagged}/{len(cases)}  — rubrics disagree by 2+ bands")
    _p(f"QA blocked          {blocked}/{len(cases)}")
    _p(f"wall time           {total_ms:.0f} ms total, {total_ms / max(1, len(cases)):.1f} ms/complaint")
    _p()
    _p("Nothing above was sent to a customer. Terminal state is PENDING_HUMAN_APPROVAL.")


def cmd_queues(args):
    q = review.queues()
    labels = {
        "compliance_48h": "COMPLIANCE — 48-hour clock running",
        "approval": "ANALYST — drafts awaiting sign-off",
        "rework": "ANALYST — QA blocked, needs rework",
        "calibration": "OPS + SENIOR ANALYSTS — rubrics disagree",
        "done": "CLOSED — human decision recorded",
    }
    for key, label in labels.items():
        rows = q[key]
        _p(f"\n{label}  ({len(rows)})")
        _p(BAR)
        for c in rows[: args.limit]:
            sev = (c.get("severity") or {}).get("band", "--")
            alt = (c.get("severity_alt") or {}).get("band", "--")
            reg = ",".join(
                f["category_id"] for f in c.get("regulatory", []) if f["strength"] == "strong"
            )
            due = (c.get("sla", {}).get("compliance_flag_due") or "")[:16]
            _p(f"  {c['case_id']}  {sev}/{alt}  {c['channel']:<10} "
               f"{(c.get('routing') or {}).get('department', '-'):<20} "
               f"{reg[:34]:<34} {('due ' + due) if due else ''}")
        if len(rows) > args.limit:
            _p(f"  ... {len(rows) - args.limit} more")


def cmd_show(args):
    c = store.load_case(args.case_id)
    _p(BAR)
    _p(f"CASE {c['case_id']}   state={c['state']}   channel={c['channel']}   received {c['received_at']}")
    _p(BAR)
    _p("\nREDACTED COMPLAINT")
    _p(c["redaction"]["redacted_text"])
    _p(f"\n  PII removed: {c['redaction']['pii_count']} — {', '.join(c['redaction']['pii_kinds'])}")
    for n in c["redaction"]["residual_risk_notes"]:
        _p(f"  ! {n}")

    _p("\nREGULATORY")
    if not c["regulatory"]:
        _p("  none detected")
    for f in c["regulatory"]:
        mark = "STRONG" if f["strength"] == "strong" else " weak "
        _p(f"  [{mark}] {f['category_id']:<18} {f['citation']}")
        _p(f"           evidence: {'; '.join(f['evidence'])}")
        if f["statutory_clock"]:
            _p(f"           clock: {f['statutory_clock']}")

    _p("\nSEVERITY")
    for tag, s in (("primary", c["severity"]), ("alternate", c["severity_alt"])):
        _p(f"  {tag:<10} {s['band']} ({s['rubric_id']}) normalized={s['normalized']:.3f} "
           f"confidence={s['confidence']}")
        for o in s["overrides_applied"]:
            _p(f"             override {o['id']}: {o['from']}->{o['to']} — {o['reason']}")
    if c.get("calibration_flag"):
        _p(f"  ** {c['calibration_note']}")
    _p("  dimensions:")
    for s in c["signals"]:
        _p(f"    {s['dimension']:<22} {s['level']}  [{s['rule_id']}] {s['evidence']}")

    _p("\nROUTING")
    r = c["routing"]
    _p(f"  {r['department']} ({r['department_label']}) -> {r['queue_email']}  conf={r['confidence']}")
    _p(f"  reason: {r['reason']}")
    if r["tiebreaker_applied"]:
        _p(f"  ownership rule applied: {r['tiebreaker_applied']}")
    for a in r["alternates"]:
        _p(f"  alt: {a['department']} ({a['score']}) via {a['evidence']}")

    _p("\nSLA")
    for k, v in sorted(c["sla"].items()):
        _p(f"  {k:<32} {v}")

    _p("\nDRAFT (not sent)")
    _p(f"  template {c['draft']['template_id']}  confidence {c['draft']['confidence']}")
    _p(f"  Subject: {c['draft']['subject']}")
    _p("  " + "\n  ".join(c["draft"]["body"].splitlines()))
    for f in c["draft"]["lint_findings"]:
        _p(f"  lint [{f['severity']}] {f['rule']}: {f['why']}")

    _p("\nQA")
    _p(f"  {'PASS' if c['qa']['passed'] else 'BLOCKED'} — checks: {', '.join(c['qa']['checks_run'])}")
    for b in c["qa"]["blocks"]:
        _p(f"  BLOCK {b['check']}: {b['detail']}")
    for w in c["qa"]["warnings"]:
        _p(f"  warn  {w['check']}: {w['detail']}")

    _p("\nAUDIT TRAIL")
    for d in c["decisions"]:
        conf = f"{d['confidence']:.2f}" if d["confidence"] is not None else " -- "
        _p(f"  {d['agent']:<20} v{d['agent_version']:<8} engine={d['engine']:<7} "
           f"{d['duration_ms']:>7.2f}ms conf={conf}  {d['summary']}")
        if d["ruleset_ids"]:
            _p(f"  {'':<20} rulesets: {', '.join(d['ruleset_ids'])}")

    _p("\nHUMAN")
    for k in ("status", "approver", "approved_at"):
        _p(f"  {k:<12} {c['human'].get(k)}")


def cmd_approve(args):
    edited = Path(args.edit).read_text() if args.edit else None
    try:
        c = review.approve(args.case_id, args.approver, edited_body=edited)
    except review.ApprovalError as e:
        _p(f"REFUSED: {e}")
        raise SystemExit(2)
    e = c["human"]["edits"]
    _p(f"{args.case_id} approved by {args.approver} at {c['human']['approved_at']}")
    _p(f"  edited={e['edited']} changed_lines={e['changed_lines']} edit_ratio={e['edit_ratio']}")


def cmd_reject(args):
    c = review.reject(args.case_id, args.approver, args.note)
    _p(f"{args.case_id} returned for rework by {args.approver}: {args.note}")
    _p(f"  state={c['state']}")


def cmd_render(args):
    try:
        _p(review.render_for_send(args.case_id))
    except review.ApprovalError as e:
        _p(f"REFUSED: {e}")
        raise SystemExit(2)


def cmd_calibrate(args):
    r = metrics.calibration_report()
    _p(BAR)
    _p("SEVERITY CALIBRATION — where the two definitions of 'serious' diverge")
    _p(BAR)
    _p(f"\n{r['n']} cases scored under both rubrics.")
    _p(f"  agree on band        {r['agree']}/{r['n']}  ({r['agreement_rate']:.1%})")
    _p(f"  disagree             {r['disagreement_count']}")
    _p(f"  disagree by 2+ bands {r['hard_flag_count']}   <- these change what an analyst does")
    _p("\nConfusion matrix (rows = Rubric A 'harm-weighted', cols = Rubric B 'exposure-weighted')")
    _p("        " + "".join(f"{b:>7}" for b in metrics.BANDS))
    for a in metrics.BANDS:
        _p(f"  A {a}  " + "".join(f"{r['matrix'][a][b]:>7}" for b in metrics.BANDS))
    _p(f"\n  band totals A: {r['band_counts_a']}")
    _p(f"  band totals B: {r['band_counts_b']}")
    _p("\nWORKLIST — bring these to the calibration session, worst divergence first")
    _p(BAR)
    for row in r["worklist"]:
        _p(f"  {row['case_id']}  A={row['band_a']} B={row['band_b']} (gap {row['gap']})  {row['archetype']}")
        _p(f"     why: {row['driver']}")
        _p(f"     \"{row['excerpt'][:120]}...\"")
    _p("\nThis table is the deliverable, not the severity score. One hour with these")
    _p("cases and the two senior analysts produces a ratified rubric; the rubric")
    _p("file is then the only thing that changes.")


def cmd_simulate(args):
    cfg = config.load()
    m = metrics.throughput_model(cfg.policy)
    _p(BAR)
    _p("BACKLOG MODEL — does this touch the six-day backlog?")
    _p(BAR)
    _p(f"\n  demand                     {m['demand_per_day']} complaints/day")
    _p(f"  analyst capacity           {m['analyst_minutes_per_day']} minutes/day (3 analysts)")
    _p(f"  breakeven                  {m['breakeven_minutes_per_complaint']} minutes/complaint")
    _p(f"\n  TODAY   {m['baseline']['minutes_per_complaint']} min/complaint -> "
       f"{m['baseline']['capacity_per_day']}/day, net {m['baseline']['net_per_day']:+}/day "
       f"({m['baseline']['verdict']})")
    _p(f"  ASSISTED {m['assisted']['minutes_per_complaint']} min/complaint -> "
       f"{m['assisted']['capacity_per_day']}/day, net {m['assisted']['net_per_day']:+}/day "
       f"({m['assisted']['verdict']})")
    _p(f"\n  backlog now                ~{m['backlog_complaints_now']} complaints")
    _p(f"  estimated drain            {m['estimated_drain_days']} working days")
    _p("\n  SENSITIVITY — the one number you have to measure in a pilot")
    _p("    review min/complaint   capacity/day   net/day   drains?   drain days")
    for s in m["sensitivity"]:
        _p(f"    {s['assisted_routine_minutes']:>18}   {s['capacity_per_day']:>12}   "
           f"{s['net_per_day']:>+7}   {str(s['drains_backlog']):>7}   "
           f"{s['drain_days'] if s['drain_days'] else '-':>10}")
    _p(f"\n  {m['assumption_warning']}")


def cmd_console(args):
    from .report import build_console

    p = build_console()
    _p(f"Review console written to {p}")
    _p(f"Open with: open {p}")


def cmd_reset(args):
    store.reset()
    _p("cleared out/cases, out/audit.jsonl and vault/")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mhb", description="Meridian Harbor complaint intake prototype")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("generate").set_defaults(func=cmd_generate)

    r = sub.add_parser("run")
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--engine", default="rules", choices=["rules", "claude"])
    r.add_argument("--rubric", default="A", choices=["A", "B"])
    r.add_argument("--fresh", action="store_true", help="clear prior cases first")
    r.add_argument("-v", "--verbose", action="store_true")
    r.set_defaults(func=cmd_run)

    q = sub.add_parser("queues")
    q.add_argument("--limit", type=int, default=12)
    q.set_defaults(func=cmd_queues)

    s = sub.add_parser("show")
    s.add_argument("case_id")
    s.set_defaults(func=cmd_show)

    a = sub.add_parser("approve")
    a.add_argument("case_id")
    a.add_argument("--approver", required=True)
    a.add_argument("--edit", help="file containing the analyst's edited body")
    a.set_defaults(func=cmd_approve)

    j = sub.add_parser("reject")
    j.add_argument("case_id")
    j.add_argument("--approver", required=True)
    j.add_argument("--note", required=True)
    j.set_defaults(func=cmd_reject)

    d = sub.add_parser("render")
    d.add_argument("case_id")
    d.set_defaults(func=cmd_render)

    sub.add_parser("calibrate").set_defaults(func=cmd_calibrate)
    sub.add_parser("simulate").set_defaults(func=cmd_simulate)
    sub.add_parser("console").set_defaults(func=cmd_console)
    sub.add_parser("reset").set_defaults(func=cmd_reset)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
