"""End-to-end assessment of question answering (work item 60; ADR 0028,
decision 7).

The end-to-end assessment starts from a question's text, in the
application as deployed (qa.answer, with the whole record and, when it is
given, the model as writer), and scores each stage:

* interpretation: whether the application read what the writer meant.
  For a list or a presence check this is the task and the scope; for a
  request to refuse, a clarification or an abstention; for a question to
  be answered with a clarifying question, a clarification;
* resolution, for a presence check: whether the condition it resolved is
  the one meant (exact), an ancestor or a descendant of it (related), or
  another (unrelated);
* retrieval: whether the complete sources answer the question (the source
  snapshot is complete) and whether the runtime's bundle holds exactly
  the reference's records (complete coverage, the same records). A
  source-available answer the bundle lacks stays a failure even when the
  runtime's abstention is right for its bundle;
* delivery: whether the released response, scored against the
  independent reference (assessment.metrics), is correct, and who wrote
  it: the model, or the template after the model's failure. The model's
  own results come from the paired comparison only.

Each stage is scored only when the one before it succeeded; the counts
are kept by the writer's intent, and nothing of a question is printed.

The questions are rows in the written test's format (qa.written): case
set, case, scope, question, what the writer meant, and for a presence
check the OMOP concept meant. The operator mode reads a development
written set; the evaluation's set is worded after the freeze (work item
64).

Questions about the whole record (ADR 0016, decision 11) are rows of
their own format: case set, case, scope, question, what the writer
meant (an answer, a refusal or a clarification) and, for an answer,
what it asks (task, kind, the item's identifiers, the selector of a
values question and the field of a visit or the person). They go
through the same pipeline, and are scored against the references of
assessment.record_references:

* interpretation: the task, the kind, whether an item is named, the
  selector and the field the runtime read (its rules are re-run on the
  question to see them), and the scope;
* resolution, for a named item: exact when the item the runtime resolved
  has the identifiers meant, or matches the same records of the case;
  related when they share an identifier or records, or for a condition
  when one concept is an ancestor of the other; else unrelated, or
  unresolved when the runtime asked which item was meant;
* retrieval: whether the record bundle holds every record (or every
  visit) of the reference;
* delivery: the answer against the reference, as the references'
  comparison judges it. A question the references prepare nothing for
  (doses), or that rests on a condition record without a standard
  concept, is counted apart.

They are counted by task and kind.

Usage:
    python -m ehr_evidence_app.assessment.end_to_end
    python -m ehr_evidence_app.assessment.end_to_end --operator-real \\
        --set written-v4
"""

from __future__ import annotations

import argparse
import collections
import csv
import dataclasses
import io
import sys
import typing

from ehr_evidence_app.assessment import metrics, references, stated
from ehr_evidence_app.assessment import record_references as refs
from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.qa import answer, written
from ehr_evidence_app.sources import mimic

INTENDED_TASKS = {"list": "condition_list",
                  "presence": "condition_presence"}
REFUSED = ("clarification", "abstention")
EVIDENTIAL = ("answer", "not_recorded")


class Assessed(typing.NamedTuple):
    """The stages of one question: each verdict, or None when an earlier
    stage failed or the stage does not apply; and the delivered answer's
    writer."""

    intent: str
    interpretation: str
    resolution: str | None = None
    retrieval: str | None = None
    delivery: str | None = None
    writer: str | None = None


def interpretation_of(row, result):
    """Return how the application read a question: right, or what it read
    instead."""
    response = result.response
    if row.intent == "refuse":
        return "right" if response.outcome in REFUSED else "answered"
    if row.intent == "clarify":
        return ("right" if response.outcome == "clarification"
                else "abstained" if response.outcome == "abstention"
                else "answered")
    request = result.request
    if response.outcome not in EVIDENTIAL or request is None:
        return "declined"
    if request.task != INTENDED_TASKS[row.intent]:
        return "other_task"
    if request.scope != row.scope:
        return "other_scope"
    return "right"


def resolution_of(row, result, vocab):
    """Return how a presence check's condition was resolved."""
    resolution = result.resolution
    concepts = tuple(resolution.concepts) if resolution is not None else ()
    if row.concept in concepts:
        return "exact"
    return "related" if written.related(vocab, concepts, row.concept) \
        else "unrelated"


def retrieval_of(oracle, case, row, bundle, complete):
    """Return whether the bundle holds exactly the reference's records:
    complete, incomplete (the sources answer and the bundle does not
    hold them all) or unanswerable (the sources are not complete)."""
    if not complete:
        return "unanswerable"
    expected = frozenset(references.record_key(*item.key)
                         for item in oracle.scope_records(case, row.scope))
    held = frozenset(stated.record_key(event) for event in bundle.events)
    if bundle.coverage.status not in ("complete", "complete_empty") or (
            held != expected):
        return "incomplete"
    return "complete"


def assess_row(row, oracle, cache, vocab, limits, request_id, writer=None,
               records=None, complete=True):
    """Return the Assessed stages of one question, or None for a question
    about a case that is not admitted or a scope the case lacks."""
    case = next((case for case in oracle.cases
                 if case.case_number == row.case_number), None)
    if case is None or row.scope not in oracle.scopes(case):
        return None
    result = answer.answer(row.question, oracle.label, row.case_number,
                           row.scope, cache, vocab, request_id, limits,
                           writer, records=records)
    author = result.written.author if result.written is not None else None
    reading = interpretation_of(row, result)
    if reading != "right" or row.intent not in INTENDED_TASKS:
        return Assessed(row.intent, reading)
    resolution = None
    if row.intent == "presence":
        resolution = resolution_of(row, result, vocab)
        if resolution == "unrelated":
            return Assessed(row.intent, reading, resolution)
    bundle = cache.qa_bundle(row.case_number, row.scope)[0]
    retrieval = retrieval_of(oracle, case, row, bundle, complete)
    if retrieval != "complete":
        return Assessed(row.intent, reading, resolution, retrieval)
    reference = references.condition_reference(
        oracle, oracle.label, case, request_id, INTENDED_TASKS[row.intent],
        row.scope, (row.concept,) if row.intent == "presence" else ())
    response = stated.read(request_id, result.response.outcome,
                           result.candidate, bundle,
                           result.response.text is not None)
    terms = metrics.score(reference, response).terms
    delivery = ("apart" if terms["apart"]
                else "correct" if terms["correct"] else "incorrect")
    return Assessed(row.intent, reading, resolution, retrieval, delivery,
                    author)


def assess(rows, inputs, vocab, limits, writer=None, records_of=None):
    """Return the Assessed stages of every question (None for one left
    out). inputs maps each case set to (oracle, bundle cache, snapshot
    complete); records_of(case set) gives its record context, when the
    whole record is answered."""
    found = []
    for number, row in enumerate(rows, 1):
        oracle, cache, complete = inputs[row.case_set]
        found.append(assess_row(
            row, oracle, cache, vocab, limits, "e-%05d" % number, writer,
            records_of(row.case_set) if records_of else None, complete))
    return found


def tally(assessed):
    """Return {intent: {stage: {verdict: count}}}, the questions left out,
    and the writers of the delivered answers: counts only."""
    found = collections.defaultdict(lambda: collections.defaultdict(
        collections.Counter))
    writers = collections.Counter()
    left_out = 0
    for item in assessed:
        if item is None:
            left_out += 1
            continue
        for stage in ("interpretation", "resolution", "retrieval",
                      "delivery"):
            verdict = getattr(item, stage)
            if verdict is not None:
                found[item.intent][stage][verdict] += 1
        if item.delivery is not None and item.writer is not None:
            writers[(item.writer, item.delivery)] += 1
    return ({intent: {stage: dict(sorted(verdicts.items()))
                      for stage, verdicts in stages.items()}
             for intent, stages in sorted(found.items())},
            left_out, dict(sorted(writers.items())))


# --------------------------------------------------------------------------
# Questions about the whole record
# --------------------------------------------------------------------------

RECORD_COLUMNS = ("id", "case_set", "case", "scope", "question", "intent",
                  "task", "kind", "codes", "groups", "concepts", "written",
                  "words", "selector", "field")
RECORD_INTENTS = ("answer", "refuse", "clarify")
SEPARATOR = ";"
ITEM_KINDS = tuple(refs.RECORD_KINDS)
# The kinds a task asks about. A list or a presence check of conditions
# is a condition question, in the written test's format.
TASK_KINDS = {
    "record_list": tuple(kind for kind in ITEM_KINDS if kind != "condition"),
    "record_presence": tuple(kind for kind in ITEM_KINDS
                             if kind != "condition"),
    "record_timing": ITEM_KINDS, "record_count": ITEM_KINDS,
    "record_values": ("lab", "vital", "microbiology", "medication"),
    "record_abnormal": refs.FLAGGED_KINDS,
    "visit_details": ("visit",), "person_details": ("person",)}
# The identifiers that name an item of a kind.
IDENTIFIERS = {"lab": ("codes",), "vital": ("groups",),
               "condition": ("concepts",),
               "medication": ("concepts", "written"),
               "procedure": ("codes", "words"), "microbiology": ("words",),
               "transfer": ("words",), "icu_stay": ()}
FIELDS = {"visit_details": refs.VISIT_ASKED,
          "person_details": refs.PERSON_ASKED}
DETAILS = tuple(FIELDS)


@dataclasses.dataclass(frozen=True)
class RecordRow:
    """One written question about the whole record; asked is what its
    writer meant it to ask (an assessment.record_references.Asked), for a
    question to be answered."""

    id: str
    case_set: str
    case_number: int
    scope: contracts.ScopeRef
    question: str
    intent: str
    asked: refs.Asked | None = None
    family: str = ""

    @property
    def stratum(self):
        """Return the row's stratum: its task and kind, or its intent."""
        if self.asked is None:
            return self.intent
        return "%s %s" % (self.asked.task, self.asked.kind)


def identifiers_of(line, kind):
    """Return the Asked fields of a row's identifiers, or None when one is
    malformed or does not name an item of the kind."""
    values = {name: [value.strip() for value in line[name].split(SEPARATOR)
                     if value.strip()]
              for name in ("codes", "groups", "concepts", "written",
                           "words")}
    allowed = IDENTIFIERS.get(kind, ())
    if any(values[name] for name in values if name not in allowed):
        return None
    if not all(value.isdigit() for value in values["codes"] + values[
            "concepts"]) or not all(value in mimic.VITAL_GROUPS
                                    for value in values["groups"]):
        return None
    phrases = {name: tuple(tuple(refs.normalize(value).split())
                           for value in values[name])
               for name in ("written", "words")}
    if any(not words for found in phrases.values() for words in found):
        return None
    return {"codes": frozenset(values["codes"]),
            "groups": frozenset(values["groups"]),
            "concepts": frozenset(int(value) for value in values["concepts"]),
            **phrases}


def record_problem(line):
    """Return what is wrong with the meaning of an answer row, or (None,
    asked)."""
    task, kind = line["task"], line["kind"]
    if task not in TASK_KINDS:
        return "task_invalid", None
    if kind == "condition" and task in ("record_list", "record_presence"):
        return "condition_question", None
    if kind not in TASK_KINDS[task]:
        return "kind_invalid", None
    selector = line["selector"] or None
    if (task == "record_values") != (selector is not None) or (
            selector is not None and selector not in refs.SELECTORS):
        return "selector_invalid", None
    field = line["field"] or None
    if (task in DETAILS) != (field is not None) or (
            field is not None and field not in FIELDS[task]):
        return "field_invalid", None
    found = identifiers_of(line, kind)
    if found is None or (task in DETAILS + ("record_list",) and any(
            found.values())):
        return "item_invalid", None
    if task == "record_presence" and not any(found.values()):
        # Whether any record of a kind is recorded is asked as its list.
        return "item_missing", None
    return None, refs.Asked(task, kind, selector=selector, field=field,
                            **found)


def parse_record_questions(data, allowed):
    """Return (rows, problems) of a file of questions about the whole
    record.

    A row naming a case set outside allowed stops the whole file, so an
    evaluation case set is never read. Other faulty rows are counted by
    problem and left out.
    """
    try:
        text = data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        header = tuple(name.strip().lower()
                       for name in reader.fieldnames or ())
        if header not in (RECORD_COLUMNS, RECORD_COLUMNS + (written.FAMILY,)):
            raise written.WrittenError("questions_columns_unexpected")
        raw = [{key.strip().lower(): (value or "").strip()
                for key, value in line.items() if key}
               for line in reader]
    except (UnicodeDecodeError, csv.Error):
        raise written.WrittenError("questions_unreadable") from None
    if len(raw) > written.MAX_QUESTIONS:
        raise written.WrittenError("questions_too_many")
    rows, problems, seen = [], collections.Counter(), set()
    for line in raw:
        if not any(line.values()):
            continue
        if line["case_set"] not in allowed:
            raise written.WrittenError("case_set_not_development")
        scope = written.scope_ref(line["scope"])
        problem = (
            "id_missing_or_repeated" if not line["id"] or line["id"] in seen
            else "case_invalid" if not line["case"].isdigit()
            else "scope_invalid" if scope is None
            else "question_missing" if not line["question"]
            else "intent_invalid" if line["intent"] not in RECORD_INTENTS
            else None)
        seen.add(line["id"])
        asked = None
        if problem is None and line["intent"] == "answer":
            problem, asked = record_problem(line)
        elif problem is None and any(line[name] for name in RECORD_COLUMNS[
                6:]):
            problem = "meaning_not_asked"
        if problem:
            problems[problem] += 1
            continue
        rows.append(RecordRow(line["id"], line["case_set"],
                              int(line["case"]), scope, line["question"],
                              line["intent"], asked,
                              line.get(written.FAMILY, "")))
    return rows, problems


class RecordInputs(typing.NamedTuple):
    """What one case set's questions about the record are assessed with:
    its QA bundle set, its record context (qa.record_answer), the
    references' cases by number, and the condition vocabulary."""

    bundle_set: object
    records: object
    cases: dict
    conditions: object


def reading_of(result, records, question):
    """Return the runtime's reading of a question it read as one about the
    whole record, by its rules re-run on the question, or None."""
    from ehr_evidence_app.qa import record_interpret
    request = result.request
    if request is None or request.task not in refs.TASKS:
        return None
    return record_interpret.interpret(answer.well_formed(question),
                                      records.terms)


def record_interpretation_of(row, result, reading, resolution):
    """Return how the application read a question about the record: right,
    or what it read instead."""
    response = result.response
    if row.intent == "refuse":
        return "right" if response.outcome in REFUSED else "answered"
    if row.intent == "clarify":
        return ("right" if response.outcome == "clarification"
                else "abstained" if response.outcome == "abstention"
                else "answered")
    asked, request = row.asked, result.request
    if request is None:
        return "declined"
    if request.task != asked.task:
        return "other_task"
    if reading is None or reading.status != "task":
        return "declined"
    if reading.kind != asked.kind:
        return "other_kind"
    if asked.task == "record_values" and reading.selector != asked.selector:
        return "other_selector"
    if asked.task in DETAILS and reading.field != asked.field:
        return "other_field"
    if bool(reading.phrase) != asked.named:
        return "other_item"
    if request.scope != row.scope:
        return "other_scope"
    unresolved = resolution is not None and resolution.status != "resolved"
    if response.outcome not in EVIDENTIAL and not (
            unresolved and response.outcome == "clarification"):
        return "declined"
    return "right"


def named_by(asked):
    """Return the identifiers of an Asked, as sets."""
    return (asked.codes, asked.groups, asked.concepts,
            frozenset(asked.written), frozenset(asked.words))


def record_resolution_of(asked, reading, resolution, case, descendants,
                         conditions):
    """Return how the item a question names was resolved: exact, related,
    unrelated or unresolved."""
    if resolution is None or resolution.status != "resolved":
        return "unresolved"
    found = refs.asked_of(reading, resolution)
    if named_by(found) == named_by(asked):
        return "exact"
    rows = case.scoped(contracts.ScopeRef(kind="case_visits"), asked.kind)
    meant = frozenset(row.key for row in rows
                      if refs.matches(row, asked, descendants))
    read = frozenset(row.key for row in rows
                     if refs.matches(row, found, descendants))
    if meant and meant == read:
        return "exact"
    shared = any(mine & theirs for mine, theirs in zip(named_by(asked),
                                                      named_by(found)))
    if shared or meant & read or (asked.kind == "condition" and any(
            written.related(conditions, found.concepts, concept)
            for concept in asked.concepts)):
        return "related"
    return "unrelated"


def record_retrieval_of(expected, bundle):
    """Return whether the record bundle holds every record of the
    reference, or for details every visit: complete or incomplete."""
    if expected.task in DETAILS:
        held = {visit.visit_number for visit in bundle.visits}
        wanted = set(expected.details) - {0}
    else:
        held = {("record", entry.kind, entry.native_id)
                for entry in bundle.records}
        wanted = set().union(*expected.records.values(), *(
            value if isinstance(value, frozenset) else (value,)
            for value in expected.picked.values()))
    return "complete" if wanted <= held else "incomplete"


def scope_of_case(case, scope):
    """Return True when a case has a scope."""
    if scope.kind == "pre_target_history":
        return case.count > 1
    if scope.kind == "selected_visit":
        return 1 <= scope.visit_number <= case.count
    return True


def assess_record_row(row, inputs, limits, request_id, writer=None):
    """Return the Assessed stages of one question about the record, or
    None for a question about a case that is not admitted or a scope the
    case lacks."""
    from ehr_evidence_app.qa import record_answer
    case = inputs.cases.get(row.case_number)
    if case is None or not scope_of_case(case, row.scope):
        return None
    result = answer.answer(row.question, row.case_set, row.case_number,
                           row.scope, inputs.bundle_set, inputs.conditions,
                           request_id, limits, writer,
                           records=inputs.records)
    author = result.written.author if result.written is not None else None
    reading = reading_of(result, inputs.records, row.question)
    resolution = (record_answer.resolution_of(inputs.records, reading)
                  if reading is not None and reading.status == "task"
                  else None)
    reading_verdict = record_interpretation_of(row, result, reading,
                                               resolution)
    if reading_verdict != "right" or row.asked is None:
        return Assessed(row.stratum, reading_verdict)

    def descendants(concept):
        """Return a condition concept and its descendants."""
        return frozenset(inputs.conditions.descendants_of(concept))
    resolved = None
    if row.asked.named:
        resolved = record_resolution_of(row.asked, reading, resolution, case,
                                        descendants, inputs.conditions)
        if resolved in ("unresolved", "unrelated"):
            return Assessed(row.stratum, reading_verdict, resolved)
    expected = refs.reference(request_id, case, row.scope, row.asked,
                              descendants)
    if expected.outcome in ("none", "unjudged"):
        return Assessed(row.stratum, reading_verdict, resolved, None,
                        "apart", author)
    bundle = inputs.records.records.bundle(row.case_number, row.scope)[0]
    retrieval = record_retrieval_of(expected, bundle)
    if retrieval != "complete":
        return Assessed(row.stratum, reading_verdict, resolved, retrieval)
    if row.asked.task in DETAILS:
        verdict = refs.details_verdict(expected, result.candidate, bundle,
                                       row.asked.field)
    else:
        verdict = refs.difference(expected, result.candidate, bundle,
                                  case.by_key, row.asked.named)
    delivery = ("apart" if verdict in ("none", "unjudged")
                else "correct" if verdict == "right" else "incorrect")
    return Assessed(row.stratum, reading_verdict, resolved, retrieval,
                    delivery, author)


def assess_records(rows, inputs, limits, writer=None):
    """Return the Assessed stages of every question about the record (None
    for one left out); inputs maps each case set to its RecordInputs."""
    return [assess_record_row(row, inputs[row.case_set], limits,
                              "r-%05d" % number, writer)
            for number, row in enumerate(rows, 1)]


# --------------------------------------------------------------------------
# Default and operator modes
# --------------------------------------------------------------------------

def synthetic_inputs():
    """Return (rows, inputs, vocabulary, limits) of the written test's
    invented questions."""
    from ehr_evidence_app.evidence import bundles, contracts
    from ehr_evidence_app.qa import suite
    snapshot, case_set, _, _, _ = bundles.synthetic_inputs()
    vocab = suite.synthetic_vocabulary()
    provenance = bundles.synthetic_provenance()
    cache = suite.BundleCache(suite.synthetic_bundle_set(), (
        provenance.source_snapshot.sha256, provenance.closure_record.sha256,
        provenance.identity_mapping.sha256))
    rows, _ = written.parse_questions(written.csv_bytes(
        written.SYNTHETIC_QUESTIONS), (case_set.label,))
    return (rows, {case_set.label: (suite.Oracle(snapshot, case_set, vocab),
                                    cache, snapshot.complete)}, vocab,
            contracts.ExecutionLimits(**bundles.SYNTHETIC_LIMITS))


SYNTHETIC_EXPECTED = (
    {"list": {"delivery": {"correct": 1}, "interpretation": {"right": 1},
              "retrieval": {"complete": 1}},
     "presence": {"delivery": {"correct": 2, "incorrect": 1},
                  "interpretation": {"declined": 1, "right": 3},
                  "resolution": {"exact": 2, "related": 1},
                  "retrieval": {"complete": 3}},
     "refuse": {"interpretation": {"right": 1}},
     "clarify": {"interpretation": {"right": 1}}},
    1)


SYNTHETIC_LABEL = "synthetic-1-v1"
SYNTHETIC_RECORD_QUESTIONS = (
    RECORD_COLUMNS,
    ("r01", SYNTHETIC_LABEL, "1", "all",
     "What was the last synthetic potassium?", "answer", "record_values",
     "lab", "50971", "", "", "", "", "latest", ""),
    ("r02", SYNTHETIC_LABEL, "1", "all",
     "When was synthetic potassium first measured?", "answer",
     "record_timing", "lab", "50971", "", "", "", "", "", ""),
    ("r03", SYNTHETIC_LABEL, "1", "all", "How many ICU stays were there?",
     "answer", "record_count", "icu_stay", "", "", "", "", "", "", ""),
    ("r04", SYNTHETIC_LABEL, "1", "all", "What was the latest heart rate?",
     "answer", "record_values", "vital", "", "heart_rate", "", "", "",
     "latest", ""),
    ("r05", SYNTHETIC_LABEL, "1", "all", "When was the patient admitted?",
     "answer", "visit_details", "visit", "", "", "", "", "", "",
     "admitted"),
    ("r06", SYNTHETIC_LABEL, "1", "all", "What is the patient's sex?",
     "answer", "person_details", "person", "", "", "", "", "", "", "sex"),
    ("r07", SYNTHETIC_LABEL, "1", "all",
     "What doses of synthetic concept 3001 were given?", "answer",
     "record_values", "medication", "", "", "7001", "", "", "all", ""),
    ("r08", SYNTHETIC_LABEL, "1", "all",
     "What was the highest synthetic potassium?", "answer", "record_values",
     "lab", "50971", "", "", "", "", "latest", ""),
    ("r09", SYNTHETIC_LABEL, "1", "all",
     "How many synthetic potassium results were there?", "answer",
     "record_values", "lab", "50971", "", "", "", "", "all", ""),
    ("r10", SYNTHETIC_LABEL, "1", "all",
     "When was synthetic potassium first measured?", "answer",
     "record_timing", "lab", "50912", "", "", "", "", "", ""),
    ("r11", SYNTHETIC_LABEL, "1", "all",
     "How many times was synthetic parent disorder recorded?", "answer",
     "record_count", "condition", "", "", "1001", "", "", "", ""),
    ("r12", SYNTHETIC_LABEL, "1", "all",
     "When was synthetic gamma disorder first recorded?", "answer",
     "record_timing", "condition", "", "", "1002", "", "", "", ""),
    ("r13", SYNTHETIC_LABEL, "1", "all", "Should we give more potassium?",
     "refuse", "", "", "", "", "", "", "", "", ""),
    ("r14", SYNTHETIC_LABEL, "1", "all",
     "What was the last synthetic potassium or heart rate?", "clarify", "",
     "", "", "", "", "", "", "", ""),
    ("r15", SYNTHETIC_LABEL, "1", "all",
     "What were all the synthetic heart rate values?", "answer",
     "record_values", "vital", "", "heart_rate", "", "", "", "all", ""),
    ("r16", SYNTHETIC_LABEL, "9", "all", "What labs were done?", "answer",
     "record_list", "lab", "", "", "", "", "", "", ""),
)
SYNTHETIC_RECORD_EXPECTED = (
    {"record_count condition": {"delivery": {"incorrect": 1},
                                "interpretation": {"right": 1},
                                "resolution": {"related": 1},
                                "retrieval": {"complete": 1}},
     "record_count icu_stay": {"delivery": {"correct": 1},
                               "interpretation": {"right": 1},
                               "retrieval": {"complete": 1}},
     "record_timing condition": {"interpretation": {"right": 1},
                                 "resolution": {"unresolved": 1}},
     "record_timing lab": {"delivery": {"correct": 1},
                           "interpretation": {"right": 2},
                           "resolution": {"exact": 1, "unrelated": 1},
                           "retrieval": {"complete": 1}},
     "record_values lab": {"delivery": {"correct": 1},
                           "interpretation": {"other_selector": 1,
                                              "other_task": 1, "right": 1},
                           "resolution": {"exact": 1},
                           "retrieval": {"complete": 1}},
     "record_values medication": {"delivery": {"apart": 1},
                                  "interpretation": {"right": 1},
                                  "resolution": {"exact": 1}},
     "record_values vital": {"delivery": {"correct": 1},
                             "interpretation": {"declined": 1, "right": 1},
                             "resolution": {"exact": 1},
                             "retrieval": {"complete": 1}},
     "visit_details visit": {"delivery": {"correct": 1},
                             "interpretation": {"right": 1},
                             "retrieval": {"complete": 1}},
     "person_details person": {"delivery": {"correct": 1},
                               "interpretation": {"right": 1},
                               "retrieval": {"complete": 1}},
     "refuse": {"interpretation": {"right": 1}},
     "clarify": {"interpretation": {"right": 1}}},
    1)


def synthetic_record_inputs():
    """Return (rows, inputs, limits) of the invented questions about the
    invented case's whole record: the application's invented sources and
    the references' reading of the same invented records."""
    from ehr_evidence_app.api import service
    from ehr_evidence_app.evidence import bundles, record_bundles
    from ehr_evidence_app.qa import suite
    vocab = suite.synthetic_vocabulary()
    source = service.invented_source(vocab)
    snapshot, case_set, extract, ancestors = (
        record_bundles.synthetic_sources())
    cases = {case.case_number: refs.Case(case, snapshot, extract, {
        concept: frozenset(found) for concept, found in ancestors.items()})
        for case in case_set.cases}
    rows, _ = parse_record_questions(written.csv_bytes(
        SYNTHETIC_RECORD_QUESTIONS), (source.label,))
    return (rows, {source.label: RecordInputs(source.bundle_set,
                                              source.records, cases, vocab)},
            contracts.ExecutionLimits(**bundles.SYNTHETIC_LIMITS))


def run_synthetic():
    """Return (counts, problems) of the invented questions: the written
    test's, then those about the whole record."""
    rows, inputs, vocab, limits = synthetic_inputs()
    found, left_out, _ = tally(assess(rows, inputs, vocab, limits))
    problems = []
    expected, expected_left_out = SYNTHETIC_EXPECTED
    if found != {intent: expected[intent] for intent in sorted(expected)}:
        problems.append("the invented questions' stages are unexpected")
    if left_out != expected_left_out:
        problems.append("the question about a case not admitted is not "
                        "left out")
    rows, inputs, limits = synthetic_record_inputs()
    record_found, record_left_out, _ = tally(assess_records(rows, inputs,
                                                            limits))
    expected, expected_left_out = SYNTHETIC_RECORD_EXPECTED
    if record_found != {stratum: expected[stratum]
                        for stratum in sorted(expected)}:
        problems.append("the invented questions about the record end at "
                        "unexpected stages")
    if record_left_out != expected_left_out:
        problems.append("the question about the record of a case not "
                        "admitted is not left out")
    return (found, left_out, record_found, record_left_out), problems


def run_real(name):
    """Assess a development written set with the templates as writers;
    return the counts."""
    from ehr_evidence_app.evidence import contracts
    from ehr_evidence_app.gnn import mapping
    from ehr_evidence_app.qa import resolve
    path = written.set_dir(name) + "\\" + written.QUESTIONS_NAME
    try:
        data, _ = mapping.read_verified(path, "questions",
                                        "input_hash_mismatch:questions")
    except mapping.MappingError as error:
        raise written.WrittenError(str(error)) from None
    rows, _ = written.parse_questions(data, written.development_labels())
    vocab = resolve.load()
    inputs = {}
    for label in sorted({row.case_set for row in rows}):
        oracle, cache, _ = written.case_set_inputs(label, vocab)
        inputs[label] = (oracle, cache, True)
    return tally(assess(rows, inputs, vocab, contracts.load_limits()))


def report(found, left_out, writers=None):
    """Print the counts by intent (or stratum) and stage."""
    for intent, stages in found.items():
        for stage, verdicts in stages.items():
            print("%-25s %-15s %s" % (intent, stage, ", ".join(
                "%s %d" % item for item in verdicts.items())))
    print("questions left out: %d" % left_out)
    for (writer, delivery), count in (writers or {}).items():
        print("delivered by %s: %s %d" % (writer, delivery, count))


def main(argv=None):
    """Run the synthetic default mode or the operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="assess a development written set with the "
                             "templates; prints counts only")
    parser.add_argument("--set", default=written.SETS[-1])
    args = parser.parse_args(argv)
    try:
        if args.operator_real:
            found, left_out, writers = run_real(args.set)
            report(found, left_out, writers)
        else:
            (found, left_out, record_found, record_left_out), problems = (
                run_synthetic())
            report(found, left_out)
            report(record_found, record_left_out)
            for problem in problems:
                print("FAILED: %s" % problem)
            if problems:
                return 1
    except (written.WrittenError, references.ReferenceError) as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
