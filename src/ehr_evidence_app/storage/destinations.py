#!/usr/bin/env python3
"""Synthetic verification of the Step 0.2 destination configuration.

Standard library only. The verifier first confirms, read-only, that every
configured destination resolves inside its fixed root (the private
workspace, or the fixed runtime root for the approved runtime/cache
categories only), outside the Git-tracked source repository, and that
the environment matches the configuration. Nothing is written until that gate
passes: on failure it reports and exits without writing markers, temporary
files or even its own report. After the gate it writes and reads back synthetic
marker files, writes temporary files explicitly into the configured temp
directory and exercises the bytecode-cache destination.

The gate also applies ehr_evidence_app.storage.policy: the configuration
must keep the fixed anchor and private root and define the complete mandatory
directory and variable set with its fixed bindings, the running interpreter's
bytecode prefix must be the configured one, and every known destination
(directories, file-valued variables, markers, the report and the bytecode
probe paths) is checked lexically and on disk without following links.
Python's default tempfile directory selection is deliberately not invoked,
because its fallback search writes test files outside the configured root.

Every result is labelled with its evidence class:

  probe        an operation was actually performed against the destination
               (file written and read back, or interpreter behaviour observed);
  declaration  the environment variable holds the configured value and the
               target directory is writable, but the owning tool was NOT run,
               so its routing behaviour remains unverified.

Usage:
    python -m ehr_evidence_app.storage.destinations [--config PATH] [--no-report]
"""

from __future__ import annotations

import argparse
import datetime
import importlib
import importlib.util
import json
import ntpath
import os
import re
import shutil
import stat
import sys
import tempfile

from ehr_evidence_app.storage import runtime as storage
from ehr_evidence_app.storage import policy as guard

REPO_ROOT = guard.REPO_ROOT
DEFAULT_CONFIG = os.path.join(REPO_ROOT, "config", "paths.json")
PLACEHOLDER = re.compile(r"\$\{(dir|dir_posix):([A-Za-z0-9_]+)\}")
PROBE_MODULE = "ehr_step02_pycache_probe"
REPORT_NAME = "destination-report.json"
GENERATED_IGNORE_NAMES = (
    "/protected-data/",
    "/cache/",
    "/environments/",
    "/temp/",
    "/outputs/",
    "/models/",
    "/clearml/",
)


def load_config(path):
    """Read the JSON destination configuration from disk."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def resolve_directories(config):
    """Resolve every configured directory key to an absolute path.

    Each key is joined to the fixed root its validated "root" field names;
    an invalid root field or relative path raises ValueError.
    """
    return {
        key: guard.configured_directory_path(key, entry)[0]
        for key, entry in config["directories"].items()
    }


def resolve_roots(config):
    """Return the fixed root (private workspace or runtime) of every key."""
    return {
        key: guard.configured_directory_path(key, entry)[1]
        for key, entry in config["directories"].items()
    }


def expand_template(template, directories):
    """Expand ${dir:key} and ${dir_posix:key} placeholders in a template."""

    def substitute(match):
        """Return the resolved path for a single placeholder match."""
        value = directories[match.group(2)]
        if match.group(1) == "dir_posix":
            value = value.replace("\\", "/")
        return value

    return PLACEHOLDER.sub(substitute, template)


def expected_env_value(entry, directories):
    """Resolve one configured environment entry to its expected value."""
    if "directory" in entry:
        return directories[entry["directory"]]
    if "file" in entry:
        return os.path.join(
            directories[entry["file"]["directory"]], entry["file"]["name"]
        )
    if "literal" in entry:
        return entry["literal"]
    if "template" in entry:
        return expand_template(entry["template"], directories)
    raise KeyError("environment entry %r defines no value" % entry.get("name"))


def is_within(path, root):
    """Return True when path equals root or lies beneath it."""
    path_key = os.path.normcase(os.path.abspath(path))
    root_key = os.path.normcase(os.path.abspath(root))
    return path_key == root_key or path_key.startswith(root_key + os.sep)


def add_check(checks, name, evidence, passed, detail):
    """Append one result to the check list and return its pass flag."""
    checks.append(
        {
            "name": name,
            "evidence": evidence,
            "status": "pass" if passed else "FAIL",
            "detail": detail,
        }
    )
    return passed


def write_marker(directory, marker_name, stamp):
    """Write a synthetic marker file and read it back for comparison."""
    path = os.path.join(directory, marker_name)
    payload = json.dumps(
        {
            "marker": "ehr-evidence-app step 0.2",
            "directory": directory,
            "written_utc": stamp,
        },
        sort_keys=True,
    )
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")
    with open(path, "r", encoding="utf-8") as handle:
        read_back = handle.read().strip()
    return path, read_back == payload


def add_containment_check(checks, name, path, kind, root):
    """Check one known destination against the fixed destination policy.

    The path must be a plain drive path inside the fixed root named for its
    key (root is guard.PRIVATE_ROOT or guard.RUNTIME_ROOT) and outside the
    source repository; existing ancestors and the leaf are inspected without
    following links. Missing leaves are accepted.
    """
    problem = guard.destination_problem(path, kind, strict=False, root=root)
    if problem is None:
        detail = (
            "%s (inside %s, outside the source repository; no reparse point "
            "or .git in existing components)" % (path, root)
        )
    else:
        detail = "fails containment: %s" % problem
    add_check(checks, name, "probe", problem is None, detail)


def check_fixed_policy(checks, config):
    """Check that the configuration keeps the fixed anchor and both roots.

    Configuration edits may choose destinations inside the fixed roots but
    cannot move or widen the anchor, the private root or the runtime root,
    and the marker file name must be a single safe name.
    """
    problem = guard.fixed_policy_problem()
    add_check(
        checks,
        "policy:fixed-layout",
        "probe",
        problem is None,
        problem
        or "%s inside %s, outside %s"
        % (guard.PRIVATE_ROOT, guard.POLICY_ANCHOR, guard.REPO_ROOT),
    )
    root = config.get("workspace_root")
    same_root = isinstance(root, str) and (
        ntpath.normcase(root) == ntpath.normcase(guard.PRIVATE_ROOT)
    )
    add_check(
        checks,
        "policy:private-root",
        "probe",
        same_root,
        "workspace_root %s %s the fixed private root %s"
        % (root, "is" if same_root else "is NOT", guard.PRIVATE_ROOT),
    )
    problem = guard.runtime_root_setting_problem(config.get("runtime_root"))
    add_check(
        checks,
        "policy:runtime-root",
        "probe",
        problem is None,
        problem or "runtime_root is the fixed runtime root %s"
        % guard.RUNTIME_ROOT,
    )
    policy = config.get("policy") or {}
    prefix = policy.get("required_root_prefix")
    same_anchor = isinstance(prefix, str) and (
        ntpath.normcase(prefix.rstrip("\\"))
        == ntpath.normcase(guard.POLICY_ANCHOR)
    )
    add_check(
        checks,
        "policy:anchor",
        "probe",
        same_anchor,
        "required_root_prefix %s %s the fixed anchor %s"
        % (prefix, "is" if same_anchor else "is NOT", guard.POLICY_ANCHOR),
    )
    problem = guard.name_problem(policy.get("marker_file_name"))
    add_check(
        checks,
        "policy:marker-name",
        "probe",
        problem is None,
        problem or "marker file name is a single safe name",
    )


def check_definitions(checks, config):
    """Check directory and environment definitions before resolving them.

    The complete mandatory set must be present: every essential directory
    key and every mandatory variable exactly once, so omitting an entry
    cannot skip its check. Directory paths must be safe private-root-relative
    paths. Destination variables must be the directory or file entry of
    their fixed key; PYTEST_ADDOPTS and CLEARML_OFFLINE_MODE must equal their
    canonical values; no other entry may be a literal or template.
    """
    directories = config["directories"]
    environment = config["environment"]
    problems = guard.environment_set_problems(directories, environment)
    for subject, problem in problems:
        add_check(checks, "envset:%s" % subject, "probe", False, problem)
    if not problems:
        add_check(
            checks,
            "envset:complete",
            "probe",
            True,
            "all %d mandatory variables and %d essential directory keys "
            "are defined"
            % (len(guard.REQUIRED_VARIABLES),
               len(guard.REQUIRED_DIRECTORY_KEYS)),
        )
    for key in sorted(directories):
        entry = directories[key]
        problem = guard.directory_root_problem(key, entry)
        if problem is None:
            problem = guard.relative_problem(entry.get("path"),
                                             guard.directory_root(entry))
        if problem:
            add_check(checks, "definition:%s" % key, "probe", False, problem)
    seen = set()
    for entry in config["environment"]:
        problem = guard.environment_entry_problem(entry, directories)
        name = entry.get("name") if isinstance(entry, dict) else None
        if problem is None and name in seen:
            problem = "%s is defined more than once" % name
        if isinstance(name, str):
            seen.add(name)
        add_check(
            checks,
            "envdef:%s" % name,
            "probe",
            problem is None,
            problem or "validated definition",
        )


def check_containment(checks, config, directories):
    """Check every known destination against the fixed destination policy.

    Read-only. This runs as a gate: nothing is written anywhere until it and
    the environment and source-separation checks have all passed. Covers
    every directory (including ones not pre-created), file-valued variables,
    each marker file, the report file and the bytecode probe source and
    cache paths, each against the fixed root of its directory key. Existing
    leaves must be of the expected kind.
    """
    roots = resolve_roots(config)
    for key in sorted(directories):
        add_containment_check(
            checks, "containment:%s" % key, directories[key], "directory",
            roots[key]
        )

    for entry in config["environment"]:
        if "file" not in entry:
            continue
        add_containment_check(
            checks,
            "containment:file:%s" % entry["name"],
            expected_env_value(entry, directories),
            "file",
            roots[entry["file"]["directory"]],
        )

    marker_name = config["policy"]["marker_file_name"]
    for key in sorted(directories):
        if config["directories"][key].get("create", True):
            add_containment_check(
                checks,
                "containment:marker:%s" % key,
                os.path.join(directories[key], marker_name),
                "file",
                roots[key],
            )
    add_containment_check(
        checks,
        "containment:report",
        os.path.join(directories["verification"], REPORT_NAME),
        "file",
        roots["verification"],
    )
    source = os.path.join(directories["temp"], PROBE_MODULE + ".py")
    add_containment_check(checks, "containment:probe-source", source, "file",
                          roots["temp"])
    if sys.pycache_prefix is not None:
        add_containment_check(
            checks,
            "containment:probe-bytecode",
            importlib.util.cache_from_source(source),
            "file",
            roots["pycache"],
        )


def check_runtime_volume(checks, config):
    """Check the runtime root's volume and presence before any write.

    Only when a directory uses the runtime root: its drive must be a fixed
    NTFS volume and the root itself must already exist as a plain directory
    (ehr_evidence_app.storage.runtime creates it with restrictive ACLs), so
    no marker or probe can create it without those controls.
    """
    if guard.RUNTIME_ROOT not in resolve_roots(config).values():
        return
    problem = guard.runtime_volume_problem()
    add_check(checks, "runtime:volume", "probe", problem is None,
              problem or "%s is on a fixed NTFS volume" % guard.RUNTIME_ROOT)
    try:
        info = os.lstat(guard.RUNTIME_ROOT)
        exists = stat.S_ISDIR(info.st_mode) and not guard.is_link_like(info)
    except OSError:
        exists = False
    add_check(
        checks, "runtime:root-exists", "probe", exists,
        "%s %s" % (guard.RUNTIME_ROOT, "is a plain directory" if exists else
                   "is missing or not a plain directory; run "
                   "ehr_evidence_app.storage.runtime --create-root first"))


def check_runtime_gate(checks, config):
    """Re-run the read-only runtime gate before any write.

    Only when a directory uses the runtime root: the pinned volume identity
    digest, disk/volume identity and health, fixed NTFS media, free space,
    path and reparse checks and the restrictive ACLs of both runtime levels
    must all pass now, so identity or ACL drift since the preflight stops
    the verifier before any marker, probe or report is written. No raw
    identifier is reported.
    """
    if guard.RUNTIME_ROOT not in resolve_roots(config).values():
        return
    try:
        results = storage.runtime_gate_checks()
    except storage.PreflightError as error:
        add_check(checks, "runtime:gate", "probe", False,
                  "runtime gate could not run: %s" % error)
        return
    for name, passed, detail in results:
        add_check(checks, "runtime:gate:%s" % name, "probe", passed, detail)


def check_runtime_prefix(checks, directories):
    """Check the interpreter's bytecode prefix before anything is written.

    PYTHONPYCACHEPREFIX is read only at interpreter start, so a matching
    environment variable does not prove the running interpreter uses it. A
    missing prefix, or one other than the configured pycache directory,
    fails the gate before any marker or probe write.
    """
    expected = directories["pycache"]
    prefix = sys.pycache_prefix
    matched = prefix is not None and (
        ntpath.normcase(os.path.abspath(prefix)) == ntpath.normcase(expected)
    )
    if prefix is None:
        detail = (
            "sys.pycache_prefix is unset; run scripts/setup-destinations.ps1 "
            "before starting Python"
        )
    elif matched:
        detail = prefix
    else:
        detail = "sys.pycache_prefix is %s (expected %s)" % (prefix, expected)
    add_check(checks, "runtime:pycache-prefix", "probe", matched, detail)


def check_markers(checks, config, directories, stamp):
    """Write and re-read a synthetic marker in every created directory."""
    marker_name = config["policy"]["marker_file_name"]

    for key in sorted(directories):
        path = directories[key]
        entry = config["directories"][key]

        if not entry.get("create", True):
            add_check(
                checks,
                "marker-skipped:%s" % key,
                "declaration",
                True,
                "%s is configured but deliberately not pre-created, so no "
                "write probe was performed" % path,
            )
            continue

        try:
            marker_path, matched = write_marker(path, marker_name, stamp)
            add_check(
                checks,
                "marker:%s" % key,
                "probe",
                matched,
                "wrote and re-read %s" % marker_path,
            )
        except OSError as error:
            add_check(
                checks,
                "marker:%s" % key,
                "probe",
                False,
                "could not write into %s: %s" % (path, error),
            )


def check_environment(checks, config, directories):
    """Compare every configured environment variable with its expected value.

    The comparison itself is only a value match. The evidence class says
    whether the destination behind the variable is exercised elsewhere in this
    report (probe) or merely configured for a tool that was not run
    (declaration).
    """
    for entry in config["environment"]:
        name = entry["name"]
        expected = expected_env_value(entry, directories)
        actual = os.environ.get(name)
        if actual is None:
            detail = "%s is not set; run scripts/setup-destinations.ps1" % name
            add_check(checks, "env:%s" % name, entry["evidence"], False, detail)
            continue
        matched = os.path.normcase(actual) == os.path.normcase(expected)
        detail = actual if matched else "%s (expected %s)" % (actual, expected)
        if entry["evidence"] == "declaration":
            detail = "%s [consumer not run]" % detail
        add_check(checks, "env:%s" % name, entry["evidence"], matched, detail)


def probe_tempfile(checks, temp_root):
    """Write temporary files explicitly into the validated temp root.

    Fails closed: the temp root must already be a plain directory, and every
    tempfile call passes it with dir=, so Python's default directory search
    (tempfile.gettempdir, which writes test files into fallback candidates
    such as C:\\TEMP when the configured one is unusable) is never invoked.
    This is evidence of explicit routing into the directory named by
    TEMP/TMP/TMPDIR, not of Python's default tempfile selection. Cleanup is
    guarded: anything that resolves outside the temp root is left in place
    and reported rather than deleted.
    """
    try:
        info = os.lstat(temp_root)
        usable = stat.S_ISDIR(info.st_mode) and not guard.is_link_like(info)
    except OSError:
        usable = False
    if not usable:
        add_check(
            checks,
            "probe:tempfile.explicit-dir",
            "probe",
            False,
            "%s is missing or not a plain directory; no temporary file was "
            "created and no fallback directory was tried" % temp_root,
        )
        return
    add_check(
        checks,
        "probe:tempfile.explicit-dir",
        "probe",
        True,
        "%s is a plain directory; the probes below pass it explicitly "
        "(default tempfile selection is not exercised)" % temp_root,
    )

    handle = tempfile.NamedTemporaryFile(
        prefix="ehr-step02-", suffix=".tmp", dir=temp_root, delete=False
    )
    try:
        handle.write(b"ehr-evidence-app step 0.2 temp probe\n")
    finally:
        handle.close()

    temp_file = os.path.abspath(handle.name)
    contained = is_within(temp_file, temp_root)
    with open(temp_file, "rb") as reader:
        content = reader.read()
    if contained:
        os.remove(temp_file)
        detail = "%s (%d bytes, removed after the probe)" % (
            temp_file,
            len(content),
        )
    else:
        detail = "%s is outside %s; left in place, not deleted" % (
            temp_file,
            temp_root,
        )
    add_check(
        checks,
        "probe:tempfile.NamedTemporaryFile",
        "probe",
        contained and content.endswith(b"probe\n"),
        detail,
    )

    temp_dir = os.path.abspath(
        tempfile.mkdtemp(prefix="ehr-step02-", dir=temp_root)
    )
    contained = is_within(temp_dir, temp_root)
    if contained:
        shutil.rmtree(temp_dir, ignore_errors=True)
        detail = "%s (removed after the probe)" % temp_dir
    else:
        detail = "%s is outside %s; left in place, recursive cleanup was " \
            "not attempted" % (temp_dir, temp_root)
    add_check(checks, "probe:tempfile.mkdtemp", "probe", contained, detail)


def probe_bytecode_cache(checks, probe_dir, pycache_root):
    """Import a generated module and locate the .pyc it produces."""
    if sys.pycache_prefix is None:
        add_check(
            checks,
            "probe:bytecode-cache",
            "probe",
            False,
            "sys.pycache_prefix is unset; PYTHONPYCACHEPREFIX must be set "
            "before the interpreter starts",
        )
        return

    source = os.path.join(probe_dir, PROBE_MODULE + ".py")
    body = '"""Generated Step 0.2 bytecode-cache probe."""\n\nVALUE = 42\n'
    with open(source, "w", encoding="utf-8") as writer:
        writer.write(body)

    cached = importlib.util.cache_from_source(source)
    if os.path.exists(cached):
        os.remove(cached)

    sys.path.insert(0, probe_dir)
    try:
        sys.modules.pop(PROBE_MODULE, None)
        importlib.invalidate_caches()
        module = importlib.import_module(PROBE_MODULE)
        value = getattr(module, "VALUE", None)
    finally:
        sys.path.remove(probe_dir)
        sys.modules.pop(PROBE_MODULE, None)

    sibling = os.path.join(probe_dir, "__pycache__")
    routed = os.path.exists(cached) and is_within(cached, pycache_root)
    detail = "imported %s; bytecode written to %s" % (source, cached)
    if not routed:
        detail = "%s (expected bytecode under %s)" % (detail, pycache_root)
    if os.path.exists(sibling):
        detail = "%s; unexpected source-side cache %s" % (detail, sibling)
    add_check(
        checks,
        "probe:bytecode-cache",
        "probe",
        value == 42 and routed and not os.path.exists(sibling),
        detail,
    )


def check_repository_separation(checks, config):
    """Confirm generated content stays outside the Git-tracked source tree."""
    repo_root = config["source_repository"]
    add_check(
        checks,
        "repo:config-matches-location",
        "probe",
        os.path.normcase(REPO_ROOT) == os.path.normcase(repo_root),
        "verifier runs from %s; configuration names %s" % (REPO_ROOT, repo_root),
    )
    add_check(
        checks,
        "repo:workspace-outside-repository",
        "probe",
        not is_within(config["workspace_root"], repo_root),
        "%s is not inside %s" % (config["workspace_root"], repo_root),
    )

    gitignore = os.path.join(repo_root, ".gitignore")
    try:
        with open(gitignore, "r", encoding="utf-8") as handle:
            lines = {line.strip() for line in handle}
    except OSError as error:
        add_check(checks, "repo:gitignore", "probe", False, str(error))
        return

    missing = [name for name in GENERATED_IGNORE_NAMES if name not in lines]
    if missing:
        detail = "missing .gitignore patterns: %s" % ", ".join(missing)
    else:
        detail = "all generated directory names are excluded by .gitignore"
    add_check(checks, "repo:gitignore", "probe", not missing, detail)


def write_report(report_dir, report):
    """Write the JSON verification report with a stable file name."""
    path = os.path.join(report_dir, REPORT_NAME)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=False)
        handle.write("\n")
    return path


def build_parser():
    """Build the command-line parser for the verifier."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG, help="path to config/paths.json"
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="print results without writing the JSON report",
    )
    return parser


def print_checks(checks):
    """Print one line per check result."""
    for check in checks:
        print(
            "[%-4s] %-11s %-42s %s"
            % (
                check["status"],
                check["evidence"],
                check["name"],
                check["detail"],
            )
        )


def gate_checks(config):
    """Run the read-only gate and return its checks; nothing is written.

    Fixed-policy and definition checks (including the complete mandatory
    set and every directory's root field) run first; when either fails the
    configuration is not resolved further, so a malformed, missing or
    relaxed entry cannot reach any later step. The environment, the running
    interpreter's bytecode prefix, the runtime volume, the current runtime
    gate (identity digest, health and ACLs), containment and
    source separation follow.
    """
    checks = []
    check_fixed_policy(checks, config)
    check_definitions(checks, config)
    if any(check["status"] != "pass" for check in checks):
        return checks
    directories = resolve_directories(config)
    check_environment(checks, config, directories)
    check_runtime_prefix(checks, directories)
    check_runtime_volume(checks, config)
    check_runtime_gate(checks, config)
    check_containment(checks, config, directories)
    check_repository_separation(checks, config)
    return checks


def main(argv=None):
    """Run every destination check and return a process exit code."""
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds"
    )

    # Gate: read-only checks. Nothing at all is written until these pass,
    # including the report itself.
    checks = gate_checks(config)

    blocking = [check for check in checks if check["status"] != "pass"]
    if blocking:
        print_checks(checks)
        print("")
        print(
            "ABORTED before any write: %d policy, definition, environment, "
            "containment or source separation check(s) failed."
            % len(blocking)
        )
        print(
            "No markers, temporary files or report were written. Fix the "
            "configuration or run scripts/setup-destinations.ps1 first."
        )
        return 1

    directories = resolve_directories(config)
    check_markers(checks, config, directories, stamp)
    try:
        probe_tempfile(checks, directories["temp"])
    except OSError as error:
        add_check(checks, "probe:tempfile", "probe", False, str(error))
    try:
        probe_bytecode_cache(
            checks, directories["temp"], directories["pycache"]
        )
    except (OSError, ImportError) as error:
        add_check(checks, "probe:bytecode-cache", "probe", False, str(error))

    failures = [check for check in checks if check["status"] != "pass"]
    report = {
        "step": "0.2",
        "generated_utc": stamp,
        "config": os.path.abspath(args.config),
        "interpreter": sys.executable,
        "workspace_root": config["workspace_root"],
        "runtime_root": config["runtime_root"],
        "evidence_note": (
            "probe = the destination was actually written to, read back or "
            "observed; declaration = the configured value is present and the "
            "directory is writable, but the owning tool was not run and its "
            "routing behaviour is unverified. An env: check is always a value "
            "comparison; its evidence class refers to the destination behind "
            "the variable. The project EHR_* variables have no consumer until "
            "later application steps. TEMP/TMP/TMPDIR probes pass the "
            "configured temp directory explicitly; Python's default tempfile "
            "directory selection is deliberately not exercised."
        ),
        "checks": checks,
        "totals": {
            "checks": len(checks),
            "failures": len(failures),
            "probes": sum(1 for c in checks if c["evidence"] == "probe"),
            "declarations": sum(
                1 for c in checks if c["evidence"] == "declaration"
            ),
        },
        "not_exercised": config["not_exercised"],
        "source_service_exception": config["source_service_exception"],
        "locations_outside_this_configuration": config[
            "locations_outside_this_configuration"
        ],
    }

    print_checks(checks)
    print("")
    print("Interpreter   : %s" % sys.executable)
    print("Workspace root: %s" % config["workspace_root"])
    print("Runtime root  : %s" % config["runtime_root"])
    print(
        "Checks        : %d (%d probes, %d declarations), %d failures"
        % (
            report["totals"]["checks"],
            report["totals"]["probes"],
            report["totals"]["declarations"],
            report["totals"]["failures"],
        )
    )
    print("Not exercised : %s" % ", ".join(config["not_exercised"]))
    print(
        "These destinations are configured. Actual routing is confirmed only "
        "for the Python operations probed above; the listed tools were not "
        "run, so their routing is unverified."
    )

    if not args.no_report:
        try:
            report_path = write_report(directories["verification"], report)
            print("Report        : %s" % report_path)
        except OSError as error:
            print("Report        : not written (%s)" % error)
            return 1

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
