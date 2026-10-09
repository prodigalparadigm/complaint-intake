"""Evaluation harness.

Produces out/evidence.json and a printed report. Run after `mhb run`.

The metrics are chosen by who has to be convinced:

  COMPLIANCE OFFICER  regulatory recall (the number that decides whether she
                      can trust the 48-hour flag), PII leakage (must be zero),
                      and the human-approval gate (must be unbypassable).
  OPS HEAD            routing accuracy, throughput, and how much rewriting the
                      drafts actually save.
  BOTH                determinism, and the severity disagreement rate that
                      says the definition is still open.

Recall and precision are reported separately for the "loud" complaints that
use trigger vocabulary and the "quiet" ones that do not. Reporting a single
blended number would hide the keyword engine's actual blind spot, which is the
one thing a compliance officer most needs to know before relying on it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from mhb import clock, config, corpus, leak, metrics, store  # noqa: E402
from mhb.engines.claude import ClaudeEngine  # noqa: E402
from mhb.orchestrator import Pipeline  # noqa: E402

BAR = "─" * 78


# --------------------------------------------------------------------- 1. PII


def eval_pii_redaction(cases: list[dict]) -> dict:
    total = found = 0
    misses = []
    for c in cases:
        gold = (c.get("gold") or {}).get("pii_values", [])
        red = (c.get("redaction") or {}).get("redacted_text", "")
        subj = (c.get("redaction") or {}).get("redacted_subject", "")
        hay = red + "\n" + subj
        for v in gold:
            total += 1
            if str(v) not in hay:
                found += 1
            else:
                misses.append({"case_id": c["case_id"], "channel": c["channel"],
                               "kind": _kind(v), "fingerprint": f"{str(v)[:2]}***{str(v)[-2:]}"})
    return {
        "injected_identifiers": total,
        "removed": found,
        "recall": round(found / total, 4) if total else 1.0,
        "misses": misses[:20],
        "miss_count": len(misses),
    }


def _kind(v: str) -> str:
    v = str(v)
    if "@" in v:
        return "EMAIL"
    if v.startswith("("):
        return "PHONE"
    if v.count("-") == 2 and len(v) == 11:
        return "SSN"
    if v[:1].isdigit() and " " in v:
        return "ADDRESS"
    return "ACCOUNT_OR_ROUTING"


def eval_case_file_leakage(cases: list[dict]) -> dict:
    vault = store.all_vault_values()
    leaking = []
    for c in cases:
        hits = leak.scan_case(c, vault)
        if hits:
            leaking.append({"case_id": c["case_id"], "hits": hits})
    return {
        "cases_scanned": len(cases),
        "vault_values": len(vault),
        "cases_with_leak": len(leaking),
        "detail": leaking[:10],
        "pass": not leaking,
    }


def eval_egress_payload(cases: list[dict], cfg) -> dict:
    """What would actually be transmitted if the Claude engine were enabled.

    Builds the real outbound request for every case and scans it. No network
    call — this is the offline proof that the model path cannot carry PII.
    """
    engine = ClaudeEngine(cfg)
    vault = store.all_vault_values()
    violations, built = [], 0
    for c in cases:
        text = (c.get("redaction") or {}).get("redacted_text", "")
        req = engine.build_request(text, c.get("human", {}).get("metadata", {}))
        built += 1
        hits = leak.scan(json.dumps(req), vault)
        if hits:
            violations.append({"case_id": c["case_id"], "hits": hits})
        try:
            ClaudeEngine._egress_guard(req, vault)
        except Exception as e:  # pragma: no cover - would indicate a real leak
            violations.append({"case_id": c["case_id"], "guard": str(e)})
    return {
        "payloads_built": built,
        "payloads_with_pii": len(violations),
        "detail": violations[:10],
        "pass": not violations,
        "note": "offline check — no request was sent; no credentials were present on the build host",
    }


# -------------------------------------------------------------- 2. regulatory


def eval_regulatory(cases: list[dict]) -> dict:
    def split(c):
        return "quiet" if (c.get("gold") or {}).get("quiet_regulatory") else "loud"

    buckets = {"loud": _reg_bucket(), "quiet": _reg_bucket(), "all": _reg_bucket()}
    per_cat: dict[str, dict] = {}
    misses = []

    for c in cases:
        gold = set((c.get("gold") or {}).get("regulatory", []))
        pred = {f["category_id"] for f in c.get("regulatory", []) if f["strength"] == "strong"}
        b = split(c)
        # Case-level: did we flag this complaint to Compliance at all?
        for key in (b, "all"):
            buckets[key]["cases"] += 1
            if gold and pred:
                buckets[key]["case_tp"] += 1
            elif gold and not pred:
                buckets[key]["case_fn"] += 1
                misses.append({"case_id": c["case_id"], "archetype": c.get("archetype"),
                               "channel": c["channel"], "missed": sorted(gold),
                               "quiet": b == "quiet",
                               "excerpt": (c.get("redaction") or {}).get("redacted_text", "")[:150]})
            elif not gold and pred:
                buckets[key]["case_fp"] += 1
            else:
                buckets[key]["case_tn"] += 1
        # Category-level
        for cat in gold | pred:
            d = per_cat.setdefault(cat, {"tp": 0, "fp": 0, "fn": 0})
            if cat in gold and cat in pred:
                d["tp"] += 1
            elif cat in gold:
                d["fn"] += 1
            else:
                d["fp"] += 1

    for b in buckets.values():
        tp, fn, fp = b["case_tp"], b["case_fn"], b["case_fp"]
        b["recall"] = round(tp / (tp + fn), 4) if tp + fn else None
        b["precision"] = round(tp / (tp + fp), 4) if tp + fp else None

    for cat, d in per_cat.items():
        d["recall"] = round(d["tp"] / (d["tp"] + d["fn"]), 3) if d["tp"] + d["fn"] else None
        d["precision"] = round(d["tp"] / (d["tp"] + d["fp"]), 3) if d["tp"] + d["fp"] else None

    return {"buckets": buckets, "per_category": dict(sorted(per_cat.items())),
            "case_level_misses": misses}


def _reg_bucket():
    return {"cases": 0, "case_tp": 0, "case_fp": 0, "case_fn": 0, "case_tn": 0}


def eval_compliance_clock(cases: list[dict], cfg) -> dict:
    window = cfg.policy["sla_hours"]["compliance_flag"]
    reg = [c for c in cases if c.get("sla", {}).get("compliance_flag_due")]
    latencies = [c["sla"].get("compliance_flag_latency_hours", 0.0) for c in reg]
    within = sum(1 for c in reg if clock.hours_between(
        c["received_at"], c["sla"]["compliance_flagged_at"]) <= window)
    missing_clock = [
        c["case_id"] for c in cases
        if {f["category_id"] for f in c.get("regulatory", []) if f["strength"] == "strong"}
        and not c.get("sla", {}).get("compliance_flag_due")
    ]
    return {
        "window_hours": window,
        "regulatory_cases": len(reg),
        "flagged_within_window": within,
        "rate": round(within / len(reg), 4) if reg else None,
        "max_latency_hours": max(latencies) if latencies else 0.0,
        "cases_missing_clock": missing_clock,
        "pass": not missing_clock and within == len(reg),
        "baseline_note": "Current manual process: discovered late (client stated). "
                         "Pipeline latency is the pipeline's own runtime, ~2 ms.",
    }


# ----------------------------------------------------------------- 3. routing


def eval_routing(cases: list[dict]) -> dict:
    correct = 0
    wrong, tiebreaker_wrong = [], []
    for c in cases:
        gold = (c.get("gold") or {}).get("department")
        pred = (c.get("routing") or {}).get("department")
        if not gold:
            continue
        if gold == pred:
            correct += 1
        else:
            row = {"case_id": c["case_id"], "archetype": c.get("archetype"),
                   "gold": gold, "predicted": pred,
                   "reason": (c.get("routing") or {}).get("reason", "")[:90],
                   "tiebreaker": (c.get("routing") or {}).get("tiebreaker_applied")}
            wrong.append(row)
            if row["tiebreaker"]:
                tiebreaker_wrong.append(row)
    n = sum(1 for c in cases if (c.get("gold") or {}).get("department"))
    by_cause: dict[str, int] = {}
    for w in wrong:
        key = f"{w['gold']} -> {w['predicted']}" + (f" (rule {w['tiebreaker']})" if w["tiebreaker"] else "")
        by_cause[key] = by_cause.get(key, 0) + 1
    return {
        "n": n,
        "correct": correct,
        "accuracy": round(correct / n, 4) if n else None,
        "misroutes": len(wrong),
        "misroutes_caused_by_ownership_rule": len(tiebreaker_wrong),
        "by_cause": dict(sorted(by_cause.items(), key=lambda kv: -kv[1])),
        "detail": wrong[:15],
    }


# ---------------------------------------------------------------- 4. severity


def eval_severity_floor(cases: list[dict]) -> dict:
    order = {"S1": 0, "S2": 1, "S3": 2, "S4": 3}
    checked, ok = 0, 0
    violations = []
    for c in cases:
        floor = (c.get("gold") or {}).get("severity_floor")
        if not floor:
            continue
        checked += 1
        a = (c.get("severity") or {}).get("band", "S4")
        b = (c.get("severity_alt") or {}).get("band", "S4")
        best = min(order[a], order[b])
        if best <= order[floor]:
            ok += 1
        else:
            violations.append({"case_id": c["case_id"], "archetype": c.get("archetype"),
                               "floor": floor, "band_a": a, "band_b": b})
    return {
        "cases_with_floor": checked,
        "met_under_at_least_one_rubric": ok,
        "rate": round(ok / checked, 4) if checked else None,
        "violations": violations[:15],
        "note": "A 'floor' is a band BOTH senior analysts would call a minimum. Checked against "
                "the more severe of the two rubrics, because clearing it under either is "
                "sufficient to prove the signal was seen.",
    }


# ------------------------------------------------------------------- 5. gates


def eval_human_gate(cases: list[dict]) -> dict:
    from mhb import review

    sent = [c["case_id"] for c in cases if c["state"] == "APPROVED_FOR_SEND"]
    unapproved_sent = [c["case_id"] for c in cases
                       if c["state"] == "APPROVED_FOR_SEND" and not c["human"].get("approver")]
    terminal_ok = all(
        c["state"] in ("PENDING_HUMAN_APPROVAL", "BLOCKED_QA", "APPROVED_FOR_SEND",
                       "RETURNED_FOR_REWORK")
        for c in cases
    )

    # Four attempted bypasses against a live case.
    probes = []
    target = next((c["case_id"] for c in cases if c["state"] == "PENDING_HUMAN_APPROVAL"), None)
    if target:
        for label, fn in (
            ("approve with empty approver", lambda: review.approve(target, "")),
            ("approve with whitespace approver", lambda: review.approve(target, "   ")),
            ("render before approval", lambda: review.render_for_send(target)),
        ):
            try:
                fn()
                probes.append({"probe": label, "blocked": False})
            except review.ApprovalError as e:
                probes.append({"probe": label, "blocked": True, "error": str(e)[:90]})
        blocked_case = next((c["case_id"] for c in cases if c["state"] == "BLOCKED_QA"), None)
        if blocked_case:
            try:
                review.approve(blocked_case, "Someone")
                probes.append({"probe": "approve a QA-blocked case", "blocked": False})
            except review.ApprovalError as e:
                probes.append({"probe": "approve a QA-blocked case", "blocked": True,
                               "error": str(e)[:90]})
        else:
            probes.append({"probe": "approve a QA-blocked case", "blocked": None,
                           "note": "no QA-blocked case in this run; covered by tests/test_gates.py"})

    return {
        "pipeline_produced_approved_cases": len(sent),
        "approved_without_named_approver": len(unapproved_sent),
        "terminal_states_valid": terminal_ok,
        "bypass_probes": probes,
        "pass": not sent and not unapproved_sent and terminal_ok
        and all(p["blocked"] in (True, None) for p in probes),
    }


# ------------------------------------------------------- 6. determinism, perf


def eval_determinism(cfg) -> dict:
    pipe = Pipeline(cfg, "rules")
    rows = corpus.load(25)
    first = [pipe.run(corpus.to_raw(r), persist=False) for r in rows]
    second = [pipe.run(corpus.to_raw(r), persist=False) for r in rows]

    def fingerprint(c):
        return json.dumps({
            "band": c.severity.band, "band_alt": c.severity_alt.band,
            "dept": c.routing.department, "reg": sorted(c.regulatory_ids),
            "draft": c.draft.body, "state": c.state,
            "dims": c.severity.dimension_levels,
        }, sort_keys=True)

    mismatches = [a.case_id for a, b in zip(first, second) if fingerprint(a) != fingerprint(b)]
    return {"cases": len(first), "mismatches": len(mismatches),
            "detail": mismatches[:5], "pass": not mismatches}


def eval_throughput(cases: list[dict]) -> dict:
    per_case, per_agent = [], {}
    for c in cases:
        total = 0.0
        for d in c["decisions"]:
            total += d["duration_ms"]
            per_agent.setdefault(d["agent"], []).append(d["duration_ms"])
        per_case.append(total)
    per_case.sort()
    return {
        "cases": len(per_case),
        "mean_ms": round(sum(per_case) / len(per_case), 2) if per_case else 0,
        "p95_ms": round(per_case[int(0.95 * (len(per_case) - 1))], 2) if per_case else 0,
        "max_ms": round(per_case[-1], 2) if per_case else 0,
        "per_agent_mean_ms": {k: round(sum(v) / len(v), 3) for k, v in sorted(per_agent.items())},
        "projected_monthly_wall_seconds": round(sum(per_case) / len(per_case) * 1400 / 1000, 1)
        if per_case else 0,
    }


def eval_draft_quality(cases: list[dict]) -> dict:
    blocks = warns = 0
    with_block = []
    template_use: dict[str, int] = {}
    for c in cases:
        d = c.get("draft") or {}
        template_use[d.get("template_id", "?")] = template_use.get(d.get("template_id", "?"), 0) + 1
        b = [f for f in d.get("lint_findings", []) if f["severity"] == "block"]
        blocks += len(b)
        warns += len(d.get("lint_findings", [])) - len(b)
        if b and c["state"] == "PENDING_HUMAN_APPROVAL":
            with_block.append(c["case_id"])
    return {
        "drafts": len(cases),
        "block_findings": blocks,
        "warn_findings": warns,
        "approval_queue_cases_with_block_findings": len(with_block),
        "template_distribution": dict(sorted(template_use.items())),
        "pass": not with_block,
    }


# -------------------------------------------------------------------- runner


def attach_gold(cases: list[dict]) -> list[dict]:
    """Join gold labels in memory, keyed on complaint_id.

    Gold labels include the raw injected identifier values, so they must never
    be written into a case file — doing so put PII back into every case and was
    caught by the independent leak scanner. They live only here, in the eval
    process, and only for the length of the run.
    """
    by_cid = {r["complaint_id"]: r for r in corpus.load()}
    joined = []
    for c in cases:
        row = by_cid.get(c["complaint_id"], {})
        joined.append({**c, "gold": row.get("gold", {}), "archetype": row.get("archetype", "")})
    return joined


def main() -> None:
    cfg = config.load()
    cases = store.all_cases()
    if not cases:
        print("No cases found. Run:  PYTHONPATH=src python3 -m mhb run --fresh")
        raise SystemExit(1)
    stored = cases
    cases = attach_gold(cases)

    ev = {
        "corpus": {"cases": len(cases),
                   "channels": _count(cases, lambda c: c["channel"]),
                   "archetypes": len({c.get("archetype") for c in cases})},
        "pii_redaction": eval_pii_redaction(cases),
        "case_file_leakage": eval_case_file_leakage(stored),
        "egress_payload": eval_egress_payload(stored, cfg),
        "regulatory": eval_regulatory(cases),
        "compliance_clock": eval_compliance_clock(cases, cfg),
        "routing": eval_routing(cases),
        "severity_floor": eval_severity_floor(cases),
        "severity_calibration": {k: v for k, v in metrics.calibration_report(stored).items()
                                 if k != "disagreements"},
        "human_gate": eval_human_gate(stored),
        "determinism": eval_determinism(cfg),
        "throughput": eval_throughput(stored),
        "draft_quality": eval_draft_quality(stored),
    }

    out = ROOT / "out" / "evidence.json"
    out.write_text(json.dumps(ev, indent=2, sort_keys=True))
    _report(ev)
    print(f"\nFull evidence written to {out.relative_to(ROOT)}")

    hard_gates = {
        "no PII in case files": ev["case_file_leakage"]["pass"],
        "no PII in outbound model payloads": ev["egress_payload"]["pass"],
        "human approval gate holds": ev["human_gate"]["pass"],
        "every regulatory case has a 48h clock": ev["compliance_clock"]["pass"],
        "pipeline is deterministic": ev["determinism"]["pass"],
        "no blocked draft in the approval queue": ev["draft_quality"]["pass"],
    }
    print("\n" + BAR)
    print("HARD GATES")
    print(BAR)
    for k, v in hard_gates.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    if not all(hard_gates.values()):
        raise SystemExit(1)


def _count(rows, key):
    out: dict = {}
    for r in rows:
        out[key(r)] = out.get(key(r), 0) + 1
    return dict(sorted(out.items()))


def _report(ev: dict) -> None:
    p = print
    p(BAR)
    p("EVIDENCE — Meridian Harbor complaint intake prototype")
    p(BAR)
    c = ev["corpus"]
    p(f"\nCorpus: {c['cases']} synthetic complaints, {c['archetypes']} archetypes, {c['channels']}")

    p("\n1. PII — THE REDACTION BOUNDARY")
    r = ev["pii_redaction"]
    p(f"   injected identifiers removed from text   {r['removed']}/{r['injected_identifiers']}  "
      f"({r['recall']:.1%} recall)")
    if r["misses"]:
        kinds = _count(r["misses"], lambda m: f"{m['kind']}/{m['channel']}")
        p(f"   MISSES by kind/channel                   {kinds}")
    l = ev["case_file_leakage"]
    p(f"   case files containing a vault value      {l['cases_with_leak']}/{l['cases_scanned']}")
    e = ev["egress_payload"]
    p(f"   outbound model payloads containing PII   {e['payloads_with_pii']}/{e['payloads_built']}  "
      f"(offline check)")

    p("\n2. REGULATORY TRIPWIRE — WHAT COMPLIANCE CARES ABOUT")
    b = ev["regulatory"]["buckets"]
    for name in ("all", "loud", "quiet"):
        d = b[name]
        rec = f"{d['recall']:.1%}" if d["recall"] is not None else "n/a"
        pre = f"{d['precision']:.1%}" if d["precision"] is not None else "n/a"
        p(f"   {name:<6} n={d['cases']:<4} recall {rec:<7} precision {pre:<7} "
          f"(tp={d['case_tp']} fn={d['case_fn']} fp={d['case_fp']})")
    p("   per category:")
    for cat, d in ev["regulatory"]["per_category"].items():
        rec = f"{d['recall']:.0%}" if d["recall"] is not None else "  - "
        pre = f"{d['precision']:.0%}" if d["precision"] is not None else "  - "
        p(f"     {cat:<20} recall {rec:>5}  precision {pre:>5}  (tp={d['tp']} fn={d['fn']} fp={d['fp']})")
    k = ev["compliance_clock"]
    p(f"   48h clock set on regulatory cases        {k['flagged_within_window']}/{k['regulatory_cases']}")
    p(f"   worst flag latency                       {k['max_latency_hours']} hours")

    p("\n3. ROUTING")
    rt = ev["routing"]
    p(f"   accuracy vs ops-head ownership rules     {rt['correct']}/{rt['n']}  ({rt['accuracy']:.1%})")
    p(f"   misroutes caused by a stated ownership rule  {rt['misroutes_caused_by_ownership_rule']}")
    for cause, n in list(rt["by_cause"].items())[:6]:
        p(f"     {n:>3}  {cause}")

    p("\n4. SEVERITY — THE UNDEFINED TERM")
    sf = ev["severity_floor"]
    p(f"   obvious-floor cases met                  {sf['met_under_at_least_one_rubric']}/"
      f"{sf['cases_with_floor']}")
    cal = ev["severity_calibration"]
    p(f"   rubric A vs B agreement                  {cal['agree']}/{cal['n']}  "
      f"({cal['agreement_rate']:.1%})")
    p(f"   disagree by 2+ bands                     {cal['hard_flag_count']}")
    p(f"   band distribution A                      {cal['band_counts_a']}")
    p(f"   band distribution B                      {cal['band_counts_b']}")

    p("\n5. THE HUMAN GATE")
    g = ev["human_gate"]
    p(f"   cases the pipeline marked sendable       {g['pipeline_produced_approved_cases']}  (must be 0)")
    for pr in g["bypass_probes"]:
        status = "blocked" if pr["blocked"] else ("n/a" if pr["blocked"] is None else "NOT BLOCKED")
        p(f"   probe: {pr['probe']:<34} {status}")

    p("\n6. OPERATIONAL")
    t = ev["throughput"]
    p(f"   mean / p95 / max per complaint           {t['mean_ms']} / {t['p95_ms']} / {t['max_ms']} ms")
    p(f"   1400 complaints/month cost               {t['projected_monthly_wall_seconds']} seconds of compute")
    d = ev["determinism"]
    p(f"   determinism (same input twice)           {d['cases'] - d['mismatches']}/{d['cases']} identical")
    dq = ev["draft_quality"]
    p(f"   drafts: block findings / warn findings   {dq['block_findings']} / {dq['warn_findings']}")
    p(f"   templates used                           {dq['template_distribution']}")


if __name__ == "__main__":
    main()
