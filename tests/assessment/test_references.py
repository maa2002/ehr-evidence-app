"""Synthetic checks for ehr_evidence_app.assessment.references.

The sources are the invented case set the invented bundles are built
from; each test changes one source record and checks that the references
follow it, or that a broken record is refused.
"""

import copy
import dataclasses
import unittest

from ehr_evidence_app.assessment import metrics, references
from ehr_evidence_app.evidence import bundles, contracts
from ehr_evidence_app.generation import shape
from ehr_evidence_app.qa import suite

PAIRS = shape.synthetic_contexts()


def sources(snapshot=None, case=None, record=None, linked=None):
    """Return the invented Sources, with any part replaced."""
    original, cases, outputs, link, _ = bundles.synthetic_inputs()
    if case is not None:
        cases = dataclasses.replace(cases, cases=(case,))
    if record is not None:
        outputs = {record["patient_index"]: record}
    return references.Sources(cases.label, snapshot or original, cases,
                              suite.synthetic_vocabulary(), outputs,
                              linked if linked is not None else link)


def the_case():
    """Return the invented case."""
    return bundles.synthetic_inputs()[1].cases[0]


class ComparisonTests(unittest.TestCase):
    """The invented templates against their references."""

    def test_templates_are_right(self):
        """Every invented template, which passes the release gate, scores
        as a correct response against its reference."""
        counts, problems = references.run_synthetic()
        self.assertEqual(problems, [])
        self.assertEqual(sum(counts.values()), len(PAIRS))

    def test_a_missing_record_is_found(self):
        """Without one condition record in the snapshot, the template
        lists and presence checks that state it no longer match their
        references. (Its explanation's reference is refused instead: a
        driver's linked record must be in the snapshot.)"""
        snapshot = bundles.synthetic_inputs()[0]
        events = dict(snapshot.events)
        events["condition"] = events["condition"][1:]
        counts = references.compare(
            sources(snapshot=dataclasses.replace(snapshot, events=events)),
            [pair for pair in PAIRS
             if pair[0].task != metrics.EXPLANATION],
            references.configured_limit())
        wrong = sum(count for (_, verdict), count in counts.items()
                    if verdict != "right")
        self.assertGreater(wrong, 0)

    def test_output_record_is_bound(self):
        """An output record that is not the one the closure record names
        by its digest is refused; one the closure names, but the linkage
        record does not, is refused too."""
        record = copy.deepcopy(bundles.synthetic_inputs()[2][
            the_case().patient_index])
        record["predicted_diagnosis"]["probability"] = 0.5
        with self.assertRaisesRegex(references.ReferenceError,
                                    "output_record_mismatch"):
            sources(record=record).explanation("x", 1, 5)
        case = dataclasses.replace(
            the_case(),
            output_record_sha256=references.amp.digest(record))
        with self.assertRaisesRegex(references.ReferenceError,
                                    "linkage_case_mismatch"):
            sources(case=case, record=record).explanation("x", 1, 5)


class ExplanationTests(unittest.TestCase):
    """The expected explanation, from the published records."""

    def test_selection(self):
        """The eligible drivers by score, highest first: the drug (0.5),
        the condition (0.25) and the procedure (0.125); the zero-score
        target-outcome condition is left out. With a limit of one, two are
        not described."""
        found = sources().explanation("x", 1, 5)
        self.assertEqual(found.outcome, "explanation")
        self.assertEqual([(driver.rank, driver.score)
                          for driver in found.drivers],
                         [(1, 0.5), (2, 0.25), (3, 0.125)])
        self.assertEqual(found.omitted, 0)
        self.assertEqual(found.prediction, (9001, 0.8175744761936437, 1.5))
        self.assertEqual(found.restrictions, frozenset((
            "target_attached_drivers_present",
            "target_outcome_entries_excluded")))
        one = sources().explanation("x", 1, 1)
        self.assertEqual(len(one.drivers), 1)
        self.assertEqual(one.omitted, 2)

    def test_history_and_context(self):
        """History holds records of pre-target visits only; context holds
        no diagnosis of the final admission."""
        found = sources().explanation("x", 1, 5)
        case = the_case()
        final = case.visit_ids[-1]
        events = sources().events
        for driver in found.drivers:
            for key in driver.history:
                self.assertNotEqual(events[key[1:]].visit_occurrence_id,
                                    final)
            for key in driver.context:
                self.assertEqual(events[key[1:]].visit_occurrence_id, final)
                self.assertNotEqual(key[1], "condition")

    def test_case_not_admitted_or_ineligible(self):
        """A case not admitted expects a case rejection; an admitted case
        without an eligible driver an abstention."""
        rejected = sources(case=dataclasses.replace(
            the_case(), admitted=False)).explanation("x", 1, 5)
        self.assertEqual(rejected.outcome, "case_rejection")
        case = the_case()
        drivers = {domain: tuple(dataclasses.replace(
            tag, narration_eligible=False) for tag in tags)
            for domain, tags in case.drivers.items()}
        ineligible = sources(case=dataclasses.replace(
            case, drivers=drivers, explanation_eligible=False))
        found = ineligible.explanation("x", 1, 5)
        self.assertEqual((found.outcome, found.drivers, found.prediction),
                         ("abstention", (), None))

    def test_broken_records_are_refused(self):
        """A linked record of another concept, an eligibility that
        contradicts the closure, and a linkage record of another kind stop
        the reference."""
        linked = copy.deepcopy(bundles.synthetic_inputs()[3])
        entry = linked["cases"][0]["domains"]["drug"]["drivers"][0]
        moved = [visit for visit in entry["visits"] if visit["event_ids"]]
        moved[0]["event_ids"] = [999999]
        with self.assertRaisesRegex(references.ReferenceError,
                                    "linked_record_mismatch"):
            sources(linked=linked).explanation("x", 1, 5)
        with self.assertRaisesRegex(references.ReferenceError,
                                    "explanation_eligibility_mismatch"):
            sources(case=dataclasses.replace(
                the_case(), explanation_eligible=False)).explanation(
                "x", 1, 5)
        with self.assertRaisesRegex(references.ReferenceError,
                                    "linkage_not_verified"):
            sources(linked={"schema": "other"})


class ConditionTests(unittest.TestCase):
    """The expected list and presence answers, from the oracle."""

    def test_list_and_presence(self):
        """A list expects every record of the scope; a presence check of a
        recorded concept expects its records as exact matches, and leaves
        records without a standard concept unjudged."""
        found = sources()
        case = found.cases.cases[0]
        scope = contracts.ScopeRef(kind="case_visits")
        listed = found.condition("l", "condition_list", 1, scope)
        self.assertEqual(listed.outcome, "answer")
        self.assertEqual(set(listed.names), set().union(*listed.groups.values()))
        concept = next(name[1] for name in listed.groups
                       if name[0] == "concept")
        present = found.condition("p", "condition_presence", 1, scope,
                                  (concept,))
        self.assertEqual(present.outcome, "answer")
        self.assertIn("exact", present.matches.values())
        self.assertEqual(present.unjudged, frozenset(
            key for key, name in listed.names.items()
            if name[0] != "concept"))
        self.assertTrue(present.source_answerable)
        self.assertEqual(case.case_number, 1)

    def test_presence_needs_a_condition(self):
        """A presence reference without a condition is refused, and so is
        a task that is not about conditions."""
        found = sources()
        scope = contracts.ScopeRef(kind="case_visits")
        with self.assertRaisesRegex(references.ReferenceError,
                                    "presence_without_condition"):
            found.condition("p", "condition_presence", 1, scope)
        with self.assertRaisesRegex(references.ReferenceError,
                                    "task_not_conditions"):
            found.condition("p", metrics.EXPLANATION, 1, scope)

    def test_configured_limit(self):
        """The frozen limit is read from config/explanation.json."""
        self.assertEqual(references.configured_limit(), 5)

    def test_development_only(self):
        """The comparison refuses the evaluation set before reading
        anything."""
        with self.assertRaisesRegex(references.ReferenceError,
                                    "case_set_held_out"):
            references.run_compare("eval-100-v1")


if __name__ == "__main__":
    unittest.main()
