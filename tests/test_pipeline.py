"""End-to-end behaviour: channel normalisation, ordering, determinism, SLAs."""
from __future__ import annotations

import unittest

from _helpers import TempStore  # noqa: E402

from mhb import clock, config, corpus, metrics, review, store  # noqa: E402
from mhb.agents.intake import IntakeAgent  # noqa: E402
from mhb.models import RawComplaint  # noqa: E402
from mhb.orchestrator import METADATA_ALLOW, Pipeline  # noqa: E402

EMAIL = """I was charged twice for the same purchase of $84.00.

Please call me at (503) 555-0147.

Best regards,
Mr. Harold Ferris
1420 Pearl Street

--
Sent from my iPhone

On Mon, Sep 28, 2026 at 9:02 AM Meridian Harbor Bank <noreply@mhb.example> wrote:
> Thank you for contacting us. Your reference is 88213.
"""

FORM = """Full name: Denise Okonkwo
Email: denise@example.com
Phone: (727) 555-5023
Account type: Checking
Branch: Riverside
Describe your complaint:
Three overdraft fees of $38 each hit my account on the same morning.
"""

NOTE = """CALL NOTE - rep R411
Caller: Mr. Marcus Baptiste - CB (415) 555-2211
cx clmd unauth txn on dc, acct 4471902233. cx frst, refd to mgr at br.
"""


class TestIntakeNormalisation(unittest.TestCase):
    def setUp(self):
        self.agent = IntakeAgent(config.load())

    def _norm(self, channel, text):
        return self.agent.normalize(
            RawComplaint("T", channel, "2026-09-14T08:00:00", text))

    def test_email_signature_and_quoted_reply_are_stripped(self):
        text, info = self._norm("email", EMAIL)
        self.assertNotIn("Sent from my iPhone", text)
        self.assertNotIn("Your reference is 88213", text)
        self.assertIn("charged twice", text)

    def test_web_form_fields_become_metadata_not_narrative(self):
        text, info = self._norm("web_form", FORM)
        self.assertIn("Three overdraft fees", text)
        self.assertNotIn("Preferred contact", text)
        self.assertTrue(any("form fields" in n for n in info["notes"]))

    def test_call_note_shorthand_is_expanded(self):
        text, info = self._norm("call_note", NOTE)
        self.assertIn("unauthorized", text)
        self.assertIn("transaction", text)
        self.assertIn("account", text)
        self.assertIn("manager", text)
        self.assertNotIn("clmd", text.replace("claimed", ""))

    def test_unknown_channel_is_flagged_for_a_human(self):
        raw = RawComplaint("T", "carrier_pigeon", "2026-09-14T08:00:00", "Help me.")
        case, text, info = self.agent.build_case(raw)
        self.assertTrue(case.human["unknown_channel"])
        self.assertTrue(any("UNKNOWN CHANNEL" in n for n in info["notes"]))

    def test_case_ids_never_look_like_an_account_number(self):
        """Regression: an all-digit id tripped the compliance lint rule that
        looks for bare digit runs in customer-facing text."""
        import re
        for i in range(400):
            raw = RawComplaint(f"T{i}", "email", "2026-09-14T08:00:00", "x")
            case, _, _ = self.agent.build_case(raw)
            self.assertIsNone(re.search(r"\d{6,}", case.case_id), case.case_id)
            self.assertFalse(set("IO01") & set(case.case_id[4:]), case.case_id)


class TestPipelineOrdering(unittest.TestCase):
    def setUp(self):
        self.ts = TempStore().__enter__()
        self.cfg = config.load()
        self.pipe = Pipeline(self.cfg, "rules")

    def tearDown(self):
        self.ts.__exit__()

    def test_agents_run_in_the_required_order(self):
        case = self.pipe.run(corpus.to_raw(corpus.load(1)[0]), persist=False)
        self.assertEqual(
            [d.agent for d in case.decisions],
            ["intake", "redaction", "regulatory_tripwire", "severity", "routing",
             "drafting", "qa_supervisor"],
        )

    def test_redaction_precedes_every_interpretation_step(self):
        case = self.pipe.run(corpus.to_raw(corpus.load(1)[0]), persist=False)
        order = [d.agent for d in case.decisions]
        red = order.index("redaction")
        for later in ("regulatory_tripwire", "severity", "routing", "drafting"):
            self.assertGreater(order.index(later), red)

    def test_unknown_channel_is_blocked_by_qa(self):
        raw = RawComplaint("T", "carrier_pigeon", "2026-09-14T08:00:00",
                           "I did not authorize a charge of $400.")
        case = self.pipe.run(raw, persist=False)
        self.assertEqual(case.state, "BLOCKED_QA")
        self.assertTrue(any(b["check"] == "QA-CHANNEL" for b in case.qa.blocks))

    def test_metadata_is_allowlisted(self):
        raw = corpus.to_raw(corpus.load(1)[0])
        raw.metadata["customer_ssn"] = "512-44-9087"
        raw.metadata["secret_note"] = "do not copy"
        case = self.pipe.run(raw, persist=False)
        self.assertNotIn("customer_ssn", case.human["metadata"])
        self.assertNotIn("secret_note", case.human["metadata"])
        for k in case.human["metadata"]:
            self.assertIn(k, METADATA_ALLOW)

    def test_sla_clocks_are_consistent(self):
        for row in corpus.load(20):
            case = self.pipe.run(corpus.to_raw(row), persist=False)
            self.assertEqual(case.sla["received_at"], case.received_at)
            self.assertGreater(
                clock.hours_between(case.received_at, case.sla["resolve_due"]), 0)
            if case.regulatory_ids:
                self.assertEqual(
                    clock.hours_between(case.received_at, case.sla["compliance_flag_due"]),
                    48.0)

    def test_every_regulatory_case_gets_a_compliance_clock(self):
        for row in corpus.load(40):
            case = self.pipe.run(corpus.to_raw(row), persist=False)
            if case.regulatory_ids:
                self.assertIn("compliance_flag_due", case.sla, case.case_id)

    def test_pipeline_is_deterministic(self):
        rows = corpus.load(20)
        def fp(c):
            return (c.severity.band, c.severity_alt.band, c.routing.department,
                    tuple(sorted(c.regulatory_ids)), c.draft.body, c.state,
                    tuple(sorted(c.severity.dimension_levels.items())))
        a = [fp(self.pipe.run(corpus.to_raw(r), persist=False)) for r in rows]
        b = [fp(self.pipe.run(corpus.to_raw(r), persist=False)) for r in rows]
        self.assertEqual(a, b)

    def test_rubric_choice_changes_bands_but_not_the_gate(self):
        pipe_b = Pipeline(config.load("B"), "rules")
        rows = corpus.load(20)
        a = [self.pipe.run(corpus.to_raw(r), persist=False) for r in rows]
        b = [pipe_b.run(corpus.to_raw(r), persist=False) for r in rows]
        self.assertNotEqual([c.severity.band for c in a], [c.severity.band for c in b])
        for c in a + b:
            self.assertIn(c.state, ("PENDING_HUMAN_APPROVAL", "BLOCKED_QA"))

    def test_audit_log_records_each_run(self):
        self.pipe.run(corpus.to_raw(corpus.load(1)[0]))
        events = [e for e in store.read_audit() if e["event"] == "pipeline_run"]
        self.assertEqual(len(events), 1)
        self.assertIn("band", events[0])
        self.assertIn("qa_passed", events[0])


class TestQueuesAndMetrics(unittest.TestCase):
    def setUp(self):
        self.ts = TempStore().__enter__()
        self.cfg = config.load()
        pipe = Pipeline(self.cfg, "rules")
        self.cases = [pipe.run(corpus.to_raw(r)) for r in corpus.load(40)]

    def tearDown(self):
        self.ts.__exit__()

    def test_compliance_queue_holds_exactly_the_regulatory_cases(self):
        q = review.queues()
        expected = {c.case_id for c in self.cases if c.regulatory_ids}
        self.assertEqual({c["case_id"] for c in q["compliance_48h"]}, expected)

    def test_approval_queue_is_severity_ordered(self):
        rank = {"S1": 0, "S2": 1, "S3": 2, "S4": 3}
        bands = [rank[c["severity"]["band"]] for c in review.queues()["approval"]]
        self.assertEqual(bands, sorted(bands))

    def test_calibration_queue_matches_the_flags(self):
        q = review.queues()
        self.assertEqual({c["case_id"] for c in q["calibration"]},
                         {c.case_id for c in self.cases if c.calibration_flag})

    def test_calibration_report_totals_are_consistent(self):
        r = metrics.calibration_report()
        self.assertEqual(r["n"], len(self.cases))
        self.assertEqual(r["agree"] + r["disagreement_count"], r["n"])
        self.assertEqual(sum(r["band_counts_a"].values()), r["n"])
        self.assertEqual(sum(r["band_counts_b"].values()), r["n"])

    def test_every_disagreement_has_a_stated_driver(self):
        for row in metrics.calibration_report()["disagreements"]:
            self.assertTrue(row["driver"])

    def test_approved_case_leaves_the_approval_queue(self):
        pending = next(c for c in self.cases if c.state == "PENDING_HUMAN_APPROVAL")
        review.approve(pending.case_id, "D. Okafor")
        q = review.queues()
        self.assertNotIn(pending.case_id, {c["case_id"] for c in q["approval"]})
        self.assertIn(pending.case_id, {c["case_id"] for c in q["done"]})


class TestThroughputModel(unittest.TestCase):
    def test_model_identifies_the_breakeven_point(self):
        cfg = config.load()
        m = metrics.throughput_model(cfg.policy)
        self.assertLess(m["baseline"]["net_per_day"], 0, "baseline must explain a growing backlog")
        self.assertGreater(m["assisted"]["net_per_day"], 0)
        self.assertGreater(m["breakeven_minutes_per_complaint"], 0)

    def test_sensitivity_table_crosses_breakeven(self):
        m = metrics.throughput_model(config.load().policy)
        verdicts = [s["drains_backlog"] for s in m["sensitivity"]]
        self.assertIn(True, verdicts)
        self.assertIn(False, verdicts, "the table must show where the business case fails")

    def test_measured_values_override_assumptions(self):
        cfg = config.load()
        base = metrics.throughput_model(cfg.policy)
        worse = metrics.throughput_model(
            cfg.policy, measured={"assisted_minutes_per_complaint_routine": 15.0})
        self.assertLess(worse["assisted"]["capacity_per_day"],
                        base["assisted"]["capacity_per_day"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
