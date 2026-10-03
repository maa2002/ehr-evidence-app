"""Synthetic checks for ehr_evidence_app.assessment.baseline.

The baseline is compared with the controlled condition on the invented
requests, written by scripted backends; no model runs and no real record
is read.
"""

import json
import re
import unittest

from ehr_evidence_app.assessment import baseline, runner
from ehr_evidence_app.generation import adapter, prompt, shape
from ehr_evidence_app.qa import suite
from ehr_evidence_app.validation import evidence

PAIRS = shape.synthetic_contexts()
VOCAB = suite.synthetic_vocabulary()
LIMITS = adapter.scripted_limits()


def listed():
    """Return an invented list request answered from records, and its
    template."""
    return next((context, template) for context, template in PAIRS
                if context.task == "condition_list"
                and template.outcome == "answer")


def writer_of(text):
    """Return a writer whose backend writes a text."""
    return adapter.Writer(adapter.ScriptedBackend(
        lambda messages, schema: adapter.output(text)), LIMITS)


def release_of(context):
    """Return the gate call of an invented request."""
    return adapter.gate_release(
        context, VOCAB if context.task != "prediction_explanation" else None)


def rules_by_number(text):
    """Return {number: rule} of a rules text."""
    return {int(match.group(1)): match.group(2)
            for match in re.finditer(r"^(\d+)\. (.*)$", text, re.MULTILINE)}


class PromptTests(unittest.TestCase):
    """Only the grounding rules differ between the conditions' prompts."""

    def test_rules_left_out(self):
        """Rules 1, 4 and 5 are left out and rules 2, 3 and 6 kept as they
        are, renumbered 1 to 3; the role before them is the same."""
        controlled = rules_by_number(prompt.RULES)
        kept = rules_by_number(baseline.rules())
        self.assertEqual(sorted(controlled), [1, 2, 3, 4, 5, 6])
        self.assertEqual(kept, {1: controlled[2], 2: controlled[3],
                                3: controlled[6]})
        self.assertEqual(prompt.RULES.split("Rules for every reply:")[0],
                         baseline.rules().split("Rules for every reply:")[0])

    def test_request_message_is_shared(self):
        """Every invented request's message, worked examples included, is
        the controlled condition's."""
        for context, _ in PAIRS:
            ours, theirs = baseline.messages(context), prompt.messages(context)
            self.assertEqual(ours[1:], theirs[1:])
            self.assertEqual(ours[0]["role"], "system")
            self.assertNotEqual(ours[0]["content"], theirs[0]["content"])


class AttemptTests(unittest.TestCase):
    """The baseline's one attempt."""

    def test_correct_document(self):
        """A correct document is written as the controlled condition
        releases it: the same candidate, outcome and text."""
        for context, template in PAIRS:
            text = shape.text(shape.document_of(template))
            ours = baseline.attempt(writer_of(text), context, template, VOCAB)
            theirs = runner.controlled(writer_of(text), context, template,
                                       release_of(context))
            self.assertTrue(ours.released and theirs.released)
            self.assertEqual((ours.candidate, ours.outcome, ours.text),
                             (theirs.candidate, theirs.outcome, theirs.text))

    def test_invented_reference(self):
        """A document citing a record the bundle does not hold: the gate
        withholds it, and the baseline gives it, the record shown as not
        in the record."""
        context, template = listed()
        document = shape.document_of(template)
        item = document["items"][0]
        document["items"][0] = ["E999", ["E999"] + item[1][1:], item[2],
                                item[3]]
        text = json.dumps(document)
        theirs = runner.controlled(writer_of(text), context, template,
                                   release_of(context))
        self.assertEqual((theirs.outcome, theirs.category, theirs.reason),
                         ("validation_rejection", "validation_rejection",
                          "gate_rejected"))
        self.assertIsNone(theirs.text)
        ours = baseline.attempt(writer_of(text), context, template, VOCAB)
        self.assertEqual(ours.outcome, "answer")
        self.assertIn(baseline.UNRESOLVED, ours.text)
        self.assertIn("E999", ours.candidate.statements[1].refs)

    def test_sufficiency_is_not_checked(self):
        """With the scope's coverage incomplete the controlled condition
        abstains before writing; the baseline writes."""
        context, template = listed()
        faulty = next(bundle for stop, bundle
                      in evidence.synthetic_faults(context.bundle)
                      if stop.outcome == "abstention")
        changed = shape.Context(context.request, faulty,
                                context.bundle_sha256)
        script = adapter.ScriptedBackend(adapter.scripts(template)[0][1])
        theirs = runner.controlled(adapter.Writer(script, LIMITS), changed,
                                   template, release_of(changed))
        self.assertEqual((theirs.outcome, theirs.reason),
                         ("abstention", "coverage_incomplete"))
        self.assertEqual(script.calls, 0)
        ours = baseline.attempt(adapter.Writer(script, LIMITS), changed,
                                template, VOCAB)
        self.assertEqual(ours.outcome, "answer")
        self.assertEqual(script.calls, 1)

    def test_binding_is_checked(self):
        """A bundle of another case is a case rejection in the baseline
        too, before anything is written."""
        context, template = listed()
        for stop, faulty in evidence.synthetic_faults(context.bundle):
            if stop.outcome != "case_rejection":
                continue
            script = adapter.ScriptedBackend(adapter.scripts(template)[0][1])
            found = baseline.attempt(
                adapter.Writer(script, LIMITS),
                shape.Context(context.request, faulty,
                              context.bundle_sha256), template, VOCAB)
            self.assertEqual((found.outcome, found.reason),
                             (stop.outcome, stop.reasons[0]))
            self.assertEqual(script.calls, 0)

    def test_failures(self):
        """Every scripted failure is the controlled condition's failure,
        with its category."""
        context, template = listed()
        for expected, script in adapter.scripts(template):
            if expected in (None, "gate_rejected"):
                continue
            found = baseline.attempt(
                adapter.Writer(adapter.ScriptedBackend(script), LIMITS),
                context, template, VOCAB)
            self.assertEqual((found.outcome, found.reason, found.category),
                             ("operational_error", expected,
                              adapter.FALLBACK_CATEGORIES[expected]))

    def test_default_mode_passes(self):
        """The module's own check finds nothing."""
        self.assertEqual(baseline.run_synthetic(), [])


if __name__ == "__main__":
    unittest.main()
