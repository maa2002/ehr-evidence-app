"""Synthetic checks for the record bundle types and their builder.

Every record here is invented (the builder's own invented case). No
source snapshot or raw extract is read.
"""

import unittest

from pydantic import ValidationError

from ehr_evidence_app.evidence import record, record_bundles


def entry(**fields):
    """Return an invented laboratory record with changes."""
    values = dict(ref="R1", kind="lab", source="labevents", native_id=1,
                  visit_ref="V1", name_status="available", name="Test",
                  code="1", measure=record.Measure(text="5", number=5.0))
    values.update(fields)
    return record.Entry(**values)


class EntryTests(unittest.TestCase):
    """A record holds the fields of its kind only."""

    def test_a_record_of_its_kind_is_valid(self):
        """An invented laboratory record is valid."""
        self.assertEqual(entry().kind, "lab")

    def test_fields_follow_the_kind(self):
        """Source, concept, name and parts must fit the kind."""
        for fields in ({"source": "chartevents"},
                       {"concept_id": 5},
                       {"name": None},
                       {"ancestors": (7,)},
                       {"dose": record.Dose(amount="1")}):
            with self.assertRaises(ValidationError):
                entry(**fields)
        with self.assertRaises(ValidationError):
            record.Entry(ref="R1", kind="medication", source="drug_exposure",
                         native_id=1, visit_ref="V1",
                         name_status="unmapped", concept_id=5)
        with self.assertRaises(ValidationError):
            record.Entry(ref="R1", kind="transfer", source="transfers",
                         native_id=1, visit_ref="V1",
                         name_status="unavailable")

    def test_a_measure_holds_a_value_and_an_ordered_range(self):
        """A measure without a value, or with a reversed range, is
        refused; an antibiotic needs its organism."""
        with self.assertRaises(ValidationError):
            record.Measure(unit="mg")
        with self.assertRaises(ValidationError):
            record.Measure(text="1", low=5.0, high=1.0)
        with self.assertRaises(ValidationError):
            record.Microbe(antibiotic="X")

    def test_item_keys_group_records(self):
        """Records of one item share a key; each ICU stay is its own."""
        self.assertEqual(record.item_key(entry(ref="R1")),
                         record.item_key(entry(ref="R2", native_id=2)))
        stay = record.Stay(unit="ICU")
        first = record.Entry(ref="R1", kind="icu_stay", source="icustays",
                             native_id=1, visit_ref="V1",
                             name_status="available", name="ICU", stay=stay)
        second = first.model_copy(update={"ref": "R2", "native_id": 2})
        self.assertNotEqual(record.item_key(first), record.item_key(second))


class BundleTests(unittest.TestCase):
    """The invented record bundles."""

    @classmethod
    def setUpClass(cls):
        """Build the invented bundles once."""
        cls.bundles, cls.counts = record_bundles.run_synthetic()
        cls.by_id = {bundle.bundle_id: bundle for bundle in cls.bundles}

    def test_one_bundle_per_scope(self):
        """History, each visit and all visits."""
        self.assertEqual(sorted(self.by_id), [
            "rec-c01-all", "rec-c01-history", "rec-c01-visit-1",
            "rec-c01-visit-2", "rec-c01-visit-3"])

    def test_the_history_leaves_the_final_admission_out(self):
        """The history holds no final-admission record, and counts
        them."""
        history = self.by_id["rec-c01-history"]
        every = self.by_id["rec-c01-all"]
        self.assertTrue(all(visit.role == "pre_target"
                            for visit in history.visits))
        final = {visit.ref for visit in every.visits
                 if visit.role == "final_admission"}
        left = sum(1 for item in every.records if item.visit_ref in final
                   and item.kind == "lab")
        self.assertEqual(
            history.coverage.of("lab").excluded_final_admission, left)

    def test_rows_outside_the_case_visits_are_counted_not_delivered(self):
        """A result without an admission is counted in the coverage."""
        every = self.by_id["rec-c01-all"]
        self.assertEqual(every.coverage.of("lab").outside_case_visits, 1)
        self.assertEqual(self.counts["records_outside_case_visits"]["lab"],
                         1)

    def test_visits_carry_their_admission_and_the_person(self):
        """Each visit has its admission; the person is in every bundle."""
        for bundle in self.bundles:
            self.assertEqual(bundle.person.sex, "F")
            self.assertTrue(all(visit.admission is not None
                                for visit in bundle.visits))

    def test_references_are_bundle_local(self):
        """A visit is referenced by its number; records by R."""
        every = self.by_id["rec-c01-all"]
        self.assertEqual([visit.ref for visit in every.visits],
                         ["V1", "V2", "V3"])
        self.assertEqual(record.refs_of(every) - {"C1", "D1"},
                         {"V1", "V2", "V3"} | {entry.ref
                                               for entry in every.records})

    def test_coverage_must_agree_with_the_records(self):
        """A bundle whose coverage miscounts is refused."""
        every = self.by_id["rec-c01-all"]
        with self.assertRaises(ValidationError):
            record.RecordBundle.model_validate(dict(
                every.model_dump(), records=every.model_dump()["records"][1:]))


class DoseTests(unittest.TestCase):
    """A prescription's dose is linked only when its sources agree."""

    def test_disagreeing_prescriptions_link_no_dose(self):
        """Two prescriptions with the same keys and different doses give
        no dose."""
        rows = [{"subject_id": "9", "hadm_id": "8", "starttime":
                 "2100-01-01 08:00:00", "stoptime": "", "drug": "A",
                 "prod_strength": "1 mg", "ndc": "", "drug_type": "MAIN",
                 "dose_val_rx": amount, "dose_unit_rx": "mg",
                 "route": "PO", "form_rx": ""} for amount in ("1", "2")]

        class Extract:
            """Invented prescriptions of one person."""

            def rows(self, subject, table):
                """Return the invented rows."""
                return rows

        found = record_bundles.doses_of(Extract(), "9", {"8"})
        self.assertEqual(list(found.values()), [None])
        found = record_bundles.doses_of(Extract(), "9", {"8"} - {"8"})
        self.assertEqual(found, {})
        key = record_bundles.prescription_key(rows[0])
        self.assertEqual(key, ("2100-01-01 08:00:00", "2100-01-01 08:00:00",
                               "A 1 mg"))


if __name__ == "__main__":
    unittest.main()
