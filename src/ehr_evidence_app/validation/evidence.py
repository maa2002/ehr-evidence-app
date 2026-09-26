"""Checks before generation (work item 35).

Before any text is written for a request, its evidence is checked. The
bundle arrives verified against its pinned manifest (the bundle set of
qa.answer refuses a changed file); then:

* it must be bound to the request: another case is identity_inconsistent,
  another scope scope_invalid (case rejection);
* its visits must be numbered from visit 1 without a gap, as the closure
  record numbers a case's visits (history_inconsistent), or be exactly
  the selected visit;
* every record must have a readable name or code (evidence_inconsistent);
* sufficiency: an answer needs complete coverage of the scope, otherwise
  the request ends in an abstention (coverage_incomplete).

Explanation requests are checked with their prediction and attributions in
work item 38. Nothing here loads a model.

Usage:
    python -m ehr_evidence_app.validation.evidence
"""

from __future__ import annotations

import argparse
import dataclasses
import sys

from ehr_evidence_app.evidence import bundles, contracts


@dataclasses.dataclass(frozen=True)
class Stop:
    """Why a request ends before generation: its outcome and reasons."""

    outcome: str
    reasons: tuple


def readable(event):
    """Return True when a record can be named: by its concept's name or by
    a source code."""
    return (event.concept.name_status == "available"
            or event.source_concept is not None
            or bool(event.concept.source_code))


def qa_stop(request, bundle):
    """Return the Stop of a QA request whose bundle cannot support an
    answer, or None."""
    if bundle.case != request.case:
        return Stop("case_rejection", ("identity_inconsistent",))
    if bundle.scope != request.scope:
        return Stop("case_rejection", ("scope_invalid",))
    numbers = [visit.visit_number for visit in bundle.visits]
    expected = ([request.scope.visit_number]
                if request.scope.kind == "selected_visit"
                else list(range(1, len(numbers) + 1)))
    if numbers != expected:
        return Stop("case_rejection", ("history_inconsistent",))
    if not all(readable(event) for event in bundle.events):
        return Stop("case_rejection", ("evidence_inconsistent",))
    if bundle.coverage.status not in ("complete", "complete_empty"):
        return Stop("abstention", ("coverage_incomplete",))
    return None


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

def request_for(bundle):
    """Return an invented QA request bound to a bundle's case and scope."""
    return contracts.Request(schema_version="1.0", request_id="q-1",
                             task="condition_list", case=bundle.case,
                             scope=bundle.scope,
                             question="List the recorded conditions.")


def synthetic_faults(bundle):
    """Return (expected Stop, bundle) pairs of invented faulty bundles."""
    event = bundle.events[0]
    unreadable = event.model_copy(update={
        "concept": event.concept.model_copy(update={
            "name_status": "unavailable", "name": None, "name_source": None,
            "source_code": None}),
        "source_concept": None})
    return [
        (Stop("case_rejection", ("identity_inconsistent",)),
         bundle.model_copy(update={"case": bundle.case.model_copy(
             update={"case_number": 2})})),
        (Stop("case_rejection", ("scope_invalid",)),
         bundle.model_copy(update={"scope": contracts.ScopeRef(
             kind="pre_target_history")})),
        (Stop("case_rejection", ("history_inconsistent",)),
         bundle.model_copy(update={"visits": bundle.visits[1:]})),
        (Stop("case_rejection", ("evidence_inconsistent",)),
         bundle.model_copy(update={"events": (unreadable,)
                                   + bundle.events[1:]})),
        (Stop("abstention", ("coverage_incomplete",)),
         bundle.model_copy(update={"coverage": bundle.coverage.model_copy(
             update={"status": "failed"})})),
    ]


def run_synthetic():
    """Return (bundles passed, bundles, faults detected, faults)."""
    qa, _, _ = bundles.run_synthetic()
    passed = sum(1 for bundle in qa
                 if qa_stop(request_for(bundle), bundle) is None)
    bundle = next(item for item in qa if item.events
                  and item.scope.kind == "case_visits")
    faults = synthetic_faults(bundle)
    detected = sum(1 for expected, faulty in faults
                   if qa_stop(request_for(bundle), faulty) == expected)
    return passed, len(qa), detected, len(faults)


def main(argv=None):
    """Check the invented bundles and invented faults of one of them."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    passed, total, detected, faults = run_synthetic()
    print("bundles that pass: %d of %d" % (passed, total))
    print("faults stopped with their outcome and reason: %d of %d" % (
        detected, faults))
    ok = passed == total and detected == faults
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
