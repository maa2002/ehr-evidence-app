"""Synthetic checks for ehr_evidence_app.quality.publication (Step 3).

Every repository used here is synthetic. Scratch Git repositories are
created strictly beneath the configured workspace test temp and receive synthetic
files and, where needed, synthetic commits, tags, refs and replacements.
The synthetic identity is passed per command with git -c; no Git
configuration file is written. The real repository is only read: its
policy, its .gitignore (through git check-ignore --no-index) and its
publishable files for the content scan. Secret-shaped, user-path and
patient-like values are assembled at run time from fragments, so this file
holds none of them.
"""

import builtins
import copy
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.quality import publication as audit  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402

REAL_POLICY = os.path.join(REPO_ROOT, "config", "publishable_policy.json")
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
FAKE_GIT = "C:\\Program Files\\Git\\cmd\\git.exe"
ENV_TOKEN = "SYNTHENVVALUE-Q7"
OBJECT_ID_TEXT = re.compile(r"[0-9a-f]{40}")

# Synthetic fragments; the assembled values exist only at run time.
BODY = "Q7w2" * 10
SAMPLE_VALUE = "Q7w2" + "x9Z4p8"
ACCOUNT = "Zq" + "xwvb"
DIGITS = "4815" + "1623"
PASSWORD_KEY = "db_pass" + "word"
API_KEY_NAME = "api" + "_key"


def token_samples():
    """Return (rule ID, token-shaped value) pairs built from fragments."""
    return (
        ("aws_access_key_id", "AK" + "IA" + "Q7W2" * 4),
        ("github_token", "gh" + "p_" + BODY),
        ("llm_provider_api_key", "sk" + "-" + BODY),
        ("huggingface_token", "hf" + "_" + BODY),
        ("slack_token", "xo" + "xb-" + "1234567890-" + BODY),
        ("google_api_key", "AI" + "za" + BODY[:35]),
        ("stripe_live_key", "sk" + "_live_" + BODY),
        ("json_web_token", ".".join(["ey" + "J" + BODY, "ey" + "J" + BODY,
                                     BODY])),
    )


def github_token():
    """Return a synthetic GitHub-token-shaped value."""
    return "gh" + "p_" + BODY


def private_key_header():
    """Return a synthetic private-key block header."""
    return "-" * 5 + "BEGIN RSA PRIVATE " + "KEY" + "-" * 5


def credential_uri(secret):
    """Return a synthetic user-and-password URI around secret."""
    return "postgresql" + "://reader:" + secret + "@localhost/db"


def user_path(style="backslash"):
    """Return a synthetic machine-specific user-profile path."""
    separator = {"backslash": "\\", "json": "\\\\", "slash": "/"}[style]
    return separator.join(["C:", "Users", ACCOUNT, "tool.py"])


def assignment(key, separator, value, quote=""):
    """Return one synthetic key/value line."""
    return "%s%s%s%s%s\n" % (key, separator, quote, value, quote)


def patient_line(field="person_id", separator=": "):
    """Return a synthetic numeric patient-identifier assignment."""
    return field + separator + DIGITS + "\n"


def configured_test_temp():
    """Return the configured workspace test scratch directory (workspace-only rules)."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def read_json(path):
    """Return a JSON file as Python data."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def oid(number):
    """Return a synthetic 40-digit hexadecimal object ID."""
    return "%040x" % number


def simulated_lstat(targets):
    """Return an lstat stand-in answering chosen paths with fake results."""
    real_lstat = os.lstat

    def fake_lstat(path, *args, **kwargs):
        """Return the simulated result for a selected path."""
        key = os.path.normcase(os.fspath(path))
        if key in targets:
            return targets[key]
        return real_lstat(path, *args, **kwargs)
    return fake_lstat


REPARSE = guard.REPARSE_POINT
JUNCTION = SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_size=0,
                           st_ino=0, st_file_attributes=0x10 | REPARSE)
FILE_LINK = SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_size=0,
                            st_ino=0, st_file_attributes=0x20 | REPARSE)
DIRECTORY = SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_size=0,
                            st_ino=0, st_file_attributes=0x10)

CLEAN_FILES = (
    ("README.md", "# Synthetic repository\n\nRun `& $GnnPython "
                  "scripts\\tool.py`.\n"),
    ("docs/GUIDE.md", "Interpreter: C:\\Users\\<account>\\anaconda3\n"),
    ("scripts/tool.py", '"""Synthetic tool."""\n'),
    ("tests/test_tool.py", '"""Synthetic tests, invented identifiers."""\n'
                           'ROW = {"person_id": "' + "7" * 6 + '"}\n'),
    ("tests/fixtures/synthetic_case.json", '{"case": "SYNTHETIC"}\n'),
    (".env.example", "PGPASSWORD=<password>\n"),
)
SCRATCH_PATHS = tuple(path for path, _ in CLEAN_FILES) + (
    "config/publishable_policy.json", ".gitignore")
SCRATCH_GIT_SETTINGS = ("user.name=Synthetic Tester",
                        "user.email=synthetic.tester@example.invalid",
                        "commit.gpgsign=false", "tag.gpgsign=false",
                        "core.autocrlf=false", "core.fsmonitor=false",
                        "gc.auto=0", "maintenance.auto=false")
SANITISED_DOCUMENTS = ("docs\\development.md", "docs\\environments.md",
                       "docs\\inherited-gnn.md")


def make_junction(test, link, target):
    """Create a real directory junction inside scratch, or skip the test."""
    try:
        import _winapi
        _winapi.CreateJunction(target, link)
    except (ImportError, AttributeError, OSError) as error:
        test.skipTest("directory junctions are unavailable here: %s"
                      % type(error).__name__)
    test.addCleanup(os.rmdir, link)  # removes the junction, not its target


def make_file_symlink(test, link, target):
    """Create a real file symlink inside scratch, or skip the test."""
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as error:
        test.skipTest("file symlinks are unavailable here: %s"
                      % type(error).__name__)
    test.addCleanup(os.remove, link)


# Policy Contract And Pure Rules (Read The Real Repository Only)

@unittest.skipUnless(os.name == "nt", "the audit supports Windows only")
class PolicyContractTestCase(unittest.TestCase):
    """The committed policy, its fixed contract and refusal of weakening."""

    def setUp(self):
        """Load the committed policy."""
        self.policy = read_json(REAL_POLICY)

    def test_committed_policy_matches_fixed_rules(self):
        """Every policy section equals the fixed rules held in the script."""
        policy = self.policy
        audit.compile_rules(policy)
        self.assertEqual(policy["schema_version"], "1.0")
        self.assertEqual(policy["step"], "3")
        self.assertEqual(policy["max_candidate_bytes"],
                         audit.HARD_MAX_CANDIDATE_BYTES)
        self.assertEqual(set(policy["publishable"]["extensions"]),
                         set(audit.PUBLISHABLE_EXTENSIONS))
        self.assertEqual(set(policy["publishable"]["names"]),
                         set(audit.PUBLISHABLE_NAMES))
        forbidden = policy["forbidden"]
        for key, fixed in (
                ("any_components", audit.FORBIDDEN_ANY_COMPONENTS),
                ("root_components", audit.FORBIDDEN_ROOT_COMPONENTS),
                ("component_suffixes", audit.FORBIDDEN_COMPONENT_SUFFIXES),
                ("extensions", audit.FORBIDDEN_EXTENSIONS),
                ("name_globs", audit.FORBIDDEN_NAME_GLOBS),
                ("name_exceptions", audit.FORBIDDEN_NAME_EXCEPTIONS)):
            self.assertEqual(set(forbidden[key]), set(fixed), key)
        self.assertEqual(policy["ignore_probes"]["ignored"],
                         list(audit.IGNORED_PROBES))
        self.assertEqual(policy["ignore_probes"]["trackable"],
                         list(audit.TRACKABLE_PROBES))
        self.assertEqual(
            [(entry["id"], entry["category"], entry["pattern"],
              entry["exempt_placeholders"])
             for entry in policy["signatures"]],
            list(audit.SIGNATURES))
        self.assertEqual(policy["user_path"]["pattern"],
                         audit.USER_PATH_PATTERN)
        self.assertEqual(set(policy["user_path"]["allowed_accounts"]),
                         set(audit.ALLOWED_ACCOUNTS))
        patient = policy["patient_identifier"]
        self.assertEqual(patient["pattern"], audit.PATIENT_PATTERN)
        self.assertEqual(patient["synthetic_fixture_roots"], ["tests"])
        self.assertEqual(patient["synthetic_declaration"], "synthetic")
        self.assertEqual(set(policy["placeholders"]["markers"]),
                         set(audit.PLACEHOLDER_MARKERS))
        self.assertEqual(policy["placeholders"]["patterns"],
                         list(audit.PLACEHOLDER_PATTERNS))

    def test_policy_covers_the_assigned_categories(self):
        """Required extensions, names, directories and probes are fixed."""
        for extension in (".pt", ".pth", ".ckpt", ".safetensors", ".onnx",
                          ".npy", ".npz", ".pkl", ".pickle", ".joblib",
                          ".h5", ".hdf5", ".arrow", ".feather", ".parquet",
                          ".db", ".sqlite", ".csv", ".tsv", ".log", ".jsonl",
                          ".zip", ".tmp", ".partial", ".pem", ".key"):
            self.assertIn(extension, audit.FORBIDDEN_EXTENSIONS)
        rules = audit.compile_rules(self.policy)
        for name in (".env", ".env.local", ".pgpass", "pg_service.conf",
                     "clearml.conf", ".netrc", ".pypirc", "id_rsa",
                     "credentials.json", "secrets.toml", "api.token"):
            self.assertEqual(audit.path_findings(name, rules),
                             ["forbidden_name"], name)
        self.assertEqual(audit.path_findings(".env.example", rules), [])
        for component in ("outputs", "output", "results", "runs", "logs",
                          "cases", "evaluation", "evaluations", "audit",
                          "audits", "cache", "caches", "environments",
                          "clearml", "checkpoints",
                          "protected-data", "patient_graphs"):
            self.assertIn(component, audit.FORBIDDEN_ANY_COMPONENTS)
            self.assertTrue(any(probe.startswith("nested/%s/" % component)
                                for probe in audit.IGNORED_PROBES),
                            component)
        for component in ("data", "models"):
            self.assertIn(component, audit.FORBIDDEN_ROOT_COMPONENTS)
            self.assertNotIn(component, audit.FORBIDDEN_ANY_COMPONENTS)
        for probe in ("nested/outputs/probe.json", "nested/output/probe.json",
                      "nested/results/probe.json", "nested/runs/probe.txt",
                      "nested/logs/probe.txt", "nested/cases/probe.json",
                      "nested/evaluation/probe.json",
                      "nested/evaluations/probe.json",
                      "nested/audit/probe.json", "nested/audits/probe.json",
                      "nested/cache/probe.txt", "nested/caches/probe.txt",
                      "nested/environments/probe.txt",
                      "nested/clearml/probe.txt", "nested/dir/.env",
                      "nested/pkg/__pycache__/probe.pyc", "probe.pt",
                      "nested/dir/probe.pt"):
            self.assertIn(probe, audit.IGNORED_PROBES)
        for probe in ("scripts/probe_tool.py", "docs/PROBE.md",
                      "config/probe.json", "tests/test_probe.py",
                      "tests/fixtures/synthetic_probe.json",
                      "tests/data/synthetic_probe.json",
                      "app/models/probe_model.py", ".env.example",
                      "CHANGELOG.md", "pyproject.toml"):
            self.assertIn(probe, audit.TRACKABLE_PROBES)

    def test_committed_policy_holds_no_sensitive_value(self):
        """The policy passes its own content scan."""
        rules = audit.compile_rules(self.policy)
        with open(REAL_POLICY, "rb") as handle:
            data = handle.read()
        self.assertEqual(audit.path_findings(
            "config/publishable_policy.json", rules), [])
        self.assertEqual(audit.content_findings(
            "config/publishable_policy.json", data, rules), [])

    def test_policy_cannot_be_weakened(self):
        """Widening, omitting or altering fixed rules is refused."""
        def signature(policy, rule_id):
            """Return the policy signature entry with rule_id."""
            return [entry for entry in policy["signatures"]
                    if entry["id"] == rule_id][0]

        def drop(items, value):
            """Remove value from a list in place."""
            items.remove(value)

        cases = (
            (lambda p: p["publishable"]["extensions"].append(".pt"),
             "beyond the fixed ceiling"),
            (lambda p: p["publishable"]["names"].append(".env"),
             "beyond the fixed ceiling"),
            (lambda p: p["publishable"]["extensions"].append(".PY"),
             "lower case"),
            (lambda p: p.update(max_candidate_bytes=2 ** 21),
             "max_candidate_bytes"),
            (lambda p: p.update(max_candidate_bytes=True),
             "max_candidate_bytes"),
            (lambda p: drop(p["forbidden"]["extensions"], ".pt"),
             "omits mandatory entries: .pt"),
            (lambda p: drop(p["forbidden"]["any_components"], "outputs"),
             "omits mandatory entries: outputs"),
            (lambda p: drop(p["forbidden"]["root_components"], "data"),
             "omits mandatory entries: data"),
            (lambda p: drop(p["forbidden"]["name_globs"], ".env"),
             "omits mandatory entries: .env"),
            (lambda p: p["forbidden"]["name_exceptions"].append(".env.local"),
             "beyond the fixed ceiling"),
            (lambda p: drop(p["ignore_probes"]["ignored"],
                            "nested/outputs/probe.json"),
             "omits mandatory entries"),
            (lambda p: drop(p["ignore_probes"]["trackable"],
                            "tests/fixtures/synthetic_probe.json"),
             "omits mandatory entries"),
            (lambda p: p["ignore_probes"]["ignored"].append(
                "scripts/probe_tool.py"), "both ignored and trackable"),
            (lambda p: p["ignore_probes"]["ignored"].append("../escape"),
             "not a safe repository-relative path"),
            (lambda p: p["signatures"].remove(signature(p, "github_token")),
             "omit mandatory signature github_token"),
            (lambda p: signature(p, "private_key_block").update(
                pattern="PRIVATE KEY"), "differs from the fixed rule"),
            (lambda p: signature(p, "private_key_block").update(
                exempt_placeholders=True), "differs from the fixed rule"),
            (lambda p: signature(p, "github_token").update(
                exempt_placeholders=True), "differs from the fixed rule"),
            (lambda p: p["signatures"].append(
                {"id": "extra_rule", "category": "token", "pattern": "(",
                 "exempt_placeholders": False}), "does not compile"),
            (lambda p: p["signatures"].append(
                {"id": "extra_rule", "category": "token", "pattern": "x*",
                 "exempt_placeholders": False}), "matches empty text"),
            (lambda p: p["signatures"].append(
                {"id": "github_token", "category": "token", "pattern": "x",
                 "exempt_placeholders": False}), "repeated or reserved"),
            (lambda p: p["signatures"].append(
                {"id": "unsafe_path", "category": "token", "pattern": "x",
                 "exempt_placeholders": False}), "repeated or reserved"),
            (lambda p: p["user_path"].update(pattern="Users"),
             "user_path.pattern"),
            (lambda p: p["user_path"]["allowed_accounts"].append(ACCOUNT),
             "beyond the fixed ceiling"),
            (lambda p: p["patient_identifier"].update(pattern="person_id"),
             "patient_identifier.pattern"),
            (lambda p: p["patient_identifier"][
                "synthetic_fixture_roots"].append("docs"),
             "beyond the fixed ceiling"),
            (lambda p: p["patient_identifier"].update(
                synthetic_declaration="fixture"), "synthetic_declaration"),
            (lambda p: p["patient_identifier"].update(
                declaration_window_bytes=10 ** 6),
             "declaration_window_bytes"),
            (lambda p: p["placeholders"]["markers"].append("TEST"),
             "beyond the fixed ceiling"),
            (lambda p: p["placeholders"]["patterns"].append(".*"),
             "beyond the fixed ceiling"),
            (lambda p: p.update(repository="C:\\elsewhere"),
             "unsupported key"),
            (lambda p: p.pop("signatures"), "lacks required keys"),
            (lambda p: p.update(schema_version="2.0"), "schema_version"),
            (lambda p: p.update(step="4"), "step must be"),
        )
        for change, expected in cases:
            with self.subTest(expected=expected):
                policy = copy.deepcopy(self.policy)
                change(policy)
                with self.assertRaises(audit.PolicyError) as caught:
                    audit.compile_rules(policy)
                self.assertIn(expected, str(caught.exception))

    def test_policy_may_be_tightened(self):
        """Fewer publishable types and extra signatures are accepted."""
        policy = copy.deepcopy(self.policy)
        policy["publishable"]["extensions"].remove(".txt")
        policy["forbidden"]["extensions"].append(".synthetic")
        policy["placeholders"]["markers"].remove("EXAMPLE")
        policy["signatures"].append(
            {"id": "synthetic_tight_rule", "category": "token",
             "pattern": "SYNTHETIC-TIGHTEN-[0-9]{4}",
             "exempt_placeholders": False})
        rules = audit.compile_rules(policy)
        self.assertEqual(audit.path_findings("docs/notes.txt", rules),
                         ["unapproved_type"])
        self.assertEqual(audit.path_findings("docs/x.synthetic", rules),
                         ["forbidden_extension"])
        data = ("value " + "SYNTHETIC-TIGHTEN-" + "1234\n").encode("ascii")
        self.assertEqual(audit.content_findings("docs/x.md", data, rules),
                         ["synthetic_tight_rule"])

    def test_cli_accepts_only_the_policy_option(self):
        """No option can choose the repository, Git or a scope."""
        parser = audit.build_parser()
        options = {option for action in parser._actions
                   for option in action.option_strings}
        self.assertEqual(options, {"-h", "--help", "--policy"})
        for argv in (["--repo", "C:\\elsewhere"], ["--git", "git.exe"],
                     ["--skip-history"]):
            with redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                audit.main(argv)


@unittest.skipUnless(os.name == "nt", "the audit supports Windows only")
class RuleTestCase(unittest.TestCase):
    """Path and content rules on synthetic in-memory text."""

    def setUp(self):
        """Compile the committed policy."""
        self.rules = audit.compile_rules(read_json(REAL_POLICY))

    def scan(self, relative, text):
        """Return content rule IDs for synthetic text."""
        data = text if isinstance(text, bytes) else text.encode("utf-8")
        return audit.content_findings(relative, data, self.rules)

    def test_path_categories(self):
        """Sensitive categories are refused; publishable types pass."""
        for path, expected in (
                ("scripts/tool.py", []), ("docs/GUIDE.md", []),
                ("config/app.json", []), ("LICENSE", []),
                ("tests/fixtures/synthetic_case.json", []),
                ("tests/data/synthetic_case.json", []),
                ("app/models/model_code.py", []), (".env.example", []),
                (".gitignore", []),
                ("outputs/answer.json", ["forbidden_directory"]),
                ("app/outputs/answer.json", ["forbidden_directory"]),
                ("app/results/table.md", ["forbidden_directory"]),
                ("app/runs/run.json", ["forbidden_directory"]),
                ("app/logs/run.txt", ["forbidden_directory"]),
                ("app/cases/case.md", ["forbidden_directory"]),
                ("app/evaluation/score.json", ["forbidden_directory"]),
                ("app/audits/x.json", ["forbidden_directory"]),
                ("app/cache/x.json", ["forbidden_directory"]),
                ("app/environments/x.txt", ["forbidden_directory"]),
                ("app/protected-data/x.json", ["forbidden_directory"]),
                ("app/clearml/x.json", ["forbidden_directory"]),
                ("data/x.json", ["forbidden_directory"]),
                ("models/x.json", ["forbidden_directory"]),
                ("nested/protected-data/x.md", ["forbidden_directory"]),
                ("pkg.egg-info/PKG-INFO", ["forbidden_directory"]),
                ("model.pt", ["forbidden_extension"]),
                ("rows.csv", ["forbidden_extension"]),
                ("run.log", ["forbidden_extension"]),
                ("trace.jsonl", ["forbidden_extension"]),
                ("key.pem", ["forbidden_extension"]),
                (".env", ["forbidden_name"]),
                (".env.local", ["forbidden_name"]),
                ("id_rsa", ["forbidden_name"]),
                ("notebook.ipynb", ["unapproved_type"]),
                ("image.png", ["unapproved_type"]),
                ("src/ehr_evidence_app/interface/static/app.js", []),
                ("src/ehr_evidence_app/interface/static/index.html", []),
                ("src/ehr_evidence_app/interface/static/app.css", []),
                ("src/ehr_evidence_app/interface/static/mark.svg", []),
                ("src/ehr_evidence_app/interface/static/nested/app.js",
                 ["unapproved_type"]),
                ("src/ehr_evidence_app/interface/static/image.png",
                 ["unapproved_type"]),
                ("docs/report.html", ["unapproved_type"]),
                ("src/ehr_evidence_app/interface/app.js",
                 ["unapproved_type"]),
                ("outputs/model.pt", ["forbidden_directory",
                                      "forbidden_extension"])):
            with self.subTest(path=path):
                self.assertEqual(audit.path_findings(path, self.rules),
                                 expected)

    def test_lexical_escapes_are_refused(self):
        """Traversal, .git, reserved and ambiguous names are refused."""
        for path, expected in (
                ("../escape.md", "unsafe_path"), ("", "unsafe_path"),
                (".git/config", "git_metadata_path"),
                ("docs/.GIT/x.md", "git_metadata_path"),
                ("docs/./x.md", "unsafe_path"), ("docs//x.md", "unsafe_path"),
                ("/rooted.md", "unsafe_path"), ("docs\\x.md", "unsafe_path"),
                ("docs/NUL.md", "unsafe_path"),
                ("docs/GIT~1/x.md", "unsafe_path"),
                ("docs/x.md.", "unsafe_path"), ("docs/x.md ", "unsafe_path"),
                ("docs/x.md:stream", "unsafe_path")):
            with self.subTest(path=path):
                self.assertEqual(audit.path_findings(path, self.rules),
                                 [expected])

    def test_token_and_private_key_signatures_are_never_exempt(self):
        """Token shapes and keys are found even beside marker words."""
        for rule_id, value in token_samples():
            with self.subTest(rule=rule_id):
                self.assertEqual(self.scan("docs/NOTE.md",
                                           "SYNTHETIC note " + value + "\n"),
                                 [rule_id])
        self.assertEqual(self.scan("docs/NOTE.md",
                                   "AK" + "IAIOSFODNN7" + "EXAMPLE\n"),
                         ["aws_access_key_id"])
        marked = "ey" + "J" + "EXAMPLE-" + BODY
        self.assertEqual(self.scan("docs/NOTE.md",
                                   ".".join([marked, marked, BODY])),
                         ["json_web_token"])
        self.assertEqual(self.scan("docs/NOTE.md", private_key_header()),
                         ["private_key_block"])
        example_key = "-" * 5 + "BEGIN EXAMPLE PRIVATE " + "KEY" + "-" * 5
        self.assertEqual(self.scan("docs/NOTE.md", example_key),
                         ["private_key_block"])
        self.assertEqual(self.scan("docs/NOTE.md",
                                   "PuTTY-User-" + "Key-File-3: x\n"),
                         ["putty_private_key"])

    def test_tests_tree_is_not_exempt_from_high_confidence_rules(self):
        """A declared synthetic test module still reports secrets."""
        text = ('"""Synthetic checks."""\nVALUE = "' + github_token()
                + '"\n' + user_path() + "\n")
        self.assertEqual(sorted(self.scan("tests/test_case.py", text)),
                         ["github_token", "machine_user_path"])

    def test_credential_uris_and_placeholders(self):
        """Only placeholder passwords in URIs are allowed."""
        self.assertEqual(self.scan("docs/DB.md",
                                   credential_uri(SAMPLE_VALUE)),
                         ["credential_uri"])
        for secret in ("<password>", "${PGPASSWORD}", "$env:PGPASSWORD",
                       "%PGPASSWORD%", "****", "CHANGEME"):
            with self.subTest(secret=secret):
                self.assertEqual(self.scan("docs/DB.md",
                                           credential_uri(secret)), [])
        self.assertEqual(self.scan("docs/DB.md",
                                   credential_uri("prefix" + "EXAMPLE1")),
                         ["credential_uri"])

    def test_quoted_and_unquoted_assignments(self):
        """Quoted multiword and unquoted config assignments are found."""
        quoted = "secret_assignment"
        unquoted = "unquoted_secret_assignment"
        for relative, text, expected in (
                ("config/app.toml",
                 assignment("pass" + "phrase", " = ",
                            "correct horse battery staple", '"'), [quoted]),
                ("scripts/tool.py",
                 assignment(PASSWORD_KEY, " = ", SAMPLE_VALUE, "'"),
                 [quoted]),
                ("config/app.json",
                 '{"' + PASSWORD_KEY + '": "' + SAMPLE_VALUE + '"}\n',
                 [quoted]),
                ("config/app.yaml",
                 assignment(API_KEY_NAME, ": ", SAMPLE_VALUE), [unquoted]),
                ("config/app.yaml",
                 "  - " + assignment(API_KEY_NAME, ": ", SAMPLE_VALUE),
                 [unquoted]),
                (".env.example",
                 assignment("PG" + "PASSWORD", "=", SAMPLE_VALUE),
                 [unquoted]),
                ("config/app.ini",
                 assignment("secret" + "_key", " = ", SAMPLE_VALUE),
                 [unquoted]),
                ("docs/SETUP.md",
                 "export " + assignment("GITHUB" + "_TOKEN", "=",
                                        SAMPLE_VALUE), [unquoted]),
                ("docs/SETUP.md",
                 assignment("client" + "_secret", ": ",
                            SAMPLE_VALUE + "  # synthetic comment"),
                 [unquoted]),
                ("config/app.yaml",
                 assignment(PASSWORD_KEY, ": ", "prefix" + "EXAMPLE"
                            + "suffix123"), [unquoted]),
                ("scripts/tool.py",
                 assignment(PASSWORD_KEY, " = ", "settings.value_2024"), []),
                ("config/app.yaml", assignment(API_KEY_NAME, ": ", ""), []),
                ("config/app.yaml",
                 assignment(API_KEY_NAME, ": ", "${API_KEY}"), []),
                ("config/app.yaml",
                 assignment(API_KEY_NAME, ": ", "<api key>"), []),
                (".env.example",
                 assignment("PG" + "PASSWORD", "=", "$PGPASSWORD"), []),
                ("config/app.yaml",
                 assignment(PASSWORD_KEY, ": ", "CHANGEME-local"), []),
                ("config/app.yaml",
                 assignment(PASSWORD_KEY, ": ", "xxxxxxxx"), []),
                ("config/app.yaml", assignment(PASSWORD_KEY, ": ", "short"),
                 []),
                ("config/app.yaml",
                 assignment("max" + "_tokens", ": ", "1024"), []),
                ("config/app.yaml",
                 assignment("token" + "izer", ": ", "bert-base-uncased"),
                 []),
                ("scripts/tool.py",
                 assignment("SEC" + "RET", " = ",
                            "SYNTHETIC-PASSWORD-NEVER-LOGGED", '"'), []),
                ("docs/SOURCE.md",
                 assignment("$env:PG" + "PASSWORD", " = ", "<password>",
                            '"'), [])):
            with self.subTest(relative=relative, text=text):
                self.assertEqual(self.scan(relative, text), expected)

    def test_placeholder_markers_are_bounded(self):
        """Markers count only as whole tokens or full placeholder forms."""
        for value, expected in (
                ("SYNTHETIC-PASSWORD-NEVER-LOGGED", True),
                ("CHANGEME", True), ("changeme_local", True),
                ("<password>", True), ("${PGPASSWORD}", True),
                ("$env:PGPASSWORD", True), ("%PGPASSWORD%", True),
                ("****", True), ("xxxxxxxx", True),
                ("prefix" + "EXAMPLE" + "suffix123", False),
                ("non" + "synthetic", False), ("SYNTHLEAK", False),
                (SAMPLE_VALUE, False), ("<password> and more", False)):
            with self.subTest(value=value):
                self.assertEqual(audit.is_placeholder(value, self.rules),
                                 expected)

    def test_machine_user_paths(self):
        """Real-looking account paths are found; placeholders pass."""
        for style in ("backslash", "json", "slash"):
            with self.subTest(style=style):
                self.assertEqual(self.scan("docs/NOTE.md", user_path(style)),
                                 ["machine_user_path"])
        self.assertEqual(self.scan("docs/NOTE.md",
                                   user_path().replace("C:", "d:")),
                         ["machine_user_path"])
        for text in ("C:\\Users\\<account>\\anaconda3",
                     "C:\\Users\\<real account>\\x",
                     "C:/Users/%USERNAME%/x", "C:\\Users\\$env:USERNAME\\x",
                     "C:\\Users\\Public\\Documents", "C:\\Users\\Default\\x",
                     "C:\\Users\\EXAMPLE-user\\x",
                     "%USERPROFILE%\\.condarc"):
            with self.subTest(text=text):
                self.assertEqual(self.scan("docs/NOTE.md", text), [])

    def test_patient_identifier_assignments(self):
        """Numeric assignments are found; discussion and indexes pass."""
        rule = "patient_identifier_assignment"
        for relative, text, expected in (
                ("docs/NOTE.md", patient_line(), [rule]),
                ("docs/NOTE.md", patient_line("subject_id", "="), [rule]),
                ("docs/NOTE.md", '{"hadm' + '_id": ' + DIGITS + "}", [rule]),
                ("docs/NOTE.md", "--patient" + "_index " + "42\n", [rule]),
                ("docs/NOTE.md", "--patient" + "-index=" + "42\n", [rule]),
                ("docs/NOTE.md", "Synthetic example: "
                 + patient_line("subject_id", "="), [rule]),
                ("docs/NOTE.md", "`person_id` and `subject_id` are "
                 "one-to-one\n", []),
                ("docs/NOTE.md", "patient" + "_index=0\n", []),
                ("docs/NOTE.md", "person_id=<digits>\n", [])):
            with self.subTest(text=text):
                self.assertEqual(self.scan(relative, text), expected)

    def test_synthetic_fixture_declarations(self):
        """Only explicit bounded declarations exempt patient values."""
        rule = "patient_identifier_assignment"
        body = patient_line()
        for relative, text, expected in (
                ("tests/fixtures/synthetic_ids.json", body, []),
                ("tests/fixtures/synthetic-ids.json", body, []),
                ("tests/fixtures/nonsynthetic_ids.json", body, [rule]),
                ("tests/fixtures/ids.json", "synthetic\n" + body, [rule]),
                ("docs/synthetic_ids.md", body, [rule]),
                ("tests/test_case.py", '"""Synthetic fixtures."""\n' + body,
                 []),
                ("tests/test_case.py", "#!/usr/bin/env python3\n"
                 '"""Synthetic fixtures."""\n' + body, []),
                ("tests/test_case.py", '"""Checks."""\n# synthetic\n' + body,
                 [rule]),
                ("tests/test_case.py", '"""Nonsynthetic checks."""\n' + body,
                 [rule]),
                ("scripts/test_case.py", '"""Synthetic."""\n' + body,
                 [rule])):
            with self.subTest(relative=relative, text=text):
                self.assertEqual(self.scan(relative, text), expected)

    def test_unreadable_content_is_a_finding(self):
        """Oversized, binary and non-UTF-8 content is reported."""
        self.assertEqual(self.scan("docs/x.md", b"abc\0def"),
                         ["binary_content"])
        self.assertEqual(self.scan("docs/x.md", b"\xff\xfe\xfd"),
                         ["not_utf8"])
        self.assertEqual(self.scan("docs/x.md", b"\xef\xbb\xbfclean\n"), [])
        big = b"a" * (self.rules.max_bytes + 1)
        self.assertEqual(self.scan("docs/x.md", big), ["oversized"])

    def test_display_path_redacts_sensitive_components(self):
        """Digit runs, signatures and control characters are hidden."""
        self.assertEqual(audit.display_path("cases/" + DIGITS + "/a.json"),
                         "cases/<redacted>/a.json")
        self.assertEqual(audit.display_path("docs/" + github_token()),
                         "docs/<redacted>")
        self.assertEqual(audit.display_path("docs/a\nb.md"),
                         "docs/<redacted>")
        self.assertEqual(audit.display_path("ab/" + "cdef" * 10),
                         "ab/<redacted>")
        self.assertEqual(audit.display_path("docs/GUIDE.md"),
                         "docs/GUIDE.md")


@unittest.skipUnless(os.name == "nt", "the audit supports Windows only")
class RealRepositoryTestCase(unittest.TestCase):
    """Read-only checks of the real repository's publishable files."""

    def setUp(self):
        """Compile the committed policy."""
        self.rules = audit.compile_rules(read_json(REAL_POLICY))

    @staticmethod
    def publishable_files():
        """Return repository-relative paths of the files to scan."""
        names = [".gitignore", "pyproject.toml"]
        names += sorted(name for name in os.listdir(REPO_ROOT)
                        if name.lower().endswith(".md"))
        for directory in ("config", "docs", "scripts", "src", "tests"):
            top = os.path.join(REPO_ROOT, directory)
            for current, dirnames, filenames in os.walk(top):
                dirnames[:] = sorted(d for d in dirnames
                                     if d != "__pycache__")
                for name in sorted(filenames):
                    relative = os.path.relpath(os.path.join(current, name),
                                               REPO_ROOT)
                    names.append(relative.replace("\\", "/"))
        return names

    def test_publishable_files_pass_the_rules(self):
        """Tool, policy, configs, docs, scripts, source and tests are clean."""
        files = self.publishable_files()
        for required in ("src/ehr_evidence_app/quality/publication.py",
                         "config/publishable_policy.json",
                         "config/paths.json",
                         "tests/quality/test_publication.py",
                         "pyproject.toml"):
            self.assertIn(required, files)
        for relative in files:
            with self.subTest(relative=relative):
                path = os.path.join(REPO_ROOT, *relative.split("/"))
                with open(path, "rb") as handle:
                    data = handle.read()
                self.assertEqual(audit.path_findings(relative, self.rules),
                                 [])
                self.assertEqual(audit.content_findings(relative, data,
                                                        self.rules), [])

    def test_sanitised_documents_use_the_placeholder(self):
        """Command examples use $GnnPython and no account path remains."""
        for relative in SANITISED_DOCUMENTS:
            with self.subTest(relative=relative):
                with open(os.path.join(REPO_ROOT, relative), "r",
                          encoding="utf-8") as handle:
                    text = handle.read()
                self.assertIn("$GnnPython = 'C:\\path\\to\\omopgnn\\"
                              "python.exe'", text)
                self.assertIn("& $GnnPython ", text)
        with open(PATHS_PATH, "r", encoding="utf-8") as handle:
            paths = json.load(handle)
        entries = paths["locations_outside_this_configuration"]["entries"]
        self.assertIn("C:\\Users\\<account>\\anaconda3, including "
                      "envs\\omopgnn\\python.exe",
                      [entry["path"] for entry in entries])

    def test_audit_runs_from_any_checkout_outside_private_roots(self):
        """Any plain checkout location passes unless it overlaps a root."""
        self.assertIsNone(audit.fixed_policy_problem())
        cases = (
            ("D:\\a\\ehr-evidence-app\\ehr-evidence-app", None),
            ("C:\\builds\\ehr-evidence-app", None),
            (audit.PRIVATE_ROOT + "\\checkout",
             "the repository and the private workspace overlap"),
            (audit.RUNTIME_ROOT + "\\checkout",
             "the repository and the runtime root overlap"),
        )
        for root, expected in cases:
            with self.subTest(root=root):
                with mock.patch.object(audit, "REPO_ROOT", root), \
                        mock.patch.object(audit, "CONFIG_DIR",
                                          root + "\\config"):
                    self.assertEqual(audit.fixed_policy_problem(), expected)
        with mock.patch.object(audit, "CONFIG_DIR", "C:\\elsewhere\\config"):
            self.assertEqual(audit.fixed_policy_problem(),
                             "the configuration directory is not the "
                             "repository config")

    def test_real_gitignore_passes_every_probe(self):
        """The real .gitignore satisfies every probe via real git."""
        try:
            git = audit.locate_git()
        except audit.OperationalError as error:
            self.skipTest("git.exe is unavailable: %s" % error)
        self.assertEqual(audit.ignore_probe_findings(git, self.rules), [])


# Scratch Repositories

class ScratchTestCase(unittest.TestCase):
    """A synthetic repository and workspace beneath the workspace test temp."""

    make_git_dir = True

    def setUp(self):
        """Create the scratch repository and patch the audit's roots."""
        if os.name != "nt":
            self.skipTest("the audit supports Windows only")
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(
            os.path.normcase(root).startswith(os.path.normcase("G:\\")),
            "test scratch space must stay in the workspace")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="step-3-", dir=root)
        self.addCleanup(self.remove_scratch)
        self.repo = os.path.join(self.work, "repo")
        self.workspace = os.path.join(self.work, "workspace")
        os.makedirs(self.workspace)
        os.makedirs(os.path.join(self.repo, "config"))
        if self.make_git_dir:
            os.makedirs(os.path.join(self.repo, ".git"))
        for relative, text in CLEAN_FILES:
            self.write(relative, text)
        shutil.copyfile(REAL_POLICY, self.path(
            "config/publishable_policy.json"))
        shutil.copyfile(os.path.join(REPO_ROOT, ".gitignore"),
                        self.path(".gitignore"))
        patcher = mock.patch.multiple(
            audit, REPO_ROOT=self.repo,
            CONFIG_DIR=os.path.join(self.repo, "config"),
            PRIVATE_ROOT=self.workspace)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.rules = audit.compile_rules(read_json(REAL_POLICY))

    def inside_scratch(self, path):
        """Return True when path lies strictly beneath the scratch tree."""
        return os.path.normcase(os.path.abspath(path)).startswith(
            os.path.normcase(os.path.abspath(self.work)) + os.sep)

    def remove_scratch(self):
        """Delete the scratch tree only if strictly beneath the temp root.

        The absolute target is checked immediately before removal; Git's
        read-only object files are made writable only inside scratch.
        """
        target = os.path.abspath(self.work)
        root = os.path.abspath(self.temp_root)
        if not os.path.normcase(target).startswith(
                os.path.normcase(root) + os.sep):
            self.fail("refusing to remove %s: outside %s" % (target, root))
        shutil.rmtree(target, onerror=self.clear_read_only)

    def clear_read_only(self, function, path, _info):
        """Retry one failed removal after clearing read-only, in scratch."""
        if not self.inside_scratch(path):
            return
        try:
            os.chmod(path, stat.S_IWRITE)
            function(path)
        except OSError:
            pass

    def path(self, relative):
        """Return the scratch repository path of a '/'-separated path."""
        return os.path.join(self.repo, *relative.split("/"))

    def write(self, relative, data):
        """Write text or bytes to a scratch repository file."""
        path = self.path(relative)
        self.assertTrue(self.inside_scratch(path))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if isinstance(data, str):
            data = data.encode("utf-8")
        with open(path, "wb") as handle:
            handle.write(data)

    def run_audit(self, argv=None):
        """Run the audit CLI, capturing stdout."""
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = audit.main(argv or [])
        return code, stream.getvalue()

    def assert_operational(self, code, output, *fragments):
        """Assert an operational failure naming every fragment."""
        self.assertEqual(code, audit.EXIT_OPERATIONAL, output)
        self.assertIn("RESULT: OPERATIONAL FAILURE", output)
        self.assertNotIn("RESULT: PASS", output)
        for fragment in fragments:
            self.assertIn(fragment, output)

    def assert_findings(self, code, output, *lines):
        """Assert a findings result containing every expected line."""
        self.assertEqual(code, audit.EXIT_FINDINGS, output)
        self.assertIn("RESULT: FINDINGS", output)
        for line in lines:
            self.assertIn(line, output)

    def assert_no_values(self, output, *values):
        """Assert that no synthetic value or object ID reached the output."""
        for value in values + (BODY, SAMPLE_VALUE, ACCOUNT, DIGITS,
                               ENV_TOKEN):
            self.assertNotIn(value, output)
        self.assertIsNone(OBJECT_ID_TEXT.search(output))


class FakeGit(object):
    """Stand-in for subprocess.run answering read-only git subcommands."""

    def __init__(self, rules):
        """Start with a clean, commit-free synthetic repository."""
        self.rules = rules
        self.calls = []
        self.overrides = {}
        self.candidates = b"".join(path.encode("utf-8") + b"\0"
                                   for path in SCRATCH_PATHS)
        self.stage = b""
        self.revs = []
        self.objects = {}
        self.trees = {}

    @staticmethod
    def subcommand(command):
        """Return (override key, remaining arguments) for a command."""
        for index, item in enumerate(command):
            if item in audit.READ_ONLY_COMMANDS:
                rest = list(command[index + 1:])
                if item == "ls-files" and "--stage" in rest:
                    return "ls-files --stage", rest
                if item == "cat-file":
                    return "cat-file " + rest[0], rest
                return item, rest
        return None, []

    def probe_output(self, request):
        """Answer check-ignore as a correct root .gitignore would."""
        records = []
        for probe in request.decode("utf-8").split("\0")[:-1]:
            if probe in self.rules.ignored_probes:
                records += [".gitignore", "1", "synthetic", probe]
            else:
                records += ["", "", "", probe]
        return "".join(record + "\0" for record in records).encode("utf-8")

    def batch(self, request, contents):
        """Answer cat-file --batch-check or --batch from the object store."""
        output = b""
        for name in request.decode("ascii").split("\n")[:-1]:
            kind, data = self.objects[name]
            output += ("%s %s %d\n" % (name, kind, len(data))).encode("ascii")
            if contents:
                output += data + b"\n"
        return output

    def default(self, key, rest, request):
        """Return the synthetic stdout for one subcommand."""
        if key == "--version":
            return b"git version 2.45.1.windows.1\n"
        if key == "check-ignore":
            return self.probe_output(request)
        if key == "ls-files":
            return self.candidates
        if key == "ls-files --stage":
            return self.stage
        if key == "rev-list":
            return "".join(name + "\n" for name in self.revs).encode("ascii")
        if key == "ls-tree":
            return "".join("%s %s %s\t%s\0" % entry
                           for entry in self.trees.get(rest[-1], [])
                           ).encode("utf-8")
        if key == "cat-file --batch-check":
            return self.batch(request, False)
        if key == "cat-file --batch":
            return self.batch(request, True)
        raise AssertionError("unexpected git subcommand")

    def __call__(self, command, **kwargs):
        """Record the call and return a CompletedProcess or raise."""
        self.calls.append((list(command), kwargs))
        key, rest = self.subcommand(command)
        request = kwargs.get("input")
        if key in self.overrides:
            action = self.overrides[key]
            if isinstance(action, BaseException):
                raise action
            code, stdout = action if isinstance(action, tuple) else (0,
                                                                     action)
        else:
            code, stdout = 0, self.default(key, rest, request)
        return subprocess.CompletedProcess(command, code, stdout, b"")


class FakeGitTestCase(ScratchTestCase):
    """Audit behaviour with git simulated; no real git runs."""

    def setUp(self):
        """Create the scratch repository and a synthetic git stand-in."""
        super().setUp()
        self.fake = FakeGit(self.rules)

    def run_fake(self, argv=None):
        """Run the audit with the simulated git."""
        with mock.patch.object(audit, "locate_git", return_value=FAKE_GIT), \
                mock.patch.object(audit.subprocess, "run", self.fake):
            return self.run_audit(argv)

    def test_clean_zero_commit_repository_passes(self):
        """A clean repository without commits passes with zero history."""
        code, output = self.run_fake()
        self.assertEqual(code, audit.EXIT_PASS, output)
        for text in ("RESULT: PASS", "Tracked files  : 0 (index entries)",
                     "History        : 0 commit(s) and 0 annotated tag(s)",
                     "History state  : no commits",
                     "Evidence       : no commits and no staged or tracked "
                     "files", "Candidates     : %d path(s): 0 tracked, %d "
                     "untracked" % (len(SCRATCH_PATHS), len(SCRATCH_PATHS))):
            self.assertIn(text, output)

    def test_git_is_invoked_read_only_and_isolated(self):
        """Every call is a bounded, shell-free, read-only, isolated call."""
        redirects = {"GIT_DIR": os.path.join(self.work, "elsewhere"),
                     "GIT_WORK_TREE": self.work,
                     "GIT_INDEX_FILE": os.path.join(self.work, "index"),
                     "GIT_OBJECT_DIRECTORY": self.work,
                     "GIT_CONFIG_PARAMETERS": "'core.fsmonitor'='true'",
                     "GIT_REPLACE_REF_BASE": "refs/synthetic/"}
        with mock.patch.dict(os.environ, redirects):
            code, output = self.run_fake()
        self.assertEqual(code, audit.EXIT_PASS, output)
        self.assertTrue(self.fake.calls)
        keys = []
        for command, kwargs in self.fake.calls:
            key, _ = FakeGit.subcommand(command)
            keys.append(key)
            self.assertEqual(command[0], FAKE_GIT)
            for option in ("--no-replace-objects", "--no-optional-locks",
                           "core.fsmonitor=false",
                           "--git-dir=" + os.path.join(self.repo, ".git"),
                           "--work-tree=" + self.repo):
                self.assertIn(option, command)
            self.assertNotIn("--literal-pathspecs", command)
            self.assertIs(kwargs["shell"], False)
            self.assertEqual(kwargs["cwd"], self.repo)
            self.assertEqual(kwargs["timeout"], audit.GIT_TIMEOUT_SECONDS)
            env = kwargs["env"]
            for name in redirects:
                self.assertNotIn(name, env)
            self.assertNotIn("GIT_LITERAL_PATHSPECS", env)
            self.assertEqual(env["GIT_NO_REPLACE_OBJECTS"], "1")
            self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")
            self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(keys, ["--version", "check-ignore", "ls-files",
                                "ls-files --stage", "rev-list"])
        command, kwargs = self.fake.calls[1]
        self.assertEqual(command[-6:], ["check-ignore", "--no-index",
                                        "--verbose", "--non-matching", "-z",
                                        "--stdin"])
        probes = self.rules.ignored_probes + self.rules.trackable_probes
        self.assertEqual(kwargs["input"], "".join(
            probe + "\0" for probe in probes).encode("utf-8"))
        command, _ = self.fake.calls[4]
        self.assertEqual(command[-5:], ["rev-list", "--objects",
                                        "--no-object-names", "--all",
                                        "--reflog"])

    def test_run_git_refuses_other_subcommands(self):
        """Only the read-only subcommand set may run."""
        with mock.patch.object(audit.subprocess, "run", self.fake):
            for args in (["add", "x"], ["commit"], ["gc"], ["replace"],
                         ["update-ref", "x"], []):
                with self.subTest(args=args), \
                        self.assertRaises(audit.OperationalError):
                    audit.run_git(FAKE_GIT, args)
        self.assertEqual(self.fake.calls, [])

    def test_git_failures_are_operational(self):
        """Failure, timeout and malformed output never read as clean."""
        blob = oid(1)
        for overrides, revs, expected in (
                ({"--version": b"git version 2.23.0\n"}, [],
                 "or later is required"),
                ({"--version": b"not git\n"}, [], "malformed"),
                ({"check-ignore": (128, b"")}, [], "exit status 128"),
                ({"check-ignore": b"x\0"}, [], "malformed"),
                ({"ls-files": subprocess.TimeoutExpired(["git"], 1)}, [],
                 "timed out"),
                ({"ls-files": FileNotFoundError()}, [], "could not be run"),
                ({"ls-files": b"README.md"}, [], "malformed"),
                ({"ls-files": b"\xff\0"}, [], "malformed"),
                ({"ls-files": b"README.md\0\0"}, [], "malformed"),
                ({"ls-files --stage": b"garbage\0"}, [], "malformed"),
                ({"rev-list": b"not-an-object-id\n"}, [], "malformed"),
                ({"rev-list": (1, b"")}, [], "exit status 1"),
                ({"cat-file --batch-check": b""}, [blob], "malformed"),
                ({"cat-file --batch": b"garbage"}, [blob], "malformed")):
            with self.subTest(expected=expected, overrides=list(overrides)):
                self.fake = FakeGit(self.rules)
                self.fake.overrides = overrides
                self.fake.revs = revs
                self.fake.objects = {blob: ("blob", b"clean\n")}
                code, output = self.run_fake()
                self.assert_operational(code, output, expected)

    def test_missing_or_unsafe_git_is_operational(self):
        """No git.exe, or one inside the repository, stops the audit."""
        empty = os.path.join(self.work, "empty-path")
        os.makedirs(empty)
        tripwire = mock.Mock(side_effect=AssertionError("git was run"))
        with mock.patch.dict(os.environ, {"PATH": empty}), \
                mock.patch.object(audit.subprocess, "run", tripwire):
            code, output = self.run_audit()
        self.assert_operational(code, output, "was not found")
        inside = self.path("tools/git.exe")
        self.write("tools/git.exe", b"synthetic")
        with mock.patch.dict(os.environ,
                             {"PATH": os.path.dirname(inside)}), \
                mock.patch.object(audit.subprocess, "run", tripwire):
            code, output = self.run_audit()
        self.assert_operational(code, output, "inside the repository")
        tripwire.assert_not_called()

    def test_locate_git_never_follows_links(self):
        """Linked executables or PATH ancestors, and odd entries, fail."""
        tools = os.path.join(self.work, "tools")
        executable = os.path.join(tools, "git.exe")
        os.makedirs(tools)
        with open(executable, "wb") as handle:
            handle.write(b"synthetic")
        with mock.patch.dict(os.environ, {"PATH": tools + "\\"}):
            self.assertEqual(audit.locate_git(), executable)
        for target, info in ((executable, FILE_LINK), (tools, JUNCTION),
                             (self.work, JUNCTION)):
            with self.subTest(target=target), \
                    mock.patch.dict(os.environ, {"PATH": tools}), \
                    mock.patch.object(audit.os, "lstat", simulated_lstat(
                        {os.path.normcase(target): info})):
                with self.assertRaises(audit.OperationalError) as caught:
                    audit.locate_git()
                self.assertIn("reparse point", str(caught.exception))
        folder = os.path.join(self.work, "folder-tools")
        os.makedirs(os.path.join(folder, "git.exe"))
        with mock.patch.dict(os.environ, {"PATH": folder}):
            with self.assertRaises(audit.OperationalError) as caught:
                audit.locate_git()
        self.assertIn("not a regular file", str(caught.exception))
        for entry in ("relative-tools", "\\\\server\\share",
                      tools.replace("\\", "/"), tools + "\\..\\tools"):
            with self.subTest(entry=entry), \
                    mock.patch.dict(os.environ, {"PATH": entry}):
                with self.assertRaises(audit.OperationalError) as caught:
                    audit.locate_git()
                self.assertIn("was not found", str(caught.exception))

    def test_real_linked_git_is_refused(self):
        """A real file symlink or junction on the git path is refused."""
        tools = os.path.join(self.work, "real-tools")
        os.makedirs(tools)
        target = os.path.join(tools, "target.exe")
        with open(target, "wb") as handle:
            handle.write(b"synthetic")
        link_dir = os.path.join(self.work, "linked-tools")
        make_junction(self, link_dir, tools)
        with open(os.path.join(tools, "git.exe"), "wb") as handle:
            handle.write(b"synthetic")
        with mock.patch.dict(os.environ, {"PATH": link_dir}):
            with self.assertRaises(audit.OperationalError) as caught:
                audit.locate_git()
        self.assertIn("reparse point", str(caught.exception))

    def test_real_symlinked_git_is_refused(self):
        """A real file symlink standing in for git.exe is refused."""
        tools = os.path.join(self.work, "symlink-tools")
        os.makedirs(tools)
        target = os.path.join(tools, "target.exe")
        with open(target, "wb") as handle:
            handle.write(b"synthetic")
        make_file_symlink(self, os.path.join(tools, "git.exe"), target)
        with mock.patch.dict(os.environ, {"PATH": tools}):
            with self.assertRaises(audit.OperationalError) as caught:
                audit.locate_git()
        self.assertIn("reparse point", str(caught.exception))

    def test_shallow_and_grafted_history_is_refused(self):
        """A shallow boundary or graft file fails closed before git runs."""
        tripwire = mock.Mock(side_effect=AssertionError("git was run"))
        git_dir = os.path.join(self.repo, ".git")
        cases = (("shallow", "shallow boundary"),
                 ("info/grafts", "legacy graft file"))
        for relative, expected in cases:
            with self.subTest(relative=relative):
                self.write(".git/" + relative, oid(1) + "\n")
                with mock.patch.object(audit, "locate_git", tripwire), \
                        mock.patch.object(audit.subprocess, "run", tripwire):
                    code, output = self.run_audit()
                self.assert_operational(code, output, expected)
                os.remove(os.path.join(git_dir, *relative.split("/")))
        info = os.path.join(git_dir, "info")
        for target in (info,):
            with mock.patch.object(audit.os, "lstat", simulated_lstat(
                    {os.path.normcase(target): JUNCTION})), \
                    mock.patch.object(audit, "locate_git", tripwire):
                code, output = self.run_audit()
            self.assert_operational(code, output, "not a plain directory")
        shutil.rmtree(info)
        with open(info, "wb") as handle:
            handle.write(b"synthetic")
        with mock.patch.object(audit, "locate_git", tripwire):
            code, output = self.run_audit()
        self.assert_operational(code, output, "not a plain directory")
        tripwire.assert_not_called()

    def test_gitfile_redirect_is_refused(self):
        """A .git file that could redirect Git is refused."""
        git_dir = os.path.join(self.repo, ".git")
        shutil.rmtree(git_dir)
        with open(git_dir, "wb") as handle:
            handle.write(b"gitdir: C:\\elsewhere\n")
        code, output = self.run_fake()
        self.assert_operational(code, output, ".git", "is not a directory")
        self.assertEqual(self.fake.calls, [])

    def test_policy_location_and_content_are_enforced(self):
        """Only a valid policy inside the repository config is read."""
        elsewhere = os.path.join(self.work, "policy.json")
        in_root = self.path("publishable_policy.json")
        for path in (elsewhere, in_root):
            shutil.copyfile(REAL_POLICY, path)
        self.write("config/duplicate.json", '{"step": "3", "step": "3"}')
        self.write("config/latin.json", b"\xff\xfe{}")
        self.write("config/broken.json", '{"step": ')
        weak = read_json(REAL_POLICY)
        weak["forbidden"]["extensions"].remove(".pt")
        self.write("config/weak.json", json.dumps(weak))
        for argv, expected in (
                (["--policy", elsewhere], "must be read from"),
                (["--policy", in_root], "must be read from"),
                (["--policy", os.path.join(self.repo, "config")],
                 "must be read from"),
                (["--policy", "config\\publishable_policy.json"],
                 "policy path"),
                (["--policy", self.path("config/publishable_policy.json")
                  .replace("\\", "/")], "policy path"),
                (["--policy", self.path("config/duplicate.json")],
                 "repeats a JSON key"),
                (["--policy", self.path("config/latin.json")],
                 "not valid UTF-8"),
                (["--policy", self.path("config/broken.json")],
                 "not valid JSON"),
                (["--policy", self.path("config/weak.json")],
                 "omits mandatory entries: .pt")):
            with self.subTest(argv=argv):
                code, output = self.run_fake(argv)
                self.assert_operational(code, output, expected)

    def test_tightened_policy_file_applies(self):
        """A stricter policy in the config directory is honoured."""
        tight = read_json(REAL_POLICY)
        tight["max_candidate_bytes"] = 16
        self.write("config/tight.json", json.dumps(tight))
        code, output = self.run_fake(["--policy",
                                      self.path("config/tight.json")])
        self.assert_findings(code, output,
                             "FINDING [candidates] README.md: oversized (1)")

    def test_unsafe_candidate_paths_are_findings_and_never_opened(self):
        """Traversal, .git and ambiguous paths are reported, not read."""
        self.fake.candidates = (b"../escape.md\0.git/config\0"
                                b"docs/GIT~1/x.md\0docs\\x.md\0"
                                b"docs/NUL.md\0docs/x.md:stream\0")
        opened = []
        real_open = builtins.open

        def recording_open(file, mode="r", *args, **kwargs):
            """Record every path opened."""
            opened.append(os.path.normcase(os.path.abspath(os.fspath(file))))
            return real_open(file, mode, *args, **kwargs)

        with mock.patch.object(builtins, "open", recording_open):
            code, output = self.run_fake()
        self.assert_findings(
            code, output,
            "FINDING [candidates] ../escape.md: unsafe_path (1)",
            "FINDING [candidates] .git/config: git_metadata_path (1)",
            "FINDING [candidates] docs/GIT~1/x.md: unsafe_path (1)",
            "FINDING [candidates] docs\\x.md: unsafe_path (1)",
            "FINDING [candidates] docs/NUL.md: unsafe_path (1)",
            "FINDING [candidates] docs/x.md:stream: unsafe_path (1)")
        policy = os.path.normcase(self.path("config/publishable_policy.json"))
        self.assertEqual(opened, [policy])

    def test_worktree_entries_must_be_plain_files(self):
        """File ancestors, directories, missing files and links fail."""
        self.write("docs/file.md", "Synthetic file.\n")
        os.makedirs(self.path("docs/dir.md"))
        self.fake.candidates = (b"docs/file.md/child.md\0docs/dir.md\0"
                                b"docs/missing.md\0")
        code, output = self.run_fake()
        self.assert_findings(
            code, output,
            "FINDING [candidates] docs/file.md/child.md: file_ancestor (1)",
            "FINDING [candidates] docs/dir.md: not_regular_file (1)",
            "FINDING [candidates] docs/missing.md: missing_file (1)")
        for target, info in ((self.path("docs"), JUNCTION),
                             (self.path("docs/GUIDE.md"), FILE_LINK)):
            with self.subTest(target=target):
                self.fake = FakeGit(self.rules)
                self.fake.candidates = b"docs/GUIDE.md\0"
                with mock.patch.object(audit.os, "lstat", simulated_lstat(
                        {os.path.normcase(target): info})):
                    code, output = self.run_fake()
                self.assert_findings(code, output, "FINDING [candidates] "
                                     "docs/GUIDE.md: link_or_reparse_point")

    def test_observed_leaf_link_is_never_opened(self):
        """A link or non-file seen just before open is refused unopened."""
        tripwire = mock.Mock(side_effect=AssertionError("opened"))
        for info, expected in ((FILE_LINK, "link_or_reparse_point"),
                               (JUNCTION, "link_or_reparse_point"),
                               (DIRECTORY, "not_regular_file")):
            with self.subTest(expected=expected), \
                    mock.patch.object(audit.os, "lstat", simulated_lstat(
                        {os.path.normcase(self.path("docs/GUIDE.md")):
                         info})), \
                    mock.patch.object(builtins, "open", tripwire):
                self.assertEqual(audit.read_worktree_file("docs/GUIDE.md",
                                                          1024),
                                 (expected, None))
        tripwire.assert_not_called()

    def test_real_junction_and_symlink_candidates_are_refused(self):
        """Real links on a candidate path are refused where supported."""
        outside = os.path.join(self.work, "outside")
        os.makedirs(outside)
        with open(os.path.join(outside, "x.md"), "wb") as handle:
            handle.write(b"Synthetic outside file.\n")
        make_junction(self, self.path("docs/linked"), outside)
        self.assertEqual(audit.worktree_problem("docs/linked/x.md"),
                         "link_or_reparse_point")

    def test_real_file_symlink_candidate_is_refused(self):
        """A real file symlink candidate is refused where supported."""
        target = os.path.join(self.work, "target.md")
        with open(target, "wb") as handle:
            handle.write(b"Synthetic target.\n")
        make_file_symlink(self, self.path("docs/link.md"), target)
        self.assertEqual(audit.worktree_problem("docs/link.md"),
                         "link_or_reparse_point")
        self.assertEqual(audit.read_worktree_file("docs/link.md", 1024),
                         ("link_or_reparse_point", None))

    def test_oversized_binary_and_non_utf8_candidates(self):
        """Unreadable candidate content is a finding, not a pass."""
        tight = read_json(REAL_POLICY)
        tight["max_candidate_bytes"] = 4096
        self.write("config/tight.json", json.dumps(tight))
        self.write("docs/big.md", "a" * 5000)
        self.write("docs/binary.md", b"abc\0def")
        self.write("docs/latin.md", b"\xff\xfe\xfd")
        self.fake.candidates = b"docs/big.md\0docs/binary.md\0docs/latin.md\0"
        code, output = self.run_fake(["--policy",
                                      self.path("config/tight.json")])
        self.assert_findings(
            code, output,
            "FINDING [candidates] docs/big.md: oversized (1)",
            "FINDING [candidates] docs/binary.md: binary_content (1)",
            "FINDING [candidates] docs/latin.md: not_utf8 (1)")

    def test_staged_and_all_reachable_history_objects_are_audited(self):
        """Index blobs, commits, tags, trees and pathless blobs are read."""
        token = github_token()
        commit, tree, note, other, tag, loose_tree, loose, sub = (
            oid(number) for number in range(1, 9))
        identity = ("Synthetic Tester <synthetic.tester@example.invalid> "
                    "0 +0000")
        commit_body = ("tree %s\nauthor %s\ncommitter %s\n\nSynthetic "
                       "message %s\n" % (tree, identity, identity, token))
        tag_body = ("object %s\ntype commit\ntag synthetic\ntagger %s\n\n"
                    "Synthetic tag %s\n" % (commit, identity, token))
        self.fake.stage = ("100644 %s 0\tdocs/STAGED.md\0" % note).encode()
        self.fake.revs = [commit, tree, note, other, tag, loose_tree, loose]
        self.fake.objects = {
            commit: ("commit", commit_body.encode("utf-8")),
            tree: ("tree", b"synthetic tree"),
            note: ("blob", ("Synthetic note %s\n" % token).encode()),
            other: ("blob", b"SYNTHETIC placeholder\n"),
            tag: ("tag", tag_body.encode("utf-8")),
            loose_tree: ("tree", b"synthetic tree"),
            loose: ("blob", ("Path %s\n" % user_path()).encode())}
        self.fake.trees = {
            commit: [("100644", "blob", note, "docs/NOTE.md"),
                     ("100644", "blob", other, "model.pt"),
                     ("120000", "blob", other, "docs/link.md"),
                     ("160000", "commit", sub, "vendor/sub")],
            loose_tree: [("100644", "blob", other, "cases/answer.json")]}
        with mock.patch.dict(os.environ, {"EHR_SYNTH_SECRET": ENV_TOKEN}):
            code, output = self.run_fake()
        self.assert_findings(
            code, output,
            "FINDING [staged] docs/STAGED.md: github_token (1)",
            "FINDING [history] <commit object>: github_token (1)",
            "FINDING [history] <annotated tag object>: github_token (1)",
            "FINDING [history] docs/NOTE.md: github_token (1)",
            "FINDING [history] model.pt: forbidden_extension (1)",
            "FINDING [history] docs/link.md: link_or_reparse_point (1)",
            "FINDING [history] vendor/sub: gitlink_entry (1)",
            "FINDING [history] cases/answer.json: forbidden_directory (1)",
            "FINDING [history] <blob outside every commit tree>: "
            "machine_user_path (1)",
            "History        : 1 commit(s) and 1 annotated tag(s) scanned; "
            "5 path(s); 2 blob(s) scanned",
            "Outside trees  : 1 tree(s) named only by a ref or tag; 1 "
            "blob(s) outside every tree listing")
        self.assert_no_values(output, token, "Synthetic message",
                              "Synthetic tag", "Synthetic note")
        batch = [kwargs["input"] for command, kwargs in self.fake.calls
                 if FakeGit.subcommand(command)[0] == "cat-file --batch"]
        self.assertTrue(any(tag.encode() in request for request in batch))


class GitRepositoryTestCase(ScratchTestCase):
    """End-to-end audits of real synthetic Git repositories."""

    make_git_dir = False

    def setUp(self):
        """Create an empty synthetic repository with the real git."""
        super().setUp()
        try:
            self.git_path = audit.locate_git()
        except audit.OperationalError as error:
            self.skipTest("git.exe is unavailable: %s" % error)
        self.hooks = os.path.join(self.work, "no-hooks")
        self.template = os.path.join(self.work, "no-template")
        os.makedirs(self.hooks)
        os.makedirs(self.template)
        self.init_repository(self.repo)

    @staticmethod
    def scratch_environment():
        """Return an environment without GIT_* values for setup commands."""
        env = {name: value for name, value in os.environ.items()
               if not name.upper().startswith("GIT_")}
        env["GIT_TERMINAL_PROMPT"] = "0"
        return env

    def init_repository(self, path):
        """Initialise a synthetic repository strictly inside scratch."""
        self.assertTrue(self.inside_scratch(path))
        result = subprocess.run(
            [self.git_path, "-c", "init.defaultBranch=main", "init", "-q",
             "--template=" + self.template, path],
            cwd=self.work, env=self.scratch_environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=120)
        self.assertEqual(result.returncode, 0, "synthetic git init failed")

    def git(self, *args, repo=None, stdin=None):
        """Run a setup git command in a scratch repository; return stdout.

        The synthetic identity and settings are passed with -c only, so no
        configuration file is written.
        """
        repo = repo or self.repo
        self.assertTrue(self.inside_scratch(repo))
        command = [self.git_path, "--git-dir=" + os.path.join(repo, ".git"),
                   "--work-tree=" + repo]
        for setting in SCRATCH_GIT_SETTINGS + ("core.hooksPath="
                                               + self.hooks,):
            command += ["-c", setting]
        options = {"cwd": repo, "env": self.scratch_environment(),
                   "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
                   "timeout": 120}
        if stdin is None:
            options["stdin"] = subprocess.DEVNULL
        else:
            options["input"] = stdin
        result = subprocess.run(command + list(args), **options)
        self.assertEqual(result.returncode, 0,
                         "synthetic git %s failed" % args[0])
        return result.stdout.decode("utf-8").strip()

    def commit_all(self, message="Synthetic commit"):
        """Stage every non-ignored file and commit it."""
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "--allow-empty", "-m",
                 message)

    def snapshot(self):
        """Return scratch metadata and digests of key .git files."""
        entries = []
        for current, directories, files in os.walk(self.work):
            for name in directories + files:
                path = os.path.join(current, name)
                info = os.lstat(path)
                entries.append((os.path.relpath(path, self.work),
                                stat.S_IFMT(info.st_mode), info.st_size,
                                info.st_mtime_ns))
        digests = {}
        for name in ("index", "config", "HEAD", "packed-refs"):
            path = os.path.join(self.repo, ".git", name)
            if os.path.isfile(path):
                with open(path, "rb") as handle:
                    digests[name] = hashlib.sha256(handle.read()).hexdigest()
        return sorted(entries), digests

    def test_zero_commit_repository_passes(self):
        """A real repository without commits passes with zero history."""
        code, output = self.run_audit()
        self.assertEqual(code, audit.EXIT_PASS, output)
        for text in ("RESULT: PASS", "Tracked files  : 0 (index entries)",
                     "Staged index   : 0 entr(ies)",
                     "History        : 0 commit(s) and 0 annotated tag(s)",
                     "Evidence       : no commits and no staged or tracked "
                     "files", " path(s): 0 tracked, "):
            self.assertIn(text, output)

    def test_real_check_ignore_probe_command(self):
        """The exact stdin check-ignore command passes with real git."""
        self.assertEqual(audit.ignore_probe_findings(self.git_path,
                                                     self.rules), [])

    def test_ignore_probe_contract_through_git(self):
        """Missing, over-broad and misplaced ignore rules are reported."""
        original = self.path(".gitignore")
        with open(original, "rb") as handle:
            text = handle.read().decode("utf-8")
        self.assertIn("\n*.pt\n", text)
        self.assertIn("\noutputs/\n", text)
        exclude = os.path.join(self.repo, ".git", "info", "exclude")
        for gitignore, excluded, expected in (
                (text.replace("\n*.pt\n", "\n"), None,
                 ["FINDING [ignore-probes] probe.pt: "
                  "ignore_probe_not_ignored (1)",
                  "FINDING [ignore-probes] nested/dir/probe.pt: "
                  "ignore_probe_not_ignored (1)"]),
                (text.replace("\noutputs/\n", "\n").replace(
                    "\n/outputs/\n", "\n"), None,
                 ["FINDING [ignore-probes] outputs/probe.json: "
                  "ignore_probe_not_ignored (1)",
                  "FINDING [ignore-probes] nested/outputs/probe.json: "
                  "ignore_probe_not_ignored (1)"]),
                (text + "*.py\n", None,
                 ["FINDING [ignore-probes] scripts/probe_tool.py: "
                  "trackable_probe_ignored (1)"]),
                (text + "/tests/data/\n", None,
                 ["FINDING [ignore-probes] tests/data/synthetic_probe.json: "
                  "trackable_probe_ignored (1)"]),
                (text.replace("\n*.pt\n", "\n"), "*.pt\n",
                 ["FINDING [ignore-probes] probe.pt: "
                  "ignore_rule_outside_gitignore (1)"])):
            with self.subTest(expected=expected[0]):
                self.write(".gitignore", gitignore)
                if excluded:
                    self.write(".git/info/exclude", excluded)
                code, output = self.run_audit()
                self.assert_findings(code, output, *expected)
                self.write(".gitignore", text)
                if os.path.exists(exclude):
                    os.remove(exclude)

    def test_clean_candidates_index_and_multi_commit_history_pass(self):
        """Clean commits, an annotated tag and staged changes pass."""
        self.commit_all("Synthetic first commit")
        self.write("README.md", "# Synthetic repository, second edition\n")
        self.commit_all("Synthetic second commit")
        self.git("tag", "-a", "-m", "Synthetic release tag", "synthetic-v1")
        self.write("docs/GUIDE.md", "Synthetic staged change.\n")
        self.git("add", "--", "docs/GUIDE.md")
        tracked = len(self.git("ls-files").splitlines())
        self.assertGreater(tracked, 0)
        code, output = self.run_audit()
        self.assertEqual(code, audit.EXIT_PASS, output)
        for text in ("History        : 2 commit(s) and 1 annotated tag(s)",
                     "Tracked files  : %d (index entries)" % tracked,
                     "Staged index   : %d entr(ies)" % tracked,
                     "RESULT: PASS"):
            self.assertIn(text, output)
        self.assertNotIn("Evidence       :", output)

    def sensitive_files(self):
        """Write synthetic sensitive files; return (path, rule) pairs."""
        pairs = (("outputs/answer.json", "forbidden_directory"),
                 ("app/cases/case.md", "forbidden_directory"),
                 ("nested/protected-data/note.txt", "forbidden_directory"),
                 (".env", "forbidden_name"), ("id_rsa", "forbidden_name"),
                 ("model.pt", "forbidden_extension"),
                 ("rows.csv", "forbidden_extension"))
        for path, _ in pairs:
            self.write(path, "SYNTHETIC placeholder\n")
        return pairs

    def test_force_added_sensitive_files_in_candidates_and_index(self):
        """Force-added sensitive files are reported in both scopes."""
        pairs = self.sensitive_files()
        self.git("add", "-f", "--", *[path for path, _ in pairs])
        code, output = self.run_audit()
        expected = []
        for path, rule in pairs:
            expected += ["FINDING [candidates] %s: %s (1)" % (path, rule),
                         "FINDING [staged] %s: %s (1)" % (path, rule)]
        self.assert_findings(code, output, *expected)
        self.assertIn("Tracked files  : %d" % len(pairs), output)

    def test_sensitive_files_and_content_only_in_history(self):
        """Removed sensitive files and secrets stay visible in history."""
        pairs = self.sensitive_files()
        self.write("docs/NOTE.md", "Synthetic note %s\n" % github_token())
        self.git("add", "-f", "--", *[path for path, _ in pairs])
        self.commit_all("Synthetic commit with sensitive files")
        self.git("rm", "-q", "--cached", "--", *[path for path, _ in pairs])
        self.write("docs/NOTE.md", "Clean synthetic note.\n")
        self.commit_all("Synthetic clean-up commit")
        code, output = self.run_audit()
        expected = ["FINDING [history] %s: %s (1)" % pair for pair in pairs]
        expected.append("FINDING [history] docs/NOTE.md: github_token (1)")
        self.assert_findings(code, output, *expected)
        self.assertNotIn("FINDING [candidates]", output)
        self.assertNotIn("FINDING [staged]", output)
        self.assert_no_values(output, github_token())

    def test_commit_and_tag_messages_are_scanned(self):
        """Commit and annotated-tag messages are scanned, never shown."""
        self.commit_all("Synthetic message " + github_token())
        self.git("tag", "-a", "-m", "Synthetic tag " + user_path(),
                 "synthetic-v1")
        code, output = self.run_audit()
        self.assert_findings(
            code, output,
            "FINDING [history] <commit object>: github_token (1)",
            "FINDING [history] <annotated tag object>: "
            "machine_user_path (1)")
        self.assert_no_values(output, github_token(), "Synthetic message",
                              "Synthetic tag")

    def test_direct_blob_and_tree_refs_are_audited(self):
        """Blobs and trees named only by refs cannot escape review."""
        self.commit_all()
        secret_blob = self.git("hash-object", "-w", "--stdin",
                               stdin=("Synthetic loose " + github_token()
                                      + "\n").encode("ascii"))
        weight = self.git("hash-object", "-w", "--stdin",
                          stdin=b"SYNTHETIC placeholder\n")
        tree = self.git("mktree", stdin=("100644 blob %s\tmodel.pt\n"
                                         % weight).encode("ascii"))
        self.git("update-ref", "refs/synthetic/blob", secret_blob)
        self.git("update-ref", "refs/synthetic/tree", tree)
        code, output = self.run_audit()
        self.assert_findings(
            code, output,
            "FINDING [history] <blob outside every commit tree>: "
            "github_token (1)",
            "FINDING [history] model.pt: forbidden_extension (1)",
            "Outside trees  : 1 tree(s) named only by a ref or tag; 1 "
            "blob(s) outside every tree listing")
        self.assert_no_values(output, github_token())

    def test_replacement_refs_cannot_hide_history(self):
        """git replace cannot mask the original commit or blob."""
        token = github_token()
        self.write("docs/NOTE.md", "Synthetic note %s\n" % token)
        self.commit_all("Synthetic first commit " + token)
        original_commit = self.git("rev-parse", "HEAD")
        original_blob = self.git("rev-parse", "HEAD:docs/NOTE.md")
        self.write("docs/NOTE.md", "Clean synthetic note.\n")
        self.commit_all("Clean synthetic second commit")
        clean_blob = self.git("rev-parse", "HEAD:docs/NOTE.md")
        tree = self.git("rev-parse", "HEAD^{tree}")
        replacement = self.git("commit-tree", tree, "-m",
                               "Clean synthetic replacement")
        self.git("replace", "-f", original_blob, clean_blob)
        self.git("replace", "-f", original_commit, replacement)
        self.assertNotIn(token, self.git("cat-file", "blob", original_blob))
        self.assertNotIn(token, self.git("cat-file", "commit",
                                         original_commit))
        code, output = self.run_audit()
        self.assert_findings(
            code, output,
            "FINDING [history] docs/NOTE.md: github_token (1)",
            "FINDING [history] <commit object>: github_token (1)")
        self.assert_no_values(output, token)

    def test_shallow_and_grafted_repositories_fail_closed(self):
        """Real shallow and graft files make the audit operational."""
        self.commit_all()
        head = self.git("rev-parse", "HEAD")
        for relative, expected in (("shallow", "shallow boundary"),
                                   ("info/grafts", "legacy graft file")):
            with self.subTest(relative=relative):
                self.write(".git/" + relative, head + "\n")
                code, output = self.run_audit()
                self.assert_operational(code, output, expected)
                os.remove(os.path.join(self.repo, ".git",
                                       *relative.split("/")))
        code, output = self.run_audit()
        self.assertEqual(code, audit.EXIT_PASS, output)

    def test_content_findings_in_candidates_hide_values(self):
        """Untracked secrets, user paths and patient values are reported."""
        self.write("docs/NOTE.md", "".join([
            "Synthetic note " + github_token() + "\n",
            private_key_header() + "\n",
            credential_uri(SAMPLE_VALUE) + "\n",
            user_path() + "\n", patient_line(),
            assignment(API_KEY_NAME, ": ", SAMPLE_VALUE)]))
        code, output = self.run_audit()
        self.assert_findings(
            code, output,
            "FINDING [candidates] docs/NOTE.md: github_token (1)",
            "FINDING [candidates] docs/NOTE.md: private_key_block (1)",
            "FINDING [candidates] docs/NOTE.md: credential_uri (1)",
            "FINDING [candidates] docs/NOTE.md: machine_user_path (1)",
            "FINDING [candidates] docs/NOTE.md: "
            "patient_identifier_assignment (1)",
            "FINDING [candidates] docs/NOTE.md: "
            "unquoted_secret_assignment (1)")
        self.assert_no_values(output, github_token(), "Synthetic note",
                              private_key_header())

    def test_inherited_git_redirects_are_ignored(self):
        """GIT_* variables cannot point the audit at another repository."""
        other = os.path.join(self.work, "other")
        os.makedirs(other)
        self.init_repository(other)
        with open(os.path.join(other, ".env"), "wb") as handle:
            handle.write(b"SYNTHETIC placeholder\n")
        self.git("add", "-f", "--", ".env", repo=other)
        self.git("commit", "-q", "--no-verify", "-m", "Synthetic other",
                 repo=other)
        redirects = {"GIT_DIR": os.path.join(other, ".git"),
                     "GIT_WORK_TREE": other,
                     "GIT_INDEX_FILE": os.path.join(other, ".git", "index"),
                     "GIT_OBJECT_DIRECTORY": os.path.join(other, ".git",
                                                          "objects")}
        with mock.patch.dict(os.environ, redirects):
            code, output = self.run_audit()
        self.assertEqual(code, audit.EXIT_PASS, output)
        self.assertIn("History        : 0 commit(s)", output)
        self.assertNotIn(other, output)

    def test_no_content_or_environment_value_leaks(self):
        """Output holds no content, value or environment value."""
        self.write("docs/NOTE.md", "Synthetic note %s %s\n%s"
                   % (github_token(), user_path(), patient_line()))
        self.commit_all("Synthetic message " + github_token())
        environment = {"EHR_SYNTH_SECRET": ENV_TOKEN,
                       "PGPASSWORD": ENV_TOKEN}
        with mock.patch.dict(os.environ, environment):
            code, output = self.run_audit()
        self.assertEqual(code, audit.EXIT_FINDINGS, output)
        self.assert_no_values(output, github_token(), "Synthetic note",
                              "Synthetic message", user_path())

    def test_audit_is_read_only(self):
        """No write, index, config, ref or object change; read-only git."""
        self.commit_all("Synthetic first commit")
        self.git("tag", "-a", "-m", "Synthetic tag", "synthetic-v1")
        self.write("docs/GUIDE.md", "Synthetic staged change.\n")
        self.git("add", "--", "docs/GUIDE.md")
        self.write("docs/UNTRACKED.md", "Synthetic untracked file.\n")
        before = self.snapshot()
        commands = []
        real_run = subprocess.run
        real_open = builtins.open
        tripwire = mock.Mock(side_effect=AssertionError("forbidden call"))

        def recording_run(command, **kwargs):
            """Record each git command and run it."""
            commands.append(list(command))
            return real_run(command, **kwargs)

        def guarded_open(file, mode="r", *args, **kwargs):
            """Refuse any write mode."""
            if any(flag in mode for flag in "wax+"):
                raise AssertionError("write attempted")
            return real_open(file, mode, *args, **kwargs)

        patches = [mock.patch.object(builtins, "open", guarded_open),
                   mock.patch.object(subprocess, "run", recording_run)]
        for owner, names in ((os, ("makedirs", "mkdir", "remove", "unlink",
                                   "rename", "replace", "rmdir", "symlink",
                                   "link", "utime", "chmod", "truncate")),
                             (shutil, ("rmtree", "copyfile", "copy",
                                       "copy2", "move"))):
            for name in names:
                patches.append(mock.patch.object(owner, name, tripwire))
        for patcher in patches:
            patcher.start()
        try:
            code, output = self.run_audit()
        finally:
            for patcher in reversed(patches):
                patcher.stop()
        self.assertEqual(code, audit.EXIT_PASS, output)
        tripwire.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(commands)
        for command in commands:
            key, _ = FakeGit.subcommand(command)
            self.assertIsNotNone(key)
            self.assertIn("--no-replace-objects", command)
            self.assertIn("--no-optional-locks", command)
            self.assertIn("core.fsmonitor=false", command)


if __name__ == "__main__":
    unittest.main()
