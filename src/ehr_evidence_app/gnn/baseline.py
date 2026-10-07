#!/usr/bin/env python3
"""Capture the inherited upstream GNN baseline without running it (Step 1).

Standard library only. For a fixed set of upstream files the record holds the
logical role, absolute path, type, size, UTC modification time and a streamed
SHA-256, each checked against the identity in config/inherited_baseline.json.
From the documentation only the title and the 'Last updated' date are
extracted. From the canonical run's metrics.json only task, data_source,
no_kg, best_epoch, best_val, test and the history length are parsed, and
they are labelled as inherited, reported values that the downstream project
did not execute. The checkpoint, patient graphs, explanations and
vocabularies are opened only to hash their bytes. No upstream code is
imported or executed.

The upstream root, the baseline output directory and the configuration
directory are fixed in code; configuration cannot redirect them. Every read is
preflighted (plain drive path, no link or reparse point, expected type) and
every write passes the Step 0.4 destination checks before any output exists.
The record is deterministic: a rerun with identical inputs leaves an
identical existing record untouched, and a different existing record is
never overwritten. Choose a new --output name for a new baseline version.

Git: without a .git entry in the upstream root, the revision and working-tree
state are recorded as null with status unavailable_no_git_metadata. With a
plain .git directory, git runs without a shell, in the upstream root, with a
bounded timeout, capturing only the revision and a count of changed entries.
Any git failure is recorded as unavailable, never as clean.

Usage:
    python -m ehr_evidence_app.gnn.baseline --output PATH
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import ntpath
import os
import re
import shutil
import stat
import subprocess
import sys

from ehr_evidence_app.storage import policy as guard

VERSION = "1.0"
SCHEMA_VERSION = "1.0"
REPO_ROOT = guard.REPO_ROOT
CONFIG_DIR = os.path.join(REPO_ROOT, "config")
CONFIG_NAME = "inherited_baseline.json"
UPSTREAM_ROOT = guard.POLICY_ANCHOR + "\\standardized-omop-gnn-7B82"
BASELINE_ROOT = guard.PRIVATE_ROOT + "\\outputs\\baselines"
CANONICAL_RUN = "diag_kg"
CHUNK_BYTES = 8 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 30
PARTIAL = ".partial"

DOCUMENTATION_ROLE = "documentation"
METRICS_ROLE = "canonical_metrics"
RUN_ROLES = ("canonical_checkpoint", "canonical_metrics",
             "canonical_explanations")
# Every role must be configured exactly once; the value is its category.
ASSET_ROLES = {
    "documentation": "documentation",
    "source_explain_patient": "upstream_source",
    "source_train": "upstream_source",
    "source_model": "upstream_source",
    "source_build_patient_graphs": "upstream_source",
    "canonical_checkpoint": "canonical_run_artifact",
    "canonical_metrics": "canonical_run_artifact",
    "canonical_explanations": "canonical_run_artifact",
    "patient_graphs": "processed_data",
    "concept_vocab": "processed_data",
    "label_vocab": "processed_data",
}
HASH_ONLY = "hashed_only_not_loaded"
CONTENT_HANDLING = {
    DOCUMENTATION_ROLE: "title_and_last_updated_extracted",
    METRICS_ROLE: "required_aggregate_fields_parsed",
}

# The canonical run identity is fixed here; configuration cannot relax it.
CANONICAL_IDENTITY = (("task", "diagnosis"), ("data_source", "omop"),
                      ("no_kg", False))
HISTORY_LENGTH = 50
# An aggregate object with more entries than this may be keyed by record
# (for example per patient), so its key names are never copied.
MAX_AGGREGATE_ENTRIES = 256
METRIC_FIELDS = ("task", "data_source", "no_kg", "best_epoch", "best_val",
                 "test", "history")
PROVENANCE = "inherited_reported"
EXECUTION_STATUS = "not_executed_by_downstream"

HEX64 = re.compile(r"^[0-9A-Fa-f]{64}$")
ISO_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
GIT_REVISION = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
METRIC_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_.@-]{0,63}$")
TITLE_LINE = re.compile(r"^#[ \t]+(.+?)[ \t]*#*[ \t]*$")
LAST_UPDATED = re.compile(
    r"last[ \t_-]*updated[^0-9\r\n]{0,16}?([0-9]{4}-[0-9]{2}-[0-9]{2})",
    re.IGNORECASE)

NO_GIT_NOTE = ("The upstream directory has no .git metadata, so there is no "
               "revision or working-tree state to record; the tree is not "
               "claimed to be clean. The SHA-256 content hashes of the listed "
               "assets are the reproducible baseline surrogate.")
GIT_ERROR_NOTE = ("git did not report a usable revision and status; the "
                  "revision and working-tree state are unknown, not clean. "
                  "The SHA-256 content hashes remain the baseline surrogate.")


class BaselineError(Exception):
    """A capture failure whose message names roles and paths only."""


def sanitise_error(error):
    """Describe a failure without repeating file content or field values."""
    if isinstance(error, OSError):
        return "%s: %s" % (type(error).__name__, error.strerror or "OS error")
    return "%s (message withheld to avoid exposing file content)" % type(
        error).__name__


def overlaps(first, second):
    """Return True when either path equals or lies beneath the other."""
    return guard.is_within(first, second) or guard.is_within(second, first)


def is_number(value):
    """Return True for a finite JSON number that is not a boolean."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def is_count(value):
    """Return True for a non-negative integer that is not a boolean."""
    return (isinstance(value, int) and not isinstance(value, bool)
            and value >= 0)


def utc_from_ns(nanoseconds):
    """Return an ISO UTC timestamp, to the second, for a time in ns."""
    epoch = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
    moment = epoch + datetime.timedelta(microseconds=nanoseconds // 1000)
    return moment.isoformat(timespec="seconds")


def sha256_stream(path):
    """Return the SHA-256 and byte count of a file read in bounded chunks."""
    digest = hashlib.sha256()
    count = 0
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(block)
            count += len(block)
    return digest.hexdigest(), count


# Fixed Roots, Configuration and Path Preflight

def fixed_policy_problem():
    """Return why the fixed roots in this module are unsafe, else None.

    The upstream root must lie under the policy anchor and apart from this
    repository, the baseline root inside the private root and apart from the
    upstream tree, so no write can land in the upstream tree.
    """
    if os.name != "nt":
        return "baseline capture supports Windows only"
    if sys.version_info < (3, 8):
        return ("Python 3.8 or later is required to inspect junctions "
                "without following them")
    problem = guard.fixed_policy_problem()
    if problem:
        return "fixed policy: %s" % problem
    for label, root in (("upstream root", UPSTREAM_ROOT),
                        ("baseline root", BASELINE_ROOT),
                        ("configuration directory", CONFIG_DIR)):
        problem = guard.path_problem(root)
        if problem:
            return "%s: %s" % (label, problem)
    if not guard.is_within(UPSTREAM_ROOT, guard.POLICY_ANCHOR, strict=True):
        return "the upstream root is not inside %s" % guard.POLICY_ANCHOR
    if not guard.is_within(CONFIG_DIR, guard.POLICY_ANCHOR, strict=True):
        return "the configuration directory is not inside %s" % (
            guard.POLICY_ANCHOR)
    if not guard.is_within(BASELINE_ROOT, guard.PRIVATE_ROOT, strict=True):
        return "the baseline root is not inside %s" % guard.PRIVATE_ROOT
    if overlaps(UPSTREAM_ROOT, REPO_ROOT):
        return "the upstream root and the source repository overlap"
    if overlaps(UPSTREAM_ROOT, BASELINE_ROOT):
        return "the baseline root and the upstream tree overlap"
    return None


def source_problem(path, kind, label):
    """Return why a read-only source path is unsafe to read, else None.

    Lexical checks first, then a walk from the drive root with lstat, so no
    link is followed: every component must exist, none may be a symlink,
    junction or other reparse point, every ancestor must be a directory and
    the leaf must be the expected kind ('file' or 'directory').
    """
    problem = guard.path_problem(path)
    if problem:
        return "%s: %s" % (label, problem)
    current = path[:3]
    parts = path[3:].split("\\")
    info = None
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return "%s: %s is missing" % (label, path)
        except OSError as error:
            return "%s: cannot inspect %s safely (%s)" % (
                label, current, type(error).__name__)
        if guard.is_link_like(info):
            return "%s: %s is a symlink, junction or other reparse point" % (
                label, current)
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            return "%s: %s is not a directory" % (label, current)
    if kind == "directory" and not stat.S_ISDIR(info.st_mode):
        return "%s: %s is not a directory" % (label, path)
    if kind == "file" and not stat.S_ISREG(info.st_mode):
        return "%s: %s is not a regular file" % (label, path)
    return None


def config_path_problem(path):
    """Return why the configuration file may not be read, else None."""
    if not isinstance(path, str):
        return "configuration path is not text"
    problem = guard.path_problem(path)
    if problem:
        return "configuration path: %s" % problem
    if not guard.is_within(path, CONFIG_DIR, strict=True):
        return "the configuration must be read from %s" % CONFIG_DIR
    return source_problem(path, "file", "configuration")


def relative_problem(relative):
    """Return why an upstream-relative asset path is unsafe, else None.

    The joined path must be a plain drive path, so '..', '.', forward
    slashes, drives, UNC prefixes, streams and reserved names are refused,
    and no component may be .git.
    """
    if not isinstance(relative, str) or not relative:
        return "relative path is empty or not text"
    if any(part.lower() == ".git" for part in relative.split("\\")):
        return "relative path names .git"
    return guard.path_problem(asset_path(relative))


def asset_path(relative):
    """Return the absolute path of an upstream-relative asset path."""
    return UPSTREAM_ROOT + "\\" + relative


def documentation_config_problem(section):
    """Return why the expected documentation metadata is unusable."""
    if not isinstance(section, dict):
        return "documentation must be an object"
    title = section.get("expected_title")
    if not isinstance(title, str) or not title.strip() or "\n" in title:
        return "documentation.expected_title must be one non-empty line"
    updated = section.get("expected_last_updated")
    if not isinstance(updated, str) or not ISO_DATE.match(updated):
        return "documentation.expected_last_updated must be YYYY-MM-DD"
    return None


def metrics_config_problem(section):
    """Return why the expected reported metric values are unusable."""
    if not isinstance(section, dict):
        return "metrics must be an object"
    if not is_count(section.get("expected_best_epoch")):
        return "metrics.expected_best_epoch must be a non-negative integer"
    values = section.get("reported_test_values")
    if not isinstance(values, dict) or not values:
        return "metrics.reported_test_values must be a non-empty object"
    for label, value in values.items():
        if not METRIC_KEY.match(label) or not is_number(value):
            return ("metrics.reported_test_values needs plain labels and "
                    "finite numbers")
    return None


def assets_config_problem(assets):
    """Return why the configured asset list is incomplete or unsafe.

    Every fixed role must appear exactly once with a safe relative path, a
    distinct file, an expected size and an expected SHA-256; canonical run
    artefacts must lie in that run's directory.
    """
    if not isinstance(assets, list):
        return "assets must be a list"
    roles = []
    paths = set()
    run_prefix = ntpath.normcase("data\\runs\\%s\\" % CANONICAL_RUN)
    for entry in assets:
        role = entry.get("role") if isinstance(entry, dict) else None
        if role not in ASSET_ROLES:
            return "assets contains an unknown or missing role"
        if role in roles:
            return "asset role %s is configured more than once" % role
        roles.append(role)
        relative = entry.get("relative_path")
        problem = relative_problem(relative)
        if problem:
            return "asset %s relative_path: %s" % (role, problem)
        key = ntpath.normcase(relative)
        if key in paths:
            return "asset %s repeats another asset's path" % role
        paths.add(key)
        if role in RUN_ROLES and not key.startswith(run_prefix):
            return "asset %s must lie in data\\runs\\%s" % (role,
                                                           CANONICAL_RUN)
        if not is_count(entry.get("expected_size_bytes")):
            return "asset %s needs a non-negative expected_size_bytes" % role
        digest = entry.get("expected_sha256")
        if not isinstance(digest, str) or not HEX64.match(digest):
            return "asset %s needs a 64-digit hex expected_sha256" % role
    missing = sorted(set(ASSET_ROLES) - set(roles))
    if missing:
        return "assets lacks required roles: %s" % ", ".join(missing)
    return None


def config_problem(config):
    """Return why the baseline configuration is unusable, else None.

    upstream_root, output_root and canonical_run must equal the values
    fixed in code, so configuration can neither redirect the upstream tree
    nor move or widen the output location.
    """
    if not isinstance(config, dict):
        return "configuration is not a JSON object"
    if config.get("schema_version") != SCHEMA_VERSION:
        return "configuration schema_version must be %s" % SCHEMA_VERSION
    for key, fixed in (("upstream_root", UPSTREAM_ROOT),
                       ("output_root", BASELINE_ROOT)):
        value = config.get(key)
        if (not isinstance(value, str)
                or ntpath.normcase(value) != ntpath.normcase(fixed)):
            return ("%s must be exactly %s; configuration cannot redirect "
                    "or widen the fixed roots" % (key, fixed))
    if config.get("canonical_run") != CANONICAL_RUN:
        return "canonical_run must be %s" % CANONICAL_RUN
    limitations = config.get("limitations", [])
    if (not isinstance(limitations, list)
            or not all(isinstance(item, str) for item in limitations)):
        return "limitations must be a list of text"
    return (documentation_config_problem(config.get("documentation"))
            or metrics_config_problem(config.get("metrics"))
            or assets_config_problem(config.get("assets")))


def load_config(path):
    """Read the configuration, reporting malformed JSON without content."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except ValueError:
        raise BaselineError("the configuration is not valid UTF-8 JSON")


def output_problem(output):
    """Return why the explicit output path may not be written, else None.

    The output must be a .json file directly inside the fixed baseline root,
    and the baseline root, the output and its .partial name must pass the
    Step 0.4 destination checks. A leftover .partial file is refused rather
    than reused or deleted.
    """
    if not isinstance(output, str):
        return "--output is required"
    problem = guard.path_problem(output)
    if problem:
        return "output: %s" % problem
    if ntpath.normcase(ntpath.dirname(output)) != ntpath.normcase(
            BASELINE_ROOT):
        return "the output must be a file directly inside %s" % BASELINE_ROOT
    name = ntpath.basename(output)
    problem = guard.name_problem(name)
    if problem:
        return "output name: %s" % problem
    if not name.lower().endswith(".json"):
        return "the output name must end with .json"
    if overlaps(output, UPSTREAM_ROOT):
        return "the output lies in the upstream tree"
    problem = guard.destinations_problem([(BASELINE_ROOT, "directory"),
                                          (output, "file"),
                                          (output + PARTIAL, "file")])
    if problem:
        return problem
    if os.path.lexists(output + PARTIAL):
        return ("%s exists from an earlier interrupted capture; inspect and "
                "remove it first" % (output + PARTIAL))
    return None


def sources_problem(config):
    """Return the first unsafe read among the upstream root and assets."""
    problem = source_problem(UPSTREAM_ROOT, "directory", "upstream root")
    if problem:
        return problem
    for entry in config["assets"]:
        problem = source_problem(asset_path(entry["relative_path"]), "file",
                                 entry["role"])
        if problem:
            return problem
    return None


# Git Revision (Optional; Absent for the Real Upstream Tree)

def git_record(status, note, invoked, revision=None, tree=None, count=None):
    """Return the upstream revision block in its fixed shape."""
    return {"revision_status": status, "revision": revision,
            "working_tree_status": tree, "changed_entry_count": count,
            "git_invoked": invoked, "note": note}


def git_environment(root):
    """Return the process environment for git without GIT_* redirects.

    Inherited GIT_* variables (GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, ...)
    are dropped so git inspects only the upstream root; optional locks and
    prompts are disabled so status does not write the index. No environment
    value is recorded.
    """
    env = {name: value for name, value in os.environ.items()
           if not name.upper().startswith("GIT_")}
    env.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0",
                "GIT_CEILING_DIRECTORIES": os.path.dirname(root)})
    return env


def run_git(root):
    """Return the revision and a changed-entry count from git, or unknown.

    Runs rev-parse and status without a shell, in the upstream root, with a
    bounded timeout. Only the revision text and the number of status lines
    are kept; file names in the status output are discarded. Any failure,
    timeout or unexpected revision is reported as unavailable, not clean.
    """
    executable = shutil.which("git")
    if not executable:
        return git_record("unavailable_git_error",
                          "git executable not found. " + GIT_ERROR_NOTE,
                          False)
    base = [executable, "-c", "core.fsmonitor=false", "--no-optional-locks"]
    options = {"cwd": root, "env": git_environment(root), "shell": False,
               "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
               "stderr": subprocess.DEVNULL, "timeout": GIT_TIMEOUT_SECONDS,
               "check": False}
    try:
        head = subprocess.run(base + ["rev-parse", "--verify", "HEAD"],
                              **options)
        tree = subprocess.run(base + ["status", "--porcelain=v1",
                                      "--untracked-files=normal"], **options)
    except (OSError, subprocess.SubprocessError) as error:
        return git_record("unavailable_git_error", "%s. %s" % (
            type(error).__name__, GIT_ERROR_NOTE), True)
    revision = (head.stdout or b"").decode("ascii", "replace").strip()
    if (head.returncode != 0 or tree.returncode != 0
            or not GIT_REVISION.match(revision)):
        return git_record("unavailable_git_error", GIT_ERROR_NOTE, True)
    changed = len([line for line in (tree.stdout or b"").splitlines()
                   if line.strip()])
    return git_record("available",
                      "Revision and changed-entry count reported by git; "
                      "changed file names are not recorded.", True,
                      revision, "clean" if changed == 0 else "modified",
                      changed)


def git_state(root):
    """Return the upstream revision block, never inventing a revision.

    No .git entry gives unavailable_no_git_metadata without running git. A
    .git entry that is a file, link or reparse point could redirect git
    elsewhere, so git is not run for it either.
    """
    try:
        info = os.lstat(os.path.join(root, ".git"))
    except FileNotFoundError:
        return git_record("unavailable_no_git_metadata", NO_GIT_NOTE, False)
    except OSError as error:
        return git_record("unavailable_git_metadata_unreadable", "%s. %s" % (
            type(error).__name__, GIT_ERROR_NOTE), False)
    if guard.is_link_like(info) or not stat.S_ISDIR(info.st_mode):
        return git_record("unavailable_unsupported_git_metadata",
                          "The .git entry is not a plain directory; git was "
                          "not run. " + GIT_ERROR_NOTE, False)
    return run_git(root)


# Asset Identity, Documentation Metadata and Reported Metrics

def capture_asset(entry):
    """Hash one preflighted asset and check it against its expected identity.

    The file is re-inspected without following links before and after the
    streamed hash; a size or modification-time change while hashing fails.
    """
    role = entry["role"]
    path = asset_path(entry["relative_path"])
    expected_size = entry["expected_size_bytes"]
    expected_sha = entry["expected_sha256"].lower()
    try:
        before = os.lstat(path)
        if before.st_size != expected_size:
            raise BaselineError("%s size differs from the expected identity "
                                "(%d bytes observed, %d expected): %s"
                                % (role, before.st_size, expected_size, path))
        digest, count = sha256_stream(path)
        after = os.lstat(path)
    except OSError as error:
        raise BaselineError("%s could not be read (%s): %s" % (
            role, type(error).__name__, path))
    if (guard.is_link_like(after) or not stat.S_ISREG(after.st_mode)
            or (before.st_size, before.st_mtime_ns)
            != (after.st_size, after.st_mtime_ns)
            or count != after.st_size):
        raise BaselineError("%s changed while it was being hashed: %s"
                            % (role, path))
    if digest != expected_sha:
        raise BaselineError("%s SHA-256 differs from the expected identity: "
                            "%s" % (role, path))
    return {"role": role, "category": ASSET_ROLES[role],
            "content_handling": CONTENT_HANDLING.get(role, HASH_ONLY),
            "relative_path": entry["relative_path"], "path": path,
            "exists": True, "type": "regular_file",
            "size_bytes": after.st_size,
            "modified_utc": utc_from_ns(after.st_mtime_ns),
            "modified_ns": after.st_mtime_ns, "sha256": digest,
            "expected_size_bytes": expected_size,
            "expected_sha256": expected_sha,
            "matches_expected_identity": True}


def documentation_metadata(path, expected):
    """Extract only the title and 'Last updated' date and check both.

    The title is the first level-one heading; the date is the first ISO date
    after a 'Last updated' label. Lines are read one at a time and dropped.
    Only the two expected values are returned, and a mismatch is reported
    without the observed text.
    """
    title = None
    updated = None
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            for line in handle:
                if title is None:
                    match = TITLE_LINE.match(line.rstrip("\r\n"))
                    if match:
                        title = match.group(1)
                if updated is None:
                    match = LAST_UPDATED.search(line)
                    if match:
                        updated = match.group(1)
                if title is not None and updated is not None:
                    break
    except UnicodeDecodeError:
        raise BaselineError("documentation is not valid UTF-8: %s" % path)
    if title is None:
        raise BaselineError("documentation has no level-one title: %s" % path)
    if title != expected["expected_title"]:
        raise BaselineError("documentation title does not match the expected "
                            "title: %s" % path)
    if updated is None:
        raise BaselineError("documentation has no 'Last updated' date: %s"
                            % path)
    if updated != expected["expected_last_updated"]:
        raise BaselineError("documentation 'Last updated' date does not match "
                            "the expected date: %s" % path)
    return {"role": DOCUMENTATION_ROLE, "path": path, "title": title,
            "last_updated": updated, "metadata_status": "matched_expected",
            "extraction": ("first level-one heading and first 'Last "
                           "updated' date only; no other documentation "
                           "text is recorded")}


def aggregate_summary(value, name):
    """Return a reported metric field reduced to finite top-level numbers.

    A number is kept as is. For an object only top-level finite numbers
    under plain metric names are kept; every other entry (lists, nested
    objects, text, non-finite numbers) is counted, never copied. An object
    too large to be an aggregate is refused.
    """
    if is_number(value):
        return {"kind": "number", "value": value}
    if not isinstance(value, dict):
        raise BaselineError("canonical metrics field %s is neither a number "
                            "nor an object" % name)
    if len(value) > MAX_AGGREGATE_ENTRIES:
        raise BaselineError("canonical metrics field %s has %d entries, more "
                            "than the %d an aggregate object may hold"
                            % (name, len(value), MAX_AGGREGATE_ENTRIES))
    kept = {}
    omitted = 0
    for key in sorted(value):
        if METRIC_KEY.match(key) and is_number(value[key]):
            kept[key] = value[key]
        else:
            omitted += 1
    return {"kind": "object", "values": kept, "omitted_entry_count": omitted}


def confirm_reported(test, expected):
    """Confirm each supplied reported test value among the test aggregates.

    Labels are descriptive; a value is confirmed when an equal top-level
    number exists in the reported test object, and the matching field names
    are recorded.
    """
    if test["kind"] == "number":
        values = {"": test["value"]}
    else:
        values = test["values"]
    confirmed = {}
    for label in sorted(expected):
        fields = sorted("test" + ("." + key if key else "")
                        for key, value in values.items()
                        if value == expected[label])
        if not fields:
            raise BaselineError("the reported test value for %s was not "
                                "found among the top-level test numbers"
                                % label)
        confirmed[label] = {"value": expected[label],
                            "matched_fields": fields}
    return confirmed


def reported_metrics(path, expected):
    """Parse only the required aggregate fields of the canonical metrics.

    Requires task=diagnosis, data_source=omop, no_kg=false (a boolean), a
    50-entry history list and the expected best_epoch. Messages never echo
    observed values other than counts.
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except UnicodeDecodeError:
        raise BaselineError("canonical metrics.json is not valid UTF-8: %s"
                            % path)
    except ValueError:
        raise BaselineError("canonical metrics.json is truncated or not "
                            "valid JSON: %s" % path)
    if not isinstance(data, dict):
        raise BaselineError("canonical metrics.json is not a JSON object")
    missing = [name for name in METRIC_FIELDS if name not in data]
    if missing:
        raise BaselineError("canonical metrics.json lacks required fields: %s"
                            % ", ".join(missing))
    for name, required in CANONICAL_IDENTITY:
        value = data[name]
        if type(value) is not type(required) or value != required:
            raise BaselineError("canonical metrics.json %s is not %s; the run "
                                "identity does not match %s"
                                % (name, json.dumps(required), CANONICAL_RUN))
    history = data["history"]
    if not isinstance(history, list):
        raise BaselineError("canonical metrics.json history is not a list")
    if len(history) != HISTORY_LENGTH:
        raise BaselineError("canonical metrics.json history has %d entries; "
                            "%d are required" % (len(history),
                                                 HISTORY_LENGTH))
    best_epoch = data["best_epoch"]
    if not is_count(best_epoch) or best_epoch != expected[
            "expected_best_epoch"]:
        raise BaselineError("canonical metrics.json best_epoch does not match "
                            "the expected value %d"
                            % expected["expected_best_epoch"])
    test = aggregate_summary(data["test"], "test")
    return {
        "identity": {"task": data["task"],
                     "data_source": data["data_source"],
                     "no_kg": data["no_kg"], "best_epoch": best_epoch,
                     "history_length": len(history)},
        "best_val": aggregate_summary(data["best_val"], "best_val"),
        "test": test,
        "confirmed_reported_test_values": confirm_reported(
            test, expected["reported_test_values"]),
    }


# Record, Write and Report

def build_record(config, config_path, assets, documentation, metrics, git):
    """Assemble the deterministic baseline record.

    No capture time is stored, so identical inputs give identical bytes.
    Reported values sit under canonical_run with inherited/reported labels;
    downstream_executed_results records that nothing was executed.
    """
    script = os.path.abspath(__file__)
    identity = metrics["identity"]
    upstream = {"root": UPSTREAM_ROOT}
    upstream.update(git)
    upstream["content_baseline"] = ("SHA-256 and size of every asset below, "
                                    "checked against config expectations")
    return {
        "record_type": "inherited_gnn_baseline",
        "schema_version": SCHEMA_VERSION,
        "step": "1",
        "capture_tool": {"path": script, "version": VERSION,
                         "sha256": sha256_stream(script)[0]},
        "configuration": {"path": os.path.abspath(config_path),
                          "sha256": sha256_stream(config_path)[0]},
        "determinism": ("No capture timestamp is stored; identical inputs "
                        "produce a byte-identical record."),
        "downstream_execution": {
            "upstream_code_imported_or_executed": False,
            "training_run": False, "inference_run": False,
            "attribution_run": False, "environment_imported": False,
            "assets_loaded_as_python_objects": False},
        "upstream": upstream,
        "documentation": documentation,
        "canonical_run": {
            "name": CANONICAL_RUN,
            "metrics_role": METRICS_ROLE,
            "provenance": PROVENANCE,
            "execution_status": EXECUTION_STATUS,
            "reported_identity": identity,
            "kg_determination": {
                "conclusion": "omop_plus_kg",
                "basis": {"data_source": identity["data_source"],
                          "no_kg": identity["no_kg"]},
                "rule": ("%s is identified as the OMOP + KG run only because "
                         "its metrics.json reports data_source=omop and "
                         "no_kg=false. Nothing further about graph "
                         "construction or KG content is inferred."
                         % CANONICAL_RUN)},
            "reported_metrics": {
                "provenance": PROVENANCE,
                "execution_status": EXECUTION_STATUS,
                "best_epoch": identity["best_epoch"],
                "history_length": identity["history_length"],
                "best_val": metrics["best_val"],
                "test": metrics["test"],
                "confirmed_reported_test_values":
                    metrics["confirmed_reported_test_values"]},
        },
        "downstream_executed_results": {
            "status": "none",
            "note": ("Step 1 executes no training, evaluation, inference or "
                     "attribution. No newly executed result exists; every "
                     "metric above is inherited and reported.")},
        "assets": assets,
        "totals": {"assets": len(assets),
                   "bytes": sum(asset["size_bytes"] for asset in assets)},
        "limitations": list(config.get("limitations", [])),
    }


def serialise(record):
    """Return the record as canonical UTF-8 JSON bytes."""
    text = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def existing_outcome(output, payload):
    """Return 'absent', 'identical' or 'conflict' for the planned output."""
    if not os.path.lexists(output):
        return "absent"
    if os.lstat(output).st_size != len(payload):
        return "conflict"
    with open(output, "rb") as handle:
        return "identical" if handle.read() == payload else "conflict"


def write_record(output, payload):
    """Write the record atomically without replacing any existing file.

    Bytes go to an exclusively created .partial file that is flushed and
    fsynced, then renamed into place. os.rename on Windows refuses an
    existing destination, so a record that appeared meanwhile is never
    replaced. The partial file this call created is removed on any failure.
    """
    partial = output + PARTIAL
    created = False
    try:
        with open(partial, "xb") as handle:
            created = True
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(partial, output)
    except BaseException:
        if created and os.path.lexists(partial):
            os.remove(partial)
        raise


def report(record, output, outcome):
    """Print roles, sizes, hashes and statuses only, never file content."""
    for asset in record["assets"]:
        print("%-28s %12d  %s" % (asset["role"], asset["size_bytes"],
                                  asset["sha256"]))
    upstream = record["upstream"]
    print("")
    print("Upstream root    : %s" % upstream["root"])
    print("Revision status  : %s" % upstream["revision_status"])
    print("Documentation    : title and last-updated date match expectations")
    print("Canonical run    : %s, reported OMOP + KG (data_source=omop, "
          "no_kg=false)" % CANONICAL_RUN)
    print("Reported metrics : %s, %s" % (PROVENANCE, EXECUTION_STATUS))
    print("Baseline record  : %s" % output)
    print("Record state     : %s" % (
        "written" if outcome == "absent"
        else "unchanged (an identical record already exists)"))
    print("RESULT: COMPLETE")


def build_parser():
    """Build the command-line parser for the baseline capture tool."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", help="configuration file in the "
                        "repository config directory")
    parser.add_argument("--output", required=True,
                        help="explicit .json record path directly inside %s"
                        % BASELINE_ROOT)
    return parser


def run(args):
    """Capture the baseline and return a process exit code.

    Every check that can fail (fixed roots, configuration, output, every
    source path, identities, documentation and metrics) runs before the
    baseline directory or any output file is created.
    """
    problem = fixed_policy_problem()
    if problem:
        raise BaselineError(problem)
    config_path = args.config or os.path.join(CONFIG_DIR, CONFIG_NAME)
    problem = config_path_problem(config_path)
    if problem:
        raise BaselineError(problem)
    config = load_config(config_path)
    problem = (config_problem(config) or output_problem(args.output)
               or sources_problem(config))
    if problem:
        raise BaselineError(problem)

    git = git_state(UPSTREAM_ROOT)
    assets = [capture_asset(entry) for entry in config["assets"]]
    by_role = {asset["role"]: asset for asset in assets}
    document = by_role[DOCUMENTATION_ROLE]
    documentation = documentation_metadata(document["path"],
                                           config["documentation"])
    documentation.update({"size_bytes": document["size_bytes"],
                          "modified_utc": document["modified_utc"],
                          "sha256": document["sha256"]})
    metrics = reported_metrics(by_role[METRICS_ROLE]["path"],
                               config["metrics"])
    record = build_record(config, config_path, assets, documentation,
                          metrics, git)
    payload = serialise(record)

    outcome = existing_outcome(args.output, payload)
    if outcome == "conflict":
        raise BaselineError("a baseline with different content already exists "
                            "at %s and was left unchanged; choose a new "
                            "--output name for a new baseline version"
                            % args.output)
    if outcome == "absent":
        problem = output_problem(args.output)
        if problem:
            raise BaselineError(problem)
        os.makedirs(BASELINE_ROOT, exist_ok=True)
        write_record(args.output, payload)
    report(record, args.output, outcome)
    return 0


def main(argv=None):
    """Run the capture tool, sanitising any failure."""
    try:
        return run(build_parser().parse_args(argv))
    except BaselineError as error:
        print("FAILED: %s" % error)
        return 1
    except KeyboardInterrupt:
        print("FAILED: interrupted before completion")
        return 1
    except Exception as error:  # sanitised: text may quote file content
        print("FAILED: %s" % sanitise_error(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
