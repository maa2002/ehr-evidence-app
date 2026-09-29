"""Candidate responses: statements with fixed slots (work item 36).

Whatever writes a response (the templates of qa.templates now, the
language model of Phase 7) produces a candidate, never released text. A
candidate is an ordered list of statements. Each statement has:

* a kind from a closed list, which fixes the facts it states, the
  evidence it cites and the answers it may appear in;
* its facts as typed slots: a condition, counts, visit numbers, a match
  kind, a scope and, in an explanation, the predicted label with its
  score and logit, and each driver with its rank and score. Slot values
  are references and numbers; the text they show is rendered from the
  evidence bundle and the vocabulary, so a writer never types a
  condition name, a count, a visit or a score;
* a wording: the words around the facts, with one placeholder, such as
  {condition}, for each slot of its kind;
* the bundle references that support it.

A notice is a statement whose wording is one of a fixed list of approved
texts: the statement that "not recorded" is not clinical absence, what
an attribution score and the model's score mean, the refusals and the
clarifying questions. A notice may hold slots of its own (the user's
term, suggested names, scopes) and never cites evidence.

An answer about the whole record (a record bundle, evidence.record) is
written the same way: its slots name records, visits and the person by
reference, and a record's name, value, time and a person's age are
rendered from them (validation.record_text).

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

CANDIDATE_VERSION = "1.1"
MAX_REFS = 50
MAX_CLAIMS = contracts.MAX_CLAIMS
MAX_TEXT = contracts.MAX_TEXT
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
         "matches": ("matching record", "matching records"),
         "omitted": ("recorded concept", "recorded concepts"),
         "total": ("visit", "visits"),
         "earlier": ("result", "results")}
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
AttributionRef = Annotated[str, StringConstraints(
    pattern=r"^A[1-9][0-9]{0,5}$")]
Score = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]
Logit = Annotated[float, Field(allow_inf_nan=False)]
Rank = Annotated[int, Field(ge=1, le=10000)]
DOMAIN_WORDS = {"condition": "diagnosis", "procedure": "procedure",
                "drug": "medication"}
RecordRef = Annotated[str, StringConstraints(pattern=r"^R[1-9][0-9]{0,5}$")]
VisitRef = Annotated[str, StringConstraints(pattern=r"^V[1-9][0-9]{0,5}$")]
TermName = Annotated[str, StringConstraints(min_length=1, max_length=255)]
QuestionKind = Literal["condition", "procedure", "medication", "lab",
                       "microbiology", "vital", "transfer", "icu_stay"]
Selector = Literal["latest", "first", "highest", "lowest", "only"]
RecordField = Literal["sex", "age", "race", "language", "insurance",
                      "marital_status", "death", "admitted", "discharged",
                      "length", "type", "admitted_from", "discharged_to",
                      "hospital_death", "summary"]
RecordMatch = Literal["item", "group", "ingredient", "class", "brand",
                      "name", "exact", "more_specific", "icd_code",
                      "more_specific_icd_code", "icd_description"]
# The record tasks a statement kind about the record may answer.
RECORD_LISTS = ("record_list",)
RECORD_ITEMS = ("record_presence", "record_timing", "record_count",
                "record_values", "record_abnormal")


@dataclasses.dataclass(frozen=True)
class Kind:
    """What a kind of statement states, cites and belongs to.

    slots: the slots it fills, each shown by one placeholder;
    refs: "events" (the records it states), "coverage" (the coverage
    record, C1), "prediction" (P1), "attribution" (the driver it names)
    or "none" (it restates the statements it summarises);
    task and outcomes: the answers it may appear in; depth: 0 for a line
    of its own, 1 for an item of a list, 2 for an item under one.
    """

    slots: tuple
    refs: str
    task: str | None
    outcomes: tuple
    depth: int = 0
    tasks: tuple = ()

    def answers(self, task):
        """Return True when a statement of this kind may answer a
        task."""
        return task in self.tasks if self.tasks else task == self.task


KINDS = {
    "list_summary": Kind(("groups", "scope", "records"), "none",
                         "condition_list", ("answer",)),
    "list_item": Kind(("condition", "count", "visits"), "events",
                      "condition_list", ("answer",), depth=1),
    "list_unmapped": Kind(("unmapped",), "coverage", "condition_list",
                          ("answer",)),
    "list_empty": Kind(("scope",), "coverage", "condition_list",
                       ("not_recorded",)),
    "presence_summary": Kind(("condition", "scope", "matches"), "none",
                             "condition_presence", ("answer",)),
    "presence_item": Kind(("condition", "count", "visits", "match"),
                          "events", "condition_presence", ("answer",),
                          depth=1),
    "presence_absent": Kind(("condition", "scope"), "coverage",
                            "condition_presence", ("not_recorded",)),
    "presence_unmapped": Kind(("unmapped",), "coverage",
                              "condition_presence", ("not_recorded",)),
    "prediction": Kind(("label", "score", "logit"), "prediction",
                       "prediction_explanation", ("explanation",)),
    "driver": Kind(("rank", "driver", "score"), "attribution",
                   "prediction_explanation", ("explanation",), depth=1),
    "driver_history": Kind(("driver", "count", "visits"), "events",
                           "prediction_explanation", ("explanation",),
                           depth=2),
    "driver_context": Kind(("driver", "count"), "events",
                           "prediction_explanation", ("explanation",),
                           depth=2),
    # Answers about the whole record (qa.record_answer).
    "record_list_summary": Kind(("items", "scope", "records"), "none",
                                None, ("answer",), tasks=RECORD_LISTS),
    "record_list_item": Kind(("item", "count", "visits"), "records", None,
                             ("answer",), depth=1, tasks=RECORD_LISTS),
    "record_stay_item": Kind(("item", "value"), "records", None,
                             ("answer",), depth=1, tasks=RECORD_LISTS),
    "record_list_empty": Kind(("kind", "scope"), "coverage", None,
                              ("not_recorded",),
                              tasks=RECORD_LISTS + RECORD_ITEMS),
    "record_presence_summary": Kind(("item", "scope", "matches"), "none",
                                    None, ("answer",),
                                    tasks=("record_presence",
                                           "record_timing")),
    "record_presence_item": Kind(("item", "count", "visits",
                                  "how"), "records", None,
                                 ("answer",), depth=1,
                                 tasks=("record_presence",)),
    "record_absent": Kind(("item", "scope"), "coverage", None,
                          ("not_recorded",), tasks=RECORD_ITEMS),
    "record_time": Kind(("selector", "item", "when", "visit"), "records",
                        None, ("answer",), depth=1,
                        tasks=("record_timing",)),
    "record_count": Kind(("item", "count", "scope", "visits"), "records",
                         None, ("answer",), tasks=("record_count",)),
    "record_count_kind": Kind(("items", "scope", "records"), "records",
                              None, ("answer",), tasks=("record_count",)),
    "record_value": Kind(("selector", "item", "value", "when", "visit"),
                         "records", None, ("answer",),
                         tasks=("record_values",)),
    "record_value_group": Kind(("item", "count"), "none", None,
                               ("answer",), tasks=("record_values",
                                                   "record_abnormal")),
    "record_value_item": Kind(("value", "when", "visit"), "records", None,
                              ("answer",), depth=1,
                              tasks=("record_values", "record_abnormal")),
    "record_abnormal_summary": Kind(("item", "scope", "matches"), "none",
                                    None, ("answer",),
                                    tasks=("record_abnormal",)),
    "record_abnormal_absent": Kind(("item", "scope"), "coverage", None,
                                   ("not_recorded",),
                                   tasks=("record_abnormal",)),
    "visit_summary": Kind(("total", "scope"), "none", None,
                          ("answer",), tasks=("visit_details",)),
    "visit_fact": Kind(("visit", "field"), "visit", None, ("answer",),
                       depth=1, tasks=("visit_details",
                                       "person_details")),
    "person_fact": Kind(("field",), "person", None, ("answer",),
                        tasks=("person_details",)),
    "person_age": Kind(("visit", "field"), "person_visit", None,
                       ("answer",), depth=1, tasks=("person_details",)),
    "notice": Kind((), "none", None, vocab.RELEASED_TEXT_OUTCOMES),
}
StatementKind = Literal[tuple(KINDS)]

SUPPORTED = ("It answers questions about what the record holds within the "
             "selected scope: conditions, procedures, medications, "
             "laboratory results, microbiology, vital signs, transfers "
             "and ICU stays, the visits and the person's details.")
ASK = ("Ask what the record holds within the selected scope: for example "
       "the list of conditions or medications, whether one named "
       "condition, medication or test is recorded, or the results of a "
       "test.")
UNSUPPORTED = {
    "treatment": "Treatment and management questions are outside what this "
                 "application answers.",
    "prognosis": "Questions about risk, prognosis or future events are "
                 "outside what this application answers.",
    "diagnosis_request": "This application does not diagnose. It reports "
                         "what is recorded.",
    "other_data": "This question asks about a kind of record that was "
                  "not recognised, such as notes or imaging reports, "
                  "which the record here does not hold.",
    "time_or_count": "This question about dates, times or counts could not "
                     "be interpreted; the visits are chosen with the scope "
                     "selector.",
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
    "drivers": Notice("The recorded concepts with the highest attribution "
                      "scores for this prediction, in order of their "
                      "scores:", ("explanation",), "prediction_explanation"),
    "drivers_omitted": Notice("Not described here, and kept in the "
                              "evidence: {omitted} with lower attribution "
                              "scores.", ("explanation",),
                              "prediction_explanation"),
    "attribution_meaning": Notice("Attribution scores are unsigned "
                                  "sensitivities of the model's output to "
                                  "recorded concepts; they do not show "
                                  "causes, and they are not shares of the "
                                  "prediction.", ("explanation",),
                                  "prediction_explanation"),
    "score_meaning": Notice("The score is the uncalibrated sigmoid of the "
                            "model's logit, not a clinical risk and not a "
                            "probability of disease.", ("explanation",),
                            "prediction_explanation"),
    "batch_sensitive": Notice("Scoring this patient's graph on its own "
                              "does not reproduce the published prediction "
                              "exactly; the published prediction is "
                              "shown.", ("explanation",),
                              "prediction_explanation"),
    "target_attached": Notice("Some of these concepts are also recorded in "
                              "the final admission; those records are "
                              "shown as final-admission context, not as "
                              "history.", ("explanation",),
                              "prediction_explanation"),
    "target_excluded": Notice("Diagnoses the model's output lists that are "
                              "recorded only in the final admission are "
                              "left out.", ("explanation",),
                              "prediction_explanation"),
    "explanation_ineligible": Notice("This prediction has no driver that "
                                     "may be described, so no explanation "
                                     "is given.", ("abstention",)),
    "no_positive_attribution": Notice("No recorded concept has a positive "
                                      "attribution score for this "
                                      "prediction, so no explanation is "
                                      "given.", ("abstention",)),
    "explanation_incomplete": Notice("The records this explanation needs "
                                     "could not be read completely, so no "
                                     "explanation is given.",
                                     ("abstention",)),
    # Answers about the whole record.
    "record_list_complete": Notice("The list is complete for the records "
                                   "of this kind within the selected "
                                   "scope.", ("answer",), "record_list"),
    "values_omitted": Notice("Earlier results are not listed here and are "
                             "kept in the evidence: {earlier}.",
                             ("answer",)),
    "not_flagged": Notice("A result without the laboratory's abnormal flag "
                          "is not stated to be normal.",
                          ("answer", "not_recorded"), "record_abnormal"),
    "vital_no_flag": Notice("Vital signs carry no abnormal flag in the "
                            "record, so the highest or lowest value is "
                            "shown instead.", ("answer", "not_recorded"),
                            "record_values"),
    "vitals_icu_only": Notice("Vital signs and ICU procedures are charted "
                              "during ICU stays only.",
                              ("answer", "not_recorded")),
    "dates_shifted": Notice("Dates are shifted by the de-identification of "
                            "MIMIC-IV, by the same interval for all of this "
                            "patient's records.", ("answer",)),
    "age_meaning": Notice("Ages are the anchor age MIMIC-IV records for the "
                          "patient plus the years to the visit, so they can "
                          "be one more than the age on the day; ages above "
                          "the range it gives exactly are given only as a "
                          "lower bound.",
                          ("answer",),
                          "person_details"),
    "death_meaning": Notice("A date of death comes from the hospital, or "
                            "from state records up to one year after the "
                            "last discharge; no date of death is not a "
                            "statement that the patient is alive.",
                            ("answer",), "person_details"),
    "several_kinds": Notice("\"{term}\" names more than one kind of record: "
                            "{options}. Which one do you mean?",
                            ("clarification",)),
    "item_suggested": Notice("\"{term}\" was not found as the name of a "
                             "test, medication, procedure or other record. "
                             "Did you mean: {labels}?", ("clarification",)),
    "item_unknown": Notice("Which test, medication or procedure do you "
                           "mean? \"{term}\" was not found as the name of "
                           "one.", ("clarification",)),
    "item_missing": Notice("Which test, medication or procedure do you "
                           "mean? Name it to ask about it.",
                           ("clarification",)),
    "item_too_broad": Notice("\"{term}\" names too many tests or items to "
                             "answer at once. Name a more specific one.",
                             ("clarification",)),
}
# The notice that discloses each restriction of an explanation.
RESTRICTION_NOTICES = {"batch_sensitive_prediction": "batch_sensitive",
                       "target_attached_drivers_present": "target_attached",
                       "target_outcome_entries_excluded": "target_excluded"}
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


class ItemCount(Frozen):
    """A count of distinct items of one kind of record."""

    kind: QuestionKind
    count: Count


class ItemName(Frozen):
    """How a statement about the record names what it is about: by a
    record of the bundle, or by the names a question's phrase resolved
    to (as the dictionary writes them)."""

    record: Optional[RecordRef] = None
    terms: tuple[TermName, ...] = Field(default=(), max_length=50)

    @model_validator(mode="after")
    def _one_source(self):
        """Exactly one of a record or terms names the item."""
        if (self.record is not None) == bool(self.terms):
            raise ValueError("an item is named by a record or by terms")
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
    label: Optional[ConceptId] = None
    score: Optional[Score] = None
    logit: Optional[Logit] = None
    driver: Optional[AttributionRef] = None
    rank: Optional[Rank] = None
    omitted: Optional[Count] = None
    kind: Optional[QuestionKind] = None
    item: Optional[ItemName] = None
    value: Optional[RecordRef] = None
    when: Optional[RecordRef] = None
    visit: Optional[VisitRef] = None
    selector: Optional[Selector] = None
    field: Optional[RecordField] = None
    how: Optional[RecordMatch] = None
    items: Optional[ItemCount] = None
    total: Optional[Count] = None
    earlier: Optional[Count] = None
    options: Optional[tuple[QuestionKind, ...]] = Field(default=None,
                                                        max_length=8)
    labels: Optional[tuple[TermName, ...]] = Field(default=None,
                                                   max_length=20)

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

    schema_version: Literal["1.1"]
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


def concept_label(concept):
    """Return the readable label of a concept of a prediction or driver."""
    if concept.name_status == "available":
        return concept.name
    if concept.name_status == "unmapped":
        return "an unmapped concept"
    return "concept %d" % concept.concept_id


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


def bundle_events(bundle):
    """Return a bundle's events by reference."""
    if bundle is None or getattr(bundle, "kind", None) == "record":
        return {}
    events = getattr(bundle, "events", None)
    if events is None:
        events = bundle.history_events + bundle.final_admission_context
    return {event.ref: event for event in events}


class Lookup:
    """The evidence and names a candidate's slots are rendered from: a
    bundle's records, prediction and drivers, and a function that gives a
    vocabulary concept's name, or None."""

    def __init__(self, bundle=None, concept_name=None):
        """Index the bundle's records, drivers and concepts."""
        self.events = bundle_events(bundle)
        self.records = {entry.ref: entry for entry in
                        getattr(bundle, "records", ()) or ()}
        self.visit_refs = {visit.ref: visit for visit in
                           getattr(bundle, "visits", ()) or ()}
        self.person = getattr(bundle, "person", None)
        self.attributions = {item.ref: item for item in
                             getattr(bundle, "attributions", ())}
        self.concepts = {}
        prediction = getattr(bundle, "prediction", None)
        if prediction is not None:
            self.concepts[prediction.concept.concept_id] = prediction.concept
        for item in self.attributions.values():
            self.concepts.setdefault(item.concept.concept_id, item.concept)
        self.concept_name = concept_name or (lambda concept: None)

    def name(self, concept):
        """Return a concept's name: the bundle's, else the vocabulary's."""
        if concept in self.concepts:
            return concept_label(self.concepts[concept])
        name = self.concept_name(concept)
        if name is None:
            raise CandidateError("concept_unknown_to_vocabulary")
        return name

    def known(self, concept):
        """Return True when a concept can be named."""
        return concept in self.concepts or (
            self.concept_name(concept) is not None)

    def record(self, ref):
        """Return a record of the bundle."""
        if ref not in self.records:
            raise CandidateError("record_not_in_bundle")
        return self.records[ref]

    def visit(self, ref):
        """Return a visit of the bundle."""
        if ref not in self.visit_refs:
            raise CandidateError("visit_not_in_bundle")
        return self.visit_refs[ref]


def condition_text(kind, name, lookup):
    """Return the name of a condition as a statement of a kind shows it."""
    if name.event is not None:
        event = lookup.events.get(name.event)
        if event is None:
            raise CandidateError("event_not_in_bundle")
        label = event_label(event)
        if kind == "list_item" and not event.concept.concept_id:
            label += " (no standard concept)"
        return label
    if name.code is not None:
        return "ICD code %s" % name.code
    return "; ".join(lookup.name(concept) for concept in name.concepts)


def driver_text(ref, lookup):
    """Return how a driver is named: its concept and domain."""
    item = lookup.attributions.get(ref)
    if item is None:
        raise CandidateError("driver_not_in_bundle")
    return "%s (%s)" % (concept_label(item.concept),
                        DOMAIN_WORDS[item.domain])


def slot_text(statement, name, lookup):
    """Return the text one slot of a statement shows."""
    value = getattr(statement.slots, name)
    if value is None or value == ():
        raise CandidateError("slot_missing")
    if name in ("scope", "requested"):
        return scope_words(value)
    if name == "condition":
        return condition_text(statement.kind, value, lookup)
    if name == "visits":
        return join_visits(value)
    if name == "match":
        return MATCH_WORDS[value]
    if name == "names":
        return "; ".join(lookup.name(concept) for concept in value)
    if name == "label":
        return lookup.name(value)
    if name == "driver":
        return driver_text(value, lookup)
    if name in ("score", "logit"):
        return shown(value)
    if name in ("term", "rank"):
        return str(value)
    if name in RECORD_SLOTS:
        return record_slot_text(statement, name, value, lookup)
    return counted(name, value)


RECORD_SLOTS = ("kind", "items", "item", "value", "when", "visit",
                "selector", "field", "how", "options", "labels")


def record_slot_text(statement, name, value, lookup):
    """Return the text of a slot of a statement about the record."""
    from ehr_evidence_app.validation import record_text as words
    if name == "kind":
        return words.kind_word(value)
    if name == "items":
        return words.items_text(value.count, value.kind)
    if name == "item":
        if value.record is not None:
            return words.record_name(lookup.record(value.record))
        return "; ".join(value.terms)
    if name == "value":
        return words.value_text(lookup.record(value))
    if name == "when":
        return words.when_text(lookup.record(value))
    if name == "visit":
        return words.visit_text(lookup.visit(value))
    if name == "selector":
        return words.SELECTOR_WORDS[value]
    if name == "how":
        return words.MATCH_WORDS[value]
    if name == "options":
        return ", ".join(words.kind_word(kind, plural=True)
                         for kind in value)
    if name == "labels":
        return "; ".join(value)
    return field_text(statement, value, lookup)


def field_text(statement, field, lookup):
    """Return a visit's or the person's field as a statement shows it."""
    from ehr_evidence_app.validation import record_text as words
    if statement.kind == "visit_fact":
        return words.visit_field(field, lookup.visit(statement.slots.visit))
    if lookup.person is None:
        raise CandidateError("person_not_in_bundle")
    visit = (lookup.visit(statement.slots.visit)
             if statement.slots.visit is not None else None)
    return words.person_field(field, lookup.person, visit)


def shown(value):
    """Return a score or logit as a response shows it: to three
    significant figures."""
    return "%.3g" % value


def sentence(statement, lookup):
    """Return a statement's wording with its slots filled in."""
    return PLACEHOLDER.sub(lambda match: slot_text(
        statement, match.group(1), lookup), statement.wording)


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


def render(candidate, bundle=None, concept_name=None):
    """Return (text, claims) rendered from a candidate's statements.

    Every statement is one line: list items are led by "- ", items under
    them by "  - "; a statement that cites evidence becomes one or more
    claims with the same text.
    """
    lookup = Lookup(bundle, concept_name)
    lines, claims = [], []
    for statement in candidate.statements:
        text = sentence(statement, lookup)
        depth = KINDS[statement.kind].depth
        lines.append(("  " * (depth - 1) + "- " + text) if depth else text)
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
        {"schema_version": "1.1", "request_id": "q-1",
         "task": "condition_list", "case": case, "scope": history,
         "outcome": "not_recorded",
         "bundle": {"bundle_id": "qa-c01-history", "sha256": "5" * 64},
         "statements": [
             {"kind": "list_empty",
              "wording": "No conditions are recorded within {scope}.",
              "slots": {"scope": history}, "refs": ["C1"]},
             {"kind": "notice", "wording": NOTICES["absence"].text,
              "notice": "absence"}]},
        {"schema_version": "1.1", "request_id": "q-2", "case": case,
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
