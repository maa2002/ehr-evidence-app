"""Support of QA statements by their cited records (work item 37).

A candidate that passed the structure checks cites records that exist.
These checks decide whether the records support what its statements say;
any failure is claim_unsupported:

* a list item names the condition of one of the records it cites, every
  record it cites is of that condition (the same standard concept, or the
  same source code for a record without one), and its count and visits
  are those of the cited records;
* a list names every condition of the scope once and cites every record
  of the scope once; its summary counts the scope's conditions and
  records, and its note on records without a standard concept counts
  them; an empty list needs complete, empty coverage;
* every record a presence item cites matches the resolved condition by
  the match kind it states, found with the resolver's own matching
  (qa.resolve.match), and its count and visits are those of the cited
  records; each condition is named once for each match kind; the answer
  cites every matching record of the scope, and its summary counts them;
* "not recorded" needs complete coverage and no record of the scope that
  matches the condition, and its note on records without a standard
  concept counts them;
* the disclosure unmapped_records_in_scope is stated exactly when the
  scope holds records without a standard concept.

Refusals and clarifying questions state no recorded facts; nothing here
applies to them.

Usage:
    python -m ehr_evidence_app.validation.support
"""

from __future__ import annotations

import argparse
import sys

from ehr_evidence_app.qa import resolve
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import structure

COMPLETE = ("complete", "complete_empty")


def visits_of(bundle, events):
    """Return the sorted visit numbers of some events."""
    numbers = {visit.ref: visit.visit_number for visit in bundle.visits}
    return tuple(sorted({numbers[event.visit_ref] for event in events}))


def of_kind(candidate, kind):
    """Return a candidate's statements of one kind."""
    return [statement for statement in candidate.statements
            if statement.kind == kind]


def item_supported(statement, bundle, events):
    """Return True when an item's records are of the condition it names
    and its count and visits are theirs."""
    slots = statement.slots
    named = events.get(slots.condition.event) if (
        slots.condition is not None
        and slots.condition.event is not None) else None
    cited = [events[ref] for ref in statement.refs if ref in events]
    return (named is not None and slots.condition.event in statement.refs
            and len(cited) == len(statement.refs)
            and all(candidates.record_group(event)
                    == candidates.record_group(named) for event in cited)
            and slots.count == len(cited)
            and tuple(slots.visits or ()) == visits_of(bundle, cited))


def count_supported(statements, number):
    """Return True when a note on records without a standard concept is
    given exactly when there are some, with their count."""
    if not number:
        return not statements
    return len(statements) == 1 and statements[0].slots.unmapped == number


def list_supported(candidate, bundle, events):
    """Return True when a list's statements are supported by the scope."""
    unmapped = bundle.coverage.unmapped_events_in_scope
    if candidate.outcome == "not_recorded":
        return (not bundle.events
                and bundle.coverage.status == "complete_empty")
    items = of_kind(candidate, "list_item")
    if bundle.coverage.status not in COMPLETE or not all(
            item_supported(item, bundle, events) for item in items):
        return False
    cited = [ref for item in items for ref in item.refs]
    named = [candidates.record_group(events[item.slots.condition.event])
             for item in items]
    groups = {candidates.record_group(event) for event in bundle.events}
    summary = of_kind(candidate, "list_summary")[0].slots
    return (len(cited) == len(set(cited)) and set(cited) == set(events)
            and len(named) == len(set(named)) and set(named) == groups
            and summary.groups == len(groups)
            and summary.records == len(bundle.events)
            and count_supported(of_kind(candidate, "list_unmapped"),
                                unmapped))


def resolution_of(name):
    """Return the resolution a condition name stands for."""
    if name.code is not None:
        return resolve.Resolution("", "resolved", "icd_code",
                                  codes=(name.code,))
    return resolve.Resolution("", "resolved", "concepts",
                              concepts=tuple(name.concepts))


def matching(bundle, name, vocab):
    """Return the match kind of every record of the scope that matches a
    condition name, by reference."""
    resolution = resolution_of(name)
    found = {}
    for event in bundle.events:
        source = event.source_concept
        kind = resolve.match(resolution, vocab, event.concept.concept_id,
                             source.code if source else None,
                             source.concept_id if source else None)
        if kind:
            found[event.ref] = kind
    return found


def presence_supported(candidate, bundle, events, vocab):
    """Return True when a presence answer's statements are supported by
    the scope's records."""
    kind = ("presence_summary" if candidate.outcome == "answer"
            else "presence_absent")
    name = of_kind(candidate, kind)[0].slots.condition
    if name.event is not None or bundle.coverage.status not in COMPLETE:
        return False
    if vocab is None and name.code is None:
        return False
    matches = matching(bundle, name, vocab)
    if candidate.outcome == "not_recorded":
        return not matches and count_supported(
            of_kind(candidate, "presence_unmapped"),
            bundle.coverage.unmapped_events_in_scope)
    items = of_kind(candidate, "presence_item")
    if not all(item_supported(item, bundle, events)
               and all(matches.get(ref) == item.slots.match
                       for ref in item.refs) for item in items):
        return False
    cited = [ref for item in items for ref in item.refs]
    named = [(candidates.record_group(events[item.slots.condition.event]),
              item.slots.match) for item in items]
    summary = of_kind(candidate, "presence_summary")[0].slots
    return (len(cited) == len(set(cited)) and set(cited) == set(matches)
            and len(named) == len(set(named))
            and summary.matches == len(matches))


def check(candidate, bundle=None, vocab=None):
    """Return the reason codes of the statements of a candidate that its
    cited records do not support; empty when it passes.

    The candidate must have passed the structure checks for this bundle;
    vocab is the condition vocabulary a presence check was resolved in.
    """
    if candidate.outcome not in structure.EVIDENTIAL or bundle is None:
        return ()
    events = candidates.bundle_events(bundle)
    disclosed = "unmapped_records_in_scope" in candidate.reason_codes
    if disclosed != bool(bundle.coverage.unmapped_events_in_scope):
        return ("claim_unsupported",)
    if candidate.task == "condition_list":
        supported = list_supported(candidate, bundle, events)
    else:
        supported = presence_supported(candidate, bundle, events, vocab)
    return () if supported else ("claim_unsupported",)


# Synthetic Default Mode (Invented Fixtures Only)

def synthetic_faults(good):
    """Return candidates with invented unsupported statements."""
    items = [index for index, statement in enumerate(good.statements)
             if statement.kind == "list_item"]
    first, second = (good.statements[index] for index in items[:2])

    def replaced(index, statement):
        """Return the candidate with one statement replaced."""
        statements = list(good.statements)
        statements[index] = statement
        return good.model_copy(update={"statements": tuple(statements)})

    def slots(statement, **changes):
        """Return a statement with some slots changed."""
        return statement.model_copy(update={
            "slots": statement.slots.model_copy(update=changes)})

    summary = good.statements[0]
    return [
        # An omitted diagnosis, counted as if it were not recorded.
        good.model_copy(update={"statements": tuple(
            slots(summary, groups=summary.slots.groups - 1)
            if statement is summary else statement
            for statement in good.statements if statement is not second)}),
        # A wrong count, wrong visits, a record of another condition.
        replaced(items[0], slots(first, count=first.slots.count + 1)),
        replaced(items[0], slots(first, visits=(1, 2, 3))),
        replaced(items[0], first.model_copy(update={
            "refs": first.refs + second.refs})),
        # A summary that miscounts the records.
        replaced(0, slots(summary, records=summary.slots.records + 1)),
        # A disclosure the scope does not call for.
        good.model_copy(update={
            "reason_codes": ("unmapped_records_in_scope",)}),
    ]


def run_synthetic():
    """Return (passed, faults detected, faults) on invented data."""
    bundle, digest = structure.synthetic_bundle()
    good = structure.synthetic_list(bundle, digest)
    passed = not check(good, bundle)
    faults = synthetic_faults(good)
    detected = sum(1 for fault in faults
                   if check(fault, bundle) == ("claim_unsupported",))
    return passed, detected, len(faults)


def main(argv=None):
    """Check an invented list answer and unsupported variants of it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    passed, detected, total = run_synthetic()
    print("supported answer passes: %s" % ("yes" if passed else "no"))
    print("unsupported statements detected: %d of %d" % (detected, total))
    ok = passed and detected == total
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
