"""What would leave the building if the Claude engine were switched on.

No network calls. `build_request` is pure, so the exact outbound payload can be
inspected offline — which is the only way to make a falsifiable promise about
egress before anyone has signed off on enabling it.
"""
from __future__ import annotations

import json
import unittest

from _helpers import TempStore  # noqa: E402

from mhb import config, corpus, leak, store  # noqa: E402
from mhb.agents.redaction import RedactionAgent  # noqa: E402
from mhb.engines.claude import ClaudeEngine, EgressViolation  # noqa: E402
from mhb.orchestrator import Pipeline  # noqa: E402

RAW = (
    "Mr. Harold Ferris here. Account 4471902233, card 4111111111111111, "
    "phone (503) 555-0147, email harold.ferris@example.com, SSN 512-44-9087. "
    "I did not authorize a charge of $1,249.00."
)


class TestOutboundPayload(unittest.TestCase):
    def setUp(self):
        self.ts = TempStore().__enter__()
        self.cfg = config.load()
        self.engine = ClaudeEngine(self.cfg)

    def tearDown(self):
        self.ts.__exit__()

    def test_payload_carries_no_pii(self):
        red = RedactionAgent(self.cfg).redact(RAW, "", "MHB-TEST")
        req = self.engine.build_request(red.redacted_text, {"channel": "email"})
        blob = json.dumps(req)
        for raw in ("4471902233", "4111111111111111", "(503) 555-0147",
                    "harold.ferris@example.com", "512-44-9087", "Harold Ferris"):
            self.assertNotIn(raw, blob)

    def test_payload_preserves_the_signal_the_model_needs(self):
        red = RedactionAgent(self.cfg).redact(RAW, "", "MHB-TEST")
        blob = json.dumps(self.engine.build_request(red.redacted_text, {}))
        self.assertIn("did not authorize", blob)
        self.assertIn("1,249.00", blob)

    def test_egress_guard_raises_on_planted_pii(self):
        """The guard must be the thing that stops a redaction bug, not a log line."""
        RedactionAgent(self.cfg).redact(RAW, "", "MHB-TEST")
        vault = store.all_vault_values()
        self.assertTrue(vault)
        bad = self.engine.build_request(
            "Customer says account 4471902233 was debited.", {})
        with self.assertRaises(EgressViolation):
            ClaudeEngine._egress_guard(bad, vault)

    def test_egress_guard_passes_clean_payload(self):
        RedactionAgent(self.cfg).redact(RAW, "", "MHB-TEST")
        ok = self.engine.build_request("Customer says account [ACCT_1] was debited.", {})
        ClaudeEngine._egress_guard(ok, store.all_vault_values())  # must not raise

    def test_guard_fingerprints_rather_than_echoing_the_value(self):
        RedactionAgent(self.cfg).redact(RAW, "", "MHB-TEST")
        bad = self.engine.build_request("account 4471902233", {})
        try:
            ClaudeEngine._egress_guard(bad, store.all_vault_values())
            self.fail("expected EgressViolation")
        except EgressViolation as e:
            self.assertNotIn("4471902233", str(e))
            self.assertIn("***", str(e))

    def test_request_targets_the_configured_model_and_structured_output(self):
        req = self.engine.build_request("placeholder text", {})
        self.assertEqual(req["model"], "claude-opus-5")
        self.assertEqual(req["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(req["thinking"], {"type": "adaptive"})

    def test_schema_constrains_categories_to_our_config(self):
        req = self.engine.build_request("placeholder text", {})
        schema = req["output_config"]["format"]["schema"]
        enum = schema["properties"]["regulatory_candidates"]["items"]["properties"]["category_id"]["enum"]
        self.assertEqual(set(enum), {c["id"] for c in self.cfg.regulatory["categories"]})

    def test_system_prompt_tells_the_model_placeholders_are_opaque(self):
        req = self.engine.build_request("x", {})
        system = req["system"][0]["text"]
        self.assertIn("de-identified", system)
        self.assertIn("Never", system)
        self.assertIn("not drafting a reply", system)

    def test_every_corpus_payload_is_clean(self):
        pipe = Pipeline(self.cfg, "rules")
        for row in corpus.load(40):
            case = pipe.run(corpus.to_raw(row))
        vault = store.all_vault_values()
        for c in store.all_cases():
            req = self.engine.build_request(
                c["redaction"]["redacted_text"], c["human"].get("metadata", {}))
            self.assertEqual(leak.scan(json.dumps(req), vault), [],
                             f"PII in outbound payload for {c['case_id']}")


class TestEngineCannotSetPolicy(unittest.TestCase):
    """The architectural promise: an engine interprets language, it never
    decides what the bank does. A model that claims maximum regulatory
    exposure cannot talk its way past Compliance's own threshold."""

    def setUp(self):
        self.cfg = config.load()

    def test_model_exposure_is_capped_by_accepted_findings(self):
        engine = ClaudeEngine(self.cfg)
        engine._cache["k"] = {
            "issue_summary": "s",
            "regulatory_candidates": [],
            "dimensions": {d: {"level": 3, "evidence": "x"} for d in
                           ("financial_harm", "vulnerability", "regulatory_exposure",
                            "recurrence", "duration_unresolved", "escalation_signals",
                            "relationship_value")},
            "department_scores": [],
        }
        engine._interpret = lambda text, meta: engine._cache["k"]
        signals = engine.extract_signals("text", {}, regulatory=[])
        exposure = next(s for s in signals if s.dimension == "regulatory_exposure")
        self.assertEqual(exposure.level, 0, "model claimed exposure 3 with no accepted finding")
        self.assertIn("capped", exposure.evidence)

    def test_other_dimensions_are_not_capped(self):
        engine = ClaudeEngine(self.cfg)
        engine._interpret = lambda text, meta: {
            "issue_summary": "s", "regulatory_candidates": [], "department_scores": [],
            "dimensions": {d: {"level": 3, "evidence": "x"} for d in
                           ("financial_harm", "vulnerability", "regulatory_exposure",
                            "recurrence", "duration_unresolved", "escalation_signals",
                            "relationship_value")},
        }
        signals = engine.extract_signals("text", {}, regulatory=[])
        harm = next(s for s in signals if s.dimension == "financial_harm")
        self.assertEqual(harm.level, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
