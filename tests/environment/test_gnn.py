"""Synthetic checks for ehr_evidence_app.environment.gnn (Step 6).

The inspected interpreter is always simulated: a placeholder file named
python.exe with a synthetic Lib\\site-packages directory in a scratch
directory under the configured workspace test temp, with subprocess mocked to
return invented probe output. The fixed manifest and probe-temp locations
are patched into the same scratch directory. One case runs the fixed probe
source in the interpreter running these tests, against synthetic
distribution and module names only, to check the probe mechanics; no real
GNN interpreter or listed dependency is inspected.
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.environment import gnn as capture  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402

PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
INTERPRETER_DIR = "SYNTHINTERPDIR"
STARTUP = {"isolated": 1, "no_site": 1, "dont_write_bytecode": 1,
           "site_imported": False}

# Transcribed independently from the Step 6 assignment.
ASSIGNED_DOCUMENTED = {
    "python": "3.12.8", "pytorch": "2.11.0+cu128", "pyg": "2.8.0.post1",
    "numpy": "1.26.4", "pandas": "2.3.3", "scikit_learn": "1.7.2",
    "scipy": "1.17.1", "pykeen": "1.11.1",
}
# Invented observations that deliberately differ from the documented set.
SYNTHETIC_VERSIONS = {
    "pytorch": "9.1.0+cpu", "pyg": "9.2.0", "numpy": "9.3.0",
    "pandas": "9.4.0", "scikit_learn": "9.5.0", "scipy": "9.6.0",
    "pykeen": "9.7.0",
}
FINGERPRINT = (3, "0" * 64)
# Credential-style variable names, assembled from fragments so the source
# holds no secret-shaped assignment; fixture values are short and harmless.
DB_CREDENTIAL_NAME = "PG" + "PASS" + "WORD"
GIT_CREDENTIAL_NAME = "GITHUB_" + "TOK" + "EN"
CLOUD_CREDENTIAL_NAME = "AWS_" + "SEC" + "RET_ACCESS_KEY"


def configured_test_temp():
    """Return the configured workspace test scratch directory (workspace-only rules)."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def package_entry(key, version=None, **overrides):
    """Return one synthetic probe package entry for a component key."""
    version = version or SYNTHETIC_VERSIONS[key]
    module = {row[0]: row[3] for row in capture.COMPONENTS}[key]
    entry = {"distribution_status": "installed",
             "distribution_version": version}
    if module:
        entry.update({"import_status": "imported", "module_version": version})
    entry.update(overrides)
    return entry


def probe_result(python_version="9.0.1", **packages):
    """Return a synthetic probe result, with per-key package overrides."""
    result = {"probe_version": capture.PROBE_VERSION,
              "python": {"version": python_version,
                         "implementation": "CPython", "pointer_bits": 64,
                         "startup": dict(STARTUP)},
              "packages": {}}
    for key in SYNTHETIC_VERSIONS:
        result["packages"][key] = packages.get(key, package_entry(key))
    return result


def probe_stdout(result, before="", after=""):
    """Return probe stdout bytes holding one marked result line."""
    text = before + capture.PROBE_MARKER + json.dumps(result) + "\n" + after
    return text.encode("utf-8")


def completed(stdout, code=0):
    """Return a simulated finished probe process."""
    return subprocess.CompletedProcess(["python"], code, stdout, None)


def parsed_manifest(**packages):
    """Build a manifest from a synthetic probe result."""
    python, observed = capture.parse_probe_output(probe_stdout(
        probe_result(**packages)))
    return capture.build_manifest(python, observed, FINGERPRINT)


def reparse_lstat(target):
    """Return an lstat replacement reporting target as a reparse point."""
    real_lstat = os.lstat

    def fake_lstat(path, *args, **kwargs):
        """Report target as a reparse point; pass other paths through."""
        info = real_lstat(path, *args, **kwargs)
        if os.path.normcase(os.fspath(path)) == os.path.normcase(target):
            return SimpleNamespace(st_mode=info.st_mode,
                                   st_file_attributes=guard.REPARSE_POINT)
        return info

    return fake_lstat


class ScratchTestCase(unittest.TestCase):
    """Scratch directory with the fixed locations patched into it."""

    def setUp(self):
        """Create scratch, a placeholder environment and patched roots."""
        base = configured_test_temp()
        if not os.path.isdir(base):
            self.skipTest("configured test scratch is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.scratch = tempfile.mkdtemp(prefix="gnn-env-", dir=base)
        self.addCleanup(shutil.rmtree, self.scratch, True)
        self.env_root = os.path.join(self.scratch, INTERPRETER_DIR)
        os.makedirs(os.path.join(self.env_root, "Lib", "site-packages"))
        self.python = os.path.join(self.env_root, "python.exe")
        with open(self.python, "wb"):
            pass
        self.root = os.path.join(self.scratch, "environments")
        self.manifest = os.path.join(self.root, "gnn-environment.json")
        self.probe_temp = os.path.join(self.scratch, "probe-temp")
        patcher = mock.patch.multiple(capture, ENVIRONMENT_ROOT=self.root,
                                      MANIFEST_PATH=self.manifest,
                                      PROBE_TEMP_ROOT=self.probe_temp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_tool(self, stdout=None, side_effect=None, argv=None):
        """Run main() with a mocked probe; return (code, console, mock)."""
        if stdout is None:
            stdout = probe_stdout(probe_result())
        console = io.StringIO()
        with mock.patch.object(capture.subprocess, "run",
                               return_value=completed(stdout),
                               side_effect=side_effect) as run:
            with redirect_stdout(console), redirect_stderr(console):
                code = capture.main(argv or ["--python", self.python])
        return code, console.getvalue(), run

    def read_manifest(self):
        """Return the written manifest bytes and parsed JSON."""
        with open(self.manifest, "rb") as handle:
            payload = handle.read()
        return payload, json.loads(payload.decode("utf-8"))

    def assert_no_interpreter(self, text):
        """Fail if text names the interpreter path or its directory."""
        lowered = text.lower()
        self.assertNotIn(INTERPRETER_DIR.lower(), lowered)
        for variant in capture.path_variants(self.python):
            self.assertNotIn(variant, lowered)


class DocumentedReferenceTestCase(unittest.TestCase):
    """Documented versions are fixed, separate and carry the PyG note."""

    def test_documented_versions_match_the_assignment(self):
        """Every documented version equals the assigned master-brief value."""
        versions = capture.documented_reference()["versions"]
        self.assertEqual({key: value["version"]
                          for key, value in versions.items()},
                         ASSIGNED_DOCUMENTED)

    def test_pyg_discrepancy_is_recorded(self):
        """Both PyG spellings are kept and neither is called installed."""
        reference = capture.documented_reference()
        self.assertEqual(reference["provenance"], "documented_not_observed")
        [item] = reference["discrepancies"]
        self.assertEqual(item["component"], "pyg")
        self.assertEqual(item["documented_version"], "2.8.0.post1")
        self.assertEqual(item["install_example_version"], "2.8.0")
        self.assertIn("Neither is assumed to match the installed version",
                      item["note"])

    def test_fixed_locations_are_inside_the_private_root(self):
        """Manifest and probe temp lie in the private root, outside Git."""
        self.assertEqual(capture.MANIFEST_PATH, guard.PRIVATE_ROOT
                         + "\\outputs\\environments\\gnn-environment.json")
        self.assertEqual(capture.PROBE_TEMP_ROOT, guard.PRIVATE_ROOT
                         + "\\temp\\gnn-environment-probe")
        self.assertIsNone(capture.fixed_policy_problem())
        for path in (capture.MANIFEST_PATH, capture.PROBE_TEMP_ROOT):
            self.assertFalse(guard.is_within(path, REPO_ROOT))

    def test_only_core_libraries_are_imported(self):
        """Imports are limited to torch, torch_geometric and numpy."""
        modules = sorted(row[3] for row in capture.COMPONENTS if row[3])
        self.assertEqual(modules, ["numpy", "torch", "torch_geometric"])


class ParseTestCase(unittest.TestCase):
    """Strict parsing of the probe's stdout."""

    def test_valid_output_with_noise_lines(self):
        """Other stdout lines are ignored and fields are rebuilt."""
        python, packages = capture.parse_probe_output(probe_stdout(
            probe_result(), before="noise from an import\n",
            after="trailing noise\n"))
        self.assertEqual(python, {"version": "9.0.1",
                                  "version_withheld": False,
                                  "implementation": "CPython",
                                  "pointer_bits": 64,
                                  "interpreter_ran": True,
                                  "startup": STARTUP})
        self.assertEqual(packages["pyg"]["installed_version"], "9.2.0")
        self.assertEqual(packages["pyg"]["import_status"], "imported")
        self.assertEqual(packages["pandas"]["import_status"], "not_checked")
        self.assertFalse(packages["pandas"]["import_checked"])

    def test_marker_count_must_be_one(self):
        """No marked line or two marked lines both fail."""
        for stdout in (b"no result here\n",
                       probe_stdout(probe_result()) * 2):
            with self.assertRaises(capture.CaptureError):
                capture.parse_probe_output(stdout)

    def test_malformed_results_fail(self):
        """Invalid JSON, wrong version, missing or unknown entries fail."""
        missing = probe_result()
        del missing["packages"]["pykeen"]
        wrong_version = probe_result()
        wrong_version["probe_version"] = 99
        bad_status = probe_result(numpy=package_entry(
            "numpy", distribution_status="weird"))
        bad_import = probe_result(numpy=package_entry(
            "numpy", import_status="weird"))
        cases = [capture.PROBE_MARKER.encode() + b"{not json\n"]
        cases += [probe_stdout(item) for item in (missing, wrong_version,
                                                   bad_status, bad_import)]
        for stdout in cases:
            with self.assertRaises(capture.CaptureError):
                capture.parse_probe_output(stdout)

    def test_site_enabled_startup_is_refused(self):
        """A probe that ran with site, bytecode or without -I is refused."""
        for name, value in (("no_site", 0), ("site_imported", True),
                            ("isolated", 0), ("dont_write_bytecode", 0),
                            ("no_site", True)):
            result = probe_result()
            result["python"]["startup"][name] = value
            with self.assertRaises(capture.CaptureError):
                capture.parse_probe_output(probe_stdout(result))
        result = probe_result()
        del result["python"]["startup"]
        with self.assertRaises(capture.CaptureError):
            capture.parse_probe_output(probe_stdout(result))

    def test_oversized_output_fails(self):
        """Output beyond the bound is refused before parsing."""
        with self.assertRaises(capture.CaptureError):
            capture.parse_probe_output(
                b"x" * (capture.MAX_PROBE_OUTPUT_BYTES + 1))

    def test_non_version_text_is_withheld(self):
        """Paths, URLs and free text in version fields never pass through."""
        for text in ("C:\\secret\\env\\python.exe", "file:///C:/wheel.whl",
                     "1.0 local build", "1.0+local/path", "x" * 70):
            _, packages = capture.parse_probe_output(probe_stdout(
                probe_result(pandas=package_entry("pandas", text),
                             numpy=package_entry("numpy", text))))
            self.assertIsNone(packages["pandas"]["installed_version"])
            self.assertTrue(packages["pandas"]["installed_version_withheld"])
            self.assertIsNone(packages["numpy"]["module_version"])
            self.assertTrue(packages["numpy"]["module_version_withheld"])

    def test_error_types_and_unknown_fields_are_sanitised(self):
        """Error text is replaced and unknown probe fields are dropped."""
        entry = package_entry("pytorch", import_status="import_failed",
                              import_error_type="OSError: C:\\x\\y.dll",
                              interpreter_path="C:\\x\\python.exe")
        _, packages = capture.parse_probe_output(probe_stdout(
            probe_result(pytorch=entry)))
        record = packages["pytorch"]
        self.assertEqual(record["import_error_type"], "unrecognised")
        self.assertIsNone(record["module_version"])
        self.assertNotIn("interpreter_path", record)


class ManifestTestCase(unittest.TestCase):
    """Separation of documented and observed versions and status handling."""

    def test_observed_values_never_alter_documented_values(self):
        """Differing observations leave the documented section untouched."""
        manifest = parsed_manifest()
        self.assertEqual(manifest["documented_reference"],
                         capture.documented_reference())
        observed = manifest["observed"]
        self.assertEqual(observed["provenance"], "observed_in_interpreter")
        self.assertEqual(observed["python"]["version"], "9.0.1")
        for key, version in SYNTHETIC_VERSIONS.items():
            self.assertEqual(observed["packages"][key]["installed_version"],
                             version)
            comparison = manifest["comparison"][key]
            self.assertEqual(comparison["documented"],
                             ASSIGNED_DOCUMENTED[key])
            self.assertEqual(comparison["observed"], version)
            self.assertEqual(comparison["result"], "differs_from_documented")
        self.assertEqual(manifest["capture_status"], "complete")
        self.assertEqual(manifest["unavailable_or_incomplete"], [])
        self.assertEqual(manifest["environment_fingerprint"]["entry_count"],
                         FINGERPRINT[0])

    def test_matching_versions_are_reported_as_matching(self):
        """Equal observed and documented versions compare as matching."""
        manifest = parsed_manifest(
            pyg=package_entry("pyg", "2.8.0.post1"),
            pytorch=package_entry("pytorch", "2.11.0+cu128"))
        self.assertEqual(manifest["comparison"]["pyg"]["result"],
                         "matches_documented")
        self.assertEqual(manifest["comparison"]["pyg"][
            "install_example_result"], "differs_from_documented")
        self.assertEqual(manifest["comparison"]["pytorch"]["result"],
                         "matches_documented")

    def test_pyg_install_example_compared_separately(self):
        """An observed 2.8.0 matches the install example only."""
        comparison = parsed_manifest(pyg=package_entry("pyg", "2.8.0"))[
            "comparison"]["pyg"]
        self.assertEqual(comparison["result"], "differs_from_documented")
        self.assertEqual(comparison["install_example"], "2.8.0")
        self.assertEqual(comparison["install_example_result"],
                         "matches_documented")

    def test_missing_packages_are_not_substituted(self):
        """Absent packages are reported, never given documented versions."""
        manifest = parsed_manifest(
            pykeen={"distribution_status": "not_installed"},
            pyg={"distribution_status": "not_installed",
                 "import_status": "module_not_found",
                 "import_error_type": "ModuleNotFoundError"})
        for key in ("pykeen", "pyg"):
            package = manifest["observed"]["packages"][key]
            self.assertEqual(package["distribution_status"], "not_installed")
            self.assertIsNone(package["installed_version"])
            self.assertIsNone(package["module_version"])
            self.assertIsNone(manifest["comparison"][key]["observed"])
            self.assertEqual(manifest["comparison"][key]["result"],
                             "not_observed")
        self.assertEqual(manifest["capture_status"], "incomplete")
        self.assertEqual(manifest["unavailable_or_incomplete"], [
            {"component": "pyg", "reason": "module_not_found"},
            {"component": "pykeen", "reason": "not_installed"}])

    def test_import_failure_is_reported(self):
        """An installed but unimportable library is flagged by type only."""
        manifest = parsed_manifest(pytorch={
            "distribution_status": "installed",
            "distribution_version": "9.1.0+cpu",
            "import_status": "import_failed", "import_error_type": "OSError"})
        package = manifest["observed"]["packages"]["pytorch"]
        self.assertEqual(package["import_status"], "import_failed")
        self.assertEqual(package["import_error_type"], "OSError")
        self.assertEqual(manifest["comparison"]["pytorch"]["observed"],
                         "9.1.0+cpu")
        self.assertEqual(manifest["unavailable_or_incomplete"], [
            {"component": "pytorch", "reason": "import_failed"}])

    def test_module_version_fallback_and_consistency(self):
        """Missing metadata falls back to the module version, labelled."""
        manifest = parsed_manifest(
            numpy={"distribution_status": "not_installed",
                   "import_status": "imported", "module_version": "9.3.0"},
            pytorch=package_entry("pytorch", module_version="9.1.1"))
        numpy = manifest["comparison"]["numpy"]
        self.assertEqual((numpy["observed"], numpy["observed_from"]),
                         ("9.3.0", "module"))
        self.assertEqual(manifest["capture_status"], "complete")
        self.assertFalse(manifest["comparison"]["pytorch"][
            "module_matches_distribution"])

    def test_no_capture_time_or_interpreter_path_field(self):
        """The manifest records that the interpreter path is not stored."""
        manifest = parsed_manifest()
        self.assertFalse(manifest["interpreter"]["path_recorded"])
        self.assertNotIn("captured_utc", manifest)
        self.assertFalse(manifest["environment_changes"][
            "packages_installed_or_upgraded"])


class CompletenessTestCase(ScratchTestCase):
    """Every required version must be valid before COMPLETE is reported."""

    def test_each_gap_makes_the_capture_incomplete(self):
        """Missing, errored, withheld or absent versions are all gaps."""
        cases = [
            ({"python_version": "not a version"},
             {"component": "python", "reason": "version_withheld"}),
            ({"pytorch": {"distribution_status": "metadata_error",
                          "distribution_error_type": "ValueError",
                          "import_status": "imported"}},
             {"component": "pytorch", "reason": "version_unavailable"}),
            ({"pyg": package_entry("pyg", "C:\\bad\\path")},
             {"component": "pyg", "reason": "version_withheld"}),
            ({"pandas": {"distribution_status": "metadata_error",
                         "distribution_error_type": "OSError"}},
             {"component": "pandas", "reason": "metadata_error"}),
            ({"scipy": {"distribution_status": "installed"}},
             {"component": "scipy", "reason": "version_unavailable"}),
            ({"scikit_learn": package_entry("scikit_learn", "file:///x")},
             {"component": "scikit_learn", "reason": "version_withheld"}),
            ({"pykeen": {"distribution_status": "not_installed"}},
             {"component": "pykeen", "reason": "not_installed"}),
        ]
        for overrides, gap in cases:
            python_version = overrides.pop("python_version", "9.0.1")
            python, packages = capture.parse_probe_output(probe_stdout(
                probe_result(python_version, **overrides)))
            self.assertEqual(capture.incomplete_components(python, packages),
                             [gap])

    def test_import_success_with_one_valid_version_is_complete(self):
        """Metadata failure is covered by a valid module version."""
        manifest = parsed_manifest(numpy={
            "distribution_status": "metadata_error",
            "distribution_error_type": "ValueError",
            "import_status": "imported", "module_version": "9.3.0"})
        self.assertEqual(manifest["capture_status"], "complete")

    def test_complete_capture_exits_successfully(self):
        """All required versions give RESULT: COMPLETE and exit 0."""
        code, console, _ = self.run_tool()
        self.assertEqual(code, 0)
        self.assertTrue(console.rstrip().endswith("RESULT: COMPLETE"))

    def test_incomplete_capture_fails_and_is_recorded(self):
        """A gap gives RESULT: INCOMPLETE, exit 1 and an explicit list."""
        code, console, _ = self.run_tool(stdout=probe_stdout(probe_result(
            pykeen={"distribution_status": "not_installed"})))
        self.assertEqual(code, 1)
        self.assertTrue(console.rstrip().endswith("RESULT: INCOMPLETE"))
        self.assertNotIn("RESULT: COMPLETE", console)
        self.assertIn("Incomplete       : pykeen (not_installed)", console)
        _, manifest = self.read_manifest()
        self.assertEqual(manifest["capture_status"], "incomplete")
        self.assertEqual(manifest["unavailable_or_incomplete"],
                         [{"component": "pykeen", "reason": "not_installed"}])
        self.assertIsNone(manifest["comparison"]["pykeen"]["observed"])


class InterpreterArgumentTestCase(ScratchTestCase):
    """The required --python argument and its validation."""

    def test_python_argument_is_required_without_default(self):
        """Omitting --python fails in argparse before anything runs."""
        console = io.StringIO()
        with mock.patch.object(capture.subprocess, "run") as run:
            with redirect_stdout(console), redirect_stderr(console):
                with self.assertRaises(SystemExit):
                    capture.main([])
        run.assert_not_called()
        self.assertIsNone(capture.build_parser()._option_string_actions[
            "--python"].default)

    def test_invalid_interpreter_values_are_refused_without_echo(self):
        """Bad values fail before the probe and are never repeated."""
        folder = os.path.join(self.env_root, "python.exe.d")
        os.makedirs(os.path.join(folder, "python.exe"))
        other = os.path.join(self.env_root, "tool.exe")
        with open(other, "wb"):
            pass
        bare = os.path.join(self.scratch, "BARE" + INTERPRETER_DIR)
        os.makedirs(bare)
        with open(os.path.join(bare, "python.exe"), "wb"):
            pass
        cases = [INTERPRETER_DIR + "\\python.exe",
                 self.python.replace("\\", "/"),
                 os.path.join(self.env_root, "missing", "python.exe"),
                 other, os.path.join(folder, "python.exe"),
                 os.path.join(bare, "python.exe"),
                 "\\\\server\\share\\" + INTERPRETER_DIR + "\\python.exe"]
        for value in cases:
            code, console, run = self.run_tool(argv=["--python", value])
            self.assertEqual(code, 1, value)
            run.assert_not_called()
            self.assertNotIn(INTERPRETER_DIR.lower(), console.lower())
        self.assertFalse(os.path.lexists(self.root))
        self.assertFalse(os.path.lexists(self.probe_temp))

    def test_reparse_components_are_refused_generically(self):
        """A reparse leaf, parent or site-packages is refused, unnamed."""
        targets = [self.python, self.env_root, self.scratch,
                   os.path.join(self.env_root, "Lib", "site-packages")]
        for target in targets:
            with mock.patch.object(capture.os, "lstat",
                                   side_effect=reparse_lstat(target)):
                problem = capture.interpreter_problem(self.python)
            self.assertIn("reparse point", problem, target)
            self.assertIn("value withheld", problem)
            self.assertNotIn(INTERPRETER_DIR.lower(), problem.lower())

    def test_reparse_drive_root_is_refused(self):
        """The drive root is checked first and a reparse root refused."""
        drive_root = self.python[:3]
        with mock.patch.object(capture.os, "lstat",
                               side_effect=reparse_lstat(drive_root)):
            self.assertEqual(capture.walk_problem(self.python, "file"),
                             "has a drive root that is a link or reparse "
                             "point")
            problem = capture.interpreter_problem(self.python)
            code, console, run = self.run_tool()
        self.assertIn("drive root", problem)
        self.assertIn("value withheld", problem)
        self.assertNotIn(INTERPRETER_DIR, problem)
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assert_no_interpreter(console)

    def test_parent_reparse_blocks_the_capture(self):
        """A reparse-point parent stops the tool before the probe."""
        with mock.patch.object(capture.os, "lstat",
                               side_effect=reparse_lstat(self.env_root)):
            code, console, run = self.run_tool()
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assert_no_interpreter(console)

    def test_environment_overlapping_outputs_is_refused(self):
        """The environment may not contain the manifest or probe temp."""
        with mock.patch.object(capture, "PROBE_TEMP_ROOT",
                               os.path.join(self.env_root, "probe")):
            self.assertIn("overlaps", capture.interpreter_problem(
                self.python))


class ProbeExecutionTestCase(ScratchTestCase):
    """The probe launch and its failure handling, with subprocess mocked."""

    def test_probe_runs_isolated_without_site(self):
        """The interpreter runs -I -S -B -c in the probe temp, no shell."""
        code, _, run = self.run_tool()
        self.assertEqual(code, 0)
        run.assert_called_once()
        args, kwargs = run.call_args
        command = args[0]
        self.assertEqual(command[:5],
                         [self.python, "-I", "-S", "-B", "-c"])
        self.assertEqual(command[5], capture.PROBE_SOURCE)
        spec = json.loads(command[6])
        self.assertEqual([row[1] for row in spec["components"]],
                         [row[2] for row in capture.COMPONENTS])
        self.assertEqual(spec["site_packages"], os.path.join(
            self.env_root, "Lib", "site-packages"))
        self.assertFalse(kwargs["shell"])
        self.assertEqual(kwargs["cwd"], self.probe_temp)
        self.assertTrue(os.path.isdir(self.probe_temp))
        self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["timeout"], capture.PROBE_TIMEOUT_SECONDS)

    def test_probe_source_does_not_process_site(self):
        """The probe adds one path and never calls site or reads .pth."""
        source = capture.PROBE_SOURCE
        self.assertNotIn("import site", source)
        self.assertNotIn("addsitedir", source)
        self.assertNotIn(".pth", source)
        self.assertEqual(source.count("sys.path.append("), 1)

    def test_minimal_environment_drops_inherited_values(self):
        """Only runtime, PATH and routed temp variables are passed on."""
        system_root = os.path.join(self.scratch, "SysRoot")
        os.makedirs(system_root)
        inherited = {
            "SYSTEMROOT": system_root, "WINDIR": "C:\\SYNTHWINDIR",
            "PATHEXT": ".EXE",
            "NUMBER_OF_PROCESSORS": "8", "PATH": "C:\\SYNTHINHERITED\\bin",
            DB_CREDENTIAL_NAME: "Zq1", "PGUSER": "Zq2",
            GIT_CREDENTIAL_NAME: "Zq3", "GIT_DIR": "C:\\x",
            CLOUD_CREDENTIAL_NAME: "Zq4",
            "PYTHONPATH": "C:\\SYNTHPY", "PYTHONSTARTUP": "C:\\x.py",
            "HTTPS_PROXY": "http://proxy", "TEMP": "C:\\SYNTHTEMP",
            "USERPROFILE": "C:\\SYNTHHOME",
            "APPDATA": "C:\\SYNTHROAMING", "LOCALAPPDATA": "C:\\SYNTHLOCAL",
            "RANDOM_SETTING": "Zq5",
        }
        env = capture.probe_environment(self.python, self.probe_temp,
                                        inherited)
        allowed = (set(capture.RUNTIME_VARIABLES)
                   | set(capture.ROUTED_VARIABLES) | {"PATH", "USERNAME"})
        self.assertLessEqual(set(env), allowed)
        for name in (DB_CREDENTIAL_NAME, GIT_CREDENTIAL_NAME,
                     CLOUD_CREDENTIAL_NAME, "PGUSER", "GIT_DIR",
                     "PYTHONPATH", "PYTHONSTARTUP", "HTTPS_PROXY",
                     "RANDOM_SETTING"):
            self.assertNotIn(name, env)
        for name in capture.ROUTED_VARIABLES:
            self.assertEqual(env[name], self.probe_temp)
        for name in ("APPDATA", "LOCALAPPDATA"):
            self.assertEqual(env[name], self.probe_temp)
        self.assertEqual(env["SYSTEMROOT"], system_root)
        self.assertEqual(env["WINDIR"], system_root)
        self.assertEqual(env["NUMBER_OF_PROCESSORS"], "8")
        entries = env["PATH"].split(";")
        self.assertEqual(entries[0], self.env_root)
        self.assertIn(os.path.join(self.env_root, "Library", "bin"), entries)
        self.assertIn(os.path.join(system_root, "System32"), entries)
        self.assertEqual(len(entries), len(capture.ENVIRONMENT_PATH_DIRS)
                         + len(capture.SYSTEM_PATH_DIRS))
        text = json.dumps(env)
        for value in ("SYNTHINHERITED", "Zq", "SYNTHPY", "proxy",
                      "SYNTHTEMP", "SYNTHHOME", "SYNTHROAMING",
                      "SYNTHLOCAL", "SYNTHWINDIR"):
            self.assertNotIn(value, text)

    def test_run_passes_the_minimal_environment(self):
        """The launched probe receives the minimal environment."""
        with mock.patch.dict(os.environ, {DB_CREDENTIAL_NAME: "Zq6",
                                          "PYTHONPATH": "C:\\SYNTHPY",
                                          "APPDATA": "C:\\SYNTHROAMING",
                                          "LOCALAPPDATA": "C:\\SYNTHLOCAL"}):
            code, console, run = self.run_tool()
        self.assertEqual(code, 0)
        env = run.call_args[1]["env"]
        self.assertNotIn(DB_CREDENTIAL_NAME, env)
        self.assertNotIn("PYTHONPATH", env)
        for name in ("TEMP", "APPDATA", "LOCALAPPDATA"):
            self.assertEqual(env[name], self.probe_temp)
        self.assertNotIn("SYNTH", json.dumps(env).replace(
            INTERPRETER_DIR, ""))
        self.assertNotIn("Zq6", console)

    def test_child_uses_the_fixed_synthetic_username(self):
        """USERNAME is the generic value, never the parent's identity."""
        system_root = os.path.join(self.scratch, "SysRoot")
        os.makedirs(system_root)
        for inherited in ({"SYSTEMROOT": system_root},
                          {"SYSTEMROOT": system_root,
                           "USERNAME": "ZqParentIdentity",
                           "USER": "ZqParentUser", "LOGNAME": "ZqParentLog",
                           "LNAME": "ZqParentL"}):
            env = capture.probe_environment(self.python, self.probe_temp,
                                            inherited)
            self.assertEqual(env["USERNAME"], "gnn-capture")
            self.assertEqual(env["USERNAME"], capture.SYNTHETIC_USERNAME)
            for name in ("USER", "LOGNAME", "LNAME"):
                self.assertNotIn(name, env)
            self.assertNotIn("ZqParent", json.dumps(env))

    def test_parent_username_and_synthetic_value_are_not_output(self):
        """Neither identity reaches the console or the manifest."""
        with mock.patch.dict(os.environ, {"USERNAME": "ZqParentIdentity"}):
            code, console, run = self.run_tool()
        self.assertEqual(code, 0)
        env = run.call_args[1]["env"]
        self.assertEqual(env["USERNAME"], capture.SYNTHETIC_USERNAME)
        self.assertNotIn("ZqParentIdentity", json.dumps(env))
        payload, _ = self.read_manifest()
        for text in (console, payload.decode("utf-8")):
            self.assertNotIn("ZqParentIdentity", text)
            self.assertNotIn(capture.SYNTHETIC_USERNAME, text)

    def test_semicolon_components_are_refused_without_echo(self):
        """No path with a PATH separator reaches the probe environment."""
        semicolon_dir = os.path.join(self.scratch, "SEMI;" + INTERPRETER_DIR)
        os.makedirs(os.path.join(semicolon_dir, "Lib", "site-packages"))
        semicolon_python = os.path.join(semicolon_dir, "python.exe")
        with open(semicolon_python, "wb"):
            pass
        environ = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}
        for python, temp in ((semicolon_python, self.probe_temp),
                             (self.python, self.probe_temp + ";SEMI")):
            with self.assertRaises(capture.CaptureError) as caught:
                capture.probe_environment(python, temp, environ)
            message = str(caught.exception)
            self.assertIn("semicolon", message)
            self.assertNotIn("SEMI", message)
            self.assertNotIn(INTERPRETER_DIR, message)
        self.assertIn("semicolon", capture.walk_problem(semicolon_python,
                                                        "file"))
        problem = capture.interpreter_problem(semicolon_python)
        self.assertIn("semicolon", problem)
        self.assertNotIn(INTERPRETER_DIR, problem)
        code, console, run = self.run_tool(
            argv=["--python", semicolon_python])
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertNotIn("SEMI", console)
        self.assertNotIn(INTERPRETER_DIR, console)

    def test_unsafe_systemroot_is_refused_without_fallback(self):
        """A missing or unsafe SYSTEMROOT fails; C:\\Windows is not used."""
        a_file = os.path.join(self.scratch, "SYNTHSYSFILE")
        with open(a_file, "wb"):
            pass
        semicolon = os.path.join(self.scratch, "SYNTHSYS;ROOT")
        linked = os.path.join(self.scratch, "SYNTHSYSLINK")
        for path in (semicolon, linked):
            os.makedirs(path)
        values = [None, "", "SYNTHSYSREL", "C:/SYNTHSYSFWD",
                  os.path.join(self.scratch, "SYNTHSYSMISSING"), a_file,
                  semicolon, linked, "\\\\server\\share\\SYNTHSYSUNC"]
        for value in values:
            environ = {} if value is None else {"SYSTEMROOT": value}
            with mock.patch.object(capture.os, "lstat",
                                   side_effect=reparse_lstat(linked)):
                with self.assertRaises(capture.CaptureError) as caught:
                    capture.probe_environment(self.python, self.probe_temp,
                                              environ)
            message = str(caught.exception)
            self.assertIn("SYSTEMROOT", message, value)
            self.assertIn("value withheld", message)
            self.assertNotIn("SYNTHSYS", message)
            self.assertNotIn("Windows\\", message)
        with mock.patch.dict(os.environ, {"SYSTEMROOT": "SYNTHSYSREL"}):
            code, console, run = self.run_tool()
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertIn("SYSTEMROOT", console)
        self.assertNotIn("SYNTHSYS", console)
        self.assertFalse(os.path.lexists(self.manifest))

    def test_unsafe_probe_temp_is_refused(self):
        """The probe temp must be inside the private root and not a link."""
        with mock.patch.object(capture, "PROBE_TEMP_ROOT",
                               "D:\\temp\\gnn-environment-probe"):
            self.assertIsNotNone(capture.fixed_policy_problem())
        os.makedirs(self.probe_temp)
        with mock.patch.object(guard.os, "lstat",
                               side_effect=reparse_lstat(self.probe_temp)):
            with self.assertRaises(capture.CaptureError):
                capture.prepare_probe_temp()
            code, _, run = self.run_tool()
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertFalse(os.path.lexists(self.manifest))

    def test_probe_failures_write_nothing_and_hide_the_path(self):
        """Start errors, timeouts and non-zero exits are reported safely."""
        start = FileNotFoundError(2, "No such file", self.python)
        timeout = subprocess.TimeoutExpired([self.python], 1)
        for side_effect in (start, timeout):
            code, console, _ = self.run_tool(side_effect=side_effect)
            self.assertEqual(code, 1)
            self.assert_no_interpreter(console)
        with mock.patch.object(capture.subprocess, "run",
                               return_value=completed(b"", code=3)):
            console = io.StringIO()
            with redirect_stdout(console):
                self.assertEqual(capture.main(["--python", self.python]), 1)
        self.assertIn("exited with code 3", console.getvalue())
        self.assertFalse(os.path.lexists(self.manifest))


class FingerprintTestCase(ScratchTestCase):
    """The environment tree fingerprint around the probe."""

    def populate(self):
        """Add synthetic files to the placeholder environment."""
        package = os.path.join(self.env_root, "Lib", "site-packages", "pkg")
        os.makedirs(package)
        self.module = os.path.join(package, "module.py")
        with open(self.module, "w", encoding="utf-8") as handle:
            handle.write("synthetic = 1\n")

    def test_stable_tree_gives_the_same_fingerprint(self):
        """Repeated fingerprints of an unchanged tree are equal."""
        self.populate()
        first = capture.environment_fingerprint(self.env_root)
        self.assertEqual(first, capture.environment_fingerprint(
            self.env_root))
        count, digest = first
        self.assertEqual(count, 5)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_changes_alter_the_fingerprint(self):
        """Content size, mtime, new and removed entries all change it."""
        self.populate()
        base = capture.environment_fingerprint(self.env_root)
        with open(self.module, "a", encoding="utf-8") as handle:
            handle.write("more = 2\n")
        grown = capture.environment_fingerprint(self.env_root)
        self.assertNotEqual(grown, base)
        info = os.stat(self.module)
        os.utime(self.module, ns=(info.st_atime_ns,
                                  info.st_mtime_ns + 5 * 10 ** 9))
        touched = capture.environment_fingerprint(self.env_root)
        self.assertNotEqual(touched, grown)
        extra = os.path.join(self.env_root, "added.txt")
        with open(extra, "wb"):
            pass
        added = capture.environment_fingerprint(self.env_root)
        self.assertEqual(added[0], touched[0] + 1)
        os.remove(extra)
        self.assertNotEqual(capture.environment_fingerprint(self.env_root),
                            added)

    def test_content_change_with_same_size_and_mtime_alters_it(self):
        """File contents are hashed, not just names and metadata."""
        self.populate()
        before = capture.environment_fingerprint(self.env_root)
        original = os.lstat(self.module)
        with open(self.module, "w", encoding="utf-8") as handle:
            handle.write("synthetic = 2\n")
        os.utime(self.module, ns=(original.st_atime_ns,
                                  original.st_mtime_ns))
        restored = os.lstat(self.module)
        self.assertEqual((restored.st_size, restored.st_mtime_ns),
                         (original.st_size, original.st_mtime_ns))
        after = capture.environment_fingerprint(self.env_root)
        self.assertEqual(after[0], before[0])
        self.assertNotEqual(after[1], before[1])

    def test_change_while_hashing_fails_without_names(self):
        """A file whose metadata moves during its hash fails closed."""
        self.populate()
        real_lstat = os.lstat
        seen = []

        def moving_lstat(path, *args, **kwargs):
            """Report a later modification time on the second look."""
            info = real_lstat(path, *args, **kwargs)
            if os.path.normcase(os.fspath(path)) != os.path.normcase(
                    self.module):
                return info
            seen.append(path)
            if len(seen) == 1:
                return info
            return SimpleNamespace(
                st_dev=info.st_dev, st_ino=info.st_ino,
                st_mode=info.st_mode, st_size=info.st_size,
                st_mtime_ns=info.st_mtime_ns + 1,
                st_file_attributes=getattr(info, "st_file_attributes", 0))

        with mock.patch.object(capture.os, "lstat",
                               side_effect=moving_lstat):
            with self.assertRaises(capture.CaptureError) as caught:
                capture.environment_fingerprint(self.env_root)
        message = str(caught.exception)
        self.assertIn("changed while it was being hashed", message)
        self.assertNotIn("module", message)
        self.assertNotIn(INTERPRETER_DIR, message)

    def test_unreadable_file_blocks_the_manifest(self):
        """An unreadable file fails closed, reported by type only."""
        self.populate()
        denied = PermissionError(13, "denied", self.module)
        with mock.patch.object(capture, "open", create=True,
                               side_effect=denied):
            with self.assertRaises(capture.CaptureError) as caught:
                capture.environment_fingerprint(self.env_root)
            code, console, run = self.run_tool()
        self.assertIn("(PermissionError)", str(caught.exception))
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertIn("could not be fingerprinted", console)
        self.assertNotIn("module", console)
        self.assert_no_interpreter(console)
        self.assertFalse(os.path.lexists(self.manifest))

    def test_change_during_probe_blocks_the_manifest(self):
        """A probe that alters the environment leaves no manifest."""
        def altering_probe(*args, **kwargs):
            """Simulate a probe that writes into the environment."""
            with open(os.path.join(self.env_root, "written.pyc"), "wb"):
                pass
            return completed(probe_stdout(probe_result()))

        code, console, _ = self.run_tool(side_effect=altering_probe)
        self.assertEqual(code, 1)
        self.assertIn("changed during the probe", console)
        self.assert_no_interpreter(console)
        self.assertFalse(os.path.lexists(self.manifest))

    def test_unreadable_environment_blocks_the_manifest(self):
        """A fingerprint failure is reported by type, without paths."""
        with mock.patch.object(capture.os, "scandir",
                               side_effect=PermissionError(
                                   13, "denied", self.env_root)):
            code, console, run = self.run_tool()
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertIn("could not be fingerprinted (PermissionError)", console)
        self.assert_no_interpreter(console)
        self.assertFalse(os.path.lexists(self.manifest))

    def assert_reparse_blocks(self, console, run):
        """Check that a reparse entry stopped the tool before the probe."""
        run.assert_not_called()
        self.assertIn("reparse point", console)
        self.assertIn("nothing was written", console)
        self.assertNotIn("LINKEDENTRY", console)
        self.assert_no_interpreter(console)
        self.assertFalse(os.path.lexists(self.manifest))
        self.assertFalse(os.path.lexists(self.root))

    def test_simulated_reparse_entry_blocks_the_manifest(self):
        """A reparse entry deep in the tree fails closed, unnamed."""
        self.populate()
        linked = os.path.join(os.path.dirname(self.module), "LINKEDENTRY")
        os.makedirs(linked)
        real_scandir = os.scandir

        class Entry:
            """A directory entry that reports LINKEDENTRY as a reparse."""

            def __init__(self, entry):
                """Wrap a real directory entry."""
                self.entry = entry
                self.name = entry.name

            def stat(self, follow_symlinks=True):
                """Return lstat-style information, faked for LINKEDENTRY."""
                info = self.entry.stat(follow_symlinks=False)
                if self.name != "LINKEDENTRY":
                    return info
                return SimpleNamespace(
                    st_mode=info.st_mode, st_size=info.st_size,
                    st_mtime_ns=info.st_mtime_ns,
                    st_file_attributes=guard.REPARSE_POINT)

        @contextlib.contextmanager
        def fake_scandir(path):
            """List a directory through wrapped entries."""
            with real_scandir(path) as listing:
                yield [Entry(entry) for entry in listing]

        with self.assertRaises(capture.CaptureError):
            with mock.patch.object(capture.os, "scandir",
                                   side_effect=fake_scandir):
                capture.environment_fingerprint(self.env_root)
        with mock.patch.object(capture.os, "scandir",
                               side_effect=fake_scandir):
            code, console, run = self.run_tool()
        self.assertEqual(code, 1)
        self.assert_reparse_blocks(console, run)

    def test_real_junction_in_the_tree_blocks_the_manifest(self):
        """A real directory junction in the tree fails closed."""
        target = os.path.join(self.scratch, "junction-target")
        os.makedirs(target)
        link = os.path.join(self.env_root, "Lib", "LINKEDENTRY")
        try:
            import _winapi
            _winapi.CreateJunction(target, link)
        except (ImportError, AttributeError, OSError) as error:
            self.skipTest("directory junctions are unavailable here: %s"
                          % type(error).__name__)
        self.addCleanup(os.rmdir, link)  # removes the junction only
        code, console, run = self.run_tool()
        self.assertEqual(code, 1)
        self.assert_reparse_blocks(console, run)

    def test_fingerprint_output_has_no_names(self):
        """Only the count and digest leave the fingerprint function."""
        self.populate()
        result = capture.environment_fingerprint(self.env_root)
        self.assertEqual(len(result), 2)
        self.assertNotIn("module", json.dumps(result))
        code, console, _ = self.run_tool()
        self.assertEqual(code, 0)
        payload, manifest = self.read_manifest()
        self.assertEqual(manifest["environment_fingerprint"]["entry_count"],
                         result[0])
        self.assertEqual(manifest["environment_fingerprint"]["sha256"],
                         result[1])
        for text in (payload.decode("utf-8"), console):
            self.assertNotIn("module.py", text)
            self.assertNotIn("site-packages\\\\pkg", text)


class OutputTestCase(ScratchTestCase):
    """Manifest location, idempotence, revalidation and path safety."""

    def test_capture_writes_the_fixed_manifest(self):
        """A valid capture writes only the fixed manifest, once."""
        code, console, _ = self.run_tool()
        self.assertEqual(code, 0)
        self.assertEqual(os.listdir(self.root), ["gnn-environment.json"])
        payload, manifest = self.read_manifest()
        self.assertEqual(manifest["record_type"], "gnn_environment_capture")
        self.assertIn("Manifest state   : written", console)
        code, console, _ = self.run_tool()
        self.assertEqual(code, 0)
        self.assertIn("unchanged", console)
        self.assertEqual(self.read_manifest()[0], payload)

    def test_destination_is_revalidated_around_each_operation(self):
        """Checks precede the read and follow directory creation."""
        events = []
        real_problem = capture.output_problem
        real_outcome = capture.existing_outcome
        real_write = capture.write_record
        real_makedirs = os.makedirs

        def checked():
            """Record a destination check."""
            events.append("check")
            return real_problem()

        def outcome(path, payload):
            """Record examination of an existing manifest."""
            events.append("read")
            return real_outcome(path, payload)

        def makedirs(path, *args, **kwargs):
            """Record creation of the manifest directory."""
            if os.path.normcase(path) == os.path.normcase(self.root):
                events.append("makedirs")
            return real_makedirs(path, *args, **kwargs)

        def write(path, payload):
            """Record the atomic write."""
            events.append("write")
            return real_write(path, payload)

        with mock.patch.multiple(capture, output_problem=checked,
                                 existing_outcome=outcome,
                                 write_record=write), \
                mock.patch.object(capture.os, "makedirs",
                                  side_effect=makedirs):
            code, _, _ = self.run_tool()
        self.assertEqual(code, 0)
        self.assertEqual(events, ["check", "check", "read", "makedirs",
                                  "check", "write"])

    def test_failed_recheck_after_directory_creation_writes_nothing(self):
        """A destination that fails after mkdir blocks the write."""
        results = iter([None, None, "synthetic post-directory problem"])
        with mock.patch.object(capture, "output_problem",
                               side_effect=lambda: next(results)):
            code, console, _ = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("synthetic post-directory problem", console)
        self.assertTrue(os.path.isdir(self.root))
        self.assertFalse(os.path.lexists(self.manifest))
        self.assertFalse(os.path.lexists(self.manifest + capture.PARTIAL))

    def test_failed_recheck_before_read_examines_nothing(self):
        """A destination that fails before the read is not examined."""
        results = iter([None, "synthetic pre-read problem"])
        with mock.patch.object(capture, "output_problem",
                               side_effect=lambda: next(results)), \
                mock.patch.object(capture, "existing_outcome") as outcome:
            code, console, _ = self.run_tool()
        self.assertEqual(code, 1)
        outcome.assert_not_called()
        self.assertIn("synthetic pre-read problem", console)
        self.assertFalse(os.path.lexists(self.root))

    def test_different_existing_manifest_is_not_overwritten(self):
        """A differing manifest is refused and left byte-identical."""
        self.assertEqual(self.run_tool()[0], 0)
        payload = self.read_manifest()[0]
        code, console, _ = self.run_tool(stdout=probe_stdout(probe_result(
            python_version="9.0.2")))
        self.assertEqual(code, 1)
        self.assertIn("left unchanged", console)
        self.assertEqual(self.read_manifest()[0], payload)

    def test_stale_partial_is_refused(self):
        """A leftover .partial file blocks the capture before the probe."""
        os.makedirs(self.root)
        with open(self.manifest + capture.PARTIAL, "wb"):
            pass
        code, console, run = self.run_tool()
        self.assertEqual(code, 1)
        run.assert_not_called()
        self.assertIn("interrupted capture", console)
        self.assertFalse(os.path.lexists(self.manifest))

    def test_unsafe_locations_are_refused_without_fallback(self):
        """Other drives, the repository and misplaced manifests fail."""
        repository = os.path.join(REPO_ROOT, "outputs", "environments")
        cases = [
            ("D:\\outputs\\environments",
             "D:\\outputs\\environments\\gnn-environment.json"),
            (guard.POLICY_ANCHOR + "\\environments",
             guard.POLICY_ANCHOR + "\\environments\\gnn-environment.json"),
            (self.root, self.root + "\\nested\\gnn-environment.json"),
            (self.root + "\\..\\escape",
             self.root + "\\..\\escape\\gnn-environment.json"),
        ]
        for root, manifest in cases:
            with mock.patch.multiple(capture, ENVIRONMENT_ROOT=root,
                                     MANIFEST_PATH=manifest):
                self.assertIsNotNone(capture.fixed_policy_problem(), root)
        with mock.patch.multiple(capture, ENVIRONMENT_ROOT=repository,
                                 MANIFEST_PATH=os.path.join(
                                     repository, "gnn-environment.json")):
            self.assertIsNotNone(capture.output_problem())
            code, _, run = self.run_tool()
            self.assertEqual(code, 1)
            run.assert_not_called()
        self.assertFalse(os.path.lexists(repository))

    def test_simulated_reparse_destination_is_refused(self):
        """A reparse point on the destination path blocks the capture."""
        os.makedirs(self.root)
        with mock.patch.object(guard.os, "lstat",
                               side_effect=reparse_lstat(self.root)):
            self.assertIn("reparse point", capture.output_problem())


class LeakTestCase(ScratchTestCase):
    """The interpreter path never reaches the console or the manifest."""

    def test_console_and_manifest_omit_the_interpreter(self):
        """Neither output names the interpreter path or directory."""
        code, console, _ = self.run_tool()
        self.assertEqual(code, 0)
        self.assert_no_interpreter(console)
        payload, _ = self.read_manifest()
        self.assert_no_interpreter(payload.decode("utf-8"))

    def test_path_smuggled_in_probe_output_is_dropped(self):
        """Paths placed in probe fields are withheld, not recorded."""
        smuggled = package_entry("scipy", self.python,
                                 distribution_error_type=self.python,
                                 location=self.python)
        result = probe_result(scipy=smuggled)
        result["python"]["implementation"] = self.python
        result["python"]["executable"] = self.python
        result["python"]["startup"]["path"] = self.python
        code, console, _ = self.run_tool(stdout=probe_stdout(result))
        self.assertEqual(code, 1)  # the withheld SciPy version is a gap
        self.assertIn("RESULT: INCOMPLETE", console)
        self.assert_no_interpreter(console)
        self.assert_no_interpreter(self.read_manifest()[0].decode("utf-8"))

    def test_leak_check_catches_every_spelling_and_fails_closed(self):
        """Backslash, forward-slash and JSON-escaped spellings are caught."""
        for text in (self.python, self.python.replace("\\", "/"),
                     os.path.dirname(self.python).upper(),
                     json.dumps(self.python)):
            self.assertIsNotNone(capture.leak_problem(
                ("{%s}" % text).encode("utf-8"), self.python))
        original = capture.build_manifest

        def leaking(python, packages, fingerprint):
            """Return a manifest that wrongly embeds the interpreter path."""
            manifest = original(python, packages, fingerprint)
            manifest["limitations"].append(self.python)
            return manifest

        with mock.patch.object(capture, "build_manifest", leaking):
            code, console, _ = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("interpreter path", console)
        self.assert_no_interpreter(console)
        self.assertFalse(os.path.lexists(self.manifest))


class ProbeMechanicsTestCase(ScratchTestCase):
    """The fixed probe source, run against synthetic names only."""

    def test_probe_source_runs_without_site(self):
        """The probe starts -I -S -B; absent names are reported absent."""
        runner = sys.executable
        if not os.path.isdir(capture.site_packages(runner)):
            self.skipTest("the test interpreter has no Lib\\site-packages "
                          "beside it")
        components = (
            ("present", "Present", "synthetic-absent-distribution-a", "json",
             "0.0"),
            ("absent", "Absent", "synthetic-absent-distribution-b",
             "synthetic_absent_module_b", "0.0"),
            ("metadata", "Metadata", "synthetic-absent-distribution-c", None,
             "0.0"),
        )
        with mock.patch.object(capture, "COMPONENTS", components):
            python, packages = capture.parse_probe_output(
                capture.run_probe(runner))
        self.assertEqual(python["version"], "%d.%d.%d" % sys.version_info[:3])
        self.assertEqual(python["startup"], STARTUP)
        self.assertEqual(packages["present"]["distribution_status"],
                         "not_installed")
        self.assertEqual(packages["present"]["import_status"], "imported")
        self.assertEqual(packages["absent"]["import_status"],
                         "module_not_found")
        self.assertEqual(packages["absent"]["import_error_type"],
                         "ModuleNotFoundError")
        self.assertEqual(packages["metadata"]["import_status"],
                         "not_checked")


if __name__ == "__main__":
    unittest.main()
