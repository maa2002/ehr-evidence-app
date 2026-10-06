"""Answers about the whole record (rules record-answer-v4).

A question read as a question about the record (qa.record_interpret) is
answered from the case's record bundle for the bound scope
(evidence.record), by templates: every fact is a slot that names a
record, a visit or the person, and every refusal and clarifying
question is an approved notice. The candidate leaves through the release
gate, which checks its structure and references, the support of each
statement by what it cites (validation.record_support) and its wording.

The language model does not write these answers: it writes the answers
about conditions and the explanations (qa.answer, validation.explanation)
until its answers about the record have been measured.

* A list names every item of the kind within the scope once (a test, a
  medication, a vital sign, a unit), with its number of records and its
  visits; each ICU stay and transfer is listed with its times.
* A presence check cites every record that matches the phrase, grouped by
  item and by how it matched; with none, it is not recorded within the
  scope, which is never a statement of clinical absence, and a "not
  recorded" about a named condition, medication or procedure states how
  many records of its kind in the scope have no standard concept and how
  they were checked.
* A time question gives the first and the last matching record.
* A count gives the number of matching records and their visits, or the
  number of items and records of a kind.
* A values question gives, for each matching item, its latest, first,
  highest or lowest value (a single record is the only one), or its
  values in time order: the latest MAX_VALUES of a named item and of
  each microbiology test, the latest of each item of another whole kind,
  the others counted; each item is named by its earliest record.
* A question about abnormal results cites every result the laboratory
  flagged abnormal (for microbiology, every result reported resistant).
* Visits and the person are answered from the visits of the scope and
  the person's own record.
* A question about what medications were given for a condition gets a
  clarification: the record does not say, so it asks about a medication
  or a drug class instead (version 3).
* A medication without a named concept is named by the drug its
  prescription names, as written, and nothing after it (version 4).
* "Not recorded" begins with the item asked about; when that is the
  question's own words in lower case, its first letter is a capital
  ("Chest x ray is not recorded"), where a name such as "pH" keeps its
  own (version 5; validation.candidate.sentence).
"""

from __future__ import annotations

import collections
import dataclasses
import hashlib
import json
import os
import threading

from ehr_evidence_app.evidence import contracts, record
from ehr_evidence_app.qa import record_interpret as reading
from ehr_evidence_app.qa import record_resolve as resolving
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import record_text as words

RULES_VERSION = "record-answer-v5"
# The values listed of one item: the latest MAX_VALUES when the question
# names the item or asks what grew in microbiology, the latest one of each
# item when it asks about another whole kind; the others are counted, and
# kept in the evidence.
MAX_VALUES = 30
# Record bundles kept parsed, most recently read last.
KEPT_BUNDLES = 12
MAX_SUGGESTIONS = 8
ABNORMAL = {"lab": "abnormal", "microbiology": "resistant"}
WORDINGS = {
    "record_list_summary": ("{items} is recorded within {scope} "
                            "({records}):",
                            "{items} are recorded within {scope} "
                            "({records}):"),
    "record_list_item": "{item}: {count} in {visits}",
    "record_stay_item": "{item}: {value}",
    "record_list_empty": "No {kind} is recorded within {scope}.",
    "record_presence_summary": "{item} is recorded within {scope}: "
                               "{matches}.",
    "record_presence_item": "{item} ({count} in {visits}; matched by "
                            "{how})",
    "record_absent": "{item} is not recorded within {scope}.",
    "record_unmapped": {
        "condition": ("{unmapped} {kind} record in this scope has no "
                      "standard concept; its ICD code and description were "
                      "checked.",
                      "{unmapped} {kind} records in this scope have no "
                      "standard concept; their ICD codes and descriptions "
                      "were checked."),
        "medication": ("{unmapped} {kind} record in this scope has no "
                       "standard concept; the drug name written in it was "
                       "checked.",
                       "{unmapped} {kind} records in this scope have no "
                       "standard concept; the drug names written in them "
                       "were checked."),
        "procedure": ("{unmapped} {kind} record in this scope has no "
                      "standard concept; the words of its name were "
                      "checked.",
                      "{unmapped} {kind} records in this scope have no "
                      "standard concept; the words of their names were "
                      "checked.")},
    "record_time": "{selector} record: {item}, {when}, in {visit}",
    "record_count": "{item}: {count} within {scope}, in {visits}.",
    "record_count_kind": ("{items} is recorded within {scope} "
                          "({records}).",
                          "{items} are recorded within {scope} "
                          "({records})."),
    "record_value": "{selector} {item}: {value}, {when}, in {visit}.",
    "record_value_group": "{item} ({count}):",
    "record_value_item": "{value}, {when}, in {visit}",
    "record_abnormal_summary": {
        "lab": "Results of {item} within {scope} flagged abnormal by the "
               "laboratory: {matches}.",
        "microbiology": "Results of {item} within {scope} reported "
                        "resistant: {matches}."},
    "record_abnormal_absent": {
        "lab": "No result of {item} within {scope} is flagged abnormal by "
               "the laboratory.",
        "microbiology": "No result of {item} within {scope} is reported "
                        "resistant."},
    "visit_summary": "{total} within {scope}:",
    "visit_fact": "{visit}: {field}",
    "person_fact": "The patient's record: {field}.",
    "person_age": "At {visit}: {field}",
}


class RecordAnswerError(Exception):
    """A fail-closed failure carrying a finding code only."""


# --------------------------------------------------------------------------
# The record bundle set
# --------------------------------------------------------------------------

class RecordSet:
    """A pinned record bundle set whose bundles are verified when read."""

    def __init__(self, label, directory, manifest, reader=None,
                 bundles=None):
        """Index the manifest's record bundles by case and scope; bundles
        holds bundles already in memory (the invented case set)."""
        self.label = label
        self.directory = directory
        self.reader = reader or self.read_file
        self.held = bundles or {}
        self.kept = collections.OrderedDict()
        self.lock = threading.Lock()
        self.entries = {}
        for entry in manifest.get("files") or []:
            scope = contracts.ScopeRef.model_validate(entry["scope"])
            self.entries[(entry["case_number"], scope)] = entry

    def read_file(self, entry):
        """Read one bundle file and verify it against its manifest
        entry."""
        path = os.path.join(self.directory, *entry["path"].split("/"))
        try:
            with open(path, "rb") as handle:
                data = handle.read(entry["size_bytes"] + 1)
        except OSError:
            raise RecordAnswerError("record_bundle_unreadable") from None
        return data

    def bundle(self, case_number, scope):
        """Return (bundle, SHA-256) of a case's record bundle for a scope,
        or None when the case has none."""
        entry = self.entries.get((case_number, scope))
        if entry is None:
            return None
        if entry["bundle_id"] in self.held:
            return self.held[entry["bundle_id"]], entry["sha256"]
        with self.lock:
            if entry["bundle_id"] in self.kept:
                self.kept.move_to_end(entry["bundle_id"])
                return self.kept[entry["bundle_id"]], entry["sha256"]
        data = self.reader(entry)
        if (len(data) != entry["size_bytes"]
                or hashlib.sha256(data).hexdigest() != entry["sha256"]):
            raise RecordAnswerError("record_bundle_hash_mismatch")
        bundle = record.RecordBundle.model_validate_json(data)
        if (bundle.bundle_id != entry["bundle_id"]
                or bundle.case.case_set != self.label
                or bundle.case.case_number != case_number
                or bundle.scope != scope):
            # A file that is not the bundle its manifest entry names.
            raise RecordAnswerError("record_bundle_unexpected")
        with self.lock:
            self.kept[entry["bundle_id"]] = bundle
            while len(self.kept) > KEPT_BUNDLES:
                self.kept.popitem(last=False)
        return bundle, entry["sha256"]


def load_record_set(plan):
    """Load a record bundle set through its pinned manifest."""
    from ehr_evidence_app.gnn import mapping
    entry = plan["manifest"]
    try:
        data, _ = mapping.read_verified(
            entry["path"], "record_manifest",
            "input_hash_mismatch:record_manifest", sha256=entry["sha256"],
            size=entry["size_bytes"])
        manifest = json.loads(data.decode("utf-8"))
    except mapping.MappingError as error:
        raise RecordAnswerError(str(error)) from None
    except (UnicodeDecodeError, ValueError):
        raise RecordAnswerError("record_manifest_unexpected") from None
    if manifest.get("case_set") != plan["label"]:
        raise RecordAnswerError("record_manifest_unexpected")
    return RecordSet(plan["label"], plan["directory"], manifest)


@dataclasses.dataclass(frozen=True)
class RecordContext:
    """What questions about the record are answered from: a case set's
    record bundles and the terms phrases resolve against."""

    records: RecordSet
    terms: resolving.Terms


# --------------------------------------------------------------------------
# Statements
# --------------------------------------------------------------------------

def wording(name, number=None, record_kind=None):
    """Return a statement kind's template wording: by the kind of record
    it is about, then by its number, when it has several."""
    entry = WORDINGS[name]
    if isinstance(entry, dict):
        entry = entry[record_kind]
    if isinstance(entry, str):
        return entry
    return entry[0] if number == 1 else entry[1]


def statement(name, number=None, refs=(), record_kind=None, **slots):
    """Return a statement of a kind (name) with the template wording."""
    return candidates.Statement(
        kind=name, wording=wording(name, number, record_kind),
        slots=candidates.Slots(**slots), refs=tuple(refs))


def visit_numbers(bundle, entries):
    """Return the sorted visit numbers of some records."""
    numbers = {visit.ref: visit.visit_number for visit in bundle.visits}
    return tuple(sorted({numbers[entry.visit_ref] for entry in entries}))


def grouped(entries):
    """Return records grouped by item, the groups ordered by their name."""
    groups = collections.OrderedDict()
    for entry in entries:
        groups.setdefault(record.item_key(entry), []).append(entry)
    return sorted(groups.values(), key=lambda items: (
        words.record_name(items[0]).casefold(), record.item_key(items[0])))


def of_kind(bundle, kind):
    """Return the scope's records of a question kind."""
    kinds = resolving.RECORD_KINDS[kind]
    return [entry for entry in bundle.records if entry.kind in kinds]


def disclosure(bundle, kind):
    """Return the reason codes an answer about a kind discloses: records
    without a standard concept or a name within the scope."""
    unnamed = sum(bundle.coverage.of(item).unnamed_in_scope
                  for item in resolving.RECORD_KINDS[kind])
    return ("unmapped_records_in_scope",) if unnamed else ()


def item_terms(question, resolution):
    """Return the ItemName of what a question asks about: its resolved
    names, or the kind's own name when it names no item."""
    if resolution is None:
        return candidates.ItemName(terms=(
            words.kind_word(question.kind, plural=True),))
    return candidates.ItemName(terms=tuple(
        name[:255] for name in resolution.names[:50]))


def timed(entry):
    """Return the order of a record in time: its start, then its row."""
    return (entry.start or "", entry.native_id)


def numeric(entry):
    """Return a record's value as a number, or None."""
    measure = entry.measure
    return measure.number if measure is not None else None


def chosen(entries, selector):
    """Return the record a selector picks among one item's records, and
    the selector it is stated with: "only" for a single record; the
    latest instead of the highest or lowest when no value is a number."""
    if len(entries) == 1:
        return entries[0], "only"
    if selector in ("highest", "lowest"):
        numbers = [entry for entry in entries if numeric(entry) is not None]
        if numbers:
            pick = (max if selector == "highest" else min)(
                numbers, key=lambda entry: (numeric(entry), timed(entry)))
            return pick, selector
        selector = "latest"
    if selector == "first":
        return min(entries, key=timed), "first"
    return max(entries, key=timed), "latest"


def has_value(entry):
    """Return True when a record holds a value to show."""
    if entry.kind in ("lab", "vital", "icu_procedure"):
        return entry.measure is not None
    return True


def shown_of(resolution, kind):
    """Return how many values of each item a list shows: MAX_VALUES for a
    named item and for microbiology, whose results of one test are its
    organisms and their antibiotics; the latest one of each item of
    another whole kind."""
    return MAX_VALUES if resolution is not None or (
        kind == "microbiology") else 1


def value_lines(items, visits, limit):
    """Return (the statements of each item's latest values in time order,
    the count of the others). Each item is named by its earliest record,
    whatever the order of the visits."""
    statements, earlier = [], 0
    for group in grouped(items):
        ordered = sorted(group, key=timed)
        shown = ordered[-limit:]
        earlier += len(ordered) - len(shown)
        statements.append(statement(
            "record_value_group",
            item=candidates.ItemName(record=ordered[0].ref),
            count=len(group)))
        statements += [statement("record_value_item", refs=(entry.ref,),
                                 value=entry.ref, when=entry.ref,
                                 visit=visits[entry.visit_ref])
                       for entry in shown]
    return statements, earlier


# --------------------------------------------------------------------------
# Candidates
# --------------------------------------------------------------------------

def candidate(request, bundle_ref, outcome, statements, reasons=()):
    """Return a candidate for a record request."""
    return candidates.Candidate(
        schema_version=candidates.CANDIDATE_VERSION,
        request_id=request.request_id, task=request.task, case=request.case,
        scope=request.scope, outcome=outcome, reason_codes=tuple(reasons),
        bundle=bundle_ref, statements=tuple(statements))


def unmapped_of(bundle, question, resolution):
    """Return how many records of the question's kind within the scope
    have no standard concept, when a "not recorded" about a named item
    states how they were checked; 0 otherwise."""
    if resolution is None or question.kind not in candidates.UNMAPPED_KINDS:
        return 0
    return sum(1 for entry in of_kind(bundle, question.kind)
               if entry.concept_id == 0)


def absent(request, bundle, bundle_ref, question, resolution, notices=()):
    """Return the "not recorded" candidate of a kind or an item; for an
    item, with the records without a standard concept that were
    checked."""
    if resolution is None:
        first = statement("record_list_empty", refs=("C1",),
                          kind=question.kind, scope=request.scope)
    else:
        first = statement("record_absent", refs=("C1",),
                          item=item_terms(question, resolution),
                          scope=request.scope)
    items = [first]
    unmapped = unmapped_of(bundle, question, resolution)
    if unmapped:
        items.append(statement("record_unmapped", unmapped, refs=("C1",),
                               record_kind=question.kind, unmapped=unmapped,
                               kind=question.kind))
    items += [candidates.notice("absence")] + [
        candidates.notice(notice) for notice in notices]
    return candidate(request, bundle_ref, "not_recorded", items,
                     disclosure(bundle, question.kind))


def kind_notices(question):
    """Return the notices a kind's answers carry."""
    if question.kind == "vital":
        return ("vitals_icu_only",)
    return ()


def list_candidate(request, bundle, bundle_ref, question):
    """Return the list of a kind within the scope."""
    entries = of_kind(bundle, question.kind)
    if not entries:
        return absent(request, bundle, bundle_ref, question, None,
                      kind_notices(question))
    if question.kind in ("icu_stay", "transfer"):
        ordered = sorted(entries, key=timed)
        statements = [statement(
            "record_list_summary", len(ordered),
            items=candidates.ItemCount(kind=question.kind,
                                       count=len(ordered)),
            scope=request.scope, records=len(ordered))]
        statements += [statement("record_stay_item", refs=(entry.ref,),
                                 item=candidates.ItemName(record=entry.ref),
                                 value=entry.ref) for entry in ordered]
    else:
        groups = grouped(entries)
        statements = [statement(
            "record_list_summary", len(groups),
            items=candidates.ItemCount(kind=question.kind,
                                       count=len(groups)),
            scope=request.scope, records=len(entries))]
        statements += [statement(
            "record_list_item", refs=[entry.ref for entry in items],
            item=candidates.ItemName(record=items[0].ref), count=len(items),
            visits=visit_numbers(bundle, items)) for items in groups]
    statements.append(candidates.notice("record_list_complete"))
    statements += [candidates.notice(notice)
                   for notice in kind_notices(question)]
    return candidate(request, bundle_ref, "answer", statements,
                     disclosure(bundle, question.kind))


def matching(context, bundle, question, resolution):
    """Return the (record, match kind) pairs of the scope that match."""
    if resolution is None:
        return [(entry, None) for entry in of_kind(bundle, question.kind)]
    found = []
    for entry in of_kind(bundle, question.kind):
        kind = context.terms.match(resolution, entry)
        if kind:
            found.append((entry, kind))
    return found


def presence_candidate(request, bundle, bundle_ref, question, resolution,
                       matches):
    """Return the presence answer of an item."""
    if not matches:
        return absent(request, bundle, bundle_ref, question, resolution,
                      kind_notices(question))
    groups = collections.OrderedDict()
    for entry, kind in matches:
        groups.setdefault((record.item_key(entry), kind), []).append(entry)
    statements = [statement("record_presence_summary",
                            item=item_terms(question, resolution),
                            scope=request.scope, matches=len(matches))]
    for (_, kind), entries in sorted(groups.items(), key=lambda item: (
            words.record_name(item[1][0]).casefold(), item[0])):
        statements.append(statement(
            "record_presence_item", refs=[entry.ref for entry in entries],
            item=candidates.ItemName(record=entries[0].ref),
            count=len(entries), visits=visit_numbers(bundle, entries),
            how=kind))
    return candidate(request, bundle_ref, "answer", statements,
                     disclosure(bundle, question.kind))


def timing_candidate(request, bundle, bundle_ref, question, resolution,
                     matches):
    """Return the first and the last matching record."""
    entries = [entry for entry, _ in matches]
    if not entries:
        return absent(request, bundle, bundle_ref, question, resolution,
                      kind_notices(question))
    ordered = sorted(entries, key=timed)
    visits = {visit.ref: visit for visit in bundle.visits}
    statements = [statement("record_presence_summary",
                            item=item_terms(question, resolution),
                            scope=request.scope, matches=len(entries))]
    picks = ([("only", ordered[0])] if len(ordered) == 1
             else [("first", ordered[0]), ("latest", ordered[-1])])
    for selector, entry in picks:
        statements.append(statement(
            "record_time", refs=(entry.ref,), selector=selector,
            item=candidates.ItemName(record=entry.ref), when=entry.ref,
            visit=visits[entry.visit_ref].ref))
    statements.append(candidates.notice("dates_shifted"))
    return candidate(request, bundle_ref, "answer", statements,
                     disclosure(bundle, question.kind))


def count_candidate(request, bundle, bundle_ref, question, resolution,
                    matches):
    """Return the number of matching records, or of a kind's items."""
    entries = [entry for entry, _ in matches]
    if not entries:
        return absent(request, bundle, bundle_ref, question, resolution,
                      kind_notices(question))
    if resolution is None:
        groups = grouped(entries)
        items = [statement(
            "record_count_kind", len(groups), refs=[entry.ref
                                                    for entry in entries],
            items=candidates.ItemCount(kind=question.kind,
                                       count=len(groups)),
            scope=request.scope, records=len(entries))]
    else:
        items = [statement("record_count", refs=[entry.ref
                                                 for entry in entries],
                           item=item_terms(question, resolution),
                           count=len(entries), scope=request.scope,
                           visits=visit_numbers(bundle, entries))]
    return candidate(request, bundle_ref, "answer", items,
                     disclosure(bundle, question.kind))


def values_candidate(request, bundle, bundle_ref, question, resolution,
                     matches):
    """Return the values of each matching item: the one a selector
    picks, or all of them in time order."""
    entries = [entry for entry, _ in matches if has_value(entry)]
    if not entries:
        return absent(request, bundle, bundle_ref, question, resolution,
                      kind_notices(question))
    visits = {visit.ref: visit.ref for visit in bundle.visits}
    statements, earlier = [], 0
    if question.selector == "all":
        statements, earlier = value_lines(entries, visits,
                                          shown_of(resolution,
                                                   question.kind))
    else:
        for items in grouped(entries):
            entry, selector = chosen(items, question.selector)
            statements.append(statement(
                "record_value", refs=(entry.ref,), selector=selector,
                item=candidates.ItemName(record=entry.ref), value=entry.ref,
                when=entry.ref, visit=visits[entry.visit_ref]))
    if earlier:
        statements.append(candidates.notice("values_omitted",
                                            earlier=earlier))
    if question.kind == "vital" and question.selector in ("highest",
                                                          "lowest") and (
            question.category == "vital_flag"):
        statements.append(candidates.notice("vital_no_flag"))
    statements += [candidates.notice(notice)
                   for notice in kind_notices(question)]
    return candidate(request, bundle_ref, "answer", statements,
                     disclosure(bundle, question.kind))


def flagged(entry):
    """Return True when a result is flagged: abnormal by the laboratory,
    or resistant in microbiology."""
    if entry.kind == "lab":
        return entry.measure is not None and entry.measure.flag == (
            "abnormal")
    if entry.kind == "microbiology":
        return entry.microbe.interpretation == "R"
    return False


def abnormal_candidate(request, bundle, bundle_ref, question, resolution,
                       matches):
    """Return every flagged result of the matching items."""
    entries = [entry for entry, _ in matches if flagged(entry)]
    notice = "not_flagged" if question.kind == "lab" else "absence"
    if not entries:
        items = [statement("record_abnormal_absent", refs=("C1",),
                           record_kind=question.kind,
                           item=item_terms(question, resolution),
                           scope=request.scope),
                 candidates.notice(notice)]
        if notice != "absence":
            items.append(candidates.notice("absence"))
        return candidate(request, bundle_ref, "not_recorded", items,
                         disclosure(bundle, question.kind))
    visits = {visit.ref: visit.ref for visit in bundle.visits}
    statements = [statement("record_abnormal_summary",
                            record_kind=question.kind,
                            item=item_terms(question, resolution),
                            scope=request.scope, matches=len(entries))]
    lines, earlier = value_lines(entries, visits,
                                 shown_of(resolution, question.kind))
    statements += lines
    if earlier:
        statements.append(candidates.notice("values_omitted",
                                            earlier=earlier))
    if question.kind == "lab":
        statements.append(candidates.notice("not_flagged"))
    return candidate(request, bundle_ref, "answer", statements,
                     disclosure(bundle, question.kind))


def visit_candidate(request, bundle, bundle_ref, question):
    """Return a field of every visit of the scope."""
    field = {"list": "summary", "count": "summary"}.get(question.field,
                                                         question.field)
    statements = [statement("visit_summary",
                            total=len(bundle.visits),
                            scope=request.scope)]
    statements += [statement("visit_fact", refs=(visit.ref,),
                             visit=visit.ref, field=field)
                   for visit in bundle.visits]
    if field in ("summary", "admitted", "discharged"):
        statements.append(candidates.notice("dates_shifted"))
    return candidate(request, bundle_ref, "answer", statements)


def person_candidate(request, bundle, bundle_ref, question):
    """Return a field of the person: the person's own record, or the one
    recorded with each visit of the scope."""
    field = question.field
    statements = []
    if field in ("sex", "death"):
        statements.append(statement("person_fact", refs=("D1",),
                                    field=field))
    if field == "death":
        statements += [statement("visit_fact", refs=(visit.ref,),
                                 visit=visit.ref, field="hospital_death")
                       for visit in bundle.visits]
        statements.append(candidates.notice("death_meaning"))
    elif field == "age":
        statements += [statement("person_age", refs=("D1", visit.ref),
                                 visit=visit.ref, field="age")
                       for visit in bundle.visits]
        statements.append(candidates.notice("age_meaning"))
    elif field != "sex":
        statements += [statement("visit_fact", refs=(visit.ref,),
                                 visit=visit.ref, field=field)
                       for visit in bundle.visits]
    return candidate(request, bundle_ref, "answer", statements)


def answer_candidate(context, request, bundle, bundle_ref, question,
                     resolution):
    """Return the candidate of a record request."""
    task = request.task
    if task == "record_list":
        return list_candidate(request, bundle, bundle_ref, question)
    if task == "visit_details":
        return visit_candidate(request, bundle, bundle_ref, question)
    if task == "person_details":
        return person_candidate(request, bundle, bundle_ref, question)
    matches = matching(context, bundle, question, resolution)
    builders = {"record_presence": presence_candidate,
                "record_timing": timing_candidate,
                "record_count": count_candidate,
                "record_values": values_candidate,
                "record_abnormal": abnormal_candidate}
    return builders[task](request, bundle, bundle_ref, question, resolution,
                          matches)


# --------------------------------------------------------------------------
# Clarifications
# --------------------------------------------------------------------------

def clarification(request_id, case, scope, question, resolution=None):
    """Return the clarifying candidate of a record question that cannot
    be answered as asked."""
    from ehr_evidence_app.qa import templates
    term = templates.term_slot(question.phrase or "")
    if question.category == "indication":
        return templates.notice(request_id, case, scope, "clarification",
                                "task_ambiguous", "indication_unrecorded",
                                term=term or "the condition")
    if question.category in ("two_items", "flag_unrecorded"):
        return templates.notice(request_id, case, scope, "clarification",
                                "task_ambiguous", question.category)
    if question.category == "several_kinds":
        return templates.notice(request_id, case, scope, "clarification",
                                "concept_ambiguous", "several_kinds",
                                term=term or "the question",
                                options=tuple(question.names))
    if resolution is not None and resolution.status == "too_broad":
        return templates.notice(request_id, case, scope, "clarification",
                                "concept_too_broad", "item_too_broad",
                                term=term)
    suggestions = tuple(name[:255] for name in (
        resolution.suggestions if resolution is not None
        else question.suggestions))
    if term and suggestions:
        return templates.notice(request_id, case, scope, "clarification",
                                "concept_unknown", "item_suggested",
                                term=term,
                                labels=suggestions[:MAX_SUGGESTIONS])
    if term:
        return templates.notice(request_id, case, scope, "clarification",
                                "concept_unknown", "item_unknown",
                                term=term)
    if question.category == "qualified":
        return templates.notice(request_id, case, scope, "clarification",
                                "task_ambiguous", "qualified")
    if question.category == "scope_unrecognised":
        return templates.notice(request_id, case, scope, "clarification",
                                "scope_ambiguous", "scope_unrecognised",
                                scope=scope)
    if question.category == "missing_item":
        return templates.notice(request_id, case, scope, "clarification",
                                "concept_unknown", "item_missing")
    return templates.notice(request_id, case, scope, "clarification",
                            "task_ambiguous", "unexplained")


def resolution_of(context, question):
    """Return the resolution of a question's phrase within its kind, or
    None for a question that names no item."""
    if not question.phrase or question.kind in ("visit", "person",
                                                "icu_stay"):
        return None
    return context.terms.resolve(question.kind, question.phrase)


__all__ = ["RecordContext", "RecordSet", "answer_candidate",
           "clarification", "load_record_set", "resolution_of", "reading"]
