"""Synthetic checks for ehr_evidence_app.validation.language.

Every wording is invented or one of the application's own templates and
approved notices.
"""

import contextlib
import io
import unittest

from ehr_evidence_app.qa import templates
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import explanation, language, structure


def codes(wording, outcome=None):
    """Return the reason codes a wording gets in a statement of an
    outcome (under every rule when it is None)."""
    return tuple(sorted({code for code, _ in language.findings(
        wording, outcome)}))


class LanguageTests(unittest.TestCase):
    """Wording rules read the writer's words in every outcome."""

    def test_synthetic_wordings(self):
        """Each prohibited class is caught; allowed wordings pass."""
        correct, total = language.run_synthetic()
        self.assertEqual(correct, total)

    def test_own_wordings_pass(self):
        """Every template wording and approved notice passes in the
        outcomes it is used in."""
        cases = [(entry, "answer")
                 for value in templates.WORDINGS.values()
                 for entry in ((value,) if isinstance(value, str)
                               else value)]
        cases += [(wording, "explanation")
                  for wording in explanation.WORDINGS.values()]
        cases += [(notice.text, outcome)
                  for notice in candidates.NOTICES.values()
                  for outcome in notice.outcomes]
        for wording, outcome in cases:
            self.assertEqual(codes(wording, outcome), (), wording)

    def test_explanation_rules(self):
        """The score read as a risk, or the prediction as prospective,
        is prohibited in explanations only."""
        wording = "The model gives {label} a risk of {score}."
        self.assertEqual(codes(wording, "explanation"),
                         ("prohibited_wording",))
        self.assertEqual(codes(wording, "abstention"), ())
        self.assertEqual(codes("{label} was forecast in advance.",
                               "explanation"), ("prohibited_wording",))
        self.assertEqual(codes("The score is not a clinical risk.",
                               "explanation"), ())

    def test_slots_are_not_read(self):
        """A fact in a slot never trips a rule; the same words typed into
        the wording do."""
        self.assertEqual(codes("{condition} is recorded."), ())
        self.assertEqual(codes("Pneumonia caused by a virus is recorded."),
                         ("prohibited_wording",))

    def test_negation(self):
        """A negation shortly before a causal or diagnostic word passes."""
        self.assertEqual(codes("Attribution is not a cause."), ())
        self.assertEqual(codes("Attribution is a cause."),
                         ("prohibited_wording",))
        self.assertEqual(codes("It does not diagnose."), ())
        self.assertEqual(codes("The model diagnosed the patient."),
                         ("prohibited_wording",))

    def test_rules_by_class(self):
        """Each class of the rules has its own finding."""
        for wording, rule in (
                ("{condition} led to the admission.", "causal"),
                ("{condition} is 40 percent of it.", "percentage"),
                ("{condition} is solely responsible.", "exclusive"),
                ("{condition} is severe.", "status"),
                ("{condition} needs treatment.", "advice"),
                ("{condition} proves it.", "certainty"),
                ("{condition} is suggestive of sepsis.", "diagnosis"),
                ("{condition} was recorded yesterday.", "dates")):
            self.assertIn(rule, [found for _, found in
                                 language.findings(wording)], wording)

    def test_candidates(self):
        """A candidate breaking a rule gets its reason code."""
        bundle, digest = structure.synthetic_bundle()
        good = structure.synthetic_list(bundle, digest)
        self.assertEqual(language.check(good), ())
        item = good.statements[1].model_copy(update={
            "wording": "{condition}: {count} in {visits}, caused by the "
                       "admission"})
        items = list(good.statements)
        items[1] = item
        bad = good.model_copy(update={"statements": tuple(items)})
        self.assertEqual(language.check(bad), ("prohibited_wording",))

    def test_command(self):
        """The default mode passes."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(language.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
