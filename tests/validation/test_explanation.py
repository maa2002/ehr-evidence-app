"""Synthetic checks for ehr_evidence_app.validation.explanation.

The explanation bundle is the bundle builder's invented one; the real mode
is only checked for refusing a held-out case set before anything is read.
"""

import contextlib
import io
import unittest

from ehr_evidence_app.evidence import bundles
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import evidence, explanation

_, EXPLANATIONS, _ = bundles.run_synthetic()
BUNDLE = EXPLANATIONS[0]
DIGEST = "5" * 64
REQUEST = evidence.explanation_request(BUNDLE)
RESPONSE, GOOD = explanation.explain(REQUEST, BUNDLE, DIGEST)


class TemplateTests(unittest.TestCase):
    """The template explanation states the bundle and nothing else."""

    def test_released(self):
        """An eligible case gets an explanation citing its prediction,
        drivers and linked records, with the case's restrictions."""
        self.assertEqual((RESPONSE.outcome, RESPONSE.reason_codes),
                         ("explanation", BUNDLE.restrictions))
        cited = {ref for claim in RESPONSE.claims for ref in claim.refs}
        self.assertIn("P1", cited)
        self.assertEqual({ref for ref in cited if ref.startswith("A")},
                         {item.ref for item in
                          explanation.selected(BUNDLE)})

    def test_drivers_in_order_with_their_scores(self):
        """The selected drivers are described highest score first, with
        their scores and ranks."""
        drivers = explanation.of_kind(GOOD, "driver")
        self.assertEqual([item.slots.driver for item in drivers],
                         [item.ref for item in
                          explanation.selected(BUNDLE)])
        scores = [item.slots.score for item in drivers]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual([item.slots.rank for item in drivers],
                         list(range(1, len(drivers) + 1)))

    def test_history_is_pre_target_and_context_is_final(self):
        """History cites pre-target linked events; context cites
        final-admission ones; neither carries a score."""
        history = {event.ref for event in BUNDLE.history_events}
        context = {event.ref for event in BUNDLE.final_admission_context}
        for item in explanation.of_kind(GOOD, "driver_history"):
            self.assertTrue(set(item.refs) <= history)
            self.assertIsNone(item.slots.score)
        for item in explanation.of_kind(GOOD, "driver_context"):
            self.assertTrue(set(item.refs) <= context)
            self.assertIsNone(item.slots.score)

    def test_notices(self):
        """What the scores mean, and each restriction, are stated."""
        notices = {item.notice for item in GOOD.statements
                   if item.kind == "notice"}
        self.assertTrue(set(explanation.MEANING_NOTICES) <= notices)
        self.assertEqual(notices - set(explanation.MEANING_NOTICES), {
            candidates.RESTRICTION_NOTICES[code]
            for code in BUNDLE.restrictions})


class CheckTests(unittest.TestCase):
    """Each preservation fault gets its own reason code."""

    def test_faults(self):
        """Prediction, drivers, order, a zero-score driver, disclosures
        and a missing notice, for the configured number of drivers and for
        one driver, where the drivers not described are stated too."""
        for limit in (None, 1):
            _, good = explanation.explain(REQUEST, BUNDLE, DIGEST,
                                          limit=limit)
            names = set()
            for name, expected, fault, bundle in explanation.synthetic_faults(
                    good, BUNDLE):
                names.add(name)
                response = explanation.release(fault, REQUEST, bundle,
                                               DIGEST, limit)
                self.assertEqual(response.outcome, "validation_rejection",
                                 name)
                self.assertIn(expected, response.reason_codes, name)
                self.assertIsNone(response.text)
        self.assertLessEqual({"omitted_drivers_miscounted",
                              "omitted_drivers_unstated",
                              "unselected_driver_described"}, names)

    def test_history_must_be_the_drivers_linked_events(self):
        """A history statement citing another driver's records is
        unsupported."""
        items = list(GOOD.statements)
        index = next(number for number, item in enumerate(items)
                     if item.kind == "driver_history")
        other = next(item for item in explanation.describable(BUNDLE)
                     if item.ref != items[index].slots.driver)
        items[index] = items[index].model_copy(update={
            "slots": items[index].slots.model_copy(update={
                "driver": other.ref})})
        fault = GOOD.model_copy(update={"statements": tuple(items)})
        self.assertEqual(explanation.check(fault, BUNDLE),
                         ("claim_unsupported",))

    def test_wording(self):
        """Reading the score as a risk is prohibited in an explanation."""
        items = list(GOOD.statements)
        items[0] = items[0].model_copy(update={
            "wording": "{label} has a risk of {score} (logit {logit})."})
        response = explanation.release(
            GOOD.model_copy(update={"statements": tuple(items)}), REQUEST,
            BUNDLE, DIGEST)
        self.assertEqual(response.reason_codes, ("prohibited_wording",))


class SelectionTests(unittest.TestCase):
    """An explanation describes the eligible drivers with the highest
    scores, and states how many more are kept in the evidence."""

    def test_configured_selection(self):
        """The committed configuration selects by attribution score, and
        its limit is not yet frozen."""
        selection = explanation.load_selection()
        self.assertEqual(selection.selection, "highest_eligible_attribution")
        self.assertEqual(explanation.configured_limit(), selection.limit)

    def test_highest_scores_first_ties_in_bundle_order(self):
        """Selection is by score, highest first; ties keep the bundle's
        order; ineligible drivers are never selected."""
        eligible = explanation.describable(BUNDLE)
        tied = BUNDLE.model_copy(update={"attributions": tuple(
            item.model_copy(update={"score": 0.5})
            if item.narration_eligible else item
            for item in BUNDLE.attributions)})
        self.assertEqual([item.ref for item in explanation.selected(tied)],
                         [item.ref for item in eligible])
        best = max(eligible, key=lambda item: item.score)
        self.assertEqual([item.ref for item in explanation.selected(
            BUNDLE, 1)], [best.ref])
        self.assertEqual(explanation.omitted(BUNDLE, 1), len(eligible) - 1)

    def test_drivers_not_described_are_stated(self):
        """With fewer drivers described than eligible, a notice states how
        many more there are; otherwise there is none."""
        _, one = explanation.explain(REQUEST, BUNDLE, DIGEST, limit=1)
        stated = [item.slots.omitted for item in one.statements
                  if item.notice == "drivers_omitted"]
        self.assertEqual(stated, [len(explanation.describable(BUNDLE)) - 1])
        self.assertEqual([item for item in GOOD.statements
                          if item.notice == "drivers_omitted"], [])
        response = explanation.release(one, REQUEST, BUNDLE, DIGEST, 1)
        self.assertIn("kept in the evidence", response.text)


class OutcomeTests(unittest.TestCase):
    """Requests that cannot be explained end before any text."""

    def test_rejections_and_abstentions(self):
        """Another prediction is rejected; an ineligible case abstains."""
        other = evidence.explanation_request(BUNDLE, "0" * 64)
        response, candidate = explanation.explain(other, BUNDLE, DIGEST)
        self.assertEqual((response.outcome, response.reason_codes,
                          candidate),
                         ("case_rejection", ("prediction_link_invalid",),
                          None))
        ineligible = BUNDLE.model_copy(update={"explanation_eligible": False})
        response, _ = explanation.explain(REQUEST, ineligible, DIGEST)
        self.assertEqual((response.outcome, response.reason_codes),
                         ("abstention", ("explanation_ineligible",)))
        self.assertIn("no explanation is given", response.text)

    def test_held_out_case_sets_are_refused(self):
        """The evaluation case set is never explained here."""
        with self.assertRaisesRegex(explanation.ExplanationError,
                                    "case_set_held_out"):
            explanation.bundle_plan("eval-100-v1")

    def test_command(self):
        """The default mode passes; the real mode needs a case set."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(explanation.main([]), 0)
            self.assertEqual(explanation.main(["--operator-real"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
