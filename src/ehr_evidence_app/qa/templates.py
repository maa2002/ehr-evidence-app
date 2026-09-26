"""Template candidates for QA responses (work items 34 and 36).

The QA answers, refusals and clarifying questions are written as candidates
(validation.candidate): every fact is a slot filled from the bundle, the
resolution or the interpretation, and every refusal and clarifying question
is an approved notice. The wordings below are the templates' own; a later
writer may reword them around the same slots.
"""

from __future__ import annotations

import collections

from ehr_evidence_app.validation import candidate as candidates

# Wordings by statement kind; a pair is (singular, plural), chosen by the
# count the verb agrees with.
WORDINGS = {
    "list_summary": ("{groups} is recorded within {scope} ({records}):",
                     "{groups} are recorded within {scope} ({records}):"),
    "list_item": "{condition}: {count} in {visits}",
    "list_unmapped": ("{unmapped} of these records has no standard concept "
                      "and is listed by its ICD code.",
                      "{unmapped} of these records have no standard concept "
                      "and are listed by their ICD code."),
    "list_empty": "No conditions are recorded within {scope}.",
    "presence_summary": "{condition} is recorded within {scope}: {matches}.",
    "presence_item": "{condition} ({count} in {visits}; matched as {match})",
    "presence_absent": "{condition} is not recorded within {scope}.",
    "presence_unmapped": ("{unmapped} record in this scope has no standard "
                          "concept; its ICD code and description were "
                          "checked.",
                          "{unmapped} records in this scope have no standard "
                          "concept; their ICD codes and descriptions were "
                          "checked."),
}
MAX_TERM = 200


def wording(kind, number=None):
    """Return a kind's wording, agreeing with a count where it has two."""
    entry = WORDINGS[kind]
    if isinstance(entry, str):
        return entry
    return entry[0] if number == 1 else entry[1]


def statement(kind, number=None, refs=(), **slots):
    """Return a statement of a kind with the template wording."""
    return candidates.Statement(kind=kind, wording=wording(kind, number),
                                slots=candidates.Slots(**slots),
                                refs=tuple(refs))


def candidate(request_id, case, response_scope, outcome, statements,
              task=None, reasons=(), bundle=None):
    """Return a candidate of the given statements for a response in a
    scope."""
    return candidates.Candidate(
        schema_version=candidates.CANDIDATE_VERSION, request_id=request_id,
        task=task, case=case, scope=response_scope, outcome=outcome,
        reason_codes=tuple(reasons), bundle=bundle,
        statements=tuple(statements))


def notice(request_id, case, response_scope, outcome, reason, notice_id,
           task=None, **slots):
    """Return the candidate of one approved notice (a refusal or a
    clarifying question) with its slots."""
    return candidate(request_id, case, response_scope, outcome,
                     [candidates.notice(notice_id, **slots)], task=task,
                     reasons=(reason,))


def term_slot(term):
    """Return the user's condition phrase as it is echoed in a notice."""
    term = " ".join((term or "").split())
    return term if len(term) <= MAX_TERM else term[:MAX_TERM - 3] + "..."


def visit_numbers(bundle, events):
    """Return the sorted visit numbers of a group of events."""
    numbers = {visit.ref: visit.visit_number for visit in bundle.visits}
    return tuple(sorted({numbers[event.visit_ref] for event in events}))


def disclosure(bundle):
    """Return the reason codes a released answer discloses."""
    return (("unmapped_records_in_scope",)
            if bundle.coverage.unmapped_events_in_scope else ())


def list_candidate(request_id, case, scope, bundle, bundle_ref):
    """Return (candidate, group keys) of the list of a completely covered
    scope."""
    if not bundle.events:
        return candidate(request_id, case, scope, "not_recorded", [
            statement("list_empty", refs=("C1",), scope=scope),
            candidates.notice("absence")], task="condition_list",
            reasons=disclosure(bundle), bundle=bundle_ref), ()
    groups = collections.OrderedDict()
    for event in bundle.events:
        groups.setdefault(candidates.record_group(event), []).append(event)
    statements = [statement("list_summary", len(groups), groups=len(groups),
                            scope=scope, records=len(bundle.events))]
    for events in groups.values():
        statements.append(statement(
            "list_item", refs=[event.ref for event in events],
            condition=candidates.ConditionName(event=events[0].ref),
            count=len(events), visits=visit_numbers(bundle, events)))
    unmapped = bundle.coverage.unmapped_events_in_scope
    if unmapped:
        statements.append(statement("list_unmapped", unmapped, refs=("C1",),
                                    unmapped=unmapped))
    statements.append(candidates.notice("list_complete"))
    return candidate(request_id, case, scope, "answer", statements,
                     task="condition_list", reasons=disclosure(bundle),
                     bundle=bundle_ref), tuple(groups)


def resolved_name(resolution):
    """Return how a resolved condition phrase is named: by its ICD code or
    by its concepts."""
    if resolution.rule == "icd_code":
        return candidates.ConditionName(code=resolution.codes[0])
    return candidates.ConditionName(concepts=tuple(resolution.concepts))


def presence_candidate(request_id, case, scope, bundle, bundle_ref,
                       resolution, matches):
    """Return the candidate of a presence check; matches are the (event,
    match kind) pairs of the scope's records."""
    name = resolved_name(resolution)
    if not matches:
        statements = [statement("presence_absent", refs=("C1",),
                                condition=name, scope=scope)]
        unmapped = bundle.coverage.unmapped_events_in_scope
        if unmapped:
            statements.append(statement("presence_unmapped", unmapped,
                                        refs=("C1",), unmapped=unmapped))
        statements.append(candidates.notice("absence"))
        return candidate(request_id, case, scope, "not_recorded", statements,
                         task="condition_presence",
                         reasons=disclosure(bundle), bundle=bundle_ref)
    groups = collections.OrderedDict()
    for event, kind in matches:
        key = (candidates.record_group(event), kind)
        groups.setdefault(key, []).append(event)
    statements = [statement("presence_summary", condition=name, scope=scope,
                            matches=len(matches))]
    for (_, kind), events in groups.items():
        statements.append(statement(
            "presence_item", refs=[event.ref for event in events],
            condition=candidates.ConditionName(event=events[0].ref),
            count=len(events), visits=visit_numbers(bundle, events),
            match=kind))
    return candidate(request_id, case, scope, "answer", statements,
                     task="condition_presence", reasons=disclosure(bundle),
                     bundle=bundle_ref)
