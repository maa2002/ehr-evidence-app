"""What a condition wrote, read for the metrics (Phase 9; ADR 0028).

Each condition's response to a request is read into the plain form of
assessment.metrics: its outcome, whether it has text, and its statements,
each with its facts and with its cited references read as what they name
in the request's bundle. Nothing is judged here; this is a reading of
identifiers, and the metrics judge it against the reference.

* An event (E...) is read as ("record", domain, native identifier), the
  coverage record (C1) as ("coverage",), the prediction (P1) as
  ("prediction",) and an attribution (A...) as ("attribution",
  concept). A reference the bundle does not hold is read as None: the
  baseline's candidates are not checked, so its documents may cite
  records that do not exist.
* A condition that a statement names by one of its records is that
  record's standard concept, ("concept", id). For a record without one
  it is the record's source code, ("source", vocabulary, code), or its
  recorded source value, ("value", text), when it has no source code. A
  condition named by concepts is ("concept", id) for a single concept;
  any other name is read as it is written.
* A notice of a restriction is read as "restriction:" and the
  restriction, and the notice of the drivers not described carries their
  number.

A response that was withheld or failed has no statements: it states
nothing.

The default mode reads the invented fixtures' template candidates.

Usage:
    python -m ehr_evidence_app.assessment.stated
"""

from __future__ import annotations

import argparse
import sys

from ehr_evidence_app.assessment import metrics
from ehr_evidence_app.validation import candidate as candidates

RESTRICTION_OF_NOTICE = {notice: restriction for restriction, notice
                         in candidates.RESTRICTION_NOTICES.items()}


def events_of(bundle):
    """Return the events of a QA or explanation bundle."""
    if getattr(bundle, "kind", None) == "explanation":
        return tuple(bundle.history_events) + tuple(
            bundle.final_admission_context)
    return tuple(getattr(bundle, "events", ()))


def record_key(event):
    """Return what an event names: its record."""
    return ("record", event.domain, event.native_id)


def condition_of(event):
    """Return the condition an event's record names."""
    if event.concept.concept_id:
        return ("concept", event.concept.concept_id)
    if event.source_concept is not None:
        return ("source", event.source_concept.vocabulary_id,
                event.source_concept.code)
    return ("value", event.concept.source_code or "")


def lookup(bundle):
    """Return {bundle reference: what it names} of a bundle, and
    {event reference: the condition of its record}."""
    names, conditions = {}, {}
    for event in events_of(bundle):
        names[event.ref] = record_key(event)
        conditions[event.ref] = condition_of(event)
    if getattr(bundle, "coverage", None) is not None:
        names[bundle.coverage.ref] = ("coverage",)
    if getattr(bundle, "prediction", None) is not None:
        names[bundle.prediction.ref] = ("prediction",)
    for attribution in getattr(bundle, "attributions", ()):
        names[attribution.ref] = ("attribution",
                                  attribution.concept.concept_id)
    return names, conditions


def condition_name(slot, conditions):
    """Return the condition a ConditionName slot names, or None."""
    if slot is None:
        return None
    if slot.event is not None:
        return conditions.get(slot.event)
    if len(slot.concepts) == 1:
        return ("concept", slot.concepts[0])
    if slot.concepts:
        return ("concepts",) + tuple(slot.concepts)
    return ("code", slot.code)


def driver_concept(slot, names):
    """Return the concept of the driver an attribution reference names, or
    None."""
    named = names.get(slot) if slot is not None else None
    return named[1] if named is not None and named[0] == "attribution" \
        else None


def read_statement(statement, names, conditions):
    """Return one candidate statement in the metrics' form."""
    slots = statement.slots
    fields = {"kind": statement.kind,
              "cited": tuple(names.get(ref) for ref in statement.refs)}
    if statement.kind == "notice":
        notice = statement.notice
        if notice in RESTRICTION_OF_NOTICE:
            notice = metrics.RESTRICTION_PREFIX + RESTRICTION_OF_NOTICE[
                notice]
        fields["notice"] = notice
        fields["count"] = slots.omitted
        return metrics.Statement(**fields)
    if statement.kind == "list_summary":
        fields["totals"] = (slots.groups, slots.records)
    elif statement.kind == "presence_summary":
        fields["totals"] = (slots.matches,)
    fields.update(
        name=condition_name(slots.condition, conditions), count=slots.count,
        visits=tuple(slots.visits) if slots.visits is not None else None,
        match=slots.match, unmapped=slots.unmapped, label=slots.label,
        score=slots.score, logit=slots.logit, rank=slots.rank,
        driver=driver_concept(slots.driver, names))
    return metrics.Statement(**fields)


def read(request_id, outcome, candidate, bundle, has_text, wording=None):
    """Return a condition's response in the metrics' form: the outcome it
    ended in and, when it has text, the statements of the candidate it
    released (or, in the baseline, rendered)."""
    if candidate is None or outcome not in metrics.TEXT_OUTCOMES:
        return metrics.Response(request_id, outcome, (), has_text, wording)
    names, conditions = lookup(bundle)
    return metrics.Response(request_id, outcome, tuple(
        read_statement(statement, names, conditions)
        for statement in candidate.statements), has_text, wording,
        tuple(candidate.reason_codes))


# --------------------------------------------------------------------------
# Default mode: the invented fixtures' template candidates
# --------------------------------------------------------------------------

def run_synthetic():
    """Return the problems of reading the invented fixtures' templates."""
    from ehr_evidence_app.generation import shape
    problems = []
    for context, template in shape.synthetic_contexts():
        found = read(template.request_id, template.outcome, template,
                     context.bundle, True)
        if len(found.statements) != len(template.statements):
            problems.append("a statement is lost")
        unnamed = [ref for statement in found.statements
                   for ref in statement.cited if ref is None]
        if unnamed:
            problems.append("a template cites a reference read as nothing")
        if any(statement.kind in metrics.ITEMS and statement.name is None
               for statement in found.statements):
            problems.append("a template's item names no condition")
    withheld = read("w", "validation_rejection", None, None, False)
    if withheld.statements:
        problems.append("a withheld response states something")
    return problems


def main(argv=None):
    """Run the default mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    problems = sorted(set(run_synthetic()))
    for problem in problems:
        print("FAILED: %s" % problem)
    if problems:
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
