"""Synthetic checks for ehr_evidence_app.qa.written.

The questions, case, bundles and vocabulary are invented; the real modes
are only checked for refusing an evaluation case set.
"""

import contextlib
import io
import unittest

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.qa import suite, written

ALLOWED = ("synthetic-1-v1",)


def parse(*lines, header=written.COLUMNS):
    """Parse invented question rows."""
    return written.parse_questions(written.csv_bytes((header,) + lines),
                                   ALLOWED)


class ParsingTests(unittest.TestCase):
    """The questions file is read strictly; faulty rows are counted."""

    def test_scopes(self):
        """History, all visits and one visit are written plainly."""
        self.assertEqual(written.scope_ref(" History "),
                         contracts.ScopeRef(kind="pre_target_history"))
        self.assertEqual(written.scope_ref("visit 3"),
                         contracts.ScopeRef(kind="selected_visit",
                                            visit_number=3))
        for text in ("visit 0", "visit", "everything", ""):
            self.assertIsNone(written.scope_ref(text), text)
        self.assertEqual(written.scope_text(written.scope_ref("all")), "all")

    def test_rows(self):
        """Valid rows are kept; each fault is counted and left out."""
        rows, problems = parse(
            ("q1", "synthetic-1-v1", "1", "all", "Any sepsis?", "presence",
             "1001"),
            ("q1", "synthetic-1-v1", "1", "all", "Again?", "list", ""),
            ("q2", "synthetic-1-v1", "x", "all", "Any?", "list", ""),
            ("q3", "synthetic-1-v1", "1", "later", "Any?", "list", ""),
            ("q4", "synthetic-1-v1", "1", "all", "", "list", ""),
            ("q5", "synthetic-1-v1", "1", "all", "Any?", "guess", ""),
            ("q6", "synthetic-1-v1", "1", "all", "Any?", "presence", ""),
            ("", "", "", "", "", "", ""))
        self.assertEqual([row.id for row in rows], ["q1"])
        self.assertEqual(rows[0].concept, 1001)
        self.assertEqual(problems, {
            "id_missing_or_repeated": 1, "case_invalid": 1,
            "scope_invalid": 1, "question_missing": 1, "intent_invalid": 1,
            "concept_invalid": 1})

    def test_optional_family_column(self):
        """A last family column labels rows for grouped results."""
        rows, problems = parse(
            ("q1", "synthetic-1-v1", "1", "all", "Any?", "clarify", "",
             "vague"),
            header=written.COLUMNS + (written.FAMILY,))
        self.assertEqual((rows[0].intent, rows[0].family, problems),
                         ("clarify", "vague", {}))

    def test_file_level_faults(self):
        """Wrong columns and other case sets stop the whole file."""
        with self.assertRaisesRegex(written.WrittenError,
                                    "questions_columns_unexpected"):
            parse(header=("id", "question"))
        with self.assertRaisesRegex(written.WrittenError,
                                    "case_set_not_development"):
            parse(("q1", "eval-100-v1", "1", "all", "Any?", "list", ""))
        with self.assertRaisesRegex(written.WrittenError,
                                    "questions_unreadable"):
            written.parse_questions(b"\xff\xfe", ALLOWED)


class ScoringTests(unittest.TestCase):
    """Written questions are scored against the oracle."""

    def test_related(self):
        """The concept, an ancestor or a descendant is related."""
        vocab = suite.synthetic_vocabulary()
        self.assertTrue(written.related(vocab, (1000,), 1001))
        self.assertTrue(written.related(vocab, (1001,), 1000))
        self.assertFalse(written.related(vocab, (1002,), 1001))

    def test_synthetic_run(self):
        """Lists, presence checks, refusals and clarifications are scored
        as expected."""
        document = written.run_synthetic()
        for key, value in written.SYNTHETIC_EXPECTED.items():
            self.assertEqual(document[key], value, key)

    def test_question_sets(self):
        """Question sets are named; an unknown name is refused."""
        for name in ("written-v2", "written-v3", "written-v4"):
            self.assertTrue(written.set_dir(name).endswith(
                "\\question-sets\\" + name))
        with self.assertRaisesRegex(written.WrittenError,
                                    "question_set_unknown"):
            written.set_dir("written-v9")

    def test_evaluation_case_sets_are_refused(self):
        """Only development case sets are questioned."""
        with self.assertRaisesRegex(written.WrittenError,
                                    "case_set_held_out"):
            written.development_case_set("eval-100-v1")
        self.assertIn("dev-30-v1", written.development_labels())
        self.assertNotIn("eval-100-v1", written.development_labels())

    def test_command(self):
        """The default mode passes; real options need --operator-real."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(written.main([]), 0)
            self.assertEqual(written.main(["--score"]), 2)
            self.assertEqual(written.main(["--operator-real", "--score",
                                           "--template"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
