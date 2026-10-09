"""The human-approval gate.

These tests exist to answer one question in a way a compliance officer can
check: can any sequence of calls get customer-facing text out of this system
without a named human approving it? Each test is an attempted bypass.
"""
from __future__ import annotations

import unittest

from _helpers import TempStore  # noqa: E402

from mhb import config, corpus, review, store  # noqa: E402
from mhb.models import TERMINAL_HUMAN_STATES  # noqa: E402
from mhb.orchestrator import Pipeline  # noqa: E402


class GateCase(unittest.TestCase):
    def setUp(self):
        self.ts = TempStore().__enter__()
        self.cfg = config.load()
        self.pipe = Pipeline(self.cfg, "rules")
        self.cases = [self.pipe.run(corpus.to_raw(r)) for r in corpus.load(12)]
        self.pending = next(c for c in self.cases if c.state == "PENDING_HUMAN_APPROVAL")

    def tearDown(self):
        self.ts.__exit__()


class TestPipelineCannotSend(GateCase):
    def test_pipeline_never_produces_an_approved_case(self):
        self.assertEqual(
            [c.case_id for c in self.cases if c.state == "APPROVED_FOR_SEND"], [])

    def test_terminal_state_is_pending_or_blocked(self):
        for c in self.cases:
            self.assertIn(c.state, ("PENDING_HUMAN_APPROVAL", "BLOCKED_QA"))

    def test_no_case_has_an_approver_after_the_pipeline(self):
        for c in self.cases:
            self.assertIsNone(c.human.get("approver"))

    def test_policy_forbids_agent_sending(self):
        self.assertFalse(self.cfg.policy["gates"]["agents_may_send_customer_text"])
        self.assertTrue(self.cfg.policy["gates"]["customer_text_requires_human_approval"])

    def test_qa_blocks_a_case_that_arrives_pre_approved(self):
        """If anything upstream ever marks a case approved, QA catches it."""
        from mhb.agents import QAAgent
        case = self.pipe.run(corpus.to_raw(corpus.load(1)[0]), persist=False)
        case.human["approver"] = "SomeAgent"
        case.qa = None
        QAAgent(self.cfg, self.pipe.engine)(case)
        self.assertFalse(case.qa.passed)
        self.assertEqual(case.state, "BLOCKED_QA")
        self.assertTrue(any("already marked approved" in b["detail"] for b in case.qa.blocks))


class TestApprovalRequiresAHuman(GateCase):
    def test_empty_approver_is_refused(self):
        with self.assertRaises(review.ApprovalError):
            review.approve(self.pending.case_id, "")

    def test_whitespace_approver_is_refused(self):
        with self.assertRaises(review.ApprovalError):
            review.approve(self.pending.case_id, "   ")

    def test_named_approver_succeeds(self):
        c = review.approve(self.pending.case_id, "D. Okafor, Senior Analyst")
        self.assertEqual(c["state"], "APPROVED_FOR_SEND")
        self.assertEqual(c["human"]["approver"], "D. Okafor, Senior Analyst")
        self.assertIsNotNone(c["human"]["approved_at"])

    def test_double_approval_is_refused(self):
        review.approve(self.pending.case_id, "D. Okafor")
        with self.assertRaises(review.ApprovalError):
            review.approve(self.pending.case_id, "Someone Else")

    def test_qa_blocked_case_cannot_be_approved(self):
        case = self.pending
        d = store.load_case(case.case_id)
        d["state"] = "BLOCKED_QA"
        d["qa"]["passed"] = False
        d["qa"]["blocks"] = [{"check": "QA-PII-LEAK", "detail": "planted for test"}]
        store.save_case(d)
        with self.assertRaises(review.ApprovalError) as ctx:
            review.approve(case.case_id, "D. Okafor")
        self.assertIn("blocked by QA", str(ctx.exception))

    def test_analyst_edits_are_recorded_as_a_diff(self):
        original = store.load_case(self.pending.case_id)["draft"]["body"]
        edited = original.replace("Thank you for", "Thanks so much for") + "\nOne more line."
        c = review.approve(self.pending.case_id, "D. Okafor", edited_body=edited)
        self.assertTrue(c["human"]["edits"]["edited"])
        self.assertGreater(c["human"]["edits"]["changed_lines"], 0)
        self.assertGreater(c["human"]["edits"]["edit_ratio"], 0)


class TestRehydrationIsGated(GateCase):
    def test_render_before_approval_is_refused(self):
        with self.assertRaises(review.ApprovalError) as ctx:
            review.render_for_send(self.pending.case_id)
        self.assertIn("requires an approved case", str(ctx.exception))

    def test_render_after_approval_restores_real_values(self):
        review.approve(self.pending.case_id, "D. Okafor")
        letter = review.render_for_send(self.pending.case_id)
        self.assertNotIn("[ACCT_", letter)
        self.assertIn(self.pending.case_id, letter)

    def test_rendered_letter_has_no_unresolved_placeholders(self):
        """A letter addressed to "{{CUSTOMER_NAME}}" must not be able to leave
        this function."""
        import re
        for case in self.cases:
            if case.state != "PENDING_HUMAN_APPROVAL":
                continue
            review.approve(case.case_id, "D. Okafor")
            letter = review.render_for_send(case.case_id)
            self.assertEqual(
                re.findall(r"\{\{[A-Z_]+\}\}|\[[A-Z]+_\d+\]", letter), [],
                f"{case.case_id} rendered with an unresolved placeholder")

    def test_analyst_name_is_the_approver(self):
        review.approve(self.pending.case_id, "D. Okafor, Senior Analyst")
        self.assertIn("D. Okafor, Senior Analyst",
                      review.render_for_send(self.pending.case_id))

    def test_render_guard_catches_an_unresolvable_slot(self):
        d = store.load_case(self.pending.case_id)
        d["draft"]["body"] += "\n\n{{UNKNOWN_SLOT}}"
        store.save_case(d)
        review.approve(self.pending.case_id, "D. Okafor")
        with self.assertRaises(review.ApprovalError) as ctx:
            review.render_for_send(self.pending.case_id)
        self.assertIn("unresolved placeholder", str(ctx.exception))

    def test_rejected_case_cannot_be_rendered(self):
        review.reject(self.pending.case_id, "D. Okafor", "wrong department")
        with self.assertRaises(review.ApprovalError):
            review.render_for_send(self.pending.case_id)

    def test_rejection_requires_a_named_reviewer(self):
        with self.assertRaises(review.ApprovalError):
            review.reject(self.pending.case_id, "", "no")


class TestAuditTrail(GateCase):
    def test_every_agent_leaves_a_decision_record(self):
        c = self.cases[0]
        agents = {d.agent for d in c.decisions}
        self.assertEqual(
            agents,
            {"intake", "redaction", "regulatory_tripwire", "severity", "routing",
             "drafting", "qa_supervisor"},
        )

    def test_decisions_carry_ruleset_versions(self):
        c = self.cases[0]
        by_agent = {d.agent: d for d in c.decisions}
        self.assertIn("redaction.v1", by_agent["redaction"].ruleset_ids)
        self.assertIn("severity.A.v1", by_agent["severity"].ruleset_ids)
        self.assertIn("regulatory.v1", by_agent["regulatory_tripwire"].ruleset_ids)

    def test_approval_is_written_to_the_audit_log(self):
        review.approve(self.pending.case_id, "D. Okafor")
        events = [e for e in store.read_audit() if e["event"] == "human_approval"]
        self.assertTrue(events)
        self.assertEqual(events[-1]["approver"], "D. Okafor")

    def test_terminal_human_states_are_the_only_closures(self):
        review.approve(self.pending.case_id, "D. Okafor")
        self.assertIn(store.load_case(self.pending.case_id)["state"], TERMINAL_HUMAN_STATES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
