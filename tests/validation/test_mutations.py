"""Synthetic checks for ehr_evidence_app.validation.mutations.

The samples are built from the invented fixtures; the real mode is only
checked for refusing a held-out case set before anything is read.
"""

import contextlib
import io
import unittest

from ehr_evidence_app.validation import mutations

SAMPLES = mutations.synthetic_samples()
COUNTS = mutations.run(SAMPLES)


class MutationTests(unittest.TestCase):
    """Every mutant is withheld with its class's reason; every original
    and control passes."""

    def test_synthetic_run(self):
        """The invented samples pass the suite."""
        self.assertTrue(mutations.passed(COUNTS))
        self.assertEqual(COUNTS["originals"]["checked"], len(SAMPLES))

    def test_every_item_is_exercised(self):
        """Each work item has error classes in the run."""
        classes = set(COUNTS["classes"])
        for error in ("bundle_of_another_case", "incomplete_coverage",
                      "malformed_document", "absent_reference",
                      "another_patient", "omitted_diagnosis",
                      "unsupported_positive",
                      "negative_without_complete_coverage",
                      "altered_label", "reordered_drivers",
                      "zero_score_driver_described", "causal_wording",
                      "percentage_wording", "date_wording",
                      "check_fails_to_run", "control_non_causation"):
            self.assertIn(error, classes)

    def test_model_mistakes_are_exercised(self):
        """Version 2: the mistakes a language model can make and a
        template cannot are in the run."""
        classes = set(COUNTS["classes"])
        for error in ("refusal_of_answerable_request",
                      "answer_about_another_condition",
                      "empty_list_for_recorded_scope", "missing_notice",
                      "extra_notice", "duplicate_notice",
                      "statements_out_of_order",
                      "history_under_another_driver",
                      "fact_of_another_record", "name_of_another_record",
                      "hedged_wording", "ranking_wording",
                      "count_in_words", "interpretive_wording",
                      "association_wording", "course_wording",
                      "certainty_wording", "recency_wording"):
            self.assertIn(error, classes)
        self.assertEqual(mutations.SUITE_VERSION, "validation-mutations-v4")

    def test_selection_and_polarity_are_exercised(self):
        """Version 3: the mistakes of driver selection and of a
        statement's polarity are in the run."""
        classes = set(COUNTS["classes"])
        for error in ("unselected_driver_described",
                      "omitted_drivers_miscounted",
                      "omitted_drivers_unstated", "negated_fact",
                      "absence_worded_as_record", "score_wrong_as_shown",
                      "attribution_score_wrong_as_shown",
                      "control_driver_history_left_out",
                      "control_driver_context_left_out",
                      "control_score_as_shown",
                      "control_attribution_score_as_shown"):
            self.assertIn(error, classes)

    def test_samples_cover_every_outcome(self):
        """Answers, negatives, refusals, clarifications and an
        explanation are sampled."""
        outcomes = {sample.candidate.outcome for sample in SAMPLES}
        self.assertEqual(outcomes, {"answer", "not_recorded", "abstention",
                                    "clarification", "explanation"})

    def test_a_missed_mutant_fails_the_run(self):
        """A class with an undetected mutant fails the suite."""
        counts = {"originals": {"checked": 1, "passed": 1},
                  "classes": {"wrong_count": {"mutants": 2,
                                              "detected": 1}}}
        self.assertFalse(mutations.passed(counts))

    def test_held_out_case_sets_are_refused(self):
        """The evaluation case set is never used."""
        with self.assertRaisesRegex(mutations.MutationError,
                                    "case_set_held_out"):
            mutations.real_samples("eval-100-v1")

    def test_command(self):
        """The default mode passes; the real mode needs a case set."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(mutations.main([]), 0)
            self.assertEqual(mutations.main(["--operator-real"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
