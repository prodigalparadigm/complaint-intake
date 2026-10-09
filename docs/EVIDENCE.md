# Evidence

Reproduce everything here with `make all`. Raw output: `out/evidence.json`.

**What this was measured on.** 120 synthetic complaints, 30 archetypes, across all
three channels (60 email, 30 web form, 30 call note), with gold labels for
regulatory category, owning department, and the exact identifier strings injected
into each complaint. Synthetic because real complaint text is precisely the data the
client told us must not move.

**What that means for how much you should believe it.** A synthetic corpus we wrote
cannot tell you how this performs on Meridian Harbor's actual mail. It can tell you
that specific properties hold, that specific failure modes are caught, and roughly
where the ceiling is. The numbers below are separated into the two kinds, because
they deserve very different levels of confidence.

---

## Part 1 — Properties that hold by construction

These are the ones to rely on. They are structural, enforced in code, and tested.

| Property | Result |
|---|---|
| Injected identifiers removed from complaint text | **454 / 454** |
| Case files containing a vault value (independent scan) | **0 / 120** |
| Outbound model payloads containing PII (offline check, 120 built) | **0 / 120** |
| Cases the pipeline marked sendable | **0** (must be 0) |
| Approval bypasses attempted / blocked | **9 / 9** (within 22 gate tests) |
| Regulatory cases with a 48-hour clock set | **92 / 92** |
| Worst compliance flag latency | **0.0 hours** |
| Determinism: same complaint processed twice | **25 / 25 identical** |
| Drafts reaching the approval queue with a blocking lint finding | **0** |
| Test suite | **110 passing**, stdlib only, no dependencies |

### The 48-hour clock

Every regulatory complaint is flagged at the moment of intake. Latency is the
pipeline's own runtime — 2.65 ms mean, 4.12 ms p95. Against a 48-hour window and a
current process the client describes as "finding them late", the margin is not
close. 1,400 complaints a month costs **3.7 seconds of compute**, on a laptop, with
no API spend.

The clock runs from `received_at`, not from when processing happened, because that
is how an examiner would count it.

### The human gate

The pipeline has no transition to a sent state. `mhb/review.py::approve()` is the
only function that produces `APPROVED_FOR_SEND`, and the same module holds the only
call to `rehydrate()` — so an agent that somehow produced a letter could not fill in
who it goes to. Attempted bypasses, all refused:

```
approve with an empty approver             → ApprovalError
approve with a whitespace approver         → ApprovalError
approve a case twice                       → ApprovalError
approve a case QA blocked                  → ApprovalError
reject without a named reviewer            → ApprovalError
render a letter before approval            → ApprovalError
render a letter on a rejected case         → ApprovalError
render a letter with an unresolved slot    → ApprovalError
a case arriving at QA already pre-approved → QA blocks it
```

The last two are worth noting because they came from running the demo rather than
from designing it. Rendering an approved letter still left `{{CUSTOMER_NAME}}` in the
text — the slot the pipeline deliberately defers was never actually filled at send
time. `render_for_send()` now resolves it (from the vault here; from the system of
record in production) and then refuses to return any letter still containing an
unresolved placeholder, so a letter addressed to a template variable cannot leave
the function.

### The leak scanner earned its place

The independent scanner caught a bug **we** introduced during this build: evaluation
gold labels, which contain the raw injected identifier values, were being written
onto case files — putting PII back into all 120 of them. The redaction agent was
working perfectly; the pipeline around it was not.

That is the ordinary shape of a PII incident, and it is the argument for verifying
the finished artifact rather than trusting the component. It also found a redaction
*precision* bug: the username rule had an optional separator, so "suspicious login
from a state" vaulted the word "from" as a username. Both have regression tests.

---

## Part 2 — Measurements, with the caveats that belong next to them

### Regulatory detection — read the second row, not the third

| Complaint type | n | Recall | Precision |
|---|---|---|---|
| Uses recognisable regulatory vocabulary | 112 | **100%** | 100% |
| Regulatory, but politely worded | 8 | **25%** | 100% |
| All complaints (blended) | 120 | 93.9% | 100% |

**Do not quote the blended number on its own.** It is an artifact of how many quiet
complaints we chose to put in the corpus, and it hides the only thing a compliance
officer needs to know before relying on this: a keyword engine cannot read a
complaint that never uses a keyword.

The six misses are all deliberate. Two archetypes in the corpus — `quiet_fee` and
`quiet_dispute` — are genuinely regulatory and written the way a calm, polite
customer actually writes:

> "When I opened my checking account the banker said the account had nothing taken
> out of it each month as long as I kept my direct deposit… Looking at my statements
> going back to the spring there is a $44 charge every single month. Nobody
> mentioned that this could happen."

That is a Reg DD disclosure complaint with a clock on it. It contains no trigger
word, and the deterministic engine does not see it. **This is the honest ceiling of
the shipped engine, and it is the entire argument for the model engine** — a model
reads that correctly. Deciding whether to enable it is DECISIONS.md Q1.

**A tuning disclosure.** Some triggers (`misled`, `apy`, `lawyer`, `in fees`,
`nobody would explain`) were added *after* inspecting misses on this corpus. Recall
measured on the same corpus is therefore partly in-sample and optimistic. The quiet
cases were deliberately **not** tuned for, which is why they are the honest
indicator. We also removed two triggers we had just added (`rate was supposed to`,
`interest rate i was quoted`) when they produced four false positives by classing a
credit-card promotional APR as a Reg DD deposit matter — rather than keep a
flattering number.

Precision is 100% on this corpus. Do not expect that to survive contact with real
mail, and do not want it to: the thresholds are set for recall, and Compliance owns
the dial in `config/regulatory_rules.json`.

### Routing — 93.3%, and every miss is flagged

| Measure | Value |
|---|---|
| Accuracy against the ownership rules we were given | **112 / 120 (93.3%)** |
| Misroutes flagged for human confirmation | **8 / 8 (100%)** |
| Misroutes caused by a stated ownership rule being too broad | **4** |

The safety property matters more than the accuracy number: **every misroute on this
corpus carries a confidence below the auto-accept threshold**, so each one reaches
an analyst marked "confirm department" with the runner-up and its evidence attached.
Correct routes average 0.89 confidence; misroutes average 0.61. The system is not
confidently wrong anywhere in this corpus.

The 4 rule-caused misroutes are a finding about the client's rules, not about the
code. The ops head's stated rule — *"Reg E disputes are worked by Cards & Disputes
regardless of channel"* — is correct for a disputed charge and wrong for identity
theft, which is also a Reg E matter and belongs with Fraud & Security. The system
obeys the rule, because ownership is management's call, but lowers its confidence
and records the conflict:

> ownership rule: Reg E disputes are worked by Cards & Disputes regardless of
> channel — BUT the complaint's own vocabulary points to FRAUD_SECURITY (identity
> theft, suspicious login, fraud; 8.5 vs 2.5). The rule as written may be too broad
> for this case; an analyst should confirm.

The remaining 4 are a genuine ambiguity: an account closed without notice,
discovered when a card declined. Cards or Deposits is a real judgment call, and it
goes to a human at 0.63.

### Severity — the number that is the finding

| Measure | Value |
|---|---|
| Rubric A and B agree on the band | **18 / 120 (15%)** |
| Disagree by 2+ bands (changes what an analyst does) | **42** |
| Obvious-floor cases met under at least one rubric | **32 / 32** |

Band distribution under each reading of the same 120 complaints:

| | S1 | S2 | S3 | S4 |
|---|---|---|---|---|
| **A** — harm-weighted | 17 | 51 | 34 | 18 |
| **B** — exposure-weighted | **86** | 22 | 6 | 6 |

Two things fall out that neither analyst predicted:

**Rubric B collapses.** "A live statutory clock is always a one" is reasonable in a
conversation and useless at volume: it makes 72% of complaints critical, which means
none are. The rule is not wrong — the *band structure* around it is, and that is a
fixable, concrete thing to discuss.

**Rubric A under-rates pure regulatory exposure.** It weights financial harm at 3.0
and regulatory exposure at 1.5, so a fair-lending complaint with no dollar figure
attached scores 0.14 normalised and lands in **S4, routine**:

> "My husband and I applied for the same auto loan with the same income and the same
> credit. My husband was approved and I was declined. The only difference is that I
> am a woman and I am 61."

Nobody would defend calling that routine. It is what the stated rule does, and it is
invisible until you run it over 120 complaints.

The 32 "floor" cases — complaints both analysts would obviously call serious —
clear their floor under at least one rubric in every instance. That is the sanity
check that the dimensions themselves are sound even while the weights are contested.

**There is deliberately no gold severity label in this corpus for ordinary cases.**
We cannot score a system against a definition the client does not have. Inventing
one and then grading ourselves against our own invention would have produced a
number that looks like evidence and is not.

---

## Part 3 — The business case, and why it is the weakest part

`make simulate`. Demand is 66.7 complaints/day against 1,080 analyst-minutes.
Break-even is **16.2 minutes per complaint**.

| | Minutes/complaint | Capacity/day | Net/day | Verdict |
|---|---|---|---|---|
| Today (assumed) | 18.0 | 60.0 | **−6.7** | backlog grows |
| Assisted (assumed) | 7.25 | 149.0 | **+82.3** | drains in ~5 days |

Sensitivity on the one number that matters — how long an analyst actually takes to
review a pre-triaged case with a draft attached:

| Review min/complaint | Net/day | Drains? | Drain days |
|---|---|---|---|
| 6 | +68.3 | yes | 5.9 |
| 10 | +31.5 | yes | 12.7 |
| 14 | +10.5 | yes | 38.2 |
| 16 | +3.0 | yes | 132.9 |
| **18** | **−3.1** | **no** | — |
| 20 | −8.3 | no | — |

**Every input above is our assumption, not a Meridian Harbor measurement.** We were
not given handling times. The model is arithmetic applied to numbers we invented
from the stated backlog, and it should be treated as a structure for thinking rather
than a forecast.

What it does establish is where the plan fails, which is the useful part. If review
time lands above ~16 minutes, this system does not clear the backlog no matter how
good its triage is. A two-week pilot measuring that single number replaces this
entire table, and it is the first thing we would do.

---

## What would change these numbers for the worse

- **Real complaint text is messier.** Our synthetic email has one signature format.
  Real mail has forwarded chains, mobile signatures, inline replies and attachments.
  Intake normalisation will need work, and bad normalisation degrades everything
  downstream.
- **Real PII is weirder.** Account numbers written with spaces, names in signature
  blocks without titles, identifiers in attachments. The residual-risk notes exist
  because we expect this.
- **The corpus is balanced; real mail is not.** 73% of our corpus is regulatory
  because we wrote it to exercise the tripwire. If Meridian Harbor's real rate is
  15%, precision matters far more than this corpus can show.
- **We tuned against our own test set.** Disclosed above. The quiet-complaint recall
  of 25% is the number least contaminated by this, and it is the one we would plan
  around.
