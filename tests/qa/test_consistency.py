"""Consistency of responses, tested without an answer key.

Two properties need no knowledge of the right answer, only of how
responses relate to each other:

* the same question written differently (in capitals or small letters,
  with other punctuation or spacing, a typographic apostrophe, or a
  polite opening such as "please" or "can you tell me") gets the same
  response;
* a larger scope never finds less: what is recorded in a visit or the
  history is recorded in all visits; a list of a larger scope holds every
  item of a smaller one's; a count is never smaller, a first time never
  later, a last time never earlier, a highest value never lower and a
  lowest never higher; and a visit's or the person's details are the same
  in every scope that holds them.

Both are checked end to end on the invented case set (conditions and the
whole record, as the service answers them) and on the invented record of
tests.qa.record_cases. Every person, record and value is invented.
"""

import unittest

from ehr_evidence_app.api import service as services
from ehr_evidence_app.evidence import record
from ehr_evidence_app.qa import answer
from tests.qa import record_cases as cases
from tests.qa import test_record_edges as edges

# The edge questions that are noise rather than questions, and conditions.
NOISE = ("<script>alert(1)</script> labs", "<b>labs</b>",
         "'; DROP TABLE labs;--", "¿Potasio?", "potassium " * 40, "", "   ",
         "???")
CONDITIONS = ("Which conditions are recorded?", "What diagnoses are recorded?",
              "Is synthetic concept 1001 recorded?",
              "Is synthetic concept 1002 recorded?",
              "Has the patient had synthetic concept 1003?",
              "Is heart failure recorded?", "Is diabetes recorded?")
QUESTIONS = tuple(dict.fromkeys(
    CONDITIONS + tuple(question for question in edges.QUESTIONS
                       if question not in NOISE)))
PLAIN = str.maketrans({"’": "'", "‘": "'", "“": '"',
                       "”": '"', " ": " ", "\t": " "})
SERVICE = services.Service.invented()
LABEL = SERVICE.case_sets()[0]["case_set"]
SOURCE = SERVICE.sources[LABEL]


def variants(question):
    """Return (label, variant) pairs of a question written differently,
    each with the same meaning."""
    stem = question.rstrip("?!. ")
    found = (("small letters", question.lower()),
             ("capitals", question.upper()),
             ("no question mark", stem),
             ("two question marks", stem + "??"),
             ("full stop", stem + "."),
             ("more spaces", "  %s  " % question.replace(" ", "  ")),
             ("a tab", question.replace(" ", "\t", 1)),
             ("a non-breaking space", question.replace(" ", " ", 1)),
             ("typographic apostrophe", question.replace("'", "’")),
             ("please first", "Please, " + question),
             ("please last", stem + ", please?"),
             ("can you tell me", "Can you tell me " + question),
             ("I'd like to know", "I'd like to know " + stem))
    return [(label, text) for label, text in found
            if text != question and stem]


def signature(response):
    """Return what must not change when a question is written
    differently: the outcome, task and reason codes, the claims'
    references, and the text in small letters with plain spacing and
    quotation marks."""
    text = " ".join((response.text or "").translate(PLAIN).casefold()
                    .split())
    return (response.outcome, response.task, response.reason_codes,
            tuple(claim.refs for claim in response.claims), text)


def service_result(question, scope, case_number=1, source=SOURCE,
                   service=SERVICE):
    """Return the Result of a question as the service answers it, without
    a model and without a runtime record."""
    return answer.result_of(question, source.label, case_number, scope,
                            source.bundle_set, service.vocabulary, "q-1",
                            service.limits, records=source.records)


def bundle_of(result, source, case_number):
    """Return the bundle a result answered from: its record bundle for a
    question about the record, its QA bundle for one about conditions."""
    scope = result.response.scope
    if result.response.task in answer_tasks("record"):
        return source.records.records.bundle(case_number, scope)[0]
    return source.bundle_set.qa_bundle(case_number, scope)[0]


def answer_tasks(family):
    """Return the tasks of answers about the record or about
    conditions."""
    tasks = ("record_list", "record_presence", "record_timing",
             "record_count", "record_values", "record_abnormal",
             "visit_details", "person_details")
    return tasks if family == "record" else ("condition_list",
                                             "condition_presence")


def key_of(entry):
    """Return what a cited record is of, the same in every scope."""
    if isinstance(entry, record.Entry):
        if entry.kind == "icu_stay":
            # Each stay is its own item, whose reference differs by scope.
            return ("icu_stay", entry.native_id)
        return record.item_key(entry)
    if entry.concept.concept_id:
        return ("condition", entry.concept.concept_id)
    source = entry.source_concept
    return ("condition", "code",
            source.code if source else entry.concept.source_code)


def item_of(slots, entries):
    """Return the key of the item a statement names."""
    item = slots.item
    if item is None:
        return None
    if item.record is not None:
        return key_of(entries[item.record])
    return ("terms",) + tuple(item.terms)


def number_of(entry):
    """Return a record's value as a number, or None."""
    measure = getattr(entry, "measure", None)
    return None if measure is None else measure.number


class Facts:
    """What an answer states that a larger scope may not contradict."""

    def __init__(self, result, bundle):
        """Read the facts of a released answer against its bundle."""
        self.outcome = result.response.outcome
        self.task = result.response.task
        entries = {entry.ref: entry for entry in getattr(
            bundle, "records", None) or getattr(bundle, "events", ())}
        visits = {visit.ref: visit.visit_number for visit in bundle.visits}
        self.items, self.counts, self.selected = set(), {}, {}
        self.matches = self.kind_records = None
        for statement in result.candidate.statements:
            slots, kind = statement.slots, statement.kind
            if kind in ("list_item", "presence_item", "record_list_item",
                        "record_presence_item", "record_stay_item"):
                self.items.update(key_of(entries[ref])
                                  for ref in statement.refs if ref in entries)
            elif kind in ("record_count", "record_value_group"):
                self.counts[(kind, item_of(slots, entries))] = slots.count
            elif kind == "record_count_kind":
                self.kind_records = slots.records
            elif kind == "record_abnormal_summary":
                self.matches = slots.matches
            elif kind in ("record_time", "record_value"):
                ref = slots.when if kind == "record_time" else slots.value
                entry = entries[ref]
                self.selected[(item_of(slots, entries), slots.selector)] = (
                    entry.start, number_of(entry))
        # Details: claims naming the person alone, or one visit (by its
        # number, which is the same in every scope).
        self.details = set()
        if self.task in ("visit_details", "person_details"):
            for claim in result.response.claims:
                if claim.refs == ("D1",):
                    self.details.add(("person", claim.text))
                elif len(claim.refs) == 1 and claim.refs[0] in visits:
                    self.details.add((visits[claim.refs[0]], claim.text))


def contradictions(smaller, larger):
    """Return how a larger scope's facts contradict a smaller one's."""
    found = []
    if smaller.task != larger.task:
        return found
    if smaller.outcome == "answer" and larger.outcome == "not_recorded":
        found.append("recorded in the smaller scope only")
    if not smaller.items <= larger.items:
        found.append("items of the smaller scope missing")
    for key, count in smaller.counts.items():
        if key in larger.counts and larger.counts[key] < count:
            found.append("a smaller count: %s" % key[0])
    if (smaller.kind_records or 0) > (larger.kind_records or 0):
        found.append("fewer records of the kind")
    if (smaller.matches or 0) > (larger.matches or 0):
        found.append("fewer flagged results")
    for (item, selector), (start, number) in smaller.selected.items():
        for (other, chosen), (then, value) in larger.selected.items():
            if other != item:
                continue
            bound = {"first": "first", "latest": "last", "only": "both",
                     "highest": "high", "lowest": "low"}
            mine, theirs = bound[selector], bound[chosen]
            if mine in ("first", "both") and theirs in ("first", "both") \
                    and start and then and then > start:
                found.append("a later first time")
            if mine in ("last", "both") and theirs in ("last", "both") \
                    and start and then and then < start:
                found.append("an earlier last time")
            if selector in ("highest", "only") and chosen in (
                    "highest", "only") and None not in (number, value) \
                    and value < number:
                found.append("a lower highest value")
            if selector in ("lowest", "only") and chosen in (
                    "lowest", "only") and None not in (number, value) \
                    and value > number:
                found.append("a higher lowest value")
    if not smaller.details <= larger.details:
        found.append("details that differ")
    return found


def nested(bundles):
    """Return the (smaller, larger) pairs of scopes whose visits nest."""
    numbers = {scope: {visit.visit_number for visit in bundle.visits}
               for scope, bundle in bundles.items()}
    return [(small, large) for small in numbers for large in numbers
            if numbers[small] < numbers[large]]


class WordingTests(unittest.TestCase):
    """The same question written differently gets the same response."""

    def check(self, ask, scopes):
        """Ask every question and its variants in each scope; return the
        differences found."""
        differences = []
        for scope in scopes:
            for question in QUESTIONS:
                found = ask(question, scope)
                if found is None:
                    continue
                expected = signature(found.response)
                for label, text in variants(question):
                    other = ask(text, scope)
                    if other is None or signature(other.response) != (
                            expected):
                        differences.append("%r, %s [%s]" % (
                            question, label, scope.kind))
        return differences

    def test_through_the_service(self):
        """Conditions and the whole record of the invented case set."""
        self.assertEqual(self.check(service_result, (
            SERVICE.scopes(SOURCE, 1)[0], SERVICE.scopes(SOURCE, 1)[-1])), [])

    def test_the_invented_record(self):
        """Questions about the invented record at its edges."""
        self.assertEqual(self.check(edges.ask, (cases.HISTORY, cases.ALL)),
                         [])


class ScopeTests(unittest.TestCase):
    """A larger scope never finds less."""

    def check(self, ask, bundles, facts_of):
        """Ask every question in every scope; return the contradictions
        between each pair of nested scopes, and how many were compared."""
        found, compared = [], 0
        for question in QUESTIONS:
            facts = {}
            for scope in bundles:
                result = ask(question, scope)
                if result is not None and result.response.outcome in (
                        "answer", "not_recorded"):
                    facts[scope] = facts_of(result)
            for small, large in nested(bundles):
                if small in facts and large in facts:
                    compared += 1
                    found += ["%r %s < %s: %s" % (
                        question, small.kind, large.kind, problem)
                        for problem in contradictions(facts[small],
                                                      facts[large])]
        return found, compared

    def test_through_the_service(self):
        """Conditions and the whole record of the invented case set."""
        scopes = SERVICE.scopes(SOURCE, 1)
        bundles = {scope: SOURCE.bundle_set.qa_bundle(1, scope)[0]
                   for scope in scopes}
        found, compared = self.check(
            service_result, bundles,
            lambda result: Facts(result, bundle_of(result, SOURCE, 1)))
        self.assertEqual(found, [])
        self.assertGreater(compared, 100)

    def test_the_invented_record(self):
        """The invented record at its edges, in its five scopes."""
        bundles = {scope: edges.CONTEXT.records.bundle(1, scope)[0]
                   for scope in cases.SCOPES}
        found, compared = self.check(
            edges.ask, bundles,
            lambda result: Facts(result, edges.CONTEXT.records.bundle(
                1, result.response.scope)[0]))
        self.assertEqual(found, [])
        self.assertGreater(compared, 300)

    def test_a_contradiction_is_found(self):
        """The comparison itself finds a larger scope that finds less:
        facts of all visits given as a smaller scope's."""
        def facts(question, scope):
            """Return the facts of a question's answer in a scope."""
            return Facts(edges.ask(question, scope),
                         edges.CONTEXT.records.bundle(1, scope)[0])

        for question, larger, problem in (
                ("How many potassium results are there?", cases.HISTORY,
                 "a smaller count: record_count"),
                ("What labs were done?", cases.HISTORY,
                 "items of the smaller scope missing"),
                ("What was the last potassium?", edges.VISIT[1],
                 "an earlier last time"),
                ("What was the lowest potassium?", edges.VISIT[2],
                 "a higher lowest value")):
            self.assertIn(problem, contradictions(
                facts(question, cases.ALL), facts(question, larger)),
                question)
            self.assertEqual(contradictions(facts(question, larger),
                                            facts(question, cases.ALL)), [],
                             question)


if __name__ == "__main__":
    unittest.main()
