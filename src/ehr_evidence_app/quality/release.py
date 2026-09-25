#!/usr/bin/env python3
"""Release metadata checks: versions, changelog entry and release notes.

Standard library only; reads repository files only. Checks that the project
version in pyproject.toml, the package __version__ and the newest released
CHANGELOG.md entry agree, that the entry has an ISO date and a link
reference, and, when a release tag is given, that the tag names that
version. With --notes-out it writes the entry's notes (the text under its
heading) for the release page.

An "Unreleased" section at the top of the changelog is allowed and is not a
release entry.

Exit codes: 0 pass, 1 problems found, 2 operational failure.

Usage:
    python -m ehr_evidence_app.quality.release
    python -m ehr_evidence_app.quality.release --tag v0.3.0 --notes-out NOTES
"""

from __future__ import annotations

import argparse
import ast
import datetime
import os
import re
import sys
import tomllib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
PYPROJECT = os.path.join(REPO_ROOT, "pyproject.toml")
PACKAGE_INIT = os.path.join(REPO_ROOT, "src", "ehr_evidence_app",
                            "__init__.py")
CHANGELOG = os.path.join(REPO_ROOT, "CHANGELOG.md")

VERSION = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
TAG = re.compile(r"^v((0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*))$")
HEADING = re.compile(r"^## \[(?P<name>[^\]]+)\](?: - (?P<date>\S+))?\s*$")
LINK = re.compile(r"^\[(?P<name>[^\]]+)\]: \S+\s*$")
UNRELEASED = "Unreleased"


class ReleaseError(Exception):
    """An operational failure: a file is missing or cannot be parsed."""


def read_text(path):
    """Return a repository text file's contents."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        raise ReleaseError("cannot read %s" % os.path.relpath(
            path, REPO_ROOT)) from None


def project_version(text):
    """Return [project] version from pyproject.toml text."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise ReleaseError("pyproject.toml is not valid TOML") from None
    version = (data.get("project") or {}).get("version")
    if not isinstance(version, str):
        raise ReleaseError("pyproject.toml has no [project] version")
    return version


def package_version(text):
    """Return the literal __version__ assigned in the package __init__."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        raise ReleaseError("the package __init__ does not parse") from None
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "__version__"
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            return node.value.value
    raise ReleaseError("the package __init__ assigns no literal __version__")


def changelog_entries(text):
    """Return (entries, link names) parsed from changelog text.

    Each entry is a dict with name, date and notes (the lines under the
    heading up to the next level-two heading or the link references).
    """
    entries, links, current = [], set(), None
    for line in text.splitlines():
        heading = HEADING.match(line)
        link = LINK.match(line)
        if heading:
            current = {"name": heading.group("name"),
                       "date": heading.group("date"), "notes": []}
            entries.append(current)
        elif line.startswith("## "):
            current = None
        elif link:
            links.add(link.group("name"))
            current = None
        elif current is not None:
            current["notes"].append(line)
    for entry in entries:
        entry["notes"] = "\n".join(entry["notes"]).strip() + "\n"
    return entries, links


def iso_date(value):
    """Return True for a real calendar date written as YYYY-MM-DD."""
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}",
                                                      value):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def check(pyproject_text, init_text, changelog_text, tag=None):
    """Return (problems, newest release entry or None)."""
    problems = []
    project = project_version(pyproject_text)
    package = package_version(init_text)
    for label, value in (("pyproject.toml version", project),
                         ("package __version__", package)):
        if not VERSION.match(value):
            problems.append("%s is not MAJOR.MINOR.PATCH" % label)
    if project != package:
        problems.append("pyproject.toml version and package __version__ "
                        "differ")
    entries, links = changelog_entries(changelog_text)
    releases = [entry for entry in entries if entry["name"] != UNRELEASED]
    if any(entry["name"] == UNRELEASED for entry in entries[1:]):
        problems.append("the Unreleased section must be the first entry")
    entry = releases[0] if releases else None
    if entry is None:
        problems.append("CHANGELOG.md has no release entry")
    else:
        if entry["name"] != project:
            problems.append("the newest CHANGELOG.md entry is not the "
                            "project version")
        if not iso_date(entry["date"]):
            problems.append("the newest CHANGELOG.md entry has no ISO date")
        if entry["name"] not in links:
            problems.append("the newest CHANGELOG.md entry has no link "
                            "reference")
        if entry["notes"].strip() == "":
            problems.append("the newest CHANGELOG.md entry is empty")
    names = [item["name"] for item in releases]
    if len(names) != len(set(names)):
        problems.append("CHANGELOG.md repeats a release entry")
    if tag is not None:
        match = TAG.match(tag)
        if not match:
            problems.append("the tag is not vMAJOR.MINOR.PATCH")
        elif match.group(1) != project:
            problems.append("the tag does not name the project version")
    return problems, entry


def write_notes(path, entry):
    """Write the release notes to a path outside the repository."""
    target = os.path.abspath(path)
    root = os.path.normcase(REPO_ROOT) + os.sep
    if os.path.normcase(target).startswith(root):
        raise ReleaseError("release notes must be written outside the "
                           "repository")
    with open(target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(entry["notes"])


def main(argv=None):
    """Check release metadata; optionally write the release notes."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", help="release tag to check, e.g. v0.3.0")
    parser.add_argument("--notes-out",
                        help="write the release notes to this file "
                             "(outside the repository)")
    args = parser.parse_args(argv)
    try:
        problems, entry = check(read_text(PYPROJECT), read_text(PACKAGE_INIT),
                                read_text(CHANGELOG), tag=args.tag)
        if problems:
            for problem in problems:
                print("PROBLEM: %s" % problem)
            print("RESULT: FAIL")
            return 1
        if args.notes_out:
            write_notes(args.notes_out, entry)
    except ReleaseError as error:
        print("FAILED: %s" % error)
        return 2
    print("Version        : %s (%s)" % (entry["name"], entry["date"]))
    if args.tag:
        print("Tag            : %s" % args.tag)
    if args.notes_out:
        print("Release notes  : written")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
