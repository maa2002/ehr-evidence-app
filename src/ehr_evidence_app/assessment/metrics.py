"""Metrics of the final evaluation (work items 58 and 59; ADR 0016).

Every metric is computed from pairs of a reference and a response, one
pair per request and condition, in the plain forms below. The references
are prepared from the source records (work items 54 and 55), and the
responses are read from what each condition wrote (assessment.stated).
Nothing here reads a bundle, a candidate or a check of the runtime, so
support is judged against the reference alone (ADR 0016, decision 7).

A record is named ("record", domain, native identifier); a condition by
its standard concept, ("concept", id), or for records without one by
their source code, ("source", vocabulary, code). A response's cited
references are read as what they name: a record, ("coverage",),
("prediction",) or ("attribution", concept), or None for a reference that
names nothing.

Per request, score() returns the terms each metric sums. Per stratum and
condition, summarise() returns each metric with its numerator and
denominator; a metric whose denominator is zero is not applicable and is
reported as None, never as 0 or 1. compare() pairs the two conditions on
the same requests: Newcombe's method 10 with the exact McNemar test for a
proportion whose denominator is the same requests in both conditions,
and the paired bootstrap over cases for every other ratio, F1 included
(assessment.statistics).

The metrics, by decision of ADR 0016:

3. Lists: exact match; condition precision, recall and F1,
   micro-averaged. A condition stated twice is one true positive and one
   false positive, and a request without a list states no condition.
4. Presence checks: scoped accuracy.
5. Answerability, over A (the requests whose reference is an answer or
   "not recorded") and N (those whose reference is a clarification or an
   abstention): coverage, selective accuracy, correct-answer yield,
   appropriate abstention, over-abstention and the unsupported-answer
   rate. A list answer is correct when it is an exact match; a presence
   answer when its outcome is the reference's and it cites exactly the
   reference's records. An operational error or a validation rejection is
   neither an answer nor an abstention.
6. Explanations: preservation (the prediction's label, score and logit,
   and every selected driver with its concept, rank and score, as a
   response shows them: to three significant figures); driver precision,
   recall and F1; evidence-consistent yield; the attribution-language
   violation rate, from the rater's wording marks (ADR 0027); and, beside
   them, the history and context lines stated right over those the
   reference has.
7. Evidence: citation resolution; claim support over the factual
   statements (fixed notices state nothing of the record); the
   unsupported-claim rate over the responses with text; and record
   precision, recall and F1 of the records cited for each condition and
   each driver's history and context. A disclosure the reference
   requires counts as a statement: records without a standard concept in
   an answer's scope, each of an explanation's case restrictions, and the
   drivers not described. A disclosure that is left out is an unsupported
   statement, since by leaving it out the response states that there is
   none.

Records without a standard concept that a presence check may match by
their ICD description have no independent reference. They are unjudged:
left out of every count. A presence answer that rests on them alone, for
a request whose reference has no other match, is counted apart from every
QA metric.

The default mode checks the metrics on examples worked by hand.

Usage:
    python -m ehr_evidence_app.assessment.metrics
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import sys
import typing

from ehr_evidence_app.assessment import statistics

ANSWERS = ("answer", "not_recorded")
DECLINES = ("clarification", "abstention")
TEXT_OUTCOMES = ("answer", "not_recorded", "explanation", "clarification",
                 "abstention")
QA_TASKS = ("condition_list", "condition_presence")
EXPLANATION = "prediction_explanation"
ITEMS = ("list_item", "presence_item")
# Notices that state a fact of the record; every other notice is a fixed
# text and states none.
OMITTED_NOTICE = "drivers_omitted"
RESTRICTION_PREFIX = "restriction:"
# The reason codes that disclose something of the evidence.
UNMAPPED_CODE = "unmapped_records_in_scope"
RESTRICTIONS = ("batch_sensitive_prediction",
                "target_attached_drivers_present",
                "target_outcome_entries_excluded")


@dataclasses.dataclass(frozen=True)
class Statement:
    """One statement of a response, as the metrics read it.

    kind is the candidate's statement kind. cited holds the references it
    cites, each read as what it names (None for one that names nothing).
    The other fields are its facts: a condition's name, a count, visit
    numbers, a match kind, summary figures, a number of records without a
    standard concept, a prediction's label, score and logit, a driver's
    concept, rank and score, or a notice's identifier (a case's
    restriction as "restriction:" and its name).
    """

    kind: str
    cited: tuple = ()
    name: tuple | None = None
    count: int | None = None
    visits: tuple | None = None
    match: str | None = None
    totals: tuple | None = None
    unmapped: int | None = None
    label: int | None = None
    score: float | None = None
    logit: float | None = None
    driver: int | None = None
    rank: int | None = None
    notice: str | None = None


@dataclasses.dataclass(frozen=True)
class Response:
    """What one condition gave for a request: its outcome, its statements,
    whether it has text, the rater's wording mark (True a pass, False a
    fail, None not rated) and its reason codes."""

    request_id: str
    outcome: str
    statements: tuple = ()
    has_text: bool = False
    wording: bool | None = None
    reason_codes: tuple = ()


@dataclasses.dataclass(frozen=True)
class Driver:
    """A selected driver as the reference has it: its concept, rank and
    score, and its records in pre-target visits (history) and in the
    final admission (context)."""

    concept: int
    rank: int
    score: float
    history: frozenset = frozenset()
    context: frozenset = frozenset()


@dataclasses.dataclass(frozen=True)
class Reference:
    """The expected response of a request, prepared from the source records.

    case identifies the request's case (case set, number), which the
    bootstrap resamples. outcome is the expected outcome. For a QA
    request: groups maps each expected condition to its records (a list:
    every condition of the scope; a presence check: those that match);
    matches maps each matching record to its match kind; names maps each
    record of the scope to its condition and visits to its visit number;
    unmapped counts the scope's records without a standard concept, and
    unjudged holds those a presence check cannot judge. citable holds
    every reference a response may cite. For an explanation: the
    prediction (label, score, logit), the selected drivers, the number of
    eligible drivers not described and the case's restrictions.
    source_answerable and runtime_sufficient record whether the complete
    sources and the runtime's evidence can answer the request.
    """

    request_id: str
    case: tuple
    task: str
    stratum: str
    outcome: str
    groups: dict = dataclasses.field(default_factory=dict)
    matches: dict = dataclasses.field(default_factory=dict)
    names: dict = dataclasses.field(default_factory=dict)
    visits: dict = dataclasses.field(default_factory=dict)
    unmapped: int = 0
    unjudged: frozenset = frozenset()
    citable: frozenset = frozenset()
    prediction: tuple | None = None
    drivers: tuple = ()
    omitted: int = 0
    restrictions: frozenset = frozenset()
    source_answerable: bool = True
    runtime_sufficient: bool = True


class Scored(typing.NamedTuple):
    """The terms one request's response contributes to the metrics."""

    request_id: str
    case: tuple
    task: str
    stratum: str
    terms: collections.Counter


class Metric(typing.NamedTuple):
    """A metric: its name, the decision of ADR 0016 that defines it, its
    form (a proportion over the same requests, a ratio of sums, or a
    precision, recall or F1 of summed true positives, false positives and
    false negatives) and the terms it is computed from."""

    name: str
    decision: int
    form: str
    terms: tuple


METRICS = (
    Metric("exact_match", 3, "proportion", ("exact", "list_answerable")),
    Metric("condition_precision", 3, "precision", ("condition_tp",
                                                   "condition_fp")),
    Metric("condition_recall", 3, "recall", ("condition_tp",
                                             "condition_fn")),
    Metric("condition_f1", 3, "f1", ("condition_tp", "condition_fp",
                                     "condition_fn")),
    Metric("scoped_accuracy", 4, "proportion", ("presence_right",
                                                "presence_judged")),
    Metric("coverage", 5, "proportion", ("answered_a", "in_a")),
    Metric("selective_accuracy", 5, "ratio", ("correct", "answered_a")),
    Metric("correct_answer_yield", 5, "proportion", ("correct", "in_a")),
    Metric("appropriate_abstention", 5, "proportion", ("appropriate",
                                                       "in_n")),
    Metric("over_abstention", 5, "proportion", ("declined_a", "in_a")),
    Metric("unsupported_answer_rate", 5, "ratio", ("unsupported_answer",
                                                   "answered")),
    Metric("preservation", 6, "ratio", ("preserved", "explanations")),
    Metric("driver_precision", 6, "precision", ("driver_tp", "driver_fp")),
    Metric("driver_recall", 6, "recall", ("driver_tp", "driver_fn")),
    Metric("driver_f1", 6, "f1", ("driver_tp", "driver_fp", "driver_fn")),
    Metric("evidence_consistent_yield", 6, "proportion", (
        "consistent", "explanation_expected")),
    Metric("attribution_language_violation_rate", 6, "ratio", (
        "violations", "rated_explanations")),
    Metric("line_completeness", 6, "ratio", ("lines_right",
                                             "lines_expected")),
    Metric("citation_resolution", 7, "ratio", ("resolved", "cited")),
    Metric("claim_support", 7, "ratio", ("supported", "claims")),
    Metric("unsupported_claim_rate", 7, "ratio", ("unsupported_response",
                                                  "with_text")),
    Metric("record_precision", 7, "precision", ("record_tp", "record_fp")),
    Metric("record_recall", 7, "recall", ("record_tp", "record_fn")),
    Metric("record_f1", 7, "f1", ("record_tp", "record_fp", "record_fn")),
)
BY_NAME = {metric.name: metric for metric in METRICS}


# Reading A Statement Against The Reference

def shown(value):
    """Return a score or logit as a response shows it: to three
    significant figures."""
    return "%.3g" % value


def shown_equal(stated, value):
    """Return True when a stated score or logit reads as the reference's
    value."""
    return stated is not None and shown(stated) == shown(value)


def cited_records(statement):
    """Return (the records a statement cites, how many of its references
    name nothing)."""
    records = frozenset(ref for ref in statement.cited
                        if ref is not None and ref[0] == "record")
    return records, sum(ref is None for ref in statement.cited)


def visits_of(reference, records):
    """Return the visit numbers of records, ascending, each once."""
    return tuple(sorted({reference.visits[key] for key in records
                         if key in reference.visits}))


def item_judged(statement, reference):
    """Return False for a presence item that cites only unjudged records."""
    records, unresolved = cited_records(statement)
    return not (statement.kind == "presence_item" and records
                and not unresolved and records <= reference.unjudged)


def item_supported(statement, reference):
    """Return True when a list or presence item states one condition of
    the scope with exactly the records it cites: each cited reference
    names a record of that condition, its count is their number, its
    visits theirs and, in a presence check, its match kind theirs."""
    records, unresolved = cited_records(statement)
    if unresolved or not records or len(records) != len(statement.cited):
        return False
    if any(reference.names.get(key) != statement.name for key in records):
        return False
    if statement.count != len(records) or statement.visits != visits_of(
            reference, records):
        return False
    if statement.kind == "presence_item":
        return {reference.matches.get(key) for key in records} == {
            statement.match}
    return True


def judged_matches(reference):
    """Return the records a presence check's reference matches."""
    return frozenset(key for records in reference.groups.values()
                     for key in records)


def line_of(statement, reference):
    """Return the reference driver a history or context statement names,
    or None."""
    return next((driver for driver in reference.drivers
                 if driver.concept == statement.driver), None)


def line_records(driver, kind):
    """Return a reference driver's history or context records."""
    return driver.history if kind == "driver_history" else driver.context


def line_supported(statement, reference):
    """Return True when a history or context line cites exactly its
    driver's records, states their number and, for history, their
    visits."""
    driver = line_of(statement, reference)
    records, unresolved = cited_records(statement)
    if driver is None or unresolved or len(records) != len(statement.cited):
        return False
    expected = line_records(driver, statement.kind)
    if not expected or records != expected:
        return False
    if statement.count != len(records):
        return False
    return statement.kind != "driver_history" or (
        statement.visits == visits_of(reference, records))


def supported(statement, reference, unjudged_cited=frozenset()):
    """Return True, False, or None for a statement that states no fact of
    the record (a fixed notice) or cannot be judged: a presence item
    citing only unjudged records, or a presence summary that counts them
    (unjudged_cited holds the unjudged records the response's items
    cite)."""
    kind = statement.kind
    if kind in ITEMS:
        if not item_judged(statement, reference):
            return None
        return item_supported(statement, reference)
    if kind == "list_summary":
        return statement.totals == (len(reference.groups),
                                    len(reference.names))
    if kind == "presence_summary":
        if unjudged_cited:
            return None
        return statement.totals == (len(judged_matches(reference)),)
    if kind in ("list_unmapped", "presence_unmapped"):
        return reference.unmapped > 0 and statement.unmapped == (
            reference.unmapped)
    if kind == "list_empty":
        return not reference.names
    if kind == "presence_absent":
        return not judged_matches(reference)
    if kind == "prediction":
        label, score, logit = reference.prediction or (None, 0.0, 0.0)
        return (reference.prediction is not None
                and statement.label == label
                and shown_equal(statement.score, score)
                and shown_equal(statement.logit, logit))
    if kind == "driver":
        return any(driver.rank == statement.rank
                   and driver.concept == statement.driver
                   and shown_equal(statement.score, driver.score)
                   for driver in reference.drivers)
    if kind in ("driver_history", "driver_context"):
        return line_supported(statement, reference)
    if kind == "notice" and statement.notice == OMITTED_NOTICE:
        return reference.omitted > 0 and statement.count == (
            reference.omitted)
    if kind == "notice" and (statement.notice or "").startswith(
            RESTRICTION_PREFIX):
        return statement.notice[len(RESTRICTION_PREFIX):] in (
            reference.restrictions)
    if kind == "notice":
        return None
    return False


def disclosure_verdicts(reference, response):
    """Return the verdicts of a response's disclosures beyond its
    statements.

    A disclosure the reference requires and the response leaves out is an
    unsupported claim: by leaving it out, the response states that there
    is none. An answer about a scope with records without a standard
    concept carries the reason code unmapped_records_in_scope, and a list
    answer or a presence check's "not recorded" also the line that counts
    them. An explanation carries the notice and the reason code of each of
    its case's restrictions, and the notice of the drivers not described
    when any are. A restriction or unmapped reason code is a claim of its
    own, supported when the reference has what it states.
    """
    verdicts = []
    statements = response.statements
    if reference.task in QA_TASKS and response.outcome in ANSWERS:
        if UNMAPPED_CODE in response.reason_codes:
            verdicts.append(reference.unmapped > 0)
        elif reference.unmapped > 0:
            verdicts.append(False)
        lined = (reference.task, response.outcome) in (
            ("condition_list", "answer"),
            ("condition_presence", "not_recorded"))
        if lined and reference.unmapped > 0 and not any(
                statement.kind in ("list_unmapped", "presence_unmapped")
                for statement in statements):
            verdicts.append(False)
    if reference.task == EXPLANATION and response.outcome == "explanation":
        noticed = {statement.notice[len(RESTRICTION_PREFIX):]
                   for statement in statements
                   if statement.kind == "notice" and (
                       statement.notice or "").startswith(
                           RESTRICTION_PREFIX)}
        coded = {code for code in response.reason_codes
                 if code in RESTRICTIONS}
        verdicts += [False] * len(reference.restrictions - noticed)
        verdicts += [False] * len(reference.restrictions - coded)
        verdicts += [code in reference.restrictions for code in sorted(coded)]
        if reference.omitted > 0 and not any(
                statement.kind == "notice"
                and statement.notice == OMITTED_NOTICE
                for statement in statements):
            verdicts.append(False)
    return verdicts


# Terms Of One Request

def counted_apart(reference, response):
    """Return True for a presence answer resting only on unjudged records
    where the reference has no other match."""
    if reference.task != "condition_presence" or response.outcome != (
            "answer") or judged_matches(reference):
        return False
    cited = set()
    for statement in response.statements:
        if statement.kind == "presence_item":
            cited |= cited_records(statement)[0]
    return bool(cited) and cited <= reference.unjudged


def condition_terms(reference, response, terms):
    """Add the condition and record terms of a list or presence check: a
    stated condition is a true positive once and a false positive when
    repeated or not the reference's; its cited records are true positives
    when they are the reference's for it."""
    items = ([statement for statement in response.statements
              if statement.kind in ITEMS
              and item_judged(statement, reference)]
             if response.outcome == "answer" else [])
    seen = {}
    for statement in items:
        records, unresolved = cited_records(statement)
        records = records - reference.unjudged
        expected = reference.groups.get(statement.name)
        if expected is None or statement.name in seen:
            terms["condition_fp"] += 1
            terms["record_fp"] += len(records) + unresolved
            continue
        terms["condition_tp"] += 1
        seen[statement.name] = records
        terms["record_tp"] += len(records & expected)
        terms["record_fp"] += len(records - expected) + unresolved
    for name, expected in reference.groups.items():
        if name not in seen:
            terms["condition_fn"] += 1
        terms["record_fn"] += len(expected - seen.get(name, frozenset()))
    return items, seen


def exact(reference, response, items, seen):
    """Return True when a list or presence answer states exactly the
    reference's conditions, once each, each citing exactly its records."""
    if reference.outcome == "not_recorded":
        return response.outcome == "not_recorded"
    if response.outcome != "answer" or len(items) != len(seen):
        return False
    if set(seen) != set(reference.groups):
        return False
    return all(cited_records(statement)[1] == 0 for statement in items) \
        and all(seen[name] == records
                for name, records in reference.groups.items())


def qa_terms(reference, response, terms):
    """Add the list, presence and answerability terms of a QA request."""
    items, seen = condition_terms(reference, response, terms)
    right = exact(reference, response, items, seen)
    if reference.task == "condition_list" and reference.outcome in ANSWERS:
        terms["list_answerable"] += 1
        terms["exact"] += right
    if reference.task == "condition_presence":
        terms["presence_judged"] += 1
        terms["presence_right"] += response.outcome == reference.outcome
    if reference.outcome in ANSWERS:
        terms["in_a"] += 1
        terms["answered_a"] += response.outcome in ANSWERS
        terms["declined_a"] += response.outcome in DECLINES
        terms["correct"] += right
    if reference.outcome in DECLINES:
        terms["in_n"] += 1
        terms["appropriate"] += response.outcome == reference.outcome


def explanation_terms(reference, response, terms, verdicts):
    """Add the driver, record and explanation terms of an explanation
    request. A request without an explanation states no driver and no
    record, so the reference's are false negatives; a driver or line
    stated twice is a false positive the second time."""
    given = response.outcome == "explanation"
    statements = response.statements if given else ()
    stated = [statement for statement in statements
              if statement.kind == "driver"]
    expected = {driver.concept for driver in reference.drivers}
    seen = set()
    for statement in stated:
        if statement.driver in expected and statement.driver not in seen:
            terms["driver_tp"] += 1
            seen.add(statement.driver)
        else:
            terms["driver_fp"] += 1
    terms["driver_fn"] += len(expected - seen)
    lines = {}
    for statement in statements:
        if statement.kind not in ("driver_history", "driver_context"):
            continue
        records, unresolved = cited_records(statement)
        driver = line_of(statement, reference)
        key = (statement.kind, statement.driver)
        line = (frozenset() if driver is None or key in lines
                else line_records(driver, statement.kind))
        lines.setdefault(key, records)
        terms["record_tp"] += len(records & line)
        terms["record_fp"] += len(records - line) + unresolved
    for driver in reference.drivers:
        for kind in ("driver_history", "driver_context"):
            terms["record_fn"] += len(line_records(driver, kind) - lines.get(
                (kind, driver.concept), frozenset()))
    if reference.outcome == "explanation":
        terms["explanation_expected"] += 1
        terms["consistent"] += given and all(
            verdict is not False for verdict in verdicts) and any(
            verdict for verdict in verdicts)
    if not given:
        return
    terms["explanations"] += 1
    predictions = [statement for statement in statements
                   if statement.kind == "prediction"]
    ranked = [(statement.driver, statement.rank, shown(statement.score))
              for statement in stated if statement.score is not None]
    terms["preserved"] += (
        reference.prediction is not None and len(predictions) == 1
        and supported(predictions[0], reference) is True
        and len(ranked) == len(stated)
        and ranked == [(driver.concept, driver.rank, shown(driver.score))
                       for driver in reference.drivers])
    for driver in reference.drivers:
        for kind in ("driver_history", "driver_context"):
            if not line_records(driver, kind):
                continue
            terms["lines_expected"] += 1
            terms["lines_right"] += any(
                statement.kind == kind and statement.driver == driver.concept
                and line_supported(statement, reference)
                for statement in statements)
    if response.wording is not None:
        terms["rated_explanations"] += 1
        terms["violations"] += response.wording is False


def score(reference, response):
    """Return the Scored terms of one request's response."""
    if reference.request_id != response.request_id:
        raise ValueError("the reference and the response are of different "
                         "requests")
    terms = collections.Counter()
    if counted_apart(reference, response):
        terms["apart"] += 1
        return Scored(reference.request_id, reference.case, reference.task,
                      reference.stratum, terms)
    unjudged_cited = frozenset().union(*(
        cited_records(statement)[0] & reference.unjudged
        for statement in response.statements
        if statement.kind == "presence_item"))
    verdicts = [supported(statement, reference, unjudged_cited)
                for statement in response.statements] + disclosure_verdicts(
        reference, response)
    judged = [verdict for verdict in verdicts if verdict is not None]
    for statement in response.statements:
        for ref in statement.cited:
            terms["cited"] += 1
            terms["resolved"] += ref is not None and ref in reference.citable
    terms["claims"] += len(judged)
    terms["supported"] += sum(judged)
    unsupported = not all(judged)
    if response.has_text:
        terms["with_text"] += 1
        terms["unsupported_response"] += unsupported
    if response.outcome in ANSWERS and reference.task in QA_TASKS:
        terms["answered"] += 1
        terms["unsupported_answer"] += unsupported
    if reference.task in QA_TASKS:
        qa_terms(reference, response, terms)
    elif reference.task == EXPLANATION:
        explanation_terms(reference, response, terms, verdicts)
    return Scored(reference.request_id, reference.case, reference.task,
                  reference.stratum, terms)


# Summaries And The Paired Comparison

def ratio(numerator, denominator):
    """Return numerator over denominator, or None when it is zero."""
    return numerator / denominator if denominator else None


def measure(form, values):
    """Return a metric's value from its summed terms, or None when it is
    not applicable: F1 is 0 when precision and recall are both 0, and not
    applicable when either is."""
    if form in ("proportion", "ratio"):
        return ratio(*values)
    if form == "precision":
        true, false = values
        return ratio(true, true + false)
    if form == "recall":
        true, missed = values
        return ratio(true, true + missed)
    true, false, missed = values
    precision = ratio(true, true + false)
    recall = ratio(true, true + missed)
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def summed(rows, metric):
    """Return a metric's terms summed over scored rows."""
    return tuple(sum(row.terms[term] for row in rows)
                 for term in metric.terms)


def summarise(rows):
    """Return {metric: (summed terms, value)} over scored rows, and the
    number counted apart."""
    found = {metric.name: (summed(rows, metric),
                           measure(metric.form, summed(rows, metric)))
             for metric in METRICS}
    return found, sum(row.terms["apart"] for row in rows)


def paired_rows(baseline, controlled):
    """Return the scored rows of both conditions, paired by request; the
    requests must be the same."""
    first = {row.request_id: row for row in baseline}
    second = {row.request_id: row for row in controlled}
    if set(first) != set(second) or len(first) != len(baseline) or len(
            second) != len(controlled):
        raise ValueError("the conditions are not paired")
    return [(first[key], second[key]) for key in sorted(first)]


def compare(baseline, controlled, metric):
    """Return the paired comparison of one metric, controlled minus
    baseline: a statistics.Paired for a proportion over the same requests
    (the requests in its denominator in both conditions), or a
    statistics.Bootstrapped otherwise; None when nothing is compared."""
    pairs = paired_rows(baseline, controlled)
    if metric.form == "proportion":
        numerator, denominator = metric.terms
        return statistics.compare_proportions([
            (one.terms[numerator] > 0, two.terms[numerator] > 0)
            for one, two in pairs
            if one.terms[denominator] and two.terms[denominator]])
    cases = collections.defaultdict(lambda: ([0] * len(metric.terms),
                                             [0] * len(metric.terms)))
    for one, two in pairs:
        sums = cases[one.case]
        for index, term in enumerate(metric.terms):
            sums[0][index] += one.terms[term]
            sums[1][index] += two.terms[term]
    found = statistics.bootstrap_difference(
        [cases[key] for key in sorted(cases)],
        lambda values: measure(metric.form, values))
    return None if found.difference is None else found


# Default Mode: Examples Worked By Hand

def record(number):
    """Return an invented condition record's key."""
    return ("record", "condition", number)


A, B, X = ("concept", 101), ("concept", 102), ("source", "ICD10CM", "Z99")


def list_reference(request_id="q1", case=("s", 1)):
    """Return an invented list reference: condition A with records 1 and 2
    (visits 1 and 2), B with record 3 (visit 2), and record 4 without a
    standard concept (visit 1)."""
    names = {record(1): A, record(2): A, record(3): B, record(4): X}
    return Reference(
        request_id, case, "condition_list", "list", "answer",
        groups={A: frozenset((record(1), record(2))),
                B: frozenset((record(3),)), X: frozenset((record(4),))},
        names=names, visits={record(1): 1, record(2): 2, record(3): 2,
                             record(4): 1},
        unmapped=1, citable=frozenset(names) | {("coverage",)})


def list_item(name, numbers, visits, count=None, cited=None):
    """Return an invented list item."""
    return Statement("list_item", cited=tuple(
        record(number) for number in numbers) if cited is None else cited,
        name=name, count=len(numbers) if count is None else count,
        visits=visits)


def perfect_list(request_id="q1"):
    """Return the list response the reference expects."""
    return Response(request_id, "answer", (
        list_item(A, (1, 2), (1, 2)), list_item(B, (3,), (2,)),
        list_item(X, (4,), (1,)),
        Statement("list_summary", totals=(3, 4)),
        Statement("list_unmapped", cited=(("coverage",),), unmapped=1),
        Statement("notice", notice="list_complete")), has_text=True,
        reason_codes=(UNMAPPED_CODE,))


def run_synthetic():
    """Return the problems found on the examples worked by hand."""
    problems = []
    reference = list_reference()
    found = score(reference, perfect_list()).terms
    want = {"exact": 1, "list_answerable": 1, "condition_tp": 3,
            "record_tp": 4, "claims": 6, "supported": 6, "cited": 5,
            "resolved": 5, "correct": 1, "in_a": 1, "answered_a": 1,
            "answered": 1, "with_text": 1}
    if {key: value for key, value in found.items() if value} != want:
        problems.append("a perfect list is not scored as one")
    # A stated twice (first with a record of B), B miscounted, X left out,
    # and a condition citing a reference to nothing. Conditions: A and B
    # true, the repeated A and the invented one false, X missed. Records:
    # 1 and 3 true, 3 (under A), 2 (under the repeated A) and the
    # reference to nothing false, 2 and 4 missed. Of the 4 items only the
    # repeated A's own line is true: 1 record, in visit 2. The record
    # without a standard concept is disclosed neither by reason code nor
    # by line: 2 more unsupported statements.
    wrong = Response("q1", "answer", (
        list_item(A, (1, 3), (1, 2)), list_item(A, (2,), (2,)),
        list_item(B, (3,), (2,), count=2),
        list_item(("concept", 999), (), (), cited=(None,), count=1)),
        has_text=True)
    found = score(reference, wrong).terms
    if (found["condition_tp"], found["condition_fp"], found["condition_fn"],
            found["record_tp"], found["record_fp"], found["record_fn"],
            found["exact"], found["claims"], found["supported"],
            found["cited"], found["resolved"]) != (2, 2, 1, 2, 3, 2, 0, 6,
                                                   1, 5, 4):
        problems.append("a wrong list is miscounted")
    if measure("f1", (0, 0, 0)) is not None or measure("f1", (0, 2, 3)) != (
            0.0) or measure("ratio", (0, 0)) is not None:
        problems.append("an empty set is not handled as ADR 0016 sets out")
    return problems


def main(argv=None):
    """Run the default mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    problems = run_synthetic()
    for problem in problems:
        print("FAILED: %s" % problem)
    if problems:
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
