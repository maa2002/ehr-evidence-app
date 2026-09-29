"""Synthetic checks for answers about the whole record.

Every case, record and dictionary here is invented (the invented case set
of the API's default mode): questions go through qa.answer with the
record context, and every answer leaves through the release gate.
"""

import unittest

from ehr_evidence_app.api import service as services
from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.qa import answer, record_answer
from ehr_evidence_app.validation import record_support

ALL = contracts.ScopeRef(kind="case_visits")
HISTORY = contracts.ScopeRef(kind="pre_target_history")


class RecordAnswerTests(unittest.TestCase):
    """Answers about the invented record."""

    @classmethod
    def setUpClass(cls):
        """Hold the invented service's source."""
        cls.service = services.Service.invented()
        cls.source = cls.service.sources["synthetic-1-v1"]

    def ask(self, question, scope=ALL, records=True):
        """Return the Result of a question about invented case 1."""
        return answer.result_of(
            question, "synthetic-1-v1", 1, scope, self.source.bundle_set,
            self.service.vocabulary, "q-1", self.service.limits,
            records=self.source.records if records else None)

    def assertAnswered(self, question, outcome, task, scope=ALL):
        """Assert a question's outcome and task; return its response."""
        response = self.ask(question, scope).response
        self.assertEqual((response.outcome, response.task), (outcome, task),
                         question)
        return response

    def test_every_task_is_answered_through_the_gate(self):
        """Each record task gets its answer, its claims citing the record
        bundle of the scope."""
        for question, task in (
                ("What labs were done?", "record_list"),
                ("Was potassium measured?", "record_presence"),
                ("When was potassium first measured?", "record_timing"),
                ("How many potassium results are there?", "record_count"),
                ("What was the last potassium?", "record_values"),
                ("Were any labs abnormal?", "record_abnormal"),
                ("How many visits?", "visit_details"),
                ("How old was the patient?", "person_details")):
            response = self.assertAnswered(question, "answer", task)
            self.assertEqual(response.bundle.bundle_id, "rec-c01-all")
            refs = {ref for claim in response.claims for ref in claim.refs}
            self.assertTrue(refs, question)
            self.assertTrue(all(ref[0] in "RVD" for ref in refs), question)

    def test_values_follow_their_selector(self):
        """The latest, first, highest and lowest values are those."""
        latest = self.ask("What was the last potassium?").response.text
        first = self.ask("What was the first potassium?").response.text
        self.assertIn("4.1 mEq/L", latest)
        self.assertIn("5.6 mEq/L", first)
        self.assertIn("flagged abnormal", first)
        highest = self.ask("What was the highest heart rate?").response
        self.assertIn("96 bpm", highest.text)

    def test_not_recorded_within_the_scope(self):
        """An item without a record in the scope is not recorded there,
        never absent from the patient."""
        response = self.assertAnswered("Was a chest x-ray done?",
                                       "not_recorded", "record_presence",
                                       HISTORY)
        self.assertIn("not a statement of clinical absence", response.text)
        response = self.assertAnswered("What vital signs were charted?",
                                       "not_recorded", "record_list",
                                       HISTORY)
        self.assertIn("ICU stays only", response.text)

    def test_condition_questions_are_unchanged(self):
        """A condition question gets the same response with or without
        the record context."""
        for question in ("Which conditions are recorded?",
                         "Is synthetic concept 1001 recorded?",
                         "Is florbnitis recorded?",
                         "How should heart failure be treated?",
                         "Will she survive?"):
            with_records = self.ask(question).response
            without = self.ask(question, records=False).response
            self.assertEqual(
                (with_records.outcome, with_records.reason_codes,
                 with_records.text),
                (without.outcome, without.reason_codes, without.text),
                question)

    def test_other_data_refused_without_the_record(self):
        """Without the record context, a record question keeps the
        condition rules' refusal."""
        response = self.ask("What medications were given?",
                            records=False).response
        self.assertEqual(response.outcome, "abstention")

    def test_clarifications(self):
        """Filters, unknown items and other scopes ask again."""
        response = self.ask("Was potassium above 5?").response
        self.assertEqual((response.outcome, response.reason_codes),
                         ("clarification", ("task_ambiguous",)))
        response = self.ask("Was potassium measured in visit 2?",
                            HISTORY).response
        self.assertEqual(response.outcome, "clarification")
        self.assertIn("scope_ambiguous", response.reason_codes)

    def test_a_record_answer_is_written_by_the_template(self):
        """Record answers are the templates' own, never a fallback."""
        found = self.ask("What labs were done?")
        self.assertEqual((found.written.author, found.written.fallback),
                         ("template", None))

    def test_a_changed_answer_is_withheld(self):
        """A candidate that leaves out a record is a validation
        rejection."""
        result = self.ask("What labs were done?")
        bundle, digest = self.source.records.records.bundle(1, ALL)
        statements = [statement for statement in result.candidate.statements
                      if statement.kind != "record_list_item"]
        broken = result.candidate.model_copy(update={
            "statements": tuple(statements[:1]) + tuple(statements[1:])})
        from ehr_evidence_app.qa import record_interpret
        from ehr_evidence_app.validation import gate
        reading = record_interpret.interpret("What labs were done?",
                                             self.source.records.terms)
        response = gate.release(
            broken, "q-1", broken.case, ALL, bundle, digest,
            self.service.vocabulary, record=record_support.RecordCheck(
                self.source.records.terms, None, reading))
        self.assertEqual(response.outcome, "validation_rejection")
        self.assertIsNone(response.text)

    def test_record_set_verifies_its_bundles(self):
        """A bundle whose bytes are not its manifest's is refused."""
        entry = {"bundle_id": "rec-x", "path": "record/rec-x.json",
                 "case_number": 1, "scope": {"kind": "case_visits"},
                 "size_bytes": 3, "sha256": "0" * 64}
        records = record_answer.RecordSet("synthetic-1-v1", "unused",
                                          {"files": [entry]},
                                          reader=lambda item: b"abc")
        with self.assertRaises(record_answer.RecordAnswerError):
            records.bundle(1, ALL)
        self.assertIsNone(records.bundle(2, ALL))


if __name__ == "__main__":
    unittest.main()
