"""Template explanations and their preservation checks (work item 38).

In Phase 7 the language model writes the explanation when a writer is
given (explain_written, generation.adapter), and the template explanation
is released in its place when the model's is not. An explanation
describes the drivers selected by config/explanation.json (ADR 0013): the
eligible attributions with the highest scores, highest first, ties in the
bundle's order, at most the configured limit. Every attribution stays in
the evidence bundle. The template explanation (explanation-template-v3,
with its checks) is built from an explanation bundle alone, without a
model:

* the selected label, as one output of the model's multi-label prediction,
  with its uncalibrated score and logit (citing P1);
* the selected drivers, highest score first, each with its attribution
  score (citing its attribution);
* under each driver, how many of its concept's records lie in which
  pre-target visits, citing its linked history events, and its records in
  the final admission as context, citing those;
* when more drivers were eligible, a notice that states how many are kept
  in the evidence and not described;
* notices on what attribution scores and the model's score mean, and one
  for each restriction of the case.

The checks apply to any explanation candidate, whoever wrote it:

* prediction_altered: the explanation does not state the bundle's
  prediction once, with its label, and its score and logit as a response
  shows them (to three significant figures);
* attribution_altered: the drivers described are not exactly the selected
  drivers, in their order and with their ranks, and their scores as a
  response shows them;
* claim_unsupported: a history statement cites other records than the
  driver's linked events in pre-target visits, a context statement other
  records than its final-admission ones, or either miscounts them or their
  visits; the restrictions disclosed, as reason codes or notices, are not
  the case's; the drivers not described are not stated, or miscounted;
* structure_invalid: a notice on what the scores mean is missing.

With the structure checks and the wording rules this enforces the evidence
claim policy: no score is tied to a date, a visit or a recurrence (history
statements carry no score, driver statements no visits), history cites
linked events in pre-target visits only, final-admission records are
context only, and zero-score drivers are never described. A driver's
history or context may be left out: a statement that is made cites all of
its records, since it states their count, but none is required, and how
complete an explanation is is measured apart from its release
(generation.measure). A score or logit is the claim a response shows, so
it is checked as shown; the template states the exact values.

Usage:
    python -m ehr_evidence_app.validation.explanation
    python -m ehr_evidence_app.validation.explanation --operator-real \\
        --case-set dev-30-v1
"""

from __future__ import annotations

import argparse
import collections
import functools
import json
import math
import os
import sys
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError

from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import bundles, contracts, readers, schema
from ehr_evidence_app.evidence.contracts import Frozen
from ehr_evidence_app.generation import adapter, shape
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import evidence, gate

TEMPLATE_VERSION = "explanation-template-v3"
SELECTION_PATH = os.path.join(contracts.REPO_ROOT, "config",
                              "explanation.json")
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


class DriverSelection(Frozen):
    """Which drivers an explanation describes."""

    selection: Literal["highest_eligible_attribution"]
    limit: int = Field(ge=1, le=100)
    frozen: bool


class SelectionFile(Frozen):
    """The committed explanation configuration."""

    schema_version: Literal["1.0"]
    purpose: Annotated[str, StringConstraints(min_length=1, max_length=600)]
    drivers: DriverSelection


def load_selection(path=SELECTION_PATH):
    """Return the validated driver selection."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return SelectionFile.model_validate_json(handle.read()).drivers
    except OSError:
        raise ExplanationError("explanation_config_unreadable") from None
    except ValidationError:
        raise ExplanationError("explanation_config_invalid") from None


@functools.lru_cache(maxsize=1)
def configured_limit():
    """Return the configured number of drivers an explanation describes."""
    return load_selection().limit


def describable(bundle):
    """Return every driver that may be described, in the bundle's order."""
    return [item for item in bundle.attributions if item.narration_eligible]


def selected(bundle, limit=None):
    """Return the drivers an explanation describes: the eligible drivers
    with the highest attribution scores, highest first, ties in the
    bundle's order, at most limit (the configured limit when None)."""
    limit = configured_limit() if limit is None else limit
    ranked = sorted(enumerate(describable(bundle)),
                    key=lambda pair: (-pair[1].score, pair[0]))
    return [item for _, item in ranked[:limit]]


def omitted(bundle, limit=None):
    """Return how many eligible drivers an explanation does not
    describe."""
    return len(describable(bundle)) - len(selected(bundle, limit))


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


def template(request, bundle, bundle_sha256, limit=None):
    """Return the template explanation candidate of an eligible bundle,
    describing the selected drivers (limit as in selected())."""
    history = {event.ref for event in bundle.history_events}
    context = {event.ref for event in bundle.final_admission_context}
    prediction = bundle.prediction
    statements = [statement("prediction", refs=("P1",),
                            label=prediction.concept.concept_id,
                            score=prediction.score, logit=prediction.logit),
                  candidates.notice("drivers")]
    for rank, item in enumerate(selected(bundle, limit), 1):
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
    left = omitted(bundle, limit)
    if left:
        statements.append(candidates.notice("drivers_omitted", omitted=left))
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


def shown_equal(stated, value):
    """Return True when a stated score or logit reads as the evidence's
    value in a response (to three significant figures)."""
    return stated is not None and (candidates.shown(stated)
                                   == candidates.shown(value))


def prediction_problems(candidate, bundle):
    """Return problems of the prediction an explanation states."""
    stated = of_kind(candidate, "prediction")
    prediction = bundle.prediction
    if len(stated) != 1 or stated[0].slots.label != (
            prediction.concept.concept_id) or not shown_equal(
                stated[0].slots.score, prediction.score) or not shown_equal(
                    stated[0].slots.logit, prediction.logit):
        return {"prediction_altered"}
    return set()


def driver_problems(candidate, bundle, limit=None):
    """Return problems of the drivers an explanation describes: exactly
    the selected drivers, in their order, with their ranks and scores."""
    attributions = {item.ref: item for item in bundle.attributions}
    stated = of_kind(candidate, "driver")
    if [item.slots.driver for item in stated] != [
            item.ref for item in selected(bundle, limit)]:
        return {"attribution_altered"}
    for rank, item in enumerate(stated, 1):
        if item.slots.rank != rank or not shown_equal(
                item.slots.score, attributions[item.slots.driver].score):
            return {"attribution_altered"}
    return set()


def record_problems(candidate, bundle):
    """Return problems of the history and context an explanation states:
    each statement made cites all of its driver's such records, with their
    count and, for history, their visits; none is required."""
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


def notice_problems(candidate, bundle, limit=None):
    """Return problems of the notices and disclosures of an explanation:
    the meaning notices, the case's restrictions, and the drivers not
    described, stated exactly when there are some, with their number."""
    notices = {item.notice for item in candidate.statements
               if item.kind == "notice"}
    if not set(MEANING_NOTICES) <= notices:
        return {"structure_invalid"}
    restrictions = set(candidates.RESTRICTION_NOTICES.values())
    if (notices & restrictions != {candidates.RESTRICTION_NOTICES[code]
                                   for code in bundle.restrictions}
            or set(candidate.reason_codes) != set(bundle.restrictions)):
        return {"claim_unsupported"}
    left = omitted(bundle, limit)
    stated = [item.slots.omitted for item in candidate.statements
              if item.notice == "drivers_omitted"]
    if stated != ([left] if left else []):
        return {"claim_unsupported"}
    return set()


def check(candidate, bundle=None, limit=None):
    """Return the reason codes of an explanation candidate that does not
    preserve its bundle's prediction, selected drivers, records and
    restrictions; empty when it passes. The candidate must have passed the
    structure checks for this bundle; limit is as in selected()."""
    if candidate.outcome != "explanation" or bundle is None:
        return ()
    problems = (prediction_problems(candidate, bundle)
                | driver_problems(candidate, bundle, limit)
                | record_problems(candidate, bundle)
                | notice_problems(candidate, bundle, limit))
    return tuple(sorted(problems))


def rejected(request, reasons, outcome="validation_rejection"):
    """Return a response to an explanation request that releases no
    text."""
    return contracts.Response(
        schema_version="1.0", request_id=request.request_id,
        task="prediction_explanation", case=request.case,
        scope=request.scope, outcome=outcome, reason_codes=tuple(reasons))


def release(candidate, request, bundle=None, bundle_sha256=None,
            limit=None):
    """Return the Response of an explanation candidate for a request,
    through the release gate (limit as in selected())."""
    return gate.release(candidate, request.request_id, request.case,
                        request.scope, bundle, bundle_sha256, drivers=limit)


def explain(request, bundle, bundle_sha256, writer=None, limit=None,
            recorder=None):
    """Return (Response, candidate or None) for an explanation request and
    its verified explanation bundle; explain_written() also says who
    wrote it. recorder (a generation.records.Recorder), when given, writes
    the protected record of the response."""
    written = explain_written(request, bundle, bundle_sha256, writer, limit)
    if recorder is not None:
        recorder.record(written.response, request,
                        bundle_sha256=bundle_sha256, written=written)
    return written.response, written.candidate


def explain_written(request, bundle, bundle_sha256, writer=None,
                    limit=None):
    """Return the generation.adapter.Written of an explanation request.

    A request the checks before generation stop is rejected, or abstains
    with its notice. An eligible explanation is written by the language
    model when a writer (a generation.adapter.Writer) is given, and by the
    template in its place when the model's candidate is not released or
    there is no writer (a recorded fallback).
    """
    stop = evidence.explanation_stop(request, bundle)
    if stop is not None and stop.outcome != "abstention":
        return adapter.Written(rejected(request, stop.reasons, stop.outcome),
                               None, "template")
    if stop is not None:
        candidate = candidates.Candidate(
            schema_version=candidates.CANDIDATE_VERSION,
            request_id=request.request_id, task="prediction_explanation",
            case=request.case, scope=request.scope, outcome="abstention",
            reason_codes=stop.reasons, statements=(candidates.notice(
                ABSTENTIONS[stop.reasons[0]]),))
        return adapter.Written(release(candidate, request), candidate,
                               "template")
    limit = configured_limit() if limit is None else limit
    drafted = template(request, bundle, bundle_sha256, limit)
    return adapter.write(
        writer, shape.Context(request, bundle, bundle_sha256, limit=limit),
        drafted, lambda candidate: release(candidate, request, bundle,
                                           bundle_sha256, limit))


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

def driver_blocks(candidate):
    """Return (head, blocks, tail) of an explanation in its order: the
    statements before the first driver, each driver with the lines under
    it, and the notices after them."""
    items = list(candidate.statements)
    first = next(index for index, item in enumerate(items)
                 if item.kind == "driver")
    end = first
    while end < len(items) and items[end].kind != "notice":
        end += 1
    blocks = []
    for item in items[first:end]:
        if item.kind == "driver":
            blocks.append([])
        blocks[-1].append(item)
    return items[:first], blocks, items[end:]


def ranked(block, rank):
    """Return a driver block with its driver's rank changed."""
    driver = block[0]
    return [driver.model_copy(update={"slots": driver.slots.model_copy(
        update={"rank": rank})})] + block[1:]


def synthetic_faults(good, bundle):
    """Return (error class, expected reason code, candidate, bundle) of
    invented faults of a correct explanation of a bundle."""
    def changed(kind, **slots):
        """Return the candidate with the first statement of a kind's slots
        changed."""
        items = list(good.statements)
        index = next(number for number, item in enumerate(items)
                     if item.kind == kind)
        items[index] = items[index].model_copy(update={
            "slots": items[index].slots.model_copy(update=slots)})
        return good.model_copy(update={"statements": tuple(items)})

    def assembled(blocks):
        """Return the candidate with its driver blocks replaced."""
        return good.model_copy(update={"statements": tuple(
            head + [item for block in blocks for item in block] + tail)})

    head, blocks, tail = driver_blocks(good)
    first = blocks[0][0]
    # The drivers without the first, ranked again; the first two swapped.
    dropped = assembled([ranked(block, rank)
                         for rank, block in enumerate(blocks[1:], 1)])
    swapped = assembled([ranked(blocks[1], 1), ranked(blocks[0], 2)]
                        + blocks[2:]) if len(blocks) > 1 else None
    # The same explanation, for a bundle whose first driver scores zero.
    zeroed = bundle.model_copy(update={"attributions": tuple(
        item.model_copy(update={"score": 0.0, "score_class": "zero",
                                "narration_eligible": False})
        if item.ref == first.slots.driver else item
        for item in bundle.attributions)})
    faults = [
        ("altered_score", "prediction_altered", changed(
            "prediction", score=bundle.prediction.score / 2), bundle),
        ("altered_label", "prediction_altered", changed(
            "prediction", logit=-1.0), bundle),
        ("altered_attribution_score", "attribution_altered", changed(
            "driver", score=first.slots.score * 2), bundle),
        ("zero_score_driver_described", "attribution_altered", good,
         zeroed),
        # Restrictions disclosed that are not the case's: none for a case
        # that has some, one for a case that has none.
        ("restriction_misdisclosed", "claim_unsupported", good.model_copy(
            update={"reason_codes": () if bundle.restrictions
                    else ("batch_sensitive_prediction",)}), bundle),
        ("missing_meaning_notice", "structure_invalid", good.model_copy(
            update={"statements": tuple(
                item for item in good.statements
                if item.notice != "attribution_meaning")}), bundle),
    ]
    if swapped is not None:
        # With one driver described, leaving it out is a structural fault.
        faults += [("omitted_driver", "attribution_altered", dropped, bundle),
                   ("reordered_drivers", "attribution_altered", swapped,
                    bundle)]
    # A score shown wrong in its last figure.
    faults += [("score_wrong_as_shown", "prediction_altered", changed(
                    "prediction", score=next_shown(bundle.prediction.score)),
                bundle),
               ("attribution_score_wrong_as_shown", "attribution_altered",
                changed("driver", score=next_shown(first.slots.score)),
                bundle)]
    lined = [index for index, block in enumerate(blocks) if len(block) > 1]
    if lined and len(blocks) > 1:
        # A driver's history and context under another driver.
        index = lined[0]
        other = 1 if index == 0 else 0
        moved = [list(block) for block in blocks]
        moved[other] = moved[other] + moved[index][1:]
        moved[index] = moved[index][:1]
        faults.append(("history_under_another_driver", "structure_invalid",
                       assembled(moved), bundle))
    notices = [number for number, item in enumerate(good.statements)
               if item.notice == "drivers_omitted"]
    if notices:
        # The drivers not described, miscounted or left unstated; one of
        # them described as well.
        extra = [item for item in describable(bundle) if item.ref not in {
            block[0].slots.driver for block in blocks}][0]
        described = assembled(blocks + [[statement(
            "driver", refs=(extra.ref,), rank=len(blocks) + 1,
            driver=extra.ref, score=extra.score)]])
        miscount = list(good.statements)
        notice = miscount[notices[0]]
        miscount[notices[0]] = notice.model_copy(update={
            "slots": notice.slots.model_copy(update={
                "omitted": notice.slots.omitted + 1})})
        faults += [
            ("omitted_drivers_miscounted", "claim_unsupported",
             good.model_copy(update={"statements": tuple(miscount)}),
             bundle),
            ("omitted_drivers_unstated", "claim_unsupported",
             good.model_copy(update={"statements": tuple(
                 item for number, item in enumerate(good.statements)
                 if number != notices[0])}), bundle),
            ("unselected_driver_described", "attribution_altered",
             described, bundle)]
    return faults


def next_shown(value):
    """Return a value shown one unit apart from another in its last shown
    figure, counted on the value as shown (0.9996 is shown as 1)."""
    shown = float(candidates.shown(value))
    exponent = math.floor(math.log10(abs(shown))) if shown else -3
    return shown + 10.0 ** (exponent - 2)


def synthetic_controls(good, bundle):
    """Return (name, candidate, bundle) of invented variants of a correct
    explanation that must be released: a driver's history or context left
    out, and scores stated to the figures a response shows."""
    def without(kind):
        """Return the candidate without its first statement of a kind."""
        index = next((number for number, item in enumerate(good.statements)
                      if item.kind == kind), None)
        if index is None:
            return None
        return good.model_copy(update={"statements": tuple(
            item for number, item in enumerate(good.statements)
            if number != index)})

    def restated(kind, name):
        """Return the candidate with a slot of its first statement of a
        kind stated only to the figures shown."""
        items = list(good.statements)
        index = next(number for number, item in enumerate(items)
                     if item.kind == kind)
        value = float(candidates.shown(getattr(items[index].slots, name)))
        items[index] = items[index].model_copy(update={
            "slots": items[index].slots.model_copy(update={name: value})})
        return good.model_copy(update={"statements": tuple(items)})

    found = [("driver_history_left_out", without("driver_history")),
             ("driver_context_left_out", without("driver_context")),
             ("score_as_shown", restated("prediction", "score")),
             ("attribution_score_as_shown", restated("driver", "score"))]
    return [(name, candidate, bundle) for name, candidate in found
            if candidate is not None]


def run_synthetic():
    """Return (explained, faults and controls as expected, faults and
    controls) on invented data, for the configured number of drivers and
    for one driver only."""
    _, explanations, _ = bundles.run_synthetic()
    bundle = explanations[0]
    request = evidence.explanation_request(bundle)
    explained, detected, total = True, 0, 0
    for limit in (None, 1):
        response, good = explain(request, bundle, "5" * 64, limit=limit)
        explained = explained and response.outcome == "explanation"
        for _, expected, fault, faulty in synthetic_faults(good, bundle):
            found = release(fault, request, faulty, "5" * 64, limit)
            total += 1
            detected += (found.outcome == "validation_rejection"
                         and expected in found.reason_codes)
        for _, control, faulty in synthetic_controls(good, bundle):
            found = release(control, request, faulty, "5" * 64, limit)
            total += 1
            detected += found.outcome == "explanation"
    return explained, detected, total


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
            counts["drivers not described"] += omitted(bundle)
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
    print("faults detected with their reason code, and controls "
          "released: %d of %d" % (detected, total))
    ok = explained and detected == total
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
