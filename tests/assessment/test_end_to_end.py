"""Synthetic checks for ehr_evidence_app.assessment.end_to_end.

The written test's invented questions about the invented case are
assessed stage by stage; no model runs and no real record is read.
"""

import dataclasses
import unittest

from ehr_evidence_app.assessment import end_to_end
from ehr_evidence_app.qa import written

ROWS, INPUTS, VOCAB, LIMITS = end_to_end.synthetic_inputs()


def row(question, intent, concept=None, scope="all"):
    """Return an invented question about the invented case."""
    found, _ = written.parse_questions(written.csv_bytes((
        written.COLUMNS,
        ("x1", "synthetic-1-v1", "1", scope, question, intent,
         "" if concept is None else str(concept)))), ("synthetic-1-v1",))
    return found[0]


def assessed(item, complete=True):
    """Return the stages of one invented question."""
    oracle, cache, _ = INPUTS["synthetic-1-v1"]
    return end_to_end.assess_row(item, oracle, cache, VOCAB, LIMITS, "x1",
                                 complete=complete)


class StageTests(unittest.TestCase):
    """Each stage is scored only when the one before it succeeded."""

    def test_default_mode(self):
        """The written test's invented questions end at the stages it
        expects."""
        _, problems = end_to_end.run_synthetic()
        self.assertEqual(problems, [])

    def test_unrelated_condition_stops_at_resolution(self):
        """A presence check read about another condition than the one
        meant stops at resolution, with nothing retrieved or delivered."""
        found = assessed(row("Is synthetic concept 1002 recorded?",
                             "presence", 1001))
        self.assertEqual((found.interpretation, found.resolution,
                          found.retrieval, found.delivery),
                         ("right", "unrelated", None, None))

    def test_sources_not_complete(self):
        """When the sources are not complete the question cannot be
        delivered as the reference has it, whatever the runtime does."""
        found = assessed(row("List the recorded conditions.", "list"),
                         complete=False)
        self.assertEqual((found.interpretation, found.retrieval,
                          found.delivery), ("right", "unanswerable", None))

    def test_refusal_answered(self):
        """A request to refuse that gets an answer is read wrongly, and
        no later stage is scored."""
        found = assessed(row("List the recorded conditions.", "refuse"))
        self.assertEqual((found.interpretation, found.resolution,
                          found.delivery), ("answered", None, None))

    def test_case_not_admitted_left_out(self):
        """A question about a case that is not admitted is left out."""
        oracle, cache, _ = INPUTS["synthetic-1-v1"]
        item = dataclasses.replace(row("List the recorded conditions.",
                                       "list"), case_number=9)
        self.assertIsNone(end_to_end.assess_row(item, oracle, cache, VOCAB,
                                                LIMITS, "x1"))

    def test_tally_holds_counts(self):
        """The tally counts verdicts by intent and stage, and writers by
        delivery."""
        found, left_out, writers = end_to_end.tally([
            end_to_end.Assessed("list", "right", None, "complete",
                                "correct", "model"),
            end_to_end.Assessed("list", "declined"), None])
        self.assertEqual(found["list"]["interpretation"],
                         {"declined": 1, "right": 1})
        self.assertEqual((left_out, writers), (1, {("model", "correct"): 1}))


if __name__ == "__main__":
    unittest.main()
