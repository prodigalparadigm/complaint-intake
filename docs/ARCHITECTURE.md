# Architecture

## The shape

```
                       ┌──────────────────────────────────────────┐
  email ──┐            │          PII BOUNDARY                    │
  web ────┼─▶ INTAKE ─▶│ REDACTION ─▶ vault/  (originals, local)   │
  call ───┘   normalise│      │                                    │
                       │      ▼ redacted text only                 │
                       │  ┌─────────────────┐                      │
                       │  │ REGULATORY      │ recall-biased,        │
                       │  │ TRIPWIRE        │ owns the 48h clock    │
                       │  └────────┬────────┘                      │
                       │           ├──────────────┐                │
                       │           ▼              ▼                │
                       │  ┌─────────────┐  ┌─────────────┐         │
                       │  │ SEVERITY    │  │ ROUTING     │         │
                       │  │ rubric A+B  │  │ ownership   │         │
                       │  └──────┬──────┘  └──────┬──────┘         │
                       │         └───────┬────────┘                │
                       │                 ▼                          │
                       │          ┌─────────────┐                  │
                       │          │ DRAFTING    │ template select  │
                       │          │             │ + slot fill      │
                       │          └──────┬──────┘                  │
                       │                 ▼                          │
                       │          ┌─────────────┐                  │
                       │          │ QA          │ can veto         │
                       │          └──────┬──────┘                  │
                       └─────────────────┼────────────────────────┘
                                         ▼
                            state = PENDING_HUMAN_APPROVAL
                                         │
                      ═══════════════════╪═══════════════════  ← the gate
                                         ▼
                              mhb/review.py  approve(case, named_human)
                                         │
                                         ▼
                              rehydrate from vault → letter
```

Everything above the gate is proposal. Nothing above the gate can cross it.

## Seven agents, and why it is seven and not one

Each agent is a narrow step that can be tested, versioned and tuned on its own,
and each records its own decision with the ruleset version that produced it.

| Agent | Job | Owned by |
|---|---|---|
| `intake` | Three channel formats into one record; expand call-centre shorthand | Retail Ops |
| `redaction` | Remove identifiers, vault originals, report what it was unsure about | Infosec / Compliance |
| `regulatory_tripwire` | Detect regulated practices, attach citations, start the 48h clock | Compliance |
| `severity` | Score seven observable dimensions; apply both rubrics; flag disagreement | Contested — see SCOPE §3 |
| `routing` | Apply ownership rules, then subject-matter vocabulary | Retail Ops |
| `drafting` | Select an approved template, fill slots, lint the result | Compliance (text) |
| `qa_supervisor` | Nine independent checks; can veto; sets the terminal state | Compliance |

The decomposition is not for its own sake. It buys four things that matter here:

**Different owners can tune different parts.** The compliance officer changes
`regulatory_rules.json`; the ops head changes `routing_map.json`; neither needs an
engineer and neither can accidentally change the other's.

**Independent failure.** A routing miss does not suppress a regulatory flag. The
tripwire deliberately does not consult severity, because in the current manual
process the missed regulatory complaints are precisely the ones that do not look
serious.

**Auditable attribution.** "Why was this a two?" resolves to one agent, one ruleset
version, and a list of quoted evidence.

**Honest confidence.** Each agent reports its own confidence about its own job,
rather than one blended number that means nothing.

### Why the orchestrator is a plain function

`orchestrator.py` runs the agents in a fixed sequence. There is no planner deciding
what to call next. For a regulated intake process that is the right trade: the order
is auditable, the same complaint takes the same path every time, a failure is
attributable to one step, and the whole thing is deterministic enough that
`test_pipeline.py` can assert byte-identical output across runs.

Model-driven orchestration would buy flexibility this process does not want. The
value here is in specialised steps, not in improvisation over a compliance workflow.

## Engines interpret; agents decide

The one architectural line worth remembering:

> **An engine may interpret language. It may never set policy.**

An engine answers questions *about text*: what does this complaint say, which
regulated practice does it touch, which department's vocabulary does it use. The
agents then apply the bank's rules — severity banding, SLA clocks, ownership
tiebreakers, template selection, lint rules, the approval gate — identically
whichever engine is loaded.

Two engines ship:

- **`rules`** (default) — deterministic, offline, zero per-unit cost, every output
  traceable to a named rule id in a config file. Every number in EVIDENCE.md came
  from this engine.
- **`claude`** — Claude Opus 5 via the Messages API with structured outputs, for the
  interpretation steps only, on redacted text only. **Written, tested offline, and
  switched off.**

Because the split is structural, swapping engines can change how well the system
*reads* but cannot change what it is *allowed to do*. `test_egress.py` proves one
instance directly: an engine that claims maximum regulatory exposure has its claim
capped at whatever Compliance's own threshold actually accepted. The model cannot
talk its way past the compliance rule.

## The PII boundary, in three independent layers

1. **Ordering.** Redaction is the second agent. No code path reaches interpretation
   with raw text.
2. **Storage separation.** `vault/` holds originals, gitignored and mode 0700.
   `out/cases/` holds placeholders. `rehydrate()` is called in exactly one place:
   rendering an approved letter.
3. **Independent verification.** `leak.py` scans finished artifacts against the
   vault's real values and does not trust the redactor's own opinion. Wired into QA
   (a hit blocks the case) and into the eval harness (a hit fails the build).

The redactor also reports what it could not be sure about — unusual name forms,
long digit runs in odd formats, digits spelled out as words — and those notes are
surfaced to the analyst in the console. A redactor that silently believes it caught
everything is more dangerous than one that admits a gap.

### Known limits, stated plainly

- Person names without a title are detected weakly. Detection is deliberately
  conservative because the false-positive rate on bank, branch and product names is
  high; the gap is reported as a residual-risk note rather than silently redacted.
- Digits spelled out in words ("four two one nine") are not caught by digit
  patterns. Detected as a risk note, not redacted.
- Free-text nicknames and relationship references ("my daughter Clara") are not
  identifiers by pattern and survive.

All three are measured in EVIDENCE.md rather than described as "edge cases".

## Config is the product

Every contestable judgment is a versioned file in `config/`, and every decision is
stamped with the ruleset id that produced it:

| File | Encodes | Owner |
|---|---|---|
| `policy.json` | Gates, SLA windows, confidence thresholds, throughput assumptions | Joint |
| `redaction_patterns.json` | What counts as an identifier | Infosec |
| `regulatory_rules.json` | Ten categories, triggers, citations, negation guards | Compliance |
| `routing_map.json` | Departments, vocabulary, ownership tiebreakers | Retail Ops |
| `severity_rubric.A.json` / `.B.json` | The two contested definitions of "serious" | Contested |
| `response_templates.json` | Every sentence that can reach a customer | Compliance |
| `draft_lint.json` | What a draft may never say | Compliance |

This is why "why was this flagged?" is answerable by reading a file rather than by
trusting a probability — and why tuning the system is a change-controlled config
edit in git rather than a retraining run.

## Data model

`models.py` enforces two rules. `RawComplaint.text` may contain PII; nothing
downstream of `RedactionResult` holds it, so `Case` carries only `redacted_text` and
a vault id. And every automated judgment is wrapped in a `Decision` carrying the
agent, its version, the engine, the ruleset ids, timing, confidence and evidence.
An assessment without provenance is not auditable, and Compliance has already
rejected one system it could not audit.

## Layout

```
config/         the contestable judgments, as versioned files
src/mhb/
  models.py     records; the PII rule and the provenance rule
  config.py     loading, with ruleset id stamping
  store.py      three stores split along the PII boundary
  leak.py       independent leak scanner
  clock.py      SLA arithmetic, measured from receipt
  orchestrator.py
  review.py     the gate: approve / reject / render
  metrics.py    calibration report + throughput model
  report.py     the self-contained HTML console
  cli.py
  agents/       seven agents + the auditing base class
  engines/      rules (default) and claude (off)
data/generate.py    synthetic corpus with gold labels
evals/run_evals.py  the evidence harness and its hard gates
tests/              112 tests, stdlib unittest, no dependencies
docs/
out/                generated: cases, audit log, evidence.json, console.html
vault/              generated: originals. gitignored.
```
