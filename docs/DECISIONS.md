# Open decisions

Everything here is a question only Meridian Harbor can answer. Each one is blocking
something specific, has a placeholder currently in the code, and names who we think
owns it. The placeholders are deliberately visible in config rather than buried, so
that nothing quietly hardens into policy because an engineer guessed once.

---

## Q1 — May redacted complaint text leave the building?

**Owner:** Compliance officer + Infosec + Legal
**Blocks:** the only measured path to closing the regulatory recall gap
**Current placeholder:** the model engine is written, tested offline, and switched
off. The shipped default makes no network call at all.

The deterministic engine misses politely-worded regulatory complaints — 25% recall
on those, measured. That is not a tuning problem, it is what keyword matching is. A
model reads them correctly.

Policy says no un-redacted text leaves the environment. We have met that and gone
further: the default engine is offline, so on day one the answer to "does complaint
text leave our environment?" is "no — not even redacted."

The question is whether you want to stop there. Three ways forward, in increasing
order of what they require from you:

1. **Stay offline.** Accept the recall gap. Mitigate by having Compliance spot-audit
   a sample of unflagged complaints monthly. Zero new risk, zero new spend, a known
   and measured blind spot.
2. **Self-hosted model inside your perimeter.** Closes most of the gap, nothing
   leaves. Costs infrastructure and someone to own it.
3. **Hosted model on redacted text only.** Closes the gap, costs about $0.02 per
   complaint at current rates. Requires a decision that placeholder-bearing text is
   not customer data for egress purposes — which is a Legal question, not a
   technical one.

If you go to option 3, `tests/test_egress.py` already proves offline what the
outbound payload would contain, and the egress guard raises rather than transmits if
a vault value ever appears in a request. We would want those two artifacts in front
of Legal, not a verbal assurance.

---

## Q2 — What does "serious" mean?

**Owner:** Head of Retail Operations, with both senior analysts in the room
**Blocks:** SLA targets, queue ordering, staffing, and whether anyone trusts the band
**Current placeholder:** two unratified rubrics, both scored, disagreement reported

This is the cheapest and highest-leverage item on the list: one hour, three people,
and the 44 flagged complaints in the console's third tab.

Bring to the session:

- **The 14% agreement rate.** Your two most experienced analysts triage the same
  complaint the same way about one time in seven. That is not a criticism of either of them;
  it is what happens when a term is never written down.
- **What Rubric B does at volume.** "A live statutory clock is always a one" makes
  86 of 120 complaints critical. The instinct is sound; the band structure around it
  is not.
- **What Rubric A does to fair lending.** A discrimination complaint with no dollar
  figure scores as routine, because harm is weighted 3.0 and regulatory exposure
  1.5. Ask whether anyone would defend that.

The output is one ratified file. The dimensions are almost certainly right already —
the 32 obvious-floor cases clear under both readings. What needs deciding is the
weights, the band cutoffs, and which override rules survive.

**Specific sub-questions the session must settle:**

- Does regulatory exposure alone, with no financial harm, justify S1?
- Does a CFPB mention automatically mean S1, or does it mean "tell Compliance
  immediately" without changing the analyst's queue order? (We think these are two
  different mechanisms that Rubric B conflates.)
- Is a repeat contact evidence of severity, or evidence of a process failure that
  should be measured separately?
- What percentage of complaints *should* be S1? If the answer is "about 5%", that
  constrains the cutoffs directly and settles most of the argument arithmetically.

---

## Q3 — How long does an analyst actually take?

**Owner:** Head of Retail Operations
**Blocks:** the entire business case
**Current placeholder:** 18 minutes baseline, 7.25 minutes assisted — both invented
by us

Break-even is 16.2 minutes per complaint. Below it the backlog drains; above it the
backlog grows no matter how good the triage is. We do not know which side of that
line you are on, and neither does anyone we spoke to.

A two-week pilot where analysts work pre-triaged cases with timing replaces the
entire model in `metrics.py` with a real number. Until then every figure in the
business case is arithmetic on a guess, and we would rather say so than present it
as a forecast.

Measure separately for routine cases and escalated ones — the blended average will
hide the thing you need to see.

---

## Q4 — Who owns credit-reporting disputes?

**Owner:** Head of Retail Operations
**Blocks:** correct routing of FCRA complaints
**Current placeholder:** `routing_map.json` sends them to Deposit Operations, marked
`PLACEHOLDER` in the file

Nobody could tell us. FCRA furnishing disputes carry a 30-day statutory
investigation window, so an unowned queue is a missed deadline waiting to happen.
This needs a name, not a department.

---

## Q5 — Who owns "we kept the customer on hold and nobody called back"?

**Owner:** Head of Retail Operations
**Blocks:** routing of contact-centre service failures
**Current placeholder:** `GENERAL_SERVICING`

This one surfaced from the evaluation rather than from discovery. The department
list we were given has no owner for a complaint about the complaint process itself —
repeated calls, long holds, promised callbacks that never came. We initially
labelled these Branch Ops, which was wrong, and correcting the label exposed the
gap.

It matters more than it sounds. "I called three times and nobody called me back" is
UDAAP-adjacent, it is a leading indicator of exactly the frustration that produces a
CFPB complaint, and right now it falls into a general queue nobody is accountable
for.

---

## Q6 — Should the Reg E ownership rule be narrowed?

**Owner:** Head of Retail Operations + Head of Fraud
**Blocks:** correct routing of identity theft
**Current placeholder:** the rule as you stated it, obeyed, with the conflict flagged

Your rule is *"Reg E disputes are worked by Cards & Disputes regardless of channel."*
Correct for a disputed charge. Wrong for account takeover and identity theft, which
are also Reg E and belong with Fraud & Security — where the containment playbook
lives, and where speed matters most.

Four complaints in our corpus hit this. The system obeys the rule, because ownership
is your call and not the system's, but lowers its confidence and records the
conflict so an analyst sees it. Our suggested narrowing:

> Reg E disputes are worked by Cards & Disputes, **except** where the complaint
> alleges identity theft, account takeover or credential compromise, which go to
> Fraud & Security.

One line in `routing_map.json`. We did not apply it, because it is a decision about
who does the work.

---

## Q7 — Do the templates survive Compliance review?

**Owner:** Compliance officer
**Blocks:** anything customer-facing
**Current placeholder:** four templates we drafted, in `response_templates.json`

These are the only sentences that can reach a customer. We wrote them to be
reviewable as flat text by someone who does not read code, and to state no outcome,
no fault finding, no refund, and no resolution date.

She should mark them up directly. Specific things we would want her ruling on:

- Does the Reg E acknowledgement say enough about the investigation timeframe
  without committing to a date we may miss?
- Is "a person is looking at it rather than a queue" in the hardship template a
  promise we can keep?
- Should the acknowledgement tell a customer their complaint has been referred to
  Compliance, or is that an internal matter?

---

## Q8 — What is the retention policy for the vault?

**Owner:** Infosec + Legal
**Blocks:** production deployment
**Current placeholder:** none. The vault grows forever.

Originals must persist long enough to render an approved letter and support an
audit, and no longer. Nobody told us what that window is. Right now `vault/` is
gitignored, mode 0700, and never pruned — fine for a prototype, not a policy.

A related question: should the vault hold originals at all, or hold pointers into
the system of record so there is only ever one copy of a customer's account number?
The second is better if your core system can support the lookup.

---

## Q9 — Does the model engine need its own approval path?

**Owner:** Compliance officer
**Blocks:** only Q1 option 3

If the model engine is ever enabled, its outputs are non-deterministic. Two
consequences worth deciding in advance rather than discovering:

- The same complaint may receive a different band on different days. Our confidence
  reporting surfaces this, but Compliance should decide whether a re-run that
  changes a band is an incident, a normal variance, or something that must be
  logged.
- "Why was this flagged?" currently resolves to a rule id in a file. Under the model
  engine it resolves to a quoted span of evidence the model chose. That is still
  auditable, but it is a different kind of auditable, and she should see an example
  before agreeing to it.
