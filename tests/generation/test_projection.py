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
        score first with their ranks, each with its own history and
        context, and how many more are not described."""
        for context, _ in PAIRS:
            if context.task != "prediction_explanation":
                continue
            view = projection.view(context)
            selected = shape.selected_drivers(context)
            self.assertEqual([driver["ref"] for driver in view["drivers"]],
                             [item.ref for item in selected])
            self.assertEqual([driver["rank"] for driver in view["drivers"]],
                             list(range(1, len(selected) + 1)))
            for driver, item in zip(view["drivers"], selected):
                cited = [ref for ref, _ in driver["history"]]
                cited += driver["context"]
                self.assertLessEqual(set(cited), set(item.linked_event_refs))
            self.assertEqual(view["drivers_not_described"],
                             shape.omitted_drivers(context))

    def test_lists_are_grouped_by_condition(self):
        """A list's view holds every record of the scope once, grouped by
        condition in the order of each condition's first record."""
        for context, template in PAIRS:
            if context.task != "condition_list":
                continue
            view = projection.view(context)
            refs = [ref for group in view["conditions"]
                    for ref, _ in group["records"]]
            self.assertEqual(sorted(refs), sorted(
                event.ref for event in context.bundle.events))
            items = [item for item in template.statements
                     if item.kind == "list_item"]
            self.assertEqual([[ref for ref, _ in group["records"]]
                              for group in view["conditions"]],
                             [list(item.refs) for item in items])

    def test_presence_views_hold_the_matching_records(self):
        """A presence check's view holds exactly the records that match
        the condition asked about, grouped by condition and match kind;
        none when it is not recorded."""
        for context, template in PAIRS:
            if context.task != "condition_presence":
                continue
            view = projection.view(context)
            items = [item for item in template.statements
                     if item.kind == "presence_item"]
            self.assertEqual([([ref for ref, _ in group["records"]],
                               group["match"])
                              for group in view["matching"]],
                             [(list(item.refs), item.slots.match)
                              for item in items])
            self.assertNotIn("counts_as", view)

    def test_views_count_their_records_but_state_no_outcome(self):
        """Each group and driver block gives the number and visits of the
        records it lists; no view states an outcome."""
        for context, _ in PAIRS:
            view = projection.view(context)
            self.assertNotIn('"outcome"', projection.text(view))
            groups = view.get("conditions", view.get("matching", []))
            for group in groups:
                self.assertEqual(group["count"], len(group["records"]))
                self.assertEqual(group["visits"], sorted(
                    {visit for _, visit in group["records"]}))
            for driver in view.get("drivers", []):
                self.assertEqual(driver["history_count"],
                                 len(driver["history"]))
                self.assertEqual(driver["context_count"],
                                 len(driver["context"]))

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
