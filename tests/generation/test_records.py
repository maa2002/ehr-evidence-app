"""Synthetic checks for ehr_evidence_app.generation.records.

Every record is written under a temporary directory standing for the
protected data, from invented fixtures; no real record is read.
"""

import contextlib
import datetime
import hashlib
import io
import json
import os
import tempfile
import unittest

from ehr_evidence_app.evidence import bundles, contracts
from ehr_evidence_app.generation import adapter, prompt, records, shape
from ehr_evidence_app.qa import answer, suite
from ehr_evidence_app.validation import explanation

PAIRS = shape.synthetic_contexts()
VOCAB = suite.synthetic_vocabulary()
CASE_SET = "synthetic-1-v1"
HISTORY = contracts.ScopeRef(kind="pre_target_history")
LIMITS = contracts.ExecutionLimits(**bundles.SYNTHETIC_LIMITS)
NOW = datetime.datetime(2026, 9, 28, 8, 30, tzinfo=datetime.timezone.utc)


def qa_bundles():
    """Return the invented QA bundle set, read from memory."""
    qa, explanations, counts = bundles.run_synthetic()
    manifest = bundles.manifest_document(qa, explanations,
                                         bundles.synthetic_provenance(),
                                         counts)
    data = {"qa/%s.json" % bundle.bundle_id: bundles.bundle_bytes(bundle)
            for bundle in qa}
    return answer.BundleSet(CASE_SET, None, manifest,
                            reader=lambda entry: data[entry["path"]])


def release_of(context):
    """Return the gate call of an invented request."""
    return adapter.gate_release(
        context, VOCAB if context.task != "prediction_explanation" else None)


class RecordTests(unittest.TestCase):
    """One protected record per response, written once, read back whole;
    aggregates hold counts only."""

    def setUp(self):
        """Stand a temporary directory in for the protected data."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.protected = scratch.name
        self.root = os.path.join(scratch.name, "runtime-records")

    def recorder(self, writer=None):
        """Return a recorder under the temporary protected data."""
        return records.Recorder(self.root, writer, lambda: NOW,
                                protected=self.protected)

    def test_records_hold_the_request_versions_and_generation(self):
        """A response the model wrote is recorded with its request, its
        evidence's digest, the versions and configuration digests, the
        response and its digest, and the model's output."""
        writer = records.scripted_writer(PAIRS)
        recorder = self.recorder(writer)
        for context, template in PAIRS:
            written = adapter.write(writer, context, template,
                                    release_of(context))
            path = recorder.record(written.response, context.request,
                                   bundle_sha256=context.bundle_sha256,
                                   written=written)
            self.assertEqual(os.path.dirname(path),
                             os.path.join(self.root, "2026-09-28"))
            record = records.read(path)
            self.assertEqual((record.request_id, record.task, record.case,
                              record.scope),
                             (context.request.request_id, context.task,
                              context.request.case, context.request.scope))
            self.assertEqual(record.response, written.response)
            self.assertEqual(record.response_sha256, hashlib.sha256(
                written.response.model_dump_json().encode(
                    "utf-8")).hexdigest())
            self.assertEqual(record.bundle_sha256, context.bundle_sha256)
            self.assertEqual(record.versions["prompt_version"],
                             prompt.PROMPT_VERSION)
            for name in records.CONFIG_FILES:
                self.assertEqual(
                    record.versions[name.replace(".json", "_sha256")],
                    records.config_digest(name))
            generation = record.generation
            self.assertEqual((generation.author, generation.fallback),
                             ("model", None))
            self.assertEqual(generation.output, written.output.text)
            self.assertEqual(generation.backend, {"backend": "scripted"})
            self.assertEqual(generation.model_candidate,
                             written.model_candidate.model_dump(mode="json"))

    def test_every_answer_and_explanation_is_recorded_once(self):
        """With a recorder each question and explanation writes one record:
        an answer from evidence without a writer is a no_model fallback, a
        notice has no generation."""
        recorder = self.recorder()
        bundle_set = qa_bundles()
        questions = ("List all recorded conditions.",
                     "What medications is he taking?")
        for number, question in enumerate(questions, 1):
            answer.answer(question, CASE_SET, 1, HISTORY, bundle_set, VOCAB,
                          "q-%d" % number, LIMITS, recorder=recorder)
        context = next(context for context, _ in PAIRS
                       if context.task == "prediction_explanation")
        explanation.explain(context.request, context.bundle,
                            context.bundle_sha256, recorder=recorder)
        found = {record.request_id: record for record in (
            records.read(path) for path in records.paths(self.root))}
        self.assertEqual(len(found), 3)
        listed, refused = found["q-1"], found["q-2"]
        self.assertEqual((listed.response.outcome, listed.question),
                         ("answer", questions[0]))
        self.assertEqual((listed.generation.author,
                          listed.generation.fallback,
                          listed.generation.category,
                          listed.generation.backend),
                         ("template", "no_model", "unavailable",
                          {"backend": "none"}))
        self.assertEqual(refused.response.outcome, "abstention")
        self.assertIsNone(refused.generation)
        explained = found[context.request.request_id]
        self.assertEqual(explained.generation.fallback, "no_model")
        self.assertEqual(explained.prediction,
                         context.request.prediction.model_dump(mode="json"))

    def test_a_response_not_written_from_evidence_has_no_generation(self):
        """A request stopped before generation is written by its template
        with no fallback, and records no generation."""
        response = records.read(self.first_record()).response
        stopped = adapter.Written(response, None, "template")
        self.assertIsNone(records.generation_of(stopped, {}))

    def first_record(self):
        """Write and return the path of one record of an invented
        answer written without a writer."""
        context, template = next(pair for pair in PAIRS
                                 if pair[0].task == "condition_list")
        written = adapter.write(None, context, template, release_of(context))
        return self.recorder().record(written.response, context.request,
                                      written=written)

    def test_records_are_written_once(self):
        """A record is never overwritten, and a root outside the protected
        data is refused."""
        path = self.first_record()
        with self.assertRaises(records.RecordError):
            records.store(self.root, records.read(path))
        with self.assertRaises(records.RecordError):
            records.Recorder(os.path.join(os.path.dirname(self.protected),
                                          "elsewhere"),
                             protected=self.protected)

    def test_only_records_are_read(self):
        """A file that is not a record is refused by its code."""
        path = self.first_record()
        with open(path, "wb") as handle:
            handle.write(b'{"schema_version": "1.0"}')
        with self.assertRaises(records.RecordError) as raised:
            records.read(path)
        self.assertEqual(str(raised.exception), "record_invalid")

    def test_aggregates_hold_counts_only(self):
        """Aggregates count requests, outcomes, authors and fallbacks and
        summarise the measures, naming nothing a record holds."""
        writer = records.scripted_writer(PAIRS)
        recorder = self.recorder(writer)
        found = []
        for context, template in PAIRS:
            written = adapter.write(writer, context, template,
                                    release_of(context))
            found.append(records.read(recorder.record(
                written.response, context.request, written=written)))
        totals = records.aggregate(found)
        self.assertEqual(totals["requests"], len(PAIRS))
        self.assertEqual(totals["written_by_the_model"], len(PAIRS))
        self.assertEqual(totals["fallbacks"], {})
        text = json.dumps(totals)
        for record in found:
            self.assertNotIn(record.request_id, text)
            if record.response.text:
                self.assertNotIn(record.response.text, text)

    def test_log_audit(self):
        """The log audit names by rule, never by what it matched, the
        secrets, patient identifiers and content markers in invented logs,
        and passes a routine line."""
        self.assertEqual(records.audit_problems(), [])
        lines = records.invented_logs()
        for number, line in enumerate(lines.values()):
            with open(os.path.join(self.protected, "run-%d.log" % number),
                      "w", encoding="utf-8") as handle:
                handle.write(line)
        flagged, count = records.audit_logs([self.protected])
        self.assertEqual(count, len(lines))
        found = set()
        for _, rules in flagged:
            found |= set(rules)
            for rule in rules:
                for line in lines.values():
                    self.assertNotIn(rule, line.split())
        self.assertLessEqual(set(lines) - {None}, found)

    def test_logs_are_read_in_pieces_at_line_ends(self):
        """A log longer than the policy's size is read in pieces cut after
        a line."""
        data = b"first line\nsecond line\nthird\n"
        pieces = list(records.chunks(data, 15))
        self.assertEqual(b"".join(pieces), data)
        self.assertTrue(all(len(piece) <= 15 for piece in pieces))
        self.assertTrue(all(piece.endswith(b"\n") for piece in pieces))

    def test_command(self):
        """The default mode passes on invented data; the operator mode
        needs both of its flags."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(records.main([]), 0)
            self.assertEqual(records.main(["--aggregate"]), 2)
            self.assertEqual(records.main(["--operator-real"]), 2)
            self.assertEqual(records.main(["--operator-real", "--aggregate",
                                           "--audit-logs"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
