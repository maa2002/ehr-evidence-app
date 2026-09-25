"""Synthetic checks for ehr_evidence_app.tracking.policy (Step 9).

Every rejected value is invented and visibly synthetic, built in memory
from the approved policy. No ClearML SDK, service, credential, patient
record or protected artefact is used; the verifier runs with network
sockets disabled. The one subprocess case runs the verifier with the
interpreter running these tests and no bytecode writes. pytest's own
temporary and cache files follow PYTEST_ADDOPTS from config/paths.json.
"""

import contextlib
import copy
import io
import json
import os
import socket
import subprocess
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SRC_ROOT = os.path.join(REPO_ROOT, "src")
MODULE = "ehr_evidence_app.tracking.policy"
SCRIPT = os.path.join(SRC_ROOT, "ehr_evidence_app", "tracking", "policy.py")
POLICY_PATH = os.path.join(REPO_ROOT, "config", "clearml_policy.json")

from ehr_evidence_app.tracking import policy as verifier  # noqa: E402

# Built at run time so no secret-shaped literal appears in this source.
SYNTHETIC_TOKEN = "gh" + "p_" + "SYNTHETIC" * 5
SYNTHETIC_CREDENTIAL = "SYNTHETIC-" + "not-a-real-credential"
SYNTHETIC_PATIENT_TEXT = "person" + "_id=" + "424242" + " synthetic note"
SYNTHETIC_HOST = "https" + "://synthetic-tracker.invalid"


def approved_policy():
    """Return a fresh parsed copy of the approved repository policy."""
    with open(POLICY_PATH, "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


def rule_ids(policy):
    """Return the sorted distinct rule IDs the verifier reports."""
    return sorted(set(rule for _, rule in verifier.check_policy(policy)))


def no_network(*args, **kwargs):
    """Refuse any socket creation during a verifier run."""
    raise AssertionError("network access attempted")


class ApprovedPolicyTests(unittest.TestCase):
    """The approved repository policy."""

    def test_approved_policy_passes(self):
        """The real policy has no findings."""
        self.assertEqual(verifier.check_policy(approved_policy()), [])

    def test_main_passes_without_network_or_clearml(self):
        """main() prints PASS, opens no socket and imports no ClearML."""
        output = io.StringIO()
        with mock.patch.object(socket, "socket", no_network), \
                contextlib.redirect_stdout(output):
            status = verifier.main([])
        self.assertEqual(status, verifier.EXIT_PASS, output.getvalue())
        self.assertEqual(output.getvalue().splitlines()[-1], "RESULT: PASS")

    def test_subprocess_passes_and_loads_no_clearml(self):
        """Fresh interpreter runs pass and never load the SDK."""
        code = ("import sys; sys.path.insert(0, %r)\n"
                "from ehr_evidence_app.tracking import policy as verifier\n"
                "status = verifier.main([])\n"
                "print('CLEARML_LOADED=%%s' %% any(name == 'clearml' or "
                "name.startswith('clearml.') for name in sys.modules))\n"
                "sys.exit(status)\n" % SRC_ROOT)
        env = dict(os.environ, PYTHONPATH=SRC_ROOT)
        for label, command in (("script", [sys.executable, "-B", "-m",
                                           MODULE]),
                               ("module", [sys.executable, "-B", "-c",
                                           code])):
            with self.subTest(run=label):
                result = subprocess.run(
                    command, cwd=REPO_ROOT, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, universal_newlines=True,
                    timeout=120, check=False)
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)
                lines = result.stdout.splitlines()
                self.assertIn("RESULT: PASS", lines)
                if label == "module":
                    self.assertEqual(lines[-1], "CLEARML_LOADED=False")

    def test_policy_records_every_lineage_field_as_not_recorded(self):
        """All report-required lineage fields exist, none claimed recorded."""
        fields = approved_policy()["lineage_fields"]
        self.assertEqual(sorted(entry["name"] for entry in fields),
                         sorted(verifier.LINEAGE_FIELDS))
        for entry in fields:
            with self.subTest(field=entry["name"]):
                self.assertEqual(entry["status"], "defined_not_recorded")

    def test_policy_is_offline_and_unauthorised(self):
        """Remote tracking is not authorised and holds no endpoint."""
        remote = approved_policy()["remote_tracking"]
        self.assertEqual(remote["status"], "not_authorized")
        self.assertEqual(remote["mode"], "offline_only")
        for field in verifier.REMOTE_VALUE_FIELDS:
            self.assertIsNone(remote[field])


class RejectionTests(unittest.TestCase):
    """Synthetic policies the verifier must reject."""

    def assert_rejected(self, policy, rule):
        """Assert policy fails with rule among its findings."""
        self.assertIn(rule, rule_ids(policy))

    def test_concrete_remote_configuration(self):
        """A concrete endpoint, workspace or project is rejected."""
        for field, value in (("api_server", SYNTHETIC_HOST),
                             ("workspace", "synthetic-workspace"),
                             ("project", "synthetic-project"),
                             ("account", "synthetic-account")):
            with self.subTest(field=field):
                policy = approved_policy()
                policy["remote_tracking"][field] = value
                self.assert_rejected(policy, "remote_configuration_present")

    def test_remote_status_enabled(self):
        """Authorised or online status is rejected."""
        for field, value in (("status", "authorized"), ("mode", "online")):
            with self.subTest(field=field):
                policy = approved_policy()
                policy["remote_tracking"][field] = value
                self.assert_rejected(policy, "remote_tracking_enabled")

    def test_package_presence_cannot_enable_remote(self):
        """Treating the installed package as remote enablement fails."""
        policy = approved_policy()
        policy["tracker"]["package_installed_implies_remote_enabled"] = True
        self.assert_rejected(policy, "remote_enabled_by_package")

    def test_url_anywhere_is_remote_configuration(self):
        """A URL in free text is treated as remote configuration."""
        policy = approved_policy()
        policy["notes"].append("Synthetic server " + SYNTHETIC_HOST)
        self.assert_rejected(policy, "remote_configuration_present")

    def test_patient_bearing_category_permitted(self):
        """Patient, raw-content or credential categories cannot be sent."""
        for category in ("patient_records", "raw_clinical_content",
                         "prompts", "llm_responses", "credentials",
                         "synthetic_patient_notes"):
            with self.subTest(category=category):
                policy = approved_policy()
                policy["categories"]["permitted_tracker_payloads"].append(
                    category)
                self.assert_rejected(policy,
                                     "patient_bearing_category_permitted")

    def test_unknown_permitted_category(self):
        """A permitted category outside the approved set is rejected."""
        policy = approved_policy()
        policy["categories"]["permitted_tracker_payloads"].append(
            "synthetic_extra_metrics")
        self.assert_rejected(policy, "unknown_permitted_category")

    def test_forbidden_category_removed(self):
        """Dropping a mandatory forbidden category is rejected."""
        policy = approved_policy()
        policy["categories"]["forbidden_tracker_payloads"].remove(
            "attribution_text")
        self.assert_rejected(policy, "forbidden_category_missing")

    def test_secret_value(self):
        """Secret-named values and token-shaped strings are rejected."""
        cases = []
        policy = approved_policy()
        policy["credential_handling"]["api_key"] = SYNTHETIC_CREDENTIAL
        cases.append(("secret-named key", policy))
        policy = approved_policy()
        policy["notes"].append(SYNTHETIC_TOKEN)
        cases.append(("token shape", policy))
        policy = approved_policy()
        policy["remote_tracking"]["secret"] = SYNTHETIC_CREDENTIAL
        cases.append(("remote secret", policy))
        for label, policy in cases:
            with self.subTest(case=label):
                self.assert_rejected(policy, "secret_present")

    def test_patient_content_value(self):
        """A patient-identifier assignment anywhere is rejected."""
        policy = approved_policy()
        policy["notes"].append(SYNTHETIC_PATIENT_TEXT)
        self.assert_rejected(policy, "patient_content_present")

    def test_runtime_protected_destination(self):
        """Protected artefacts or server volumes on the runtime root are rejected."""
        runtime = verifier.RUNTIME_ROOT + "\\synthetic-protected"
        cases = []
        policy = approved_policy()
        policy["destinations"]["protected_case_artefacts"]["root_path"] = (
            runtime)
        cases.append(("protected root", policy))
        policy = approved_policy()
        policy["local_server"]["volume_root_required"] = runtime
        cases.append(("server volume", policy))
        policy = approved_policy()
        policy["destinations"]["runtime_cache"][
            "protected_data_permitted"] = True
        cases.append(("runtime protected data", policy))
        policy = approved_policy()
        policy["destinations"]["runtime_cache"][
            "local_server_volumes_permitted"] = True
        cases.append(("runtime server volumes", policy))
        for label, policy in cases:
            with self.subTest(case=label):
                self.assert_rejected(policy, "runtime_protected_destination")

    def test_protected_destination_outside_w(self):
        """A protected root outside the workspace is rejected."""
        policy = approved_policy()
        policy["destinations"]["protected_case_artefacts"]["root_path"] = (
            "C:\\synthetic-protected")
        self.assert_rejected(policy, "protected_destination_outside_workspace")

    def test_missing_separation(self):
        """Merged or overlapping metadata/protected categories fail."""
        cases = []
        policy = approved_policy()
        del policy["destinations"]["protected_case_artefacts"]
        cases.append(("protected destination removed", policy))
        policy = approved_policy()
        policy["destinations"]["protected_case_artefacts"][
            "sent_to_tracker"] = True
        cases.append(("protected sent to tracker", policy))
        policy = approved_policy()
        policy["destinations"]["tracker_metadata"]["patient_bearing"] = True
        cases.append(("tracker metadata patient-bearing", policy))
        policy = approved_policy()
        policy["destinations"]["tracker_metadata"]["categories"] = (
            "forbidden_tracker_payloads")
        cases.append(("tracker uses protected categories", policy))
        policy = approved_policy()
        policy["categories"]["forbidden_tracker_payloads"].append(
            "aggregate_evaluation_metrics")
        cases.append(("overlapping categories", policy))
        policy = approved_policy()
        del policy["categories"]["forbidden_tracker_payloads"]
        cases.append(("forbidden list removed", policy))
        for label, policy in cases:
            with self.subTest(case=label):
                self.assert_rejected(policy, "missing_separation")

    def test_invalid_lineage_field(self):
        """Wrong, duplicated or recorded lineage fields are rejected."""
        mutations = (
            ("recorded status", lambda f: f[0].update(status="recorded")),
            ("unknown name", lambda f: f.append(dict(
                f[0], name="synthetic_field"))),
            ("duplicate", lambda f: f.append(dict(f[0]))),
            ("content form", lambda f: f[3].update(
                tracked_as="prompt_text")),
            ("raw evaluation", lambda f: f[6].update(
                tracked_as="fingerprint")),
            ("extra value", lambda f: f[0].update(value="synthetic")),
            ("not an object", lambda f: f.append("synthetic")),
        )
        for label, mutate in mutations:
            with self.subTest(case=label):
                policy = approved_policy()
                mutate(policy["lineage_fields"])
                self.assert_rejected(policy, "invalid_lineage_field")

    def test_missing_lineage_field(self):
        """Removing a required lineage field is rejected."""
        policy = approved_policy()
        policy["lineage_fields"] = [
            entry for entry in policy["lineage_fields"]
            if entry["name"] != "prompt_version"]
        self.assert_rejected(policy, "lineage_field_missing")

    def test_step_10_boundary(self):
        """Claiming SDK configuration here is rejected."""
        policy = approved_policy()
        policy["step_10"]["sdk_configuration"] = "configured"
        self.assert_rejected(policy, "step_10_boundary_invalid")

    def test_malformed_values_do_not_crash(self):
        """Wrong JSON types produce findings, not exceptions."""
        for section in ("tracker", "remote_tracking", "categories",
                        "destinations", "local_server", "lineage_fields"):
            for value in (None, [], "synthetic", {"root": []}):
                with self.subTest(section=section, value=value):
                    policy = approved_policy()
                    policy[section] = value
                    self.assertTrue(verifier.check_policy(policy))
        self.assertEqual(verifier.check_policy([]),
                         [("schema", "policy_not_object")])


class DiagnosticsTests(unittest.TestCase):
    """Output safety and determinism."""

    def test_report_never_echoes_values(self):
        """FAIL output holds rule IDs only, not synthetic values."""
        policy = approved_policy()
        policy["credential_handling"]["api_key"] = SYNTHETIC_CREDENTIAL
        policy["notes"].extend([SYNTHETIC_TOKEN, SYNTHETIC_PATIENT_TEXT])
        policy["remote_tracking"]["api_server"] = SYNTHETIC_HOST
        policy["categories"]["permitted_tracker_payloads"].append(
            "synthetic_patient_notes")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            verifier.report("synthetic-policy", verifier.check_policy(policy))
        text = output.getvalue()
        self.assertTrue(text.splitlines()[-1].startswith("RESULT: FAIL"))
        for value in (SYNTHETIC_CREDENTIAL, SYNTHETIC_TOKEN, "424242",
                      SYNTHETIC_HOST, "synthetic-tracker",
                      "synthetic_patient_notes"):
            self.assertNotIn(value, text)

    def test_findings_are_deterministic(self):
        """Repeated checks return identical, sorted findings."""
        policy = approved_policy()
        policy["remote_tracking"]["project"] = "synthetic-project"
        policy["lineage_fields"].append("synthetic")
        first = verifier.check_policy(copy.deepcopy(policy))
        self.assertEqual(first, verifier.check_policy(policy))
        self.assertEqual(first, sorted(first, key=lambda item: (
            verifier.SECTIONS.index(item[0]), item[1])))

    def test_policy_outside_config_is_operational_failure(self):
        """A policy path outside the config directory is refused unread."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = verifier.main(["--config", SCRIPT])
        self.assertEqual(status, verifier.EXIT_OPERATIONAL)
        self.assertIn("RESULT: OPERATIONAL FAILURE", output.getvalue())

    def test_duplicate_keys_refused(self):
        """Repeated JSON keys cannot hide a value."""
        with self.assertRaises(verifier.OperationalError):
            json.loads('{"step": "9", "step": "9"}',
                       object_pairs_hook=verifier.reject_duplicate_keys)


if __name__ == "__main__":
    unittest.main()
