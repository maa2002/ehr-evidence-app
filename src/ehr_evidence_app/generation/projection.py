"""The model's view of an evidence bundle (Phase 7; ADR 0013, ADR 0014).

The model does not read the canonical bundle: it reads a compact,
deterministic projection of it, structured for its task (projection-v4).
The canonical bundle stays unchanged for provenance and validation, and
the release gate checks the model's candidate against it. The projection
leaves out serialization redundancy and what the task does not use, and
arranges the evidence with the counts and visits derived from it, but
without the outcome of the answer:

* a list: the coverage status, the number of records without a standard
  concept, the numbers of conditions and records, and the scope's records
  grouped by condition (a standard concept, or the ICD source code of
  records without one), in the order of each condition's first record,
  each record as [reference, visit number], each group with its number of
  records and its visit numbers;
* a presence check: the coverage status, the number of records without a
  standard concept, the number of matching records, and the records that
  match the condition asked about, grouped by condition and match kind in
  the same way; the set is empty when no record matches. A record matches
  as the resolver matches it (qa.resolve.match, through
  generation.shape.asked);
* an explanation: the prediction's label, score and logit; the case's
  restrictions, and the approved notice that discloses each; each
  selected driver, highest score first, with its rank,
  reference and attribution score, its linked records in pre-target
  visits as [reference, visit number] with their number and visit
  numbers, and its records in the final admission with their number; and
  how many eligible drivers are not described.

Names, dates, native identifiers, visit and record metadata and provenance
are left out: the model refers to records by their reference, and every
name a response shows is rendered from the canonical bundle. The model
still writes the complete candidate: its outcome, the records each
statement cites, the counts and visits it states, the notices and every
wording. Version 2 gave no counts or visits; the model was withheld for
them alone often enough to meet the test of ADR 0014 (decision 2).
Version 4 gives the notice of each restriction, a fixed lookup the model
got wrong in half the explanations sampled.

answer() writes the correct compact document from the projection alone.
The default and operator modes compare it with the template's document for
every request of the invented fixtures or a development case set, which
shows that the projection holds everything its task needs.

Usage:
    python -m ehr_evidence_app.generation.projection
    python -m ehr_evidence_app.generation.projection --operator-real \\
        --case-set dev-30-v1
"""

from __future__ import annotations

import argparse
import collections
import json
import sys

from ehr_evidence_app.generation import shape

PROJECTION_VERSION = "projection-v4"
RECORD_COLUMNS = ["ref", "visit"]


def source_of(event):
    """Return a record's ICD source code as "VOCABULARY CODE", or None."""
    source = event.source_concept
    if source is not None:
        return "%s %s" % (source.vocabulary_id, source.code)
    return event.concept.source_code


def visit_numbers(bundle):
    """Return the visit number of each visit reference."""
    return {visit.ref: visit.visit_number for visit in bundle.visits}


def condition_of(event):
    """Return the condition a record is of: its standard concept, or its
    ICD source code when it has none."""
    concept = event.concept.concept_id
    return ("concept_id", concept) if concept else ("source",
                                                     source_of(event))


def match_of(event, asked):
    """Return how a record matches the condition asked about, or None: by
    its source code for a question about an ICD code; otherwise by its
    standard concept, or by its source code when it has none."""
    matches = {(entry.by, entry.concept_id if entry.by == "concept" else
                "%s %s" % (entry.vocabulary_id, entry.code)
                if entry.vocabulary_id else entry.code): entry.match
               for entry in asked.counts_as}
    if asked.by == "concept" and event.concept.concept_id:
        return matches.get(("concept", event.concept.concept_id))
    return matches.get(("source_code", source_of(event)))


def grouped(keyed, numbers):
    """Return records grouped by key, in the order of each group's first
    record, as [{key fields..., "records": [[ref, visit], ...], "count":
    their number, "visits": their visit numbers}]."""
    groups = collections.OrderedDict()
    for key, event in keyed:
        groups.setdefault(key, []).append(
            [event.ref, numbers[event.visit_ref]])
    return [dict(key, records=records, count=len(records),
                 visits=sorted({visit for _, visit in records}))
            for key, records in groups.items()]


def qa_view(context):
    """Return the projection of a list or presence request."""
    bundle = context.bundle
    numbers = visit_numbers(bundle)
    view = {"task": context.task,
            "coverage": bundle.coverage.status,
            "records_without_standard_concept":
                bundle.coverage.unmapped_events_in_scope,
            "record_columns": RECORD_COLUMNS}
    if context.asked is None:
        groups = grouped(
            (((condition_of(event),), event) for event in bundle.events),
            numbers)
        view.update(condition_count=len(groups),
                    record_count=len(bundle.events), conditions=groups)
        return view
    matching = [(event, match_of(event, context.asked))
                for event in bundle.events]
    groups = grouped(
        (((condition_of(event), ("match", match)), event)
         for event, match in matching if match), numbers)
    view.update(matching_count=sum(group["count"] for group in groups),
                matching=groups)
    return view


def driver_block(rank, item, history, final, numbers):
    """Return a selected driver's block: its rank, reference and score,
    its linked records in pre-target visits with their number and visit
    numbers, and its records in the final admission with their number."""
    past = [[ref, numbers[history[ref].visit_ref]]
            for ref in item.linked_event_refs if ref in history]
    context = [ref for ref in item.linked_event_refs if ref in final]
    return {"rank": rank, "ref": item.ref, "score": item.score,
            "history": past, "history_count": len(past),
            "history_visits": sorted({visit for _, visit in past}),
            "context": context, "context_count": len(context)}


def explanation_view(context):
    """Return the projection of an explanation request."""
    bundle = context.bundle
    numbers = visit_numbers(bundle)
    history = {event.ref: event for event in bundle.history_events}
    final = {event.ref for event in bundle.final_admission_context}
    prediction = bundle.prediction
    return {"task": context.task,
            "prediction": {"label": prediction.concept.concept_id,
                           "score": prediction.score,
                           "logit": prediction.logit},
            "restrictions": list(bundle.restrictions),
            "restriction_notices": [RESTRICTION_NOTICES[code]
                                    for code in bundle.restrictions],
            "drivers": [driver_block(rank, item, history, final, numbers)
                        for rank, item in enumerate(
                            shape.selected_drivers(context), 1)],
            "drivers_not_described": shape.omitted_drivers(context)}


def view(context):
    """Return the model's view of a request's bundle."""
    if context.task == "prediction_explanation":
        return explanation_view(context)
    return qa_view(context)


def text(projection):
    """Return a projection as compact JSON text."""
    return json.dumps(projection, separators=(",", ":"), ensure_ascii=False)


# --------------------------------------------------------------------------
# The correct document from the projection alone
# --------------------------------------------------------------------------

RESTRICTION_NOTICES = {"batch_sensitive_prediction": "batch_sensitive",
                       "target_attached_drivers_present": "target_attached",
                       "target_outcome_entries_excluded": "target_excluded"}


def wording(kind, number=None):
    """Return the template's wording of a kind."""
    from ehr_evidence_app.qa import templates
    from ehr_evidence_app.validation import explanation
    if kind in templates.WORDINGS:
        return templates.wording(kind, number)
    return explanation.WORDINGS[kind]


def item(group):
    """Return the compact item of a group of records: its first record, for
    a presence check its match kind, its records, and the visits and count
    the view gives."""
    records = group["records"]
    values = {"condition": records[0][0],
              "refs": [record[0] for record in records],
              "visits": group["visits"], "count": group["count"]}
    if "match" not in group:
        return shape.row_of(shape.LIST_ROW, **values)
    return shape.row_of(shape.PRESENCE_ROW, match=group["match"], **values)


def unmapped_line(projection, kind):
    """Return the line on records without a standard concept, or None."""
    number = projection["records_without_standard_concept"]
    return {"wording": wording(kind, number), "unmapped": number} \
        if number else None


def reason_codes(projection):
    """Return the disclosure a scope's records without a standard concept
    call for."""
    return (["unmapped_records_in_scope"]
            if projection["records_without_standard_concept"] else [])


def list_answer(projection):
    """Return the correct list document of a projection."""
    groups = projection["conditions"]
    if not groups:
        return {"outcome": "not_recorded", "reason_codes":
                reason_codes(projection),
                "empty": {"wording": wording("list_empty")},
                "notices": ["absence"]}
    return {"outcome": "answer", "reason_codes": reason_codes(projection),
            "item_wording": wording("list_item"),
            "items": [item(group) for group in groups],
            "summary": {"wording": wording("list_summary",
                                           projection["condition_count"]),
                        "groups": projection["condition_count"],
                        "records": projection["record_count"]},
            "unmapped": unmapped_line(projection, "list_unmapped"),
            "notices": ["list_complete"]}


def presence_answer(projection):
    """Return the correct presence document of a projection."""
    groups = projection["matching"]
    if not groups:
        return {"outcome": "not_recorded",
                "reason_codes": reason_codes(projection),
                "absent": {"wording": wording("presence_absent")},
                "unmapped": unmapped_line(projection, "presence_unmapped"),
                "notices": ["absence"]}
    return {"outcome": "answer", "reason_codes": reason_codes(projection),
            "item_wording": wording("presence_item"),
            "items": [item(group) for group in groups],
            "summary": {"wording": wording("presence_summary"),
                        "matches": projection["matching_count"]}}


def explanation_answer(projection):
    """Return the correct explanation document of a projection."""
    drivers = []
    for driver in projection["drivers"]:
        history, final = driver["history"], driver["context"]
        drivers.append(shape.row_of(
            shape.DRIVER_ROW, driver=driver["ref"], rank=driver["rank"],
            score=driver["score"],
            history=shape.row_of(
                shape.HISTORY_ROW, refs=[record for record, _ in history],
                visits=driver["history_visits"],
                count=driver["history_count"]) if history else None,
            context=shape.row_of(shape.CONTEXT_ROW, refs=list(final),
                                 count=driver["context_count"])
            if final else None))
    left = projection["drivers_not_described"]
    prediction = projection["prediction"]
    return {"outcome": "explanation",
            "reason_codes": projection["restrictions"],
            "prediction": {"wording": wording("prediction"),
                           "label": prediction["label"],
                           "score": prediction["score"],
                           "logit": prediction["logit"]},
            "driver_wording": wording("driver"),
            "history_wording": wording("driver_history"),
            "context_wording": wording("driver_context"),
            "drivers": drivers,
            "notices": ["drivers"] + (["drivers_omitted"] if left else [])
            + ["attribution_meaning", "score_meaning"]
            + projection["restriction_notices"],
            "omitted": left or None}


def answer(projection):
    """Return the correct compact document of a projection."""
    if projection["task"] == "condition_list":
        return list_answer(projection)
    if projection["task"] == "condition_presence":
        return presence_answer(projection)
    return explanation_answer(projection)


# --------------------------------------------------------------------------
# Default and operator modes
# --------------------------------------------------------------------------

def compare(pairs):
    """Return (requests, answered from the projection alone, characters of
    the bundles, characters of the projections)."""
    answered = bundle_chars = view_chars = 0
    for context, template in pairs:
        projection = view(context)
        document = json.loads(shape.text(shape.document_of(template)))
        answered += answer(json.loads(text(projection))) == document
        bundle_chars += len(context.bundle.model_dump_json(exclude_none=True))
        view_chars += len(text(projection))
    return len(pairs), answered, bundle_chars, view_chars


def main(argv=None):
    """Answer every invented request, or every request of a development
    case set, from its projection alone; prints counts only."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="use a development case set; prints aggregate "
                             "counts only")
    parser.add_argument("--case-set",
                        help="the development case set (dev-30-v1 or "
                             "pilot-10-v1)")
    args = parser.parse_args(argv)
    if args.operator_real != bool(args.case_set):
        print("FAILED: --operator-real and --case-set go together")
        return 2
    if args.operator_real:
        from ehr_evidence_app.generation import adapter
        try:
            pairs, _ = adapter.development_requests(args.case_set)
        except Exception as error:  # noqa: BLE001 - type name only
            print("FAILED: %s" % type(error).__name__)
            return 1
    else:
        pairs = shape.synthetic_contexts()
    total, answered, bundle_chars, view_chars = compare(pairs)
    print("requests answered correctly from their projection alone: %d of "
          "%d" % (answered, total))
    print("characters: bundles %d, projections %d (%.0f%%)" % (
        bundle_chars, view_chars, 100.0 * view_chars / max(bundle_chars, 1)))
    print("projection %s" % PROJECTION_VERSION)
    ok = answered == total
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
