"""Independent references of the final evaluation (work items 54 and 55;
ADR 0028).

Every compared request has a reference prepared from the source records,
by code that reads no bundle and runs no part of the runtime's pipeline
(reading, resolution, retrieval, answering or checks):

* A list or presence check of conditions uses the development suite's
  oracle (qa.suite.Oracle), which reads the case set's source snapshot,
  its cases' visits and the condition vocabulary's tables.
  - A list expects the scope's records by condition: a standard concept,
    or the source code of records without one, each record with its
    visit number.
  - A presence check expects the records whose standard concept is the
    condition asked about (exact) or descends from it (more specific).
    The scope's records without a standard concept are unjudged, since a
    presence check may match them by ICD description, for which there is
    no independent reference.
* An explanation is read from the case set's published records.
  - The output record gives the selected prediction's label, score and
    logit, and the drivers with their scores.
  - The closure record gives each driver's tags and the case's
    restrictions; the linkage record gives each driver's linked records.
  - The snapshot confirms each linked record's visit and concept.
  - The expected drivers are the eligible ones with the highest scores,
    highest first, ties in the output's order, at most the frozen limit
    (config/explanation.json). Each comes with its linked records in
    pre-target visits (history) and its linked drug and procedure records
    of the final admission (context; the final admission's diagnoses are
    never shown). Entries of the target outcome are left out.
  - The reference also holds the number of eligible drivers not
    described and the expected outcome: an explanation, an abstention for
    a case without an eligible driver, or a case rejection for a case not
    admitted.

Each QA reference states whether the complete sources answer the request
(the snapshot is complete), apart from whether the runtime's bundle does.

The comparison mode builds, for a development case set, the references
of the requests the development benchmark writes
(generation.adapter.development_requests). It then scores each template
document against its reference with assessment.metrics. A template, which
passes the release gate, is expected to score as a correct response;
every difference is counted by kind, and nothing of a request is printed.
The evaluation set is refused.

Usage:
    python -m ehr_evidence_app.assessment.references
    python -m ehr_evidence_app.assessment.references --operator-real \\
        --case-set dev-30-v1
"""

from __future__ import annotations

import argparse
import collections
import json
import sys

from ehr_evidence_app.assessment import metrics, stated
from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import readers
from ehr_evidence_app.gnn import provenance as amp
from ehr_evidence_app.qa import resolve, suite

DOMAINS = ("condition", "procedure", "drug")
COVERAGE = ("coverage",)
PREDICTION = ("prediction",)
LIMIT_PATH = "config/explanation.json"
LINKAGE_SCHEMA = "driver-event-linkage"


class ReferenceError(Exception):
    """A fail-closed failure carrying a finding code only."""


def record_key(domain, native_id):
    """Return a record's name in the metrics."""
    return ("record", domain, native_id)


# Conditions

def case_visits(oracle, case):
    """Return {record: visit number} of every condition record of a case:
    its visit's place among the case's visits."""
    visits = {}
    for number, visit_id in enumerate(case.visit_ids, 1):
        for item in oracle.records[case.case_number][visit_id]:
            visits[record_key(*item.key)] = number
    return visits


def condition_reference(oracle, case_set, case, request_id, task, scope,
                        asked=(), stratum=None, complete=True):
    """Return the reference of a list (task condition_list) or of a
    presence check of the concepts asked (condition_presence) in a scope
    of a case."""
    if task not in metrics.QA_TASKS:
        raise ReferenceError("task_not_conditions")
    found = oracle.scope_records(case, scope)
    numbers = case_visits(oracle, case)
    names = {record_key(*item.key): item.group for item in found}
    groups, matches, unjudged = collections.defaultdict(set), {}, set()
    if task == "condition_list":
        for key, name in names.items():
            groups[name].add(key)
    else:
        if not asked:
            raise ReferenceError("presence_without_condition")
        members = frozenset().union(*(oracle.members(concept)
                                      for concept in asked))
        for item in found:
            key = record_key(*item.key)
            if not item.concept_id:
                unjudged.add(key)
            elif item.concept_id in members:
                groups[item.group].add(key)
                matches[key] = ("exact" if item.concept_id in asked
                                else "more_specific")
    return metrics.Reference(
        request_id, (case_set, case.case_number), task, stratum or task,
        "answer" if groups else "not_recorded",
        groups={name: frozenset(keys) for name, keys in groups.items()},
        matches=matches, names=names,
        visits={key: numbers[key] for key in names},
        unmapped=sum(not item.concept_id for item in found),
        unjudged=frozenset(unjudged),
        citable=frozenset(names) | {COVERAGE}, source_answerable=complete)


# Explanations

def configured_limit(path=LIMIT_PATH):
    """Return the frozen number of drivers an explanation describes."""
    import os
    with open(os.path.join(registry.REPO_ROOT, path), "r",
              encoding="utf-8") as handle:
        drivers = json.load(handle)["drivers"]
    if drivers.get("selection") != "highest_eligible_attribution" or not (
            isinstance(drivers.get("limit"), int) and drivers["limit"] > 0):
        raise ReferenceError("explanation_limit_unexpected")
    return drivers["limit"]


def linked_records(domain, driver, link, positions, final, events):
    """Return (history, context, visit numbers) of one driver's linkage
    entry, each linked record checked against the snapshot: its visit and
    its concept must be the entry's."""
    history, context, visits = set(), set(), {}
    for visit in link.get("visits") or ():
        position = positions.get(visit.get("visit_occurrence_id"))
        if position is None:
            raise ReferenceError("linked_visit_not_in_case")
        for native_id in visit.get("event_ids") or ():
            event = events.get((domain, native_id))
            if (event is None or event.visit_occurrence_id
                    != visit["visit_occurrence_id"]
                    or event.concept_id != driver["concept_id"]):
                raise ReferenceError("linked_record_mismatch")
            key = record_key(domain, native_id)
            if position < final:
                history.add(key)
                visits[key] = position + 1
            elif domain != "condition":
                context.add(key)
    return frozenset(history), frozenset(context), visits


def explanation_reference(case_set, case, record, entry, events, limit,
                          request_id):
    """Return the reference of the explanation of a case's selected
    prediction.

    record is the case's published output record, entry its linkage
    record entry and events the snapshot's records by (domain, native
    identifier).
    """
    if not case.admitted:
        return metrics.Reference(request_id, (case_set, case.case_number),
                                 metrics.EXPLANATION, "explanation",
                                 "case_rejection")
    selected = record["predicted_diagnosis"]
    positions = {visit_id: position
                 for position, visit_id in enumerate(case.visit_ids)}
    final = len(case.visit_ids) - 1
    eligible, citable, visits, order = [], {COVERAGE, PREDICTION}, {}, 0
    for domain in DOMAINS:
        drivers = record["drivers"][domain]
        tags = case.drivers[domain]
        links = entry["domains"][domain]["drivers"]
        if not len(drivers) == len(tags) == len(links):
            raise ReferenceError("driver_count_mismatch")
        for driver, tag, link in zip(drivers, tags, links):
            if tag.target_outcome_concept:
                continue
            history, context, numbers = linked_records(
                domain, driver, link, positions, final, events)
            citable |= history | context | {("attribution",
                                             driver["concept_id"])}
            visits.update(numbers)
            if tag.narration_eligible:
                eligible.append((driver["score"], order, driver["concept_id"],
                                 history, context))
            order += 1
    ranked = sorted(eligible, key=lambda item: (-item[0], item[1]))[:limit]
    drivers = tuple(metrics.Driver(concept, rank, score, history, context)
                    for rank, (score, _, concept, history, context)
                    in enumerate(ranked, 1))
    if bool(eligible) != case.explanation_eligible:
        raise ReferenceError("explanation_eligibility_mismatch")
    return metrics.Reference(
        request_id, (case_set, case.case_number), metrics.EXPLANATION,
        "explanation", "explanation" if eligible else "abstention",
        visits=visits, citable=frozenset(citable),
        prediction=(selected["concept_id"], selected["probability"],
                    selected["logit"]) if eligible else None,
        drivers=drivers if eligible else (),
        omitted=max(0, len(eligible) - limit) if eligible else 0,
        restrictions=frozenset(case.restrictions))


# Reading a Case Set's Sources

class Sources:
    """A case set's source records: its snapshot, cases, condition
    vocabulary, published output records (by patient index) and linkage
    record."""

    def __init__(self, label, snapshot, cases, vocab, outputs, linkage):
        """Hold a case set's source records, checking the linkage record's
        kind."""
        if (not isinstance(linkage, dict)
                or linkage.get("schema") != LINKAGE_SCHEMA
                or linkage.get("status") != "verified"):
            raise ReferenceError("linkage_not_verified")
        self.label = label
        self.snapshot = snapshot
        self.cases = cases
        self.oracle = suite.Oracle(snapshot, cases, vocab)
        self.outputs = outputs
        self.linkage = {item["case_number"]: item
                        for item in linkage.get("cases") or ()}
        self.events = {(event.domain, event.native_id): event
                       for domain in DOMAINS
                       for event in snapshot.events[domain]}
        self.by_number = {case.case_number: case for case in cases.cases}

    @classmethod
    def read(cls, case_set):
        """Read a case set's source records through their pins."""
        from ehr_evidence_app.evidence import bundles
        pilot = case_set == readers.PILOT_LABEL
        plan = readers.real_plan(None if pilot else case_set)
        inputs = bundles.real_plan(None if pilot else case_set)
        held = []
        outputs = readers.parse_json(readers.read_pinned(
            inputs["outputs"], "outputs", held), "outputs")
        linkage = readers.parse_json(readers.read_pinned(
            inputs["linkage"], "linkage", held), "linkage")
        return cls(case_set, readers.read_snapshot(plan),
                   readers.read_cases(plan), resolve.load(),
                   {item["patient_index"]: item for item in outputs},
                   linkage)

    def condition(self, request_id, task, case_number, scope, asked=(),
                  stratum=None):
        """Return the reference of a condition request."""
        return condition_reference(
            self.oracle, self.label, self.by_number[case_number], request_id,
            task, scope, asked, stratum, self.snapshot.complete)

    def explanation(self, request_id, case_number, limit):
        """Return the reference of a case's explanation request, from the
        output record the closure record names by its digest."""
        case = self.by_number[case_number]
        record = self.outputs.get(case.patient_index)
        entry = self.linkage.get(case_number)
        if case.admitted and (record is None or amp.digest(record)
                              != case.output_record_sha256):
            raise ReferenceError("output_record_mismatch")
        if case.admitted and (entry is None or entry.get(
                "output_record_sha256") != case.output_record_sha256):
            raise ReferenceError("linkage_case_mismatch")
        return explanation_reference(self.label, case, record, entry,
                                     self.events, limit, request_id)


# Comparison with the Templates on a Development Case Set

def stratum_of(context, template):
    """Return a compared request's stratum, as the development benchmark
    names it."""
    task = context.task
    if task == "condition_list":
        return "list" if template.outcome == "answer" else "empty list"
    if task == "condition_presence":
        recorded = {event.concept.concept_id
                    for event in context.bundle.events}
        concepts = set(context.asked.name.concepts)
        return ("presence, recorded" if concepts & recorded
                else "presence, recorded elsewhere")
    return "explanation"


def faults(scored, task):
    """Return how a scored template differs from a correct response: an
    empty tuple when it does not."""
    terms = scored.terms
    if terms["apart"]:
        return ("counted apart",)
    found = []
    if terms["claims"] != terms["supported"]:
        found.append("unsupported statement")
    if terms["cited"] != terms["resolved"]:
        found.append("unresolved reference")
    if task == metrics.EXPLANATION:
        if not terms["preserved"]:
            found.append("not preserved")
        if not terms["consistent"]:
            found.append("not consistent")
    elif not terms["correct"]:
        found.append("not correct")
    return tuple(found)


def compare(sources, pairs, limit):
    """Return {(stratum, verdict): count} of the templates of the
    development requests against their references; an explanation that
    describes another number of drivers than the frozen limit is compared
    with that number."""
    counts = collections.Counter()
    for number, (context, template) in enumerate(pairs, 1):
        request_id = "c-%05d" % number
        if context.task == metrics.EXPLANATION:
            reference = sources.explanation(
                request_id, context.request.case.case_number,
                limit if context.limit is None else context.limit)
        else:
            reference = sources.condition(
                request_id, context.task, context.request.case.case_number,
                context.request.scope,
                tuple(context.asked.name.concepts) if context.asked else (),
                stratum_of(context, template))
        response = stated.read(request_id, template.outcome, template,
                               context.bundle, True)
        found = faults(metrics.score(reference, response), context.task)
        stratum = stratum_of(context, template)
        if not found:
            counts[(stratum, "right")] += 1
        for fault in found:
            counts[(stratum, fault)] += 1
    return counts


def run_compare(case_set):
    """Compare the references with the templates of a development case
    set; return the counts."""
    from ehr_evidence_app.generation import adapter
    if case_set != readers.PILOT_LABEL and registry.spec(case_set)[
            "purpose"] != "development":
        raise ReferenceError("case_set_held_out")
    pairs, _ = adapter.development_requests(case_set)
    return compare(Sources.read(case_set), pairs, configured_limit())


# Default Mode: Invented Fixtures

def synthetic_sources():
    """Return the Sources of the invented case set the invented bundles
    are built from."""
    from ehr_evidence_app.evidence import bundles
    snapshot, cases, outputs, linked, _ = bundles.synthetic_inputs()
    return Sources(cases.label, snapshot, cases, suite.synthetic_vocabulary(),
                   outputs, linked)


def run_synthetic():
    """Return (counts, problems) of the invented templates against their
    references: every template must be right."""
    from ehr_evidence_app.generation import shape
    pairs = shape.synthetic_contexts()
    counts = compare(synthetic_sources(), pairs, configured_limit())
    problems = ["%s: %s" % key for key in sorted(counts)
                if key[1] != "right"]
    if sum(counts.values()) != len(pairs):
        problems.append("a request is not compared")
    if {stratum for stratum, _ in counts} != {
            "list", "empty list", "presence, recorded",
            "presence, recorded elsewhere", "explanation"}:
        problems.append("a stratum is not compared")
    return counts, problems


def report(counts):
    """Print the comparison's counts: strata and kinds only."""
    for (stratum, verdict), count in sorted(counts.items()):
        print("%-30s %-24s %d" % (stratum, verdict, count))
    wrong = sum(count for (_, verdict), count in counts.items()
                if verdict != "right")
    print("templates differing from their references: %d" % wrong)


def main(argv=None):
    """Run the synthetic default mode or the operator-only comparison."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="compare the references with the templates of "
                             "a development case set; prints counts only")
    parser.add_argument("--case-set", default="dev-30-v1")
    args = parser.parse_args(argv)
    try:
        if args.operator_real:
            report(run_compare(args.case_set))
        else:
            counts, problems = run_synthetic()
            report(counts)
            for problem in problems:
                print("FAILED: %s" % problem)
            if problems:
                return 1
    except (ReferenceError, readers.ReaderError, registry.RegistryError,
            resolve.ResolutionError) as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
