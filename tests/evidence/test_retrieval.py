"""Synthetic checks for ehr_evidence_app.evidence.retrieval and concepts.

The snapshot and case are the bundle builder's invented fixtures, extended
inline; no real record is read.
"""

import dataclasses
import unittest

from ehr_evidence_app.evidence import bundles
from ehr_evidence_app.evidence import concepts as names
from ehr_evidence_app.evidence import readers, retrieval


def fixture():
    """Return an invented (snapshot, case) with three visits."""
    snapshot, case_set, _, _, _ = bundles.synthetic_inputs()
    return snapshot, case_set.cases[0]


def with_events(snapshot, domain, *events):
    """Return the snapshot with extra invented events in one domain."""
    extra = dict(snapshot.events)
    extra[domain] = snapshot.events[domain] + tuple(events)
    return dataclasses.replace(snapshot, events=extra)


def event(domain, native_id, visit_id, concept_id, person_id=77, **values):
    """Return one invented source event."""
    fields = dict(start_date=None, start_datetime=None, end_date=None,
                  end_datetime=None, source_value=None,
                  source_concept_id=None)
    fields.update(values)
    return readers.SourceEvent(domain=domain, native_id=native_id,
                               person_id=person_id,
                               visit_occurrence_id=visit_id,
                               concept_id=concept_id, **fields)


class ScopeTests(unittest.TestCase):
    """Scopes map to graph positions."""

    def test_positions(self):
        """History drops the final visit; a selected visit is one position."""
        _, case = fixture()
        scope = retrieval.scope
        self.assertEqual(retrieval.scope_positions(
            case, scope("pre_target_history")), [0, 1])
        self.assertEqual(retrieval.scope_positions(
            case, scope("selected_visit", 3)), [2])
        self.assertEqual(retrieval.scope_positions(
            case, scope("case_visits")), [0, 1, 2])
        with self.assertRaises(retrieval.ScopeError):
            retrieval.scope_positions(case, scope("selected_visit", 4))
        single = dataclasses.replace(case, visit_ids=(801,))
        with self.assertRaises(retrieval.ScopeError):
            retrieval.scope_positions(single, scope("pre_target_history"))


class RetrieveTests(unittest.TestCase):
    """Scoped retrieval with explicit coverage."""

    def test_history(self):
        """History holds pre-target conditions and counts the rest."""
        snapshot, case = fixture()
        found = retrieval.retrieve(snapshot, case,
                                   retrieval.scope("pre_target_history"))
        self.assertEqual([visit.role for visit in found.visits],
                         ["pre_target", "pre_target"])
        self.assertEqual([item.native_id for item in found.events], [1])
        coverage = found.coverage
        self.assertEqual((coverage.status, coverage.visits_in_scope,
                          coverage.events_in_scope,
                          coverage.excluded_final_admission_events,
                          coverage.events_outside_case_visits),
                         ("complete", 2, 1, 2, 0))

    def test_selected_and_all_visits(self):
        """An empty visit is complete_empty; all visits exclude nothing."""
        snapshot, case = fixture()
        empty = retrieval.retrieve(snapshot, case,
                                   retrieval.scope("selected_visit", 2))
        self.assertEqual((empty.coverage.status, empty.events,
                          empty.visits[0].ref, empty.visits[0].visit_number),
                         ("complete_empty", (), "V1", 2))
        every = retrieval.retrieve(snapshot, case,
                                   retrieval.scope("case_visits"))
        self.assertEqual([item.native_id for item in every.events],
                         [1, 2, 3])
        self.assertEqual(every.visits[-1].role, "final_admission")
        self.assertEqual(every.coverage.excluded_final_admission_events, 0)

    def test_unmapped_records_are_kept_and_disclosed(self):
        """Concept 0 records are delivered with their source code."""
        snapshot, case = fixture()
        snapshot = with_events(snapshot, "drug",
                               event("drug", 8, 801, 0, source_value="RAW"))
        found = retrieval.retrieve(snapshot, case,
                                   retrieval.scope("pre_target_history"),
                                   ("drug",))
        unmapped = [item for item in found.events
                    if item.concept.name_status == "unmapped"]
        self.assertEqual(len(unmapped), 2)
        self.assertEqual(found.coverage.unmapped_events_in_scope, 2)
        self.assertEqual(found.coverage.reason_codes,
                         ("unmapped_records_in_scope",))
        self.assertIn("RAW", [item.concept.source_code for item in unmapped])

    def test_other_people_and_outside_records(self):
        """Other persons are ignored; unplaced records are only counted."""
        snapshot, case = fixture()
        snapshot = with_events(snapshot, "condition",
                               event("condition", 20, 801, 1001,
                                     person_id=78),
                               event("condition", 21, 999, 1001),
                               event("condition", 22, None, 1001))
        found = retrieval.retrieve(snapshot, case,
                                   retrieval.scope("case_visits"))
        self.assertEqual([item.native_id for item in found.events],
                         [1, 2, 3])
        self.assertEqual(found.coverage.events_outside_case_visits, 2)

    def test_ordering(self):
        """Events order by visit, domain, start time, native identifier."""
        snapshot, case = fixture()
        snapshot = with_events(
            snapshot, "condition",
            event("condition", 30, 801, 1001, start_date="2100-01-01"),
            event("condition", 9, 801, 1002,
                  start_datetime="2100-01-01 00:00:00"))
        found = retrieval.retrieve(snapshot, case,
                                   retrieval.scope("selected_visit", 1))
        self.assertEqual([item.native_id for item in found.events],
                         [1, 30, 9])
        self.assertEqual([item.ref for item in found.events],
                         ["E1", "E2", "E3"])

    def test_unavailable_retrieval_delivers_nothing(self):
        """Unadmitted cases, incomplete snapshots and bad scopes end early."""
        snapshot, case = fixture()
        for kase, snap, scope, reason in (
                (dataclasses.replace(case, admitted=False), snapshot,
                 retrieval.scope("case_visits"), "case_not_admitted"),
                (case, dataclasses.replace(snapshot, complete=False),
                 retrieval.scope("case_visits"), "coverage_incomplete"),
                (case, snapshot, retrieval.scope("selected_visit", 9),
                 "scope_invalid"),
                (case, dataclasses.replace(snapshot, visits={}),
                 retrieval.scope("case_visits"), "scope_invalid")):
            found = retrieval.retrieve(snap, kase, scope)
            self.assertEqual((found.visits, found.events,
                              found.coverage.status,
                              found.coverage.reason_codes),
                             ((), (), "unavailable", (reason,)))
        with self.assertRaises(ValueError):
            retrieval.retrieve(snapshot, case, retrieval.scope("case_visits"),
                               ())

    def test_visit_of_another_person_is_refused(self):
        """A case visit held by another person makes the scope invalid."""
        snapshot, case = fixture()
        visits = dict(snapshot.visits)
        visits[801] = dataclasses.replace(visits[801], person_id=78)
        found = retrieval.retrieve(dataclasses.replace(snapshot,
                                                       visits=visits),
                                   case, retrieval.scope("case_visits"))
        self.assertEqual(found.coverage.reason_codes, ("scope_invalid",))


class ConceptNameTests(unittest.TestCase):
    """Names come from the snapshot, then the legacy map; never guessed."""

    ROW = readers.ConceptRow

    def test_sources_in_order(self):
        """The snapshot wins; the legacy map fills gaps; both state it."""
        snapshot = {1: self.ROW(1, "Snapshot", "Condition", "SNOMED"),
                    2: self.ROW(2, None, "Drug", "RxNorm")}
        legacy = {1: self.ROW(1, "Legacy", "Condition", "SNOMED"),
                  2: self.ROW(2, "Legacy two", "Drug", "RxNorm"),
                  3: self.ROW(3, "Legacy three", None, None)}
        first = names.concept(1, snapshot, legacy=legacy)
        second = names.concept(2, snapshot, legacy=legacy)
        third = names.concept(3, snapshot, legacy=legacy)
        self.assertEqual((first.name, first.name_source),
                         ("Snapshot", "source_snapshot"))
        self.assertEqual((second.name, second.name_source),
                         ("Legacy two", "legacy_concept_map"))
        self.assertEqual((third.name, third.domain_id), ("Legacy three",
                                                         None))

    def test_unavailable_and_unmapped(self):
        """Missing names stay unavailable; concept 0 is unmapped."""
        snapshot = {2: self.ROW(2, None, "Drug", "RxNorm")}
        missing = names.concept(2, snapshot)
        self.assertEqual((missing.name_status, missing.name,
                          missing.domain_id), ("unavailable", None, "Drug"))
        self.assertEqual(names.concept(5, {}).name_status, "unavailable")
        unmapped = names.concept(0, snapshot, "S" * 300)
        self.assertEqual((unmapped.name_status, len(unmapped.source_code)),
                         ("unmapped", names.NAME_LIMIT))
        self.assertIsNone(names.concept(0, snapshot).source_code)

    def test_source_concepts(self):
        """A source concept needs a vocabulary and a code; names may be
        unavailable; no source concept gives None."""
        table = {5: self.ROW(5, "Synthetic source", "Condition",
                             "ICD10CM", "Q50.9"),
                 6: self.ROW(6, None, "Condition", "ICD9CM", "999"),
                 7: self.ROW(7, "No code", "Condition", "ICD9CM")}
        found = names.source_concept(5, table)
        self.assertEqual((found.vocabulary_id, found.code, found.name,
                          found.name_status),
                         ("ICD10CM", "Q50.9", "Synthetic source",
                          "available"))
        self.assertEqual(names.source_concept(6, table).name_status,
                         "unavailable")
        for concept_id in (7, 8, 0, None):
            self.assertIsNone(names.source_concept(concept_id, table))

    def test_long_names_are_clipped(self):
        """Names longer than the record limit are clipped, not refused."""
        snapshot = {1: self.ROW(1, "N" * 400, "Condition", "SNOMED")}
        self.assertEqual(len(names.concept(1, snapshot).name),
                         names.NAME_LIMIT)


if __name__ == "__main__":
    unittest.main()
