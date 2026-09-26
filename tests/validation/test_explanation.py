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
                          explanation.describable(BUNDLE)})

    def test_drivers_in_order_with_their_scores(self):
        """Drivers keep the bundle's order and scores."""
        drivers = explanation.of_kind(GOOD, "driver")
        self.assertEqual([item.slots.driver for item in drivers],
                         [item.ref for item in
                          explanation.describable(BUNDLE)])
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
        and a missing notice."""
        for expected, fault, bundle in explanation.synthetic_faults(
                GOOD, BUNDLE):
            response = explanation.release(fault, REQUEST, bundle, DIGEST)
            self.assertEqual(response.outcome, "validation_rejection")
            self.assertIn(expected, response.reason_codes)
            self.assertIsNone(response.text)

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
