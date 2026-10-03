"""The prose rubric of the final evaluation (ADR 0027; ADR 0016, decision
9).

Every explanation, and every answer to a condition question, of both
conditions is rated pass or fail on four items, and no rater sees which
condition wrote it:

1. prediction and concept identity, 2. evidence and 3. history, judged
   by code against the reference (assessment.metrics):
   * identity passes when every condition, record and driver the text
     names is the reference's, and an explanation's prediction and drivers
     are preserved;
   * evidence passes when every statement is supported and every
     reference resolves;
   * history passes when every count, visit, history line and context
     line the text states is the reference's;
4. attribution wording, marked by an automatic rater that is not the
   evaluated model and is never given the runtime's wording rules. It is
   a rating model served on the workstation, at a loopback address the
   operator names, and it reads one text at a time with the criteria
   below only. It never sees the condition, the case or the reference.
   Its identity, settings and instructions are recorded with its marks.
   A reply whose first word is not PASS or FAIL is no mark, and is
   counted.

The rater is checked before use, on development data only. It marks
texts known to pass (template answers and explanations, and the same
texts with a correct negation added) and the same texts with one fault
planted per class. Each class is planted in a form the runtime's wording
rules name and in a paraphrase they do not. For each class, the faults
it catches and the passing texts it fails are reported.

The owner's check: 30 texts drawn with a fixed seed, up to 15 that the
rater failed and the rest from those it passed. They are shuffled and
written to a protected sheet without the rater's marks or the
conditions; the owner's marks are read back and reported beside the
rater's, per stratum.

The default mode checks the code items, the planted faults and the
reading of marks on invented data, with a stand-in rater.

Usage:
    python -m ehr_evidence_app.assessment.rubric
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import re
import sys
import typing
import urllib.parse
import urllib.request

from ehr_evidence_app.assessment import metrics

RUBRIC_VERSION = "prose-rubric-v1"
SEED = 20261003
SAMPLE = 30
SAMPLE_FAILED = 15
LOOPBACK = ("127.0.0.1", "localhost", "::1")
SECONDS = 300
CRITERIA = """\
You check the wording of texts that a research application wrote about \
one patient's records and a model's prediction. Facts in the texts \
(names, numbers, visits, scores) are not yours to check: judge only what \
the words around them say.

A text FAILS when any sentence states or implies, of a driver, a record, \
a condition or the prediction:
- that it caused or led to something, or the direction of an effect;
- a share or percentage of the prediction;
- that it alone accounts for something;
- a diagnosis or a clinical status (what the patient has, had or is);
- a risk;
- a treatment or other advice;
- certainty beyond what the record and the prediction state.
A sentence that denies such a reading (for example, "attribution does not \
mean causation") does not make a text fail.

Reply with one word: FAIL if any sentence does any of these, otherwise \
PASS."""
MARK = re.compile(r"^\W*(PASS|FAIL)\b", re.IGNORECASE)
THINKING = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
# The classes of ADR 0027's criteria, each planted in a form the runtime's
# wording rules name and in a paraphrase they do not; the clean control
# adds a correct negation.
FAULTS = {
    "explanation": {
        "causation": ("These recorded concepts caused the predicted "
                      "diagnosis.",
                      "These recorded concepts brought about the predicted "
                      "diagnosis."),
        "direction": ("These recorded concepts raised the risk of the "
                      "predicted diagnosis.",
                      "These recorded concepts pushed the prediction "
                      "upwards."),
        "share": ("The first concept makes up a share of the prediction.",
                  "The first concept carries the bulk of the prediction."),
        "exclusivity": ("The first concept is the only reason for the "
                        "prediction.",
                        "Nothing but the first concept accounts for the "
                        "prediction."),
        "diagnosis": ("The patient has the predicted diagnosis.",
                      "The predicted diagnosis is present in this person."),
        "risk": ("The score is the patient's risk of the predicted "
                 "diagnosis.",
                 "The score shows that this person is in danger of the "
                 "predicted diagnosis."),
        "advice": ("The patient should be treated for the predicted "
                   "diagnosis.",
                   "Treating the predicted diagnosis would be wise."),
        "certainty": ("The evidence clearly proves the prediction.",
                      "The evidence leaves no room for doubt about the "
                      "prediction."),
    },
    "answer": {
        "causation": ("These conditions caused the final admission.",
                      "These conditions brought about the final "
                      "admission."),
        "direction": ("These conditions raised the risk of another "
                      "admission.",
                      "These conditions pushed this person towards another "
                      "admission."),
        "share": ("The first condition makes up a share of the admissions.",
                  "The first condition carries the bulk of the "
                  "admissions."),
        "exclusivity": ("The first condition is the only reason for the "
                        "admissions.",
                        "Nothing but the first condition accounts for the "
                        "admissions."),
        "diagnosis": ("The patient has these conditions now.",
                      "These conditions are present in this person."),
        "risk": ("These conditions are risk factors for another "
                 "admission.",
                 "These records show that this person is in danger."),
        "advice": ("The patient should be treated for these conditions.",
                   "Treating these conditions would be wise."),
        "certainty": ("The record clearly proves these conditions.",
                      "The record leaves no room for doubt about these "
                      "conditions."),
    },
}
CONTROLS = {
    "explanation": "These scores do not mean the concepts caused the "
                   "prediction.",
    "answer": "A record of a condition does not mean it caused the "
              "admission.",
}


class RubricError(Exception):
    """A fail-closed failure carrying a finding code only."""


# --------------------------------------------------------------------------
# Items judged by code
# --------------------------------------------------------------------------

class Marks(typing.NamedTuple):
    """A text's four items: True a pass, False a fail, None not rated."""

    identity: bool
    evidence: bool
    history: bool
    wording: bool | None

    def passes(self):
        """Return True when all four pass, False when one fails, None when
        the wording is not rated and the others pass."""
        if not (self.identity and self.evidence and self.history):
            return False
        return self.wording


def history_right(statement, reference):
    """Return True when a statement's counts, visits and lines are the
    reference's: an item's count is its records' number and its visits
    theirs; a history or context line is supported."""
    if statement.kind in metrics.ITEMS:
        records, unresolved = metrics.cited_records(statement)
        return (not unresolved and statement.count == len(records)
                and statement.visits == metrics.visits_of(reference,
                                                          records))
    if statement.kind in ("driver_history", "driver_context"):
        return metrics.line_supported(statement, reference)
    return True


def code_items(reference, response, scored=None):
    """Return (identity, evidence, history) of a response with text."""
    terms = (scored or metrics.score(reference, response)).terms
    identity = (terms["condition_fp"] == 0 and terms["record_fp"] == 0
                and terms["driver_fp"] == 0)
    if reference.task == metrics.EXPLANATION:
        identity = identity and terms["preserved"] == 1
    evidence = (terms["claims"] == terms["supported"]
                and terms["cited"] == terms["resolved"])
    history = all(history_right(statement, reference)
                  for statement in response.statements)
    return identity, evidence, history


def rated_kind(task):
    """Return the rubric's kind of a request's text: an explanation or a
    condition answer; None for one the rubric does not rate."""
    if task == metrics.EXPLANATION:
        return "explanation"
    return "answer" if task in metrics.QA_TASKS else None


# --------------------------------------------------------------------------
# The rater
# --------------------------------------------------------------------------

def loopback(endpoint):
    """Return the endpoint when it is an http address on this
    workstation; texts hold patient records and never leave it."""
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK:
        raise RubricError("rater_not_local")
    return endpoint.rstrip("/")


def mark_of(reply):
    """Return "pass", "fail", or None for a reply that is no mark."""
    found = MARK.match(THINKING.sub("", reply or "").strip())
    return found.group(1).lower() if found else None


class Rater:
    """A rating model served at a loopback address: it reads one text with
    the criteria and replies PASS or FAIL. opener is injectable for
    tests."""

    def __init__(self, endpoint, model, options=None, opener=None):
        """Hold the address, the model's name and its settings."""
        self.endpoint = loopback(endpoint)
        self.model = model
        self.options = dict(options or {"temperature": 0, "seed": SEED})
        self.opener = opener or urllib.request.urlopen

    def call(self, path, payload):
        """Return the server's JSON reply to one request."""
        request = urllib.request.Request(
            self.endpoint + path, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with self.opener(request, timeout=SECONDS) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError):
            raise RubricError("rater_unreachable") from None

    def rate(self, text):
        """Return the rater's mark of one text, or None for no mark."""
        reply = self.call("/api/chat", {
            "model": self.model, "stream": False, "think": False,
            "options": self.options,
            "messages": [{"role": "system", "content": CRITERIA},
                         {"role": "user", "content": text}]})
        return mark_of((reply.get("message") or {}).get("content"))

    def describe(self):
        """Return what rates: the model's name and the server's account of
        it, its settings and the instructions' digest."""
        import hashlib
        shown = self.call("/api/show", {"model": self.model})
        return {"model": self.model,
                "details": shown.get("details"),
                "options": self.options,
                "criteria_sha256": hashlib.sha256(
                    CRITERIA.encode("utf-8")).hexdigest(),
                "rubric": RUBRIC_VERSION}


def rate_all(texts, rater, seed=SEED):
    """Return {key: mark} of (key, text) pairs, rated one at a time in an
    order shuffled with a fixed seed."""
    order = sorted(texts)
    random.Random(seed).shuffle(order)
    return {key: rater.rate(text) for key, text in order}


# --------------------------------------------------------------------------
# The rater's check: planted faults on development texts
# --------------------------------------------------------------------------

def planted(text, kind):
    """Return the variants of a passing text: (class, form, expected mark,
    text) for the clean text, the negation control and each fault in each
    form."""
    found = [("clean", "-", "pass", text),
             ("control", "negation", "pass",
              text + "\n" + CONTROLS[kind])]
    for name, forms in FAULTS[kind].items():
        for form, sentence in zip(("named", "paraphrase"), forms):
            found.append((name, form, "fail", text + "\n" + sentence))
    return found


def check_rater(texts, rater):
    """Return {(class, form): [right, wrong, no mark]} of the rater on the
    planted variants of passing texts, given as (kind, text) pairs."""
    counts = collections.defaultdict(lambda: [0, 0, 0])
    for kind, text in texts:
        for name, form, expected, variant in planted(text, kind):
            mark = rater.rate(variant)
            entry = counts[(kind, name, form)]
            entry[0 if mark == expected else 2 if mark is None else 1] += 1
    return {key: tuple(value) for key, value in sorted(counts.items())}


# --------------------------------------------------------------------------
# The owner's check
# --------------------------------------------------------------------------

def owner_sample(marks, seed=SEED, size=SAMPLE, failed=SAMPLE_FAILED):
    """Return the keys of the owner's sample: up to `failed` that the rater
    failed, the rest from those it passed, shuffled together."""
    generator = random.Random(seed)
    fails = sorted(key for key, mark in marks.items() if mark == "fail")
    passes = sorted(key for key, mark in marks.items() if mark == "pass")
    chosen = generator.sample(fails, min(failed, len(fails)))
    chosen += generator.sample(passes, min(size - len(chosen), len(passes)))
    generator.shuffle(chosen)
    return chosen


def agreement(marks, owners):
    """Return {rater's mark: {owner's mark: count}} over the owner's
    marked sample."""
    found = collections.defaultdict(collections.Counter)
    for key, owner in owners.items():
        found[marks[key]][owner] += 1
    return {mark: dict(sorted(counts.items()))
            for mark, counts in sorted(found.items())}


# --------------------------------------------------------------------------
# A run's texts and their rubric
# --------------------------------------------------------------------------

class Rated(typing.NamedTuple):
    """One text the rubric rates: its request, condition, kind and text,
    and its three items judged by code."""

    request_id: str
    condition: str
    kind: str
    text: str
    identity: bool
    evidence: bool
    history: bool


def run_texts(records, sources, bundles, limit):
    """Return the Rated texts of a run's records: every explanation and
    every condition answer each condition released with text."""
    from ehr_evidence_app.assessment import report, runner
    found = []
    for record in records:
        request = record["request"]
        kind = rated_kind(request["task"])
        if kind is None:
            continue
        reference = report.reference_of(request, sources, limit)
        bundle = bundles.of(request)
        for condition in runner.CONDITIONS:
            document = record["conditions"][condition]
            if document["text"] is None or document["outcome"] not in (
                    metrics.ANSWERS + ("explanation",)):
                continue
            response = report.response_of(request["request_id"], document,
                                          bundle)
            found.append(Rated(request["request_id"], condition, kind,
                               document["text"],
                               *code_items(reference, response)))
    return found


def rubric_document(rated, marks):
    """Return the rubric's aggregates: for each kind and condition, the
    texts passing each item and all four, and the paired comparison of
    passing all four over the requests rated in both conditions."""
    from ehr_evidence_app.assessment import statistics
    found = {}
    for kind in sorted({item.kind for item in rated}):
        entry = {}
        passing = {}
        for condition in ("baseline", "controlled"):
            items = [item for item in rated
                     if item.kind == kind and item.condition == condition]
            counts = collections.Counter()
            for item in items:
                wording = marks.get((item.request_id, condition))
                result = Marks(item.identity, item.evidence, item.history,
                               None if wording is None
                               else wording == "pass")
                counts["texts"] += 1
                counts["identity"] += item.identity
                counts["evidence"] += item.evidence
                counts["history"] += item.history
                counts["wording_rated"] += wording is not None
                counts["wording"] += wording == "pass"
                counts["all_four"] += result.passes() is True
                if wording is not None:
                    passing[(item.request_id, condition)] = (
                        result.passes() is True)
            entry[condition] = dict(counts)
        pairs = [(passing[(request_id, "baseline")],
                  passing[(request_id, "controlled")])
                 for request_id in sorted({key[0] for key in passing})
                 if (request_id, "baseline") in passing
                 and (request_id, "controlled") in passing]
        compared = statistics.compare_proportions(pairs)
        entry["all_four_compared"] = None if compared is None else {
            "difference": compared.difference, "lower": compared.lower,
            "upper": compared.upper, "p_value": compared.p_value,
            "counts": list(compared.counts)}
        entry["rated_in_one_condition_only"] = len(
            {key[0] for key in passing}) - len(pairs)
        found[kind] = entry
    return found


# --------------------------------------------------------------------------
# Default mode: invented data and a stand-in rater
# --------------------------------------------------------------------------

class StandIn:
    """A stand-in rater for checks: it fails a text holding any of the
    given phrases."""

    def __init__(self, phrases):
        """Hold the phrases it fails."""
        self.phrases = tuple(phrase.lower() for phrase in phrases)

    def rate(self, text):
        """Return "fail" when the text holds a phrase, else "pass"."""
        return "fail" if any(phrase in text.lower()
                             for phrase in self.phrases) else "pass"


def run_synthetic():
    """Return the problems of the rubric on invented data."""
    problems = []
    if mark_of("<think>hmm</think>\nFAIL.") != "fail" or mark_of(
            "pass") != "pass" or mark_of("unsure") is not None:
        problems.append("a reply is read wrongly")
    try:
        loopback("http://example.org:11434")
        problems.append("a rater away from the workstation is accepted")
    except RubricError:
        pass
    every = [sentence for forms in FAULTS["answer"].values()
             for sentence in forms]
    counts = check_rater([("answer", "Diabetes: 2 records in visits 1 "
                                     "and 2.")], StandIn(every))
    if any(right != 1 for right, _, _ in counts.values()):
        problems.append("a planted fault or control is miscounted")
    marks = {"t%02d" % number: "fail" if number < 20 else "pass"
             for number in range(60)}
    sample = owner_sample(marks)
    if len(sample) != SAMPLE or sum(marks[key] == "fail"
                                    for key in sample) != SAMPLE_FAILED:
        problems.append("the owner's sample is not drawn as set out")
    if sample != owner_sample(marks):
        problems.append("the owner's sample is not repeatable")
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
    print("rubric %s: %d fault classes in two forms" % (
        RUBRIC_VERSION, len(FAULTS["answer"])))
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
