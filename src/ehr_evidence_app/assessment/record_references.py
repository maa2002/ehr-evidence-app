"""Independent references of questions about the whole record (ADR 0016,
decision 11; ADR 0028, decision 4).

A question about the record is named by what its writer meant: its task,
its kind of record, the item it asks about (as the kind's identifiers),
its selector and, for a visit or the person, the field. Its reference is
prepared from the case set's raw extract and source snapshot by code that
reads no record bundle and runs no part of the runtime's reading,
resolution, answering or checks. It shares only the pinned readers of
the source records and the vocabularies' tables.

* **Scope.** A case's visits are its closure record's, numbered in order.
  The snapshot records each visit's admission ("subject|admission"). A
  raw row belongs to the visit whose admission it names, and a condition,
  procedure or medication record of the snapshot to the visit it names.
  The pre-target history is every visit before the last.
* **Items** (ADR 0016, decision 11). An item is:
  - a condition, procedure or medication: its standard concept, or for a
    record without one its source code and written name;
  - a laboratory test or ICU item: its item number;
  - a microbiology test: its name and specimen;
  - a transfer: its care unit;
  - an ICU stay: the stay itself.
* **Matching an asked item.** It is given as identifiers:
  - laboratory and ICU items by their numbers, vital signs by their group;
  - conditions by standard concepts and their descendants (a record
    without a standard concept is unjudged);
  - medications by drug concepts among a record's concept, ingredients
    and classes, and a record without a standard concept by names written
    as whole words in its drug name;
  - procedures, microbiology tests and care units by the words of their
    names.
* **Expected answers.**
  - A list gives every item of the kind with its records.
  - A presence check gives every matching record, or "not recorded".
  - A time gives the first and the last matching record by time, then
    row.
  - A count gives the matching records, or a kind's items and records.
  - Values give, for each matching item, the record its selector picks:
    the latest, first, highest or lowest (the latest when no value is a
    number; a single record is the only one). For all values: the latest
    30 of a named item, and the latest of each item of another whole
    kind. A microbiology test's values are its cultures, the records
    taken at one time (from record-answer-v6): the latest or the first
    culture, or every culture that grew an organism and the latest of
    the others, 30 cultures in all.
  - Abnormal results are the laboratory results flagged abnormal and the
    microbiology results reported resistant.
  - Visit details give the field for every visit of the scope; person
    details give the person's sex or date of death, or the field for
    every visit.

Medication doses come from the prescription each drug record was made
from, and are not prepared here; a question about doses has no
reference.

The comparison mode prepares, for a development case set, the reference
of structured questions about every kind in every scope, the items taken
from the runtime's resolution of the names of records present. It answers
each with the templates (qa.record_answer) and compares what each
answer cites with its reference. Differences are counted by task, kind
and kind of difference; nothing of a record is printed.

Usage:
    python -m ehr_evidence_app.assessment.record_references
    python -m ehr_evidence_app.assessment.record_references \\
        --operator-real --case-set dev-30-v1
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import re
import sys
import unicodedata

from ehr_evidence_app.sources import mimic

TASKS = ("record_list", "record_presence", "record_timing", "record_count",
         "record_values", "record_abnormal", "visit_details",
         "person_details")
ITEM_TASKS = ("record_presence", "record_timing", "record_count",
              "record_values", "record_abnormal")
RECORD_KINDS = {"condition": ("condition",),
                "procedure": ("procedure", "icu_procedure"),
                "medication": ("medication",), "lab": ("lab",),
                "microbiology": ("microbiology",), "vital": ("vital",),
                "transfer": ("transfer",), "icu_stay": ("icu_stay",)}
OMOP_KINDS = {"condition": "condition", "procedure": "procedure",
              "drug": "medication"}
SELECTORS = ("latest", "first", "highest", "lowest", "all")
MAX_VALUES = 30
SHORT_WORD = 4
VITAL_GROUP = {str(item): group for group, items in mimic.VITAL_GROUPS.items()
               for item in items}
RESISTANT = "R"
VISIT_FIELDS = {"admitted": "admittime", "discharged": "dischtime",
                "type": "admission_type",
                "admitted_from": "admission_location",
                "discharged_to": "discharge_location"}
PERSON_VISIT_FIELDS = {"race": "race", "language": "language",
                       "insurance": "insurance",
                       "marital_status": "marital_status"}


class RecordReferenceError(Exception):
    """A fail-closed failure carrying a finding code only."""


def normalize(text):
    """Return a text in lower case, without accents, with plain words."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def words_in(words, text):
    """Return True when a text holds every word: short words whole,
    longer ones within a word."""
    tokens = text.split()
    return all(word in tokens if len(word) < SHORT_WORD
               else any(word in token for token in tokens)
               for word in words)


def clean(value):
    """Return a raw field's text, or None when it is empty."""
    value = (value or "").strip()
    return value or None


def number_of(value):
    """Return a raw field as a finite number, or None."""
    try:
        found = float(value)
    except (TypeError, ValueError):
        return None
    return found if found == found and abs(found) != float("inf") else None


def integer(value):
    """Return a raw field as an integer of ASCII digits, or None."""
    value = clean(value)
    return int(value) if value and value.isascii() and value.isdigit() \
        else None


def time_of(value):
    """Return a raw time to the second, or a date."""
    value = clean(value)
    return value[:19] if value else ""


@dataclasses.dataclass(frozen=True)
class Row:
    """One record of a case as the references read it."""

    kind: str
    native_id: int
    visit: int
    item: tuple
    time: str = ""
    number: float | None = None
    value: str | None = None
    flagged: bool = False
    concept: int = 0
    ancestors: frozenset = frozenset()
    words: str = ""
    written: str = ""
    group: str | None = None
    valued: bool = True

    @property
    def key(self):
        """Return the record's name in the metrics."""
        return ("record", self.kind, self.native_id)

    @property
    def order(self):
        """Return the record's place in time: its time, then its row."""
        return (self.time, self.native_id)


@dataclasses.dataclass(frozen=True)
class Asked:
    """What a question asks about: its task and kind, the item's
    identifiers (codes: laboratory or ICU item numbers; groups: vital
    signs; concepts: conditions or drug concepts; written: names a
    medication without a concept is matched by; words: words of names),
    the selector and the field."""

    task: str
    kind: str | None = None
    codes: frozenset = frozenset()
    groups: frozenset = frozenset()
    concepts: frozenset = frozenset()
    written: tuple = ()
    words: tuple = ()
    selector: str | None = None
    field: str | None = None

    @property
    def named(self):
        """Return True when the question names an item."""
        return bool(self.codes or self.groups or self.concepts
                    or self.written or self.words)


@dataclasses.dataclass(frozen=True)
class RecordReference:
    """The expected answer of a question about the record.

    outcome is "answer" or "not_recorded" ("unjudged" for a condition
    question resting on records without a standard concept, and "none"
    for one this module prepares no reference for); records holds the
    records an answer names, by item (a list, a presence check, a count,
    abnormal results); picked holds the records each item's selector
    picks (values), or the first and the last (a time); details holds
    each visit's value of the field asked about (or the person's, under
    0); visits holds the visit numbers of the records.
    """

    request_id: str
    task: str
    kind: str | None
    outcome: str
    records: dict = dataclasses.field(default_factory=dict)
    picked: dict = dataclasses.field(default_factory=dict)
    details: dict = dataclasses.field(default_factory=dict)
    visits: tuple = ()


# A Case's Records

def visits_of(case, snapshot):
    """Return {admission: visit number} of a case's visits; two visits of
    one admission stop the references."""
    found = {}
    for number, visit_id in enumerate(case.visit_ids, 1):
        source = snapshot.visits.get(visit_id)
        if source is None:
            raise RecordReferenceError("case_visit_missing")
        subject, _, admission = (source.source_value or "").partition("|")
        if subject == str(case.subject_id) and admission:
            if admission in found:
                raise RecordReferenceError("visit_admission_repeated")
            found[admission] = number
    return found


def full_time(value):
    """Return a time as 'YYYY-MM-DD HH:MM:SS', or ''."""
    value = (value or "").replace("T", " ")[:19]
    return value + ":00" if len(value) == 16 else value


def prescribed(extract, subject, admissions):
    """Return {(admission, start, stop, code): written drug or None} of a
    person's prescriptions: the drug each one writes, keyed as the
    source's drug records carry it (the start and stop times, and the NDC
    or else the drug's name and strength). None when prescriptions that
    share the keys write different drugs."""
    found = {}
    for row in extract.rows(subject, "prescriptions"):
        admission = clean(row.get("hadm_id"))
        drug = clean(row.get("drug"))
        start = full_time(clean(row.get("starttime")))
        if admission not in admissions or not drug or not start:
            continue
        stop = full_time(clean(row.get("stoptime"))) or start
        code = clean(row.get("ndc")) or " ".join(filter(None, (
            drug, clean(row.get("prod_strength")))))
        key = (admission, start, stop, code)
        if key in found and found[key] != drug:
            found[key] = None
        else:
            found.setdefault(key, drug)
    return found


def omop_rows(case, snapshot, ancestors, written_of=None):
    """Return the condition, procedure and medication records of a
    case's visits from the snapshot. written_of(event, visit number)
    gives the drug a medication's prescription writes, or None."""
    numbers = {visit_id: number
               for number, visit_id in enumerate(case.visit_ids, 1)}
    found = []
    for domain, kind in OMOP_KINDS.items():
        for event in snapshot.events[domain]:
            if event.person_id != case.person_id or (
                    event.visit_occurrence_id not in numbers):
                continue
            source = snapshot.concepts.get(event.source_concept_id) \
                if event.source_concept_id else None
            code = ((source.code or "") if source is not None
                    else event.source_value or "")
            written = event.source_value or ""
            if kind == "medication" and written_of is not None:
                written = written_of(
                    event, numbers[event.visit_occurrence_id]) or ""
            names = []
            if event.concept_id:
                row = snapshot.concepts.get(event.concept_id)
                names.append(row.name if row is not None else "")
                item = (kind, "concept", event.concept_id)
            else:
                item = (kind, "code", code, written)
            if source is not None:
                names.append(source.name or "")
            names.append(written)
            found.append(Row(
                kind, event.native_id, numbers[event.visit_occurrence_id],
                item, time_of(event.start_datetime or event.start_date),
                concept=event.concept_id or 0,
                ancestors=frozenset(ancestors.get(event.concept_id, ()))
                if kind == "medication" else frozenset(),
                words=normalize(" ".join(names)),
                written=normalize(written) if not event.concept_id
                else ""))
    return found


def raw_rows(extract, subject, admissions):
    """Return a person's raw records of the case's admissions."""
    found = []

    def visit(row):
        """Return the visit number of a raw row's admission, or None."""
        return admissions.get(clean(row.get("hadm_id")))
    for row in extract.rows(subject, "labevents"):
        number = visit(row)
        if number is None:
            continue
        item = extract.labs.get(row["itemid"], {})
        valued = bool(clean(row["value"])) or (
            number_of(row["valuenum"]) is not None)
        found.append(Row(
            "lab", integer(row["labevent_id"]), number,
            ("lab", "item", row["itemid"]), time_of(row["charttime"]),
            number_of(row["valuenum"]), clean(row["value"]),
            valued and clean(row["flag"]) == "abnormal", valued=valued,
            words=normalize(" ".join(filter(None, (
                item.get("label"), item.get("fluid"),
                item.get("category")))))))
    for row in extract.rows(subject, "chartevents"):
        number = visit(row)
        if number is None:
            continue
        item = extract.items.get(row["itemid"], {})
        found.append(Row(
            "vital", integer(row["row_number"]), number,
            ("vital", "item", row["itemid"]), time_of(row["charttime"]),
            number_of(row["valuenum"]), clean(row["value"]),
            words=normalize(item.get("label")),
            group=VITAL_GROUP.get(row["itemid"]),
            valued=bool(clean(row["value"])) or (
                number_of(row["valuenum"]) is not None)))
    for row in extract.rows(subject, "procedureevents"):
        number = visit(row)
        if number is None:
            continue
        item = extract.items.get(row["itemid"], {})
        found.append(Row(
            "icu_procedure", integer(row["row_number"]), number,
            ("icu_procedure", "item", row["itemid"]),
            time_of(row["starttime"]), number_of(row["value"]),
            clean(row["value"]),
            words=normalize(" ".join(filter(None, (
                item.get("label"), row.get("ordercategoryname"))))),
            valued=bool(clean(row["value"]))))
    for row in extract.rows(subject, "microbiologyevents"):
        number = visit(row)
        if number is None:
            continue
        resistant = (clean(row["interpretation"]) == RESISTANT
                     and bool(clean(row["ab_name"]))
                     and bool(clean(row["org_name"])))
        found.append(Row(
            "microbiology", integer(row["microevent_id"]), number,
            ("microbiology", "test",
             clean(row["test_name"]) or clean(row["spec_type_desc"]) or "",
             clean(row["spec_type_desc"]) or ""),
            time_of(row["charttime"] or row["chartdate"]),
            value=clean(row["org_name"]), flagged=resistant,
            words=normalize(" ".join(filter(None, (
                row["test_name"], row["spec_type_desc"], row["org_name"],
                row["ab_name"] if clean(row["org_name"]) else None))))))
    for row in extract.rows(subject, "transfers"):
        number = visit(row)
        if number is None:
            continue
        found.append(Row(
            "transfer", integer(row["transfer_id"]), number,
            ("transfer", "unit", clean(row["careunit"]) or ""),
            time_of(row["intime"]), words=normalize(row["careunit"])))
    for row in extract.rows(subject, "icustays"):
        number = visit(row)
        if number is None:
            continue
        found.append(Row(
            "icu_stay", integer(row["stay_id"]), number,
            ("icu_stay", "record", row["stay_id"]), time_of(row["intime"]),
            words=normalize(" ".join(filter(None, (
                row["first_careunit"], row["last_careunit"]))))))
    return found


class Case:
    """One case's records, admissions and person, for the references."""

    def __init__(self, case, snapshot, extract, ancestors):
        """Read a case's records from the snapshot and the raw extract."""
        self.case = case
        self.admissions = visits_of(case, snapshot)
        self.count = len(case.visit_ids)
        subject = str(case.subject_id)
        drugs = prescribed(extract, subject, self.admissions)
        admission_of = {number: admission for admission, number
                        in self.admissions.items()}

        def written_of(event, number):
            """Return the drug the prescription of a drug record writes."""
            if not event.start_datetime:
                return None
            start = full_time(event.start_datetime)
            stop = full_time(event.end_datetime) or start
            return drugs.get((admission_of.get(number), start, stop,
                              (event.source_value or "").strip()))
        self.rows = omop_rows(case, snapshot, ancestors, written_of) + (
            raw_rows(extract, subject, self.admissions))
        self.admission_rows = {
            number: extract.admissions.get(admission)
            for admission, number in self.admissions.items()}
        self.person = extract.patients.get(subject)
        self.by_key = {row.key: row for row in self.rows}

    def numbers(self, scope):
        """Return the visit numbers of a scope."""
        if scope.kind == "pre_target_history":
            return tuple(range(1, self.count))
        if scope.kind == "selected_visit":
            return (scope.visit_number,)
        return tuple(range(1, self.count + 1))

    def scoped(self, scope, kind):
        """Return the scope's records of a question kind."""
        numbers = set(self.numbers(scope))
        kinds = RECORD_KINDS[kind]
        return [row for row in self.rows
                if row.visit in numbers and row.kind in kinds]


# Matching and the Expected Answers

def matches(row, asked, descendants):
    """Return True when a record is of the asked item; None for a
    condition record without a standard concept, which is unjudged.
    descendants(concept) gives a condition concept's descendants."""
    if row.kind in ("lab", "icu_procedure") and asked.codes:
        return row.item[2] in asked.codes
    if row.kind == "vital" and asked.groups:
        return row.group in asked.groups
    if row.kind == "condition":
        if not row.concept:
            return None
        return any(row.concept in descendants(concept)
                   for concept in asked.concepts)
    if row.kind == "medication":
        found = ({row.concept} | set(row.ancestors)) & set(asked.concepts) \
            if row.concept else set()
        if found:
            return True
        return not row.concept and any(
            set(words) <= set(row.written.split())
            for words in asked.written if words)
    return any(words_in(words, row.words) for words in asked.words)


def by_item(rows):
    """Return {item: records} of records."""
    found = collections.defaultdict(list)
    for row in rows:
        found[row.item].append(row)
    return found


def pick(rows, selector):
    """Return the record a selector picks among one item's records."""
    if len(rows) == 1:
        return rows[0]
    if selector in ("highest", "lowest"):
        numbers = [row for row in rows if row.number is not None]
        if numbers:
            return (max if selector == "highest" else min)(
                numbers, key=lambda row: (row.number, row.order))
    if selector == "first":
        return min(rows, key=lambda row: row.order)
    return max(rows, key=lambda row: row.order)


def shown(rows, named, kind):
    """Return the records an answer shows of one item's values or flagged
    results: the latest MAX_VALUES of a named item and of a microbiology
    test, the latest one of an item of another whole kind; the others are
    counted, not named."""
    limit = MAX_VALUES if named or kind == "microbiology" else 1
    return sorted(rows, key=lambda row: row.order)[-limit:]


def keyed(rows):
    """Return the metric names of records."""
    return frozenset(row.key for row in rows)


def cultures(rows):
    """Return a microbiology test's records by culture: those taken at one
    time, in time order."""
    found = collections.OrderedDict()
    for row in sorted(rows, key=lambda row: row.order):
        found.setdefault(row.time, []).append(row)
    return list(found.values())


def culture_rows(rows, selector):
    """Return the records an answer shows of a microbiology test's
    values: those of its latest or first culture, as the selector says;
    otherwise every culture that grew an organism and the latest of the
    others, up to MAX_VALUES cultures in all."""
    tests = cultures(rows)
    if selector in ("latest", "first"):
        return tests[-1] if selector == "latest" else tests[0]
    grew = [index for index, culture in enumerate(tests)
            if any(row.value for row in culture)]
    other = [index for index in range(len(tests)) if index not in grew]
    room = max(0, MAX_VALUES - len(grew))
    kept = set(grew) | set(other[len(other) - room:] if room else ())
    return [row for index, culture in enumerate(tests) if index in kept
            for row in culture]


def item_reference(request_id, case, scope, asked, descendants):
    """Return the reference of a question about an item, or a whole kind,
    of the record."""
    rows = case.scoped(scope, asked.kind)
    if asked.named:
        verdicts = [(row, matches(row, asked, descendants)) for row in rows]
        unjudged = [row for row, verdict in verdicts if verdict is None]
        rows = [row for row, verdict in verdicts if verdict]
        if not rows and unjudged:
            return RecordReference(request_id, asked.task, asked.kind,
                                   "unjudged")
    task = asked.task
    if task == "record_abnormal":
        rows = [row for row in rows if row.flagged]
    if task == "record_values":
        rows = [row for row in rows if row.valued]
    if not rows:
        return RecordReference(request_id, task, asked.kind, "not_recorded")
    groups = by_item(rows)
    visits = tuple(sorted({row.visit for row in rows}))
    records = {item: keyed(found) for item, found in groups.items()}
    if task == "record_abnormal":
        records = {item: keyed(shown(found, asked.named, asked.kind))
                   for item, found in groups.items()}
    picked = {}
    if task == "record_timing":
        ordered = sorted(rows, key=lambda row: row.order)
        picked = {"first": ordered[0].key, "last": ordered[-1].key}
    elif task == "record_values":
        selector = asked.selector or "latest"
        for item, found in groups.items():
            if asked.kind == "microbiology":
                picked[item] = keyed(culture_rows(found, selector))
            else:
                picked[item] = (keyed(shown(found, asked.named, asked.kind))
                                if selector == "all"
                                else frozenset((pick(found, selector).key,)))
    return RecordReference(request_id, task, asked.kind, "answer", records,
                           picked, visits=visits)


def list_reference(request_id, case, scope, asked):
    """Return the reference of a list of a kind."""
    rows = case.scoped(scope, asked.kind)
    if not rows:
        return RecordReference(request_id, asked.task, asked.kind,
                               "not_recorded")
    return RecordReference(
        request_id, asked.task, asked.kind, "answer",
        {item: keyed(found) for item, found in by_item(rows).items()},
        visits=tuple(sorted({row.visit for row in rows})))


def visit_reference(request_id, case, scope, asked):
    """Return the reference of a question about the scope's visits: the
    field asked about, for every visit."""
    details = {}
    for number in case.numbers(scope):
        row = case.admission_rows.get(number) or {}
        if asked.field in VISIT_FIELDS:
            details[number] = clean(row.get(VISIT_FIELDS[asked.field]))
        elif asked.field == "length":
            details[number] = (time_of(row.get("admittime")),
                               time_of(row.get("dischtime")))
        else:
            details[number] = number
    return RecordReference(request_id, asked.task, "visit", "answer",
                           details=details,
                           visits=case.numbers(scope))


def person_reference(request_id, case, scope, asked):
    """Return the reference of a question about the person."""
    person = case.person or {}
    if asked.field == "sex":
        details = {0: clean(person.get("gender"))}
    elif asked.field == "death":
        details = {0: (clean(person.get("dod")) or "")[:10] or None}
    elif asked.field == "age":
        details = {number: (integer(person.get("anchor_age")),
                            integer(person.get("anchor_year")),
                            time_of((case.admission_rows.get(number) or {})
                                    .get("admittime"))[:4])
                   for number in case.numbers(scope)}
    else:
        column = PERSON_VISIT_FIELDS.get(asked.field)
        details = {number: clean((case.admission_rows.get(number) or {})
                                 .get(column)) if column else None
                   for number in case.numbers(scope)}
    return RecordReference(request_id, asked.task, "person", "answer",
                           details=details, visits=case.numbers(scope))


def reference(request_id, case, scope, asked, descendants=lambda concept:
              frozenset((concept,))):
    """Return the RecordReference of a question about the record."""
    if asked.task not in TASKS:
        raise RecordReferenceError("task_not_about_the_record")
    if asked.task == "record_values" and asked.kind == "medication":
        return RecordReference(request_id, asked.task, asked.kind, "none")
    if asked.task == "record_list":
        return list_reference(request_id, case, scope, asked)
    if asked.task == "visit_details":
        return visit_reference(request_id, case, scope, asked)
    if asked.task == "person_details":
        return person_reference(request_id, case, scope, asked)
    return item_reference(request_id, case, scope, asked, descendants)


# Comparison with the Templates on a Development Case Set

QUESTION_KINDS = tuple(RECORD_KINDS)
SELECTED_KINDS = ("lab", "vital")
FLAGGED_KINDS = ("lab", "microbiology")
VISIT_ASKED = ("count", "admitted", "discharged", "type", "admitted_from",
               "discharged_to", "length")
PERSON_ASKED = ("sex", "death", "age", "race", "language", "insurance",
                "marital_status")
ITEMS_PER_KIND = 2


def asked_of(question, resolution):
    """Return what a structured question asks, its item given by the
    runtime's resolution of its phrase: the same item for both sides, so
    that the comparison is of the answers."""
    fields = {"task": question.task, "kind": question.kind,
              "selector": question.selector, "field": question.field}
    if resolution is None or resolution.status != "resolved":
        return Asked(**fields)
    keys = tuple(resolution.keys)
    if question.kind == "lab":
        fields["codes"] = frozenset(str(key) for key in keys)
    elif question.kind == "vital":
        fields["groups"] = frozenset(keys)
    elif question.kind == "medication":
        fields["concepts"] = frozenset(concept for _, concept in keys)
        fields["written"] = tuple(resolution.written)
    elif question.kind == "condition":
        fields["concepts"] = frozenset(
            getattr(resolution.condition, "concepts", ()) or ())
    elif question.kind == "procedure":
        fields["words"] = tuple(tuple(key) if isinstance(key, tuple)
                                else tuple(str(key).split()) for key in keys)
    else:
        fields["words"] = tuple(tuple(str(key).split()) for key in keys)
    return Asked(**fields)


def cited(candidate, bundle):
    """Return {statement index: the records it cites} of a candidate, as
    the metrics name records."""
    entries = {entry.ref: entry for entry in bundle.records}
    found = {}
    for index, statement in enumerate(candidate.statements):
        keys = frozenset(("record", entries[ref].kind, entries[ref].native_id)
                         for ref in statement.refs if ref in entries)
        if keys:
            found[index] = keys
    return found


def detail_of(bundle, number, field):
    """Return a visit's value of a field as the record bundle holds it, in
    the form the references give it."""
    visit = next((item for item in bundle.visits
                  if item.visit_number == number), None)
    admission = visit.admission if visit is not None else None
    if field == "count":
        return number
    if admission is None:
        return None
    if field == "length":
        return (admission.admit_time or "", admission.discharge_time or "")
    return {"admitted": admission.admit_time,
            "discharged": admission.discharge_time,
            "type": admission.admission_type,
            "admitted_from": admission.admission_location,
            "discharged_to": admission.discharge_location,
            "race": admission.race, "language": admission.language,
            "insurance": admission.insurance,
            "marital_status": admission.marital_status}.get(field)


def difference(found, candidate, bundle, rows, named=False):
    """Return how a template answer differs from its reference, or
    "right". rows maps each record of the case to the references' Row;
    named says the question names an item, so that a condition record
    without a standard concept it cites is unjudged and left out."""
    if found.outcome in ("none", "unjudged"):
        return found.outcome
    if candidate.outcome != found.outcome:
        return "outcome %s, expected %s" % (candidate.outcome, found.outcome)
    if found.outcome == "not_recorded":
        return "right"
    statements = cited(candidate, bundle)
    unjudged = frozenset(key for keys in statements.values() for key in keys
                         if named and key[1] == "condition"
                         and not rows[key].concept)
    if unjudged and found.task == "record_timing":
        # The first or the last record may be one that cannot be judged.
        return "unjudged"
    every = frozenset(key for keys in statements.values() for key in keys
                      if key not in unjudged)
    if found.task == "record_timing":
        expected = frozenset(found.picked.values())
    elif found.task == "record_values":
        expected = frozenset().union(*found.picked.values())
    else:
        expected = frozenset().union(*found.records.values())
    if every != expected:
        return ("records missing" if every < expected
                else "records added" if every > expected
                else "records differ")
    if found.task == "record_list":
        items = collections.defaultdict(set)
        for key in every:
            items[rows[key].item].add(key)
        if {frozenset(keys) for keys in items.values()} != set(
                found.records.values()):
            return "items grouped otherwise"
    return "right"


def phrases_of(bundle, kind, drugs):
    """Return up to ITEMS_PER_KIND phrases naming items of a kind present
    in a scope: the records' names, an ingredient's name for a medication
    and a group's name for a vital sign."""
    found = []
    for entry in bundle.records:
        if entry.kind not in RECORD_KINDS[kind]:
            continue
        if kind == "medication":
            names = [drugs.drugs[concept].name for concept in entry.ancestors
                     if concept in drugs.drugs
                     and drugs.drugs[concept].kind == "ingredient"]
        elif kind == "vital":
            names = [entry.group.replace("_", " ")] if entry.group else []
        else:
            names = [entry.name] if entry.name else []
        for name in names[:1]:
            phrase = normalize(name)
            if phrase and phrase not in found:
                found.append(phrase)
    return found[:ITEMS_PER_KIND]


def questions_of(bundle, kind, drugs=None):
    """Return the structured questions about a kind in a scope: its list;
    for up to ITEMS_PER_KIND of its items present, every item task; and
    the whole kind's count, values and abnormal results."""
    from ehr_evidence_app.qa import record_interpret
    question = record_interpret.RecordQuestion
    found = [question("task", task="record_list", kind=kind),
             question("task", task="record_count", kind=kind)]
    if kind in SELECTED_KINDS:
        found.append(question("task", task="record_values", kind=kind,
                              selector="all"))
    if kind in FLAGGED_KINDS:
        found.append(question("task", task="record_abnormal", kind=kind,
                              selector="all"))
    if kind == "icu_stay":
        return found
    for phrase in phrases_of(bundle, kind, drugs):
        for task in ("record_presence", "record_timing", "record_count"):
            found.append(question("task", task=task, kind=kind,
                                  phrase=phrase))
        if kind in SELECTED_KINDS:
            for selector in ("latest", "first", "highest", "lowest", "all"):
                found.append(question("task", task="record_values",
                                      kind=kind, phrase=phrase,
                                      selector=selector))
        if kind in FLAGGED_KINDS:
            found.append(question("task", task="record_abnormal", kind=kind,
                                  phrase=phrase, selector="all"))
    return found


def run_compare(case_set):
    """Compare the references with the template answers of a development
    case set; return {(task, kind, verdict): count}."""
    from ehr_evidence_app.api import service
    from ehr_evidence_app.casesets import registry
    from ehr_evidence_app.evidence import contracts, readers, record_bundles
    from ehr_evidence_app.qa import (record_answer, record_interpret,
                                     record_vocabulary, resolve)
    if case_set != readers.PILOT_LABEL and registry.spec(case_set)[
            "purpose"] != "development":
        raise RecordReferenceError("case_set_held_out")
    drugs = record_vocabulary.load()
    conditions = resolve.load()
    context = service.record_context(case_set, drugs, conditions)
    plan = readers.real_plan(None if case_set == readers.PILOT_LABEL
                             else case_set)
    snapshot = readers.read_snapshot(plan)
    cases = readers.read_cases(plan)
    held = []
    tables, _ = record_bundles.read_extract(record_bundles.real_plan(
        case_set), held)
    extract = record_bundles.Extract(tables)
    ancestors = {concept: frozenset(found)
                 for concept, found in drugs.ancestors.items()}

    def descendants(concept):
        """Return a condition concept and its descendants."""
        return frozenset(conditions.descendants_of(concept))

    counts = collections.Counter()
    number = 0
    for case in cases.cases:
        if not case.admitted:
            continue
        mine = Case(case, snapshot, extract, ancestors)
        scopes = ([contracts.ScopeRef(kind="pre_target_history")]
                  if mine.count > 1 else [])
        scopes += [contracts.ScopeRef(kind="selected_visit",
                                      visit_number=visit)
                   for visit in range(1, mine.count + 1)]
        scopes.append(contracts.ScopeRef(kind="case_visits"))
        for scope in scopes:
            found = context.records.bundle(case.case_number, scope)
            if found is None:
                counts[("scope", "-", "no record bundle")] += 1
                continue
            bundle, digest = found
            bundle_ref = contracts.BundleRef(bundle_id=bundle.bundle_id,
                                             sha256=digest)
            questions = [question for kind in QUESTION_KINDS
                         for question in questions_of(bundle, kind, drugs)]
            questions += [record_interpret.RecordQuestion(
                "task", task="visit_details", kind="visit", field=field)
                for field in VISIT_ASKED]
            questions += [record_interpret.RecordQuestion(
                "task", task="person_details", kind="person", field=field)
                for field in PERSON_ASKED]
            for question in questions:
                number += 1
                request = contracts.Request(
                    schema_version="1.0", request_id="k-%06d" % number,
                    task=question.task,
                    case=contracts.CaseRef(case_set=case_set,
                                           case_number=case.case_number),
                    scope=scope, question="Invented.")
                resolution = record_answer.resolution_of(context, question)
                if question.phrase and (resolution is None
                                        or resolution.status != "resolved"):
                    counts[(question.task, question.kind,
                            "phrase not resolved")] += 1
                    continue
                candidate = record_answer.answer_candidate(
                    context, request, bundle, bundle_ref, question,
                    resolution)
                asked = asked_of(question, resolution)
                expected = reference(request.request_id, mine, scope, asked,
                                     descendants)
                if expected.task in ("visit_details", "person_details"):
                    verdict = details_verdict(expected, candidate, bundle,
                                              question.field)
                else:
                    verdict = difference(expected, candidate, bundle,
                                         mine.by_key, asked.named)
                counts[(question.task, question.kind, verdict)] += 1
    return counts


def details_verdict(found, candidate, bundle, field):
    """Return how a template answer about the visits or the person
    differs from its reference: the record bundle's values against the
    raw extract's, for every visit of the scope."""
    if candidate.outcome != "answer":
        return "outcome %s, expected answer" % candidate.outcome
    for number, expected in found.details.items():
        if found.task == "person_details" and field in ("sex", "death"):
            held = (bundle.person.sex if field == "sex"
                    else bundle.person.date_of_death)
        elif field == "age":
            age, year, _ = expected
            held, expected = ((bundle.person.anchor_age,
                               bundle.person.anchor_year), (age, year))
        else:
            held = detail_of(bundle, number, field)
        if held != expected:
            return "%s differs" % field
    return "right"


# Default Mode: An Invented Case

# The invented case's person, subject and admissions.
SYNTHETIC_PERSON = 7
SYNTHETIC_SUBJECT = 100
SYNTHETIC_FIRST, SYNTHETIC_FINAL = "11", "12"


class InventedExtract:
    """An invented raw extract of one person."""

    def __init__(self, tables, labs=None, items=None, admissions=None,
                 patients=None):
        """Hold the rows by table."""
        self.tables = tables
        self.labs = labs or {}
        self.items = items or {}
        self.admissions = admissions or {}
        self.patients = patients or {}

    def rows(self, subject, table):
        """Return the rows of a table."""
        return self.tables.get(table, [])


def synthetic_case():
    """Return an invented Case: two visits (admissions 11 and 12), three
    potassium results, a heart rate, a transfer and an ICU stay."""
    from ehr_evidence_app.evidence import readers
    case = readers.Case(
        case_number=1, patient_index=1, graph_position=1,
        person_id=SYNTHETIC_PERSON, subject_id=SYNTHETIC_SUBJECT,
        visit_ids=(501, 502), admitted=True,
        explanation_eligible=True, restrictions=(),
        upstream_partition="test", output_record_sha256="0" * 64,
        batch_sensitive=False, single_graph_reproduces_published=True,
        label_correspondence="established", drivers={})
    visits = {501: readers.SourceVisit(
        501, SYNTHETIC_PERSON, 9201, "2100-01-01", "2100-01-03",
        "%d|%s" % (SYNTHETIC_SUBJECT, SYNTHETIC_FIRST)),
        502: readers.SourceVisit(
        502, SYNTHETIC_PERSON, 9201, "2100-02-01", "2100-02-04",
        "%d|%s" % (SYNTHETIC_SUBJECT, SYNTHETIC_FINAL))}
    snapshot = readers.Snapshot("synthetic", "0" * 64, "0" * 64, True,
                                visits, {"condition": (), "procedure": (),
                                         "drug": ()}, {})

    def lab(number, admission, time, value, flag=""):
        return {"labevent_id": str(number), "hadm_id": admission,
                "itemid": "50971", "charttime": time,
                "value": str(value), "valuenum": str(value),
                "flag": flag}
    extract = InventedExtract(
        {"labevents": [lab(1, SYNTHETIC_FIRST, "2100-01-01 08:00:00", 3.1,
                           "abnormal"),
                       lab(2, SYNTHETIC_FINAL, "2100-02-01 08:00:00", 4.5),
                       lab(3, SYNTHETIC_FINAL, "2100-02-02 08:00:00", 5.6,
                           "abnormal")],
         "chartevents": [{"row_number": "9", "hadm_id": SYNTHETIC_FINAL,
                          "itemid": "220045", "charttime":
                          "2100-02-01 09:00:00", "value": "88",
                          "valuenum": "88"}],
         "transfers": [{"transfer_id": "21",
                        "hadm_id": SYNTHETIC_FINAL,
                        "careunit": "Medical Intensive Care Unit (MICU)",
                        "intime": "2100-02-01 10:00:00"}],
         "icustays": [{"stay_id": "31", "hadm_id": SYNTHETIC_FINAL,
                       "first_careunit": "Medical Intensive Care Unit "
                                         "(MICU)", "last_careunit": "",
                       "intime": "2100-02-01 10:00:00"}]},
        labs={"50971": {"label": "Potassium", "fluid": "Blood",
                        "category": "Chemistry"}},
        items={"220045": {"label": "Heart Rate"}},
        admissions={SYNTHETIC_FIRST: {"admittime": "2100-01-01 07:00:00",
                           "dischtime": "2100-01-03 12:00:00",
                           "admission_type": "URGENT"},
                    SYNTHETIC_FINAL: {"admittime": "2100-02-01 07:00:00",
                           "dischtime": "2100-02-04 12:00:00",
                           "admission_type": "EW EMER."}},
        patients={str(SYNTHETIC_SUBJECT): {
            "gender": "F", "anchor_age": "60",
                          "anchor_year": "2100", "dod": ""}})
    return Case(case, snapshot, extract, {})


def run_synthetic():
    """Return the problems of the references on the invented case."""
    from ehr_evidence_app.evidence import contracts
    problems = []
    case = synthetic_case()
    every = contracts.ScopeRef(kind="case_visits")
    history = contracts.ScopeRef(kind="pre_target_history")
    potassium = frozenset(("50971",))
    found = reference("r1", case, every,
                      Asked("record_values", "lab", codes=potassium,
                            selector="highest"))
    if list(found.picked.values()) != [frozenset((("record", "lab", 3),))]:
        problems.append("the highest value is not picked")
    found = reference("r2", case, every,
                      Asked("record_timing", "lab", codes=potassium))
    if found.picked != {"first": ("record", "lab", 1),
                        "last": ("record", "lab", 3)}:
        problems.append("the first and last results are not found")
    found = reference("r3", case, history,
                      Asked("record_abnormal", "lab"))
    if found.records != {("lab", "item", "50971"): frozenset((
            ("record", "lab", 1),))}:
        problems.append("the flagged result of the history is not found")
    found = reference("r4", case, history,
                      Asked("record_presence", "vital",
                            groups=frozenset(("heart_rate",))))
    if found.outcome != "not_recorded":
        problems.append("a heart rate of the final admission is in the "
                        "history")
    found = reference("r5", case, every,
                      Asked("record_presence", "transfer", words=(
                          ("micu",),)))
    if found.outcome != "answer":
        problems.append("a care unit is not matched by its words")
    found = reference("r6", case, every, Asked("visit_details",
                                               field="type"))
    if found.details != {1: "URGENT", 2: "EW EMER."}:
        problems.append("a visit's details are not read")
    found = reference("r7", case, every, Asked("record_values", "lab",
                                                selector="all"))
    if list(found.picked.values()) != [frozenset((("record", "lab", 3),))]:
        problems.append("all values of a whole kind are not the latest of "
                        "each item")
    return problems


def report(counts):
    """Print the comparison's counts by task, kind and verdict only."""
    for (task, kind, verdict), count in sorted(counts.items()):
        print("%-16s %-13s %-38s %d" % (task, kind, verdict, count))
    differing = sum(count for (_, _, verdict), count in counts.items()
                    if verdict not in ("right", "none", "unjudged",
                                       "phrase not resolved"))
    print("template answers differing from their references: %d"
          % differing)


def main(argv=None):
    """Run the default mode, or the operator's comparison."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="compare the references with the template "
                             "answers of a development case set; prints "
                             "counts only")
    parser.add_argument("--case-set", default="dev-30-v1")
    args = parser.parse_args(argv)
    if args.operator_real:
        try:
            report(run_compare(args.case_set))
        except RecordReferenceError as error:
            print("FAILED: %s" % error)
            return 1
        print("RESULT: PASS")
        return 0
    problems = run_synthetic()
    for problem in problems:
        print("FAILED: %s" % problem)
    if problems:
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
