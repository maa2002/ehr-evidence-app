"""Synthetic checks for ehr_evidence_app.generation.adapter.

The backends are scripted and every request is invented; no model runs
and no real record is read.
"""

import contextlib
import io
import unittest
from unittest import mock

from ehr_evidence_app.evidence import bundles, contracts
from ehr_evidence_app.generation import adapter, shape
from ehr_evidence_app.qa import answer, suite
from ehr_evidence_app.validation import evidence, explanation

PAIRS = shape.synthetic_contexts()
VOCAB = suite.synthetic_vocabulary()
LIMITS = adapter.scripted_limits()
ALL = contracts.ScopeRef(kind="case_visits")


def release_of(context):
    """Return the gate call of an invented request."""
    return adapter.gate_release(
        context, VOCAB if context.task != "prediction_explanation" else None)


def writes(document):
    """Return a script that writes a document."""
    text = shape.text(document)
    return lambda messages, schema: adapter.output(text)


def first(task, outcome):
    """Return the first invented (context, template) of a task and
    outcome."""
    return next((context, template) for context, template in PAIRS
                if context.task == task and template.outcome == outcome)


class AdapterTests(unittest.TestCase):
    """The model's candidate is released only through the gate; anything
    else is the template's, with its reason."""

    def test_scripted_runs(self):
        """Every way a writer can fail ends in its fallback with the
        template's response, and a correct document is released as the
        model's."""
        counts = adapter.run_synthetic()
        self.assertTrue(adapter.passed(counts))
        self.assertEqual(set(counts) - {"model_released"},
                         set(adapter.FALLBACKS) - {"busy"})

    def test_one_request_at_a_time(self):
        """A request while the model writes another falls back as busy."""
        context, template = PAIRS[0]
        writer = adapter.Writer(adapter.ScriptedBackend(writes(
            shape.document_of(template))), LIMITS)
        self.assertTrue(writer.slots.acquire(blocking=False))
        try:
            busy = adapter.write(writer, context, template,
                                 release_of(context))
        finally:
            writer.slots.release()
        self.assertEqual((busy.author, busy.fallback), ("template", "busy"))
        free = adapter.write(writer, context, template, release_of(context))
        self.assertEqual((free.author, free.fallback), ("model", None))

    def test_attempts(self):
        """Only a failed backend is tried again, within the attempt
        limit."""
        context, template = PAIRS[0]
        text = shape.text(shape.document_of(template))
        calls = []

        def flaky(messages, schema):
            """Fail once, then write."""
            calls.append(1)
            if len(calls) == 1:
                raise adapter.BackendError("backend_failed")
            return adapter.output(text)

        twice = LIMITS.model_copy(update={"max_generation_attempts": 2})
        written = adapter.write(adapter.Writer(adapter.ScriptedBackend(flaky),
                                               twice),
                                context, template, release_of(context))
        self.assertEqual(written.author, "model")
        calls.clear()
        written = adapter.write(adapter.Writer(adapter.ScriptedBackend(flaky),
                                               LIMITS),
                                context, template, release_of(context))
        self.assertEqual(written.fallback, "backend_failed")
        timeouts = adapter.ScriptedBackend(adapter.raising("timeout"))
        written = adapter.write(adapter.Writer(timeouts, twice), context,
                                template, release_of(context))
        self.assertEqual((written.fallback, timeouts.calls), ("timeout", 1))

    def test_late_output_is_not_read(self):
        """Output that arrives after the time limit is a timeout."""
        context, template = PAIRS[0]
        writer = adapter.Writer(adapter.ScriptedBackend(writes(
            shape.document_of(template))), LIMITS)
        with mock.patch.object(adapter.time, "monotonic",
                               side_effect=[0.0, 10000.0]):
            written = adapter.write(writer, context, template,
                                    release_of(context))
        self.assertEqual(written.fallback, "timeout")

    def test_gate_rejections_keep_their_reasons(self):
        """A candidate the gate withholds is recorded with the gate's
        reasons, and the template's response is released."""
        context, template = first("condition_list", "answer")
        document = shape.document_of(template)
        document["items"][0][shape.LIST_ROW.index("count")] += 1
        written = adapter.write(adapter.Writer(adapter.ScriptedBackend(
            writes(document)), LIMITS), context, template,
            release_of(context))
        self.assertEqual((written.fallback, written.gate_reasons),
                         ("gate_rejected", ("claim_unsupported",)))
        self.assertIsNotNone(written.model_candidate)
        self.assertEqual(written.response, release_of(context)(template))

    def test_backend_failures_are_closed(self):
        """A backend failure names one of the known reasons."""
        with self.assertRaises(ValueError):
            adapter.BackendError("unknown")

    def test_fallback_categories(self):
        """Every fallback has a category; requests over a limit are
        limit_exceeded, never insufficient evidence."""
        self.assertEqual(set(adapter.FALLBACK_CATEGORIES),
                         set(adapter.FALLBACKS))
        for reason in ("prompt_over_budget", "output_over_budget",
                       "truncated"):
            self.assertEqual(adapter.FALLBACK_CATEGORIES[reason],
                             "limit_exceeded")
        self.assertNotIn("evidence_insufficient",
                         set(adapter.FALLBACK_CATEGORIES.values()))
        context, template = PAIRS[0]
        written = adapter.write(None, context, template, release_of(context))
        self.assertEqual(written.category, "unavailable")

    def test_answers_are_written_through_the_adapter(self):
        """With a writer, the model's list and presence answers are
        released as its own; without one, the template's, as no_model
        fallbacks; refusals never reach the model."""
        bundle_set = suite.synthetic_bundle_set()

        def ask(question, writer=None):
            """Answer an invented question in all case visits."""
            return answer.answer(question, "synthetic-1-v1", 1, ALL,
                                 bundle_set, VOCAB, "q-1", LIMITS, writer)

        seen = []
        for question in ("List all recorded conditions.",
                         "Is synthetic parent disorder recorded?"):
            plain = ask(question)
            self.assertEqual((plain.written.author, plain.written.fallback),
                             ("template", "no_model"))
            text = shape.text(shape.document_of(plain.candidate))

            def script(messages, schema, text=text):
                """Write the template's document, noting the prompt."""
                seen.append(messages[1]["content"])
                return adapter.output(text)

            writer = adapter.Writer(adapter.ScriptedBackend(script), LIMITS)
            modelled = ask(question, writer)
            self.assertEqual((modelled.written.author,
                              modelled.written.fallback), ("model", None))
            self.assertEqual(modelled.response, plain.response)
            refusal = ask("What medications is she on?", writer)
            self.assertIsNone(refusal.written)
            self.assertEqual(writer.backend.calls, 1)
        self.assertIn('"counts_as"', seen[1])

    def test_explanations_are_written_through_the_adapter(self):
        """With a writer, the model's explanation is released as its own;
        without one, the template's, as a no_model fallback."""
        _, explanations, _ = bundles.run_synthetic()
        bundle = explanations[0]
        request = evidence.explanation_request(bundle)
        plain = explanation.explain_written(request, bundle, "5" * 64)
        self.assertEqual((plain.author, plain.fallback),
                         ("template", "no_model"))
        writer = adapter.Writer(adapter.ScriptedBackend(writes(
            shape.document_of(plain.candidate))), LIMITS)
        modelled = explanation.explain_written(request, bundle, "5" * 64,
                                               writer)
        self.assertEqual((modelled.author, modelled.fallback),
                         ("model", None))
        self.assertEqual(modelled.response, plain.response)
        self.assertEqual(explanation.explain(request, bundle, "5" * 64,
                                             writer)[0], plain.response)

    def test_held_out_case_sets_are_refused(self):
        """The evaluation case set is never used."""
        with self.assertRaisesRegex(adapter.DevelopmentError,
                                    "case_set_held_out"):
            adapter.development_requests("eval-100-v1")

    def test_command(self):
        """The default mode passes; the real mode needs a case set."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(adapter.main([]), 0)
            self.assertEqual(adapter.main(["--operator-real"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
