#!/usr/bin/env python3
"""Verify the ClearML tracker and storage policy (Step 9).

Standard library only, read-only and CPU-only. Exit status: 0 PASS,
1 FAIL (policy findings), 2 operational failure.

config/clearml_policy.json is read only from the repository config
directory. The verifier never imports the ClearML SDK, reads or writes a
ClearML configuration file, creates a directory or contacts any service;
paths in the policy are compared lexically and never inspected on disk.

The check fails closed when the policy:

1. records a concrete remote server, workspace, project or account, a URL,
   or anything other than not_authorized/offline_only remote tracking, or
   treats the installed package as enabling remote tracking;
2. permits a patient-bearing, content, credential or file category as a
   tracker payload, omits a mandatory forbidden category or holds an
   unknown permitted category;
3. holds a secret-named value, a token- or key-shaped string or
   patient-identifier assignment anywhere;
4. places protected case artefacts or local server volumes in the
   disposable runtime root (or anywhere outside the protected private
   workspace), or lets the runtime root hold protected data;
5. does not keep tracker metadata and protected case artefacts as
   distinct, disjoint categories and destinations;
6. omits, duplicates or misdescribes a required lineage field, or claims
   that a lineage record already exists.

Output holds section names, rule IDs and counts only; no policy value,
secret or synthetic content is printed.

Usage:
    python -m ehr_evidence_app.tracking.policy [--config PATH]
"""

import argparse
import json
import ntpath
import os
import re
import stat
import sys

from ehr_evidence_app.storage import policy as guard

SCHEMA_VERSION = "1.0"
STEP = "9"
REPO_ROOT = guard.REPO_ROOT
CONFIG_DIR = os.path.join(REPO_ROOT, "config")
CONFIG_NAME = "clearml_policy.json"
PRIVATE_ROOT = guard.PRIVATE_ROOT
RUNTIME_ROOT = guard.RUNTIME_ROOT
MAX_CONFIG_BYTES = 64 * 1024

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_OPERATIONAL = 2

TOP_REQUIRED = ("schema_version", "step", "purpose", "tracker",
                "remote_tracking", "credential_handling", "categories",
                "destinations", "local_server", "lineage_fields", "step_10")
TOP_OPTIONAL = ("notes", "model_artefacts")
# The one exception to the forbidden model_files: the listed pretrained
# models, not trained on this project's data, whose files and revision are
# pinned in config/generation.json (ADR 0018).
MODEL_ARTEFACTS = {
    "status": "listed_pretrained_models_only",
    "decision": "adr_0018",
    "listed_models": [{
        "repository": "google/medgemma-1.5-4b-it",
        "files_and_revision": "config/generation.json",
        "trained_on_project_data": False,
        "licence": "health-ai-developer-foundations"}],
    "never_uploaded": ["patient_derived_models", "gnn_checkpoints",
                       "model_outputs", "patient_records", "prompts",
                       "llm_responses", "data_files"],
}

TRACKER = {
    "name": "clearml",
    "package": "clearml",
    "locked_version": "2.1.12",
    "selection_basis": "present_in_approved_application_lock",
    "package_installed_implies_remote_enabled": False,
}
REMOTE_VALUE_FIELDS = ("api_server", "web_server", "files_server",
                       "workspace", "project", "account")
REMOTE_TRACKING = dict(
    [(field, None) for field in REMOTE_VALUE_FIELDS]
    + [("status", "not_authorized"), ("mode", "offline_only"),
       ("authorization_gate", "user_approved_step_10_configuration"),
       ("service_contacted_in_step_9", False)])
CREDENTIAL_HANDLING = {
    "source": "environment_variables_or_untracked_local_file",
    "configured_in_step_9": False,
    "values_in_policy": False,
    "values_in_git": False,
    "values_in_logs": False,
    "values_in_tracker_payloads": False,
}
STEP_10 = {
    "sdk_configuration": "deferred_to_step_10",
    "remote_authorization": "requires_user_approval",
    "tracking_calls": "deferred_to_later_steps",
}

PERMITTED_KEY = "permitted_tracker_payloads"
FORBIDDEN_KEY = "forbidden_tracker_payloads"
KNOWN_PERMITTED = frozenset([
    "non_patient_run_metadata", "version_identifiers", "code_fingerprints",
    "configuration_fingerprints", "evidence_schema_fingerprints",
    "asset_fingerprints", "aggregate_evaluation_metrics",
    "aggregate_validation_metrics", "aggregate_operational_metrics",
    "protected_w_references", "protected_w_checksums"])
REQUIRED_FORBIDDEN = frozenset([
    "patient_records", "patient_identifiers", "raw_clinical_content",
    "derived_clinical_content", "prompts", "llm_responses",
    "attribution_text", "case_level_outputs", "credentials", "tokens",
    "model_files", "data_files"])
# Words that mark a category as patient-bearing content, a credential or a
# file; such a category may never be a permitted tracker payload.
PROTECTED_CATEGORY = re.compile(
    r"patient|clinical|prompt|response|attribution|case|note|record|text|"
    r"raw|credential|token|secret|password|key|model_file|data_file|"
    r"weight|checkpoint|graph")

TRACKER_METADATA = {
    "categories": PERMITTED_KEY,
    "patient_bearing": False,
    "remote_upload": "not_authorized",
    "lineage_authority": False,
}
TRACKER_METADATA_ROOTS = {"runtime": "clearml_cache",
                          "workspace": "clearml_local"}
PROTECTED_ARTEFACTS = {
    "categories": FORBIDDEN_KEY,
    "patient_bearing": True,
    "sent_to_tracker": False,
    "tracker_representation": "protected_w_references_and_checksums_only",
    "lineage_authority": True,
}
RUNTIME_CACHE = {
    "root_path": RUNTIME_ROOT,
    "use": "disposable_runtime_and_cache_only",
    "protected_data_permitted": False,
    "local_server_volumes_permitted": False,
}
LOCAL_SERVER_STATUSES = ("not_deployed",)

LINEAGE_FIELDS = ("code_revision", "case_set_version", "llm_configuration",
                  "prompt_version", "evidence_schema_version",
                  "validation_configuration", "evaluation_outputs")
LINEAGE_FORMS = {
    "code_revision": ("version_identifier", "fingerprint"),
    "case_set_version": ("version_identifier", "fingerprint"),
    "llm_configuration": ("version_identifier", "fingerprint"),
    "prompt_version": ("version_identifier", "fingerprint"),
    "evidence_schema_version": ("version_identifier", "fingerprint"),
    "validation_configuration": ("version_identifier", "fingerprint"),
    "evaluation_outputs": ("aggregate_and_protected_reference",),
}
LINEAGE_KEYS = frozenset(["name", "tracked_as", "status"])
LINEAGE_STATUS = "defined_not_recorded"

SECRET_KEY = re.compile(
    r"(?i)password|passwd|passphrase|secret|token|api[_-]?key|"
    r"access[_-]?key|private[_-]?key|credential")
SECRET_SHAPE = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"\bsk-[A-Za-z0-9_-]{20,}|\bhf_[A-Za-z0-9]{20,}|"
    r"\bxox[abposr]-[A-Za-z0-9-]{10,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|"
    r"\beyJ[A-Za-z0-9_-]{10,}\.|-{5}BEGIN[A-Z ]*PRIVATE KEY|"
    r"(?<![0-9A-Za-z])[0-9A-Fa-f]{32,}(?![0-9A-Za-z])")
URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://")
PATIENT_IDENTIFIER = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:person_id|subject_id|hadm_id|patient_index)"
    r"\s*[:=]\s*[0-9]{2,}")

SECTIONS = ("schema", "tracker", "remote_tracking", "credential_handling",
            "secrets", "categories", "destinations", "local_server",
            "lineage_fields", "step_10", "model_artefacts")


class OperationalError(Exception):
    """A failure that prevents trustworthy verification; never a PASS."""


def sanitise_error(error):
    """Describe an unexpected failure without repeating any value."""
    if isinstance(error, OSError):
        return "%s: %s" % (type(error).__name__, error.strerror or "OS error")
    return "%s (message withheld to avoid exposing values)" % type(
        error).__name__


# Policy File

def reject_duplicate_keys(pairs):
    """Build a JSON object, refusing repeated keys that could hide values."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise OperationalError("the policy repeats a JSON key")
        result[key] = value
    return result


def read_policy(path):
    """Return the parsed policy from the repository config directory.

    The path must lie inside the config directory and be a regular file
    reached without a link at its leaf. Errors never quote content.
    """
    if not guard.is_within(path, CONFIG_DIR, strict=True):
        raise OperationalError("the policy must be read from %s" % CONFIG_DIR)
    try:
        info = os.lstat(path)
    except OSError as error:
        raise OperationalError("cannot inspect the policy (%s)"
                               % type(error).__name__)
    if guard.is_link_like(info) or not stat.S_ISREG(info.st_mode):
        raise OperationalError("the policy is not a regular file")
    if info.st_size > MAX_CONFIG_BYTES:
        raise OperationalError("the policy is larger than %d bytes"
                               % MAX_CONFIG_BYTES)
    with open(path, "rb") as handle:
        data = handle.read(MAX_CONFIG_BYTES + 1)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise OperationalError("the policy is not valid UTF-8")
    try:
        return json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except ValueError:
        raise OperationalError("the policy is not valid JSON")


# Checks (Values Are Examined, Never Returned or Printed)

def same_value(value, expected):
    """Return True when value equals expected with identical JSON types."""
    if isinstance(expected, dict):
        return (isinstance(value, dict) and set(value) == set(expected)
                and all(same_value(value[key], expected[key])
                        for key in expected))
    return type(value) is type(expected) and value == expected


def walk(value, key=None):
    """Yield (key, value) for every JSON node; key is the owning key."""
    yield key, value
    if isinstance(value, dict):
        for name, item in value.items():
            yield None, name
            for pair in walk(item, name):
                yield pair
    elif isinstance(value, list):
        for item in value:
            for pair in walk(item, key):
                yield pair


def is_runtime_path(value):
    """Return True when a path string lies inside the runtime root."""
    return (isinstance(value, str) and ntpath.isabs(value)
            and guard.is_within(value, RUNTIME_ROOT))


def is_workspace_protected(value):
    """Return True when a path string lies inside the private root."""
    return (isinstance(value, str) and ntpath.isabs(value)
            and guard.is_within(value, PRIVATE_ROOT))


def check_schema(policy):
    """Return schema findings for the top level of the policy."""
    missing = set(TOP_REQUIRED) - set(policy)
    unknown = set(policy) - set(TOP_REQUIRED) - set(TOP_OPTIONAL)
    findings = [("schema", "required_key_missing") for _ in missing]
    findings += [("schema", "unsupported_key") for _ in unknown]
    if not same_value(policy.get("schema_version"), SCHEMA_VERSION):
        findings.append(("schema", "schema_version_invalid"))
    if not same_value(policy.get("step"), STEP):
        findings.append(("schema", "step_invalid"))
    purpose = policy.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        findings.append(("schema", "purpose_invalid"))
    notes = policy.get("notes", [])
    if (not isinstance(notes, list)
            or not all(isinstance(item, str) and item.strip()
                       for item in notes)):
        findings.append(("schema", "notes_invalid"))
    return findings


def check_tracker(section):
    """Return findings for the tracker selection."""
    if not isinstance(section, dict):
        return [("tracker", "tracker_invalid")]
    findings = []
    if section.get("package_installed_implies_remote_enabled") is not False:
        findings.append(("tracker", "remote_enabled_by_package"))
    if not same_value(section, TRACKER):
        findings.append(("tracker", "tracker_invalid"))
    return findings


def check_remote(section):
    """Return findings unless remote tracking is unauthorised and empty.

    Any concrete endpoint, workspace, project or account value, any extra
    field and any status other than not_authorized/offline_only is a
    remote configuration.
    """
    if not isinstance(section, dict):
        return [("remote_tracking", "remote_configuration_present")]
    findings = []
    concrete = [field for field in REMOTE_VALUE_FIELDS
                if section.get(field) is not None]
    extra = set(section) - set(REMOTE_TRACKING)
    findings += [("remote_tracking", "remote_configuration_present")
                 for _ in concrete + sorted(extra)]
    if (section.get("status") != "not_authorized"
            or section.get("mode") != "offline_only"):
        findings.append(("remote_tracking", "remote_tracking_enabled"))
    if not findings and not same_value(section, REMOTE_TRACKING):
        findings.append(("remote_tracking", "remote_policy_invalid"))
    return findings


def check_credentials(section):
    """Return findings unless credentials stay outside policy and payloads."""
    if not same_value(section, CREDENTIAL_HANDLING):
        return [("credential_handling", "credential_handling_invalid")]
    return []


def check_content(policy):
    """Return findings for secrets, URLs or patient identifiers anywhere.

    A secret-named key may hold only null, false, an object or a list;
    every string (keys included) is checked for token/key shapes, URLs and
    patient-identifier assignments.
    """
    findings = []
    for key, value in walk(policy):
        if (key is not None and SECRET_KEY.search(key)
                and not (value is None or value is False
                         or isinstance(value, (dict, list)))):
            findings.append(("secrets", "secret_present"))
        if not isinstance(value, str):
            continue
        if SECRET_SHAPE.search(value):
            findings.append(("secrets", "secret_present"))
        if URL.search(value):
            findings.append(("remote_tracking",
                             "remote_configuration_present"))
        if PATIENT_IDENTIFIER.search(value):
            findings.append(("secrets", "patient_content_present"))
    return findings


def category_list(section, key):
    """Return a category list, or None when it is not a list of text."""
    items = section.get(key) if isinstance(section, dict) else None
    if (not isinstance(items, list) or not items
            or not all(isinstance(item, str) and item for item in items)):
        return None
    return items


def check_categories(section):
    """Return findings for the permitted and forbidden payload categories."""
    permitted = category_list(section, PERMITTED_KEY)
    forbidden = category_list(section, FORBIDDEN_KEY)
    if (permitted is None or forbidden is None
            or set(section) != set([PERMITTED_KEY, FORBIDDEN_KEY])):
        return [("categories", "missing_separation")]
    findings = []
    if (len(set(permitted)) != len(permitted)
            or len(set(forbidden)) != len(forbidden)):
        findings.append(("categories", "duplicate_category"))
    for item in set(permitted) - KNOWN_PERMITTED:
        if item in REQUIRED_FORBIDDEN or PROTECTED_CATEGORY.search(item):
            findings.append(("categories",
                             "patient_bearing_category_permitted"))
        else:
            findings.append(("categories", "unknown_permitted_category"))
    findings += [("categories", "forbidden_category_missing")
                 for _ in REQUIRED_FORBIDDEN - set(forbidden)]
    if set(permitted) & set(forbidden):
        findings.append(("categories", "missing_separation"))
    return findings


def check_fixed_fields(section, expected):
    """Return True when section holds every expected field with its value."""
    return isinstance(section, dict) and all(
        key in section and same_value(section[key], value)
        for key, value in expected.items())


def check_destinations(section):
    """Return findings for tracker, protected and runtime destinations."""
    if not isinstance(section, dict):
        return [("destinations", "missing_separation")]
    findings = []
    if set(section) != set(["tracker_metadata", "protected_case_artefacts",
                            "runtime_cache"]):
        findings.append(("destinations", "missing_separation"))

    tracker = section.get("tracker_metadata")
    if (not check_fixed_fields(tracker, TRACKER_METADATA)
            or set(tracker) != set(TRACKER_METADATA) | set(
                ["local_directory_key", "root"])
            or not isinstance(tracker.get("root"), str)
            or TRACKER_METADATA_ROOTS.get(tracker["root"])
            != tracker.get("local_directory_key")):
        findings.append(("destinations", "missing_separation"))

    protected = section.get("protected_case_artefacts")
    if (not check_fixed_fields(protected, PROTECTED_ARTEFACTS)
            or set(protected) != set(PROTECTED_ARTEFACTS) | set(
                ["root_path"])):
        findings.append(("destinations", "missing_separation"))
    root = protected.get("root_path") if isinstance(protected, dict) else None
    if is_runtime_path(root):
        findings.append(("destinations", "runtime_protected_destination"))
    elif not is_workspace_protected(root):
        findings.append(("destinations",
                         "protected_destination_outside_workspace"))

    runtime = section.get("runtime_cache")
    if isinstance(runtime, dict) and (
            runtime.get("protected_data_permitted") is not False
            or runtime.get("local_server_volumes_permitted") is not False):
        findings.append(("destinations", "runtime_protected_destination"))
    elif not same_value(runtime, RUNTIME_CACHE):
        findings.append(("destinations", "runtime_cache_invalid"))
    return findings


def check_local_server(section):
    """Return findings unless any local server volume stays in the workspace."""
    if not isinstance(section, dict) or set(section) != set([
            "status", "volume_root_required",
            "separate_verification_required"]):
        return [("local_server", "local_server_invalid")]
    findings = []
    root = section.get("volume_root_required")
    if is_runtime_path(root):
        findings.append(("local_server", "runtime_protected_destination"))
    elif not is_workspace_protected(root):
        findings.append(("local_server",
                         "protected_destination_outside_workspace"))
    if (section.get("status") not in LOCAL_SERVER_STATUSES
            or section.get("separate_verification_required") is not True):
        findings.append(("local_server", "local_server_invalid"))
    return findings


def check_lineage(fields):
    """Return findings for the report-required lineage field definitions."""
    if not isinstance(fields, list):
        return [("lineage_fields", "invalid_lineage_field")]
    findings = []
    seen = []
    for entry in fields:
        if (not isinstance(entry, dict) or set(entry) != LINEAGE_KEYS
                or not isinstance(entry.get("name"), str)
                or entry.get("name") not in LINEAGE_FORMS
                or entry.get("name") in seen
                or entry.get("tracked_as") not in LINEAGE_FORMS.get(
                    entry.get("name"), ())
                or entry.get("status") != LINEAGE_STATUS):
            findings.append(("lineage_fields", "invalid_lineage_field"))
        if (isinstance(entry, dict) and isinstance(entry.get("name"), str)
                and entry.get("name") in LINEAGE_FORMS):
            seen.append(entry.get("name"))
    findings += [("lineage_fields", "lineage_field_missing")
                 for name in LINEAGE_FIELDS if name not in seen]
    return findings


def check_policy(policy):
    """Return every finding for a parsed policy, sorted deterministically.

    Each finding is a (section, rule ID) pair; no policy value is included.
    """
    if not isinstance(policy, dict):
        return [("schema", "policy_not_object")]
    findings = check_schema(policy)
    findings += check_tracker(policy.get("tracker"))
    findings += check_remote(policy.get("remote_tracking"))
    findings += check_credentials(policy.get("credential_handling"))
    findings += check_content(policy)
    findings += check_categories(policy.get("categories"))
    findings += check_destinations(policy.get("destinations"))
    findings += check_local_server(policy.get("local_server"))
    findings += check_lineage(policy.get("lineage_fields"))
    if not same_value(policy.get("step_10"), STEP_10):
        findings.append(("step_10", "step_10_boundary_invalid"))
    if "model_artefacts" in policy and not same_value(
            policy["model_artefacts"], MODEL_ARTEFACTS):
        findings.append(("model_artefacts", "model_artefacts_invalid"))
    return sorted(findings, key=lambda item: (SECTIONS.index(item[0]),
                                              item[1]))


# Command Line

def report(config_path, findings):
    """Print the policy summary, rule IDs and the PASS/FAIL result."""
    print("ClearML tracker and storage policy verification")
    print("Policy           : %s" % config_path)
    print("Tracker          : ClearML %s; remote tracking not_authorized, "
          "offline_only (policy only; no SDK import or service contact)"
          % TRACKER["locked_version"])
    print("Lineage fields   : %d required" % len(LINEAGE_FIELDS))
    counts = {}
    for finding in findings:
        counts[finding] = counts.get(finding, 0) + 1
    for (section, rule_id) in sorted(counts, key=lambda item: (
            SECTIONS.index(item[0]), item[1])):
        print("FINDING [%s] %s (%d)" % (section, rule_id,
                                        counts[section, rule_id]))
    if findings:
        print("RESULT: FAIL (%d finding(s))" % len(findings))
    else:
        print("RESULT: PASS")


def build_parser():
    """Build the command-line parser; only a policy file may be chosen."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="policy inside the repository "
                        "config directory (default: config\\%s)"
                        % CONFIG_NAME)
    return parser


def run(args):
    """Check the policy file and return the process exit status."""
    config_path = os.path.abspath(args.config or os.path.join(CONFIG_DIR,
                                                              CONFIG_NAME))
    findings = check_policy(read_policy(config_path))
    report(config_path, findings)
    return EXIT_FAIL if findings else EXIT_PASS


def main(argv=None):
    """Run the verifier, sanitising any failure into an operational result."""
    try:
        return run(build_parser().parse_args(argv))
    except OperationalError as error:
        print("OPERATIONAL FAILURE: %s" % error)
    except KeyboardInterrupt:
        print("OPERATIONAL FAILURE: interrupted before completion")
    except Exception as error:  # sanitised: text may quote values
        print("OPERATIONAL FAILURE: %s" % sanitise_error(error))
    print("RESULT: OPERATIONAL FAILURE")
    return EXIT_OPERATIONAL


if __name__ == "__main__":
    sys.exit(main())
