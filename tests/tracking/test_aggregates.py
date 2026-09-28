"""Synthetic tests for the runtime records' tracker aggregates.

The records are the invented requests', written by a scripted backend in
memory; a manifest is written only to the workspace test scratch, and the
remote mode runs against the in-memory fake tracker of the registration
tests. No real record, credential or service is used.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.tracking import aggregates
from ehr_evidence_app.tracking import controlled as tracking
from tests.tracking.test_registration import (
    ALLOWED_ACTIONS, SCRATCH, FakeAdapter, regular_file, valid_environment)

INVENTED = aggregates.invented_records()


def relabelled(case_set="dev-30-v1"):
    """Return the invented records under a case set name the tracker
    accepts, in memory only."""
    return [record.model_copy(update={"case": contracts.CaseRef(
        case_set=case_set, case_number=record.case.case_number)})
        for record in INVENTED]


class AggregateTests(unittest.TestCase):
    """Counts only, one configuration, one manifest per set of records."""

    def test_default_mode(self):
        """The invented records give every aggregate and a task the
        tracker accepts, holding nothing a record holds."""
        count, problems = aggregates.run_synthetic()
        self.assertEqual(problems, [])
        self.assertEqual(count, len(INVENTED))

    def test_tracker_outputs(self):
        """The shares are of the responses written from evidence; the
        latency is the median of the model's attempts, in milliseconds."""
        outputs = aggregates.tracker_outputs({
            "requests": 10, "written_from_evidence": 8,
            "written_by_the_model": 6, "fallbacks": {"gate_rejected": 2},
            "seconds": {"median": 12.5, "p90": 20.0, "largest": 30.0}})
        self.assertEqual(outputs, {"requests": 10,
                                   "model_written_rate": 0.75,
                                   "fallback_rate": 0.25,
                                   "latency_ms": 12500.0})
        self.assertEqual(aggregates.tracker_outputs({
            "requests": 2, "written_from_evidence": 0,
            "written_by_the_model": 0, "fallbacks": {}, "seconds": None}),
            {"requests": 2})

    def test_one_configuration_per_manifest(self):
        """Records written under two configurations are not summed."""
        found = relabelled()
        changed = found[0].model_copy(update={"versions": dict(
            found[0].versions, prompt_version="prompt-v0")})
        with self.assertRaises(aggregates.AggregateError) as raised:
            aggregates.manifest_of([changed] + found[1:], "dev-30-v1")
        self.assertEqual(str(raised.exception),
                         "records_of_several_configurations")

    def test_new_metric_names_are_bounded(self):
        """The shares must lie between zero and one and the requests be a
        whole number."""
        manifest = aggregates.manifest_of(relabelled(), "dev-30-v1")
        path = aggregates.manifest_path(manifest)
        lineage = aggregates.lineage_of(manifest, path, "a" * 64)
        for change in ({"model_written_rate": 1.5}, {"fallback_rate": -0.1},
                       {"requests": 2.5}):
            with self.assertRaises(tracking.UnsafeMetadata):
                tracking.build_task_payload("project", dict(
                    lineage, evaluation_outputs=dict(
                        lineage["evaluation_outputs"], **change)), {})

    def test_manifests_are_written_once(self):
        """The same records give the same manifest, left as it is; a
        different one is never written over it."""
        manifest = aggregates.manifest_of(relabelled(), "dev-30-v1")
        with tempfile.TemporaryDirectory() as directory:
            path = aggregates.manifest_path(manifest, directory)
            aggregates.write_manifest(manifest, path)
            aggregates.write_manifest(manifest, path)
            with self.assertRaises(aggregates.ManifestConflict):
                aggregates.write_manifest(dict(manifest, records=0), path)


class WorkspaceTests(unittest.TestCase):
    """The offline and remote modes, with manifests in the test scratch."""

    def setUp(self):
        """Use a directory in the workspace test scratch."""
        if not os.path.isdir(SCRATCH):
            self.skipTest("configured test scratch is missing; run "
                          "scripts/setup-destinations.ps1 first")
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.directory = scratch.name

    def test_offline(self):
        """A case set's manifest is written and its task checked; records
        of a synthetic case set never reach the tracker."""
        result = aggregates.offline_result("dev-30-v1", relabelled(),
                                           self.directory)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["records"], len(INVENTED))
        self.assertTrue(os.path.exists(os.path.join(
            self.directory, result["manifest"])))
        refused = aggregates.offline_result(INVENTED[0].case.case_set,
                                            INVENTED, self.directory)
        self.assertEqual(refused["finding"], "metadata_refused")

    def test_remote(self):
        """The remote mode creates, reads back and archives one task whose
        tags hold the aggregates and nothing of a record."""
        adapter = FakeAdapter()
        result = aggregates.remote_result(
            "dev-30-v1", valid_environment(), lambda: adapter, relabelled(),
            self.directory, lstat=regular_file)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(adapter.actions(), ALLOWED_ACTIONS)
        tags = adapter.created["tags"]
        self.assertIn("lineage:evaluation_outputs:requests=%d"
                      % len(INVENTED), tags)
        self.assertIn("provenance:runtime_aggregates", tags)
        text = json.dumps(adapter.created)
        for record in INVENTED:
            self.assertNotIn(record.request_id, text)

    def test_command(self):
        """The default mode passes; an operator mode needs one mode and a
        case set."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(aggregates.main([]), 0)
            self.assertEqual(aggregates.main(["--operator-real"]), 2)
            self.assertEqual(aggregates.main(["--operator-real",
                                              "--operator-remote",
                                              "--case-set", "dev-30-v1"]),
                             2)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
