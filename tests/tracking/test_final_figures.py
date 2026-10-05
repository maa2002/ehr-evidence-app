"""Synthetic tests for the final evaluation's figures kept in the tracker.

The records are invented, written to the workspace test scratch in the
layout of the protected records; the remote mode runs against the
in-memory fake tracker of the registration tests. No real record,
credential or service is used.
"""

import contextlib
import hashlib
import io
import json
import ntpath
import os
import tempfile
import unittest

from ehr_evidence_app.tracking import controlled as tracking
from ehr_evidence_app.tracking import final_figures as figures
from tests.tracking.test_registration import (
    SCRATCH, TASK_ID, FakeAdapter, regular_file, valid_environment)

DOCUMENTS, DIGESTS = figures.invented_documents()
MANIFEST = figures.build_manifest(DOCUMENTS, DIGESTS, figures.INVENTED_CONFIG)


def events_of(scalars):
    """Return the scalar events of (metric, series, value) triples."""
    return tracking.build_scalar_events("task_1", scalars, 1000)


class ManifestTests(unittest.TestCase):
    """The figures read, the manifest built and what is refused."""

    def test_default_mode(self):
        """The invented records give a manifest and task, and what the
        checks refuse is refused."""
        count, problems = figures.run_synthetic()
        self.assertEqual(problems, [])
        self.assertEqual(count, len(figures.triples(MANIFEST)))

    def test_each_value_of_a_metric(self):
        """A metric is sent in both conditions, with its difference, its
        interval and, where checked, its interval by patient; a precision
        names its part."""
        coverage = MANIFEST["scalars"]["coverage"]
        self.assertEqual(
            {series for series in coverage if series.startswith("all: ")},
            {"all: baseline", "all: controlled", "all: difference",
             "all: lower", "all: upper", "all: patient-level lower",
             "all: patient-level upper"})
        self.assertEqual(MANIFEST["scalars"]["precision"][
            "lists: conditions: controlled"], 1.0)
        self.assertEqual(MANIFEST["scalars"]["rubric pass rate"][
            "answers: baseline"], 0.75)
        self.assertEqual(MANIFEST["scalars"]["read as expected"]["all"], 9)

    def test_configuration_names_the_frozen_version(self):
        """The configuration gives the run's version, the freeze, the
        question set, the rubric and the baseline, and the driver limit
        of the frozen configuration only."""
        configuration = MANIFEST["configuration"]
        self.assertEqual(
            {key: configuration[key] for key in (
                "commit", "measured_on", "prompt_version", "drivers",
                "freeze", "question_set", "rubric", "baseline")},
            {"commit": "0123abc", "measured_on": "2026-01-01",
             "prompt_version": "v9", "drivers": 5, "freeze": "freeze-v3",
             "question_set": "final-v2", "rubric": "prose-rubric-v4",
             "baseline": "baseline-v1"})
        with self.assertRaisesRegex(figures.FiguresError,
                                    "configuration_not_frozen"):
            figures.build_manifest(DOCUMENTS, DIGESTS, b"{}")

    def test_records_of_another_freeze_or_run(self):
        """Records naming another freeze or run are refused."""
        for name, change in (
                ("end_to_end", {"freeze_sha256": "0" * 64}),
                ("by_patient", {"run": "invented-16-v1-20260102T000000Z"}),
                ("ratings", {"run": "invented-16-v1-20260102T000000Z"})):
            documents = dict(DOCUMENTS, **{name: dict(DOCUMENTS[name],
                                                      **change)})
            with self.assertRaisesRegex(figures.FiguresError,
                                        "sources_disagree"):
                figures.build_manifest(documents, DIGESTS,
                                       figures.INVENTED_CONFIG)

    def test_no_mark_text_or_rating_model(self):
        """Neither a mark, a text key nor the rating model reaches the
        manifest or the task."""
        payload = figures.build_payload(
            "project_1", MANIFEST, figures.manifest_path(MANIFEST),
            figures.checksum_of(MANIFEST))
        text = json.dumps([MANIFEST, payload])
        for word in (figures.INVENTED_RATER, "x-001", "marks"):
            self.assertNotIn(word, text)

    def test_a_figure_twice_is_refused(self):
        """One metric under one series is kept once."""
        scalars = figures.Scalars()
        scalars.add("coverage", "all: baseline", 0.5)
        with self.assertRaisesRegex(figures.FiguresError, "figure_twice"):
            scalars.add("coverage", "all: baseline", 0.5)


class AllowlistTests(unittest.TestCase):
    """The tracker's names and bounds for the final evaluation."""

    def test_signed_series_only(self):
        """A share may be negative under a difference or the limit of an
        interval, never under a condition's value, and a count never."""
        self.assertEqual(len(events_of([("coverage", "all: difference",
                                         -0.5),
                                        ("coverage", "all: patient-level "
                                         "lower", -1.0)])), 2)
        for scalar in (("coverage", "all: baseline", -0.1),
                       ("coverage", "all: difference", -1.5),
                       ("questions", "all", -1),
                       ("coverage", "all: middle", 0.5),
                       ("novel figure", "all: baseline", 0.5)):
            with self.assertRaises(tracking.UnsafeMetadata):
                events_of([scalar])

    def test_a_kind_bounds_its_scalars(self):
        """The final kind takes up to its own bound; every other kind the
        default."""
        scalars = [("questions", series, 1)
                   for series in tracking.HELD_OUT_FAMILIES]
        self.assertEqual(len(tracking.build_scalar_events(
            "task_1", scalars, 9)), 9)
        with self.assertRaises(tracking.UnsafeMetadata):
            tracking.build_scalar_events("task_1", scalars, 8)
        self.assertEqual(tracking.TASK_KINDS["final_figures"]["scalars"],
                         1000)
        self.assertNotIn("scalars", tracking.TASK_KINDS["measurement"])

    def test_the_kind_bound_reaches_the_tracker(self):
        """A final task sends more scalars than another kind may: the
        sending of a task applies its kind's bound."""
        from ehr_evidence_app.tracking import connection
        many = [("questions", "all", 1)] + [
            ("coverage", "%s: %s" % (stratum, value), 0.5)
            for stratum in tracking.FINAL_STRATA
            for value in tracking.FINAL_VALUES] + [
            (metric, "%s: %s: %s" % (stratum, part, value), 0.5)
            for metric in ("precision", "recall", "F1")
            for stratum in tracking.FINAL_STRATA
            for part in tracking.FINAL_PARTS
            for value in tracking.FINAL_VALUES]
        self.assertGreater(len(many), tracking.MAX_SCALARS)
        payload = figures.build_payload(
            "b2" * 16, MANIFEST, figures.manifest_path(MANIFEST),
            figures.checksum_of(MANIFEST))
        _, findings = connection.create_controlled_task(
            FakeAdapter(), "b2" * 16, payload, "final_figures", many)
        self.assertEqual(findings, [])
        _, findings = connection.create_controlled_task(
            FakeAdapter(), "b2" * 16, payload, "measurement", many)
        self.assertIn("scalars_refused", findings)

    def test_task_name_and_fields(self):
        """The task is named by its case set and commit, and the new
        fields take their one form."""
        self.assertEqual(tracking.task_name("final_figures", {
            "case_set": "eval-100-v1", "commit": "d1322c1"}),
            "Final evaluation: eval-100-v1, d1322c1")
        for field, value in (("freeze", "freeze 3"),
                             ("question_set", "written-v4"),
                             ("rubric", "rubric-v4"),
                             ("baseline", "baseline")):
            with self.assertRaises(tracking.UnsafeMetadata):
                tracking.build_configuration({field: value})


class WorkspaceTests(unittest.TestCase):
    """The protected records read, the manifest written once, and the
    offline and remote modes, in the test scratch."""

    def setUp(self):
        """Write invented records in the layout of the protected ones."""
        if not os.path.isdir(SCRATCH):
            self.skipTest("configured test scratch is missing; run "
                          "scripts/setup-destinations.ps1 first")
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.protected = ntpath.join(scratch.name, "assessment")
        self.directory = ntpath.join(scratch.name, "lineage")
        documents = json.loads(json.dumps(DOCUMENTS))
        freeze = json.dumps(documents["freeze"]).encode("utf-8")
        digest = hashlib.sha256(freeze).hexdigest()
        self.write(ntpath.join("freeze", figures.frozen.RECORD_NAME), freeze)
        for name in ("run", "by_patient", "end_to_end"):
            documents[name]["freeze_sha256"] = digest
        documents["instr"]["inputs"]["freeze"] = digest
        run = figures.INVENTED_RUN
        for name, path in figures.source_paths(
                run, digest, "final-v2", self.protected).items():
            if name != "freeze":
                self.write(ntpath.relpath(path, self.protected),
                           json.dumps(documents[name]).encode("utf-8"))

    def write(self, relative, data):
        """Write one invented record."""
        path = ntpath.join(self.protected, relative)
        os.makedirs(ntpath.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)

    def remote(self, adapter):
        """Run the remote mode against a fake tracker."""
        return figures.remote_result(
            figures.INVENTED_RUN, valid_environment(), lambda: adapter,
            self.protected, self.directory, figures.INVENTED_CONFIG,
            lstat=regular_file)

    def test_ratings_kept_as_aggregates(self):
        """Of the ratings, only the run and the aggregates are read."""
        documents, _ = figures.read_sources(figures.INVENTED_RUN,
                                            self.protected)
        self.assertEqual(set(documents["ratings"]), {"run", "document"})

    def test_manifest_written_once(self):
        """The same figures give the same file, left as it is; a different
        file in its place is refused."""
        result = figures.offline_result(
            figures.INVENTED_RUN, self.protected, self.directory,
            figures.INVENTED_CONFIG)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["task"],
                         "Final evaluation: invented-16-v1, 0123abc")
        path, manifest = figures.prepared(
            figures.INVENTED_RUN, self.protected, self.directory,
            figures.INVENTED_CONFIG)
        with open(path, "ab") as handle:
            handle.write(b" ")
        with self.assertRaises(figures.ManifestConflict):
            figures.write_manifest(manifest, self.directory)

    def test_remote_sends_once(self):
        """The task is created with every scalar, read back and completed,
        and kept in the index; a second run sends nothing."""
        adapter = FakeAdapter()
        result = self.remote(adapter)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["task_ids"], [TASK_ID])
        actions = adapter.actions()
        self.assertEqual(actions.count("tasks.create"), 1)
        self.assertEqual(actions.count("tasks.completed"), 1)
        self.assertEqual(actions.count("events.add"), result["scalars"])
        self.assertNotIn("tasks.archive", actions)
        again = FakeAdapter()
        result = self.remote(again)
        self.assertEqual((result["status"], result["created"]), ("PASS", 0))
        self.assertEqual(again.actions(), [])
        sent = json.dumps([payload for _, payload in adapter.calls])
        self.assertNotIn(figures.INVENTED_RATER, sent)

    def test_missing_record(self):
        """A record missing stops the figures before anything is sent."""
        os.remove(ntpath.join(self.protected, "instr", "%s-%s.json" % (
            figures.INSTR_TEST, os.listdir(ntpath.join(
                self.protected, "instr"))[0][-21:-5])))
        adapter = FakeAdapter()
        result = self.remote(adapter)
        self.assertEqual((result["status"], result["finding"]),
                         ("FAIL", "source_missing"))
        self.assertEqual(adapter.actions(), [])


class CommandTests(unittest.TestCase):
    """The command's modes."""

    def test_command(self):
        """The default mode passes; an operator mode needs a run, and the
        two are one or the other."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(figures.main([]), 0)
            self.assertEqual(figures.main(["--operator-real"]), 2)
            self.assertEqual(figures.main(["--operator-real",
                                           "--operator-remote",
                                           "--run", "x"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
