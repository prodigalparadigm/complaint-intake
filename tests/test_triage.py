"""Regulatory tripwire, severity rubrics, routing and drafting."""
from __future__ import annotations

import unittest

from _helpers import TempStore  # noqa: E402

from mhb import clock, config, corpus  # noqa: E402
from mhb.agents import (  # noqa: E402
    DraftingAgent, RegulatoryAgent, RoutingAgent, SeverityAgent,
)
from mhb.engines.rules import RulesEngine  # noqa: E402
from mhb.models import DIMENSIONS, Case, Signal  # noqa: E402
from mhb.orchestrator import Pipeline  # noqa: E402


def blank_case(text: str, received="2026-09-14T08:00:00") -> Case:
    c = Case(case_id="MHB-TESTAAAA", complaint_id="T1", channel="email",
             received_at=received, content_hash="h")
    c.redaction = {"redacted_text": text, "redacted_subject": "", "vault_id": "v",
                   "pii_count": 0, "pii_kinds": [], "placeholders": [],
                   "residual_risk_notes": []}
    c.human = {"metadata": {}}
    return c


class TestRegulatoryTripwire(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()
        self.agent = RegulatoryAgent(self.cfg, RulesEngine(self.cfg))

    def test_detects_a_disputed_transaction(self):
        f = self.agent.assess("There is a charge I did not make on my card.")
        self.assertIn("REG_E_EFT", [x.category_id for x in f if x.strength == "strong"])

    def test_carries_the_citation_and_the_clock(self):
        f = next(x for x in self.agent.assess("I did not authorize this charge.")
                 if x.category_id == "REG_E_EFT")
        self.assertIn("1005.11", f.citation)
        self.assertIn("10 business days", f.statutory_clock)

    def test_local_negation_downgrades_to_weak(self):
        f = self.agent.assess(
            "I am not saying this was unauthorized, I just want the balance fixed.")
        rege = [x for x in f if x.category_id == "REG_E_EFT"]
        self.assertTrue(rege)
        self.assertEqual(rege[0].strength, "weak")

    def test_document_scope_disclaimer_downgrades_a_later_hit(self):
        """Regression: an explicit up-front disclaimer followed by incidental
        vocabulary later tripped Reg E and then misrouted the whole case."""
        f = self.agent.assess(
            "I want to be clear that I am not saying this was unauthorized — my wife made it. "
            "The problem is your app showed the wrong balance and made me think we had been "
            "charged twice.")
        rege = [x for x in f if x.category_id == "REG_E_EFT"]
        self.assertTrue(rege)
        self.assertEqual(rege[0].strength, "weak",
                         "document-scope disclaimer should downgrade the whole category")

    def test_regulator_and_legal_threats_are_separate_categories(self):
        reg = [x.category_id for x in self.agent.assess("I have filed with the CFPB.")]
        legal = [x.category_id for x in self.agent.assess("My attorney advised me to write.")]
        self.assertIn("REGULATOR_CONTACT", reg)
        self.assertNotIn("REGULATOR_CONTACT", legal)
        self.assertIn("LEGAL_MEDIA_THREAT", legal)

    def test_bbb_is_not_treated_as_a_regulator(self):
        ids = [x.category_id for x in self.agent.assess(
            "I have contacted the Better Business Bureau.")]
        self.assertNotIn("REGULATOR_CONTACT", ids)

    def test_no_trigger_means_no_finding(self):
        self.assertEqual(self.agent.assess("Your branch hours are inconvenient."), [])

    def test_clock_runs_from_receipt_not_processing(self):
        case = blank_case("I did not authorize this charge.", received="2026-09-14T08:00:00")
        self.agent(case)
        self.assertEqual(case.sla["compliance_flag_due"], "2026-09-16T08:00:00")
        self.assertEqual(
            clock.hours_between(case.received_at, case.sla["compliance_flag_due"]), 48.0)

    def test_recall_bias_is_configured(self):
        for cat in self.cfg.regulatory["categories"]:
            self.assertEqual(cat["min_hits"], 1,
                             f"{cat['id']} should need only one hit to surface")


class TestSeverityRubrics(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()
        self.agent = SeverityAgent(self.cfg, RulesEngine(self.cfg))

    def _signals(self, **levels):
        return [Signal(d, levels.get(d, 0), "TEST", "test evidence") for d in DIMENSIONS]

    def test_rubrics_share_the_same_dimensions(self):
        self.assertEqual(set(self.cfg.rubric_primary["dimensions"]),
                         set(self.cfg.rubric_secondary["dimensions"]))

    def test_all_zero_is_routine(self):
        a = self.agent.apply_rubric(self.cfg.rubric_primary, self._signals())
        self.assertEqual(a.band, "S4")
        self.assertEqual(a.raw_score, 0.0)

    def test_acute_vulnerability_forces_s1_under_rubric_a(self):
        a = self.agent.apply_rubric(self.cfg.rubric_primary, self._signals(vulnerability=3))
        self.assertEqual(a.band, "S1")
        self.assertTrue(any(o["id"] == "A-OV1" and o["from"] != o["to"]
                            for o in a.overrides_applied))

    def test_regulator_contact_forces_s1_under_rubric_b(self):
        b = self.agent.apply_rubric(self.cfg.rubric_secondary,
                                    self._signals(escalation_signals=3))
        self.assertEqual(b.band, "S1")

    def test_the_rubrics_genuinely_disagree_on_pure_exposure(self):
        """A fair-lending complaint with no money attached: rubric A rates it
        routine, rubric B rates it critical. This is the finding, not a bug."""
        sig = self._signals(regulatory_exposure=3)
        a = self.agent.apply_rubric(self.cfg.rubric_primary, sig)
        b = self.agent.apply_rubric(self.cfg.rubric_secondary, sig)
        self.assertEqual(a.band, "S4")
        self.assertEqual(b.band, "S1")

    def test_the_rubrics_genuinely_disagree_on_pure_harm(self):
        sig = self._signals(financial_harm=3, vulnerability=2)
        a = self.agent.apply_rubric(self.cfg.rubric_primary, sig)
        b = self.agent.apply_rubric(self.cfg.rubric_secondary, sig)
        self.assertLess(["S1", "S2", "S3", "S4"].index(a.band),
                        ["S1", "S2", "S3", "S4"].index(b.band))

    def test_calibration_flag_raised_on_a_two_band_gap(self):
        case = blank_case("I applied for a loan and was denied because of my age. "
                          "Nobody would tell me why I was denied.")
        RegulatoryAgent(self.cfg, RulesEngine(self.cfg))(case)
        self.agent(case)
        self.assertNotEqual(case.severity.band, case.severity_alt.band)
        self.assertTrue(case.calibration_flag)
        self.assertIn("would triage this complaint differently", case.calibration_note)

    def test_a_categorical_override_is_more_confident_than_a_score(self):
        override = self.agent.apply_rubric(self.cfg.rubric_primary,
                                           self._signals(vulnerability=3))
        scored = self.agent.apply_rubric(self.cfg.rubric_primary,
                                         self._signals(financial_harm=1, recurrence=1))
        self.assertGreater(override.confidence, scored.confidence)

    def test_confidence_is_lowest_right_at_a_band_cutoff(self):
        """Regression: the confidence slope used to saturate at the 0.95 ceiling
        for anything more than 0.1 from a cutoff, so the number said nothing."""
        rubric = self.cfg.rubric_primary
        cutoff = next(b["min_normalized"] for b in rubric["bands"] if b["id"] == "S2")
        max_possible = sum(float(d["weight"]) * 3 for d in rubric["dimensions"].values())
        # Construct a case landing almost exactly on the S2 cutoff.
        best, best_gap = None, 9.9
        for harm in range(4):
            for vuln in range(4):
                for expo in range(4):
                    sig = self._signals(financial_harm=harm, vulnerability=vuln,
                                        regulatory_exposure=expo)
                    a = self.agent.apply_rubric(rubric, sig)
                    gap = abs(a.normalized - cutoff)
                    if gap < best_gap and not a.overrides_applied:
                        best, best_gap = a, gap
        self.assertIsNotNone(best)
        self.assertLess(best_gap, 0.05)
        self.assertLess(best.confidence, 0.65,
                        "a case sitting on a band cutoff must report low confidence")

    def test_every_dimension_is_scored_with_evidence(self):
        engine = RulesEngine(self.cfg)
        sig = engine.extract_signals("I was charged $500 I did not authorize.", {}, [])
        self.assertEqual({s.dimension for s in sig}, set(DIMENSIONS))
        for s in sig:
            self.assertTrue(s.evidence, f"{s.dimension} has no evidence string")
            self.assertTrue(s.rule_id)


class TestRouting(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load()
        self.agent = RoutingAgent(self.cfg, RulesEngine(self.cfg))

    def test_vocabulary_routing(self):
        d = self.agent.decide("My mobile app keeps crashing when I log in.", [])
        self.assertEqual(d.department, "DIGITAL_BANKING")

    def test_word_boundaries_are_respected(self):
        """Regression: 'applied' matched the term 'app', and 'transfer'
        matched the term 'nsf'."""
        d = self.agent.decide(
            "I applied for an auto loan and the loan application was declined.", [])
        self.assertEqual(d.department, "LENDING")
        d2 = self.agent.decide("I asked for a transfer between my accounts.", [])
        self.assertNotIn("nsf", d2.reason)

    def test_ownership_rule_overrides_vocabulary(self):
        d = self.agent.decide("My mobile app showed a charge I did not authorize.",
                              ["REG_E_EFT"])
        self.assertEqual(d.department, "CARDS_DISPUTES")
        self.assertEqual(d.tiebreaker_applied.split(" [")[0], "REG_E_EFT")

    def test_ownership_rule_conflict_lowers_confidence(self):
        """The ops head's Reg E rule sends identity theft to Cards & Disputes.
        The rule wins, but the conflict must be visible."""
        d = self.agent.decide(
            "Someone used my identity, there is a suspicious login, this is identity theft "
            "and there were withdrawals I did not authorize. Fraud on my account.",
            ["REG_E_EFT"])
        self.assertEqual(d.department, "CARDS_DISPUTES")
        self.assertLess(d.confidence, self.cfg.policy["confidence_thresholds"]["routing_autoaccept"])
        self.assertIn("may be too broad", d.reason)
        self.assertIn("conflict:FRAUD_SECURITY", d.tiebreaker_applied)

    def test_unmatched_text_falls_back_with_low_confidence(self):
        d = self.agent.decide("I have a general question about things.", [])
        self.assertEqual(d.department, self.cfg.routing["fallback"])
        self.assertLess(d.confidence, 0.5)

    def test_close_calls_are_reported(self):
        d = self.agent.decide(
            "An overdraft fee hit my checking account and the mobile app would not load.", [])
        self.assertTrue(d.alternates)


class TestDrafting(unittest.TestCase):
    def setUp(self):
        self.ts = TempStore().__enter__()
        self.cfg = config.load()
        self.pipe = Pipeline(self.cfg, "rules")

    def tearDown(self):
        self.ts.__exit__()

    def _case(self, text):
        return self.pipe.run(
            corpus.to_raw({"complaint_id": "T", "channel": "email",
                           "received_at": "2026-09-14T08:00:00", "text": text,
                           "subject": "", "metadata": {}}), persist=False)

    def test_draft_never_contains_an_unfilled_pipeline_slot(self):
        c = self._case("I did not authorize a charge of $400 on my card.")
        for slot in ("{{CASE_ID}}", "{{ISSUE_SUMMARY}}", "{{DEPARTMENT_LABEL}}",
                     "{{NEXT_STEP}}"):
            self.assertNotIn(slot, c.draft.body)

    def test_customer_name_is_deliberately_deferred(self):
        c = self._case("I did not authorize a charge of $400 on my card.")
        self.assertIn("{{CUSTOMER_NAME}}", c.draft.body)
        self.assertIn("{{CUSTOMER_NAME}}", self.cfg.templates["deferred_slots"])

    def test_draft_quotes_its_own_case_id(self):
        c = self._case("I did not authorize a charge of $400 on my card.")
        self.assertIn(c.case_id, c.draft.body)

    def test_reg_e_complaint_gets_the_investigation_template(self):
        c = self._case("I did not authorize a charge of $400 on my card.")
        self.assertEqual(c.draft.template_id, "T-ACK-REGE")

    def test_hardship_complaint_gets_the_hardship_template(self):
        c = self._case("Your hold on my deposit means I cannot pay my rent. "
                       "I am on a fixed income.")
        self.assertEqual(c.draft.template_id, "T-ACK-HARDSHIP")

    def test_draft_states_no_outcome(self):
        c = self._case("I did not authorize a charge of $400 on my card.")
        body = c.draft.body.lower()
        for forbidden in ("we will refund", "we were at fault", "we guarantee",
                          "has been refunded", "we admit"):
            self.assertNotIn(forbidden, body)

    def test_lint_blocks_an_admission_of_liability(self):
        agent = DraftingAgent(self.cfg, RulesEngine(self.cfg))
        findings = agent.lint("Subject", "We were at fault and we will refund you in full.")
        rules = {f["rule"] for f in findings if f["severity"] == "block"}
        self.assertIn("L-ADMIT", rules)
        self.assertIn("L-REFUND", rules)

    def test_lint_blocks_a_bare_identifier(self):
        agent = DraftingAgent(self.cfg, RulesEngine(self.cfg))
        findings = agent.lint("Subject", "Your account 4471902233 has been reviewed.")
        self.assertIn("L-PII", {f["rule"] for f in findings})

    def test_lint_blocks_an_unapproved_deadline(self):
        agent = DraftingAgent(self.cfg, RulesEngine(self.cfg))
        findings = agent.lint("Subject", "We will fix this within 48 hours.")
        self.assertIn("L-DEADLINE", {f["rule"] for f in findings})

    def test_lint_allows_a_clean_template(self):
        agent = DraftingAgent(self.cfg, RulesEngine(self.cfg))
        clean = ("Dear {{CUSTOMER_NAME}},\n\nThank you for contacting us about your account. "
                 "We have opened case MHB-ABCDEFGH and will write to you with what we find.\n\n"
                 "Sincerely,\n{{ANALYST_NAME}}")
        self.assertEqual(agent.lint("We received your complaint", clean), [])

    def test_issue_topic_comes_from_the_approved_map(self):
        c = self._case("I did not authorize a charge of $400 on my card.")
        approved = set(self.cfg.templates["issue_topics"]["by_regulatory"].values()) | \
            set(self.cfg.templates["issue_topics"]["by_department"].values()) | \
            {self.cfg.templates["issue_topics"]["fallback"]}
        self.assertTrue(any(t in c.draft.body for t in approved),
                        "letter must use a compliance-approved topic phrase")

    def test_analyst_summary_is_kept_out_of_the_letter(self):
        c = self._case("I did not authorize a charge of $400 on my card.")
        self.assertTrue(c.human.get("analyst_summary"))
        self.assertNotIn(c.human["analyst_summary"], c.draft.body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
