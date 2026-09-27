"""Synthetic checks for ehr_evidence_app.generation.prompt.

Every prompt is built from the invented fixtures; no real record is read.
"""

import contextlib
import io
import json
import unittest

from ehr_evidence_app.generation import projection, prompt, shape
from ehr_evidence_app.validation import candidate as candidates

PAIRS = shape.synthetic_contexts()
END = "\n\nReply with the JSON document only."


class PromptTests(unittest.TestCase):
    """The rules, a worked example, the task and the evidence view."""

    def test_messages(self):
        """Two messages: the rules, then the example, the task and the
        request's evidence view, last; never the canonical bundle."""
        for context, _ in PAIRS:
            messages = prompt.messages(context)
            self.assertEqual([message["role"] for message in messages],
                             ["system", "user"])
            self.assertEqual(messages[0]["content"], prompt.RULES)
            body = messages[1]["content"]
            self.assertTrue(body.endswith(
                projection.text(projection.view(context)) + END))
            self.assertNotIn(context.bundle.bundle_id, body)

    def test_examples_are_invented(self):
        """Each task's worked example uses the invented fixtures, with
        records to answer from; the list and presence examples group
        several records into one item; an explanation's example leaves
        drivers out exactly when its request does."""
        examples = prompt.examples()
        self.assertEqual(set(examples), {
            "condition_list", "condition_presence",
            ("prediction_explanation", True),
            ("prediction_explanation", False)})
        for key, (context, template) in examples.items():
            self.assertTrue(context.request.case.case_set.startswith(
                "synthetic-"))
            self.assertIn(template.outcome, ("answer", "explanation"))
            if context.task != "prediction_explanation":
                self.assertGreater(max(len(item.refs)
                                       for item in template.statements
                                       if item.kind.endswith("_item")), 1)
        for context, _ in PAIRS:
            if context.task != "prediction_explanation":
                continue
            example, template = prompt.example_of(context)
            notices = json.loads(shape.text(shape.document_of(template)))[
                "notices"]
            self.assertEqual("drivers_omitted" in notices,
                             shape.omitted_drivers(context) > 0)

    def test_presence_prompts_carry_what_counts(self):
        """A presence prompt names the condition, and its view says which
        concepts or sources count as it, and how records are matched."""
        context = next(context for context, _ in PAIRS
                       if context.task == "condition_presence"
                       and context.asked.counts_as)
        self.assertIn(context.asked.label, prompt.task_text(context))
        view = projection.view(context)
        self.assertEqual(view["matched_by"], context.asked.by)
        self.assertEqual(len(view["counts_as"]),
                         len(context.asked.counts_as))

    def test_tasks_name_their_placeholders(self):
        """Each request's task names the placeholders of every statement
        its reply may hold, and the one each wording begins with."""
        for context, _ in PAIRS:
            text = prompt.task_text(context)
            for kind in prompt.worded_kinds(context):
                for slot in candidates.KINDS[kind].slots:
                    self.assertIn("{%s}" % slot, text, (context.task, kind))
                subject = shape.SUBJECTS.get(kind)
                if subject:
                    self.assertIn("begins with {%s}" % subject, text,
                                  (context.task, kind))

    def test_replies_offered_are_the_admissible(self):
        """A list of a scope with records is offered as an answer only,
        with the answer's example wordings; one without, as empty."""
        for context, template in PAIRS:
            if context.task != "condition_list":
                continue
            text = prompt.task_text(context)
            if context.bundle.events:
                self.assertNotIn('"outcome": "not_recorded"', text)
                self.assertNotIn("list_empty:", text)
            else:
                self.assertNotIn('"outcome": "answer"', text)
                self.assertIn("list_empty:", text)

    def test_versions(self):
        """The prompt, shape and projection versions are recorded with
        every candidate."""
        self.assertEqual(prompt.describe(), {
            "prompt_version": prompt.PROMPT_VERSION,
            "shape_version": shape.SHAPE_VERSION,
            "projection_version": projection.PROJECTION_VERSION})

    def test_command(self):
        """The default mode passes on invented data."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(prompt.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
