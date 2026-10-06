"""Answers wrong in several facts at once, and wrong answers that agree
with themselves.

The mutation tests (test_mutations, test_record_mutations) break one fact
of a correct answer at a time. A writer can get several facts wrong at
once, and at worst wrong in a way that agrees with itself: a record left
out and every count lowered to match. Such an answer passes any check of
the answer against itself; only a check against the record finds it.
Three kinds are made here, from correct answers of every task:

* two mistakes at once: a one-fact mutant broken again by a second;
* an answer written from a copy of the record with one thing misread: a
  record left out, moved in time, given another value or flag, taken for
  another item or moved to another visit. The templates write it from
  the copy, so its counts, references and times agree with each other,
  and it is checked against the real record;
* the right answer to another question, scope or condition, given as
  this one's.

Each must be withheld unless it states what the correct answer states:
a list or presence item may be named by any of its own records, and the
groups of different items (list and presence items, and a value list's
groups with their lines) and the notices may come in any order, since no
reader relies on it; stays and transfers keep their time order. Every
correct answer is released. Every person, record and value is invented.
"""

import dataclasses
import json
import random
import types
import unittest

from pydantic import ValidationError

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.qa import answer, record_answer, record_interpret, \
    resolve, suite, templates
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import mutations
from tests.qa import record_cases as cases
from tests.qa import test_consistency as consistency
from tests.qa import test_record_edges as edges
from tests.validation import test_record_mutations as record_mutations

SEED = 20260930
WITHHELD = ("validation_rejection", "structure_invalid")
NAMED = ("record_list_item", "record_presence_item", "list_item",
         "presence_item")
GROUPS = NAMED + ("record_value_group",)
OTHER_ITEMS = 3
CONTEXT = record_mutations.CONTEXT
VOCAB = suite.synthetic_vocabulary()


# Answers Compared As What They State

def canonical(candidate, bundle):
    """Return a candidate as data, each list or presence item named by
    what it is of rather than by which of its records, and its notices in
    one order."""
    entries = {entry.ref: entry for entry in getattr(
        bundle, "records", None) or getattr(bundle, "events", ())}
    data = candidate.model_dump(mode="json")
    for statement in data["statements"]:
        slots = statement.get("slots") or {}
        if statement["kind"] not in NAMED:
            continue
        for name, field in (("item", "record"), ("condition", "event")):
            named = slots.get(name) or {}
            if named.get(field) in entries:
                named[field] = list(consistency.key_of(
                    entries[named[field]]))
    notices = sorted((statement for statement in data["statements"]
                      if statement["kind"] == "notice"),
                     key=lambda statement: statement["notice"])
    data["statements"] = in_one_order([
        statement for statement in data["statements"]
        if statement["kind"] != "notice"]) + notices
    return data


def in_one_order(statements):
    """Return statements with their groups of different items, each a
    list or presence item or a value group with its lines, in one order;
    as written when the groups are not together."""
    starts = [index for index, statement in enumerate(statements)
              if statement["kind"] in GROUPS]
    if len(starts) < 2:
        return statements
    end = starts[-1] + 1
    while end < len(statements) and (
            statements[end]["kind"] == "record_value_item"):
        end += 1
    groups = []
    for statement in statements[starts[0]:end]:
        if statement["kind"] in GROUPS:
            groups.append([statement])
        elif statement["kind"] == "record_value_item" and groups:
            groups[-1].append(statement)
        else:
            return statements
    ordered = sorted(groups, key=lambda group: json.dumps(group,
                                                         sort_keys=True))
    return (statements[:starts[0]]
            + [statement for group in ordered for statement in group]
            + statements[end:])


def parsed(candidate):
    """Return a candidate as a writer's document would be read, or None
    when it does not parse."""
    try:
        return candidates.parse(candidate.model_dump(mode="json"))
    except ValidationError:
        return None


# A Record Misread

def shifted(text, years):
    """Return a time moved by whole years."""
    return "%04d%s" % (int(text[:4]) + years, text[4:])


def with_records(bundle, entries):
    """Return a record bundle holding other records, its coverage counted
    again."""
    kinds = []
    for item in bundle.coverage.kinds:
        found = [entry for entry in entries if entry.kind == item.kind]
        kinds.append(item.model_copy(update={
            "in_scope": len(found),
            "unnamed_in_scope": sum(entry.name_status != "available"
                                    for entry in found),
            "status": "complete" if found else "complete_empty"}))
    return bundle.model_copy(update={
        "records": tuple(entries), "coverage": bundle.coverage.model_copy(
            update={"kinds": tuple(kinds)})})


def misread_records(bundle, ref):
    """Return (label, bundle) pairs: a record bundle with one record
    misread."""
    entries = list(bundle.records)
    index = next(index for index, entry in enumerate(entries)
                 if entry.ref == ref)
    entry = entries[index]
    found = [("left out", with_records(
        bundle, entries[:index] + entries[index + 1:]))]

    def changed(label, **update):
        """Add the bundle with this record changed."""
        copy = list(entries)
        copy[index] = entry.model_copy(update=update)
        found.append((label, with_records(bundle, copy)))

    starts = sorted(item.start for item in entries if item.start)
    if entry.start:
        changed("moved after every record", start=shifted(starts[-1], 1),
                end=None)
        changed("moved before every record", start=shifted(starts[0], -1),
                end=None)
    measure = entry.measure
    if measure is not None and measure.number is not None:
        numbers = [item.measure.number for item in entries
                   if item.measure is not None
                   and item.measure.number is not None]
        span = max(numbers) - min(numbers) + 1
        changed("a higher value", measure=measure.model_copy(
            update={"number": measure.number + span}))
        changed("a lower value", measure=measure.model_copy(
            update={"number": measure.number - span}))
    if measure is not None:
        changed("its flag turned", measure=measure.model_copy(
            update={"flag": None if measure.flag else "abnormal"}))
    others = {}
    for item in entries:
        key = consistency.key_of(item)
        if item.kind == entry.kind and key != consistency.key_of(entry):
            others.setdefault(key, item)
    for other in list(others.values())[:OTHER_ITEMS]:
        update = {name: getattr(other, name) for name in (
            "name", "name_status", "concept_id", "code", "vocabulary",
            "detail", "group", "ancestors")}
        if entry.microbe is not None and other.microbe is not None:
            update["microbe"] = entry.microbe.model_copy(
                update={"specimen": other.microbe.specimen})
        if entry.dose is not None:
            update["dose"] = entry.dose.model_copy(update={
                "drug": other.dose.drug if other.dose else other.name})
        changed("taken for another item", **update)
    visits = [visit.ref for visit in bundle.visits
              if visit.ref != entry.visit_ref]
    if visits:
        changed("moved to another visit", visit_ref=visits[0])
    return found


def with_events(bundle, events):
    """Return a QA bundle holding other events, its coverage counted
    again."""
    return bundle.model_copy(update={
        "events": tuple(events), "coverage": bundle.coverage.model_copy(
            update={"events_in_scope": len(events),
                    "unmapped_events_in_scope": sum(
                        not event.concept.concept_id for event in events)})})


def misread_events(bundle, ref):
    """Return (label, bundle) pairs: a QA bundle with one event misread."""
    events = list(bundle.events)
    index = next(index for index, event in enumerate(events)
                 if event.ref == ref)
    event = events[index]
    found = [("left out", with_events(
        bundle, events[:index] + events[index + 1:]))]

    def changed(label, **update):
        """Add the bundle with this event changed."""
        copy = list(events)
        copy[index] = event.model_copy(update=update)
        found.append((label, with_events(bundle, copy)))

    others = {}
    for item in events:
        key = consistency.key_of(item)
        if key != consistency.key_of(event):
            others.setdefault(key, item)
    for other in list(others.values())[:OTHER_ITEMS]:
        changed("taken for another condition", concept=other.concept,
                source_concept=other.source_concept)
    visits = [visit.ref for visit in bundle.visits
              if visit.ref != event.visit_ref]
    if visits:
        changed("moved to another visit", visit_ref=visits[0])
    return found


# Samples

class RecordSample(record_mutations.Sample):
    """A released answer about the invented record, and how its question
    is read."""

    def __init__(self, question, scope):
        """Answer a question and hold its reading."""
        super().__init__(question, scope)
        self.reading = record_interpret.interpret(question, CONTEXT.terms)
        self.resolution = record_answer.resolution_of(CONTEXT, self.reading)

    def written_from(self, bundle):
        """Return the templates' answer written from another bundle, as
        an answer to this sample's request."""
        request = contracts.Request(
            schema_version="1.0", request_id=self.candidate.request_id,
            task=self.reading.task, case=cases.CASE, scope=self.scope,
            question=self.question)
        return record_answer.answer_candidate(
            CONTEXT, request, bundle, self.candidate.bundle, self.reading,
            self.resolution)

    def cited(self):
        """Return the records the answer cites or names."""
        found = set()
        for statement in self.candidate.statements:
            found.update(ref for ref in statement.refs if ref[0] == "R")
            slots = statement.slots
            found.update(ref for ref in (slots.value, slots.when) if ref)
            if slots.item is not None and slots.item.record:
                found.add(slots.item.record)
        return found


def condition_samples():
    """Return samples of condition answers of the invented case set in
    every scope, and of its explanation: lists, presence checks of a
    recorded condition and of one recorded elsewhere, and notices."""
    found = mutations.qa_samples(consistency.LABEL,
                                 consistency.SOURCE.bundle_set, VOCAB, [])
    return found + [sample for sample in mutations.synthetic_samples()
                    if sample.candidate.task == "prediction_explanation"]


def condition_outcome(sample, candidate):
    """Return the outcome of a condition sample's candidate, read as a
    writer's document."""
    document = parsed(candidate)
    if document is None:
        return "structure_invalid"
    return mutations.released(sample, document)[0]


def condition_written_from(sample, bundle):
    """Return the templates' condition answer written from another
    bundle, as an answer to this sample's request."""
    candidate = sample.candidate
    if candidate.task == "condition_list":
        return templates.list_candidate(candidate.request_id, sample.case,
                                        sample.scope, bundle,
                                        candidate.bundle)[0]
    resolution = resolve.Resolution("", "resolved", "concepts",
                                    concepts=sample.condition.concepts)
    return templates.presence_candidate(
        candidate.request_id, sample.case, sample.scope, bundle,
        candidate.bundle, resolution,
        answer.record_matches(bundle, resolution, VOCAB))


def given_as(candidate, target):
    """Return another answer given as the answer to a target's request."""
    return candidate.model_copy(update={
        "request_id": target.request_id, "case": target.case,
        "scope": target.scope, "bundle": target.bundle})


# Tests

class RecordAnswerTests(unittest.TestCase):
    """Answers about the whole record."""

    @classmethod
    def setUpClass(cls):
        """Answer every sample question once."""
        cls.samples = [RecordSample(question, scope)
                       for question, scope in record_mutations.SAMPLES]

    def expect(self, sample, candidate, label, missed):
        """Record a candidate that is not the correct answer and is not
        withheld; return 1 when it is not the correct answer. The answer
        itself must be released; one that states the same in another way
        may be either."""
        if candidate == sample.candidate:
            self.assertEqual(sample.outcome(candidate),
                             sample.response.outcome, label)
            return 0
        if canonical(candidate, sample.bundle) == canonical(
                sample.candidate, sample.bundle):
            return 0
        if sample.outcome(candidate) not in WITHHELD:
            missed.append("%s [%s]: %s" % (sample.question, sample.scope.kind,
                                           label))
        return 1

    def test_two_mistakes_at_once_are_withheld(self):
        """A one-fact mutant broken again by a second mistake."""
        chance, missed, total = random.Random(SEED), [], 0
        for sample in self.samples:
            firsts = record_mutations.mutants(sample)
            for first_label, first in chance.sample(firsts,
                                                    min(12, len(firsts))):
                try:
                    seconds = record_mutations.mutants(types.SimpleNamespace(
                        candidate=first, bundle=sample.bundle))
                except (StopIteration, IndexError, KeyError, ValueError,
                        AttributeError):
                    continue
                for label, second in chance.sample(seconds,
                                                   min(4, len(seconds))):
                    total += self.expect(sample, second, "%s, then %s" % (
                        first_label, label), missed)
        self.assertEqual(missed, [])
        self.assertGreater(total, 1500)

    def test_answers_from_a_misread_record_are_withheld(self):
        """Each record the answer cites, and five it does not, misread in
        every way that applies; the answer written from the misread
        record is checked against the real one."""
        chance, missed, total, failed = random.Random(SEED), [], 0, 0
        for sample in self.samples:
            cited = sample.cited()
            others = sorted(entry.ref for entry in sample.bundle.records
                            if entry.ref not in cited)
            for ref in sorted(cited) + chance.sample(others,
                                                     min(5, len(others))):
                for label, bundle in misread_records(sample.bundle, ref):
                    try:
                        written = sample.written_from(bundle)
                    except (ValidationError, ValueError, KeyError):
                        failed += 1
                        continue
                    total += self.expect(sample, written, "%s %s" % (
                        ref, label), missed)
        self.assertEqual(missed, [])
        self.assertGreater(total, 300)
        self.assertLess(failed, total // 10)

    def test_answers_to_other_questions_are_withheld(self):
        """The right answer to another question of the same task, or to
        the same question in another scope, given as this one's."""
        missed, total = [], 0
        for sample in self.samples:
            for other in self.samples:
                if other is not sample and (other.candidate.task
                                            == sample.candidate.task):
                    total += self.expect(sample, given_as(
                        other.candidate, sample.candidate),
                        "the answer to %r" % other.question, missed)
            for scope in cases.SCOPES:
                if scope == sample.scope:
                    continue
                found = edges.ask(sample.question, scope)
                if found is not None and found.candidate is not None and (
                        found.response.outcome in ("answer",
                                                   "not_recorded")):
                    total += self.expect(sample, given_as(
                        found.candidate, sample.candidate),
                        "its answer in %s" % scope.kind, missed)
        self.assertEqual(missed, [])
        self.assertGreater(total, 150)


class ConditionAnswerTests(unittest.TestCase):
    """Condition lists, presence checks and explanations, which the model
    writes."""

    @classmethod
    def setUpClass(cls):
        """Build the samples once."""
        cls.samples = condition_samples()

    def expect(self, sample, candidate, label, missed):
        """Record a candidate that is not the correct answer and is not
        withheld; return 1 when it is not the correct answer."""
        if canonical(candidate, sample.bundle) == canonical(
                sample.candidate, sample.bundle):
            return 0
        if condition_outcome(sample, candidate) not in WITHHELD:
            missed.append("%s %s [%s]: %s" % (
                sample.candidate.task, sample.candidate.outcome,
                sample.scope.kind, label))
        return 1

    def test_the_samples_cover_every_task(self):
        """Lists, presence checks both ways, and explanations, each
        released as written."""
        found = {(sample.candidate.task, sample.candidate.outcome)
                 for sample in self.samples if sample.bundle is not None}
        self.assertTrue({("condition_list", "answer"),
                         ("condition_presence", "answer"),
                         ("condition_presence", "not_recorded"),
                         ("prediction_explanation", "explanation")} <= found)
        for sample in self.samples:
            self.assertEqual(condition_outcome(sample, sample.candidate),
                             sample.candidate.outcome)

    def test_two_mistakes_at_once_are_withheld(self):
        """A one-fact mutant broken again by a second mistake."""
        chance, missed, total = random.Random(SEED), [], 0

        def broken(sample):
            """Return the mutants that change the candidate alone."""
            return [mutant for mutant in mutations.mutants(sample)
                    if mutant.candidate is not None and mutant.bundle is None
                    and mutant.checks is None and mutant.document is None
                    and mutant.outcome == mutations.REJECTION]

        for sample in self.samples:
            firsts = broken(sample)
            for first in chance.sample(firsts, min(12, len(firsts))):
                try:
                    seconds = broken(dataclasses.replace(
                        sample, candidate=first.candidate))
                except (StopIteration, IndexError, KeyError, ValueError,
                        AttributeError, TypeError):
                    continue
                for second in chance.sample(seconds, min(4, len(seconds))):
                    total += self.expect(sample, second.candidate, "%s, then %s"
                                         % (first.error, second.error),
                                         missed)
        self.assertEqual(missed, [])
        self.assertGreater(total, 500)

    def test_answers_from_misread_events_are_withheld(self):
        """Each event of a list or presence check's scope misread; the
        answer written from the misread bundle is checked against the
        real one."""
        missed, total = [], 0
        for sample in self.samples:
            if sample.candidate.task not in ("condition_list",
                                             "condition_presence"):
                continue
            for event in sample.bundle.events:
                for label, bundle in misread_events(sample.bundle,
                                                    event.ref):
                    total += self.expect(
                        sample, condition_written_from(sample, bundle),
                        "%s %s" % (event.ref, label), missed)
        self.assertEqual(missed, [])
        self.assertGreater(total, 20)

    def test_answers_to_other_requests_are_withheld(self):
        """The right answer about another scope or condition given as
        this one's."""
        missed, total = [], 0
        for sample in self.samples:
            if sample.bundle is None or sample.candidate.task not in (
                    "condition_list", "condition_presence"):
                continue
            for other in self.samples:
                if other is not sample and other.bundle is not None and (
                        other.candidate.task == sample.candidate.task):
                    total += self.expect(sample, given_as(
                        other.candidate, sample.candidate), "the answer of %s"
                        " %s" % (other.scope.kind, other.condition),
                        missed)
        self.assertEqual(missed, [])
        self.assertGreater(total, 20)


if __name__ == "__main__":
    unittest.main()
