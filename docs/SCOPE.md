# What they asked for, and what they need

## The ask

> "We get roughly 1,400 customer complaints a month across email, a web form, and
> call-center notes. Three analysts read every one, decide how serious it is, figure
> out which department owns it, and draft a response. They're six days behind on
> average. We want AI to handle complaints."

## The short version of our answer

"Handle complaints" bundles five different jobs. Three of them are safely
automatable today, one is automatable only after Meridian Harbor makes a decision
it has not yet made, and one must not be automated at all in this engagement.
Treating them as one project is how the last vendor pilot died.

| The job | Automatable? | What we built |
|---|---|---|
| Read three channels into one shape | Yes | Intake agent |
| Strip PII before anything else touches it | Yes, and first | Redaction agent + vault + leak scanner |
| Spot regulated practices and start the 48-hour clock | Yes, with recall bias and human verification | Regulatory tripwire |
| Decide how serious it is | **Not yet** — the term is undefined | Both candidate rubrics, scored in parallel, with the disagreement measured |
| Decide which department owns it | Partly — the ownership rules have gaps | Routing agent, with conflicts surfaced |
| Write what the customer reads | **No** — Compliance has already vetoed this | Template selection and slot-filling only, behind a hard human gate |

The honest framing for Friday: **this is not AI handling complaints. It is AI doing
everything up to the point where judgment starts, so that three analysts spend their
day on judgment instead of on transcription, lookup, and boilerplate.**

---

## 1. The backlog is a capacity deficit, and triage alone will not fix it

Work the arithmetic before designing anything.

- 1,400 complaints ÷ 21 working days = **66.7 complaints/day**
- 3 analysts × 6 productive hours = 1,080 analyst-minutes/day
- Break-even is therefore **16.2 minutes per complaint**

At an assumed 18 minutes of true end-to-end handling time, capacity is 60/day
against demand of 66.7/day. The backlog grows by about seven complaints a day. Six
days behind is not a one-off — it is the steady state of a system running 11% over
capacity, and it will be nine days behind next quarter.

This has a sharp consequence for scope. **A system that sorts the queue better but
does not reduce minutes-per-complaint changes nothing.** Perfect prioritisation of a
queue you cannot clear just means you are six days late on the right things. So
every design decision here was judged against one question: does this remove minutes
from the analyst's hands?

What removes minutes: the complaint arrives already de-identified, already checked
against ten regulated practices with citations attached, already routed with a named
alternative, with the acknowledgement letter already drafted and the case number
already in it. What does not remove minutes: a severity score the analyst does not
trust and re-derives by reading the complaint anyway.

`make simulate` prints this model with a sensitivity table. Read the sensitivity
table, not the headline: it shows the plan works down to about 16 minutes of review
time and fails above it. Nobody at Meridian Harbor has measured that number, and it
is the only number that decides whether this is worth building.

---

## 2. The regulatory miss is a different problem, and it is more urgent

The ops head framed everything as backlog. Buried in discovery is something worse:

> Complaints that touch potential regulatory issues are supposed to be flagged to
> Compliance within 48 hours, and they're currently finding them late.

This is not a throughput problem. It is a **detection** problem with a regulator
attached, and it is not fixed by clearing the backlog, because the complaints that
get missed are not the ones that look urgent. A politely-worded fee complaint from a
customer who is not angry sits in the queue looking routine; it is a Reg DD matter
with a clock on it.

Two things follow:

**It should ship first and separately.** The tripwire is a read-only classifier over
inbound text. It changes no workflow, touches no customer, and requires no analyst
to change how they work. It could be in production in a fortnight while the rest of
this is still being argued about. Bundling it into a larger "AI complaints platform"
delays the one component with a statutory deadline behind the one component the
compliance officer will fight hardest about.

**It must be tuned for recall, not accuracy.** The cost asymmetry is extreme and
one-directional: a false positive costs a compliance analyst two minutes of reading;
a false negative costs a missed statutory deadline and a finding at the next exam.
Every threshold in `config/regulatory_rules.json` is set accordingly, and the
reasoning is written in the config file where Compliance can read and change it
without an engineer.

---

## 3. "Serious" is undefined, and that is a finding, not an obstacle

> Nobody could tell you what "serious" means; the two senior analysts disagree about
> it in front of you.

This is the most important thing discovery turned up, and the easiest to paper over.
The tempting move is to pick the more senior analyst's instincts, encode them, call
the output a severity score, and let the apparent objectivity of a number settle an
argument that was never settled. That produces a model the analysts override
constantly, and within two months nobody looks at the score.

We did something else. The system separates two things that were tangled together:

- **Observation** — seven dimensions scored 0–3 with a quoted span of evidence:
  financial harm, vulnerability, regulatory exposure, recurrence, how long it has
  been unresolved, escalation signals, relationship. Both analysts agree these are
  the things that matter. This part is automatable and shared.
- **Valuation** — the weights, the band cutoffs, the override rules that turn those
  observations into the word "serious". This is contested, and it is a policy
  artifact Meridian Harbor owns.

We wrote both analysts' readings as config files — `severity_rubric.A.json`
(harm-weighted: "the customer is actually out money") and `severity_rubric.B.json`
(exposure-weighted: "this is the one that ends up in an exam finding") — and score
every complaint under both.

They agree on **15% of complaints**.

That number is the deliverable. It converts an unresolvable corridor argument into a
worklist of specific complaints where the two readings diverge, which is an agenda
for a one-hour meeting. The console's third tab is that agenda. And the exercise
immediately surfaced two things neither analyst would have predicted:

- Rubric B, applied literally, rates **86 of 120 complaints critical**. The rule "a
  live statutory clock is always a one" is reasonable in isolation and useless at
  volume — if 72% of complaints are critical, none are.
- Rubric A rates a **fair-lending complaint with no dollar amount attached as
  routine**, because it weights financial harm at 3.0 and regulatory exposure at
  1.5. Nobody would defend that out loud. It is what the stated rule does.

Neither of these is visible in a conversation. Both are obvious in a table.

---

## 4. The compliance officer's veto is a design constraint, not a risk to manage

> The compliance officer will not accept any customer-facing text going out without
> human sign-off, and she's the one who killed the last vendor pilot.

The weak response is a checkbox and an approval screen. She has presumably seen
that before, which is why there was a last pilot.

We treated the veto as a hard architectural constraint and designed so that the
guarantee is structural rather than procedural:

- The pipeline's terminal state is `PENDING_HUMAN_APPROVAL`. There is no transition
  out of it inside the pipeline.
- The only function that produces `APPROVED_FOR_SEND` lives in `mhb/review.py`,
  requires a named approver, and refuses any case QA blocked.
- That same function is the **only** place customer identifiers are restored into a
  letter. An agent that somehow produced a letter could not fill in who it goes to.
- The agent does not write prose. It selects a compliance-approved template and
  fills slots. The entire set of sentences that can reach a customer is a flat JSON
  file she can read in ten minutes without reading any code.
- A lint pass blocks admissions of liability, promised outcomes, unapproved
  deadlines, customer-blaming language, and bare digit strings — and a draft that
  trips one cannot enter the approval queue at all.

`tests/test_gates.py` holds 22 tests of the gate, nine of which are attempted bypasses asserted to fail.
The claim we can make to her is not "we are careful"; it is "here is the test file".

---

## 5. PII: the strongest version of the constraint is also the easiest to defend

> Complaint text contains account numbers and customer PII, and policy says none of
> it leaves their environment un-redacted.

We went one step further than the policy, because the stronger claim is cheaper to
make than the weaker one is to prove. The default engine is deterministic and
makes **no network call at all**, so the answer to "does complaint text leave our
environment?" is "no — not even redacted" on day one. That removes the egress
question from the critical path entirely and lets the pilot start while Legal and
Infosec take as long as they need on the model question.

The boundary is enforced in three independent places rather than asserted once:

1. Redaction runs second, immediately after normalisation. No downstream agent is
   reachable with raw text.
2. Originals go to a separate gitignored `vault/`; case files hold placeholders.
3. An **independent leak scanner** — which does not trust the redactor — scans every
   finished case file against the vault's actual values, and a hit blocks the case.

That third check earned its place during this build. It caught a bug we introduced
ourselves: evaluation gold labels, which contain the raw injected identifiers, were
being attached to case files, putting PII back into all 120 of them. The redactor
was working perfectly. The pipeline around it was not. This is the normal shape of a
PII incident, and it is the argument for checking the artifact rather than trusting
the component.

A Claude engine is written, tested offline, and **switched off**. `build_request()`
is a pure function, so `tests/test_egress.py` proves what the outbound payload would
contain without sending anything, and an egress guard raises rather than transmits
if a vault value ever appears in a request. Turning it on is a client decision, not
an engineering one — see DECISIONS.md Q1.

---

## 6. What we deliberately did not build

**Resolution.** The system acknowledges; it does not investigate, decide, or remedy.
Investigation is where the regulatory risk actually concentrates and where analyst
expertise actually lives.

**Sending.** Nothing is wired to an outbound mail path. Deliberate: a prototype that
cannot send is a prototype whose worst-case failure is an embarrassing draft in a
queue.

**Headcount reduction.** The ops head was explicit that the analysts are not going
anywhere, so we did not build toward it and the business case does not assume it.
The case is that three analysts clear 67 complaints a day instead of 60 — not that
two analysts do the work of three. This also happens to be the only version that
survives the trust problem: a system the analysts believe is there to replace them
will be fed the minimum cooperation required.

**A severity score presented as settled.** Covered above. The system reports two
bands and tells you when they disagree.

**Automatic learning from analyst edits.** The approval flow records the diff
between the agent's draft and what the analyst sent, because that is the honest
measure of whether drafting helps. We did not close the loop and feed it back:
auto-tuning customer-facing text on an uncontrolled signal is precisely the kind of
thing that ends a pilot. The data is there for a human to look at.

---

## What we would do next, in order

1. **Ship the regulatory tripwire alone.** Two weeks, read-only, no workflow change,
   nothing customer-facing. It addresses the only finding with a statutory deadline.
2. **Run the calibration session.** One hour, the two senior analysts, the ops head,
   the 42 flagged complaints. Output is one ratified rubric file. Everything
   downstream of "serious" is blocked on this and it costs an hour.
3. **Measure review time.** Two weeks of analysts working pre-triaged cases with
   timing. This replaces the entire business case model with a real number.
4. **Then** decide about the model engine, armed with a measured recall gap from
   step 1 showing exactly what the keyword engine misses.
