"""Candidate responses: statements with fixed slots (work item 36).

Whatever writes a response (the templates of qa.templates now, the
language model of Phase 7) produces a candidate, never released text. A
candidate is an ordered list of statements. Each statement has:

* a kind from a closed list, which fixes the facts it states, the
  evidence it cites and the answers it may appear in;
* its facts as typed slots: a condition, counts, visit numbers, a match
  kind, a scope. Slot values are references and numbers; the text they
  show is rendered from the evidence bundle and the vocabulary, so a
  writer never types a condition name, a count or a visit;
* a wording: the words around the facts, with one placeholder, such as
  {condition}, for each slot of its kind;
* the bundle references that support it.

A notice is a statement whose wording is one of a fixed list of approved
texts: the statement that "not recorded" is not clinical absence, the
refusals and the clarifying questions. A notice may hold slots of its own
(the user's term, suggested names, scopes) and never cites evidence.

The released text and claims are rendered from a candidate's statements
only, so no sentence reaches the user that is not a statement of the
candidate. Whether a candidate may be released is decided by the checks of
this package; render assumes a candidate that passed them.

Usage:
    python -m ehr_evidence_app.validation.candidate
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from typing import Annotated, Literal, Optional

from pydantic import Field, StringConstraints, ValidationError, \
    model_validator

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.evidence.contracts import CaseRef, Frozen, \
    Identifier, ScopeRef

CANDIDATE_VERSION = "1.0"
MAX_REFS = 50
MAX_CLAIMS = 200
MAX_TEXT = 20000
MAX_STATEMENTS = 1000
MATCH_WORDS = {"exact": "exact concept",
               "more_specific": "a more specific concept",
               "icd_code": "ICD code",
               "more_specific_icd_code": "a more specific ICD code",
               "icd_description": "ICD description (no standard concept)"}
# Counts shown with their noun; "unmapped" is shown as a bare number.
NOUNS = {"count": ("record", "records"),
         "records": ("record", "records"),
         "groups": ("distinct condition", "distinct conditions"),
         "matches": ("matching record", "matching records")}
PLACEHOLDER = re.compile(r"\{([a-z]+)\}")

EventRef = Annotated[str, StringConstraints(pattern=r"^E[1-9][0-9]{0,5}$")]
CodeText = Annotated[str, StringConstraints(
    pattern=r"^[A-Za-z0-9][A-Za-z0-9.]{0,15}$")]
TermText = Annotated[str, StringConstraints(min_length=1, max_length=200)]
WordingText = Annotated[str, StringConstraints(min_length=1, max_length=600)]
ConceptId = Annotated[int, Field(ge=1)]
VisitNumber = Annotated[int, Field(ge=1, le=1000)]
Count = Annotated[int, Field(ge=0, le=1000000)]
MatchKind = Literal[tuple(MATCH_WORDS)]


@dataclasses.dataclass(frozen=True)
class Kind:
    """What a kind of statement states, cites and belongs to.

    slots: the slots it fills, each shown by one placeholder;
    refs: "events" (the records it states), "coverage" (the coverage
    record, C1) or "none" (it restates the statements it summarises);
    task and outcomes: the answers it may appear in; item: it is shown as
    one line of a list.
    """

    slots: tuple
    refs: str
    task: str | None
    outcomes: tuple
    item: bool = False


KINDS = {
    "list_summary": Kind(("groups", "scope", "records"), "none",
                         "condition_list", ("answer",)),
    "list_item": Kind(("condition", "count", "visits"), "events",
                      "condition_list", ("answer",), item=True),
    "list_unmapped": Kind(("unmapped",), "coverage", "condition_list",
                          ("answer",)),
    "list_empty": Kind(("scope",), "coverage", "condition_list",
                       ("not_recorded",)),
    "presence_summary": Kind(("condition", "scope", "matches"), "none",
                             "condition_presence", ("answer",)),
    "presence_item": Kind(("condition", "count", "visits", "match"),
                          "events", "condition_presence", ("answer",),
                          item=True),
    "presence_absent": Kind(("condition", "scope"), "coverage",
                            "condition_presence", ("not_recorded",)),
    "presence_unmapped": Kind(("unmapped",), "coverage",
                              "condition_presence", ("not_recorded",)),
    "notice": Kind((), "none", None, vocab.RELEASED_TEXT_OUTCOMES),
}
StatementKind = Literal[tuple(KINDS)]

SUPPORTED = ("It can list the conditions recorded within the selected scope, "
             "or check whether a named condition is recorded there.")
ASK = ("Ask for the list of conditions recorded within the selected scope, "
       "or whether one named condition is recorded there.")
UNSUPPORTED = {
    "treatment": "Treatment and management questions are outside what this "
                 "application answers.",
    "prognosis": "Questions about risk, prognosis or future events are "
                 "outside what this application answers.",
    "diagnosis_request": "This application does not diagnose. It reports "
                         "what is recorded.",
    "other_data": "Only recorded conditions (diagnoses) can be queried here; "
                  "medications, procedures, results and notes cannot.",
    "time_or_count": "Questions about dates, times, the order of events or "
                     "the number of times something was recorded are "
                     "outside what this application answers; the visits "
                     "are chosen with the scope selector.",
    "ranking": "Questions about which condition is first, main or most "
               "important are outside what this application answers: the "
               "recorded conditions are not ranked.",
    "count_list": "Lists of only some of the conditions, such as three of "
                  "them, are outside what this application answers: the "
                  "recorded conditions are not ranked, so none is chosen "
                  "over another.",
}


@dataclasses.dataclass(frozen=True)
class Notice:
    """An approved text, the outcomes it may end and, for a notice of an
    answer, the task it belongs to."""

    text: str
    outcomes: tuple
    task: str | None = None


NOTICES = {
    "absence": Notice("This means not recorded within the selected scope; "
                      "it is not a statement of clinical absence.",
                      ("not_recorded",)),
    "list_complete": Notice("The list is complete for the recorded OMOP "
                            "condition records of this case at the recorded "
                            "snapshot.", ("answer",), "condition_list"),
    "coverage_incomplete": Notice("The records of this scope could not be "
                                  "read completely, so no answer is given.",
                                  ("abstention",)),
    **{"unsupported_" + category: Notice(text + " " + SUPPORTED,
                                         ("abstention",))
       for category, text in UNSUPPORTED.items()},
    "concept_ambiguous": Notice("\"{term}\" can mean several conditions: "
                                "{names}. Which one do you mean?",
                                ("clarification",)),
    "concept_suggested": Notice("\"{term}\" was not found as a condition "
                                "name. Did you mean: {names}?",
                                ("clarification",)),
    "concept_unknown": Notice("Which condition do you mean? \"{term}\" was "
                              "not found as a condition name.",
                              ("clarification",)),
    "concept_missing": Notice("Which condition do you mean? Name the "
                              "condition to check.", ("clarification",)),
    "concept_too_broad": Notice("\"{term}\" covers too many conditions to "
                                "check at once. Name a more specific "
                                "condition, or ask for the list of recorded "
                                "conditions.", ("clarification",)),
    "scope_other": Notice("The question asks about {requested}, but the "
                          "selected scope is {scope}. Change the scope to ask "
                          "about it.", ("clarification",)),
    "scope_several": Notice("The question names more than one scope. Ask "
                            "about one scope at a time.", ("clarification",)),
    "scope_unrecognised": Notice("The question names a visit, stay or time in "
                                 "words that are not recognised as a scope, "
                                 "so it is not answered. The selected scope "
                                 "is {scope}; change it with the scope "
                                 "selector to ask about another part of the "
                                 "record.", ("clarification",)),
    "visit_missing": Notice("The question names a visit that this case does "
                            "not have.", ("clarification",)),
    "case_other": Notice("Questions are answered for the selected case only. "
                         "Select another case to ask about it.",
                         ("clarification",)),
    "prediction_selection": Notice("Explanations of model predictions are "
                                   "given in the explanation workflow, for a "
                                   "selected prediction.", ("clarification",)),
    "qualified": Notice("Exclusions and filters such as \"except\", \"only\" "
                        "or \"not\" are not applied to answers. " + ASK,
                        ("clarification",)),
    "unexplained": Notice("Some words of the question could not be "
                          "interpreted, so it is not answered. " + ASK,
                          ("clarification",)),
    "two_conditions": Notice("Ask about one condition at a time.",
                             ("clarification",)),
    "two_tasks": Notice("Ask one question at a time: a list, or one "
                        "condition.", ("clarification",)),
    "not_understood": Notice("The question was not understood. " + ASK,
                             ("clarification",)),
}
NoticeId = Literal[tuple(NOTICES)]


class CandidateError(Exception):
    """A candidate that cannot be rendered, carrying a finding code only."""


class ConditionName(Frozen):
    """How a statement names a condition: by a recorded event, by
    vocabulary concepts or by an ICD code. The name shown is rendered from
    the evidence or the vocabulary."""

    event: Optional[EventRef] = None
    concepts: tuple[ConceptId, ...] = Field(default=(), max_length=50)
    code: Optional[CodeText] = None

    @model_validator(mode="after")
    def _one_source(self):
        """Exactly one of an event, concepts or a code names the condition."""
        if sum((self.event is not None, bool(self.concepts),
                self.code is not None)) != 1:
            raise ValueError("a condition is named by exactly one of an "
                             "event, concepts or a code")
        return self


class Slots(Frozen):
    """The facts of one statement; its kind decides which it has."""

    scope: Optional[ScopeRef] = None
    requested: Optional[ScopeRef] = None
    condition: Optional[ConditionName] = None
    count: Optional[Count] = None
    records: Optional[Count] = None
    groups: Optional[Count] = None
    matches: Optional[Count] = None
    unmapped: Optional[Count] = None
    visits: Optional[tuple[VisitNumber, ...]] = Field(default=None,
                                                      max_length=1000)
    match: Optional[MatchKind] = None
    term: Optional[TermText] = None
    names: Optional[tuple[ConceptId, ...]] = Field(default=None,
                                                   max_length=50)

    def filled(self):
        """Return the names of the slots that hold a value."""
        return tuple(name for name in type(self).model_fields
                     if getattr(self, name) is not None)


class Statement(Frozen):
    """One statement: its kind, wording, facts and supporting references.

    A notice names its approved text; any other statement has no notice.
    """

    kind: StatementKind
    wording: WordingText
    slots: Slots = Slots()
    refs: tuple[contracts.EvidenceRef, ...] = Field(default=(),
                                                    max_length=5000)
    notice: Optional[NoticeId] = None


class Candidate(Frozen):
    """A candidate response to one request, before any check."""

    schema_version: Literal["1.0"]
    request_id: Identifier
    task: Optional[vocab.Task] = None
    case: CaseRef
    scope: ScopeRef
    outcome: vocab.Outcome
    reason_codes: tuple[vocab.ReasonCode, ...] = ()
    bundle: Optional[contracts.BundleRef] = None
    statements: tuple[Statement, ...] = Field(min_length=1,
                                              max_length=MAX_STATEMENTS)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def placeholders(wording):
    """Return the placeholder names of a wording, in order."""
    return tuple(PLACEHOLDER.findall(wording))


def expected_slots(statement):
    """Return the slots a statement's kind, or its notice, requires."""
    if statement.kind == "notice":
        notice = NOTICES.get(statement.notice)
        return placeholders(notice.text) if notice else ()
    return KINDS[statement.kind].slots


def scope_words(scope):
    """Return how a scope is named in text."""
    if scope.kind == "pre_target_history":
        return "the pre-target history (the visits before the final admission)"
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


def event_label(event):
    """Return the readable label of one condition record."""
    if event.concept.name_status == "available":
        return event.concept.name
    source = event.source_concept
    if source is not None:
        code = "%s %s" % (source.vocabulary_id, source.code)
        return "%s, %s" % (code, source.name) if source.name else code
    return "code %s" % (event.concept.source_code or "unknown")


def record_group(event):
    """Return the key of the condition a record is of: its standard
    concept, or its source code when it has none. A list names each such
    condition once."""
    if event.concept.concept_id:
        return ("concept", event.concept.concept_id)
    source = event.source_concept
    if source is not None:
        return ("source", source.vocabulary_id, source.code)
    return ("value", event.concept.source_code or "")


def counted(name, number):
    """Return a count with its noun ("1 record", "3 distinct conditions")."""
    if name not in NOUNS:
        return str(number)
    singular, plural = NOUNS[name]
    return "%d %s" % (number, singular if number == 1 else plural)


def concept_names(concepts, concept_name):
    """Return the vocabulary names of concepts, joined with semicolons."""
    names = [concept_name(concept) for concept in concepts]
    if any(name is None for name in names):
        raise CandidateError("concept_unknown_to_vocabulary")
    return "; ".join(names)


def condition_text(kind, name, events, concept_name):
    """Return the name of a condition as a statement of a kind shows it."""
    if name.event is not None:
        event = events.get(name.event)
        if event is None:
            raise CandidateError("event_not_in_bundle")
        label = event_label(event)
        if kind == "list_item" and not event.concept.concept_id:
            label += " (no standard concept)"
        return label
    if name.code is not None:
        return "ICD code %s" % name.code
    return concept_names(name.concepts, concept_name)


def slot_text(statement, name, events, concept_name):
    """Return the text one slot of a statement shows."""
    slots = statement.slots
    value = getattr(slots, name)
    if value is None:
        raise CandidateError("slot_missing")
    if name in ("scope", "requested"):
        return scope_words(value)
    if name == "condition":
        return condition_text(statement.kind, value, events, concept_name)
    if name == "visits":
        if not value:
            raise CandidateError("slot_missing")
        return join_visits(value)
    if name == "match":
        return MATCH_WORDS[value]
    if name == "names":
        return concept_names(value, concept_name)
    if name == "term":
        return value
    return counted(name, value)


def sentence(statement, events, concept_name=None):
    """Return a statement's wording with its slots filled in."""
    concept_name = concept_name or (lambda concept: None)
    return PLACEHOLDER.sub(lambda match: slot_text(
        statement, match.group(1), events, concept_name), statement.wording)


def claims_of(text, refs):
    """Return the claims of one statement, at most MAX_REFS references
    each."""
    body = text[:-1] if text.endswith(".") else text
    refs = list(refs)
    chunks = [refs[start:start + MAX_REFS]
              for start in range(0, len(refs), MAX_REFS)]
    if len(chunks) == 1:
        return [contracts.Claim(text=body + ".", refs=tuple(chunks[0]))]
    return [contracts.Claim(text="%s (records %d to %d of %d)." % (
        body, index * MAX_REFS + 1, index * MAX_REFS + len(chunk),
        len(refs)), refs=tuple(chunk)) for index, chunk in enumerate(chunks)]


def bundle_events(bundle):
    """Return a bundle's events by reference."""
    if bundle is None:
        return {}
    events = getattr(bundle, "events", None)
    if events is None:
        events = bundle.history_events + bundle.final_admission_context
    return {event.ref: event for event in events}


def render(candidate, bundle=None, concept_name=None):
    """Return (text, claims) rendered from a candidate's statements.

    Every statement is one line, a list item led by "- "; a statement that
    cites records becomes one or more claims with the same text.
    """
    events = bundle_events(bundle)
    lines, claims = [], []
    for statement in candidate.statements:
        text = sentence(statement, events, concept_name)
        lines.append(("- " + text) if KINDS[statement.kind].item else text)
        if statement.refs:
            claims += claims_of(text, statement.refs)
    return "\n".join(lines), tuple(claims)


def response(candidate, bundle=None, concept_name=None):
    """Return the Response a checked candidate releases."""
    text, claims = render(candidate, bundle, concept_name)
    return contracts.Response(
        schema_version="1.0", request_id=candidate.request_id,
        task=candidate.task, case=candidate.case, scope=candidate.scope,
        outcome=candidate.outcome, reason_codes=candidate.reason_codes,
        text=text, claims=claims, bundle=candidate.bundle)


def notice(notice_id, **slots):
    """Return a notice statement with its approved wording."""
    return Statement(kind="notice", wording=NOTICES[notice_id].text,
                     slots=Slots(**slots), notice=notice_id)


# --------------------------------------------------------------------------
# Synthetic default mode (invented values only)
# --------------------------------------------------------------------------

def synthetic_candidates():
    """Return (valid, invalid) invented candidate documents."""
    case = {"case_set": "synthetic-1-v1", "case_number": 1}
    history = {"kind": "pre_target_history"}
    valid = [
        {"schema_version": "1.0", "request_id": "q-1",
         "task": "condition_list", "case": case, "scope": history,
         "outcome": "not_recorded",
         "bundle": {"bundle_id": "qa-c01-history", "sha256": "5" * 64},
         "statements": [
             {"kind": "list_empty",
              "wording": "No conditions are recorded within {scope}.",
              "slots": {"scope": history}, "refs": ["C1"]},
             {"kind": "notice", "wording": NOTICES["absence"].text,
              "notice": "absence"}]},
        {"schema_version": "1.0", "request_id": "q-2", "case": case,
         "scope": history, "outcome": "clarification",
         "reason_codes": ["concept_unknown"],
         "statements": [
             {"kind": "notice", "wording": NOTICES["concept_unknown"].text,
              "slots": {"term": "florbnitis"},
              "notice": "concept_unknown"}]},
    ]
    invalid = [
        dict(valid[0], statements=[]),
        dict(valid[0], outcome="answered"),
        dict(valid[1], statements=[{"kind": "notice", "wording": "x",
                                    "notice": "no_such_notice"}]),
        dict(valid[0], statements=[{
            "kind": "list_item", "wording": "{condition}",
            "slots": {"condition": {"event": "E1", "code": "I50"}},
            "refs": ["E1"]}]),
        dict(valid[0], extra="field"),
    ]
    return valid, invalid


def parse(document):
    """Return the Candidate of a JSON-like document (as a writer's output
    arrives); raises ValidationError when it is malformed."""
    return Candidate.model_validate_json(json.dumps(document))


def run_checks():
    """Return problems found with the invented candidates."""
    problems = []
    valid, invalid = synthetic_candidates()
    for document in valid:
        try:
            parse(document)
        except ValidationError:
            problems.append("a valid synthetic candidate was refused")
    for document in invalid:
        try:
            parse(document)
        except ValidationError:
            continue
        problems.append("an invalid synthetic candidate was accepted")
    for notice_id, entry in NOTICES.items():
        wording = PLACEHOLDER.sub("", entry.text)
        if "{" in wording or "}" in wording:
            problems.append("notice %s has a stray brace" % notice_id)
    return problems


def main(argv=None):
    """Check the invented candidates and the approved notices."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    problems = run_checks()
    for problem in problems:
        print("PROBLEM: %s" % problem)
    print("statement kinds: %d, approved notices: %d" % (len(KINDS),
                                                        len(NOTICES)))
    print("RESULT: %s" % ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
