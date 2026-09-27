"""Synthetic checks for ehr_evidence_app.validation.structure.

Every candidate is invented and checked against the bundle builder's
invented bundles; no real record is read.
"""

import contextlib
import io
import unittest

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import structure

BUNDLE, DIGEST = structure.synthetic_bundle()
GOOD = structure.synthetic_list(BUNDLE, DIGEST)
HISTORY = contracts.ScopeRef(kind="pre_target_history")


def check(candidate, concept_name=None, condition=None, bound=None):
    """Check a candidate for the invented request, with the invented
    bundle bound when the candidate answers from evidence (or as bound
    says)."""
    if bound is None:
        bound = candidate.outcome in ("answer", "not_recorded",
                                      "explanation")
    return structure.check(candidate, "q-1", GOOD.case, GOOD.scope,
                           BUNDLE if bound else None,
                           DIGEST if bound else None, concept_name,
                           condition)


def clarification(*statements, **fields):
    """Return an invented clarification candidate."""
    values = dict(schema_version=candidates.CANDIDATE_VERSION,
                  request_id="q-1", case=GOOD.case, scope=GOOD.scope,
                  outcome="clarification",
                  reason_codes=("concept_unknown",),
                  statements=statements)
    values.update(fields)
    return candidates.Candidate(**values)


class StructureTests(unittest.TestCase):
    """Each structural fault gets its own reason code."""

    def test_valid_candidate_passes(self):
        """A well-formed list answer passes."""
        self.assertEqual(check(GOOD), ())

    def test_faults(self):
        """Wording, references, composition, order, reasons, case and
        bundle, for the request with its bundle bound."""
        for expected, variant in structure.synthetic_variants(GOOD):
            self.assertIn(expected, check(variant, bound=True), expected)

    def test_parse(self):
        """A malformed document is structure_invalid before any check."""
        valid, invalid = candidates.synthetic_candidates()
        parsed, problems = structure.parse(valid[0])
        self.assertEqual((parsed.outcome, problems), ("not_recorded", ()))
        self.assertEqual(structure.parse(invalid[0]),
                         (None, ("structure_invalid",)))

    def test_notices_are_fixed(self):
        """A changed notice, or one for another outcome, is refused."""
        notice = candidates.notice("concept_unknown", term="florbnitis")
        self.assertEqual(check(clarification(notice)), ())
        changed = notice.model_copy(update={
            "wording": "\"{term}\" is not a condition; it was never "
                       "recorded."})
        self.assertEqual(check(clarification(changed)),
                         ("structure_invalid",))
        absence = candidates.notice("absence")
        self.assertEqual(check(clarification(absence)),
                         ("structure_invalid",))

    def test_clarifications_cite_nothing(self):
        """A clarification has no bundle and cites no evidence."""
        notice = candidates.notice("concept_unknown", term="florbnitis")
        self.assertEqual(check(clarification(notice, bundle=GOOD.bundle),
                               bound=True), ("structure_invalid",))
        cited = notice.model_copy(update={"refs": ("C1",)})
        self.assertIn("structure_invalid", check(clarification(cited)))

    def test_scope_slots_name_the_candidates_scope(self):
        """A statement about another scope is a scope violation."""
        items = list(GOOD.statements)
        items[0] = items[0].model_copy(update={
            "slots": items[0].slots.model_copy(update={"scope": HISTORY})})
        self.assertEqual(check(GOOD.model_copy(update={
            "statements": tuple(items)})), ("scope_violated",))

    def test_bound_requests_are_answered_from_their_bundle(self):
        """A refusal of a request whose bundle is bound, and complete, is
        a scope violation; the same refusal without a bundle passes."""
        refusal = GOOD.model_copy(update={
            "outcome": "abstention", "bundle": None,
            "reason_codes": ("coverage_incomplete",),
            "statements": (candidates.notice("coverage_incomplete"),)})
        self.assertEqual(structure.check(refusal, "q-1", GOOD.case,
                                         GOOD.scope, BUNDLE, DIGEST),
                         ("scope_violated",))
        self.assertEqual(check(refusal), ())

    def test_notices_are_stated_once(self):
        """A notice stated twice is refused."""
        self.assertEqual(check(GOOD.model_copy(update={
            "statements": GOOD.statements + GOOD.statements[-1:]})),
            ("structure_invalid",))

    def test_statements_keep_their_order(self):
        """An item before its summary, or the notice inside the list, is
        refused; the template order passes."""
        items = GOOD.statements
        for reordered in (items[1:2] + items[:1] + items[2:],
                          items[:1] + items[-1:] + items[1:-1]):
            self.assertEqual(check(GOOD.model_copy(update={
                "statements": reordered})), ("structure_invalid",))

    def test_presence_answers_name_the_condition_asked_about(self):
        """A presence answer about another condition than the question's
        is a scope violation."""
        absent = candidates.Candidate(
            schema_version=candidates.CANDIDATE_VERSION, request_id="q-1",
            task="condition_presence", case=GOOD.case, scope=GOOD.scope,
            outcome="not_recorded", bundle=GOOD.bundle,
            statements=(candidates.Statement(
                kind="presence_absent",
                wording="{condition} is not recorded within {scope}.",
                slots=candidates.Slots(
                    condition=candidates.ConditionName(code="Z99"),
                    scope=GOOD.scope), refs=("C1",)),
                candidates.notice("absence")))
        asked = candidates.ConditionName(code="Z99")
        other = candidates.ConditionName(code="Z98")
        self.assertEqual(check(absent, condition=asked), ())
        self.assertEqual(check(absent, condition=other),
                         ("scope_violated",))

    def test_concepts_must_be_in_the_vocabulary(self):
        """A named concept the vocabulary lacks does not resolve."""
        notice = candidates.notice("concept_suggested", term="synthetik",
                                   names=(1001,))
        self.assertEqual(check(clarification(notice), {}.get),
                         ("reference_unresolved",))
        self.assertEqual(check(clarification(notice), {1001: "A"}.get), ())

    def test_command(self):
        """The default mode passes on invented data."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(structure.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
