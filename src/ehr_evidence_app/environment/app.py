#!/usr/bin/env python3
"""Check the downstream application environment with a CPU-only smoke run (Step 7).

The tool uses the Python standard library only (tomllib needs
Python 3.11 or later) and can be run by any such interpreter. It checks:

1. pyproject.toml declares the planned app/runtime/test/ClearML packages
   and none of the excluded GNN, CUDA or LLM runtime packages;
2. requirements/app.lock.txt exists, pins every package exactly
   (name==version) and contains no excluded package; a missing lock is
   reported as "lock resolution pending";
3. the application environment at the configured UV_PROJECT_ENVIRONMENT
   (config/paths.json key uv_project_env, under its configured fixed root:
   the runtime root) is a Python 3.12.8 virtual environment without
   system site-packages;
4. a smoke probe run by that environment's interpreter imports the
   application package from src and the selected dependencies, whose
   installed versions must equal the lock pins, and confirms that torch,
   torch_geometric and the other excluded modules are neither loaded nor
   installed and that no upstream GNN directory is on sys.path.

The probe runs once, without a shell, with -I -B (isolated, no bytecode
written) and a minimal environment: Windows runtime variables, a PATH of
the environment's Scripts directory and the system directories, and temp,
home and cache variables routed to the configured temp directory on the
runtime root (temp\\app-environment-smoke), which is also its working
directory.
CLEARML_OFFLINE_MODE is 1 and CLEARML_CONFIG_FILE names a file that must not
exist, so no ClearML server or credential is used; inherited credentials,
tokens and Python settings are not passed on. Nothing is installed, no
upstream code, model, weight or patient data is loaded and no service is
contacted. The tool writes nothing except the probe scratch directory and
prints RESULT: PASS (exit 0) or RESULT: FAIL (exit 1).

Usage:
    python -m ehr_evidence_app.environment.app
"""

from __future__ import annotations

import json
import ntpath
import os
import re
import subprocess
import sys

from ehr_evidence_app.storage import policy as guard

REPO_ROOT = guard.REPO_ROOT
PATHS_CONFIG = os.path.join(REPO_ROOT, "config", "paths.json")
UPSTREAM_CONFIG = os.path.join(REPO_ROOT, "config", "upstream_references.json")
PYPROJECT_PATH = os.path.join(REPO_ROOT, "pyproject.toml")
LOCK_PATH = os.path.join(REPO_ROOT, "requirements", "app.lock.txt")
SRC_DIR = os.path.join(REPO_ROOT, "src")
APP_MODULE = "ehr_evidence_app"
SMOKE_DIR_NAME = "app-environment-smoke"
EXPECTED_PYTHON = "3.12.8"
PROBE_FLAGS = ("-I", "-B")
PROBE_TIMEOUT_SECONDS = 300
PROBE_MARKER = "APP_ENVIRONMENT_PROBE_JSON:"
MAX_PROBE_OUTPUT_BYTES = 1024 * 1024

# (distribution, import module): the selected app/runtime/test/ClearML set.
IMPORT_CHECKS = (
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    ("pydantic", "pydantic"),
    ("streamlit", "streamlit"),
    ("clearml", "clearml"),
    ("pytest", "pytest"),
)
REQUIRED_DISTRIBUTIONS = tuple(name for name, _ in IMPORT_CHECKS)
# Packages that belong to the GNN environment or a later LLM runtime step.
FORBIDDEN_DISTRIBUTIONS = frozenset([
    "torch", "torchvision", "torchaudio", "torch-geometric", "torch-scatter",
    "torch-sparse", "torch-cluster", "torch-spline-conv", "pyg-lib",
    "pykeen", "triton", "transformers", "accelerate", "bitsandbytes",
    "xformers", "vllm", "llama-cpp-python", "ctransformers",
    "onnxruntime-gpu", "sentence-transformers",
])
FORBIDDEN_PREFIXES = ("nvidia-", "cuda-", "cupy")
FORBIDDEN_MODULES = ("torch", "torch_geometric", "pykeen", "transformers",
                     "triton", "accelerate", "bitsandbytes")

# Windows runtime variables copied as they are; everything else is dropped.
RUNTIME_VARIABLES = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC",
                     "PATHEXT", "OS", "NUMBER_OF_PROCESSORS",
                     "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER")
# Temp, home and cache variables, all routed to the smoke directory.
ROUTED_VARIABLES = ("TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
                    "APPDATA", "LOCALAPPDATA", "XDG_CACHE_HOME",
                    "CLEARML_CACHE_DIR", "MPLCONFIGDIR")
SYNTHETIC_USERNAME = "app-smoke"
FIXED_VARIABLES = {"CLEARML_OFFLINE_MODE": "1",
                   "STREAMLIT_BROWSER_GATHER_USAGE_STATS": "false"}
CLEARML_CONFIG_NAME = "clearml.conf"
SYSTEM_PATH_DIRS = ("System32", "", "System32\\Wbem")

# An exact pin must end with the continuation marker; its hashes follow on
# indented --hash=sha256:<64 hex> lines, all but the last also continued.
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*==\s*"
                 r"([A-Za-z0-9][A-Za-z0-9.+!_-]*)\s*(;[^\\]*)?\\$")
HASH_LINE = re.compile(r"^[ \t]+--hash=sha256:[0-9A-Fa-f]{64}[ \t]*(\\)?$")

# Runs in the application interpreter under -I -B. It writes one marked
# JSON line to stdout; anything printed during imports goes to stderr,
# which the calling process discards.
PROBE_SOURCE = r'''
import importlib
import importlib.metadata as metadata
import importlib.util as util
import json
import os
import platform
import sys

spec = json.loads(sys.argv[1])
real_stdout = sys.stdout
sys.stdout = sys.stderr


def norm(path):
    """Return path absolute and case-normalised for prefix comparison."""
    return os.path.normcase(os.path.abspath(path))


sys.path.insert(0, spec["src"])
result = {"python": platform.python_version(),
          "isolated": sys.flags.isolated,
          "prefix": sys.prefix,
          "in_virtual_environment": sys.prefix != sys.base_prefix,
          "app": {}, "packages": {}}
try:
    app = importlib.import_module(spec["app_module"])
except (Exception, SystemExit) as error:
    result["app"] = {"import_status": "import_failed",
                     "import_error_type": type(error).__name__}
else:
    location = norm(getattr(app, "__file__", "") or "")
    result["app"] = {"import_status": "imported",
                     "version": getattr(app, "__version__", None),
                     "from_source": location.startswith(
                         norm(spec["src"]) + os.sep)}
for distribution, module in spec["imports"]:
    entry = {}
    try:
        entry["distribution_version"] = metadata.version(distribution)
    except metadata.PackageNotFoundError:
        entry["distribution_version"] = None
    try:
        importlib.import_module(module)
    except (Exception, SystemExit) as error:
        entry["import_status"] = "import_failed"
        entry["import_error_type"] = type(error).__name__
    else:
        entry["import_status"] = "imported"
    result["packages"][distribution] = entry
result["forbidden_loaded"] = sorted(
    name for name in spec["forbidden_modules"] if name in sys.modules)
result["forbidden_installed"] = sorted(
    name for name in spec["forbidden_modules"]
    if util.find_spec(name) is not None)
upstream = norm(spec["upstream_root"])
result["upstream_on_path"] = any(
    norm(entry) == upstream or norm(entry).startswith(upstream + os.sep)
    for entry in sys.path if entry)
real_stdout.write(spec["marker"] + json.dumps(result) + "\n")
real_stdout.flush()
'''


class CheckError(Exception):
    """A check that stops the smoke run before the probe completes."""


def normalise_name(name):
    """Return a distribution name in PEP 503 normalised form."""
    return re.sub(r"[-_.]+", "-", name).lower()


def forbidden_names(names):
    """Return the sorted excluded distributions among normalised names."""
    return sorted(name for name in names
                  if name in FORBIDDEN_DISTRIBUTIONS
                  or name.startswith(FORBIDDEN_PREFIXES))


def requirement_name(requirement):
    """Return the normalised distribution name of a PEP 508 requirement."""
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
    if not match:
        raise CheckError("pyproject.toml has an unreadable requirement")
    return normalise_name(match.group(1))


def declaration_problems(path=PYPROJECT_PATH):
    """Return problems with the pyproject.toml dependency declaration."""
    try:
        import tomllib
    except ImportError:
        return ["Python 3.11 or later is needed to read pyproject.toml"]
    try:
        with open(path, "rb") as handle:
            project = tomllib.load(handle).get("project", {})
    except (OSError, ValueError) as error:
        return ["pyproject.toml could not be read (%s)"
                % type(error).__name__]
    requirements = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        requirements.extend(extra)
    names = {requirement_name(item) for item in requirements}
    problems = ["pyproject.toml does not declare %s" % name
                for name in REQUIRED_DISTRIBUTIONS if name not in names]
    problems += ["pyproject.toml declares excluded package %s" % name
                 for name in forbidden_names(names)]
    return problems


def parse_lock(text):
    """Return ({name: version}, problems) from uv pip compile output.

    Blank and comment lines are skipped outside a continuation. Every
    other unindented line must be an exact name==version pin ending with
    the continuation marker, followed by one or more indented
    --hash=sha256:<64 hex> lines; each hash line except the last carries
    the marker too. Hashless or unmarked pins, malformed or orphan hash
    lines, interrupted continuations and every other -- directive (index,
    find-links, install or build options) are problems.
    """
    pins, problems = {}, []
    expecting = False  # the previous pin or hash line ended with "\"
    current = None  # line number of the pin whose hashes are being read
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        stripped = line.strip()
        if expecting:
            match = HASH_LINE.match(line)
            if match:
                expecting = match.group(1) is not None
                continue
            expecting = False
            problems.append("lock pin on line %d is not followed by a valid "
                            "hash line (line %d)" % (current, number))
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("--hash"):
            problems.append("lock line %d is %s hash line" % (
                number, "an orphan" if HASH_LINE.match(line)
                else "a malformed"))
            continue
        if stripped.startswith("-"):
            problems.append("lock line %d is a disallowed directive"
                            % number)
            continue
        match = None if line[:1] in (" ", "\t") else PIN.match(line)
        if not match:
            problems.append("lock line %d is not an exact name==version pin "
                            "ending with a continuation marker and hashes"
                            % number)
            continue
        name = normalise_name(match.group(1))
        if name in pins and pins[name] != match.group(3):
            problems.append("lock pins %s twice with different versions"
                            % name)
        pins[name] = match.group(3)
        expecting, current = True, number
    if expecting:
        problems.append("lock ends inside the continuation of the pin on "
                        "line %d" % current)
    return pins, problems


def lock_problems(path=LOCK_PATH):
    """Return ({name: version}, problems) for the committed lock file."""
    if not os.path.isfile(path):
        return {}, ["requirements/app.lock.txt is missing: lock resolution "
                    "pending (see docs/environments.md)"]
    with open(path, "r", encoding="utf-8") as handle:
        pins, problems = parse_lock(handle.read())
    problems += ["lock does not pin %s" % name
                 for name in REQUIRED_DISTRIBUTIONS if name not in pins]
    problems += ["lock contains excluded package %s" % name
                 for name in forbidden_names(pins)]
    return pins, problems


def configured_directory(key, paths_config=PATHS_CONFIG):
    """Return (path, fixed root) of a configured directory.

    The workspace_root and runtime_root settings must be the fixed roots,
    and the key's root field must be permitted for it, so the returned root
    is either guard.PRIVATE_ROOT or guard.RUNTIME_ROOT.
    """
    with open(paths_config, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    problem = (guard.configured_root_problem(config.get("workspace_root"),
                                             "workspace_root")
               or guard.runtime_root_setting_problem(
                   config.get("runtime_root")))
    if problem:
        raise CheckError(problem)
    try:
        entry = config["directories"][key]
    except (KeyError, TypeError):
        raise CheckError("config/paths.json has no directory %s" % key)
    try:
        return guard.configured_directory_path(key, entry)
    except ValueError as error:
        raise CheckError("directory %s" % error)


def upstream_root(upstream_config=UPSTREAM_CONFIG):
    """Return the configured upstream GNN root (a path; never opened)."""
    with open(upstream_config, "r", encoding="utf-8") as handle:
        root = json.load(handle).get("upstream_root")
    if not isinstance(root, str) or not root:
        raise CheckError("config/upstream_references.json has no "
                         "upstream_root")
    return root


def read_pyvenv(environment):
    """Return the key/value settings of an environment's pyvenv.cfg."""
    path = ntpath.join(environment, "pyvenv.cfg")
    settings = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            key, separator, value = line.partition("=")
            if separator:
                settings[key.strip().lower()] = value.strip()
    return settings


def environment_problems(environment, root):
    """Return problems with the application virtual environment layout.

    root is the fixed root the environment was configured under and is
    passed to the destination check explicitly.
    """
    problem = guard.destination_problem(environment, "directory", root=root)
    if problem:
        return [problem]
    python = ntpath.join(environment, "Scripts", "python.exe")
    if not os.path.isfile(python):
        return ["the application environment does not exist yet at the "
                "configured UV_PROJECT_ENVIRONMENT (see "
                "docs/environments.md)"]
    try:
        settings = read_pyvenv(environment)
    except OSError as error:
        return ["pyvenv.cfg could not be read (%s)" % type(error).__name__]
    problems = []
    version = settings.get("version_info") or settings.get("version")
    if version != EXPECTED_PYTHON:
        problems.append("the environment records Python %s, not %s"
                        % (version, EXPECTED_PYTHON))
    if settings.get("include-system-site-packages", "").lower() != "false":
        problems.append("the environment must not include system "
                        "site-packages")
    return problems


def prepare_smoke_dir(temp_root, root):
    """Validate, create and revalidate the smoke scratch directory.

    temp_root is the configured temp directory and root the fixed root it
    was configured under, passed explicitly to every destination check.
    """
    smoke = temp_root + "\\" + SMOKE_DIR_NAME
    config_file = smoke + "\\" + CLEARML_CONFIG_NAME
    problem = guard.destinations_problem([(smoke, "directory"),
                                          (config_file, "file")], root=root)
    if problem:
        raise CheckError("smoke directory: %s" % problem)
    os.makedirs(smoke, exist_ok=True)
    problem = guard.destination_problem(smoke, "directory", root=root)
    if problem:
        raise CheckError("smoke directory: %s" % problem)
    if os.path.lexists(config_file):
        raise CheckError("a ClearML configuration file exists in the smoke "
                         "directory; remove it so no server is configured")
    return smoke


def probe_environment(environment, smoke, environ=None):
    """Return the minimal process environment for the smoke probe.

    Only fixed Windows runtime variables are copied; temp, home and cache
    variables point at the smoke directory; ClearML is offline with a
    configuration file that does not exist. No credential, token, proxy
    or PYTHON* variable is passed on.
    """
    environ = os.environ if environ is None else environ
    system_root = environ.get("SYSTEMROOT")
    if not system_root or ";" in system_root:
        raise CheckError("SYSTEMROOT is not a plain Windows directory")
    if ";" in environment or ";" in smoke:
        raise CheckError("a configured path contains a semicolon")
    env = {name: environ[name] for name in RUNTIME_VARIABLES
           if environ.get(name)}
    env["SYSTEMROOT"] = system_root
    env["WINDIR"] = system_root
    directories = [ntpath.join(environment, "Scripts")]
    directories += [ntpath.join(system_root, part) if part else system_root
                    for part in SYSTEM_PATH_DIRS]
    env["PATH"] = ";".join(directories)
    for name in ROUTED_VARIABLES:
        env[name] = smoke
    env["CLEARML_CONFIG_FILE"] = smoke + "\\" + CLEARML_CONFIG_NAME
    env["USERNAME"] = SYNTHETIC_USERNAME
    env.update(FIXED_VARIABLES)
    return env


def probe_command(python, upstream):
    """Return the argument list that runs the smoke probe isolated."""
    spec = json.dumps({"marker": PROBE_MARKER, "src": SRC_DIR,
                       "app_module": APP_MODULE,
                       "imports": [list(item) for item in IMPORT_CHECKS],
                       "forbidden_modules": list(FORBIDDEN_MODULES),
                       "upstream_root": upstream})
    return [python] + list(PROBE_FLAGS) + ["-c", PROBE_SOURCE, spec]


def run_probe(environment, smoke, upstream):
    """Run the probe once in the smoke directory and return its result."""
    python = ntpath.join(environment, "Scripts", "python.exe")
    try:
        completed = subprocess.run(
            probe_command(python, upstream), shell=False, cwd=smoke,
            env=probe_environment(environment, smoke),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=PROBE_TIMEOUT_SECONDS,
            check=False)
    except subprocess.TimeoutExpired:
        raise CheckError("the smoke probe did not finish within %d seconds"
                         % PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as error:
        raise CheckError("the application interpreter could not be started "
                         "(%s)" % type(error).__name__)
    if completed.returncode != 0:
        raise CheckError("the smoke probe exited with code %d"
                         % completed.returncode)
    return parse_probe_output(completed.stdout or b"")


def parse_probe_output(stdout):
    """Return the single marked JSON result from the probe's stdout."""
    if len(stdout) > MAX_PROBE_OUTPUT_BYTES:
        raise CheckError("the smoke probe produced too much output")
    text = stdout.decode("utf-8", errors="replace")
    lines = [line[len(PROBE_MARKER):] for line in text.splitlines()
             if line.startswith(PROBE_MARKER)]
    if len(lines) != 1:
        raise CheckError("the smoke probe did not return exactly one result")
    try:
        result = json.loads(lines[0])
    except ValueError:
        raise CheckError("the smoke probe result is not valid JSON")
    if not isinstance(result, dict):
        raise CheckError("the smoke probe result is not an object")
    return result


def probe_problems(result, pins, environment):
    """Return problems found in a smoke probe result."""
    problems = []
    if result.get("python") != EXPECTED_PYTHON:
        problems.append("the probe ran Python %s, not %s"
                        % (result.get("python"), EXPECTED_PYTHON))
    if result.get("isolated") != 1:
        problems.append("the probe did not run in isolated mode")
    prefix = result.get("prefix")
    if not (result.get("in_virtual_environment") is True
            and isinstance(prefix, str)
            and ntpath.normcase(ntpath.normpath(prefix))
            == ntpath.normcase(ntpath.normpath(environment))):
        problems.append("the probe did not run in the configured "
                        "application environment")
    app = result.get("app") or {}
    if app.get("import_status") != "imported":
        problems.append("the application package did not import (%s)"
                        % app.get("import_error_type"))
    elif app.get("from_source") is not True:
        problems.append("the application package was not imported from src")
    packages = result.get("packages") or {}
    for distribution, module in IMPORT_CHECKS:
        entry = packages.get(distribution) or {}
        if entry.get("import_status") != "imported":
            problems.append("%s did not import (%s)"
                            % (module, entry.get("import_error_type")))
        installed = entry.get("distribution_version")
        if installed != pins.get(distribution):
            problems.append("%s installed version %s differs from lock pin %s"
                            % (distribution, installed,
                               pins.get(distribution)))
    for name in result.get("forbidden_loaded") or []:
        problems.append("excluded module %s was imported" % name)
    for name in result.get("forbidden_installed") or []:
        problems.append("excluded module %s is installed" % name)
    if result.get("upstream_on_path") is not False:
        problems.append("an upstream GNN directory is on sys.path")
    return problems


def report(result, problems):
    """Print the observed versions and the overall result."""
    if result:
        print("Python           : %s" % result.get("python"))
        app = result.get("app") or {}
        print("%-17s: %s" % (APP_MODULE, app.get("import_status")))
        for distribution, _ in IMPORT_CHECKS:
            entry = (result.get("packages") or {}).get(distribution) or {}
            print("%-17s: %s (%s)" % (distribution,
                                      entry.get("distribution_version"),
                                      entry.get("import_status")))
    for problem in problems:
        print("Problem          : %s" % problem)
    print("RESULT: %s" % ("PASS" if not problems else "FAIL"))


def run():
    """Run every check and return a process exit code."""
    problems = declaration_problems()
    pins, found = lock_problems()
    problems += found
    environment, root = configured_directory("uv_project_env")
    found = environment_problems(environment, root)
    problems += found
    result = None
    if not found:
        smoke = prepare_smoke_dir(*configured_directory("temp"))
        result = run_probe(environment, smoke, upstream_root())
        problems += probe_problems(result, pins, environment)
    report(result, problems)
    return 0 if not problems else 1


def main():
    """Run the smoke check, reporting any stopping failure by type only."""
    try:
        return run()
    except CheckError as error:
        print("FAILED: %s" % error)
    except KeyboardInterrupt:
        print("FAILED: interrupted before completion")
    except Exception as error:  # sanitised: text may name local paths
        print("FAILED: unexpected %s" % type(error).__name__)
    print("RESULT: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
