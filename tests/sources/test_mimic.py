"""Synthetic checks for ehr_evidence_app.sources.mimic.

Every raw file here is invented: two invented persons, one other, and rows
that exercise quoting, line breaks inside a value, Latin-1 bytes and the
vital-sign list. No raw MIMIC-IV file and no source snapshot is read; the
snapshot's persons are supplied by a stub. Scratch directories are created
only under the configured workspace test temp directory.
"""

import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from ehr_evidence_app.sources import mimic
from ehr_evidence_app.storage import policy as guard

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def table(name):
    """Return the Table of a name."""
    return next(item for item in mimic.TABLES if item.name == name)


def scanned(name, data, routes=None):
    """Scan invented bytes of a table; return (counts, emitted rows)."""
    rows = []
    counts = mimic.scan(io.BytesIO(data), table(name),
                        routes if routes is not None else mimic.routes_of(
                            mimic.SYNTHETIC_SUBJECTS),
                        lambda labels, number, fields: rows.append(
                            (labels, number, fields)))
    return counts, rows


class ReadingTests(unittest.TestCase):
    """Records, fields and the rows a scan keeps."""

    def test_records_keep_quoted_line_breaks_and_drop_line_ends(self):
        """A quoted value may hold a line break; CRLF ends are removed."""
        data = b'a,b\r\n1,"x\r\ny"\r\n2,"z"\n3,w'
        counter = {"sha256": hashlib.sha256(), "size": 0}
        found = list(mimic.records(io.BytesIO(data), counter))
        self.assertEqual(found, [b"a,b", b'1,"x\r\ny"', b'2,"z"', b"3,w"])
        self.assertEqual(counter["size"], len(data))
        self.assertEqual(counter["sha256"].hexdigest(),
                         hashlib.sha256(data).hexdigest())

    def test_an_unterminated_quote_fails(self):
        """A record whose quote never closes is refused."""
        counter = {"sha256": hashlib.sha256(), "size": 0}
        with self.assertRaises(mimic.ExtractionError):
            list(mimic.records(io.BytesIO(b'a\n1,"open\n2'), counter))

    def test_synthetic_mode_passes(self):
        """The invented extraction keeps exactly the expected rows."""
        kept, latin1, problems = mimic.run_synthetic()
        self.assertEqual(problems, [])
        self.assertEqual(latin1, 1)
        self.assertEqual(kept["synthetic-b-v1"]["chartevents"], 1)

    def test_a_header_other_than_the_pinned_one_fails(self):
        """The raw header must be exactly the table's."""
        with self.assertRaises(mimic.ExtractionError) as caught:
            scanned("patients", b"subject_id,sex\n900001,F\n")
        self.assertIn("header_unexpected", str(caught.exception))
        with self.assertRaises(mimic.ExtractionError):
            scanned("patients", b"")

    def test_a_row_with_the_wrong_number_of_fields_fails(self):
        """A kept row must have every column of the header."""
        header = ",".join(table("patients").header).encode()
        with self.assertRaises(mimic.ExtractionError) as caught:
            scanned("patients", header + b"\n900001,F,60\n")
        self.assertIn("record_malformed", str(caught.exception))

    def test_only_the_persons_rows_and_the_vital_items_are_kept(self):
        """Other persons and chart items outside the list are skipped."""
        data = mimic.synthetic_files()["chartevents"]
        counts, rows = scanned("chartevents", data)
        self.assertEqual(counts["records"], 2)
        self.assertEqual(counts["kept"], {"synthetic-b-v1": 1})
        self.assertEqual([number for _, number, _ in rows], [1])
        self.assertEqual(rows[0][2][table("chartevents").index("itemid")],
                         "220045")

    def test_a_quote_before_the_person_column_reads_the_record_whole(self):
        """The fast path is not trusted when a quote precedes the person."""
        header = ",".join(table("labevents").header).encode()
        row = (b'"1",900001,800001,1,1,P1,2100-01-02 06:00:00,'
               b'2100-01-02 07:00:00,5,5,x,1,9,,ROUTINE,')
        counts, rows = scanned("labevents", header + b"\n" + row + b"\n")
        self.assertEqual(counts["kept"], {"synthetic-a-v1": 1})
        self.assertEqual(rows[0][2][0], "1")

    def test_dictionaries_are_kept_whole_for_every_case_set(self):
        """An item dictionary has no person: every row goes to every set."""
        counts, rows = scanned("d_items", mimic.synthetic_files()["d_items"])
        self.assertEqual(counts["kept"], {"synthetic-a-v1": 2,
                                          "synthetic-b-v1": 2})
        self.assertEqual(rows[0][0], ("synthetic-a-v1", "synthetic-b-v1"))

    def test_names_come_from_every_row(self):
        """The dictionary of names reads every row, kept or not."""
        header = ",".join(table("transfers").header).encode()
        data = header + (b"\n900001,800001,1,admit,Unit A,t,t"
                         b"\n900009,800009,2,admit,Unit B,t,t\n")
        counts, rows = scanned("transfers", data)
        self.assertEqual(counts["names"], {"careunit": ["Unit A",
                                                        "Unit B"]})
        self.assertEqual(len(rows), 1)


class CollectorTests(unittest.TestCase):
    """Keys, links and the written CSV of one case set."""

    def test_a_repeated_key_fails(self):
        """A table's own key must be unique."""
        collector = mimic.Collector("synthetic-a-v1", {"800001"})
        fields = ["900001", "800001", "700001", "admit", "Unit", "t", "t"]
        collector.add(table("transfers"), 1, fields)
        with self.assertRaises(mimic.ExtractionError):
            collector.add(table("transfers"), 2, fields)

    def test_rows_are_counted_by_their_admission_and_written_in_order(self):
        """Rows in a snapshot visit are counted; the CSV keeps the row
        numbers and drops the dropped columns."""
        collector = mimic.Collector("synthetic-a-v1", {"800001"})
        chart = table("chartevents")
        for number, admission in ((3, "800001"), (7, "800002")):
            collector.add(chart, number, ["900001", admission, "600001",
                                          "5", "t", "t", "220045", "80",
                                          "80", "bpm", "0"])
        self.assertEqual(collector.linked["chartevents"], 1)
        rows = list(csv.reader(io.StringIO(
            collector.table_bytes(chart).decode("utf-8"))))
        self.assertEqual(rows[0], [mimic.ROW_NUMBER] + list(chart.keep))
        self.assertNotIn("caregiver_id", rows[0])
        self.assertEqual([row[0] for row in rows[1:]], ["3", "7"])


@unittest.skipUnless(os.name == "nt", "Windows is the supported platform")
class ExtractTests(unittest.TestCase):
    """The whole extraction over invented raw files in scratch."""

    def setUp(self):
        """Write the invented raw files and a plan into scratch."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.work = tempfile.mkdtemp(prefix="records-", dir=root)
        self.addCleanup(shutil.rmtree, self.work, True)
        raw = os.path.join(self.work, "raw")
        pins = {}
        for item in mimic.TABLES:
            path = os.path.join(raw, item.path)
            os.makedirs(os.path.dirname(path))
            data = mimic.synthetic_files()[item.name]
            with open(path, "wb") as handle:
                handle.write(data)
            pins[item.name] = (len(data), hashlib.sha256(data).hexdigest())
        self.pins = pins
        protected = os.path.join(self.work, "protected")
        self.plan = {
            "raw_root": raw,
            "inputs": {item.name: {"path": os.path.join(raw, item.path),
                                   "pin": pins[item.name]}
                       for item in mimic.TABLES},
            "snapshots": {label: {"label": label}
                          for label in mimic.SYNTHETIC_SUBJECTS},
            "output_root": os.path.join(protected, "source-records"),
            "protected_root": protected,
        }
        persons = {label: (subjects, mimic.SYNTHETIC_ADMISSIONS[label],
                           "5" * 64)
                   for label, subjects in mimic.SYNTHETIC_SUBJECTS.items()}
        patches = (
            mock.patch.object(mimic, "INPUTS", pins),
            mock.patch.object(mimic, "LABELS",
                              tuple(mimic.SYNTHETIC_SUBJECTS)),
            mock.patch.object(mimic, "snapshot_persons",
                              lambda snapshot: persons[snapshot["label"]]),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_extraction_publishes_one_verified_directory_per_case_set(self):
        """Each case set gets its tables, its names and a manifest."""
        manifests = mimic.extract(self.plan)
        for label, manifest in manifests.items():
            self.assertEqual(mimic.verify(self.plan, label), [])
            files = {entry["file"]: entry for entry in manifest["files"]}
            self.assertEqual(set(files), {item.name + ".csv"
                                          for item in mimic.TABLES}
                             | {mimic.NAMES_FILE})
            self.assertTrue(all(entry["sha256"] == self.pins[
                entry["table"]][1] for entry in manifest["inputs"]))
            self.assertFalse(manifest["patient_bearing"])
        admissions = next(entry for entry in manifests[
            "synthetic-a-v1"]["files"] if entry["file"] == "admissions.csv")
        self.assertEqual((admissions["rows"],
                          admissions["rows_in_snapshot_visits"]), (1, 1))
        self.assertFalse(os.path.lexists(os.path.join(
            self.plan["output_root"], "synthetic-a-v1" + mimic.PARTIAL)))

    def test_a_changed_input_publishes_nothing(self):
        """An input whose digest is not its pin stops the extraction."""
        pins = dict(self.pins, labevents=(1, "0" * 64))
        with mock.patch.object(mimic, "INPUTS", pins):
            self.plan["inputs"]["labevents"]["pin"] = pins["labevents"]
            with self.assertRaises(mimic.ExtractionError) as caught:
                mimic.extract(self.plan)
        self.assertIn("input_hash_mismatch:labevents", str(caught.exception))
        self.assertFalse(os.path.lexists(self.plan["output_root"])
                         and os.listdir(self.plan["output_root"]))

    def test_an_unpinned_input_or_an_existing_output_is_refused(self):
        """Nothing is read without pins, and nothing is overwritten."""
        with mock.patch.object(mimic, "INPUTS",
                               dict(self.pins, patients=None)):
            with self.assertRaises(mimic.ExtractionError):
                mimic.extract(self.plan)
        mimic.extract(self.plan)
        with self.assertRaises(mimic.ExtractionError) as caught:
            mimic.extract(self.plan)
        self.assertIn("output_exists", str(caught.exception))

    def test_verify_finds_a_changed_or_an_extra_file(self):
        """A re-read file must be the manifest's, and no file is extra."""
        mimic.extract(self.plan)
        directory = os.path.join(self.plan["output_root"], "synthetic-a-v1")
        with open(os.path.join(directory, "patients.csv"), "ab") as handle:
            handle.write(b"x")
        with open(os.path.join(directory, "extra.csv"), "wb") as handle:
            handle.write(b"x")
        problems = mimic.verify(self.plan, "synthetic-a-v1")
        self.assertIn("files_unexpected", problems)
        self.assertIn("file_hash_mismatch:patients.csv", problems)

    def test_raw_inputs_must_be_permitted_sources(self):
        """A raw input outside the raw directory is refused."""
        outside = os.path.join(self.work, "patients.csv")
        with open(outside, "wb") as handle:
            handle.write(b"x")
        self.plan["inputs"]["patients"]["path"] = outside
        with self.assertRaises(mimic.ExtractionError) as caught:
            mimic.extract(self.plan)
        self.assertIn("input_path_rejected", str(caught.exception))


class CommandTests(unittest.TestCase):
    """The command line and its fixed plan."""

    def test_default_mode_passes_and_prints_counts_only(self):
        """The synthetic mode passes and prints no invented value."""
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(mimic.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())
        self.assertNotIn("900001", output.getvalue())

    def test_real_modes_need_the_operator_flag(self):
        """--verify-only and --measure-inputs are operator modes."""
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(mimic.main(["--verify-only"]), 2)
            self.assertEqual(mimic.main(["--measure-inputs"]), 2)

    def test_the_plan_reads_the_archive_and_writes_protected_data(self):
        """Inputs lie in the raw directory; outputs in protected data."""
        self.assertEqual(mimic.RAW_ROOT, guard.RAW_SOURCE_DIR)
        self.assertTrue(guard.is_within(mimic.OUTPUT_ROOT,
                                        guard.PRIVATE_ROOT + "\\protected-data",
                                        strict=True))
        self.assertEqual(set(mimic.INPUTS), set(mimic.TABLE_NAMES))
        self.assertEqual(len(mimic.VITAL_ITEMS), sum(
            len(items) for items in mimic.VITAL_GROUPS.values()))


if __name__ == "__main__":
    unittest.main()
