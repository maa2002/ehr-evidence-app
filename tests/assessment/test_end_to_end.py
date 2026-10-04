"""Synthetic checks for ehr_evidence_app.assessment.end_to_end.

The written test's invented questions about the invented case, and
invented questions about its whole record, are assessed stage by stage;
no model runs and no real record is read.
"""

import collections
import copy
import dataclasses
import unittest

from ehr_evidence_app.assessment import end_to_end
from ehr_evidence_app.qa import written

ROWS, INPUTS, VOCAB, LIMITS = end_to_end.synthetic_inputs()
RECORD_ROWS, RECORD_INPUTS, RECORD_LIMITS = (
    end_to_end.synthetic_record_inputs())
LABEL = end_to_end.SYNTHETIC_LABEL


def row(question, intent, concept=None, scope="all"):
    """Return an invented question about the invented case."""
    found, _ = written.parse_questions(written.csv_bytes((
        written.COLUMNS,
        ("x1", "synthetic-1-v1", "1", scope, question, intent,
         "" if concept is None else str(concept)))), ("synthetic-1-v1",))
    return found[0]


def assessed(item, complete=True):
    """Return the stages of one invented question."""
    oracle, cache, _ = INPUTS["synthetic-1-v1"]
    return end_to_end.assess_row(item, oracle, cache, VOCAB, LIMITS, "x1",
                                 complete=complete)


class StageTests(unittest.TestCase):
    """Each stage is scored only when the one before it succeeded."""

    def test_default_mode(self):
        """The written test's invented questions end at the stages it
        expects."""
        _, problems = end_to_end.run_synthetic()
        self.assertEqual(problems, [])

    def test_unrelated_condition_stops_at_resolution(self):
        """A presence check read about another condition than the one
        meant stops at resolution, with nothing retrieved or delivered."""
        found = assessed(row("Is synthetic concept 1002 recorded?",
                             "presence", 1001))
        self.assertEqual((found.interpretation, found.resolution,
                          found.retrieval, found.delivery),
                         ("right", "unrelated", None, None))

    def test_sources_not_complete(self):
        """When the sources are not complete the question cannot be
        delivered as the reference has it, whatever the runtime does."""
        found = assessed(row("List the recorded conditions.", "list"),
                         complete=False)
        self.assertEqual((found.interpretation, found.retrieval,
                          found.delivery), ("right", "unanswerable", None))

    def test_refusal_answered(self):
        """A request to refuse that gets an answer is read wrongly, and
        no later stage is scored."""
        found = assessed(row("List the recorded conditions.", "refuse"))
        self.assertEqual((found.interpretation, found.resolution,
                          found.delivery), ("answered", None, None))

    def test_case_not_admitted_left_out(self):
        """A question about a case that is not admitted is left out."""
        oracle, cache, _ = INPUTS["synthetic-1-v1"]
        item = dataclasses.replace(row("List the recorded conditions.",
                                       "list"), case_number=9)
        self.assertIsNone(end_to_end.assess_row(item, oracle, cache, VOCAB,
                                                LIMITS, "x1"))

    def test_first_of_each_intent(self):
        """A sample keeps up to the number asked of each intent, in the
        set's order."""
        rows = [row("List the recorded conditions.", "list"),
                row("Is synthetic concept 1001 recorded?", "presence", 1001),
                row("List the conditions.", "list"),
                row("Is synthetic concept 1002 recorded?", "presence",
                    1002)]
        self.assertEqual(end_to_end.first_of_each(rows, 1),
                         [rows[0], rows[1]])
        self.assertEqual(end_to_end.first_of_each(rows, 5), rows)

    def test_tally_holds_counts(self):
        """The tally counts verdicts by intent and stage, and writers by
        delivery."""
        found, left_out, writers = end_to_end.tally([
            end_to_end.Assessed("list", "right", None, "complete",
                                "correct", "model"),
            end_to_end.Assessed("list", "declined"), None])
        self.assertEqual(found["list"]["interpretation"],
                         {"declined": 1, "right": 1})
        self.assertEqual((left_out, writers), (1, {("model", "correct"): 1}))


def record_file(*lines, label=LABEL):
    """Return the rows and problems of invented questions about the
    record: each line (question, intent, task, kind, codes, groups,
    concepts, written, words, selector, field), about case 1 in all its
    visits."""
    return end_to_end.parse_record_questions(written.csv_bytes(
        (end_to_end.RECORD_COLUMNS,) + tuple(
            ("y%d" % number, label, "1", "all") + tuple(line)
            for number, line in enumerate(lines, 1))), (LABEL,))


def record_row(question, task, kind, codes="", groups="", concepts="",
               selector="", field=""):
    """Return one invented question to be answered about the record."""
    rows, problems = record_file((question, "answer", task, kind, codes,
                                  groups, concepts, "", "", selector,
                                  field))
    assert not problems, problems
    return rows[0]


def record_assessed(item, inputs=None):
    """Return the stages of one invented question about the record."""
    return end_to_end.assess_record_row(
        item, inputs or RECORD_INPUTS[LABEL], RECORD_LIMITS, "y1")


class RecordRowTests(unittest.TestCase):
    """The format of the questions about the whole record."""

    def test_invented_rows_read(self):
        """The default mode's rows are all read; the case not admitted is
        kept for the assessment to leave out."""
        rows, problems = end_to_end.parse_record_questions(
            written.csv_bytes(end_to_end.SYNTHETIC_RECORD_QUESTIONS),
            (LABEL,))
        self.assertEqual(problems, collections.Counter())
        self.assertEqual(len(rows), len(
            end_to_end.SYNTHETIC_RECORD_QUESTIONS) - 1)
        self.assertEqual(rows[0].asked.codes, frozenset(("50971",)))
        self.assertEqual(rows[0].stratum, "record_values lab")
        self.assertEqual(rows[12].stratum, "refuse")

    def test_a_meaning_the_references_cannot_judge_is_refused(self):
        """Each row's meaning is checked against what the references
        prepare; a faulty row is counted by its problem and left out."""
        blank = ("", "", "", "", "", "", "")
        for line, problem in (
                (("record_presence", "condition", "", "", "1001", "", "",
                  "", ""), "condition_question"),
                (("record_list", "condition") + blank, "condition_question"),
                (("record_abnormal", "vital") + blank, "kind_invalid"),
                (("record_count", "drug") + blank, "kind_invalid"),
                (("record_trend", "lab") + blank, "task_invalid"),
                (("record_values", "lab", "50971", "", "", "", "", "", ""),
                 "selector_invalid"),
                (("record_timing", "lab", "50971", "", "", "", "", "first",
                  ""), "selector_invalid"),
                (("record_values", "lab", "50971", "", "", "", "", "most",
                  ""), "selector_invalid"),
                (("visit_details", "visit") + blank, "field_invalid"),
                (("person_details", "person", "", "", "", "", "", "",
                  "admitted"), "field_invalid"),
                (("record_timing", "lab", "", "heart_rate", "", "", "", "",
                  ""), "item_invalid"),
                (("record_timing", "lab", "potassium", "", "", "", "", "",
                  ""), "item_invalid"),
                (("record_timing", "vital", "", "pulse", "", "", "", "",
                  ""), "item_invalid"),
                (("record_list", "lab", "50971", "", "", "", "", "", ""),
                 "item_invalid"),
                (("record_presence", "lab") + blank, "item_missing"),
                (("visit_details", "visit", "", "", "", "", "unit", "",
                  "admitted"), "item_invalid")):
            with self.subTest(problem=problem, line=line):
                rows, problems = record_file(("A question?", "answer")
                                             + line)
                self.assertEqual((rows, problems),
                                 ([], collections.Counter({problem: 1})))
        rows, problems = record_file(("Should she get heparin?", "refuse",
                                      "record_presence", "medication", "",
                                      "", "", "", "", "", ""))
        self.assertEqual(problems, collections.Counter(
            {"meaning_not_asked": 1}))

    def test_identifiers_are_read_as_the_references_name_items(self):
        """Several identifiers are separated by semicolons; names are
        normalised into words."""
        rows, problems = record_file((
            "Was heparin or Lasix given?", "answer", "record_presence",
            "medication", "", "", "1; 2", "Heparin Sodium;LASIX", "", "",
            ""))
        self.assertFalse(problems)
        asked = rows[0].asked
        self.assertEqual((asked.concepts, asked.written),
                         (frozenset((1, 2)), (("heparin", "sodium"),
                                              ("lasix",))))
        self.assertTrue(asked.named)

    def test_an_evaluation_case_set_stops_the_file(self):
        """A row about a case set outside the allowed ones stops the
        file."""
        with self.assertRaisesRegex(written.WrittenError,
                                    "case_set_not_development"):
            record_file(("What labs were done?", "answer", "record_list",
                         "lab", "", "", "", "", "", "", ""),
                        label="eval-100-v1")


class RecordStageTests(unittest.TestCase):
    """Questions about the record, stage by stage."""

    def test_default_mode(self):
        """The invented questions about the record end at the stages the
        default mode expects."""
        found, left_out, _ = end_to_end.tally(end_to_end.assess_records(
            RECORD_ROWS, RECORD_INPUTS, RECORD_LIMITS))
        expected, expected_left_out = end_to_end.SYNTHETIC_RECORD_EXPECTED
        self.assertEqual(found, {stratum: expected[stratum]
                                 for stratum in sorted(expected)})
        self.assertEqual(left_out, expected_left_out)

    def test_a_correct_answer_has_every_stage(self):
        """A question read, resolved, retrieved and answered as meant is
        scored at every stage, and its writer named."""
        found = record_assessed(record_row(
            "What was the last synthetic potassium?", "record_values", "lab",
            codes="50971", selector="latest"))
        self.assertEqual(found, end_to_end.Assessed(
            "record_values lab", "right", "exact", "complete", "correct",
            "template"))

    def test_a_related_item_is_scored_against_the_item_meant(self):
        """A parent condition read for its child is related, and the
        answer, which counts the parent's other child too, is incorrect
        for the child meant."""
        found = record_assessed(record_row(
            "How many times was synthetic parent disorder recorded?",
            "record_count", "condition", concepts="1001"))
        self.assertEqual((found.resolution, found.retrieval, found.delivery),
                         ("related", "complete", "incorrect"))
        exact = record_assessed(record_row(
            "How many times was synthetic parent disorder recorded?",
            "record_count", "condition", concepts="1000"))
        self.assertEqual((exact.resolution, exact.delivery),
                         ("exact", "correct"))

    def test_the_same_records_are_an_exact_resolution(self):
        """Items named otherwise that match the same records of the case
        resolve exactly: the creatinine meant beside the potassium has no
        record in the case's visits."""
        found = record_assessed(record_row(
            "How many synthetic potassium results were there?",
            "record_count", "lab", codes="50971;50912"))
        self.assertEqual((found.resolution, found.delivery),
                         ("exact", "correct"))

    def test_a_record_the_bundle_lacks_fails_retrieval(self):
        """When the record bundle lacks a record of the reference,
        retrieval fails and nothing is delivered, whatever the answer
        says."""
        inputs = RECORD_INPUTS[LABEL]
        records = copy.copy(inputs.records.records)
        records.held = {
            key: bundle.model_copy(update={"records": tuple(
                entry for entry in bundle.records
                if not (entry.kind == "lab" and entry.native_id == 12))})
            for key, bundle in records.held.items()}
        lacking = inputs._replace(records=dataclasses.replace(
            inputs.records, records=records))
        found = record_assessed(record_row(
            "What was the last synthetic potassium?", "record_values", "lab",
            codes="50971", selector="latest"), lacking)
        self.assertEqual((found.interpretation, found.resolution,
                          found.retrieval, found.delivery),
                         ("right", "exact", "incomplete", None))

    def test_a_whole_kind_read_for_an_item_is_another_item(self):
        """A question meant about one item and read about the whole kind
        is read wrongly."""
        found = record_assessed(record_row(
            "What labs were done?", "record_list", "lab"))
        self.assertEqual(found.delivery, "correct")
        found = record_assessed(record_row(
            "How many labs were done?", "record_count", "lab",
            codes="50971"))
        self.assertEqual((found.interpretation, found.resolution),
                         ("other_item", None))

    def test_another_field_or_kind(self):
        """A field or a kind other than the one meant stops at
        interpretation."""
        found = record_assessed(record_row(
            "When was the patient discharged?", "visit_details", "visit",
            field="admitted"))
        self.assertEqual(found.interpretation, "other_field")
        found = record_assessed(record_row(
            "How many ICU stays were there?", "record_count", "transfer"))
        self.assertEqual(found.interpretation, "other_kind")

    def test_a_scope_the_case_lacks_is_left_out(self):
        """A question about a visit the case lacks is left out."""
        item = dataclasses.replace(record_row(
            "What labs were done?", "record_list", "lab"),
            scope=end_to_end.contracts.ScopeRef(kind="selected_visit",
                                                visit_number=7))
        self.assertIsNone(record_assessed(item))


if __name__ == "__main__":
    unittest.main()
