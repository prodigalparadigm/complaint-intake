# Meridian Harbor Bank — complaint intake prototype

A working multi-agent complaint intake system, built against a discovery brief from
a 900-person regional bank: 1,400 complaints a month across three channels, three
analysts six days behind, regulatory complaints being found late, a compliance
officer who has already killed one vendor pilot over customer-facing text, and
nobody able to define what "serious" means.

**What it does:** normalises complaints from email, web form and call-centre notes;
strips PII before anything else touches the text; detects ten regulated practices
and starts the 48-hour compliance clock at the moment of intake; scores severity
under *both* contested definitions and reports where they disagree; routes to an
owning department; and drafts an acknowledgement from compliance-approved templates.

**What it does not do:** send anything. The pipeline's terminal state is
`PENDING_HUMAN_APPROVAL` and there is no transition out of it. A named human
approves, and that approval is the only moment customer identifiers are restored
into a letter.

---

## Run it

No dependencies. Python 3.11+. Nothing here makes a network call.

```bash
make all        # generate data, run the pipeline, measure it, run the tests, build the console
open out/console.html
```

Individually:

```bash
make data       # regenerate the 120-complaint synthetic corpus
make run        # run the pipeline, write out/cases/ + out/audit.jsonl
make evals      # measure behaviour -> out/evidence.json, enforce the hard gates
make test       # 112 tests, stdlib unittest
make console    # build the self-contained HTML review console
make calibrate  # where the two definitions of "serious" diverge
make simulate   # backlog arithmetic with a sensitivity table
```

Working a single case:

```bash
PYTHONPATH=src python3 -m mhb queues
PYTHONPATH=src python3 -m mhb show MHB-XXXXXXXX      # full case + audit trail
PYTHONPATH=src python3 -m mhb approve MHB-XXXXXXXX --approver "D. Okafor"
PYTHONPATH=src python3 -m mhb render  MHB-XXXXXXXX   # the letter, rehydrated
```

`render` on an unapproved case is refused. That is the point.

---

## Read it in this order

| | |
|---|---|
| **[QUICKSTART.md](QUICKSTART.md)** | Run the demo end to end, change behaviour without touching code, troubleshoot. Read this first if you just want it working. |
| **[docs/SCOPE.md](docs/SCOPE.md)** | What they asked for vs. what they need. The argument, and what we deliberately did not build. Start here. |
| **[docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)** | The 40-minute Friday script for the ops head and the compliance officer, with the objections they will raise. |
| **[docs/EVIDENCE.md](docs/EVIDENCE.md)** | Measured results, separated into "holds by construction" and "measured, with caveats". Includes what we got wrong. |
| **[docs/DECISIONS.md](docs/DECISIONS.md)** | Nine open questions only the client can answer, each with what it blocks. |
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | Seven agents, the engine/policy split, the PII boundary. |

---

## The three things worth knowing

**1. The severity rubric is the finding, not the feature.**
Nobody could define "serious", and the two senior analysts disagreed in front of us.
Rather than encode one analyst's instincts and present the output as objective, the
system scores every complaint under both readings. **They agree 14% of the time.**
That number converts an unresolvable argument into a worklist of 44 specific
complaints — an agenda for a one-hour meeting that would actually settle it.

**2. The human gate is structural, not procedural.**
There is no code path from an agent to a customer. The only function producing
`APPROVED_FOR_SEND` requires a named approver and refuses QA-blocked cases, and it
is the sole caller of `rehydrate()` — so a rogue letter could not even be addressed.
`tests/test_gates.py` holds 22 tests of the gate, nine of which are attempted bypasses asserted to fail.

**3. The default engine makes no network call at all.**
Policy said no un-redacted text leaves the environment. The shipped engine is
deterministic and offline, so the answer is "nothing leaves, redacted or not". A
Claude engine is written and tested offline but **switched off** — enabling it is a
client decision (DECISIONS.md Q1), and `tests/test_egress.py` proves what the
outbound payload would contain without sending anything.

---

## Results at a glance

| | |
|---|---|
| Injected identifiers removed from text | 454 / 454 |
| Case files containing PII (independent scan) | **0 / 120** |
| Regulatory recall — complaints using regulatory vocabulary | **100%** |
| Regulatory recall — politely-worded regulatory complaints | **25%** ← the real ceiling |
| Regulatory cases flagged within 48h | 92 / 92, worst latency 0.0h |
| Routing accuracy | 93.3%, and **every misroute flagged** for human confirmation |
| Rubric A vs B agreement on severity | **14%** |
| Cases the pipeline marked sendable | 0 |
| Throughput | 2.65 ms/complaint — 3.7 seconds for a month's volume |
| Tests | 112 passing |

Measured on 120 synthetic complaints with gold labels. Synthetic because the real
text is exactly the data that must not move. See EVIDENCE.md for what that does and
does not let you conclude — including a tuning disclosure, and the PII bug our own
leak scanner caught in this build.

---

## Shape of the repo

```
config/     every contestable judgment, as a versioned file
src/mhb/    models, store, orchestrator, review gate, metrics, console builder
  agents/   intake · redaction · regulatory · severity · routing · drafting · qa
  engines/  rules (default, offline) · claude (written, tested, off)
data/       synthetic corpus generator
evals/      the evidence harness and its six hard gates
tests/      112 tests, no dependencies
out/        generated — cases, audit log, evidence.json, console.html
vault/      generated — original identifier values. gitignored.
```

Config is the product: `regulatory_rules.json` is owned by Compliance,
`routing_map.json` by Retail Operations, `response_templates.json` holds every
sentence that can reach a customer. Each decision in a case file is stamped with the
ruleset version that produced it, so "why was this flagged?" is answered by reading
a file, not by trusting a score.
