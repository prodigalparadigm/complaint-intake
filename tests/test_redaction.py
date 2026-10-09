"""The PII boundary."""
from __future__ import annotations

import unittest

from _helpers import TempStore  # noqa: E402

from mhb import config, corpus, leak, store  # noqa: E402
from mhb.agents.redaction import RedactionAgent, aba, luhn  # noqa: E402
from mhb.orchestrator import Pipeline  # noqa: E402

SAMPLE = (
    "My name is Mr. Harold Ferris. My checking account 4471902233 was charged. "
    "Card 4111111111111111 was used without permission. Reach me at (503) 555-0147 "
    "or harold.ferris@example.com. SSN 512-44-9087, routing 121000248. "
    "I live at 1420 Pearl Street. The amount was $1,249.00 on 03/14/1958."
)


class TestValidators(unittest.TestCase):
    def test_luhn_accepts_valid_pan(self):
        self.assertTrue(luhn("4111111111111111"))

    def test_luhn_rejects_invalid_pan(self):
        self.assertFalse(luhn("4111111111111112"))

    def test_luhn_rejects_wrong_length(self):
        self.assertFalse(luhn("411111111111"))

    def test_aba_accepts_valid_routing(self):
        self.assertTrue(aba("121000248"))

    def test_aba_rejects_bad_checksum(self):
        self.assertFalse(aba("121000249"))


class TestRedaction(unittest.TestCase):
    def setUp(self):
        self.ts = TempStore().__enter__()
        self.agent = RedactionAgent(config.load())

    def tearDown(self):
        self.ts.__exit__()

    def test_all_identifier_classes_removed(self):
        r = self.agent.redact(SAMPLE, "", "MHB-TEST")
        for raw in ("4471902233", "4111111111111111", "(503) 555-0147",
                    "harold.ferris@example.com", "512-44-9087", "121000248",
                    "1420 Pearl Street", "Harold Ferris"):
            self.assertNotIn(raw, r.redacted_text, f"{raw!r} survived redaction")

    def test_severity_signal_is_preserved(self):
        """Dollar amounts must survive — they carry the harm signal and are
        not identifying on their own."""
        r = self.agent.redact(SAMPLE, "", "MHB-TEST")
        self.assertIn("$1,249.00", r.redacted_text)

    def test_card_wins_over_bare_account_rule(self):
        r = self.agent.redact("Card 4111111111111111 was used.", "", "MHB-TEST")
        self.assertIn("[CARD_1]", r.redacted_text)
        self.assertNotIn("[ACCT_", r.redacted_text)

    def test_same_value_gets_same_placeholder(self):
        r = self.agent.redact(
            "Account 4471902233 and again account 4471902233.", "", "MHB-TEST")
        self.assertEqual(r.redacted_text.count("[ACCT_1]"), 2)
        self.assertNotIn("[ACCT_2]", r.redacted_text)

    def test_vault_roundtrip(self):
        r = self.agent.redact(SAMPLE, "", "MHB-TEST")
        restored = store.rehydrate(r.redacted_text, r.vault_id)
        self.assertIn("4471902233", restored)
        self.assertIn("harold.ferris@example.com", restored)

    def test_public_view_excludes_original_values(self):
        r = self.agent.redact(SAMPLE, "", "MHB-TEST")
        import json
        blob = json.dumps(r.public())
        self.assertNotIn("4471902233", blob)
        self.assertNotIn("harold.ferris@example.com", blob)

    def test_spelled_out_digits_raise_a_residual_risk_note(self):
        r = self.agent.redact(
            "My account is four four seven one and I want it fixed.", "", "MHB-TEST")
        self.assertTrue(any("spelled out" in n for n in r.residual_risk_notes),
                        f"expected a spelled-digits note, got {r.residual_risk_notes}")

    def test_untitled_name_raises_a_residual_risk_note(self):
        r = self.agent.redact("I spoke to Gregory Tannenbaum at the desk.", "", "MHB-TEST")
        self.assertTrue(any("not covered by a redaction rule" in n
                            for n in r.residual_risk_notes))

    def test_bank_name_does_not_trigger_a_false_name_note(self):
        r = self.agent.redact("I bank with Meridian Harbor and use Online Banking.",
                              "", "MHB-TEST")
        self.assertEqual(r.residual_risk_notes, [])

    def test_username_rule_requires_a_separator(self):
        """Regression: an optional separator made 'suspicious login from a
        state' vault the word 'from' as a username."""
        r = self.agent.redact("There was a suspicious login from a state I have never been to.",
                              "", "MHB-TEST")
        self.assertNotIn("[USERNAME_1]", r.redacted_text)
        self.assertIn("from", r.redacted_text)


class TestEndToEndLeakage(unittest.TestCase):
    def test_case_files_contain_no_vault_values(self):
        """The load-bearing claim: a case file physically does not contain an
        account number. Run across a slice of the real corpus."""
        with TempStore():
            cfg = config.load()
            pipe = Pipeline(cfg, "rules")
            for row in corpus.load(30):
                pipe.run(corpus.to_raw(row))
            vault = store.all_vault_values()
            self.assertGreater(len(vault), 0, "vault should not be empty")
            offenders = [
                c["case_id"] for c in store.all_cases() if leak.scan_case(c, vault)
            ]
            self.assertEqual(offenders, [], f"PII found in case files: {offenders}")

    def test_injected_identifiers_are_removed_from_redacted_text(self):
        with TempStore():
            cfg = config.load()
            pipe = Pipeline(cfg, "rules")
            total = missed = 0
            for row in corpus.load(40):
                case = pipe.run(corpus.to_raw(row), persist=False)
                for v in row["gold"]["pii_values"]:
                    total += 1
                    if str(v) in case.redacted_text:
                        missed += 1
            self.assertGreater(total, 0)
            self.assertEqual(missed, 0, f"{missed}/{total} injected identifiers survived")


if __name__ == "__main__":
    unittest.main(verbosity=2)
