"""Tests of what the interface calls things (interface.labels)."""

import unittest

from ehr_evidence_app.evidence import schema
from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.generation import adapter
from ehr_evidence_app.interface import labels


class LabelTests(unittest.TestCase):
    """Outcomes, scopes, authors, scores and escaping."""

    def test_every_outcome_and_fallback_has_words(self):
        """Every outcome has a name and a colour, and every fallback a
        reason."""
        self.assertEqual(set(labels.OUTCOMES), set(vocab.OUTCOMES))
        self.assertEqual(set(labels.TONES), set(vocab.OUTCOMES))
        self.assertEqual(set(labels.FALLBACKS), set(adapter.FALLBACKS))

    def test_scopes(self):
        """A scope is named as a sentence and as a menu names it."""
        for scope, sentence, option in (
                ({"kind": "pre_target_history"}, "the pre-target history",
                 "Pre-target history"),
                ({"kind": "selected_visit", "visit_number": 3}, "visit 3",
                 "Visit 3"),
                ({"kind": "case_visits"}, "all case visits",
                 "All case visits")):
            self.assertEqual(labels.scope(scope), sentence)
            self.assertEqual(labels.scope_option(scope), option)

    def test_authors(self):
        """Who wrote a response, and why the template did."""
        self.assertEqual(labels.author({"author": "model"}),
                         "written by the model")
        self.assertEqual(labels.author({"author": "template",
                                        "fallback": "gate_rejected"}),
                         "written by the template: validation withheld the "
                         "model's reply")
        self.assertEqual(labels.author({"author": "template",
                                        "fallback": None}),
                         "written by the template")

    def test_scores_are_never_percentages(self):
        """A score shows to three significant figures, never as a
        percentage, and its meaning is the schema's."""
        self.assertEqual(labels.score(0.8175744761936437), "0.818")
        self.assertEqual(labels.score(1.5), "1.5")
        self.assertNotIn("%", labels.score(0.5))
        self.assertEqual(labels.NOT_A_RISK, schema.Prediction.model_fields[
            "score_meaning"].default)
        self.assertIn("unsigned", labels.UNSIGNED)
        self.assertIn("not that it caused it", labels.UNSIGNED)

    def test_counts(self):
        """A count takes its noun in the singular or the plural."""
        self.assertEqual(labels.counted(1, "case"), "1 case")
        self.assertEqual(labels.counted(26, "case"), "26 cases")
        self.assertEqual(labels.counted(0, "visit"), "0 visits")

    def test_references_and_escaping(self):
        """References keep their first order, once each; Markdown's
        characters show as written."""
        self.assertEqual(labels.references({"claims": [
            {"refs": ["E2", "E1"]}, {"refs": ["E1", "P1"]}]}),
                         ["E2", "E1", "P1"])
        self.assertEqual(labels.escaped("*a* [b](c) 1.5"),
                         r"\*a\* \[b\]\(c\) 1\.5")
        self.assertIn("not answering", labels.error("api_unreachable"))
        self.assertIn("other code", labels.error("other_code"))


if __name__ == "__main__":
    unittest.main()
