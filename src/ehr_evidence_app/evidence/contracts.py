#!/usr/bin/env python3
"""Request, settings and response contracts (work item 23).

A request binds one task to one case and one explicit scope; a response
carries exactly one of eight outcomes. The rules that make outcomes
distinct are enforced structurally here, before any language model exists:

* QA tasks need a question and carry no prediction reference; an
  explanation request needs a prediction reference, carries no question and
  uses the pre-target history scope.
* A selected-visit scope names its visit; the other scopes name none.
* Text is released only for answer, not-recorded, explanation,
  clarification and abstention outcomes. Case rejection, validation
  rejection and operational error release no text and no claims.
* Every non-answer outcome states at least one reason code from its own
  closed list; evidence-based outcomes reference their evidence bundle.

Execution limits (input size, output length, concurrency, duration,
attempts, evidence size) are fixed ceilings in code; config/
execution_limits.json may lower them but never raise them.

JSON Schemas for the contracts are committed under docs/schemas and must
match the models; --write-schemas regenerates them.

Usage:
    python -m ehr_evidence_app.evidence.contracts
    python -m ehr_evidence_app.evidence.contracts --write-schemas
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, \
    ValidationError, model_validator

from ehr_evidence_app.evidence import vocabulary as vocab

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
LIMITS_PATH = os.path.join(REPO_ROOT, "config", "execution_limits.json")
SCHEMA_DIR = os.path.join(REPO_ROOT, "docs", "schemas")
CONTRACT_VERSION = "1.0"

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, StringConstraints(
    pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")]
CaseSetId = Annotated[str, StringConstraints(
    pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$")]
EvidenceRef = Annotated[str, StringConstraints(
    pattern=r"^[VEPAC][1-9][0-9]{0,5}$")]
ClaimText = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
ResponseText = Annotated[str, StringConstraints(min_length=1,
                                                max_length=20000)]
QuestionText = Annotated[str, StringConstraints(min_length=1,
                                                max_length=4000)]

# Fixed ceilings; configuration may lower a limit, never raise it.
LIMIT_CEILINGS = {
    "max_question_chars": 2000,
    "max_output_tokens": 4096,
    "max_concurrent_requests": 8,
    "max_request_seconds": 600,
    "max_generation_attempts": 3,
    "max_evidence_items": 5000,
    "max_bundle_bytes": 8 * 1024 * 1024,
}


class ContractError(Exception):
    """A contract or configuration problem, carrying a short message."""


class Frozen(BaseModel):
    """Base for contract models: immutable, strict, no unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ExecutionLimits(Frozen):
    """Bounded execution limits; a request outside them ends explicitly."""

    max_question_chars: int = Field(ge=1, le=LIMIT_CEILINGS[
        "max_question_chars"])
    max_output_tokens: int = Field(ge=1, le=LIMIT_CEILINGS[
        "max_output_tokens"])
    max_concurrent_requests: int = Field(ge=1, le=LIMIT_CEILINGS[
        "max_concurrent_requests"])
    max_request_seconds: int = Field(ge=1, le=LIMIT_CEILINGS[
        "max_request_seconds"])
    max_generation_attempts: int = Field(ge=1, le=LIMIT_CEILINGS[
        "max_generation_attempts"])
    max_evidence_items: int = Field(ge=1, le=LIMIT_CEILINGS[
        "max_evidence_items"])
    max_bundle_bytes: int = Field(ge=1024, le=LIMIT_CEILINGS[
        "max_bundle_bytes"])


class LimitsFile(Frozen):
    """The committed execution-limits configuration file."""

    schema_version: Literal["1.0"]
    purpose: Annotated[str, StringConstraints(min_length=1, max_length=600)]
    limits: ExecutionLimits


class CaseRef(Frozen):
    """One case of one case set, numbered from 1 in the closure record."""

    case_set: CaseSetId
    case_number: int = Field(ge=1, le=10000)


class ScopeRef(Frozen):
    """An explicit record scope; only a selected visit names its visit."""

    kind: vocab.Scope
    visit_number: Optional[int] = Field(default=None, ge=1, le=1000)

    @model_validator(mode="after")
    def _visit_number_matches_kind(self):
        """A selected-visit scope needs a visit number; others forbid one."""
        if (self.kind == "selected_visit") != (self.visit_number is not None):
            raise ValueError("visit_number is required for selected_visit "
                             "and forbidden for other scopes")
        return self


class PredictionRef(Frozen):
    """The selected inherited prediction, by its output record digest."""

    output_record_sha256: Sha256


class Request(Frozen):
    """One task for one case in one explicit scope."""

    schema_version: Literal["1.0"]
    request_id: Identifier
    task: vocab.Task
    case: CaseRef
    scope: ScopeRef
    question: Optional[QuestionText] = None
    prediction: Optional[PredictionRef] = None

    @model_validator(mode="after")
    def _task_shape(self):
        """QA needs a question; an explanation needs a prediction."""
        if self.task in vocab.QA_TASKS:
            if self.question is None or self.prediction is not None:
                raise ValueError("QA requests need a question and no "
                                 "prediction reference")
        else:
            if self.prediction is None or self.question is not None:
                raise ValueError("explanation requests need a prediction "
                                 "reference and no question")
            if self.scope.kind not in vocab.EXPLANATION_SCOPES:
                raise ValueError("explanations use the pre-target history "
                                 "scope")
        return self


class Claim(Frozen):
    """One factual statement and the evidence records that support it."""

    text: ClaimText
    refs: tuple[EvidenceRef, ...] = Field(min_length=1, max_length=50)


class BundleRef(Frozen):
    """The evidence bundle a response was built from."""

    bundle_id: Identifier
    sha256: Sha256


class Response(Frozen):
    """Exactly one outcome for one request.

    The task is absent only when the question could not be interpreted as
    any task; answers and explanations always name theirs.
    """

    schema_version: Literal["1.0"]
    request_id: Identifier
    task: Optional[vocab.Task] = None
    case: CaseRef
    scope: ScopeRef
    outcome: vocab.Outcome
    reason_codes: tuple[vocab.ReasonCode, ...] = ()
    text: Optional[ResponseText] = None
    claims: tuple[Claim, ...] = Field(default=(), max_length=200)
    bundle: Optional[BundleRef] = None

    @model_validator(mode="after")
    def _outcome_shape(self):
        """Keep the eight outcomes structurally distinct."""
        allowed = vocab.REASONS_BY_OUTCOME[self.outcome]
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise ValueError("reason codes repeat")
        if any(code not in allowed for code in self.reason_codes):
            raise ValueError("a reason code does not belong to the outcome")
        if (self.outcome in vocab.REASON_REQUIRED_OUTCOMES
                and not self.reason_codes):
            raise ValueError("this outcome must state a reason code")
        released = self.outcome in vocab.RELEASED_TEXT_OUTCOMES
        if released != (self.text is not None):
            raise ValueError("text is released only for answer, "
                             "not-recorded, explanation, clarification and "
                             "abstention outcomes")
        if self.claims and not released:
            raise ValueError("an outcome that releases no text carries no "
                             "claims")
        evidential = self.outcome in ("answer", "not_recorded",
                                      "explanation")
        if evidential and (not self.claims or self.bundle is None):
            raise ValueError("answers and explanations cite claims from "
                             "their evidence bundle")
        if self.outcome == "explanation" and (
                self.task != "prediction_explanation"):
            raise ValueError("only explanation requests get explanations")
        if self.outcome in ("answer", "not_recorded") and (
                self.task not in vocab.QA_TASKS):
            raise ValueError("only QA requests get answers")
        return self


def load_limits(path=LIMITS_PATH):
    """Return the validated execution limits from the configuration."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        raise ContractError("execution limits configuration is "
                            "unreadable") from None
    try:
        return LimitsFile.model_validate_json(text).limits
    except ValidationError as error:
        raise ContractError("execution limits configuration is invalid: "
                            "%d problem(s)" % error.error_count()) from None


def request_limit_problem(request, limits):
    """Return a reason code when a request exceeds a limit, else None."""
    if request.question is not None and (
            len(request.question) > limits.max_question_chars):
        return "limit_exceeded"
    return None


def json_schemas():
    """Return the JSON Schema of each committed contract, by file name."""
    from ehr_evidence_app.evidence import schema  # Bundles build on these.
    schemas = {"request.schema.json": Request.model_json_schema(),
               "response.schema.json": Response.model_json_schema(),
               "execution-limits.schema.json":
                   LimitsFile.model_json_schema()}
    schemas.update(schema.bundle_schemas())
    return schemas


def schema_text(schema):
    """Return the canonical committed text of one JSON Schema."""
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def schema_drift(directory=SCHEMA_DIR, schemas=None):
    """Return the names of committed schemas that differ from the models."""
    drift = []
    for name, schema in (schemas or json_schemas()).items():
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                committed = handle.read()
        except OSError:
            drift.append(name)
            continue
        if committed != schema_text(schema):
            drift.append(name)
    return sorted(drift)


def write_schemas(directory=SCHEMA_DIR, schemas=None):
    """Write the canonical JSON Schemas into the documentation directory."""
    os.makedirs(directory, exist_ok=True)
    for name, schema in sorted((schemas or json_schemas()).items()):
        with open(os.path.join(directory, name), "w", encoding="utf-8",
                  newline="\n") as handle:
            handle.write(schema_text(schema))


# --------------------------------------------------------------------------
# Synthetic examples (invented values only)
# --------------------------------------------------------------------------

SYNTHETIC_DIGEST = "5" * 64


def synthetic_examples():
    """Return (valid, invalid) invented request and response documents."""
    case = {"case_set": "synthetic-3-v1", "case_number": 2}
    history = {"kind": "pre_target_history"}
    valid = [
        ("request", {"schema_version": "1.0", "request_id": "q-001",
                     "task": "condition_presence", "case": case,
                     "scope": history,
                     "question": "Is synthetic condition A recorded?"}),
        ("request", {"schema_version": "1.0", "request_id": "x-001",
                     "task": "prediction_explanation", "case": case,
                     "scope": history,
                     "prediction": {"output_record_sha256":
                                    SYNTHETIC_DIGEST}}),
        ("response", {"schema_version": "1.0", "request_id": "q-001",
                      "task": "condition_presence", "case": case,
                      "scope": history, "outcome": "not_recorded",
                      "text": "Synthetic condition A is not recorded "
                              "within the selected scope.",
                      "claims": [{"text": "No matching record in scope.",
                                  "refs": ["C1"]}],
                      "bundle": {"bundle_id": "qa-c02-history",
                                 "sha256": SYNTHETIC_DIGEST}}),
        ("response", {"schema_version": "1.0", "request_id": "q-002",
                      "task": "condition_list", "case": case,
                      "scope": history, "outcome": "clarification",
                      "reason_codes": ["scope_ambiguous"],
                      "text": "Which visit do you mean?"}),
        ("response", {"schema_version": "1.0", "request_id": "q-003",
                      "task": "condition_list", "case": case,
                      "scope": history, "outcome": "operational_error",
                      "reason_codes": ["timeout"]}),
    ]
    invalid = [
        ("request", dict(valid[0][1], question=None)),
        ("request", dict(valid[1][1], scope={"kind": "case_visits"})),
        ("request", dict(valid[0][1], scope={"kind": "selected_visit"})),
        ("response", dict(valid[4][1], text="unchecked text")),
        ("response", dict(valid[3][1], reason_codes=[])),
        ("response", dict(valid[3][1], reason_codes=["timeout"])),
        ("response", dict(valid[2][1], claims=[])),
        ("response", dict(valid[2][1], task="prediction_explanation")),
    ]
    return valid, invalid


def validate_document(kind, document):
    """Validate one JSON document as a request or response contract."""
    model = Request if kind == "request" else Response
    return model.model_validate_json(json.dumps(document))


def run_checks():
    """Return problems found in the limits, examples and committed schemas."""
    problems = []
    try:
        load_limits()
    except ContractError as error:
        problems.append(str(error))
    valid, invalid = synthetic_examples()
    for kind, document in valid:
        try:
            validate_document(kind, document)
        except ValidationError:
            problems.append("a valid synthetic %s was refused" % kind)
    for kind, document in invalid:
        try:
            validate_document(kind, document)
        except ValidationError:
            continue
        problems.append("an invalid synthetic %s was accepted" % kind)
    for name in schema_drift():
        problems.append("committed schema %s differs from the model" % name)
    return problems


def main(argv=None):
    """Check the contracts, or regenerate the committed JSON Schemas."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write-schemas", action="store_true",
                        help="regenerate docs/schemas from the models")
    args = parser.parse_args(argv)
    if args.write_schemas:
        write_schemas()
        print("Schemas written: %d" % len(json_schemas()))
    problems = run_checks()
    for problem in problems:
        print("PROBLEM: %s" % problem)
    print("RESULT: %s" % ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
