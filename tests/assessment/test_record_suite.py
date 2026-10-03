"""Synthetic checks for ehr_evidence_app.assessment.record_suite.

The generated questions about the invented case's whole record are
assessed end to end; no model runs and no real record is read.
"""

import collections
import unittest

from ehr_evidence_app.assessment import record_references as refs
from ehr_evidence_app.assessment import record_suite
from ehr_evidence_app.evidence import contracts

LABEL, INPUTS, SCOPES_OF, NAMER, LIMITS = record_suite.synthetic_inputs()
CASE = INPUTS.cases[1]
EVERY = contracts.ScopeRef(kind="case_visits")


def row_of(kind, **fields):
    """Return the invented case's first record of a kind, with fields."""
    return next(row for row in CASE.rows if row.kind == kind and all(
        getattr(row, name) == value for name, value in fields.items()))


class NamerTests(unittest.TestCase):
    """Each kind's item is named by its records' name."""

    def test_names_and_identifiers(self):
        """A result by its label, a vital sign by its group, a condition
        by its concept, a medication by its one ingredient, a test and a
        unit by the words of their names."""
        self.assertEqual(NAMER(row_of("lab")), (
            "SYNTHETIC POTASSIUM", {"codes": frozenset(("50971",))}))
        self.assertEqual(NAMER(row_of("vital")), (
            "heart rate", {"groups": frozenset(("heart_rate",))}))
        self.assertEqual(NAMER(row_of("condition", concept=1002)), (
            "SYNTHETIC CONCEPT 1002", {"concepts": frozenset((1002,))}))
        self.assertEqual(NAMER(row_of("medication", concept=3001)), (
            "SYNTHETIC CONCEPT 3001", {
                "concepts": frozenset((7001,)),
                "written": (("synthetic", "concept", "3001"),)}))
        self.assertEqual(NAMER(row_of("microbiology"))[1], {
            "words": (("synthetic", "culture"),)})
        self.assertEqual(NAMER(row_of("transfer"))[1], {
            "words": (("synthetic", "unit"),)})
        self.assertEqual(NAMER(row_of("icu_procedure"))[1], {
            "words": (("synthetic", "chest", "x", "ray"),)})

    def test_a_label_two_items_share_names_both(self):
        """The item meant by a label is every item so labelled."""
        namer = record_suite.Namer(
            type("Extract", (), {"labs": {
                "1": {"label": "Potassium"}, "2": {"label": "POTASSIUM"},
                "3": {"label": "Sodium"}}, "items": {}})(),
            type("Snapshot", (), {"concepts": {}})(), None)
        row = refs.Row("lab", 9, 1, ("lab", "item", "2"))
        self.assertEqual(namer(row), ("POTASSIUM",
                                      {"codes": frozenset(("1", "2"))}))

    def test_unnamed_records_are_left_out(self):
        """A drug record without a concept that writes no drug, and one
        whose concept has several ingredients, are not named; a written
        drug that the vocabulary names means its concepts too."""
        from ehr_evidence_app.qa import record_vocabulary
        self.assertIsNone(NAMER(row_of("medication", concept=0)))
        drugs = record_vocabulary.RecordVocabulary(
            drugs={concept: record_vocabulary.DrugConcept(
                concept, "ingredient", "RxNorm", "Ingredient",
                "C%d" % concept, "ingredient %d" % concept)
                for concept in (7001, 7002)},
            drug_names={}, brands={}, ancestors={}, procedure_names=())
        drugs.drugs[9001] = record_vocabulary.DrugConcept(
            9001, "atc", "ATC", "ATC 5th", "X01", "Ingredient 7001")
        namer = record_suite.Namer(
            type("Extract", (), {"labs": {}, "items": {}})(),
            type("Snapshot", (), {"concepts": {}})(), drugs)
        several = refs.Row("medication", 9, 1, ("medication", "concept", 5),
                           concept=5, ancestors=frozenset((7001, 7002)))
        self.assertIsNone(namer(several))
        written = refs.Row("medication", 9, 1, ("medication", "code", "X",
                                                "ingredient 7001"),
                           written="ingredient 7001")
        self.assertEqual(namer(written), ("ingredient 7001", {
            "concepts": frozenset((7001, 9001)),
            "written": (("ingredient", "7001"),)}))
        one = refs.Row("medication", 9, 1, ("medication", "concept", 5),
                       concept=5, ancestors=frozenset((7001, 8000)))
        self.assertEqual(namer(one), ("ingredient 7001", {
            "concepts": frozenset((7001, 9001)),
            "written": (("ingredient", "7001"),)}))


class QuestionTests(unittest.TestCase):
    """The generated questions."""

    def test_items_once_each_in_order(self):
        """A scope's items are named once each, in the order of their first
        record, up to ITEMS."""
        found = record_suite.items_of(CASE, EVERY, "condition", NAMER)
        self.assertEqual([name for name, _ in found],
                         ["SYNTHETIC CONCEPT 1001", "SYNTHETIC CONCEPT 1002"])
        self.assertLessEqual(len(found), record_suite.ITEMS)
        self.assertEqual(record_suite.items_of(CASE, contracts.ScopeRef(
            kind="selected_visit", visit_number=1), "vital", NAMER), [])

    def test_wordings_are_filled_and_meant(self):
        """Each wording with an item is asked once for each item, with the
        item's identifiers as its meaning."""
        found = record_suite.questions_of(LABEL, CASE, EVERY, NAMER)
        forms = collections.Counter(form for form, _ in found)
        whole = [form for form in record_suite.FORMS
                 if "{name}" not in form.wording]
        self.assertTrue(all(forms[form] == 1 for form in whole))
        last = next(row for form, row in found
                    if form.wording == "What was the last {name}?"
                    and form.kind == "lab")
        self.assertEqual(last.question, "What was the last synthetic "
                                        "potassium?")
        self.assertEqual((last.asked.task, last.asked.selector,
                          last.asked.codes),
                         ("record_values", "latest", frozenset(("50971",))))

    def test_every_form_is_a_meaning_the_references_judge(self):
        """Each wording's task, kind, selector and field are ones a row
        about the record may mean."""
        from ehr_evidence_app.assessment import end_to_end
        for form in record_suite.FORMS:
            with self.subTest(form=form.label):
                self.assertIn(form.kind, end_to_end.TASK_KINDS[form.task])
                self.assertEqual(form.selector is not None,
                                 form.task == "record_values")
                self.assertEqual(form.field is not None,
                                 form.task in end_to_end.DETAILS)


class RunTests(unittest.TestCase):
    """The default mode."""

    def test_default_mode_passes(self):
        """The invented case's suite ends as expected."""
        _, totals, problems = record_suite.run_synthetic()
        self.assertEqual(problems, [])
        self.assertEqual(totals, record_suite.SYNTHETIC_EXPECTED)

    def test_the_evaluation_set_is_refused(self):
        """The operator mode refuses a case set that is not for
        development."""
        with self.assertRaisesRegex(record_suite.RecordSuiteError,
                                    "case_set_held_out"):
            record_suite.run_real("eval-100-v1")


if __name__ == "__main__":
    unittest.main()
