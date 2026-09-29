#!/usr/bin/env python3
"""Destination path and read-only source checks shared by the tools (Step 0.4).

Standard library only. Windows is the supported platform; destination checks
fail closed elsewhere.

The policy anchor and the private runtime root are fixed here, not read from
configuration, so a configuration edit can choose destinations inside the
private root but can never move or widen either root.

Both roots live under the one policy anchor on the internal NTFS volume.
The private workspace root is authoritative for protected data, outputs,
logs, verification records and ClearML configuration. A separate runtime
root may hold only the disposable runtime/cache directory keys named in
RUNTIME_DIRECTORY_KEYS (environments, including the model environment,
managed Python installations, temp, test temp/cache, package/bytecode/XDG
caches, Hugging Face, Torch, CUDA, Conda, models and the ClearML cache).
Callers name the root they intend explicitly; the default is
always the private workspace root, and any other path fails closed. Every
destination is
checked lexically first (absolute drive path, no UNC/device prefix, no
alternate stream, reserved name, 8.3 short name or other ambiguous spelling)
and then on disk without following links: existing ancestors and leaves must
not be symlinks, junctions or other reparse points, no existing ancestor may
contain a .git entry, and an existing leaf must be of the expected kind.
Missing leaves are allowed; nothing beneath the first missing component exists
to be inspected.

Source access is the other half: the legacy export directory on C: is a
read-only copy source, the Athena vocabulary directory and the raw
MIMIC-IV directory in the cold archive are read-only sources, the
configured PostgreSQL database on loopback is a read-only connection
source, and psql runs from an allow-listed environment whose read-only
options cannot be overridden by inherited settings.

These are checks at one moment. They cannot stop a concurrent external change
to the filesystem made after the check and before the write.
"""

from __future__ import annotations

import ntpath
import os
import re
import stat
import sys

POLICY_ANCHOR = "G:\\Industry Dataset"
PRIVATE_ROOT = POLICY_ANCHOR + "\\ehr-evidence-workspace"
RUNTIME_ROOT = "G:\\Industry Dataset\\ehr-evidence-runtime"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
# Directory keys that config/paths.json may place beneath RUNTIME_ROOT
# (root "runtime"). Every other key, including every workspace-only
# category, must stay beneath PRIVATE_ROOT. Mirrored in
# setup-destinations.ps1.
RUNTIME_DIRECTORY_KEYS = frozenset([
    "runtime_root", "temp", "pytest_temp", "pytest_cache", "hf_home",
    "hf_hub", "hf_assets", "hf_datasets", "torch_home", "pip_cache",
    "uv_cache", "xdg_cache", "pycache", "conda_pkgs", "conda_envs",
    "uv_project_env", "venvs", "models", "clearml_cache",
    "python_installs", "cuda_cache", "llm_env",
])
ROOT_NAMES = {"workspace": PRIVATE_ROOT, "runtime": RUNTIME_ROOT}
DRIVE_FIXED = 3
LEGACY_SOURCE_DIR = "C:\\omop_export"
# The Athena vocabulary release in the cold archive: vocabulary only, no
# patient data. Read-only, and only by the vocabulary builds.
VOCABULARY_SOURCE_DIR = "W:\\Industry Dataset\\Athena"
# The raw MIMIC-IV 3.1 hospital and ICU modules in the cold archive:
# credentialed patient data. Read-only, and only by the record extraction.
RAW_SOURCE_DIR = "W:\\Industry Dataset\\mimic-iv-3.1"

ALLOWED_SOURCE_HOSTS = ("127.0.0.1", "::1", "localhost")
REQUIRED_SOURCE_DATABASE = "mimiciv_omop"
PSQL_EXECUTABLE_NAME = "psql.exe"
REDIRECT_VARIABLES = ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE")
CONNECTION_VARIABLES = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER",
                        "PGPASSWORD")
PROCESS_VARIABLES = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATH",
                     "PATHEXT", "TEMP", "TMP")
PORT = re.compile(r"^[1-9][0-9]{0,4}$")

# The mandatory environment set, fixed in code. Every variable must be
# defined exactly once. Destination variables are bound to their directory
# key, whose path the configuration may choose inside the private root. The
# only template and literal values are the canonical ones below; no other
# variable may use a template or literal. Mirrored in setup-destinations.ps1.
DIRECTORY_VARIABLES = {
    "EHR_WORKSPACE_ROOT": "workspace_root",
    "EHR_RUNTIME_ROOT": "runtime_root",
    "EHR_PREPARED_DATA_DIR": "prepared_data",
    "EHR_OUTPUTS_DIR": "outputs",
    "EHR_LOGS_DIR": "logs",
    "EHR_MODELS_DIR": "models",
    "EHR_CLEARML_DIR": "clearml_local",
    "TEMP": "temp",
    "TMP": "temp",
    "TMPDIR": "temp",
    "PYTHONPYCACHEPREFIX": "pycache",
    "HF_HOME": "hf_home",
    "HF_HUB_CACHE": "hf_hub",
    "HF_ASSETS_CACHE": "hf_assets",
    "HF_DATASETS_CACHE": "hf_datasets",
    "TORCH_HOME": "torch_home",
    "PIP_CACHE_DIR": "pip_cache",
    "UV_CACHE_DIR": "uv_cache",
    "UV_PROJECT_ENVIRONMENT": "uv_project_env",
    "UV_PYTHON_INSTALL_DIR": "python_installs",
    "CUDA_CACHE_PATH": "cuda_cache",
    "XDG_CACHE_HOME": "xdg_cache",
    "CONDA_PKGS_DIRS": "conda_pkgs",
    "CONDA_ENVS_DIRS": "conda_envs",
    "PYTEST_DEBUG_TEMPROOT": "pytest_temp",
    "CLEARML_CACHE_DIR": "clearml_cache",
}
FILE_VARIABLES = {"CLEARML_CONFIG_FILE": "clearml_local"}
TEMPLATE_VARIABLES = {
    "PYTEST_ADDOPTS": ('--basetemp="${dir_posix:pytest_temp}" '
                       '-o "cache_dir=${dir_posix:pytest_cache}"'),
}
LITERAL_VARIABLES = {"CLEARML_OFFLINE_MODE": "1"}
REQUIRED_VARIABLES = tuple(sorted(
    list(DIRECTORY_VARIABLES) + list(FILE_VARIABLES)
    + list(TEMPLATE_VARIABLES) + list(LITERAL_VARIABLES)))
REQUIRED_DIRECTORY_KEYS = tuple(sorted(
    set(DIRECTORY_VARIABLES.values()) | set(FILE_VARIABLES.values())
    | {"verification", "pytest_temp", "pytest_cache", "test_scratch"}))
# Consumers split these values into several paths, so a delimiter inside
# the configured path would create an unintended (possibly relative) entry.
LIST_VARIABLES = ("CONDA_PKGS_DIRS", "CONDA_ENVS_DIRS")
LIST_DELIMITERS = (";", ",")
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
PLACEHOLDER = re.compile(r"\$\{(dir|dir_posix):([A-Za-z0-9_]+)\}")
VALUE_KINDS = ("directory", "file", "literal", "template")

DRIVE_PATH = re.compile(r"^[A-Za-z]:\\")
FORBIDDEN_CHARACTERS = frozenset('<>:"/|?*')
SHORT_NAME = re.compile(r"~[0-9]")
DEVICE_SUFFIXES = "123456789\u00b9\u00b2\u00b3"
RESERVED_NAMES = frozenset(
    ["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"]
    + ["COM" + suffix for suffix in DEVICE_SUFFIXES]
    + ["LPT" + suffix for suffix in DEVICE_SUFFIXES])
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


# --------------------------------------------------------------------------
# Lexical checks
# --------------------------------------------------------------------------

def component_problem(part):
    """Return why one path component is unsafe on Windows, else None."""
    if not part:
        return "path has an empty component or a trailing separator"
    if part in (".", ".."):
        return "path contains a '.' or '..' component"
    if part != part.rstrip(". "):
        return "a path component ends with a dot or space"
    if FORBIDDEN_CHARACTERS.intersection(part):
        return ("a path component contains a reserved character or an "
                "alternate data stream")
    if part.split(".")[0].rstrip(" ").upper() in RESERVED_NAMES:
        return "a path component is a reserved device name"
    if SHORT_NAME.search(part):
        return "a path component looks like an 8.3 short name"
    return None


def path_problem(path):
    """Return why path is not a plain absolute Windows drive path, else None.

    Rejects relative and drive-relative paths, UNC and device paths, forward
    slashes, empty, '.' or '..' components, trailing dots or spaces,
    reserved characters (including ':' alternate data streams), reserved
    device names, 8.3 short names and any spelling Windows would normalise
    to a different path.
    """
    if not isinstance(path, str) or not path:
        return "path is empty or not text"
    if any(ord(character) < 32 for character in path):
        return "path contains a control character"
    if path.startswith(("\\\\", "//")):
        return "UNC and device paths are not permitted"
    if "/" in path:
        return "forward slashes are ambiguous; use backslashes"
    if not DRIVE_PATH.match(path):
        return ("path must be absolute with a drive letter; relative and "
                "drive-relative paths are not permitted")
    for part in path[3:].split("\\"):
        problem = component_problem(part)
        if problem:
            return problem
    if os.name == "nt" and (ntpath.normcase(os.path.abspath(path))
                            != ntpath.normcase(path)):
        return "path has an ambiguous Windows spelling"
    return None


def name_problem(name):
    """Return why name is not one safe file or directory name, else None."""
    if not isinstance(name, str) or "\\" in name or "/" in name:
        return "name must be a single path component"
    if name.lower() == ".git":
        return "name must not be .git"
    return component_problem(name)


def fixed_root(root):
    """Return root unchanged when it is one of the two fixed roots.

    Only PRIVATE_ROOT or RUNTIME_ROOT is accepted, compared exactly, so a
    caller can never pass an arbitrary root to widen a check.
    """
    if root not in (PRIVATE_ROOT, RUNTIME_ROOT):
        raise ValueError("root must be PRIVATE_ROOT or RUNTIME_ROOT")
    return root


def relative_problem(relative, root=PRIVATE_ROOT):
    """Return why a configured root-relative path is unsafe, else None."""
    fixed_root(root)
    if relative == ".":
        return None
    if not isinstance(relative, str):
        return "relative path is not text"
    return path_problem(root + "\\" + relative)


def directory_root_problem(key, entry):
    """Return why a configured directory's root field is not permitted.

    The optional "root" field names "workspace" (the default) or
    "runtime". Only keys in RUNTIME_DIRECTORY_KEYS may name "runtime"; every
    other key, including workspace-only categories, must stay in the private
    workspace root.
    """
    if not isinstance(entry, dict):
        return "%s: directory entry is not an object" % key
    name = entry.get("root", "workspace")
    if name not in ROOT_NAMES:
        return "%s: root must be \"workspace\" or \"runtime\"" % key
    if name == "runtime" and key not in RUNTIME_DIRECTORY_KEYS:
        return ("%s is a workspace-only category; only approved "
                "runtime/cache categories may use the runtime root %s"
                % (key, RUNTIME_ROOT))
    return None


def directory_root(entry):
    """Return the fixed root named by a validated directory entry."""
    return ROOT_NAMES[entry.get("root", "workspace")]


def configured_directory_path(key, entry):
    """Return (absolute path, fixed root) for one configured directory.

    Raises ValueError when the root field or the relative path is unsafe;
    nothing is inspected on disk here.
    """
    problem = directory_root_problem(key, entry)
    if problem:
        raise ValueError(problem)
    root = directory_root(entry)
    relative = entry.get("path")
    problem = relative_problem(relative, root)
    if problem:
        raise ValueError("%s: %s" % (key, problem))
    return (root if relative == "." else root + "\\" + relative), root


def is_within(path, root, strict=False):
    """Return True when path equals or lies beneath root, by component."""
    key = ntpath.normcase(ntpath.normpath(path))
    base = ntpath.normcase(ntpath.normpath(root)).rstrip("\\")
    if key.startswith(base + "\\"):
        return True
    return not strict and key == base


def fixed_policy_problem():
    """Return why the fixed anchor, both roots and repository are unsafe.

    Both roots must be plain paths strictly inside the policy anchor. The
    runtime root must overlap neither the private root nor the source
    repository, and the private root must not overlap the repository.
    """
    for root in (POLICY_ANCHOR, PRIVATE_ROOT, RUNTIME_ROOT):
        problem = path_problem(root)
        if problem:
            return "%s: %s" % (root, problem)
    if not is_within(PRIVATE_ROOT, POLICY_ANCHOR, strict=True):
        return "the private root is not inside the policy anchor"
    if not is_within(RUNTIME_ROOT, POLICY_ANCHOR, strict=True):
        return "the runtime root is not inside the policy anchor"
    if is_within(PRIVATE_ROOT, REPO_ROOT) or is_within(REPO_ROOT,
                                                       PRIVATE_ROOT):
        return "the private root and the source repository overlap"
    for other in (PRIVATE_ROOT, REPO_ROOT):
        if is_within(RUNTIME_ROOT, other) or is_within(other, RUNTIME_ROOT):
            return "the runtime root overlaps %s" % other
    return None


def configured_root_problem(value, label):
    """Return why a configured root would relax the fixed private root."""
    problem = path_problem(value)
    if problem:
        return "%s: %s" % (label, problem)
    if not is_within(value, PRIVATE_ROOT):
        return ("%s must stay inside %s; configuration cannot relocate or "
                "widen the private root" % (label, PRIVATE_ROOT))
    return None


def runtime_root_setting_problem(value):
    """Return why a configured runtime_root differs from the fixed root."""
    if not isinstance(value, str) or (ntpath.normcase(value)
                                      != ntpath.normcase(RUNTIME_ROOT)):
        return ("runtime_root must be the fixed runtime root %s; "
                "configuration cannot relocate it" % RUNTIME_ROOT)
    return None


# --------------------------------------------------------------------------
# Filesystem checks (no link is followed)
# --------------------------------------------------------------------------

def is_link_like(info):
    """Return True for lstat info of a symlink, junction or reparse point."""
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & REPARSE_POINT)


def filesystem_problem(path, kind):
    """Inspect the existing components of path without following links.

    Walks from the drive root: every existing ancestor must be a plain
    directory without a .git entry, and no existing component may be a
    symlink, junction or other reparse point. The first missing component
    ends the walk. An existing leaf must be of the expected kind, and an
    existing directory leaf must not contain .git either.
    """
    current = path[:3]
    parts = path[3:].split("\\")
    info = None
    for index, part in enumerate(parts):
        if os.path.lexists(os.path.join(current, ".git")):
            return "%s: existing ancestor %s contains .git" % (path, current)
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return None
        except OSError as error:
            return "%s: cannot inspect %s safely (%s)" % (
                path, current, type(error).__name__)
        if is_link_like(info):
            return ("%s: %s is a symlink, junction or other reparse point"
                    % (path, current))
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            return "%s: existing ancestor %s is not a directory" % (
                path, current)
    if kind == "directory":
        if not stat.S_ISDIR(info.st_mode):
            return "%s: existing destination is not a directory" % path
        if os.path.lexists(os.path.join(path, ".git")):
            return "%s: existing destination contains .git" % path
    elif not stat.S_ISREG(info.st_mode):
        return "%s: existing destination is not a regular file" % path
    return None


def destination_problem(path, kind="directory", strict=True,
                        root=PRIVATE_ROOT):
    """Return why path is not an approved destination, else None.

    kind is 'directory' or 'file'. root is the fixed root the caller intends
    and defaults to the private workspace root; only RUNTIME_ROOT may be
    named instead, so runtime destinations are always requested explicitly.
    strict requires the path to lie beneath the root rather than equal it.
    """
    if kind not in ("directory", "file"):
        raise ValueError("kind must be 'directory' or 'file'")
    fixed_root(root)
    if os.name != "nt":
        return "destination checks support Windows only"
    if sys.version_info < (3, 8):
        return ("Python 3.8 or later is required to inspect junctions "
                "without following them")
    problem = fixed_policy_problem()
    if problem:
        return "fixed policy: %s" % problem
    problem = path_problem(path)
    if problem:
        return "%s: %s" % (path, problem)
    if not is_within(path, root, strict=strict):
        return "%s: destination is not inside %s" % (path, root)
    if is_within(path, REPO_ROOT):
        return "%s: destination is inside the source repository" % path
    if ".git" in [part.lower() for part in path.split("\\")]:
        return "%s: destination names a .git directory" % path
    return filesystem_problem(path, kind)


def destinations_problem(entries, root=PRIVATE_ROOT):
    """Return the first problem among (path, kind) pairs, else None."""
    for path, kind in entries:
        problem = destination_problem(path, kind, root=root)
        if problem:
            return problem
    return None


def runtime_volume_problem(root=RUNTIME_ROOT):
    """Return why the runtime root's volume is not a fixed NTFS volume.

    Read-only: queries the drive type and file-system name of the root's
    drive. Exact disk/volume identity, health, ACL and encryption checks
    belong to ehr_evidence_app.storage.runtime.
    """
    fixed_root(root)
    if os.name != "nt":
        return "runtime volume checks support Windows only"
    import ctypes
    drive = root[:3]
    kernel32 = ctypes.windll.kernel32
    if kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive)) != DRIVE_FIXED:
        return "%s is not a fixed local volume" % drive
    name = ctypes.create_unicode_buffer(64)
    if not kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(drive), None, 0, None, None, None, name, 64):
        return "%s volume information is unavailable" % drive
    if name.value != "NTFS":
        return "%s is %s, not NTFS" % (drive, name.value or "unknown")
    return None


# --------------------------------------------------------------------------
# Environment definitions (config/paths.json)
# --------------------------------------------------------------------------

def binding_problem(name, kind, entry):
    """Return why a mandatory variable deviates from its fixed binding.

    Destination variables must be the directory or file entry of their fixed
    key; PYTEST_ADDOPTS and CLEARML_OFFLINE_MODE must equal their canonical
    template or literal exactly, so no option, override or relative path can
    be appended. Any other variable may only be a directory or file entry.
    """
    if name in DIRECTORY_VARIABLES:
        key = DIRECTORY_VARIABLES[name]
        if kind != "directory" or entry["directory"] != key:
            return "%s must be the directory entry for key %s" % (name, key)
    elif name in FILE_VARIABLES:
        key = FILE_VARIABLES[name]
        spec = entry.get("file")
        if (kind != "file" or not isinstance(spec, dict)
                or spec.get("directory") != key):
            return "%s must be a file entry in directory key %s" % (name, key)
    elif name in TEMPLATE_VARIABLES:
        if kind != "template" or entry["template"] != TEMPLATE_VARIABLES[name]:
            return "%s must be exactly the canonical template %s" % (
                name, TEMPLATE_VARIABLES[name])
    elif name in LITERAL_VARIABLES:
        if kind != "literal" or entry["literal"] != LITERAL_VARIABLES[name]:
            return "%s must be exactly the literal %s" % (
                name, LITERAL_VARIABLES[name])
    elif kind not in ("directory", "file"):
        return ("%s: additional variables must be validated directory or "
                "file entries; literal and template values are fixed in code"
                % name)
    return None


def environment_entry_problem(entry, directory_keys):
    """Return why one environment entry could bypass path checks, else None.

    Checks the name, a single value kind, the fixed binding of mandatory
    variables, that every referenced directory key exists and that a file
    entry names one safe file.
    """
    name = entry.get("name") if isinstance(entry, dict) else None
    if not isinstance(name, str) or not ENV_NAME.match(name):
        return "environment variable name is not a plain upper-case name"
    kinds = [kind for kind in VALUE_KINDS if kind in entry]
    if len(kinds) != 1:
        return "%s must define exactly one of %s" % (
            name, ", ".join(VALUE_KINDS))
    kind = kinds[0]
    problem = binding_problem(name, kind, entry)
    if problem:
        return problem
    if kind == "directory":
        key = entry["directory"]
        if not isinstance(key, str) or key not in directory_keys:
            return "%s names an unknown directory key" % name
    elif kind == "file":
        spec = entry["file"] if isinstance(entry["file"], dict) else {}
        key = spec.get("directory")
        if not isinstance(key, str) or key not in directory_keys:
            return "%s names an unknown directory key" % name
        problem = name_problem(spec.get("name"))
        if problem:
            return "%s file name: %s" % (name, problem)
    elif kind == "template":
        for match in PLACEHOLDER.finditer(entry["template"]):
            if match.group(2) not in directory_keys:
                return "%s template names an unknown directory key" % name
    return None


def environment_set_problems(directories, environment):
    """Return (subject, problem) pairs for an incomplete mandatory set.

    Independent of which entries happen to be present: every essential
    directory key and every mandatory variable must be defined, each variable
    exactly once, list-valued variables must not point at a path that
    contains a list delimiter, and every directory's root field must be
    permitted for its key.
    """
    problems = []
    for key in REQUIRED_DIRECTORY_KEYS:
        if key not in directories:
            problems.append(("directory:%s" % key,
                             "required directory key %s is missing" % key))
    for key in sorted(directories):
        problem = directory_root_problem(key, directories[key])
        if problem:
            problems.append(("root:%s" % key, problem))
    names = [entry.get("name") for entry in environment
             if isinstance(entry, dict)]
    for name in REQUIRED_VARIABLES:
        count = names.count(name)
        if count != 1:
            problems.append((name, "required environment variable %s is "
                             "defined %d times; exactly once is required"
                             % (name, count)))
    for name in LIST_VARIABLES:
        entry = directories.get(DIRECTORY_VARIABLES[name])
        path = entry.get("path") if isinstance(entry, dict) else None
        if isinstance(path, str) and any(mark in path
                                         for mark in LIST_DELIMITERS):
            problems.append((name, "%s path contains a list delimiter (%s)"
                             % (name, " or ".join(LIST_DELIMITERS))))
    return problems


# --------------------------------------------------------------------------
# Read-only sources
# --------------------------------------------------------------------------

def source_directory_problem(path):
    """Return why a read-only copy source directory is not permitted.

    Allowed: exactly the legacy export directory on C: (read-only source
    exception) or a directory under the policy anchor outside the
    disposable runtime root. Sources are read, never written, so they are
    not subject to destination checks.
    """
    problem = path_problem(path)
    if problem:
        return "source directory: %s" % problem
    if ntpath.normcase(path) == ntpath.normcase(LEGACY_SOURCE_DIR):
        return None
    if (is_within(path, POLICY_ANCHOR, strict=True)
            and not is_within(path, RUNTIME_ROOT)):
        return None
    return ("source directory must be %s or lie under %s outside the "
            "runtime root" % (LEGACY_SOURCE_DIR, POLICY_ANCHOR))


def vocabulary_source_problem(path, directory=VOCABULARY_SOURCE_DIR):
    """Return why path is not a readable vocabulary source file, else None.

    The directory must be exactly the Athena vocabulary directory, or a
    directory under the policy anchor outside the disposable runtime root.
    The file must lie directly in it and be a plain regular file reached
    without symlinks, junctions or reparse points. Vocabulary sources are
    read, never written.
    """
    for label, value in (("vocabulary directory", directory),
                         ("vocabulary file", path)):
        problem = path_problem(value)
        if problem:
            return "%s: %s" % (label, problem)
    if not (ntpath.normcase(directory) == ntpath.normcase(
            VOCABULARY_SOURCE_DIR)
            or (is_within(directory, POLICY_ANCHOR, strict=True)
                and not is_within(directory, RUNTIME_ROOT))):
        return ("vocabulary directory must be %s or lie under %s outside "
                "the runtime root" % (VOCABULARY_SOURCE_DIR, POLICY_ANCHOR))
    if ntpath.normcase(ntpath.dirname(path)) != ntpath.normcase(directory):
        return "vocabulary file must lie directly in its directory"
    problem = filesystem_problem(path, "file")
    if problem:
        return problem
    if not os.path.isfile(path):
        return "vocabulary file is missing"
    return None


def raw_source_problem(path, directory=RAW_SOURCE_DIR):
    """Return why path is not a readable raw MIMIC-IV source file, else None.

    The directory must be exactly the raw MIMIC-IV directory, or a
    directory under the policy anchor outside the disposable runtime root.
    The file must lie beneath it and be a plain regular file reached
    without symlinks, junctions or reparse points. Raw sources are read,
    never written.
    """
    for label, value in (("raw directory", directory), ("raw file", path)):
        problem = path_problem(value)
        if problem:
            return "%s: %s" % (label, problem)
    if not (ntpath.normcase(directory) == ntpath.normcase(RAW_SOURCE_DIR)
            or (is_within(directory, POLICY_ANCHOR, strict=True)
                and not is_within(directory, RUNTIME_ROOT))):
        return ("raw directory must be %s or lie under %s outside the "
                "runtime root" % (RAW_SOURCE_DIR, POLICY_ANCHOR))
    if not is_within(path, directory, strict=True):
        return "raw file must lie beneath its directory"
    problem = filesystem_problem(path, "file")
    if problem:
        return problem
    if not os.path.isfile(path):
        return "raw file is missing"
    return None


def executable_problem(path):
    """Return why a psql executable path is unsafe to launch, else None.

    An existing installation on C: is permitted; the path must be a plain
    local drive path to an existing file named psql.exe.
    """
    problem = path_problem(path)
    if problem:
        return "psql executable path: %s" % problem
    if ntpath.basename(path).lower() != PSQL_EXECUTABLE_NAME:
        return "the psql executable must be named %s" % PSQL_EXECUTABLE_NAME
    if not os.path.isfile(path):
        return "the configured psql executable was not found"
    return None


def source_policy_problem(allowed_hosts, database, timeouts):
    """Return why configured source settings widen the fixed source policy.

    Configured hosts must be a non-empty subset of the fixed loopback hosts,
    the database must be the fixed source database and every timeout must be
    a positive integer (zero would disable a PostgreSQL timeout).
    """
    if (not isinstance(allowed_hosts, list) or not allowed_hosts
            or any(host not in ALLOWED_SOURCE_HOSTS
                   for host in allowed_hosts)):
        return ("configured allowed source hosts must be a non-empty subset "
                "of the local loopback hosts")
    if database != REQUIRED_SOURCE_DATABASE:
        return ("configuration changes the required source database from %s"
                % REQUIRED_SOURCE_DATABASE)
    for name in sorted(timeouts):
        value = timeouts[name]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return "%s must be a positive integer" % name
    return None


def connection_problem(environ, allowed_hosts, database):
    """Return why the process connection settings are not the permitted source.

    Only presence is examined for PGUSER and PGPASSWORD; no credential or
    connection value is included in any returned message.
    """
    for name in REDIRECT_VARIABLES:
        if environ.get(name, ""):
            return ("%s is set; it can redirect the source connection. Unset "
                    "it for this process" % name)
    host = environ.get("PGHOST", "")
    if host not in allowed_hosts or host not in ALLOWED_SOURCE_HOSTS:
        return "PGHOST is not one of the allowed loopback hosts"
    name = environ.get("PGDATABASE", "")
    if "=" in name or "://" in name:
        return ("PGDATABASE looks like a connection string or URI; only the "
                "plain database name is permitted")
    if name != database or name != REQUIRED_SOURCE_DATABASE:
        return "PGDATABASE is not %s" % REQUIRED_SOURCE_DATABASE
    port = environ.get("PGPORT", "")
    if not PORT.match(port) or int(port) > 65535:
        return "PGPORT is not a port number between 1 and 65535"
    if not environ.get("PGUSER", "").strip():
        return "PGUSER is not set in the process environment"
    if not environ.get("PGPASSWORD", ""):
        return "PGPASSWORD is not set in the process environment"
    return None


def controlled_psql_environment(environ, forced):
    """Return an allow-listed psql environment with forced settings applied.

    Only basic Windows process variables and the validated connection
    variables are inherited; forced values (read-only PGOPTIONS, timeouts,
    encoding, application name) are applied last. PGHOSTADDR, service,
    credential-file and any inherited PGOPTIONS are never passed on.
    """
    env = {}
    for name in PROCESS_VARIABLES:
        value = environ.get(name)
        if value:
            env[name] = value
    for name in CONNECTION_VARIABLES:
        env[name] = environ.get(name, "")
    env.update(forced)
    return env
