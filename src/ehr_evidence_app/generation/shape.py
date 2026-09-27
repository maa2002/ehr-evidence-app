"""The shape of a model-written candidate, and its schema (work item 41).

In Phase 7 the language model writes the complete candidate of every
response from evidence (ADR 0012): list and presence answers, "not
recorded" included, and explanations. Clarifications, refusals and
abstentions are settled before any evidence is read and stay approved
notices. The model writes one compact JSON document in the shape of its
answer (candidate-shape-v3, ADR 0013), each row listing records before
what is counted from them, and each summary after its items:

* a list: its outcome, one wording for every item, and each condition as
  [the record that names it, its records, their visits, their count];
  the summary; the line on records without a standard concept, the
  notices and the reason codes;
* a presence check: the same, each item as [the record that names it,
  its match kind, its records, their visits, their count], or the "not
  recorded" line;
* an explanation: the prediction with its label, score and logit; one
  wording each for the driver, history and context lines; each selected
  driver as [its reference, rank, score, history or null, context or
  null], history as [records, visits, count] and context as [records,
  count]; the notices, the number of drivers not described and the
  reason codes.

A list keeps every condition, and an explanation every selected driver:
only the prose is shared. What the request fixes (its identifiers, case,
scope and bundle, and the condition a presence question asks about) is
filled in by candidate(), which also puts each statement where its answer
puts it.

schema() is the JSON Schema of one request's document, which the model
runtime enforces while the model writes: references name records,
visits and selected drivers of the bundle only; notices are those the
answer may hold; a statement that cites the prediction or a driver states
that reference's own values, and an explanation the number of drivers
not described; a list of a scope with records is an answer; and a
wording holds no digit and each of its kind's placeholders exactly once,
led by its subject (the condition, the label, the driver). The schema
admits every correct document in that style, and many wrong ones:
whether a document is right is decided by the release gate on the
candidate it becomes. violations() checks a document against its schema
again, since no document is trusted to have been constrained.

Usage:
    python -m ehr_evidence_app.generation.shape
"""

from __future__ import annotations

import argparse
import dataclasses
import itertools
import json
import re
import sys

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.qa import resolve, templates
from ehr_evidence_app.validation import candidate as candidates

SHAPE_VERSION = "candidate-shape-v3"
MAX_WORDING = 600
# A wording's words: letters, spaces and plain punctuation. Runs are not
# bounded here, since bounded runs make the grammar slow to compile; the
# worker stops output that runs on (generation.worker).
WORDS = r"[A-Za-z ,.;:()'/-]*"
# The placeholder each wording begins with, its statement's subject; an
# empty list has none.
SUBJECTS = {"list_summary": "groups", "list_item": "condition",
            "list_unmapped": "unmapped", "presence_summary": "condition",
            "presence_item": "condition", "presence_absent": "condition",
            "presence_unmapped": "unmapped", "prediction": "label",
            "driver": "rank", "driver_history": "driver",
            "driver_context": "driver"}
# The columns of a document's rows, in the order they are written:
# records first, then what is counted from them.
LIST_ROW = ("condition", "refs", "visits", "count")
PRESENCE_ROW = ("condition", "match", "refs", "visits", "count")
DRIVER_ROW = ("driver", "rank", "score", "history", "context")
HISTORY_ROW = ("refs", "visits", "count")
CONTEXT_ROW = ("refs", "count")
# How the model runtime writes JSON between values; a document's text is
# written the same way.
SEPARATORS = (", ", ": ")
# The notice that introduces an explanation's drivers comes right after
# its prediction; every other notice ends its answer.
DRIVERS_NOTICE = "drivers"
OMITTED_NOTICE = "drivers_omitted"


class ShapeError(Exception):
    """A document that does not stand for a candidate, carrying a short
    finding code only."""


@dataclasses.dataclass(frozen=True)
class CountsAs:
    """One concept of a bundle that counts as the condition a presence
    question asks about, and how it matches: a standard concept (by
    "concept"), or an ICD source code (by "source_code"), which is how a
    question about an ICD code, and a record without a standard concept,
    are matched."""

    by: str
    concept_id: int
    vocabulary_id: str | None
    code: str | None
    match: str


@dataclasses.dataclass(frozen=True)
class Asked:
    """The condition a presence question asks about: its name, how it is
    shown, which concepts of the bundle count as it, and how records are
    matched: by "source_code" for a question about an ICD code (every
    record by its source code), by "concept" otherwise (a record by its
    standard concept, or by its source code when it has none)."""

    name: candidates.ConditionName
    label: str
    counts_as: tuple = ()
    by: str = "concept"


@dataclasses.dataclass(frozen=True)
class Context:
    """What a request fixes: the request, its verified bundle and digest,
    for a presence question the condition asked about, and for an
    explanation how many drivers it describes (None: the configured
    number)."""

    request: contracts.Request
    bundle: object
    bundle_sha256: str
    asked: Asked | None = None
    limit: int | None = None

    @property
    def task(self):
        """Return the request's task."""
        return self.request.task


def asked(resolution, bundle, vocabulary):
    """Return the Asked of a resolved presence question for a bundle."""
    name = templates.resolved_name(resolution)
    if resolution.rule == "icd_code":
        label = "ICD code " + ", ".join(resolution.codes)
    else:
        label = "; ".join(vocabulary.rows[concept].name
                          for concept in resolution.concepts)
    found = {}
    for event in bundle.events:
        source = event.source_concept
        kind = resolve.match(resolution, vocabulary, event.concept.concept_id,
                             source.code if source else None,
                             source.concept_id if source else None)
        if not kind:
            continue
        if resolution.rule == "icd_code" or not event.concept.concept_id:
            key = ("source_code", 0,
                   source.vocabulary_id if source else None,
                   source.code if source else event.concept.source_code)
        else:
            key = ("concept", event.concept.concept_id, None, None)
        found[key] = kind
    return Asked(name, label, tuple(CountsAs(*key, match)
                                    for key, match in sorted(
                                        found.items(), key=str)),
                 "source_code" if resolution.rule == "icd_code"
                 else "concept")


def selected_drivers(context):
    """Return the drivers an explanation request's answer describes."""
    # Imported here: validation.explanation writes through this package.
    from ehr_evidence_app.validation import explanation
    return explanation.selected(context.bundle, context.limit)


def omitted_drivers(context):
    """Return how many eligible drivers the answer does not describe."""
    from ehr_evidence_app.validation import explanation
    return explanation.omitted(context.bundle, context.limit)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

def wording(kind):
    """Return the schema of a wording of a statement kind: one line of
    letters, spaces and plain punctuation, holding each placeholder of the
    kind's slots exactly once and no other, beginning with its subject's
    and the others in any order."""
    subject = SUBJECTS.get(kind)
    head = r"\{%s\}" % subject if subject else ""
    rest = [slot for slot in candidates.KINDS[kind].slots
            if slot != subject]
    orders = [head + "".join(WORDS + r"\{%s\}" % slot for slot in order)
              + WORDS for order in itertools.permutations(rest)]
    return {"type": "string", "minLength": 1, "maxLength": MAX_WORDING,
            "pattern": "^(?:%s)$" % "|".join(orders)}


def row_of(columns, **values):
    """Return a row of values in the order of its columns."""
    return [values[column] for column in columns]


def values_of(columns, row):
    """Return the values of a row by column name."""
    return dict(zip(columns, row))


def worded(kind):
    """Return a reference to a statement kind's wording schema, defined
    once in the request's schema."""
    return {"$ref": "#/$defs/%s" % kind}


def integer(least, most):
    """Return the schema of a whole number in a range."""
    return {"type": "integer", "minimum": least, "maximum": most}


def chosen(values, least=1):
    """Return the schema of distinct values chosen from a list."""
    return {"type": "array", "items": {"enum": list(values)},
            "minItems": least, "maxItems": max(len(values), least),
            "uniqueItems": True}


def fields(properties):
    """Return the schema of an object with exactly these properties."""
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def row(*parts):
    """Return the schema of a fixed-length array of these parts."""
    return {"type": "array", "prefixItems": list(parts), "items": False,
            "minItems": len(parts), "maxItems": len(parts)}


def optional(schema_):
    """Return the schema of a value or null."""
    return {"anyOf": [schema_, {"type": "null"}]}


def rows(item, most):
    """Return the schema of a list of at least one row, at most most."""
    return {"type": "array", "items": item, "minItems": 1,
            "maxItems": max(most, 1)}


def notice_ids(task, outcome):
    """Return the approved notices an answer of a task may hold."""
    return [notice_id for notice_id, notice in candidates.NOTICES.items()
            if outcome in notice.outcomes
            and notice.task in (None, task)]


def reason_codes(outcome):
    """Return the schema of the reason codes an outcome may disclose."""
    return chosen(vocab.REASONS_BY_OUTCOME[outcome], least=0)


def visit_numbers(visits):
    """Return the visit numbers of some visits, in order."""
    return sorted({visit.visit_number for visit in visits})


def list_schema(context):
    """Return the schema of a list answer's document: an answer for a
    scope with records, "not recorded" for one without."""
    bundle = context.bundle
    refs = [event.ref for event in bundle.events]
    if not refs:
        return fields({"outcome": {"const": "not_recorded"},
                       "reason_codes": reason_codes("not_recorded"),
                       "empty": fields({"wording": worded("list_empty")}),
                       "notices": chosen(notice_ids(context.task,
                                                    "not_recorded"), 0)})
    total = len(refs)
    return fields({
        "outcome": {"const": "answer"},
        "reason_codes": reason_codes("answer"),
        "item_wording": worded("list_item"),
        "items": rows(row(*row_of(
            LIST_ROW, condition={"enum": refs}, refs=chosen(refs),
            visits=chosen(visit_numbers(bundle.visits)),
            count=integer(1, total))), total),
        "summary": fields({"wording": worded("list_summary"),
                           "groups": integer(1, total),
                           "records": integer(1, total)}),
        "unmapped": optional(fields({"wording": worded("list_unmapped"),
                                     "unmapped": integer(1, total)})),
        "notices": chosen(notice_ids(context.task, "answer"), 0)})


def presence_schema(context):
    """Return the schema of a presence check's document."""
    bundle = context.bundle
    refs = [event.ref for event in bundle.events]
    total = max(len(refs), 1)
    absent = fields({
        "outcome": {"const": "not_recorded"},
        "reason_codes": reason_codes("not_recorded"),
        "absent": fields({"wording": worded("presence_absent")}),
        "unmapped": optional(fields({"wording": worded("presence_unmapped"),
                                     "unmapped": integer(1, total)})),
        "notices": chosen(notice_ids(context.task, "not_recorded"), 0)})
    if not refs:
        return absent
    answer = fields({
        "outcome": {"const": "answer"},
        "reason_codes": reason_codes("answer"),
        "item_wording": worded("presence_item"),
        "items": rows(row(*row_of(
            PRESENCE_ROW, condition={"enum": refs},
            match={"enum": list(candidates.MATCH_WORDS)},
            refs=chosen(refs), visits=chosen(visit_numbers(bundle.visits)),
            count=integer(1, total))), total),
        "summary": fields({"wording": worded("presence_summary"),
                           "matches": integer(1, total)})})
    return {"anyOf": [answer, absent]}


def explanation_schema(context):
    """Return the schema of an explanation's document; the history and
    context of a driver are defined once under $defs."""
    bundle = context.bundle
    prediction = bundle.prediction
    history = [event.ref for event in bundle.history_events]
    final = [event.ref for event in bundle.final_admission_context]
    past = visit_numbers(visit for visit in bundle.visits
                         if visit.role == "pre_target")
    drivers = selected_drivers(context)
    lines = {"history": {"type": "null"}, "context": {"type": "null"}}
    if history and past:
        lines["history"] = optional(row(*row_of(
            HISTORY_ROW, refs=chosen(history), visits=chosen(past),
            count=integer(1, len(history)))))
    if final:
        lines["context"] = optional(row(*row_of(
            CONTEXT_ROW, refs=chosen(final), count=integer(1, len(final)))))
    left = omitted_drivers(context)
    notices = [notice_id
               for notice_id in notice_ids(context.task, "explanation")
               if left or notice_id != OMITTED_NOTICE]
    document = fields({
        "outcome": {"const": "explanation"},
        "reason_codes": reason_codes("explanation"),
        "prediction": fields({"wording": worded("prediction"),
                              "label": {"const": prediction.concept
                                        .concept_id},
                              "score": {"const": prediction.score},
                              "logit": {"const": prediction.logit}}),
        "driver_wording": worded("driver"),
        "history_wording": worded("driver_history"),
        "context_wording": worded("driver_context"),
        "drivers": rows({"anyOf": [row(*row_of(
            DRIVER_ROW, driver={"const": item.ref},
            rank=integer(1, len(drivers)), score={"const": item.score},
            history={"$ref": "#/$defs/history"},
            context={"$ref": "#/$defs/context"})) for item in drivers]},
            len(drivers)),
        "notices": chosen(notices, 0),
        "omitted": {"const": left} if left else {"type": "null"}})
    return dict(document, **{"$defs": lines})


# The statement kinds whose wordings each task's documents hold.
WORDED = {"condition_list": ("list_summary", "list_item", "list_unmapped",
                             "list_empty"),
          "condition_presence": ("presence_summary", "presence_item",
                                 "presence_absent", "presence_unmapped"),
          "prediction_explanation": ("prediction", "driver",
                                     "driver_history", "driver_context")}


def schema(context):
    """Return the JSON Schema of the document a request's answer is
    written as; the wordings, and an explanation's history and context,
    are defined once under $defs."""
    builders = {"condition_list": list_schema,
                "condition_presence": presence_schema,
                "prediction_explanation": explanation_schema}
    found = builders[context.task](context)
    definitions = dict(found.get("$defs", {}))
    definitions.update({kind: wording(kind)
                        for kind in WORDED[context.task]})
    return dict(found, **{"$defs": definitions})


# --------------------------------------------------------------------------
# Checking a document against its schema
# --------------------------------------------------------------------------

TYPES = {"object": dict, "array": list, "string": str, "null": type(None),
         "boolean": bool}
KEYWORDS = frozenset(("anyOf", "const", "enum", "type", "properties",
                      "required", "additionalProperties", "items",
                      "prefixItems", "minItems", "maxItems", "uniqueItems",
                      "minLength", "maxLength", "pattern", "minimum",
                      "maximum", "$ref", "$defs"))
DEFINITION = re.compile(r"^#/\$defs/([A-Za-z0-9_]+)$")


def same(value, expected):
    """Return True when a JSON value equals an expected one, booleans,
    numbers and strings never standing for each other."""
    numeric = (isinstance(value, (int, float))
               and not isinstance(value, bool))
    expected_numeric = (isinstance(expected, (int, float))
                        and not isinstance(expected, bool))
    return (isinstance(value, bool) == isinstance(expected, bool)
            and numeric == expected_numeric and value == expected)


def typed(value, name):
    """Return True when a JSON value has a JSON Schema type."""
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value,
                                                                  bool)
    return isinstance(value, TYPES[name])


def array_violations(value, schema_, path, root):
    """Return where a JSON array breaks its schema: its length, repeated
    items, and each item against prefixItems and items."""
    found = []
    if len(value) < schema_.get("minItems", 0) or len(value) > schema_.get(
            "maxItems", len(value)):
        found.append(path + ": wrong number of items")
    if schema_.get("uniqueItems") and len({json.dumps(
            item, sort_keys=True) for item in value}) != len(value):
        found.append(path + ": repeated items")
    prefix = schema_.get("prefixItems", [])
    for index, item in enumerate(value):
        where = "%s[%d]" % (path, index)
        if index < len(prefix):
            found += violations(item, prefix[index], where, root)
        elif schema_.get("items") is False:
            found.append(where + ": no item allowed here")
        elif isinstance(schema_.get("items"), dict):
            found += violations(item, schema_["items"], where, root)
    return found


def violations(value, schema_, path="$", root=None):
    """Return where a JSON value breaks a schema of the keywords this
    module writes; an unknown keyword or reference is a ValueError."""
    root = schema_ if root is None else root
    unknown = set(schema_) - KEYWORDS
    if unknown:
        raise ValueError("unsupported schema keyword: %s" % sorted(unknown))
    if "$ref" in schema_:
        match = DEFINITION.match(schema_["$ref"])
        if match is None or match.group(1) not in root.get("$defs", {}):
            raise ValueError("unresolved schema reference")
        return violations(value, root["$defs"][match.group(1)], path, root)
    if "anyOf" in schema_:
        if all(violations(value, branch, path, root) for branch in
               schema_["anyOf"]):
            return [path + ": matches no alternative"]
        return []
    if "const" in schema_ and not same(value, schema_["const"]):
        return [path + ": not the fixed value"]
    if "enum" in schema_ and not any(same(value, option)
                                     for option in schema_["enum"]):
        return [path + ": not an allowed value"]
    if "type" in schema_ and not typed(value, schema_["type"]):
        return [path + ": not of type " + schema_["type"]]
    found = []
    if isinstance(value, dict):
        properties = schema_.get("properties", {})
        found += [path + ": missing " + name
                  for name in schema_.get("required", ()) if name not in
                  value]
        if schema_.get("additionalProperties") is False:
            found += [path + ": unexpected " + name for name in value
                      if name not in properties]
        for name, item in value.items():
            if name in properties:
                found += violations(item, properties[name],
                                    path + "." + name, root)
    if isinstance(value, list):
        found += array_violations(value, schema_, path, root)
    if isinstance(value, str):
        if not schema_.get("minLength", 0) <= len(value) <= schema_.get(
                "maxLength", len(value)):
            found.append(path + ": wrong length")
        if "pattern" in schema_ and not re.search(schema_["pattern"], value):
            found.append(path + ": does not match its pattern")
    if typed(value, "number") and not (
            schema_.get("minimum", value) <= value
            <= schema_.get("maximum", value)):
        found.append(path + ": out of range")
    return found


# --------------------------------------------------------------------------
# Documents and candidates
# --------------------------------------------------------------------------

def statement(kind, wording_, refs=(), **slots):
    """Return a statement of a kind with a wording, facts and references."""
    return candidates.Statement(kind=kind, wording=wording_,
                                slots=candidates.Slots(**slots),
                                refs=tuple(refs))


def visits(numbers):
    """Return visit numbers, which are a set, in order."""
    return tuple(sorted(numbers))


def qa_statements(context, document):
    """Return the statements of a QA document, in their answer's order."""
    scope = context.request.scope
    unmapped = document.get("unmapped")
    kind = ("list_unmapped" if context.task == "condition_list"
            else "presence_unmapped")
    tail = ([statement(kind, unmapped["wording"], ("C1",),
                       unmapped=unmapped["unmapped"])]
            if unmapped else [])
    if context.task == "condition_list":
        if document["outcome"] == "not_recorded":
            return [statement("list_empty", document["empty"]["wording"],
                              ("C1",), scope=scope)]
        summary = document["summary"]
        items = [values_of(LIST_ROW, row) for row in document["items"]]
        return [statement("list_summary", summary["wording"],
                          groups=summary["groups"], scope=scope,
                          records=summary["records"])] + [
            statement("list_item", document["item_wording"], item["refs"],
                      condition=candidates.ConditionName(
                          event=item["condition"]),
                      count=item["count"], visits=visits(item["visits"]))
            for item in items] + tail
    name = context.asked.name
    if document["outcome"] == "not_recorded":
        return [statement("presence_absent", document["absent"]["wording"],
                          ("C1",), condition=name, scope=scope)] + tail
    summary = document["summary"]
    items = [values_of(PRESENCE_ROW, row) for row in document["items"]]
    return [statement("presence_summary", summary["wording"], condition=name,
                      scope=scope, matches=summary["matches"])] + [
        statement("presence_item", document["item_wording"], item["refs"],
                  condition=candidates.ConditionName(event=item["condition"]),
                  count=item["count"], visits=visits(item["visits"]),
                  match=item["match"])
        for item in items]


def explanation_statements(document):
    """Return the statements of an explanation document, before its
    closing notices."""
    part = document["prediction"]
    found = [statement("prediction", part["wording"], ("P1",),
                       label=part["label"], score=part["score"],
                       logit=part["logit"])]
    if DRIVERS_NOTICE in document["notices"]:
        found.append(candidates.notice(DRIVERS_NOTICE))
    for row in document["drivers"]:
        item = values_of(DRIVER_ROW, row)
        driver = item["driver"]
        found.append(statement("driver", document["driver_wording"],
                               (driver,), rank=item["rank"], driver=driver,
                               score=item["score"]))
        if item["history"]:
            history = values_of(HISTORY_ROW, item["history"])
            found.append(statement("driver_history",
                                   document["history_wording"],
                                   history["refs"], driver=driver,
                                   count=history["count"],
                                   visits=visits(history["visits"])))
        if item["context"]:
            final = values_of(CONTEXT_ROW, item["context"])
            found.append(statement("driver_context",
                                   document["context_wording"],
                                   final["refs"], driver=driver,
                                   count=final["count"]))
    return found


def closing_notices(context, document):
    """Return the notices that end an answer, in the document's order; the
    notice on drivers not described states the document's number."""
    found = []
    for notice_id in document.get("notices", ()):
        if context.task == "prediction_explanation" and (
                notice_id == DRIVERS_NOTICE):
            continue
        if notice_id == OMITTED_NOTICE:
            found.append(candidates.notice(notice_id,
                                           omitted=document.get("omitted")))
        else:
            found.append(candidates.notice(notice_id))
    return found


def candidate(context, document):
    """Return the Candidate a document stands for in a request; raises
    ShapeError when it does not stand for one."""
    try:
        if context.task == "prediction_explanation":
            statements = explanation_statements(document)
        else:
            statements = qa_statements(context, document)
        statements += closing_notices(context, document)
        request = context.request
        return candidates.Candidate(
            schema_version=candidates.CANDIDATE_VERSION,
            request_id=request.request_id, task=request.task,
            case=request.case, scope=request.scope,
            outcome=document["outcome"],
            reason_codes=tuple(document.get("reason_codes", ())),
            bundle=contracts.BundleRef(bundle_id=context.bundle.bundle_id,
                                       sha256=context.bundle_sha256),
            statements=tuple(statements))
    except (KeyError, TypeError, AttributeError, ValueError):
        # pydantic's ValidationError is a ValueError.
        raise ShapeError("document_malformed") from None


def shared_wording(items):
    """Return the one wording some statements share; raises ShapeError
    when they differ, since a compact document holds one per kind."""
    wordings = {item.wording for item in items}
    if len(wordings) > 1:
        raise ShapeError("wordings_differ")
    return wordings.pop() if wordings else None


def document_of(candidate_):
    """Return the compact document a candidate is written as; the inverse
    of candidate() for a candidate in its answer's order whose statements
    of one kind share their wording."""
    by_kind = {}
    for item in candidate_.statements:
        by_kind.setdefault(item.kind, []).append(item)
    notices = [item.notice for item in by_kind.get("notice", ())]
    document = {"outcome": candidate_.outcome,
                "reason_codes": list(candidate_.reason_codes)}
    unmapped = (by_kind.get("list_unmapped")
                or by_kind.get("presence_unmapped"))
    if candidate_.task == "prediction_explanation":
        return explanation_document(candidate_, by_kind, notices, document)
    if candidate_.outcome == "not_recorded":
        if candidate_.task == "condition_list":
            document["empty"] = {"wording": by_kind["list_empty"][0].wording}
        else:
            document["absent"] = {
                "wording": by_kind["presence_absent"][0].wording}
            document["unmapped"] = {
                "wording": unmapped[0].wording,
                "unmapped": unmapped[0].slots.unmapped} if unmapped else None
        document["notices"] = notices
        return document
    if candidate_.task == "condition_list":
        summary = by_kind["list_summary"][0]
        items = by_kind["list_item"]
        document.update(
            item_wording=shared_wording(items),
            items=[row_of(LIST_ROW, condition=item.slots.condition.event,
                          refs=list(item.refs),
                          visits=list(item.slots.visits),
                          count=item.slots.count) for item in items],
            summary={"wording": summary.wording,
                     "groups": summary.slots.groups,
                     "records": summary.slots.records},
            unmapped={"wording": unmapped[0].wording,
                      "unmapped": unmapped[0].slots.unmapped}
            if unmapped else None, notices=notices)
        return document
    summary = by_kind["presence_summary"][0]
    items = by_kind["presence_item"]
    document.update(
        item_wording=shared_wording(items),
        items=[row_of(PRESENCE_ROW, condition=item.slots.condition.event,
                      match=item.slots.match, refs=list(item.refs),
                      visits=list(item.slots.visits),
                      count=item.slots.count) for item in items],
        summary={"wording": summary.wording,
                 "matches": summary.slots.matches})
    return document


def explanation_document(candidate_, by_kind, notices, document):
    """Return the compact document of an explanation candidate."""
    prediction = by_kind["prediction"][0]
    lines = {}
    for item in candidate_.statements:
        if item.kind in ("driver_history", "driver_context"):
            lines[(item.kind, item.slots.driver)] = item
    drivers = []
    for item in by_kind["driver"]:
        history = lines.get(("driver_history", item.slots.driver))
        final = lines.get(("driver_context", item.slots.driver))
        drivers.append(row_of(
            DRIVER_ROW, driver=item.slots.driver, rank=item.slots.rank,
            score=item.slots.score,
            history=row_of(HISTORY_ROW, refs=list(history.refs),
                           visits=list(history.slots.visits),
                           count=history.slots.count) if history else None,
            context=row_of(CONTEXT_ROW, refs=list(final.refs),
                           count=final.slots.count) if final else None))
    templates_ = {kind: shared_wording(by_kind.get(kind, ()))
                  for kind in ("driver", "driver_history", "driver_context")}
    omitted = [item.slots.omitted for item in by_kind.get("notice", ())
               if item.notice == OMITTED_NOTICE]
    document.update(
        prediction={"wording": prediction.wording,
                    "label": prediction.slots.label,
                    "score": prediction.slots.score,
                    "logit": prediction.slots.logit},
        driver_wording=templates_["driver"],
        history_wording=templates_["driver_history"]
        or example_wording("driver_history"),
        context_wording=templates_["driver_context"]
        or example_wording("driver_context"),
        drivers=drivers, notices=notices,
        omitted=omitted[0] if omitted else None)
    return document


def example_wording(kind):
    """Return the template's wording of a statement kind."""
    # Imported here: validation.explanation writes through this package.
    from ehr_evidence_app.validation import explanation
    found = dict(explanation.WORDINGS)
    found.update({name: templates.wording(name, 2)
                  for name in templates.WORDINGS})
    return found[kind]


def text(document):
    """Return a document as JSON text, as the model runtime writes it."""
    return json.dumps(document, separators=SEPARATORS, ensure_ascii=False)


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

def synthetic_contexts():
    """Return (context, template candidate) pairs of the invented
    fixtures: lists and presence checks in every scope, and the
    explanation."""
    # Imported here: qa.answer and validation.explanation write through
    # this module.
    from ehr_evidence_app.evidence import bundles
    from ehr_evidence_app.qa import answer, suite
    from ehr_evidence_app.validation import evidence, explanation
    vocabulary = suite.synthetic_vocabulary()
    bundle_set = suite.synthetic_bundle_set()
    found = []
    for number, (case_number, scope) in enumerate(sorted(
            bundle_set.entries, key=lambda key: (
                key[0], key[1].kind, key[1].visit_number or 0))):
        bundle, digest = bundle_set.qa_bundle(case_number, scope)
        ref = contracts.BundleRef(bundle_id=bundle.bundle_id, sha256=digest)
        case = contracts.CaseRef(case_set="synthetic-1-v1",
                                 case_number=case_number)

        def request(task):
            """Return an invented request of a task in this scope."""
            return contracts.Request(
                schema_version="1.0", request_id="g-%03d" % number,
                task=task, case=case, scope=scope, question="Invented.")

        template, _ = templates.list_candidate("g-%03d" % number, case,
                                               scope, bundle, ref)
        found.append((Context(request("condition_list"), bundle, digest),
                      template))
        recorded = sorted({event.concept.concept_id
                           for event in bundle.events
                           if event.concept.concept_id in vocabulary.rows})
        elsewhere = [concept for concept in sorted(vocabulary.rows)
                     if concept not in recorded]
        for concept in recorded[:1] + elsewhere[:1]:
            resolution = resolve.Resolution("", "resolved", "concepts",
                                            concepts=(concept,))
            template = templates.presence_candidate(
                "g-%03d" % number, case, scope, bundle, ref, resolution,
                answer.record_matches(bundle, resolution, vocabulary))
            found.append((Context(request("condition_presence"), bundle,
                                  digest, asked(resolution, bundle,
                                                vocabulary)), template))
    _, explanations, _ = bundles.run_synthetic()
    bundle = explanations[0]
    request = evidence.explanation_request(bundle)
    for limit in (None, 1):
        template = explanation.template(request, bundle, "5" * 64, limit)
        found.append((Context(request, bundle, "5" * 64, limit=limit),
                      template))
    return found


def synthetic_faults(document):
    """Return invented documents that break their schema."""
    faults = [dict(document, extra="field"),
              dict(document, outcome="abstention"),
              {key: value for key, value in document.items()
               if key != "reason_codes"}]
    if document.get("items"):
        first = list(document["items"][0])
        faults.append(dict(document, items=[first[:1] + ["x"] + first[2:]]
                           + document["items"][1:]))
        faults.append(dict(document, items=[first + ["extra"]]
                           + document["items"][1:]))
        faults.append(dict(document,
                           item_wording="{condition}: 3 records in {visits}"))
    if document.get("drivers"):
        driver = list(document["drivers"][0])
        driver[2] = driver[2] + 1.0
        faults.append(dict(document, drivers=[driver]
                           + document["drivers"][1:]))
    return faults


def run_synthetic():
    """Return (round trips, contexts, faults refused, faults) on the
    invented fixtures."""
    trips, refused, total = 0, 0, 0
    pairs = synthetic_contexts()
    for context, template in pairs:
        document = json.loads(text(document_of(template)))
        rules = schema(context)
        if not violations(document, rules) and candidate(
                context, document) == template:
            trips += 1
        for fault in synthetic_faults(document):
            total += 1
            refused += bool(violations(fault, rules))
    return trips, len(pairs), refused, total


def main(argv=None):
    """Write the invented template answers as documents and read them
    back; check that broken documents break their schema."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    trips, pairs, refused, total = run_synthetic()
    print("template answers written and read back unchanged: %d of %d"
          % (trips, pairs))
    print("broken documents refused by their schema: %d of %d"
          % (refused, total))
    print("shape %s" % SHAPE_VERSION)
    ok = trips == pairs and refused == total
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
