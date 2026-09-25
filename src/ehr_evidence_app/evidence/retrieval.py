"""Scoped retrieval with explicit coverage (work item 27).

A scope is one of:

* pre_target_history: the case visits before the final admission;
* selected_visit: one case visit, numbered from 1 in graph order;
* case_visits: every case visit, including the final admission (QA only).

Retrieval returns the scope's visits, every recorded event of the requested
domains in those visits (repeated records stay separate), and a coverage
record. Coverage is complete (or complete and empty) only for an admitted
case read from a complete snapshot; otherwise it is unavailable with a
reason, and nothing is delivered. It also counts unmapped records in scope,
final-admission records a history scope excludes, and any records of the
person that no case visit holds (these are never delivered).
"""

from __future__ import annotations

import dataclasses

from ehr_evidence_app.evidence import concepts as names
from ehr_evidence_app.evidence import contracts, schema
from ehr_evidence_app.evidence import vocabulary as vocab


class ScopeError(Exception):
    """The requested scope does not exist for this case."""


@dataclasses.dataclass(frozen=True)
class Retrieval:
    """The visits, events and coverage for one case and scope."""

    visits: tuple
    events: tuple
    coverage: schema.Coverage


def scope_positions(case, scope):
    """Return the graph positions a scope covers for a case."""
    count = len(case.visit_ids)
    final = count - 1
    if scope.kind == "pre_target_history":
        if count < 2:
            raise ScopeError("the case has no pre-target visit")
        return list(range(final))
    if scope.kind == "selected_visit":
        if scope.visit_number > count:
            raise ScopeError("the case has no such visit")
        return [scope.visit_number - 1]
    return list(range(count))


def visit_record(case, snapshot, position, ref):
    """Return the Visit record for one graph position."""
    visit_id = case.visit_ids[position]
    source = snapshot.visits.get(visit_id)
    if source is None or source.person_id != case.person_id:
        raise ScopeError("a case visit is missing from the snapshot")
    return schema.Visit(
        ref=ref, visit_occurrence_id=visit_id, position=position,
        visit_number=position + 1,
        role=("final_admission" if position == len(case.visit_ids) - 1
              else "pre_target"),
        visit_concept_id=source.visit_concept_id,
        start_date=source.start_date, end_date=source.end_date)


def event_sort_key(event, positions):
    """Order events by visit, domain, start time and native identifier."""
    return (positions[event.visit_occurrence_id],
            vocab.DOMAINS.index(event.domain),
            event.start_datetime or event.start_date or "",
            event.native_id)


def event_record(event, ref, visit_ref, concept_table):
    """Return the Event record for one source row."""
    return schema.Event(
        ref=ref, domain=event.domain,
        source_table=vocab.SOURCE_TABLES[event.domain],
        native_id=event.native_id, visit_ref=visit_ref,
        concept=names.concept(event.concept_id, concept_table,
                              event.source_value),
        start_date=event.start_date, start_datetime=event.start_datetime,
        end_date=event.end_date, end_datetime=event.end_datetime)


def unavailable(scope, domains, snapshot, reason):
    """Return an unavailable retrieval that delivers nothing."""
    coverage = schema.Coverage(
        scope=scope, status="unavailable", domains=tuple(domains),
        source_snapshot=snapshot.label, visits_in_scope=0,
        events_in_scope=0, unmapped_events_in_scope=0,
        excluded_final_admission_events=0, events_outside_case_visits=0,
        reason_codes=(reason,))
    return Retrieval(visits=(), events=(), coverage=coverage)


def retrieve(snapshot, case, scope, domains=("condition",)):
    """Return the scoped visits, events and coverage for one case."""
    domains = tuple(domain for domain in vocab.DOMAINS if domain in domains)
    if not domains:
        raise ValueError("no domain requested")
    if not case.admitted:
        return unavailable(scope, domains, snapshot, "case_not_admitted")
    if not snapshot.complete:
        return unavailable(scope, domains, snapshot, "coverage_incomplete")
    try:
        positions = scope_positions(case, scope)
        visits = tuple(visit_record(case, snapshot, position,
                                    "V%d" % (index + 1))
                       for index, position in enumerate(positions))
    except ScopeError:
        return unavailable(scope, domains, snapshot, "scope_invalid")
    by_visit = {visit.visit_occurrence_id: visit for visit in visits}
    all_positions = {visit_id: position
                     for position, visit_id in enumerate(case.visit_ids)}
    final_visit = case.visit_ids[-1]
    selected, excluded, outside = [], 0, 0
    for domain in domains:
        for event in snapshot.events[domain]:
            if event.person_id != case.person_id:
                continue
            if event.visit_occurrence_id not in all_positions:
                outside += 1
            elif event.visit_occurrence_id in by_visit:
                selected.append(event)
            elif event.visit_occurrence_id == final_visit:
                excluded += 1
    selected.sort(key=lambda item: event_sort_key(item, all_positions))
    events = tuple(
        event_record(event, "E%d" % (index + 1),
                     by_visit[event.visit_occurrence_id].ref,
                     snapshot.concepts)
        for index, event in enumerate(selected))
    unmapped = sum(event.concept.name_status == "unmapped"
                   for event in events)
    reasons = ("unmapped_records_in_scope",) if unmapped else ()
    coverage = schema.Coverage(
        scope=scope, status="complete" if events else "complete_empty",
        domains=domains, source_snapshot=snapshot.label,
        visits_in_scope=len(visits), events_in_scope=len(events),
        unmapped_events_in_scope=unmapped,
        excluded_final_admission_events=(
            excluded if scope.kind == "pre_target_history" else 0),
        events_outside_case_visits=outside, reason_codes=reasons)
    return Retrieval(visits=visits, events=events, coverage=coverage)


def scope(kind, visit_number=None):
    """Return a ScopeRef."""
    return contracts.ScopeRef(kind=kind, visit_number=visit_number)
