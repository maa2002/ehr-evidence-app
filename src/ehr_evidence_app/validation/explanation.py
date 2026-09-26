"""Template explanations and their preservation checks (work item 38).

The template explanation (ADR 0011, decision 5) is built from an
explanation bundle alone, without a model:

* the selected label, as one output of the model's multi-label prediction,
  with its uncalibrated score and logit (citing P1);
* every driver that may be described, in the order the model lists them,
  with its attribution score (citing its attribution);
* under each driver, how many of its concept's records lie in which
  pre-target visits, citing its linked history events, and its records in
  the final admission as context, citing those;
* notices on what attribution scores and the model's score mean, and one
  for each restriction of the case.

The checks apply to any explanation candidate, whoever wrote it:

* prediction_altered: the explanation does not state the bundle's
  prediction once, with its label, score and logit;
* attribution_altered: the drivers described are not exactly the bundle's
  describable drivers, in its order and with its scores and ranks;
* claim_unsupported: a history statement cites other records than the
  driver's linked events in pre-target visits, a context statement other
  records than its final-admission ones, or either miscounts them or their
  visits; the restrictions disclosed, as reason codes or notices, are not
  the case's;
* structure_invalid: a notice on what the scores mean is missing.

With the structure checks and the wording rules this enforces the evidence
claim policy: no score is tied to a date, a visit or a recurrence (history
statements carry no score, driver statements no visits), history cites
linked events in pre-target visits only, final-admission records are
context only, and zero-score drivers are never described.

Usage:
    python -m ehr_evidence_app.validation.explanation
    python -m ehr_evidence_app.validation.explanation --operator-real \\
        --case-set dev-30-v1
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import sys

from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import bundles, contracts, readers, schema
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import evidence, gate

TEMPLATE_VERSION = "explanation-template-v1"
WORDINGS = {
    "prediction": "{label} is the label the model selected, one output of "
                  "its multi-label prediction; its uncalibrated score is "
                  "{score} (logit {logit}).",
    "driver": "{rank}. {driver}: attribution score {score}",
    "driver_history": "{driver}: {count} in {visits}, before the final "
                      "admission",
    "driver_context": "{driver}: {count} in the final admission, as context",
}
MEANING_NOTICES = ("drivers", "attribution_meaning", "score_meaning")
# The abstention notice of each reason an explanation is not given.
ABSTENTIONS = {"explanation_ineligible": "explanation_ineligible",
               "no_positive_attribution": "no_positive_attribution",
               "evidence_insufficient": "explanation_incomplete"}


class ExplanationError(Exception):
    """A fail-closed failure carrying a finding code only."""


def describable(bundle):
    """Return the drivers an explanation describes, in the bundle's order."""
    return [item for item in bundle.attributions if item.narration_eligible]


def visits_of(bundle, refs):
    """Return the sorted visit numbers of some events."""
    events = candidates.bundle_events(bundle)
    numbers = {visit.ref: visit.visit_number for visit in bundle.visits}
    return tuple(sorted({numbers[events[ref].visit_ref] for ref in refs}))


def statement(kind, refs=(), **slots):
    """Return a statement of a kind with the template wording."""
    return candidates.Statement(kind=kind, wording=WORDINGS[kind],
                                slots=candidates.Slots(**slots),
                                refs=tuple(refs))


def template(request, bundle, bundle_sha256):
    """Return the template explanation candidate of an eligible bundle."""
    history = {event.ref for event in bundle.history_events}
    context = {event.ref for event in bundle.final_admission_context}
    prediction = bundle.prediction
    statements = [statement("prediction", refs=("P1",),
                            label=prediction.concept.concept_id,
                            score=prediction.score, logit=prediction.logit),
                  candidates.notice("drivers")]
    for rank, item in enumerate(describable(bundle), 1):
        statements.append(statement("driver", refs=(item.ref,), rank=rank,
                                    driver=item.ref, score=item.score))
        linked = [ref for ref in item.linked_event_refs if ref in history]
        if linked:
            statements.append(statement(
                "driver_history", refs=linked, driver=item.ref,
                count=len(linked), visits=visits_of(bundle, linked)))
        final = [ref for ref in item.linked_event_refs if ref in context]
        if final:
            statements.append(statement("driver_context", refs=final,
                                        driver=item.ref, count=len(final)))
    statements += [candidates.notice("attribution_meaning"),
                   candidates.notice("score_meaning")]
    statements += [candidates.notice(candidates.RESTRICTION_NOTICES[code])
                   for code in bundle.restrictions]
    return candidates.Candidate(
        schema_version=candidates.CANDIDATE_VERSION,
        request_id=request.request_id, task="prediction_explanation",
        case=request.case, scope=request.scope, outcome="explanation",
        reason_codes=tuple(bundle.restrictions),
        bundle=contracts.BundleRef(bundle_id=bundle.bundle_id,
                                   sha256=bundle_sha256),
        statements=tuple(statements))


def of_kind(candidate, kind):
    """Return a candidate's statements of one kind."""
    return [item for item in candidate.statements if item.kind == kind]


def prediction_problems(candidate, bundle):
    """Return problems of the prediction an explanation states."""
    stated = of_kind(candidate, "prediction")
    prediction = bundle.prediction
    if len(stated) != 1 or (stated[0].slots.label, stated[0].slots.score,
                            stated[0].slots.logit) != (
            prediction.concept.concept_id, prediction.score,
            prediction.logit):
        return {"prediction_altered"}
    return set()


def driver_problems(candidate, bundle):
    """Return problems of the drivers an explanation describes."""
    attributions = {item.ref: item for item in bundle.attributions}
    stated = of_kind(candidate, "driver")
    if [item.slots.driver for item in stated] != [
            item.ref for item in describable(bundle)]:
        return {"attribution_altered"}
    for rank, item in enumerate(stated, 1):
        if (item.slots.rank != rank or item.slots.score
                != attributions[item.slots.driver].score):
            return {"attribution_altered"}
    return set()


def record_problems(candidate, bundle):
    """Return problems of the history and context an explanation states."""
    attributions = {item.ref: item for item in bundle.attributions}
    allowed = {"driver_history": {event.ref
                                  for event in bundle.history_events},
               "driver_context": {event.ref for event in
                                  bundle.final_admission_context}}
    for kind, refs in allowed.items():
        seen = set()
        for item in of_kind(candidate, kind):
            driver = attributions.get(item.slots.driver)
            if driver is None or not driver.narration_eligible or (
                    item.slots.driver in seen):
                return {"claim_unsupported"}
            seen.add(item.slots.driver)
            linked = {ref for ref in driver.linked_event_refs if ref in refs}
            if (set(item.refs) != linked
                    or item.slots.count != len(linked)
                    or (kind == "driver_history" and tuple(
                        item.slots.visits) != visits_of(bundle, linked))):
                return {"claim_unsupported"}
    return set()


def notice_problems(candidate, bundle):
    """Return problems of the notices and disclosures of an explanation."""
    notices = {item.notice for item in candidate.statements
               if item.kind == "notice"}
    if not set(MEANING_NOTICES) <= notices:
        return {"structure_invalid"}
    restrictions = set(candidates.RESTRICTION_NOTICES.values())
    if (notices & restrictions != {candidates.RESTRICTION_NOTICES[code]
                                   for code in bundle.restrictions}
            or set(candidate.reason_codes) != set(bundle.restrictions)):
        return {"claim_unsupported"}
    return set()


def check(candidate, bundle=None):
    """Return the reason codes of an explanation candidate that does not
    preserve its bundle's prediction, drivers, records and restrictions;
    empty when it passes. The candidate must have passed the structure
    checks for this bundle."""
    if candidate.outcome != "explanation" or bundle is None:
        return ()
    problems = (prediction_problems(candidate, bundle)
                | driver_problems(candidate, bundle)
                | record_problems(candidate, bundle)
                | notice_problems(candidate, bundle))
    return tuple(sorted(problems))


def rejected(request, reasons, outcome="validation_rejection"):
    """Return a response to an explanation request that releases no
    text."""
    return contracts.Response(
        schema_version="1.0", request_id=request.request_id,
        task="prediction_explanation", case=request.case,
        scope=request.scope, outcome=outcome, reason_codes=tuple(reasons))


def release(candidate, request, bundle=None, bundle_sha256=None):
    """Return the Response of an explanation candidate for a request,
    through the release gate."""
    return gate.release(candidate, request.request_id, request.case,
                        request.scope, bundle, bundle_sha256)


def explain(request, bundle, bundle_sha256):
    """Return (Response, candidate or None) for an explanation request and
    its verified explanation bundle."""
    stop = evidence.explanation_stop(request, bundle)
    if stop is not None and stop.outcome != "abstention":
        return rejected(request, stop.reasons, stop.outcome), None
    if stop is not None:
        candidate = candidates.Candidate(
            schema_version=candidates.CANDIDATE_VERSION,
            request_id=request.request_id, task="prediction_explanation",
            case=request.case, scope=request.scope, outcome="abstention",
            reason_codes=stop.reasons, statements=(candidates.notice(
                ABSTENTIONS[stop.reasons[0]]),))
        return release(candidate, request), candidate
    candidate = template(request, bundle, bundle_sha256)
    return release(candidate, request, bundle, bundle_sha256), candidate


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

def synthetic_faults(good, bundle):
    """Return (expected reason code, candidate, bundle) triples of invented
    faults of a correct explanation."""
    def changed(kind, **slots):
        """Return the candidate with the first statement of a kind's slots
        changed."""
        items = list(good.statements)
        index = next(number for number, item in enumerate(items)
                     if item.kind == kind)
        items[index] = items[index].model_copy(update={
            "slots": items[index].slots.model_copy(update=slots)})
        return good.model_copy(update={"statements": tuple(items)})

    drivers = [index for index, item in enumerate(good.statements)
               if item.kind == "driver"]
    first = good.statements[drivers[0]]
    swapped = list(good.statements)
    swapped[drivers[0]], swapped[drivers[1]] = (
        swapped[drivers[1]].model_copy(update={"slots": swapped[
            drivers[1]].slots.model_copy(update={"rank": 1})}),
        swapped[drivers[0]].model_copy(update={"slots": swapped[
            drivers[0]].slots.model_copy(update={"rank": 2})}))
    # The same explanation, for a bundle whose first driver scores zero.
    zeroed = bundle.model_copy(update={"attributions": tuple(
        item.model_copy(update={"score": 0.0, "score_class": "zero",
                                "narration_eligible": False})
        if item.ref == first.slots.driver else item
        for item in bundle.attributions)})
    return [
        ("prediction_altered", changed("prediction",
                                       score=bundle.prediction.score / 2),
         bundle),
        ("prediction_altered", changed("prediction", logit=-1.0), bundle),
        ("attribution_altered", changed("driver",
                                        score=first.slots.score * 2), bundle),
        ("attribution_altered", good.model_copy(update={
            "statements": tuple(item for item in good.statements
                                if item is not first)}), bundle),
        ("attribution_altered", good.model_copy(update={
            "statements": tuple(swapped)}), bundle),
        ("attribution_altered", good, zeroed),
        # Restrictions disclosed that are not the case's: none for a case
        # that has some, one for a case that has none.
        ("claim_unsupported", good.model_copy(update={
            "reason_codes": () if bundle.restrictions
            else ("batch_sensitive_prediction",)}), bundle),
        ("structure_invalid", good.model_copy(update={
            "statements": tuple(item for item in good.statements
                                if item.notice != "attribution_meaning")}),
         bundle),
    ]


def run_synthetic():
    """Return (explained, faults detected, faults) on invented data."""
    _, explanations, _ = bundles.run_synthetic()
    bundle = explanations[0]
    request = evidence.explanation_request(bundle)
    response, good = explain(request, bundle, "5" * 64)
    explained = response.outcome == "explanation"
    detected = 0
    faults = synthetic_faults(good, bundle)
    for expected, fault, faulty in faults:
        found = release(fault, request, faulty, "5" * 64)
        detected += (found.outcome == "validation_rejection"
                     and expected in found.reason_codes)
    return explained, detected, len(faults)


# --------------------------------------------------------------------------
# Operator mode (real development case sets; aggregate counts only)
# --------------------------------------------------------------------------

def bundle_plan(case_set):
    """Return (directory, pinned manifest entry) of a case set's bundles."""
    if case_set == mapping.PILOT_LABEL:
        directory = bundles.OUTPUT_DIR
        return directory, {"path": directory + "\\" + bundles.MANIFEST_NAME,
                           "size_bytes": bundles.PILOT_MANIFEST_SIZE,
                           "sha256": bundles.PILOT_MANIFEST_SHA256}
    if registry.spec(case_set)["purpose"] != "development":
        raise ExplanationError("case_set_held_out")
    return (registry.bundles_dir(case_set),
            registry.pinned(case_set, "bundles"))


def read_bundles(case_set):
    """Return (case number, bundle, SHA-256) of every explanation bundle of
    a development case set, each verified against the pinned manifest."""
    directory, entry = bundle_plan(case_set)
    data, _ = mapping.read_verified(
        entry["path"], "bundle_manifest",
        "input_hash_mismatch:bundle_manifest", sha256=entry["sha256"],
        size=entry["size_bytes"])
    manifest = json.loads(data.decode("utf-8"))
    if manifest.get("schema") != bundles.SCHEMA or manifest.get(
            "case_set") != case_set:
        raise ExplanationError("bundle_manifest_unexpected")
    found = []
    for item in manifest.get("files") or []:
        if item.get("kind") != "explanation":
            continue
        path = os.path.join(directory, *item["path"].split("/"))
        content, _ = mapping.read_verified(
            path, item["bundle_id"], "bundle_hash_mismatch",
            sha256=item["sha256"], size=item["size_bytes"])
        found.append((item["case_number"],
                      schema.ExplanationBundle.model_validate_json(content),
                      item["sha256"]))
    return found


def run_real(case_set):
    """Explain the selected prediction of every admitted case of a
    development case set; return aggregate counts."""
    plan = readers.real_plan(None if case_set == mapping.PILOT_LABEL
                             else case_set)
    cases = {case.case_number: case
             for case in readers.read_cases(plan).cases if case.admitted}
    counts = collections.Counter()
    for case_number, bundle, digest in read_bundles(case_set):
        case = cases.get(case_number)
        if case is None:
            raise ExplanationError("bundle_case_not_admitted")
        request = contracts.Request(
            schema_version="1.0", request_id="x-%03d" % case_number,
            task="prediction_explanation",
            case=contracts.CaseRef(case_set=case_set,
                                   case_number=case_number),
            scope=contracts.ScopeRef(kind="pre_target_history"),
            prediction=contracts.PredictionRef(
                output_record_sha256=case.output_record_sha256))
        response, candidate = explain(request, bundle, digest)
        counts["cases"] += 1
        counts["outcome " + response.outcome] += 1
        for code in response.reason_codes:
            counts["reason " + code] += 1
        if response.outcome == "explanation":
            for item in candidate.statements:
                counts["statements " + (item.notice or item.kind)] += 1
            counts["claims"] += len(response.claims)
    return dict(sorted(counts.items()))


def main(argv=None):
    """Run the synthetic default mode or the operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="explain every case of a development case set; "
                             "prints aggregate counts only")
    parser.add_argument("--case-set",
                        help="the development case set (dev-30-v1 or "
                             "pilot-10-v1)")
    args = parser.parse_args(argv)
    if args.operator_real != bool(args.case_set):
        print("FAILED: --operator-real and --case-set go together")
        return 2
    if args.operator_real:
        try:
            counts = run_real(args.case_set)
        except (ExplanationError, registry.RegistryError,
                readers.ReaderError, mapping.MappingError) as error:
            print("FAILED: %s" % error)
            return 1
        for key, value in counts.items():
            print("%-48s %d" % (key, value))
        ok = counts.get("outcome validation_rejection", 0) == 0
        print("template %s" % TEMPLATE_VERSION)
        print("RESULT: %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    explained, detected, total = run_synthetic()
    print("template explanation released: %s" % ("yes" if explained
                                                 else "no"))
    print("faults detected with their reason code: %d of %d" % (detected,
                                                                total))
    ok = explained and detected == total
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
