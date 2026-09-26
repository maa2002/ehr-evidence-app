"""The shape of a model-written candidate, and its schema (work item 41).

In Phase 7 the language model writes the complete candidate of every
response from evidence (ADR 0012): list and presence answers, "not
recorded" included, and explanations. Clarifications, refusals and
abstentions are settled before any evidence is read and stay approved
notices. The model writes one JSON document in the shape of its answer:

* a list: its outcome, the summary, one item per condition (the record
  that names it, its count, visits and records), the line on records
  without a standard concept, the notices and the reason codes;
* a presence check: its outcome, the summary or the "not recorded" line,
  the items with their match kinds, the unmapped line, the notices and
  the reason codes;
* an explanation: the prediction with its label, score and logit, each
  driver with its rank and score and the history and final-admission
  context under it, the notices and the reason codes.

Every statement carries its wording, with the placeholders of its kind.
What the request fixes (its identifiers, case, scope and bundle, and the
condition a presence question asks about) is filled in by candidate(),
which also puts each statement where its answer puts it.

schema() is the JSON Schema of one request's document, which the model
runtime enforces while the model writes: references name records,
visits and drivers of the bundle only, notices are those an answer of the
task may hold, a statement that cites one prediction or driver states
that reference's own values, and wordings hold no digit and no
placeholder of another kind. The schema admits every correct document,
and many wrong ones: whether a document is right is decided by the
release gate on the candidate it becomes. violations() checks a document
against its schema again, since no document is trusted to have been
constrained.

Usage:
    python -m ehr_evidence_app.generation.shape
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.qa import resolve, templates
from ehr_evidence_app.validation import candidate as candidates

SHAPE_VERSION = "candidate-shape-v1"
MAX_WORDING = 600
# The notice that introduces an explanation's drivers comes right after
# its prediction; every other notice ends its answer.
DRIVERS_NOTICE = "drivers"


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
    shown, and which concepts of the bundle count as it."""

    name: candidates.ConditionName
    label: str
    counts_as: tuple = ()


@dataclasses.dataclass(frozen=True)
class Context:
    """What a request fixes: the request, its verified bundle and digest,
    and for a presence question the condition asked about."""

    request: contracts.Request
    bundle: object
    bundle_sha256: str
    asked: Asked | None = None

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
                                        found.items(), key=str)))


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------

def wording(kind):
    """Return the schema of a wording of a statement kind: one line of
    words with no digit, and placeholders of the kind's slots only."""
    slots = "|".join(candidates.KINDS[kind].slots)
    return {"type": "string", "minLength": 1, "maxLength": MAX_WORDING,
            "pattern": r"^(?:[^0-9{}\r\n]|\{(?:%s)\})+$" % slots}


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


def optional(schema):
    """Return the schema of a value or null."""
    return {"anyOf": [schema, {"type": "null"}]}


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
    """Return the schema of a list answer's document."""
    bundle = context.bundle
    refs = [event.ref for event in bundle.events]
    visits = visit_numbers(bundle.visits)
    empty = fields({"outcome": {"const": "not_recorded"},
                    "reason_codes": reason_codes("not_recorded"),
                    "empty": fields({"wording": wording("list_empty")}),
                    "notices": chosen(notice_ids(context.task,
                                                 "not_recorded"), 0)})
    if not refs:
        return empty
    total = len(refs)
    item = fields({"wording": wording("list_item"),
                   "condition": {"enum": refs},
                   "count": integer(1, total),
                   "visits": chosen(visits), "refs": chosen(refs)})
    answer = fields({
        "outcome": {"const": "answer"},
        "reason_codes": reason_codes("answer"),
        "summary": fields({"wording": wording("list_summary"),
                           "groups": integer(1, total),
                           "records": integer(1, total)}),
        "items": {"type": "array", "items": item, "minItems": 1,
                  "maxItems": total},
        "unmapped": optional(fields({"wording": wording("list_unmapped"),
                                     "unmapped": integer(1, total)})),
        "notices": chosen(notice_ids(context.task, "answer"), 0)})
    return {"anyOf": [answer, empty]}


def presence_schema(context):
    """Return the schema of a presence check's document."""
    bundle = context.bundle
    refs = [event.ref for event in bundle.events]
    visits = visit_numbers(bundle.visits)
    total = max(len(refs), 1)
    absent = fields({
        "outcome": {"const": "not_recorded"},
        "reason_codes": reason_codes("not_recorded"),
        "absent": fields({"wording": wording("presence_absent")}),
        "unmapped": optional(fields({"wording": wording("presence_unmapped"),
                                     "unmapped": integer(1, total)})),
        "notices": chosen(notice_ids(context.task, "not_recorded"), 0)})
    if not refs:
        return absent
    item = fields({"wording": wording("presence_item"),
                   "condition": {"enum": refs},
                   "count": integer(1, total), "visits": chosen(visits),
                   "match": {"enum": list(candidates.MATCH_WORDS)},
                   "refs": chosen(refs)})
    answer = fields({
        "outcome": {"const": "answer"},
        "reason_codes": reason_codes("answer"),
        "summary": fields({"wording": wording("presence_summary"),
                           "matches": integer(1, total)}),
        "items": {"type": "array", "items": item, "minItems": 1,
                  "maxItems": total}})
    return {"anyOf": [answer, absent]}


def explanation_schema(context):
    """Return the schema of an explanation's document."""
    bundle = context.bundle
    prediction = bundle.prediction
    history = [event.ref for event in bundle.history_events]
    final = [event.ref for event in bundle.final_admission_context]
    past = visit_numbers(visit for visit in bundle.visits
                         if visit.role == "pre_target")
    drivers = len(bundle.attributions)
    lines = {"history": {"type": "null"}, "context": {"type": "null"}}
    if history and past:
        lines["history"] = optional(fields({
            "wording": wording("driver_history"),
            "count": integer(1, len(history)), "visits": chosen(past),
            "refs": chosen(history)}))
    if final:
        lines["context"] = optional(fields({
            "wording": wording("driver_context"),
            "count": integer(1, len(final)), "refs": chosen(final)}))
    return fields({
        "outcome": {"const": "explanation"},
        "reason_codes": reason_codes("explanation"),
        "prediction": fields({"wording": wording("prediction"),
                              "label": {"const": prediction.concept
                                        .concept_id},
                              "score": {"const": prediction.score},
                              "logit": {"const": prediction.logit}}),
        "drivers": {"type": "array", "minItems": 1, "maxItems": drivers,
                    "items": {"anyOf": [fields({
                        "wording": wording("driver"),
                        "driver": {"const": item.ref},
                        "rank": integer(1, drivers),
                        "score": {"const": item.score}, **lines})
                        for item in bundle.attributions]}},
        "notices": chosen(notice_ids(context.task, "explanation"), 0)})


def schema(context):
    """Return the JSON Schema of the document a request's answer is
    written as."""
    builders = {"condition_list": list_schema,
                "condition_presence": presence_schema,
                "prediction_explanation": explanation_schema}
    return builders[context.task](context)


# --------------------------------------------------------------------------
# Checking a document against its schema
# --------------------------------------------------------------------------

TYPES = {"object": dict, "array": list, "string": str, "null": type(None),
         "boolean": bool}
KEYWORDS = frozenset(("anyOf", "const", "enum", "type", "properties",
                      "required", "additionalProperties", "items",
                      "minItems", "maxItems", "uniqueItems", "minLength",
                      "maxLength", "pattern", "minimum", "maximum"))


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


def violations(value, schema_, path="$"):
    """Return where a JSON value breaks a schema of the keywords this
    module writes; an unknown keyword is a ValueError."""
    unknown = set(schema_) - KEYWORDS
    if unknown:
        raise ValueError("unsupported schema keyword: %s" % sorted(unknown))
    if "anyOf" in schema_:
        if all(violations(value, branch, path) for branch in
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
                                    path + "." + name)
    if isinstance(value, list):
        if len(value) < schema_.get("minItems", 0) or len(value) > schema_.get(
                "maxItems", len(value)):
            found.append(path + ": wrong number of items")
        if schema_.get("uniqueItems") and len({json.dumps(
                item, sort_keys=True) for item in value}) != len(value):
            found.append(path + ": repeated items")
        if "items" in schema_:
            for index, item in enumerate(value):
                found += violations(item, schema_["items"],
                                    "%s[%d]" % (path, index))
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

def statement(kind, part, refs=(), **slots):
    """Return a statement of a kind from a document's part."""
    return candidates.Statement(kind=kind, wording=part["wording"],
                                slots=candidates.Slots(**slots),
                                refs=tuple(refs))


def visits(part):
    """Return the visit numbers of a part, which are a set, in order."""
    return tuple(sorted(part["visits"]))


def item_slots(part):
    """Return the slots of a list or presence item's part."""
    return {"condition": candidates.ConditionName(event=part["condition"]),
            "count": part["count"], "visits": visits(part)}


def qa_statements(context, document):
    """Return the statements of a QA document, in their answer's order."""
    scope = context.request.scope
    unmapped = document.get("unmapped")
    kind = ("list_unmapped" if context.task == "condition_list"
            else "presence_unmapped")
    tail = ([statement(kind, unmapped, ("C1",),
                       unmapped=unmapped["unmapped"])]
            if unmapped else [])
    if context.task == "condition_list":
        if document["outcome"] == "not_recorded":
            return [statement("list_empty", document["empty"], ("C1",),
                              scope=scope)]
        summary = document["summary"]
        return [statement("list_summary", summary, groups=summary["groups"],
                          scope=scope, records=summary["records"])] + [
            statement("list_item", part, part["refs"], **item_slots(part))
            for part in document["items"]] + tail
    name = context.asked.name
    if document["outcome"] == "not_recorded":
        return [statement("presence_absent", document["absent"], ("C1",),
                          condition=name, scope=scope)] + tail
    summary = document["summary"]
    return [statement("presence_summary", summary, condition=name,
                      scope=scope, matches=summary["matches"])] + [
        statement("presence_item", part, part["refs"], match=part["match"],
                  **item_slots(part)) for part in document["items"]]


def explanation_statements(document):
    """Return the statements of an explanation document, before its
    notices."""
    part = document["prediction"]
    found = [statement("prediction", part, ("P1",), label=part["label"],
                       score=part["score"], logit=part["logit"])]
    if DRIVERS_NOTICE in document["notices"]:
        found.append(candidates.notice(DRIVERS_NOTICE))
    for driver in document["drivers"]:
        found.append(statement("driver", driver, (driver["driver"],),
                               rank=driver["rank"], driver=driver["driver"],
                               score=driver["score"]))
        history = driver.get("history")
        if history:
            found.append(statement("driver_history", history,
                                   history["refs"], driver=driver["driver"],
                                   count=history["count"],
                                   visits=visits(history)))
        final = driver.get("context")
        if final:
            found.append(statement("driver_context", final, final["refs"],
                                   driver=driver["driver"],
                                   count=final["count"]))
    return found


def candidate(context, document):
    """Return the Candidate a document stands for in a request; raises
    ShapeError when it does not stand for one."""
    try:
        if context.task == "prediction_explanation":
            statements = explanation_statements(document)
        else:
            statements = qa_statements(context, document)
        statements += [candidates.notice(notice_id)
                       for notice_id in document.get("notices", ())
                       if notice_id != DRIVERS_NOTICE or (
                           context.task != "prediction_explanation")]
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


def part(statement_, **values):
    """Return the document part of a statement: its wording and values."""
    return dict({"wording": statement_.wording}, **values)


def item_part(statement_, **values):
    """Return the document part of a list or presence item."""
    slots = statement_.slots
    return part(statement_, condition=slots.condition.event,
                count=slots.count, visits=list(slots.visits),
                refs=list(statement_.refs), **values)


def document_of(candidate_):
    """Return the document a candidate is written as; the inverse of
    candidate() for a candidate in its answer's order."""
    by_kind = {}
    for item in candidate_.statements:
        by_kind.setdefault(item.kind, []).append(item)
    notices = [item.notice for item in by_kind.get("notice", ())]
    document = {"outcome": candidate_.outcome,
                "reason_codes": list(candidate_.reason_codes)}
    unmapped = (by_kind.get("list_unmapped")
                or by_kind.get("presence_unmapped"))
    if candidate_.task == "prediction_explanation":
        prediction = by_kind["prediction"][0]
        drivers = []
        for item in by_kind["driver"]:
            lines = {line.kind: line for line in candidate_.statements
                     if line.kind in ("driver_history", "driver_context")
                     and line.slots.driver == item.slots.driver}
            history = lines.get("driver_history")
            final = lines.get("driver_context")
            drivers.append(part(
                item, driver=item.slots.driver, rank=item.slots.rank,
                score=item.slots.score,
                history=part(history, count=history.slots.count,
                             visits=list(history.slots.visits),
                             refs=list(history.refs)) if history else None,
                context=part(final, count=final.slots.count,
                             refs=list(final.refs)) if final else None))
        document.update(prediction=part(
            prediction, label=prediction.slots.label,
            score=prediction.slots.score, logit=prediction.slots.logit),
            drivers=drivers, notices=notices)
        return document
    if candidate_.outcome == "not_recorded":
        if candidate_.task == "condition_list":
            document["empty"] = part(by_kind["list_empty"][0])
        else:
            document["absent"] = part(by_kind["presence_absent"][0])
            document["unmapped"] = part(
                unmapped[0], unmapped=unmapped[0].slots.unmapped) \
                if unmapped else None
        document["notices"] = notices
        return document
    if candidate_.task == "condition_list":
        summary = by_kind["list_summary"][0]
        document.update(
            summary=part(summary, groups=summary.slots.groups,
                         records=summary.slots.records),
            items=[item_part(item) for item in by_kind["list_item"]],
            unmapped=part(unmapped[0], unmapped=unmapped[0].slots.unmapped)
            if unmapped else None, notices=notices)
        return document
    summary = by_kind["presence_summary"][0]
    document.update(summary=part(summary, matches=summary.slots.matches),
                    items=[item_part(item, match=item.slots.match)
                           for item in by_kind["presence_item"]])
    return document


def text(document):
    """Return a document as compact JSON text, as a writer emits it."""
    return json.dumps(document, separators=(",", ":"), ensure_ascii=False)


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
    template = explanation.template(request, bundle, "5" * 64)
    found.append((Context(request, bundle, "5" * 64), template))
    return found


def synthetic_faults(document):
    """Return invented documents that break their schema."""
    faults = [dict(document, extra="field"),
              dict(document, outcome="abstention"),
              {key: value for key, value in document.items()
               if key != "reason_codes"}]
    if document.get("items"):
        item = dict(document["items"][0], refs=["E99999"])
        faults.append(dict(document, items=[item] + document["items"][1:]))
        worded = dict(document["items"][0],
                      wording="{condition}: 3 records in {visits}")
        faults.append(dict(document, items=[worded]
                           + document["items"][1:]))
    if document.get("drivers"):
        driver = dict(document["drivers"][0],
                      score=document["drivers"][0]["score"] + 1.0)
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
