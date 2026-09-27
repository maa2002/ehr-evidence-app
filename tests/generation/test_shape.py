"""Synthetic checks for ehr_evidence_app.generation.shape.

Contexts and template candidates come from the invented fixtures; no real
record is read.
"""

import contextlib
import io
import json
import unittest

from ehr_evidence_app.generation import shape
from ehr_evidence_app.qa import resolve, suite
from ehr_evidence_app.validation import structure

PAIRS = shape.synthetic_contexts()


def first(task, outcome=None):
    """Return the first invented (context, template) of a task and
    outcome."""
    return next((context, template) for context, template in PAIRS
                if context.task == task
                and outcome in (None, template.outcome))


class ShapeTests(unittest.TestCase):
    """A document stands for exactly one candidate of its request."""

    def test_round_trip(self):
        """Every invented template answer is written as a document that
        meets its schema and is read back unchanged."""
        for context, template in PAIRS:
            document = json.loads(shape.text(shape.document_of(template)))
            self.assertEqual(shape.violations(document,
                                              shape.schema(context)), [])
            self.assertEqual(shape.candidate(context, document), template)

    def test_every_answer_is_sampled(self):
        """Lists and presence checks, recorded and not, and an
        explanation."""
        found = {(context.task, template.outcome)
                 for context, template in PAIRS}
        self.assertLessEqual({("condition_list", "answer"),
                              ("condition_list", "not_recorded"),
                              ("condition_presence", "answer"),
                              ("condition_presence", "not_recorded"),
                              ("prediction_explanation", "explanation")},
                             found)

    def test_the_request_fixes_its_fields(self):
        """The request's identifiers, case, scope, bundle and asked
        condition are filled in; a document cannot name others."""
        context, template = first("condition_presence", "answer")
        made = shape.candidate(context, shape.document_of(template))
        request = context.request
        self.assertEqual((made.request_id, made.case, made.scope,
                          made.bundle.sha256),
                         (request.request_id, request.case, request.scope,
                          context.bundle_sha256))
        self.assertEqual(made.statements[0].slots.condition,
                         context.asked.name)
        rules = shape.schema(context)
        for key in ("case", "scope", "bundle", "request_id"):
            self.assertTrue(shape.violations(
                dict(shape.document_of(template), **{key: "x"}), rules))

    def test_statements_take_their_answers_order(self):
        """The drivers notice follows the prediction and the other notices
        end the explanation, whatever order the document lists them in."""
        context, template = first("prediction_explanation")
        document = shape.document_of(template)
        document["notices"] = list(reversed(document["notices"]))
        made = shape.candidate(context, document)
        self.assertEqual(made.statements[1].notice, "drivers")
        self.assertEqual(structure.order_problems(made), set())

    def test_visits_are_a_set(self):
        """Visit numbers are read in order, however they are written."""
        context, template = first("condition_list", "answer")
        document = shape.document_of(template)
        numbers = sorted({visit.visit_number
                          for visit in context.bundle.visits})
        document["items"][0][shape.LIST_ROW.index("visits")] = list(
            reversed(numbers))
        made = shape.candidate(context, document)
        self.assertEqual(made.statements[1].slots.visits, tuple(numbers))

    def test_lists_are_compact(self):
        """A list keeps every condition, as a row under one shared
        wording; statements that do not share one cannot be written
        compactly."""
        context, template = first("condition_list", "answer")
        document = shape.document_of(template)
        items = [item for item in template.statements
                 if item.kind == "list_item"]
        self.assertEqual(len(document["items"]), len(items))
        self.assertEqual(document["item_wording"], items[0].wording)
        if len(items) > 1:
            varied = list(template.statements)
            index = varied.index(items[1])
            varied[index] = items[1].model_copy(update={
                "wording": "{visits}: {condition}, {count}"})
            with self.assertRaisesRegex(shape.ShapeError, "wordings_differ"):
                shape.document_of(template.model_copy(update={
                    "statements": tuple(varied)}))

    def test_explanations_state_the_drivers_not_described(self):
        """With fewer drivers described than eligible, the document states
        how many more there are."""
        context, template = next(
            (context, template) for context, template in PAIRS
            if context.task == "prediction_explanation"
            and context.limit == 1)
        document = shape.document_of(template)
        self.assertEqual(len(document["drivers"]), 1)
        self.assertIn("drivers_omitted", document["notices"])
        self.assertEqual(document["omitted"], shape.omitted_drivers(context))
        self.assertEqual(shape.candidate(context, document), template)

    def test_schema_refuses_faults(self):
        """Extra fields, refused outcomes, missing codes, references
        outside the bundle, digits in wordings and a driver's score
        changed all break their schema."""
        for context, template in PAIRS:
            rules = shape.schema(context)
            for fault in shape.synthetic_faults(shape.document_of(template)):
                self.assertTrue(shape.violations(fault, rules))

    def test_references_are_the_bundles(self):
        """An item may cite and be named by the bundle's records only."""
        context, _ = first("condition_list", "answer")
        columns = shape.values_of(shape.LIST_ROW, shape.schema(context)[
            "properties"]["items"]["items"]["prefixItems"])
        condition, cited = columns["condition"], columns["refs"]
        refs = sorted(event.ref for event in context.bundle.events)
        self.assertEqual(sorted(cited["items"]["enum"]), refs)
        self.assertEqual(sorted(condition["enum"]), refs)

    def test_tuples(self):
        """A row has exactly its parts, each of its own kind."""
        rules = shape.row({"enum": ["E1"]}, shape.integer(1, 9))
        self.assertEqual(shape.violations(["E1", 2], rules), [])
        for value in (["E1"], ["E1", 2, 3], ["E2", 2], ["E1", "2"]):
            self.assertTrue(shape.violations(value, rules), value)

    def test_empty_scopes_admit_only_not_recorded(self):
        """A scope with no records cannot be listed."""
        context, _ = first("condition_list", "not_recorded")
        rules = shape.schema(context)
        self.assertEqual(rules["properties"]["outcome"],
                         {"const": "not_recorded"})

    def test_wordings(self):
        """A wording holds no digit and each of its kind's placeholders
        exactly once, on one line, beginning with its subject and the
        others in any order; an empty list's has no subject."""
        rules = shape.wording("list_item")
        for text, allowed in (("{condition}: {count} in {visits}", True),
                              ("{condition}: {visits}, {count}", True),
                              ("{count} in {visits} for {condition}", False),
                              ("{condition}: 3 in {visits}", False),
                              ("{condition}: {count}", False),
                              ("{condition}: {count} in {visits} {count}",
                               False),
                              ("{condition} within {scope}", False),
                              ("{condition}\n{count} in {visits}", False),
                              ("", False)):
            self.assertEqual(not shape.violations(text, rules), allowed,
                             text)
        self.assertFalse(shape.violations(
            "No conditions are recorded within {scope}.",
            shape.wording("list_empty")))

    def test_violations(self):
        """Booleans never stand for numbers, an alternative must match,
        and a keyword this module does not write is refused."""
        self.assertTrue(shape.violations(True, {"const": 1}))
        self.assertEqual(shape.violations(1.0, {"const": 1}), [])
        self.assertTrue(shape.violations("1", {"enum": [1]}))
        self.assertTrue(shape.violations(3, {"anyOf": [{"const": 1},
                                                       {"const": 2}]}))
        self.assertTrue(shape.violations([1, 1], {"type": "array",
                                                  "uniqueItems": True}))
        self.assertTrue(shape.violations({"a": 1}, shape.fields({})))
        self.assertTrue(shape.violations(0, shape.integer(1, 5)))
        with self.assertRaises(ValueError):
            shape.violations(1, {"format": "date"})

    def test_malformed_documents(self):
        """A document that stands for no candidate is refused."""
        context, _ = first("condition_list", "answer")
        with self.assertRaisesRegex(shape.ShapeError, "document_malformed"):
            shape.candidate(context, {"outcome": "answer"})

    def test_icd_code_questions_count_source_codes(self):
        """A question about an ICD code counts records by their source
        code, those with a standard concept too."""
        vocabulary = suite.synthetic_vocabulary()
        context, _ = first("condition_list", "answer")
        coded = [event for event in context.bundle.events
                 if event.source_concept is not None]
        self.assertTrue(coded)
        key = resolve.code_key(coded[0].source_concept.code)
        resolution = resolve.Resolution("", "resolved", "icd_code",
                                        codes=(key,))
        asked = shape.asked(resolution, context.bundle, vocabulary)
        self.assertTrue(asked.counts_as)
        self.assertEqual({entry.by for entry in asked.counts_as},
                         {"source_code"})
        self.assertEqual(asked.label, "ICD code " + key)

    def test_command(self):
        """The default mode passes on invented data."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(shape.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
