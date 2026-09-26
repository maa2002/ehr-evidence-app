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


def check(candidate, concept_name=None):
    """Check a candidate for the invented request and bundle."""
    return structure.check(candidate, "q-1", GOOD.case, GOOD.scope, BUNDLE,
                           DIGEST, concept_name)


def clarification(*statements, **fields):
    """Return an invented clarification candidate."""
    values = dict(schema_version="1.0", request_id="q-1", case=GOOD.case,
                  scope=GOOD.scope, outcome="clarification",
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
        """Wording, references, composition, reasons, case and bundle."""
        for expected, variant in structure.synthetic_variants(GOOD):
            self.assertIn(expected, check(variant), expected)

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
        self.assertEqual(check(clarification(notice, bundle=GOOD.bundle)),
                         ("structure_invalid",))
        cited = notice.model_copy(update={"refs": ("C1",)})
        self.assertIn("structure_invalid", check(clarification(cited)))

    def test_scope_slots_name_the_candidates_scope(self):
        """A statement about another scope is a scope violation."""
        items = list(GOOD.statements)
        items[0] = items[0].model_copy(update={
            "slots": items[0].slots.model_copy(update={"scope": HISTORY})})
        self.assertEqual(check(GOOD.model_copy(update={
            "statements": tuple(items)})), ("scope_violated",))

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
