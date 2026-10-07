"""The held-out questions of MIMIC-IV-Ext-Instr, read once with the frozen
rules (ADR 0016, decision 11; ADR 0028, decision 7; ADR 0029).

Every question of the dataset's held-out partition (qa.heldout's pinned
partition record) is read as the application routes it, without a
bundle: by the condition rules first and, where those refuse a question
about other data or name no condition, by the rules for questions about
the whole record (qa.record_interpret), with the final case set's
dictionaries. Each family of questions has an expected reading, fixed
before the partition is read:

* the all-diagnoses list: a condition list;
* a ranking of diagnoses (first, top three, top five): not answered,
  since billing order is not in the evidence;
* demographics: the person's details;
* admission information: a visit's or the person's details;
* transfers: transfers or ICU stays;
* procedures, laboratory results, prescriptions and microbiology: a
  question about that kind of record.

A reading is counted as its expected one or not, by family. The read
needs the freeze, writes its aggregate result once to a protected record
named by the freeze, and prints counts only; question and answer text
never leave the process. The default mode reads an invented archive with
invented dictionaries.

Usage:
    python -m ehr_evidence_app.assessment.instr
    python -m ehr_evidence_app.assessment.instr --operator-final
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
import time

from ehr_evidence_app.qa import answer, heldout, interpret, record_interpret
from ehr_evidence_app.qa import record_resolve, resolve

TEST_VERSION = "instr-held-out-reading-v2"
RESULTS_DIR = "\\assessment\\instr"
# The kinds a family's questions are expected to be read as; an empty
# tuple expects the question not to be answered.
EXPECTED = {
    "diagnoses_list": ("condition_list",),
    "diagnoses_ranking": (),
    "patient_demographics": ("person",),
    "admission_info": ("visit", "person"),
    "transfers": ("transfer", "icu_stay"),
    "procedureevents": ("procedure",),
    "labevents": ("lab",),
    "prescriptions": ("medication",),
    "microbiologyevents": ("microbiology",),
}
ANSWERED = ("list", "presence")


class InstrError(Exception):
    """A fail-closed failure carrying a finding code only."""


def record_reading(question, terms, tasks_only=False):
    """Return the record rules' reading of a question, as the application
    answers it (qa.answer.record_result): "record:<task>:<kind>", or
    "clarification:<reason>", an item whose phrase does not resolve being
    asked about; None when it is not one about the record, or, with
    tasks_only, not read as a task."""
    found = record_interpret.interpret(question, terms)
    if found.status == "task":
        if found.phrase and found.kind not in ("visit", "person",
                                               "icu_stay"):
            resolution = terms.resolve(found.kind, found.phrase)
            if resolution.status != "resolved":
                return "clarification:record_%s" % resolution.status
        return "record:%s:%s" % (found.task, found.kind)
    if found.status == "clarify" and not tasks_only:
        return "clarification:%s" % found.reason
    return None


def reading(question, vocab, terms):
    """Return how the application reads a question, without a bundle:
    "list", "presence", "record:<task>:<kind>", "clarification:<reason>"
    or "abstention:<rule>"."""
    question = answer.well_formed(question)
    interpretation = interpret.interpret(question)
    if interpretation.status in ("abstain", "clarify") and (
            answer.record_allowed(interpretation)):
        found = record_reading(question, terms)
        if found is not None:
            return found
    if interpretation.status == "abstain":
        return "abstention:%s" % interpretation.category
    if interpretation.status == "clarify":
        return "clarification:%s" % interpretation.reason
    if interpretation.task == "condition_list":
        return "list"
    resolution = answer.resolve_phrase(interpretation, vocab,
                                       suggestions=False)
    if resolution.status == "unknown" or any(
            record_resolve.class_word(term)
            for term in (interpretation.terms or (interpretation.term,))):
        found = record_reading(question, terms, tasks_only=True)
        if found is not None:
            return found
    if interpretation.guard and resolution.status == "unknown":
        status, reason, category = interpretation.guard
        return ("abstention:%s" % category if status == "abstain"
                else "clarification:%s" % reason)
    if resolution.status == "resolved":
        return "presence"
    return "clarification:concept_%s" % resolution.status


def answered(found):
    """Return True when a reading answers the question."""
    return found in ANSWERED or found.startswith("record:")


def met(family, found):
    """Return True when a reading is the one the family expects."""
    expected = EXPECTED[family]
    if not expected:
        return not answered(found)
    if expected == ("condition_list",):
        return found == "list"
    return found.startswith("record:") and found.rsplit(":", 1)[1] in expected


def run_test(archive, held_out, vocab, terms, digests):
    """Read every held-out question; return the aggregate result."""
    started = time.monotonic()
    readings = {family: collections.Counter() for family in EXPECTED}
    correct = collections.Counter()
    for hadm, record in archive.records("qa_event.jsonl"):
        if hadm not in held_out:
            continue
        if record.get("event_type") not in heldout.EVENT_TYPES:
            raise InstrError("event_type_unexpected")
        question = record.get("q") or ""
        family = heldout.family_of(record["event_type"], question)
        found = reading(question, vocab, terms)
        readings[family][found] += 1
        correct[family] += met(family, found)
    families = {family: {"questions": sum(found.values()),
                         "as_expected": correct[family],
                         "readings": dict(sorted(found.items()))}
                for family, found in readings.items() if found}
    document = {
        "test": TEST_VERSION,
        "rules": {"interpretation": interpret.RULES_VERSION,
                  "resolution": resolve.RULES_VERSION,
                  "answer": answer.RULES_VERSION,
                  "record_questions": record_interpret.RULES_VERSION,
                  "record_resolution": record_resolve.RULES_VERSION},
        "inputs": digests,
        "questions": sum(entry["questions"] for entry in families.values()),
        "as_expected": sum(entry["as_expected"]
                           for entry in families.values()),
        "families": families}
    document["summary_sha256"] = hashlib.sha256(json.dumps(
        document, sort_keys=True, separators=(",", ":")).encode(
            "utf-8")).hexdigest()
    document["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return document


def final_terms(case_set, conditions):
    """Return the terms of a case set's dictionaries, as the application
    serving it resolves phrases against them."""
    from ehr_evidence_app.qa import record_vocabulary
    from ehr_evidence_app.sources import mimic
    drugs = record_vocabulary.load()
    tables = mimic.read_files(case_set, ("d_labitems.csv", "d_items.csv",
                                         mimic.NAMES_FILE))
    labs, items, micro, units = record_resolve.extract_dictionaries({
        name[:-4]: rows for name, rows in tables.items()})
    return record_resolve.Terms(labs, items, micro, units, drugs,
                                drugs.procedure_names, conditions)


def run_final():
    """Read the held-out partition once with the frozen rules and the
    final case set's dictionaries; write the result to a protected record
    named by the freeze, once; return it."""
    from ehr_evidence_app.assessment import freeze
    from ehr_evidence_app.gnn import mapping
    try:
        _, digest = freeze.allowed(freeze.FINAL)
    except freeze.FreezeError as error:
        raise InstrError(str(error)) from None
    directory = freeze.PROTECTED_ROOT + RESULTS_DIR
    path = os.path.join(directory, "%s-%s.json" % (TEST_VERSION,
                                                   digest[:16]))
    problem = mapping.output_problem(path, freeze.PROTECTED_ROOT)
    if problem:
        raise InstrError(problem)
    if os.path.lexists(path):
        raise InstrError("read_before")
    plan = heldout.real_plan()
    held = []
    held_out = heldout.load_partition(plan, held)
    archive = heldout.open_archive(heldout.read_pinned(
        plan["archive"], "instr_archive", held))
    vocab = resolve.load()
    terms = final_terms(freeze.FINAL, vocab)
    try:
        mapping.confirm_unchanged(held)
    except mapping.MappingError as error:
        raise InstrError(str(error)) from None
    document = run_test(archive, held_out, vocab, terms, {
        "archive": plan["archive"]["sha256"],
        "partition": plan["partition"]["sha256"],
        "vocabulary_manifest": vocab.digest, "freeze": digest})
    os.makedirs(directory, exist_ok=True)
    try:
        with open(path, "x", encoding="utf-8", newline="\n") as handle:
            json.dump(document, handle, indent=1, sort_keys=True)
    except FileExistsError:
        raise InstrError("read_before") from None
    return document


# Default Mode: An Invented Archive and Invented Dictionaries

SYNTHETIC_QUESTIONS = (
    (9103, "diagnoses_icd", "Can you list the diagnoses billed for this "
                            "patient?"),
    (9103, "diagnoses_icd", "What were the top three billed diagnoses of "
                            "the patient?"),
    (9103, "labevents", "What was the last synthetic potassium?"),
    (9103, "labevents", "What was the last Synthetic analyte measurement "
                        "during the first 24 hours?"),
    (9103, "prescriptions", "Was synthetic concept 3001 given?"),
    (9103, "patient_demographics", "What was the gender of the patient?"),
    (9103, "admission_info", "When was the patient admitted?"),
    (9103, "transfers", "Which units was the patient in?"),
    (9103, "microbiologyevents", "What organisms grew?"),
    (9102, "diagnoses_icd", "What were the billed diagnoses of the "
                            "patient?"),
)
# The question about the first 24 hours names a time the rules do not take
# as a scope, and is asked back; the question of an admission outside the
# held-out partition is not read.
SYNTHETIC_EXPECTED = {"questions": 9, "as_expected": 8}


def run_synthetic():
    """Return (result, problems) of the invented archive."""
    from ehr_evidence_app.qa import suite
    from ehr_evidence_app.validation import record_support
    archive = heldout.open_archive(heldout.archive_bytes(
        heldout.synthetic_files(questions=SYNTHETIC_QUESTIONS)))
    document = heldout.partition_document(archive, heldout.SYNTHETIC_OURS,
                                          {"name": "synthetic"}, {})
    held_out = frozenset(document["partitions"]["held_out"]["hadm_ids"])
    vocab = suite.synthetic_vocabulary()
    result = run_test(archive, held_out, vocab,
                      record_support.synthetic_terms(vocab),
                      {"mode": "synthetic"})
    problems = []
    if {key: result[key] for key in SYNTHETIC_EXPECTED} != (
            SYNTHETIC_EXPECTED):
        problems.append("the invented questions are read unexpectedly")
    return result, problems


def report(document):
    """Print the result: aggregate counts and digests only."""
    line = "%-44s %s"
    print(line % ("test", document["test"]))
    print(line % ("questions", document["questions"]))
    print(line % ("as expected", document["as_expected"]))
    for family, entry in document["families"].items():
        print(line % (family, "%d questions, %d as expected" % (
            entry["questions"], entry["as_expected"])))
        for found, count in entry["readings"].items():
            print(line % ("  " + found, count))
    for key, value in document["rules"].items():
        print(line % ("rules " + key, value))
    for key, value in document["inputs"].items():
        print(line % ("input " + key, value))
    print(line % ("summary_sha256", document["summary_sha256"]))


def main(argv=None):
    """Run the synthetic default mode or the operator's final read."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-final", action="store_true",
                        help="read the held-out partition once with the "
                             "frozen rules; prints counts only")
    args = parser.parse_args(argv)
    try:
        if args.operator_final:
            report(run_final())
        else:
            document, problems = run_synthetic()
            report(document)
            for problem in problems:
                print("FAILED: %s" % problem)
            if problems:
                return 1
    except (InstrError, heldout.HeldOutError) as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
