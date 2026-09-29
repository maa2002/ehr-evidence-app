"""Edge cases of the record bundle types and their builder.

Every row, record and case here is invented: the builder's own invented
case, changed one row at a time, and the invented edge record of
tests.qa.record_cases. No source snapshot or raw extract is read. Scratch
directories are created only under the configured workspace test temp
directory.
"""

import dataclasses
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from pydantic import ValidationError

from ehr_evidence_app.evidence import bundles as qa_bundles
from ehr_evidence_app.evidence import record, record_bundles
from ehr_evidence_app.evidence.schema import DigestRef
from tests.qa import record_cases as cases
from tests.sources.test_mimic import configured_test_temp

SUBJECT = record_bundles.SYNTHETIC_SUBJECT


def synthetic_build(values=None, change=None):
    """Build the invented case's record bundles, as the builder's
    synthetic mode does, with the visits' source values and the raw
    tables changed; return (bundles, counts)."""
    snapshot, case_set, _, _, _ = qa_bundles.synthetic_inputs()
    case = case_set.cases[0]
    values = values or ["%s|80000%d" % (SUBJECT, index + 1)
                        for index in range(len(case.visit_ids))]
    snapshot = dataclasses.replace(snapshot, visits={
        visit_id: dataclasses.replace(snapshot.visits[visit_id],
                                      source_value=value)
        for visit_id, value in zip(case.visit_ids, values)})
    case = dataclasses.replace(case, subject_id=int(SUBJECT))
    case_set = dataclasses.replace(case_set, cases=(case,))
    tables = record_bundles.synthetic_extract(case, snapshot)
    if change is not None:
        change(tables)
    provenance = record.RecordProvenance(
        source_snapshot=DigestRef(name="synthetic-1-v1", sha256="1" * 64),
        record_extract=DigestRef(name="synthetic-1-v1-records",
                                 sha256="6" * 64),
        identity_mapping=DigestRef(name="synthetic-mapping", sha256="4" * 64),
        closure_record=DigestRef(name="synthetic-closure", sha256="3" * 64),
        concept_table=DigestRef(name="synthetic-concepts", sha256="2" * 64),
        builder=record_bundles.BUILDER)
    return record_bundles.build_set(
        case_set, snapshot, record_bundles.Extract(tables), {3001: (7001,)},
        provenance, {"snapshot": "synthetic-1-v1",
                     "extract": "synthetic-1-v1-records"})


def every(bundles):
    """Return the all-visits bundle."""
    return next(bundle for bundle in bundles
                if bundle.scope.kind == "case_visits")


class BuilderEdgeTests(unittest.TestCase):
    """The builder at its edges, on the invented case."""

    def test_two_visits_of_one_admission_fail(self):
        """Rows of an admission two visits share would belong to either."""
        values = ["%s|800001" % SUBJECT] * 3
        with self.assertRaises(record_bundles.RecordBundleError) as caught:
            synthetic_build(values)
        self.assertIn("visit_admission_repeated", str(caught.exception))

    def test_a_visit_of_another_person_links_no_row(self):
        """A visit whose source value names another person gets no
        admission, and its rows are counted outside the case visits."""
        values = ["999999|800001"] + ["%s|80000%d" % (SUBJECT, index)
                                      for index in (2, 3)]
        built, _ = synthetic_build(values)
        bundle = every(built)
        self.assertIsNone(bundle.visits[0].admission)
        self.assertEqual(bundle.coverage.of("lab").outside_case_visits, 2)

    def test_the_build_is_deterministic(self):
        """The same inputs give the same bytes."""
        first, counts = synthetic_build()
        second, again = synthetic_build()
        self.assertEqual([record_bundles.bundle_bytes(bundle)
                          for bundle in first],
                         [record_bundles.bundle_bytes(bundle)
                          for bundle in second])
        self.assertEqual(counts, again)

    def test_a_bundle_over_its_limits_fails(self):
        """A bundle with more records than MAX_RECORDS is refused."""
        with mock.patch.object(record_bundles, "MAX_RECORDS", 2):
            with self.assertRaises(record_bundles.RecordBundleError) as \
                    caught:
                synthetic_build()
        self.assertIn("bundle_limit_exceeded", str(caught.exception))

    def test_a_malformed_row_fails_closed(self):
        """A time in another form, or an identifier that is not ASCII
        digits, stops the build."""
        def iso_time(tables):
            tables["labevents"][0]["charttime"] = "2100-01-01T09:00:00"

        def wide_digits(tables):
            tables["labevents"][0]["labevent_id"] = "１２"

        for change in (iso_time, wide_digits):
            with self.assertRaises(record_bundles.RecordBundleError) as \
                    caught:
                synthetic_build(change=change)
            self.assertIn("record_invalid", str(caught.exception))

    def test_raw_values_are_kept_only_as_far_as_they_hold(self):
        """A reversed reference range, an interpretation without an
        antibiotic and an antibiotic without an organism are left out;
        a death time without a death in hospital too."""
        def change(tables):
            lab = tables["labevents"][0]
            lab["ref_range_lower"], lab["ref_range_upper"] = "5.1", "3.3"
            micro = tables["microbiologyevents"][0]
            micro["org_name"] = ""
            micro["interpretation"] = "R"
            admission = tables["admissions"][0]
            admission["deathtime"] = "2100-01-02 10:00:00"
            admission["hospital_expire_flag"] = "0"
            tables["patients"][0]["gender"] = "U"
            tables["patients"][0]["dod"] = "2101-02-03 04:05:06"

        bundle = every(synthetic_build(change=change)[0])
        lab = next(entry for entry in bundle.records if entry.kind == "lab")
        self.assertEqual((lab.measure.low, lab.measure.high), (None, None))
        micro = next(entry for entry in bundle.records
                     if entry.kind == "microbiology")
        self.assertEqual((micro.microbe.organism, micro.microbe.antibiotic,
                          micro.microbe.interpretation), (None, None, None))
        admission = bundle.visits[0].admission
        self.assertEqual((admission.death_time, admission.hospital_death),
                         (None, False))
        self.assertEqual((bundle.person.sex, bundle.person.date_of_death),
                         (None, "2101-02-03"))

    def test_the_history_counts_the_final_admission_left_out(self):
        """Every kind's final-admission records are counted in the
        history's coverage, and none is delivered."""
        built, _ = synthetic_build()
        history = next(bundle for bundle in built
                       if bundle.scope.kind == "pre_target_history")
        final = every(built)
        last = final.visits[-1].ref
        for kind in record.KINDS:
            left = sum(1 for entry in final.records
                       if entry.kind == kind and entry.visit_ref == last)
            self.assertEqual(
                history.coverage.of(kind).excluded_final_admission, left,
                kind)
        self.assertTrue(all(visit.role == "pre_target"
                            for visit in history.visits))


class HelperTests(unittest.TestCase):
    """The builder's small readers of raw values."""

    def test_text_integers_and_times(self):
        """Fields are stripped; integers are ASCII digits; times keep
        their seconds."""
        self.assertIsNone(record_bundles.text("  "))
        self.assertEqual(record_bundles.text(" a "), "a")
        for value, expected in (("12", 12), (" 7 ", 7), ("-1", None),
                                ("1.0", None), ("²", None),
                                ("１", None), ("", None), (None, None)):
            self.assertEqual(record_bundles.integer(value), expected, value)
        self.assertEqual(record_bundles.time_of("2100-01-01 08:00:00.123"),
                         "2100-01-01 08:00:00")
        self.assertEqual(record_bundles.time_of("2100-01-01"), "2100-01-01")
        self.assertIsNone(record_bundles.time_of(""))
        self.assertEqual(record_bundles.full_time("2100-01-01 08:00"),
                         "2100-01-01 08:00:00")

    def test_measures(self):
        """A measure needs a value; numbers must be finite; only the
        laboratory's "abnormal" is a flag."""
        self.assertIsNone(record_bundles.measure("", "", "mg"))
        self.assertIsNone(record_bundles.measure(None, "nan", "mg"))
        found = record_bundles.measure("", "inf", "mg")
        self.assertIsNone(found)
        found = record_bundles.measure("POS", "", "", flag="delta")
        self.assertEqual((found.text, found.number, found.flag),
                         ("POS", None, None))
        found = record_bundles.measure("5", "5", "mg", "1", "9", "abnormal",
                                       "STAT")
        self.assertEqual((found.number, found.low, found.high, found.flag,
                          found.priority), (5.0, 1.0, 9.0, "abnormal",
                                            "STAT"))

    def test_prescription_keys(self):
        """The keys an OMOP drug record carries: start, stop or start,
        and the NDC else the drug and its strength."""
        row = {"starttime": "2100-01-01 08:00:00", "stoptime": "",
               "drug": "Heparin", "ndc": "", "prod_strength": ""}
        self.assertEqual(record_bundles.prescription_key(row),
                         ("2100-01-01 08:00:00", "2100-01-01 08:00:00",
                          "Heparin"))
        self.assertEqual(record_bundles.prescription_key(dict(
            row, ndc="00338", stoptime="2100-01-02 08:00:00")),
            ("2100-01-01 08:00:00", "2100-01-02 08:00:00", "00338"))
        self.assertIsNone(record_bundles.prescription_key(dict(
            row, starttime="")))
        self.assertIsNone(record_bundles.prescription_key(dict(row,
                                                               drug="")))

    def test_doses_link_only_when_every_source_agrees(self):
        """Once two prescriptions of the same keys disagree, no dose is
        linked, whatever follows; other admissions stay apart."""
        def row(amount, admission="8"):
            """Return an invented prescription."""
            return {"subject_id": "9", "hadm_id": admission,
                    "starttime": "2100-01-01 08:00:00", "stoptime": "",
                    "drug": "A", "prod_strength": "1 mg", "ndc": "",
                    "drug_type": "MAIN", "dose_val_rx": amount,
                    "dose_unit_rx": "mg", "route": "PO", "form_rx": ""}

        class Extract:
            """Invented prescriptions of one person."""

            def __init__(self, rows):
                """Hold the rows."""
                self.found = rows

            def rows(self, subject, table):
                """Return the invented rows."""
                return self.found

        for amounts in (("1", "1"), ("1", "2", "1"), ("1", "1", "2")):
            found = record_bundles.doses_of(Extract([row(amount)
                                                     for amount in amounts]),
                                            "9", {"8"})
            linked = list(found.values())[0]
            if len(set(amounts)) == 1:
                self.assertEqual(linked.amount, "1")
            else:
                self.assertIsNone(linked, amounts)
        found = record_bundles.doses_of(Extract([row("1"), row("2", "7")]),
                                        "9", {"8", "7"})
        self.assertEqual(sorted(dose.amount for dose in found.values()),
                         ["1", "2"])

    def test_drug_keys(self):
        """An OMOP drug record without a start time has no keys."""
        event = mock.Mock(start_datetime=None, end_datetime=None,
                          source_value="A")
        self.assertIsNone(record_bundles.drug_key(event))
        event = mock.Mock(start_datetime="2100-01-01 08:00",
                          end_datetime=None, source_value=None)
        self.assertEqual(record_bundles.drug_key(event),
                         ("2100-01-01 08:00:00", "2100-01-01 08:00:00", ""))


class TypeEdgeTests(unittest.TestCase):
    """The invariants of the record bundle types, broken one at a time."""

    @classmethod
    def setUpClass(cls):
        """Hold the invented edge record's all-visits bundle."""
        cls.bundle = cases.bundles()[-1]

    def entry(self, **fields):
        """Return an invented laboratory record with changes."""
        values = dict(ref="R1", kind="lab", source="labevents", native_id=1,
                      visit_ref="V1", name_status="available", name="Test",
                      code="1", measure=record.Measure(text="5", number=5.0))
        values.update(fields)
        return record.Entry(**values)

    def test_records_hold_the_parts_of_their_kind(self):
        """Each kind's part is its own; some kinds need theirs."""
        stay = record.Stay(unit="Unit")
        for fields in (
                {"kind": "transfer", "source": "transfers", "measure": None},
                {"kind": "microbiology", "source": "microbiologyevents",
                 "measure": None},
                {"kind": "vital", "source": "chartevents",
                 "dose": record.Dose(drug="A")},
                {"stay": stay},
                {"name_status": "available", "name": None},
                {"name_status": "unavailable"},
                {"kind": "condition", "source": "condition_occurrence",
                 "measure": None, "concept_id": 5,
                 "name_status": "unmapped", "name": None},
                {"kind": "condition", "source": "condition_occurrence",
                 "measure": None, "concept_id": 0},
                {"native_id": 0}, {"ref": "R0"}, {"visit_ref": "X1"},
                {"start": "2100-1-1"}, {"ancestors": (1,)}):
            with self.subTest(fields=fields):
                with self.assertRaises(ValidationError):
                    self.entry(**fields)

    def test_values_are_finite_and_ranges_ordered(self):
        """No value is infinite or not a number; a range is ordered."""
        for fields in ({"number": float("nan")}, {"number": float("inf")},
                       {"text": "1", "low": 2.0, "high": 1.0},
                       {"unit": "mg"}):
            with self.assertRaises(ValidationError):
                record.Measure(**fields)
        self.assertIsNotNone(record.Measure(text="1", low=1.0, high=1.0))
        with self.assertRaises(ValidationError):
            record.Stay(days=-1.0)
        with self.assertRaises(ValidationError):
            record.Microbe(interpretation="R")

    def test_people_admissions_and_visits(self):
        """Ages stop at 91; a death time needs a death in hospital; visit
        numbers count positions."""
        for fields in ({"anchor_age": 92}, {"anchor_age": -1},
                       {"sex": "X"}, {"anchor_year": 1800}):
            with self.assertRaises(ValidationError):
                record.Person(**dict({"native_id": 1, "anchor_age": 60,
                                      "anchor_year": 2100}, **fields))
        with self.assertRaises(ValidationError):
            record.Admission(native_id=1, death_time="2100-01-01 00:00:00",
                             hospital_death=False)
        with self.assertRaises(ValidationError):
            record.RecordVisit(ref="V1", position=0, visit_number=2,
                               role="pre_target", visit_concept_id=0)

    def test_coverage_describes_every_kind_in_order(self):
        """An empty kind is complete and empty; unnamed records are
        records; every kind is covered once, in order."""
        with self.assertRaises(ValidationError):
            record.KindCoverage(kind="lab", status="complete_empty",
                                in_scope=1, unnamed_in_scope=0,
                                excluded_final_admission=0,
                                outside_case_visits=0)
        with self.assertRaises(ValidationError):
            record.KindCoverage(kind="lab", status="complete", in_scope=1,
                                unnamed_in_scope=2,
                                excluded_final_admission=0,
                                outside_case_visits=0)
        document = self.bundle.coverage.model_dump(mode="json")
        record.RecordCoverage.model_validate_json(json.dumps(document))
        with self.assertRaises(ValidationError):
            record.RecordCoverage.model_validate_json(json.dumps(dict(
                document, kinds=document["kinds"][::-1])))

    def test_bundles_hold_their_own_scope(self):
        """References are unique, records lie in the bundle's visits, and
        a scope holds only its own visits."""
        document = self.bundle.model_dump(mode="json")

        def broken(**changes):
            """Return whether a bundle document with changes is refused."""
            try:
                record.RecordBundle.model_validate_json(json.dumps(
                    dict(document, **changes)))
            except ValidationError:
                return True
            return False

        self.assertFalse(broken())
        records = document["records"]
        self.assertTrue(broken(records=[records[0], dict(records[1],
                                                         ref=records[0]
                                                         ["ref"])]
                               + records[2:]))
        self.assertTrue(broken(records=[dict(records[0], visit_ref="V9")]
                               + records[1:]))
        self.assertTrue(broken(records=[records[0], dict(
            records[1], native_id=records[0]["native_id"],
            kind=records[0]["kind"], source=records[0]["source"])]
            + records[2:]))
        self.assertTrue(broken(scope={"kind": "pre_target_history",
                                      "visit_number": None}))
        self.assertTrue(broken(scope={"kind": "selected_visit",
                                      "visit_number": 1}))
        self.assertTrue(broken(visits=[]))
        final = dict(document["visits"][0], role="final_admission")
        self.assertTrue(broken(visits=[final] + document["visits"][1:]))

    def test_item_keys(self):
        """Records without a concept or a name are one item; a written
        drug or a source name keeps records apart; each ICU stay is its
        own."""
        unnamed = record.Entry(ref="R1", kind="medication",
                               source="drug_exposure", native_id=1,
                               visit_ref="V1", name_status="unmapped",
                               concept_id=0, code="0")
        other = unnamed.model_copy(update={"ref": "R2", "native_id": 2,
                                           "code": "1"})
        self.assertEqual(record.item_key(unnamed), record.item_key(other))
        written = unnamed.model_copy(update={
            "dose": record.Dose(drug="Vancomycin")})
        self.assertNotEqual(record.item_key(written),
                            record.item_key(unnamed))
        keys = {record.item_key(entry) for entry in self.bundle.records
                if entry.kind == "icu_stay"}
        self.assertEqual(len(keys), 2)
        self.assertEqual(record.finite(" 5 "), 5.0)
        for value in ("nan", "inf", "-inf", "", None, "abc"):
            self.assertIsNone(record.finite(value), value)


@unittest.skipUnless(os.name == "nt", "Windows is the supported platform")
class PublishTests(unittest.TestCase):
    """Publishing a record bundle set in scratch."""

    def setUp(self):
        """Make a scratch plan."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.work = tempfile.mkdtemp(prefix="record-bundles-", dir=root)
        self.addCleanup(shutil.rmtree, self.work, True)
        self.plan = {"output_dir": os.path.join(self.work, "out",
                                                "set-records-v1")}

    def test_a_failed_publish_leaves_nothing_behind(self):
        """When the directory cannot be renamed, every file written and
        the partial directory are removed."""
        files = {"record/rec-c01-all.json": b"{}\n", "manifest.json": b"{}\n"}
        with mock.patch.object(record_bundles.os, "rename",
                               side_effect=OSError("refused")):
            with self.assertRaises(OSError):
                record_bundles.publish(self.plan, files)
        self.assertEqual(os.listdir(os.path.dirname(
            self.plan["output_dir"])), [])
        record_bundles.publish(self.plan, files)
        self.assertEqual(sorted(os.listdir(self.plan["output_dir"])),
                         ["manifest.json", "record"])
        with self.assertRaises(OSError):
            record_bundles.publish(self.plan, files)


if __name__ == "__main__":
    unittest.main()
