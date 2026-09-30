"""Tests of what the interface calls things (interface.labels)."""

import unittest

from ehr_evidence_app.api import service
from ehr_evidence_app.evidence import schema
from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.generation import adapter
from ehr_evidence_app.interface import labels


class LabelTests(unittest.TestCase):
    """Outcomes, scopes, authors, the model's state, values, scores and
    references."""

    def test_every_outcome_and_fallback_has_words(self):
        """Every outcome has a title-case name and a tone the page knows,
        and every fallback a reason."""
        self.assertEqual(set(labels.OUTCOMES), set(vocab.OUTCOMES))
        self.assertEqual(labels.outcome("not_recorded"), "Not Recorded")
        self.assertEqual(set(labels.TONES), set(vocab.OUTCOMES))
        self.assertLessEqual(set(labels.TONES.values()),
                             {"good", "info", "neutral", "warn", "bad"})
        self.assertEqual(labels.tone("answer"), "good")
        self.assertEqual(labels.tone("unknown"), "neutral")
        self.assertEqual(set(labels.FALLBACKS), set(adapter.FALLBACKS))

    def test_title_case(self):
        """A name gains capitals but for short joining words inside it; a
        word holding a capital or a digit is kept as written."""
        for text, titled in (
                ("Aortocoronary bypass graft present",
                 "Aortocoronary Bypass Graft Present"),
                ("Heart failure due to hypertension",
                 "Heart Failure Due to Hypertension"),
                ("Non-ST elevation myocardial infarction",
                 "Non-ST Elevation Myocardial Infarction"),
                ("heparin sodium, porcine 5000 UNT/ML Injectable Solution",
                 "Heparin Sodium, Porcine 5000 UNT/ML Injectable Solution"),
                ("Chronic kidney disease, stage 3 (moderate)",
                 "Chronic Kidney Disease, Stage 3 (Moderate)"),
                ("Crohn's disease of large intestine",
                 "Crohn's Disease of Large Intestine"),
                ("pH abnormal", "pH Abnormal"),
                ("SYNTHETIC CONCEPT 1001", "SYNTHETIC CONCEPT 1001"),
                ("", "")):
            self.assertEqual(labels.title(text), titled)

    def test_scopes(self):
        """A scope is named as the menus name it."""
        self.assertEqual(labels.scope({"kind": "pre_target_history"}),
                         "Prior History")
        self.assertEqual(labels.scope({"kind": "selected_visit",
                                       "visit_number": 3}), "Visit 3")
        self.assertEqual(labels.scope({"kind": "case_visits"}), "All Visits")
        self.assertEqual(set(labels.SCOPES) | {"selected_visit"},
                         set(vocab.SCOPES))

    def test_authors(self):
        """Who wrote a response, and why the template did."""
        self.assertEqual(labels.author({"author": "model"}), "By the Model")
        self.assertEqual(labels.author({"author": "template",
                                        "fallback": "busy"}),
                         "By the Template · Model Busy")
        self.assertEqual(labels.author({"author": "template",
                                        "fallback": None}),
                         "By the Template")

    def test_model_states(self):
        """Every state of the model has a short name and a meaning."""
        for state in service.MODEL_STATES:
            name, meaning = labels.model(state)
            self.assertTrue(name and meaning)
        self.assertEqual(labels.model("ready"),
                         ("Model Ready", "The model writes each response."))

    def test_values_and_dates(self):
        """The evidence's codes read as words, and dates as dates."""
        self.assertEqual(labels.words("drug"), "Medication")
        self.assertEqual(labels.words("final_admission"), "Final Admission")
        self.assertEqual(labels.words("target_and_earlier"),
                         "Final Admission and Earlier")
        self.assertEqual(labels.words("verified"), "Verified")
        self.assertEqual(labels.words(None), "")
        self.assertEqual(labels.date("2100-01-02"), "2 Jan 2100")
        self.assertEqual(labels.date(None), "")
        self.assertEqual(labels.date("unknown"), "unknown")
        for status in vocab.COVERAGE_STATES:
            name, meaning = labels.coverage(status)
            self.assertTrue(name and meaning)

    def test_scores_are_never_percentages(self):
        """A score shows to three significant figures, never as a
        percentage, and its meaning is the schema's."""
        self.assertEqual(labels.score(0.8175744761936437), "0.818")
        self.assertEqual(labels.score(1.5), "1.5")
        self.assertNotIn("%", labels.score(0.5))
        self.assertEqual(labels.NOT_A_RISK, schema.Prediction.model_fields[
            "score_meaning"].default)
        self.assertIn("unsigned", labels.UNSIGNED)
        # The plain words about an explanation keep the rules the responses
        # keep: nothing hedged, ranked or counted in words.
        from ehr_evidence_app.validation import language
        # (The final admission's date in the target comes from the record,
        # as every date the page shows does; the rules are for typed ones.)
        for text in (labels.EXPLANATION_INTRO, labels.SCORE_PLAIN,
                     labels.TARGET.format(visit=""),
                     labels.RECORD_EXACT.format(records="2 records", visit=3),
                     labels.RECORD_ICD.format(records="1 record", visit=3),
                     labels.RECORD_MORE_SPECIFIC.format(
                         names="Hypertensive Heart Disease"),
                     labels.RECORD_ABSENT.format(visit=3),
                     labels.RECORD_UNKNOWN.format(visit=3)
                     ) + tuple(meaning for _, meaning in labels.GLOSSARY):
            self.assertEqual(language.findings(text, "explanation",
                                               "notice"), [], text)
        self.assertIn("not that it caused it", labels.UNSIGNED)

    def test_counts(self):
        """A count takes its noun in the singular or the plural."""
        self.assertEqual(labels.counted(1, "visit"), "1 Visit")
        self.assertEqual(labels.counted(26, "patient"), "26 Patients")
        self.assertEqual(labels.counted(0, "visit"), "0 Visits")

    def test_references_and_refusals(self):
        """References keep their first order, once each; a refusal says
        what it means."""
        self.assertEqual(labels.references({"claims": [
            {"refs": ["E2", "E1"]}, {"refs": ["E1", "P1"]}]}),
                         ["E2", "E1", "P1"])
        self.assertIn("isn't answering", labels.error("api_unreachable"))
        self.assertIn("other code", labels.error("other_code"))


if __name__ == "__main__":
    unittest.main()
