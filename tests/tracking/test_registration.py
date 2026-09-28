"""Synthetic Step 13 tests for inherited-input registration.

Every baseline is a small synthetic fixture written to workspace test scratch; the
approved baseline hash is pinned to the fixture with mock.patch and restored
afterwards. No upstream asset, patient record, ClearML SDK, credential or
service is used: the remote mode runs against an in-memory fake adapter.
"""
import builtins
import contextlib
import copy
import hashlib
import io
import json
import logging
import os
import stat
import sys
import tempfile
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
from ehr_evidence_app.tracking import registration as register  # noqa: E402
from ehr_evidence_app.tracking import connection as checker  # noqa: E402
from ehr_evidence_app.tracking import controlled as tracking  # noqa: E402


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(os.path.join(REPO, "config", "paths.json"), "r",
              encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


SCRATCH = configured_test_temp()
REAL_APPROVED_SHA256 = register.APPROVED_BASELINE_SHA256
PROJECT_ID = "b2" * 16
TASK_ID = "d3" * 16
CONFIG_PATH = "C:" + r"\Users\fixture\clearml.conf"
CACHE_PATH = checker.guard.RUNTIME_ROOT + r"\cache\clearml"
ALLOWED_ACTIONS = ["users.get_current_user", "projects.get_all",
                   "tasks.create", "tasks.get_all", "tasks.completed"]
# The actions of a registration whose readback failed: it is not completed.
READBACK_ACTIONS = ALLOWED_ACTIONS[:4]
INHERITED_TAGS = {"provenance:inherited_reported",
                  "execution:not_executed_by_downstream"}


def fixture_baseline():
    """Return a small synthetic Step 1 baseline fixture."""
    assets = []
    for role in register.BASELINE_ROLES:
        assets.append({"role": role, "relative_path": "fixture\\%s" % role,
                       "size_bytes": 10, "sha256": "a" * 64})
    return {"step": "1", "configuration": {"path": "config/inherited_baseline.json",
            "sha256": "d" * 64}, "capture_tool": {"sha256": "b" * 64},
            "canonical_run": {"name": "diag_kg",
            "reported_metrics": {"best_epoch": 42, "history_length": 50,
                "provenance": "inherited_reported",
                "execution_status": "not_executed_by_downstream",
                "test": {"values": {"auroc_macro": 0.9062,
                                      "auprc_macro": 0.3130}}}},
            "downstream_execution": {"training_run": False}, "assets": assets}


def write_fixture(path, baseline):
    """Write one fixture baseline and return the uppercase digest of its bytes."""
    raw = json.dumps(baseline, sort_keys=True).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(raw)
    return hashlib.sha256(raw).hexdigest().upper()


def valid_environment():
    """Return a operator environment naming a fake off-repository config."""
    return {checker.CONFIG_VARIABLE: CONFIG_PATH,
            checker.CACHE_VARIABLE: CACHE_PATH}


def regular_file(path):
    """Return fake lstat information for a regular file (never on disk)."""
    return type("Stat", (), {"st_mode": stat.S_IFREG | 0o600,
                              "st_file_attributes": 0})()


class FakeAdapter(object):
    """In-memory ClearML session stand-in recording every service action."""

    def __init__(self, readback=None,
                 archive_response=(200, {"data": {"archived": 1}}),
                 complete_response=(200, {"data": {"updated": 1,
                                                   "fields": {}}})):
        """Configure optional readback, archive and completion
        behaviour."""
        self.readback = readback
        self.archive_response = archive_response
        self.complete_response = complete_response
        self.calls = []
        self.created = None
        self.connected_to = None

    def offline(self):
        """Report online mode."""
        return False

    def load_config(self):
        """Return an opaque configuration object."""
        return object()

    def configured_hosts(self, config):
        """Return exactly the declared endpoints."""
        return dict(checker.ENDPOINTS)

    def connect(self, config, api_server):
        """Record the API server the session would authenticate against."""
        self.connected_to = api_server

    def supports_api_version(self, version):
        """Report an older API so the base readback projection is used."""
        return False

    def call(self, service, action, payload):
        """Answer the approved service actions and reject anything else."""
        action_key = service + "." + action
        self.calls.append((action_key, copy.deepcopy(payload)))
        if action_key == "users.get_current_user":
            return 200, {"data": {"user": {
                "company": {"name": checker.WORKSPACE}}}}
        if action_key == "projects.get_all":
            return 200, {"data": {"projects": [
                {"id": PROJECT_ID, "name": checker.PROJECT}]}}
        if action_key == "tasks.create":
            self.created = copy.deepcopy(payload)
            return 200, {"data": {"id": TASK_ID}}
        if action_key == "tasks.get_all":
            if self.readback:
                return self.readback(payload, self.created)
            task = dict(self.created, id=TASK_ID,
                        script={"binary": "python", "entry_point": "",
                                "repository": "", "requirements": {}},
                        execution={"test_split": 0, "artifacts": [],
                                   "dataviews": {}, "model_desc": "",
                                   "model_labels": [], "parameters": {}},
                        output={}, configuration={},
                        hyperparams=copy.deepcopy(
                            self.created.get("hyperparams", {})))
            return 200, {"data": {"tasks": [task]}}
        if action_key == "tasks.archive":
            return self.archive_response
        if action_key == "events.add":
            return 200, {"data": {}}
        if action_key == "tasks.completed":
            return self.complete_response
        raise AssertionError("unexpected service action " + action_key)

    def actions(self):
        """Return the ordered service action names."""
        return [name for name, _ in self.calls]


class ScratchCase(unittest.TestCase):
    """Provide a workspace scratch directory and a fixture-pinned baseline hash."""

    def setUp(self):
        """Create a per-test scratch directory under the workspace."""
        if not os.path.isdir(SCRATCH):
            self.skipTest("configured test scratch is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self._directory = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.directory = self._directory.name
        self.addCleanup(self._directory.cleanup)
        self.baseline_path = os.path.join(self.directory, "baseline.json")
        self.registration_path = os.path.join(self.directory, "registration.json")

    def pin(self, baseline):
        """Write baseline and pin the approved digest to it for this test."""
        digest = write_fixture(self.baseline_path, baseline)
        patcher = mock.patch.object(register, "APPROVED_BASELINE_SHA256", digest)
        patcher.start()
        self.addCleanup(patcher.stop)
        return digest

    def load_and_build(self, baseline):
        """Pin, load and build the registration record for one fixture."""
        self.pin(baseline)
        loaded, assets = register.load_baseline(self.baseline_path)
        return register.build_registration(loaded, assets, self.baseline_path)

    def remote(self, adapter, baseline=None):
        """Run the operator remote mode against an in-memory adapter."""
        self.pin(fixture_baseline() if baseline is None else baseline)
        return register.remote_result(
            valid_environment(), lambda: adapter, self.baseline_path,
            self.registration_path, lstat=regular_file)


class ManifestTests(ScratchCase):
    """Offline manifest identity, provenance and determinism."""

    def test_manifest_is_reused_inherited(self):
        """The manifest labels every input and metric as inherited."""
        self.pin(fixture_baseline())
        result = register.offline_result(self.baseline_path, self.registration_path)
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["artifact_upload"])
        with open(self.registration_path, encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(record["canonical_run"], "diag_kg")
        self.assertEqual(record["provenance"], "reused_inherited")
        self.assertEqual(record["execution_status"], "not_executed_by_downstream")
        self.assertFalse(record["artifact_upload"])
        self.assertFalse(record["patient_content_loaded"])
        self.assertEqual(record["documented_reported_metrics"],
                         {"macro_auroc": 0.9062, "macro_auprc": 0.313,
                          "best_epoch": 42, "history_length": 50})
        self.assertEqual(record["documented_reported_metrics_provenance"],
                         {"provenance": "inherited_reported",
                          "execution_status": "not_executed_by_downstream"})
        self.assertEqual([item["role"] for item in record["inputs"]],
                         list(register.ROLES))
        for item in record["inputs"]:
            self.assertEqual(item["provenance"], "reused_inherited")
            self.assertEqual(item["execution_status"], "not_executed_by_downstream")
        self.assertNotIn("patient_graphs", [item["role"] for item in record["inputs"]])

    def test_repeated_manifest_bytes_are_identical(self):
        """Two offline runs on one baseline write byte-identical manifests."""
        self.pin(fixture_baseline())
        outputs = []
        for _ in range(2):
            register.offline_result(self.baseline_path, self.registration_path)
            with open(self.registration_path, "rb") as handle:
                outputs.append(handle.read())
        self.assertEqual(outputs[0], outputs[1])
        self.assertTrue(outputs[0].endswith(b"\n"))
        self.assertNotIn(b"\r\n", outputs[0])
        self.assertFalse(os.path.exists(self.registration_path + ".partial"))
        record = json.loads(outputs[0])
        self.assertEqual(outputs[0].decode("utf-8"),
                         json.dumps(record, indent=2, sort_keys=True) + "\n")

    def test_different_existing_manifest_is_never_replaced(self):
        """A differing manifest is refused in both modes and left intact."""
        prior = b'{"synthetic": "prior registration"}\n'
        self.pin(fixture_baseline())
        with open(self.registration_path, "wb") as handle:
            handle.write(prior)
        with self.assertRaises(register.RegistrationConflict):
            register.offline_result(self.baseline_path, self.registration_path)
        factory = mock.Mock(side_effect=AssertionError("adapter created"))
        result = register.remote_result(
            valid_environment(), factory, self.baseline_path,
            self.registration_path, lstat=regular_file)
        self.assertEqual(result, {"status": "FAIL", "mode": "operator_remote",
                                  "finding": "registration_conflict"})
        factory.assert_not_called()
        with open(self.registration_path, "rb") as handle:
            self.assertEqual(handle.read(), prior)
        self.assertFalse(os.path.exists(self.registration_path + ".partial"))
        stdout = io.StringIO()
        with mock.patch.object(register, "offline_result",
                               side_effect=register.RegistrationConflict(
                                   "registration_conflict")), \
                contextlib.redirect_stdout(stdout):
            self.assertEqual(register.main([]), 1)
        self.assertEqual(json.loads(stdout.getvalue()),
                         {"status": "FAIL", "mode": "offline",
                          "finding": "registration_conflict"})

    def test_real_pin_is_an_uppercase_digest(self):
        """The approved pin is a fixed SHA-256 left untouched by tests."""
        self.assertEqual(register.APPROVED_BASELINE_SHA256, REAL_APPROVED_SHA256)
        self.assertRegex(REAL_APPROVED_SHA256, r"^[A-F0-9]{64}$")


class BaselineIdentityTests(ScratchCase):
    """Pinned identity, canonical run and malformed baseline rejection."""

    def test_tampered_baseline_fails_pinned_hash(self):
        """Changing one byte after pinning is an identity failure."""
        self.pin(fixture_baseline())
        with open(self.baseline_path, "rb") as handle:
            raw = handle.read()
        with open(self.baseline_path, "wb") as handle:
            handle.write(raw.replace(b'"size_bytes": 10', b'"size_bytes": 11', 1))
        with self.assertRaisesRegex(ValueError, "approved Step 1 authority"):
            register.load_baseline(self.baseline_path)
        with self.assertRaises(ValueError):
            register.offline_result(self.baseline_path, self.registration_path)
        self.assertFalse(os.path.exists(self.registration_path))

    def test_unpinned_fixture_fails_real_pin(self):
        """A fixture never matches the real approved baseline hash."""
        write_fixture(self.baseline_path, fixture_baseline())
        with self.assertRaisesRegex(ValueError, "approved Step 1 authority"):
            register.load_baseline(self.baseline_path)

    def test_wrong_canonical_run_with_matching_hash_is_rejected(self):
        """A hash-matching baseline naming another run or step still fails."""
        for field, value in (("name", "other_run"), ("step", "2")):
            baseline = fixture_baseline()
            if field == "name":
                baseline["canonical_run"]["name"] = value
            else:
                baseline["step"] = value
            with self.subTest(field=field):
                self.pin(baseline)
                with self.assertRaisesRegex(ValueError, "diag_kg"):
                    register.load_baseline(self.baseline_path)

    def test_malformed_roles_assets_and_provenance_are_rejected(self):
        """Each malformed role, asset or provenance field fails closed."""
        def asset(index, **changes):
            """Return a mutation replacing fields of one asset entry."""
            def mutate(baseline):
                """Apply the asset field changes."""
                baseline["assets"][index].update(changes)
            return mutate

        def setter(*keys, value):
            """Return a mutation assigning value at a nested key path."""
            def mutate(baseline):
                """Assign the nested value."""
                target = baseline
                for key in keys[:-1]:
                    target = target[key]
                target[keys[-1]] = value
            return mutate

        def remover(*keys):
            """Return a mutation deleting a nested key."""
            def mutate(baseline):
                """Delete the nested key."""
                target = baseline
                for key in keys[:-1]:
                    target = target[key]
                del target[keys[-1]]
            return mutate

        cases = {
            "missing_role": lambda b: b["assets"].pop(),
            "duplicate_role": asset(-1, role=register.BASELINE_ROLES[0]),
            "unknown_role": asset(0, role="unexpected_role"),
            "extra_asset": lambda b: b["assets"].append(dict(b["assets"][0])),
            "assets_not_list": setter("assets", value={}),
            "asset_not_object": lambda b: b["assets"].__setitem__(0, "fixture"),
            "sha_not_hex": asset(0, sha256="z" * 64),
            "sha_short": asset(0, sha256="a" * 63),
            "sha_not_text": asset(0, sha256=7),
            "size_negative": asset(0, size_bytes=-1),
            "size_bool": asset(0, size_bytes=True),
            "size_float": asset(0, size_bytes=10.0),
            "size_text": asset(0, size_bytes="10"),
            "path_parent": asset(0, relative_path="..\\escape"),
            "path_forward_parent": asset(0, relative_path="../escape"),
            "path_forward_slash": asset(0, relative_path="fixture/role"),
            "path_drive": asset(0, relative_path="C:\\escape"),
            "path_unc": asset(0, relative_path="\\\\server\\share"),
            "path_rooted": asset(0, relative_path="\\rooted"),
            "path_empty": asset(0, relative_path=""),
            "path_empty_part": asset(0, relative_path="fixture\\\\role"),
            "path_not_text": asset(0, relative_path=None),
            "metric_provenance": setter("canonical_run", "reported_metrics",
                                        "provenance", value="downstream_executed"),
            "metric_execution": setter("canonical_run", "reported_metrics",
                                       "execution_status", value="executed"),
            "metric_provenance_missing": remover("canonical_run", "reported_metrics",
                                                 "provenance"),
            "metrics_not_object": setter("canonical_run", "reported_metrics", value=[]),
            "training_claimed": setter("downstream_execution", "training_run", value=True),
            "training_missing": remover("downstream_execution"),
            "config_sha": setter("configuration", "sha256", value="short"),
            "config_path": setter("configuration", "path", value=None),
            "capture_tool_missing": remover("capture_tool"),
            "capture_tool_sha": setter("capture_tool", "sha256", value="b" * 10),
            "run_not_object": setter("canonical_run", value="diag_kg"),
        }
        for label, mutate in cases.items():
            baseline = fixture_baseline()
            mutate(baseline)
            with self.subTest(case=label):
                self.pin(baseline)
                with self.assertRaises(ValueError):
                    register.load_baseline(self.baseline_path)

    def test_top_level_array_is_rejected(self):
        """A hash-matching JSON array is not a baseline record."""
        self.pin([fixture_baseline()])
        with self.assertRaisesRegex(ValueError, "JSON object"):
            register.load_baseline(self.baseline_path)


class MetricDomainTests(ScratchCase):
    """Reported metric bounds and the best_epoch/history_length relation."""

    def mutated(self, **changes):
        """Return a fixture with reported metric fields replaced."""
        baseline = fixture_baseline()
        metrics = baseline["canonical_run"]["reported_metrics"]
        for name, value in changes.items():
            if name in ("auroc_macro", "auprc_macro"):
                metrics["test"]["values"][name] = value
            else:
                metrics[name] = value
        return baseline

    def test_invalid_metric_domains_are_rejected(self):
        """Out-of-range, non-finite, boolean or text metrics fail."""
        cases = [
            {"auroc_macro": 1.01}, {"auroc_macro": -0.01},
            {"auroc_macro": float("nan")}, {"auroc_macro": float("inf")},
            {"auroc_macro": True}, {"auroc_macro": "0.9"},
            {"auprc_macro": 1.5}, {"auprc_macro": -1}, {"auprc_macro": None},
            {"best_epoch": -1}, {"best_epoch": True}, {"best_epoch": 42.0},
            {"best_epoch": 51}, {"history_length": 0},
            {"history_length": -5}, {"history_length": 50.0},
            {"history_length": False}, {"best_epoch": 10, "history_length": 9},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaisesRegex(ValueError, "domains"):
                    self.load_and_build(self.mutated(**changes))

    def test_missing_metric_values_are_rejected(self):
        """Missing reported values fail as incomplete, not with KeyError."""
        baseline = fixture_baseline()
        del baseline["canonical_run"]["reported_metrics"]["test"]["values"]["auprc_macro"]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.load_and_build(baseline)
        baseline = fixture_baseline()
        baseline["canonical_run"]["reported_metrics"]["test"] = None
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.load_and_build(baseline)

    def test_metric_boundaries_are_accepted(self):
        """Score endpoints and 0 <= best_epoch <= history_length pass."""
        cases = [
            {"auroc_macro": 0, "auprc_macro": 1},
            {"auroc_macro": 1.0, "auprc_macro": 0.0},
            {"best_epoch": 0, "history_length": 1},
            {"best_epoch": 50, "history_length": 50},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                record = self.load_and_build(self.mutated(**changes))
                metrics = record["documented_reported_metrics"]
                self.assertLessEqual(metrics["best_epoch"], metrics["history_length"])

    def test_tracking_schema_bounds_inherited_metrics(self):
        """The Step 11 schema enforces score and count domains."""
        lineage = {
            "code_revision": "inherited-baseline-v1",
            "case_set_version": "diag-kg",
            "llm_configuration_fingerprint": "a" * 64,
            "prompt_version": "not-applicable-inherited",
            "evidence_schema_version": "not-applicable-inherited",
            "validation_configuration_fingerprint": "b" * 64,
            "evaluation_outputs": {},
            "protected_reference": register.REGISTRATION_PATH,
            "protected_checksum": "c" * 64,
        }
        good = {"macro_auroc": 1, "macro_auprc": 0, "best_epoch": 0,
                "history_length": 1_000_000}
        tags = tracking.build_metadata_tags(
            dict(lineage, evaluation_outputs=good), {})
        self.assertIn("lineage:evaluation_outputs:history_length=1000000", tags)
        for bad in ({"macro_auroc": 1.0001}, {"macro_auprc": -0.1},
                    {"best_epoch": 1.5}, {"best_epoch": -1},
                    {"history_length": 1_000_001}, {"history_length": True}):
            with self.subTest(bad=bad):
                with self.assertRaises(tracking.UnsafeMetadata):
                    tracking.build_metadata_tags(
                        dict(lineage, evaluation_outputs=bad), {})


class RemoteRegistrationTests(ScratchCase):
    """In-memory operator remote mode: create and read back; the task
    stays visible (ADR 0018)."""

    def test_remote_create_and_readback_success(self):
        """One metadata-only task is created and read back, and it is not
        archived."""
        adapter = FakeAdapter()
        result = self.remote(adapter)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["task_id"], TASK_ID)
        self.assertEqual(result["canonical_run"], "diag_kg")
        self.assertEqual(result["inputs"], len(register.ROLES))
        self.assertFalse(result["artifact_upload"])
        self.assertEqual(adapter.actions(), ALLOWED_ACTIONS)
        self.assertEqual(adapter.connected_to, checker.ENDPOINTS["api_server"])
        self.assertEqual(adapter.created["project"], PROJECT_ID)
        self.assertEqual(adapter.created["name"],
                         "Inherited input registration")
        self.assertNotIn("tasks.archive", adapter.actions())
        self.assertTrue(os.path.isfile(self.registration_path))

    def test_payload_has_exact_inherited_tags(self):
        """Provenance tags are exact, once each, beside approved lineage tags."""
        adapter = FakeAdapter()
        self.assertEqual(self.remote(adapter)["status"], "PASS")
        tags = adapter.created["tags"]
        self.assertEqual(len(tags), len(set(tags)))
        self.assertEqual({tag for tag in tags if not tag.startswith("lineage:")},
                         INHERITED_TAGS)
        metric_tags = {tag for tag in tags
                       if tag.startswith("lineage:evaluation_outputs:")}
        self.assertEqual(metric_tags, {
            "lineage:evaluation_outputs:best_epoch=42",
            "lineage:evaluation_outputs:history_length=50",
            "lineage:evaluation_outputs:macro_auprc=0.313",
            "lineage:evaluation_outputs:macro_auroc=0.9062"})
        with open(self.registration_path, "rb") as handle:
            checksum = hashlib.sha256(handle.read()).hexdigest()
        self.assertIn("lineage:protected_checksum=" + checksum, tags)
        self.assertIn("lineage:protected_reference=" + self.registration_path, tags)

    def test_no_upload_action_or_artefact_content_is_sent(self):
        """Only metadata actions run; no asset path, hash or upload field is sent."""
        adapter = FakeAdapter()
        self.assertEqual(self.remote(adapter)["status"], "PASS")
        self.assertEqual(set(adapter.actions()), set(ALLOWED_ACTIONS))
        for action in adapter.actions():
            for word in ("upload", "artifact", "model", "file", "event"):
                self.assertNotIn(word, action.lower())
        self.assertEqual(set(adapter.created),
                         {"name", "type", "project", "comment", "tags"})
        sent = json.dumps([payload for _, payload in adapter.calls])
        for item in fixture_baseline()["assets"]:
            self.assertNotIn(json.dumps(item["relative_path"])[1:-1], sent)
        self.assertNotIn("a" * 64, sent)
        self.assertNotIn("d" * 64, sent)
        self.assertNotIn("patient", sent.lower())

    def test_readback_probe_failure_fails(self):
        """A readback mismatch fails the registration."""
        def mismatch(query, created):
            """Return a task whose tags differ from the payload."""
            task = dict(created, id=TASK_ID, tags=["unexpected"])
            return 200, {"data": {"tasks": [task]}}

        adapter = FakeAdapter(readback=mismatch)
        result = self.remote(adapter)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["findings"], ["readback_mismatch"])
        self.assertEqual(result["task_id"], TASK_ID)
        self.assertEqual(adapter.actions(), READBACK_ACTIONS)

    def test_readback_captured_field_fails(self):
        """Captured model/output fields on readback fail the
        registration."""
        def captured(query, created):
            """Return a task with a captured output destination."""
            task = dict(created, id=TASK_ID, output={"destination": "files"})
            return 200, {"data": {"tasks": [task]}}

        adapter = FakeAdapter(readback=captured)
        result = self.remote(adapter)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("readback_mismatch", result["findings"])
        self.assertEqual(adapter.actions().count("tasks.archive"), 0)

    def test_readback_arbitrary_exception_fails(self):
        """An unexpected readback exception fails and is sanitised."""
        def broken(query, created):
            """Raise an exception whose text must not be reported."""
            raise RuntimeError("fixture failure quoting token-value-xyz")

        adapter = FakeAdapter(readback=broken)
        result = self.remote(adapter)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["findings"], ["readback_failed"])
        self.assertEqual(adapter.actions().count("tasks.archive"), 0)
        self.assertNotIn("token-value-xyz", json.dumps(result))

    def test_registration_is_never_archived(self):
        """Whatever the tracker would answer to an archive, the
        registration never asks for one."""
        for response in ((403, {"meta": {"result_subcode": 14}}),
                         (200, {"data": {"archived": 0}})):
            adapter = FakeAdapter(archive_response=response)
            with self.subTest(response=response[0]):
                self.assertEqual(self.remote(adapter)["status"], "PASS")
                self.assertNotIn("tasks.archive", adapter.actions())

    def test_readback_failure_is_reported(self):
        """A failed readback is the registration's finding."""
        def broken(query, created):
            """Raise during readback."""
            raise RuntimeError("fixture")

        adapter = FakeAdapter(readback=broken)
        result = self.remote(adapter)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["findings"], ["readback_failed"])
        self.assertNotIn("tasks.completed", adapter.actions())

    def test_completion_failure_is_reported(self):
        """A registration read back but not marked completed fails."""
        for response in ((403, {"meta": {"result_subcode": 14}}),
                         (200, {"data": {"updated": 0}})):
            adapter = FakeAdapter(complete_response=response)
            with self.subTest(response=response[0]):
                result = self.remote(adapter)
                self.assertEqual(result["status"], "FAIL")
                self.assertIn(result["findings"][0], ("service_rejected",
                                                      "complete_failed"))

    def test_noisy_sdk_output_is_sanitized(self):
        """SDK stdout, stderr and logging are discarded; the result is clean."""
        noise_value = "fixture-credential-value-123"

        class NoisyAdapter(FakeAdapter):
            """Adapter that writes a secret and a config path everywhere."""

            def __init__(self):
                """Emit noise while the SDK would be imported."""
                print("sdk import %s %s" % (noise_value, CONFIG_PATH))
                super(NoisyAdapter, self).__init__()

            def load_config(self):
                """Emit noise while loading configuration."""
                sys.stderr.write("sdk warning %s %s\n" % (noise_value, CONFIG_PATH))
                logging.getLogger("clearml").error("config %s", noise_value)
                return super(NoisyAdapter, self).load_config()

            def call(self, service, action, payload):
                """Emit noise on every request."""
                print("request %s %s" % (action, noise_value))
                return super(NoisyAdapter, self).call(service, action, payload)

        self.pin(fixture_baseline())
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = register.remote_result(
                valid_environment(), NoisyAdapter, self.baseline_path,
                self.registration_path, lstat=regular_file)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        serialized = json.dumps(result)
        self.assertNotIn(noise_value, serialized)
        self.assertNotIn(CONFIG_PATH, serialized)

    def test_invalid_baseline_fails_before_adapter_is_built(self):
        """A tampered or wrong-run baseline never reaches the SDK."""
        factory = mock.Mock(side_effect=AssertionError("adapter created"))
        baseline = fixture_baseline()
        baseline["canonical_run"]["name"] = "other_run"
        self.pin(baseline)
        result = register.remote_result(
            valid_environment(), factory, self.baseline_path,
            self.registration_path, lstat=regular_file)
        self.assertEqual(result, {"status": "FAIL", "mode": "operator_remote",
                                  "finding": "registration_failed"})
        factory.assert_not_called()
        self.assertFalse(os.path.exists(self.registration_path))

    def test_missing_config_fails_without_adapter(self):
        """No operator configuration means no SDK import or contact."""
        factory = mock.Mock(side_effect=AssertionError("adapter created"))
        self.pin(fixture_baseline())
        result = register.remote_result({}, factory, self.baseline_path,
                                         self.registration_path, lstat=regular_file)
        self.assertEqual(result["status"], "FAIL")
        factory.assert_not_called()


class FileAccessTests(ScratchCase):
    """Only the baseline and the registration manifest are ever opened."""

    def test_open_spy_sees_only_baseline_and_registration(self):
        """Offline and remote modes open no upstream or other file."""
        allowed = {os.path.normcase(os.path.abspath(path)) for path in (
            self.baseline_path, self.registration_path,
            self.registration_path + ".partial")}
        real_open = builtins.open
        opened = []

        def spy(file, *args, **kwargs):
            """Record every path opened through the builtin."""
            opened.append(os.path.normcase(os.path.abspath(os.fspath(file))))
            return real_open(file, *args, **kwargs)

        self.pin(fixture_baseline())
        runs = {
            "offline": lambda: register.offline_result(
                self.baseline_path, self.registration_path),
            "remote": lambda: register.remote_result(
                valid_environment(), FakeAdapter, self.baseline_path,
                self.registration_path, lstat=regular_file),
        }
        for mode, run in runs.items():
            del opened[:]
            clearml_loaded = "clearml" in sys.modules
            with self.subTest(mode=mode):
                with mock.patch.object(builtins, "open", spy):
                    result = run()
                self.assertEqual(result["status"], "PASS", result)
                self.assertTrue(opened)
                self.assertLessEqual(set(opened), allowed)
                self.assertIn(os.path.normcase(self.baseline_path), opened)
                self.assertEqual("clearml" in sys.modules, clearml_loaded)


if __name__ == "__main__":
    unittest.main()
