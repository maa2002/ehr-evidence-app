"""Scoped condition answers from evidence bundles (work item 34).

One question about the active case and scope becomes exactly one response:

1. interpretation (qa.interpret) ends unsupported, unclear or other-patient
   questions, and questions holding words it cannot account for, with an
   abstention or clarification;
2. scope binding (qa.scope) keeps the active scope, or asks before any
   change;
3. the case's QA bundle for that scope is read from the pinned bundle set,
   verified against its manifest and checked before any text is written
   (validation.evidence): a bundle that does not fit the request ends in a
   case rejection, and coverage that is not complete in an abstention;
4. a list names every distinct condition with its records, and lists
   records without a standard concept by ICD code;
5. a presence check resolves the longest candidate of the condition phrase
   that names something (qa.resolve). A phrase that names nothing and holds
   an out-of-scope word ("chest X-ray", "medication list") gets that rule's
   abstention or clarification. Any matching record makes the condition
   recorded, citing every match. With complete coverage and no match it is
   not recorded within the scope, which is never a statement of clinical
   absence. Ambiguous, unknown and too broad terms get a clarification.

Every response with text is written as a candidate: its facts are
slots, and its refusals and clarifying questions are approved notices
(qa.templates). The candidate leaves through the release gate
(validation.gate), which checks its structure and references, the
support of its statements by their cited records and its wording, and
renders the released text and claims from it; a presence answer is also
checked against the condition asked about. A candidate that fails is
withheld as a validation rejection.

An answer from evidence (a list, or a presence check with its "not
recorded") is written by the language model when a writer is given
(generation.adapter): the model writes the complete candidate from the
complete bundle, and the template candidate is released instead only when
the model's is not (a recorded fallback). Refusals and clarifying
questions are settled before any evidence is read and stay the
templates' notices.

When the case set's record bundles are given (records), a question the
condition rules refuse as being about other data, times, counts or words
they cannot place, or one whose condition phrase names no condition, is
read as a question about the whole record (qa.record_interpret) and
answered from the record bundle of the scope by the templates
(qa.record_answer). So is a presence question whose phrase is a fixed
word for a class of medications, before its condition is looked for
(version 6): "any nsaids" asks about medications, though the condition
vocabulary also calls an overdose NSAIDs. Every other question the
condition rules answer is answered exactly as without the records. A
request for advice, or about the future, keeps its refusal.
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import json
import os
import re

from pydantic import ValidationError

from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import bundles, contracts, schema
from ehr_evidence_app.generation import adapter, shape
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.qa import interpret as interpreter
from ehr_evidence_app.qa import record_answer, record_interpret
from ehr_evidence_app.qa import record_resolve, resolve, templates
from ehr_evidence_app.qa import scope as scopes
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import evidence, gate, record_support

RULES_VERSION = "qa-answer-v6"
PILOT = "pilot-10-v1"
# Out-of-scope rules that end in a clarification rather than an abstention,
# with the notice of each.
CLARIFYING_RULES = {"explanation": "prediction_selection",
                    "other_patient": "case_other"}
# Clarifications of the interpretation's fail-safes and task checks.
CATEGORY_NOTICES = {"scope_unrecognised": "scope_unrecognised",
                    "qualified": "qualified", "unexplained": "unexplained",
                    "two_conditions": "two_conditions",
                    "two_tasks": "two_tasks"}
RESOLUTION_REASONS = {"ambiguous": "concept_ambiguous",
                      "unknown": "concept_unknown",
                      "too_broad": "concept_too_broad"}
# The most concept names a clarifying question offers.
MAX_NAMES = 20
# The refusals and clarifications of the condition rules after which a
# question is read as one about the whole record.
RECORD_CATEGORIES = ("other_data", "time_or_count", "unexplained",
                     "unrecognised", "scope_unrecognised")
# A treatment question about a dose is one about the record; one asking
# for advice is not. A question about death that is not about the future
# is one about the record.
DOSE = re.compile(r"\b(?:dose|doses|dosage|dosing|how much|strength|"
                  r"route)\b")
ADVICE = re.compile(r"\b(?:should|recommend\w*|manage|management|treat|"
                    r"treatment|treatments|therapy|therapies|start|stop|"
                    r"prescribe)\b")
FUTURE = re.compile(r"\b(?:will|would|going to|likely|likelihood|chance|"
                    r"chances|risk|expected|prognosis|outlook|get worse|"
                    r"getting worse|worsen\w*|improve\w*|recover\w*|"
                    r"progress\w*)\b")


class AnswerError(Exception):
    """A fail-closed pipeline failure carrying a finding code only."""


# --------------------------------------------------------------------------
# Bundle sets
# --------------------------------------------------------------------------

def bundle_set_plan(case_set):
    """Return the pinned manifest of a case set's current bundle set."""
    if case_set == PILOT:
        directory = bundles.OUTPUT_DIR
        return {"label": PILOT, "directory": directory,
                "manifest": {"path": directory + "\\" + bundles.MANIFEST_NAME,
                             "size_bytes": bundles.PILOT_MANIFEST_SIZE,
                             "sha256": bundles.PILOT_MANIFEST_SHA256}}
    try:
        entry = registry.pinned(case_set, "bundles")
    except registry.RegistryError as error:
        raise AnswerError(str(error)) from None
    return {"label": case_set, "directory": registry.bundles_dir(case_set),
            "manifest": entry}


class BundleSet:
    """A pinned bundle set whose bundles are verified when read."""

    def __init__(self, label, directory, manifest, reader=None):
        """Index the manifest's QA bundles by case and scope."""
        self.label = label
        self.directory = directory
        self.reader = reader or self.read_file
        self.entries = {}
        self.visits = collections.Counter()
        for entry in manifest.get("files") or []:
            if entry.get("kind") != "qa":
                continue
            scope = contracts.ScopeRef.model_validate(entry["scope"])
            self.entries[(entry["case_number"], scope)] = entry
            if scope.kind == "selected_visit":
                self.visits[entry["case_number"]] += 1

    def read_file(self, entry):
        """Read one bundle file and verify it against its manifest entry."""
        path = os.path.join(self.directory, *entry["path"].split("/"))
        try:
            data, _ = mapping.read_verified(
                path, entry["bundle_id"], "bundle_hash_mismatch",
                sha256=entry["sha256"], size=entry["size_bytes"])
        except mapping.MappingError as error:
            raise AnswerError(str(error)) from None
        return data

    def visit_count(self, case_number):
        """Return how many visits a case has, or 0 for an unknown case."""
        return self.visits.get(case_number, 0)

    def qa_bundle(self, case_number, scope):
        """Return (bundle, SHA-256) of a case's QA bundle for a scope."""
        entry = self.entries.get((case_number, scope))
        if entry is None:
            raise AnswerError("bundle_missing")
        data = self.reader(entry)
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise AnswerError("bundle_hash_mismatch")
        return schema.QABundle.model_validate_json(data), entry["sha256"]


def load_bundle_set(plan):
    """Load a bundle set through its pinned manifest."""
    entry = plan["manifest"]
    try:
        data, _ = mapping.read_verified(
            entry["path"], "bundle_manifest",
            "input_hash_mismatch:bundle_manifest", sha256=entry["sha256"],
            size=entry["size_bytes"])
        manifest = json.loads(data.decode("utf-8"))
    except mapping.MappingError as error:
        raise AnswerError(str(error)) from None
    except (UnicodeDecodeError, ValueError):
        raise AnswerError("bundle_manifest_unexpected") from None
    if (manifest.get("schema") != bundles.SCHEMA
            or manifest.get("case_set") != plan["label"]):
        raise AnswerError("bundle_manifest_unexpected")
    return BundleSet(plan["label"], plan["directory"], manifest)


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Result:
    """A response with the facts the development suite scores, the
    candidate it was rendered from and, for an answer from evidence, who
    wrote it (generation.adapter.Written)."""

    response: contracts.Response
    request: contracts.Request | None = None
    bundle_sha256: str | None = None
    matched: tuple = ()
    groups: tuple = ()
    resolution: resolve.Resolution | None = None
    candidate: candidates.Candidate | None = None
    written: adapter.Written | None = None


def respond(request_id, case, scope, outcome, reasons=(), task=None):
    """Return a validated Response that releases no text."""
    return contracts.Response(
        schema_version="1.0", request_id=request_id, task=task, case=case,
        scope=scope, outcome=outcome, reason_codes=tuple(reasons))


def out_of_scope(request_id, case, scope, status, reason, category):
    """Return the candidate of an out-of-scope rule's abstention or
    clarification."""
    if status == "abstain":
        return templates.notice(request_id, case, scope, "abstention",
                                reason, "unsupported_" + category)
    return templates.notice(request_id, case, scope, "clarification", reason,
                            CLARIFYING_RULES[category])


def interpretation_notice(request_id, case, scope, interpretation):
    """Return the clarification candidate of an interpretation that is not
    a task."""
    reason, category = interpretation.reason, interpretation.category
    if category == "scope_unrecognised":
        return templates.notice(request_id, case, scope, "clarification",
                                reason, "scope_unrecognised", scope=scope)
    if category in CATEGORY_NOTICES:
        return templates.notice(request_id, case, scope, "clarification",
                                reason, CATEGORY_NOTICES[category])
    if reason == "concept_unknown":
        term = templates.term_slot(interpretation.term)
        if term:
            return templates.notice(request_id, case, scope, "clarification",
                                    reason, "concept_unknown", term=term)
        return templates.notice(request_id, case, scope, "clarification",
                                reason, "concept_missing")
    return templates.notice(request_id, case, scope, "clarification", reason,
                            "not_understood")


def binding_notice(request_id, case, scope, binding, task):
    """Return the clarification candidate of a scope that cannot be
    bound."""
    if binding.reason == "scope_ambiguous" and binding.requested is not None:
        return templates.notice(request_id, case, scope, "clarification",
                                binding.reason, "scope_other", task=task,
                                requested=binding.requested, scope=scope)
    notice_id = ("scope_several" if binding.reason == "scope_ambiguous"
                 else "visit_missing")
    return templates.notice(request_id, case, scope, "clarification",
                            binding.reason, notice_id, task=task)


def resolution_notice(request_id, case, scope, interpretation, resolution):
    """Return the clarification candidate of a condition phrase that does
    not resolve to one condition."""
    reason = RESOLUTION_REASONS[resolution.status]
    task = interpretation.task
    term = templates.term_slot(interpretation.term)
    names = tuple(resolution.concepts)[:MAX_NAMES]
    if not term:
        return templates.notice(request_id, case, scope, "clarification",
                                reason, "concept_missing", task=task)
    if resolution.status == "ambiguous":
        return templates.notice(request_id, case, scope, "clarification",
                                reason, "concept_ambiguous", task=task,
                                term=term, names=names)
    if resolution.status == "too_broad":
        return templates.notice(request_id, case, scope, "clarification",
                                reason, "concept_too_broad", task=task,
                                term=term)
    if names:
        return templates.notice(request_id, case, scope, "clarification",
                                reason, "concept_suggested", task=task,
                                term=term, names=names)
    return templates.notice(request_id, case, scope, "clarification", reason,
                            "concept_unknown", task=task, term=term)


# --------------------------------------------------------------------------
# Answers
# --------------------------------------------------------------------------

def resolve_phrase(interpretation, vocab, suggestions=True):
    """Resolve the longest candidate of the condition phrase that names
    something; when none does, the shortest, with suggestions unless they
    are not wanted."""
    terms = interpretation.terms or (interpretation.term,)
    for term in terms[:-1]:
        resolution = resolve.resolve(term, vocab, suggestions=False)
        if resolution.status != "unknown":
            return resolution
    return resolve.resolve(terms[-1], vocab, suggestions=suggestions)


def record_matches(bundle, resolution, vocab):
    """Return the (event, match kind) pairs of the records that match a
    resolved phrase."""
    found = []
    for event in bundle.events:
        source = event.source_concept
        kind = resolve.match(resolution, vocab, event.concept.concept_id,
                             source.code if source else None,
                             source.concept_id if source else None)
        if kind:
            found.append((event, kind))
    return found


def well_formed(question):
    """Return a question as text that can be written: an unpaired
    surrogate, which no encoding holds, becomes the replacement
    character."""
    if not question:
        return question
    return question.encode("utf-16", "surrogatepass").decode("utf-16",
                                                              "replace")


def answer(question, case_set, case_number, active_scope, bundle_set, vocab,
           request_id, limits, writer=None, recorder=None, records=None):
    """Return the Result for one question about the active case and scope;
    writer (a generation.adapter.Writer) writes the answers from evidence,
    and without one the templates do, each a "no_model" fallback.
    recorder (a generation.records.Recorder), when given, writes the
    protected record of the response. records (a
    qa.record_answer.RecordContext), when given, answers questions about
    the whole record."""
    question = well_formed(question)
    result = result_of(question, case_set, case_number, active_scope,
                       bundle_set, vocab, request_id, limits, writer,
                       records)
    if recorder is not None:
        recorder.record(result.response, result.request, question=question,
                        bundle_sha256=result.bundle_sha256,
                        written=result.written)
    return result


def record_allowed(interpretation):
    """Return True when a question the condition rules do not answer may
    be read as one about the whole record."""
    category, text = interpretation.category, interpretation.text
    if category in RECORD_CATEGORIES:
        return True
    if category == "treatment":
        return bool(DOSE.search(text)) and not ADVICE.search(text)
    if category == "prognosis":
        return not FUTURE.search(text)
    if category == "ranking":
        # "The first potassium" is a time; "the first diagnosis" stays a
        # ranking of conditions, which is refused.
        return not re.search(r"%s" % interpreter.CONDITIONS, text)
    return False


def record_result(question, case, active_scope, bundle_set, vocab,
                  request_id, records, tasks_only=False):
    """Return the Result of a question read as one about the whole record,
    or None when it is not one (with tasks_only, unless it is read as a
    task)."""
    question = well_formed(question)
    reading = record_interpret.interpret(question, records.terms)
    if reading.status == "none" or (tasks_only
                                    and reading.status != "task"):
        return None
    if reading.status == "clarify":
        candidate = record_answer.clarification(request_id, case,
                                                active_scope, reading)
        return Result(gate.release(candidate, request_id, case,
                                   active_scope, vocab=vocab),
                      candidate=candidate)
    visit_count = bundle_set.visit_count(case.case_number)
    if not visit_count:
        return Result(respond(request_id, case, active_scope,
                              "case_rejection",
                              reasons=("case_not_admitted",)))
    binding = scopes.bind(reading.mentions, active_scope, visit_count)
    if binding.status != "bound":
        candidate = binding_notice(request_id, case, active_scope, binding,
                                   reading.task)
        return Result(gate.release(candidate, request_id, case,
                                   active_scope, vocab=vocab),
                      candidate=candidate)
    scope = binding.scope
    found = records.records.bundle(case.case_number, scope)
    if found is None:
        return Result(respond(request_id, case, scope, "case_rejection",
                              reasons=("scope_invalid",), task=reading.task))
    bundle, digest = found
    request = contracts.Request(
        schema_version="1.0", request_id=request_id, task=reading.task,
        case=case, scope=scope, question=question)
    resolution = record_answer.resolution_of(records, reading)
    if resolution is not None and resolution.status != "resolved":
        candidate = record_answer.clarification(request_id, case, scope,
                                                reading, resolution)
        return Result(gate.release(candidate, request_id, case, scope,
                                   vocab=vocab),
                      request=request, bundle_sha256=digest,
                      candidate=candidate)
    bundle_ref = contracts.BundleRef(bundle_id=bundle.bundle_id,
                                     sha256=digest)
    try:
        candidate = record_answer.answer_candidate(records, request, bundle,
                                                   bundle_ref, reading,
                                                   resolution)
    except ValidationError:
        return Result(respond(request_id, case, scope, "operational_error",
                              reasons=("limit_exceeded",), task=reading.task),
                      request=request, bundle_sha256=digest)
    response = gate.release(
        candidate, request_id, case, scope, bundle, digest, vocab,
        record=record_support.RecordCheck(records.terms, resolution,
                                          reading))
    return Result(response, request=request, bundle_sha256=digest,
                  candidate=candidate,
                  written=adapter.Written(response, candidate, "template"))


def result_of(question, case_set, case_number, active_scope, bundle_set,
              vocab, request_id, limits, writer=None, records=None):
    """Return the Result for one question (see answer)."""
    case = contracts.CaseRef(case_set=case_set, case_number=case_number)
    question = well_formed(question)
    if len(question or "") > limits.max_question_chars:
        return Result(respond(request_id, case, active_scope,
                              "operational_error",
                              reasons=("limit_exceeded",)))
    interpretation = interpreter.interpret(question)

    def unbound(candidate):
        """Return the Result of a candidate before any scope is bound."""
        return Result(gate.release(candidate, request_id, case,
                                   active_scope, vocab=vocab),
                      candidate=candidate)

    if records is not None and interpretation.status in (
            "abstain", "clarify") and record_allowed(interpretation):
        found = record_result(question, case, active_scope, bundle_set,
                              vocab, request_id, records)
        if found is not None:
            return found
    if interpretation.status == "abstain" or (
            interpretation.status == "clarify"
            and interpretation.category in CLARIFYING_RULES):
        return unbound(out_of_scope(request_id, case, active_scope,
                                    interpretation.status,
                                    interpretation.reason,
                                    interpretation.category))
    if interpretation.status == "clarify":
        return unbound(interpretation_notice(request_id, case, active_scope,
                                             interpretation))
    visit_count = bundle_set.visit_count(case_number)
    if not visit_count:
        return Result(respond(request_id, case, active_scope,
                              "case_rejection",
                              reasons=("case_not_admitted",)))
    binding = scopes.bind(interpretation.mentions, active_scope, visit_count)
    if binding.status != "bound":
        return unbound(binding_notice(request_id, case, active_scope,
                                      binding, interpretation.task))
    scope = binding.scope
    try:
        bundle, digest = bundle_set.qa_bundle(case_number, scope)
    except AnswerError as error:
        if str(error) != "bundle_missing":
            raise
        return Result(respond(request_id, case, scope, "case_rejection",
                              reasons=("scope_invalid",),
                              task=interpretation.task))
    request = contracts.Request(
        schema_version="1.0", request_id=request_id,
        task=interpretation.task, case=case, scope=scope,
        question=question)

    def bound(candidate, **fields):
        """Return the Result of a notice for the bound request, which
        cites no evidence."""
        return Result(gate.release(candidate, request_id, case, scope,
                                   vocab=vocab),
                      request=request, bundle_sha256=digest,
                      candidate=candidate, **fields)

    def written(template, resolution=None, **fields):
        """Return the Result of an answer from evidence: the writer's
        candidate, or the template's in its place. A presence answer is
        checked against the condition asked about."""
        condition = (templates.resolved_name(resolution) if resolution
                     else None)

        def release(candidate):
            """Release a candidate for this request through the gate."""
            return gate.release(candidate, request_id, case, scope, bundle,
                                digest, vocab, condition=condition)

        asked = (shape.asked(resolution, bundle, vocab)
                 if resolution and writer is not None else None)
        outcome = adapter.write(writer, shape.Context(request, bundle, digest,
                                                      asked),
                                template, release)
        return Result(outcome.response, request=request,
                      bundle_sha256=digest, candidate=outcome.candidate,
                      written=outcome, resolution=resolution, **fields)

    stop = evidence.qa_stop(request, bundle)
    if stop is not None and stop.outcome == "abstention":
        return bound(templates.notice(request_id, case, scope, "abstention",
                                      stop.reasons[0], "coverage_incomplete",
                                      task=interpretation.task))
    if stop is not None:
        return Result(respond(request_id, case, scope, stop.outcome,
                              reasons=stop.reasons, task=interpretation.task),
                      request=request, bundle_sha256=digest)
    bundle_ref = contracts.BundleRef(bundle_id=bundle.bundle_id,
                                     sha256=digest)
    if interpretation.task == "condition_list":
        candidate, groups = templates.list_candidate(request_id, case, scope,
                                                     bundle, bundle_ref)
        return written(candidate, groups=groups)
    resolution = resolve_phrase(interpretation, vocab)
    if records is not None and (resolution.status == "unknown" or any(
            record_resolve.class_word(term) for term in (
                interpretation.terms or (interpretation.term,)))):
        found = record_result(question, case, active_scope, bundle_set,
                              vocab, request_id, records, tasks_only=True)
        if found is not None:
            return found
    if interpretation.guard and resolution.status == "unknown":
        return bound(out_of_scope(request_id, case, scope,
                                  *interpretation.guard),
                     resolution=resolution)
    if resolution.status != "resolved":
        return bound(resolution_notice(request_id, case, scope,
                                       interpretation, resolution),
                     resolution=resolution)
    matches = record_matches(bundle, resolution, vocab)
    candidate = templates.presence_candidate(request_id, case, scope, bundle,
                                             bundle_ref, resolution, matches)
    return written(candidate, resolution,
                   matched=tuple(sorted((event.domain, event.native_id)
                                        for event, _ in matches)))
