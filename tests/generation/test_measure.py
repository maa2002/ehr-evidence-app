"""Synthetic checks for ehr_evidence_app.generation.measure.

The sampling and summaries are checked on the invented fixtures; no model
runs and no real record is read.
"""

import contextlib
import dataclasses
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

    def test_scores_compare_as_shown(self):
        """A score rounded to the figures a response shows is no
        difference; one wrong in its last shown figure is."""
        expected = {"outcome": "explanation",
                    "prediction": {"label": 9, "score": 0.8175744761936437,
                                   "logit": 1.5}}
        rounded = {"outcome": "explanation",
                   "prediction": {"label": 9, "score": 0.818, "logit": 1.5}}
        wrong = {"outcome": "explanation",
                 "prediction": {"label": 9, "score": 0.819, "logit": 1.5}}
        task = "prediction_explanation"
        self.assertEqual(measure.differences(task, rounded, expected), set())
        self.assertEqual(measure.differences(task, wrong, expected),
                         {"prediction.score"})

    def test_notices_are_named(self):
        """A notice missing or added is named by its identifier."""
        expected = {"outcome": "explanation",
                    "notices": ["drivers", "target_attached"]}
        stated = {"outcome": "explanation",
                  "notices": ["drivers", "target_excluded"]}
        self.assertEqual(measure.differences("prediction_explanation",
                                             stated, expected),
                         {"notices: target_attached missing",
                          "notices: target_excluded added"})

    def test_additions_are_named(self):
        """A disclosure or reason code added where the correct answer has
        none, or left out where it has one, is named with its
        direction."""
        expected = {"outcome": "not_recorded", "reason_codes": [],
                    "absent": {"wording": "{condition} is not recorded "
                                          "within {scope}."},
                    "unmapped": None, "notices": ["absence"]}
        stated = dict(expected, reason_codes=["unmapped_records_in_scope"],
                      unmapped={"wording": "{unmapped} have no standard "
                                           "concept.", "unmapped": 2})
        task = "condition_presence"
        self.assertEqual(measure.differences(task, stated, expected),
                         {"reason_codes: unmapped_records_in_scope added",
                          "unmapped added"})
        self.assertEqual(measure.differences(task, expected, stated),
                         {"reason_codes: unmapped_records_in_scope missing",
                          "unmapped left out"})

    def test_history_findings_name_their_part(self):
        """A driver's history or context row is named by the part that
        differs, or as left out; counts and visits alone are
        bookkeeping."""
        correct = [["A1", 1, 0.5, [["E1", "E2"], [1, 3], 2], None]]
        miscounted = [["A1", 1, 0.5, [["E1", "E2"], [1, 3], 3], None]]
        left_out = [["A1", 1, 0.5, None, None]]
        task = "prediction_explanation"
        self.assertEqual(measure.row_differences(task, miscounted, correct),
                         {"drivers[history.count]"})
        self.assertEqual(measure.row_differences(task, left_out, correct),
                         {"drivers[history] left out"})
        rejected = adapter.Written(response=None, candidate=None,
                                   author="template",
                                   fallback="gate_rejected")
        self.assertTrue(measure.bookkeeping_only(
            rejected, {"drivers[history.count]", "summary.records"}))
        self.assertFalse(measure.bookkeeping_only(
            rejected, {"drivers[history.count]", "items: missing"}))

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
        items = [item for pairs in results.values() for item, *_ in pairs]
        self.assertEqual(len(items), len(PAIRS))
        self.assertEqual({item.author for item in items}, {"model"})
        self.assertEqual({name for pairs in results.values()
                          for _, names, *_ in pairs for name in names}, set())
        entries = [result.stated for pairs in results.values()
                   for result in pairs if result.stated is not None]
        self.assertTrue(entries)
        for stated_history, history, stated_context, context in entries:
            self.assertEqual((stated_history, stated_context),
                             (history, context))
        self.assertEqual(
            sorted((result.unmapped for pairs in results.values()
                    for result in pairs), key=str),
            sorted((None if context.task == "prediction_explanation"
                    else context.bundle.coverage.unmapped_events_in_scope > 0
                    for context, _ in PAIRS), key=str))
        needs = sorted(result.needed for pairs in results.values()
                       for result in pairs)
        self.assertEqual(needs, sorted(
            adapter.ScriptedBackend.count(shape.text(shape.document_of(
                template))) for _, template in PAIRS))
        middle = needs[len(needs) // 2]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            measure.report("synthetic-1-v1", results, 0.0,
                           [(needs[-1], 1), (middle, 1)])
        self.assertIn("released as the model wrote them %d of %d"
                      % (len(PAIRS), len(PAIRS)), output.getvalue())
        self.assertIn("released within %d output tokens and 1 s %d of %d"
                      % (needs[-1], len(PAIRS), len(PAIRS)),
                      output.getvalue())
        self.assertIn("released within %d output tokens and 1 s %d of %d"
                      % (middle, sum(need <= middle for need in needs),
                         len(PAIRS)), output.getvalue())
        self.assertIn("released, with records without a standard concept "
                      "in scope: ", output.getvalue())
        for key in ("records", "drivers", "history lines"):
            self.assertIn("%s: precision 1.000, recall 1.000, F1 1.000"
                          % key, output.getvalue())
        self.assertIn("outcome right: ", output.getvalue())
        self.assertNotIn("withheld documents", output.getvalue())

    def test_scores_of_lists(self):
        """A condition is a group of the correct list, named by any of its
        records: a repeat or a row naming none is a false positive, and a
        record counts only for its own group. No document states
        nothing."""
        expected = {"outcome": "answer", "items": [
            ["E1", ["E1", "E2"], [1, 2], 2], ["E3", ["E3"], [1], 1]]}
        stated = {"outcome": "answer", "items": [
            ["E1", ["E1"], [1], 1], ["E2", ["E2"], [2], 1],
            ["E9", ["E9"], [1], 1]]}
        scores = measure.task_scores("condition_list", stated, expected)
        self.assertEqual(scores, {"conditions": (1, 2, 1),
                                  "records": (2, 1, 1), "outcome": 1})
        self.assertEqual(measure.task_scores("condition_list", None,
                                             expected),
                         {"conditions": (0, 0, 2), "records": (0, 0, 3),
                          "outcome": 0})
        self.assertEqual(measure.rates(1, 2, 1),
                         "precision 0.333, recall 0.500, F1 0.400 "
                         "(TP 1, FP 2, FN 1)")
        self.assertEqual(measure.rates(0, 0, 0),
                         "precision n/a, recall n/a, F1 n/a "
                         "(TP 0, FP 0, FN 0)")

    def test_scores_of_explanations(self):
        """A driver is named by its reference; a line is right when its
        records, visits and number are, and a line stated for a driver
        that has none is a false positive, named with whose records it
        cites."""
        expected = {"outcome": "explanation", "drivers": [
            ["D1", 1, 0.5, [["E1", "E2"], [1, 2], 2], None],
            ["D2", 2, 0.3, None, [["E5"], 1]]]}
        stated = {"outcome": "explanation", "drivers": [
            ["D1", 1, 0.5, [["E1", "E2"], [1, 2], 2], [["E7"], 1]],
            ["D2", 2, 0.3, [["E1"], [1], 1], [["E5"], 1]],
            ["D9", 3, 0.1, None, None]]}
        self.assertEqual(measure.task_scores("prediction_explanation",
                                             stated, expected),
                         {"drivers": (2, 1, 0), "history lines": (1, 1, 0),
                          "context lines": (1, 1, 0)})
        self.assertEqual(measure.line_kinds(stated, expected), {
            "history right": 1, "context right": 1,
            "context added, with other records": 1,
            "history added, with another driver's records": 1,
            "rows for no selected driver": 1})

    def test_rows_of_a_withheld_list(self):
        """The rows of a withheld list are named by what they cite."""
        context, template = next(
            pair for pair in PAIRS if pair[0].task == "condition_list"
            and any(len(row[1]) > 1 for row in shape.document_of(
                pair[1]).get("items") or []))
        expected = shape.document_of(template)
        stated = json.loads(json.dumps(expected))
        at = next(index for index, row in enumerate(stated["items"])
                  if len(row[1]) > 1)
        stated["items"][at][1] = stated["items"][at][1][:1]
        stated["items"].append(expected["items"][at])
        stated["items"].append(["E999", ["E999"], [1], 1])
        kinds = measure.row_kinds(context, stated, expected)
        self.assertEqual(kinds["rows with part of their records"], 1)
        self.assertEqual(kinds["rows repeating a condition"], 1)
        self.assertEqual(kinds["rows naming no condition"], 1)
        self.assertEqual(kinds["rows with their records exactly"],
                         len(expected["items"]))
        self.assertNotIn("conditions not named", kinds)

    def test_model_documents(self):
        """Only a finished output that parses as a JSON object is the
        model's document."""
        for text, finished, found in (('{"outcome": "answer"}', True, True),
                                      ('{"outcome": "answer"}', False, False),
                                      ("{", True, False),
                                      ("[]", True, False)):
            written = adapter.Written(response=None, candidate=None,
                                      author="template", fallback="malformed",
                                      output=adapter.output(text, finished))
            self.assertEqual(measure.model_document(written) is not None,
                             found)

    def test_released_within_lower_limits(self):
        """A released candidate counts within a lower limit only when its
        template document and its output fit the tokens and it took no
        longer than the seconds; a fallback never counts."""
        output = adapter.Output(text="{}", prompt_tokens=10,
                                output_tokens=100, finished=True,
                                seconds=50.0)
        released = adapter.Written(response=None, candidate=None,
                                   author="model", output=output)
        self.assertTrue(measure.released_within(released, 100, 100, 50))
        self.assertFalse(measure.released_within(released, 101, 100, 50))
        self.assertFalse(measure.released_within(released, 90, 99, 50))
        self.assertFalse(measure.released_within(released, 90, 100, 49))
        withheld = dataclasses.replace(released, author="template",
                                       fallback="gate_rejected")
        self.assertFalse(measure.released_within(withheld, 90, 100, 50))

    def test_paired_comparison(self):
        """The paired interval holds its difference, turns over with the
        conditions, and narrows as the pairs agree; the exact McNemar test
        counts the discordant pairs only."""
        difference, lower, upper = measure.paired_difference(10, 3, 1, 6)
        self.assertAlmostEqual(difference, -2 / 20)
        self.assertTrue(lower < difference < upper)
        self.assertEqual(measure.paired_difference(10, 1, 3, 6),
                         (-difference, -upper, -lower))
        wide = measure.paired_difference(10, 3, 1, 6)
        narrow = measure.paired_difference(100, 30, 10, 60)
        self.assertLess(narrow[2] - narrow[1], wide[2] - wide[1])
        self.assertEqual(measure.mcnemar(0, 6), 2 / 64)
        self.assertEqual(measure.mcnemar(3, 3), 1.0)
        self.assertEqual(measure.mcnemar(0, 0), 1.0)
        low, high = measure.wilson(5, 10)
        self.assertAlmostEqual(low + high, 1.0)

    def test_record_of_a_run(self):
        """A run's scalars count each stratum, its unions and all the
        requests, score each stratum and measure the model's attempts; with
        the run's configuration they make a record the tracker accepts."""
        from ehr_evidence_app.evidence import contracts
        from ehr_evidence_app.tracking import measurements
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
        needs = [result.needed for pairs in results.values()
                 for result in pairs]
        scalars = measure.measurement_scalars(results, 12.5,
                                              (max(needs), 1))
        groups = {measure.STRATA[measure.group_of(context, template)]
                  for context, template in PAIRS}
        self.assertEqual(scalars["requests"]["all"], len(PAIRS))
        self.assertEqual(scalars["released"], scalars["requests"])
        self.assertEqual(scalars["released within limits"],
                         scalars["requests"])
        self.assertEqual(set(scalars["requests"]),
                         groups | {"all lists", "presence", "all"})
        self.assertEqual(scalars["requests"]["all lists"],
                         scalars["requests"]["lists"]
                         + scalars["requests"]["empty lists"])
        self.assertNotIn("all", scalars["outcome right"])
        self.assertEqual(set(scalars["F1"].values()), {1.0})
        self.assertIn("explanations: history lines", scalars["F1"])
        self.assertEqual(scalars["seconds"]["all: total"], 12.5)
        self.assertIn("all: median", scalars["output tokens"])
        limits = contracts.load_limits()
        configuration = measure.run_configuration(
            "dev-30-v1", "bf16-mixed", limits, 8, None, (max(needs), 1),
            "0123abc", "2026-09-28")
        self.assertEqual(configuration["prompt_version"], "v9")
        self.assertEqual(configuration["max_output_tokens"],
                         limits.max_output_tokens)
        record = measurements.build_record(configuration, scalars)
        self.assertEqual(record["scalars"]["release rate"]["all"], 1.0)
        text = json.dumps(record)
        for pairs in results.values():
            for result in pairs:
                self.assertNotIn(result.written.output.text, text)

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
            self.assertEqual(measure.main(["--tokens", "--case-set",
                                           "dev-30-v1", "--within", "768",
                                           "120"]), 2)
            self.assertEqual(measure.main(["--tokens", "--case-set",
                                           "dev-30-v1", "--record"]), 2)
            self.assertEqual(measure.main([
                "--benchmark", "--case-set", "dev-30-v1", "--record",
                "--within", "512", "60", "--within", "256", "30"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
