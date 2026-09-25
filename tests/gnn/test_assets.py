"""Synthetic Step 15 tests for inherited source-asset validation.

Every upstream tree, configuration and checkpoint probe is a small synthetic
fixture written to workspace test scratch. The approved configuration digest, the
upstream root and the configuration/probe paths are pinned to each fixture
with mock.patch and restored afterwards. No real upstream asset, patient
record, tensor, model object, credential or service is used; the only real
files read are the committed repository configuration and probe records.
"""
import builtins
import contextlib
import hashlib
import io
import json
import os
import pickle
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
from ehr_evidence_app.gnn import assets as validator  # noqa: E402


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(os.path.join(REPO, "config", "paths.json"), "r",
              encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


SCRATCH = configured_test_temp()
REAL_CONFIG_SHA256 = validator.APPROVED_CONFIG_SHA256
REAL_UPSTREAM_ROOT = validator.UPSTREAM_ROOT
REAL_CONFIG_PATH = validator.CONFIG_PATH
REAL_PROBE_PATH = validator.PROBE_PATH
REAL_OUTPUT_PATH = validator.OUTPUT_PATH

RELATIVE = {
    "documentation": "GNN_Replication_Documentation.md",
    "source_explain_patient": "src\\gnn\\explain_patient.py",
    "source_train": "src\\gnn\\train.py",
    "source_model": "src\\gnn\\model.py",
    "source_build_patient_graphs": "src\\preprocess\\build_patient_graphs.py",
    "canonical_checkpoint": "data\\runs\\diag_kg\\checkpoints\\best_model.pt",
    "canonical_metrics": "data\\runs\\diag_kg\\results\\metrics.json",
    "canonical_explanations": "data\\runs\\diag_kg\\explanations.json",
    "patient_graphs": "data\\processed\\patient_graphs.pt",
    "concept_vocab": "data\\processed\\concept_vocab.json",
    "label_vocab": "data\\processed\\label_vocab.json",
}
# Synthetic opaque bytes: parsing them as JSON (or unpickling them) would fail.
CHECKPOINT_BYTES = b"\x80\x04SYNTHETIC-CHECKPOINT-OPAQUE\x00\xff"
GRAPH_BYTES = b"\x80\x04SYNTHETIC-GRAPH-OPAQUE-BYTES\x00\xfe"
CONTENT_MARKERS = ("SYNTHLABEL", "SYNTHCONCEPT", "SYNTHETIC-EXPLANATION",
                   "SYNTHETIC-CHECKPOINT", "SYNTHETIC-GRAPH",
                   "synthetic source", "Synthetic documentation")


def encode(value):
    """Return compact UTF-8 JSON bytes for a synthetic document."""
    return json.dumps(value, sort_keys=True).encode("utf-8")


def metrics_object():
    """Return a synthetic canonical diag_kg metrics object."""
    return {"task": "diagnosis", "data_source": "omop", "no_kg": False,
            "best_epoch": 42, "best_val": {"auroc_macro": 0.5},
            "test": {"auroc_macro": validator.AUROC_MACRO,
                     "auprc_macro": validator.AUPRC_MACRO},
            "history": [{"epoch": index} for index in range(50)]}


def default_contents():
    """Return synthetic bytes for all eleven roles."""
    return {
        "documentation": b"# Synthetic documentation\n",
        "source_explain_patient": b"# synthetic source explain\n",
        "source_train": b"# synthetic source train\n",
        "source_model": b"# synthetic source model\n",
        "source_build_patient_graphs": b"# synthetic source graphs\n",
        "canonical_checkpoint": CHECKPOINT_BYTES,
        "canonical_metrics": encode(metrics_object()),
        "canonical_explanations": encode(
            [{"target_index": 0, "detail": "SYNTHETIC-EXPLANATION-VALUE"},
             {"target_index": 1, "detail": "SYNTHETIC-EXPLANATION-OTHER"}]),
        "patient_graphs": GRAPH_BYTES,
        "concept_vocab": encode(
            {"domain_a": {"SYNTHCONCEPT1": 0, "SYNTHCONCEPT2": 1},
             "domain_b": {"SYNTHCONCEPT3": 0},
             "domain_c": {"SYNTHCONCEPT4": "SYNTHCONCEPT-NAME"}}),
        "label_vocab": encode(["SYNTHLABEL%03d" % index
                               for index in range(275)]),
    }


def digest(data):
    """Return the uppercase SHA-256 of bytes."""
    return hashlib.sha256(data).hexdigest().upper()


def fake_stat(mode, attributes=0, size=0, mtime=0):
    """Return fake lstat information (never read from disk)."""
    return types.SimpleNamespace(st_mode=mode, st_file_attributes=attributes,
                                 st_size=size, st_mtime_ns=mtime)


@contextlib.contextmanager
def open_spy():
    """Record every (normalised path, mode) opened through the builtin."""
    opened = []
    real_open = builtins.open

    def spy(file, mode="r", *args, **kwargs):
        """Record the call and delegate to the real open."""
        opened.append((os.path.normcase(os.path.abspath(os.fspath(file))),
                       mode))
        return real_open(file, mode, *args, **kwargs)

    with mock.patch.object(builtins, "open", spy):
        yield opened


@contextlib.contextmanager
def replaced_lstat(target, info):
    """Return info from lstat for target only; delegate everything else."""
    real = os.lstat
    key = os.path.normcase(target)

    def fake(path, *args, **kwargs):
        """Substitute the fake result for the target path."""
        if os.path.normcase(os.fspath(path)) == key:
            return info
        return real(path, *args, **kwargs)

    with mock.patch.object(validator.os, "lstat", fake):
        yield


class Tree(object):
    """One synthetic upstream tree with its configuration and probe."""

    def __init__(self, base):
        """Name the fixture paths beneath base."""
        self.root = os.path.join(base, "upstream")
        self.config_path = os.path.join(base, "inherited_baseline.json")
        self.probe_path = os.path.join(base, "checkpoint_probe.json")
        self.contents = default_contents()

    def path(self, role):
        """Return the absolute fixture path of one role."""
        return self.root + "\\" + RELATIVE[role]

    def config(self):
        """Return a synthetic Step 1 configuration for the current contents."""
        return {
            "schema_version": "1.0", "step": "1",
            "purpose": "synthetic fixture",
            "upstream_root": self.root,
            "output_root": "unused",
            "canonical_run": "diag_kg",
            "documentation": {"expected_title": "Synthetic",
                              "expected_last_updated": "2026-01-01"},
            "metrics": {"expected_best_epoch": 42,
                        "reported_test_values": {
                            "macro_auroc": validator.AUROC_MACRO,
                            "macro_auprc": validator.AUPRC_MACRO}},
            "assets": [{"role": role, "relative_path": RELATIVE[role],
                        "expected_size_bytes": len(self.contents[role]),
                        "expected_sha256": digest(self.contents[role])}
                       for role in validator.ROLES],
            "limitations": ["synthetic"],
        }

    def probe(self):
        """Return a probe record matching the synthetic checkpoint."""
        return {
            "schema_version": "1.0", "step": "15",
            "record_type": "checkpoint_compatibility_probe",
            "purpose": "synthetic probe",
            "canonical_run": "diag_kg",
            "checkpoint_role": "canonical_checkpoint",
            "checkpoint_sha256": digest(self.contents["canonical_checkpoint"]),
            "checkpoint_size_bytes": len(self.contents["canonical_checkpoint"]),
            "output_heads": {"graph_head": 275, "seq_head": 275},
            "label_count": 275,
            "label_order_status": "dimensional_compatibility_only",
            "limitations": ["synthetic limitation"],
        }

    def write_assets(self):
        """Write every role's bytes beneath the upstream root."""
        for role, data in self.contents.items():
            path = self.path(role)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(data)


class ScratchCase(unittest.TestCase):
    """Build fresh fixtures under workspace scratch and pin the validator to them."""

    def setUp(self):
        """Create a per-test scratch directory under the workspace."""
        if not os.path.isdir(SCRATCH):
            self.skipTest("configured test scratch is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self._directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(self._directory.cleanup)
        self.count = 0

    def build(self, contents=None, config=None, probe=None, config_raw=None,
              probe_raw=None):
        """Write one fixture, pin the validator to it and return the tree.

        contents, config and probe are optional callables mutating the
        default role bytes, configuration dict and probe dict in place;
        config_raw/probe_raw replace the written bytes entirely.
        """
        self.count += 1
        base = os.path.join(self._directory.name, "case%03d" % self.count)
        os.makedirs(base)
        tree = Tree(base)
        if contents:
            contents(tree.contents)
        document = tree.config()
        probe_document = tree.probe()
        if config:
            config(document)
        if probe:
            probe(probe_document)
        tree.write_assets()
        raw = config_raw if config_raw is not None else encode(document)
        with open(tree.config_path, "wb") as handle:
            handle.write(raw)
        with open(tree.probe_path, "wb") as handle:
            handle.write(probe_raw if probe_raw is not None
                         else encode(probe_document))
        patcher = mock.patch.multiple(
            validator, UPSTREAM_ROOT=tree.root, CONFIG_PATH=tree.config_path,
            PROBE_PATH=tree.probe_path,
            APPROVED_CONFIG_SHA256=hashlib.sha256(raw).hexdigest(),
            OUTPUT_PATH=os.path.join(base, "out", "source-assets.json"))
        patcher.start()
        self.addCleanup(patcher.stop)
        return tree

    def assert_no_asset_opened(self, tree, opened):
        """Assert that no path beneath the fixture upstream root was opened."""
        root = os.path.normcase(tree.root) + os.sep
        self.assertFalse([path for path, _ in opened if path.startswith(root)])


class ValidFixtureTests(ScratchCase):
    """A matching synthetic tree passes and emits metadata only."""

    def test_valid_tree_passes_with_metadata_only(self):
        """All eleven roles are hashed and the four JSON roles are checked."""
        tree = self.build()
        record = validator.validate_assets()
        self.assertEqual(record["status"], "PASS", record["findings"])
        self.assertEqual(record["findings"], [])
        self.assertEqual(record["asset_count"], 11)
        self.assertEqual([item["role"] for item in record["assets"]],
                         list(validator.ROLES))
        for item in record["assets"]:
            self.assertEqual(item["sha256"],
                             digest(tree.contents[item["role"]]))
            self.assertEqual(item["content_handling"],
                             "bounded_json_structure_checked"
                             if item["role"] in validator.JSON_ROLES
                             else "bytes_hashed_only")
        self.assertEqual(record["label_count"], 275)
        self.assertEqual(record["label_vocab"],
                         {"status": "valid", "entry_count": 275})
        self.assertEqual(record["concept_vocab"]["domain_entry_counts"],
                         {"domain_a": 2, "domain_b": 1, "domain_c": 1})
        self.assertEqual(record["canonical_metrics"]["status"], "valid")
        self.assertEqual(record["canonical_explanations"],
                         {"status": "valid", "entry_count": 2})
        self.assertEqual(record["checkpoint_probe"]["status"], "valid")
        self.assertTrue(record["label_dimension_match"])
        self.assertFalse(record["artifact_upload"])
        text = json.dumps(record)
        for marker in CONTENT_MARKERS:
            self.assertNotIn(marker, text)
        self.assertNotIn("target_index", text)

    def test_label_ordering_is_not_claimed(self):
        """Only dimensional compatibility is recorded; index order is not."""
        self.build()
        record = validator.validate_assets()
        self.assertEqual(record["label_index_correspondence"], "not_established")
        self.assertEqual(record["checkpoint_probe"]["label_order_status"],
                         "dimensional_compatibility_only")
        for key in record:
            self.assertNotIn("order", key)
        self.assertNotIn("label_order_compatibility", json.dumps(record))

    def test_manifest_bytes_are_deterministic(self):
        """Two runs write byte-identical, sorted, LF-terminated manifests."""
        self.build()
        outputs = []
        for _ in range(2):
            validator.write_record(validator.validate_assets(),
                                   validator.OUTPUT_PATH)
            with open(validator.OUTPUT_PATH, "rb") as handle:
                outputs.append(handle.read())
        self.assertEqual(outputs[0], outputs[1])
        self.assertTrue(outputs[0].endswith(b"\n"))
        self.assertNotIn(b"\r\n", outputs[0])
        self.assertFalse(os.path.exists(validator.OUTPUT_PATH + ".partial"))
        record = json.loads(outputs[0])
        self.assertEqual(outputs[0].decode("utf-8"), validator.serialise(record))

    def test_existing_record_is_never_replaced(self):
        """An identical record stays untouched; a different one is refused."""
        self.build()
        record = validator.validate_assets()
        self.assertEqual(validator.write_record(record, validator.OUTPUT_PATH),
                         "written")
        self.assertEqual(validator.write_record(record, validator.OUTPUT_PATH),
                         "unchanged")
        with open(validator.OUTPUT_PATH, "rb") as handle:
            original = handle.read()
        changed = dict(record, asset_count=record["asset_count"] + 1)
        with self.assertRaises(validator.RecordConflict):
            validator.write_record(changed, validator.OUTPUT_PATH)
        with open(validator.OUTPUT_PATH, "rb") as handle:
            self.assertEqual(handle.read(), original)
        self.assertFalse(os.path.exists(validator.OUTPUT_PATH + ".partial"))
        stdout = io.StringIO()
        with mock.patch.object(validator, "validate_assets",
                               return_value=changed), \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(validator.main(["--write"]), 2)
        self.assertEqual(json.loads(stdout.getvalue()),
                         {"status": "OPERATIONAL",
                          "findings": ["output_conflict"]})
        with open(validator.OUTPUT_PATH, "rb") as handle:
            self.assertEqual(handle.read(), original)

    def test_cli_prints_summary_only_and_writes_on_request(self):
        """The CLI prints a sanitized summary and writes only with --write."""
        self.build()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(validator.main([]), 0)
        self.assertFalse(os.path.exists(validator.OUTPUT_PATH))
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary, {"status": "PASS", "asset_count": 11,
                                   "label_count": 275, "findings": []})
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(validator.main(["--write"]), 0)
        self.assertTrue(os.path.isfile(validator.OUTPUT_PATH))
        for marker in CONTENT_MARKERS:
            self.assertNotIn(marker, stdout.getvalue())

    def test_cli_failure_exit_code(self):
        """A failing validation exits 1 with finding codes only."""
        self.build(contents=lambda c: c.update(label_vocab=encode([1, 2])))
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(validator.main([]), 1)
        summary = json.loads(stdout.getvalue())
        self.assertEqual(summary["status"], "FAIL")
        self.assertEqual(summary["findings"], ["label_count_mismatch"])
        self.assertEqual(summary["label_count"], 2)


class HashOnlyTests(ScratchCase):
    """Checkpoint and graph bytes are hashed and never deserialized."""

    def test_checkpoint_and_graphs_are_only_hashed(self):
        """Opaque bytes are streamed once in binary mode and never parsed."""
        tree = self.build()
        parsed = []
        real_parse = validator.parse_json_bytes

        def parse_spy(raw, **kwargs):
            """Record every byte string handed to the JSON parser."""
            parsed.append(raw)
            return real_parse(raw, **kwargs)

        def forbidden(*args, **kwargs):
            """Fail if any deserializer is reached."""
            raise AssertionError("deserializer called")

        modules_before = set(sys.modules)
        with mock.patch.object(validator, "parse_json_bytes", parse_spy), \
                mock.patch.object(pickle, "load", forbidden), \
                mock.patch.object(pickle, "loads", forbidden), \
                mock.patch.object(pickle, "Unpickler", forbidden), \
                open_spy() as opened:
            record = validator.validate_assets()
        self.assertEqual(record["status"], "PASS", record["findings"])
        for role, data in (("canonical_checkpoint", CHECKPOINT_BYTES),
                           ("patient_graphs", GRAPH_BYTES)):
            with self.subTest(role=role):
                self.assertNotIn(data, parsed)
                calls = [mode for path, mode in opened
                         if path == os.path.normcase(tree.path(role))]
                self.assertEqual(calls, ["rb"])
        json_bytes = [tree.contents[role] for role in validator.JSON_ROLES]
        self.assertEqual(sorted(parsed[1:-1]), sorted(json_bytes))
        self.assertEqual(len(parsed), 6)
        for name in ("torch", "torch_geometric", "numpy"):
            if name not in modules_before:
                self.assertNotIn(name, sys.modules)
        self.assertNotIn("pickle", vars(validator))
        self.assertNotIn("torch", vars(validator))

    def test_each_asset_is_opened_once(self):
        """Every upstream file is opened exactly once; none is reopened."""
        tree = self.build()
        with open_spy() as opened:
            validator.validate_assets()
        for role in validator.ROLES:
            with self.subTest(role=role):
                self.assertEqual(
                    [path for path, _ in opened
                     if path == os.path.normcase(tree.path(role))],
                    [os.path.normcase(tree.path(role))])


class ConfigurationTests(ScratchCase):
    """The configuration is authoritative and checked before any asset."""

    def run_config(self, **kwargs):
        """Build a fixture, validate it and return (tree, record, opened)."""
        tree = self.build(**kwargs)
        with open_spy() as opened:
            record = validator.validate_assets()
        return tree, record, opened

    def test_tampered_configuration_fails_before_assets(self):
        """One changed byte after pinning is an identity failure."""
        tree = self.build()
        with open(tree.config_path, "rb") as handle:
            raw = handle.read()
        with open(tree.config_path, "wb") as handle:
            handle.write(raw.replace(b'"step": "1"', b'"step": "2"', 1))
        with open_spy() as opened:
            record = validator.validate_assets()
        self.assertEqual(record["findings"], ["configuration_identity_mismatch"])
        self.assertEqual(record["asset_count"], 0)
        self.assert_no_asset_opened(tree, opened)

    def test_unpinned_fixture_fails_real_pin(self):
        """A fixture never matches the real approved configuration hash."""
        tree = self.build()
        with mock.patch.object(validator, "APPROVED_CONFIG_SHA256",
                               REAL_CONFIG_SHA256), open_spy() as opened:
            record = validator.validate_assets()
        self.assertEqual(record["findings"], ["configuration_identity_mismatch"])
        self.assert_no_asset_opened(tree, opened)

    def test_scope_changes_with_matching_hash_fail(self):
        """Wrong step, run, upstream root or metric expectations fail."""
        def setter(key, value):
            """Return a mutation assigning one top-level configuration key."""
            return lambda config: config.__setitem__(key, value)

        def metrics(**changes):
            """Return a mutation replacing metric expectation fields."""
            def mutate(config):
                """Apply the metric changes."""
                config["metrics"].update(changes)
            return mutate

        cases = {
            "step": setter("step", "2"),
            "step_int": setter("step", 1),
            "run": setter("canonical_run", "diag_no_kg"),
            "root_other": setter("upstream_root", REAL_UPSTREAM_ROOT),
            "root_case": lambda c: c.__setitem__("upstream_root",
                                                 c["upstream_root"].upper()),
            "root_missing": lambda c: c.pop("upstream_root"),
            "best_epoch": metrics(expected_best_epoch=41),
            "best_epoch_float": metrics(expected_best_epoch=42.0),
            "reported_value": metrics(reported_test_values={
                "macro_auroc": 0.9, "macro_auprc": validator.AUPRC_MACRO}),
            "reported_extra": metrics(reported_test_values={
                "macro_auroc": validator.AUROC_MACRO,
                "macro_auprc": validator.AUPRC_MACRO, "extra": 1.0}),
            "metrics_missing": lambda c: c.pop("metrics"),
            "metrics_list": setter("metrics", []),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                tree, record, opened = self.run_config(config=mutate)
                self.assertEqual(record["findings"],
                                 ["configuration_scope_mismatch"])
                self.assertEqual(record["status"], "FAIL")
                self.assert_no_asset_opened(tree, opened)

    def test_malformed_configuration_documents_fail(self):
        """Non-object, invalid, NaN and duplicate-key documents fail."""
        cases = {
            "array": (b"[]", "configuration_scope_mismatch"),
            "invalid": (b"{", "configuration_invalid"),
            "nan": (b'{"step": NaN}', "configuration_invalid"),
            "duplicate": (b'{"step": "1", "step": "1"}', "configuration_invalid"),
            "not_utf8": (b"\xff\xfe{}", "configuration_invalid"),
        }
        for label, (raw, finding) in cases.items():
            with self.subTest(case=label):
                tree, record, opened = self.run_config(config_raw=raw)
                self.assertEqual(record["findings"], [finding])
                self.assert_no_asset_opened(tree, opened)

    def test_missing_duplicate_unknown_roles_fail(self):
        """The exact eleven-role set is required, each role once."""
        def drop(role):
            """Return a mutation removing one role."""
            return lambda c: c.__setitem__("assets", [
                item for item in c["assets"] if item["role"] != role])

        def rename(index, value):
            """Return a mutation renaming one asset's role."""
            return lambda c: c["assets"][index].__setitem__("role", value)

        cases = {"missing_%s" % role: drop(role)
                 for role in validator.JSON_ROLES + ("canonical_checkpoint",
                                                     "patient_graphs")}
        cases.update({
            "duplicate": rename(0, "label_vocab"),
            "unknown": rename(0, "unexpected_role"),
            "role_missing": lambda c: c["assets"][0].pop("role"),
            "role_list": rename(0, ["documentation"]),
            "extra": lambda c: c["assets"].append(dict(c["assets"][0])),
            "empty": lambda c: c.__setitem__("assets", []),
            "not_list": lambda c: c.__setitem__("assets", {}),
            "entry_not_object": lambda c: c["assets"].__setitem__(0, "x"),
        })
        for label, mutate in cases.items():
            with self.subTest(case=label):
                tree, record, opened = self.run_config(config=mutate)
                self.assertEqual(record["findings"], ["asset_role_set_invalid"])
                self.assert_no_asset_opened(tree, opened)

    def test_unsafe_relative_paths_fail(self):
        """Absolute, UNC, drive, slash, dot, traversal and odd paths fail."""
        paths = {
            "parent": "..\\escape.json",
            "inner_parent": "data\\processed\\..\\..\\escape.json",
            "dot": ".\\data\\processed\\label_vocab.json",
            "inner_dot": "data\\.\\processed\\label_vocab.json",
            "forward_parent": "../escape.json",
            "forward_slash": "data/processed/label_vocab.json",
            "absolute": "C:\\escape\\label_vocab.json",
            "drive_relative": "C:escape.json",
            "unc": "\\\\server\\share\\label_vocab.json",
            "device": "\\\\?\\C:\\label_vocab.json",
            "rooted": "\\data\\processed\\label_vocab.json",
            "empty": "",
            "empty_part": "data\\\\label_vocab.json",
            "trailing_separator": "data\\processed\\",
            "stream": "data\\processed\\label_vocab.json:stream",
            "trailing_dot": "data\\processed\\label_vocab.json.",
            "reserved": "data\\processed\\CON.json",
            "short_name": "data\\PROCES~1\\label_vocab.json",
            "git": ".git\\label_vocab.json",
            "control": "data\\processed\\label\x01vocab.json",
            "not_text": None,
            "number": 7,
        }
        for label, value in paths.items():
            def mutate(config, value=value):
                """Replace the label vocabulary path."""
                for item in config["assets"]:
                    if item["role"] == "label_vocab":
                        item["relative_path"] = value
            with self.subTest(case=label):
                tree, record, opened = self.run_config(config=mutate)
                self.assertEqual(record["findings"],
                                 ["asset_path_invalid:label_vocab"])
                self.assertEqual(record["asset_count"], 0)
                self.assert_no_asset_opened(tree, opened)

    def test_run_roles_and_duplicate_paths_fail(self):
        """Run artefacts stay in the run directory; paths are distinct."""
        def move_checkpoint(config):
            """Place the checkpoint outside the canonical run directory."""
            config["assets"][5]["relative_path"] = "data\\processed\\best.pt"

        def duplicate(config):
            """Point the label vocabulary at the concept vocabulary path."""
            for item in config["assets"]:
                if item["role"] == "label_vocab":
                    item["relative_path"] = RELATIVE["concept_vocab"].upper()

        for label, mutate, role in (
                ("outside_run", move_checkpoint, "canonical_checkpoint"),
                ("duplicate_path", duplicate, "label_vocab")):
            with self.subTest(case=label):
                tree, record, opened = self.run_config(config=mutate)
                self.assertEqual(record["findings"],
                                 ["asset_path_invalid:%s" % role])
                self.assert_no_asset_opened(tree, opened)

    def test_malformed_size_and_hash_fail(self):
        """Sizes must be non-negative integers; digests 64 hex characters."""
        def field(name, value):
            """Return a mutation setting one label vocabulary field."""
            def mutate(config):
                """Apply the field change."""
                for item in config["assets"]:
                    if item["role"] == "label_vocab":
                        if value is KeyError:
                            del item[name]
                        else:
                            item[name] = value
            return mutate

        cases = {
            "size_negative": ("expected_size_bytes", -1, "size"),
            "size_bool": ("expected_size_bytes", True, "size"),
            "size_float": ("expected_size_bytes", 10.0, "size"),
            "size_text": ("expected_size_bytes", "10", "size"),
            "size_null": ("expected_size_bytes", None, "size"),
            "size_missing": ("expected_size_bytes", KeyError, "size"),
            "size_over_json_bound": ("expected_size_bytes",
                                     validator.MAX_JSON_BYTES + 1, "size"),
            "sha_short": ("expected_sha256", "A" * 63, "sha256"),
            "sha_long": ("expected_sha256", "A" * 65, "sha256"),
            "sha_not_hex": ("expected_sha256", "G" * 64, "sha256"),
            "sha_spaces": ("expected_sha256", " " + "A" * 63, "sha256"),
            "sha_newline": ("expected_sha256", "A" * 64 + "\n", "sha256"),
            "sha_int": ("expected_sha256", 7, "sha256"),
            "sha_null": ("expected_sha256", None, "sha256"),
            "sha_missing": ("expected_sha256", KeyError, "sha256"),
        }
        for label, (name, value, kind) in cases.items():
            with self.subTest(case=label):
                tree, record, opened = self.run_config(config=field(name, value))
                self.assertEqual(record["findings"],
                                 ["asset_%s_invalid:label_vocab" % kind])
                self.assert_no_asset_opened(tree, opened)

    def test_missing_or_linked_configuration_is_unavailable(self):
        """A missing configuration or one behind a link is refused."""
        tree = self.build()
        with replaced_lstat(tree.config_path,
                            fake_stat(stat.S_IFREG | 0o600, 0x400)), \
                open_spy() as opened:
            record = validator.validate_assets()
        self.assertEqual(record["findings"], ["configuration_unavailable"])
        self.assertEqual(opened, [])
        os.remove(tree.config_path)
        record = validator.validate_assets()
        self.assertEqual(record["findings"], ["configuration_unavailable"])


class LinkAndChangeTests(ScratchCase):
    """Links, reparse points and changes during reading are refused."""

    def test_simulated_links_are_rejected_without_opening(self):
        """A reparse leaf, symlink leaf or junction ancestor is refused."""
        cases = {
            "reparse_file": ("label_vocab", False,
                             fake_stat(stat.S_IFREG | 0o600, 0x400)),
            "symlink_file": ("canonical_checkpoint", False,
                             fake_stat(stat.S_IFLNK | 0o777)),
            "junction_ancestor": ("patient_graphs", True,
                                  fake_stat(stat.S_IFDIR | 0o700, 0x400)),
        }
        for label, (role, ancestor, info) in cases.items():
            with self.subTest(case=label):
                tree = self.build()
                target = tree.path(role)
                if ancestor:
                    target = os.path.dirname(target)
                with replaced_lstat(target, info), open_spy() as opened:
                    record = validator.validate_assets()
                self.assertIn("asset_link_rejected:%s" % role,
                              record["findings"])
                self.assertEqual(record["status"], "FAIL")
                self.assertNotIn(os.path.normcase(tree.path(role)),
                                 [path for path, _ in opened])

    def test_real_symlink_is_rejected_when_supported(self):
        """A real file symlink is refused (skipped where unsupported)."""
        tree = self.build()
        path = tree.path("label_vocab")
        moved = path + ".real"
        os.replace(path, moved)
        try:
            os.symlink(moved, path)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unsupported on this volume or account")
        record = validator.validate_assets()
        self.assertIn("asset_link_rejected:label_vocab", record["findings"])

    def test_change_during_read_is_detected(self):
        """A modification-time change after reading fails the asset."""
        tree = self.build()
        target = os.path.normcase(tree.path("canonical_metrics"))
        real = os.lstat
        calls = []

        def changing(path, *args, **kwargs):
            """Report a new modification time on the third lstat of target."""
            info = real(path, *args, **kwargs)
            if os.path.normcase(os.fspath(path)) == target:
                calls.append(path)
                if len(calls) >= 3:
                    return fake_stat(info.st_mode,
                                     getattr(info, "st_file_attributes", 0),
                                     info.st_size, info.st_mtime_ns + 1)
            return info

        with mock.patch.object(validator.os, "lstat", changing):
            record = validator.validate_assets()
        self.assertIn("asset_changed:canonical_metrics", record["findings"])
        self.assertEqual(record["canonical_metrics"]["status"], "not_evaluated")

    def test_handle_mismatch_is_detected(self):
        """An open handle whose size differs from lstat fails the asset."""
        tree = self.build()
        real = os.fstat
        target_size = len(tree.contents["patient_graphs"])

        def fstat(descriptor):
            """Report a different size for the graph file handle."""
            info = real(descriptor)
            if info.st_size == target_size:
                return fake_stat(info.st_mode, 0, info.st_size + 1,
                                 info.st_mtime_ns)
            return info

        with mock.patch.object(validator.os, "fstat", fstat):
            record = validator.validate_assets()
        self.assertIn("asset_changed:patient_graphs", record["findings"])

    def test_missing_asset_is_unavailable(self):
        """A missing file fails clearly; other assets are still checked."""
        tree = self.build()
        os.remove(tree.path("canonical_checkpoint"))
        record = validator.validate_assets()
        self.assertEqual(record["findings"],
                         ["asset_unavailable:canonical_checkpoint"])
        self.assertEqual(record["asset_count"], 10)
        self.assertEqual(record["checkpoint_probe"]["status"], "valid")


class IdentityTests(ScratchCase):
    """Observed bytes must match the configured identity before parsing."""

    def test_wrong_hash_or_size_fails_without_parsing(self):
        """Changed JSON bytes are an identity failure and are not parsed."""
        tree = self.build()
        replacement = encode(["SYNTHLABEL"] * 274)
        with open(tree.path("label_vocab"), "wb") as handle:
            handle.write(replacement)
        parsed = []
        real_parse = validator.parse_json_bytes

        def parse_spy(raw, **kwargs):
            """Record every byte string handed to the JSON parser."""
            parsed.append(raw)
            return real_parse(raw, **kwargs)

        with mock.patch.object(validator, "parse_json_bytes", parse_spy):
            record = validator.validate_assets()
        self.assertEqual(record["findings"],
                         ["asset_identity_mismatch:label_vocab"])
        self.assertNotIn(replacement, parsed)
        self.assertEqual(record["label_vocab"]["status"], "not_evaluated")
        self.assertIsNone(record["label_count"])
        self.assertFalse(record["label_dimension_match"])

    def test_same_size_wrong_hash_fails(self):
        """A same-size byte change in a hash-only file is a mismatch."""
        tree = self.build()
        data = bytearray(tree.contents["canonical_checkpoint"])
        data[-1] ^= 0x01
        with open(tree.path("canonical_checkpoint"), "wb") as handle:
            handle.write(bytes(data))
        record = validator.validate_assets()
        self.assertIn("asset_identity_mismatch:canonical_checkpoint",
                      record["findings"])
        self.assertIn("checkpoint_compatibility_mismatch", record["findings"])

    def test_oversized_json_is_refused_before_reading(self):
        """A JSON role larger than the bound is refused as a mismatch."""
        tree = self.build()
        with open(tree.path("label_vocab"), "wb") as handle:
            handle.write(b" " * 9000)
        with mock.patch.object(validator, "MAX_JSON_BYTES", 8192), \
                open_spy() as opened:
            record = validator.validate_assets()
        self.assertIn("asset_identity_mismatch:label_vocab", record["findings"])
        self.assertNotIn(os.path.normcase(tree.path("label_vocab")),
                         [path for path, _ in opened])


class StructureTests(ScratchCase):
    """Safe shapes of the four JSON roles, with identity pinned to them."""

    def check(self, role, value, finding):
        """Pin a fixture whose role holds value and expect one finding."""
        data = value if isinstance(value, bytes) else encode(value)
        self.build(contents=lambda c: c.__setitem__(role, data))
        record = validator.validate_assets()
        self.assertEqual(record["status"], "FAIL")
        self.assertIn(finding, record["findings"])
        self.assertEqual([item for item in record["findings"]
                          if not item.startswith("checkpoint")], [finding])
        return record

    def test_label_vocabulary_shapes(self):
        """Only a 275-entry sequence of scalar labels passes."""
        cases = {
            "short": (["L"] * 274, "label_count_mismatch"),
            "long": (["L"] * 276, "label_count_mismatch"),
            "empty": ([], "label_count_mismatch"),
            "mapping": ({"L%03d" % index: index for index in range(275)},
                        "label_vocab_invalid"),
            "nested": ([["L"]] * 275, "label_vocab_invalid"),
            "object_items": ([{"L": 1}] * 275, "label_vocab_invalid"),
            "bool_item": ([True] * 275, "label_vocab_invalid"),
            "null_item": ([None] * 275, "label_vocab_invalid"),
            "text": ("L" * 275, "label_vocab_invalid"),
            "invalid_json": (b"[", "label_vocab_invalid"),
            "nan": (b"[NaN]", "label_vocab_invalid"),
        }
        for label, (value, finding) in cases.items():
            with self.subTest(case=label):
                record = self.check("label_vocab", value, finding)
                self.assertFalse(record["label_dimension_match"])
        self.build(contents=lambda c: c.__setitem__(
            "label_vocab", encode(list(range(275)))))
        self.assertEqual(validator.validate_assets()["status"], "PASS")

    def test_concept_vocabulary_shapes(self):
        """Exactly three safe domain keys with bounded scalar mappings pass."""
        good = {"domain_a": {"x": 1}, "domain_b": {"y": "z"},
                "domain_c": {}}
        cases = {
            "two_domains": {"domain_a": {}, "domain_b": {}},
            "four_domains": dict(good, domain_d={}),
            "list": [good],
            "domain_list": dict(good, domain_c=[1, 2]),
            "domain_scalar": dict(good, domain_c=5),
            "nested_value": dict(good, domain_c={"x": {"y": 1}}),
            "list_value": dict(good, domain_c={"x": [1]}),
            "bool_value": dict(good, domain_c={"x": True}),
            "null_value": dict(good, domain_c={"x": None}),
            "unsafe_key": {"domain a": {}, "domain_b": {}, "domain_c": {}},
            "digit_key": {"1domain": {}, "domain_b": {}, "domain_c": {}},
        }
        for label, value in cases.items():
            with self.subTest(case=label):
                record = self.check("concept_vocab", value,
                                    "concept_vocab_invalid")
                self.assertEqual(record["concept_vocab"]["status"], "invalid")
        with self.subTest(case="too_many_entries"):
            with mock.patch.object(validator, "MAX_CONCEPT_ENTRIES", 1):
                self.check("concept_vocab",
                           dict(good, domain_c={"a": 1, "b": 2}),
                           "concept_vocab_invalid")

    def test_metrics_shapes_and_identity(self):
        """The canonical diag_kg object with exact documented values passes."""
        def changed(**changes):
            """Return metrics with top-level or test fields replaced."""
            value = metrics_object()
            for name, item in changes.items():
                if name in ("auroc_macro", "auprc_macro"):
                    value["test"][name] = item
                else:
                    value[name] = item
            return value

        mismatch = "metrics_identity_mismatch"
        invalid = "metrics_invalid"
        cases = {
            "best_epoch": (changed(best_epoch=41), mismatch),
            "best_epoch_float": (changed(best_epoch=42.0), mismatch),
            "best_epoch_text": (changed(best_epoch="42"), mismatch),
            "auroc_close": (changed(auroc_macro=validator.AUROC_MACRO + 1e-12),
                            mismatch),
            "auroc_text": (changed(auroc_macro=str(validator.AUROC_MACRO)),
                           mismatch),
            "auprc_null": (changed(auprc_macro=None), mismatch),
            "no_kg_true": (changed(no_kg=True), mismatch),
            "no_kg_zero": (changed(no_kg=0), mismatch),
            "task": (changed(task="mortality"), mismatch),
            "source": (changed(data_source="other"), mismatch),
            "history_short": (changed(history=[{}] * 49), mismatch),
            "history_not_list": (changed(history={}), invalid),
            "test_not_object": (changed(test=[1]), invalid),
            "array": ([metrics_object()], invalid),
            "missing_best_val": ({key: value for key, value
                                  in metrics_object().items()
                                  if key != "best_val"}, invalid),
            "invalid_json": (b"{", invalid),
            "nan": (b'{"best_epoch": NaN}', invalid),
        }
        for label, (value, finding) in cases.items():
            with self.subTest(case=label):
                record = self.check("canonical_metrics", value, finding)
                self.assertEqual(record["canonical_metrics"]["status"],
                                 "invalid")
        with self.subTest(case="non_finite_elsewhere"):
            value = metrics_object()
            value["best_val"] = {"auroc_macro": float("nan")}
            raw = json.dumps(value).encode("utf-8")
            self.assertIn(b"NaN", raw)
            self.build(contents=lambda c: c.__setitem__(
                "canonical_metrics", raw))
            record = validator.validate_assets()
            self.assertEqual(record["status"], "PASS", record["findings"])

    def test_explanation_shapes(self):
        """A bounded non-empty sequence of safe-key objects passes."""
        cases = {
            "empty": [],
            "mapping": {"target_index": 0},
            "scalar_items": [1, 2],
            "empty_object": [{}],
            "unsafe_key": [{"bad key": 1}],
            "digit_key": [{"1key": 1}],
            "long_key": [{"k" * 65: 1}],
            "newline_key": [{"key\n": 1}],
            "mixed": [{"target_index": 0}, "row"],
            "invalid_json": b"[",
        }
        for label, value in cases.items():
            with self.subTest(case=label):
                self.check("canonical_explanations", value,
                           "explanations_invalid")
        with self.subTest(case="too_many_entries"):
            with mock.patch.object(validator, "MAX_EXPLANATIONS", 1):
                self.check("canonical_explanations",
                           [{"target_index": 0}, {"target_index": 1}],
                           "explanations_invalid")
        with self.subTest(case="too_many_keys"):
            with mock.patch.object(validator, "MAX_EXPLANATION_KEYS", 1):
                self.check("canonical_explanations",
                           [{"a": 0, "b": 1}], "explanations_invalid")


class ProbeTests(ScratchCase):
    """The checkpoint compatibility probe has an exact schema and values."""

    def run_probe(self, **kwargs):
        """Build a fixture with a probe change and validate it."""
        self.build(**kwargs)
        return validator.validate_assets()

    def test_probe_value_mismatches_fail(self):
        """Wrong identity, run, role, heads, count or status is a mismatch."""
        def setter(key, value):
            """Return a mutation assigning one probe field."""
            return lambda probe: probe.__setitem__(key, value)

        def head(name, value):
            """Return a mutation assigning one output head width."""
            return lambda probe: probe["output_heads"].__setitem__(name, value)

        cases = {
            "sha": setter("checkpoint_sha256", "A" * 64),
            "size": setter("checkpoint_size_bytes", len(CHECKPOINT_BYTES) + 1),
            "graph_head": head("graph_head", 274),
            "seq_head": head("seq_head", 276),
            "label_count": setter("label_count", 274),
            "order_status": setter("label_order_status",
                                   "index_order_verified"),
            "run": setter("canonical_run", "diag_no_kg"),
            "role": setter("checkpoint_role", "patient_graphs"),
            "step": setter("step", "14"),
            "schema_version": setter("schema_version", "2.0"),
            "record_type": setter("record_type", "other"),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                record = self.run_probe(probe=mutate)
                self.assertEqual(record["findings"],
                                 ["checkpoint_compatibility_mismatch"])
                self.assertEqual(record["checkpoint_probe"],
                                 {"status": "invalid"})
                self.assertFalse(record["label_dimension_match"])

    def test_probe_must_match_configured_checkpoint(self):
        """A probe for other checkpoint bytes fails even if self-consistent."""
        other = CHECKPOINT_BYTES + b"other"
        record = self.run_probe(probe=lambda probe: probe.update(
            checkpoint_sha256=digest(other),
            checkpoint_size_bytes=len(other)))
        self.assertEqual(record["findings"],
                         ["checkpoint_compatibility_mismatch"])

    def test_probe_schema_violations_fail(self):
        """Extra, missing or mistyped fields violate the exact schema."""
        def setter(key, value):
            """Return a mutation assigning one probe field."""
            return lambda probe: probe.__setitem__(key, value)

        cases = {
            "extra_key": setter("label_order_verified", True),
            "missing_key": lambda probe: probe.pop("limitations"),
            "heads_extra": setter("output_heads", {"graph_head": 275,
                                                   "seq_head": 275,
                                                   "other_head": 275}),
            "heads_missing": setter("output_heads", {"graph_head": 275}),
            "heads_bool": setter("output_heads", {"graph_head": True,
                                                  "seq_head": 275}),
            "heads_list": setter("output_heads", [275, 275]),
            "label_count_text": setter("label_count", "275"),
            "label_count_bool": setter("label_count", True),
            "sha_short": setter("checkpoint_sha256", "A" * 63),
            "sha_int": setter("checkpoint_sha256", 5),
            "sha_newline": setter("checkpoint_sha256", "A" * 64 + "\n"),
            "size_negative": setter("checkpoint_size_bytes", -1),
            "size_float": setter("checkpoint_size_bytes", 17.0),
            "status_int": setter("label_order_status", 1),
            "purpose_empty": setter("purpose", " "),
            "limitations_empty": setter("limitations", []),
            "limitations_item": setter("limitations", [1]),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                record = self.run_probe(probe=mutate)
                self.assertEqual(record["findings"],
                                 ["checkpoint_probe_schema_invalid"])
        for label, raw in (("array", b"[]"), ("invalid", b"{"),
                           ("duplicate", b'{"step": "15", "step": "15"}')):
            with self.subTest(case=label):
                record = self.run_probe(probe_raw=raw)
                self.assertEqual(record["findings"],
                                 ["checkpoint_probe_schema_invalid"])

    def test_missing_or_linked_probe_is_unavailable(self):
        """A missing probe or one behind a reparse point fails closed."""
        tree = self.build()
        with replaced_lstat(tree.probe_path,
                            fake_stat(stat.S_IFREG | 0o600, 0x400)):
            record = validator.validate_assets()
        self.assertEqual(record["findings"], ["checkpoint_probe_unavailable"])
        os.remove(tree.probe_path)
        record = validator.validate_assets()
        self.assertEqual(record["findings"], ["checkpoint_probe_unavailable"])


class CommittedAuthorityTests(unittest.TestCase):
    """The committed configuration and probe satisfy the contract offline.

    Only the two repository configuration files are read; no upstream asset
    is opened.
    """

    def test_real_pins_are_unchanged(self):
        """The fixed roots, paths and approved digest are the real ones."""
        self.assertEqual(validator.APPROVED_CONFIG_SHA256, REAL_CONFIG_SHA256)
        self.assertEqual(REAL_UPSTREAM_ROOT,
                         "G:\\Industry Dataset\\standardized-omop-gnn-7B82")
        self.assertEqual(REAL_CONFIG_PATH, os.path.join(
            REPO, "config", "inherited_baseline.json"))
        self.assertEqual(REAL_PROBE_PATH, os.path.join(
            REPO, "config", "checkpoint_compatibility_probe.json"))
        self.assertTrue(REAL_OUTPUT_PATH.startswith(
            "G:\\Industry Dataset\\ehr-evidence-workspace\\outputs\\"))

    def test_committed_configuration_and_probe_are_valid(self):
        """The committed Step 1 config matches its pin and passes lexically."""
        with open_spy() as opened:
            _, observed, raw = validator.read_regular_file(
                REAL_CONFIG_PATH, validator.MAX_CONFIG_BYTES)
            config = validator.parse_json_bytes(raw)
            _, _, probe_raw = validator.read_regular_file(
                REAL_PROBE_PATH, validator.MAX_PROBE_BYTES)
            probe = validator.parse_json_bytes(probe_raw)
        self.assertEqual(observed.lower(), REAL_CONFIG_SHA256.lower())
        self.assertEqual(validator.config_findings(config), [])
        entries = {item["role"]: item for item in config["assets"]}
        self.assertEqual(
            validator.probe_findings(probe, entries["canonical_checkpoint"],
                                     None), [])
        self.assertEqual(sorted(path for path, _ in opened),
                         sorted(os.path.normcase(path) for path in
                                (REAL_CONFIG_PATH, REAL_PROBE_PATH)))


if __name__ == "__main__":
    unittest.main()
