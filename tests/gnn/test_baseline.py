"""Synthetic checks for ehr_evidence_app.gnn.baseline (Step 1).

Fixtures are small synthetic files with invented values, written in a scratch
directory under the configured workspace test temp. The fixed upstream, baseline and
configuration roots are patched to scratch directories, so the real upstream
tree, its documentation and its assets are never opened. Git is simulated
with mocks; no git process is started.
"""

import copy
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.gnn import baseline as capture  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402

REAL_CONFIG = os.path.join(REPO_ROOT, "config", "inherited_baseline.json")
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
TOKEN = "SYNTHLEAK"
ENV_TOKEN = "SYNTHENVVALUE"
REVISION = "0123456789abcdef0123456789abcdef01234567"
FAKE_GIT = "C:\\synthetic\\git.exe"  # never executed; subprocess is mocked
REMOVE = object()

# Transcribed independently from the Step 1 assignment.
SUPPLIED_IDENTITIES = {
    "documentation": ("GNN_Replication_Documentation.md", 14277,
                      "C825B55A6F5A5A48C4F99B6511CBF88306C5CA54D9253450DD76D07"
                      "D29042E26"),
    "source_explain_patient": ("src\\gnn\\explain_patient.py", 8582,
                               "1799F536C69D2312A4277F867F7836D7CD56DDC54A448"
                               "F24924597199A3B87D9"),
    "source_train": ("src\\gnn\\train.py", 30887,
                     "EB412E5BEFF75371D30DE509AD7D8605F727BF9E25815D15D07CDAE"
                     "BEBDEA6DF"),
    "source_model": ("src\\gnn\\model.py", 22789,
                     "558E1B83CCF7667896A8736B4916E2CD230A9878D83BB9DC7F806A7"
                     "ACF75CFC6"),
    "source_build_patient_graphs": (
        "src\\preprocess\\build_patient_graphs.py", 17827,
        "175AAF7335B72C41ECC262D88203E8000323FE60451C92E419EFFD47111D1F70"),
    "canonical_checkpoint": (
        "data\\runs\\diag_kg\\checkpoints\\best_model.pt", 19523207,
        "5785E247D9A41D1D1534474D9771265EA0918767607C44B5DB9EA364D5326FE1"),
    "canonical_metrics": (
        "data\\runs\\diag_kg\\results\\metrics.json", 7752,
        "C4382CA881BBC728DBC94236C262E66001C0E97E7BCB6E1FCBE6A0D4166B7A9D"),
    "canonical_explanations": (
        "data\\runs\\diag_kg\\explanations.json", 11665,
        "1D28152A4491860E0AA4BD12B0E24A6EEC960CAE1E1DDE24A5FDEDBBB13C41A7"),
    "patient_graphs": (
        "data\\processed\\patient_graphs.pt", 756482261,
        "A650F130E08DCFA472CD50985DFCF9D140772AEBCA40AC0F9B62E8103049DE8C"),
    "concept_vocab": (
        "data\\processed\\concept_vocab.json", 449469,
        "4AC38FC78A194ABFD731CC38F178A83BB1C3C67782AA2734576FA41DBCC67A4E"),
    "label_vocab": (
        "data\\processed\\label_vocab.json", 2832,
        "32C1E2DFE9FC89363F56D0B9A84EDF828173EC1286DBC597CDB8FF10E1993A57"),
}


def configured_test_temp():
    """Return the configured workspace test scratch directory (workspace-only rules)."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def read_real_config():
    """Return the repository baseline configuration as a dictionary."""
    with open(REAL_CONFIG, "r", encoding="utf-8") as handle:
        return json.load(handle)


def make_junction(test, link, target):
    """Create a real directory junction inside scratch, or skip the test."""
    try:
        import _winapi
        _winapi.CreateJunction(target, link)
    except (ImportError, AttributeError, OSError) as error:
        test.skipTest("directory junctions are unavailable here: %s"
                      % type(error).__name__)
    test.addCleanup(os.rmdir, link)  # removes the junction, not its target


def set_asset(config, role, key, value):
    """Set one field of the configured asset with the given role."""
    for entry in config["assets"]:
        if entry["role"] == role:
            entry[key] = value
            return
    raise KeyError(role)


def completed(stdout, code=0):
    """Return a simulated finished git process."""
    return subprocess.CompletedProcess(["git"], code, stdout, None)


class RepositoryConfigurationTestCase(unittest.TestCase):
    """The committed configuration and fixed roots, checked lexically."""

    def test_configuration_matches_the_supplied_baseline_facts(self):
        """Roles, paths, sizes and hashes equal the operator's facts."""
        config = read_real_config()
        self.assertIsNone(capture.config_problem(config))
        self.assertEqual(capture.UPSTREAM_ROOT,
                         "G:\\Industry Dataset\\standardized-omop-gnn-7B82")
        self.assertEqual(capture.BASELINE_ROOT,
                         "G:\\Industry Dataset\\ehr-evidence-workspace"
                         "\\outputs\\baselines")
        self.assertEqual(capture.CANONICAL_RUN, "diag_kg")
        found = {entry["role"]: (entry["relative_path"],
                                 entry["expected_size_bytes"],
                                 entry["expected_sha256"])
                 for entry in config["assets"]}
        self.assertEqual(found, SUPPLIED_IDENTITIES)
        self.assertEqual(config["documentation"], {
            "expected_title": "GNN Replication \u2014 Project Documentation",
            "expected_last_updated": "2026-09-22"})
        self.assertEqual(config["metrics"]["expected_best_epoch"], 42)
        self.assertEqual(config["metrics"]["reported_test_values"], {
            "macro_auroc": 0.9062469508269994,
            "macro_auprc": 0.31301594151588924})

    @unittest.skipUnless(os.name == "nt", "Windows path policy")
    def test_fixed_roots_pass_the_policy(self):
        """The real fixed roots satisfy the fixed-root policy."""
        self.assertIsNone(capture.fixed_policy_problem())


@unittest.skipUnless(os.name == "nt", "the capture tool supports Windows only")
class CaptureTestCase(unittest.TestCase):
    """Capture behaviour on synthetic upstream fixtures under workspace test temp."""

    def setUp(self):
        """Create synthetic upstream, config and baseline roots in scratch."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(
            os.path.normcase(root).startswith(os.path.normcase("G:\\")),
            "test scratch space must stay in the workspace")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="step-1-", dir=root)
        self.addCleanup(self.remove_scratch)
        self.upstream = os.path.join(self.work, "upstream-gnn")
        self.baselines = os.path.join(self.work, "baselines")
        self.config_dir = os.path.join(self.work, "config")
        os.makedirs(self.upstream)
        os.makedirs(self.config_dir)
        self.patch_roots(self.upstream)
        self.real = read_real_config()
        self.config_path = os.path.join(self.config_dir, capture.CONFIG_NAME)
        self.output = os.path.join(self.baselines, "baseline-v1.json")
        self.write_fixtures()
        self.write_config()

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the temp root.

        The absolute scratch path is checked immediately before rmtree, so a
        wrong or relocated path is left alone rather than deleted.
        """
        target = os.path.abspath(self.work)
        root = os.path.abspath(self.temp_root)
        if not os.path.normcase(target).startswith(
                os.path.normcase(root) + os.sep):
            self.fail("refusing to remove %s: outside %s" % (target, root))
        shutil.rmtree(target, ignore_errors=True)

    def patch_roots(self, upstream):
        """Point the fixed roots of the capture module at scratch."""
        patcher = mock.patch.multiple(capture, UPSTREAM_ROOT=upstream,
                                      BASELINE_ROOT=self.baselines,
                                      CONFIG_DIR=self.config_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- fixtures -----------------------------------------------------------

    def asset(self, role):
        """Return the scratch path of the synthetic asset with this role."""
        for entry in self.real["assets"]:
            if entry["role"] == role:
                return os.path.join(self.upstream, entry["relative_path"])
        raise KeyError(role)

    def documentation_text(self, title=None, updated=None):
        """Return synthetic documentation with a title and update line."""
        if title is None:
            title = self.real["documentation"]["expected_title"]
        if updated is None:
            updated = self.real["documentation"]["expected_last_updated"]
        lines = []
        if title is not REMOVE:
            lines.append("# %s" % title)
        lines += ["", "Synthetic introduction %s." % TOKEN, ""]
        if updated is not REMOVE:
            lines.append("**Last updated:** %s" % updated)
        lines += ["", "## Section", "Synthetic body %s-2026-01-01." % TOKEN]
        return ("\n".join(lines) + "\n").encode("utf-8")

    def metrics_document(self, **changes):
        """Return synthetic metrics JSON bytes with optional field changes.

        Per-epoch history, per-label lists, text and invented per-patient
        rows carry the leak token so any copying would be detected.
        """
        reported = self.real["metrics"]["reported_test_values"]
        data = {
            "task": "diagnosis", "data_source": "omop", "no_kg": False,
            "best_epoch": self.real["metrics"]["expected_best_epoch"],
            "best_val": {"auroc_macro": 0.81, "note": TOKEN},
            "test": {"auroc_macro": reported["macro_auroc"],
                     "auprc_macro": reported["macro_auprc"], "loss": 0.25,
                     "per_label_auroc": [0.5, 0.75], "labels": [TOKEN],
                     "nested": {"inner": 1.0}},
            "history": [{"epoch": index, "note": TOKEN}
                        for index in range(50)],
            "patient_predictions": [{"person_id": TOKEN + "-1",
                                     "score": 0.7}],
        }
        for key, value in changes.items():
            if value is REMOVE:
                data.pop(key)
            else:
                data[key] = value
        return json.dumps(data).encode("utf-8")

    def write_asset(self, role, data):
        """Write synthetic bytes for one asset role."""
        path = self.asset(role)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)

    def write_fixtures(self):
        """Write every synthetic upstream asset."""
        for entry in self.real["assets"]:
            role = entry["role"]
            if role == capture.DOCUMENTATION_ROLE:
                data = self.documentation_text()
            elif role == capture.METRICS_ROLE:
                data = self.metrics_document()
            else:
                data = ("synthetic %s bytes %s\n" % (role, TOKEN)).encode()
            self.write_asset(role, data)

    def write_config(self, change=None, upstream=None):
        """Write a scratch configuration matching the current fixtures."""
        config = copy.deepcopy(self.real)
        config["upstream_root"] = upstream or self.upstream
        config["output_root"] = self.baselines
        for entry in config["assets"]:
            with open(self.asset(entry["role"]), "rb") as handle:
                data = handle.read()
            entry["expected_size_bytes"] = len(data)
            entry["expected_sha256"] = hashlib.sha256(data).hexdigest().upper()
        if change:
            change(config)
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
        return config

    # -- helpers ------------------------------------------------------------

    def run_capture(self, output=None, config=None):
        """Run the capture CLI, capturing stdout."""
        argv = ["--output", output or self.output]
        if config is not None:
            argv = ["--config", config] + argv
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = capture.main(argv)
        return code, stream.getvalue()

    def load_record(self, path=None):
        """Return a written baseline record."""
        with open(path or self.output, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def assert_refused(self, code, output, expected, created=False):
        """Assert a sanitised refusal that wrote no record or partial file."""
        self.assertEqual(code, 1, output)
        self.assertIn("FAILED", output)
        self.assertIn(expected, output)
        self.assertNotIn(TOKEN, output)
        self.assertFalse(os.path.exists(self.output))
        self.assertFalse(os.path.exists(self.output + capture.PARTIAL))
        if not created:
            self.assertFalse(os.path.exists(self.baselines))

    # -- successful capture -------------------------------------------------

    def test_valid_capture_records_identity_and_reported_values(self):
        """A valid capture records every asset and labels reported values."""
        code, output = self.run_capture()
        self.assertEqual(code, 0, output)
        self.assertIn("RESULT: COMPLETE", output)
        record = self.load_record()
        self.assertEqual(record["record_type"], "inherited_gnn_baseline")
        self.assertEqual(record["configuration"]["path"], self.config_path)
        self.assertEqual(sorted(asset["role"] for asset in record["assets"]),
                         sorted(capture.ASSET_ROLES))
        for asset in record["assets"]:
            with open(self.asset(asset["role"]), "rb") as handle:
                data = handle.read()
            self.assertEqual(asset["path"], self.asset(asset["role"]))
            self.assertEqual(asset["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(asset["size_bytes"], len(data))
            self.assertTrue(asset["exists"])
            self.assertEqual(asset["type"], "regular_file")
            self.assertTrue(asset["modified_utc"].endswith("+00:00"))
            self.assertTrue(asset["matches_expected_identity"])
        handling = {asset["role"]: asset["content_handling"]
                    for asset in record["assets"]}
        for role in ("canonical_checkpoint", "patient_graphs",
                     "canonical_explanations", "concept_vocab",
                     "label_vocab", "source_train"):
            self.assertEqual(handling[role], capture.HASH_ONLY, role)
        self.assertEqual(record["totals"]["assets"], 11)

        upstream = record["upstream"]
        self.assertEqual(upstream["root"], self.upstream)
        self.assertIsNone(upstream["revision"])
        self.assertIsNone(upstream["working_tree_status"])
        self.assertEqual(upstream["revision_status"],
                         "unavailable_no_git_metadata")
        self.assertFalse(upstream["git_invoked"])

        documentation = record["documentation"]
        self.assertEqual(documentation["title"],
                         self.real["documentation"]["expected_title"])
        self.assertEqual(documentation["last_updated"], "2026-09-22")
        self.assertEqual(documentation["sha256"], hashlib.sha256(
            self.documentation_text()).hexdigest())

        run = record["canonical_run"]
        self.assertEqual(run["name"], "diag_kg")
        self.assertEqual(run["provenance"], "inherited_reported")
        self.assertEqual(run["execution_status"],
                         "not_executed_by_downstream")
        self.assertEqual(run["reported_identity"], {
            "task": "diagnosis", "data_source": "omop", "no_kg": False,
            "best_epoch": 42, "history_length": 50})
        self.assertEqual(run["kg_determination"]["conclusion"],
                         "omop_plus_kg")
        self.assertEqual(run["kg_determination"]["basis"],
                         {"data_source": "omop", "no_kg": False})
        metrics = run["reported_metrics"]
        self.assertEqual(metrics["provenance"], "inherited_reported")
        self.assertEqual(metrics["execution_status"],
                         "not_executed_by_downstream")
        reported = self.real["metrics"]["reported_test_values"]
        self.assertEqual(metrics["test"]["values"], {
            "auprc_macro": reported["macro_auprc"],
            "auroc_macro": reported["macro_auroc"], "loss": 0.25})
        self.assertEqual(metrics["test"]["omitted_entry_count"], 3)
        self.assertEqual(metrics["best_val"]["values"], {"auroc_macro": 0.81})
        confirmed = metrics["confirmed_reported_test_values"]
        self.assertEqual(confirmed["macro_auroc"]["matched_fields"],
                         ["test.auroc_macro"])
        self.assertEqual(confirmed["macro_auprc"]["matched_fields"],
                         ["test.auprc_macro"])
        self.assertEqual(record["downstream_executed_results"]["status"],
                         "none")
        self.assertFalse(any(record["downstream_execution"].values()))
        self.assertEqual(os.listdir(self.baselines), ["baseline-v1.json"])

    def test_rerun_with_identical_inputs_is_idempotent(self):
        """A second identical capture leaves the record byte-for-byte."""
        self.assertEqual(self.run_capture()[0], 0)
        with open(self.output, "rb") as handle:
            first = handle.read()
        stamp = os.stat(self.output).st_mtime_ns
        code, output = self.run_capture()
        self.assertEqual(code, 0, output)
        self.assertIn("unchanged", output)
        with open(self.output, "rb") as handle:
            self.assertEqual(handle.read(), first)
        self.assertEqual(os.stat(self.output).st_mtime_ns, stamp)
        self.assertEqual(os.listdir(self.baselines), ["baseline-v1.json"])

    def test_conflicting_completed_output_is_never_overwritten(self):
        """Different content needs a new output path; the old one stays."""
        self.assertEqual(self.run_capture()[0], 0)
        with open(self.output, "rb") as handle:
            first = handle.read()
        self.write_config(lambda config: config["limitations"].append(
            "Synthetic extra limitation."))
        code, output = self.run_capture()
        self.assertEqual(code, 1, output)
        self.assertIn("different content", output)
        with open(self.output, "rb") as handle:
            self.assertEqual(handle.read(), first)
        self.assertFalse(os.path.exists(self.output + capture.PARTIAL))

        second = os.path.join(self.baselines, "baseline-v2.json")
        code, output = self.run_capture(output=second)
        self.assertEqual(code, 0, output)
        with open(second, "rb") as handle:
            self.assertNotEqual(handle.read(), first)

        foreign = os.path.join(self.baselines, "baseline-v3.json")
        with open(foreign, "wb") as handle:
            handle.write(b"{}\n")
        code, output = self.run_capture(output=foreign)
        self.assertEqual(code, 1, output)
        with open(foreign, "rb") as handle:
            self.assertEqual(handle.read(), b"{}\n")

    # -- asset identity -----------------------------------------------------

    def test_missing_asset_is_refused_before_any_output(self):
        """A missing file or directory stops the capture."""
        os.remove(self.asset("canonical_checkpoint"))
        code, output = self.run_capture()
        self.assert_refused(code, output, "canonical_checkpoint")
        self.assertIn("is missing", output)

        shutil.rmtree(os.path.dirname(self.asset("patient_graphs")))
        code, output = self.run_capture()
        self.assert_refused(code, output, "is missing")

    def test_wrong_asset_type_is_refused(self):
        """A directory for a file, or a file for a directory, is refused."""
        os.remove(self.asset("label_vocab"))
        os.makedirs(self.asset("label_vocab"))
        code, output = self.run_capture()
        self.assert_refused(code, output, "not a regular file")
        self.assertIn("label_vocab", output)
        os.rmdir(self.asset("label_vocab"))
        self.write_asset("label_vocab", b"synthetic label vocab\n")
        self.write_config()

        gnn = os.path.dirname(self.asset("source_train"))
        shutil.rmtree(gnn)
        with open(gnn, "wb") as handle:
            handle.write(b"not a directory\n")
        code, output = self.run_capture()
        self.assert_refused(code, output, "is not a directory")

    def test_wrong_size_or_hash_is_refused(self):
        """Expected size and SHA-256 mismatches stop the capture."""
        for change, expected in (
                (lambda config: set_asset(config, "source_model",
                                          "expected_sha256", "0" * 64),
                 "source_model SHA-256 differs"),
                (lambda config: set_asset(
                    config, "concept_vocab", "expected_size_bytes",
                    os.path.getsize(self.asset("concept_vocab")) + 1),
                 "concept_vocab size differs")):
            self.write_config(change)
            code, output = self.run_capture()
            self.assert_refused(code, output, expected)

        self.write_config()
        path = self.asset("canonical_explanations")
        with open(path, "rb") as handle:
            data = handle.read()
        with open(path, "wb") as handle:
            handle.write(data[:-2] + b"X" + data[-1:])  # same size
        code, output = self.run_capture()
        self.assert_refused(code, output,
                            "canonical_explanations SHA-256 differs")

    def test_change_while_hashing_is_detected(self):
        """A modification-time change during hashing fails the capture."""
        original = capture.sha256_stream
        target = os.path.normcase(self.asset("concept_vocab"))

        def touching(path):
            """Hash normally, then move the target's modification time."""
            result = original(path)
            if os.path.normcase(path) == target:
                info = os.stat(path)
                os.utime(path, ns=(info.st_atime_ns,
                                   info.st_mtime_ns + 2 * 10 ** 9))
            return result

        with mock.patch.object(capture, "sha256_stream", touching):
            code, output = self.run_capture()
        self.assert_refused(code, output,
                            "concept_vocab changed while it was being hashed")

    # -- documentation and metrics ------------------------------------------

    def test_documentation_metadata_mismatch_is_refused(self):
        """Wrong or missing title/date fails without echoing the text."""
        for data, expected in (
                (self.documentation_text(title="Other %s title" % TOKEN),
                 "title does not match"),
                (self.documentation_text(title=REMOVE),
                 "no level-one title"),
                (self.documentation_text(updated="2026-09-21"),
                 "date does not match"),
                (self.documentation_text(updated=REMOVE),
                 "no 'Last updated' date"),
                (b"\xff\xfe# not utf-8\n", "not valid UTF-8")):
            with self.subTest(expected=expected):
                self.write_asset(capture.DOCUMENTATION_ROLE, data)
                self.write_config()
                code, output = self.run_capture()
                self.assert_refused(code, output, expected)
                self.assertNotIn("Other", output)
                self.assertNotIn("2026-09-21", output)

    def test_metrics_identity_mismatch_is_refused(self):
        """Identity, history, epoch and field problems stop the capture."""
        many = {"metric%d" % index: 0.1 for index in range(300)}
        for changes, expected in (
                ({"task": "mortality"}, 'task is not "diagnosis"'),
                ({"data_source": "mimic"}, 'data_source is not "omop"'),
                ({"no_kg": 0}, "no_kg is not false"),
                ({"no_kg": "false"}, "no_kg is not false"),
                ({"history": [{}] * 49}, "history has 49 entries"),
                ({"history": {"loss": [0.1] * 50}}, "history is not a list"),
                ({"best_epoch": 7}, "best_epoch does not match"),
                ({"best_epoch": True}, "best_epoch does not match"),
                ({"best_val": REMOVE}, "lacks required fields: best_val"),
                ({"history": REMOVE}, "lacks required fields: history"),
                ({"test": "high"}, "test is neither a number nor an object"),
                ({"test": {"auroc_macro": 0.5}},
                 "reported test value for macro_auprc"),
                ({"test": many}, "more than the 256")):
            with self.subTest(fields=sorted(changes)):
                self.write_asset(capture.METRICS_ROLE,
                                 self.metrics_document(**changes))
                self.write_config()
                code, output = self.run_capture()
                self.assert_refused(code, output, expected)
                self.assertNotIn("mortality", output)
                self.assertNotIn("mimic", output)

    def test_no_kg_true_is_refused(self):
        """A run reporting no_kg=true is not accepted as the OMOP + KG run."""
        self.write_asset(capture.METRICS_ROLE,
                         self.metrics_document(no_kg=True))
        self.write_config()
        code, output = self.run_capture()
        self.assert_refused(code, output, "no_kg is not false")

    def test_truncated_or_invalid_metrics_json_is_refused(self):
        """Truncated, empty, non-object and non-UTF-8 metrics are refused."""
        full = self.metrics_document()
        for data, expected in (
                (full[:len(full) // 2], "truncated or not valid JSON"),
                (b"", "truncated or not valid JSON"),
                (b"not json", "truncated or not valid JSON"),
                (b"[1, 2]", "not a JSON object"),
                (b"\xff\xfe{}", "not valid UTF-8")):
            with self.subTest(expected=expected, size=len(data)):
                self.write_asset(capture.METRICS_ROLE, data)
                self.write_config()
                code, output = self.run_capture()
                self.assert_refused(code, output, expected)

    # -- git ------------------------------------------------------------------

    def test_missing_git_metadata_is_recorded_without_running_git(self):
        """No .git: null revision/state, unavailable status, git not run."""
        tripwire = AssertionError("git must not run")
        with mock.patch.object(capture.subprocess, "run",
                               side_effect=tripwire) as runner, \
                mock.patch.object(capture.shutil, "which",
                                  side_effect=tripwire):
            code, output = self.run_capture()
        self.assertEqual(code, 0, output)
        runner.assert_not_called()
        upstream = self.load_record()["upstream"]
        self.assertIsNone(upstream["revision"])
        self.assertIsNone(upstream["working_tree_status"])
        self.assertEqual(upstream["revision_status"],
                         "unavailable_no_git_metadata")
        self.assertIn("not claimed to be clean", upstream["note"])
        self.assertIn("unavailable_no_git_metadata", output)

    def test_git_failure_is_never_reported_clean(self):
        """Failed, timed-out or odd git output is unavailable, not clean."""
        os.makedirs(os.path.join(self.upstream, ".git"))
        for index, effect in enumerate((
                [completed(b"", 128), completed(b"", 128)],
                [completed((REVISION + "\n").encode()), completed(b"", 128)],
                [completed(b"not-a-revision\n"), completed(b"")],
                subprocess.TimeoutExpired(["git"], 30),
                OSError(2, "simulated"))):
            path = os.path.join(self.baselines, "git-failure-%d.json" % index)
            with self.subTest(case=index), \
                    mock.patch.object(capture.shutil, "which",
                                      return_value=FAKE_GIT), \
                    mock.patch.object(capture.subprocess, "run",
                                      side_effect=effect):
                code, output = self.run_capture(output=path)
                self.assertEqual(code, 0, output)
                upstream = self.load_record(path)["upstream"]
                self.assertEqual(upstream["revision_status"],
                                 "unavailable_git_error")
                self.assertIsNone(upstream["revision"])
                self.assertIsNone(upstream["working_tree_status"])
                self.assertIsNone(upstream["changed_entry_count"])

        path = os.path.join(self.baselines, "git-missing.json")
        with mock.patch.object(capture.shutil, "which", return_value=None):
            code, output = self.run_capture(output=path)
        self.assertEqual(code, 0, output)
        upstream = self.load_record(path)["upstream"]
        self.assertEqual(upstream["revision_status"], "unavailable_git_error")
        self.assertIsNone(upstream["working_tree_status"])
        self.assertFalse(upstream["git_invoked"])

    def test_git_success_uses_a_bounded_shell_free_call(self):
        """Git runs bounded, shell-free and without GIT_* redirects."""
        os.makedirs(os.path.join(self.upstream, ".git"))
        for status, tree, count in ((b"", "clean", 0),
                                    (b" M src/private-name.py\n?? new.txt\n",
                                     "modified", 2)):
            calls = []

            def fake_run(command, **kwargs):
                """Record the call and return a simulated git result."""
                calls.append((command, kwargs))
                if "rev-parse" in command:
                    return completed((REVISION + "\n").encode("ascii"))
                return completed(status)

            path = os.path.join(self.baselines, "git-%s.json" % tree)
            with mock.patch.dict(os.environ, {"GIT_DIR": "C:\\elsewhere",
                                              "GIT_WORK_TREE": "C:\\x"}), \
                    mock.patch.object(capture.shutil, "which",
                                      return_value=FAKE_GIT), \
                    mock.patch.object(capture.subprocess, "run", fake_run):
                code, output = self.run_capture(output=path)
            self.assertEqual(code, 0, output)
            upstream = self.load_record(path)["upstream"]
            self.assertEqual(upstream["revision_status"], "available")
            self.assertEqual(upstream["revision"], REVISION)
            self.assertEqual(upstream["working_tree_status"], tree)
            self.assertEqual(upstream["changed_entry_count"], count)
            self.assertEqual(len(calls), 2)
            for command, kwargs in calls:
                self.assertEqual(kwargs["cwd"], self.upstream)
                self.assertIs(kwargs["shell"], False)
                self.assertEqual(kwargs["timeout"],
                                 capture.GIT_TIMEOUT_SECONDS)
                self.assertNotIn("GIT_DIR", kwargs["env"])
                self.assertNotIn("GIT_WORK_TREE", kwargs["env"])
                self.assertEqual(kwargs["env"]["GIT_OPTIONAL_LOCKS"], "0")
                self.assertIn("--no-optional-locks", command)
            with open(path, "r", encoding="utf-8") as handle:
                self.assertNotIn("private-name", handle.read())

    def test_git_metadata_file_is_not_followed(self):
        """A .git file (gitlink) could redirect git, so git is not run."""
        with open(os.path.join(self.upstream, ".git"), "w") as handle:
            handle.write("gitdir: C:\\elsewhere\n")
        with mock.patch.object(capture.subprocess, "run",
                               side_effect=AssertionError("no git")) as runner:
            code, output = self.run_capture()
        self.assertEqual(code, 0, output)
        runner.assert_not_called()
        upstream = self.load_record()["upstream"]
        self.assertEqual(upstream["revision_status"],
                         "unavailable_unsupported_git_metadata")
        self.assertIsNone(upstream["working_tree_status"])

    # -- configuration and path policy ----------------------------------------

    def test_lexical_escapes_in_asset_paths_are_refused(self):
        """Relative paths cannot escape or alias the upstream tree."""
        for relative in ("..\\escape.txt", "data\\..\\..\\escape.txt",
                         "data/runs/diag_kg/x.json", "C:\\Windows\\win.ini",
                         "\\\\server\\share\\x", "src\\.git\\config",
                         "GNN.md:stream", "", "src\\NUL", "src\\train.py.",
                         "PROGRA~1\\x.py", ".\\GNN.md"):
            with self.subTest(relative=relative):
                self.write_config(lambda config: set_asset(
                    config, "source_train", "relative_path", relative))
                code, output = self.run_capture()
                self.assert_refused(code, output,
                                    "source_train relative_path")

    def test_configuration_cannot_redirect_or_weaken_the_baseline(self):
        """Roots, run, roles and identities in configuration are enforced."""
        real_upstream = "G:\\Industry Dataset\\standardized-omop-gnn-7B82"
        vocab = self.real["assets"][9]["relative_path"]
        for change, expected in (
                (lambda c: c.update(upstream_root=real_upstream),
                 "upstream_root must be exactly"),
                (lambda c: c.update(upstream_root=self.work),
                 "upstream_root must be exactly"),
                (lambda c: c.update(output_root=os.path.join(
                    self.work, "elsewhere")), "output_root must be exactly"),
                (lambda c: c.update(canonical_run="diag_nokg"),
                 "canonical_run must be diag_kg"),
                (lambda c: c.update(schema_version="2.0"), "schema_version"),
                (lambda c: c["assets"].pop(),
                 "lacks required roles: label_vocab"),
                (lambda c: c["assets"].append(dict(c["assets"][0])),
                 "more than once"),
                (lambda c: set_asset(c, "canonical_checkpoint",
                                     "relative_path",
                                     "data\\runs\\other\\best_model.pt"),
                 "must lie in data\\runs\\diag_kg"),
                (lambda c: set_asset(c, "label_vocab", "relative_path",
                                     vocab),
                 "repeats another asset's path"),
                (lambda c: set_asset(c, "label_vocab", "expected_sha256",
                                     "xyz"), "expected_sha256"),
                (lambda c: set_asset(c, "label_vocab", "expected_size_bytes",
                                     -1), "expected_size_bytes"),
                (lambda c: c["documentation"].update(
                    expected_last_updated="22/09/2026"), "YYYY-MM-DD"),
                (lambda c: c["metrics"].update(expected_best_epoch=True),
                 "expected_best_epoch")):
            with self.subTest(expected=expected):
                self.write_config(change)
                code, output = self.run_capture()
                self.assert_refused(code, output, expected)

        with open(self.config_path, "w", encoding="utf-8") as handle:
            handle.write("{")
        code, output = self.run_capture()
        self.assert_refused(code, output, "not valid UTF-8 JSON")

    def test_configuration_outside_the_config_directory_is_refused(self):
        """Only a configuration in the fixed config directory is read."""
        elsewhere = os.path.join(self.work, "other-config.json")
        shutil.copyfile(self.config_path, elsewhere)
        code, output = self.run_capture(config=elsewhere)
        self.assert_refused(code, output, "must be read from")
        code, output = self.run_capture(
            config="config\\inherited_baseline.json")
        self.assert_refused(code, output, "configuration path")

    def test_output_policy_is_checked_before_any_write(self):
        """Unsafe or misplaced outputs are refused and nothing is created."""
        outside = os.path.join(self.work, "elsewhere", "baseline.json")
        in_repo = os.path.join(REPO_ROOT, "baseline-must-not-exist.json")
        in_upstream = os.path.join(self.upstream, "baseline.json")
        for path, expected in (
                ("baseline.json", "output: path must be absolute"),
                (outside, "directly inside"),
                (os.path.join(self.baselines, "nested", "b.json"),
                 "directly inside"),
                (os.path.join(self.baselines, "baseline.txt"),
                 "must end with .json"),
                (self.output.replace("\\", "/"), "forward slashes"),
                (self.output + ":stream", "output:"),
                (os.path.join(self.baselines, "NUL.json"),
                 "reserved device name"),
                (os.path.join(self.baselines, ".git"), "output name"),
                (in_repo, "directly inside"),
                (in_upstream, "directly inside")):
            with self.subTest(path=path):
                code, output = self.run_capture(output=path)
                self.assert_refused(code, output, expected)
        for path in (outside, in_repo, in_upstream):
            self.assertFalse(os.path.exists(path), path)

    def test_existing_wrong_kind_and_stale_partial_are_refused(self):
        """A directory at the output or a stale partial stops the capture."""
        os.makedirs(self.output)
        code, output = self.run_capture()
        self.assertEqual(code, 1, output)
        self.assertIn("not a regular file", output)
        self.assertEqual(os.listdir(self.output), [])
        os.rmdir(self.output)

        partial = self.output + capture.PARTIAL
        with open(partial, "wb") as handle:
            handle.write(b"stale")
        code, output = self.run_capture()
        self.assertEqual(code, 1, output)
        self.assertIn("interrupted capture", output)
        self.assertFalse(os.path.exists(self.output))
        with open(partial, "rb") as handle:
            self.assertEqual(handle.read(), b"stale")

    def test_simulated_reparse_points_are_refused(self):
        """Simulated links on reads and writes stop the capture early."""
        real_lstat = os.lstat
        reparse = guard.REPARSE_POINT
        junction = SimpleNamespace(st_mode=stat.S_IFDIR | 0o777,
                                   st_file_attributes=0x10 | reparse)
        file_link = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777,
                                    st_file_attributes=0x20 | reparse)
        other = SimpleNamespace(st_mode=stat.S_IFREG | 0o666,
                                st_file_attributes=0x80 | reparse)
        targets = {}

        def fake_lstat(path, *args, **kwargs):
            """Return a simulated result for the selected scratch path."""
            key = os.path.normcase(os.fspath(path))
            if key in targets:
                return targets[key]
            return real_lstat(path, *args, **kwargs)

        for path, info, label in (
                (self.upstream, junction, "upstream root"),
                (os.path.join(self.upstream, "data"), junction,
                 "canonical_checkpoint"),
                (self.asset("patient_graphs"), file_link, "patient_graphs"),
                (self.asset("documentation"), other, "documentation"),
                (self.config_path, file_link, "configuration"),
                (self.baselines, junction, self.baselines)):
            with self.subTest(label=label):
                targets.clear()
                targets[os.path.normcase(path)] = info
                with mock.patch.object(capture.os, "lstat", fake_lstat):
                    code, output = self.run_capture()
                self.assert_refused(code, output, "reparse point")
                self.assertIn(label, output)

    def test_real_junction_upstream_root_is_refused(self):
        """A real junction standing in for the upstream root is refused."""
        link = os.path.join(self.work, "upstream-link")
        make_junction(self, link, self.upstream)
        self.patch_roots(link)
        self.write_config(upstream=link)
        code, output = self.run_capture()
        self.assert_refused(code, output, "reparse point")

    def test_failure_during_write_leaves_no_partial_or_record(self):
        """A failed atomic rename removes the partial and writes nothing."""
        with mock.patch.object(capture.os, "rename",
                               side_effect=OSError(28, "simulated")):
            code, output = self.run_capture()
        self.assert_refused(code, output, "OSError", created=True)
        self.assertEqual(os.listdir(self.baselines), [])

    def test_no_patient_content_or_environment_value_leaks(self):
        """Record and console hold no fixture content or environment value."""
        with mock.patch.dict(os.environ, {"EHR_SYNTH_SECRET": ENV_TOKEN,
                                          "PGPASSWORD": ENV_TOKEN}):
            code, output = self.run_capture()
        self.assertEqual(code, 0, output)
        with open(self.output, "rb") as handle:
            raw = handle.read()
        for token in (TOKEN, ENV_TOKEN, "person_id", "patient_predictions",
                      "Synthetic introduction", "Synthetic body",
                      "per_label_auroc", "inner"):
            self.assertNotIn(token.encode("utf-8"), raw, token)
            self.assertNotIn(token, output, token)


if __name__ == "__main__":
    unittest.main()
