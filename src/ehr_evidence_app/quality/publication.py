#!/usr/bin/env python3
"""Audit publishable repository content read-only before any commit (Step 3).

Standard library only, read-only, Windows and Python 3.8 or later. Exit
status: 0 PASS, 1 policy or content findings, 2 operational failure.

The local git.exe (found on PATH and checked without following links) is
run without a shell, with an explicit --git-dir and --work-tree,
--no-replace-objects, a bounded timeout, no prompts or optional locks,
core.fsmonitor=false and every inherited GIT_* variable removed. Only
read-only subcommands are allowed. A shallow or grafted repository is
refused because its history would be incomplete. Four checks run:

1. ignore probes - ``git check-ignore --no-index --stdin`` must confirm that
   the repository's own .gitignore ignores every required probe path (root
   and nested) and leaves every publishable probe path trackable;
2. candidates - ``git ls-files --cached --others --exclude-standard``, read
   from the working tree as plain regular files beneath the repository and
   outside .git, never through a symlink, junction or other reparse point;
3. staged index - every ``git ls-files --stage`` entry, read as a blob from
   the object database;
4. history - every object reachable from all refs and reflogs
   (``rev-list --objects``): each commit tree's paths and blobs, the
   commit and annotated-tag objects themselves (messages and metadata),
   trees named directly by a ref or tag, and blobs outside every commit
   tree. Nothing is checked out. A repository without commits reports zero.

Every path is checked for sensitive, generated and unapproved categories,
even when force-added or present only in history. The interface's page
(HTML, CSS, JavaScript and an SVG icon) is a publishable type in the page's
own directory only, a fixed rule the policy cannot widen. Publishable files and
history objects must be bounded UTF-8 text and are scanned for
high-confidence secret signatures, private keys, credential-bearing URIs,
quoted and unquoted secret assignments, machine-specific user-profile paths
and numeric patient-identifier assignments. Documented placeholders and
clearly synthetic values are allowed; the tests tree is not exempt from the
high-confidence checks.

config/publishable_policy.json declares the rules. It is read only from the
repository config directory and must equal or tighten the fixed rules held
here; it cannot widen publishable types, drop a mandatory category, probe or
signature, or redirect the repository or the Git binary.

Output names paths (unsafe components redacted), scopes, rule IDs and counts
only; never file content, messages, object IDs, matched values, Git object
bytes or environment values. Nothing is deleted, rewritten, staged or
untracked.

Usage:
    python -m ehr_evidence_app.quality.publication [--policy PATH]
"""

import argparse
import fnmatch
import json
import ntpath
import os
import posixpath
import re
import stat
import subprocess
import sys

from ehr_evidence_app.storage import policy as guard

SCHEMA_VERSION = "1.0"
STEP = "3"
REPO_ROOT = guard.REPO_ROOT
CONFIG_DIR = os.path.join(REPO_ROOT, "config")
POLICY_NAME = "publishable_policy.json"
PRIVATE_ROOT = guard.PRIVATE_ROOT
RUNTIME_ROOT = guard.RUNTIME_ROOT
MAX_POLICY_BYTES = 256 * 1024
HARD_MAX_CANDIDATE_BYTES = 1024 * 1024
MAX_DECLARATION_WINDOW = 2048
MAX_PATTERN_LENGTH = 600

EXIT_PASS = 0
EXIT_FINDINGS = 1
EXIT_OPERATIONAL = 2

GIT_EXECUTABLE_NAME = "git.exe"
GIT_MINIMUM_VERSION = (2, 24)
GIT_TIMEOUT_SECONDS = 120
MAX_GIT_OUTPUT_BYTES = 256 * 1024 * 1024
OBJECT_BATCH_BYTES = 32 * 1024 * 1024
READ_ONLY_COMMANDS = frozenset(["--version", "ls-files", "check-ignore",
                                "rev-list", "ls-tree", "cat-file"])
GIT_GLOBAL_OPTIONS = ("--no-pager", "--no-optional-locks",
                      "--no-replace-objects")
GIT_SETTINGS = ("core.fsmonitor=false", "core.quotePath=false",
                "protocol.allow=never")
# Files whose presence means reachable history is truncated or rewritten.
HISTORY_BOUNDARY_FILES = ((("shallow",), "a shallow boundary"),
                          (("info", "grafts"), "a legacy graft file"))

OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
STAGE_ENTRY = re.compile(
    r"^([0-7]{6}) ([0-9a-f]{40}|[0-9a-f]{64}) ([0-3])\t(.+)$", re.S)
TREE_ENTRY = re.compile(
    r"^([0-7]{6}) (blob|commit|tree) ([0-9a-f]{40}|[0-9a-f]{64})\t(.+)$",
    re.S)
BATCH_HEADER = re.compile(
    r"^([0-9a-f]{40}|[0-9a-f]{64}) (blob|tree|commit|tag) ([0-9]+)$")
COMMIT_TREE = re.compile(rb"^tree ([0-9a-f]{40}|[0-9a-f]{64})\n")
GIT_VERSION = re.compile(r"^git version ([0-9]+)\.([0-9]+)[0-9A-Za-z.\-]*$")
DOCSTRING_START = re.compile(
    r"\A(?:#![^\n]*\n)?[ \t\r\n]*[rRuU]?(?:\x22{3}|\x27{3})([^\r\n]*)")
DIGIT_RUN = re.compile(r"[0-9]{5,}")
HEX_RUN = re.compile(r"[0-9A-Fa-f]{16,}")
REGULAR_MODES = ("100644", "100755")
LINK_MODE = "120000"
GITLINK_MODE = "160000"
TREE_MODE = "040000"

SCOPES = ("ignore-probes", "candidates", "staged", "history")
COMMIT_LABEL = "<commit object>"
TAG_LABEL = "<annotated tag object>"
PATHLESS_LABEL = "<blob outside every commit tree>"

# --------------------------------------------------------------------------
# Fixed rules. The policy file must declare these; it may tighten them
# (fewer publishable types or placeholders, more forbidden entries, probes
# or signatures) but never weaken them.
# --------------------------------------------------------------------------

# Ceilings: the policy may list a subset only.
PUBLISHABLE_EXTENSIONS = (".cfg", ".ini", ".json", ".md", ".ps1", ".py",
                          ".pyi", ".sql", ".toml", ".txt", ".yaml", ".yml")
PUBLISHABLE_NAMES = (".editorconfig", ".env.example", ".gitattributes",
                     ".gitignore", "license", "notice")
# The interface's page files are publishable in its own directory only.
PAGE_DIRECTORY = "src/ehr_evidence_app/interface/static"
PAGE_EXTENSIONS = (".css", ".html", ".js", ".svg")
FORBIDDEN_NAME_EXCEPTIONS = (".env.example",)

# Mandatory minimums: the policy must list at least these. Names that are
# unambiguously generated or private are refused at any depth; data,
# models and similar names are refused at the root only, so source and
# test packages such as tests/data or app/models stay trackable.
FORBIDDEN_ANY_COMPONENTS = (
    "__pycache__", ".aws", ".azure", ".cache", ".clearml", ".conda",
    ".docker", ".eggs", ".gnupg",
    ".huggingface", ".hypothesis", ".idea", ".ipynb_checkpoints", ".kube",
    ".mypy_cache", ".nox", ".pytest_cache", ".ruff_cache", ".ssh", ".tox",
    ".venv", ".vs", ".vscode", "audit", "audits", "cache", "caches",
    "cases", "checkpoints", "clearml", "environments", "evaluation",
    "evaluations", "gcloud", "htmlcov", "lightning_logs", "logs", "mlruns",
    "node_modules", "output", "outputs", "patient-data",
    "patient-graphs", "patient_data", "patient_graphs", "protected-data",
    "protected_data", "results", "runs", "site-packages", "source-exports",
    "source_exports", "venv", "wandb")
FORBIDDEN_ROOT_COMPONENTS = (
    "build", "data", "dist", "env", "envs", "exports", "extracts", "graphs",
    "models", "patients", "private", "secrets", "temp", "tmp")
FORBIDDEN_COMPONENT_SUFFIXES = (".egg-info",)
FORBIDDEN_EXTENSIONS = (
    ".7z", ".arrow", ".avro", ".bak", ".bin", ".bz2", ".ckpt", ".crdownload",
    ".csv", ".db", ".dcm", ".dta", ".duckdb", ".feather", ".ggml", ".gguf",
    ".gz", ".h5", ".hdf5", ".jks", ".joblib", ".jsonl", ".kdbx", ".key",
    ".keystore", ".lmdb", ".log", ".mdb", ".model", ".ndjson", ".npy",
    ".npz", ".onnx", ".orc", ".p12", ".parquet", ".part", ".partial", ".pem",
    ".pfx", ".pickle", ".pkl", ".ppk", ".pt", ".pth", ".pyc", ".pyd", ".pyo",
    ".rar", ".rdata", ".rds", ".safetensors", ".sas7bdat", ".sav", ".sqlite",
    ".sqlite3", ".swo", ".swp", ".tar", ".temp", ".tgz", ".tmp", ".tsv",
    ".xls", ".xlsx", ".xz", ".zip", ".zst")
FORBIDDEN_NAME_GLOBS = (
    "*.credentials", "*.db-journal", "*.secret", "*.secrets", "*.sqlite*",
    "*.token", "*~", ".coverage", ".coverage.*", ".ds_store", ".env",
    ".env.*", ".netrc", ".npmrc", ".pg_service.conf", ".pgpass", ".pypirc",
    "_netrc", "clearml.conf", "coverage.xml", "credentials",
    "credentials.*", "desktop.ini", "id_dsa", "id_ecdsa", "id_ed25519",
    "id_rsa", "pg_service.conf", "pgpass.conf", "secret.*", "secrets.*",
    "service-account*.json", "thumbs.db", "token.*", "tokens.*", "~$*")

# Synthetic probe paths checked through git check-ignore --no-index.
IGNORED_PROBES = (
    # protected, source, patient, case, evaluation, audit and output data
    "protected-data/probe.txt", "nested/protected-data/probe.txt",
    "nested/patient-data/probe.txt", "nested/patient_graphs/probe.txt",
    "nested/source-exports/probe.txt", "data/probe.txt",
    "cases/probe.json", "nested/cases/probe.json",
    "evaluation/probe.json", "nested/evaluation/probe.json",
    "evaluations/probe.json", "nested/evaluations/probe.json",
    "audit/probe.json", "nested/audit/probe.json",
    "audits/probe.json", "nested/audits/probe.json",
    "outputs/probe.json", "nested/outputs/probe.json",
    "output/probe.json", "nested/output/probe.json",
    "results/probe.json", "nested/results/probe.json",
    "runs/probe.txt", "nested/runs/probe.txt",
    "logs/probe.txt", "nested/logs/probe.txt", "graphs/probe.txt",
    # graphs, checkpoints, weights and serialised data
    "models/probe.txt", "nested/checkpoints/probe.txt", "probe.pt",
    "nested/dir/probe.pt", "probe.pth", "probe.ckpt",
    "nested/dir/probe.safetensors", "probe.onnx", "probe.bin", "probe.npy",
    "probe.npz", "probe.pkl", "probe.pickle", "probe.joblib", "probe.h5",
    "probe.hdf5", "probe.arrow", "probe.feather", "probe.parquet",
    "nested/dir/probe.parquet", "probe.db", "probe.sqlite", "probe.sqlite3",
    "probe.duckdb", "probe.csv", "nested/dir/probe.csv", "probe.tsv",
    # logs, response and trace files, archives, temporary and partial files
    "probe.log", "nested/dir/probe.log", "probe.jsonl",
    "nested/dir/probe.jsonl", "probe.zip", "probe.tar.gz", "probe.7z",
    "probe.tmp", "probe.partial", "nested/dir/probe.part", "probe.bak",
    "temp/probe.txt",
    # caches, build artefacts and notebook checkpoints
    "__pycache__/probe.cpython-38.pyc", "nested/pkg/__pycache__/probe.pyc",
    "probe.pyc", ".pytest_cache/probe", ".mypy_cache/probe",
    ".ruff_cache/probe", "cache/probe.txt", "nested/cache/probe.txt",
    "caches/probe.txt", "nested/caches/probe.txt", "nested/.cache/probe",
    "build/probe.txt", "dist/probe.whl", "probe.egg-info/PKG-INFO",
    "nested/.ipynb_checkpoints/probe.ipynb", ".coverage",
    "htmlcov/probe.html",
    # environments, local package and model caches, trackers
    "environments/probe.txt", "nested/environments/probe.txt",
    ".venv/pyvenv.cfg", "nested/.venv/pyvenv.cfg", "venv/pyvenv.cfg",
    ".conda/probe", "node_modules/probe/index.js",
    "nested/node_modules/probe/index.js", "mlruns/probe", "wandb/probe",
    "lightning_logs/probe", "clearml/probe.txt", "nested/clearml/probe.txt",
    ".clearml/probe",
    # secrets and credentials
    ".env", "nested/dir/.env", ".env.local", "nested/dir/.env.production",
    "probe.pem", "nested/dir/probe.key", "id_rsa", "nested/.ssh/probe",
    "probe.token", "credentials.json", "nested/dir/secrets.toml", ".pgpass",
    "pgpass.conf", "pg_service.conf", ".pg_service.conf", "clearml.conf",
    "nested/dir/clearml.conf", ".netrc", "_netrc", ".pypirc",
    ".aws/credentials", ".azure/probe", ".config/gcloud/probe", "probe.p12",
    "probe.pfx",
    # local tool, editor and OS state
    ".tool-state/settings.json", ".vscode/settings.json",
    ".idea/workspace.xml", ".DS_Store", "Thumbs.db")
TRACKABLE_PROBES = (
    "README.md", "CHANGELOG.md", "pyproject.toml", ".gitignore",
    ".env.example",
    "config/probe.json", "docs/PROBE.md", "scripts/probe_tool.py",
    "nested/pkg/probe_module.py", "app/models/probe_model.py",
    "tests/test_probe.py", "tests/data/synthetic_probe.json",
    "tests/fixtures/synthetic_probe.json")

# High-confidence signatures: (rule ID, category, pattern, placeholder
# exemption). \x22, \x27 and \x60 are the double quote, single quote and
# backtick. A named group "secret" marks the value checked for a
# placeholder; otherwise the whole match is checked. Token shapes and
# private keys are never exempt, whatever text they contain.
SIGNATURES = (
    ("private_key_block", "private_key",
     r"-{5}BEGIN(?: [A-Z0-9]+)* PRIVATE KEY(?: BLOCK)?-{5}", False),
    ("putty_private_key", "private_key", r"PuTTY-User-Key-File-[0-9]+:",
     False),
    ("aws_access_key_id", "token", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b", False),
    ("github_token", "token",
     r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})",
     False),
    ("llm_provider_api_key", "token",
     r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}", False),
    ("huggingface_token", "token", r"\bhf_[A-Za-z0-9]{34,}\b", False),
    ("slack_token", "token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}", False),
    ("google_api_key", "token", r"\bAIza[0-9A-Za-z_-]{35}", False),
    ("stripe_live_key", "token", r"\b[rs]k_live_[0-9A-Za-z]{16,}", False),
    ("json_web_token", "token",
     r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}"
     r"\.[A-Za-z0-9_-]{10,}", False),
    ("credential_uri", "credential_uri",
     r"\b[A-Za-z][A-Za-z0-9+.-]{1,30}://[^\s:/?#@\x22\x27\x60]*:"
     r"(?P<secret>[^\s/?#@\x22\x27\x60]+)@", True),
    # A quoted value of 8+ characters, spaces allowed, on one line.
    ("secret_assignment", "secret_assignment",
     r"(?i)\b[A-Za-z0-9_]*(?:password|passwd|passphrase|secret|"
     r"api[_-]?key|access[_-]?key|auth[_-]?token|access[_-]?token|"
     r"api[_-]?token|client[_-]?secret|private[_-]?key)[A-Za-z0-9_]*"
     r"[\x22\x27]?[ \t]*[:=][ \t]*(?P<quote>[\x22\x27])"
     r"(?P<secret>(?:(?!(?P=quote))[^\r\n]){8,})(?P=quote)", True),
    # An unquoted .env, shell, YAML or INI value of 8+ characters that
    # fills the rest of its line (an optional # or ; comment may follow).
    # The key must end with the keyword. Not applied to Python sources,
    # where an unquoted value is an expression; quoted literals there are
    # covered by secret_assignment.
    ("unquoted_secret_assignment", "unquoted_assignment",
     r"(?im)^[ \t]*(?:-[ \t]+)?(?:export[ \t]+|set[ \t]+|\$env:)?"
     r"[A-Za-z0-9_.-]*(?:password|passwd|passphrase|secret|token|"
     r"api[_-]?key|access[_-]?key|secret[_-]?key|private[_-]?key|"
     r"client[_-]?secret)[ \t]*[:=][ \t]*"
     r"(?P<secret>[^\s\x22\x27\x60#;()\[\]{},<>$%*]"
     r"[^\s\x22\x27\x60()\[\]{},]{7,})[ \t]*(?:[#;][^\r\n]*)?\r?$", True),
)
SIGNATURE_CATEGORIES = ("credential_uri", "private_key", "secret_assignment",
                        "token", "unquoted_assignment")
UNQUOTED_EXEMPT_EXTENSIONS = (".py", ".pyi")

USER_PATH_ID = "machine_user_path"
USER_PATH_PATTERN = (r"(?i)\b[A-Z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/)"
                     r"(?P<account><[^<>\r\n]*>|[^\\/\s<>\x22\x27\x60]+)")
ALLOWED_ACCOUNTS = ("All", "Default", "Public")

PATIENT_RULE_ID = "patient_identifier_assignment"
PATIENT_PATTERN = (
    r"(?i)(?:(?<![A-Za-z0-9_])(?:person_id|subject_id|hadm_id|"
    r"patient_index)[\x22\x27]?\s*[:=]\s*[\x22\x27]?|"
    r"--(?:person|subject|hadm)[_-]id[ =][\x22\x27]?|"
    r"--patient[_-]index[ =][\x22\x27]?)[0-9]{2,}(?![0-9A-Za-z_])")
SYNTHETIC_FIXTURE_ROOTS = ("tests",)
SYNTHETIC_DECLARATION = "synthetic"

# Markers count only as whole tokens (no letter or digit on either side).
PLACEHOLDER_MARKERS = ("CHANGEME", "EXAMPLE", "PLACEHOLDER", "REDACTED",
                       "SYNTHETIC")
# Full-value placeholder forms.
PLACEHOLDER_PATTERNS = (
    r"<[^<>\r\n]+>",
    r"\$\{[A-Za-z_][A-Za-z0-9_]*\}",
    r"\$(?:env:)?[A-Za-z_][A-Za-z0-9_]*",
    r"%[A-Za-z_][A-Za-z0-9_]*%",
    r"\*+|x{3,}|X{3,}|\.{3,}",
)

PATH_RULE_IDS = ("unsafe_path", "git_metadata_path", "forbidden_directory",
                 "forbidden_extension", "forbidden_name", "unapproved_type")
ENTRY_RULE_IDS = ("link_or_reparse_point", "gitlink_entry",
                  "unsupported_entry", "file_ancestor", "not_regular_file",
                  "missing_file", "unreadable_file", "changed_during_read")
CONTENT_RULE_IDS = ("oversized", "binary_content", "not_utf8", USER_PATH_ID,
                    PATIENT_RULE_ID)
PROBE_RULE_IDS = ("ignore_probe_not_ignored", "ignore_rule_outside_gitignore",
                  "trackable_probe_ignored")
RESERVED_RULE_IDS = frozenset(PATH_RULE_IDS + ENTRY_RULE_IDS
                              + CONTENT_RULE_IDS + PROBE_RULE_IDS)
RULE_ID = re.compile(r"^[a-z][a-z0-9_]{2,40}$")

TOP_REQUIRED = ("schema_version", "step", "purpose", "max_candidate_bytes",
                "publishable", "forbidden", "ignore_probes", "signatures",
                "user_path", "patient_identifier", "placeholders")
TOP_OPTIONAL = ("notes",)
PUBLISHABLE_KEYS = ("extensions", "names")
FORBIDDEN_KEYS = ("any_components", "root_components", "component_suffixes",
                  "extensions", "name_globs", "name_exceptions")
PROBE_KEYS = ("ignored", "trackable")
SIGNATURE_KEYS = ("id", "category", "pattern", "exempt_placeholders")
USER_PATH_KEYS = ("id", "pattern", "allowed_accounts")
PATIENT_KEYS = ("id", "pattern", "synthetic_fixture_roots",
                "synthetic_declaration", "declaration_window_bytes")
PLACEHOLDER_KEYS = ("markers", "patterns")

FIXED_SIGNATURE_PATTERNS = tuple(re.compile(pattern)
                                 for _, _, pattern, _ in SIGNATURES)


class OperationalError(Exception):
    """A failure that prevents a trustworthy audit; never a clean result."""


class PolicyError(OperationalError):
    """The policy is unreadable, malformed or weaker than the fixed rules."""


class Rules(object):
    """Validated policy values and compiled patterns for one audit run."""

    def __init__(self, **values):
        """Store the validated values as attributes."""
        self.__dict__.update(values)


def sanitise_error(error):
    """Describe an unexpected failure without repeating any value."""
    if isinstance(error, OSError):
        return "%s: %s" % (type(error).__name__, error.strerror or "OS error")
    return "%s (message withheld to avoid exposing content)" % type(
        error).__name__


def bounded_token(words):
    """Compile a case-insensitive pattern matching any word as a token."""
    return re.compile(r"(?i)(?<![A-Za-z0-9])(?:%s)(?![A-Za-z0-9])"
                      % "|".join(re.escape(word) for word in words))


# --------------------------------------------------------------------------
# Fixed roots and read-only filesystem inspection
# --------------------------------------------------------------------------

def fixed_policy_problem():
    """Return why the roots fixed in this module are unsafe, else None.

    The audit only ever inspects the repository that contains it, so the
    checkout may live anywhere (the project location or a CI runner) as long
    as it is a plain local path that overlaps neither the private workspace
    nor the runtime root.
    """
    if os.name != "nt":
        return "the audit supports Windows only"
    if sys.version_info < (3, 8):
        return ("Python 3.8 or later is required to inspect junctions "
                "without following them")
    for label, path in (("repository root", REPO_ROOT),
                        ("configuration directory", CONFIG_DIR)):
        problem = guard.path_problem(path)
        if problem:
            return "%s: %s" % (label, problem)
    for label, root in (("private workspace", PRIVATE_ROOT),
                        ("runtime root", RUNTIME_ROOT)):
        if guard.is_within(REPO_ROOT, root) or guard.is_within(root,
                                                               REPO_ROOT):
            return "the repository and the %s overlap" % label
    if ntpath.normcase(CONFIG_DIR) != ntpath.normcase(REPO_ROOT + "\\config"):
        return "the configuration directory is not the repository config"
    return None


def walk_problem(path, kind, label):
    """Return why path is not an existing plain entry of kind, else None.

    Walks from the drive root with lstat, so no link is followed: every
    component must exist, none may be a symlink, junction or other reparse
    point, ancestors must be directories and the leaf must be a 'directory'
    or a regular 'file'. Nothing is opened.
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
            return "%s: %s is missing" % (label, current)
        except OSError as error:
            return "%s: cannot inspect %s safely (%s)" % (
                label, current, type(error).__name__)
        if guard.is_link_like(info):
            return "%s: %s is a symlink, junction or other reparse point" % (
                label, current)
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            return "%s: ancestor %s is not a directory" % (label, current)
    if kind == "directory" and not stat.S_ISDIR(info.st_mode):
        return "%s: %s is not a directory" % (label, path)
    if kind == "file" and not stat.S_ISREG(info.st_mode):
        return "%s: %s is not a regular file" % (label, path)
    return None


def repository_problem():
    """Return why the repository root or its .git is not a plain directory.

    A .git file (gitfile), link or reparse point could redirect Git to
    another repository, so it is refused.
    """
    return (walk_problem(REPO_ROOT, "directory", "repository root")
            or walk_problem(os.path.join(REPO_ROOT, ".git"), "directory",
                            ".git"))


def history_boundary_problem():
    """Return why reachable history would be incomplete, else None.

    Any entry at .git\\shallow (a shallow boundary) or .git\\info\\grafts (a
    legacy graft file) is refused. Only lstat metadata is inspected; the
    entries are never opened or followed. A .git\\info that is a link,
    reparse point or non-directory is refused because grafts beneath it
    cannot be ruled out safely.
    """
    git_dir = os.path.join(REPO_ROOT, ".git")
    for parts, label in HISTORY_BOUNDARY_FILES:
        shown = ".git\\" + "\\".join(parts)
        current = git_dir
        for index, part in enumerate(parts):
            current = os.path.join(current, part)
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                break
            except OSError as error:
                return "%s cannot be inspected safely (%s)" % (
                    shown, type(error).__name__)
            if index < len(parts) - 1:
                if (guard.is_link_like(info)
                        or not stat.S_ISDIR(info.st_mode)):
                    return ("%s is not a plain directory, so %s cannot be "
                            "ruled out" % (os.path.dirname(shown), shown))
            else:
                return ("the repository has %s (%s); its history would be "
                        "incomplete, so the audit is refused"
                        % (label, shown))
    return None


# --------------------------------------------------------------------------
# Policy
# --------------------------------------------------------------------------

def policy_path_problem(path):
    """Return why the policy file may not be read, else None."""
    if not isinstance(path, str):
        return "the policy path is not text"
    problem = guard.path_problem(path)
    if problem:
        return "policy path: %s" % problem
    if not guard.is_within(path, CONFIG_DIR, strict=True):
        return "the policy must be read from %s" % CONFIG_DIR
    return walk_problem(path, "file", "policy")


def reject_duplicate_keys(pairs):
    """Build a JSON object, refusing repeated keys that could hide values."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError("the policy repeats a JSON key")
        result[key] = value
    return result


def read_policy(path):
    """Return the parsed policy; errors never quote its content."""
    if os.lstat(path).st_size > MAX_POLICY_BYTES:
        raise PolicyError("the policy is larger than %d bytes"
                          % MAX_POLICY_BYTES)
    with open(path, "rb") as handle:
        data = handle.read(MAX_POLICY_BYTES + 1)
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise PolicyError("the policy is not valid UTF-8")
    try:
        return json.loads(text, object_pairs_hook=reject_duplicate_keys)
    except ValueError:
        raise PolicyError("the policy is not valid JSON")


def check_keys(value, required, optional, label):
    """Raise PolicyError when an object lacks or adds keys."""
    if not isinstance(value, dict):
        raise PolicyError("%s must be an object" % label)
    missing = sorted(set(required) - set(value))
    if missing:
        raise PolicyError("%s lacks required keys: %s" % (label,
                                                          ", ".join(missing)))
    unknown = set(value) - set(required) - set(optional)
    if unknown:
        raise PolicyError("%s has %d unsupported key(s)" % (label,
                                                            len(unknown)))
    return value


def text_list(value, label, lower=False):
    """Return a list of distinct, trimmed, non-empty strings."""
    if (not isinstance(value, list)
            or not all(isinstance(item, str) and item and item == item.strip()
                       for item in value)):
        raise PolicyError("%s must be a list of non-empty trimmed text"
                          % label)
    if len(set(value)) != len(value):
        raise PolicyError("%s repeats an entry" % label)
    if lower and any(item != item.lower() for item in value):
        raise PolicyError("%s entries must be lower case" % label)
    return value


def require_superset(items, mandatory, label):
    """Raise PolicyError when the policy omits a mandatory entry."""
    missing = sorted(set(mandatory) - set(items))
    if missing:
        raise PolicyError("%s omits mandatory entries: %s; the policy cannot "
                          "weaken them" % (label, ", ".join(missing)))


def require_subset(items, ceiling, label):
    """Raise PolicyError when the policy widens a fixed ceiling."""
    extra = set(items) - set(ceiling)
    if extra:
        raise PolicyError("%s adds %d entr(ies) beyond the fixed ceiling; the "
                          "policy cannot widen it" % (label, len(extra)))


def compile_signatures(entries):
    """Validate signature entries; return (id, category, compiled, exempt).

    Every fixed signature must appear with its exact category, pattern and
    exemption; extra signatures may only add findings.
    """
    if not isinstance(entries, list) or not entries:
        raise PolicyError("signatures must be a non-empty list")
    seen = {}
    for index, entry in enumerate(entries):
        label = "signatures[%d]" % index
        check_keys(entry, SIGNATURE_KEYS, (), label)
        rule_id = entry["id"]
        if not isinstance(rule_id, str) or not RULE_ID.match(rule_id):
            raise PolicyError("%s has an invalid id" % label)
        if rule_id in seen or rule_id in RESERVED_RULE_IDS:
            raise PolicyError("signature %s is repeated or reserved"
                              % rule_id)
        if entry["category"] not in SIGNATURE_CATEGORIES:
            raise PolicyError("signature %s has an unknown category"
                              % rule_id)
        if not isinstance(entry["exempt_placeholders"], bool):
            raise PolicyError("signature %s exempt_placeholders must be true "
                              "or false" % rule_id)
        pattern = entry["pattern"]
        if (not isinstance(pattern, str) or not pattern
                or len(pattern) > MAX_PATTERN_LENGTH):
            raise PolicyError("signature %s pattern must be text of at most "
                              "%d characters" % (rule_id, MAX_PATTERN_LENGTH))
        try:
            compiled = re.compile(pattern)
        except re.error:
            raise PolicyError("signature %s pattern does not compile"
                              % rule_id)
        if compiled.search(""):
            raise PolicyError("signature %s pattern matches empty text"
                              % rule_id)
        seen[rule_id] = (entry["category"], pattern,
                         entry["exempt_placeholders"], compiled)
    for rule_id, category, pattern, exempt in SIGNATURES:
        if rule_id not in seen:
            raise PolicyError("signatures omit mandatory signature %s"
                              % rule_id)
        if seen[rule_id][:3] != (category, pattern, exempt):
            raise PolicyError("mandatory signature %s differs from the fixed "
                              "rule; the policy cannot weaken it" % rule_id)
    return tuple((rule_id, value[0], value[3], value[2])
                 for rule_id, value in seen.items())


def compile_probes(section):
    """Validate the probe lists; return (ignored, trackable) tuples."""
    check_keys(section, PROBE_KEYS, (), "ignore_probes")
    ignored = text_list(section["ignored"], "ignore_probes.ignored")
    trackable = text_list(section["trackable"], "ignore_probes.trackable")
    require_superset(ignored, IGNORED_PROBES, "ignore_probes.ignored")
    require_superset(trackable, TRACKABLE_PROBES, "ignore_probes.trackable")
    if set(ignored) & set(trackable):
        raise PolicyError("a probe path is listed as both ignored and "
                          "trackable")
    for probe in ignored + trackable:
        if git_path_problem(probe):
            raise PolicyError("an ignore probe is not a safe "
                              "repository-relative path")
    return tuple(ignored), tuple(trackable)


def compile_forbidden(section):
    """Validate the forbidden categories; return them as a dictionary."""
    check_keys(section, FORBIDDEN_KEYS, (), "forbidden")
    values = {}
    for key, mandatory in (("any_components", FORBIDDEN_ANY_COMPONENTS),
                           ("root_components", FORBIDDEN_ROOT_COMPONENTS),
                           ("component_suffixes",
                            FORBIDDEN_COMPONENT_SUFFIXES),
                           ("extensions", FORBIDDEN_EXTENSIONS),
                           ("name_globs", FORBIDDEN_NAME_GLOBS)):
        items = text_list(section[key], "forbidden." + key, lower=True)
        require_superset(items, mandatory, "forbidden." + key)
        values[key] = items
    if not all(item.startswith(".") for item in values["extensions"]):
        raise PolicyError("forbidden.extensions entries must start with '.'")
    exceptions = text_list(section["name_exceptions"],
                           "forbidden.name_exceptions", lower=True)
    require_subset(exceptions, FORBIDDEN_NAME_EXCEPTIONS,
                   "forbidden.name_exceptions")
    values["name_exceptions"] = exceptions
    return values


def compile_rules(policy):
    """Validate a parsed policy against the fixed rules; return Rules.

    The schema is closed. Ceilings (publishable types, name exceptions,
    allowed accounts, fixture roots, placeholders) may only shrink;
    mandatory lists (forbidden categories, probes, signatures) may only
    grow; the user-path and patient-identifier patterns are fixed.
    Messages name keys and rule IDs only, never policy values.
    """
    check_keys(policy, TOP_REQUIRED, TOP_OPTIONAL, "policy")
    if policy["schema_version"] != SCHEMA_VERSION:
        raise PolicyError("schema_version must be %s" % SCHEMA_VERSION)
    if policy["step"] != STEP:
        raise PolicyError("step must be \"%s\"" % STEP)
    if not isinstance(policy["purpose"], str) or not policy["purpose"].strip():
        raise PolicyError("purpose must be non-empty text")
    text_list(policy.get("notes", []), "notes")
    max_bytes = policy["max_candidate_bytes"]
    if (not isinstance(max_bytes, int) or isinstance(max_bytes, bool)
            or not 1 <= max_bytes <= HARD_MAX_CANDIDATE_BYTES):
        raise PolicyError("max_candidate_bytes must be an integer from 1 to "
                          "%d" % HARD_MAX_CANDIDATE_BYTES)

    publishable = check_keys(policy["publishable"], PUBLISHABLE_KEYS, (),
                             "publishable")
    extensions = text_list(publishable["extensions"],
                           "publishable.extensions", lower=True)
    require_subset(extensions, PUBLISHABLE_EXTENSIONS,
                   "publishable.extensions")
    names = text_list(publishable["names"], "publishable.names", lower=True)
    require_subset(names, PUBLISHABLE_NAMES, "publishable.names")

    forbidden = compile_forbidden(policy["forbidden"])
    ignored, trackable = compile_probes(policy["ignore_probes"])
    signatures = compile_signatures(policy["signatures"])

    user = check_keys(policy["user_path"], USER_PATH_KEYS, (), "user_path")
    if user["id"] != USER_PATH_ID:
        raise PolicyError("user_path.id must be %s" % USER_PATH_ID)
    if user["pattern"] != USER_PATH_PATTERN:
        raise PolicyError("user_path.pattern differs from the fixed rule; "
                          "the policy cannot weaken it")
    accounts = text_list(user["allowed_accounts"],
                         "user_path.allowed_accounts")
    require_subset([item.lower() for item in accounts],
                   [item.lower() for item in ALLOWED_ACCOUNTS],
                   "user_path.allowed_accounts")

    patient = check_keys(policy["patient_identifier"], PATIENT_KEYS, (),
                         "patient_identifier")
    if patient["id"] != PATIENT_RULE_ID:
        raise PolicyError("patient_identifier.id must be %s"
                          % PATIENT_RULE_ID)
    if patient["pattern"] != PATIENT_PATTERN:
        raise PolicyError("patient_identifier.pattern differs from the "
                          "fixed rule; the policy cannot weaken it")
    roots = text_list(patient["synthetic_fixture_roots"],
                      "patient_identifier.synthetic_fixture_roots",
                      lower=True)
    require_subset(roots, SYNTHETIC_FIXTURE_ROOTS,
                   "patient_identifier.synthetic_fixture_roots")
    if patient["synthetic_declaration"] != SYNTHETIC_DECLARATION:
        raise PolicyError("patient_identifier.synthetic_declaration must be "
                          "%s" % SYNTHETIC_DECLARATION)
    window = patient["declaration_window_bytes"]
    if (not isinstance(window, int) or isinstance(window, bool)
            or not 1 <= window <= MAX_DECLARATION_WINDOW):
        raise PolicyError("patient_identifier.declaration_window_bytes must "
                          "be an integer from 1 to %d"
                          % MAX_DECLARATION_WINDOW)

    placeholders = check_keys(policy["placeholders"], PLACEHOLDER_KEYS, (),
                              "placeholders")
    markers = text_list(placeholders["markers"], "placeholders.markers")
    require_subset(markers, PLACEHOLDER_MARKERS, "placeholders.markers")
    patterns = text_list(placeholders["patterns"], "placeholders.patterns")
    require_subset(patterns, PLACEHOLDER_PATTERNS, "placeholders.patterns")

    return Rules(
        max_bytes=max_bytes,
        publishable_extensions=frozenset(extensions),
        publishable_names=frozenset(names),
        any_components=frozenset(forbidden["any_components"]),
        root_components=frozenset(forbidden["root_components"]),
        component_suffixes=tuple(forbidden["component_suffixes"]),
        forbidden_extensions=frozenset(forbidden["extensions"]),
        name_globs=tuple(forbidden["name_globs"]),
        name_exceptions=frozenset(forbidden["name_exceptions"]),
        ignored_probes=ignored,
        trackable_probes=trackable,
        signatures=signatures,
        user_path=re.compile(USER_PATH_PATTERN),
        allowed_accounts=frozenset(item.lower() for item in accounts),
        patient=re.compile(PATIENT_PATTERN),
        fixture_roots=frozenset(roots),
        declaration=bounded_token([SYNTHETIC_DECLARATION]),
        window=window,
        markers=bounded_token(markers) if markers else None,
        placeholders=tuple(re.compile(item) for item in patterns))


def load_rules(path):
    """Read the policy from the repository config directory; return Rules."""
    problem = policy_path_problem(path)
    if problem:
        raise PolicyError(problem)
    return compile_rules(read_policy(path))


# --------------------------------------------------------------------------
# Path rules
# --------------------------------------------------------------------------

def git_path_problem(relative):
    """Return a rule ID when a Git path is unsafe or names .git, else None.

    Git paths use '/'. Rooted paths, backslashes, empty, '.' or '..'
    components, reserved characters (including ':' streams), reserved device
    names, 8.3-like aliases and trailing dots or spaces are unsafe; the
    joined path must stay strictly beneath the repository.
    """
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or relative.startswith("/")):
        return "unsafe_path"
    parts = relative.split("/")
    if any(part.lower() == ".git" for part in parts):
        return "git_metadata_path"
    if any(guard.component_problem(part) for part in parts):
        return "unsafe_path"
    joined = REPO_ROOT + "\\" + "\\".join(parts)
    if (guard.path_problem(joined)
            or not guard.is_within(joined, REPO_ROOT, strict=True)):
        return "unsafe_path"
    return None


def path_findings(relative, rules):
    """Return the path-category rule IDs for one repository-relative path.

    Forbidden directories, extensions and names are reported even when the
    file was force-added; anything else must be a publishable type.
    """
    problem = git_path_problem(relative)
    if problem:
        return [problem]
    parts = relative.split("/")
    directories = [part.lower() for part in parts[:-1]]
    name = parts[-1].lower()
    found = []
    if ((directories and directories[0] in rules.root_components)
            or any(part in rules.any_components
                   or part.endswith(rules.component_suffixes)
                   for part in directories)):
        found.append("forbidden_directory")
    extension = posixpath.splitext(name)[1]
    if extension in rules.forbidden_extensions:
        found.append("forbidden_extension")
    if (name not in rules.name_exceptions
            and any(fnmatch.fnmatchcase(name, glob)
                    for glob in rules.name_globs)):
        found.append("forbidden_name")
    page = (posixpath.dirname(relative) == PAGE_DIRECTORY
            and extension in PAGE_EXTENSIONS)
    if (not found and name not in rules.publishable_names
            and extension not in rules.publishable_extensions and not page):
        found.append("unapproved_type")
    return found


# --------------------------------------------------------------------------
# Content rules
# --------------------------------------------------------------------------

def is_placeholder(value, rules):
    """Return True for a documented placeholder or clearly synthetic value.

    A value qualifies when a placeholder marker appears in it as a whole
    token (no letter or digit on either side) or when the whole value is a
    placeholder form such as <name>, ${NAME}, $env:NAME, %NAME% or a mask.
    """
    if rules.markers is not None and rules.markers.search(value):
        return True
    return any(pattern.fullmatch(value) for pattern in rules.placeholders)


def synthetic_fixture(relative, text, rules):
    """Return True for an explicitly declared synthetic fixture.

    Qualifies only beneath a fixture root: a file whose name contains the
    declaration word as a whole token (synthetic_case.json, not
    nonsynthetic_case.json), or a test_*.py module whose opening docstring
    line declares it synthetic. Only the patient-identifier rule honours
    this; every other rule applies to every file.
    """
    parts = relative.split("/")
    if len(parts) < 2 or parts[0].lower() not in rules.fixture_roots:
        return False
    name = parts[-1]
    if rules.declaration.search(posixpath.splitext(name)[0]):
        return True
    if not fnmatch.fnmatchcase(name.lower(), "test_*.py"):
        return False
    match = DOCSTRING_START.match(text[:rules.window])
    return bool(match and rules.declaration.search(match.group(1)))


def content_findings(relative, data, rules):
    """Return content rule IDs (one per occurrence) for publishable text.

    relative is the repository path, or "" for a history object without a
    path. The data must be at most the policy size, free of NUL bytes and
    valid UTF-8. Matched values are examined only to decide placeholder
    status and are never returned.
    """
    if len(data) > rules.max_bytes:
        return ["oversized"]
    if b"\0" in data:
        return ["binary_content"]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return ["not_utf8"]
    extension = posixpath.splitext(relative.lower())[1]
    found = []
    for rule_id, category, pattern, exempt in rules.signatures:
        if (category == "unquoted_assignment"
                and extension in UNQUOTED_EXEMPT_EXTENSIONS):
            continue
        for match in pattern.finditer(text):
            value = match.group(0)
            if "secret" in pattern.groupindex and match.group("secret"):
                value = match.group("secret")
            if exempt and is_placeholder(value, rules):
                continue
            found.append(rule_id)
    for match in rules.user_path.finditer(text):
        account = match.group("account")
        if (account.lower() in rules.allowed_accounts
                or is_placeholder(account, rules)):
            continue
        found.append(USER_PATH_ID)
    if not synthetic_fixture(relative, text, rules):
        found.extend(PATIENT_RULE_ID
                     for _ in rules.patient.finditer(text))
    return found


# --------------------------------------------------------------------------
# Working-tree candidates
# --------------------------------------------------------------------------

def worktree_problem(relative):
    """Return a rule ID when a candidate is not a plain regular file.

    Walks from the repository root with lstat: no component may be a
    symlink, junction or other reparse point, every ancestor must be a
    directory and the leaf must be a regular file. Nothing is opened.
    """
    current = REPO_ROOT
    parts = relative.split("/")
    info = None
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return "missing_file"
        except OSError:
            return "unreadable_file"
        if guard.is_link_like(info):
            return "link_or_reparse_point"
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            return "file_ancestor"
    if not stat.S_ISREG(info.st_mode):
        return "not_regular_file"
    return None


def read_worktree_file(relative, max_bytes):
    """Return (rule ID, None) or (None, bytes) for a checked candidate.

    The leaf is inspected again with lstat immediately before opening: a
    link, reparse point or non-regular leaf observed then is refused and
    never opened, and an oversized file is not read. The opened handle must
    still be a regular file of the same size and identity. A replacement
    between that lstat and open remains a residual race.
    """
    path = os.path.join(REPO_ROOT, *relative.split("/"))
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return "missing_file", None
    except OSError:
        return "unreadable_file", None
    if guard.is_link_like(before):
        return "link_or_reparse_point", None
    if not stat.S_ISREG(before.st_mode):
        return "not_regular_file", None
    if before.st_size > max_bytes:
        return "oversized", None
    try:
        with open(path, "rb") as handle:
            after = os.fstat(handle.fileno())
            data = handle.read(max_bytes + 1)
    except OSError:
        return "unreadable_file", None
    if (not stat.S_ISREG(after.st_mode) or after.st_size != before.st_size
            or len(data) != after.st_size
            or (before.st_ino and after.st_ino
                and before.st_ino != after.st_ino)):
        return "changed_during_read", None
    return None, data


# --------------------------------------------------------------------------
# Git (read-only)
# --------------------------------------------------------------------------

def executable_problem(path):
    """Return why an existing git.exe path is unsafe to run, else None.

    Walks from the drive root with lstat, never following a link: no
    component may be a symlink, junction or other reparse point, every
    ancestor must be a directory and the leaf must be a regular file.
    """
    current = path[:3]
    parts = path[3:].split("\\")
    info = None
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except OSError as error:
            return "cannot inspect %s safely (%s)" % (current,
                                                      type(error).__name__)
        if guard.is_link_like(info):
            return ("%s is a symlink, junction or other reparse point"
                    % current)
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            return "ancestor %s is not a directory" % current
    if not stat.S_ISREG(info.st_mode):
        return "%s is not a regular file" % path
    return None


def locate_git():
    """Return the first existing git.exe on PATH once it passes every check.

    Only absolute drive-letter PATH entries are considered; relative, UNC,
    device and ambiguous entries are skipped and the current directory is
    never searched implicitly. The first git.exe that exists must lie
    outside the repository and be reachable without any link or reparse
    point, or the audit stops. PATH is not changed and nothing is run here.
    """
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        entry = entry.strip().strip('"').rstrip("\\")
        if not entry:
            continue
        candidate = entry + "\\" + GIT_EXECUTABLE_NAME
        if guard.path_problem(candidate):
            continue
        if not os.path.lexists(candidate):
            continue
        if guard.is_within(candidate, REPO_ROOT):
            raise OperationalError("a git.exe inside the repository is on "
                                   "PATH; it will not be run")
        problem = executable_problem(candidate)
        if problem:
            raise OperationalError("the first git.exe on PATH is unsafe to "
                                   "run: %s" % problem)
        return candidate
    raise OperationalError("git.exe was not found as a plain local path on "
                           "PATH")


def git_environment():
    """Return the git process environment without inherited GIT_* values.

    GIT_DIR, GIT_WORK_TREE, GIT_INDEX_FILE, GIT_OBJECT_DIRECTORY,
    GIT_CONFIG_* and every other GIT_* variable are dropped; replacement
    objects, prompts, optional locks and lazy fetches are disabled. No value
    is reported.
    """
    env = {name: value for name, value in os.environ.items()
           if not name.upper().startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
                "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1",
                "GIT_CEILING_DIRECTORIES": os.path.dirname(REPO_ROOT),
                "GCM_INTERACTIVE": "never"})
    return env


def git_command(git, args):
    """Return the full git argument list for one read-only subcommand."""
    command = [git] + list(GIT_GLOBAL_OPTIONS) + [
        "--git-dir=" + os.path.join(REPO_ROOT, ".git"),
        "--work-tree=" + REPO_ROOT]
    for setting in GIT_SETTINGS:
        command += ["-c", setting]
    return command + list(args)


def run_git(git, args, stdin=None, accept=(0,)):
    """Run one read-only git subcommand; return (exit status, stdout).

    No shell, explicit working directory and repository, bounded timeout.
    A launch failure, timeout, unexpected exit status or oversized output
    is an operational failure. stderr is captured and discarded.
    """
    name = args[0] if args else ""
    if name not in READ_ONLY_COMMANDS:
        raise OperationalError("refusing to run a git subcommand outside the "
                               "read-only set")
    options = {"cwd": REPO_ROOT, "env": git_environment(), "shell": False,
               "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
               "timeout": GIT_TIMEOUT_SECONDS, "check": False}
    if stdin is None:
        options["stdin"] = subprocess.DEVNULL
    else:
        options["input"] = stdin
    try:
        result = subprocess.run(git_command(git, args), **options)
    except subprocess.TimeoutExpired:
        raise OperationalError("git %s timed out after %d seconds"
                               % (name, GIT_TIMEOUT_SECONDS))
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise OperationalError("git %s could not be run (%s)"
                               % (name, type(error).__name__))
    if result.returncode not in accept:
        raise OperationalError("git %s failed with exit status %s"
                               % (name, result.returncode))
    output = result.stdout
    if not isinstance(output, bytes) or len(output) > MAX_GIT_OUTPUT_BYTES:
        raise OperationalError("git %s returned malformed or oversized "
                               "output" % name)
    return result.returncode, output


def malformed(label):
    """Return the operational error for malformed git output."""
    return OperationalError("git %s returned malformed output" % label)


def nul_records(data, label):
    """Split NUL-terminated git output into UTF-8 records, failing closed."""
    if not data:
        return []
    if not data.endswith(b"\0"):
        raise malformed(label)
    try:
        return data[:-1].decode("utf-8").split("\0")
    except UnicodeDecodeError:
        raise malformed(label)


def path_records(data, label):
    """Return distinct non-empty NUL-terminated paths, in order."""
    records = nul_records(data, label)
    if any(not record for record in records):
        raise malformed(label)
    return list(dict.fromkeys(records))


def object_id_lines(data, label):
    """Return distinct object IDs from newline-separated git output."""
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError:
        raise malformed(label)
    if any(not OBJECT_ID.match(line) for line in lines):
        raise malformed(label)
    return list(dict.fromkeys(lines))


def git_version(git):
    """Return the validated git version text; old or odd output fails."""
    _, output = run_git(git, ["--version"])
    try:
        text = output.decode("ascii").strip()
    except UnicodeDecodeError:
        raise malformed("--version")
    match = GIT_VERSION.match(text)
    if not match:
        raise malformed("--version")
    if (int(match.group(1)), int(match.group(2))) < GIT_MINIMUM_VERSION:
        raise OperationalError("git %d.%d or later is required"
                               % GIT_MINIMUM_VERSION)
    return text[len("git version "):]


def ignore_probe_findings(git, rules):
    """Return findings from checking every probe with git check-ignore.

    The probes are passed as NUL-terminated stdin records (-z requires
    --stdin). --no-index checks patterns only. Output must be exactly four
    NUL-terminated fields per probe, in input order. A required probe must
    be ignored by a non-negated rule in the repository's root .gitignore
    (not info/exclude or a global excludes file); a trackable probe must
    not be ignored by any rule.
    """
    probes = rules.ignored_probes + rules.trackable_probes
    request = "".join(probe + "\0" for probe in probes).encode("utf-8")
    _, output = run_git(git, ["check-ignore", "--no-index", "--verbose",
                              "--non-matching", "-z", "--stdin"],
                        stdin=request, accept=(0, 1))
    records = nul_records(output, "check-ignore")
    if len(records) != 4 * len(probes):
        raise malformed("check-ignore")
    required = set(rules.ignored_probes)
    findings = []
    for index, probe in enumerate(probes):
        source, line, pattern, path = records[4 * index:4 * index + 4]
        if path != probe or (line and not line.isdigit()):
            raise malformed("check-ignore")
        if not pattern and (source or line):
            raise malformed("check-ignore")
        ignored = bool(pattern) and not pattern.startswith("!")
        if probe in required:
            if not ignored:
                findings.append(("ignore-probes", probe,
                                 "ignore_probe_not_ignored"))
            elif source != ".gitignore":
                findings.append(("ignore-probes", probe,
                                 "ignore_rule_outside_gitignore"))
        elif ignored:
            findings.append(("ignore-probes", probe,
                             "trackable_probe_ignored"))
    return findings


def object_info(git, oids):
    """Return {oid: (type, size)} via cat-file --batch-check, in order."""
    if not oids:
        return {}
    request = ("\n".join(oids) + "\n").encode("ascii")
    _, output = run_git(git, ["cat-file", "--batch-check"], stdin=request)
    try:
        lines = output.decode("ascii").split("\n")
    except UnicodeDecodeError:
        raise malformed("cat-file --batch-check")
    if lines[-1] != "" or len(lines) - 1 != len(oids):
        raise malformed("cat-file --batch-check")
    info = {}
    for oid, line in zip(oids, lines):
        match = BATCH_HEADER.match(line)
        if not match or match.group(1) != oid:
            raise malformed("cat-file --batch-check")
        info[oid] = (match.group(2), int(match.group(3)))
    return info


def read_objects(git, oids, info):
    """Return [(oid, bytes)] via cat-file --batch in bounded batches.

    Each object's type and size must match the earlier --batch-check.
    """
    batches = []
    current = []
    total = 0
    for oid in oids:
        if current and total + info[oid][1] > OBJECT_BATCH_BYTES:
            batches.append(current)
            current = []
            total = 0
        current.append(oid)
        total += info[oid][1]
    if current:
        batches.append(current)
    objects = []
    for batch in batches:
        request = ("\n".join(batch) + "\n").encode("ascii")
        _, output = run_git(git, ["cat-file", "--batch"], stdin=request)
        position = 0
        for oid in batch:
            kind, size = info[oid]
            end = output.find(b"\n", position)
            if end < 0:
                raise malformed("cat-file --batch")
            try:
                header = output[position:end].decode("ascii")
            except UnicodeDecodeError:
                raise malformed("cat-file --batch")
            match = BATCH_HEADER.match(header)
            if (not match or match.group(1) != oid
                    or match.group(2) != kind
                    or int(match.group(3)) != size):
                raise malformed("cat-file --batch")
            start = end + 1
            stop = start + size
            if output[stop:stop + 1] != b"\n":
                raise malformed("cat-file --batch")
            objects.append((oid, output[start:stop]))
            position = stop + 1
        if position != len(output):
            raise malformed("cat-file --batch")
    return objects


def object_entry_findings(scope, entries, git, rules):
    """Audit (path, mode, oid) entries from the index or a tree.

    Paths are checked first. Links, gitlinks and other non-file modes are
    findings and are not read. Remaining entries must be blobs; they are
    size-checked, read from the object database and scanned once per path.
    Returns (findings, number of distinct blobs scanned).
    """
    findings = []
    paths_by_blob = {}
    for path, mode, oid in entries:
        rule_ids = path_findings(path, rules)
        if mode not in REGULAR_MODES:
            rule_ids = [rule_id for rule_id in rule_ids
                        if rule_id != "unapproved_type"]
            rule_ids.append({LINK_MODE: "link_or_reparse_point",
                             GITLINK_MODE: "gitlink_entry"}.get(
                                 mode, "unsupported_entry"))
        if rule_ids:
            findings.extend((scope, path, rule_id) for rule_id in rule_ids)
        else:
            paths_by_blob.setdefault(oid, []).append(path)
    info = object_info(git, list(paths_by_blob))
    if any(kind != "blob" for kind, _ in info.values()):
        raise malformed("cat-file --batch-check")
    readable = []
    for oid, paths in paths_by_blob.items():
        if info[oid][1] > rules.max_bytes:
            findings.extend((scope, path, "oversized") for path in paths)
        else:
            readable.append(oid)
    for oid, data in read_objects(git, readable, info):
        for path in paths_by_blob[oid]:
            findings.extend((scope, path, rule_id)
                            for rule_id in content_findings(path, data,
                                                            rules))
    return findings, len(readable)


def candidate_scope(git, rules):
    """Audit current Git candidates from the working tree.

    Returns (candidate paths, findings). A path that fails a path rule is
    never opened.
    """
    _, output = run_git(git, ["ls-files", "--cached", "--others",
                              "--exclude-standard", "-z"])
    candidates = path_records(output, "ls-files")
    findings = []
    for relative in candidates:
        rule_ids = path_findings(relative, rules)
        if rule_ids:
            findings.extend(("candidates", relative, rule_id)
                            for rule_id in rule_ids)
            continue
        problem = worktree_problem(relative)
        data = None
        if not problem:
            problem, data = read_worktree_file(relative, rules.max_bytes)
        if problem:
            findings.append(("candidates", relative, problem))
            continue
        findings.extend(("candidates", relative, rule_id)
                        for rule_id in content_findings(relative, data,
                                                        rules))
    return candidates, findings


def staged_scope(git, rules):
    """Audit every index entry; return (paths, blobs scanned, findings)."""
    _, output = run_git(git, ["ls-files", "--stage", "-z"])
    entries = []
    for record in nul_records(output, "ls-files --stage"):
        match = STAGE_ENTRY.match(record)
        if not match:
            raise malformed("ls-files --stage")
        entries.append((match.group(4), match.group(1), match.group(2)))
    entries = list(dict.fromkeys(entries))
    findings, blobs = object_entry_findings("staged", entries, git, rules)
    paths = list(dict.fromkeys(path for path, _, _ in entries))
    return paths, blobs, findings


def tree_entries(git, tree_ish, entries, covered):
    """Add ls-tree -r -t entries of a commit or tree; mark subtrees covered.

    File, link and gitlink entries go into entries as (path, mode, oid);
    subtree IDs go into covered so they are not listed again.
    """
    _, output = run_git(git, ["ls-tree", "-r", "-t", "-z", "--full-tree",
                              tree_ish])
    for record in nul_records(output, "ls-tree"):
        match = TREE_ENTRY.match(record)
        if not match:
            raise malformed("ls-tree")
        mode, kind, oid, path = match.groups()
        if kind == "tree":
            if mode != TREE_MODE:
                raise malformed("ls-tree")
            covered.add(oid)
        else:
            entries.add((path, mode, oid))


def history_scope(git, rules):
    """Audit every object reachable from all refs and reflogs.

    rev-list --objects (with replacement objects disabled) lists every
    reachable commit, tree, blob and annotated tag; cat-file confirms each
    type. Commit and annotated-tag objects (messages and metadata) are
    scanned as text. Every commit tree, and every tree reachable only
    through a ref or tag, is listed with its paths; those paths and blobs
    are audited like index entries. Blobs outside every tree listing are
    scanned as text too. Nothing is checked out and no object ID, message
    or content is reported. Returns (evidence tuple, findings).
    """
    _, output = run_git(git, ["rev-list", "--objects", "--no-object-names",
                              "--all", "--reflog"])
    oids = object_id_lines(output, "rev-list")
    info = object_info(git, oids)
    commits = [oid for oid in oids if info[oid][0] == "commit"]
    tags = [oid for oid in oids if info[oid][0] == "tag"]
    trees = sorted(oid for oid in oids if info[oid][0] == "tree")
    blobs = [oid for oid in oids if info[oid][0] == "blob"]
    findings = []

    labels = dict((oid, COMMIT_LABEL) for oid in commits)
    labels.update((oid, TAG_LABEL) for oid in tags)
    readable = []
    for oid in commits + tags:
        if info[oid][1] > rules.max_bytes:
            findings.append(("history", labels[oid], "oversized"))
        else:
            readable.append(oid)
    covered = set()
    for oid, data in read_objects(git, readable, info):
        findings.extend(("history", labels[oid], rule_id)
                        for rule_id in content_findings("", data, rules))
        match = COMMIT_TREE.match(data)
        if labels[oid] == COMMIT_LABEL and match:
            covered.add(match.group(1).decode("ascii"))

    entries = set()
    for commit in commits:
        tree_entries(git, commit, entries, covered)
    detached = 0
    for tree in trees:
        if tree not in covered:
            detached += 1
            covered.add(tree)
            tree_entries(git, tree, entries, covered)
    entries = sorted(entries)
    found, scanned = object_entry_findings("history", entries, git, rules)
    findings += found

    named = set(oid for _, _, oid in entries)
    pathless = [oid for oid in blobs if oid not in named]
    readable = []
    for oid in pathless:
        if info[oid][1] > rules.max_bytes:
            findings.append(("history", PATHLESS_LABEL, "oversized"))
        else:
            readable.append(oid)
    for _, data in read_objects(git, readable, info):
        findings.extend(("history", PATHLESS_LABEL, rule_id)
                        for rule_id in content_findings("", data, rules))

    paths = set(path for path, _, _ in entries)
    evidence = (len(commits), len(tags), len(paths),
                scanned + len(readable), detached, len(pathless))
    return evidence, findings


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def display_path(path):
    """Return path for display with potentially sensitive parts redacted.

    Components with control characters, runs of five or more digits, runs
    of sixteen or more hexadecimal characters (object IDs, for example in
    Git notes paths), secret signatures or excessive length are replaced by
    <redacted>.
    """
    parts = []
    for part in path.split("/"):
        if (not part or len(part) > 120 or not part.isprintable()
                or DIGIT_RUN.search(part) or HEX_RUN.search(part)
                or any(pattern.search(part)
                       for pattern in FIXED_SIGNATURE_PATTERNS)):
            parts.append("<redacted>")
        else:
            parts.append(part)
    return "/".join(parts)


def report(evidence, findings):
    """Print scopes, counts and findings only; never content or values."""
    history = evidence["history"]
    print("Publishable content audit")
    print("Repository     : %s" % REPO_ROOT)
    print("Policy         : %s" % evidence["policy"])
    print("Git            : git.exe from PATH, version %s"
          % evidence["version"])
    print("Ignore probes  : %d must-ignore and %d must-track path(s) checked "
          "with git check-ignore --no-index" % evidence["probes"])
    print("Candidates     : %d path(s): %d tracked, %d untracked and not "
          "ignored" % evidence["candidates"])
    print("Tracked files  : %d (index entries)" % evidence["tracked"])
    print("Staged index   : %d entr(ies); %d blob(s) scanned"
          % evidence["staged"])
    print("History        : %d commit(s) and %d annotated tag(s) scanned; "
          "%d path(s); %d blob(s) scanned" % history[:4])
    print("Outside trees  : %d tree(s) named only by a ref or tag; %d "
          "blob(s) outside every tree listing" % history[4:])
    if history[0] == 0:
        print("History state  : no commits; zero commit history audited")
    if evidence["tracked"] == 0 and history[0] == 0:
        print("Evidence       : no commits and no staged or tracked files; "
              "every candidate is an untracked working-tree file")
    counts = {}
    for scope, path, rule_id in findings:
        key = (SCOPES.index(scope), display_path(path), rule_id)
        counts[key] = counts.get(key, 0) + 1
    for (scope, path, rule_id), count in sorted(counts.items()):
        print("FINDING [%s] %s: %s (%d)" % (SCOPES[scope], path, rule_id,
                                            count))
    if findings:
        print("RESULT: FINDINGS (%d finding(s) across %d path(s))"
              % (len(findings), len(set((key[0], key[1])
                                        for key in counts))))
    else:
        print("RESULT: PASS")


def build_parser():
    """Build the command-line parser; only a policy file may be chosen."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--policy", help="policy file inside the repository "
                        "config directory (default: config\\%s)"
                        % POLICY_NAME)
    return parser


def run(args):
    """Run every read-only check and return the process exit status."""
    problem = fixed_policy_problem()
    if problem:
        raise OperationalError("fixed policy: %s" % problem)
    policy_path = args.policy or os.path.join(CONFIG_DIR, POLICY_NAME)
    rules = load_rules(policy_path)
    problem = repository_problem() or history_boundary_problem()
    if problem:
        raise OperationalError(problem)
    git = locate_git()
    version = git_version(git)
    findings = ignore_probe_findings(git, rules)
    candidates, found = candidate_scope(git, rules)
    findings += found
    staged_paths, staged_blobs, found = staged_scope(git, rules)
    findings += found
    history, found = history_scope(git, rules)
    findings += found
    tracked = set(staged_paths)
    untracked = [path for path in candidates if path not in tracked]
    report({"policy": policy_path, "version": version,
            "probes": (len(rules.ignored_probes),
                       len(rules.trackable_probes)),
            "candidates": (len(candidates), len(candidates) - len(untracked),
                           len(untracked)),
            "tracked": len(tracked),
            "staged": (len(staged_paths), staged_blobs),
            "history": history}, findings)
    return EXIT_FINDINGS if findings else EXIT_PASS


def main(argv=None):
    """Run the audit, sanitising any failure into an operational result."""
    try:
        return run(build_parser().parse_args(argv))
    except OperationalError as error:
        print("OPERATIONAL FAILURE: %s" % error)
    except KeyboardInterrupt:
        print("OPERATIONAL FAILURE: interrupted before completion")
    except Exception as error:  # sanitised: text may quote file content
        print("OPERATIONAL FAILURE: %s" % sanitise_error(error))
    print("RESULT: OPERATIONAL FAILURE")
    return EXIT_OPERATIONAL


if __name__ == "__main__":
    sys.exit(main())
