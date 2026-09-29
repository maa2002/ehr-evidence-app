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


def random_rows(generator, count, width):
    """Return invented rows whose fields hold commas, quotes, line breaks
    of both kinds, accents and empty values."""
    pieces = ("a", "b", ",", '"', "\n", "\r\n", " ", "é", "µ", "1", "")
    return [[("".join(generator.choice(pieces)
                      for _ in range(generator.randrange(0, 6))))
             for _ in range(width)] for _ in range(count)]


class RecordEdgeTests(unittest.TestCase):
    """Records and scans at their edges: every chunk size, blank lines,
    marks, quotes and bytes."""

    def records_of(self, data, chunk):
        """Return the records of bytes read in chunks of a size, and the
        counter."""
        counter = {"sha256": hashlib.sha256(), "size": 0}
        with mock.patch.object(mimic, "CHUNK_BYTES", chunk):
            found = list(mimic.records(io.BytesIO(data), counter))
        return found, counter

    def test_records_agree_with_the_csv_module_at_every_chunk_size(self):
        """Random rows written by the csv module are read back whole,
        whatever the chunk size and the line ends, and hashed exactly."""
        import random
        generator = random.Random(20260930)
        for trial in range(40):
            rows = random_rows(generator, generator.randrange(1, 12),
                               generator.randrange(1, 5))
            ending = generator.choice(("\n", "\r\n"))
            text = io.StringIO(newline="")
            csv.writer(text, lineterminator=ending).writerows(rows)
            data = text.getvalue().encode("utf-8")
            if generator.random() < 0.3:
                data = data[:-len(ending)]
            for chunk in (1, 2, 3, 7, 64, 1 << 20):
                found, counter = self.records_of(data, chunk)
                parsed = [mimic.fields_of(record.decode("utf-8"))
                          for record in found]
                self.assertEqual(parsed, rows, (trial, chunk))
                self.assertEqual(counter["size"], len(data))
                self.assertEqual(counter["sha256"].hexdigest(),
                                 hashlib.sha256(data).hexdigest())

    def test_a_character_split_between_chunks_is_read_whole(self):
        """A multi-byte character across a chunk boundary is kept."""
        data = "a,b\n1,é\n2,µ€\n".encode("utf-8")
        found, _ = self.records_of(data, 1)
        self.assertEqual([record.decode("utf-8") for record in found],
                         ["a,b", "1,é", "2,µ€"])

    def test_a_blank_line_is_no_row_in_any_table(self):
        """A blank line inside a table is refused, in every table alike."""
        for item in mimic.TABLES:
            header = ",".join(item.header).encode()
            with self.assertRaises(mimic.ExtractionError, msg=item.name) \
                    as caught:
                scanned(item.name, header + b"\n\n")
            self.assertIn("record_malformed", str(caught.exception))
        # A file's own final line end is no blank line.
        counts, _ = scanned("patients", ",".join(
            table("patients").header).encode() + b"\n")
        self.assertEqual(counts["records"], 0)

    def test_a_byte_order_mark_and_line_ends_are_accepted(self):
        """The header may start with a byte order mark and end in CRLF; a
        header alone holds no rows."""
        header = ("﻿" + ",".join(table("patients").header)).encode(
            "utf-8")
        counts, rows = scanned("patients", header + b"\r\n")
        self.assertEqual((counts["records"], rows), (0, []))
        counts, rows = scanned("patients", header + b"\r\n900001,F,60,2100,"
                                                    b"2008 - 2010,")
        self.assertEqual(len(rows), 1)

    def test_quoted_keys_and_items_are_read_whole(self):
        """A quoted person or item is read by the full parse, and the
        vital-sign list still applies."""
        header = ",".join(table("chartevents").header).encode()
        data = header + (b'\n"900002",800002,600002,5,t,t,"220045",88,88,'
                         b'bpm,0\n"900002",800002,600002,5,t,t,"999999",x,,'
                         b',0\n')
        counts, rows = scanned("chartevents", data)
        self.assertEqual(counts["kept"], {"synthetic-b-v1": 1})
        self.assertEqual(rows[0][2][6], "220045")

    def test_latin1_is_counted_on_kept_rows_only(self):
        """A Latin-1 byte in a row that is not kept is not counted."""
        header = ",".join(table("patients").header).encode()
        data = header + b"\n900009,F,60,2100,2008 - 2010\xb5,\n"
        counts, rows = scanned("patients", data)
        self.assertEqual((counts["decoded_latin1"], rows), (0, []))
        data = header + b"\n900001,F,60,2100,2008 - 2010\xb5,\n"
        counts, rows = scanned("patients", data)
        self.assertEqual(counts["decoded_latin1"], 1)
        self.assertEqual(rows[0][2][4], "2008 - 2010\xb5")

    def test_a_person_of_two_case_sets_is_kept_for_both(self):
        """A row is emitted once with every label whose persons hold it."""
        routes = mimic.routes_of({"synthetic-a-v1": {"900001"},
                                  "synthetic-b-v1": {"900001"}})
        counts, rows = scanned("patients",
                               mimic.synthetic_files()["patients"], routes)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], ("synthetic-a-v1", "synthetic-b-v1"))
        self.assertEqual(counts["kept"], {"synthetic-a-v1": 1,
                                          "synthetic-b-v1": 1})

    def test_a_row_too_short_before_the_person_fails(self):
        """A row without the person's column is malformed."""
        header = ",".join(table("labevents").header).encode()
        with self.assertRaises(mimic.ExtractionError):
            scanned("labevents", header + b"\n1\n")
        with self.assertRaises(mimic.ExtractionError):
            scanned("d_labitems", b"itemid,label,fluid,category\n1,A\n")

    def test_names_are_stripped_and_empty_ones_left_out(self):
        """The dictionary of names holds stripped, non-empty values."""
        header = ",".join(table("transfers").header).encode()
        data = header + (b"\n900001,800001,1,admit, Unit A ,t,t"
                         b"\n900001,800001,2,admit,,t,t"
                         b"\n900009,800009,3,admit,Unit A,t,t\n")
        counts, _ = scanned("transfers", data)
        self.assertEqual(counts["names"], {"careunit": ["Unit A"]})


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

    def test_published_files_are_read_verified(self):
        """A published file is read only when it is its manifest's; a file
        the manifest does not list, or one changed, is refused."""
        mimic.extract(self.plan)
        directory = os.path.join(self.plan["output_root"], "synthetic-a-v1")
        path = os.path.join(directory, mimic.MANIFEST_NAME)
        with open(path, "rb") as handle:
            data = handle.read()
        pin = {"path": path, "size_bytes": len(data),
               "sha256": hashlib.sha256(data).hexdigest()}
        with mock.patch.object(mimic, "pinned_manifest", lambda label: pin):
            found = mimic.read_files("synthetic-a-v1", ("patients.csv",
                                                        mimic.NAMES_FILE))
            self.assertEqual([row["subject_id"]
                              for row in found["patients.csv"]], ["900001"])
            with self.assertRaises(mimic.ExtractionError) as caught:
                mimic.read_files("synthetic-a-v1", ("extra.csv",))
            self.assertIn("extract_file_unlisted", str(caught.exception))
            with open(os.path.join(directory, "patients.csv"),
                      "ab") as handle:
                handle.write(b"x")
            with self.assertRaises(mimic.ExtractionError) as caught:
                mimic.read_files("synthetic-a-v1", ("patients.csv",))
            self.assertIn("extract_hash_mismatch", str(caught.exception))
        pin = dict(pin, sha256="0" * 64)
        with mock.patch.object(mimic, "pinned_manifest", lambda label: pin):
            with self.assertRaises(mimic.ExtractionError):
                mimic.read_files("synthetic-a-v1", ("patients.csv",))

    def test_a_failed_publish_leaves_nothing_behind(self):
        """When a directory cannot be renamed, every partial directory is
        removed and nothing is published."""
        with mock.patch.object(mimic.os, "rename",
                               side_effect=OSError("refused")):
            with self.assertRaises(OSError):
                mimic.extract(self.plan)
        self.assertEqual(os.listdir(self.plan["output_root"]), [])
        manifests = mimic.extract(self.plan)
        self.assertEqual(set(manifests), set(mimic.SYNTHETIC_SUBJECTS))

    def test_verify_reads_what_it_is_given(self):
        """A missing or foreign manifest is reported, never trusted."""
        self.assertEqual(mimic.verify(self.plan, "synthetic-a-v1"),
                         ["manifest_unreadable"])
        mimic.extract(self.plan)
        self.assertEqual(mimic.verify(self.plan, "synthetic-a-v1"), [])
        directory = os.path.join(self.plan["output_root"], "synthetic-a-v1")
        path = os.path.join(directory, mimic.MANIFEST_NAME)
        with open(path, "rb") as handle:
            manifest = json.loads(handle.read())
        manifest["version_label"] = "synthetic-b-v1"
        with open(path, "wb") as handle:
            handle.write(json.dumps(manifest).encode("utf-8"))
        self.assertIn("manifest_unexpected",
                      mimic.verify(self.plan, "synthetic-a-v1"))

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
        self.assertTrue(guard.is_within(
            mimic.OUTPUT_ROOT, guard.PRIVATE_ROOT + "\\protected-data",
            strict=True))
        self.assertEqual(set(mimic.INPUTS), set(mimic.TABLE_NAMES))
        self.assertEqual(len(mimic.VITAL_ITEMS), sum(
            len(items) for items in mimic.VITAL_GROUPS.values()))


if __name__ == "__main__":
    unittest.main()
