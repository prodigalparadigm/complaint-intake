# Friday walkthrough

**Room:** Head of Retail Operations, Compliance Officer. **Time:** 40 minutes, 15 of
it theirs. **Screen:** `out/console.html` — a single file, opened from disk, no
network.

Before they arrive: `make all`, then open the console on the Compliance tab.

The two of them want different things and have different fears. The ops head wants
the backlog gone and is worried this is a headcount play that will spook her
analysts. The compliance officer killed the last pilot and is worried about text
going out unreviewed. **Address the compliance officer's fear in the first ninety
seconds**, because until it is addressed she is not listening to anything else.

---

## 0:00 — Open by narrowing the ask (2 min)

> "You asked for AI to handle complaints. I want to start by telling you what we are
> not going to do, because I think that is the more useful half.
>
> We are not going to let anything write to your customers. We are not going to
> decide how serious a complaint is — I will come back to that, because it turned
> out to be the most interesting thing we found. And we are not going to touch
> investigation or resolution.
>
> What we built does everything up to the point where judgment starts. The complaint
> arrives already de-identified, already checked against ten regulated practices with
> the citations attached, already routed, with the acknowledgement drafted and the
> case number already in it. Your three analysts still make every decision. They just
> stop spending their day on transcription and boilerplate."

Pause there. That sentence is the whole engagement.

---

## 0:02 — The compliance officer's objection, answered first (6 min)

> "You told us no customer-facing text goes out without a human signing it. So let
> me show you what we did with that, because I do not think a checkbox would have
> satisfied you."

**Show:** the yellow banner at the top of the console. Read it aloud.

> "The pipeline's last state is *pending human approval*. There is no next step
> inside it. The only function in the codebase that can mark a letter sendable lives
> in a separate file, requires a named person, and refuses any case that failed QA.
>
> And here is the part I would want if I were you: that same function is the only
> place in the system where a customer's real account number or name gets put back
> into a letter. Everything in this queue is de-identified. So even if something
> upstream went badly wrong and produced a letter on its own — it could not fill in
> who to send it to."

**Show:** expand any case, scroll to the draft. Point at the highlighted
`{{CUSTOMER_NAME}}`.

> "That slot is empty on purpose. It gets filled from your system of record at the
> moment a human signs. Which means a draft sitting in this queue identifies nobody."

> "We tried nine different ways to get around it and wrote each attempt down as a
> test. Empty approver, blank approver, render the letter before approval, approve
> something QA already blocked, approve twice. They all fail. That is
> `tests/test_gates.py` — I would rather hand your auditor a test file than a
> promise."

**Then, the templates:**

> "The agent does not write prose. It picks one of four templates and fills in the
> blanks. Every sentence that can reach a customer is in one file, as plain text —
> you can read all four in ten minutes without reading any code, and I would like
> you to mark them up. Changing one is a compliance change, tracked in version
> control, not a model that drifted."

> "And there is a lint pass on top. If a draft ever said 'we were at fault', or
> promised a refund, or committed to a deadline you have not agreed to, or blamed
> the customer — it cannot even enter the approval queue. It goes to rework."

**Anticipate:** *"What if the model says something unexpected?"*

> "It cannot. It is not generating sentences — it is selecting one of four
> paragraphs you approved and filling slots. The failure mode is a wrong template,
> not a wrong sentence. And a wrong template is visible in two seconds because the
> template id is on screen."

---

## 0:08 — PII (4 min)

> "Your policy is that no un-redacted complaint text leaves your environment. We went
> further than that, because the stronger claim turned out to be cheaper to make.
>
> The system we are showing you makes no network call at all. Not to us, not to
> anybody. So the answer to 'does complaint text leave the building?' is not 'only
> after redaction' — it is 'no'. That also means this can start on Monday while your
> Legal and Infosec people take as long as they want on the separate question of
> whether we should ever use a language model here."

**Show:** a case with a `redaction unsure` pill. Expand it.

> "Here is the part I want to be straight about. Redaction is not perfect and this
> one tells you when it is unsure. It caught the account number, the phone, the
> email. It is flagging that 'Gregory Tannenbaum' looks like a person's name it was
> not confident enough to remove automatically — because if we auto-removed every
> capitalised pair of words we would redact your own branch names.
>
> A redactor that silently believes it caught everything is more dangerous than one
> that admits a gap. This one admits them, in front of the analyst."

**The honest story, which will do more for trust than any number:**

> "We also check our own work independently. There is a scanner that does not trust
> the redactor — it takes every finished case file and searches it for the actual
> values we removed. During this build it caught a bug we had introduced ourselves:
> we were attaching evaluation labels to case files, and those labels contained the
> original account numbers. We had put the PII back into all 120 cases. The
> redaction was working perfectly. The thing around it was not.
>
> That is what a real PII incident looks like, and it is why we check the finished
> artifact instead of trusting the component. It is now zero out of 120, and there
> is a test that fails the build if it ever is not."

---

## 0:12 — The 48-hour clock (5 min)

> "You told us regulatory complaints are supposed to reach Compliance inside 48
> hours and you are finding them late. I think that is a bigger problem than the
> backlog, and it is a different problem."

**Show:** the Compliance tab, the 92-case queue.

> "Every complaint here touches a regulated practice. Each one was flagged at the
> moment it arrived — not when an analyst got to it. Worst latency across all 120:
> zero point zero hours, because the whole pipeline runs in under three
> milliseconds. For your full monthly volume that is under four seconds of compute."

**Expand one.** Point at the citation and the evidence.

> "It does not just say 'regulatory'. It says Reg E, 12 CFR 1005.11, ten business
> days to investigate — and here are the exact words in the customer's complaint
> that triggered it: 'charges I did not make'. You can confirm or dismiss that by
> reading one line. You never have to trust a score."

**The number, with the caveat attached:**

> "On complaints that use recognisable regulatory language, it catches 100%. On
> complaints that are regulatory but politely worded, it catches 25%."

Let that sit, then:

> "That second number is the honest one and I want you to hear it from me rather
> than find it. Here is one it misses."

**Read the quiet_fee complaint aloud.** Then:

> "That is a Reg DD disclosure complaint with a clock on it. The customer is not
> angry. She does not use a single word the system is looking for. Today that
> complaint sits in your queue looking routine until somebody reads it — which is
> exactly the kind you told us you are finding late.
>
> This version does not fix that. A language model would read it correctly, and that
> is the real argument for using one — but that is your call, not ours, and it is
> question one in the decisions document."

**Why this should ship first:**

> "This piece is read-only. It changes nobody's workflow, touches no customer,
> requires no analyst to do anything differently. It could be running in a fortnight
> while the two of you are still arguing about the rest of it. I would not bundle
> the one component with a statutory deadline behind the one component that needs
> the most debate."

---

## 0:17 — "Serious" (10 min) — the centrepiece

> "This is the part I most want your time on. When we asked what makes a complaint
> serious, nobody could tell us, and your two senior analysts disagreed about it in
> front of us.
>
> The easy thing would have been to pick one of them, encode it, and hand you a
> severity score. I want to explain why we did not, because I think that is how the
> last one failed. If we quietly pick one analyst's instincts and put a number on
> it, the number looks objective, the other analyst overrides it constantly, and in
> two months nobody looks at it."

**Switch to the third tab.**

> "So instead we wrote down both of their definitions as explicit rules, and scored
> all 120 complaints under each. Rubric A is harm-weighted — 'the customer is
> actually out money.' Rubric B is exposure-weighted — 'this is the one that ends up
> in an exam finding.'
>
> They agree on fourteen percent."

Pause.

> "Fifteen. Your two most experienced people would triage the same complaint the
> same way about one time in seven. That is not a criticism of either of them. It is
> what happens when a word is never written down — and it is happening in your queue
> right now, invisibly, depending on who picks up which complaint."

**Point at the matrix.** Green diagonal, purple cells.

> "Green is agreement. Purple is where they are two or more bands apart — one of
> them would put a named owner on it today and the other would put it in the
> standard queue. Forty-four complaints."

**Then the two findings:**

> "Running it this way surfaced two things I do not think either of them would have
> predicted.
>
> First — look at Rubric B's column. Applied literally, 'a live statutory clock is
> always a one' makes 86 of 120 complaints critical. The instinct is completely
> reasonable. At volume it means nothing is critical. The rule is not wrong, the
> band structure around it is, and that is a fixable thing to talk about.
>
> Second — Rubric A. It weights financial harm at three and regulatory exposure at
> one and a half. So a fair-lending complaint with no dollar amount attached comes
> out as *routine*."

**Read the loan_discrim complaint aloud.**

> "Routine. Nobody would defend that out loud. It is what the stated rule does, and
> you cannot see it in a conversation — you can only see it by running it over a
> hundred complaints."

**The ask:**

> "So what I am handing you is not a severity model. It is this list. Forty-four
> specific complaints where your two analysts would do different things. Put the
> three of you in a room for an hour with this list and you will come out with a
> definition — not an abstract one, a decided one. Then that becomes a single file,
> and the system scores to it.
>
> Until that hour happens, the system shows both answers and tells the analyst when
> they conflict. That is the honest thing to do, and it is also, I think, more
> useful than a confident number would have been."

---

## 0:27 — The backlog, with its weakest assumption exposed (6 min)

**Switch to the Evidence tab, scroll to the sensitivity table.**

> "Now the backlog. First, the arithmetic of why it exists, because I do not think
> it is a motivation problem.
>
> 1,400 complaints over 21 days is 67 a day. Three analysts at six productive hours
> is 1,080 minutes. So you break even at 16.2 minutes per complaint. If true handling
> time is around 18 minutes, you are running about 11% over capacity and losing
> roughly seven complaints a day. Six days behind is not bad luck — it is the steady
> state. Next quarter it is nine days.
>
> That has a hard consequence: a system that sorts your queue better but does not
> reduce minutes-per-complaint changes nothing. You would just be six days late on
> the right things."

> "On our assumptions, with triage and a draft already done, review drops to about 7
> minutes, capacity goes to 149 a day, and the backlog clears in about a week."

**Then immediately:**

> "And now the caveat, which matters more than the number. Every input there is ours,
> not yours. Nobody gave us handling times. So look at this table instead."

**Point at the 18-minute row where it flips to 'still grows'.**

> "This shows where the plan fails. If your analysts take more than about 16 minutes
> to review a pre-triaged case, this does not clear your backlog no matter how good
> the triage is.
>
> I do not know which side of that line you are on. Neither does anyone we spoke to.
> So the single most valuable thing you could do in the next fortnight is not build
> anything — it is run twenty complaints through this with a stopwatch. That one
> number replaces this entire table, and it decides whether the project is worth
> doing."

**For the headcount fear, unprompted:**

> "One more thing. You told us the analysts are not going anywhere, so we did not
> build toward that and the business case does not assume it. The case is that three
> analysts clear 67 a day instead of 60. Not that two do the work of three. That is
> also the only version that works in practice — a system your analysts think is
> there to replace them will get exactly the cooperation that deserves."

---

## 0:33 — What we need from you (3 min)

> "Four things, in order of how much they are blocking.
>
> **One hour from the two senior analysts and yourself** on those 44 complaints.
> This is blocking everything downstream of the word 'serious' and it costs an hour.
>
> **Two weeks of stopwatch data** on analyst review time. Replaces our entire
> business case with a real number.
>
> **A decision from Compliance and Legal** on whether redacted text may ever leave
> the building. That is the only thing standing between you and the 25% of quiet
> regulatory complaints we currently miss. Not urgent — the offline version works
> today — but it caps what this can do.
>
> **Two names.** Who owns credit-reporting disputes, and who owns 'I called three
> times and nobody called me back'. Both currently route to placeholder queues, and
> the first one has a 30-day statutory clock on it."

> "There are nine of these written up with what each one blocks. I would rather hand
> you the open questions than pretend we closed them."

---

## 0:36 — Close (2 min)

> "To summarise what is actually true today: 120 complaints go through end to end in
> under three milliseconds each. Zero PII in any case file, verified by a scanner
> that does not trust the redactor. Every regulatory complaint flagged at intake with
> a citation. Nothing sent to anybody, and nine tested attempts to get around
> that, all failing. Routing is right 93% of the time and every single one it gets
> wrong is marked for a human to confirm — it is not confidently wrong anywhere.
>
> And the thing I would take away if I were you: you do not have a definition of
> 'serious', and that is costing you more than the backlog is. The backlog is an
> arithmetic problem with a known fix. The definition is a one-hour conversation
> you have not had, and until you have it, no system anybody builds you — ours or
> anyone else's — can prioritise your queue."

---

## Likely objections

**"Why not just use ChatGPT / a model for all of it?"**
> For reading, a model would be better, and that is question one. For deciding, no —
> the issue is not capability. Your compliance officer needs to be able to answer
> "why was this flagged" by reading a file, and your ops head needs the same
> complaint to get the same answer on Tuesday as on Friday. We put the model where
> judgment about language belongs and kept policy in config you control.

**"Our complaints don't look like your synthetic ones."**
> Correct, and that is the main limitation. The numbers tell you properties hold and
> roughly where the ceiling is; they cannot tell you performance on your mail. A
> fortnight of your real volume, redacted, in a shadow run next to your analysts,
> would. That is the right next test and we would not claim anything stronger.

**"Three milliseconds — is it actually doing anything?"**
> It is pattern matching, not thinking. That is a deliberate trade: it means no API
> bill, no network, nothing to explain to Infosec, and output you can audit by
> reading a config file. The cost is the 25% of quiet complaints it misses, which we
> measured and showed you rather than hid.

**"What happens when it gets one wrong?"**
> An analyst sees it, because every one of them reaches an analyst. That is the point
> of the gate. Worst case on this corpus is a complaint arriving in the wrong
> department's queue with "confirm department" on it and the right answer listed
> underneath — which is roughly what happens today when a human misroutes one,
> except faster and with the alternative attached.

**"How much will the real thing cost to run?"**
> As shown: nothing per complaint. If you enable the model engine, roughly two cents
> a complaint — under thirty dollars a month at your volume. The cost of this project
> is people's time, not compute, and the biggest line item is the hour of calibration
> I keep asking for.
