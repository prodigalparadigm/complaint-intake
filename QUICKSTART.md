# Quickstart

For whoever picks this up next — a teammate running the demo, or an engineer
about to change something. Every command below was run against this commit.

---

## 60 seconds

```bash
git clone https://github.com/prodigalparadigm/complaint-intake.git
cd complaint-intake
make all
open out/console.html
```

`make all` generates the corpus, runs 120 complaints through the pipeline,
measures the result against six hard gates, runs 112 tests, and builds the
review console. It takes about ten seconds.

**Prerequisites:** Python 3.11+ and `make`. That's it — no pip install, no
virtualenv, no API key, no network. The default engine is deterministic and
makes no outbound call, which is the point rather than a convenience.

If `make all` ends with six `PASS` lines and `OK`, everything works.

---

## The demo, in the order it should be shown

### 1. Run the pipeline

```bash
make run
```

Read the summary it prints. The line that matters is the last one: the terminal
state is `PENDING_HUMAN_APPROVAL` for all 120 cases. The pipeline has no
transition out of it.

### 2. Open the console

```bash
make console && open out/console.html
```

A single self-contained HTML file. No CDN, no fonts fetched, no build step — it
opens from disk inside the bank's own network, which is a requirement and not a
nicety. Four tabs: the compliance 48-hour queue, the analyst sign-off queue, the
severity calibration worklist, and the evidence.

### 3. Walk one case end to end

`MHB-TWFN5ZS6` is the best single case to demo. It is a **call-centre note**, so
it shows shorthand normalisation; it has **four identifiers redacted** including
a routing number; it trips **Reg E** with a citation and a statutory clock; and
the **two rubrics disagree by two bands**, so it carries a calibration flag.

```bash
PYTHONPATH=src python3 -m mhb show MHB-TWFN5ZS6
```

Things to point at in that output, in order:

- **Redacted complaint** — `[NAME_1]`, `[PHONE_1]`, `[ACCT_1]`, `[ROUTING_1]`.
  The dollar figure survives on purpose: it carries the harm signal and is not
  identifying on its own.
- **The residual-risk note** — "Possible person or place name not covered by a
  redaction rule: Pine Hollow". The redactor says what it was unsure about
  instead of silently claiming success.
- **Regulatory** — not just "regulatory", but `Reg E — 12 CFR 1005.11`, a
  10-business-day clock, and the exact words that triggered it
  (`atm did not dispense`). A flag is confirmed by reading, not by trusting a
  score.
- **Severity** — Rubric A says S3, Rubric B says S1, and the case is flagged for
  calibration. This is the system reporting that the two senior analysts would
  triage it differently, rather than picking one and hiding the disagreement.
- **Routing** — `confidence 0.95`, applied by the ops head's own ownership rule,
  with the runner-up listed.
- **Audit trail** — every agent, its version, the ruleset ids it used, its
  timing and its confidence.

### 4. Show that it cannot send

This is the part the compliance officer cares about. All three are expected to
fail:

```bash
PYTHONPATH=src python3 -m mhb render MHB-TWFN5ZS6
PYTHONPATH=src python3 -m mhb approve MHB-TWFN5ZS6 --approver ""
PYTHONPATH=src python3 -m mhb approve MHB-TWFN5ZS6 --approver "   "
```

```
REFUSED: refusing to render case MHB-TWFN5ZS6: state is PENDING_HUMAN_APPROVAL,
         and rehydration of customer data requires an approved case
REFUSED: approval requires a named approver; anonymous approval is not permitted
```

### 5. Then approve as a named human, and render

```bash
PYTHONPATH=src python3 -m mhb approve MHB-TWFN5ZS6 --approver "D. Okafor, Senior Analyst"
PYTHONPATH=src python3 -m mhb render  MHB-TWFN5ZS6
```

The rendered letter is addressed to the real customer by name, with the real
account number restored. Contrast it with what sits in the review queue, which
is de-identified:

```bash
PYTHONPATH=src python3 -c "
import sys; sys.path.insert(0,'src')
from mhb import store
print(store.load_case('MHB-TWFN5ZS6')['draft']['body'])"
```

The draft in the queue says `{{CUSTOMER_NAME}}`. That slot is filled only at
send time, by `review.render_for_send()`, which is the single rehydration point
in the system and refuses to run on an unapproved case. A draft sitting in the
queue identifies nobody.

An analyst can edit before signing, and the edit is recorded as a diff — which
is the honest measure of whether drafting actually saves time:

```bash
PYTHONPATH=src python3 -m mhb approve <CASE_ID> --approver "Name" --edit edited.txt
```

### 6. The two analyses

```bash
make calibrate   # where the two definitions of "serious" diverge
make simulate    # backlog arithmetic, with the sensitivity table
```

`calibrate` prints the confusion matrix and the worklist. The headline is that
the two rubrics agree on **14%** of complaints, and that Rubric B — applied
literally — rates 86 of 120 complaints critical, which means none are.

`simulate` prints the capacity model. Read the **sensitivity table**, not the
headline: it shows the plan works down to about 16 minutes of review time per
complaint and fails above it. Every input is an assumption, not a Meridian
Harbor measurement.

### 7. The evidence

```bash
make evals
```

Writes `out/evidence.json` and enforces six hard gates. It exits non-zero if any
gate fails, so it is safe to put in CI.

---

## Useful commands

| Command | What it does |
|---|---|
| `make all` | Everything: data, pipeline, evals, tests, console |
| `make run` | Pipeline only — rewrites `out/cases/` and `out/audit.jsonl` |
| `make test` | 112 tests, stdlib `unittest`, no dependencies |
| `make clean` | Clears `out/` and `vault/`. Never touches `config/` or `data/` |
| `mhb queues` | The four worklists |
| `mhb show <ID>` | One case, with its full audit trail |
| `mhb approve <ID> --approver "Name"` | The only way to a sendable letter |
| `mhb reject <ID> --approver "Name" --note "..."` | Return for rework |
| `mhb render <ID>` | The letter as the customer receives it |
| `mhb run --rubric B` | Re-run with the other severity rubric as primary |
| `mhb run --limit 10 -v` | Small, verbose run while developing |

`mhb` means `PYTHONPATH=src python3 -m mhb`. Set `alias mhb='PYTHONPATH=src python3 -m mhb'`
from the repo root if you are going to use it much.

**Case ids are stable.** They hash the complaint id and receipt time, not the
text, so `MHB-TWFN5ZS6` is the same case on your machine as on mine, and stays
the same if you edit the corpus wording.

---

## Changing behaviour without touching code

Every contestable judgment is a versioned file in `config/`, and every decision
in a case file records the ruleset id that produced it. Edit the file, re-run,
and the audit trail reflects it.

| File | Change this to... | Owner |
|---|---|---|
| `regulatory_rules.json` | add a trigger, a category, or retune `min_hits` | Compliance |
| `routing_map.json` | change department vocabulary or ownership rules | Retail Ops |
| `severity_rubric.A/B.json` | change what "serious" means | Contested — see DECISIONS.md Q2 |
| `response_templates.json` | change any sentence a customer can read | Compliance |
| `draft_lint.json` | change what a draft may never say | Compliance |
| `redaction_patterns.json` | change what counts as an identifier | Infosec |
| `policy.json` | SLA windows, confidence thresholds, the gates | Joint |

Two ordering rules inside `redaction_patterns.json` are load-bearing and
commented there: `ROUTING` runs before `SSN` (a 9-digit routing number matches
the SSN shape, and whichever rule claims a span decides the label), and
`CARD_PAN` runs before the account rules (so a 16-digit PAN is not shredded by a
10-digit account match).

After any config change:

```bash
make run && make evals && make test
```

---

## Switching on the Claude engine

It is written, tested offline, and **off**. Enabling it is a client decision,
not an engineering one — see `docs/DECISIONS.md` Q1, which asks whether redacted
text may leave the building at all.

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # or: ant auth login
PYTHONPATH=src python3 -m mhb run --engine claude --limit 10 -v
```

Needs `anthropic>=0.104`. It only ever receives redacted text, and
`ClaudeEngine._egress_guard()` re-scans the finished payload against the PII
vault and raises before opening a socket — a redaction bug becomes a crash, not
a disclosure. `tests/test_egress.py` proves offline what the outbound payload
would contain.

---

## What is generated vs. committed

```
out/      cases, audit log, evidence.json, console.html   generated, gitignored
vault/    original identifier values, mode 0700           generated, gitignored
config/   the contestable judgments                       committed
data/     the synthetic corpus and its generator          committed
```

`vault/` holds the real values behind every placeholder. It is gitignored and
`make clean` removes it. If you ever see it in `git status`, stop and work out
why before committing.

The corpus in `data/synthetic/complaints.jsonl` is **entirely fabricated** —
names, accounts, routing numbers, card numbers. It is regenerated deterministically
by `data/generate.py` from a fixed seed, so a `make data` on your machine
produces byte-identical output to mine.

---

## Troubleshooting

**`No cases found. Run: PYTHONPATH=src python3 -m mhb run --fresh`**
You ran `make evals` or `make console` before `make run`. Run `make all`.

**`FileNotFoundError: data/synthetic/complaints.jsonl missing`**
Run `make data`.

**`ModuleNotFoundError: No module named 'mhb'`**
You are missing `PYTHONPATH=src`, or you are not in the repo root.

**A hard gate fails after a config edit**
That is the harness doing its job. `out/evidence.json` has the detail. The most
likely one is `no PII in case files` — check whether whatever you added to the
case object carries raw values. That exact bug happened during the build:
evaluation gold labels were attached to case files and put identifiers back into
all 120 of them. The redactor was fine; the pipeline around it was not.

**Tests pass but the console looks wrong**
`make console` reads `out/cases/`. Re-run `make run` first.

---

## Where to read next

| | |
|---|---|
| `docs/SCOPE.md` | What the client asked for vs. what they need. Start here. |
| `docs/WALKTHROUGH.md` | The 40-minute stakeholder script, with likely objections |
| `docs/EVIDENCE.md` | Measured results, including what we got wrong |
| `docs/DECISIONS.md` | Nine open questions only the client can answer |
| `docs/ARCHITECTURE.md` | Seven agents, the engine/policy split, the PII boundary |
