"""Written wording test (work item 34.3).

Questions worded by a writer who has not read the interpretation rules,
about development cases. Each row names the case, the active scope, the
question, what the writer meant (a list, a presence check of one
condition, a request the application should refuse, or a question it
should answer with a clarifying question) and, for a presence check, the
OMOP concept meant. The file is pinned by its SHA-256 when scored, and it
is scored with the rules as they are; the rules are not changed to fit
it.

Scoring runs every question through the whole pipeline (qa.answer) with
the case set's pinned bundles, and compares with the development suite's
oracle (qa.suite), which reads the source snapshot and the vocabulary:

* list: the response is a list citing exactly the scope's records;
* presence: the question is understood when it is read as a presence
  check whose resolved concept is the concept meant, an ancestor or a
  descendant of it; it is exact when the answer also returns exactly the
  records of the concept meant (matches by ICD description aside);
* refuse: the response is a clarification or an abstention;
* clarify: the response is a clarification (a question naming another
  scope or a visit the case lacks, two requests or two conditions at
  once, a list of only some conditions, or one too vague to act on).

To help the writer choose conditions, an overview of each case's recorded
conditions by scope can be written to the protected workspace; it is
never printed. Only aggregate counts are printed.
"""

from __future__ import annotations

import argparse
import collections
import csv
import dataclasses
import hashlib
import io
import json
import os
import sys

from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import bundles, contracts, readers
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.qa import answer, interpret, resolve, suite
from ehr_evidence_app.storage import policy as guard

TEST_VERSION = "written-wording-v1"
PROTECTED_ROOT = guard.PRIVATE_ROOT + "\\protected-data"
QUESTION_SETS = PROTECTED_ROOT + "\\question-sets"
# Each set is written once and scored once; a later rule version is
# measured on a new set.
SETS = ("written-v1", "written-v2", "written-v3", "written-v4")
WRITTEN_DIR = QUESTION_SETS + "\\" + SETS[0]
QUESTIONS_NAME = "questions.csv"
OVERVIEW_NAME = "case-overview-%s.csv"
COLUMNS = ("id", "case_set", "case", "scope", "question", "intent",
           "concept")
# An optional last column: a free label that groups the results.
FAMILY = "family"
INTENTS = ("list", "presence", "refuse", "clarify")
EVIDENTIAL = ("answer", "not_recorded")
REFUSED = ("clarification", "abstention")
MAX_QUESTIONS = 500


class WrittenError(Exception):
    """A fail-closed failure carrying a finding code only."""


@dataclasses.dataclass(frozen=True)
class Row:
    """One written question."""

    id: str
    case_set: str
    case_number: int
    scope: contracts.ScopeRef
    question: str
    intent: str
    concept: int | None
    family: str = ""


def scope_ref(text):
    """Return the ScopeRef of 'history', 'all' or 'visit N', or None."""
    words = (text or "").strip().lower().split()
    if words == ["history"]:
        return contracts.ScopeRef(kind="pre_target_history")
    if words == ["all"]:
        return contracts.ScopeRef(kind="case_visits")
    if len(words) == 2 and words[0] == "visit" and words[1].isdigit() \
            and int(words[1]) > 0:
        return contracts.ScopeRef(kind="selected_visit",
                                  visit_number=int(words[1]))
    return None


def scope_text(scope):
    """Return how a scope is written in the files."""
    if scope.kind == "pre_target_history":
        return "history"
    if scope.kind == "case_visits":
        return "all"
    return "visit %d" % scope.visit_number


def parse_questions(data, allowed):
    """Return (rows, problems) of a questions file.

    A row naming a case set outside allowed stops the whole file, so an
    evaluation case set is never read. Other faulty rows are counted by
    problem and left out.
    """
    try:
        text = data.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        header = tuple(name.strip().lower()
                       for name in reader.fieldnames or ())
        if header not in (COLUMNS, COLUMNS + (FAMILY,)):
            raise WrittenError("questions_columns_unexpected")
        raw = [{key.strip().lower(): (value or "").strip()
                for key, value in line.items() if key}
               for line in reader]
    except (UnicodeDecodeError, csv.Error):
        raise WrittenError("questions_unreadable") from None
    if len(raw) > MAX_QUESTIONS:
        raise WrittenError("questions_too_many")
    rows, problems, seen = [], collections.Counter(), set()
    for line in raw:
        if not any(line.values()):
            continue
        if line["case_set"] not in allowed:
            raise WrittenError("case_set_not_development")
        scope = scope_ref(line["scope"])
        concept = line["concept"]
        problem = (
            "id_missing_or_repeated" if not line["id"] or line["id"] in seen
            else "case_invalid" if not line["case"].isdigit()
            else "scope_invalid" if scope is None
            else "question_missing" if not line["question"]
            else "intent_invalid" if line["intent"] not in INTENTS
            else "concept_invalid" if (
                line["intent"] == "presence" and not concept.isdigit())
            else None)
        seen.add(line["id"])
        if problem:
            problems[problem] += 1
            continue
        rows.append(Row(line["id"], line["case_set"], int(line["case"]),
                        scope, line["question"], line["intent"],
                        int(concept) if concept.isdigit() else None,
                        line.get(FAMILY, "")))
    return rows, problems


def related(vocab, resolved, concept):
    """Return True when a resolved concept is the concept, an ancestor or
    a descendant of it."""
    return any(found == concept or vocab.is_ancestor(found, concept)
               or vocab.is_ancestor(concept, found) for found in resolved)


def score_row(row, oracle, cache, vocab, limits, request_id, unmapped):
    """Return (verdict, outcome) of one written question."""
    case = next((case for case in oracle.cases
                 if case.case_number == row.case_number), None)
    if case is None:
        return "invalid:case_not_admitted", None
    if row.scope not in oracle.scopes(case):
        return "invalid:scope_not_in_case", None
    if row.intent == "presence" and (
            vocab.rows.get(row.concept) is None
            or vocab.rows[row.concept].kind != "standard"):
        return "invalid:concept_not_standard", None
    result = answer.answer(row.question, oracle.label, row.case_number,
                           row.scope, cache, vocab, request_id, limits)
    response = result.response
    outcome = response.outcome + (":" + ",".join(response.reason_codes)
                                  if response.reason_codes else "")
    records = oracle.scope_records(case, row.scope)
    if row.intent == "refuse":
        return ("correct" if response.outcome in REFUSED
                else "answered"), outcome
    if row.intent == "clarify":
        return ("correct" if response.outcome == "clarification"
                else "abstained" if response.outcome == "abstention"
                else "answered"), outcome
    if response.outcome not in EVIDENTIAL:
        return "not_understood", outcome
    bundle = cache.qa_bundle(row.case_number, row.scope)[0]
    if row.intent == "list":
        if response.task != "condition_list":
            return "not_understood", outcome
        expected = frozenset(record.key for record in records)
        return ("correct" if suite.cited(response, bundle) == expected
                else "records_differ"), outcome
    resolution = result.resolution
    if (response.task != "condition_presence" or resolution is None
            or not related(vocab, resolution.concepts, row.concept)):
        return "not_understood", outcome
    returned = frozenset(result.matched) - unmapped
    return ("exact" if returned == oracle.matches(records, row.concept)
            else "understood"), outcome


def score(rows, problems, inputs, vocab, limits, digests):
    """Score parsed rows; inputs maps each case set to (oracle, cache)."""
    verdicts = {intent: collections.Counter() for intent in INTENTS}
    outcomes = {intent: collections.Counter() for intent in INTENTS}
    families = collections.defaultdict(collections.Counter)
    invalid = collections.Counter(problems)
    for index, row in enumerate(rows, 1):
        oracle, cache = inputs[row.case_set]
        unmapped = frozenset(
            record.key for visits in oracle.records.values()
            for found in visits.values() for record in found
            if not record.concept_id)
        verdict, outcome = score_row(row, oracle, cache, vocab, limits,
                                     "w-%04d" % index, unmapped)
        if verdict.startswith("invalid:"):
            invalid[verdict[len("invalid:"):]] += 1
            continue
        verdicts[row.intent][verdict] += 1
        outcomes[row.intent][outcome] += 1
        if row.family:
            families["%s/%s" % (row.intent, row.family)][verdict] += 1
    document = {
        "test": TEST_VERSION,
        "rules": {"interpretation": interpret.RULES_VERSION,
                  "resolution": resolve.RULES_VERSION,
                  "answer": answer.RULES_VERSION},
        "inputs": digests,
        "questions_scored": sum(sum(found.values())
                                for found in verdicts.values()),
        "rows_left_out": dict(sorted(invalid.items())),
        "verdicts": {intent: dict(sorted(found.items()))
                     for intent, found in verdicts.items() if found},
        "outcomes": {intent: dict(sorted(found.items()))
                     for intent, found in outcomes.items() if found},
        "families": {family: dict(sorted(found.items()))
                     for family, found in sorted(families.items())}}
    document["summary_sha256"] = hashlib.sha256(json.dumps(
        document, sort_keys=True, separators=(",", ":")).encode(
            "utf-8")).hexdigest()
    return document


# --------------------------------------------------------------------------
# Real modes
# --------------------------------------------------------------------------

def development_case_set(label):
    """Refuse a case set that is not a development case set."""
    if label != answer.PILOT and registry.spec(label)["purpose"] \
            != "development":
        raise WrittenError("case_set_held_out")


def case_set_inputs(label, vocab, final=False):
    """Return (oracle, bundle cache, bundle manifest digest) of a
    development case set, or of the final case set when final says that
    the caller has checked the freeze (assessment.freeze)."""
    if not final:
        development_case_set(label)
    plan = readers.real_plan(None if label == answer.PILOT else label)
    snapshot = readers.read_snapshot(plan)
    cases = readers.read_cases(plan)
    bundle_plan = answer.bundle_set_plan(label)
    cache = suite.BundleCache(answer.load_bundle_set(bundle_plan), (
        snapshot.manifest_sha256, cases.closure_sha256,
        cases.mapping_sha256))
    return (suite.Oracle(snapshot, cases, vocab), cache,
            bundle_plan["manifest"]["sha256"])


def development_labels():
    """Return the labels of the development case sets."""
    return (answer.PILOT,) + tuple(
        label for label, spec in registry.CASE_SETS.items()
        if spec["purpose"] == "development")


def write_new(path, data):
    """Write a new protected file; an existing one is refused."""
    problem = mapping.output_problem(os.path.dirname(path), PROTECTED_ROOT)
    if problem:
        raise WrittenError(problem)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise WrittenError("output_exists") from None


def csv_bytes(lines):
    """Return CSV bytes (UTF-8 with a byte-order mark, for spreadsheets)."""
    buffer = io.StringIO(newline="")
    csv.writer(buffer, lineterminator="\r\n").writerows(lines)
    return buffer.getvalue().encode("utf-8-sig")


def set_dir(name):
    """Return the protected folder of a named question set."""
    if name not in SETS:
        raise WrittenError("question_set_unknown")
    return QUESTION_SETS + "\\" + name


def write_template(name=SETS[0]):
    """Write the empty questions file for the writer to fill in."""
    write_new(set_dir(name) + "\\" + QUESTIONS_NAME, csv_bytes([COLUMNS]))


def overview_lines(oracle, vocab):
    """Return the overview rows: recorded conditions by case and scope."""
    lines = [("case_set", "case", "scope", "concept", "condition",
              "records")]
    for case in oracle.cases:
        for scope in oracle.scopes(case):
            records = oracle.scope_records(case, scope)
            counts = collections.Counter(record.concept_id
                                         for record in records
                                         if record.concept_id)
            for concept, count in sorted(counts.items()):
                row = vocab.rows.get(concept)
                lines.append((oracle.label, case.case_number,
                              scope_text(scope), concept,
                              row.name if row else "", count))
            uncoded = sum(1 for record in records if not record.concept_id)
            if uncoded:
                lines.append((oracle.label, case.case_number,
                              scope_text(scope), "",
                              "(records without a standard concept)",
                              uncoded))
    return lines


def write_overview(label, name=SETS[0]):
    """Write the overview of one development case set; return its size."""
    vocab = resolve.load()
    oracle, _, _ = case_set_inputs(label, vocab)
    data = csv_bytes(overview_lines(oracle, vocab))
    write_new(set_dir(name) + "\\" + OVERVIEW_NAME % label, data)
    return len(data)


def run_score(name=SETS[0]):
    """Score the questions file once with the rules as they are."""
    held = []
    try:
        data, signature = mapping.read_verified(
            set_dir(name) + "\\" + QUESTIONS_NAME, "questions",
            "input_hash_mismatch:questions")
    except mapping.MappingError as error:
        raise WrittenError(str(error)) from None
    held.append((set_dir(name) + "\\" + QUESTIONS_NAME, signature,
                 "questions"))
    rows, problems = parse_questions(data, development_labels())
    vocab = resolve.load()
    inputs, digests = {}, {"questions": hashlib.sha256(data).hexdigest(),
                           "vocabulary_manifest": vocab.digest}
    for label in sorted({row.case_set for row in rows}):
        oracle, cache, digest = case_set_inputs(label, vocab)
        inputs[label] = (oracle, cache)
        digests["bundle_manifest " + label] = digest
    try:
        mapping.confirm_unchanged(held)
    except mapping.MappingError as error:
        raise WrittenError(str(error)) from None
    return score(rows, problems, inputs, vocab, contracts.load_limits(),
                 digests)


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

SYNTHETIC_QUESTIONS = (
    COLUMNS,
    ("q1", "synthetic-1-v1", "1", "history",
     "Which conditions has the patient had before the last admission?",
     "list", ""),
    ("q2", "synthetic-1-v1", "1", "all",
     "Has she ever had synthetic concept 1002?", "presence", "1002"),
    ("q3", "synthetic-1-v1", "1", "history", "any synthetic alpha disorder?",
     "presence", "1001"),
    ("q4", "synthetic-1-v1", "1", "all",
     "Is synthetic parent disorder recorded?", "presence", "1001"),
    ("q5", "synthetic-1-v1", "1", "visit 2", "Tell me about her kidneys",
     "presence", "1002"),
    ("q6", "synthetic-1-v1", "1", "history",
     "What dose of aspirin should she get?", "refuse", ""),
    ("q7", "synthetic-1-v1", "9", "all", "Is synthetic concept 1001 "
                                         "recorded?", "presence", "1001"),
    ("q8", "synthetic-1-v1", "1", "history", "Is synthetic concept 1002 "
                                             "recorded in the final "
                                             "admission?", "clarify", ""),
)
SYNTHETIC_EXPECTED = {
    "questions_scored": 7,
    "rows_left_out": {"case_not_admitted": 1},
    "verdicts": {"list": {"correct": 1},
                 "presence": {"exact": 2, "not_understood": 1,
                              "understood": 1},
                 "refuse": {"correct": 1},
                 "clarify": {"correct": 1}}}


def run_synthetic():
    """Score invented questions about the synthetic case in memory."""
    snapshot, case_set, _, _, _ = bundles.synthetic_inputs()
    vocab = suite.synthetic_vocabulary()
    provenance = bundles.synthetic_provenance()
    cache = suite.BundleCache(suite.synthetic_bundle_set(), (
        provenance.source_snapshot.sha256, provenance.closure_record.sha256,
        provenance.identity_mapping.sha256))
    rows, problems = parse_questions(csv_bytes(SYNTHETIC_QUESTIONS),
                                     (case_set.label,))
    return score(rows, problems,
                 {case_set.label: (suite.Oracle(snapshot, case_set, vocab),
                                   cache)},
                 vocab, contracts.ExecutionLimits(**bundles.SYNTHETIC_LIMITS),
                 {"mode": "synthetic"})


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def report(document):
    """Print the scores: aggregate counts and digests only."""
    line = "%-44s %s"
    print(line % ("test", document["test"]))
    print(line % ("questions scored", document["questions_scored"]))
    for key, value in document["rows_left_out"].items():
        print(line % ("left out " + key, value))
    for intent, found in document["verdicts"].items():
        print(line % (intent, ", ".join("%s %d" % item
                                        for item in found.items())))
        for outcome, count in document["outcomes"][intent].items():
            print(line % ("  " + outcome, count))
    if document["families"]:
        print("families")
    for family, found in document["families"].items():
        print(line % ("  " + family, ", ".join("%s %d" % item
                                               for item in found.items())))
    for key, value in document["rules"].items():
        print(line % ("rules " + key, value))
    for key, value in document["inputs"].items():
        print(line % ("input " + key, value))
    print(line % ("summary_sha256", document["summary_sha256"]))


def main(argv=None):
    """Run the synthetic default mode or an operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="use real development case sets; prints "
                             "aggregate counts only")
    parser.add_argument("--template", action="store_true",
                        help="write the empty questions file")
    parser.add_argument("--set", default=SETS[0], choices=SETS,
                        help="the question set (default %(default)s)")
    parser.add_argument("--overview",
                        help="write the recorded-condition overview of a "
                             "development case set")
    parser.add_argument("--score", action="store_true",
                        help="score the questions file")
    args = parser.parse_args(argv)
    chosen = [args.template, bool(args.overview), args.score]
    if not args.operator_real and any(chosen):
        print("FAILED: --template, --overview and --score need "
              "--operator-real")
        return 2
    if args.operator_real and sum(chosen) != 1:
        print("FAILED: give exactly one of --template, --overview and "
              "--score")
        return 2
    try:
        if args.operator_real and args.template:
            write_template(args.set)
            print("questions file written")
        elif args.operator_real and args.overview:
            print("overview written: %d bytes" % write_overview(
                args.overview, args.set))
        elif args.operator_real:
            report(run_score(args.set))
        else:
            document = run_synthetic()
            report(document)
            if any(document[key] != value
                   for key, value in SYNTHETIC_EXPECTED.items()):
                print("FAILED: synthetic_result_unexpected")
                return 1
    except (WrittenError, answer.AnswerError, suite.SuiteError,
            readers.ReaderError, registry.RegistryError,
            resolve.ResolutionError, contracts.ContractError) as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
