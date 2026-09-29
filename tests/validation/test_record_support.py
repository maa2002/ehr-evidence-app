"""Synthetic checks for ehr_evidence_app.validation.record_support.

Answers about the invented record are changed one fact at a time; every
change must be found. No patient record is read.
"""

import unittest

from ehr_evidence_app.api import service as services
from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.qa import answer, record_interpret
from ehr_evidence_app.validation import record_support

ALL = contracts.ScopeRef(kind="case_visits")


class RecordSupportTests(unittest.TestCase):
    """Supported answers pass; changed ones do not."""

    @classmethod
    def setUpClass(cls):
        """Hold the invented source and its record bundle."""
        cls.service = services.Service.invented()
        cls.source = cls.service.sources["synthetic-1-v1"]
        cls.bundle, _ = cls.source.records.records.bundle(1, ALL)

    def answered(self, question):
        """Return (candidate, check) of a question's released answer."""
        result = answer.result_of(
            question, "synthetic-1-v1", 1, ALL, self.source.bundle_set,
            self.service.vocabulary, "q-1", self.service.limits,
            records=self.source.records)
        self.assertEqual(result.response.outcome, "answer", question)
        reading = record_interpret.interpret(question,
                                             self.source.records.terms)
        resolution = (self.source.records.terms.resolve(reading.kind,
                                                        reading.phrase)
                      if reading.phrase else None)
        return result.candidate, record_support.RecordCheck(
            self.source.records.terms, resolution, reading)

    def changed(self, candidate, index, **slots):
        """Return the candidate with one statement's slots changed."""
        statements = list(candidate.statements)
        statements[index] = statements[index].model_copy(update={
            "slots": statements[index].slots.model_copy(update=slots)})
        return candidate.model_copy(update={"statements": tuple(statements)})

    def assertUnsupported(self, candidate, check):
        """Assert that a changed candidate is not supported."""
        self.assertEqual(record_support.check(candidate, self.bundle, check),
                         ("claim_unsupported",))

    def test_supported_answers_pass(self):
        """Each released answer is supported."""
        for question in ("What labs were done?", "Was potassium measured?",
                         "When was potassium first measured?",
                         "How many potassium results are there?",
                         "What was the last potassium?",
                         "What were all the potassium values?",
                         "Were any labs abnormal?", "How many visits?",
                         "Did the patient die?", "Was she in the ICU?"):
            candidate, check = self.answered(question)
            self.assertEqual(record_support.check(candidate, self.bundle,
                                                  check), (), question)

    def test_a_list_that_miscounts_or_leaves_an_item_out(self):
        """A list's counts and items are the scope's."""
        candidate, check = self.answered("What labs were done?")
        summary = candidate.statements[0]
        self.assertUnsupported(self.changed(
            candidate, 0, records=summary.slots.records + 1), check)
        self.assertUnsupported(candidate.model_copy(update={
            "statements": candidate.statements[:1]
            + candidate.statements[2:]}), check)

    def test_a_value_that_is_not_its_selector(self):
        """The latest value must be the latest record."""
        candidate, check = self.answered("What was the last potassium?")
        first, _ = self.answered("What was the first potassium?")
        other = first.statements[0].slots.value
        self.assertUnsupported(self.changed(candidate, 0, value=other,
                                            when=other), check)
        self.assertUnsupported(self.changed(candidate, 0,
                                            selector="highest"), check)

    def test_a_time_that_is_not_the_first(self):
        """The first record must be the earliest."""
        candidate, check = self.answered(
            "When was potassium first measured?")
        index = next(index for index, statement in
                     enumerate(candidate.statements)
                     if statement.kind == "record_time")
        self.assertUnsupported(self.changed(candidate, index,
                                            selector="latest"), check)

    def test_a_count_that_is_wrong(self):
        """A count is the number of matching records."""
        candidate, check = self.answered(
            "How many potassium results are there?")
        self.assertUnsupported(self.changed(candidate, 0, count=5), check)

    def test_a_visit_left_out(self):
        """Every visit of the scope is given."""
        candidate, check = self.answered("How many visits?")
        self.assertUnsupported(candidate.model_copy(update={
            "statements": candidate.statements[:-2]
            + candidate.statements[-1:]}), check)

    def test_a_disclosure_that_does_not_follow(self):
        """unmapped_records_in_scope is stated exactly when it applies."""
        candidate, check = self.answered("What labs were done?")
        self.assertUnsupported(candidate.model_copy(update={
            "reason_codes": ("unmapped_records_in_scope",)}), check)

    def test_without_the_record_check_nothing_is_supported(self):
        """A record answer cannot pass without what it is checked
        against."""
        candidate, _ = self.answered("What labs were done?")
        self.assertEqual(record_support.check(candidate, self.bundle, None),
                         ("claim_unsupported",))

    def test_synthetic_mode(self):
        """The module's own invented check passes."""
        passed, detected, total = record_support.run_synthetic()
        self.assertTrue(passed)
        self.assertEqual(detected, total)


if __name__ == "__main__":
    unittest.main()
