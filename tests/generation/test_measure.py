"""Synthetic checks for ehr_evidence_app.generation.measure.

The sampling and summaries are checked on the invented fixtures; no model
runs and no real record is read.
"""

import contextlib
import io
import json
import unittest

from ehr_evidence_app.generation import adapter, measure, projection, shape
from ehr_evidence_app.qa import suite

PAIRS = shape.synthetic_contexts()
VOCAB = suite.synthetic_vocabulary()
END = "\n\nReply with the JSON document only."


def release_of(context):
    """Return the gate call of an invented request."""
    return adapter.gate_release(
        context, VOCAB if context.task != "prediction_explanation" else None)


class MeasureTests(unittest.TestCase):
    """A deterministic sample of each stratum, and plain summaries."""

    def test_sample_is_deterministic_and_stratified(self):
        """The same requests are drawn every time, at most per_group of
        each task and outcome."""
        once = measure.sample(PAIRS, 1)
        self.assertEqual(measure.sample(PAIRS, 1), once)
        groups = [measure.group_of(context, template)
                  for context, template in once]
        self.assertEqual(len(groups), len(set(groups)))
        self.assertEqual(set(groups), {measure.group_of(context, template)
                                       for context, template in PAIRS})
        self.assertEqual(len(measure.sample(PAIRS, 100)), len(PAIRS))

    def test_summary(self):
        """n, min, median, 90th percentile and max."""
        self.assertEqual(measure.summary([3, 1, 2]),
                         "n=3 min=1 median=2 p90=3 max=3")
        self.assertEqual(measure.summary([]), "n=0")

    def test_other_numbers_of_drivers(self):
        """An explanation request can be measured with another number of
        drivers, its template following it."""
        context, template = next(pair for pair in PAIRS
                                 if pair[0].task == "prediction_explanation")
        other, drafted = measure.with_limit(context, template, 1)
        self.assertEqual(other.limit, 1)
        self.assertEqual(len([item for item in drafted.statements
                              if item.kind == "driver"]), 1)

    def test_one_task_and_another_number_of_drivers(self):
        """The benchmark can keep one task's requests, and have the same
        explanations describe another number of drivers."""
        task = "prediction_explanation"
        plain = measure.chosen_requests(PAIRS, 100, task)
        fewer = measure.chosen_requests(PAIRS, 100, task, 1)
        self.assertTrue(plain)
        self.assertEqual([context.request for context, _ in fewer],
                         [context.request for context, _ in plain])
        self.assertEqual({(context.task, context.limit)
                          for context, _ in fewer}, {(task, 1)})
        lists = measure.chosen_requests(PAIRS, 100, "condition_list", 1)
        self.assertEqual(len(lists), len([
            pair for pair in PAIRS if pair[0].task == "condition_list"]))
        self.assertEqual({context.limit for context, _ in lists}, {None})

    def test_differences_name_fields_only(self):
        """A document is compared with the template's by field, rows by
        the condition or driver they state, visits and references as sets,
        wordings not at all."""
        context, template = next(
            pair for pair in PAIRS if pair[0].task == "condition_list"
            and len(shape.document_of(pair[1])["items"]) > 1)
        expected = shape.document_of(template)
        same = json.loads(json.dumps(expected))
        same["item_wording"] = "{condition} in {visits}, {count}"
        same["items"][0][2] = list(reversed(same["items"][0][2]))
        self.assertEqual(measure.differences(context.task, same, expected),
                         set())
        swapped = json.loads(json.dumps(expected))
        swapped["items"].reverse()
        self.assertEqual(measure.differences(context.task, swapped,
                                             expected), {"items: order"})
        fewer = json.loads(json.dumps(expected))
        fewer["items"].pop()
        fewer["summary"]["groups"] -= 1
        self.assertEqual(measure.differences(context.task, fewer, expected),
                         {"items: missing", "summary.groups"})
        empty = {"outcome": "not_recorded", "reason_codes": [],
                 "empty": {"wording": "None within {scope}."},
                 "notices": ["absence"]}
        self.assertEqual(measure.differences(context.task, empty, expected),
                         {"outcome not_recorded instead of answer"})

    def test_findings_of_written_documents(self):
        """Output cut off is placed by its last key; a document that
        breaks its schema is named by the rule it breaks."""
        context, template = next(
            pair for pair in PAIRS if pair[0].task == "condition_list"
            and pair[1].outcome == "answer")

        def written(text, finished=True, fallback="gate_rejected"):
            """Return an invented fallback with an output."""
            return adapter.Written(response=None, candidate=None,
                                   author="template", fallback=fallback,
                                   output=adapter.output(text, finished))

        cut = written('{"outcome": "answer", "reason_codes": [], '
                      '"summary": {"wording": "Recorded are', False,
                      "truncated")
        self.assertEqual(measure.findings(context, template, cut),
                         {"cut after wording, inside a string"})
        self.assertEqual(measure.findings(context, template,
                                          written("{", True, "malformed")),
                         {"not JSON"})
        document = shape.document_of(template)
        document["items"][0][2] = document["items"][0][2] * 2
        self.assertIn("$.items[][2]: repeated items", measure.findings(
            context, template, written(shape.text(document), True,
                                       "schema_violated")))

    def test_benchmark_reports_its_sample(self):
        """A writer that returns each request's template document has
        every sampled request released as written, and the report counts
        them."""
        documents = {projection.text(projection.view(context)): shape.text(
            shape.document_of(template)) for context, template in PAIRS}

        def script(messages, schema):
            """Write the template document of the request's view."""
            body = messages[1]["content"]
            view = body[:-len(END)].rsplit("\n\n", 1)[1]
            return adapter.output(documents[view])

        writer = adapter.Writer(adapter.ScriptedBackend(script),
                                adapter.scripted_limits())
        results = measure.write_sample(PAIRS, release_of, writer, 100)
        items = [item for pairs in results.values() for item, _ in pairs]
        self.assertEqual(len(items), len(PAIRS))
        self.assertEqual({item.author for item in items}, {"model"})
        self.assertEqual({name for pairs in results.values()
                          for _, names in pairs for name in names}, set())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            measure.report("synthetic-1-v1", results, 0.0)
        self.assertIn("released as the model wrote them %d of %d"
                      % (len(PAIRS), len(PAIRS)), output.getvalue())

    def test_labels_name_the_placement(self):
        """The BF16 configuration is never called GPU-resident."""
        self.assertIn("mixed GPU/CPU", measure.PRECISION_LABELS["bf16-mixed"])
        self.assertNotIn("GPU-resident",
                         measure.PRECISION_LABELS["bf16-mixed"])

    def test_command(self):
        """The default mode passes; an operator mode needs one mode and a
        case set."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(measure.main([]), 0)
            self.assertEqual(measure.main(["--tokens"]), 2)
            self.assertEqual(measure.main(["--tokens", "--benchmark",
                                           "--case-set", "dev-30-v1"]), 2)
            self.assertEqual(measure.main(["--benchmark", "--case-set",
                                           "dev-30-v1", "--drivers", "0"]),
                             2)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
