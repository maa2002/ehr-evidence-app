"""Scoped condition answers from evidence bundles (work item 34).

One question about the active case and scope becomes exactly one response:

1. interpretation (qa.interpret) ends unsupported, unclear or other-patient
   questions with an abstention or clarification;
2. scope binding (qa.scope) keeps the active scope, or asks before any
   change;
3. the case's QA bundle for that scope is read from the pinned bundle set
   and verified against its manifest;
4. a list needs complete coverage: it names every distinct condition with
   its records, and lists records without a standard concept by ICD code;
5. a presence check resolves the longest candidate of the condition phrase
   that names something (qa.resolve). A phrase that names nothing and holds
   an out-of-scope word ("chest X-ray", "medication list") gets that rule's
   abstention or clarification. Any matching record makes the condition
   recorded, citing every match. With complete coverage and no match it is
   not recorded within the scope, which is never a statement of clinical
   absence. Ambiguous, unknown and too broad terms get a clarification.
   Coverage that is not complete gives an abstention.

Claims cite the bundle's own references, and the response is bound to the
bundle by identifier and digest and checked with the citation rules before
it is returned. Text comes from fixed templates; Phase 7 may rephrase it
behind the Phase 6 checks.
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import json
import os

from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import bundles, contracts, schema
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.qa import interpret as interpreter
from ehr_evidence_app.qa import resolve
from ehr_evidence_app.qa import scope as scopes

RULES_VERSION = "qa-answer-v2"
PILOT = "pilot-10-v1"
MAX_REFS = 50
CLAIM_LIMIT = 200
ABSENCE = ("This means not recorded within the selected scope; it is not a "
           "statement of clinical absence.")
MATCH_WORDS = {"exact": "exact concept",
               "more_specific": "a more specific concept",
               "icd_code": "ICD code",
               "more_specific_icd_code": "a more specific ICD code",
               "icd_description": "ICD description (no standard concept)"}
UNSUPPORTED_TEXT = {
    "treatment": "Treatment and management questions are outside what this "
                 "application answers.",
    "prognosis": "Questions about risk, prognosis or future events are "
                 "outside what this application answers.",
    "diagnosis_request": "This application does not diagnose. It reports "
                         "what is recorded.",
    "other_data": "Only recorded conditions (diagnoses) can be queried here; "
                  "medications, procedures, results and notes cannot.",
    "time_or_count": "Questions about dates, times, the order of events or "
                     "how often something was recorded are outside what "
                     "this application answers; the visits are chosen with "
                     "the scope selector.",
    "ranking": "Questions about which condition is first, main or most "
               "important are outside what this application answers: the "
               "recorded conditions are not ranked.",
}
SUPPORTED = ("It can list the conditions recorded within the selected scope, "
             "or check whether a named condition is recorded there.")
# Out-of-scope rules that end in a clarification rather than an abstention.
UNSUPPORTED_CATEGORIES = ("explanation", "other_patient")


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
# Wording
# --------------------------------------------------------------------------

def scope_words(scope):
    """Return how a scope is named in answers."""
    if scope.kind == "pre_target_history":
        return "pre-target history (the visits before the final admission)"
    if scope.kind == "selected_visit":
        return "visit %d" % scope.visit_number
    return "all case visits, including the final admission"


def join_visits(numbers):
    """Return 'visit 1', 'visits 1 and 3' or 'visits 1, 2 and 4'."""
    numbers = sorted(numbers)
    if len(numbers) == 1:
        return "visit %d" % numbers[0]
    return "visits %s and %d" % (", ".join(str(n) for n in numbers[:-1]),
                                 numbers[-1])


def concept_label(vocab, resolution):
    """Return how a resolved phrase is echoed in text."""
    if resolution.rule == "icd_code":
        return "ICD code %s" % resolution.codes[0]
    names = [vocab.rows[concept].name for concept in resolution.concepts
             if concept in vocab.rows]
    return "; ".join(names)


def event_label(event):
    """Return the readable label of one condition record."""
    if event.concept.name_status == "available":
        return event.concept.name
    source = event.source_concept
    if source is not None:
        code = "%s %s" % (source.vocabulary_id, source.code)
        return "%s, %s" % (code, source.name) if source.name else code
    return "code %s" % (event.concept.source_code or "unknown")


def group_key(event):
    """Return the key that groups repeated records of one condition."""
    if event.concept.concept_id:
        return ("concept", event.concept.concept_id)
    source = event.source_concept
    if source is not None:
        return ("source", source.vocabulary_id, source.code)
    return ("value", event.concept.source_code or "")


def chunked_claims(text, refs):
    """Return claims citing refs, split into groups of at most MAX_REFS."""
    refs = list(refs)
    chunks = [refs[start:start + MAX_REFS]
              for start in range(0, len(refs), MAX_REFS)] or [[]]
    if len(chunks) == 1:
        return [contracts.Claim(text=text, refs=tuple(chunks[0]))]
    return [contracts.Claim(text="%s (records %d to %d of %d)" % (
        text, index * MAX_REFS + 1, index * MAX_REFS + len(chunk),
        len(refs)), refs=tuple(chunk)) for index, chunk in enumerate(chunks)]


# --------------------------------------------------------------------------
# Answers
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Result:
    """A response with the facts the development suite scores."""

    response: contracts.Response
    request: contracts.Request | None = None
    bundle_sha256: str | None = None
    matched: tuple = ()
    groups: tuple = ()
    resolution: resolve.Resolution | None = None


def respond(request_id, case, scope, outcome, text=None, reasons=(),
            task=None, claims=(), bundle=None):
    """Return a validated Response."""
    return contracts.Response(
        schema_version="1.0", request_id=request_id, task=task, case=case,
        scope=scope, outcome=outcome, reason_codes=tuple(reasons), text=text,
        claims=tuple(claims), bundle=bundle)


def disclosure(bundle):
    """Return the reason codes a released answer discloses."""
    return (("unmapped_records_in_scope",)
            if bundle.coverage.unmapped_events_in_scope else ())


def visit_numbers(bundle, events):
    """Return the visit numbers of a group of events."""
    numbers = {visit.ref: visit.visit_number for visit in bundle.visits}
    return {numbers[event.visit_ref] for event in events}


def list_answer(request_id, case, scope, bundle, bundle_ref):
    """Return the list answer of a completely covered scope."""
    words = scope_words(scope)
    if not bundle.events:
        return respond(
            request_id, case, scope, "not_recorded",
            text="No conditions are recorded within the %s. %s" % (
                words, ABSENCE),
            reasons=disclosure(bundle), task="condition_list",
            claims=[contracts.Claim(text="No condition records within the "
                                         "scope.", refs=("C1",))],
            bundle=bundle_ref), ()
    groups = collections.OrderedDict()
    for event in bundle.events:
        groups.setdefault(group_key(event), []).append(event)
    lines, claims = [], []
    for key, events in groups.items():
        label = event_label(events[0])
        uncoded = " (no standard concept)" if key[0] != "concept" else ""
        detail = "%d record%s in %s" % (len(events), "" if len(events) == 1
                                        else "s",
                                        join_visits(visit_numbers(bundle,
                                                                  events)))
        lines.append("- %s%s: %s" % (label, uncoded, detail))
        claims += chunked_claims("%s%s: %s." % (label, uncoded, detail),
                                 [event.ref for event in events])
    if len(claims) > CLAIM_LIMIT:
        raise AnswerError("claim_limit_exceeded")
    unmapped = bundle.coverage.unmapped_events_in_scope
    note = ("\n%d of these records have no standard concept and are listed "
            "by their ICD code." % unmapped) if unmapped else ""
    text = ("%s within the %s (%s):\n%s%s\nThe list is complete for the "
            "recorded OMOP condition records of this case at the recorded "
            "snapshot." % (
                "1 distinct condition is recorded" if len(groups) == 1
                else "%d distinct conditions are recorded" % len(groups),
                words, "1 record" if len(bundle.events) == 1
                else "%d records" % len(bundle.events), "\n".join(lines),
                note))
    return respond(request_id, case, scope, "answer", text=text,
                   reasons=disclosure(bundle), task="condition_list",
                   claims=claims, bundle=bundle_ref), tuple(groups)


def presence_answer(request_id, case, scope, bundle, bundle_ref, resolution,
                    vocab):
    """Return the presence answer for a resolved condition."""
    words = scope_words(scope)
    label = concept_label(vocab, resolution)
    matches = []
    for event in bundle.events:
        source = event.source_concept
        kind = resolve.match(resolution, vocab, event.concept.concept_id,
                             source.code if source else None,
                             source.concept_id if source else None)
        if kind:
            matches.append((event, kind))
    unmapped = bundle.coverage.unmapped_events_in_scope
    if not matches:
        checked = (" %d records in this scope have no standard concept; "
                   "their ICD codes and descriptions were checked."
                   % unmapped) if unmapped else ""
        return respond(
            request_id, case, scope, "not_recorded",
            text="%s is not recorded within the %s.%s %s" % (
                label, words, checked, ABSENCE),
            reasons=disclosure(bundle), task="condition_presence",
            claims=[contracts.Claim(
                text="No record within the scope matches %s." % label,
                refs=("C1",))], bundle=bundle_ref), ()
    groups = collections.OrderedDict()
    for event, kind in matches:
        groups.setdefault((group_key(event), kind), []).append(event)
    lines, claims = [], []
    for (key, kind), events in groups.items():
        detail = "%s (%d record%s in %s; matched as %s)" % (
            event_label(events[0]), len(events),
            "" if len(events) == 1 else "s",
            join_visits(visit_numbers(bundle, events)), MATCH_WORDS[kind])
        lines.append("- " + detail)
        claims += chunked_claims(detail + ".",
                                 [event.ref for event in events])
    if len(claims) > CLAIM_LIMIT:
        raise AnswerError("claim_limit_exceeded")
    text = ("%s is recorded within the %s: %d matching record%s.\n%s" % (
        label, words, len(matches), "" if len(matches) == 1 else "s",
        "\n".join(lines)))
    matched = tuple(sorted((event.domain, event.native_id)
                           for event, _ in matches))
    return respond(request_id, case, scope, "answer", text=text,
                   reasons=disclosure(bundle), task="condition_presence",
                   claims=claims, bundle=bundle_ref), matched


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


def out_of_scope(request_id, case, scope, status, reason, category):
    """Return the abstention or clarification of an out-of-scope rule."""
    if status == "abstain":
        return respond(request_id, case, scope, "abstention",
                       text="%s %s" % (UNSUPPORTED_TEXT[category],
                                       SUPPORTED),
                       reasons=(reason,))
    return respond(request_id, case, scope, "clarification",
                   text=clarification_text(interpreter.Interpretation(
                       "clarify", reason=reason, category=category)),
                   reasons=(reason,))


def clarification_text(interpretation, resolution=None, vocab=None,
                       requested=None, active=None):
    """Return the text of a clarification."""
    reason = (resolution and {"ambiguous": "concept_ambiguous",
                              "unknown": "concept_unknown",
                              "too_broad": "concept_too_broad"}.get(
        resolution.status)) or interpretation.reason
    if reason == "concept_ambiguous":
        names = [vocab.rows[concept].name for concept in resolution.concepts]
        return ("\"%s\" can mean several conditions: %s. Which one do you "
                "mean?" % (interpretation.term, "; ".join(names)))
    if reason == "concept_unknown":
        if resolution is not None and resolution.concepts:
            names = [vocab.rows[concept].name
                     for concept in resolution.concepts]
            return ("\"%s\" was not found as a condition name. Did you mean: "
                    "%s?" % (interpretation.term, "; ".join(names)))
        return ("Which condition do you mean? \"%s\" was not found as a "
                "condition name." % (interpretation.term or ""))
    if reason == "concept_too_broad":
        return ("\"%s\" covers too many conditions to check at once. Name a "
                "more specific condition, or ask for the list of recorded "
                "conditions." % interpretation.term)
    if reason == "scope_ambiguous" and requested is not None:
        return ("The question asks about the %s, but the selected scope is "
                "the %s. Change the scope to ask about it." % (
                    scope_words(requested), scope_words(active)))
    if reason == "scope_ambiguous":
        return ("The question names more than one scope. Ask about one "
                "scope at a time.")
    if reason == "visit_ambiguous":
        return "The question names a visit that this case does not have."
    if reason == "case_ambiguous":
        return ("Questions are answered for the selected case only. Select "
                "another case to ask about it.")
    if reason == "prediction_selection_needed":
        return ("Explanations of model predictions are given in the "
                "explanation workflow, for a selected prediction.")
    if interpretation.category == "two_tasks":
        return "Ask one question at a time: a list, or one condition."
    return "The question was not understood. " + SUPPORTED


def answer(question, case_set, case_number, active_scope, bundle_set, vocab,
           request_id, limits):
    """Return the Result for one question about the active case and scope."""
    case = contracts.CaseRef(case_set=case_set, case_number=case_number)
    if len(question or "") > limits.max_question_chars:
        return Result(respond(request_id, case, active_scope,
                              "operational_error",
                              reasons=("limit_exceeded",)))
    interpretation = interpreter.interpret(question)
    if interpretation.status == "abstain" or (
            interpretation.status == "clarify"
            and interpretation.category in UNSUPPORTED_CATEGORIES):
        return Result(out_of_scope(request_id, case, active_scope,
                                   interpretation.status,
                                   interpretation.reason,
                                   interpretation.category))
    if interpretation.status == "clarify":
        return Result(respond(request_id, case, active_scope,
                              "clarification",
                              text=clarification_text(interpretation),
                              reasons=(interpretation.reason,)))
    visit_count = bundle_set.visit_count(case_number)
    if not visit_count:
        return Result(respond(request_id, case, active_scope,
                              "case_rejection",
                              reasons=("case_not_admitted",)))
    binding = scopes.bind(interpretation.mentions, active_scope, visit_count)
    if binding.status != "bound":
        return Result(respond(
            request_id, case, active_scope, "clarification",
            text=clarification_text(
                dataclasses.replace(interpretation, reason=binding.reason),
                requested=binding.requested, active=active_scope),
            reasons=(binding.reason,), task=interpretation.task))
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
    if bundle.coverage.status not in ("complete", "complete_empty"):
        return Result(respond(
            request_id, case, scope, "abstention",
            text="The records of this scope could not be read completely, "
                 "so no answer is given.",
            reasons=("coverage_incomplete",), task=interpretation.task),
            request=request, bundle_sha256=digest)
    bundle_ref = contracts.BundleRef(bundle_id=bundle.bundle_id,
                                     sha256=digest)
    resolution = None
    if interpretation.task == "condition_list":
        response, groups = list_answer(request_id, case, scope, bundle,
                                       bundle_ref)
        result = Result(response, request, digest, groups=groups)
    else:
        resolution = resolve_phrase(interpretation, vocab)
        if interpretation.guard and resolution.status == "unknown":
            return Result(out_of_scope(request_id, case, scope,
                                       *interpretation.guard),
                          request=request, bundle_sha256=digest,
                          resolution=resolution)
        if resolution.status != "resolved":
            reason = {"ambiguous": "concept_ambiguous",
                      "unknown": "concept_unknown",
                      "too_broad": "concept_too_broad"}[resolution.status]
            return Result(respond(
                request_id, case, scope, "clarification",
                text=clarification_text(interpretation, resolution, vocab),
                reasons=(reason,), task=interpretation.task),
                request=request, bundle_sha256=digest,
                resolution=resolution)
        response, matched = presence_answer(request_id, case, scope, bundle,
                                            bundle_ref, resolution, vocab)
        result = Result(response, request, digest, matched=matched,
                        resolution=resolution)
    problems = schema.citation_problems(result.response, bundle, digest)
    if problems:
        raise AnswerError("citation_problem:%s" % ",".join(problems))
    return result
