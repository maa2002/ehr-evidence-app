"""Synthetic checks for ehr_evidence_app.assessment.instr.

The reading without a bundle is held to what the application does with
the same questions on the invented case set; every question, archive and
dictionary is invented.
"""

import unittest

from ehr_evidence_app.api import service
from ehr_evidence_app.assessment import instr
from ehr_evidence_app.evidence import bundles, contracts
from ehr_evidence_app.interface import labels
from ehr_evidence_app.qa import answer, interpret, record_interpret, suite
from tests.qa import test_record_edges

VOCAB = suite.synthetic_vocabulary()
SOURCE = service.invented_source(VOCAB)
LIMITS = contracts.ExecutionLimits(**bundles.SYNTHETIC_LIMITS)
EVERY = contracts.ScopeRef(kind="case_visits")
HELP = tuple(example for _, rows in labels.HELP_SECTIONS
             for _, examples in rows for example in examples)


def deployed(question):
    """Return (outcome, task) of the application's response to a question
    about the invented case in all its visits."""
    result = answer.answer(question, SOURCE.label, 1, EVERY,
                           SOURCE.bundle_set, VOCAB, "i-1", LIMITS,
                           records=SOURCE.records)
    return (result.response.outcome,
            result.request.task if result.request is not None else None)


def names_a_scope(question):
    """Return True when a question names a scope, whose binding only a
    bundle can judge."""
    text = answer.well_formed(question)
    found = record_interpret.interpret(text, SOURCE.records.terms)
    return bool(interpret.interpret(text).mentions or found.mentions)


class ReadingTests(unittest.TestCase):
    """The reading without a bundle, against the application."""

    def test_default_mode(self):
        """The invented archive's questions are read as expected, but the
        one naming a time the rules do not take as a scope."""
        _, problems = instr.run_synthetic()
        self.assertEqual(problems, [])

    def test_reading_is_the_applications(self):
        """For every edge question and help example that names no scope
        and is within the length limit, the reading is what the
        application does: an answer of the task read, or the same
        clarification or abstention."""
        mismatches = []
        for question in dict.fromkeys(test_record_edges.QUESTIONS + HELP):
            if len(question) > LIMITS.max_question_chars or names_a_scope(
                    question):
                continue
            found = instr.reading(question, VOCAB, SOURCE.records.terms)
            outcome, task = deployed(question)
            if found.startswith("record:"):
                agrees = (task == found.split(":")[1]
                          and outcome in ("answer", "not_recorded"))
            elif found == "list":
                agrees = task == "condition_list" and outcome in (
                    "answer", "not_recorded", "abstention")
            elif found == "presence":
                agrees = task == "condition_presence" and outcome in (
                    "answer", "not_recorded")
            else:
                agrees = outcome == found.split(":")[0]
            if not agrees:
                mismatches.append((question, found, outcome, task))
        self.assertEqual(mismatches, [])

    def test_expected_readings(self):
        """Each family's expected reading: a condition list, nothing
        answered for a ranking, and the kind of record of the others."""
        self.assertTrue(instr.met("diagnoses_list", "list"))
        self.assertFalse(instr.met("diagnoses_list", "presence"))
        self.assertTrue(instr.met("diagnoses_ranking",
                                  "clarification:record_unknown"))
        self.assertFalse(instr.met("diagnoses_ranking", "list"))
        self.assertFalse(instr.met("diagnoses_ranking",
                                   "record:record_count:condition"))
        self.assertTrue(instr.met("labevents", "record:record_values:lab"))
        self.assertFalse(instr.met("labevents",
                                   "record:record_values:vital"))
        self.assertFalse(instr.met("labevents", "clarification:x"))
        self.assertTrue(instr.met("transfers",
                                  "record:record_count:icu_stay"))
        self.assertTrue(instr.met("admission_info",
                                  "record:person_details:person"))
        self.assertEqual(set(instr.EXPECTED) - {"diagnoses_list",
                                                "diagnoses_ranking"},
                         set(instr.heldout.FAMILIES) - {
                             "diagnoses_list", "diagnoses_ranking"})

    def test_final_read_needs_the_freeze(self):
        """The final read reads nothing before the freeze."""
        with self.assertRaisesRegex(instr.InstrError, "not_frozen"):
            instr.run_final()


if __name__ == "__main__":
    unittest.main()
