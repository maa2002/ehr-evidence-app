"""Synthetic checks for ehr_evidence_app.sources.pilot (Steps 0.3.2, 0.4).

Fixtures are inline synthetic CSVs with invented identifiers. No database
connection is made: subprocess.run is replaced by a guard that fails the test
if it is ever called, and psql execution is replaced by a stub that writes the
synthetic snapshot. No source export or patient content is read. Scratch
directories are created only under the configured workspace test temp directory.
"""

import copy
import csv
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.casesets import registry  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402
from ehr_evidence_app.sources import pilot as pilot  # noqa: E402

CONFIG_PATH = os.path.join(REPO_ROOT, "config", "source_pilot.json")
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
LABEL = "synthetic-pilot-v1"
SECRET = "SYNTHETIC-PASSWORD-NEVER-LOGGED"
USER = "synthetic_reader"
TOKENS = ("700001", "700002", "SYNTHSUBJ", "900001", "SYN-")

# Two synthetic persons, four visits and events that deliberately include a
# null visit link, a repeated concept and a concept with no metadata row.
BASE_DATA = {
    "person": [
        {"person_id": "700001", "subject_id": "SYNTHSUBJ-1"},
        {"person_id": "700002", "subject_id": "SYNTHSUBJ-2"},
    ],
    "visit_occurrence": [
        {"visit_occurrence_id": "900001", "person_id": "700001",
         "visit_concept_id": "9201"},
        {"visit_occurrence_id": "900002", "person_id": "700001",
         "visit_concept_id": "9203"},
        {"visit_occurrence_id": "900003", "person_id": "700002",
         "visit_concept_id": "9201"},
        {"visit_occurrence_id": "900004", "person_id": "700002",
         "visit_concept_id": "262"},
    ],
    "condition_occurrence": [
        {"condition_occurrence_id": "110001", "person_id": "700001",
         "visit_occurrence_id": "900001", "condition_concept_id": "320128",
         "condition_source_concept_id": "440001"},
        {"condition_occurrence_id": "110002", "person_id": "700001",
         "visit_occurrence_id": "", "condition_concept_id": "320128",
         "condition_source_concept_id": "440001"},
        {"condition_occurrence_id": "110003", "person_id": "700002",
         "visit_occurrence_id": "900003", "condition_concept_id": "0",
         "condition_source_concept_id": "440002"},
    ],
    "procedure_occurrence": [
        {"procedure_occurrence_id": "210001", "person_id": "700001",
         "visit_occurrence_id": "900002", "procedure_concept_id": "4163872",
         "procedure_source_concept_id": "450001"},
        {"procedure_occurrence_id": "210002", "person_id": "700002",
         "visit_occurrence_id": "", "procedure_concept_id": "4163872",
         "procedure_source_concept_id": "450001"},
    ],
    "drug_exposure": [
        {"drug_exposure_id": "310001", "person_id": "700001",
         "visit_occurrence_id": "900001", "drug_concept_id": "1112807",
         "drug_source_concept_id": "460001"},
        {"drug_exposure_id": "310002", "person_id": "700001",
         "visit_occurrence_id": "900001", "drug_concept_id": "1112807",
         "drug_source_concept_id": "460001"},
        {"drug_exposure_id": "310003", "person_id": "700002",
         "visit_occurrence_id": "900004", "drug_concept_id": "1112807",
         "drug_source_concept_id": "460001"},
    ],
    "referenced_concepts": [
        {"concept_id": "0", "concept_name": "No matching concept"},
        {"concept_id": "262", "concept_name": "Emergency Room and Inpatient"},
        {"concept_id": "9201", "concept_name": "Inpatient Visit"},
        {"concept_id": "9203", "concept_name": "Emergency Room Visit"},
        {"concept_id": "320128", "concept_name": "Essential hypertension"},
        {"concept_id": "440001", "concept_name": ""},  # absent metadata
        {"concept_id": "440002", "concept_name": "Synthetic source condition"},
        {"concept_id": "450001", "concept_name": "Synthetic source procedure"},
        {"concept_id": "460001", "concept_name": "Synthetic source drug"},
        {"concept_id": "1112807", "concept_name": "Synthetic ingredient"},
        {"concept_id": "4163872", "concept_name": "Synthetic procedure"},
    ],
}

BASE_METADATA = {
    "database_name": "mimiciv_omop",
    "current_schema": "public",
    "server_version": "18.0",
    "server_version_num": "180000",
    "server_version_full": "PostgreSQL 18.0, synthetic build",
    "transaction_isolation": "repeatable read",
    "transaction_read_only": "on",
    "default_transaction_read_only": "on",
    "statement_timeout": "10min",
    "snapshot_id": "3054:3054:",
    "assigned_xact_id": "",
    "snapshot_time_utc": "2026-09-23T00:00:00Z",
}


def configured_test_temp():
    """Return the configured workspace test scratch directory (workspace-only rules)."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def default_counts(data):
    """Return the independent source counts implied by a synthetic fixture."""
    counts = {pilot.COHORT_KEY: len(data["person"]),
              pilot.CONCEPT_KEY: len(data["referenced_concepts"])}
    for name in ("person", "visit_occurrence", "condition_occurrence",
                 "procedure_occurrence", "drug_exposure"):
        counts[name] = len(data[name])
    return {key: str(value) for key, value in counts.items()}


def make_junction(test, link, target):
    """Create a real directory junction inside scratch, or skip the test."""
    try:
        import _winapi
        _winapi.CreateJunction(target, link)
    except (ImportError, AttributeError, OSError) as error:
        test.skipTest("directory junctions are unavailable here: %s"
                      % type(error).__name__)
    test.addCleanup(os.rmdir, link)  # removes the junction, not its target


def write_csv(path, headers, rows):
    """Write one synthetic CSV, filling unspecified columns with a token."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "SYN-" + name)
                             for name in headers})


class PilotTestCase(unittest.TestCase):
    """Extraction control flow and snapshot validation on synthetic files."""

    def setUp(self):
        """Create scratch space, synthetic credentials and a psql guard."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest(
                "configured test temp is missing; run "
                "scripts/setup-destinations.ps1 first"
            )
        self.assertTrue(
            os.path.normcase(root).startswith(os.path.normcase("G:\\")),
            "test scratch space must stay in the workspace",
        )
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="step-0-3-2-", dir=root)
        self.addCleanup(self.remove_scratch)
        self.dest = os.path.join(self.work, "dest")
        os.makedirs(self.dest)
        os.makedirs(os.path.join(self.work, "bin"))
        self.psql = os.path.join(self.work, "bin", "psql.exe")
        with open(self.psql, "wb") as handle:
            handle.write(b"synthetic placeholder; never executed\n")
        self.config = pilot.load_config(CONFIG_PATH)
        self.version_dir = os.path.join(self.dest, LABEL)
        self.calls = []
        self.set_environment()
        self.block_subprocess()

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the temp root.

        The absolute scratch path is resolved and checked immediately before
        the recursive removal, so a wrong or relocated path is left alone.
        """
        target = os.path.abspath(self.work)
        root = os.path.abspath(self.temp_root)
        strictly_inside = os.path.normcase(target).startswith(
            os.path.normcase(root) + os.sep
        )
        if not strictly_inside:
            self.fail("refusing to remove %s: outside %s" % (target, root))
        shutil.rmtree(target, ignore_errors=True)

    def set_environment(self, **overrides):
        """Install synthetic connection settings for the duration of a test.

        Redirect and option variables are cleared unless a test sets them; a
        value of None removes the variable. Originals are restored after.
        """
        values = {"PGHOST": "127.0.0.1", "PGPORT": "5432",
                  "PGDATABASE": "mimiciv_omop", "PGUSER": USER,
                  "PGPASSWORD": SECRET, "PGHOSTADDR": None,
                  "PGSERVICE": None, "PGSERVICEFILE": None,
                  "PGOPTIONS": None}
        values.update(overrides)
        restored = set(getattr(self, "_restored", ()))
        for name, value in values.items():
            if name not in restored:
                self.addCleanup(self.restore_environment, name,
                                os.environ.get(name))
                restored.add(name)
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._restored = tuple(restored)

    def restore_environment(self, name, value):
        """Restore one environment variable to its pre-test value."""
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    def block_subprocess(self):
        """Fail the test if any real process, including psql, is launched.

        The attempt is recorded as well as refused, so a launch cannot be
        swallowed by the tool's own sanitised exception handling.
        """
        self.launched = []
        original = pilot.subprocess.run
        self.addCleanup(self.assert_nothing_launched)
        self.addCleanup(setattr, pilot.subprocess, "run", original)

        def blocked(*args, **kwargs):
            """Refuse every real subprocess launch during the tests."""
            self.launched.append(args[0] if args else kwargs.get("args"))
            raise AssertionError("no process may be launched in tests")

        pilot.subprocess.run = blocked

    def assert_nothing_launched(self):
        """Assert that no test attempted to launch a real process."""
        self.assertEqual(self.launched, [],
                         "tests must never launch a process or open a "
                         "database connection")

    def install_psql_stub(self, data=None, counts=None, metadata=None,
                          run_state="finished", exit_code=0):
        """Replace psql execution with a writer of the synthetic snapshot."""
        original = pilot.run_psql
        self.addCleanup(setattr, pilot, "run_psql", original)

        def stub(config, version_dir, sql_path, options):
            """Write synthetic partial extracts instead of querying a DB."""
            self.calls.append(sql_path)
            pilot.write_bytes(
                os.path.join(version_dir, config["stdout_log_name"]),
                b"COPY 0\n")
            pilot.write_bytes(
                os.path.join(version_dir, config["stderr_log_name"]), b"")
            if run_state == "finished":
                self.write_fixture(version_dir, pilot.PARTIAL, data, counts,
                                   metadata)
                return {"run_state": "finished", "exit_code": 0}
            return {"run_state": run_state, "exit_code": exit_code,
                    "reason": "synthetic psql failure"}

        pilot.run_psql = stub

    def write_fixture(self, version_dir, suffix="", data=None, counts=None,
                      metadata=None):
        """Write a complete synthetic snapshot into a version directory."""
        data = copy.deepcopy(data or BASE_DATA)
        merged = default_counts(data)
        merged.update({key: str(value)
                       for key, value in (counts or {}).items()})
        meta = dict(BASE_METADATA)
        meta.update(metadata or {})
        for name, spec in pilot.all_specs(self.config):
            write_csv(os.path.join(version_dir, spec["file"] + suffix),
                      pilot.output_names(spec), data[name])
        write_csv(os.path.join(version_dir,
                               self.config["counts_file"] + suffix),
                  list(merged), [merged])
        write_csv(os.path.join(version_dir,
                               self.config["metadata_file"] + suffix),
                  list(meta), [meta])

    def run_cli(self, extra=None, label=LABEL, dest_root=None, cases=2,
                config=CONFIG_PATH):
        """Run the pilot CLI against the synthetic scratch tree."""
        argv = ["--config", config,
                "--dest-root", dest_root or self.dest,
                "--version-label", label,
                "--psql", self.psql]
        if cases is not None:
            argv += ["--cases", str(cases)]
        argv += extra or []
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = pilot.main(argv)
        return code, stream.getvalue()

    def manifest(self):
        """Return the manifest written into the synthetic version directory."""
        path = os.path.join(self.version_dir, self.config["manifest_name"])
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded, {entry["table"]: entry
                        for entry in loaded.get("tables", [])}

    def extract_clean(self, label):
        """Extract a clean synthetic snapshot and return its version path."""
        code, output = self.run_cli(label=label)
        self.assertEqual(code, 0, output)
        return os.path.join(self.dest, label)

    def edit_manifest(self, version_dir, change):
        """Apply change to the manifest stored in a version directory."""
        path = os.path.join(version_dir, self.config["manifest_name"])
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        change(loaded)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(loaded, handle)

    def assert_verify_fails(self, label, config=CONFIG_PATH):
        """Assert that verify-only rejects a version without claiming coverage.

        Returns the written verification report for further checks.
        """
        code, output = self.run_cli(label=label, config=config,
                                    extra=["--verify-only"])
        self.assertEqual(code, 1, label + "\n" + output)
        self.assertIn("RESULT: INCOMPLETE", output, label)
        self.assertIn("Coverage is NOT established", output, label)
        self.assertNotIn("Coverage is complete", output, label)
        self.assert_no_identifiers(output)
        path = os.path.join(self.dest, label,
                            self.config["verification_report_name"])
        with open(path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["status"], "incomplete", label)
        return report

    def assert_no_identifiers(self, text):
        """Assert that no synthetic identifier or secret appears in text."""
        for token in TOKENS + (SECRET, USER):
            self.assertNotIn(token, text)

    # -- extraction flow ---------------------------------------------------

    def test_clean_snapshot_completes_and_is_hashed(self):
        """A consistent snapshot validates, is promoted and is hashed."""
        self.install_psql_stub()
        code, output = self.run_cli()
        self.assertEqual(code, 0, output)
        loaded, tables = self.manifest()
        self.assertEqual(loaded["status"], "complete")
        self.assertEqual(loaded["run_state"], "finished")
        self.assertTrue(loaded["promoted"])
        self.assertEqual(len(tables), 6)
        for name, entry in tables.items():
            self.assertFalse(entry["file"].endswith(pilot.PARTIAL), name)
            self.assertTrue(os.path.isfile(
                os.path.join(self.version_dir, entry["file"])), name)
            self.assertEqual(entry["status"], "complete", name)
            self.assertEqual(entry["failures"], [], name)
            self.assertEqual(entry["rows_parsed"], entry["expected_rows"],
                             name)
        for spec_name, spec in pilot.all_specs(self.config):
            final = os.path.join(self.version_dir, spec["file"])
            self.assertTrue(os.path.isfile(final), spec_name)
            self.assertFalse(os.path.exists(final + pilot.PARTIAL), spec_name)
        for entry in loaded["files"]:
            self.assertTrue(entry["present"], entry["file"])
            self.assertEqual(
                entry["sha256"],
                pilot.sha256_file(entry["path"],
                                  self.config["chunk_bytes"]))
            self.assertEqual(entry["size_bytes"],
                             os.path.getsize(entry["path"]))
        self.assertEqual(loaded["connection"]["queried_schema"], "omopcdm")
        self.assertEqual(loaded["validation"]["queried_schema"], "omopcdm")
        self.assertIn("Coverage is complete only within", output)
        self.assertIn("RESULT: COMPLETE", output)
        self.assert_no_identifiers(output)
        self.assert_no_identifiers(json.dumps(loaded))

    def test_row_count_mismatch_blocks_promotion(self):
        """A count that differs from the source count fails that table."""
        self.install_psql_stub(counts={"condition_occurrence": 99})
        code, output = self.run_cli()
        self.assertEqual(code, 1, output)
        loaded, tables = self.manifest()
        self.assertEqual(loaded["status"], "incomplete")
        self.assertEqual(loaded["run_state"], "finished")
        self.assertFalse(loaded["promoted"])
        entry = tables["condition_occurrence"]
        self.assertEqual(entry["rows_parsed"], 3)
        self.assertEqual(entry["expected_rows"], 99)
        self.assertEqual(entry["failures"],
                         ["row_count_differs_from_source_count"])
        self.assertEqual(tables["drug_exposure"]["status"], "complete")
        conditions = os.path.join(self.version_dir, "conditions.csv")
        self.assertFalse(os.path.exists(conditions))
        self.assertTrue(os.path.isfile(conditions + pilot.PARTIAL))
        self.assertEqual(entry["file"], "conditions.csv" + pilot.PARTIAL)
        self.assertIn("Coverage is NOT established", output)
        self.assertNotIn("Coverage is complete", output)
        self.assertIn("RESULT: INCOMPLETE", output)

    def test_duplicate_native_identifiers_fail(self):
        """A repeated native primary key is detected and fails the table."""
        data = copy.deepcopy(BASE_DATA)
        duplicate = dict(data["drug_exposure"][0])
        data["drug_exposure"].append(duplicate)
        self.install_psql_stub(data=data)
        code, output = self.run_cli()
        self.assertEqual(code, 1, output)
        loaded, tables = self.manifest()
        entry = tables["drug_exposure"]
        self.assertEqual(entry["rows_parsed"], 4)
        self.assertEqual(entry["expected_rows"], 4)
        self.assertEqual(entry["duplicate_primary_keys"], 1)
        self.assertEqual(entry["failures"], ["duplicate_primary_keys"])
        self.assertEqual(loaded["status"], "incomplete")

    def test_cross_patient_visit_link_fails(self):
        """An event linked to another person's visit is a linkage failure."""
        data = copy.deepcopy(BASE_DATA)
        data["condition_occurrence"][2]["visit_occurrence_id"] = "900001"
        self.install_psql_stub(data=data)
        code, output = self.run_cli()
        self.assertEqual(code, 1, output)
        _, tables = self.manifest()
        entry = tables["condition_occurrence"]
        self.assertEqual(entry["visit_person_mismatches"], 1)
        self.assertEqual(entry["visit_links_unresolved"], 0)
        self.assertEqual(entry["failures"], ["visit_person_mismatches"])

    def test_unknown_visit_link_fails(self):
        """A visit link that resolves to no extracted visit is a failure."""
        data = copy.deepcopy(BASE_DATA)
        data["drug_exposure"][0]["visit_occurrence_id"] = "999999"
        self.install_psql_stub(data=data)
        code, output = self.run_cli()
        self.assertEqual(code, 1, output)
        _, tables = self.manifest()
        entry = tables["drug_exposure"]
        self.assertEqual(entry["visit_links_unresolved"], 1)
        self.assertEqual(entry["failures"], ["visit_links_unresolved"])

    def test_event_for_unselected_person_fails(self):
        """An event whose person is not selected fails coverage checking."""
        data = copy.deepcopy(BASE_DATA)
        data["procedure_occurrence"].append(
            {"procedure_occurrence_id": "210003", "person_id": "700099",
             "visit_occurrence_id": "", "procedure_concept_id": "4163872",
             "procedure_source_concept_id": "450001"})
        self.install_psql_stub(data=data)
        code, output = self.run_cli()
        self.assertEqual(code, 1, output)
        _, tables = self.manifest()
        entry = tables["procedure_occurrence"]
        self.assertEqual(entry["rows_parsed"], entry["expected_rows"])
        self.assertEqual(entry["rows_person_not_selected"], 1)
        self.assertEqual(entry["failures"], ["rows_person_not_selected"])

    def test_null_visit_links_are_preserved_not_failures(self):
        """Null visit links are counted, kept in the file and not failures."""
        self.install_psql_stub()
        code, output = self.run_cli()
        self.assertEqual(code, 0, output)
        loaded, tables = self.manifest()
        self.assertEqual(loaded["validation"]["null_visit_links_total"], 2)
        self.assertEqual(tables["condition_occurrence"]["null_visit_links"], 1)
        self.assertEqual(tables["procedure_occurrence"]["null_visit_links"], 1)
        self.assertEqual(tables["condition_occurrence"]["failures"], [])
        path = os.path.join(self.version_dir, "conditions.csv")
        with open(path, "r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["visit_occurrence_id"], "")

    def test_missing_concept_labels_are_limitations_not_failures(self):
        """A referenced concept with no metadata is kept and reported."""
        self.install_psql_stub()
        code, output = self.run_cli()
        self.assertEqual(code, 0, output)
        loaded, tables = self.manifest()
        entry = tables[pilot.CONCEPT_KEY]
        self.assertEqual(entry["missing_labels"], 1)
        self.assertEqual(entry["failures"], [])
        self.assertEqual(loaded["validation"]["concepts_without_metadata"], 1)
        path = os.path.join(self.version_dir, "concepts.csv")
        with open(path, "r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        blank = [row for row in rows if not row["concept_name"]]
        self.assertEqual(len(blank), 1)
        self.assertEqual(blank[0]["concept_id"], "440001")
        self.assertEqual(len(rows), entry["expected_rows"])

    def test_non_read_only_transaction_fails(self):
        """Observed settings that are not read-only repeatable read fail."""
        self.install_psql_stub(metadata={"transaction_read_only": "off",
                                         "transaction_isolation":
                                         "read committed",
                                         "snapshot_id": ""})
        code, output = self.run_cli()
        self.assertEqual(code, 1, output)
        loaded, _ = self.manifest()
        reasons = loaded["validation"]["transaction_failures"]
        self.assertIn("transaction was not read only", reasons)
        self.assertIn("transaction isolation was not repeatable read",
                      reasons)
        self.assertIn("no transaction snapshot identifier was recorded",
                      reasons)
        self.assertEqual(loaded["status"], "incomplete")

    def test_assigned_transaction_id_is_metadata_not_a_failure(self):
        """An assigned transaction id is kept as metadata, not judged."""
        os.makedirs(self.version_dir)
        self.write_fixture(self.version_dir,
                           metadata={"assigned_xact_id": "3055"})
        records, summary = pilot.validate_snapshot(self.version_dir,
                                                   self.config, 2)
        self.assertEqual([record["status"] for record in records],
                         ["complete"] * 6)
        self.assertEqual(summary["transaction_failures"], [])
        self.assertEqual(summary["transaction"]["assigned_xact_id"], "3055")
        self.assertEqual(summary["status"], "complete")

    def test_selected_person_count_is_checked_separately(self):
        """A cohort size that differs from the request is reported."""
        self.install_psql_stub(counts={pilot.COHORT_KEY: 3})
        code, output = self.run_cli()
        self.assertEqual(code, 1, output)
        loaded, _ = self.manifest()
        failures = loaded["validation"]["selection_failures"]
        self.assertTrue(any("requested case count" in reason
                            for reason in failures), failures)
        self.assertEqual(loaded["validation"]["selected_persons_parsed"], 2)

    def test_psql_failure_leaves_an_explicitly_incomplete_version(self):
        """A non-zero psql exit leaves an incomplete, unpromoted version."""
        self.install_psql_stub(run_state="failed", exit_code=3)
        code, output = self.run_cli()
        self.assertEqual(code, 1, output)
        loaded, _ = self.manifest()
        self.assertEqual(loaded["run_state"], "failed")
        self.assertEqual(loaded["status"], "incomplete")
        self.assertEqual(loaded["execution"]["exit_code"], 3)
        for _, spec in pilot.all_specs(self.config):
            self.assertFalse(
                os.path.exists(os.path.join(self.version_dir, spec["file"])))
        self.assertIn("incomplete", output)

    # -- refusal and safety ------------------------------------------------

    def test_existing_version_directory_is_never_overwritten(self):
        """Extraction refuses an existing version and runs nothing."""
        os.makedirs(self.version_dir)
        marker = os.path.join(self.version_dir, "manifest.json")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write('{"status": "complete"}\n')
        self.install_psql_stub()
        code, output = self.run_cli()
        self.assertEqual(code, 1, output)
        self.assertIn("already exists", output)
        self.assertEqual(self.calls, [])
        with open(marker, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), '{"status": "complete"}\n')
        self.assertEqual(os.listdir(self.version_dir), ["manifest.json"])

    def test_unsafe_label_or_destination_creates_nothing(self):
        """Unsafe labels and out-of-bounds destinations are rejected."""
        self.install_psql_stub()
        for label in ["..", "a/b", "a\\b", ".git", "C:\\temp", "."]:
            code, output = self.run_cli(label=label)
            self.assertEqual(code, 1, label)
            self.assertIn("FAILED", output)
        outside = os.path.join(REPO_ROOT, "scratch-must-not-exist")
        code, output = self.run_cli(dest_root=outside)
        self.assertEqual(code, 1)
        self.assertIn("FAILED", output)
        self.assertFalse(os.path.exists(outside))
        self.assertEqual(self.calls, [])

    def test_non_loopback_host_and_wrong_database_are_rejected(self):
        """A remote host or another database never reaches extraction."""
        self.install_psql_stub()
        for overrides, expected in (
            ({"PGHOST": "10.0.0.5"}, "loopback"),
            ({"PGDATABASE": "postgres"}, "PGDATABASE"),
            ({"PGPORT": "not-a-port"}, "PGPORT"),
            ({"PGUSER": ""}, "PGUSER"),
            ({"PGPASSWORD": ""}, "PGPASSWORD"),
        ):
            self.set_environment(**overrides)
            code, output = self.run_cli()
            self.assertEqual(code, 1, output)
            self.assertIn(expected, output)
            self.assertFalse(os.path.exists(self.version_dir))
            self.set_environment()
        self.assertEqual(self.calls, [])

    def test_case_count_bounds_are_enforced(self):
        """Case counts outside the configured range are refused."""
        self.install_psql_stub()
        for cases in (0, -1, 101):
            code, output = self.run_cli(cases=cases)
            self.assertEqual(code, 1, output)
            self.assertIn("case count", output)
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(self.version_dir))

    def test_missing_psql_executable_is_refused(self):
        """A missing psql executable stops before anything is created."""
        os.remove(self.psql)
        self.install_psql_stub()
        code, output = self.run_cli()
        self.assertEqual(code, 1, output)
        self.assertIn("psql executable", output)
        self.assertFalse(os.path.exists(self.version_dir))

    # -- generated SQL -----------------------------------------------------

    def test_generated_sql_is_one_read_only_transaction(self):
        """The generated SQL reads only, in one transaction, with one CTE."""
        targets = pilot.copy_targets(self.config, self.version_dir,
                                     pilot.PARTIAL)
        sql = pilot.build_sql(self.config, 10, targets)
        lines = [line for line in sql.splitlines()
                 if line and not line.startswith("--")]
        self.assertEqual(lines[0], "\\set ON_ERROR_STOP on")
        self.assertIn("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;",
                      lines)
        self.assertEqual(lines[-1], "COMMIT;")
        self.assertEqual(sql.count("BEGIN"), 1)
        self.assertEqual(sql.count("COMMIT;"), 1)
        upper = sql.upper()
        for forbidden in ("INSERT ", "UPDATE ", "DELETE ", "CREATE ", "DROP ",
                          "ALTER ", "TRUNCATE", "GRANT ", "PROGRAM",
                          "PG_READ_FILE", "PG_LS_DIR", "REFRESH "):
            self.assertNotIn(forbidden, upper, forbidden)
        copies = [line for line in lines if line.startswith("\\copy ")]
        self.assertEqual(len(copies), 8)
        cohort = pilot.cohort_cte(self.config, 10)
        self.assertIn("LIMIT 10)", cohort)
        for line in copies:
            self.assertNotIn("\n", line)
            self.assertIn("WITH (FORMAT csv, HEADER true, ENCODING 'UTF8')",
                          line)
            self.assertIn(self.version_dir.replace("\\", "/"), line)
            self.assertIn(pilot.PARTIAL + "'", line)
        for line in copies[1:]:
            self.assertIn(cohort, line)
        self.assertIn("count(DISTINCT v.visit_occurrence_id) >= 2", cohort)
        self.assertIn("v.visit_concept_id IN (9201, 9203, 262)", cohort)

    def test_generated_sql_never_joins_events_to_the_cohort(self):
        """Extraction filters with a semi-join so row counts cannot change."""
        for name, spec in self.config["tables"].items():
            query = pilot.table_query(self.config, spec, 5)
            self.assertIn("WHERE t.person_id IN (SELECT person_id FROM "
                          "cohort)", query, name)
            self.assertNotIn(" JOIN ", query.upper(), name)
        concepts = pilot.concept_query(self.config, 5)
        self.assertIn("LEFT JOIN omopcdm.concept c ON c.concept_id = "
                      "r.concept_id", concepts)
        counts = pilot.counts_query(self.config, 5)
        for name in ("person", "visit_occurrence", "condition_occurrence",
                     "procedure_occurrence", "drug_exposure"):
            self.assertIn("AS %s" % name, counts)
        self.assertIn("(SELECT count(*) FROM cohort) AS selected_persons",
                      counts)

    def test_psql_environment_forces_read_only_without_credentials(self):
        """The psql environment forces read-only and drops credential files."""
        os.environ["PGPASSFILE"] = os.path.join(self.work, "never-read")
        self.addCleanup(os.environ.pop, "PGPASSFILE", None)
        env = pilot.psql_environment(self.config)
        self.assertIn("-c default_transaction_read_only=on", env["PGOPTIONS"])
        self.assertIn("-c statement_timeout=%d"
                      % self.config["statement_timeout_ms"], env["PGOPTIONS"])
        for name in pilot.CREDENTIAL_FILE_VARS:
            self.assertNotIn(name, env)
        self.assertEqual(env.get("PGPASSWORD"), SECRET)
        self.assertNotIn(SECRET, env["PGOPTIONS"])

    # -- verify-only -------------------------------------------------------

    def test_verify_only_confirms_and_then_detects_tampering(self):
        """Verification re-reads a completed snapshot and spots a change."""
        self.install_psql_stub()
        self.assertEqual(self.run_cli()[0], 0)
        calls_after_extract = len(self.calls)
        code, output = self.run_cli(extra=["--verify-only"])
        self.assertEqual(code, 0, output)
        report_path = os.path.join(
            self.version_dir, self.config["verification_report_name"])
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(report["files_changed_since_manifest"], [])
        self.assertEqual(report["validation"]["cases_requested"], 2)

        staged = os.path.join(self.version_dir, "drugs.csv")
        with open(staged, "a", encoding="utf-8") as handle:
            handle.write("tampered\n")
        code, output = self.run_cli(extra=["--verify-only"])
        self.assertEqual(code, 1, output)
        with open(report_path, "r", encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("drugs.csv", report["files_changed_since_manifest"])
        self.assertEqual(len(self.calls), calls_after_extract)
        self.assert_no_identifiers(output)

    def test_verify_only_on_a_missing_version_is_refused(self):
        """Verification of a directory that does not exist fails cleanly."""
        self.install_psql_stub()
        code, output = self.run_cli(extra=["--verify-only"])
        self.assertEqual(code, 1, output)
        self.assertIn("does not exist", output)
        self.assertEqual(self.calls, [])

    def test_verify_only_requires_a_valid_completed_manifest(self):
        """Absent, corrupt, incomplete or list-less manifests never verify."""
        self.install_psql_stub()
        name = self.config["manifest_name"]

        def absent(version):
            """Remove the manifest."""
            os.remove(os.path.join(version, name))

        def corrupt(version):
            """Replace the manifest with invalid JSON."""
            path = os.path.join(version, name)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not json")

        def incomplete(version):
            """Mark the recorded run as incomplete."""
            self.edit_manifest(version,
                               lambda loaded: loaded.update(status="x"))

        def listless(version):
            """Remove the recorded file hash list."""
            self.edit_manifest(version, lambda loaded: loaded.pop("files"))

        for label, damage, expected in (
            ("m-absent", absent, "manifest.json is missing or malformed"),
            ("m-corrupt", corrupt, "manifest.json is missing or malformed"),
            ("m-incomplete", incomplete,
             "manifest does not record a completed, promoted run"),
            ("m-listless", listless, "manifest has no file hash list"),
        ):
            version = self.extract_clean(label)
            damage(version)
            report = self.assert_verify_fails(label)
            self.assertIn(expected, report["integrity_problems"], label)

    def test_verify_only_requires_every_hash_record_and_file(self):
        """Omitted, duplicate or wrong records and missing files all fail."""
        self.install_psql_stub()

        def edit(change):
            """Return a damage step that edits the stored manifest."""
            return lambda version: self.edit_manifest(version, change)

        def remove(file_name):
            """Return a damage step that deletes one completed file."""
            return lambda version: os.remove(os.path.join(version, file_name))

        def omit_log_record(loaded):
            """Drop the stderr log hash record from the manifest."""
            loaded["files"] = [entry for entry in loaded["files"]
                               if entry["file"] != "psql_stderr.log"]

        def duplicate_record(loaded):
            """Repeat the first hash record in the manifest."""
            loaded["files"].append(dict(loaded["files"][0]))

        def wrong_hash(loaded):
            """Replace the recorded drugs.csv hash with another valid one."""
            for entry in loaded["files"]:
                if entry["file"] == "drugs.csv":
                    entry["sha256"] = "0" * 64

        def add_unrecorded(version):
            """Place a file the manifest does not record."""
            path = os.path.join(version, "unrecorded.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("synthetic\n")

        for label, damage, expected in (
            ("h-omitted", edit(omit_log_record),
             "no complete hash/size record for psql_stderr.log"),
            ("h-duplicate", edit(duplicate_record),
             "manifest file list has a malformed or duplicate entry"),
            ("h-wrong", edit(wrong_hash),
             "file changed since the manifest: drugs.csv"),
            ("h-no-sql", remove("pilot_extract.sql"),
             "recorded file is missing: pilot_extract.sql"),
            ("h-no-log", remove("psql_stdout.log"),
             "recorded file is missing: psql_stdout.log"),
            ("h-no-data", remove("persons.csv"),
             "recorded file is missing: persons.csv"),
            ("h-extra", add_unrecorded,
             "an unexpected file is present or recorded"),
        ):
            version = self.extract_clean(label)
            damage(version)
            report = self.assert_verify_fails(label)
            self.assertIn(expected, report["integrity_problems"], label)

    def test_verify_only_compares_the_current_configuration_hash(self):
        """A configuration differing from the recorded one never verifies."""
        self.install_psql_stub()
        self.extract_clean(LABEL)
        changed = os.path.join(self.work, "source_pilot_changed.json")
        with open(CONFIG_PATH, "rb") as handle:
            data = handle.read()
        with open(changed, "wb") as handle:
            handle.write(data + b"\n")
        report = self.assert_verify_fails(LABEL, config=changed)
        self.assertIn("current configuration SHA256 differs from the "
                      "recorded configuration", report["integrity_problems"])

    # -- Step 0.4 path and source checks ------------------------------------

    def write_config(self, change):
        """Write a changed copy of the pilot configuration into scratch."""
        config = copy.deepcopy(self.config)
        change(config)
        path = os.path.join(self.work, "source_pilot_variant.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle)
        return path

    def assert_refused_before_anything(self, code, output, expected):
        """Assert a refusal that created nothing and launched no psql."""
        self.assertEqual(code, 1, output)
        self.assertIn("FAILED", output)
        self.assertIn(expected, output)
        self.assertFalse(os.path.exists(self.version_dir))
        self.assertEqual(self.calls, [])
        self.assert_no_identifiers(output)

    def test_connection_redirects_and_bad_ports_are_refused(self):
        """PGHOSTADDR, services, conninfo, host lists and bad ports fail."""
        self.install_psql_stub()
        for overrides, expected in (
            ({"PGHOSTADDR": "10.0.0.5"}, "PGHOSTADDR"),
            ({"PGHOSTADDR": "127.0.0.1"}, "PGHOSTADDR"),
            ({"PGSERVICE": "synthetic"}, "PGSERVICE"),
            ({"PGSERVICEFILE": os.path.join(self.work, "never.conf")},
             "PGSERVICEFILE"),
            ({"PGDATABASE": "dbname=mimiciv_omop host=10.0.0.5"},
             "connection string"),
            ({"PGDATABASE": "postgresql://10.0.0.5/mimiciv_omop"},
             "connection string"),
            ({"PGHOST": "127.0.0.1,10.0.0.5"}, "loopback"),
            ({"PGHOST": "localhost.example.org"}, "loopback"),
            ({"PGPORT": "0"}, "PGPORT"),
            ({"PGPORT": "65536"}, "PGPORT"),
            ({"PGPORT": "05432"}, "PGPORT"),
            ({"PGPORT": "+5432"}, "PGPORT"),
            ({"PGPORT": " 5432"}, "PGPORT"),
        ):
            self.set_environment(**overrides)
            code, output = self.run_cli()
            self.assert_refused_before_anything(code, output, expected)
            self.assertNotIn("10.0.0.5", output)
            self.set_environment()

    def test_permitted_source_settings_are_accepted(self):
        """Loopback hosts, the fixed database and valid ports are accepted."""
        policy = self.config["policy"]
        for host in ("127.0.0.1", "::1", "localhost"):
            for port in ("1", "5432", "65535"):
                environ = {"PGHOST": host, "PGPORT": port,
                           "PGDATABASE": "mimiciv_omop", "PGUSER": USER,
                           "PGPASSWORD": SECRET}
                self.assertIsNone(guard.connection_problem(
                    environ, policy["allowed_hosts"],
                    policy["required_database"]), (host, port))
        self.assertIsNone(pilot.connection_problem(self.config, self.psql))
        self.assertIsNone(pilot.source_config_problem(self.config))

    def test_hostile_inherited_settings_never_reach_psql(self):
        """Inherited PGOPTIONS, redirects and credential files are dropped."""
        self.set_environment(
            PGOPTIONS="-c default_transaction_read_only=off",
            PGHOSTADDR="10.0.0.5", PGSERVICE="synthetic",
            PGSERVICEFILE=os.path.join(self.work, "never.conf"))
        self.addCleanup(self.restore_environment, "PGPASSFILE",
                        os.environ.get("PGPASSFILE"))
        os.environ["PGPASSFILE"] = os.path.join(self.work, "never-read")
        env = pilot.psql_environment(self.config)
        self.assertEqual(
            env["PGOPTIONS"],
            "-c default_transaction_read_only=on -c statement_timeout=%d "
            "-c lock_timeout=%d -c idle_in_transaction_session_timeout=%d"
            % (self.config["statement_timeout_ms"],
               self.config["lock_timeout_ms"],
               self.config["idle_in_transaction_timeout_ms"]))
        self.assertNotIn("=off", env["PGOPTIONS"])
        allowed = set(guard.PROCESS_VARIABLES + guard.CONNECTION_VARIABLES
                      + ("PGOPTIONS", "PGCONNECT_TIMEOUT",
                         "PGCLIENTENCODING", "PGAPPNAME"))
        self.assertLessEqual(set(env), allowed)
        for name in guard.REDIRECT_VARIABLES + ("PGPASSFILE",):
            self.assertNotIn(name, env)

    def test_psql_runs_with_fixed_flags_and_controlled_environment(self):
        """psql runs with -X -w ON_ERROR_STOP in the controlled environment."""
        os.makedirs(self.version_dir)
        seen = []

        def recording(command, **kwargs):
            """Record one psql invocation without launching any process."""
            seen.append((command, kwargs))
            return pilot.subprocess.CompletedProcess(command, 0, b"", b"")

        pilot.subprocess.run = recording  # restored by the setUp cleanup
        self.set_environment(PGOPTIONS="-c default_transaction_read_only=off")
        sql = os.path.join(self.version_dir, self.config["sql_name"])
        record = pilot.run_psql(self.config, self.version_dir, sql,
                                {"psql": self.psql, "subprocess_timeout": 5})
        self.assertEqual(record["run_state"], "finished")
        self.assertEqual(len(seen), 1)
        command, kwargs = seen[0]
        self.assertEqual(command, [self.psql, "-X", "-w", "-v",
                                   "ON_ERROR_STOP=1", "-f", sql])
        self.assertEqual(kwargs["env"], pilot.psql_environment(self.config))
        self.assertIn("default_transaction_read_only=on",
                      kwargs["env"]["PGOPTIONS"])
        self.assertNotIn("=off", kwargs["env"]["PGOPTIONS"])
        for argument in command:
            self.assertNotIn(SECRET, argument)
            self.assertNotIn(USER, argument)

    def test_configuration_cannot_widen_source_or_destination_policy(self):
        """Relaxed hosts, database, timeouts or roots are refused up front."""
        self.install_psql_stub()
        for change, expected in (
            (lambda c: c["policy"]["allowed_hosts"].append("10.0.0.5"),
             "allowed source hosts"),
            (lambda c: c["policy"].update(allowed_hosts=[]),
             "allowed source hosts"),
            (lambda c: c["policy"].update(required_database="postgres"),
             "required source database"),
            (lambda c: c.update(statement_timeout_ms=0),
             "statement_timeout_ms"),
            (lambda c: c.update(lock_timeout_ms=True), "lock_timeout_ms"),
            (lambda c: c["policy"].update(
                required_destination_root="G:\\Industry Dataset"),
             "private root"),
            (lambda c: c.update(destination_root="C:\\ehr-pilot"),
             "private root"),
        ):
            code, output = self.run_cli(config=self.write_config(change))
            self.assert_refused_before_anything(code, output, expected)

    def test_unapproved_destination_roots_fail_before_any_write(self):
        """Drive, UNC, relative, ambiguous and sibling roots are refused."""
        self.install_psql_stub()
        for root in ("C:\\ehr-pilot-scratch", "D:\\ehr-pilot-scratch",
                     "\\\\server\\share\\ehr", "\\\\?\\" + self.dest,
                     "relative\\dest", "W:relative",
                     self.dest.replace("\\", "/"), self.dest + "\\..\\dest",
                     self.dest + ".", self.dest + " ", self.dest + ":stream",
                     self.dest + "\\NUL", self.dest + "\\con.txt",
                     "W:\\INDUST~1\\ehr-evidence-workspace\\dest",
                     guard.PRIVATE_ROOT + "-old\\dest",
                     guard.PRIVATE_ROOT + "2\\dest",
                     guard.POLICY_ANCHOR + "\\other\\dest",
                     os.path.join(REPO_ROOT, "scratch-must-not-exist")):
            for extra in ([], ["--verify-only"]):
                code, output = self.run_cli(dest_root=root, extra=extra)
                self.assertEqual(code, 1, root)
                self.assertIn("FAILED", output, root)
        self.assertEqual(self.calls, [])
        self.assertEqual(os.listdir(self.dest), [])
        self.assertFalse(os.path.exists(
            os.path.join(REPO_ROOT, "scratch-must-not-exist")))

    def test_git_and_file_ancestors_fail_before_any_write(self):
        """A .git directory, a .git file or a file ancestor stops the run."""
        self.install_psql_stub()
        fake_repo = os.path.join(self.work, "fake-repo")
        os.makedirs(os.path.join(fake_repo, ".git"))
        worktree = os.path.join(self.work, "worktree")
        os.makedirs(worktree)
        with open(os.path.join(worktree, ".git"), "w",
                  encoding="utf-8") as handle:
            handle.write("gitdir: synthetic\n")
        blocker = os.path.join(self.work, "blocker")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("synthetic\n")
        for root, expected in ((fake_repo, ".git"), (worktree, ".git"),
                               (blocker, "not a directory")):
            code, output = self.run_cli(dest_root=root)
            self.assertEqual(code, 1, output)
            self.assertIn(expected, output)
            self.assertFalse(os.path.exists(os.path.join(root, LABEL)))
        self.assertEqual(self.calls, [])

    def test_verify_only_refuses_wrong_kind_leaves_without_writing(self):
        """A known file name occupied by a directory stops verify-only."""
        self.install_psql_stub()
        version = self.extract_clean(LABEL)
        manifest = os.path.join(version, self.config["manifest_name"])
        with open(manifest, "rb") as handle:
            before = handle.read()
        report = os.path.join(version,
                              self.config["verification_report_name"])
        os.makedirs(report + pilot.PARTIAL)
        code, output = self.run_cli(extra=["--verify-only"])
        self.assertEqual(code, 1, output)
        self.assertIn("not a regular file", output)
        self.assertFalse(os.path.exists(report))
        with open(manifest, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_junction_destination_is_refused_before_any_write(self):
        """A real junction in the destination stops extraction early."""
        self.install_psql_stub()
        target = os.path.join(self.work, "junction-target")
        os.makedirs(target)
        link = os.path.join(self.work, "junction")
        make_junction(self, link, target)
        code, output = self.run_cli(dest_root=link)
        self.assertEqual(code, 1, output)
        self.assertIn("reparse point", output)
        self.assertEqual(os.listdir(target), [])
        self.assertEqual(self.calls, [])

    def test_psql_must_be_a_local_psql_executable(self):
        """A differently named or UNC psql path is refused before writing."""
        self.install_psql_stub()
        other = os.path.join(self.work, "bin", "other.exe")
        with open(other, "wb") as handle:
            handle.write(b"synthetic placeholder; never executed\n")
        for path, expected in ((other, "psql.exe"),
                               ("\\\\server\\share\\psql.exe", "UNC"),
                               ("psql.exe", "absolute")):
            code, output = self.run_cli(extra=["--psql", path])
            self.assert_refused_before_anything(code, output, expected)

    # -- case-set cohorts --------------------------------------------------

    CASE_SET = "synthetic-2-v1"
    CASE_SPEC = {"purpose": "evaluation", "partition": "test", "size": 2,
                 "seed": 3, "min_visit_nodes": 2, "excluded_case_sets": [],
                 "statement": "Synthetic case set statement."}

    def register_case_set(self, cases=None):
        """Register an invented case set and pin its selection record."""
        path = os.path.join(self.work, "synthetic-selection.json")
        document = {"schema": registry.SELECTION_SCHEMA,
                    "case_set": self.CASE_SET, "spec": self.CASE_SPEC,
                    "cases": cases or [
                        {"case_number": 1, "patient_index": 3,
                         "graph_position": 3, "subject_id": 11},
                        {"case_number": 2, "patient_index": 5,
                         "graph_position": 6, "subject_id": 12}]}
        with open(path, "wb") as handle:
            handle.write(json.dumps(document).encode("utf-8"))
        with open(path, "rb") as handle:
            data = handle.read()
        pin = (len(data), hashlib.sha256(data).hexdigest())
        for patcher in (
                mock.patch.dict(registry.CASE_SETS,
                                {self.CASE_SET: self.CASE_SPEC}),
                mock.patch.dict(registry.PINS,
                                {self.CASE_SET: {"selection": pin}}),
                mock.patch.object(registry, "load_config", lambda: None),
                mock.patch.object(registry, "stage_path",
                                  lambda label, stage: path)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return path, pin

    def test_case_set_cohort_is_explicit_and_safe(self):
        """An explicit cohort names each subject once; nothing else passes."""
        cohort = pilot.cohort_cte(self.config, 2, (11, 12))
        self.assertEqual(cohort, "WITH cohort AS (SELECT p.person_id FROM "
                                 "omopcdm.person p WHERE "
                                 "p.person_source_value IN ('11', '12'))")
        targets = pilot.copy_targets(self.config, self.version_dir,
                                     pilot.PARTIAL)
        sql = pilot.build_sql(self.config, 2, targets, (11, 12))
        self.assertNotIn("LIMIT", sql)
        copies = [line for line in sql.splitlines()
                  if line.startswith("\\copy ")]
        for line in copies[1:]:
            self.assertIn(cohort, line)
        for subjects, cases in (((11, 12), 3), ((11, 11), 2), ((0, 12), 2),
                                ((True, 12), 2), (("11", 12), 2),
                                ([11, 12], 2), ((), 0)):
            with self.assertRaises(ValueError):
                pilot.cohort_cte(self.config, cases, subjects)

    def test_case_set_extraction_records_its_selection(self):
        """A case-set run extracts its subjects and records the selection."""
        path, pin = self.register_case_set()
        self.install_psql_stub()
        self.version_dir = os.path.join(self.dest, self.CASE_SET)
        code, output = self.run_cli(label=self.CASE_SET, cases=None,
                                    extra=["--case-set", self.CASE_SET])
        self.assertEqual(code, 0, output)
        self.assert_no_identifiers(output)
        manifest, _ = self.manifest()
        selection = manifest["selection"]
        self.assertEqual(selection["cases_requested"], 2)
        self.assertEqual(selection["case_set"]["selection_record"],
                         {"path": path, "size_bytes": pin[0],
                          "sha256": pin[1]})
        self.assertEqual(selection["purpose"], self.CASE_SPEC["statement"])
        self.assertEqual(manifest["limitations"][0],
                         self.CASE_SPEC["statement"])
        self.assertIn(self.CASE_SET, manifest["scope"]["selection"])
        with open(os.path.join(self.version_dir, self.config["sql_name"]),
                  "r", encoding="utf-8") as handle:
            self.assertIn("p.person_source_value IN ('11', '12')",
                          handle.read())
        code, output = self.run_cli(label=self.CASE_SET, cases=None,
                                    extra=["--case-set", self.CASE_SET,
                                           "--verify-only"])
        self.assertEqual(code, 0, output)
        self.edit_manifest(self.version_dir, lambda loaded: loaded[
            "selection"]["case_set"]["selection_record"].update(
                sha256="0" * 64))
        code, output = self.run_cli(label=self.CASE_SET, cases=None,
                                    extra=["--case-set", self.CASE_SET,
                                           "--verify-only"])
        self.assertEqual(code, 1, output)
        self.assertIn("pinned case-set selection", output)

    def test_case_set_selection_must_match_its_pin(self):
        """A changed or unregistered selection stops before any write."""
        path, _ = self.register_case_set()
        self.install_psql_stub()
        with open(path, "ab") as handle:
            handle.write(b" ")
        code, output = self.run_cli(label=self.CASE_SET, cases=None,
                                    extra=["--case-set", self.CASE_SET])
        self.assertEqual(code, 1)
        self.assertIn("stage_hash_mismatch:selection", output)
        self.assertEqual(self.calls, [])
        self.assertFalse(os.path.exists(os.path.join(self.dest,
                                                     self.CASE_SET)))
        code, output = self.run_cli(label="other-2-v1", cases=None,
                                    extra=["--case-set", "other-2-v1"])
        self.assertEqual(code, 1)
        self.assertIn("case_set_unknown", output)

    def test_case_set_fixes_count_and_label(self):
        """--case-set refuses an explicit case count or another label."""
        self.register_case_set()
        for label, cases in ((self.CASE_SET, 2), ("other-label", None)):
            code, output = self.run_cli(label=label, cases=cases,
                                        extra=["--case-set", self.CASE_SET])
            self.assertEqual(code, 1)
            self.assertIn("fixes the case count", output)


if __name__ == "__main__":
    unittest.main()
