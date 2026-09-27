"""Synthetic checks for ehr_evidence_app.generation.projection.

Projections are built from the invented fixtures; the real mode is only
checked for refusing a held-out case set before anything is read.
"""

import contextlib
import io
import json
import unittest

from ehr_evidence_app.generation import projection, shape

PAIRS = shape.synthetic_contexts()


class ProjectionTests(unittest.TestCase):
    """The model's view holds what its task needs, and nothing else."""

    def test_every_request_is_answered_from_its_projection(self):
        """The correct compact document follows from the projection
        alone, for every invented request."""
        for context, template in PAIRS:
            view = json.loads(projection.text(projection.view(context)))
            self.assertEqual(projection.answer(view), json.loads(
                shape.text(shape.document_of(template))), context.task)

    def test_left_out(self):
        """Names, dates, native identifiers and provenance are not in the
        view; the records keep their references."""
        context, _ = next(pair for pair in PAIRS
                          if pair[0].task == "condition_list"
                          and pair[0].bundle.events)
        text = projection.text(projection.view(context))
        for event in context.bundle.events:
            self.assertIn('"%s"' % event.ref, text)
            if event.start_date:
                self.assertNotIn(event.start_date, text)
            if event.concept.name:
                self.assertNotIn(event.concept.name, text)
        self.assertNotIn("provenance", text)
        self.assertNotIn(context.bundle.bundle_id, text)

    def test_explanations_show_the_selected_drivers(self):
        """An explanation's view holds the selected drivers only, highest
        score first, and how many more are not described."""
        for context, _ in PAIRS:
            if context.task != "prediction_explanation":
                continue
            view = projection.view(context)
            self.assertEqual([row[0] for row in view["drivers"]],
                             [item.ref for item in
                              shape.selected_drivers(context)])
            self.assertEqual(view["drivers_not_described"],
                             shape.omitted_drivers(context))

    def test_command(self):
        """The default mode passes; the real mode needs a case set and
        refuses the evaluation case set."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(projection.main([]), 0)
            self.assertEqual(projection.main(["--operator-real"]), 2)
            self.assertEqual(projection.main(["--operator-real",
                                              "--case-set",
                                              "eval-100-v1"]), 1)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
