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

Usage:
    python -m ehr_evidence_app.assessment.end_to_end
    python -m ehr_evidence_app.assessment.end_to_end --operator-real \\
        --set written-v4
"""

from __future__ import annotations

import argparse
import collections
import sys
import typing

from ehr_evidence_app.assessment import metrics, references, stated
from ehr_evidence_app.qa import answer, written

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


def run_synthetic():
    """Return (counts, problems) of the invented questions."""
    rows, inputs, vocab, limits = synthetic_inputs()
    found, left_out, _ = tally(assess(rows, inputs, vocab, limits))
    problems = []
    expected, expected_left_out = SYNTHETIC_EXPECTED
    if found != {intent: expected[intent] for intent in sorted(expected)}:
        problems.append("the invented questions' stages are unexpected")
    if left_out != expected_left_out:
        problems.append("the question about a case not admitted is not "
                        "left out")
    return (found, left_out), problems


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
    """Print the counts by intent and stage."""
    for intent, stages in found.items():
        for stage, verdicts in stages.items():
            print("%-9s %-15s %s" % (intent, stage, ", ".join(
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
            (found, left_out), problems = run_synthetic()
            report(found, left_out)
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
