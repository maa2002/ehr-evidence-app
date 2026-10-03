"""Synthetic checks for ehr_evidence_app.assessment.record_references.

The case is invented: two visits, laboratory results, a heart rate, a
transfer, an ICU stay and prescriptions; no real record is read.
"""

import dataclasses
import unittest

from ehr_evidence_app.assessment import record_references as refs
from ehr_evidence_app.evidence import contracts, readers

EVERY = contracts.ScopeRef(kind="case_visits")
HISTORY = contracts.ScopeRef(kind="pre_target_history")
SECOND = contracts.ScopeRef(kind="selected_visit", visit_number=2)
POTASSIUM = frozenset(("50971",))


def lab_key(number):
    """Return an invented laboratory result's name in the metrics."""
    return ("record", "lab", number)


class ReferenceTests(unittest.TestCase):
    """Expected answers on the invented case."""

    def setUp(self):
        """Read the invented case."""
        self.case = refs.synthetic_case()

    def test_default_mode(self):
        """The module's own checks pass."""
        self.assertEqual(refs.run_synthetic(), [])

    def test_scopes(self):
        """The history is the visit before the last; a raw row belongs to
        the visit whose admission it names."""
        self.assertEqual(self.case.numbers(HISTORY), (1,))
        self.assertEqual({row.native_id for row in
                          self.case.scoped(HISTORY, "lab")}, {1})
        self.assertEqual({row.native_id for row in
                          self.case.scoped(SECOND, "lab")}, {2, 3})

    def test_selectors(self):
        """The latest, first, highest and lowest of one item, and all of
        a named item in time order."""
        picked = {selector: refs.reference(
            "v", self.case, EVERY, refs.Asked(
                "record_values", "lab", codes=POTASSIUM,
                selector=selector)).picked[("lab", "item", "50971")]
            for selector in ("latest", "first", "highest", "lowest", "all")}
        self.assertEqual(picked["latest"], frozenset((lab_key(3),)))
        self.assertEqual(picked["first"], frozenset((lab_key(1),)))
        self.assertEqual(picked["highest"], frozenset((lab_key(3),)))
        self.assertEqual(picked["lowest"], frozenset((lab_key(1),)))
        self.assertEqual(picked["all"], frozenset(
            (lab_key(1), lab_key(2), lab_key(3))))

    def test_single_record_is_the_only_one(self):
        """With one record of the item in the scope, every selector picks
        it."""
        for selector in ("latest", "first", "highest", "lowest"):
            found = refs.reference("v", self.case, HISTORY, refs.Asked(
                "record_values", "lab", codes=POTASSIUM, selector=selector))
            self.assertEqual(list(found.picked.values()),
                             [frozenset((lab_key(1),))])

    def test_values_need_a_value(self):
        """A result without a value is not a value: with none left, the
        values are not recorded; a flag without a value is no flag."""
        rows = [dataclasses.replace(row, value=None, number=None,
                                    valued=False, flagged=False)
                if row.kind == "lab" else row for row in self.case.rows]
        case = refs.synthetic_case()
        case.rows = rows
        found = refs.reference("v", case, EVERY, refs.Asked(
            "record_values", "lab", codes=POTASSIUM, selector="latest"))
        self.assertEqual(found.outcome, "not_recorded")
        found = refs.reference("a", case, EVERY, refs.Asked(
            "record_abnormal", "lab"))
        self.assertEqual(found.outcome, "not_recorded")

    def test_flagged_whole_kind_shows_the_latest(self):
        """Abnormal results of a whole kind name the latest flagged result
        of each item; of a named item, every flagged one (up to 30)."""
        whole = refs.reference("a", self.case, EVERY, refs.Asked(
            "record_abnormal", "lab"))
        self.assertEqual(whole.records, {("lab", "item", "50971"):
                                         frozenset((lab_key(3),))})
        named = refs.reference("a", self.case, EVERY, refs.Asked(
            "record_abnormal", "lab", codes=POTASSIUM))
        self.assertEqual(named.records, {("lab", "item", "50971"):
                                         frozenset((lab_key(1),
                                                    lab_key(3)))})

    def test_lists_and_counts(self):
        """A list gives every item of the kind with its records; a count
        of an item its records and their visits."""
        listed = refs.reference("l", self.case, EVERY,
                                refs.Asked("record_list", "lab"))
        self.assertEqual(listed.records, {("lab", "item", "50971"):
                                          frozenset((lab_key(1), lab_key(2),
                                                     lab_key(3)))})
        counted = refs.reference("c", self.case, SECOND, refs.Asked(
            "record_count", "lab", codes=POTASSIUM))
        self.assertEqual((counted.visits, len(counted.records[
            ("lab", "item", "50971")])), ((2,), 2))
        stays = refs.reference("s", self.case, EVERY,
                               refs.Asked("record_list", "icu_stay"))
        self.assertEqual(len(stays.records), 1)

    def test_visits_and_person(self):
        """A visit's details for every visit of the scope; the person's sex
        and death."""
        found = refs.reference("d", self.case, EVERY, refs.Asked(
            "visit_details", field="admitted"))
        self.assertEqual(found.details, {1: "2100-01-01 07:00:00",
                                         2: "2100-02-01 07:00:00"})
        found = refs.reference("p", self.case, EVERY, refs.Asked(
            "person_details", field="sex"))
        self.assertEqual(found.details, {0: "F"})
        found = refs.reference("p", self.case, EVERY, refs.Asked(
            "person_details", field="death"))
        self.assertEqual(found.details, {0: None})

    def test_medication_doses_have_no_reference(self):
        """A question about doses has no reference here."""
        found = refs.reference("m", self.case, EVERY, refs.Asked(
            "record_values", "medication", selector="all"))
        self.assertEqual(found.outcome, "none")

    def test_unknown_task_refused(self):
        """A task that is not about the record is refused."""
        with self.assertRaises(refs.RecordReferenceError):
            refs.reference("x", self.case, EVERY, refs.Asked(
                "condition_list"))


class MatchingTests(unittest.TestCase):
    """What an asked item matches."""

    def test_written_names_and_concepts(self):
        """A medication matches by its concept and its ingredients, and one
        without a standard concept by the whole words of the drug its
        prescription writes; a condition without one is unjudged."""
        asked = refs.Asked("record_presence", "medication",
                           concepts=frozenset((1361,)),
                           written=(("heparin",),))
        with_concept = refs.Row("medication", 1, 1, ("medication",),
                                concept=40000, ancestors=frozenset((1361,)))
        written = refs.Row("medication", 2, 1, ("medication",),
                           written="heparin sodium")
        other = refs.Row("medication", 3, 1, ("medication",),
                         written="heparinoid cream")
        self.assertTrue(refs.matches(with_concept, asked, None))
        self.assertTrue(refs.matches(written, asked, None))
        self.assertFalse(refs.matches(other, asked, None))
        condition = refs.Row("condition", 4, 1, ("condition",))
        self.assertIsNone(refs.matches(condition, refs.Asked(
            "record_presence", "condition", concepts=frozenset((5,))),
            lambda concept: frozenset((concept,))))

    def test_words(self):
        """Short words match whole, longer ones within a word."""
        self.assertTrue(refs.words_in(("micu",), "medical icu micu"))
        self.assertTrue(refs.words_in(("cardi",), "cardiac surgery"))
        self.assertFalse(refs.words_in(("cu",), "micu"))

    def test_prescriptions_link(self):
        """A prescription's drug is found by its admission, times and NDC;
        two prescriptions with the same keys and different drugs name
        none."""
        extract = refs.InventedExtract({"prescriptions": [
            {"hadm_id": "11", "drug": "Heparin", "starttime":
             "2100-01-01 08:00:00", "stoptime": "2100-01-02 08:00:00",
             "ndc": "123"},
            {"hadm_id": "11", "drug": "Warfarin", "starttime":
             "2100-01-01 08:00:00", "stoptime": "2100-01-02 08:00:00",
             "ndc": "456"},
            {"hadm_id": "11", "drug": "Other", "starttime":
             "2100-01-01 08:00:00", "stoptime": "2100-01-02 08:00:00",
             "ndc": "456"}]})
        found = refs.prescribed(extract, "100", {"11": 1})
        self.assertEqual(found[("11", "2100-01-01 08:00:00",
                                "2100-01-02 08:00:00", "123")], "Heparin")
        self.assertIsNone(found[("11", "2100-01-01 08:00:00",
                                 "2100-01-02 08:00:00", "456")])
        self.assertEqual(refs.full_time("2100-01-01T08:00"),
                         "2100-01-01 08:00:00")

    def test_visit_admission_repeated_refused(self):
        """Two visits of one admission stop the references."""
        case = refs.synthetic_case()
        snapshot = readers.Snapshot(
            "x", "0" * 64, "0" * 64, True,
            {501: readers.SourceVisit(501, 7, 9201, None, None, "100|11"),
             502: readers.SourceVisit(502, 7, 9201, None, None, "100|11")},
            {"condition": (), "procedure": (), "drug": ()}, {})
        with self.assertRaisesRegex(refs.RecordReferenceError,
                                    "visit_admission_repeated"):
            refs.visits_of(case.case, snapshot)


if __name__ == "__main__":
    unittest.main()
