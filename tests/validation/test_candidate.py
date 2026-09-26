"""Synthetic checks for ehr_evidence_app.validation.candidate.

Candidates are invented and rendered against the bundle builder's invented
bundles; no real record is read.
"""

import unittest

from pydantic import ValidationError

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import structure

BUNDLE, DIGEST = structure.synthetic_bundle()
GOOD = structure.synthetic_list(BUNDLE, DIGEST)
HISTORY = contracts.ScopeRef(kind="pre_target_history")
NAMES = {1001: "Synthetic concept 1001", 1002: "Synthetic concept 1002"}


class ContractTests(unittest.TestCase):
    """The candidate contract keeps facts in typed slots."""

    def test_synthetic_documents(self):
        """Invented documents are accepted or refused as intended."""
        self.assertEqual(candidates.run_checks(), [])

    def test_a_condition_is_named_one_way(self):
        """An event, concepts or a code; never two, never none."""
        with self.assertRaises(ValidationError):
            candidates.ConditionName(event="E1", code="I509")
        with self.assertRaises(ValidationError):
            candidates.ConditionName()
        self.assertEqual(candidates.ConditionName(concepts=(1001,)).concepts,
                         (1001,))

    def test_filled_slots(self):
        """Only slots that hold a value count as filled."""
        slots = candidates.Slots(count=2, visits=(1, 3))
        self.assertEqual(slots.filled(), ("count", "visits"))

    def test_notices_use_known_slots(self):
        """Every placeholder of an approved notice is a slot."""
        for notice_id, notice in candidates.NOTICES.items():
            for name in candidates.placeholders(notice.text):
                self.assertIn(name, candidates.Slots.model_fields, notice_id)


class RenderTests(unittest.TestCase):
    """Text and claims are rendered from the statements only."""

    def test_list(self):
        """Summary, items and the completeness notice, one per line."""
        text, claims = candidates.render(GOOD, BUNDLE)
        lines = text.split("\n")
        self.assertEqual(lines[0], "2 distinct conditions are recorded within "
                                   "all case visits, including the final "
                                   "admission (3 records):")
        self.assertTrue(lines[1].startswith("- SYNTHETIC CONCEPT 1001: 2 "
                                            "records in visit"))
        self.assertEqual(lines[-1], candidates.NOTICES["list_complete"].text)
        self.assertEqual([claim.refs for claim in claims],
                         [("E1", "E2"), ("E3",)])
        self.assertEqual(claims[0].text, lines[1][2:] + ".")

    def test_facts_come_from_the_evidence(self):
        """The wording cannot change a fact: the name is the bundle's."""
        item = GOOD.statements[1].model_copy(update={
            "wording": "Recorded: {condition} ({count} in {visits})"})
        text = candidates.sentence(item, candidates.Lookup(BUNDLE))
        self.assertTrue(text.startswith("Recorded: SYNTHETIC CONCEPT 1001 "
                                        "(2 records in visit"))

    def test_concepts_and_codes(self):
        """Resolved concepts take their vocabulary names; codes show as
        codes; an unknown concept cannot be rendered."""
        statement = candidates.Statement(
            kind="presence_absent",
            wording="{condition} is not recorded within {scope}.",
            slots=candidates.Slots(
                condition=candidates.ConditionName(concepts=(1001, 1002)),
                scope=HISTORY), refs=("C1",))
        self.assertEqual(candidates.sentence(
            statement, candidates.Lookup(None, NAMES.get)),
                         "Synthetic concept 1001; Synthetic concept 1002 is "
                         "not recorded within the pre-target history (the "
                         "visits before the final admission).")
        coded = statement.model_copy(update={"slots": candidates.Slots(
            condition=candidates.ConditionName(code="I509"), scope=HISTORY)})
        self.assertTrue(candidates.sentence(
            coded, candidates.Lookup()).startswith(
            "ICD code I509 is not recorded"))
        with self.assertRaisesRegex(candidates.CandidateError,
                                    "concept_unknown_to_vocabulary"):
            candidates.sentence(statement, candidates.Lookup())

    def test_long_references_are_split(self):
        """At most 50 references per claim, with the range in the text."""
        refs = ["E%d" % number for number in range(1, 121)]
        claims = candidates.claims_of("X: 120 records in visit 1", refs)
        self.assertEqual([len(claim.refs) for claim in claims], [50, 50, 20])
        self.assertEqual(claims[2].text,
                         "X: 120 records in visit 1 (records 101 to 120 of "
                         "120).")

    def test_words(self):
        """Scopes, visit lists and counts read naturally."""
        self.assertEqual(candidates.scope_words(contracts.ScopeRef(
            kind="selected_visit", visit_number=2)), "visit 2")
        self.assertEqual(candidates.join_visits((3, 1, 2)),
                         "visits 1, 2 and 3")
        self.assertEqual(candidates.counted("groups", 1),
                         "1 distinct condition")
        self.assertEqual(candidates.counted("unmapped", 4), "4")

    def test_response(self):
        """The released response carries the candidate's outcome, bundle,
        text and claims."""
        response = candidates.response(GOOD, BUNDLE)
        self.assertEqual((response.outcome, response.bundle, response.task),
                         ("answer", GOOD.bundle, "condition_list"))
        self.assertEqual(len(response.claims), 2)


if __name__ == "__main__":
    unittest.main()
