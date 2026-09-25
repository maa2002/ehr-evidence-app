"""Synthetic checks for the Step 0.4 path and source policy.

Covers ehr_evidence_app.storage.policy directly, the read-only gate of
ehr_evidence_app.storage.destinations and the preflight of
scripts/setup-destinations.ps1. No database, source export or patient content
is touched and no psql is launched. Every file, directory, junction or link is
created only under the configured workspace test scratch directory or, for runtime
destinations, under the configured test temp on the runtime root. Windows
only.
"""

import hashlib
import io
import json
import ntpath
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

from ehr_evidence_app.storage import runtime as storage  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402
from ehr_evidence_app.sources import pilot as pilot  # noqa: E402
from ehr_evidence_app.sources import exports as staging  # noqa: E402
from ehr_evidence_app.storage import destinations as verify  # noqa: E402

PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
SETUP_SCRIPT = os.path.join(REPO_ROOT, "scripts", "setup-destinations.ps1")
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
SECRET = "SYNTHETIC-PASSWORD-NEVER-LOGGED"
USER = "synthetic_reader"
CONFIGURED = object()  # use the variant's own pycache directory
TIMEOUTS = {"statement_timeout_ms": 120000, "lock_timeout_ms": 30000,
            "idle_in_transaction_timeout_ms": 300000,
            "connect_timeout_seconds": 10, "subprocess_timeout_seconds": 600}


def configured_test_temp():
    """Return the configured workspace test scratch directory (workspace-only rules)."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def configured_runtime_test_temp():
    """Return the configured test temp directory on the runtime root."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    entry = paths["directories"]["pytest_temp"]
    return guard.configured_directory_path("pytest_temp", entry)[0]


def is_runtime_entry(entry):
    """Return True when a configured directory entry names the runtime root."""
    return entry.get("root") == "runtime"


def remove_link(link):
    """Remove a test-created link or junction only, never its target."""
    if not os.path.lexists(link):
        return
    if os.path.isdir(link):
        os.rmdir(link)
    else:
        os.remove(link)


def make_junction(test, link, target):
    """Create a real directory junction inside scratch, or skip the test."""
    try:
        import _winapi
        _winapi.CreateJunction(target, link)
    except (ImportError, AttributeError, OSError) as error:
        test.skipTest("directory junctions are unavailable here: %s"
                      % type(error).__name__)
    test.addCleanup(remove_link, link)


def make_symlink(test, link, target, directory):
    """Create a real symlink inside scratch, or skip without privilege."""
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as error:
        test.skipTest("symlinks are unavailable here: %s"
                      % type(error).__name__)
    test.addCleanup(remove_link, link)


def tree_snapshot(root):
    """Return every directory and file digest under root, for comparison."""
    state = {}
    for current, directories, files in os.walk(root):
        for name in directories:
            state[os.path.relpath(os.path.join(current, name), root)] = "dir"
        for name in files:
            path = os.path.join(current, name)
            with open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
            state[os.path.relpath(path, root)] = digest
    return state


def write_text(path, text="synthetic\n"):
    """Write a small synthetic text file, creating its parent directory."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


@unittest.skipUnless(os.name == "nt", "Windows is the supported platform")
class ScratchTestCase(unittest.TestCase):
    """Shared scratch space under the configured workspace and runtime test roots."""

    def setUp(self):
        """Create workspace and runtime scratch directories for one test."""
        root = configured_test_temp()
        runtime_root = configured_runtime_test_temp()
        for path in (root, runtime_root):
            if not os.path.isdir(path):
                self.skipTest("configured test scratch is missing; run "
                              "scripts/setup-destinations.ps1 first")
        self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT,
                                        strict=True),
                        "test scratch space must stay in the private root")
        self.assertTrue(guard.is_within(runtime_root, guard.RUNTIME_ROOT,
                                        strict=True),
                        "runtime scratch must stay in the runtime root")
        self.temp_root = root
        self.runtime_temp_root = runtime_root
        self.work = tempfile.mkdtemp(prefix="step-0-4-", dir=root)
        self.addCleanup(self.remove_scratch, self.work, root)
        self.runtime_work = tempfile.mkdtemp(prefix="storage-",
                                             dir=runtime_root)
        self.addCleanup(self.remove_scratch, self.runtime_work, runtime_root)

    def remove_scratch(self, work, temp_root):
        """Delete a scratch tree only if it is strictly under its temp root.

        The absolute scratch path is resolved and checked immediately before
        the recursive removal, so a wrong or relocated path is left alone.
        """
        target = os.path.abspath(work)
        root = os.path.abspath(temp_root)
        if not os.path.normcase(target).startswith(
                os.path.normcase(root) + os.sep):
            self.fail("refusing to remove %s: outside %s" % (target, root))
        shutil.rmtree(target, ignore_errors=True)

    def scratch_config(self):
        """Return paths.json with every directory moved into scratch.

        Workspace entries move into the workspace scratch and runtime entries
        into the runtime scratch, each relative to its own fixed root.
        """
        with open(PATHS_PATH, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        base = os.path.relpath(self.work, guard.PRIVATE_ROOT)
        runtime_base = os.path.relpath(self.runtime_work, guard.RUNTIME_ROOT)
        for entry in config["directories"].values():
            prefix = runtime_base if is_runtime_entry(entry) else base
            entry["path"] = (prefix if entry["path"] == "."
                             else prefix + "\\" + entry["path"])
        return config

    def snapshot(self):
        """Return snapshots of both scratch trees, for comparison."""
        return tree_snapshot(self.work), tree_snapshot(self.runtime_work)

    def write_config(self, config, name):
        """Write a configuration variant into scratch and return its path."""
        path = os.path.join(self.work, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
        return path

    def relative(self, *parts):
        """Return a private-root-relative path beneath this scratch tree."""
        return "\\".join([os.path.relpath(self.work, guard.PRIVATE_ROOT)]
                         + list(parts))

    def runtime_relative(self, *parts):
        """Return a runtime-root-relative path beneath the runtime scratch."""
        base = os.path.relpath(self.runtime_work, guard.RUNTIME_ROOT)
        return "\\".join([base] + list(parts))


def replace_entry(name, value):
    """Return a change that replaces one environment entry's value kind."""

    def change(config):
        """Swap the value fields of the named environment entry."""
        for entry in config["environment"]:
            if entry["name"] == name:
                for kind in guard.VALUE_KINDS:
                    entry.pop(kind, None)
                entry.update(value)

    return change


def remove_entries(*names):
    """Return a change that deletes the named environment entries."""

    def change(config):
        """Drop every environment entry whose name is listed."""
        config["environment"] = [entry for entry in config["environment"]
                                 if entry.get("name") not in names]

    return change


def add_entry(entry):
    """Return a change that appends one more environment entry."""

    def change(config):
        """Append a copy of the entry to the environment list."""
        config["environment"].append(dict(entry))

    return change


def move_directory(key, relative):
    """Return a change that points one directory key at a new path."""

    def change(config):
        """Set the relative path of one configured directory."""
        config["directories"][key]["path"] = relative

    return change


def set_root(key, root):
    """Return a change that sets one directory key's root field."""

    def change(config):
        """Set the root field of one configured directory."""
        config["directories"][key]["root"] = root

    return change


class PathPolicyTests(ScratchTestCase):
    """Direct checks of the shared path and source policy."""

    def test_fixed_policy_constants_and_layout(self):
        """The anchor and private root are fixed and outside the repo."""
        self.assertEqual(guard.POLICY_ANCHOR, "G:\\Industry Dataset")
        self.assertEqual(guard.PRIVATE_ROOT,
                         "G:\\Industry Dataset\\ehr-evidence-workspace")
        self.assertEqual(guard.REQUIRED_SOURCE_DATABASE, "mimiciv_omop")
        self.assertEqual(guard.ALLOWED_SOURCE_HOSTS,
                         ("127.0.0.1", "::1", "localhost"))
        self.assertIsNone(guard.fixed_policy_problem())
        self.assertFalse(guard.is_within(guard.PRIVATE_ROOT, guard.REPO_ROOT))
        self.assertFalse(guard.is_within(guard.REPO_ROOT, guard.PRIVATE_ROOT))

    def test_cli_parsers_expose_no_policy_override(self):
        """No tool offers a flag that could bypass the fixed policy."""
        expected = {
            "staging": {"-h", "--help", "--config", "--source-dir",
                        "--dest-root", "--version-label"},
            "pilot": {"-h", "--help", "--config", "--dest-root",
                      "--version-label", "--cases", "--psql",
                      "--subprocess-timeout", "--verify-only",
                      "--case-set"},
            "verify": {"-h", "--help", "--config", "--no-report"},
        }
        for name, parser in (("staging", staging.build_parser()),
                             ("pilot", pilot.build_parser()),
                             ("verify", verify.build_parser())):
            options = {option for action in parser._actions
                       for option in action.option_strings}
            self.assertEqual(options, expected[name], name)

    def test_approved_destinations_and_missing_leaves_are_accepted(self):
        """Existing and not-yet-created private destinations pass."""
        existing_file = os.path.join(self.work, "report.json")
        write_text(existing_file)
        for path, kind in (
            (self.work, "directory"),
            (self.work.lower(), "directory"),
            (os.path.join(self.work, "a", "b", "c"), "directory"),
            (os.path.join(self.work, "x", "report.json"), "file"),
            (existing_file, "file"),
            (existing_file + ".partial", "file"),
        ):
            self.assertIsNone(guard.destination_problem(path, kind), path)
        self.assertIsNone(guard.destination_problem(
            guard.PRIVATE_ROOT, "directory", strict=False))
        self.assertIn("not inside", guard.destination_problem(
            guard.PRIVATE_ROOT, "directory", strict=True))

    def test_lexical_and_location_mistakes_are_rejected(self):
        """Wrong drives, UNC, relative, ambiguous and sibling paths fail."""
        root = guard.PRIVATE_ROOT
        for path, expected in (
            ("C:\\ehr-outputs", "not inside"),
            ("D:\\Industry Dataset\\ehr-evidence-workspace\\outputs",
             "not inside"),
            ("\\\\server\\share\\outputs", "UNC"),
            ("\\\\?\\" + root + "\\outputs", "UNC"),
            ("\\\\.\\" + root + "\\outputs", "UNC"),
            ("outputs", "absolute"),
            ("W:Industry Dataset\\ehr-evidence-workspace", "absolute"),
            ("\\Industry Dataset\\ehr-evidence-workspace", "absolute"),
            (root.replace("\\", "/") + "/outputs", "forward slashes"),
            (root + "\\..\\ehr-evidence-app", "'..'"),
            (root + "\\.\\outputs", "'..'"),
            (root + "\\\\outputs", "empty component"),
            (root + "\\outputs\\", "empty component"),
            (root + "\\outputs.", "dot or space"),
            (root + "\\outputs ", "dot or space"),
            (root + "\\outputs:secret", "alternate data stream"),
            (root + "\\report.json::$DATA", "alternate data stream"),
            (root + "\\NUL", "reserved device"),
            (root + "\\con.txt", "reserved device"),
            (root + "\\COM1", "reserved device"),
            (root + "\\lpt\u00b9.log", "reserved device"),
            ("W:\\INDUST~1\\ehr-evidence-workspace\\outputs", "8.3"),
            (root + "\\out\x00puts", "control character"),
            (root + "-old\\outputs", "not inside"),
            (root + "2", "not inside"),
            (root[:-1], "not inside"),
            (guard.POLICY_ANCHOR + "\\outputs", "not inside"),
            (guard.REPO_ROOT + "\\outputs", "not inside"),
            (root + "\\outputs\\.git\\hooks", ".git"),
            ("", "empty"),
            (None, "empty"),
        ):
            problem = guard.destination_problem(path, "directory")
            self.assertIsNotNone(problem, repr(path))
            self.assertIn(expected, problem, repr(path))

    def test_git_ancestors_and_wrong_kinds_are_rejected(self):
        """.git entries, file ancestors and kind mismatches are refused."""
        repo = os.path.join(self.work, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        worktree = os.path.join(self.work, "worktree")
        write_text(os.path.join(worktree, ".git"), "gitdir: synthetic\n")
        blocker = os.path.join(self.work, "blocker.txt")
        write_text(blocker)
        folder = os.path.join(self.work, "folder")
        os.makedirs(folder)
        for path, kind, expected in (
            (os.path.join(repo, "out"), "directory", ".git"),
            (os.path.join(repo, "out", "report.json"), "file", ".git"),
            (repo, "directory", ".git"),
            (os.path.join(worktree, "out"), "directory", ".git"),
            (os.path.join(blocker, "child"), "directory", "not a directory"),
            (blocker, "directory", "not a directory"),
            (folder, "file", "not a regular file"),
        ):
            problem = guard.destination_problem(path, kind)
            self.assertIsNotNone(problem, path)
            self.assertIn(expected, problem, path)

    def test_junction_ancestors_and_leaves_are_rejected(self):
        """A real junction is refused as an ancestor or as the leaf."""
        target = os.path.join(self.work, "target")
        write_text(os.path.join(target, "inside.txt"))
        link = os.path.join(self.work, "junction")
        make_junction(self, link, target)
        for path, kind in ((link, "directory"),
                           (os.path.join(link, "child"), "directory"),
                           (os.path.join(link, "inside.txt"), "file")):
            problem = guard.destination_problem(path, kind)
            self.assertIsNotNone(problem, path)
            self.assertIn("reparse point", problem, path)

    def test_symlink_leaves_are_rejected(self):
        """Directory, file and dangling symlinks are refused."""
        target = os.path.join(self.work, "target")
        target_file = os.path.join(target, "real.txt")
        write_text(target_file)
        cases = ((os.path.join(self.work, "dir-link"), target, True,
                  "directory"),
                 (os.path.join(self.work, "file-link.txt"), target_file,
                  False, "file"),
                 (os.path.join(self.work, "dangling.txt"),
                  os.path.join(self.work, "missing.txt"), False, "file"))
        for link, destination, directory, kind in cases:
            make_symlink(self, link, destination, directory)
            problem = guard.destination_problem(link, kind)
            self.assertIsNotNone(problem, link)
            self.assertIn("reparse point", problem, link)

    def test_configured_roots_cannot_relax_the_private_root(self):
        """A configured root may narrow but never widen the private root."""
        for value in ("G:\\Industry Dataset", "W:\\", "C:\\omop_export",
                      guard.PRIVATE_ROOT + "-old",
                      guard.PRIVATE_ROOT + "\\..\\ehr-evidence-app"):
            self.assertIsNotNone(
                guard.configured_root_problem(value, "root"), value)
        for value in (guard.PRIVATE_ROOT,
                      guard.PRIVATE_ROOT + "\\protected-data\\source-pilot"):
            self.assertIsNone(guard.configured_root_problem(value, "root"))

    def test_source_directories_are_limited_to_approved_sources(self):
        """Only the legacy export or a directory under the anchor may be read."""
        for path in ("C:\\omop_export", "c:\\OMOP_EXPORT", self.work,
                     guard.POLICY_ANCHOR + "\\synthetic-source"):
            self.assertIsNone(guard.source_directory_problem(path), path)
        for path in ("D:\\omop_export", "C:\\omop_export\\nested",
                     "C:\\omop_export2", "C:\\omop_export\\",
                     "C:\\omop_export.", "\\\\server\\share\\omop_export",
                     "omop_export", "C:omop_export", guard.POLICY_ANCHOR):
            self.assertIsNotNone(guard.source_directory_problem(path), path)

    def test_psql_executable_checks(self):
        """psql must be an existing local file named psql.exe."""
        psql = os.path.join(self.work, "bin", "psql.exe")
        write_text(psql, "synthetic placeholder; never executed\n")
        other = os.path.join(self.work, "bin", "other.exe")
        write_text(other, "synthetic placeholder; never executed\n")
        self.assertIsNone(guard.executable_problem(psql))
        for path, expected in (
            (other, "psql.exe"),
            (os.path.join(self.work, "missing", "psql.exe"), "not found"),
            ("\\\\server\\share\\psql.exe", "UNC"),
            ("psql.exe", "absolute"),
            (psql + ":stream", "alternate data stream"),
        ):
            problem = guard.executable_problem(path)
            self.assertIsNotNone(problem, path)
            self.assertIn(expected, problem, path)

    def test_source_policy_cannot_be_widened(self):
        """Hosts, database and timeouts cannot relax the fixed policy."""
        hosts = ["127.0.0.1", "::1", "localhost"]
        self.assertIsNone(guard.source_policy_problem(
            hosts, "mimiciv_omop", TIMEOUTS))
        self.assertIsNone(guard.source_policy_problem(
            ["127.0.0.1"], "mimiciv_omop", TIMEOUTS))
        for allowed in (hosts + ["10.0.0.5"], [], "127.0.0.1",
                        ["127.0.0.1", "0.0.0.0"]):
            self.assertIn("allowed source hosts", guard.source_policy_problem(
                allowed, "mimiciv_omop", TIMEOUTS), repr(allowed))
        for database in ("postgres", "template1", "", None):
            self.assertIn("database", guard.source_policy_problem(
                hosts, database, TIMEOUTS), repr(database))
        for value in (0, -1, True, "10", 1.5, None):
            timeouts = dict(TIMEOUTS, statement_timeout_ms=value)
            self.assertIn("statement_timeout_ms", guard.source_policy_problem(
                hosts, "mimiciv_omop", timeouts), repr(value))

    def test_connection_settings_are_checked_without_echoing_values(self):
        """Loopback, database, port and redirect rules are all enforced."""
        base = {"PGHOST": "127.0.0.1", "PGPORT": "5432",
                "PGDATABASE": "mimiciv_omop", "PGUSER": USER,
                "PGPASSWORD": SECRET}
        hosts = list(guard.ALLOWED_SOURCE_HOSTS)
        for overrides in ({}, {"PGHOST": "::1"}, {"PGHOST": "localhost"},
                          {"PGPORT": "1"}, {"PGPORT": "65535"},
                          {"PGHOSTADDR": ""}):
            environ = dict(base, **overrides)
            self.assertIsNone(guard.connection_problem(
                environ, hosts, "mimiciv_omop"), overrides)
        for overrides, expected in (
            ({"PGHOSTADDR": "10.0.0.5"}, "PGHOSTADDR"),
            ({"PGHOSTADDR": "127.0.0.1"}, "PGHOSTADDR"),
            ({"PGSERVICE": "synthetic"}, "PGSERVICE"),
            ({"PGSERVICEFILE": "C:\\never.conf"}, "PGSERVICEFILE"),
            ({"PGHOST": ""}, "PGHOST"),
            ({"PGHOST": "10.0.0.5"}, "PGHOST"),
            ({"PGHOST": "127.0.0.1,10.0.0.5"}, "PGHOST"),
            ({"PGHOST": "/tmp"}, "PGHOST"),
            ({"PGDATABASE": "postgres"}, "PGDATABASE"),
            ({"PGDATABASE": ""}, "PGDATABASE"),
            ({"PGDATABASE": "dbname=mimiciv_omop host=10.0.0.5"},
             "connection string"),
            ({"PGDATABASE": "postgresql://10.0.0.5/mimiciv_omop"},
             "connection string"),
            ({"PGDATABASE": "postgres://127.0.0.1/mimiciv_omop"},
             "connection string"),
            ({"PGPORT": ""}, "PGPORT"),
            ({"PGPORT": "0"}, "PGPORT"),
            ({"PGPORT": "65536"}, "PGPORT"),
            ({"PGPORT": "99999"}, "PGPORT"),
            ({"PGPORT": "05432"}, "PGPORT"),
            ({"PGPORT": "5432 "}, "PGPORT"),
            ({"PGPORT": "54a2"}, "PGPORT"),
            ({"PGUSER": " "}, "PGUSER"),
            ({"PGPASSWORD": ""}, "PGPASSWORD"),
        ):
            environ = dict(base, **overrides)
            problem = guard.connection_problem(environ, hosts,
                                               "mimiciv_omop")
            self.assertIsNotNone(problem, overrides)
            self.assertIn(expected, problem, overrides)
            self.assertNotIn(SECRET, problem)
            self.assertNotIn(USER, problem)
            self.assertNotIn("10.0.0.5", problem)
        narrowed = dict(base, PGHOST="localhost")
        self.assertIn("PGHOST", guard.connection_problem(
            narrowed, ["127.0.0.1"], "mimiciv_omop"))

    def test_controlled_environment_is_allow_listed(self):
        """Only OS basics, validated connection values and forced values."""
        environ = {"SYSTEMROOT": "C:\\Windows", "PATH": "C:\\Windows",
                   "PGHOST": "127.0.0.1", "PGPORT": "5432",
                   "PGDATABASE": "mimiciv_omop", "PGUSER": USER,
                   "PGPASSWORD": SECRET,
                   "PGOPTIONS": "-c default_transaction_read_only=off",
                   "PGHOSTADDR": "10.0.0.5", "PGSERVICE": "synthetic",
                   "PGSERVICEFILE": "C:\\never.conf",
                   "PGPASSFILE": "C:\\never.pass", "PGSYSCONFDIR": "C:\\x",
                   "PGSSLMODE": "disable", "PGTARGETSESSIONATTRS": "any",
                   "UNRELATED": "synthetic"}
        forced = {"PGOPTIONS": "-c default_transaction_read_only=on"}
        env = guard.controlled_psql_environment(environ, forced)
        self.assertEqual(set(env), {"SYSTEMROOT", "PATH", "PGHOST", "PGPORT",
                                    "PGDATABASE", "PGUSER", "PGPASSWORD",
                                    "PGOPTIONS"})
        self.assertEqual(env["PGOPTIONS"],
                         "-c default_transaction_read_only=on")
        self.assertEqual(env["PGPASSWORD"], SECRET)

    def test_environment_definitions_cannot_bypass_path_checks(self):
        """Fixed bindings, canonical values and no free literals/templates."""
        keys = {"temp": {}, "outputs": {}, "clearml_local": {},
                "pytest_temp": {}, "pytest_cache": {}}
        canonical = guard.TEMPLATE_VARIABLES["PYTEST_ADDOPTS"]
        for entry in (
            {"name": "TEMP", "directory": "temp"},
            {"name": "CLEARML_CONFIG_FILE",
             "file": {"directory": "clearml_local", "name": "clearml.conf"}},
            {"name": "CLEARML_OFFLINE_MODE", "literal": "1"},
            {"name": "PYTEST_ADDOPTS", "template": canonical},
            {"name": "EXTRA_TOOL_DIR", "directory": "outputs"},
        ):
            self.assertIsNone(guard.environment_entry_problem(entry, keys),
                              entry)
        for entry, expected in (
            ({"name": "TEMP", "literal": "C:\\Temp"},
             "directory entry for key temp"),
            ({"name": "TEMP", "template": "${dir:temp}"},
             "directory entry for key temp"),
            ({"name": "TEMP", "directory": "outputs"},
             "directory entry for key temp"),
            ({"name": "PYTHONPYCACHEPREFIX", "literal": "x"},
             "directory entry for key pycache"),
            ({"name": "CLEARML_OFFLINE_MODE", "literal": "0"},
             "exactly the literal"),
            ({"name": "CLEARML_OFFLINE_MODE", "literal": "D:\\clearml"},
             "exactly the literal"),
            ({"name": "CLEARML_OFFLINE_MODE", "literal": 1},
             "exactly the literal"),
            ({"name": "CLEARML_OFFLINE_MODE", "template": "1"},
             "exactly the literal"),
            ({"name": "PYTEST_ADDOPTS",
              "template": canonical + " --basetemp=. -o cache_dir=."},
             "canonical template"),
            ({"name": "PYTEST_ADDOPTS", "template": canonical + " -p no:x"},
             "canonical template"),
            ({"name": "PYTEST_ADDOPTS",
              "template": canonical.replace("pytest_temp", "PYTEST_X")
              .replace("pytest_cache", "pytest_temp")
              .replace("PYTEST_X", "pytest_cache")},
             "canonical template"),
            ({"name": "PYTEST_ADDOPTS", "template": "--basetemp=C:\\pytest"},
             "canonical template"),
            ({"name": "PYTEST_ADDOPTS", "literal": "--basetemp=relative"},
             "canonical template"),
            ({"name": "PYTEST_ADDOPTS", "literal": canonical},
             "canonical template"),
            ({"name": "PIP_TARGET", "literal": "relative"},
             "additional variables"),
            ({"name": "EXTRA_OPTS", "template": "${dir:temp}"},
             "additional variables"),
            ({"name": "EXTRA_TOOL_DIR", "directory": "unknown"},
             "unknown directory"),
            ({"name": "EXTRA_FILE", "file": "clearml.conf"},
             "unknown directory"),
            ({"name": "CLEARML_CONFIG_FILE",
              "file": {"directory": "outputs", "name": "clearml.conf"}},
             "file entry in directory key clearml_local"),
            ({"name": "CLEARML_CONFIG_FILE", "file": "clearml.conf"},
             "file entry in directory key clearml_local"),
            ({"name": "CLEARML_CONFIG_FILE",
              "file": {"directory": "clearml_local",
                       "name": "..\\clearml.conf"}}, "single path component"),
            ({"name": "CLEARML_CONFIG_FILE",
              "file": {"directory": "clearml_local", "name": ".git"}},
             ".git"),
            ({"name": "CLEARML_CONFIG_FILE",
              "file": {"directory": "clearml_local",
                       "name": "clearml.conf:ads"}}, "alternate data stream"),
            ({"name": "TEMP", "directory": "temp", "literal": "1"},
             "exactly one"),
            ({"name": "TEMP"}, "exactly one"),
            ({"name": "temp", "directory": "temp"}, "upper-case"),
            ({"directory": "temp"}, "upper-case"),
        ):
            problem = guard.environment_entry_problem(entry, keys)
            self.assertIsNotNone(problem, entry)
            self.assertIn(expected, problem, entry)

    def test_mandatory_set_matches_configuration_and_detects_gaps(self):
        """The fixed mandatory set is complete in paths.json; gaps fail."""
        with open(PATHS_PATH, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        self.assertEqual(
            sorted(entry["name"] for entry in config["environment"]),
            sorted(guard.REQUIRED_VARIABLES))
        self.assertLessEqual(set(guard.REQUIRED_DIRECTORY_KEYS),
                             set(config["directories"]))
        self.assertEqual(guard.environment_set_problems(
            config["directories"], config["environment"]), [])
        for change, subjects in (
            (remove_entries("TEMP", "TMP", "TMPDIR"),
             {"TEMP", "TMP", "TMPDIR"}),
            (remove_entries("PYTEST_ADDOPTS"), {"PYTEST_ADDOPTS"}),
            (remove_entries("CLEARML_OFFLINE_MODE"),
             {"CLEARML_OFFLINE_MODE"}),
            (add_entry({"name": "TEMP", "directory": "temp"}), {"TEMP"}),
            (lambda c: c["directories"].pop("verification"),
             {"directory:verification"}),
            (move_directory("conda_envs", "environments\\conda;relative"),
             {"CONDA_ENVS_DIRS"}),
            (move_directory("conda_pkgs", "cache\\conda,pkgs"),
             {"CONDA_PKGS_DIRS"}),
        ):
            variant = json.loads(json.dumps(config))
            change(variant)
            found = {subject for subject, _ in guard.environment_set_problems(
                variant["directories"], variant["environment"])}
            self.assertEqual(found, subjects)

    def test_simulated_reparse_and_kind_attributes(self):
        """Mocked lstat results exercise every branch without real links."""
        real_lstat = os.lstat
        folder = stat.S_IFDIR | 0o777
        regular = stat.S_IFREG | 0o666
        link = stat.S_IFLNK | 0o777
        reparse = guard.REPARSE_POINT
        fakes = {
            "sim-junction": SimpleNamespace(
                st_mode=folder, st_file_attributes=0x10 | reparse),
            "sim-dir-symlink": SimpleNamespace(
                st_mode=link, st_file_attributes=0x10 | reparse),
            "sim-file-symlink.txt": SimpleNamespace(
                st_mode=link, st_file_attributes=0x20 | reparse),
            "sim-dangling.txt": SimpleNamespace(
                st_mode=link, st_file_attributes=reparse),
            "sim-link-without-attributes.txt": SimpleNamespace(st_mode=link),
            "sim-other-reparse.txt": SimpleNamespace(
                st_mode=regular, st_file_attributes=0x80 | reparse),
            "sim-plain-dir": SimpleNamespace(
                st_mode=folder, st_file_attributes=0x10),
            "sim-plain-file.txt": SimpleNamespace(
                st_mode=regular, st_file_attributes=0x20),
        }

        def fake_lstat(path, *args, **kwargs):
            """Return a simulated result for the named scratch children."""
            text = os.fspath(path)
            parent, name = os.path.split(text)
            if ntpath.normcase(parent) == ntpath.normcase(self.work):
                if name == "sim-denied":
                    raise PermissionError(13, "simulated")
                if name in fakes:
                    return fakes[name]
            return real_lstat(path, *args, **kwargs)

        def sim(*parts):
            """Return a scratch path for a simulated component."""
            return os.path.join(self.work, *parts)

        with mock.patch.object(guard.os, "lstat", fake_lstat):
            for path, kind, expected in (
                (sim("sim-junction"), "directory", "reparse point"),
                (sim("sim-junction", "child"), "directory", "reparse point"),
                (sim("sim-junction", "a.txt"), "file", "reparse point"),
                (sim("sim-dir-symlink"), "directory", "reparse point"),
                (sim("sim-file-symlink.txt"), "file", "reparse point"),
                (sim("sim-dangling.txt"), "file", "reparse point"),
                (sim("sim-link-without-attributes.txt"), "file",
                 "reparse point"),
                (sim("sim-other-reparse.txt"), "file", "reparse point"),
                (sim("sim-plain-file.txt"), "directory", "not a directory"),
                (sim("sim-plain-file.txt", "child"), "directory",
                 "not a directory"),
                (sim("sim-plain-dir"), "file", "not a regular file"),
                (sim("sim-denied", "child"), "directory", "cannot inspect"),
            ):
                problem = guard.destination_problem(path, kind)
                self.assertIsNotNone(problem, path)
                self.assertIn(expected, problem, path)
            for path, kind in ((sim("sim-plain-dir"), "directory"),
                               (sim("sim-plain-dir", "child"), "directory"),
                               (sim("sim-plain-file.txt"), "file")):
                self.assertIsNone(guard.destination_problem(path, kind),
                                  path)
        self.assertEqual(os.listdir(self.work), [])


WORKSPACE_ONLY_KEYS = ("workspace_root", "prepared_data", "outputs", "logs",
               "verification", "clearml_local", "test_scratch")
RUNTIME_KEYS = ("runtime_root", "temp", "pytest_temp", "pytest_cache",
                "hf_home", "hf_hub", "hf_assets", "hf_datasets", "torch_home",
                "pip_cache", "uv_cache", "xdg_cache", "pycache", "conda_pkgs",
                "conda_envs", "uv_project_env", "venvs", "models",
                "clearml_cache")


class RuntimePolicyTests(ScratchTestCase):
    """The two-tier policy: exact runtime categories, workspace authority."""

    def test_fixed_runtime_root_and_categories(self):
        """The runtime root and its category set are fixed in code."""
        self.assertEqual(guard.RUNTIME_ROOT,
                         "G:\\Industry Dataset\\ehr-evidence-runtime")
        self.assertEqual(guard.RUNTIME_DIRECTORY_KEYS,
                         frozenset(RUNTIME_KEYS))
        self.assertEqual(guard.DIRECTORY_VARIABLES["EHR_RUNTIME_ROOT"],
                         "runtime_root")
        for key in WORKSPACE_ONLY_KEYS:
            self.assertNotIn(key, guard.RUNTIME_DIRECTORY_KEYS)
        self.assertIsNone(guard.fixed_policy_problem())

    def test_configuration_places_every_category_on_its_tier(self):
        """paths.json puts runtime categories in the runtime root, the rest in the workspace."""
        with open(PATHS_PATH, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        self.assertIsNone(guard.runtime_root_setting_problem(
            config["runtime_root"]))
        for key, entry in config["directories"].items():
            path, root = guard.configured_directory_path(key, entry)
            if key in RUNTIME_KEYS:
                self.assertEqual(root, guard.RUNTIME_ROOT, key)
            else:
                self.assertEqual(root, guard.PRIVATE_ROOT, key)
            self.assertTrue(guard.is_within(path, root), key)
        self.assertEqual(
            {key for key, entry in config["directories"].items()
             if is_runtime_entry(entry)}, set(RUNTIME_KEYS))

    def test_root_fields_accept_only_approved_runtime_categories(self):
        """Only the fixed runtime keys may name the runtime root."""
        for key in RUNTIME_KEYS:
            for root in ("runtime", "workspace"):
                self.assertIsNone(guard.directory_root_problem(
                    key, {"path": "x", "root": root}), (key, root))
        for key in WORKSPACE_ONLY_KEYS + ("gnn_mirror", "prepared_cache",
                                  "extra_tool"):
            problem = guard.directory_root_problem(
                key, {"path": "x", "root": "runtime"})
            self.assertIn("workspace-only category", problem, key)
            self.assertIsNone(guard.directory_root_problem(
                key, {"path": "x"}), key)
        for value in ("G:", "RUNTIME", "", None, 1, guard.RUNTIME_ROOT):
            self.assertIn("root must be", guard.directory_root_problem(
                "temp", {"path": "x", "root": value}), repr(value))
        self.assertIn("not an object",
                      guard.directory_root_problem("temp", "temp"))

    def test_configured_paths_resolve_against_the_named_root(self):
        """Relative paths join their own root and cannot escape it."""
        self.assertEqual(guard.configured_directory_path(
            "temp", {"path": "temp", "root": "runtime"}),
            (guard.RUNTIME_ROOT + "\\temp", guard.RUNTIME_ROOT))
        self.assertEqual(guard.configured_directory_path(
            "runtime_root", {"path": ".", "root": "runtime"}),
            (guard.RUNTIME_ROOT, guard.RUNTIME_ROOT))
        self.assertEqual(guard.configured_directory_path(
            "outputs", {"path": "outputs"}),
            (guard.PRIVATE_ROOT + "\\outputs", guard.PRIVATE_ROOT))
        for key, entry in (
                ("temp", {"path": "..\\ehr-evidence-workspace",
                          "root": "runtime"}),
                ("temp", {"path": "C:\\temp", "root": "runtime"}),
                ("temp", {"path": "temp\\", "root": "runtime"}),
                ("outputs", {"path": "outputs", "root": "runtime"})):
            with self.assertRaises(ValueError, msg=entry):
                guard.configured_directory_path(key, entry)
        for value in ("G:\\Industry Dataset", "G:\\", None,
                      guard.RUNTIME_ROOT + "-old", guard.PRIVATE_ROOT):
            self.assertIsNotNone(guard.runtime_root_setting_problem(value))
        self.assertIsNone(guard.runtime_root_setting_problem(
            guard.RUNTIME_ROOT.lower()))

    def test_runtime_destinations_require_the_explicit_root(self):
        """Runtime paths pass only with root=RUNTIME_ROOT and only inside it."""
        inside = os.path.join(self.runtime_work, "cache", "uv")
        self.assertIsNone(guard.destination_problem(
            inside, "directory", root=guard.RUNTIME_ROOT))
        self.assertIsNone(guard.destination_problem(
            guard.RUNTIME_ROOT, "directory", strict=False,
            root=guard.RUNTIME_ROOT))
        # The default root is the workspace; no runtime path is accepted
        # without naming it.
        self.assertIn("not inside", guard.destination_problem(
            inside, "directory"))
        self.assertIn("not inside", guard.destinations_problem(
            [(inside, "directory")]))
        self.assertIn("trailing separator", guard.destination_problem(
            "G:\\", "directory", root=guard.RUNTIME_ROOT))
        for path in ("G:\\Industry Dataset",
                     "G:\\Industry Dataset\\other",
                     guard.RUNTIME_ROOT + "-old\\temp",
                     guard.RUNTIME_ROOT + "2",
                     "H:\\Industry Dataset\\ehr-evidence-runtime\\temp",
                     guard.PRIVATE_ROOT + "\\outputs",
                     os.path.join(self.work, "outputs")):
            problem = guard.destination_problem(path, "directory",
                                                root=guard.RUNTIME_ROOT)
            self.assertIn("not inside", problem, path)
        self.assertIn("not inside", guard.destination_problem(
            guard.RUNTIME_ROOT, "directory", root=guard.RUNTIME_ROOT))
        for root in ("G:\\", "G:\\Industry Dataset", guard.POLICY_ANCHOR,
                     guard.RUNTIME_ROOT.lower(),
                     guard.RUNTIME_ROOT + "\\temp"):
            with self.assertRaises(ValueError, msg=root):
                guard.destination_problem(inside, "directory", root=root)
            with self.assertRaises(ValueError, msg=root):
                guard.relative_problem("temp", root)

    def test_w_source_and_output_rules_are_preserved(self):
        """Workspace destinations pass by default and never with the runtime root."""
        output = os.path.join(self.work, "outputs", "report.json")
        self.assertIsNone(guard.destination_problem(output, "file"))
        self.assertIn("not inside", guard.destination_problem(
            output, "file", root=guard.RUNTIME_ROOT))
        for value in (guard.RUNTIME_ROOT, guard.RUNTIME_ROOT + "\\temp"):
            self.assertIsNotNone(guard.configured_root_problem(value, "root"))
            self.assertIsNotNone(guard.source_directory_problem(value))

    def test_runtime_git_and_kind_rules_apply(self):
        """.git ancestors and wrong kinds are refused on the runtime root."""
        repo = os.path.join(self.runtime_work, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        blocker = os.path.join(self.runtime_work, "blocker.txt")
        write_text(blocker)
        for path, kind, expected in (
                (os.path.join(repo, "cache"), "directory", ".git"),
                (os.path.join(blocker, "child"), "directory",
                 "not a directory"),
                (self.runtime_work, "file", "not a regular file")):
            problem = guard.destination_problem(path, kind,
                                                root=guard.RUNTIME_ROOT)
            self.assertIn(expected, problem, path)

    def test_runtime_junctions_are_rejected(self):
        """A real NTFS junction on the runtime root is refused."""
        target = os.path.join(self.runtime_work, "target")
        write_text(os.path.join(target, "inside.txt"))
        link = os.path.join(self.runtime_work, "junction")
        make_junction(self, link, target)
        for path, kind in ((link, "directory"),
                           (os.path.join(link, "child"), "directory"),
                           (os.path.join(link, "inside.txt"), "file")):
            problem = guard.destination_problem(path, kind,
                                                root=guard.RUNTIME_ROOT)
            self.assertIn("reparse point", problem, path)

    def test_runtime_volume_is_fixed_ntfs(self):
        """The configured runtime root is on a fixed NTFS volume."""
        self.assertIsNone(guard.runtime_volume_problem())
        with self.assertRaises(ValueError):
            guard.runtime_volume_problem("G:\\")


SYNTHETIC_SID = "S-1-5-21-1-2-3-1001"


def synthetic_volume_facts(**overrides):
    """Return passing synthetic runtime volume facts (no real identifier)."""
    facts = {"file_system": "NTFS", "drive_type": "Fixed",
             "volume_health": "Healthy", "volume_operational": "OK",
             "size_bytes": 10 ** 12, "free_bytes": 5 * 10 ** 11,
             "volume_id": "\\\\?\\Volume{synthetic-volume}\\",
             "partition_guid": "{synthetic-partition}",
             "disk_model": storage.EXPECTED_DISK_MODEL, "bus_type": "NVMe",
             "disk_health": "Healthy", "disk_operational": "Online",
             "media_type": "SSD", "bitlocker": {"available": False}}
    facts.update(overrides)
    return facts


def synthetic_acl(**overrides):
    """Return a synthetic restrictive runtime ACL with optional overrides."""
    rules = [{"sid": sid, "rights": storage.FULL_CONTROL, "type": "Allow",
              "inherited": False, "inheritance": storage.INHERIT_BOTH,
              "propagation": "None"}
             for sid in (storage.SYSTEM_SID, storage.ADMINISTRATORS_SID,
                         SYNTHETIC_SID)]
    acl = {"protected": True, "owner": SYNTHETIC_SID, "rules": rules}
    acl.update(overrides)
    return acl


class VerifierGateTests(ScratchTestCase):
    """The verifier gate refuses before any marker, temp or report write."""

    def setUp(self):
        """Replace the runtime gate's queries with synthetic current state.

        The real gate logic (pin, identity, health and ACL evaluation) runs
        on self.gate_state, which drift tests change; no PowerShell query
        is made.
        """
        super().setUp()
        self.gate_state = {
            "facts": synthetic_volume_facts(),
            "pinned": storage.identity_digest(synthetic_volume_facts()),
            "acls": [("runtime-parent", synthetic_acl()),
                     ("runtime-root", synthetic_acl())]}
        state = self.gate_state
        for name, stub in (
                ("collect_volume_facts", lambda: state["facts"]),
                ("read_pinned_digest", lambda: state["pinned"]),
                ("current_user_sid", lambda: SYNTHETIC_SID),
                ("collect_runtime_acls", lambda: state["acls"])):
            patcher = mock.patch.object(storage, name, side_effect=stub)
            patcher.start()
            self.addCleanup(patcher.stop)

    def expected_environment(self, config):
        """Return the environment values a configuration would expect.

        A variant whose directories cannot be resolved (an unsafe root field
        or relative path) expects no values; the gate refuses it before any
        environment comparison.
        """
        try:
            directories = verify.resolve_directories(config)
        except ValueError:
            return {}
        return {entry["name"]: verify.expected_env_value(entry, directories)
                for entry in config["environment"]
                if "name" in entry and any(kind in entry
                                           for kind in guard.VALUE_KINDS)}

    def seed_prior_outputs(self):
        """Write a prior report and marker into the scratch destinations."""
        config = self.scratch_config()
        directories = verify.resolve_directories(config)
        marker = config["policy"]["marker_file_name"]
        write_text(os.path.join(directories["verification"],
                                verify.REPORT_NAME), "prior report\n")
        write_text(os.path.join(directories["outputs"], marker),
                   "prior marker\n")

    def configured_prefix(self, config):
        """Return the configured pycache directory, or None if undefined."""
        if "pycache" not in config["directories"]:
            return None
        try:
            return verify.resolve_directories(config)["pycache"]
        except ValueError:
            return None

    def run_verifier(self, config, extra_env=None, prefix=CONFIGURED):
        """Run verify.main on a variant with a matching simulated runtime.

        The environment holds the values the variant expects plus extra_env,
        and sys.pycache_prefix is the variant's pycache directory unless a
        prefix is given. Both are restored afterwards.
        """
        path = self.write_config(config, "paths-variant.json")
        environment = self.expected_environment(config)
        environment.update(extra_env or {})
        if prefix is CONFIGURED:
            prefix = self.configured_prefix(config)
        stream = io.StringIO()
        with mock.patch.dict(os.environ, environment), \
                mock.patch.object(verify.sys, "pycache_prefix", prefix), \
                redirect_stdout(stream):
            code = verify.main(["--config", path])
        return code, stream.getvalue()

    def assert_gate_refused(self, config, check_name, extra_env=None,
                            prefix=CONFIGURED):
        """Run the verifier on a variant and assert it wrote nothing."""
        self.write_config(config, "paths-variant.json")
        before = self.snapshot()
        environment_before = dict(os.environ)
        prefix_before = sys.pycache_prefix
        code, output = self.run_verifier(config, extra_env, prefix)
        self.assertEqual(code, 1, output)
        self.assertIn("ABORTED before any write", output)
        failed = [line.split()[2] for line in output.splitlines()
                  if line.startswith("[FAIL]")]
        self.assertIn(check_name, failed, output)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(dict(os.environ), environment_before)
        self.assertEqual(sys.pycache_prefix, prefix_before)

    def test_gate_accepts_scratch_destinations_and_missing_leaves(self):
        """A relocated-in-private-root configuration passes the gate."""
        config = self.scratch_config()
        with mock.patch.dict(os.environ, self.expected_environment(config)), \
                mock.patch.object(verify.sys, "pycache_prefix",
                                  self.configured_prefix(config)):
            checks = verify.gate_checks(config)
        failures = [check for check in checks if check["status"] != "pass"]
        self.assertEqual(failures, [])
        names = {check["name"] for check in checks}
        for expected in ("policy:private-root", "policy:anchor",
                         "policy:runtime-root", "runtime:volume",
                         "runtime:root-exists",
                         "runtime:gate:volume-identity",
                         "runtime:gate:disk-health",
                         "runtime:gate:acl:runtime-parent",
                         "runtime:gate:acl:runtime-root",
                         "envset:complete", "envdef:TEMP",
                         "envdef:EHR_RUNTIME_ROOT",
                         "envdef:PYTEST_ADDOPTS", "runtime:pycache-prefix",
                         "containment:uv_project_env",
                         "containment:file:CLEARML_CONFIG_FILE",
                         "containment:marker:outputs", "containment:report",
                         "containment:probe-source",
                         "containment:probe-bytecode"):
            self.assertIn(expected, names)
        details = {check["name"]: check["detail"] for check in checks}
        self.assertIn(guard.RUNTIME_ROOT, details["containment:temp"])
        self.assertIn(guard.PRIVATE_ROOT, details["containment:outputs"])
        self.assertEqual(os.listdir(self.work), [])
        self.assertEqual(os.listdir(self.runtime_work), [])

    def test_identity_and_acl_drift_stop_the_verifier_before_writes(self):
        """Runtime drift since the preflight aborts with nothing written."""
        good_acls = list(self.gate_state["acls"])
        extra = dict(synthetic_acl()["rules"][0], sid="S-1-5-11")
        for check_name, change in (
                ("runtime:gate:volume-identity",
                 {"facts": synthetic_volume_facts(
                     volume_id="\\\\?\\Volume{other-volume}\\")}),
                ("runtime:gate:volume-identity",
                 {"facts": synthetic_volume_facts(size_bytes=10 ** 12 + 1)}),
                ("runtime:gate:volume-pin", {"pinned": None}),
                ("runtime:gate:volume-health",
                 {"facts": synthetic_volume_facts(volume_health="Warning")}),
                ("runtime:gate:file-system",
                 {"facts": synthetic_volume_facts(file_system="exFAT")}),
                ("runtime:gate:acl:runtime-root",
                 {"acls": [good_acls[0], ("runtime-root", synthetic_acl(
                     rules=synthetic_acl()["rules"] + [extra]))]}),
                ("runtime:gate:acl:runtime-parent",
                 {"acls": [("runtime-parent", synthetic_acl(
                     protected=False)), good_acls[1]]}),
                ("runtime:gate:acl:runtime-root",
                 {"acls": [good_acls[0], ("runtime-root", None)]})):
            with self.subTest(check=check_name, change=sorted(change)):
                saved = dict(self.gate_state)
                self.gate_state.update(change)
                try:
                    self.seed_prior_outputs()
                    self.assert_gate_refused(self.scratch_config(),
                                             check_name)
                finally:
                    self.gate_state.clear()
                    self.gate_state.update(saved)

    def test_unavailable_runtime_gate_stops_the_verifier(self):
        """A runtime gate that cannot run fails closed before writes."""
        self.seed_prior_outputs()
        with mock.patch.object(storage, "collect_volume_facts",
                               side_effect=storage.PreflightError(
                                   "synthetic query failure")):
            self.assert_gate_refused(self.scratch_config(), "runtime:gate")

    def test_gate_refuses_an_incomplete_mandatory_set(self):
        """Omitted, duplicated or delimiter-bearing entries stop the gate."""
        outside = {"TEMP": "C:\\Windows\\Temp", "TMP": "C:\\Windows\\Temp",
                   "TMPDIR": "C:\\Windows\\Temp"}
        for check_name, change, extra_env in (
            ("envset:TEMP", remove_entries("TEMP", "TMP", "TMPDIR"), outside),
            ("envset:PYTEST_ADDOPTS", remove_entries("PYTEST_ADDOPTS"),
             {"PYTEST_ADDOPTS": "--basetemp=."}),
            ("envset:CLEARML_OFFLINE_MODE",
             remove_entries("CLEARML_OFFLINE_MODE"), None),
            ("envset:PYTHONPYCACHEPREFIX",
             remove_entries("PYTHONPYCACHEPREFIX"), None),
            ("envset:TEMP",
             add_entry({"name": "TEMP", "directory": "temp",
                        "evidence": "probe", "purpose": "duplicate"}), None),
            ("envset:directory:verification",
             lambda c: c["directories"].pop("verification"), None),
            ("envset:CONDA_ENVS_DIRS",
             lambda c: c["directories"]["conda_envs"].update(
                 path=c["directories"]["conda_envs"]["path"] + ";relative"),
             None),
            ("envset:CONDA_PKGS_DIRS",
             lambda c: c["directories"]["conda_pkgs"].update(
                 path=c["directories"]["conda_pkgs"]["path"] + ",relative"),
             None),
        ):
            with self.subTest(check=check_name):
                self.seed_prior_outputs()
                config = self.scratch_config()
                change(config)
                self.assert_gate_refused(config, check_name, extra_env)

    def test_gate_refuses_a_missing_or_wrong_runtime_prefix(self):
        """The running interpreter's bytecode prefix is checked up front."""
        self.seed_prior_outputs()
        config = self.scratch_config()
        # Only unset or scratch-local wrong prefixes are simulated, so even an
        # unexpected import could never write bytecode outside scratch.
        for prefix in (None, os.path.join(self.work, "other-pycache")):
            with self.subTest(prefix=prefix):
                self.assert_gate_refused(config, "runtime:pycache-prefix",
                                         prefix=prefix)

    def test_gate_refuses_relaxed_policy_and_definitions(self):
        """Relocated roots and path-carrying definitions stop the verifier."""
        for check_name, change in (
            ("policy:private-root",
             lambda c: c.update(workspace_root="G:\\Industry Dataset")),
            ("policy:private-root",
             lambda c: c.update(workspace_root=guard.PRIVATE_ROOT + "-old")),
            ("policy:anchor",
             lambda c: c["policy"].update(required_root_prefix="W:\\")),
            ("policy:marker-name",
             lambda c: c["policy"].update(
                 marker_file_name="..\\marker.txt")),
            ("definition:outputs",
             move_directory("outputs", "..\\ehr-evidence-app\\outputs")),
            ("definition:outputs", move_directory("outputs", "C:\\outputs")),
            ("definition:outputs",
             move_directory("outputs", "\\\\server\\share\\outputs")),
            ("definition:logs", move_directory("logs", "outputs\\logs.")),
            ("envdef:TEMP", replace_entry("TEMP", {"literal": "C:\\Temp"})),
            ("envdef:TEMP", replace_entry("TEMP", {"template": "C:\\Temp"})),
            ("envdef:TEMP", replace_entry("TEMP", {"directory": "outputs"})),
            ("envdef:PYTEST_ADDOPTS",
             replace_entry("PYTEST_ADDOPTS",
                           {"template": "--basetemp=C:\\pytest"})),
            ("envdef:PYTEST_ADDOPTS",
             replace_entry("PYTEST_ADDOPTS", {
                 "template": guard.TEMPLATE_VARIABLES["PYTEST_ADDOPTS"]
                 + " --basetemp=. -o cache_dir=."})),
            ("envdef:PYTEST_ADDOPTS",
             replace_entry("PYTEST_ADDOPTS",
                           {"literal": "--basetemp=relative"})),
            ("envdef:CLEARML_OFFLINE_MODE",
             replace_entry("CLEARML_OFFLINE_MODE",
                           {"literal": "D:\\clearml"})),
            ("envdef:CLEARML_OFFLINE_MODE",
             replace_entry("CLEARML_OFFLINE_MODE", {"literal": "0"})),
            ("envdef:PIP_TARGET",
             add_entry({"name": "PIP_TARGET", "literal": "relative",
                        "evidence": "declaration", "purpose": "extra"})),
            ("envdef:CLEARML_CONFIG_FILE",
             replace_entry("CLEARML_CONFIG_FILE",
                           {"file": {"directory": "clearml_local",
                                     "name": "..\\..\\clearml.conf"}})),
            ("policy:runtime-root",
             lambda c: c.update(runtime_root="G:\\Industry Dataset")),
            ("policy:runtime-root", lambda c: c.pop("runtime_root")),
            ("envset:root:outputs", set_root("outputs", "runtime")),
            ("envset:root:verification", set_root("verification",
                                                  "runtime")),
            ("envset:root:clearml_local", set_root("clearml_local",
                                                   "runtime")),
            ("envset:root:prepared_data", set_root("prepared_data",
                                                   "runtime")),
            ("envset:root:temp", set_root("temp", "G:")),
            ("definition:temp", move_directory("temp", "..\\outputs")),
            ("envdef:TEMP", replace_entry(
                "TEMP", {"directory": "outputs"})),
        ):
            with self.subTest(check=check_name):
                self.seed_prior_outputs()
                config = self.scratch_config()
                change(config)
                self.assert_gate_refused(config, check_name)

    def test_gate_refuses_git_file_and_wrong_kind_components(self):
        """.git ancestors, file ancestors and occupied leaves stop it."""
        self.seed_prior_outputs()
        config = self.scratch_config()
        directories = verify.resolve_directories(config)
        marker = config["policy"]["marker_file_name"]
        os.makedirs(os.path.join(self.work, "gitrepo", ".git"))
        write_text(os.path.join(self.work, "blocker"))
        os.makedirs(os.path.join(self.runtime_work, "gitrepo", ".git"))
        os.makedirs(os.path.join(directories["models"], marker))
        os.makedirs(os.path.join(directories["clearml_local"],
                                 "clearml.conf"))
        os.makedirs(os.path.join(directories["hf_home"], ".git"))
        os.makedirs(os.path.join(self.work, "verification-2",
                                 verify.REPORT_NAME))
        for check_name, change in (
            ("containment:outputs",
             move_directory("outputs", self.relative("gitrepo", "outputs"))),
            ("containment:logs",
             move_directory("logs", self.relative("blocker", "logs"))),
            ("containment:marker:models", lambda c: None),
            ("containment:file:CLEARML_CONFIG_FILE", lambda c: None),
            ("containment:hf_home", lambda c: None),
            ("containment:torch_home",
             move_directory("torch_home",
                            self.runtime_relative("gitrepo", "torch"))),
            ("containment:report",
             move_directory("verification",
                            self.relative("verification-2"))),
        ):
            with self.subTest(check=check_name):
                variant = self.scratch_config()
                change(variant)
                self.assert_gate_refused(variant, check_name)

    def test_gate_refuses_a_junction_ancestor(self):
        """A real junction inside a configured destination stops it."""
        self.seed_prior_outputs()
        target = os.path.join(self.work, "junction-target")
        os.makedirs(target)
        link = os.path.join(self.work, "junction")
        make_junction(self, link, target)
        config = self.scratch_config()
        move_directory("outputs", self.relative("junction", "outputs"))(config)
        self.assert_gate_refused(config, "containment:outputs")
        self.assertEqual(os.listdir(target), [])

    def test_gate_refuses_a_runtime_junction_ancestor(self):
        """A real junction on the NTFS runtime root stops the gate."""
        self.seed_prior_outputs()
        target = os.path.join(self.runtime_work, "junction-target")
        os.makedirs(target)
        link = os.path.join(self.runtime_work, "junction")
        make_junction(self, link, target)
        config = self.scratch_config()
        move_directory("models",
                       self.runtime_relative("junction", "models"))(config)
        self.assert_gate_refused(config, "containment:models")
        self.assertEqual(os.listdir(target), [])

    def test_missing_temp_fails_closed_without_default_search(self):
        """With no configured temp directory, the run writes nothing anywhere.

        The gate accepts not-yet-created leaves, so this exercises the write
        phase: markers, temp probes and the report must all fail without any
        fallback. Python's default temp directory search is a tripwire.
        """
        calls = self.forbid_default_tempdir()
        config = self.scratch_config()
        # Every configured directory, including the scratch-level
        # workspace_root and runtime_root keys, is absent, so no marker can
        # land anywhere.
        move_directory("workspace_root", self.relative("absent-root"))(config)
        move_directory("runtime_root",
                       self.runtime_relative("absent-root"))(config)
        self.write_config(config, "paths-variant.json")
        before = self.snapshot()
        code, output = self.run_verifier(config)
        self.assertEqual(code, 1, output)
        self.assertEqual(calls, [])
        self.assertIn("no fallback directory was tried", output)
        self.assertEqual(self.snapshot(), before)

    def forbid_default_tempdir(self):
        """Replace Python's default temp directory search with a tripwire."""
        calls = []

        def tripwire(*args, **kwargs):
            """Record and refuse any default temp directory lookup."""
            calls.append(args)
            raise AssertionError("default temp directory search was used")

        for name in ("gettempdir", "gettempdirb", "_get_default_tempdir"):
            patcher = mock.patch.object(verify.tempfile, name, tripwire)
            patcher.start()
            self.addCleanup(patcher.stop)
        return calls

    def test_temp_probe_uses_only_the_explicit_directory(self):
        """A usable temp root is probed explicitly and cleaned up."""
        calls = self.forbid_default_tempdir()
        temp_root = os.path.join(self.work, "temp")
        os.makedirs(temp_root)
        checks = []
        verify.probe_tempfile(checks, temp_root)
        self.assertEqual([check["status"] for check in checks],
                         ["pass", "pass", "pass"], checks)
        self.assertIn("default tempfile selection is not exercised",
                      checks[0]["detail"])
        self.assertEqual(calls, [])
        self.assertEqual(os.listdir(temp_root), [])

    def test_unusable_temp_roots_fail_closed(self):
        """Missing, file, linked or unwritable roots never fall back."""
        calls = self.forbid_default_tempdir()
        missing = os.path.join(self.work, "missing-temp")
        occupied = os.path.join(self.work, "temp-is-a-file")
        write_text(occupied, "prior content\n")
        simulated = os.path.join(self.work, "simulated-link-temp")
        real_lstat = os.lstat

        def fake_lstat(path, *args, **kwargs):
            """Report the simulated temp root as a junction."""
            if ntpath.normcase(os.fspath(path)) == ntpath.normcase(simulated):
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o777,
                                       st_file_attributes=0x410)
            return real_lstat(path, *args, **kwargs)

        before = tree_snapshot(self.work)
        with mock.patch.object(verify.os, "lstat", fake_lstat):
            for temp_root in (missing, occupied, simulated):
                checks = []
                verify.probe_tempfile(checks, temp_root)
                self.assertEqual(len(checks), 1, temp_root)
                self.assertEqual(checks[0]["status"], "FAIL", temp_root)
                self.assertIn("no fallback directory was tried",
                              checks[0]["detail"])
        self.assertEqual(tree_snapshot(self.work), before)

        writable = os.path.join(self.work, "denied-temp")
        os.makedirs(writable)
        with mock.patch.object(verify.tempfile, "_mkstemp_inner",
                               side_effect=PermissionError(13, "denied")):
            with self.assertRaises(OSError):
                verify.probe_tempfile([], writable)
        self.assertEqual(os.listdir(writable), [])
        self.assertEqual(calls, [])


@unittest.skipUnless(POWERSHELL, "PowerShell is not available")
class SetupPreflightTests(ScratchTestCase):
    """setup-destinations.ps1 refuses before creating or setting anything."""

    def run_powershell(self, command):
        """Run one command in a child PowerShell and return its output."""
        completed = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
             command],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180,
            check=False)
        output = (completed.stdout.decode("utf-8", "replace")
                  + completed.stderr.decode("utf-8", "replace"))
        if "running scripts is disabled" in output:
            self.skipTest("the PowerShell execution policy blocks the "
                          "setup script; no override is used")
        return output

    def run_setup(self, config, operator="&"):
        """Run setup on a scratch configuration in a child PowerShell.

        operator is '&' for an ordinary invocation or '.' for dot-sourcing;
        both must apply, or refuse, identically.
        """
        path = self.write_config(config, "paths-setup.json")
        output = self.run_powershell(
            "$ErrorActionPreference = 'Stop'; $priorTemp = $env:TEMP; "
            "foreach ($name in 'EHR_OUTPUTS_DIR', 'PYTEST_ADDOPTS') "
            "{ [Environment]::SetEnvironmentVariable($name, "
            "'SENTINEL-UNCHANGED', 'Process') }; "
            "try { %s '%s' -ConfigPath '%s' -Quiet; 'SETUP=ok' } "
            "catch { 'SETUP=failed: ' + $_.Exception.Message }; "
            "'EHR_OUTPUTS_DIR=' + $env:EHR_OUTPUTS_DIR; "
            "'PYTEST_ADDOPTS=' + $env:PYTEST_ADDOPTS; "
            "'TEMP_UNCHANGED=' + ($env:TEMP -ceq $priorTemp)"
            % (operator, SETUP_SCRIPT, path))
        self.assertIn("SETUP=", output)
        return output

    def test_dot_sourced_setup_applies_the_same_environment(self):
        """Dot-sourcing runs the full preflight and sets the process env."""
        output = self.run_setup(self.scratch_config(), operator=".")
        self.assertIn("SETUP=ok", output)
        expected = os.path.join(self.work, "outputs")
        self.assertIn(("EHR_OUTPUTS_DIR=" + expected).lower(),
                      output.lower())
        self.assertIn("PYTEST_ADDOPTS=--basetemp=\"%s/temp/pytest\""
                      % self.runtime_work.replace("\\", "/"), output)
        self.assertIn("TEMP_UNCHANGED=False", output)
        self.assertTrue(os.path.isdir(expected))

    def test_dot_sourced_setup_refuses_and_changes_nothing(self):
        """A dot-sourced refusal creates nothing and sets no variable."""
        write_text(os.path.join(self.work, "outputs",
                                "_ehr-destination-marker.txt"),
                   "prior marker\n")
        for expected, change in (
            ("fixed private root",
             lambda c: c.update(workspace_root="G:\\Industry Dataset")),
            ("required environment variable",
             remove_entries("TEMP", "TMP", "TMPDIR")),
            ("canonical template",
             replace_entry("PYTEST_ADDOPTS", {
                 "template": guard.TEMPLATE_VARIABLES["PYTEST_ADDOPTS"]
                 + " --basetemp=. -o cache_dir=."})),
            ("Refusing directory 'outputs'",
             move_directory("outputs", "C:\\ehr-outputs")),
        ):
            with self.subTest(expected=expected):
                config = self.scratch_config()
                change(config)
                self.write_config(config, "paths-setup.json")
                before = self.snapshot()
                output = self.run_setup(config, operator=".")
                self.assertIn("SETUP=failed", output)
                self.assertIn(expected, output)
                for name in ("EHR_OUTPUTS_DIR", "PYTEST_ADDOPTS"):
                    self.assertIn("%s=SENTINEL-UNCHANGED" % name, output)
                self.assertIn("TEMP_UNCHANGED=True", output)
                self.assertEqual(self.snapshot(), before)

    def test_attribute_interpretation_with_simulated_attributes(self):
        """Get-AttributeProblem handles simulated FileAttributes values.

        Only that pure function is extracted from the script with the
        PowerShell parser and defined in the child session; the script body
        is not run, so nothing is read, created or set. Real links are not
        needed for these branches.
        """
        cases = (
            ("JUNCTION_ANCESTOR", 0x410, False, "Directory", "reparse point"),
            ("DIR_SYMLINK_LEAF", 0x410, True, "Directory", "reparse point"),
            ("FILE_SYMLINK_LEAF", 0x420, True, "File", "reparse point"),
            ("DANGLING_LINK_LEAF", 0x400, True, "File", "reparse point"),
            ("OTHER_REPARSE_FILE", 0x480, True, "File", "reparse point"),
            ("FILE_ANCESTOR", 0x20, False, "Directory", "not a directory"),
            ("DIR_AS_FILE_LEAF", 0x10, True, "File", "not a regular file"),
            ("FILE_AS_DIR_LEAF", 0x20, True, "Directory", "not a directory"),
            ("PLAIN_DIR_ANCESTOR", 0x10, False, "File", ""),
            ("PLAIN_DIR_LEAF", 0x10, True, "Directory", ""),
            ("PLAIN_FILE_LEAF", 0x20, True, "File", ""),
            ("NORMAL_FILE_LEAF", 0x80, True, "File", ""),
        )
        lines = ["'%s=' + [string](Get-AttributeProblem -Attributes %d "
                 "-Path 'X:\\simulated' -IsLeaf $%s -Kind %s)"
                 % (label, value, str(leaf).lower(), kind)
                 for label, value, leaf, kind, _ in cases]
        before = tree_snapshot(self.work)
        output = self.run_powershell(
            "$ErrorActionPreference = 'Stop'; "
            "$ast = [System.Management.Automation.Language.Parser]::"
            "ParseFile('%s', [ref]$null, [ref]$null); "
            "$function = $ast.Find({ param($node) $node -is "
            "[System.Management.Automation.Language.FunctionDefinitionAst] "
            "-and $node.Name -eq 'Get-AttributeProblem' }, $true); "
            "if ($null -eq $function) { throw 'function not found' }; "
            ". ([scriptblock]::Create($function.Extent.Text)); %s"
            % (SETUP_SCRIPT, "; ".join(lines)))
        results = dict(line.split("=", 1) for line in output.splitlines()
                       if "=" in line)
        for label, _, _, _, expected in cases:
            self.assertIn(label, results, output)
            if expected:
                self.assertIn(expected, results[label], label)
            else:
                self.assertEqual(results[label].strip(), "", label)
        self.assertEqual(tree_snapshot(self.work), before)

    def run_extracted(self, names, lines):
        """Define only the named pure functions from the script and run lines.

        The functions are extracted with the PowerShell parser; the script
        body is not run, so nothing is read, created or set.
        """
        output = self.run_powershell(
            "$ErrorActionPreference = 'Stop'; "
            "$ast = [System.Management.Automation.Language.Parser]::"
            "ParseFile('%s', [ref]$null, [ref]$null); "
            "foreach ($name in @(%s)) { "
            "$function = $ast.Find({ param($node) $node -is "
            "[System.Management.Automation.Language.FunctionDefinitionAst] "
            "-and $node.Name -eq $name }, $true); "
            "if ($null -eq $function) { throw ('function not found: ' + "
            "$name) }; . ([scriptblock]::Create($function.Extent.Text)) }; "
            "%s" % (SETUP_SCRIPT, ", ".join("'%s'" % name for name in names),
                    "; ".join(lines)))
        return dict(line.split("=", 1) for line in output.splitlines()
                    if "=" in line and line.split("=", 1)[0].isupper())

    def test_runtime_gate_functions_detect_identity_and_acl_drift(self):
        """Setup's pure gate functions match Python and refuse drift."""
        def ps_value(value):
            """Return a PowerShell literal for a synthetic Python value."""
            if isinstance(value, bool):
                return "$true" if value else "$false"
            if isinstance(value, int):
                return "[int64]%d" % value
            if value is None:
                return "$null"
            if isinstance(value, dict):
                return "@{%s}" % "; ".join("%s = %s" % (key, ps_value(item))
                                           for key, item in value.items())
            if isinstance(value, list):
                return "@(%s)" % ", ".join(ps_value(item) for item in value)
            return "'%s'" % str(value).replace("'", "''")

        facts = synthetic_volume_facts()
        facts.pop("bitlocker")
        digest = storage.identity_digest(facts)
        extra = dict(synthetic_acl()["rules"][0], sid="S-1-5-11")
        cases = {
            "DIGEST": "Get-RuntimeIdentityDigest -Facts %s" % ps_value(facts),
            "GOOD": "Get-RuntimeFactsProblem -Facts %s -PinnedDigest '%s'"
                    % (ps_value(facts), digest),
            "NO_PIN": "Get-RuntimeFactsProblem -Facts %s -PinnedDigest $null"
                      % ps_value(facts),
            "OTHER_VOLUME": "Get-RuntimeFactsProblem -Facts %s "
                            "-PinnedDigest '%s'" % (ps_value(dict(
                                facts, volume_id="\\\\?\\Volume{x}\\")),
                                digest),
            "UNHEALTHY": "Get-RuntimeFactsProblem -Facts %s "
                         "-PinnedDigest '%s'" % (ps_value(dict(
                             facts, disk_health="Warning")), digest),
            "EXFAT": "Get-RuntimeFactsProblem -Facts %s -PinnedDigest '%s'"
                     % (ps_value(dict(facts, file_system="exFAT")), digest),
            "ACL_GOOD": "Get-RuntimeAclProblem -Acl %s -UserSid '%s'"
                        % (ps_value(synthetic_acl()), SYNTHETIC_SID),
            "ACL_OTHER": "Get-RuntimeAclProblem -Acl %s -UserSid '%s'"
                         % (ps_value(synthetic_acl(
                             rules=synthetic_acl()["rules"] + [extra])),
                            SYNTHETIC_SID),
            "ACL_INHERIT": "Get-RuntimeAclProblem -Acl %s -UserSid '%s'"
                           % (ps_value(synthetic_acl(protected=False)),
                              SYNTHETIC_SID),
            "ACL_MISSING": "Get-RuntimeAclProblem -Acl %s -UserSid '%s'"
                           % (ps_value(synthetic_acl(
                               rules=synthetic_acl()["rules"][:2])),
                              SYNTHETIC_SID),
        }
        before = self.snapshot()
        results = self.run_extracted(
            ["Get-RuntimeExpectation", "Get-RuntimeIdentityDigest",
             "Get-RuntimeFactsProblem", "Get-RuntimeAclProblem"],
            ["'%s=' + [string](%s)" % (label, command)
             for label, command in cases.items()])
        self.assertEqual(results["DIGEST"].strip(), digest)
        self.assertEqual(results["GOOD"].strip(), "")
        self.assertEqual(results["ACL_GOOD"].strip(), "")
        for label, expected in (
                ("NO_PIN", "no pinned runtime identity digest"),
                ("OTHER_VOLUME", "differs from the pinned digest"),
                ("UNHEALTHY", "disk is not healthy"),
                ("EXFAT", "not NTFS"),
                ("ACL_OTHER", "another principal"),
                ("ACL_INHERIT", "inheritance is not disabled"),
                ("ACL_MISSING", "lacks inherited full control")):
            self.assertIn(expected, results[label], label)
        for value in results.values():
            self.assertNotIn("synthetic-volume", value)
        self.assertEqual(self.snapshot(), before)

    def test_setup_accepts_scratch_config_and_creates_only_scratch(self):
        """A valid in-private-root configuration is applied in scratch."""
        output = self.run_setup(self.scratch_config())
        self.assertIn("SETUP=ok", output)
        expected = os.path.join(self.work, "outputs")
        self.assertIn(("EHR_OUTPUTS_DIR=" + expected).lower(),
                      output.lower())
        self.assertIn("PYTEST_ADDOPTS=--basetemp=\"%s/temp/pytest\""
                      % self.runtime_work.replace("\\", "/"), output)
        self.assertTrue(os.path.isdir(expected))
        self.assertTrue(os.path.isdir(os.path.join(self.runtime_work,
                                                   "temp")))
        self.assertTrue(os.path.isdir(os.path.join(self.work,
                                                   "test-scratch")))
        for work in (self.work, self.runtime_work):
            self.assertFalse(os.path.exists(os.path.join(
                work, "environments", "uv", "ehr-evidence-app")))
        for name in ("temp", "cache", "models", "environments"):
            self.assertFalse(os.path.exists(os.path.join(self.work, name)),
                             name)
        for name in ("outputs", "protected-data", "clearml"):
            self.assertFalse(os.path.exists(os.path.join(self.runtime_work,
                                                         name)), name)

    def test_setup_failures_create_nothing_and_set_nothing(self):
        """Every refused configuration leaves scratch and env unchanged."""
        write_text(os.path.join(self.work, "outputs",
                                "_ehr-destination-marker.txt"),
                   "prior marker\n")
        os.makedirs(os.path.join(self.work, "gitrepo", ".git"))
        write_text(os.path.join(self.work, "blocker"))
        for expected, change in (
            ("fixed private root",
             lambda c: c.update(workspace_root="G:\\Industry Dataset")),
            ("fixed anchor",
             lambda c: c["policy"].update(required_root_prefix="W:\\")),
            ("Refusing directory 'outputs'",
             move_directory("outputs", "C:\\ehr-outputs")),
            ("'..'",
             move_directory("outputs", "..\\ehr-evidence-app\\outputs")),
            ("empty component",
             move_directory("outputs", "\\\\server\\share\\x")),
            ("directory entry for key temp",
             replace_entry("TEMP", {"literal": "C:\\Temp"})),
            ("directory entry for key temp",
             replace_entry("TEMP", {"directory": "outputs"})),
            ("canonical template",
             replace_entry("PYTEST_ADDOPTS",
                           {"template": "--basetemp=C:\\pytest"})),
            ("canonical template",
             replace_entry("PYTEST_ADDOPTS", {
                 "template": guard.TEMPLATE_VARIABLES["PYTEST_ADDOPTS"]
                 + " --basetemp=. -o cache_dir=."})),
            ("canonical template",
             replace_entry("PYTEST_ADDOPTS",
                           {"literal": "--basetemp=relative"})),
            ("exactly the literal 1",
             replace_entry("CLEARML_OFFLINE_MODE", {"literal": "0"})),
            ("additional variables",
             add_entry({"name": "PIP_TARGET", "literal": "relative",
                        "evidence": "declaration", "purpose": "extra"})),
            ("required environment variable",
             remove_entries("TEMP", "TMP", "TMPDIR")),
            ("PYTEST_ADDOPTS is defined 0 times",
             remove_entries("PYTEST_ADDOPTS")),
            ("TEMP is defined 2 times",
             add_entry({"name": "TEMP", "directory": "temp",
                        "evidence": "probe", "purpose": "duplicate"})),
            ("required directory key verification",
             lambda c: c["directories"].pop("verification")),
            ("list delimiter",
             lambda c: c["directories"]["conda_envs"].update(
                 path=c["directories"]["conda_envs"]["path"] + ";relative")),
            ("single path component",
             replace_entry("CLEARML_CONFIG_FILE",
                           {"file": {"directory": "clearml_local",
                                     "name": "..\\..\\clearml.conf"}})),
            ("contains .git",
             move_directory("outputs", self.relative("gitrepo", "outputs"))),
            ("not a directory",
             move_directory("logs", self.relative("blocker", "logs"))),
            ("fixed runtime root",
             lambda c: c.update(runtime_root="G:\\Industry Dataset")),
            ("fixed runtime root", lambda c: c.pop("runtime_root")),
            ("outputs is a workspace-only category", set_root("outputs", "runtime")),
            ("verification is a workspace-only category",
             set_root("verification", "runtime")),
            ("clearml_local is a workspace-only category",
             set_root("clearml_local", "runtime")),
            ("test_scratch is a workspace-only category",
             set_root("test_scratch", "runtime")),
            ("root must be", set_root("temp", "G:")),
            ("Refusing directory 'temp'",
             move_directory("temp", "..\\outputs")),
        ):
            with self.subTest(expected=expected):
                config = self.scratch_config()
                change(config)
                self.write_config(config, "paths-setup.json")
                before = self.snapshot()
                output = self.run_setup(config)
                self.assertIn("SETUP=failed", output)
                self.assertIn(expected, output)
                for name in ("EHR_OUTPUTS_DIR", "PYTEST_ADDOPTS"):
                    self.assertIn("%s=SENTINEL-UNCHANGED" % name, output)
                self.assertIn("TEMP_UNCHANGED=True", output)
                self.assertEqual(self.snapshot(), before)

    def test_setup_refuses_a_junction_ancestor(self):
        """A real junction inside a destination aborts setup untouched."""
        target = os.path.join(self.work, "junction-target")
        os.makedirs(target)
        link = os.path.join(self.work, "junction")
        make_junction(self, link, target)
        config = self.scratch_config()
        move_directory("outputs", self.relative("junction", "outputs"))(config)
        self.write_config(config, "paths-setup.json")
        before = self.snapshot()
        output = self.run_setup(config)
        self.assertIn("SETUP=failed", output)
        self.assertIn("reparse point", output)
        self.assertIn("EHR_OUTPUTS_DIR=SENTINEL-UNCHANGED", output)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(os.listdir(target), [])

    def test_setup_refuses_a_runtime_junction_ancestor(self):
        """A real junction on the NTFS runtime root aborts setup untouched."""
        target = os.path.join(self.runtime_work, "junction-target")
        os.makedirs(target)
        link = os.path.join(self.runtime_work, "junction")
        make_junction(self, link, target)
        config = self.scratch_config()
        move_directory("models",
                       self.runtime_relative("junction", "models"))(config)
        self.write_config(config, "paths-setup.json")
        before = self.snapshot()
        output = self.run_setup(config)
        self.assertIn("SETUP=failed", output)
        self.assertIn("reparse point", output)
        self.assertIn("EHR_OUTPUTS_DIR=SENTINEL-UNCHANGED", output)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(os.listdir(target), [])


if __name__ == "__main__":
    unittest.main()
