"""Synthetic checks for ehr_evidence_app.assessment.runner.

The invented requests are written in both conditions by scripted
backends; records are written in the configured workspace test scratch
directory, and those checks are skipped where it is missing.
"""

import json
import os
import shutil
import tempfile
import unittest

from ehr_evidence_app.assessment import runner, statistics
from ehr_evidence_app.generation import adapter, shape
from ehr_evidence_app.qa import suite
from ehr_evidence_app.storage import policy as guard

PAIRS = shape.synthetic_contexts()
VOCAB = suite.synthetic_vocabulary()
LIMITS = adapter.scripted_limits()
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")


def release_of(context):
    """Return the gate call of an invented request."""
    return adapter.gate_release(
        context, VOCAB if context.task != "prediction_explanation" else None)


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


class OutcomeTests(unittest.TestCase):
    """What each condition's outcome is."""

    def test_default_mode(self):
        """Correct documents are released alike in both conditions, and
        every challenge ends as it should."""
        summary, found, problems = runner.run_synthetic()
        self.assertEqual(problems, [])
        self.assertEqual(summary["requests"], len(PAIRS))
        self.assertEqual(len(found), 20)
        outcomes = {}
        for name, task, outcome, count in summary["outcomes"]:
            outcomes.setdefault(name, {})[(task, outcome)] = count
        self.assertEqual(outcomes["controlled"], outcomes["baseline"])

    def test_challenges(self):
        """A case rejection is shared; where evidence does not suffice the
        controlled condition abstains and the baseline writes; a failure
        is the same in both."""
        found = runner.run_challenges(LIMITS, VOCAB)
        self.assertEqual(found["list: coverage_incomplete"]["controlled"],
                         ("abstention", "abstention"))
        self.assertEqual(found["list: coverage_incomplete"]["baseline"][1],
                         "answer")
        self.assertEqual(found["explanation: no_positive_attribution"][
            "baseline"][1], "explanation")
        self.assertEqual(found["list: identity_inconsistent"]["baseline"],
                         ("case_rejection", "case_rejection"))
        self.assertEqual(found["failure: prompt_over_budget"]["baseline"],
                         ("limit_exceeded", "limit_exceeded"))

    def test_withheld_candidate_is_kept_not_credited(self):
        """A candidate the gate withholds is the model's failure: a
        validation rejection without text, its candidate kept for the
        record; the template that would be released is not credited."""
        context, template = next(
            (context, template) for context, template in PAIRS
            if context.task == "prediction_explanation")
        expected, script = [item for item in adapter.scripts(template)
                            if item[0] == "gate_rejected"][0]
        found = runner.controlled(
            adapter.Writer(adapter.ScriptedBackend(script), LIMITS), context,
            template, release_of(context))
        self.assertEqual((found.outcome, found.category, found.reason,
                          found.released, found.text),
                         ("validation_rejection", "validation_rejection",
                          expected, False, None))
        self.assertIsNotNone(found.candidate)
        self.assertTrue(found.gate_reasons)

    def test_distribution(self):
        """Median, 90th percentile of nearest rank and largest, worked by
        hand."""
        self.assertEqual(statistics.distribution([5, 1, 3]),
                         {"count": 3, "median": 3, "p90": 5, "largest": 5})
        self.assertEqual(statistics.distribution([4, 1, 3, 2]),
                         {"count": 4, "median": 2.5, "p90": 4,
                          "largest": 4})
        self.assertEqual(statistics.distribution([]),
                         {"count": 0, "median": None, "p90": None,
                          "largest": None})

    def test_evaluation_set_refused(self):
        """The operator mode refuses the evaluation set before anything is
        read or written."""
        with self.assertRaisesRegex(runner.RunnerError, "case_set_held_out"):
            runner.run_real("eval-100-v1", 1)


class RecordTests(unittest.TestCase):
    """Each request's protected record, written once."""

    def setUp(self):
        """Make a scratch protected root in the workspace test temp."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT, True))
        if guard.fixed_policy_problem():
            self.skipTest("the fixed layout policy does not hold here")
        self.root = root
        self.work = tempfile.mkdtemp(prefix="assessment-runner-", dir=root)
        self.addCleanup(self.remove_scratch)
        self.protected = os.path.join(self.work, "protected")
        self.runs = os.path.join(self.protected, "assessment", "paired")

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def test_records(self):
        """A run writes its configuration, one record per request with
        both conditions, and its summary; a second run of the same
        identifier is refused."""
        context, template = PAIRS[0]
        writer = adapter.Writer(adapter.ScriptedBackend(
            adapter.scripts(template)[0][1]), LIMITS)
        directory = runner.open_run("run-1", {"case_set": "synthetic"},
                                    self.runs, self.protected)
        summary = runner.run([(context, template)], writer, release_of,
                             VOCAB, directory, "run-1")
        runner.close_run(directory, summary)
        names = sorted(os.listdir(os.path.join(directory, runner.RECORDS)))
        self.assertEqual(names, ["00001.json"])
        with open(os.path.join(directory, runner.RECORDS, names[0]),
                  encoding="utf-8") as handle:
            record = json.load(handle)
        self.assertEqual(set(record["conditions"]), {"controlled",
                                                     "baseline"})
        self.assertEqual(record["request"]["request_id"],
                         context.request.request_id)
        for name in runner.CONDITIONS:
            condition = record["conditions"][name]
            self.assertEqual(condition["outcome"], template.outcome)
            self.assertEqual(len(condition["prompt_sha256"]), 64)
        self.assertNotEqual(record["conditions"]["controlled"][
            "prompt_sha256"], record["conditions"]["baseline"][
            "prompt_sha256"])
        for name in ("run.json", "summary.json"):
            self.assertTrue(os.path.isfile(os.path.join(directory, name)))
        with self.assertRaisesRegex(runner.RunnerError, "run_exists"):
            runner.open_run("run-1", {}, self.runs, self.protected)

    def test_unprotected_directory_refused(self):
        """A run directory outside the protected root is refused."""
        with self.assertRaisesRegex(runner.RunnerError,
                                    "output_not_protected"):
            runner.open_run("run-2", {}, os.path.join(self.work, "open"),
                            self.protected)


if __name__ == "__main__":
    unittest.main()
