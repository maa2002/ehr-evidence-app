#!/usr/bin/env python3
"""Check the ClearML connection declaration and hosted connection (Step 10).

Modes (exit status: 0 PASS, 1 FAIL, 2 operational failure):

(default)
    Declaration check only. Standard library; the ClearML SDK is not
    imported, the process environment is not read and no service is
    contacted. Also runs the Step 9 policy check.
--preflight
    Live, read-only connection check run by the operator. The
    environment must name the existing untracked configuration file
    through CLEARML_CONFIG_FILE, must not select offline mode and must
    not override hosts, credentials or configuration through other
    variables. The configuration is loaded and its API, web and files
    endpoints are compared with the declaration *before* any request is
    sent, so credentials never go to an undeclared server. The probe then
    authenticates, confirms the workspace name and looks up the project.
    Nothing is created.
--synthetic-task
    The preflight, then exactly one clearly synthetic, metadata-only task
    in the declared project (the project is created only if absent). The
    task is read back to confirm that no script, repository, diff or
    package content was captured, and is then archived.

The live modes use only the SDK's session and configuration classes;
Task.init and Task.create are never called, so framework auto-connect,
argument-parser and stream capture, resource monitoring, repository
detection and package freezing are never started. SDK output and logging
are suppressed during the probe; this tool prints only fixed rule IDs,
declared (public) values, HTTP status codes, exception class names,
timings and the created task ID. It never prints, copies, hashes or
parses the configuration file, its path or any credential; the file is
examined with lstat only and read solely by the SDK.

A missing configuration variable, missing file, offline mode, an
override variable, an endpoint, workspace or project mismatch, an
authentication or SDK failure, or a service rejection is a FAIL. There
is no offline fallback.

Usage:
    python -m ehr_evidence_app.tracking.connection [--config PATH]
        [--preflight | --synthetic-task]
"""

import argparse
import contextlib
import io
import json
import logging
import ntpath
import os
import re
import stat
import sys
import time

from ehr_evidence_app.storage import policy as guard
from ehr_evidence_app.tracking import policy as step9
from ehr_evidence_app.tracking import controlled as tracking

SCHEMA_VERSION = "1.0"
STEP = "10"
REPO_ROOT = guard.REPO_ROOT
CONFIG_DIR = os.path.join(REPO_ROOT, "config")
CONFIG_NAME = "clearml_connection.json"
POLICY_PATH = os.path.join(CONFIG_DIR, step9.CONFIG_NAME)
MAX_CONFIG_BYTES = 64 * 1024

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_OPERATIONAL = 2

MODE_DECLARATION = "declaration"
MODE_PREFLIGHT = "preflight"
MODE_SYNTHETIC_TASK = "synthetic-task"
MODE_CONTROLLED_TRACKING = "controlled-tracking-probe"

# -- Approved declaration (fixed minimums; the file must match exactly) ----

TOP_REQUIRED = ("schema_version", "step", "purpose", "tracker",
                "authorization", "endpoints", "workspace", "project",
                "configuration_file", "payload_policy", "synthetic_task",
                "model_artefacts")
TOP_OPTIONAL = ("notes",)

TRACKER = {"name": "clearml", "package": "clearml",
           "locked_version": "2.1.12"}
AUTHORIZATION = {
    "status": "user_authorized",
    "mode": "hosted_metadata_only",
    "remote_payloads": "metadata_only",
    "offline_fallback": False,
}
ENDPOINT_ROLES = ("api_server", "web_server", "files_server")
ROLE_LABELS = {"api_server": "API server", "web_server": "Web server",
               "files_server": "Files server"}
ENDPOINTS = {
    "web_server": "https://app.clear.ml",
    "api_server": "https://api.clear.ml",
    "files_server": "https://files.clear.ml",
}
# Exact authenticated workspace (company) name reported by the service,
# including the two spaces between the given name and family name.
WORKSPACE = "Abdullah  Aamir's workspace"
PROJECT = "ehr-evidence-app"
CONFIG_VARIABLE = "CLEARML_CONFIG_FILE"
CONFIGURATION_FILE = {
    "environment_variable": CONFIG_VARIABLE,
    "scope": "process_local",
    "path_recorded": False,
    "contents_read_by_checker": False,
    "inside_repository_permitted": False,
    "override_variables_permitted": False,
}
PAYLOAD_POLICY = {
    "policy_file": "config/" + step9.CONFIG_NAME,
    "remote_payloads": "metadata_only",
    "protected_records": "local_w_only",
}
CAPTURE_FLAGS = ("task_init_used", "auto_connect_frameworks",
                 "auto_connect_arg_parser", "auto_connect_streams",
                 "auto_resource_monitoring", "detect_repository",
                 "capture_packages", "capture_environment",
                 "upload_artifacts")
SYNTHETIC_TASK = dict(
    [("task_name", "SYNTHETIC step-10 connection check"),
     ("task_type", "testing"),
     ("tags", ["synthetic", "step-10", "connection-check", "metadata-only"]),
     ("comment", "SYNTHETIC Step 10 connection check. Metadata only; no "
                 "patient, protected, prompt, response, attribution, model "
                 "or data content."),
     ("max_tasks_per_run", 1),
     ("archive_after_creation", True)]
    + [(flag, False) for flag in CAPTURE_FLAGS])
MODEL_ARTEFACTS = {
    "status": "not_enabled",
    "upload_in_step_10": False,
    "enablement_gate": "separate_step_10_11_policy_amendment",
    "hyperparameters": "later_explicit_allowlist_only",
    "requirements": ["policy_amendment", "dedicated_staging_path",
                     "artefact_manifest", "privacy_review",
                     "explicit_enablement_gate"],
    "manifest_fields": ["logical_name", "type", "size_bytes", "sha256",
                        "provenance", "non_patient_non_secret_confirmation"],
    "always_excluded": ["data_files", "samples", "labels", "prompts",
                        "patient_level_records", "optimizer_state",
                        "cached_batches", "tokenizer_inputs", "debug_dumps",
                        "traces", "arbitrary_training_directories",
                        "automatic_environment_capture",
                        "automatic_source_capture"],
}

# -- Probe environment ------------------------------------------------------

CACHE_VARIABLE = "CLEARML_CACHE_DIR"
OFFLINE_VARIABLES = ("CLEARML_OFFLINE_MODE", "TRAINS_OFFLINE_MODE")
# Variables that would bypass or override the declared configuration file
# (hosts, keys, tokens, alternative files, TLS verification) or make the
# SDK log configuration details. Any non-empty value fails the preflight.
OVERRIDE_VARIABLES = (
    "CLEARML_API_HOST", "TRAINS_API_HOST",
    "CLEARML_WEB_HOST", "TRAINS_WEB_HOST",
    "CLEARML_FILES_HOST", "TRAINS_FILES_HOST",
    "CLEARML_API_ACCESS_KEY", "TRAINS_API_ACCESS_KEY",
    "CLEARML_API_SECRET_KEY", "TRAINS_API_SECRET_KEY",
    "CLEARML_AUTH_TOKEN", "TRAINS_CONFIG_FILE",
    "CLEARML_CONFIG_PATH", "TRAINS_CONFIG_PATH",
    "CLEARML_API_HOST_VERIFY_CERT", "TRAINS_API_HOST_VERIFY_CERT",
    "CLEARML_CONFIG_VERBOSE", "CLEARML_API_VERBOSE", "TRAINS_API_VERBOSE",
)
DRIVE_PATH = re.compile(r"^[A-Za-z]:\\")

# -- Service vocabulary -----------------------------------------------------

WORKER_NAME = "ehr-evidence-step10-preflight"
SERVER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Task script fields that would hold captured source, repository or
# package content; all must be empty on the synthetic task.
CAPTURE_FIELDS = ("repository", "branch", "version_num", "entry_point",
                  "diff", "requirements")
TASK_PAYLOAD_KEYS = ("name", "type", "project", "comment", "tags")
SDK_ERRORS = {
    "ImportError": "sdk_unavailable",
    "ModuleNotFoundError": "sdk_unavailable",
    "MissingConfigError": "credentials_missing",
    "LoginError": "authentication_failed",
    "ConnectionError": "service_unreachable",
    "ConnectTimeout": "service_unreachable",
    "ReadTimeout": "service_unreachable",
    "Timeout": "service_unreachable",
    "SSLError": "service_unreachable",
    "MaxRetryError": "service_unreachable",
}

# -- Content rules ----------------------------------------------------------

URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://")
LOCAL_PATH = re.compile(
    r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/]|^\\\\|[\\/](?:Users|home)[\\/]")

SECTIONS = ("schema", "tracker", "authorization", "endpoints", "workspace",
            "project", "configuration_file", "payload_policy",
            "synthetic_task", "model_artefacts", "content", "step_9_policy",
            "environment", "sdk", "connection", "task")


class OperationalError(Exception):
    """A failure that prevents trustworthy checking; never a PASS."""


class ProbeFailure(Exception):
    """A fail-closed live-probe finding holding a fixed rule, not a value."""

    def __init__(self, section, rule, detail="", additional_findings=()):
        """Record fixed-vocabulary findings without exposing service values."""
        super(ProbeFailure, self).__init__(rule)
        self.finding = (section, rule, detail)
        self.findings = [self.finding] + list(additional_findings)


def finding(section, rule, detail=""):
    """Return one (section, rule ID, detail) finding tuple."""
    return (section, rule, detail)


# --------------------------------------------------------------------------
# Declaration file
# --------------------------------------------------------------------------

def read_declaration(path):
    """Return the parsed declaration from the repository config directory.

    The path must lie inside the config directory and be a regular file
    reached without a link at its leaf. Errors never quote content.
    """
    if not guard.is_within(path, CONFIG_DIR, strict=True):
        raise OperationalError("the declaration must be read from %s"
                               % CONFIG_DIR)
    try:
        info = os.lstat(path)
    except OSError as error:
        raise OperationalError("cannot inspect the declaration (%s)"
                               % type(error).__name__)
    if guard.is_link_like(info) or not stat.S_ISREG(info.st_mode):
        raise OperationalError("the declaration is not a regular file")
    if info.st_size > MAX_CONFIG_BYTES:
        raise OperationalError("the declaration is larger than %d bytes"
                               % MAX_CONFIG_BYTES)
    with open(path, "rb") as handle:
        data = handle.read(MAX_CONFIG_BYTES + 1)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise OperationalError("the declaration is not valid UTF-8")
    try:
        return json.loads(text,
                          object_pairs_hook=step9.reject_duplicate_keys)
    except step9.OperationalError:
        raise OperationalError("the declaration repeats a JSON key")
    except ValueError:
        raise OperationalError("the declaration is not valid JSON")


# --------------------------------------------------------------------------
# Declaration checks (values are examined, never returned or printed)
# --------------------------------------------------------------------------

def check_schema(declaration):
    """Return schema findings for the top level of the declaration."""
    missing = set(TOP_REQUIRED) - set(declaration)
    unknown = set(declaration) - set(TOP_REQUIRED) - set(TOP_OPTIONAL)
    findings = [finding("schema", "required_key_missing") for _ in missing]
    findings += [finding("schema", "unsupported_key") for _ in unknown]
    if not step9.same_value(declaration.get("schema_version"),
                            SCHEMA_VERSION):
        findings.append(finding("schema", "schema_version_invalid"))
    if not step9.same_value(declaration.get("step"), STEP):
        findings.append(finding("schema", "step_invalid"))
    purpose = declaration.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        findings.append(finding("schema", "purpose_invalid"))
    notes = declaration.get("notes", [])
    if (not isinstance(notes, list)
            or not all(isinstance(item, str) and item.strip()
                       for item in notes)):
        findings.append(finding("schema", "notes_invalid"))
    return findings


def check_authorization(section):
    """Return findings unless hosted metadata-only use is user-authorised.

    Any other status or mode, or a permitted offline fallback, is an
    unauthorised mode.
    """
    if (not isinstance(section, dict)
            or section.get("status") != AUTHORIZATION["status"]
            or section.get("mode") != AUTHORIZATION["mode"]
            or section.get("offline_fallback") is not False):
        return [finding("authorization", "remote_not_authorized")]
    if not step9.same_value(section, AUTHORIZATION):
        return [finding("authorization", "authorization_invalid")]
    return []


def check_endpoints(section):
    """Return findings unless exactly the three approved HTTPS endpoints."""
    if not isinstance(section, dict) or set(section) != set(ENDPOINTS):
        return [finding("endpoints", "endpoint_not_approved")]
    return [finding("endpoints", "endpoint_not_approved", role)
            for role in ENDPOINT_ROLES
            if not step9.same_value(section.get(role), ENDPOINTS[role])]


def check_synthetic_task(section):
    """Return findings for the synthetic task definition.

    Any capture flag other than false is reported as auto-capture; any
    other difference from the approved definition is invalid.
    """
    if not isinstance(section, dict):
        return [finding("synthetic_task", "synthetic_task_invalid")]
    enabled = [flag for flag in CAPTURE_FLAGS
               if section.get(flag) is not False]
    if enabled:
        return [finding("synthetic_task", "auto_capture_enabled", flag)
                for flag in enabled]
    if not step9.same_value(section, SYNTHETIC_TASK):
        return [finding("synthetic_task", "synthetic_task_invalid")]
    return []


def check_model_artefacts(section):
    """Return findings unless model artefact upload stays gated and off."""
    if isinstance(section, dict) and (
            section.get("upload_in_step_10") is not False
            or section.get("status") != MODEL_ARTEFACTS["status"]):
        return [finding("model_artefacts", "model_artefact_enabled")]
    if not step9.same_value(section, MODEL_ARTEFACTS):
        return [finding("model_artefacts", "model_artefact_gate_invalid")]
    return []


def content_findings(document, section="content"):
    """Return findings for secrets, paths, URLs or patient content.

    A secret-named key may hold only null, false, an object or a list.
    Every string (keys included) is checked for token/key shapes,
    patient-identifier assignments, local filesystem paths and URLs other
    than the approved endpoints.
    """
    approved = set(ENDPOINTS.values())
    findings = []
    for key, value in step9.walk(document):
        if (key is not None and step9.SECRET_KEY.search(key)
                and not (value is None or value is False
                         or isinstance(value, (dict, list)))):
            findings.append(finding(section, "secret_present"))
        if not isinstance(value, str):
            continue
        if step9.SECRET_SHAPE.search(value):
            findings.append(finding(section, "secret_present"))
        if step9.PATIENT_IDENTIFIER.search(value):
            findings.append(finding(section, "patient_content_present"))
        if LOCAL_PATH.search(value):
            findings.append(finding(section, "local_path_present"))
        if URL.search(value) and value not in approved:
            findings.append(finding(section, "endpoint_not_approved"))
    return findings


def check_declaration(declaration):
    """Return every finding for a parsed declaration, sorted.

    Each finding is a (section, rule ID, detail) tuple; the detail is a
    fixed field or role name, never a declaration value.
    """
    if not isinstance(declaration, dict):
        return [finding("schema", "declaration_not_object")]
    findings = check_schema(declaration)
    if not step9.same_value(declaration.get("tracker"), TRACKER):
        findings.append(finding("tracker", "tracker_invalid"))
    findings += check_authorization(declaration.get("authorization"))
    findings += check_endpoints(declaration.get("endpoints"))
    if not step9.same_value(declaration.get("workspace"), WORKSPACE):
        findings.append(finding("workspace", "workspace_not_approved"))
    if not step9.same_value(declaration.get("project"), PROJECT):
        findings.append(finding("project", "project_not_approved"))
    if not step9.same_value(declaration.get("configuration_file"),
                            CONFIGURATION_FILE):
        findings.append(finding("configuration_file",
                                "config_reference_invalid"))
    if not step9.same_value(declaration.get("payload_policy"),
                            PAYLOAD_POLICY):
        findings.append(finding("payload_policy", "payload_policy_invalid"))
    findings += check_synthetic_task(declaration.get("synthetic_task"))
    findings += check_model_artefacts(declaration.get("model_artefacts"))
    findings += content_findings(declaration)
    return sort_findings(findings)


def sort_findings(findings):
    """Return findings in deterministic section, rule and detail order."""
    return sorted(findings, key=lambda item: (SECTIONS.index(item[0]),
                                              item[1], item[2]))


def check_step9_policy():
    """Return a finding when the Step 9 payload policy does not verify."""
    if step9.check_policy(step9.read_policy(POLICY_PATH)):
        return [finding("step_9_policy", "step_9_policy_invalid")]
    return []


# --------------------------------------------------------------------------
# Probe environment (checked before the SDK is imported)
# --------------------------------------------------------------------------

def config_file_problems(path, lstat):
    """Return findings for the configuration file named by the variable.

    The file must lie outside this repository and be an existing regular
    file that is not a link; it is inspected with lstat only, never opened.
    """
    if guard.is_within(path, REPO_ROOT):
        return [finding("environment", "config_file_inside_repository")]
    try:
        info = lstat(path)
    except OSError:
        return [finding("environment", "config_file_missing")]
    if guard.is_link_like(info) or not stat.S_ISREG(info.st_mode):
        return [finding("environment", "config_file_not_regular")]
    return []


def is_set(environ, name):
    """Return True when an environment variable holds a non-empty value."""
    return bool(environ.get(name, ""))


def environment_problems(environ, lstat=os.lstat):
    """Return fail-closed findings for the live-probe process environment.

    Only variable names are reported; no value is returned or printed.
    """
    findings = []
    value = environ.get(CONFIG_VARIABLE, "")
    if not value.strip():
        findings.append(finding("environment", "config_variable_missing",
                                CONFIG_VARIABLE))
    elif not (DRIVE_PATH.match(value) and ntpath.isabs(value)):
        findings.append(finding("environment", "config_path_not_absolute",
                                CONFIG_VARIABLE))
    else:
        findings += config_file_problems(value, lstat)
    findings += [finding("environment", "offline_mode_set", name)
                 for name in OFFLINE_VARIABLES if is_set(environ, name)]
    findings += [finding("environment", "override_variable_set", name)
                 for name in OVERRIDE_VARIABLES if is_set(environ, name)]
    cache = environ.get(CACHE_VARIABLE, "")
    if not (DRIVE_PATH.match(cache) and guard.is_within(
            cache, guard.RUNTIME_ROOT, strict=True)):
        findings.append(finding("environment", "cache_destination_invalid",
                                CACHE_VARIABLE))
    return findings


# --------------------------------------------------------------------------
# Live probe
# --------------------------------------------------------------------------

def null_logger():
    """Return a logger that discards every record passed to the SDK."""
    logger = logging.getLogger("ehr_evidence.clearml_preflight")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    return logger


class SdkAdapter(object):
    """Minimal bridge to the installed ClearML SDK, used by live modes only.

    Only the session and configuration classes are imported. Task.init and
    Task.create are never reached, so no framework, argument-parser,
    stream, resource, repository or package hook is started.
    """

    def __init__(self):
        """Import the SDK session and configuration classes."""
        from clearml.backend_api.session import Session
        from clearml.config import ConfigWrapper
        self._session_class = Session
        self._config_wrapper = ConfigWrapper
        self._session = None

    def offline(self):
        """Return True when the SDK started in offline mode."""
        return bool(getattr(self._session_class, "_offline_mode", False))

    def load_config(self):
        """Load the SDK configuration named by CLEARML_CONFIG_FILE."""
        return self._config_wrapper._init()

    def configured_hosts(self, config):
        """Return the API, web and files hosts the configuration selects."""
        session = self._session_class
        return {"api_server": session.get_api_server_host(config=config),
                "web_server": session.get_app_server_host(config=config),
                "files_server": session.get_files_server_host(
                    config=config)}

    def connect(self, config, api_server):
        """Authenticate against the declared API server only."""
        self._session = self._session_class(
            worker=WORKER_NAME, host=api_server, config=config,
            logger=null_logger())

    def supports_api_version(self, version):
        """Check the negotiated API version locally without another request."""
        check = getattr(self._session_class, "check_min_api_version", None)
        return bool(check(version)) if callable(check) else False

    def call(self, service, action, payload):
        """Send one POST API request; return (HTTP status, parsed body)."""
        response = self._session.send_request(service, action,
                                              method="post", json=payload)
        if response is None:
            raise ProbeFailure("sdk", "offline_mode_set")
        try:
            body = response.json()
        except ValueError:
            body = None
        return response.status_code, body


@contextlib.contextmanager
def quiet_sdk():
    """Discard SDK stdout, stderr and logging for the enclosed block."""
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            yield
    finally:
        logging.disable(previous)


def normalise_url(value):
    """Return a host URL without trailing slashes, or None if not text."""
    if not isinstance(value, str):
        return None
    return value.strip().rstrip("/")


def safe_id(value):
    """Return value when it is a plain server-generated ID, else None."""
    if isinstance(value, str) and SERVER_ID.match(value):
        return value
    return None


def call_service(adapter, service, action, payload):
    """Send one request and return its data object, failing closed.

    A non-200 status or a malformed body is a finding naming only the
    service action, HTTP status and numeric result subcode.
    """
    status, body = adapter.call(service, action, payload)
    label = "%s.%s" % (service, action)
    if status != 200:
        meta = body.get("meta") if isinstance(body, dict) else None
        subcode = meta.get("result_subcode") if isinstance(
            meta, dict) else None
        detail = "%s HTTP %s" % (label, status if isinstance(
            status, int) else "unknown")
        if isinstance(subcode, int) and not isinstance(subcode, bool):
            detail += " subcode %d" % subcode
        raise ProbeFailure("connection", "service_rejected", detail)
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise ProbeFailure("connection", "unexpected_response", label)
    return data


def confirm_workspace(adapter):
    """Confirm the authenticated workspace is the declared workspace."""
    data = call_service(adapter, "users", "get_current_user", {})
    user = data.get("user")
    company = user.get("company") if isinstance(user, dict) else None
    name = company.get("name") if isinstance(company, dict) else None
    if not isinstance(name, str) or not name:
        raise ProbeFailure("connection", "workspace_unverified")
    if name != WORKSPACE:
        raise ProbeFailure("connection", "workspace_mismatch")


def find_project(adapter):
    """Return the declared project's ID, or None when it does not exist."""
    data = call_service(adapter, "projects", "get_all", {
        "name": "^%s$" % re.escape(PROJECT),
        "only_fields": ["id", "name"],
        "search_hidden": False})
    projects = data.get("projects")
    if not isinstance(projects, list):
        raise ProbeFailure("connection", "unexpected_response",
                           "projects.get_all")
    matches = [item for item in projects
               if isinstance(item, dict) and item.get("name") == PROJECT]
    if len(matches) > 1:
        raise ProbeFailure("connection", "project_ambiguous")
    if not matches:
        return None
    project_id = safe_id(matches[0].get("id"))
    if project_id is None:
        raise ProbeFailure("connection", "unexpected_response",
                           "projects.get_all")
    return project_id


def synthetic_task_payload(project_id):
    """Return the metadata-only tasks.create payload, checked for content.

    The payload is built only from the approved declaration constants and
    the server-generated project ID. The ID is excluded from the content
    check because server IDs are hexadecimal and would match key shapes.
    """
    payload = {"name": SYNTHETIC_TASK["task_name"],
               "type": SYNTHETIC_TASK["task_type"],
               "project": project_id,
               "comment": SYNTHETIC_TASK["comment"],
               "tags": list(SYNTHETIC_TASK["tags"])}
    described = dict((key, value) for key, value in payload.items()
                     if key != "project")
    if (sorted(payload) != sorted(TASK_PAYLOAD_KEYS)
            or safe_id(project_id) is None
            or content_findings(described, "task")
            or "SYNTHETIC" not in payload["name"]):
        raise ProbeFailure("task", "payload_not_metadata_only")
    return payload


def verify_created_task(adapter, task_id, project_id):
    """Read the created task back and confirm nothing was captured."""
    data = call_service(adapter, "tasks", "get_all", {
        "id": [task_id],
        "only_fields": ["id", "name", "type", "project", "tags", "script"]})
    tasks = data.get("tasks")
    if not (isinstance(tasks, list) and len(tasks) == 1
            and isinstance(tasks[0], dict)):
        raise ProbeFailure("task", "readback_mismatch")
    task = tasks[0]
    if (task.get("id") != task_id
            or task.get("name") != SYNTHETIC_TASK["task_name"]
            or task.get("type") != SYNTHETIC_TASK["task_type"]
            or task.get("project") != project_id
            or not isinstance(task.get("tags"), list)
            or sorted(task["tags"]) != sorted(SYNTHETIC_TASK["tags"])):
        raise ProbeFailure("task", "readback_mismatch")
    script = task.get("script")
    if script is not None and not isinstance(script, dict):
        raise ProbeFailure("task", "readback_mismatch")
    captured = [name for name in CAPTURE_FIELDS
                if isinstance(script, dict) and script.get(name)]
    if captured:
        raise ProbeFailure("task", "automatic_capture_detected",
                           ",".join(captured))


def verify_controlled_task(adapter, task_id, project_id, expected):
    """Read back the controlled metadata task and reject capture fields."""
    projection = ["id", "name", "type", "project", "tags", "comment",
                  "script", "execution", "output", "configuration",
                  "hyperparams"]
    if adapter.supports_api_version("2.13"):
        projection.extend(("runtime", "models"))
    data = call_service(adapter, "tasks", "get_all", {
        "id": [task_id], "only_fields": projection})
    tasks = data.get("tasks")
    if not (isinstance(tasks, list) and len(tasks) == 1
            and isinstance(tasks[0], dict)):
        raise ProbeFailure("task", "readback_mismatch")
    task = tasks[0]
    if task.get("id") != task_id or task.get("project") != project_id:
        raise ProbeFailure("task", "readback_mismatch")
    try:
        tracking.validate_readback(task, expected)
    except tracking.UnsafeMetadata:
        raise ProbeFailure("task", "readback_mismatch")
    if task.get("comment") != expected.get("comment"):
        raise ProbeFailure("task", "readback_mismatch")


def archive_controlled_task(adapter, task_id):
    """Archive the Step 11 task or fail the controlled operation."""
    result = archive_task(adapter, task_id)
    if result != "archived":
        raise ProbeFailure("task", "archive_failed")
    return result


def archive_task(adapter, task_id):
    """Archive the synthetic task; return a fixed-vocabulary outcome."""
    try:
        data = call_service(adapter, "tasks", "archive",
                            {"tasks": [task_id]})
    except ProbeFailure as failure:
        return "not archived (%s)" % (failure.finding[2]
                                      or failure.finding[1])
    if data.get("archived") == 1:
        return "archived"
    return "not archived (tasks.archive reported no change)"


class Timer(object):
    """Collect named phase durations from an injectable clock."""

    def __init__(self, clock):
        """Start the overall timer."""
        self.clock = clock
        self.start = clock()
        self.phases = []

    def run(self, name, function, *args):
        """Run function(*args), record its duration and return its result."""
        began = self.clock()
        try:
            return function(*args)
        finally:
            self.phases.append((name, self.clock() - began))

    def summary(self):
        """Return a one-line summary of the phase and total durations."""
        parts = ["%s %.2fs" % (name, seconds)
                 for name, seconds in self.phases]
        parts.append("total %.2fs" % (self.clock() - self.start))
        return "; ".join(parts)


def probe_service(adapter_factory, create_task, facts, timer,
                  controlled_tracking=False):
    """Run the live connection checks in order, raising on any failure.

    Endpoints are compared before connect(), so credentials are never
    sent to a server other than the declared API server.
    """
    adapter = timer.run("sdk_import", adapter_factory)
    if adapter.offline():
        raise ProbeFailure("sdk", "offline_mode_set")
    config = timer.run("config_load", adapter.load_config)
    hosts = adapter.configured_hosts(config)
    for role in ENDPOINT_ROLES:
        if normalise_url(hosts.get(role)) != ENDPOINTS[role]:
            raise ProbeFailure("connection", "endpoint_mismatch", role)
    for role in ENDPOINT_ROLES:
        facts.append((ROLE_LABELS[role], "confirmed %s" % ENDPOINTS[role]))
    timer.run("authenticate", adapter.connect, config,
              ENDPOINTS["api_server"])
    facts.append(("Authentication", "confirmed (credentials withheld)"))
    timer.run("workspace", confirm_workspace, adapter)
    facts.append(("Workspace", "confirmed %s" % WORKSPACE))
    project_id = timer.run("project", find_project, adapter)
    if not create_task:
        facts.append(("Project", "%s %s" % (
            "present" if project_id else
            "absent (created only by --synthetic-task)", PROJECT)))
        facts.append(("Synthetic task", "not requested; nothing created"))
        return
    if project_id is None:
        data = timer.run("project_create", call_service, adapter,
                         "projects", "create", {
                             "name": PROJECT,
                             "description": SYNTHETIC_TASK["comment"]})
        project_id = safe_id(data.get("id"))
        if project_id is None:
            raise ProbeFailure("connection", "unexpected_response",
                               "projects.create")
        facts.append(("Project", "created %s" % PROJECT))
    else:
        facts.append(("Project", "present %s" % PROJECT))
    if controlled_tracking:
        lineage = {
            "code_revision": "probe-revision-01",
            "case_set_version": "probe-case-set-01",
            "llm_configuration_fingerprint": "a" * 64,
            "prompt_version": "probe-template-01",
            "evidence_schema_version": "probe-evidence-01",
            "validation_configuration_fingerprint": "b" * 64,
            "evaluation_outputs": {"accuracy": 0.0},
            "protected_reference": r"G:\Industry Dataset\ehr-evidence-workspace\outputs\tracking\controlled-tracking-probe",
            "protected_checksum": "c" * 64,
        }
        payload = tracking.build_task_payload(
            project_id, lineage, {"batch_size": 1})
    else:
        payload = synthetic_task_payload(project_id)
    data = timer.run("task_create", call_service, adapter, "tasks",
                     "create", payload)
    task_id = safe_id(data.get("id"))
    if task_id is None:
        facts.append(("Synthetic task", "created (ID withheld: unexpected "
                                        "format)"))
        raise ProbeFailure("task", "unexpected_task_id")
    facts.append(("Synthetic task", "created %s (metadata only)" % task_id))
    if controlled_tracking:
        try:
            timer.run("task_readback", verify_controlled_task, adapter,
                      task_id, project_id, payload)
        except ProbeFailure as readback_failure:
            try:
                facts.append(("Archive", timer.run(
                    "task_archive", archive_controlled_task, adapter, task_id)))
            except ProbeFailure as archive_failure:
                raise ProbeFailure(
                    readback_failure.finding[0], readback_failure.finding[1],
                    readback_failure.finding[2],
                    additional_findings=[archive_failure.finding])
            raise
        facts.append(("Payload boundary", "approved lineage and scalar hyperparameter allowlists"))
    else:
        timer.run("task_readback", verify_created_task, adapter, task_id,
                  project_id)
    facts.append(("Auto capture", "none (no Task.init/Task.create; no "
                                  "script, repository, diff or package "
                                  "fields)"))
    archive_function = archive_controlled_task if controlled_tracking else archive_task
    facts.append(("Archive", timer.run("task_archive", archive_function,
                                       adapter, task_id)))


def run_probe(create_task, environ, adapter_factory, lstat=os.lstat,
              clock=time.monotonic, controlled_tracking=False):
    """Run the fail-closed live probe; return (findings, facts, timing).

    The environment is checked before the SDK is imported; any environment
    finding stops the probe without importing or contacting anything.
    """
    facts = []
    findings = environment_problems(environ, lstat)
    if findings:
        return findings, facts, None
    facts.append(("Config variable", "%s set (path withheld)"
                  % CONFIG_VARIABLE))
    facts.append(("Offline mode", "not set"))
    timer = Timer(clock)
    try:
        with quiet_sdk():
            probe_service(adapter_factory, create_task, facts, timer,
                          controlled_tracking=controlled_tracking)
    except ProbeFailure as failure:
        findings = list(failure.findings)
    except Exception as error:  # sanitised: text may quote values
        name = type(error).__name__
        findings = [finding("sdk", SDK_ERRORS.get(name, "sdk_failure"),
                            name)]
    return findings, facts, timer.summary()


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

MODE_TEXT = {
    MODE_DECLARATION: "declaration only (no SDK import, environment read "
                      "or service contact)",
    MODE_PREFLIGHT: "live preflight (read-only; nothing created)",
    MODE_SYNTHETIC_TASK: "live preflight and one synthetic metadata-only task",
    MODE_CONTROLLED_TRACKING: "operator-only live probe and one allowlisted metadata task",
}


def report(config_path, mode, findings, facts, timing):
    """Print the check summary, sanitised facts, findings and the result."""
    print("ClearML connection check")
    print("Declaration      : %s" % config_path)
    print("Mode             : %s" % MODE_TEXT[mode])
    print("Declared target  : workspace %s, project %s, API %s"
          % (WORKSPACE, PROJECT, ENDPOINTS["api_server"]))
    for label, text in facts:
        print("%-17s: %s" % (label, text))
    if timing:
        print("Timing           : %s" % timing)
    counts = {}
    for item in findings:
        counts[item] = counts.get(item, 0) + 1
    for section, rule_id, detail in sort_findings(list(counts)):
        print("FINDING [%s] %s%s (%d)" % (
            section, rule_id, " [%s]" % detail if detail else "",
            counts[section, rule_id, detail]))
    if findings:
        print("RESULT: FAIL (%d finding(s))" % len(findings))
    else:
        print("RESULT: PASS")


def build_parser():
    """Build the command-line parser; live modes need an explicit flag."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="declaration inside the repository "
                        "config directory (default: config\\%s)"
                        % CONFIG_NAME)
    live = parser.add_mutually_exclusive_group()
    live.add_argument("--preflight", action="store_true",
                      help="live read-only connection check")
    live.add_argument("--synthetic-task", action="store_true",
                      help="live check plus one synthetic metadata-only task")
    live.add_argument("--controlled-tracking-probe", action="store_true",
                      help="operator-only live check plus one allowlisted metadata task")
    return parser


def run(args, environ, adapter_factory, lstat):
    """Check the declaration, then any requested live mode; return status."""
    config_path = os.path.abspath(args.config or os.path.join(CONFIG_DIR,
                                                              CONFIG_NAME))
    mode = (MODE_CONTROLLED_TRACKING if args.controlled_tracking_probe else
            MODE_SYNTHETIC_TASK if args.synthetic_task else
            MODE_PREFLIGHT if args.preflight else MODE_DECLARATION)
    findings = check_declaration(read_declaration(config_path))
    try:
        findings += check_step9_policy()
    except step9.OperationalError as error:
        raise OperationalError("Step 9 policy: %s" % error)
    facts = [("Declaration check", "FAIL" if findings else "PASS")]
    timing = None
    if mode != MODE_DECLARATION and not findings:
        probe_findings, probe_facts, timing = run_probe(
            mode in (MODE_SYNTHETIC_TASK, MODE_CONTROLLED_TRACKING),
            os.environ if environ is None else environ,
            adapter_factory, lstat,
            controlled_tracking=(mode == MODE_CONTROLLED_TRACKING))
        findings += probe_findings
        facts += probe_facts
    elif mode != MODE_DECLARATION:
        facts.append(("Live probe", "not started (declaration findings)"))
    report(config_path, mode, findings, facts, timing)
    return EXIT_FAIL if findings else EXIT_PASS


def main(argv=None, environ=None, adapter_factory=SdkAdapter,
         lstat=os.lstat):
    """Run the checker, sanitising any failure into an operational result.

    environ, adapter_factory and lstat are injectable for synthetic tests.
    """
    try:
        return run(build_parser().parse_args(argv), environ,
                   adapter_factory, lstat)
    except OperationalError as error:
        print("OPERATIONAL FAILURE: %s" % error)
    except KeyboardInterrupt:
        print("OPERATIONAL FAILURE: interrupted before completion")
    except Exception as error:  # sanitised: text may quote values
        print("OPERATIONAL FAILURE: %s" % step9.sanitise_error(error))
    print("RESULT: OPERATIONAL FAILURE")
    return EXIT_OPERATIONAL


if __name__ == "__main__":
    sys.exit(main())
