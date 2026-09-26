"""Mutation suite for the validation checks (work item 40.1).

The checks are tested by breaking correct responses. Correct candidates are
built by the templates (QA lists and presence checks in every scope, the
refusals and clarifying questions, and the template explanation) and every
error class of the project brief is applied to them, or to the bundles they
come from. A mutant is detected when it ends in its class's outcome with its
class's reason code; every original must be released unchanged, and so must
every control (a correct statement that attribution is not causation added
to an answer). Results are counted per error class:

* item 35, a case rejection: a bundle of another case or scope, a history
  gap, a record without a name, a prediction not selected; an abstention:
  incomplete coverage;
* item 36: a malformed document, a missing slot, a fact typed into a
  wording, a changed notice (structure_invalid); an absent reference
  (reference_unresolved); another patient, scope or bundle
  (scope_violated);
* item 37 (claim_unsupported): an omitted or added diagnosis, a wrong
  count or visits, a record of another condition, an unsupported
  positive, a negative for a recorded condition or without complete
  coverage;
* item 38: an altered label or score (prediction_altered); an altered
  attribution score, reordered or omitted drivers, a zero-score driver
  described (attribution_altered); a final-admission record as history,
  a restriction misdisclosed (claim_unsupported);
* item 39: causal, directional, percentage, exclusive, diagnostic, advice
  and status wording (prohibited_wording); a date or recurrence typed into
  a wording (date_unsupported);
* item 40: a check that fails to run (operational error,
  validator_failed).

The default mode uses invented fixtures. The operator mode uses the
development case sets and prints aggregate counts only; the evaluation case
set is never used.

Usage:
    python -m ehr_evidence_app.validation.mutations
    python -m ehr_evidence_app.validation.mutations --operator-real \\
        --case-set dev-30-v1
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import sys

from pydantic import ValidationError

from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import bundles, contracts, readers
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.qa import answer, resolve, suite, templates
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import evidence, explanation, gate, \
    structure

SUITE_VERSION = "validation-mutations-v1"
REJECTION = "validation_rejection"
# Phrases added to a statement's wording, by error class.
WORDINGS = {
    "causal_wording": ", caused by the final admission",
    "directional_wording": ", which raised the risk of admission",
    "percentage_wording": ", accounting for a share of the prediction",
    "exclusive_wording": ", and nothing else",
    "diagnostic_wording": ", consistent with sepsis",
    "advice_wording": ", which should be treated",
    "status_wording": ", now resolved",
    "date_wording": ", in March",
    "recurrence_wording": ", recorded again",
}
DATE_CLASSES = ("date_wording", "recurrence_wording")
CONTROL = ", which does not mean causation"


class MutationError(Exception):
    """A fail-closed failure carrying a finding code only."""


@dataclasses.dataclass(frozen=True)
class Sample:
    """A correct candidate, the request it answers and its bundle."""

    candidate: candidates.Candidate
    request: contracts.Request | None
    case: contracts.CaseRef
    scope: contracts.ScopeRef
    bundle: object = None
    digest: str | None = None
    vocab: object = None


@dataclasses.dataclass(frozen=True)
class Mutant:
    """One error applied to a sample, and what must come of it."""

    error: str
    outcome: str
    reason: str
    candidate: candidates.Candidate | None = None
    bundle: object = None
    request: contracts.Request | None = None
    document: dict | None = None
    checks: list | None = None


# --------------------------------------------------------------------------
# Mutations
# --------------------------------------------------------------------------

def replace(candidate, index, **changes):
    """Return a candidate with one statement changed."""
    items = list(candidate.statements)
    items[index] = items[index].model_copy(update=changes)
    return candidate.model_copy(update={"statements": tuple(items)})


def reslot(candidate, index, **slots):
    """Return a candidate with slots of one statement changed."""
    return replace(candidate, index, slots=candidate.statements[
        index].slots.model_copy(update=slots))


def indices(candidate, kind):
    """Return the positions of a candidate's statements of one kind."""
    return [index for index, item in enumerate(candidate.statements)
            if item.kind == kind]


def crashing(candidate, bundle, vocab):
    """A check that fails to run."""
    raise RuntimeError("check_failed")


def contract_mutants(sample):
    """Return mutants of the contract, references and binding (36)."""
    good = sample.candidate
    found = []
    document = good.model_dump(mode="json")
    document["statements"] = "not a list of statements"
    found.append(Mutant("malformed_document", REJECTION,
                        "structure_invalid", document=document))
    facts = [index for index, item in enumerate(good.statements)
             if item.kind != "notice"]
    if facts:
        index = facts[0]
        slot = candidates.KINDS[good.statements[index].kind].slots[0]
        found.append(Mutant("missing_slot", REJECTION, "structure_invalid",
                            reslot(good, index, **{slot: None})))
        found.append(Mutant(
            "fact_typed_in_wording", REJECTION, "structure_invalid",
            replace(good, index, wording=good.statements[index].wording
                    + " (2 records)")))
    notices = indices(good, "notice")
    if notices:
        found.append(Mutant(
            "changed_notice", REJECTION, "structure_invalid",
            replace(good, notices[0], wording=good.statements[
                notices[0]].wording + " Nothing else was checked.")))
    cited = [index for index, item in enumerate(good.statements)
             if candidates.KINDS[item.kind].refs == "events"]
    if cited:
        found.append(Mutant(
            "absent_reference", REJECTION, "reference_unresolved",
            replace(good, cited[0], refs=good.statements[cited[0]].refs
                    + ("E99999",))))
    found.append(Mutant("another_patient", REJECTION, "scope_violated",
                        good.model_copy(update={"case": good.case.model_copy(
                            update={"case_number": good.case.case_number
                                    + 1000})})))
    other = (contracts.ScopeRef(kind="case_visits")
             if good.scope.kind != "case_visits"
             else contracts.ScopeRef(kind="pre_target_history"))
    found.append(Mutant("another_scope", REJECTION, "scope_violated",
                        good.model_copy(update={"scope": other})))
    if good.bundle is not None:
        found.append(Mutant("another_bundle", REJECTION, "scope_violated",
                            good.model_copy(update={
                                "bundle": good.bundle.model_copy(
                                    update={"sha256": "0" * 64})})))
    return found


def wording_mutants(sample):
    """Return mutants of the wording rules (39) and the control."""
    good = sample.candidate
    facts = [index for index, item in enumerate(good.statements)
             if item.kind != "notice"]
    if not facts:
        return []
    index = facts[0]
    wording = good.statements[index].wording
    found = [Mutant(error, REJECTION, "date_unsupported"
                    if error in DATE_CLASSES else "prohibited_wording",
                    replace(good, index, wording=wording + phrase))
             for error, phrase in WORDINGS.items()]
    found.append(Mutant("control_non_causation", good.outcome, "",
                        replace(good, index, wording=wording + CONTROL)))
    return found


def list_mutants(sample):
    """Return mutants of a list answer's support (37)."""
    good = sample.candidate
    items = indices(good, "list_item")
    if good.outcome != "answer" or not items:
        return []
    summary = indices(good, "list_summary")[0]
    first = good.statements[items[0]]
    found = []
    if len(items) > 1:
        dropped = good.model_copy(update={"statements": tuple(
            item for index, item in enumerate(good.statements)
            if index != items[0])})
        dropped = reslot(dropped, summary, groups=len(items) - 1,
                         records=good.statements[summary].slots.records
                         - first.slots.count)
        found.append(Mutant("omitted_diagnosis", REJECTION,
                            "claim_unsupported", dropped))
        second = good.statements[items[1]]
        found.append(Mutant("record_of_another_condition", REJECTION,
                            "claim_unsupported",
                            replace(good, items[0], refs=first.refs
                                    + second.refs[:1])))
    added = good.model_copy(update={"statements": good.statements[
        :items[0] + 1] + (first,) + good.statements[items[0] + 1:]})
    added = reslot(added, summary,
                   groups=good.statements[summary].slots.groups + 1)
    found.append(Mutant("added_diagnosis", REJECTION, "claim_unsupported",
                        added))
    found.append(Mutant("wrong_count", REJECTION, "claim_unsupported",
                        reslot(good, items[0],
                               count=first.slots.count + 1)))
    numbers = {visit.visit_number for visit in sample.bundle.visits}
    spare = sorted(numbers - set(first.slots.visits))
    if spare:
        found.append(Mutant("wrong_visits", REJECTION, "claim_unsupported",
                            reslot(good, items[0], visits=tuple(sorted(
                                set(first.slots.visits) | {spare[0]})))))
    return found


def presence_mutants(sample):
    """Return mutants of a presence check's support (37)."""
    good = sample.candidate
    found = []
    if good.outcome == "answer":
        items = indices(good, "presence_item")
        summary = indices(good, "presence_summary")[0]
        found.append(Mutant("wrong_count", REJECTION, "claim_unsupported",
                            reslot(good, summary, matches=good.statements[
                                summary].slots.matches + 1)))
        found.append(Mutant("unsupported_positive", REJECTION,
                            "claim_unsupported",
                            reslot(good, items[0], match="icd_description")))
        absent = [templates.statement(
            "presence_absent", refs=("C1",),
            condition=good.statements[summary].slots.condition,
            scope=good.scope), candidates.notice("absence")]
        found.append(Mutant("negative_for_recorded_condition", REJECTION,
                            "claim_unsupported", good.model_copy(update={
                                "outcome": "not_recorded",
                                "statements": tuple(absent)})))
    elif good.outcome == "not_recorded":
        failed = sample.bundle.model_copy(update={
            "coverage": sample.bundle.coverage.model_copy(
                update={"status": "failed"})})
        found.append(Mutant("negative_without_complete_coverage", REJECTION,
                            "claim_unsupported", good, bundle=failed))
    return found


def explanation_mutants(sample):
    """Return mutants of an explanation's preservation (38)."""
    good, bundle = sample.candidate, sample.bundle
    faults = explanation.synthetic_faults(good, bundle)
    names = {0: "altered_score", 1: "altered_label",
             2: "altered_attribution_score", 3: "omitted_driver",
             4: "reordered_drivers", 5: "zero_score_driver_described",
             6: "restriction_misdisclosed"}
    found = [Mutant(names[index], REJECTION, expected, fault,
                    bundle=faulty)
             for index, (expected, fault, faulty) in enumerate(faults)
             if index in names]
    context = {event.ref for event in bundle.final_admission_context}
    for index in indices(good, "driver_history"):
        driver = next(item for item in bundle.attributions
                      if item.ref == good.statements[index].slots.driver)
        final = [ref for ref in driver.linked_event_refs if ref in context]
        if final:
            found.append(Mutant(
                "final_admission_record_as_history", REJECTION,
                "claim_unsupported",
                replace(good, index, refs=good.statements[index].refs
                        + (final[0],))))
            break
    return found


def bundle_mutants(sample):
    """Return mutants of the bundle a request is answered from (35)."""
    bundle, request = sample.bundle, sample.request
    if bundle is None or request is None:
        return []
    explaining = request.task == "prediction_explanation"
    stop = "case_rejection"
    found = [
        Mutant("bundle_of_another_case", stop, "identity_inconsistent",
               bundle=bundle.model_copy(update={"case": bundle.case.model_copy(
                   update={"case_number": bundle.case.case_number + 1000})})),
        Mutant("incomplete_coverage", "abstention",
               "evidence_insufficient" if explaining
               else "coverage_incomplete",
               bundle=bundle.model_copy(update={
                   "coverage": bundle.coverage.model_copy(
                       update={"status": "failed"})})),
    ]
    visits = bundle.visits
    gapped = (visits[1:] if len(visits) > 1 else (visits[0].model_copy(
        update={"visit_number": visits[0].visit_number + 1,
                "position": visits[0].position + 1}),))
    found.append(Mutant("history_gap", stop, "history_inconsistent",
                        bundle=bundle.model_copy(update={"visits": gapped})))
    events = candidates.bundle_events(bundle)
    if events:
        first = next(iter(events.values()))
        unreadable = first.model_copy(update={
            "concept": first.concept.model_copy(update={
                "name_status": "unavailable", "name": None,
                "name_source": None, "source_code": None}),
            "source_concept": None})
        field = "history_events" if explaining else "events"
        records = getattr(bundle, field)
        if records and records[0].ref == first.ref:
            found.append(Mutant(
                "record_without_name", stop, "evidence_inconsistent",
                bundle=bundle.model_copy(update={
                    field: (unreadable,) + tuple(records[1:])})))
    if explaining:
        found.append(Mutant(
            "prediction_not_selected", stop, "prediction_link_invalid",
            request=request.model_copy(update={
                "prediction": contracts.PredictionRef(
                    output_record_sha256="0" * 64)})))
    else:
        other = (contracts.ScopeRef(kind="case_visits")
                 if bundle.scope.kind != "case_visits"
                 else contracts.ScopeRef(kind="pre_target_history"))
        found.append(Mutant("bundle_of_another_scope", stop,
                            "scope_invalid",
                            bundle=bundle.model_copy(update={"scope": other})))
    return found


def mutants(sample):
    """Return every mutant that applies to a sample."""
    found = contract_mutants(sample) + wording_mutants(sample)
    task = sample.candidate.task
    if task == "condition_list":
        found += list_mutants(sample)
    elif task == "condition_presence":
        found += presence_mutants(sample)
    elif task == "prediction_explanation" and (
            sample.candidate.outcome == "explanation"):
        found += explanation_mutants(sample)
    if sample.candidate.outcome in ("answer", "not_recorded", "explanation"):
        found += bundle_mutants(sample)
    found.append(Mutant("check_fails_to_run", "operational_error",
                        "validator_failed", sample.candidate,
                        checks=[("crashing", crashing)]))
    return found


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------

def released(sample, candidate, bundle=None, checks=None):
    """Return (outcome, reason codes) of a candidate through the gate."""
    response = gate.release(candidate, candidate.request_id, sample.case,
                            sample.scope, bundle or sample.bundle
                            if candidate.bundle is not None else None,
                            sample.digest, sample.vocab, checks)
    return response.outcome, response.reason_codes


def result_of(sample, mutant):
    """Return (outcome, reason codes) of one mutant."""
    if mutant.document is not None:
        parsed, problems = structure.parse(mutant.document)
        return (REJECTION, problems) if parsed is None else ("parsed", ())
    if mutant.candidate is None:
        request = mutant.request or sample.request
        bundle = mutant.bundle or sample.bundle
        stop = (evidence.explanation_stop(request, bundle)
                if request.task == "prediction_explanation"
                else evidence.qa_stop(request, bundle))
        return (stop.outcome, stop.reasons) if stop else ("released", ())
    return released(sample, mutant.candidate, mutant.bundle, mutant.checks)


def run(samples):
    """Return the counts of a mutation run over some samples."""
    classes = collections.defaultdict(lambda: [0, 0])
    originals = [0, 0]
    for sample in samples:
        outcome, reasons = released(sample, sample.candidate)
        originals[0] += 1
        originals[1] += (outcome == sample.candidate.outcome
                         and reasons == sample.candidate.reason_codes)
        for mutant in mutants(sample):
            outcome, reasons = result_of(sample, mutant)
            entry = classes[mutant.error]
            entry[0] += 1
            if mutant.error.startswith("control_"):
                entry[1] += outcome == mutant.outcome
            else:
                entry[1] += (outcome == mutant.outcome
                             and mutant.reason in reasons)
    return {"originals": {"checked": originals[0], "passed": originals[1]},
            "classes": {error: {"mutants": entry[0], "detected": entry[1]}
                        for error, entry in sorted(classes.items())}}


def passed(counts):
    """Return True when every original and control passes and every
    mutant is detected."""
    return (counts["originals"]["checked"] == counts["originals"]["passed"]
            and all(entry["mutants"] == entry["detected"]
                    for entry in counts["classes"].values()))


# --------------------------------------------------------------------------
# Samples
# --------------------------------------------------------------------------

def qa_samples(case_set, bundle_set, vocab, requested):
    """Return QA samples of every scope bundle of a bundle set: a list, a
    presence check of a recorded condition and of one recorded elsewhere,
    and the answers to the questions requested (for refusals and
    clarifying questions)."""
    limits = contracts.ExecutionLimits(**bundles.SYNTHETIC_LIMITS)
    found, recorded = [], set()
    entries = sorted(bundle_set.entries, key=lambda key: (
        key[0], key[1].kind, key[1].visit_number or 0))
    loaded = []
    for case_number, scope in entries:
        bundle, digest = bundle_set.qa_bundle(case_number, scope)
        loaded.append((case_number, scope, bundle, digest))
        recorded.update(event.concept.concept_id for event in bundle.events
                        if event.concept.concept_id in vocab.rows)
    for index, (case_number, scope, bundle, digest) in enumerate(loaded, 1):
        case = contracts.CaseRef(case_set=case_set, case_number=case_number)
        ref = contracts.BundleRef(bundle_id=bundle.bundle_id, sha256=digest)

        def sample(candidate, task):
            """Return a sample of a candidate for a task in this scope."""
            request = contracts.Request(
                schema_version="1.0", request_id=candidate.request_id,
                task=task, case=case, scope=scope, question="Invented.")
            return Sample(candidate, request, case, scope, bundle, digest,
                          vocab)

        rid = "m-%05d" % index
        candidate, _ = templates.list_candidate(rid, case, scope, bundle,
                                                ref)
        found.append(sample(candidate, "condition_list"))
        mine = sorted({event.concept.concept_id for event in bundle.events
                       if event.concept.concept_id in vocab.rows})
        for concept in mine[:1] + [
                next((other for other in sorted(recorded) if not
                      answer.record_matches(bundle, resolve.Resolution(
                          "", "resolved", "concepts", concepts=(other,)),
                          vocab)), None)]:
            if concept is None:
                continue
            resolution = resolve.Resolution("", "resolved", "concepts",
                                            concepts=(concept,))
            matches = answer.record_matches(bundle, resolution, vocab)
            candidate = templates.presence_candidate(
                rid, case, scope, bundle, ref, resolution, matches)
            found.append(sample(candidate, "condition_presence"))
    for case_number, scope, question in requested:
        result = answer.answer(question, case_set, case_number, scope,
                               bundle_set, vocab, "m-question", limits)
        if result.candidate is not None:
            case = contracts.CaseRef(case_set=case_set,
                                     case_number=case_number)
            found.append(Sample(result.candidate, result.request, case,
                                scope, vocab=vocab))
    return found


def explanation_samples(pairs):
    """Return explanation samples of (request, bundle, digest) triples."""
    found = []
    for request, bundle, digest in pairs:
        _, candidate = explanation.explain(request, bundle, digest)
        found.append(Sample(candidate, request, request.case, request.scope,
                            bundle, digest))
    return found


QUESTIONS = ("What medications is the patient taking?",
             "Is florbnitis recorded?", "Is it recorded?",
             "List the conditions except the chronic ones.")


def synthetic_samples():
    """Return samples built from the invented fixtures."""
    vocab = suite.synthetic_vocabulary()
    bundle_set = suite.synthetic_bundle_set()
    history = contracts.ScopeRef(kind="pre_target_history")
    found = qa_samples("synthetic-1-v1", bundle_set, vocab,
                       [(1, history, question) for question in QUESTIONS])
    _, explanations, _ = bundles.run_synthetic()
    bundle = explanations[0]
    found += explanation_samples([(evidence.explanation_request(bundle),
                                   bundle, "5" * 64)])
    return found


def real_samples(case_set):
    """Return samples of a development case set."""
    if case_set != mapping.PILOT_LABEL and registry.spec(case_set)[
            "purpose"] != "development":
        raise MutationError("case_set_held_out")
    vocab = resolve.load()
    bundle_set = answer.load_bundle_set(answer.bundle_set_plan(case_set))
    plan = readers.real_plan(None if case_set == mapping.PILOT_LABEL
                             else case_set)
    cases = {case.case_number: case
             for case in readers.read_cases(plan).cases if case.admitted}
    history = contracts.ScopeRef(kind="pre_target_history")
    found = qa_samples(case_set, bundle_set, vocab,
                       [(number, history, question)
                        for number in sorted(cases)[:1]
                        for question in QUESTIONS])
    pairs = []
    for case_number, bundle, digest in explanation.read_bundles(case_set):
        case = cases[case_number]
        pairs.append((contracts.Request(
            schema_version="1.0", request_id="x-%03d" % case_number,
            task="prediction_explanation", case=bundle.case,
            scope=bundle.scope, prediction=contracts.PredictionRef(
                output_record_sha256=case.output_record_sha256)),
            bundle, digest))
    return found + explanation_samples(pairs)


def report(counts):
    """Print a mutation run: aggregate counts only."""
    originals = counts["originals"]
    print("%-44s %d of %d" % ("originals released unchanged",
                              originals["passed"], originals["checked"]))
    for error, entry in counts["classes"].items():
        label = ("control passed" if error.startswith("control_")
                 else "detected")
        print("%-44s %s %d of %d" % (error, label, entry["detected"],
                                     entry["mutants"]))
    totals = [0, 0]
    for error, entry in counts["classes"].items():
        if not error.startswith("control_"):
            totals[0] += entry["mutants"]
            totals[1] += entry["detected"]
    print("%-44s %d of %d in %d classes" % (
        "mutants detected", totals[1], totals[0],
        sum(1 for error in counts["classes"]
            if not error.startswith("control_"))))
    print("suite %s" % SUITE_VERSION)


def main(argv=None):
    """Run the mutation suite on invented data or a development case
    set."""
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
    try:
        samples = (real_samples(args.case_set) if args.operator_real
                   else synthetic_samples())
        counts = run(samples)
    except (MutationError, registry.RegistryError, readers.ReaderError,
            mapping.MappingError, answer.AnswerError,
            resolve.ResolutionError, explanation.ExplanationError,
            ValidationError) as error:
        print("FAILED: %s" % type(error).__name__)
        return 1
    report(counts)
    ok = passed(counts)
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
