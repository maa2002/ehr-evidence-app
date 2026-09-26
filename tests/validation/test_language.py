"""Synthetic checks for ehr_evidence_app.validation.language.

Every wording is invented or one of the application's own templates and
approved notices.
"""

import contextlib
import io
import unittest

from ehr_evidence_app.qa import templates
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import language, structure


def codes(wording):
    """Return the reason codes a wording gets."""
    return tuple(sorted({code for code, _ in language.findings(wording)}))


class LanguageTests(unittest.TestCase):
    """Wording rules read the writer's words in every outcome."""

    def test_synthetic_wordings(self):
        """Each prohibited class is caught; allowed wordings pass."""
        correct, total = language.run_synthetic()
        self.assertEqual(correct, total)

    def test_own_wordings_pass(self):
        """Every template wording and approved notice passes."""
        wordings = [entry for value in templates.WORDINGS.values()
                    for entry in ((value,) if isinstance(value, str)
                                  else value)]
        wordings += [notice.text for notice in candidates.NOTICES.values()]
        for wording in wordings:
            self.assertEqual(codes(wording), (), wording)

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
