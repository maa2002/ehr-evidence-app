"""The freeze of the evaluated version (work item 64; ADR 0028, decision 1;
ADR 0029).

The freeze writes one protected record, once, before the final case set
is read: the commit the results are recorded under, the versions of
everything that writes, checks and scores a response, the digests of the
configuration it reads and of the pins of the records it rests on, and
the rating model's identity and settings as the operator names them. The
rating model is named in the protected record only.

An operator mode that reads the final case set asks `allowed`, which
refuses unless the record exists, the code and configuration now running
are the frozen commit's, with nothing changed or added, and every
version and digest is the record's. A development case set needs no
freeze; any other is refused.

The final question sets are worded after the freeze, so they are not in
its record: each is registered by its files' digests in a protected
record of its own, once, before it is read.

The records of releases 0.11.0 (freeze-v1) and 0.11.1 (freeze-v2) were
superseded before any result of the final case set was written (ADR 0029,
amendments): they are kept as they were written, and only the record of
this version's freeze is read. The question set registered under
freeze-v2, final-v1, was superseded with it.

Usage:
    python -m ehr_evidence_app.assessment.freeze
    python -m ehr_evidence_app.assessment.freeze --operator-freeze \\
        --rater-model <rating model>
    python -m ehr_evidence_app.assessment.freeze --operator-check
    python -m ehr_evidence_app.assessment.freeze --operator-register final-v2
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys

from ehr_evidence_app.storage import policy as guard

FINAL = "eval-100-v1"
FREEZE_VERSION = "freeze-v3"
SCHEMA = "assessment-freeze"
PROTECTED_ROOT = guard.PRIVATE_ROOT + "\\protected-data"
FREEZE_DIR = PROTECTED_ROOT + "\\assessment\\freeze"
RECORD_NAME = FREEZE_VERSION + ".json"
# The paths whose content is the evaluated code and configuration.
CODE_PATHS = ("src", "config")
# The final question sets: their files are worded after the freeze, and
# registered by digest before they are read.
QUESTION_SETS = PROTECTED_ROOT + "\\question-sets"
FINAL_QUESTIONS = ("final-v2",)
QUESTION_FILES = ("questions.csv", "record-questions.csv")
REGISTRATION_NAME = "registration.json"


class FreezeError(Exception):
    """A fail-closed failure carrying a finding code only."""


def digest_of(value):
    """Return the SHA-256 of a value's canonical JSON."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(
        ",", ":")).encode("utf-8")).hexdigest()


def versions():
    """Return what the evaluated version is made of: versions and digests
    only."""
    from ehr_evidence_app.assessment import baseline, partition, rubric
    from ehr_evidence_app.casesets import registry
    from ehr_evidence_app.evidence import record_bundles
    from ehr_evidence_app.generation import records
    from ehr_evidence_app.qa import record_vocabulary, vocabulary
    from ehr_evidence_app.sources import mimic
    found = dict(records.versions())
    found.update({
        "baseline": baseline.BASELINE_VERSION,
        "prose_rubric": rubric.RUBRIC_VERSION,
        "rater_instructions_sha256": rubric.CRITERIA_SHA256,
        "partition": "%s %d %s" % ((partition.VERSION,) + tuple(
            partition.PIN)),
        "condition_vocabulary": vocabulary.OUTPUT_VERSION,
        "record_vocabulary": record_vocabulary.OUTPUT_VERSION,
        "record_vocabulary_pin_sha256": digest_of(record_vocabulary.PIN),
        "final_case_set": FINAL,
        "final_case_set_pins_sha256": digest_of(registry.PINS[FINAL]),
        "final_record_bundles_pin_sha256": digest_of(
            record_bundles.PINS[FINAL]),
        "final_extract_pin_sha256": digest_of(mimic.EXTRACT_PINS[FINAL])})
    return found


def git(repo, *arguments, run=subprocess.run):
    """Return (exit code, output) of one git command in the repository."""
    try:
        done = run(("git", "-C", repo) + arguments, capture_output=True,
                   text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        raise FreezeError("git_unavailable") from None
    return done.returncode, done.stdout.strip()


def code_state(repo, run=subprocess.run):
    """Return the short commit of the checked-out code, refusing changed
    or added files in the code and configuration."""
    code, changed = git(repo, "status", "--porcelain",
                        "--untracked-files=all", "--", *CODE_PATHS, run=run)
    if code != 0:
        raise FreezeError("git_unavailable")
    if changed:
        raise FreezeError("code_modified")
    code, commit = git(repo, "rev-parse", "--short=7", "HEAD", run=run)
    if code != 0 or len(commit) != 7:
        raise FreezeError("git_unavailable")
    return commit


def same_code(repo, commit, run=subprocess.run):
    """Return True when the checked-out code and configuration are the
    commit's, though later commits may have changed other files."""
    code_state(repo, run)
    code, _ = git(repo, "diff", "--quiet", commit, "HEAD", "--",
                  *CODE_PATHS, run=run)
    if code not in (0, 1):
        raise FreezeError("git_unavailable")
    return code == 0


def repo_root():
    """Return the root of the checkout the code runs from."""
    return guard.REPO_ROOT


def freeze_dir(directory=None):
    """Return the freeze record's directory: the one given, or FREEZE_DIR
    as it is when called."""
    return FREEZE_DIR if directory is None else directory


def sets_root(root=None):
    """Return the final question sets' root: the one given, or
    QUESTION_SETS as it is when called."""
    return QUESTION_SETS if root is None else root


def record_path(directory=None):
    """Return the freeze record's path."""
    return os.path.join(freeze_dir(directory), RECORD_NAME)


def write_record(document, directory=None, protected=None):
    """Write the freeze record once; return its SHA-256."""
    from ehr_evidence_app.gnn import mapping
    directory = freeze_dir(directory)
    problem = mapping.output_problem(
        directory, PROTECTED_ROOT if protected is None else protected)
    if problem:
        raise FreezeError(problem)
    data = json.dumps(document, indent=1, sort_keys=True).encode("utf-8")
    os.makedirs(directory, exist_ok=True)
    try:
        with open(record_path(directory), "xb") as handle:
            handle.write(data)
    except FileExistsError:
        raise FreezeError("frozen_before") from None
    return hashlib.sha256(data).hexdigest()


def freeze(rater_model, rater_options=None, think=False,
           directory=None, protected=None, repo=None, run=subprocess.run):
    """Write the freeze record of the code now checked out, once; return
    its SHA-256. rater_model is the rating model's name as its server
    knows it."""
    from ehr_evidence_app.assessment import rubric
    if not rater_model:
        raise FreezeError("rater_model_missing")
    commit = code_state(repo or repo_root(), run)
    return write_record({
        "schema": SCHEMA, "version": FREEZE_VERSION, "commit": commit,
        "frozen_at": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "final_case_set": FINAL, "versions": versions(),
        "rater": {"model": rater_model,
                  "options": dict(rater_options or {
                      "temperature": 0, "seed": rubric.SEED}),
                  "think": bool(think),
                  "instructions_sha256": rubric.CRITERIA_SHA256}},
        directory, protected)


def read_record(directory=None):
    """Return (record, SHA-256) of the freeze record."""
    try:
        with open(record_path(directory), "rb") as handle:
            data = handle.read()
        document = json.loads(data.decode("utf-8"))
    except FileNotFoundError:
        raise FreezeError("not_frozen") from None
    except (OSError, UnicodeDecodeError, ValueError):
        raise FreezeError("record_unreadable") from None
    if document.get("schema") != SCHEMA or document.get(
            "version") != FREEZE_VERSION:
        raise FreezeError("record_unexpected")
    return document, hashlib.sha256(data).hexdigest()


def require(directory=None, repo=None, run=subprocess.run):
    """Return (record, SHA-256) of the freeze when the code running is its
    version; refuse otherwise."""
    document, digest = read_record(directory)
    if not same_code(repo or repo_root(), document["commit"], run):
        raise FreezeError("code_not_frozen")
    if versions() != document["versions"]:
        raise FreezeError("versions_changed")
    return document, digest


def allowed(case_set, directory=None):
    """Return None for a development case set, and the freeze record and
    its SHA-256 for the final one when the code running is the frozen
    version; refuse any other case set."""
    from ehr_evidence_app.casesets import registry
    from ehr_evidence_app.evidence import readers
    if case_set == readers.PILOT_LABEL:
        return None
    try:
        purpose = registry.spec(case_set)["purpose"]
    except registry.RegistryError:
        raise FreezeError("case_set_unknown") from None
    if purpose == "development":
        return None
    if case_set != FINAL:
        raise FreezeError("case_set_held_out")
    return require(directory)


def question_files(name, root=None):
    """Return the paths of a final question set's files."""
    if name not in FINAL_QUESTIONS:
        raise FreezeError("question_set_unknown")
    directory = os.path.join(sets_root(root), name)
    return directory, {file: os.path.join(directory, file)
                       for file in QUESTION_FILES}


def register(name, root=None, directory=None):
    """Register a final question set, worded after the freeze, by its
    files' sizes and digests, once; return the registration."""
    record, freeze_digest = read_record(directory)
    folder, files = question_files(name, root)
    found = {}
    for file, path in files.items():
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            raise FreezeError("question_file_missing") from None
        found[file] = {"size_bytes": len(data),
                       "sha256": hashlib.sha256(data).hexdigest()}
    registration = {"question_set": name, "freeze_sha256": freeze_digest,
                    "frozen_at": record["frozen_at"], "files": found}
    try:
        with open(os.path.join(folder, REGISTRATION_NAME), "x",
                  encoding="utf-8", newline="\n") as handle:
            json.dump(registration, handle, indent=1, sort_keys=True)
    except FileExistsError:
        raise FreezeError("registered_before") from None
    return registration


def registered(name, root=None, directory=None):
    """Return a final question set's registration, refusing one made under
    another freeze."""
    _, freeze_digest = read_record(directory)
    folder, _ = question_files(name, root)
    try:
        with open(os.path.join(folder, REGISTRATION_NAME),
                  encoding="utf-8") as handle:
            registration = json.load(handle)
    except FileNotFoundError:
        raise FreezeError("question_set_not_registered") from None
    except (OSError, ValueError):
        raise FreezeError("registration_unreadable") from None
    if registration.get("freeze_sha256") != freeze_digest:
        raise FreezeError("registration_of_another_freeze")
    return registration


def read_question_file(name, file, root=None, directory=None):
    """Return the bytes of a registered final question file, verified
    against its registration."""
    from ehr_evidence_app.gnn import mapping
    registration = registered(name, root, directory)
    entry = registration["files"][file]
    _, files = question_files(name, root)
    try:
        data, _ = mapping.read_verified(
            files[file], file, "question_file_changed",
            sha256=entry["sha256"], size=entry["size_bytes"])
    except mapping.MappingError as error:
        raise FreezeError(str(error)) from None
    return data


# Default and Operator Modes

def run_synthetic():
    """Return the problems of the freeze's checks on a stand-in
    repository: a changed file is refused, a later commit that changes
    only other files is the same code."""
    problems = []

    class Git:
        """A stand-in for git: one status, one HEAD, one diff answer."""

        def __init__(self, status="", head="abc1234", diff=0):
            self.status, self.head, self.diff = status, head, diff

        def __call__(self, command, **_):
            action = command[3]
            output = {"status": self.status, "rev-parse": self.head}.get(
                action, "")
            code = self.diff if action == "diff" else 0
            return subprocess.CompletedProcess(command, code, output, "")
    try:
        code_state("repo", run=Git(status=" M src/x.py"))
        problems.append("a changed file is not refused")
    except FreezeError:
        pass
    if code_state("repo", run=Git()) != "abc1234":
        problems.append("the commit is not read")
    if not same_code("repo", "abc1234", run=Git(head="def5678")):
        problems.append("a later commit that changes no code is refused")
    if same_code("repo", "abc1234", run=Git(diff=1)):
        problems.append("changed code is taken as frozen")
    if set(versions()) < {"prompt_version", "baseline", "prose_rubric",
                          "rater_instructions_sha256"}:
        problems.append("the version misses a part it is made of")
    return problems


def report_versions(document):
    """Print a freeze record's commit and versions: no data."""
    print("%-34s %s" % ("commit", document["commit"]))
    print("%-34s %s" % ("frozen at", document["frozen_at"]))
    for name, value in sorted(document["versions"].items()):
        print("%-34s %s" % (name, value))


def main(argv=None):
    """Run the default mode, or an operator step of the freeze."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-freeze", action="store_true",
                        help="write the freeze record of the code checked "
                             "out, once")
    parser.add_argument("--rater-model", default=None,
                        help="the rating model's name, as its server knows "
                             "it")
    parser.add_argument("--operator-check", action="store_true",
                        help="check that the code running is the frozen "
                             "version")
    parser.add_argument("--operator-register", metavar="QUESTION_SET",
                        default=None,
                        help="register a final question set by its files' "
                             "digests, once")
    args = parser.parse_args(argv)
    try:
        if args.operator_freeze:
            digest = freeze(args.rater_model)
            report_versions(read_record()[0])
            print("freeze record sha256 %s" % digest)
        elif args.operator_check:
            document, digest = require()
            report_versions(document)
            print("freeze record sha256 %s: the code running is frozen"
                  % digest)
        elif args.operator_register:
            registration = register(args.operator_register)
            for file, entry in sorted(registration["files"].items()):
                print("%-24s %d bytes, sha256 %s" % (
                    file, entry["size_bytes"], entry["sha256"]))
        else:
            problems = run_synthetic()
            for problem in problems:
                print("FAILED: %s" % problem)
            if problems:
                return 1
    except FreezeError as error:
        print("FAILED: %s" % error)
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
