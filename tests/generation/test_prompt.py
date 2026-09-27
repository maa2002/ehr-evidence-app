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
        """Each task's worked examples use the invented fixtures; the list
        and presence answers group several records into one item; every
        list and presence example discloses a record without a standard
        concept, a presence check's "not recorded" too; an explanation's
        example leaves drivers out exactly when its request does."""
        examples = prompt.examples()
        self.assertEqual(set(examples), {
            "condition_list", "condition_presence",
            ("condition_presence", False),
            ("prediction_explanation", True),
            ("prediction_explanation", False)})
        for key, (context, template) in examples.items():
            self.assertTrue(context.request.case.case_set.startswith(
                "synthetic-"))
            if context.task == "prediction_explanation":
                continue
            self.assertEqual(template.reason_codes,
                             ("unmapped_records_in_scope",))
            self.assertEqual(projection.view(context)[
                "records_without_standard_concept"], 1)
            if template.outcome == "answer":
                self.assertGreater(max(len(item.refs)
                                       for item in template.statements
                                       if item.kind.endswith("_item")), 1)
        absent = examples[("condition_presence", False)][1]
        self.assertEqual(absent.outcome, "not_recorded")
        self.assertIn("presence_unmapped",
                      {item.kind for item in absent.statements})
        for context, _ in PAIRS:
            chosen = prompt.examples_of(context)
            if context.task == "condition_presence":
                self.assertEqual({template.outcome for _, template in chosen},
                                 {"answer", "not_recorded"})
            if context.task != "prediction_explanation":
                continue
            (example, template), = chosen
            notices = json.loads(shape.text(shape.document_of(template)))[
                "notices"]
            self.assertEqual("drivers_omitted" in notices,
                             shape.omitted_drivers(context) > 0)

    def test_presence_prompts_carry_the_matching_records(self):
        """A presence prompt names the condition, and its view lists the
        records that count as it."""
        context = next(context for context, _ in PAIRS
                       if context.task == "condition_presence"
                       and context.asked.counts_as)
        self.assertIn(context.asked.label, prompt.task_text(context))
        body = prompt.messages(context)[1]["content"]
        self.assertIn('"matching":[{', body[body.index("Now the request."):])

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

    def test_every_reply_is_offered(self):
        """A list or presence check is offered both of its replies, each
        with the condition on the view that calls for it, and the example
        wordings of both, whatever its evidence."""
        for context, template in PAIRS:
            if context.task == "prediction_explanation":
                continue
            text = prompt.task_text(context)
            self.assertIn('"outcome": "answer"', text)
            self.assertIn('"outcome": "not_recorded"', text)
            for kind in shape.WORDED[context.task]:
                self.assertIn("- %s:" % kind, text)

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
