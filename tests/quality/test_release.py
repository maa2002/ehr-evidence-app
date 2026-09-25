"""Synthetic checks for ehr_evidence_app.quality.release.

Changelog, pyproject and package texts are invented inline; the one
real-repository case only reads the committed metadata files.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

from ehr_evidence_app.quality import release

PYPROJECT = '[project]\nname = "synthetic"\nversion = "1.2.3"\n'
INIT = '"""Synthetic package."""\n\n__version__ = "1.2.3"\n'
CHANGELOG = """# Changelog

## [Unreleased]

### Added

- Synthetic work in progress.

## [1.2.3] - 2026-09-26

First synthetic release.

### Added

- Synthetic feature.

## [1.2.2] - 2026-09-01

- Older synthetic release.

[1.2.3]: https://example.invalid/compare/v1.2.2...v1.2.3
[1.2.2]: https://example.invalid/tree/v1.2.2
"""


class CheckTests(unittest.TestCase):
    """Version agreement, changelog shape and tag rules."""

    def problems(self, pyproject=PYPROJECT, init=INIT, changelog=CHANGELOG,
                 tag=None):
        """Return only the problems for invented texts."""
        return release.check(pyproject, init, changelog, tag=tag)[0]

    def test_consistent_metadata_passes(self):
        """Agreeing versions, a dated entry and a link reference pass."""
        problems, entry = release.check(PYPROJECT, INIT, CHANGELOG,
                                        tag="v1.2.3")
        self.assertEqual(problems, [])
        self.assertEqual((entry["name"], entry["date"]),
                         ("1.2.3", "2026-09-26"))
        self.assertTrue(entry["notes"].startswith("First synthetic release."))
        self.assertIn("- Synthetic feature.", entry["notes"])
        self.assertNotIn("Older synthetic release", entry["notes"])
        self.assertNotIn("example.invalid", entry["notes"])

    def test_disagreements_are_reported(self):
        """Every mismatch is a problem with a specific message."""
        cases = (
            (dict(init=INIT.replace("1.2.3", "1.2.4")), "differ"),
            (dict(pyproject=PYPROJECT.replace("1.2.3", "1.2")),
             "MAJOR.MINOR.PATCH"),
            (dict(changelog=CHANGELOG.replace("## [1.2.3] - 2026-09-26",
                                              "## [1.2.4] - 2026-09-26")),
             "not the project version"),
            (dict(changelog=CHANGELOG.replace("2026-09-26", "2026-02-30")),
             "ISO date"),
            (dict(changelog=CHANGELOG.replace("[1.2.3]: ", "[9.9.9]: ")),
             "link reference"),
            (dict(changelog=CHANGELOG.replace(
                "## [1.2.2] - 2026-09-01", "## [1.2.3] - 2026-09-01")),
             "repeats"),
            (dict(changelog="# Changelog\n\n## [Unreleased]\n"),
             "no release entry"),
            (dict(tag="v1.2.4"), "does not name"),
            (dict(tag="1.2.3"), "vMAJOR.MINOR.PATCH"),
            (dict(tag="v1.2.3; rm -rf"), "vMAJOR.MINOR.PATCH"),
        )
        for overrides, message in cases:
            with self.subTest(message=message):
                problems = self.problems(**overrides)
                self.assertTrue(any(message in item for item in problems),
                                problems)

    def test_unreleased_must_come_first(self):
        """An Unreleased section below a release is refused."""
        moved = CHANGELOG.replace("## [Unreleased]\n", "").replace(
            "## [1.2.2]", "## [Unreleased]\n\n## [1.2.2]")
        self.assertIn("the Unreleased section must be the first entry",
                      self.problems(changelog=moved))

    def test_operational_failures(self):
        """Unparseable metadata is an operational error, not a pass."""
        for pyproject, init in (("[project\n", INIT),
                                ("[tool]\n", INIT),
                                (PYPROJECT, "__version__ = get()\n"),
                                (PYPROJECT, "def (:\n")):
            with self.subTest(pyproject=pyproject, init=init):
                with self.assertRaises(release.ReleaseError):
                    release.check(pyproject, init, CHANGELOG)

    def test_notes_are_written_outside_the_repository_only(self):
        """Notes go to an outside path; a repository path is refused."""
        entry = release.check(PYPROJECT, INIT, CHANGELOG)[1]
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "notes.md")
            release.write_notes(target, entry)
            with open(target, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), entry["notes"])
        with self.assertRaises(release.ReleaseError):
            release.write_notes(os.path.join(release.REPO_ROOT, "notes.md"),
                                entry)


class RepositoryTests(unittest.TestCase):
    """The committed metadata is consistent."""

    def test_repository_metadata_is_consistent(self):
        """pyproject, the package and the changelog agree."""
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(release.main([]), 0, stream.getvalue())
        self.assertIn("RESULT: PASS", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
