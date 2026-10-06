#!/usr/bin/env python3
"""Capture the installed GNN environment versions without changing it (Step 6).

The tool uses the Python standard library only. The interpreter to
inspect is given by the required --python argument; its path is never
printed, logged or saved. Every component of that path, and of the
environment's Lib\\site-packages directory, is inspected from the drive root
with lstat; links, junctions and other reparse points are refused.

The interpreter is launched once, without a shell, with -I -S -B: isolated
(no PYTHON* variables, user site or current directory), without the site
module (so no .pth file or sitecustomize code runs) and without writing
bytecode. It receives a minimal environment (Windows runtime variables, a
PATH of the environment's own and system directories, and temp/cache
variables routed to a fixed private probe directory, which is also its
working directory); credentials, tokens, Python settings and unrelated
variables are not passed on. A small fixed probe then:

- reports the Python version and its startup flags;
- appends only the validated site-packages directory to sys.path;
- reads the installed distribution version of every listed dependency from
  package metadata (importlib.metadata);
- imports only torch, torch_geometric and numpy to verify that they load,
  keeping the module __version__ and the exception type name on failure.

No upstream project code is imported or run, no model, checkpoint, graph or
patient data is loaded and nothing is installed or upgraded. The whole
environment tree (the interpreter's directory) is fingerprinted before and
after the probe from relative names, entry types, sizes, modification times,
reparse attributes and streamed file contents; only the entry count and an
aggregate digest are kept. A change, a link or reparse point anywhere in
the tree, or a fingerprint that cannot be taken, blocks the manifest.

Probe output is parsed strictly and rebuilt from validated fields only;
version strings that are not plain version numbers are withheld, and the
manifest is refused if it would contain the interpreter path. Documented
reference versions from the replication documentation are kept apart from
observed versions and never substituted for them. If any required version is
missing, withheld or unimportable, the manifest says so, the tool prints
RESULT: INCOMPLETE and exits with a failure code.

The manifest has a fixed name inside the fixed private directory
outputs\\environments, outside the Git repository. The destination is
checked before the probe, immediately before any existing manifest is
examined and again after its directory is created, just before the atomic
write. There is no fallback location. These are checks at moments in time;
they cannot prevent a concurrent external change made between a check and
the following operation. No capture timestamp is stored: an identical
existing manifest is left unchanged and a different one is never
overwritten.

Usage:
    python -m ehr_evidence_app.environment.gnn --python PATH
"""

from __future__ import annotations

import argparse
import hashlib
import json
import ntpath
import os
import re
import stat
import subprocess
import sys

from ehr_evidence_app.storage import policy as guard
from ehr_evidence_app.gnn.baseline import (PARTIAL, existing_outcome, serialise,
                                        write_record)

VERSION = "1.1"
SCHEMA_VERSION = "1.1"
ENVIRONMENT_ROOT = guard.PRIVATE_ROOT + "\\outputs\\environments"
MANIFEST_PATH = ENVIRONMENT_ROOT + "\\gnn-environment.json"
PROBE_TEMP_ROOT = guard.PRIVATE_ROOT + "\\temp\\gnn-environment-probe"
SITE_PACKAGES = ("Lib", "site-packages")
PROBE_TIMEOUT_SECONDS = 600
PROBE_MARKER = "GNN_ENVIRONMENT_PROBE_JSON:"
PROBE_VERSION = 2
PROBE_FLAGS = ("-I", "-S", "-B")
MAX_PROBE_OUTPUT_BYTES = 1024 * 1024
CHUNK_BYTES = 8 * 1024 * 1024
REPARSE_IN_TREE = ("the interpreter environment contains a symlink, "
                   "junction or other reparse point, so it cannot be "
                   "fingerprinted completely; nothing was written")
INTERPRETER_NAME = re.compile(r"^python(3(\.[0-9]{1,2})?)?\.exe$",
                              re.IGNORECASE)

# Windows runtime variables copied as they are; everything else inherited
# (credentials, tokens, PYTHON*, PG*, GIT_*, proxies, ...) is dropped.
RUNTIME_VARIABLES = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC",
                     "PATHEXT", "OS", "NUMBER_OF_PROCESSORS",
                     "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER")
# Temp, home and cache variables, all set to the fixed probe directory.
ROUTED_VARIABLES = ("TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
                    "APPDATA", "LOCALAPPDATA", "XDG_CACHE_HOME",
                    "TORCH_HOME", "CUDA_CACHE_PATH", "MPLCONFIGDIR")
# A fixed synthetic identity so getpass.getuser() (reached by PyTorch cache
# discovery) finds USERNAME instead of importing the POSIX-only pwd module.
# The host's own USERNAME is never read or passed on.
SYNTHETIC_USERNAME = "gnn-capture"
# Environment directories placed on PATH, as conda activation would, so
# that bundled DLLs load; followed by the Windows system directories.
ENVIRONMENT_PATH_DIRS = ("", "Library\\mingw-w64\\bin", "Library\\usr\\bin",
                         "Library\\bin", "Scripts", "bin")
SYSTEM_PATH_DIRS = ("System32", "", "System32\\Wbem")

# (key, label, distribution, import module or None, documented version).
# Only the three core libraries are imported; the rest are metadata only.
COMPONENTS = (
    ("pytorch", "PyTorch", "torch", "torch", "2.11.0+cu128"),
    ("pyg", "PyG", "torch-geometric", "torch_geometric", "2.8.0.post1"),
    ("numpy", "NumPy", "numpy", "numpy", "1.26.4"),
    ("pandas", "pandas", "pandas", None, "2.3.3"),
    ("scikit_learn", "scikit-learn", "scikit-learn", None, "1.7.2"),
    ("scipy", "SciPy", "scipy", None, "1.17.1"),
    ("pykeen", "PyKEEN", "pykeen", None, "1.11.1"),
)
DOCUMENTED_PYTHON = "3.12.8"
DOCUMENTED_SOURCE = ("inherited GNN replication documentation "
                     "(documented environment)")
PYG_INSTALL_EXAMPLE = "2.8.0"
PYG_DISCREPANCY = (
    "The replication documentation lists PyG 2.8.0.post1, but its install "
    "example "
    "names PyG 2.8.0. The documented value 2.8.0.post1 is used as the "
    "reference; the install-example value is recorded separately. Neither "
    "is assumed to match the installed version, which is observed "
    "independently.")

# A plain PEP 440 style version with an optional local label, nothing else,
# so no path, URL or free text can pass through a version field.
VERSION_TEXT = re.compile(
    r"^[0-9]+(\.[0-9]+)*((a|b|rc)[0-9]+)?(\.post[0-9]+)?(\.dev[0-9]+)?"
    r"(\+[A-Za-z0-9]+(\.[A-Za-z0-9]+)*)?$")
MAX_VERSION_LENGTH = 64
ERROR_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")
IMPLEMENTATION = re.compile(r"^[A-Za-z]{1,32}$")
DISTRIBUTION_STATUSES = ("installed", "not_installed", "metadata_error")
IMPORT_STATUSES = ("imported", "module_not_found", "import_failed")
REQUIRED_STARTUP = {"isolated": 1, "no_site": 1, "dont_write_bytecode": 1,
                    "site_imported": False}

# Runs in the inspected interpreter under -I -S -B. It writes nothing but
# one marked JSON line to stdout; anything printed during imports is
# diverted to stderr, which the calling process discards. Only the given
# site-packages directory is added to sys.path; .pth files are not read.
PROBE_SOURCE = r'''
import importlib
import importlib.metadata as metadata
import json
import platform
import struct
import sys

spec = json.loads(sys.argv[1])
real_stdout = sys.stdout
sys.stdout = sys.stderr
startup = {"isolated": sys.flags.isolated, "no_site": sys.flags.no_site,
           "dont_write_bytecode": sys.flags.dont_write_bytecode,
           "site_imported": "site" in sys.modules}
sys.path.append(spec["site_packages"])
result = {"probe_version": spec["probe_version"],
          "python": {"version": platform.python_version(),
                     "implementation": platform.python_implementation(),
                     "pointer_bits": struct.calcsize("P") * 8,
                     "startup": startup},
          "packages": {}}
for key, distribution, module in spec["components"]:
    entry = {}
    try:
        entry["distribution_version"] = metadata.version(distribution)
        entry["distribution_status"] = "installed"
    except metadata.PackageNotFoundError:
        entry["distribution_status"] = "not_installed"
    except Exception as error:
        entry["distribution_status"] = "metadata_error"
        entry["distribution_error_type"] = type(error).__name__
    if module:
        try:
            loaded = importlib.import_module(module)
        except ModuleNotFoundError as error:
            missing = error.name == module
            entry["import_status"] = ("module_not_found" if missing
                                      else "import_failed")
            entry["import_error_type"] = type(error).__name__
        except (Exception, SystemExit) as error:
            entry["import_status"] = "import_failed"
            entry["import_error_type"] = type(error).__name__
        else:
            entry["import_status"] = "imported"
            version = getattr(loaded, "__version__", None)
            if isinstance(version, str):
                entry["module_version"] = version
    result["packages"][key] = entry
real_stdout.write(spec["marker"] + json.dumps(result) + "\n")
real_stdout.flush()
'''

NOT_RECORDED = ("The inspected interpreter's path is deliberately not "
                "recorded, printed or logged.")
LIMITATIONS = [
    "Versions come from installed package metadata and, for PyTorch, PyG "
    "and NumPy, from the imported module's __version__; they describe the "
    "interpreter at capture time only.",
    "Only PyTorch, PyG and NumPy imports are verified. pandas, "
    "scikit-learn, SciPy and PyKEEN are checked through metadata only and "
    "are not imported.",
    "The probe runs without the site module, so .pth files are not "
    "processed; a package that relies on a .pth entry may show as "
    "unimportable here although it loads in normal use.",
    "A successful import does not show that CUDA, compiled extensions or "
    "upstream GNN code work; no model, checkpoint, graph or data was "
    "loaded.",
    "The environment root is taken to be the interpreter's directory "
    "(conda layout, with Lib\\site-packages beneath it).",
    "Version strings that are not plain version numbers are withheld and "
    "flagged rather than recorded.",
    "Path checks and fingerprints are taken at moments in time; they "
    "cannot prevent a concurrent external change between a check and the "
    "following operation.",
]


class CaptureError(Exception):
    """A capture failure whose message never contains the interpreter path."""


def sanitise_error(error):
    """Describe an unexpected failure by type only, never by its text."""
    return "%s (message withheld to avoid exposing local paths)" % type(
        error).__name__


def sha256_file(path):
    """Return the SHA-256 of a small file."""
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


# Fixed Destinations And Interpreter Checks

def fixed_policy_problem():
    """Return why the fixed manifest or probe-temp location is unsafe."""
    if os.name != "nt":
        return "environment capture supports Windows only"
    problem = guard.fixed_policy_problem()
    if problem:
        return "fixed policy: %s" % problem
    for label, path in (("environment root", ENVIRONMENT_ROOT),
                        ("manifest", MANIFEST_PATH),
                        ("probe temp", PROBE_TEMP_ROOT)):
        problem = guard.path_problem(path)
        if problem:
            return "%s: %s" % (label, problem)
        if not guard.is_within(path, guard.PRIVATE_ROOT, strict=True):
            return "the %s is not inside %s" % (label, guard.PRIVATE_ROOT)
    if ntpath.normcase(ntpath.dirname(MANIFEST_PATH)) != ntpath.normcase(
            ENVIRONMENT_ROOT):
        return "the manifest must lie directly inside %s" % ENVIRONMENT_ROOT
    return None


def output_problem():
    """Return why the fixed manifest may not be written, else None.

    The environment root, the manifest and its .partial name must pass the
    Step 0.4 destination checks, and a leftover .partial file is refused
    rather than reused or deleted.
    """
    problem = guard.destinations_problem([(ENVIRONMENT_ROOT, "directory"),
                                          (MANIFEST_PATH, "file"),
                                          (MANIFEST_PATH + PARTIAL, "file")])
    if problem:
        return problem
    if os.path.lexists(MANIFEST_PATH + PARTIAL):
        return ("%s exists from an earlier interrupted capture; inspect and "
                "remove it first" % (MANIFEST_PATH + PARTIAL))
    return None


def require_output():
    """Raise unless the fixed manifest destination passes its checks now."""
    problem = output_problem()
    if problem:
        raise CaptureError(problem)


def prepare_probe_temp():
    """Validate, create and revalidate the fixed private probe directory."""
    problem = guard.destination_problem(PROBE_TEMP_ROOT, "directory")
    if problem:
        raise CaptureError("probe temp: %s" % problem)
    os.makedirs(PROBE_TEMP_ROOT, exist_ok=True)
    problem = guard.destination_problem(PROBE_TEMP_ROOT, "directory")
    if problem:
        raise CaptureError("probe temp: %s" % problem)
    return PROBE_TEMP_ROOT


def environment_root(python):
    """Return the environment root: the interpreter's directory."""
    return ntpath.dirname(python)


def site_packages(python):
    """Return the environment's Lib\\site-packages directory."""
    return ntpath.join(environment_root(python), *SITE_PACKAGES)


def walk_problem(path, kind):
    """Return a generic reason why path is unsafe, never naming it.

    The drive root is inspected with lstat first, then every further
    component: each must exist, none may be a symlink, junction or other
    reparse point, every parent must be a directory and the leaf must be of
    the expected kind ('file' or 'directory'). No component may contain a
    semicolon, the Windows PATH separator.
    """
    if guard.path_problem(path):
        return "is not a plain absolute drive path"
    if ";" in path:
        return "has a component containing a semicolon"
    current = path[:3]
    try:
        info = os.lstat(current)
    except OSError:
        return "has a missing or unreadable drive root"
    if guard.is_link_like(info):
        return "has a drive root that is a link or reparse point"
    if not stat.S_ISDIR(info.st_mode):
        return "has a drive root that is not a directory"
    parts = path[3:].split("\\")
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except OSError:
            return "has a missing or unreadable component"
        if guard.is_link_like(info):
            return "has a link, junction or reparse point component"
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            return "has a parent component that is not a directory"
    if kind == "file" and not stat.S_ISREG(info.st_mode):
        return "is not a regular file"
    if kind == "directory" and not stat.S_ISDIR(info.st_mode):
        return "is not a directory"
    return None


def overlaps(first, second):
    """Return True when either path equals or lies beneath the other."""
    return guard.is_within(first, second) or guard.is_within(second, first)


def interpreter_problem(path):
    """Return why --python is unusable, else None, without echoing it.

    The value must name python.exe (or python3.exe, python3.N.exe); the
    interpreter file and the environment's Lib\\site-packages directory
    must pass the full component walk, and the environment must not overlap
    the manifest or probe-temp directories.
    """
    if not isinstance(path, str) or not path:
        return "--python is required"
    if guard.path_problem(path):
        return ("--python must be a plain absolute drive path (value "
                "withheld)")
    if not INTERPRETER_NAME.match(ntpath.basename(path)):
        return "--python must name a python.exe interpreter (value withheld)"
    problem = walk_problem(path, "file")
    if problem:
        return "--python %s (value withheld)" % problem
    problem = walk_problem(site_packages(path), "directory")
    if problem:
        return ("the interpreter's Lib\\site-packages directory %s (value "
                "withheld)" % problem)
    root = environment_root(path)
    if overlaps(root, ENVIRONMENT_ROOT) or overlaps(root, PROBE_TEMP_ROOT):
        return ("the interpreter environment overlaps the manifest or probe "
                "directory (value withheld)")
    return None


# Environment Fingerprint

def entry_kind(info):
    """Return the fingerprint type label of lstat-style information."""
    if guard.is_link_like(info):
        return "reparse"
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    if stat.S_ISREG(info.st_mode):
        return "file"
    return "other"


def file_identity(info):
    """Return the identity and metadata compared around a file hash."""
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, getattr(info, "st_file_attributes", 0))


def file_content_digest(path):
    """Return (lstat info, SHA-256 hex) of one regular file's content.

    The file is inspected with lstat before and after a streamed hash, and
    the open handle is checked against it: it must stay a regular file
    that is not a reparse point, with the same identity (device and file
    index), size, mode, modification time and attributes, and exactly its
    size must be read. Any difference raises CaptureError without naming
    the file; unreadable files raise OSError to the caller.
    """
    before = os.lstat(path)
    if guard.is_link_like(before) or not stat.S_ISREG(before.st_mode):
        raise CaptureError(REPARSE_IN_TREE)
    digest = hashlib.sha256()
    count = 0
    with open(path, "rb") as handle:
        opened = os.fstat(handle.fileno())
        for block in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(block)
            count += len(block)
    after = os.lstat(path)
    if (file_identity(after) != file_identity(before)
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (before.st_dev, before.st_ino, before.st_size)
            or count != before.st_size or guard.is_link_like(after)):
        raise CaptureError("a file in the interpreter environment changed "
                           "while it was being hashed; nothing was written")
    return after, digest.hexdigest()


def environment_fingerprint(root):
    """Return (entry count, SHA-256) describing the tree under root.

    Each entry contributes its root-relative name, type, size, modification
    time and reparse attribute, and every regular file also its streamed
    content SHA-256, checked for changes during hashing. Any symlink,
    junction or other reparse point in the tree fails closed, so the
    fingerprint covers every path imports could reach. Only the count and
    aggregate digest are returned, so no name, root path or content leaves
    this function. Failure, including an unreadable file, raises
    CaptureError without naming any path.
    """
    digest = hashlib.sha256()
    count = 0
    pending = [""]
    try:
        while pending:
            relative = pending.pop()
            directory = os.path.join(root, relative) if relative else root
            with os.scandir(directory) as listing:
                entries = sorted(listing, key=lambda item: item.name)
            for entry in entries:
                info = entry.stat(follow_symlinks=False)
                name = (relative + "\\" + entry.name if relative
                        else entry.name)
                kind = entry_kind(info)
                if kind == "reparse":
                    raise CaptureError(REPARSE_IN_TREE)
                if kind == "directory":
                    # NTFS directory listings can carry a stale time for a
                    # recently changed subdirectory; read it fresh and fail
                    # closed if it is no longer a plain directory.
                    info = os.lstat(os.path.join(directory, entry.name))
                    if entry_kind(info) != "directory":
                        raise CaptureError(REPARSE_IN_TREE)
                content = ""
                if kind == "file":
                    info, content = file_content_digest(
                        os.path.join(directory, entry.name))
                attributes = getattr(info, "st_file_attributes", 0)
                line = "%s\0%s\0%d\0%d\0%d\0%s\n" % (
                    kind, name, info.st_size, info.st_mtime_ns,
                    attributes & guard.REPARSE_POINT, content)
                digest.update(line.encode("utf-8", "surrogatepass"))
                count += 1
                if kind == "directory":
                    pending.append(name)
    except OSError as error:
        raise CaptureError("the interpreter environment could not be "
                           "fingerprinted (%s); nothing was written"
                           % type(error).__name__)
    return count, digest.hexdigest()


# Probe Execution And Strict Parsing

def probe_spec(python):
    """Return the JSON argument that tells the probe what to inspect."""
    return json.dumps({"probe_version": PROBE_VERSION, "marker": PROBE_MARKER,
                       "site_packages": site_packages(python),
                       "components": [[key, distribution, module]
                                      for key, _, distribution, module, _
                                      in COMPONENTS]})


def probe_command(python):
    """Return the argument list that runs the probe isolated, without site."""
    return [python] + list(PROBE_FLAGS) + ["-c", PROBE_SOURCE,
                                           probe_spec(python)]


def probe_environment(python, temp, environ=None):
    """Return the minimal process environment for the probe.

    Only fixed Windows runtime variables are copied. SYSTEMROOT must be a
    plain absolute path to an existing directory, with no link, reparse or
    semicolon component; there is no fallback, and WINDIR is set to the
    same validated value. PATH is built only from the validated
    environment root and system root, so no entry can contain the PATH
    separator. Temp, home and cache variables point at the private probe
    directory, and USERNAME is the fixed synthetic identity, never the
    host's. No other inherited variable is passed on, and failures never
    repeat a path or value.
    """
    environ = os.environ if environ is None else environ
    root = environment_root(python)
    for label, path in (("the interpreter environment", root),
                        ("the probe temp directory", temp)):
        if not isinstance(path, str) or ";" in path:
            raise CaptureError("%s path has a component containing a "
                               "semicolon (value withheld)" % label)
    system_root = environ.get("SYSTEMROOT")
    problem = (walk_problem(system_root, "directory")
               if isinstance(system_root, str) and system_root
               else "is not set")
    if problem:
        raise CaptureError("SYSTEMROOT %s; the probe needs a plain existing "
                           "Windows directory (value withheld)" % problem)
    env = {}
    for name in RUNTIME_VARIABLES:
        value = environ.get(name)
        if value:
            env[name] = value
    env["SYSTEMROOT"] = system_root
    env["WINDIR"] = system_root
    directories = [ntpath.join(root, part) if part else root
                   for part in ENVIRONMENT_PATH_DIRS]
    directories += [ntpath.join(system_root, part) if part else system_root
                    for part in SYSTEM_PATH_DIRS]
    env["PATH"] = ";".join(directories)
    for name in ROUTED_VARIABLES:
        env[name] = temp
    env["USERNAME"] = SYNTHETIC_USERNAME
    return env


def run_probe(python):
    """Run the probe once in the private probe directory; return its stdout.

    No shell is used; stdin is closed and stderr (which may contain paths
    from import warnings) is discarded. Failures are reported by type,
    exit code or timeout only.
    """
    temp = prepare_probe_temp()
    try:
        completed = subprocess.run(
            probe_command(python), shell=False, cwd=temp,
            env=probe_environment(python, temp), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        raise CaptureError("the interpreter probe did not finish within %d "
                           "seconds" % PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as error:
        raise CaptureError("the interpreter could not be started (%s)"
                           % type(error).__name__)
    if completed.returncode != 0:
        raise CaptureError("the interpreter probe exited with code %d"
                           % completed.returncode)
    return completed.stdout or b""


def clean_version(value):
    """Return (version, withheld) keeping only plain version text."""
    if value is None:
        return None, False
    if (isinstance(value, str) and len(value) <= MAX_VERSION_LENGTH
            and VERSION_TEXT.match(value)):
        return value, False
    return None, True


def clean_error_type(value):
    """Return an exception type name, or a placeholder for anything else."""
    if value is None:
        return None
    if isinstance(value, str) and ERROR_TYPE.match(value):
        return value
    return "unrecognised"


def parse_startup(section):
    """Return the probe's startup flags, refusing a site-enabled start."""
    if not isinstance(section, dict):
        raise CaptureError("the probe did not report its startup flags")
    startup = {}
    for name, required in REQUIRED_STARTUP.items():
        value = section.get(name)
        if type(value) is not type(required) or value != required:
            raise CaptureError("the probe did not start isolated, without "
                               "site and without bytecode writing")
        startup[name] = value
    return startup


def parse_python(section):
    """Return the validated observed Python block from the probe."""
    if not isinstance(section, dict):
        raise CaptureError("the probe did not report a Python block")
    startup = parse_startup(section.get("startup"))
    version, withheld = clean_version(section.get("version"))
    implementation = section.get("implementation")
    if not isinstance(implementation, str) or not IMPLEMENTATION.match(
            implementation):
        implementation = None
    bits = section.get("pointer_bits")
    return {"version": version, "version_withheld": withheld,
            "implementation": implementation,
            "pointer_bits": bits if bits in (32, 64) else None,
            "interpreter_ran": True, "startup": startup}


def parse_package(entry, distribution, module):
    """Return the validated observed record of one dependency.

    Unknown fields are dropped; statuses must be known values and versions
    must be plain version text, otherwise they are withheld and flagged.
    """
    if not isinstance(entry, dict):
        raise CaptureError("the probe returned a malformed package entry")
    status = entry.get("distribution_status")
    if status not in DISTRIBUTION_STATUSES:
        raise CaptureError("the probe returned an unknown distribution "
                           "status")
    installed, installed_withheld = clean_version(
        entry.get("distribution_version") if status == "installed" else None)
    record = {"distribution": distribution, "distribution_status": status,
              "installed_version": installed,
              "installed_version_withheld": installed_withheld,
              "distribution_error_type": clean_error_type(
                  entry.get("distribution_error_type")),
              "import_module": module, "import_checked": module is not None}
    if module is None:
        record.update({"import_status": "not_checked",
                       "import_error_type": None, "module_version": None,
                       "module_version_withheld": False})
        return record
    import_status = entry.get("import_status")
    if import_status not in IMPORT_STATUSES:
        raise CaptureError("the probe returned an unknown import status")
    module_version, module_withheld = clean_version(
        entry.get("module_version") if import_status == "imported" else None)
    record.update({"import_status": import_status,
                   "import_error_type": clean_error_type(
                       entry.get("import_error_type")),
                   "module_version": module_version,
                   "module_version_withheld": module_withheld})
    return record


def parse_probe_output(stdout):
    """Return (python, packages) from the probe's stdout bytes.

    Exactly one marked line must be present; other lines are ignored. The
    result is rebuilt from validated fields, so unexpected content in the
    probe output cannot reach the manifest.
    """
    if len(stdout) > MAX_PROBE_OUTPUT_BYTES:
        raise CaptureError("the probe output is unexpectedly large")
    text = stdout.decode("utf-8", "replace")
    lines = [line[len(PROBE_MARKER):] for line in text.splitlines()
             if line.startswith(PROBE_MARKER)]
    if len(lines) != 1:
        raise CaptureError("the probe output held %d result lines; exactly "
                           "one is required" % len(lines))
    try:
        data = json.loads(lines[0])
    except ValueError:
        raise CaptureError("the probe result is not valid JSON")
    if not isinstance(data, dict) or data.get("probe_version") != (
            PROBE_VERSION):
        raise CaptureError("the probe result has an unexpected version")
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise CaptureError("the probe did not report a package block")
    observed = {}
    for key, _, distribution, module, _ in COMPONENTS:
        if key not in packages:
            raise CaptureError("the probe did not report %s" % key)
        observed[key] = parse_package(packages[key], distribution, module)
    return parse_python(data.get("python")), observed


# Manifest

def documented_reference():
    """Return the documented reference versions, apart from observations."""
    versions = {"python": {"label": "Python", "version": DOCUMENTED_PYTHON}}
    for key, label, distribution, _, documented in COMPONENTS:
        versions[key] = {"label": label, "distribution": distribution,
                         "version": documented}
    return {"source": DOCUMENTED_SOURCE,
            "provenance": "documented_not_observed",
            "versions": versions,
            "discrepancies": [{"component": "pyg",
                               "documented_version": "2.8.0.post1",
                               "install_example_version": PYG_INSTALL_EXAMPLE,
                               "note": PYG_DISCREPANCY}]}


def compare(documented, observed):
    """Return how an observed version relates to the documented one."""
    if observed is None:
        return "not_observed"
    return ("matches_documented" if observed == documented
            else "differs_from_documented")


def comparisons(python, packages):
    """Compare each observed version with its documented reference.

    The installed distribution version is compared; when metadata is
    missing the imported module's version is used and labelled as such. A
    missing observation is 'not_observed', never the documented value.
    """
    result = {"python": {"documented": DOCUMENTED_PYTHON,
                         "observed": python["version"],
                         "observed_from": "interpreter",
                         "result": compare(DOCUMENTED_PYTHON,
                                           python["version"])}}
    for key, _, _, _, documented in COMPONENTS:
        package = packages[key]
        observed, source = package["installed_version"], "distribution"
        if observed is None and package["module_version"] is not None:
            observed, source = package["module_version"], "module"
        entry = {"documented": documented, "observed": observed,
                 "observed_from": source if observed is not None else None,
                 "result": compare(documented, observed)}
        if package["installed_version"] and package["module_version"]:
            entry["module_matches_distribution"] = (
                package["installed_version"] == package["module_version"])
        if key == "pyg":
            entry["install_example"] = PYG_INSTALL_EXAMPLE
            entry["install_example_result"] = compare(PYG_INSTALL_EXAMPLE,
                                                      observed)
        result[key] = entry
    return result


def version_gap(package, candidates):
    """Return why no valid version was observed, or None if one was."""
    if any(package[field] is not None for field in candidates):
        return None
    withheld = any(package[field + "_withheld"] for field in candidates)
    return "version_withheld" if withheld else "version_unavailable"


def incomplete_components(python, packages):
    """Return the required observations that are missing or invalid.

    Python needs a valid version. An import-checked library needs a
    successful import and a valid distribution or module version. A
    metadata-only dependency needs installed status and a valid metadata
    version. Each gap is listed with its reason.
    """
    gaps = []
    if python["version"] is None:
        gaps.append({"component": "python", "reason": (
            "version_withheld" if python["version_withheld"]
            else "version_unavailable")})
    for key, _, _, module, _ in COMPONENTS:
        package = packages[key]
        if module is not None:
            reason = (package["import_status"]
                      if package["import_status"] != "imported"
                      else version_gap(package, ("installed_version",
                                                 "module_version")))
        elif package["distribution_status"] != "installed":
            reason = package["distribution_status"]
        else:
            reason = version_gap(package, ("installed_version",))
        if reason:
            gaps.append({"component": key, "reason": reason})
    return gaps


def build_manifest(python, packages, fingerprint):
    """Assemble the deterministic environment manifest.

    Documented and observed versions live in separate sections. No capture
    time, interpreter path, file name or environment value is stored; the
    environment is described by its entry count and aggregate digest only.
    """
    script = os.path.abspath(__file__)
    gaps = incomplete_components(python, packages)
    return {
        "record_type": "gnn_environment_capture",
        "schema_version": SCHEMA_VERSION,
        "step": "6",
        "capture_tool": {"name": "ehr_evidence_app.environment.gnn",
                         "version": VERSION, "sha256": sha256_file(script)},
        "interpreter": {"path_recorded": False, "note": NOT_RECORDED,
                        "launch": ("-I -S -B -c <fixed probe>, no shell, "
                                   "minimal environment")},
        "determinism": ("No capture timestamp is stored; an identical "
                        "environment produces a byte-identical manifest."),
        "environment_changes": {
            "packages_installed_or_upgraded": False,
            "upstream_code_imported_or_executed": False,
            "models_checkpoints_graphs_or_data_loaded": False,
            "site_module_and_pth_files_skipped": True,
            "bytecode_writing_disabled": True,
            "fingerprint_unchanged_during_probe": True},
        "environment_fingerprint": {
            "entry_count": fingerprint[0], "sha256": fingerprint[1],
            "basis": ("root-relative names, entry types, sizes, "
                      "modification times, reparse attributes and "
                      "SHA-256 of every file's content; names and "
                      "content are not recorded")},
        "documented_reference": documented_reference(),
        "observed": {"provenance": "observed_in_interpreter",
                     "python": python, "packages": packages},
        "comparison": comparisons(python, packages),
        "capture_status": "complete" if not gaps else "incomplete",
        "unavailable_or_incomplete": gaps,
        "limitations": list(LIMITATIONS),
    }


def path_variants(path):
    """Return spellings of a path that could appear in the manifest text."""
    variants = set()
    for candidate in (path, ntpath.dirname(path)):
        if len(candidate) > 3:
            for spelling in (candidate, candidate.replace("\\", "/"),
                             candidate.replace("\\", "\\\\")):
                variants.add(spelling.lower())
    return variants


def leak_problem(payload, python):
    """Return why the manifest text would expose the interpreter, else None."""
    text = payload.decode("utf-8").lower()
    if any(variant in text for variant in path_variants(python)):
        return ("the manifest would contain the interpreter path or its "
                "directory; nothing was written")
    return None


def report(manifest, outcome):
    """Print versions and statuses only, never the interpreter path.

    Ends with RESULT: COMPLETE only when every required version was
    observed; otherwise lists the gaps and ends with RESULT: INCOMPLETE.
    """
    python = manifest["comparison"]["python"]
    print("%-13s %-22s %-22s %s" % ("component", "observed", "documented",
                                    "result"))
    print("%-13s %-22s %-22s %s" % ("python", python["observed"],
                                    python["documented"], python["result"]))
    for key, _, _, _, _ in COMPONENTS:
        entry = manifest["comparison"][key]
        package = manifest["observed"]["packages"][key]
        status = entry["result"]
        if package["import_checked"]:
            status += ", import %s" % package["import_status"]
        print("%-13s %-22s %-22s %s" % (key, entry["observed"],
                                        entry["documented"], status))
    print("")
    print("PyG note         : documented 2.8.0.post1, install example 2.8.0; "
          "see documented_reference.discrepancies")
    fingerprint = manifest["environment_fingerprint"]
    print("Environment      : %d entries, unchanged during the probe"
          % fingerprint["entry_count"])
    print("Capture status   : %s" % manifest["capture_status"])
    for gap in manifest["unavailable_or_incomplete"]:
        print("Incomplete       : %s (%s)" % (gap["component"], gap["reason"]))
    print("Manifest         : %s" % MANIFEST_PATH)
    print("Manifest state   : %s" % (
        "written" if outcome == "absent"
        else "unchanged (an identical manifest already exists)"))
    print("RESULT: %s" % ("COMPLETE" if manifest["capture_status"]
                          == "complete" else "INCOMPLETE"))


def build_parser():
    """Build the command-line parser; --python is required, no default."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", required=True,
                        help="path of the existing GNN interpreter to "
                        "inspect (never printed or saved)")
    return parser


def run(args):
    """Capture the environment and return a process exit code.

    The fixed locations, the interpreter and the destination are checked
    before the probe. The environment is fingerprinted before and after
    it; any change blocks the manifest. The destination is rechecked just
    before an existing manifest is examined and again after its directory
    is created. An incomplete capture is recorded but returns 1.
    """
    problem = (fixed_policy_problem() or interpreter_problem(args.python)
               or output_problem())
    if problem:
        raise CaptureError(problem)
    root = environment_root(args.python)
    before = environment_fingerprint(root)
    python, packages = parse_probe_output(run_probe(args.python))
    after = environment_fingerprint(root)
    if after != before:
        raise CaptureError("the interpreter environment changed during the "
                           "probe; nothing was written")
    manifest = build_manifest(python, packages, after)
    payload = serialise(manifest)
    problem = leak_problem(payload, args.python)
    if problem:
        raise CaptureError(problem)

    require_output()
    outcome = existing_outcome(MANIFEST_PATH, payload)
    if outcome == "conflict":
        raise CaptureError("a manifest with different content already exists "
                           "at %s and was left unchanged; move it aside after "
                           "review to record a new capture" % MANIFEST_PATH)
    if outcome == "absent":
        os.makedirs(ENVIRONMENT_ROOT, exist_ok=True)
        require_output()
        write_record(MANIFEST_PATH, payload)
    report(manifest, outcome)
    return 0 if manifest["capture_status"] == "complete" else 1


def main(argv=None):
    """Run the capture tool, sanitising any failure."""
    try:
        return run(build_parser().parse_args(argv))
    except CaptureError as error:
        print("FAILED: %s" % error)
        return 1
    except KeyboardInterrupt:
        print("FAILED: interrupted before completion")
        return 1
    except Exception as error:  # sanitised: text may name local paths
        print("FAILED: %s" % sanitise_error(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
