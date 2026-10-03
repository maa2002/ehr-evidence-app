"""Synthetic checks for ehr_evidence_app.assessment.report.

Invented runs are written in memory by scripted backends and scored
against the invented references; no model runs and no real record is
read.
"""

import json
import os
import shutil
import tempfile
import unittest

from ehr_evidence_app.assessment import references, report, runner
from ehr_evidence_app.generation import adapter, shape
from ehr_evidence_app.qa import suite
from ehr_evidence_app.storage import policy as guard

VOCAB = suite.synthetic_vocabulary()
LIMITS = adapter.scripted_limits()
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def release_of(context):
    """Return the gate call of an invented request."""
    return adapter.gate_release(
        context, VOCAB if context.task != "prediction_explanation" else None)


def invented_run(writes):
    """Return the records of the invented requests, each written in both
    conditions by a backend that writes writes(context, template)."""
    records = []
    for number, (context, template) in enumerate(
            shape.synthetic_contexts(), 1):
        text = writes(context, template)
        writer = adapter.Writer(adapter.ScriptedBackend(
            lambda messages, schema, text=text: adapter.output(text)),
            LIMITS)
        kept = []
        runner.run([(context, template)], writer, release_of, VOCAB,
                   kept=kept)
        kept[0]["request"]["request_id"] = "r-%03d" % number
        records.append(kept[0])
    return records


def scored_report(records):
    """Return the report of invented records."""
    return report.report_document(report.score_records(
        records, references.synthetic_sources(), report.synthetic_bundles(),
        references.configured_limit()))


def correct(context, template):
    """Write the template's own document."""
    return shape.text(shape.document_of(template))


def invented_reference(context, template):
    """Write lists with a record the bundle does not hold in their first
    item, and every other request correctly."""
    document = shape.document_of(template)
    if context.task == "condition_list" and template.outcome == "answer":
        item = document["items"][0]
        document["items"][0] = ["E999", ["E999"] + item[1][1:], item[2],
                                item[3]]
    return json.dumps(document)


class ReportTests(unittest.TestCase):
    """The report of invented runs."""

    def test_default_mode(self):
        """Correct documents score perfectly, alike in both conditions."""
        document, problems = report.run_synthetic()
        self.assertEqual(problems, [])
        self.assertIn(report.ALL, document["strata"])

    def test_conditions_differ(self):
        """With a record the bundle does not hold in each list's first
        item, the gate withholds those lists and the baseline gives them:
        the controlled condition answers fewer lists, and the baseline's
        answers hold unsupported statements and references to nothing."""
        lists = scored_report(invented_run(invented_reference))[
            "strata"]["list"]["metrics"]
        coverage = lists["coverage"]["values"]
        self.assertEqual(coverage["baseline"], 1.0)
        self.assertEqual(coverage["controlled"], 0.0)
        self.assertLess(lists["coverage"]["comparison"]["difference"], 0)
        unsupported = lists["unsupported_answer_rate"]["values"]
        self.assertEqual(unsupported["baseline"], 1.0)
        self.assertIsNone(unsupported["controlled"])
        self.assertLess(lists["citation_resolution"]["values"]["baseline"],
                        1.0)
        self.assertEqual(lists["exact_match"]["values"],
                         {"baseline": 0.0, "controlled": 0.0})

    def test_bundle_must_match(self):
        """A record naming another bundle digest is refused."""
        records = invented_run(correct)
        records[0]["request"]["bundle"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(report.ReportError, "bundle_changed"):
            scored_report(records)

    def test_unfinished_run_refused(self):
        """A run without its summary, or with records missing, is not
        scored."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT, True))
        work = tempfile.mkdtemp(prefix="assessment-report-", dir=root)
        try:
            os.makedirs(os.path.join(work, runner.RECORDS))
            with open(os.path.join(work, "run.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({"requests": 1}, handle)
            with self.assertRaisesRegex(report.ReportError,
                                        "run_not_finished"):
                report.read_run(work)
            with open(os.path.join(work, "summary.json"), "w",
                      encoding="utf-8") as handle:
                json.dump({}, handle)
            with self.assertRaisesRegex(report.ReportError,
                                        "run_records_missing"):
                report.read_run(work)
        finally:
            if guard.is_within(work, root, strict=True):
                shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
