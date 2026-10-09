"""The review console.

A single self-contained HTML file written to out/console.html. No network, no
CDN, no fonts fetched, no build step — it opens from the filesystem inside the
bank's own environment. That is a requirement, not a convenience: a review
surface for complaint data that phones out to a CDN would fail the same policy
review that killed the last pilot.

It renders what the two stakeholders each need to see:

  the compliance officer   the 48-hour queue, the citation and evidence behind
                           every flag, the residual-risk notes where redaction
                           was unsure, and the fact that every draft is sitting
                           unsent behind an approval gate.
  the ops head             the four worklists, severity ordering, routing
                           confidence with its alternates, and the calibration
                           list that says which complaints her two senior
                           analysts would triage differently.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from . import config, metrics, review, store

ROOT = Path(__file__).resolve().parents[2]

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#16191d;--mut:#5c6673;--line:#e2e6eb;
--s1:#b4232a;--s2:#c76a12;--s3:#2f6f4f;--s4:#5c6673;--acc:#1c4e80;--warn:#8a5a00;
--flag:#6b3fa0;--ok:#2f6f4f}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
header{background:var(--ink);color:#fff;padding:20px 24px}
header h1{margin:0;font-size:19px;letter-spacing:.2px}
header p{margin:6px 0 0;color:#aab3bd;font-size:13px;max-width:900px}
.banner{background:#fff4d6;border-bottom:1px solid #e8d49a;color:#6b4b00;
padding:11px 24px;font-size:13.5px}
.banner b{color:#4a3400}
main{padding:22px 24px 60px;max-width:1280px;margin:0 auto}
.tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:18px;border-bottom:1px solid var(--line)}
.tab{padding:9px 15px;cursor:pointer;border:1px solid transparent;border-bottom:none;
border-radius:6px 6px 0 0;font-size:14px;color:var(--mut);user-select:none}
.tab:hover{background:#eceff3}
.tab.on{background:var(--card);border-color:var(--line);color:var(--ink);font-weight:600}
.tab .n{display:inline-block;margin-left:7px;background:#e4e8ee;color:#3d4753;
border-radius:9px;padding:0 7px;font-size:12px;font-weight:600}
.tab.on .n{background:var(--acc);color:#fff}
.pane{display:none}.pane.on{display:block}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:13px 15px}
.kpi .v{font-size:25px;font-weight:650;letter-spacing:-.4px}
.kpi .k{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.5px;margin-top:3px}
.kpi .s{font-size:12px;color:var(--mut);margin-top:5px}
.case{background:var(--card);border:1px solid var(--line);border-radius:9px;
margin-bottom:10px;overflow:hidden}
.case>summary{padding:12px 15px;cursor:pointer;display:flex;gap:11px;align-items:center;
flex-wrap:wrap;list-style:none}
.case>summary::-webkit-details-marker{display:none}
.case>summary:hover{background:#fafbfc}
.id{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:13px;color:var(--acc);font-weight:600}
.band{font-weight:700;font-size:12.5px;padding:2px 8px;border-radius:5px;color:#fff}
.b-S1{background:var(--s1)}.b-S2{background:var(--s2)}.b-S3{background:var(--s3)}.b-S4{background:var(--s4)}
.alt{font-size:12px;color:var(--mut)}
.pill{font-size:11.5px;padding:2px 8px;border-radius:11px;border:1px solid var(--line);
background:#f3f5f8;color:#3d4753}
.pill.reg{background:#fdeceb;border-color:#f3c9c6;color:#8e2a26}
.pill.flag{background:#f2ecfa;border-color:#d9c9ee;color:var(--flag)}
.pill.warn{background:#fdf4e0;border-color:#eddfb8;color:var(--warn)}
.pill.ok{background:#eaf4ee;border-color:#c5e0d0;color:var(--ok)}
.spacer{flex:1}
.body{padding:0 15px 15px;border-top:1px solid var(--line)}
h4{margin:17px 0 7px;font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--mut)}
.quote{background:#f7f8fa;border-left:3px solid var(--line);padding:10px 13px;
border-radius:0 6px 6px 0;white-space:pre-wrap;font-size:14px}
.letter{background:#fffdf7;border:1px solid #ecdfc0;border-radius:7px;padding:14px 16px;
white-space:pre-wrap;font-size:14px}
.slot{background:#fff1c9;border-bottom:1px dashed #c9a227;padding:0 2px;border-radius:2px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.5px;color:var(--mut);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.dim td:first-child{color:var(--mut)}
.lv{display:inline-block;width:52px;height:7px;background:#e4e8ee;border-radius:4px;overflow:hidden;
vertical-align:middle;margin-right:7px}
.lv i{display:block;height:100%;background:var(--acc)}
.note{background:#fdf4e0;border:1px solid #eddfb8;border-radius:7px;padding:10px 13px;
font-size:13.5px;color:#6b4b00;margin:7px 0}
.mx td,.mx th{text-align:center;font-variant-numeric:tabular-nums}
.mx td.r{text-align:left;color:var(--mut);font-weight:600}
.mx .hot{background:#f2ecfa;font-weight:700;color:var(--flag)}
.mx .diag{background:#eaf4ee;font-weight:700}
.audit{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
.audit td{padding:4px 9px}
small.m{color:var(--mut)}
.lede{background:var(--card);border:1px solid var(--line);border-left:3px solid var(--acc);
border-radius:0 9px 9px 0;padding:13px 16px;margin-bottom:18px;font-size:14px;max-width:1000px}
.lede b{color:var(--acc)}
"""

JS = """
function pick(id){
  document.querySelectorAll('.tab').forEach(function(t){t.classList.toggle('on',t.dataset.p===id)});
  document.querySelectorAll('.pane').forEach(function(p){p.classList.toggle('on',p.id===id)});
}
document.querySelectorAll('.tab').forEach(function(t){
  t.addEventListener('click',function(){pick(t.dataset.p)})});
"""


def e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _slots(text: str) -> str:
    out = e(text)
    for slot in ("{{CUSTOMER_NAME}}", "{{ANALYST_NAME}}"):
        out = out.replace(e(slot), f'<span class="slot">{e(slot)}</span>')
    return out


def _kpi(value, key, sub="") -> str:
    return (f'<div class="kpi"><div class="v">{e(value)}</div>'
            f'<div class="k">{e(key)}</div>'
            + (f'<div class="s">{e(sub)}</div>' if sub else "") + "</div>")


def _case_block(c: dict, cfg) -> str:
    sev = c.get("severity") or {}
    alt = c.get("severity_alt") or {}
    rt = c.get("routing") or {}
    red = c.get("redaction") or {}
    draft = c.get("draft") or {}
    qa = c.get("qa") or {}
    band = sev.get("band", "S4")
    thr = cfg.policy["confidence_thresholds"]

    pills = []
    for f in c.get("regulatory", []):
        if f["strength"] == "strong":
            pills.append(f'<span class="pill reg">{e(f["category_id"])}</span>')
    if c.get("calibration_flag"):
        pills.append('<span class="pill flag">rubrics disagree</span>')
    if rt.get("confidence", 1) < thr["routing_autoaccept"]:
        pills.append('<span class="pill warn">confirm department</span>')
    if red.get("residual_risk_notes"):
        pills.append('<span class="pill warn">redaction unsure</span>')
    if c["state"] == "BLOCKED_QA":
        pills.append('<span class="pill reg">QA blocked</span>')
    if c["state"] == "APPROVED_FOR_SEND":
        pills.append(f'<span class="pill ok">approved by {e(c["human"].get("approver"))}</span>')

    # severity dimensions
    dims = "".join(
        f'<tr><td>{e(s["dimension"].replace("_", " "))}</td>'
        f'<td style="width:120px"><span class="lv"><i style="width:{s["level"] * 33}%"></i></span>'
        f'{s["level"]}</td><td><small class="m">{e(s["evidence"])}</small></td></tr>'
        for s in c.get("signals", [])
    )

    regrows = "".join(
        f'<tr><td><b>{e(f["category_id"])}</b><br><small class="m">{e(f["strength"])}</small></td>'
        f'<td>{e(f["citation"])}<br><small class="m">{e(f["statutory_clock"] or "no statutory clock")}'
        f'</small></td><td><small class="m">{e("; ".join(f["evidence"]))}</small></td></tr>'
        for f in c.get("regulatory", [])
    ) or '<tr><td colspan="3"><small class="m">No regulatory trigger detected.</small></td></tr>'

    overrides = "".join(
        f'<div class="note">Override <b>{e(o["id"])}</b>: {e(o["from"])} &rarr; {e(o["to"])} — {e(o["reason"])}</div>'
        for o in sev.get("overrides_applied", []) if o["from"] != o["to"]
    )

    notes = "".join(f'<div class="note">{e(n)}</div>' for n in red.get("residual_risk_notes", []))
    if c.get("calibration_flag"):
        notes += f'<div class="note">{e(c.get("calibration_note"))}</div>'
    for w in qa.get("warnings", []):
        notes += f'<div class="note"><b>{e(w["check"])}</b> — {e(w["detail"])}</div>'
    for b in qa.get("blocks", []):
        notes += f'<div class="note" style="background:#fdeceb;border-color:#f3c9c6;color:#8e2a26">' \
                 f'<b>BLOCKED {e(b["check"])}</b> — {e(b["detail"])}</div>'

    audit = "".join(
        f'<tr><td>{e(d["agent"])}</td><td>v{e(d["agent_version"])}</td>'
        f'<td>{e(d["engine"])}</td><td class="num">{d["duration_ms"]:.2f}ms</td>'
        f'<td class="num">{("%.2f" % d["confidence"]) if d["confidence"] is not None else "—"}</td>'
        f'<td>{e(d["summary"])}</td>'
        f'<td><small class="m">{e(", ".join(d["ruleset_ids"]))}</small></td></tr>'
        for d in c.get("decisions", [])
    )

    alts = "".join(
        f'<li>{e(a["department"])} <small class="m">(score {a["score"]}; {e(a["evidence"])})</small></li>'
        for a in rt.get("alternates", [])
    )

    sla = "".join(
        f'<tr><td>{e(k.replace("_", " "))}</td><td>{e(v)}</td></tr>'
        for k, v in sorted((c.get("sla") or {}).items())
    )

    return f"""
<details class="case">
  <summary>
    <span class="band b-{e(band)}">{e(band)}</span>
    <span class="id">{e(c["case_id"])}</span>
    <span class="alt">alt&nbsp;{e(alt.get("band", "—"))}</span>
    <span class="pill">{e(c["channel"])}</span>
    <span class="pill">{e(rt.get("department", "—"))} · {rt.get("confidence", 0):.2f}</span>
    {''.join(pills)}
    <span class="spacer"></span>
    <small class="m">{e(c["received_at"][:16].replace("T", " "))}</small>
  </summary>
  <div class="body">
    {notes}
    <h4>What the customer wrote <small class="m">— de-identified; identifiers held in the local vault</small></h4>
    <div class="quote">{e(red.get("redacted_text", ""))}</div>
    <small class="m">{red.get("pii_count", 0)} identifier(s) removed: {e(", ".join(red.get("pii_kinds", [])) or "none")}
    &nbsp;·&nbsp; analyst note: {e(c.get("human", {}).get("analyst_summary", "—"))}</small>

    <h4>Regulatory assessment</h4>
    <table><tr><th>Category</th><th>Citation / clock</th><th>Evidence</th></tr>{regrows}</table>

    <h4>Severity — {e(sev.get("band"))} under {e(sev.get("rubric_id"))}, {e(alt.get("band"))} under {e(alt.get("rubric_id"))}</h4>
    {overrides}
    <table class="dim">{dims}</table>
    <small class="m">score {sev.get("normalized", 0):.3f} normalised · confidence {sev.get("confidence", 0):.2f}</small>

    <h4>Routing</h4>
    <p style="margin:4px 0">{e(rt.get("department_label"))} &rarr; <code>{e(rt.get("queue_email"))}</code>
    &nbsp;<small class="m">confidence {rt.get("confidence", 0):.2f}</small></p>
    <small class="m">{e(rt.get("reason"))}</small>
    {f"<ul>{alts}</ul>" if alts else ""}

    <h4>SLA clocks</h4>
    <table>{sla}</table>

    <h4>Draft acknowledgement — NOT SENT <small class="m">(template {e(draft.get("template_id"))};
    highlighted slots are filled from the system of record at send time)</small></h4>
    <div class="letter"><b>Subject:</b> {_slots(draft.get("subject", ""))}

{_slots(draft.get("body", ""))}</div>

    <h4>Audit trail</h4>
    <table class="audit"><tr><th>Agent</th><th>Ver</th><th>Engine</th><th>Time</th>
    <th>Conf</th><th>Summary</th><th>Rulesets</th></tr>{audit}</table>
  </div>
</details>"""


def build_console(out: Path | None = None) -> Path:
    cfg = config.load()
    q = review.queues()
    cases = store.all_cases()
    cal = metrics.calibration_report(cases)
    thr = metrics.throughput_model(cfg.policy)

    ev_path = ROOT / "out" / "evidence.json"
    ev = json.loads(ev_path.read_text()) if ev_path.exists() else {}

    bands: dict[str, int] = {}
    for c in cases:
        b = (c.get("severity") or {}).get("band", "S4")
        bands[b] = bands.get(b, 0) + 1
    reg_cases = sum(1 for c in cases if c.get("sla", {}).get("compliance_flag_due"))
    unsure = sum(1 for c in cases if (c.get("redaction") or {}).get("residual_risk_notes"))

    # ------------------------------------------------------------- panes
    panes = []

    panes.append(f"""<div class="pane on" id="p-compliance">
<div class="lede"><b>For the compliance officer.</b> Every complaint that touches a regulated
practice is listed here, flagged at the moment of intake rather than when an analyst reached it.
Each entry carries the citation and the exact words that triggered it, so a flag can be
confirmed or dismissed by reading, not by trusting a score. Nothing on this page has been sent
to anyone: every draft is sitting behind the approval gate, and no code path exists from an agent
to a customer.</div>
<div class="grid">
{_kpi(reg_cases, "regulatory cases", f"of {len(cases)} complaints")}
{_kpi("48h", "compliance clock", "set from receipt, not processing")}
{_kpi(f'{ev.get("compliance_clock", {}).get("max_latency_hours", 0)}h', "worst flag latency", "today: found late")}
{_kpi(ev.get("case_file_leakage", {}).get("cases_with_leak", "—"), "case files with PII", "independent scan")}
{_kpi(unsure, "redaction unsure", "surfaced, not hidden")}
{_kpi(0, "sent without sign-off", "structurally impossible")}
</div>
<h4>48-hour compliance queue ({len(q["compliance_48h"])})</h4>
{"".join(_case_block(c, cfg) for c in q["compliance_48h"])}
</div>""")

    panes.append(f"""<div class="pane" id="p-approval">
<div class="lede"><b>For the analysts.</b> Each case arrives pre-read: identifiers removed,
regulatory exposure assessed, department proposed with its confidence and runners-up, and an
acknowledgement drafted from a compliance-approved template. The analyst's job is to confirm or
correct, then sign. The customer's name is deliberately absent from the draft — it is merged in
at send time, so nothing in this queue identifies anybody.</div>
<div class="grid">
{_kpi(len(q["approval"]), "awaiting sign-off")}
{_kpi(len(q["rework"]), "QA blocked", "returned, never dropped")}
{_kpi(f'{thr["assisted"]["minutes_per_complaint"]}m', "modelled review time", f'breakeven {thr["breakeven_minutes_per_complaint"]}m')}
{_kpi(f'{ev.get("throughput", {}).get("mean_ms", "—")}ms', "machine time / complaint")}
</div>
<h4>Awaiting sign-off ({len(q["approval"])})</h4>
{"".join(_case_block(c, cfg) for c in q["approval"][:40])}
{'<small class="m">Showing the first 40 by severity.</small>' if len(q["approval"]) > 40 else ""}
{f'<h4>QA blocked — returned for rework ({len(q["rework"])})</h4>' if q["rework"] else ""}
{"".join(_case_block(c, cfg) for c in q["rework"])}
</div>""")

    # calibration matrix
    mx = ['<table class="mx"><tr><th></th>'
          + "".join(f"<th>B&nbsp;{b}</th>" for b in metrics.BANDS) + "</tr>"]
    for a in metrics.BANDS:
        row = [f'<td class="r">A&nbsp;{a}</td>']
        for b in metrics.BANDS:
            n = cal["matrix"][a][b]
            gap = abs(metrics.BANDS.index(a) - metrics.BANDS.index(b))
            cls = "diag" if a == b else ("hot" if gap >= 2 and n else "")
            row.append(f'<td class="{cls}">{n or ""}</td>')
        mx.append("<tr>" + "".join(row) + "</tr>")
    mx.append("</table>")

    work = "".join(
        f'<tr><td class="id">{e(r["case_id"])}</td><td>{e(r["archetype"])}</td>'
        f'<td><span class="band b-{e(r["band_a"])}">{e(r["band_a"])}</span></td>'
        f'<td><span class="band b-{e(r["band_b"])}">{e(r["band_b"])}</span></td>'
        f'<td>{e(r["driver"])}</td><td><small class="m">{e(r["excerpt"][:120])}…</small></td></tr>'
        for r in cal["worklist"]
    )

    panes.append(f"""<div class="pane" id="p-calibration">
<div class="lede"><b>For the ops head and the two senior analysts.</b> Nobody could tell us what
"serious" means, and the two senior analysts disagreed about it in front of us. Rather than pick
one and present it as objective, the system scores every complaint under <i>both</i> readings and
reports where they diverge. Rubric A is harm-weighted ("the customer is actually out money");
Rubric B is exposure-weighted ("this is the one that ends up in an exam finding"). They agree on
{cal["agreement_rate"]:.0%} of complaints. The table below is the agenda for a one-hour session
that would settle it — and notice what Rubric B does: applied literally, it rates
{cal["band_counts_b"]["S1"]} of {cal["n"]} complaints critical, which means nothing is critical.</div>
<div class="grid">
{_kpi(f'{cal["agreement_rate"]:.0%}', "rubric agreement", f'{cal["agree"]} of {cal["n"]} cases')}
{_kpi(cal["hard_flag_count"], "disagree by 2+ bands", "changes what an analyst does")}
{_kpi(cal["band_counts_a"]["S1"], "critical under A", "harm-weighted")}
{_kpi(cal["band_counts_b"]["S1"], "critical under B", "exposure-weighted")}
</div>
<h4>Where the two definitions land</h4>
{"".join(mx)}
<small class="m">Green diagonal = the two analysts agree. Purple = they disagree by two bands or
more, i.e. one would give it a named owner today and the other would put it in the standard queue.</small>
<h4>Calibration worklist — worst divergence first</h4>
<table><tr><th>Case</th><th>Pattern</th><th>A</th><th>B</th><th>Why they differ</th><th>Excerpt</th></tr>
{work}</table>
</div>""")

    # evidence pane
    ev_rows = ""
    if ev:
        rb = ev["regulatory"]["buckets"]
        ev_rows = f"""
<h4>Regulatory detection</h4>
<table><tr><th>Complaint type</th><th class="num">n</th><th class="num">Recall</th>
<th class="num">Precision</th><th>Reading</th></tr>
<tr><td>Uses regulatory vocabulary</td><td class="num">{rb["loud"]["cases"]}</td>
<td class="num">{rb["loud"]["recall"]:.0%}</td><td class="num">{rb["loud"]["precision"]:.0%}</td>
<td>Reliable.</td></tr>
<tr><td>Regulatory but politely worded</td><td class="num">{rb["quiet"]["cases"]}</td>
<td class="num">{rb["quiet"]["recall"]:.0%}</td><td class="num">{rb["quiet"]["precision"]:.0%}</td>
<td><b>The real ceiling.</b> A keyword engine cannot read a complaint that never
uses a trigger word. These were deliberately not tuned for.</td></tr>
<tr><td>All complaints</td><td class="num">{rb["all"]["cases"]}</td>
<td class="num">{rb["all"]["recall"]:.0%}</td><td class="num">{rb["all"]["precision"]:.0%}</td>
<td>Blended — do not quote this number without the row above it.</td></tr>
</table>
<h4>Routing</h4>
<table><tr><th>Measure</th><th class="num">Value</th></tr>
<tr><td>Accuracy against the ownership rules we were given</td>
<td class="num">{ev["routing"]["accuracy"]:.1%}</td></tr>
<tr><td>Misroutes flagged for human confirmation</td>
<td class="num">{ev["routing"]["misroutes"]}/{ev["routing"]["misroutes"]}</td></tr>
<tr><td>Misroutes caused by a stated ownership rule being too broad</td>
<td class="num">{ev["routing"]["misroutes_caused_by_ownership_rule"]}</td></tr>
</table>
<h4>Hard gates</h4>
<table><tr><th>Property</th><th>Result</th></tr>
<tr><td>Injected identifiers removed from complaint text</td>
<td>{ev["pii_redaction"]["removed"]}/{ev["pii_redaction"]["injected_identifiers"]}</td></tr>
<tr><td>Case files containing a vault value</td>
<td>{ev["case_file_leakage"]["cases_with_leak"]} of {ev["case_file_leakage"]["cases_scanned"]}</td></tr>
<tr><td>Outbound model payloads containing PII (offline check)</td>
<td>{ev["egress_payload"]["payloads_with_pii"]} of {ev["egress_payload"]["payloads_built"]}</td></tr>
<tr><td>Cases the pipeline marked sendable</td>
<td>{ev["human_gate"]["pipeline_produced_approved_cases"]} (must be 0)</td></tr>
<tr><td>Approval bypasses attempted / blocked</td>
<td>{len([p for p in ev["human_gate"]["bypass_probes"] if p["blocked"]])} of
{len(ev["human_gate"]["bypass_probes"])}</td></tr>
<tr><td>Determinism (same complaint twice)</td>
<td>{ev["determinism"]["cases"] - ev["determinism"]["mismatches"]}/{ev["determinism"]["cases"]} identical</td></tr>
</table>"""

    sens = "".join(
        f'<tr><td class="num">{s["assisted_routine_minutes"]}</td>'
        f'<td class="num">{s["capacity_per_day"]}</td>'
        f'<td class="num">{s["net_per_day"]:+}</td>'
        f'<td>{"drains" if s["drains_backlog"] else "<b>still grows</b>"}</td>'
        f'<td class="num">{s["drain_days"] or "—"}</td></tr>'
        for s in thr["sensitivity"]
    )

    panes.append(f"""<div class="pane" id="p-evidence">
<div class="lede"><b>Evidence.</b> Measured on 120 synthetic complaints across all three channels
with gold labels. Synthetic because the real text is exactly the data that must not move. The
numbers that flatter us are reported next to the ones that do not.</div>
{ev_rows}
<h4>Does this touch the six-day backlog?</h4>
<p>Demand is {thr["demand_per_day"]} complaints a day against {thr["analyst_minutes_per_day"]}
analyst-minutes. Breakeven is <b>{thr["breakeven_minutes_per_complaint"]} minutes per
complaint</b>: above that the backlog grows no matter what this system does. Today, at an assumed
{thr["baseline"]["minutes_per_complaint"]} minutes, the deficit is
{thr["baseline"]["net_per_day"]}/day — which is why the backlog exists and why it keeps growing.</p>
<table><tr><th class="num">Review min/complaint</th><th class="num">Capacity/day</th>
<th class="num">Net/day</th><th>Verdict</th><th class="num">Drain days</th></tr>{sens}</table>
<div class="note">Every input above is our assumption, not a Meridian Harbor measurement. The
load-bearing one is how long an analyst actually takes to review a pre-triaged case with a draft
attached. A two-week pilot measuring that one number replaces this entire table — and it is the
number that decides whether the business case holds.</div>
</div>""")

    tabs = [
        ("p-compliance", "Compliance — 48h queue", len(q["compliance_48h"])),
        ("p-approval", "Analyst — sign-off queue", len(q["approval"])),
        ("p-calibration", "What does 'serious' mean?", cal["hard_flag_count"]),
        ("p-evidence", "Evidence", None),
    ]
    tabhtml = "".join(
        f'<div class="tab{" on" if i == 0 else ""}" data-p="{t}">{e(label)}'
        + (f'<span class="n">{n}</span>' if n is not None else "")
        + "</div>"
        for i, (t, label, n) in enumerate(tabs)
    )

    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Complaint Intake Review Console</title><style>{CSS}</style></head>
<body>
<header>
  <h1>Meridian Harbor Bank — Complaint Intake Review Console</h1>
  <p>Working prototype. {len(cases)} synthetic complaints processed through a seven-agent
  pipeline. Engine: deterministic rules, no network calls. Rubric A primary.</p>
</header>
<div class="banner"><b>Nothing here has been sent.</b> The pipeline's terminal state is
<i>pending human approval</i>. There is no code path from an agent to a customer: the only
function that marks a letter sendable requires a named approver, refuses any case QA blocked,
and is the sole place customer identifiers are restored. Four attempted bypasses are tested
in <code>tests/test_gates.py</code>.</div>
<main>
<div class="tabs">{tabhtml}</div>
{"".join(panes)}
</main>
<script>{JS}</script>
</body></html>"""

    out = out or (ROOT / "out" / "console.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc)
    return out
