"""Synthetic checks for ehr_evidence_app.sources.exports (Steps 0.3.1, 0.4).

Fixtures are inline synthetic CSVs with invented values. No source export,
database or patient content is read. Scratch directories are created only
under the configured workspace test temp directory. The legacy C: source path is
checked as a string only; it is never opened by these tests.
"""

import copy
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.storage import policy as guard  # noqa: E402
from ehr_evidence_app.sources import exports as staging  # noqa: E402

CONFIG_PATH = os.path.join(REPO_ROOT, "config", "source_exports.json")
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
TOKEN = "SYNTHID"
LABEL = "synthetic-v1"


def configured_test_temp():
    """Return the configured workspace test scratch directory (workspace-only rules)."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def make_junction(test, link, target):
    """Create a real directory junction inside scratch, or skip the test."""
    try:
        import _winapi
        _winapi.CreateJunction(target, link)
    except (ImportError, AttributeError, OSError) as error:
        test.skipTest("directory junctions are unavailable here: %s"
                      % type(error).__name__)
    test.addCleanup(os.rmdir, link)  # removes the junction, not its target


class StagingTestCase(unittest.TestCase):
    """Copy, reuse, mismatch and validation behaviour on synthetic files."""

    def setUp(self):
        """Create synthetic source and destination trees under workspace test temp."""
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
        self.work = tempfile.mkdtemp(prefix="step-0-3-1-", dir=root)
        self.addCleanup(self.remove_scratch)
        self.source = os.path.join(self.work, "source")
        self.dest = os.path.join(self.work, "dest")
        os.makedirs(self.source)
        os.makedirs(self.dest)
        self.config = staging.load_config(CONFIG_PATH)
        self.write_sources()

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the temp root.

        The recursive removal is guarded: the absolute scratch path is
        resolved and checked immediately before rmtree, so a wrong or
        relocated path is left alone rather than deleted.
        """
        target = os.path.abspath(self.work)
        root = os.path.abspath(self.temp_root)
        strictly_inside = os.path.normcase(target).startswith(
            os.path.normcase(root) + os.sep
        )
        if not strictly_inside:
            self.fail("refusing to remove %s: outside %s" % (target, root))
        shutil.rmtree(target, ignore_errors=True)

    def write_sources(self, drop_header=None, blank_identity=False):
        """Write one small synthetic CSV per configured table."""
        for name, spec in self.config["tables"].items():
            headers = list(spec["required_headers"])
            if drop_header and drop_header[0] == name:
                headers.remove(drop_header[1])
            rows = []
            for index in range(2):
                row = ["%s-%s-%d" % (TOKEN, field, index) for field in headers]
                rows.append(row)
            rows.append(list(rows[0]))  # exact duplicate, preserved as-is
            if blank_identity:
                identity = spec["required_identity_fields"][0]
                if identity in headers:
                    rows[1][headers.index(identity)] = "  "
            lines = [",".join(headers)] + [",".join(row) for row in rows]
            path = os.path.join(self.source, spec["file"])
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write("\n".join(lines) + "\n")

    def run_staging(self, source_dir=None, dest_root=None, label=LABEL,
                    config=CONFIG_PATH):
        """Run the staging CLI on the synthetic trees, capturing stdout."""
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = staging.main(
                [
                    "--config", config,
                    "--source-dir", source_dir or self.source,
                    "--dest-root", dest_root or self.dest,
                    "--version-label", label,
                ]
            )
        return code, stream.getvalue()

    def manifest(self):
        """Return the staged manifest as a table-name keyed dictionary."""
        path = os.path.join(self.dest, LABEL, "manifest.json")
        with open(path, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded, {entry["table"]: entry for entry in loaded["tables"]}

    def test_copy_verifies_and_reports_only_aggregates(self):
        """A first run copies, verifies and prints no row content."""
        code, output = self.run_staging()
        self.assertEqual(code, 0, output)
        loaded, tables = self.manifest()
        self.assertEqual(loaded["status"], "complete")
        self.assertEqual(len(tables), 5)
        for name, entry in tables.items():
            self.assertEqual(entry["status"], "complete", name)
            self.assertEqual(entry["action"], "copied", name)
            self.assertEqual(entry["copy_status"], "verified", name)
            self.assertEqual(
                entry["source_sha256"], entry["destination_sha256"], name
            )
            self.assertEqual(
                entry["source_size_bytes"], entry["destination_size_bytes"]
            )
            self.assertEqual(entry["rows_parsed"], 3, name)
            self.assertTrue(os.path.isfile(entry["destination_path"]))
        self.assertNotIn(TOKEN, output)
        self.assertEqual(loaded["totals"]["rows_parsed"], 15)
        self.assertEqual(loaded["completeness"]["status"], "unverified")

    def test_verified_rerun_reuses_without_rereading_source(self):
        """A matching rerun reuses the copy and leaves it untouched."""
        self.assertEqual(self.run_staging()[0], 0)
        _, first = self.manifest()
        staged = first["kg_concepts"]["destination_path"]
        stamp = os.stat(staged).st_mtime_ns
        code, output = self.run_staging()
        self.assertEqual(code, 0, output)
        _, second = self.manifest()
        for name, entry in second.items():
            self.assertEqual(entry["action"], "reused", name)
            self.assertEqual(entry["status"], "complete", name)
            self.assertFalse(entry["source_reread"], name)
        self.assertEqual(os.stat(staged).st_mtime_ns, stamp)

    def test_tampered_destination_is_not_overwritten(self):
        """Appended bytes are detected and the destination is left alone."""
        self.assertEqual(self.run_staging()[0], 0)
        _, first = self.manifest()
        staged = first["clinical_drugs"]["destination_path"]
        with open(staged, "ab") as handle:
            handle.write(b"tampered\n")
        with open(staged, "rb") as handle:
            tampered = handle.read()
        code, output = self.run_staging()
        self.assertEqual(code, 1, output)
        _, second = self.manifest()
        entry = second["clinical_drugs"]
        self.assertEqual(entry["status"], "incomplete")
        self.assertEqual(entry["action"], "kept-unchanged")
        self.assertNotEqual(
            entry["source_sha256"], entry["destination_sha256"]
        )
        with open(staged, "rb") as handle:
            self.assertEqual(handle.read(), tampered)
        self.assertEqual(second["kg_concepts"]["status"], "complete")

    def test_truncated_destination_is_detected(self):
        """A shortened destination fails verification on the next run."""
        self.assertEqual(self.run_staging()[0], 0)
        _, first = self.manifest()
        staged = first["clinical_visits"]["destination_path"]
        with open(staged, "rb+") as handle:
            handle.truncate(os.path.getsize(staged) - 5)
        self.assertEqual(self.run_staging()[0], 1)
        _, second = self.manifest()
        entry = second["clinical_visits"]
        self.assertEqual(entry["status"], "incomplete")
        self.assertLess(
            entry["destination_size_bytes"], entry["source_size_bytes"]
        )

    def test_missing_required_header_fails_that_table_only(self):
        """A missing required header is reported without stopping the rest."""
        self.write_sources(drop_header=("clinical_conditions", "person_id"))
        code, output = self.run_staging()
        self.assertEqual(code, 1, output)
        loaded, tables = self.manifest()
        entry = tables["clinical_conditions"]
        self.assertEqual(entry["status"], "incomplete")
        self.assertEqual(entry["copy_status"], "verified")
        self.assertEqual(entry["missing_required_headers"], ["person_id"])
        self.assertEqual(entry["identity_fields_absent"], ["person_id"])
        self.assertEqual(loaded["status"], "incomplete")
        self.assertEqual(tables["clinical_visits"]["status"], "complete")

    def test_blank_identifiers_are_counted_and_duplicates_preserved(self):
        """Blank identity fields are counted; duplicate rows are kept."""
        self.write_sources(blank_identity=True)
        code, output = self.run_staging()
        self.assertEqual(code, 0, output)
        _, tables = self.manifest()
        entry = tables["clinical_visits"]
        self.assertEqual(entry["rows_parsed"], 3)
        self.assertEqual(entry["rows_missing_identity"], 1)
        self.assertEqual(entry["missing_identity_by_field"]["subject_id"], 1)
        with open(entry["destination_path"], "r", encoding="utf-8") as handle:
            body = handle.read().splitlines()
        self.assertEqual(body[1], body[3])

    def test_interrupted_rerun_replaces_the_complete_manifest(self):
        """An interrupted rerun must not leave the prior manifest current."""
        self.assertEqual(self.run_staging()[0], 0)
        self.assertEqual(self.manifest()[0]["status"], "complete")
        original = staging.stage_table
        self.addCleanup(setattr, staging, "stage_table", original)
        seen = []

        def interrupting(name, spec, options, previous):
            """Stage normally until the third table, then interrupt."""
            seen.append(name)
            if len(seen) == 3:
                raise KeyboardInterrupt()
            return original(name, spec, options, previous)

        staging.stage_table = interrupting
        code, output = self.run_staging()
        self.assertEqual(code, 1, output)
        loaded, tables = self.manifest()
        self.assertEqual(loaded["status"], "incomplete")
        self.assertEqual(loaded["run_state"], "interrupted")
        self.assertEqual(loaded["totals"]["tables_complete"], 2)
        self.assertEqual(
            tables[seen[2]]["reason"], "interrupted before completion"
        )
        self.assertEqual(tables["kg_concepts"]["reason"], "not started")

    def test_alternate_source_with_matching_stat_is_rehashed(self):
        """A different source path with identical stat must be rehashed."""
        self.assertEqual(self.run_staging()[0], 0)
        alternate = os.path.join(self.work, "source-b")
        os.makedirs(alternate)
        for spec in self.config["tables"].values():
            origin = os.path.join(self.source, spec["file"])
            with open(origin, "rb") as handle:
                data = handle.read()
            if spec["file"] == "clinical_procedures.csv":
                data = data[:-2] + b"X" + data[-1:]  # same size, new bytes
            twin = os.path.join(alternate, spec["file"])
            with open(twin, "wb") as handle:
                handle.write(data)
            info = os.stat(origin)
            os.utime(twin, ns=(info.st_atime_ns, info.st_mtime_ns))

        code, output = self.run_staging(source_dir=alternate)
        self.assertEqual(code, 1, output)
        _, tables = self.manifest()
        for name, entry in tables.items():
            self.assertTrue(entry["source_reread"], name)
            self.assertEqual(
                entry["source_size_bytes"],
                os.path.getsize(os.path.join(alternate, entry["file"])),
            )
        changed = tables["clinical_procedures"]
        self.assertEqual(changed["status"], "incomplete")
        self.assertEqual(changed["action"], "kept-unchanged")
        self.assertEqual(tables["clinical_visits"]["status"], "complete")

    def test_unsafe_labels_and_destinations_are_rejected(self):
        """Unsafe components and out-of-bounds destinations create nothing."""
        for label in ["..", "a/b", "a\\b", ".git", "C:\\temp", "."]:
            code, output = self.run_staging(label=label)
            self.assertEqual(code, 1, label)
            self.assertIn("FAILED", output)

        outside = os.path.join(REPO_ROOT, "scratch-must-not-exist")
        code, output = self.run_staging(dest_root=outside)
        self.assertEqual(code, 1)
        self.assertIn("FAILED", output)
        self.assertFalse(os.path.exists(outside))

        same = os.path.join(self.dest, LABEL)
        code, output = self.run_staging(source_dir=same)
        self.assertEqual(code, 1)
        self.assertIn("same directory", output)

    def test_missing_source_file_is_reported_and_sanitised(self):
        """A missing source yields an incomplete record and no destination."""
        os.remove(os.path.join(self.source, "kg_concepts.csv"))
        code, output = self.run_staging()
        self.assertEqual(code, 1, output)
        loaded, tables = self.manifest()
        entry = tables["kg_concepts"]
        self.assertEqual(entry["status"], "incomplete")
        self.assertEqual(entry["copy_status"], "unverified")
        self.assertIn("FileNotFoundError", entry["reason"])
        self.assertFalse(
            os.path.exists(
                os.path.join(self.dest, "synthetic-v1", "kg_concepts.csv")
            )
        )
        self.assertEqual(loaded["totals"]["tables_complete"], 4)

    # -- Step 0.4 path and source checks ------------------------------------

    def write_config(self, change):
        """Write a changed copy of the staging configuration into scratch."""
        config = copy.deepcopy(self.config)
        change(config)
        path = os.path.join(self.work, "source_exports_variant.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle)
        return path

    def assert_refused(self, code, output, expected):
        """Assert a refusal that staged, created and copied nothing."""
        self.assertEqual(code, 1, output)
        self.assertIn("FAILED", output)
        self.assertIn(expected, output)
        self.assertEqual(os.listdir(self.dest), [])

    def test_unapproved_sources_are_refused_before_any_copy(self):
        """Only the legacy export or a source under the anchor may be read."""
        for source in ("D:\\omop_export", "C:\\other_export",
                       "C:\\omop_export\\nested", "C:\\omop_export2",
                       "\\\\server\\share\\omop_export", "relative\\source",
                       "C:omop_export", "C:/omop_export",
                       self.source + ".", self.source + ":stream"):
            code, output = self.run_staging(source_dir=source)
            self.assert_refused(code, output, "source directory")
        # String checks only: the real legacy export is never opened here.
        self.assertIsNone(guard.source_directory_problem("C:\\omop_export"))
        self.assertIsNone(guard.source_directory_problem("c:\\OMOP_EXPORT"))
        self.assertIsNone(guard.source_directory_problem(self.source))

    def test_configuration_cannot_relocate_the_private_root(self):
        """Relaxed configured roots are refused before anything is written."""
        for change in (
            lambda c: c["policy"].update(
                required_destination_root="G:\\Industry Dataset"),
            lambda c: c["policy"].update(required_destination_root="W:\\"),
            lambda c: c["policy"].update(
                required_destination_root="C:\\omop_export"),
            lambda c: c.update(destination_root="D:\\source-exports"),
        ):
            code, output = self.run_staging(config=self.write_config(change))
            self.assert_refused(code, output, "destination_root")
            self.assertTrue(
                "private root" in output or "empty component" in output,
                output)

    def test_unapproved_destination_roots_fail_before_any_copy(self):
        """Drive, UNC, relative, ambiguous and sibling roots are refused."""
        for root in ("C:\\staging", "D:\\staging", "\\\\server\\share\\x",
                     "\\\\?\\" + self.dest, "relative\\dest", "W:relative",
                     self.dest.replace("\\", "/"), self.dest + "\\..\\dest",
                     self.dest + ".", self.dest + " ", self.dest + ":ads",
                     self.dest + "\\NUL",
                     guard.PRIVATE_ROOT + "-old\\dest",
                     guard.PRIVATE_ROOT + "2",
                     guard.POLICY_ANCHOR + "\\other",
                     os.path.join(REPO_ROOT, "scratch-must-not-exist")):
            code, output = self.run_staging(dest_root=root)
            self.assertEqual(code, 1, root)
            self.assertIn("FAILED", output, root)
        self.assertEqual(os.listdir(self.dest), [])
        self.assertFalse(os.path.exists(
            os.path.join(REPO_ROOT, "scratch-must-not-exist")))

    def test_wrong_kind_leaves_and_git_ancestors_fail_before_any_copy(self):
        """An occupied .partial name or a .git ancestor stops staging."""
        version = os.path.join(self.dest, LABEL)
        os.makedirs(os.path.join(version, "clinical_visits.csv.partial"))
        code, output = self.run_staging()
        self.assertEqual(code, 1, output)
        self.assertIn("not a regular file", output)
        self.assertEqual(os.listdir(version),
                         ["clinical_visits.csv.partial"])

        fake_repo = os.path.join(self.work, "fake-repo")
        os.makedirs(os.path.join(fake_repo, ".git"))
        code, output = self.run_staging(dest_root=fake_repo)
        self.assertEqual(code, 1, output)
        self.assertIn(".git", output)
        self.assertEqual(os.listdir(fake_repo), [".git"])

    def test_junction_destination_is_refused_before_any_copy(self):
        """A real junction in the destination path stops staging."""
        target = os.path.join(self.work, "junction-target")
        os.makedirs(target)
        link = os.path.join(self.work, "junction")
        make_junction(self, link, target)
        code, output = self.run_staging(dest_root=link)
        self.assertEqual(code, 1, output)
        self.assertIn("reparse point", output)
        self.assertEqual(os.listdir(target), [])


if __name__ == "__main__":
    unittest.main()
