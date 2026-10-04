"""Synthetic checks for ehr_evidence_app.assessment.freeze.

A stand-in git answers for the repository, and the records are written to
the configured test scratch directory; no real record is read.
"""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from ehr_evidence_app.assessment import freeze
from tests.assessment.test_report import configured_test_temp


class Git:
    """A stand-in for git: one status, one HEAD and one diff answer."""

    def __init__(self, status="", head="abc1234", diff=0):
        """Hold the answers."""
        self.status, self.head, self.diff = status, head, diff

    def __call__(self, command, **_):
        """Answer one git command."""
        action = command[3]
        output = {"status": self.status, "rev-parse": self.head}.get(action,
                                                                     "")
        code = self.diff if action == "diff" else 0
        return subprocess.CompletedProcess(command, code, output, "")


class CodeTests(unittest.TestCase):
    """The code a freeze names, and the code running."""

    def test_default_mode(self):
        """The module's own check finds nothing."""
        self.assertEqual(freeze.run_synthetic(), [])

    def test_changed_or_added_code_is_refused(self):
        """A changed or untracked file in the code or configuration is not
        the frozen code."""
        for status in (" M src/a.py", "?? config/b.json"):
            with self.assertRaisesRegex(freeze.FreezeError, "code_modified"):
                freeze.code_state("repo", run=Git(status=status))

    def test_later_commits_of_other_files(self):
        """A later commit that changes no code is the frozen code; one that
        changes code is not."""
        self.assertTrue(freeze.same_code("repo", "abc1234",
                                         run=Git(head="def5678")))
        self.assertFalse(freeze.same_code("repo", "abc1234",
                                          run=Git(diff=1)))
        with self.assertRaisesRegex(freeze.FreezeError, "git_unavailable"):
            freeze.same_code("repo", "abc1234", run=Git(diff=128))

    def test_versions_name_what_results_rest_on(self):
        """The version holds the rules, the prompt, the baseline, the
        rubric and the final case set's pins."""
        found = freeze.versions()
        for name in ("prompt_version", "qa_answer", "record_questions",
                     "wording_rules", "baseline", "prose_rubric",
                     "rater_instructions_sha256", "final_case_set",
                     "final_case_set_pins_sha256", "generation_sha256"):
            self.assertIn(name, found)
        self.assertEqual(found["final_case_set"], freeze.FINAL)


SET = freeze.FINAL_QUESTIONS[0]


class RecordTests(unittest.TestCase):
    """The freeze record, the gate and the final question sets."""

    def setUp(self):
        """Make a protected root beneath the configured test scratch."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "setup-destinations first")
        self.protected = tempfile.mkdtemp(prefix="freeze-", dir=root)
        self.directory = os.path.join(self.protected, "freeze")

    def tearDown(self):
        """Remove the test's records."""
        shutil.rmtree(self.protected, ignore_errors=True)

    def frozen(self, head="abc1234"):
        """Write a freeze record of a stand-in repository."""
        return freeze.freeze("rating model", directory=self.directory,
                             protected=self.protected, repo="repo",
                             run=Git(head=head))

    def test_written_once_and_required(self):
        """The record is written once, names the rater, and is required
        by the code it names; other code or versions are refused."""
        with self.assertRaisesRegex(freeze.FreezeError, "not_frozen"):
            freeze.require(self.directory, repo="repo", run=Git())
        digest = self.frozen()
        with self.assertRaisesRegex(freeze.FreezeError, "frozen_before"):
            self.frozen()
        document, found = freeze.require(self.directory, repo="repo",
                                         run=Git(head="def5678"))
        self.assertEqual(found, digest)
        self.assertEqual(document["rater"]["model"], "rating model")
        with self.assertRaisesRegex(freeze.FreezeError, "code_not_frozen"):
            freeze.require(self.directory, repo="repo", run=Git(diff=1))
        changed = dict(freeze.versions(), prompt_version="changed")
        with mock.patch.object(freeze, "versions", lambda: changed):
            with self.assertRaisesRegex(freeze.FreezeError,
                                        "versions_changed"):
                freeze.require(self.directory, repo="repo", run=Git())
        with self.assertRaisesRegex(freeze.FreezeError,
                                    "rater_model_missing"):
            freeze.freeze("", directory=self.directory,
                          protected=self.protected, repo="repo", run=Git())

    def test_directories_read_when_called(self):
        """The gate reads the freeze record of the directory set when it
        is called, and no test reads the workspace's."""
        self.assertNotEqual(freeze.FREEZE_DIR, freeze.PROTECTED_ROOT
                            + "\\assessment\\freeze")
        self.assertFalse(os.path.exists(freeze.FREEZE_DIR))
        self.assertFalse(os.path.exists(freeze.QUESTION_SETS))
        digest = self.frozen()
        with mock.patch.object(freeze, "FREEZE_DIR", self.directory):
            _, found = freeze.require(repo="repo", run=Git())
        self.assertEqual(found, digest)
        with self.assertRaisesRegex(freeze.FreezeError, "not_frozen"):
            freeze.require(repo="repo", run=Git())

    def test_case_sets(self):
        """A development case set needs no freeze; the final one needs it;
        an unknown one is refused."""
        self.assertIsNone(freeze.allowed("dev-30-v1", self.directory))
        with self.assertRaisesRegex(freeze.FreezeError, "not_frozen"):
            freeze.allowed(freeze.FINAL, self.directory)
        with self.assertRaisesRegex(freeze.FreezeError, "case_set_unknown"):
            freeze.allowed("nothing-1-v1", self.directory)

    def test_question_sets_registered_once(self):
        """A final question set is registered by its files' digests under
        the freeze, once, and its files are read only as registered."""
        self.frozen()
        root = os.path.join(self.protected, "question-sets")
        folder, files = freeze.question_files(SET, root)
        os.makedirs(folder)
        with self.assertRaisesRegex(freeze.FreezeError,
                                    "question_file_missing"):
            freeze.register(SET, root, self.directory)
        for name, path in files.items():
            with open(path, "wb") as handle:
                handle.write(("%s rows\n" % name).encode("utf-8"))
        registration = freeze.register(SET, root, self.directory)
        self.assertEqual(set(registration["files"]),
                         set(freeze.QUESTION_FILES))
        with self.assertRaisesRegex(freeze.FreezeError, "registered_before"):
            freeze.register(SET, root, self.directory)
        data = freeze.read_question_file(SET, "questions.csv", root,
                                         self.directory)
        self.assertEqual(data, b"questions.csv rows\n")
        with open(files["questions.csv"], "wb") as handle:
            handle.write(b"changed\n")
        with self.assertRaisesRegex(freeze.FreezeError,
                                    "question_file_changed"):
            freeze.read_question_file(SET, "questions.csv", root,
                                      self.directory)
        with self.assertRaisesRegex(freeze.FreezeError,
                                    "question_set_unknown"):
            freeze.question_files("written-v4", root)

    def test_registration_of_another_freeze_refused(self):
        """A registration made under another freeze record is refused."""
        self.frozen()
        root = os.path.join(self.protected, "question-sets")
        folder, _ = freeze.question_files(SET, root)
        os.makedirs(folder)
        with open(os.path.join(folder, freeze.REGISTRATION_NAME), "w",
                  encoding="utf-8") as handle:
            json.dump({"freeze_sha256": "0" * 64, "files": {}}, handle)
        with self.assertRaisesRegex(freeze.FreezeError,
                                    "registration_of_another_freeze"):
            freeze.registered(SET, root, self.directory)


if __name__ == "__main__":
    unittest.main()
