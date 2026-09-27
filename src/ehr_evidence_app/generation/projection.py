"""The model's view of an evidence bundle (Phase 7; ADR 0013).

The model does not read the canonical bundle: it reads a compact,
deterministic projection of it (projection-v1). The canonical bundle stays
unchanged for provenance and validation, and the release gate checks the
model's candidate against it. The projection removes only serialization
redundancy and what the task does not use:

* a list or presence check: each record of the scope as a row of its
  reference, visit number, standard concept (0 when it has none) and ICD
  source code; the coverage status and the number of records without a
  standard concept; for a presence check, which concepts or source codes
  count as the condition asked about, and how. Names, dates, native
  identifiers, visit and record metadata and provenance are left out: the
  model refers to records by their reference, and every name and fact it
  states is rendered from the canonical bundle;
* an explanation: the prediction's label, score and logit; the case's
  restrictions; the selected drivers, highest score first, each with its
  reference, score, and linked records in pre-target visits (reference
  and visit number) and in the final admission (reference); and how many
  eligible drivers are not described.

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

PROJECTION_VERSION = "projection-v1"
RECORD_COLUMNS = ["ref", "visit", "concept_id", "source"]
DRIVER_COLUMNS = ["ref", "score", "history", "context"]


def source_of(event):
    """Return a record's ICD source code as "VOCABULARY CODE", or None."""
    source = event.source_concept
    if source is not None:
        return "%s %s" % (source.vocabulary_id, source.code)
    return event.concept.source_code


def visit_numbers(bundle):
    """Return the visit number of each visit reference."""
    return {visit.ref: visit.visit_number for visit in bundle.visits}


def qa_view(context):
    """Return the projection of a list or presence request."""
    bundle = context.bundle
    numbers = visit_numbers(bundle)
    view = {"task": context.task,
            "coverage": bundle.coverage.status,
            "records_without_standard_concept":
                bundle.coverage.unmapped_events_in_scope,
            "columns": RECORD_COLUMNS,
            "records": [[event.ref, numbers[event.visit_ref],
                         event.concept.concept_id, source_of(event)]
                        for event in bundle.events]}
    if context.asked is not None:
        view["matched_by"] = context.asked.by
        view["counts_as"] = [
            ["concept_id", entry.concept_id, entry.match]
            if entry.by == "concept" else
            ["source", "%s %s" % (entry.vocabulary_id, entry.code)
             if entry.vocabulary_id else entry.code, entry.match]
            for entry in context.asked.counts_as]
    return view


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
            "columns": DRIVER_COLUMNS,
            "drivers": [[item.ref, item.score,
                         [[ref, numbers[history[ref].visit_ref]]
                          for ref in item.linked_event_refs
                          if ref in history],
                         [ref for ref in item.linked_event_refs
                          if ref in final]]
                        for item in shape.selected_drivers(context)],
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


def condition_of(record):
    """Return the condition a record is of: its standard concept, or its
    source code when it has none."""
    ref, _, concept, source = record
    return ("concept", concept) if concept else ("source", source)


def grouped(records):
    """Return records grouped by key, in the order of their first record."""
    groups = collections.OrderedDict()
    for key, record in records:
        groups.setdefault(key, []).append(record)
    return groups


def item(records, match=None):
    """Return a compact item: its first record, for a presence check its
    match kind, its records, their visits and their count."""
    values = {"condition": records[0][0],
              "refs": [record[0] for record in records],
              "visits": sorted({record[1] for record in records}),
              "count": len(records)}
    if match is None:
        return shape.row_of(shape.LIST_ROW, **values)
    return shape.row_of(shape.PRESENCE_ROW, match=match, **values)


def unmapped_line(projection, kind):
    """Return the line on records without a standard concept, or None."""
    number = projection["records_without_standard_concept"]
    return {"wording": wording(kind, number), "unmapped": number} \
        if number else None


def list_answer(projection):
    """Return the correct list document of a projection."""
    records = projection["records"]
    codes = (["unmapped_records_in_scope"]
             if projection["records_without_standard_concept"] else [])
    if not records:
        return {"outcome": "not_recorded", "reason_codes": codes,
                "empty": {"wording": wording("list_empty")},
                "notices": ["absence"]}
    groups = grouped((condition_of(record), record) for record in records)
    return {"outcome": "answer", "reason_codes": codes,
            "item_wording": wording("list_item"),
            "items": [item(group) for group in groups.values()],
            "summary": {"wording": wording("list_summary", len(groups)),
                        "groups": len(groups), "records": len(records)},
            "unmapped": unmapped_line(projection, "list_unmapped"),
            "notices": ["list_complete"]}


def match_of(record, counts_as, matched_by):
    """Return how a record matches the condition asked about, or None: by
    its source code for a question about an ICD code; otherwise by its
    standard concept, or by its source code when it has none."""
    _, _, concept, source = record
    if matched_by == "concept" and concept:
        return counts_as.get(("concept_id", concept))
    return counts_as.get(("source", source))


def presence_answer(projection):
    """Return the correct presence document of a projection."""
    counts_as = {(by, value): match
                 for by, value, match in projection["counts_as"]}
    codes = (["unmapped_records_in_scope"]
             if projection["records_without_standard_concept"] else [])
    matching = [(record, match_of(record, counts_as,
                                  projection["matched_by"]))
                for record in projection["records"]]
    matching = [(record, match) for record, match in matching if match]
    if not matching:
        return {"outcome": "not_recorded", "reason_codes": codes,
                "absent": {"wording": wording("presence_absent")},
                "unmapped": unmapped_line(projection, "presence_unmapped"),
                "notices": ["absence"]}
    groups = grouped(((condition_of(record), match), record)
                     for record, match in matching)
    return {"outcome": "answer", "reason_codes": codes,
            "item_wording": wording("presence_item"),
            "items": [item(records, match)
                      for (_, match), records in groups.items()],
            "summary": {"wording": wording("presence_summary"),
                        "matches": len(matching)}}


def explanation_answer(projection):
    """Return the correct explanation document of a projection."""
    drivers = []
    for rank, (ref, score, history, final) in enumerate(
            projection["drivers"], 1):
        drivers.append(shape.row_of(
            shape.DRIVER_ROW, driver=ref, rank=rank, score=score,
            history=shape.row_of(
                shape.HISTORY_ROW, refs=[record for record, _ in history],
                visits=sorted({number for _, number in history}),
                count=len(history)) if history else None,
            context=shape.row_of(shape.CONTEXT_ROW, refs=list(final),
                                 count=len(final)) if final else None))
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
            + [RESTRICTION_NOTICES[code]
               for code in projection["restrictions"]],
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
