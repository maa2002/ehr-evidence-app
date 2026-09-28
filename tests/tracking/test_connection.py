"""Synthetic checks for ehr_evidence_app.tracking.connection (Step 10).

Every value here is invented and visibly synthetic. No ClearML SDK,
service, credential, configuration file, patient record or protected
artefact is used: live-probe logic runs against in-memory fake adapters
and fake SDK modules, configuration files are represented by fake lstat
results (nothing is created on disk), and sockets are disabled for
in-process runs. Subprocess cases run with every CLEARML_/TRAINS_
variable removed and never load the SDK. pytest's own temporary and
cache files follow PYTEST_ADDOPTS from config/paths.json.
"""

import contextlib
import copy
import io
import json
import os
import re
import socket
import stat
import subprocess
import sys
import types
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
SRC_ROOT = os.path.join(REPO_ROOT, "src")
MODULE = "ehr_evidence_app.tracking.connection"
SCRIPT = os.path.join(SRC_ROOT, "ehr_evidence_app", "tracking",
                      "connection.py")
DECLARATION_PATH = os.path.join(REPO_ROOT, "config",
                                "clearml_connection.json")
DOC_PATH = os.path.join(REPO_ROOT, "docs", "experiment-tracking.md")

from ehr_evidence_app.tracking import connection as checker  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402

# Built at run time so no path-, secret- or URL-shaped literal appears in
# this source.
SYNTHETIC_CONFIG_PATH = ("C:" + "\\" + "Users" + "\\synthetic-account"
                         "\\synthetic-tracker.conf")
SYNTHETIC_SENSITIVE = "SYNTHETIC-" + "not-a-real-sensitive-value"
SYNTHETIC_EMAIL = "synthetic-user" + "@" + "example.invalid"
SYNTHETIC_USER = "Synthetic Person"
SYNTHETIC_COMPANY_ID = "c0" * 16
SYNTHETIC_PROJECT_ID = "a1" * 16
SYNTHETIC_NEW_PROJECT_ID = "b2" * 16
SYNTHETIC_TASK_ID = "d3" * 16
SYNTHETIC_HOST = "https" + "://api.synthetic-tracker.invalid"
SYNTHETIC_CACHE = guard.RUNTIME_ROOT + "\\cache\\clearml"


def approved_declaration():
    """Return a fresh parsed copy of the approved repository declaration."""
    with open(DECLARATION_PATH, "rb") as handle:
        return json.loads(handle.read().decode("utf-8"))


def rule_ids(findings):
    """Return the sorted distinct rule IDs in a list of findings."""
    return sorted(set(item[1] for item in findings))


def no_network(*args, **kwargs):
    """Refuse any socket creation during a checker run."""
    raise AssertionError("network access attempted")


def regular_file(path):
    """Return fake lstat info for a plain regular file (never on disk)."""
    return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600,
                                 st_file_attributes=0)


def missing_file(path):
    """Fake lstat for a path that does not exist."""
    raise FileNotFoundError(2, "synthetic missing file")


def link_file(path):
    """Return fake lstat info for a reparse point."""
    return types.SimpleNamespace(st_mode=stat.S_IFREG | 0o600,
                                 st_file_attributes=guard.REPARSE_POINT)


def valid_environ():
    """Return a synthetic probe environment that passes every check."""
    return {checker.CONFIG_VARIABLE: SYNTHETIC_CONFIG_PATH,
            checker.CACHE_VARIABLE: SYNTHETIC_CACHE}


def service_responses():
    """Return the default synthetic service responses by action."""
    def task_readback(payload, created):
        """Return the created task exactly as submitted, with no script."""
        task = dict(created, id=SYNTHETIC_TASK_ID, script={})
        del task["comment"]
        return 200, {"data": {"tasks": [task]}}

    return {
        "users.get_current_user": (200, {"data": {"user": {
            "id": SYNTHETIC_COMPANY_ID, "name": SYNTHETIC_USER,
            "email": SYNTHETIC_EMAIL,
            "company": {"id": SYNTHETIC_COMPANY_ID,
                        "name": checker.WORKSPACE}}}}),
        "projects.get_all": (200, {"data": {"projects": [
            {"id": SYNTHETIC_PROJECT_ID, "name": checker.PROJECT}]}}),
        "projects.create": (200, {"data": {"id": SYNTHETIC_NEW_PROJECT_ID}}),
        "tasks.create": (200, {"data": {"id": SYNTHETIC_TASK_ID}}),
        "tasks.get_all": task_readback,
        "tasks.archive": (200, {"data": {"archived": 1}}),
    }


class FakeAdapter(object):
    """In-memory stand-in for the SDK adapter that records every call."""

    def __init__(self, hosts=None, offline=False, connect_error=None,
                 noisy=False, api_213=False, **overrides):
        """Configure hosts, failures and per-action response overrides."""
        self.hosts = dict(checker.ENDPOINTS) if hosts is None else hosts
        self.is_offline = offline
        self.connect_error = connect_error
        self.noisy = noisy
        self.api_213 = api_213
        self.responses = service_responses()
        self.responses.update(overrides)
        self.connected = None
        self.calls = []
        self.created = None

    def offline(self):
        """Return the synthetic offline flag."""
        return self.is_offline

    def supports_api_version(self, version):
        """Return a locally negotiated synthetic API level."""
        self.calls.append(("api.version_check", version))
        return self.api_213 and version == "2.13"

    def load_config(self):
        """Return a synthetic config, optionally printing a fake secret."""
        if self.noisy:
            print("synthetic sdk output %s %s" % (SYNTHETIC_SENSITIVE,
                                                  SYNTHETIC_CONFIG_PATH))
            sys.stderr.write("synthetic sdk warning %s\n"
                             % SYNTHETIC_SENSITIVE)
        return "synthetic-config"

    def configured_hosts(self, config):
        """Return the synthetic configured hosts."""
        return dict(self.hosts)

    def connect(self, config, api_server):
        """Record the connection target; optionally raise a failure."""
        self.connected = api_server
        if self.connect_error is not None:
            raise self.connect_error

    def call(self, service, action, payload):
        """Record the request and return the configured response."""
        name = "%s.%s" % (service, action)
        self.calls.append((name, copy.deepcopy(payload)))
        if name == "tasks.create":
            self.created = copy.deepcopy(payload)
        response = self.responses[name]
        if callable(response):
            return response(payload, self.created)
        return copy.deepcopy(response)

    def actions(self):
        """Return the names of the requested service actions in order."""
        return [name for name, _ in self.calls]


def run_main(argv, adapter=None, environ=None, lstat=regular_file):
    """Run checker.main with synthetic inputs; return (status, output)."""
    output = io.StringIO()
    factory = (lambda: adapter) if adapter is not None else no_factory
    with mock.patch.object(socket, "socket", no_network), \
            contextlib.redirect_stdout(output):
        status = checker.main(argv, environ=(valid_environ() if environ
                                             is None else environ),
                              adapter_factory=factory, lstat=lstat)
    return status, output.getvalue()


def no_factory():
    """Fail if the SDK adapter is created when it must not be."""
    raise AssertionError("the SDK adapter must not be created")


def probe(create_task, adapter, environ=None, lstat=regular_file):
    """Run checker.run_probe with a fake adapter; return its findings."""
    with mock.patch.object(socket, "socket", no_network):
        findings, _, _ = checker.run_probe(
            create_task, valid_environ() if environ is None else environ,
            lambda: adapter, lstat=lstat)
    return findings


def clean_environment():
    """Return os.environ without any CLEARML_ or TRAINS_ variable."""
    return dict((name, value) for name, value in os.environ.items()
                if not name.upper().startswith(("CLEARML_", "TRAINS_")))


class ApprovedDeclarationTests(unittest.TestCase):
    """The approved repository declaration and offline default mode."""

    def test_approved_declaration_passes(self):
        """The real declaration has no findings."""
        self.assertEqual(checker.check_declaration(approved_declaration()),
                         [])

    def test_declaration_records_authorised_target(self):
        """Endpoints, workspace, project and variable name are recorded."""
        declaration = approved_declaration()
        self.assertEqual(declaration["endpoints"], checker.ENDPOINTS)
        self.assertEqual(declaration["workspace"],
                         "Abdullah  Aamir's workspace")
        self.assertEqual(declaration["project"], "ehr-evidence-app")
        self.assertEqual(declaration["configuration_file"][
            "environment_variable"], "CLEARML_CONFIG_FILE")
        self.assertIs(declaration["configuration_file"]["path_recorded"],
                      False)

    def test_auto_connect_and_capture_disabled(self):
        """Every framework, source and environment capture flag is off."""
        task = approved_declaration()["synthetic_task"]
        for flag in checker.CAPTURE_FLAGS:
            with self.subTest(flag=flag):
                self.assertIs(task[flag], False)
        self.assertEqual(task["max_tasks_per_run"], 1)

    def test_model_artefacts_limited_to_listed_models(self):
        """Only the listed pretrained model may be uploaded, with its
        manifest, notice and agreement; patient-derived models never."""
        artefacts = approved_declaration()["model_artefacts"]
        self.assertEqual(artefacts["status"], "enabled_for_listed_models")
        self.assertIs(artefacts["upload_in_step_10"], False)
        self.assertEqual(artefacts["listed_models"],
                         ["google/medgemma-1.5-4b-it"])
        self.assertTrue(artefacts["licence"]["notice_file_required"])
        self.assertTrue(artefacts["licence"]["agreement_copy_required"])
        for kind in ("patient_derived_models", "gnn_checkpoints", "prompts"):
            self.assertIn(kind, artefacts["always_excluded"])
        for field in ("sha256", "provenance",
                      "non_patient_non_secret_confirmation"):
            self.assertIn(field, artefacts["manifest_fields"])

    def test_default_mode_passes_without_network_or_sdk(self):
        """Declaration mode passes and never builds the SDK adapter."""
        status, output = run_main([], environ={})
        self.assertEqual(status, checker.EXIT_PASS, output)
        self.assertEqual(output.splitlines()[-1], "RESULT: PASS")

    def test_subprocess_runs_never_load_clearml(self):
        """Fresh interpreters pass declaration mode and fail preflight
        closed without credentials, never loading the SDK."""
        code = ("import sys; sys.path.insert(0, %r)\n"
                "from ehr_evidence_app.tracking import connection as checker\n"
                "status = checker.main(sys.argv[1:])\n"
                "print('CLEARML_LOADED=%%s' %% any(name == 'clearml' or "
                "name.startswith('clearml.') for name in sys.modules))\n"
                "sys.exit(status)\n" % SRC_ROOT)
        cases = (("script", [sys.executable, "-B", "-m", MODULE], 0),
                 ("module", [sys.executable, "-B", "-c", code], 0),
                 ("preflight", [sys.executable, "-B", "-c", code,
                                "--preflight"], 1),
                 ("synthetic-task", [sys.executable, "-B", "-c", code,
                                     "--synthetic-task"], 1))
        for label, command, expected in cases:
            with self.subTest(run=label):
                result = subprocess.run(
                    command, cwd=REPO_ROOT,
                    env=dict(clean_environment(), PYTHONPATH=SRC_ROOT),
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    universal_newlines=True, timeout=120, check=False)
                self.assertEqual(result.returncode, expected,
                                 result.stdout + result.stderr)
                lines = result.stdout.splitlines()
                if label != "script":
                    self.assertEqual(lines[-1], "CLEARML_LOADED=False")
                if expected:
                    self.assertIn("config_variable_missing", result.stdout)
                    self.assertIn("RESULT: FAIL (", result.stdout)
                else:
                    self.assertIn("RESULT: PASS", lines)


class DeclarationRejectionTests(unittest.TestCase):
    """Synthetic declarations the checker must reject."""

    def assert_rejected(self, declaration, rule):
        """Assert the declaration fails with rule among its findings."""
        self.assertIn(rule, rule_ids(checker.check_declaration(declaration)))

    def test_wrong_endpoint(self):
        """Another host, plain HTTP or a missing role is rejected."""
        for role, value in (("api_server", SYNTHETIC_HOST),
                            ("web_server", "http" + "://app.clear.ml"),
                            ("files_server", None)):
            with self.subTest(role=role):
                declaration = approved_declaration()
                declaration["endpoints"][role] = value
                self.assert_rejected(declaration, "endpoint_not_approved")

    def test_url_in_free_text(self):
        """An unapproved URL anywhere in the declaration is rejected."""
        declaration = approved_declaration()
        declaration["notes"].append("Synthetic server " + SYNTHETIC_HOST)
        self.assert_rejected(declaration, "endpoint_not_approved")

    def test_wrong_workspace_or_project(self):
        """A different workspace or project name is rejected."""
        for field, rule in (("workspace", "workspace_not_approved"),
                            ("project", "project_not_approved")):
            with self.subTest(field=field):
                declaration = approved_declaration()
                declaration[field] = "synthetic-other"
                self.assert_rejected(declaration, rule)

    def test_workspace_must_match_exactly(self):
        """Single-space, case or whitespace variants are rejected."""
        for value in ("Abdullah Aamir's workspace",
                      "abdullah  aamir's workspace",
                      " Abdullah  Aamir's workspace"):
            with self.subTest(value=value):
                declaration = approved_declaration()
                declaration["workspace"] = value
                self.assert_rejected(declaration, "workspace_not_approved")

    def test_unauthorised_or_offline_mode(self):
        """An unauthorised status, offline mode or fallback is rejected."""
        for field, value in (("status", "not_authorized"),
                             ("mode", "offline_only"),
                             ("offline_fallback", True)):
            with self.subTest(field=field):
                declaration = approved_declaration()
                declaration["authorization"][field] = value
                self.assert_rejected(declaration, "remote_not_authorized")

    def test_auto_capture_enabled(self):
        """Turning on any capture flag is rejected."""
        for flag in checker.CAPTURE_FLAGS:
            with self.subTest(flag=flag):
                declaration = approved_declaration()
                declaration["synthetic_task"][flag] = True
                self.assert_rejected(declaration, "auto_capture_enabled")

    def test_model_artefact_upload_enabled(self):
        """Enabling artefact upload in Step 10 is rejected."""
        declaration = approved_declaration()
        declaration["model_artefacts"]["upload_in_step_10"] = True
        self.assert_rejected(declaration, "model_artefact_enabled")
        declaration = approved_declaration()
        declaration["model_artefacts"]["always_excluded"].remove("prompts")
        self.assert_rejected(declaration, "model_artefact_gate_invalid")
        declaration = approved_declaration()
        declaration["model_artefacts"]["listed_models"].append(
            "inherited/gnn-checkpoint")
        self.assert_rejected(declaration, "model_artefact_gate_invalid")

    def test_config_path_or_secret_recorded(self):
        """A local path, key value or token shape in the file is rejected."""
        declaration = approved_declaration()
        declaration["configuration_file"]["path"] = SYNTHETIC_CONFIG_PATH
        self.assert_rejected(declaration, "local_path_present")
        self.assert_rejected(declaration, "config_reference_invalid")
        declaration = approved_declaration()
        declaration["configuration_file"]["secret_key"] = SYNTHETIC_SENSITIVE
        self.assert_rejected(declaration, "secret_present")
        declaration = approved_declaration()
        declaration["notes"].append("gh" + "p_" + "SYNTHETIC" * 5)
        self.assert_rejected(declaration, "secret_present")
        declaration = approved_declaration()
        declaration["notes"].append("person" + "_id=" + "424242")
        self.assert_rejected(declaration, "patient_content_present")

    def test_malformed_values_do_not_crash(self):
        """Wrong JSON types produce findings, not exceptions."""
        for section in ("tracker", "authorization", "endpoints",
                        "configuration_file", "synthetic_task",
                        "model_artefacts", "workspace"):
            for value in (None, [], "synthetic", {"status": []}):
                with self.subTest(section=section, value=value):
                    declaration = approved_declaration()
                    declaration[section] = value
                    self.assertTrue(checker.check_declaration(declaration))
        self.assertEqual(checker.check_declaration([]),
                         [("schema", "declaration_not_object", "")])

    def test_declaration_outside_config_is_operational_failure(self):
        """A declaration path outside the config directory is refused."""
        status, output = run_main(["--config", SCRIPT], environ={})
        self.assertEqual(status, checker.EXIT_OPERATIONAL)
        self.assertIn("RESULT: OPERATIONAL FAILURE", output)


class EnvironmentTests(unittest.TestCase):
    """Fail-closed probe environment checks (before any SDK import)."""

    def rules(self, environ, lstat=regular_file):
        """Return the environment rule IDs for a synthetic environment."""
        return rule_ids(checker.environment_problems(environ, lstat))

    def test_valid_environment(self):
        """A process-local config file and runtime cache pass."""
        self.assertEqual(self.rules(valid_environ()), [])

    def test_missing_config_variable(self):
        """An unset or blank CLEARML_CONFIG_FILE fails."""
        for value in (None, "", "   "):
            with self.subTest(value=value):
                environ = valid_environ()
                if value is None:
                    del environ[checker.CONFIG_VARIABLE]
                else:
                    environ[checker.CONFIG_VARIABLE] = value
                self.assertEqual(self.rules(environ),
                                 ["config_variable_missing"])

    def test_config_path_problems(self):
        """Relative, missing, linked or in-repository files fail."""
        cases = (("relative", "synthetic.conf", regular_file,
                  "config_path_not_absolute"),
                 ("missing", SYNTHETIC_CONFIG_PATH, missing_file,
                  "config_file_missing"),
                 ("link", SYNTHETIC_CONFIG_PATH, link_file,
                  "config_file_not_regular"),
                 ("in repository", os.path.join(REPO_ROOT, "synthetic.conf"),
                  regular_file, "config_file_inside_repository"))
        for label, path, lstat, rule in cases:
            with self.subTest(case=label):
                environ = valid_environ()
                environ[checker.CONFIG_VARIABLE] = path
                self.assertEqual(self.rules(environ, lstat), [rule])

    def test_offline_mode_fails(self):
        """Offline mode is a failed preflight, never a fallback."""
        for name in checker.OFFLINE_VARIABLES:
            with self.subTest(variable=name):
                environ = valid_environ()
                environ[name] = "1"
                self.assertEqual(self.rules(environ), ["offline_mode_set"])

    def test_override_variables_fail(self):
        """Host, key, token or alternative-config overrides fail."""
        for name in ("CLEARML_API_HOST", "CLEARML_API_ACCESS_KEY",
                     "CLEARML_API_SECRET_KEY", "CLEARML_AUTH_TOKEN",
                     "TRAINS_CONFIG_FILE", "CLEARML_API_HOST_VERIFY_CERT"):
            with self.subTest(variable=name):
                environ = valid_environ()
                environ[name] = SYNTHETIC_SENSITIVE
                self.assertEqual(self.rules(environ),
                                 ["override_variable_set"])

    def test_cache_destination(self):
        """The SDK cache must be under the configured runtime root."""
        for value in (None, "", "C:\\synthetic-cache", guard.RUNTIME_ROOT):
            with self.subTest(value=value):
                environ = valid_environ()
                if value is None:
                    del environ[checker.CACHE_VARIABLE]
                else:
                    environ[checker.CACHE_VARIABLE] = value
                self.assertEqual(self.rules(environ),
                                 ["cache_destination_invalid"])

    def test_environment_failure_never_imports_sdk(self):
        """A failed environment check stops before the adapter exists."""
        environ = valid_environ()
        environ["CLEARML_OFFLINE_MODE"] = "1"
        for argv in (["--preflight"], ["--synthetic-task"]):
            with self.subTest(argv=argv):
                status, output = run_main(argv, environ=environ)
                self.assertEqual(status, checker.EXIT_FAIL, output)
                self.assertIn("offline_mode_set", output)


class ProbeTests(unittest.TestCase):
    """Live-probe logic against a synthetic in-memory adapter."""

    def test_preflight_success_creates_nothing(self):
        """The preflight authenticates, reads and never writes."""
        adapter = FakeAdapter()
        status, output = run_main(["--preflight"], adapter)
        self.assertEqual(status, checker.EXIT_PASS, output)
        self.assertEqual(adapter.actions(), ["users.get_current_user",
                                             "projects.get_all"])
        self.assertEqual(adapter.connected, checker.ENDPOINTS["api_server"])
        self.assertIn("Workspace        : confirmed Abdullah  Aamir's "
                      "workspace", output)
        self.assertIn("present ehr-evidence-app", output)

    def test_preflight_with_absent_project(self):
        """An absent project is reported, not created, in preflight."""
        adapter = FakeAdapter(**{"projects.get_all": (
            200, {"data": {"projects": []}})})
        self.assertEqual(probe(False, adapter), [])
        self.assertNotIn("projects.create", adapter.actions())

    def test_synthetic_task_success(self):
        """Exactly one metadata-only task is created, checked, archived."""
        adapter = FakeAdapter()
        status, output = run_main(["--synthetic-task"], adapter)
        self.assertEqual(status, checker.EXIT_PASS, output)
        self.assertEqual(adapter.actions(), [
            "users.get_current_user", "projects.get_all", "tasks.create",
            "tasks.get_all", "tasks.archive"])
        payload = adapter.created
        self.assertEqual(sorted(payload), sorted(checker.TASK_PAYLOAD_KEYS))
        self.assertEqual(payload["project"], SYNTHETIC_PROJECT_ID)
        self.assertIn("SYNTHETIC", payload["name"])
        self.assertIn("created %s" % SYNTHETIC_TASK_ID, output)
        self.assertIn("Archive          : archived", output)

    def test_synthetic_task_creates_absent_project(self):
        """The project is created only when absent, then used once."""
        adapter = FakeAdapter(**{"projects.get_all": (
            200, {"data": {"projects": []}})})
        self.assertEqual(probe(True, adapter), [])
        self.assertEqual(adapter.actions().count("projects.create"), 1)
        self.assertEqual(adapter.actions().count("tasks.create"), 1)
        self.assertEqual(adapter.created["project"],
                         SYNTHETIC_NEW_PROJECT_ID)

    def test_sdk_offline_mode_fails(self):
        """An SDK started offline fails before any connection."""
        adapter = FakeAdapter(offline=True)
        self.assertEqual(rule_ids(probe(True, adapter)),
                         ["offline_mode_set"])
        self.assertIsNone(adapter.connected)
        self.assertEqual(adapter.calls, [])

    def test_wrong_configured_endpoint_fails_before_connect(self):
        """A mismatched host fails before credentials are sent."""
        for role in checker.ENDPOINT_ROLES:
            with self.subTest(role=role):
                hosts = dict(checker.ENDPOINTS)
                hosts[role] = SYNTHETIC_HOST
                adapter = FakeAdapter(hosts=hosts)
                findings = probe(True, adapter)
                self.assertEqual(findings, [("connection",
                                             "endpoint_mismatch", role)])
                self.assertIsNone(adapter.connected)

    def test_trailing_slash_endpoint_accepted(self):
        """A configured host with a trailing slash is the same host."""
        hosts = dict((role, url + "/")
                     for role, url in checker.ENDPOINTS.items())
        self.assertEqual(probe(False, FakeAdapter(hosts=hosts)), [])

    def test_authentication_failures_are_sanitised(self):
        """SDK exceptions map to rule IDs; their messages are withheld."""
        cases = (("MissingConfigError", "credentials_missing"),
                 ("LoginError", "authentication_failed"),
                 ("ConnectionError", "service_unreachable"),
                 ("SyntheticUnexpectedError", "sdk_failure"))
        for name, rule in cases:
            with self.subTest(error=name):
                error = type(name, (Exception,), {})(SYNTHETIC_SENSITIVE)
                adapter = FakeAdapter(connect_error=error)
                status, output = run_main(["--synthetic-task"], adapter)
                self.assertEqual(status, checker.EXIT_FAIL, output)
                self.assertIn(rule, output)
                self.assertNotIn(SYNTHETIC_SENSITIVE, output)
                self.assertEqual(adapter.calls, [])

    def test_wrong_workspace(self):
        """Another or unreported workspace name fails closed."""
        cases = (("other", {"name": "synthetic-other-workspace"},
                  "workspace_mismatch"),
                 ("single space", {"name": "Abdullah Aamir's workspace"},
                  "workspace_mismatch"),
                 ("trailing space", {"name": checker.WORKSPACE + " "},
                  "workspace_mismatch"),
                 ("missing name", {"id": SYNTHETIC_COMPANY_ID},
                  "workspace_unverified"),
                 ("missing company", None, "workspace_unverified"))
        for label, company, rule in cases:
            with self.subTest(case=label):
                user = {"name": SYNTHETIC_USER}
                if company is not None:
                    user["company"] = company
                adapter = FakeAdapter(**{"users.get_current_user": (
                    200, {"data": {"user": user}})})
                self.assertEqual(rule_ids(probe(True, adapter)), [rule])
                self.assertNotIn("tasks.create", adapter.actions())

    def test_ambiguous_project(self):
        """Two projects with the declared name fail closed."""
        adapter = FakeAdapter(**{"projects.get_all": (200, {"data": {
            "projects": [{"id": SYNTHETIC_PROJECT_ID,
                          "name": checker.PROJECT},
                         {"id": SYNTHETIC_NEW_PROJECT_ID,
                          "name": checker.PROJECT}]}})})
        self.assertEqual(rule_ids(probe(True, adapter)),
                         ["project_ambiguous"])
        self.assertNotIn("tasks.create", adapter.actions())

    def test_service_rejects_synthetic_task(self):
        """A rejected task create fails once, with no retry or fallback."""
        adapter = FakeAdapter(**{"tasks.create": (403, {
            "meta": {"result_subcode": 12,
                     "result_msg": SYNTHETIC_SENSITIVE}})})
        status, output = run_main(["--synthetic-task"], adapter)
        self.assertEqual(status, checker.EXIT_FAIL, output)
        self.assertIn("service_rejected [tasks.create HTTP 403 subcode 12]",
                      output)
        self.assertNotIn(SYNTHETIC_SENSITIVE, output)
        self.assertEqual(adapter.actions().count("tasks.create"), 1)

    def test_automatic_capture_detected(self):
        """A read-back task holding source or package content fails."""
        def captured(payload, created):
            """Return the task with a synthetic captured repository."""
            task = dict(created, id=SYNTHETIC_TASK_ID,
                        script={"repository": "synthetic-repository"})
            return 200, {"data": {"tasks": [task]}}

        adapter = FakeAdapter(**{"tasks.get_all": captured})
        self.assertEqual(probe(True, adapter),
                         [("task", "automatic_capture_detected",
                           "repository")])

    def test_archive_failure_is_reported_not_fatal(self):
        """A created, verified task that cannot be archived still passes."""
        adapter = FakeAdapter(**{"tasks.archive": (400, {})})
        status, output = run_main(["--synthetic-task"], adapter)
        self.assertEqual(status, checker.EXIT_PASS, output)
        self.assertIn("not archived (tasks.archive HTTP 400)", output)


class LeakageTests(unittest.TestCase):
    """Sanitised diagnostics and tracked-file content."""

    def test_output_withholds_path_secret_and_identity(self):
        """No config path, secret, user or company value is printed."""
        adapter = FakeAdapter(noisy=True)
        status, output = run_main(["--synthetic-task"], adapter)
        self.assertEqual(status, checker.EXIT_PASS, output)
        for value in (SYNTHETIC_CONFIG_PATH, "synthetic-account",
                      "synthetic-tracker.conf", SYNTHETIC_SENSITIVE,
                      SYNTHETIC_EMAIL, SYNTHETIC_USER, SYNTHETIC_COMPANY_ID,
                      "synthetic sdk"):
            self.assertNotIn(value, output)
        self.assertIn("CLEARML_CONFIG_FILE set (path withheld)", output)

    def test_task_payload_is_metadata_only(self):
        """The create payload holds only declared synthetic metadata."""
        payload = checker.synthetic_task_payload(SYNTHETIC_PROJECT_ID)
        described = dict(payload)
        del described["project"]
        self.assertEqual(checker.content_findings(described), [])
        text = json.dumps(payload)
        for value in ("Users", "\\\\", "://", "@"):
            self.assertNotIn(value, text)
        with self.assertRaises(checker.ProbeFailure):
            checker.synthetic_task_payload("synthetic id with spaces")

    def test_tracked_connection_files_hold_no_path_or_other_url(self):
        """Only the three approved endpoints and no user path appear."""
        url = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s\"'`)<>\]]+")
        user_path = re.compile(r"(?i)\b[A-Z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/)")
        approved = set(checker.ENDPOINTS.values())
        for path in (DECLARATION_PATH, SCRIPT, DOC_PATH):
            with self.subTest(path=os.path.basename(path)):
                with open(path, "rb") as handle:
                    text = handle.read().decode("utf-8")
                found = set(match.rstrip(".,;:") for match in
                            url.findall(text))
                self.assertLessEqual(found, approved)
                self.assertIsNone(user_path.search(text))


class ControlledTrackingProbeTests(unittest.TestCase):
    """Operator mode uses one bounded payload and archives after readback."""

    def test_operator_probe_submits_only_allowlisted_metadata(self):
        def readback(query, created):
            task = dict(created, id=SYNTHETIC_TASK_ID, script={})
            task["tags"] = list(reversed(task["tags"]))
            return 200, {"data": {"tasks": [task]}}

        adapter = FakeAdapter(**{"tasks.get_all": readback})
        status, output = run_main(["--controlled-tracking-probe"], adapter)
        self.assertEqual(status, checker.EXIT_PASS, output)
        self.assertEqual(adapter.actions().count("tasks.create"), 1)
        self.assertEqual(adapter.actions().count("tasks.archive"), 1)
        payload = adapter.created
        self.assertEqual(set(payload), set(("name", "type", "project", "comment", "tags")))
        self.assertEqual(payload["name"], checker.tracking.TASK_NAME)
        readback_request = next(payload for name, payload in adapter.calls
                                if name == "tasks.get_all")
        requested = set(readback_request["only_fields"])
        expected_projection = {"id", "name", "type", "project", "tags", "comment",
                              "script", "execution", "output", "configuration",
                              "hyperparams"}
        unsupported_fields = {"artifacts", "artifact", "model", "data", "source",
                              "packages", "environment", "console", "stdout",
                              "stderr", "streams", "resource_monitor", "repository",
                              "branch", "version_num", "entry_point", "diff",
                              "requirements", "container", "input"}
        self.assertEqual(requested, expected_projection)
        self.assertFalse(unsupported_fields & requested)
        self.assertIn(("api.version_check", "2.13"), adapter.calls)
        self.assertTrue(all(tag.startswith(("lineage:", "hyperparameter:"))
                            for tag in payload["tags"]))
        self.assertNotIn("SYNTHETIC", json.dumps(payload))
        self.assertNotIn("script", payload)

    def test_api_213_projection_adds_only_runtime_and_models(self):
        def readback(query, created):
            task = dict(created, id=SYNTHETIC_TASK_ID, script={}, runtime={}, models={})
            return 200, {"data": {"tasks": [task]}}

        adapter = FakeAdapter(api_213=True, **{"tasks.get_all": readback})
        status, output = run_main(["--controlled-tracking-probe"], adapter)
        self.assertEqual(status, checker.EXIT_PASS, output)
        request = next(payload for name, payload in adapter.calls
                       if name == "tasks.get_all")
        self.assertEqual(set(request["only_fields"]), {
            "id", "name", "type", "project", "tags", "comment", "script",
            "execution", "output", "configuration", "hyperparams", "runtime",
            "models"})
        self.assertIn(("api.version_check", "2.13"), adapter.calls)

    def test_nested_empty_clearml_shells_are_accepted(self):
        def readback(query, created):
            task = dict(created, id=SYNTHETIC_TASK_ID,
                        script={"binary": "python", "entry_point": "",
                                "repository": "", "requirements": {}, "diff": None,
                                "version_num": ""},
                        execution={"test_split": 0, "artifacts": [],
                                   "dataviews": {}, "model_desc": "",
                                   "model_labels": [], "parameters": {}},
                        output={"model": None, "destination": "", "other": {}},
                        configuration={"content": {}, "other": []},
                        hyperparams={"empty": {}}, runtime={},
                        models={"input": None, "output": []})
            return 200, {"data": {"tasks": [task]}}

        adapter = FakeAdapter(api_213=True, **{"tasks.get_all": readback})
        status, output = run_main(["--controlled-tracking-probe"], adapter)
        self.assertEqual(status, checker.EXIT_PASS, output)
        self.assertEqual(adapter.actions().count("tasks.archive"), 1)

    def test_sensitive_readback_fields_fail_closed(self):
        for field, value in (("runtime", {"python": "captured"}),
                             ("models", [{"name": "captured"}]),
                             ("input", {"uri": "captured"}),
                             ("output", {"destination": "captured"}),
                             ("execution", {"parameters": {"x": "y"}}),
                             ("hyperparams", {"x": "captured"}),
                             ("configuration", {"x": "captured"}),
                             ("execution", {"parameters": {"batch_size": 0}}),
                             ("execution", {"test_split": 0.5}),
                             ("execution", {"unapproved": "captured"}),
                             ("script", {"binary": r"C:\Python\python.exe"}),
                             ("script", {"binary": "patient"}),
                             ("script", {"repository": "captured"}),
                             ("prompt_payload", "captured")):
            def readback(query, created):
                task = dict(created, id=SYNTHETIC_TASK_ID, script={})
                task[field] = value
                return 200, {"data": {"tasks": [task]}}

            with self.subTest(field=field):
                adapter = FakeAdapter(**{"tasks.get_all": readback})
                status, output = run_main(["--controlled-tracking-probe"], adapter)
                self.assertEqual(status, checker.EXIT_FAIL, output)
                self.assertIn("readback_mismatch", output)
                self.assertEqual(adapter.actions().count("tasks.archive"), 1)

    def test_readback_and_archive_failures_are_both_reported(self):
        def captured(query, created):
            task = dict(created, id=SYNTHETIC_TASK_ID,
                        script={"repository": "captured source"})
            return 200, {"data": {"tasks": [task]}}

        adapter = FakeAdapter(**{
            "tasks.get_all": captured,
            "tasks.archive": (403, {"meta": {"result_subcode": 12}}),
        })
        status, output = run_main(["--controlled-tracking-probe"], adapter)
        self.assertEqual(status, checker.EXIT_FAIL, output)
        self.assertIn("readback_mismatch", output)
        self.assertIn("archive_failed", output)

    def test_readback_capture_is_a_failed_probe_and_task_is_archived(self):
        def captured(query, created):
            task = dict(created, id=SYNTHETIC_TASK_ID,
                        script={"repository": "synthetic repository"})
            return 200, {"data": {"tasks": [task]}}

        adapter = FakeAdapter(**{"tasks.get_all": captured})
        status, output = run_main(["--controlled-tracking-probe"], adapter)
        self.assertEqual(status, checker.EXIT_FAIL, output)
        self.assertIn("readback_mismatch", output)
        self.assertEqual(adapter.actions().count("tasks.create"), 1)
        self.assertEqual(adapter.actions().count("tasks.archive"), 1)


class SdkAdapterTests(unittest.TestCase):
    """The real adapter wired to fake SDK modules (no SDK is imported)."""

    def fake_modules(self, adapter):
        """Return fake clearml modules routing requests to adapter."""
        accessed = []
        sessions = []
        config = object()

        class FakeResponse(object):
            """Synthetic HTTP response."""

            def __init__(self, status, body):
                """Store the synthetic status and body."""
                self.status_code = status
                self.body = body

            def json(self):
                """Return the synthetic body."""
                return self.body

        class FakeSession(object):
            """Synthetic Session recording its construction and requests."""

            _offline_mode = False

            def __init__(self, **kwargs):
                """Record constructor arguments."""
                self.kwargs = kwargs
                self.methods = []
                sessions.append(self)

            @classmethod
            def get_api_server_host(cls, config=None):
                """Return the approved API host."""
                return checker.ENDPOINTS["api_server"]

            @classmethod
            def get_app_server_host(cls, config=None):
                """Return the approved web host."""
                return checker.ENDPOINTS["web_server"]

            @classmethod
            def get_files_server_host(cls, config=None):
                """Return the approved files host."""
                return checker.ENDPOINTS["files_server"]

            def send_request(self, service, action, method=None, json=None):
                """Route the request to the synthetic adapter."""
                self.methods.append(method)
                return FakeResponse(*adapter.call(service, action, json))

        class FakeConfigWrapper(object):
            """Synthetic configuration loader."""

            @classmethod
            def _init(cls):
                """Return the synthetic configuration object."""
                return config

        def record(name):
            """Record any top-level SDK attribute access and refuse it."""
            accessed.append(name)
            raise AttributeError(name)

        root = types.ModuleType("clearml")
        root.__getattr__ = record
        api = types.ModuleType("clearml.backend_api")
        session_module = types.ModuleType("clearml.backend_api.session")
        session_module.Session = FakeSession
        config_module = types.ModuleType("clearml.config")
        config_module.ConfigWrapper = FakeConfigWrapper
        modules = {"clearml": root, "clearml.backend_api": api,
                   "clearml.backend_api.session": session_module,
                   "clearml.config": config_module}
        return modules, sessions, accessed, config

    def test_adapter_uses_session_only_with_declared_host(self):
        """One session, declared host, synthetic worker, POST, no Task."""
        adapter = FakeAdapter()
        modules, sessions, accessed, config = self.fake_modules(adapter)
        with mock.patch.dict(sys.modules, modules), \
                mock.patch.object(socket, "socket", no_network):
            findings, facts, _ = checker.run_probe(
                True, valid_environ(), checker.SdkAdapter,
                lstat=regular_file)
        self.assertEqual(findings, [])
        self.assertEqual(len(sessions), 1)
        kwargs = sessions[0].kwargs
        self.assertEqual(kwargs["host"], checker.ENDPOINTS["api_server"])
        self.assertEqual(kwargs["worker"], checker.WORKER_NAME)
        self.assertIs(kwargs["config"], config)
        self.assertEqual(set(sessions[0].methods), set(["post"]))
        self.assertEqual(adapter.actions().count("tasks.create"), 1)
        self.assertNotIn("Task", accessed)


if __name__ == "__main__":
    unittest.main()
