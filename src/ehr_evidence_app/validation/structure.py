"""Structure and reference checks of a candidate (work item 36).

A candidate (validation.candidate) is checked against the request it
answers and the bundle bound to that request, before any other check:

* structure_invalid: the document is malformed; its outcome, reason codes,
  task and statements do not fit together; a wording's placeholders are
  not exactly the slots of its kind, or a notice's wording is not its
  approved text; a statement cites evidence its kind does not cite; or the
  response it renders would break the response contract;
* scope_violated: the candidate, or a scope it states, names another
  request, case or scope, or another bundle than the one bound to the
  request (the citation check of evidence.schema, extended to candidates);
* reference_unresolved: a reference, a named event, driver or visit
  number, or a concept, that the bundle or the vocabulary lacks.

These checks say nothing about whether the cited records support what a
statement says; that is work item 37 (validation.support).

Usage:
    python -m ehr_evidence_app.validation.structure
"""

from __future__ import annotations

import argparse
import collections
import sys

from pydantic import ValidationError

from ehr_evidence_app.evidence import bundles, contracts, schema
from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.validation import candidate as candidates

# How many statements of each kind an answer holds: (minimum, maximum).
COMPOSITION = {
    ("condition_list", "answer"): {"list_summary": (1, 1),
                                   "list_item": (1, None),
                                   "list_unmapped": (0, 1),
                                   "notice": (1, 1)},
    ("condition_list", "not_recorded"): {"list_empty": (1, 1),
                                         "notice": (1, 1)},
    ("condition_presence", "answer"): {"presence_summary": (1, 1),
                                       "presence_item": (1, None)},
    ("condition_presence", "not_recorded"): {"presence_absent": (1, 1),
                                             "presence_unmapped": (0, 1),
                                             "notice": (1, 1)},
    ("prediction_explanation", "explanation"): {"prediction": (1, 1),
                                                "driver": (1, None),
                                                "driver_history": (0, None),
                                                "driver_context": (0, None),
                                                "notice": (3, None)},
}
EVIDENTIAL = ("answer", "not_recorded")


def parse(document):
    """Return (candidate, problems) of a writer's JSON-like document."""
    try:
        return candidates.parse(document), ()
    except ValidationError:
        return None, ("structure_invalid",)


def outcome_problems(candidate):
    """Return problems of the outcome, reason codes, task and bundle."""
    outcome = candidate.outcome
    if outcome not in vocab.RELEASED_TEXT_OUTCOMES:
        return {"structure_invalid"}
    codes = candidate.reason_codes
    allowed = vocab.REASONS_BY_OUTCOME[outcome]
    if (len(set(codes)) != len(codes)
            or any(code not in allowed for code in codes)
            or (outcome in vocab.REASON_REQUIRED_OUTCOMES and not codes)):
        return {"structure_invalid"}
    if outcome in EVIDENTIAL:
        if candidate.task not in vocab.QA_TASKS or candidate.bundle is None:
            return {"structure_invalid"}
    elif outcome == "explanation":
        if (candidate.task != "prediction_explanation"
                or candidate.bundle is None):
            return {"structure_invalid"}
    elif candidate.bundle is not None:
        return {"structure_invalid"}
    return set()


def binding_problems(candidate, bundle, bundle_sha256):
    """Return problems of the bundle a candidate names."""
    if candidate.bundle is None:
        return set()
    if (bundle is None or candidate.bundle.bundle_id != bundle.bundle_id
            or candidate.bundle.sha256 != bundle_sha256
            or bundle.case != candidate.case
            or bundle.scope != candidate.scope):
        return {"scope_violated"}
    return set()


def wording_problems(statement):
    """Return problems of a statement's wording and slots."""
    names = candidates.placeholders(statement.wording)
    left = candidates.PLACEHOLDER.sub("", statement.wording)
    expected = candidates.expected_slots(statement)
    if ("{" in left or "}" in left
            or sorted(names) != sorted(expected)
            or set(statement.slots.filled()) != set(expected)
            or statement.slots.visits == ()):
        return {"structure_invalid"}
    return set()


def kind_problems(statement, candidate):
    """Return problems of a statement's kind or notice in its candidate."""
    if (statement.kind == "notice") != (statement.notice is not None):
        return {"structure_invalid"}
    if statement.kind == "notice":
        approved = candidates.NOTICES[statement.notice]
        if (statement.wording != approved.text
                or candidate.outcome not in approved.outcomes
                or approved.task not in (None, candidate.task)):
            return {"structure_invalid"}
        return set()
    kind = candidates.KINDS[statement.kind]
    if (candidate.task != kind.task
            or candidate.outcome not in kind.outcomes):
        return {"structure_invalid"}
    return set()


def cites_as_its_kind(statement):
    """Return True when a statement cites what its kind cites."""
    rule = candidates.KINDS[statement.kind].refs
    refs = statement.refs
    if len(set(refs)) != len(refs):
        return False
    if rule == "events":
        return bool(refs) and all(ref.startswith("E") for ref in refs)
    if rule == "coverage":
        return refs == ("C1",)
    if rule == "prediction":
        return refs == ("P1",)
    if rule == "attribution":
        return refs == (statement.slots.driver,)
    return not refs


def reference_problems(statement, candidate, bundle, concept_name):
    """Return problems of a statement's references and the evidence and
    concepts its slots name."""
    problems = set()
    if not cites_as_its_kind(statement):
        problems.add("structure_invalid")
    refs = statement.refs
    slots = statement.slots
    if slots.scope is not None and slots.scope != candidate.scope:
        problems.add("scope_violated")
    lookup = candidates.Lookup(bundle, concept_name)
    if bundle is not None:
        if any(ref not in schema.bundle_refs(bundle) for ref in refs):
            problems.add("reference_unresolved")
        condition = slots.condition
        if condition is not None and condition.event is not None and (
                condition.event not in lookup.events):
            problems.add("reference_unresolved")
        if slots.driver is not None and (
                slots.driver not in lookup.attributions):
            problems.add("reference_unresolved")
        numbers = {visit.visit_number for visit in bundle.visits}
        if slots.visits and any(number not in numbers
                                for number in slots.visits):
            problems.add("reference_unresolved")
    elif refs or slots.visits or slots.driver is not None or (
            slots.condition is not None
            and slots.condition.event is not None):
        problems.add("reference_unresolved")
    concepts = tuple(slots.names or ()) + (
        slots.condition.concepts if slots.condition is not None else ()) + (
        (slots.label,) if slots.label is not None else ())
    if any(not lookup.known(concept) for concept in concepts):
        problems.add("reference_unresolved")
    return problems


def composition_problems(candidate):
    """Return problems of the statements an answer holds."""
    rules = COMPOSITION.get((candidate.task, candidate.outcome))
    if rules is None:
        return set()
    counts = collections.Counter(statement.kind
                                 for statement in candidate.statements)
    if any(kind not in rules for kind in counts):
        return {"structure_invalid"}
    for kind, (least, most) in rules.items():
        if counts[kind] < least or (most is not None
                                    and counts[kind] > most):
            return {"structure_invalid"}
    return set()


def size_problems(candidate, bundle, concept_name):
    """Return problems of the response a candidate renders."""
    try:
        released = candidates.response(candidate, bundle, concept_name)
    except (ValidationError, candidates.CandidateError):
        return {"structure_invalid"}
    if (len(released.text) > candidates.MAX_TEXT
            or len(released.claims) > candidates.MAX_CLAIMS):
        return {"structure_invalid"}
    return set()


def check(candidate, request_id, case, scope, bundle=None,
          bundle_sha256=None, concept_name=None):
    """Return the reason codes of a candidate's structure and reference
    problems, sorted; empty when it passes.

    request_id, case and scope are those of the request being answered;
    bundle and bundle_sha256 the bundle bound to it, if any; concept_name
    returns a vocabulary concept's name, or None for an unknown concept.
    """
    problems = set()
    if candidate.request_id != request_id:
        problems.add("structure_invalid")
    if candidate.case != case or candidate.scope != scope:
        problems.add("scope_violated")
    problems |= outcome_problems(candidate)
    problems |= binding_problems(candidate, bundle, bundle_sha256)
    for statement in candidate.statements:
        problems |= kind_problems(statement, candidate)
        problems |= wording_problems(statement)
        problems |= reference_problems(statement, candidate, bundle,
                                       concept_name)
    problems |= composition_problems(candidate)
    if not problems:
        problems |= size_problems(candidate, bundle, concept_name)
    return tuple(sorted(problems))


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

def synthetic_bundle():
    """Return an invented QA bundle with records, and its digest."""
    qa, _, _ = bundles.run_synthetic()
    bundle = next(item for item in qa if item.events
                  and item.scope.kind == "case_visits")
    return bundle, "5" * 64


def synthetic_list(bundle, digest):
    """Return a list answer candidate for an invented bundle."""
    groups = collections.OrderedDict()
    for event in bundle.events:
        groups.setdefault(event.concept.concept_id or event.ref,
                          []).append(event)
    visits = {visit.ref: visit.visit_number for visit in bundle.visits}
    statements = [candidates.Statement(
        kind="list_summary",
        wording="{groups} are recorded within {scope} ({records}):",
        slots=candidates.Slots(groups=len(groups), scope=bundle.scope,
                               records=len(bundle.events)))]
    for events in groups.values():
        statements.append(candidates.Statement(
            kind="list_item", wording="{condition}: {count} in {visits}",
            slots=candidates.Slots(
                condition=candidates.ConditionName(event=events[0].ref),
                count=len(events),
                visits=tuple(sorted({visits[event.visit_ref]
                                     for event in events}))),
            refs=tuple(event.ref for event in events)))
    statements.append(candidates.notice("list_complete"))
    return candidates.Candidate(
        schema_version="1.0", request_id="q-1", task="condition_list",
        case=bundle.case, scope=bundle.scope, outcome="answer",
        bundle=contracts.BundleRef(bundle_id=bundle.bundle_id,
                                   sha256=digest),
        statements=tuple(statements))


def synthetic_variants(good):
    """Return (expected reason code, candidate) pairs of invented faults."""
    item = good.statements[1]

    def with_item(**changes):
        """Return the candidate with its first item changed."""
        statements = list(good.statements)
        statements[1] = item.model_copy(update=changes)
        return good.model_copy(update={"statements": tuple(statements)})

    other_case = good.case.model_copy(update={"case_number": 2})
    return [
        ("structure_invalid", with_item(wording="{condition}: {count}")),
        ("structure_invalid", with_item(refs=("C1",))),
        ("structure_invalid", good.model_copy(update={
            "statements": good.statements[1:]})),
        ("structure_invalid", good.model_copy(update={
            "reason_codes": ("coverage_incomplete",)})),
        ("reference_unresolved", with_item(refs=("E999",))),
        ("reference_unresolved", with_item(slots=item.slots.model_copy(
            update={"visits": (99,)}))),
        ("scope_violated", good.model_copy(update={"case": other_case})),
        ("scope_violated", good.model_copy(update={
            "bundle": good.bundle.model_copy(update={"sha256": "6" * 64})})),
    ]


def run_synthetic():
    """Return (passed, variants detected, variants) on invented data."""
    bundle, digest = synthetic_bundle()
    good = synthetic_list(bundle, digest)
    passed = not check(good, "q-1", good.case, good.scope, bundle, digest)
    variants = synthetic_variants(good)
    detected = sum(1 for expected, variant in variants
                   if expected in check(variant, "q-1", good.case,
                                        good.scope, bundle, digest))
    return passed, detected, len(variants)


def main(argv=None):
    """Check an invented candidate and invented faults of it."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    passed, detected, total = run_synthetic()
    print("valid candidate passes: %s" % ("yes" if passed else "no"))
    print("faults detected with their reason code: %d of %d" % (detected,
                                                                total))
    ok = passed and detected == total
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
