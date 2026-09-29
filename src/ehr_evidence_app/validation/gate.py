"""The release gate (work item 40).

Every response with text leaves the application through release(). The
gate runs every check the candidate's answer requires, in order:

1. structure and references (validation.structure), always;
2. then, when those pass: the support of QA statements by their cited
   records (validation.support) for condition lists and presence checks,
   and (validation.record_support) for answers about the whole record,
   the preservation of the prediction and drivers
   (validation.explanation) for explanations, and the wording rules
   (validation.language) for every outcome;
3. the citation check of evidence.schema on the rendered response.

It maps what the checks find to one outcome:

* no problem: the response, rendered from the candidate's statements only,
  so no text is released that was not checked;
* problems: a validation rejection carrying their reason codes, with no
  text and no claims; the candidate is withheld;
* a check that fails to run: an operational error (validator_failed),
  with no text; nothing is released on an unchecked path.

Usage:
    python -m ehr_evidence_app.validation.gate
"""

from __future__ import annotations

import argparse
import sys

from ehr_evidence_app.evidence import contracts, schema
from ehr_evidence_app.evidence import vocabulary as vocabularies
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import language, record_support, \
    structure, support


def concept_namer(vocab):
    """Return a function giving a concept's vocabulary name, or None."""
    def name(concept):
        """Return one concept's name, or None when it is unknown."""
        row = vocab.rows.get(concept) if vocab is not None else None
        return row.name if row is not None else None
    return name


def required_checks(candidate, drivers=None, record=None):
    """Return the (name, check) pairs a candidate's answer requires after
    its structure; each check takes (candidate, bundle, vocab) and returns
    reason codes. drivers is how many drivers an explanation describes
    (the configured number when None); record is the
    validation.record_support.RecordCheck of an answer about the
    record."""
    # Imported here: validation.explanation releases through this gate.
    from ehr_evidence_app.validation import explanation
    checks = []
    if candidate.task in ("condition_list", "condition_presence"):
        checks.append(("support", support.check))
    if candidate.task in vocabularies.RECORD_TASKS:
        checks.append(("record_support",
                       lambda item, bundle, _: record_support.check(
                           item, bundle, record)))
    if candidate.task == "prediction_explanation":
        checks.append(("explanation",
                       lambda item, bundle, _: explanation.check(
                           item, bundle, drivers)))
    checks.append(("language", lambda item, *_: language.check(item)))
    return checks


def withheld(candidate, request_id, case, scope, outcome, reasons):
    """Return a response that releases no text."""
    return contracts.Response(
        schema_version="1.0", request_id=request_id, task=candidate.task,
        case=case, scope=scope, outcome=outcome, reason_codes=tuple(reasons))


def release(candidate, request_id, case, scope, bundle=None,
            bundle_sha256=None, vocab=None, checks=None, condition=None,
            drivers=None, record=None):
    """Return the Response of a candidate for the request (request_id,
    case, scope): rendered from it when every required check passes, a
    validation rejection when any finds a problem, an operational error
    when one fails to run.

    bundle is the bundle bound to the request, if any; condition is the
    ConditionName a presence question was resolved to; drivers is how many
    drivers an explanation describes (the configured number when None);
    record is what an answer about the record is checked against.
    checks replaces the required checks after structure (for tests).
    """
    concept_name = concept_namer(vocab)
    try:
        problems = set(structure.check(candidate, request_id, case, scope,
                                       bundle, bundle_sha256, concept_name,
                                       condition))
        if not problems:
            for _, check in (required_checks(candidate, drivers, record)
                             if checks is None else checks):
                problems.update(check(candidate, bundle, vocab))
        if not problems:
            response = candidates.response(candidate, bundle, concept_name)
            if bundle is not None:
                problems.update(schema.citation_problems(response, bundle,
                                                         bundle_sha256))
    except Exception:  # noqa: BLE001 - any failure of a check fails closed
        return withheld(candidate, request_id, case, scope,
                        "operational_error", ("validator_failed",))
    if problems:
        return withheld(candidate, request_id, case, scope,
                        "validation_rejection", sorted(problems))
    return response


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

def crashing(candidate, bundle, vocab):
    """A check that fails to run."""
    raise RuntimeError("check_failed")


def run_synthetic():
    """Return (released, rejected, crashed) outcomes on invented data: a
    correct candidate, a candidate with an unapproved sentence, and a
    correct candidate under a check that fails to run."""
    bundle, digest = structure.synthetic_bundle()
    good = structure.synthetic_list(bundle, digest)
    items = list(good.statements)
    items[-1] = items[-1].model_copy(update={
        "wording": items[-1].wording + " Nothing else was checked."})
    unchecked = good.model_copy(update={"statements": tuple(items)})
    outcomes = []
    for candidate, checks in ((good, None), (unchecked, None),
                              (good, [("crashing", crashing)])):
        outcomes.append(release(candidate, "q-1", good.case, good.scope,
                                bundle, digest, checks=checks).outcome)
    return tuple(outcomes)


def main(argv=None):
    """Release, withhold and fail closed on invented candidates."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    outcomes = run_synthetic()
    expected = ("answer", "validation_rejection", "operational_error")
    for label, outcome in zip(("correct candidate",
                               "candidate with an unapproved sentence",
                               "check that fails to run"), outcomes):
        print("%-40s %s" % (label, outcome))
    ok = outcomes == expected
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
