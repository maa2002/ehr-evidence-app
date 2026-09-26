"""Synthetic checks for ehr_evidence_app.generation.prompt.

Every prompt is built from the invented fixtures; no real record is read.
"""

import contextlib
import io
import unittest

from ehr_evidence_app.generation import prompt, shape
from ehr_evidence_app.validation import candidate as candidates

PAIRS = shape.synthetic_contexts()
END = "\n\nReply with the JSON document only."


class PromptTests(unittest.TestCase):
    """The rules, a worked example, the task and the complete bundle."""

    def test_messages(self):
        """Two messages: the rules, then the example, the task and the
        request's complete bundle, last."""
        for context, _ in PAIRS:
            messages = prompt.messages(context)
            self.assertEqual([message["role"] for message in messages],
                             ["system", "user"])
            self.assertEqual(messages[0]["content"], prompt.RULES)
            self.assertTrue(messages[1]["content"].endswith(
                prompt.bundle_text(context.bundle) + END))

    def test_the_bundle_is_complete(self):
        """The bundle's text in a prompt reads back as the bundle."""
        for context, _ in PAIRS:
            self.assertEqual(type(context.bundle).model_validate_json(
                prompt.bundle_text(context.bundle)), context.bundle)

    def test_examples_are_invented(self):
        """Each task's worked example uses the invented fixtures, with
        records to answer from."""
        examples = prompt.examples()
        self.assertEqual(set(examples), {"condition_list",
                                         "condition_presence",
                                         "prediction_explanation"})
        for task, (context, template) in examples.items():
            self.assertTrue(context.request.case.case_set.startswith(
                "synthetic-"))
            self.assertEqual(context.task, task)
            self.assertIn(template.outcome, ("answer", "explanation"))

    def test_presence_prompts_state_what_counts(self):
        """A presence prompt names the condition and which concepts of the
        bundle count as it, and how."""
        context = next(context for context, _ in PAIRS
                       if context.task == "condition_presence"
                       and context.asked.counts_as)
        text = prompt.task_text(context)
        self.assertIn(context.asked.label, text)
        for entry in context.asked.counts_as:
            self.assertIn(": %s" % entry.match, text)
        nothing = shape.Asked(context.asked.name, "X", ())
        self.assertIn("no record matches", prompt.counts_as_text(nothing))

    def test_tasks_name_their_placeholders(self):
        """Each task names the placeholders of every statement its
        answers hold."""
        for task, kinds in (
                ("condition_list", ("list_summary", "list_item",
                                    "list_unmapped", "list_empty")),
                ("condition_presence", ("presence_summary", "presence_item",
                                        "presence_absent",
                                        "presence_unmapped")),
                ("prediction_explanation", ("prediction", "driver",
                                            "driver_history",
                                            "driver_context"))):
            text = prompt.TASKS[task]
            for kind in kinds:
                for slot in candidates.KINDS[kind].slots:
                    self.assertIn("{{%s}}" % slot, text, (task, kind))

    def test_versions(self):
        """The prompt and shape versions are recorded with every
        candidate."""
        self.assertEqual(prompt.describe(),
                         {"prompt_version": prompt.PROMPT_VERSION,
                          "shape_version": shape.SHAPE_VERSION})

    def test_command(self):
        """The default mode passes on invented data."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(prompt.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
