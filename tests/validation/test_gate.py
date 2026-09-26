"""Synthetic checks for ehr_evidence_app.validation.gate.

Candidates are invented and checked against the bundle builder's invented
bundles; no real record is read.
"""

import contextlib
import io
import unittest

from ehr_evidence_app.validation import gate, structure

BUNDLE, DIGEST = structure.synthetic_bundle()
GOOD = structure.synthetic_list(BUNDLE, DIGEST)


def release(candidate, checks=None):
    """Release a candidate for the invented request and bundle."""
    return gate.release(candidate, "q-1", GOOD.case, GOOD.scope, BUNDLE,
                        DIGEST, checks=checks)


class GateTests(unittest.TestCase):
    """The gate releases checked text only, and fails closed."""

    def test_outcomes(self):
        """Released, withheld, and an operational error for a check that
        fails to run."""
        self.assertEqual(gate.run_synthetic(),
                         ("answer", "validation_rejection",
                          "operational_error"))

    def test_withheld_responses_release_nothing(self):
        """A rejection and an operational error carry no text or claims."""
        items = list(GOOD.statements)
        items[1] = items[1].model_copy(update={
            "wording": items[1].wording + ", caused by the admission"})
        rejected = release(GOOD.model_copy(update={
            "statements": tuple(items)}))
        crashed = release(GOOD, checks=[("crashing", gate.crashing)])
        for response, outcome, reasons in (
                (rejected, "validation_rejection", ("prohibited_wording",)),
                (crashed, "operational_error", ("validator_failed",))):
            self.assertEqual((response.outcome, response.reason_codes,
                              response.text, response.claims),
                             (outcome, reasons, None, ()))

    def test_released_text_is_rendered_from_the_candidate(self):
        """The released text is the candidate's statements, rendered."""
        response = release(GOOD)
        self.assertEqual(response.outcome, "answer")
        self.assertEqual(len(response.text.split("\n")),
                         len(GOOD.statements))

    def test_required_checks(self):
        """Each answer runs its own checks after structure."""
        names = [name for name, _ in gate.required_checks(GOOD)]
        self.assertEqual(names, ["support", "language"])
        clarification = GOOD.model_copy(update={"task": None})
        self.assertEqual([name for name, _ in
                          gate.required_checks(clarification)],
                         ["language"])
        explanation = GOOD.model_copy(update={
            "task": "prediction_explanation"})
        self.assertEqual([name for name, _ in
                          gate.required_checks(explanation)],
                         ["explanation", "language"])

    def test_command(self):
        """The default mode passes."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(gate.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
