"""Synthetic tests for the measurements kept in the tracker.

The records are invented, or those of the committed history of Phase 7,
which holds aggregates only; they are written to the workspace test
scratch, and the remote mode runs against the in-memory fake tracker of
the registration tests. No real record, credential or service is used.
"""

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest

from ehr_evidence_app.tracking import controlled as tracking
from ehr_evidence_app.tracking import measurements
from tests.tracking.test_registration import (
    SCRATCH, TASK_ID, FakeAdapter, regular_file, valid_environment)

HISTORY = measurements.load_history()


def changed(record, **scalars):
    """Return a copy of a record with some metrics' values replaced."""
    return dict(record, scalars=dict(record["scalars"], **scalars))


class RecordTests(unittest.TestCase):
    """A record's form, configuration and counts."""

    def test_default_mode(self):
        """The invented record and the history check, and what the checks
        refuse is refused."""
        count, problems = measurements.run_synthetic()
        self.assertEqual(problems, [])
        self.assertEqual(count, 1 + len(HISTORY))

    def test_history(self):
        """Every run of the history is a backfilled record of its commit,
        each with its own file; one is assembled."""
        self.assertEqual(len(HISTORY), 17)
        self.assertEqual({record["source"] for record in HISTORY},
                         {"backfill"})
        self.assertEqual(sum(record["assembled"] for record in HISTORY), 1)
        self.assertEqual(len({measurements.record_path(record)
                              for record in HISTORY}), len(HISTORY))
        commits = {record["configuration"]["commit"] for record in HISTORY}
        self.assertEqual(commits, {"7f14baf", "96edab4", "42c9011",
                                   "20e02a0", "93762df", "f54a152",
                                   "a66dfd6", "18cccbd", "87027bd"})
        final = [record for record in HISTORY if record["assembled"]][0]
        self.assertEqual((final["scalars"]["released"]["all"],
                          final["scalars"]["requests"]["all"]), (385, 430))
        self.assertEqual(final["scalars"]["release rate"]["all"], 0.895349)

    def test_counts_must_agree(self):
        """Released beyond the requests, a union that is not the sum of
        its strata, a rate that is not the counts' and lower limits
        without their counts are refused."""
        record = HISTORY[0]
        requests = record["scalars"]["requests"]
        for bad in (changed(record, released=dict(requests)),
                    changed(record, released=dict(
                        record["scalars"]["released"], lists=9)),
                    changed(record, requests=dict(requests, all=40)),
                    changed(record, **{"release rate": {"all": 0.5}}),
                    changed(record, **{"released within limits": {
                        "all": 1}}),
                    dict(record, source="measured"),
                    dict(record, assembled=True, source="run")):
            with self.assertRaises(measurements.MeasurementError):
                measurements.check_record(bad)

    def test_configuration_is_required_and_checked(self):
        """A record names its commit and date, and its fields take their
        permitted forms only."""
        record = HISTORY[0]
        for configuration in (
                {key: value for key, value in record[
                    "configuration"].items() if key != "commit"},
                dict(record["configuration"], within_seconds=60),
                dict(record["configuration"], measured_on="yesterday"),
                dict(record["configuration"], notes="free text")):
            with self.assertRaises(measurements.MeasurementError):
                measurements.check_record(dict(record,
                                               configuration=configuration))

    def test_task_of_a_record(self):
        """A record's task is named by its configuration, marked
        backfilled, and its lineage names the record and its aggregates
        only."""
        record = HISTORY[8]
        path = measurements.record_path(record)
        payload = measurements.build_payload(
            "project_1", record, path, measurements.checksum_of(record))
        self.assertEqual(payload["name"], "Generation measurement: "
                         "dev-30-v1, bf16-mixed, prompt v7, 93762df")
        self.assertIn("provenance:backfilled", payload["tags"])
        self.assertIn("lineage:code_revision=93762df", payload["tags"])
        self.assertIn("lineage:evaluation_outputs:requests=430",
                      payload["tags"])
        self.assertIn("lineage:evaluation_outputs:model_written_rate="
                      "0.730233", payload["tags"])
        self.assertIn("lineage:protected_reference=" + path, payload["tags"])
        section = payload["hyperparams"]["Configuration"]
        self.assertEqual(section["max_request_seconds"]["value"], "120")
        events = tracking.build_scalar_events(
            "task_1", measurements.triples(record))
        self.assertIn(("release rate", "all", 0.730233),
                      {(event["metric"], event["variant"], event["value"])
                       for event in events})


class CommitTests(unittest.TestCase):
    """The commit a run's record names."""

    def git(self, status="", commit="0123abc", code=0):
        """Return a stand-in for subprocess.run answering git."""
        def run(arguments, **_):
            """Answer one git command."""
            output = status if "status" in arguments else commit + "\n"
            return subprocess.CompletedProcess(arguments, code, output, "")
        return run

    def test_code_commit(self):
        """A clean checkout gives its commit; modified tracked files, a
        failing git or an unexpected answer give none."""
        self.assertEqual(measurements.code_commit(run=self.git()), "0123abc")
        for run, code in ((self.git(status=" M src/x.py"),
                           "working_tree_modified"),
                          (self.git(code=128), "commit_unknown"),
                          (self.git(commit="HEAD"), "commit_unknown")):
            with self.assertRaises(measurements.MeasurementError) as raised:
                measurements.code_commit(run=run)
            self.assertEqual(str(raised.exception), code)


class WorkspaceTests(unittest.TestCase):
    """Records written once, the offline and remote modes, in the test
    scratch."""

    def setUp(self):
        """Use a directory in the workspace test scratch."""
        if not os.path.isdir(SCRATCH):
            self.skipTest("configured test scratch is missing; run "
                          "scripts/setup-destinations.ps1 first")
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.directory = scratch.name

    def test_records_are_written_once(self):
        """The same record gives the same file, left as it is; a file
        altered afterwards fails every read."""
        record = measurements.invented_record()
        path = measurements.write_record(record, self.directory)
        self.assertEqual(measurements.write_record(record, self.directory),
                         path)
        self.assertEqual(measurements.read_records(self.directory),
                         [(path, record)])
        with open(path, "ab") as handle:
            handle.write(b" ")
        with self.assertRaises(measurements.MeasurementError):
            measurements.read_records(self.directory)

    def test_offline(self):
        """The history's records are written and every task checked."""
        result = measurements.offline_result(self.directory)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual((result["records"], result["backfilled"],
                          result["to_send"]), (17, 17, 17))

    def test_remote_sends_each_record_once(self):
        """Every record not yet sent becomes a completed task, kept in the
        index; a second run sends none."""
        adapter = FakeAdapter()
        result = measurements.remote_result(
            valid_environment(), lambda: adapter, self.directory,
            lstat=regular_file)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["created"], 17)
        self.assertEqual(adapter.actions().count("tasks.create"), 17)
        self.assertEqual(adapter.actions().count("tasks.completed"), 17)
        self.assertNotIn("tasks.archive", adapter.actions())
        self.assertEqual(set(measurements.read_index(
            self.directory).values()), {TASK_ID})
        again = FakeAdapter()
        result = measurements.remote_result(
            valid_environment(), lambda: again, self.directory,
            lstat=regular_file)
        self.assertEqual((result["status"], result["created"]), ("PASS", 0))
        self.assertEqual(again.actions(), [])

    def test_remote_stops_at_a_finding(self):
        """A task read back wrong stops the run; the records sent before it
        stay in the index, and it is not."""
        created = []

        def readback(query, task):
            """Read the second task back with another name."""
            created.append(task["name"])
            found = dict(task, id=TASK_ID)
            if len(created) == 2:
                found["name"] = "renamed"
            return 200, {"data": {"tasks": [found]}}

        adapter = FakeAdapter(readback=readback)
        result = measurements.remote_result(
            valid_environment(), lambda: adapter, self.directory,
            lstat=regular_file)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["findings"], ["readback_mismatch"])
        self.assertEqual(len(measurements.read_index(self.directory)), 1)

    def test_nothing_of_a_request_is_sent(self):
        """The tracker receives names, configuration and numbers only."""
        adapter = FakeAdapter()
        measurements.remote_result(valid_environment(), lambda: adapter,
                                   self.directory, lstat=regular_file)
        sent = json.dumps([payload for _, payload in adapter.calls])
        for word in ("entry", "Phase 7", "assembled from"):
            self.assertNotIn(word, sent)


class CommandTests(unittest.TestCase):
    """The command's modes."""

    def test_command(self):
        """The default mode passes; the two operator modes are one or the
        other."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(measurements.main([]), 0)
            self.assertEqual(measurements.main(["--operator-real",
                                                "--operator-remote"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
