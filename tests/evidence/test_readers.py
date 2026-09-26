"""Synthetic checks for ehr_evidence_app.evidence.readers.

The parsers are exercised on invented bytes and records held in memory;
the file-reading entry points are covered end to end over invented scratch
files by tests/evidence/test_bundles.py. No real record is read.
"""

import unittest

from ehr_evidence_app.evidence import readers
from ehr_evidence_app.gnn import admission
from ehr_evidence_app.gnn import provenance as amp

VISITS = (b"visit_occurrence_id,person_id,visit_concept_id,visit_start_date,"
          b"visit_end_date\r\n801,77,9201,2100-01-01,2100-01-02\r\n"
          b"802,77,9201,2100-02-01,\r\n")
CONDITIONS = (b"condition_occurrence_id,person_id,visit_occurrence_id,"
              b"condition_concept_id,condition_start_date,"
              b"condition_start_datetime,condition_end_date,"
              b"condition_end_datetime,condition_source_value,"
              b"condition_source_concept_id\n"
              b"11,77,801,1001,2100-01-01,2100-01-01 10:30:00.000,,,SRC-1,5\n"
              b"12,77,,0,2100-01-02,,,,SRC-2,\n")


def rows(data, columns):
    """Return parsed CSV rows of invented bytes."""
    return readers.csv_rows(data, "synthetic", columns)


def tag(position, **changes):
    """Return one invented closure driver tag."""
    values = {"position": position, "score_class": "positive",
              "attachment": "earlier_only", "target_outcome_concept": False,
              "narration_eligible": True}
    values.update(changes)
    return values


def mapping_record():
    """Return an invented identity-mapping record."""
    return {"patient_index": 4, "graph_position": 5, "person_id": 77,
            "subject_id": 99,
            "visit_nodes": [{"node_position": 0, "visit_occurrence_id": 801},
                            {"node_position": 1, "visit_occurrence_id": 802}]}


def closure_item(record, **changes):
    """Return an invented closure case entry bound to record."""
    values = {"case_position": 0, "patient_index": 4, "graph_position": 5,
              "mapping_record_sha256": amp.digest(record),
              "output_record_sha256": "6" * 64,
              "admission": {"status": admission.ADMITTED,
                            "restrictions": ["batch_sensitive_prediction"]},
              "prediction": {"batch_sensitive": True,
                             "single_graph_reproduces_published": True},
              "label_evidence": {"correspondence": "established"},
              "upstream_partition": "validation",
              "explanation_eligible": True,
              "drivers": {"condition": [tag(0)], "procedure": [],
                          "drug": [tag(0, score_class="zero",
                                       narration_eligible=False)]}}
    values.update(changes)
    return values


class ValueTests(unittest.TestCase):
    """Strict scalar parsing."""

    def test_integers(self):
        """Only strict decimal integers pass; empty optional is None."""
        self.assertEqual(readers.required_int("12", "x"), 12)
        self.assertIsNone(readers.optional_int("", "x"))
        for value in ("", "012", "1.0", "-", " 3", "+3"):
            with self.assertRaisesRegex(readers.ReaderError,
                                        "value_not_integer:x"):
                readers.required_int(value, "x")

    def test_dates_and_datetimes(self):
        """Dates are ISO; datetimes lose fractions and use one space."""
        self.assertEqual(readers.optional_date(" 2100-01-02 ", "d"),
                         "2100-01-02")
        self.assertIsNone(readers.optional_date("", "d"))
        with self.assertRaisesRegex(readers.ReaderError, "value_not_date:d"):
            readers.optional_date("02/01/2100", "d")
        self.assertEqual(readers.optional_datetime("2100-01-02T03:04:05.123",
                                                   "t"),
                         "2100-01-02 03:04:05")
        self.assertEqual(readers.optional_datetime("2100-01-02 03:04", "t"),
                         "2100-01-02 03:04")
        self.assertIsNone(readers.optional_datetime(None, "t"))
        for value in ("2100-01-02", "2100-01-02 3:04"):
            with self.assertRaisesRegex(readers.ReaderError,
                                        "value_not_datetime:t"):
                readers.optional_datetime(value, "t")

    def test_text(self):
        """Blank text is None; other text is stripped."""
        self.assertIsNone(readers.optional_text("  "))
        self.assertEqual(readers.optional_text(" a b "), "a b")


class CsvTests(unittest.TestCase):
    """CSV and JSON parsing from verified bytes."""

    def test_required_columns_and_encoding(self):
        """Missing columns and non-UTF-8 bytes fail closed."""
        self.assertEqual(len(rows(VISITS, readers.VISIT_COLUMNS)), 2)
        with self.assertRaisesRegex(readers.ReaderError,
                                    "input_columns_missing:synthetic"):
            rows(VISITS, readers.VISIT_COLUMNS + ("other",))
        with self.assertRaisesRegex(readers.ReaderError,
                                    "input_not_utf8:synthetic"):
            rows(b"\xff\xfe", ("a",))
        with self.assertRaisesRegex(readers.ReaderError,
                                    "input_columns_missing:synthetic"):
            rows(b"", ("a",))

    def test_json(self):
        """Invalid JSON fails closed."""
        self.assertEqual(readers.parse_json(b'{"a": 1}', "j"), {"a": 1})
        with self.assertRaisesRegex(readers.ReaderError, "input_not_json:j"):
            readers.parse_json(b"{", "j")

    def test_visits(self):
        """Visits parse by identifier; duplicates are refused."""
        visits = readers.parse_visits(rows(VISITS, readers.VISIT_COLUMNS))
        self.assertEqual(sorted(visits), [801, 802])
        self.assertIsNone(visits[802].end_date)
        with self.assertRaisesRegex(readers.ReaderError,
                                    "duplicate_native_key:visit"):
            readers.parse_visits(rows(VISITS + VISITS.splitlines(True)[1],
                                      readers.VISIT_COLUMNS))

    def test_events(self):
        """Events keep file order, raw values and empty visit links."""
        columns = readers.DOMAIN_COLUMNS["condition"][1:] + (
            "person_id", "visit_occurrence_id")
        events = readers.parse_events("condition", rows(CONDITIONS, columns))
        self.assertEqual([event.native_id for event in events], [11, 12])
        first, second = events
        self.assertEqual((first.visit_occurrence_id, first.concept_id,
                          first.start_datetime, first.source_value,
                          first.source_concept_id),
                         (801, 1001, "2100-01-01 10:30:00", "SRC-1", 5))
        self.assertEqual((second.visit_occurrence_id, second.concept_id,
                          second.source_concept_id), (None, 0, None))
        doubled = CONDITIONS + CONDITIONS.splitlines(True)[1]
        with self.assertRaisesRegex(readers.ReaderError,
                                    "duplicate_native_key:condition"):
            readers.parse_events("condition", rows(doubled, columns))

    def test_concepts(self):
        """Empty names stay empty; duplicates are refused."""
        data = (b"concept_id,concept_name,domain_id,vocabulary_id\n"
                b"1001,Synthetic,Condition,SNOMED\n2001,,Procedure,\n")
        concepts = readers.parse_concepts(rows(data, ("concept_id",)))
        self.assertEqual(concepts[1001].name, "Synthetic")
        self.assertIsNone(concepts[1001].code)
        coded = readers.parse_concepts(rows(
            b"concept_id,concept_name,domain_id,vocabulary_id,"
            b"concept_code\n5,Source,Condition,ICD10CM,Q50.9\n",
            ("concept_id",)))
        self.assertEqual(coded[5].code, "Q50.9")
        self.assertIsNone(concepts[2001].name)
        self.assertIsNone(concepts[2001].vocabulary_id)
        with self.assertRaisesRegex(readers.ReaderError,
                                    "duplicate_native_key:concept"):
            readers.parse_concepts(rows(data + b"1001,Other,Condition,X\n",
                                        ("concept_id",)))


class ManifestTests(unittest.TestCase):
    """The snapshot manifest pins every file it lists."""

    PLAN = {"label": "synthetic-1-v1", "snapshot_dir": "S:\\snapshot"}

    def manifest(self, **changes):
        """Return an invented complete manifest."""
        values = {"status": "complete", "run_state": "finished",
                  "promoted": True, "version_label": "synthetic-1-v1",
                  "files": [{"file": "visits.csv", "present": True,
                             "size_bytes": 10, "sha256": "A" * 64},
                            {"file": "drugs.csv", "present": False,
                             "size_bytes": 0, "sha256": "b" * 64}]}
        values.update(changes)
        return values

    def test_complete_manifest(self):
        """Files resolve under the snapshot directory with lower-case pins."""
        entries = readers.manifest_entries(self.manifest(), self.PLAN)
        self.assertEqual(readers.file_entry(entries, self.PLAN, "visits.csv"),
                         {"path": "S:\\snapshot\\visits.csv",
                          "size_bytes": 10, "sha256": "a" * 64})
        for name in ("drugs.csv", "concepts.csv"):
            with self.assertRaisesRegex(readers.ReaderError,
                                        "snapshot_file_unrecorded:" + name):
                readers.file_entry(entries, self.PLAN, name)

    def test_incomplete_manifest(self):
        """An unfinished, unpromoted or relabelled snapshot is refused."""
        for changes in ({"status": "partial"}, {"run_state": "running"},
                        {"promoted": False},
                        {"version_label": "synthetic-2-v1"}):
            with self.assertRaisesRegex(readers.ReaderError,
                                        "snapshot_not_complete"):
                readers.manifest_entries(self.manifest(**changes), self.PLAN)
        with self.assertRaisesRegex(readers.ReaderError,
                                    "snapshot_not_complete"):
            readers.manifest_entries([], self.PLAN)


class CaseTests(unittest.TestCase):
    """Cases are bound to their person and visits through the mapping."""

    def test_driver_tags(self):
        """Tags parse per domain in position order; bad tags are refused."""
        tags = readers.driver_tags({"condition": [tag(0), tag(1)]})
        self.assertEqual([item.position for item in tags["condition"]],
                         [0, 1])
        self.assertEqual((tags["procedure"], tags["drug"]), ((), ()))
        for domains in ({"condition": [tag(1)]},
                        {"drug": [tag(0, score_class="negative")]},
                        {"drug": [tag(0, attachment="nearby")]},
                        {"drug": ["x"]}):
            with self.assertRaisesRegex(readers.ReaderError,
                                        "closure_driver_tag_invalid"):
                readers.driver_tags(domains)

    def test_visit_ids(self):
        """Visit nodes are contiguous, positive and unique."""
        self.assertEqual(readers.visit_ids(mapping_record()), (801, 802))
        for nodes in ([], [{"node_position": 1, "visit_occurrence_id": 801}],
                      [{"node_position": 0, "visit_occurrence_id": 0}],
                      [{"node_position": 0, "visit_occurrence_id": "801"}]):
            with self.assertRaisesRegex(readers.ReaderError,
                                        "mapping_visit_nodes_invalid"):
                readers.visit_ids({"visit_nodes": nodes})
        repeated = [{"node_position": 0, "visit_occurrence_id": 801},
                    {"node_position": 1, "visit_occurrence_id": 801}]
        with self.assertRaisesRegex(readers.ReaderError,
                                    "mapping_visit_repeated"):
            readers.visit_ids({"visit_nodes": repeated})

    def test_closure_case(self):
        """A closure entry becomes a Case bound to its mapping record."""
        record = mapping_record()
        records = readers.index_records([record])
        case = readers.closure_case(closure_item(record), records)
        self.assertEqual((case.case_number, case.person_id, case.visit_ids,
                          case.admitted, case.batch_sensitive,
                          case.restrictions, case.label_correspondence),
                         (1, 77, (801, 802), True, True,
                          ("batch_sensitive_prediction",), "established"))

    def test_quarantined_case_keeps_no_visits(self):
        """A quarantined case binds by digest alone and keeps no visits."""
        record = dict(mapping_record(), patient_index=None,
                      graph_position=None,
                      visit_nodes=[{"node_position": 0,
                                    "visit_occurrence_id": None}])
        quarantined = readers.closure_case(closure_item(
            record, admission={"status": admission.QUARANTINED,
                               "restrictions": []}),
            readers.index_records([mapping_record(), record]))
        self.assertEqual((quarantined.admitted, quarantined.visit_ids),
                         (False, ()))

    def test_closure_case_mismatches(self):
        """Another mapping record, restriction or partition is refused."""
        record = mapping_record()
        records = readers.index_records([record])
        other = readers.index_records([dict(record, person_id=78)])
        for item, index, code in (
                (closure_item(record), other, "closure_mapping_mismatch"),
                (closure_item(record), {}, "closure_mapping_mismatch"),
                (closure_item(record, graph_position=6), records,
                 "closure_mapping_mismatch"),
                (closure_item(record, patient_index=5), records,
                 "closure_mapping_mismatch"),
                (closure_item(record, admission={
                    "status": admission.ADMITTED,
                    "restrictions": ["unknown"]}), records,
                 "closure_restriction_unknown"),
                (closure_item(record, upstream_partition="holdout"),
                 records, "closure_partition_invalid")):
            with self.assertRaisesRegex(readers.ReaderError, code):
                readers.closure_case(item, index)


class PlanTests(unittest.TestCase):
    """The real plan pins its inputs without reading them."""

    def test_real_plan_pins(self):
        """Closure, mapping, manifest and legacy map carry size and digest."""
        plan = readers.real_plan()
        self.assertEqual(plan["closure"]["sha256"], readers.CLOSURE_SHA256)
        self.assertEqual(plan["closure"]["size_bytes"], readers.CLOSURE_SIZE)
        for role in ("manifest", "closure", "mapping", "legacy_concepts"):
            self.assertEqual(len(plan[role]["sha256"]), 64)
        self.assertTrue(plan["legacy_concepts"]["path"].endswith(
            "kg_concepts.csv"))


if __name__ == "__main__":
    unittest.main()
