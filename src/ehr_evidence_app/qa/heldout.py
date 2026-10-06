"""Held-out wording test on MIMIC-IV-Ext-Instr (work item 34.2).

MIMIC-IV-Ext-Instr (PhysioNet, credentialed) holds question and answer
pairs generated from MIMIC-IV admissions with fixed templates and
paraphrased by GPT-3.5. Its diagnosis questions come from four list
templates: all billed diagnoses, the first, the top three and the top
five. Its other questions ask about laboratory results, microbiology,
prescriptions, procedures, transfers, demographics and admission details.

The test asks what the rules would do with every question of the held-out
partition, without a bundle:

* an all-diagnoses question must be read as a condition list;
* a ranking question (first, top three, top five) must not be answered:
  billing order is not in the evidence, and an unordered list would not
  answer it;
* a question about other data must not be answered.

A question is answered when it is read as a list, or as a presence check
whose condition phrase resolves. The rules are never changed to fit this
test. Only aggregate counts are printed; question and answer text never
leave the process.

The partition record comes first and fixes every later use of the
dataset. The archive's own split is patient-level, but its validation file
holds the whole test set, some admissions are in no split, and patients of
our case sets appear in its training split. So:

* held-out: the test admissions (the only partition this module reads);
* validation: validation admissions outside the test set;
* training: training admissions;
* excluded: admissions in no split;

and every admission of a patient in our case sets is removed from
training and validation. Training and validation stay untouched until the
Phase 7 and Phase 9 plans decide their use.
"""

from __future__ import annotations

import argparse
import collections
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import zipfile

import ehr_evidence_app
from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import readers
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.qa import answer, interpret, resolve, suite
from ehr_evidence_app.storage import policy as guard

TEST_VERSION = "instr-held-out-wording-v1"
SCHEMA = "question-set-partitions"
SCHEMA_VERSION = "1.0"
PROTECTED_ROOT = guard.PRIVATE_ROOT + "\\protected-data"
QUESTION_SETS = PROTECTED_ROOT + "\\question-sets"
ARCHIVE_NAME = ("mimic-iv-ext-instr-a-dataset-of-450k-ehr-grounded-"
                "instruction-following-examples-1.0.0.zip")
ARCHIVE = {"path": QUESTION_SETS + "\\mimic-iv-ext-instr\\" + ARCHIVE_NAME,
           "size_bytes": 25261967,
           "sha256": ("cc6f30f1004f026d9533a8fe7ebfefa3"
                      "164bc4eb81321dfdb3c4f3b9f1359c65")}
PARTITION_VERSION = "mimic-iv-ext-instr-partitions-v1"
PARTITION_DIR = QUESTION_SETS + "\\" + PARTITION_VERSION
PARTITION_NAME = "partitions.json"
# (size in bytes, SHA-256) of the published partition record.
PARTITION_PIN = (485743, "4bfa37dd40235d5cbee6d3608ea7e20c"
                         "20e17bbc26821c0b05186f9a14144f95")
CASE_SETS = (answer.PILOT, "dev-30-v1", "eval-100-v1")
REQUIRED = ("cohort.csv", "cohort_train.csv", "cohort_val.csv",
            "cohort_test.csv", "qa_event.jsonl", "qa_note.jsonl")
EVENT_TYPES = ("patient_demographics", "admission_info", "transfers",
               "diagnoses_icd", "procedureevents", "labevents",
               "prescriptions", "microbiologyevents")
FAMILIES = ("diagnoses_list", "diagnoses_ranking") + tuple(
    event for event in EVENT_TYPES if event != "diagnoses_icd")
PARTITIONS = ("held_out", "validation", "training")
RULES = (
    "held_out: every admission of cohort_test.csv",
    "validation: admissions of cohort_val.csv that are not in "
    "cohort_test.csv",
    "training: admissions of cohort_train.csv",
    "excluded: admissions of cohort.csv in no split file",
    "admissions of patients in the pilot, development and evaluation case "
    "sets are removed from training and validation",
)
# Words of the ranking templates (first, top three, top five) and of their
# paraphrases; the all-diagnoses template has none of them.
RANKING = re.compile(
    r"\b(?:first|top|primary|principal|main|leading|initial|initially|"
    r"three|five|3|5|1st)\b")
ANSWERED = ("list", "presence")
BUILDER = "ehr_evidence_app.qa.heldout %s" % ehr_evidence_app.__version__
PARTIAL = ".partial"


class HeldOutError(Exception):
    """A fail-closed failure carrying a finding code only."""


def real_plan():
    """Return the pinned archive, the partition record and its output."""
    return {"archive": dict(ARCHIVE),
            "partition": None if PARTITION_PIN is None else {
                "path": PARTITION_DIR + "\\" + PARTITION_NAME,
                "size_bytes": PARTITION_PIN[0], "sha256": PARTITION_PIN[1]},
            "output_dir": PARTITION_DIR, "protected_root": PROTECTED_ROOT}


# The Archive

class Archive:
    """The verified members of the dataset archive, by name."""

    def __init__(self, members):
        """Keep the verified member bytes."""
        self.members = members

    def rows(self, name):
        """Return the rows of a CSV member."""
        try:
            return list(csv.DictReader(io.StringIO(
                self.members[name].decode("utf-8"), newline="")))
        except (KeyError, UnicodeDecodeError, csv.Error):
            raise HeldOutError("archive_member_unreadable:%s" % name) \
                from None

    def records(self, name):
        """Yield the records of a JSON-lines member."""
        try:
            for line in self.members[name].decode("utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    yield int(record["hadm_id"]), record
        except (KeyError, UnicodeDecodeError, TypeError, ValueError):
            raise HeldOutError("archive_member_unreadable:%s" % name) \
                from None


def open_archive(data):
    """Return the Archive of verified bytes; every member must match the
    archive's own SHA256SUMS."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            prefix = bundle.namelist()[0].split("/")[0] + "/"
            sums = {}
            for line in bundle.read(prefix + "SHA256SUMS.txt").decode(
                    "utf-8").splitlines():
                if line.strip():
                    value, name = line.split(None, 1)
                    sums[name.strip().lstrip("*")] = value.lower()
            members = {}
            for info in bundle.infolist():
                name = info.filename[len(prefix):]
                if info.is_dir() or name == "SHA256SUMS.txt":
                    continue
                content = bundle.read(info)
                if sums.get(name) != hashlib.sha256(content).hexdigest():
                    raise HeldOutError("archive_member_mismatch")
                members[name] = content
    except (zipfile.BadZipFile, KeyError, IndexError, UnicodeDecodeError,
            ValueError):
        raise HeldOutError("archive_unexpected") from None
    if any(name not in members for name in REQUIRED):
        raise HeldOutError("archive_member_missing")
    return Archive(members)


def read_pinned(entry, name, held):
    """Read one pinned file once; return its verified bytes."""
    try:
        data, signature = mapping.read_verified(
            entry["path"], name, "input_hash_mismatch:%s" % name,
            sha256=entry["sha256"], size=entry["size_bytes"])
    except mapping.MappingError as error:
        raise HeldOutError(str(error)) from None
    held.append((entry["path"], signature, name))
    return data


# Partitions

def owners(rows):
    """Return {admission: patient} of cohort rows."""
    found = {}
    for row in rows:
        try:
            found[int(row["hadm_id"])] = int(row["subject_id"])
        except (KeyError, TypeError, ValueError):
            raise HeldOutError("cohort_row_invalid") from None
    return found


def partition_document(archive, ours, source, case_sets):
    """Return the partition record: the rules applied to the archive's
    split, with the admissions of our patients removed from training and
    validation."""
    cohort = owners(archive.rows("cohort.csv"))
    split = {name: owners(archive.rows("cohort_%s.csv" % name))
             for name in ("train", "val", "test")}
    patients = {name: set(found.values()) for name, found in split.items()}
    if patients["train"] & (patients["val"] | patients["test"]):
        raise HeldOutError("split_not_patient_disjoint")
    listed = set(split["train"]) | set(split["val"]) | set(split["test"])
    if not listed <= set(cohort):
        raise HeldOutError("split_admission_unlisted")
    held_out = set(split["test"])
    validation = {hadm for hadm in split["val"]
                  if hadm not in held_out and cohort[hadm] not in ours}
    training = {hadm for hadm in split["train"] if cohort[hadm] not in ours}
    members = {"held_out": held_out, "validation": validation,
               "training": training}
    place = {hadm: name for name, found in members.items() for hadm in found}
    events = {name: collections.Counter() for name in PARTITIONS}
    notes = collections.Counter()
    outside = 0
    for hadm, record in archive.records("qa_event.jsonl"):
        if record.get("event_type") not in EVENT_TYPES:
            raise HeldOutError("event_type_unexpected")
        outside += hadm not in cohort
        if hadm in place:
            events[place[hadm]][record["event_type"]] += 1
    for hadm, _ in archive.records("qa_note.jsonl"):
        outside += hadm not in cohort
        if hadm in place:
            notes[place[hadm]] += 1
    excluded = {
        "admissions_in_no_split": len(set(cohort) - listed),
        "questions_of_admissions_outside_cohort": outside,
        "training_admissions_of_case_set_patients":
            len(split["train"]) - len(training),
        "validation_admissions_in_the_test_split":
            len(set(split["val"]) & held_out),
        "validation_admissions_of_case_set_patients": sum(
            1 for hadm in split["val"]
            if hadm not in held_out and cohort[hadm] in ours),
    }
    return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
            "version": PARTITION_VERSION, "builder": BUILDER,
            "patient_bearing": True,
            "licence": "PhysioNet Credentialed Health Data License 1.5.0; "
                       "private workspace only",
            "source": source, "case_sets": case_sets, "rules": list(RULES),
            "excluded": excluded,
            "partitions": {name: {
                "admissions": len(found),
                "patients": len({cohort[hadm] for hadm in found}),
                "event_questions": dict(sorted(events[name].items())),
                "note_questions": notes[name],
                "hadm_ids": sorted(found)}
                for name, found in members.items()}}


def case_set_patients(labels=CASE_SETS):
    """Return the patients of our case sets and what was read for them."""
    patients, read = set(), {}
    for label in labels:
        cases = readers.read_cases(readers.real_plan(
            None if label == answer.PILOT else label))
        patients |= {case.subject_id for case in cases.cases}
        read[label] = {"closure_sha256": cases.closure_sha256,
                       "cases": len(cases.cases)}
    return patients, read


def output_preconditions(plan):
    """Refuse an existing or unprotected output."""
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        if os.path.lexists(path):
            raise HeldOutError("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise HeldOutError(problem)


def record_bytes(document):
    """Return the canonical bytes of a partition record."""
    return (json.dumps(document, sort_keys=True, separators=(",", ":"))
            + "\n").encode("utf-8")


def publish(plan, data):
    """Write the record through a new .partial directory and rename it."""
    partial = plan["output_dir"] + PARTIAL
    target = os.path.join(partial, PARTITION_NAME)
    os.makedirs(os.path.dirname(plan["output_dir"]), exist_ok=True)
    os.mkdir(partial)
    try:
        with open(target, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.rename(partial, plan["output_dir"])
        except OSError:
            raise HeldOutError("output_rename_failed") from None
    except BaseException:
        if os.path.lexists(target):
            os.remove(target)
        if os.path.isdir(partial):
            os.rmdir(partial)
        raise


def run_partition(plan=None):
    """Build and publish the partition record; return it with its pin."""
    plan = plan or real_plan()
    if guard.fixed_policy_problem():
        raise HeldOutError("fixed_policy_rejected")
    output_preconditions(plan)
    held = []
    archive = open_archive(read_pinned(plan["archive"], "instr_archive",
                                       held))
    ours, read = case_set_patients()
    try:
        mapping.confirm_unchanged(held)
    except mapping.MappingError as error:
        raise HeldOutError(str(error)) from None
    document = partition_document(archive, ours, {
        "name": ARCHIVE_NAME, "release": "1.0.0",
        "size_bytes": plan["archive"]["size_bytes"],
        "sha256": plan["archive"]["sha256"]}, read)
    data = record_bytes(document)
    publish(plan, data)
    return document, (len(data), hashlib.sha256(data).hexdigest())


# The Held-Out Test

def family_of(event_type, question):
    """Return the question family: the all-diagnoses list, a ranking of
    diagnoses, or another event type."""
    if event_type != "diagnoses_icd":
        return event_type
    if RANKING.search(interpret.normalize(question)):
        return "diagnoses_ranking"
    return "diagnoses_list"


def reading(question, vocab):
    """Return (reading, scope phrase present) of a question, without a
    bundle: "list", "presence" (a resolved condition phrase),
    "clarification:<reason>" or "abstention:<rule>"."""
    interpretation = interpret.interpret(question)
    mentioned = bool(interpretation.mentions)
    if interpretation.status == "abstain":
        return "abstention:" + interpretation.category, mentioned
    if interpretation.status == "clarify":
        return "clarification:" + interpretation.reason, mentioned
    if interpretation.task == "condition_list":
        return "list", mentioned
    resolution = answer.resolve_phrase(interpretation, vocab,
                                       suggestions=False)
    if interpretation.guard and resolution.status == "unknown":
        status, reason, category = interpretation.guard
        return ("abstention:" + category if status == "abstain"
                else "clarification:" + reason), mentioned
    if resolution.status == "resolved":
        return "presence", mentioned
    return "clarification:concept_" + resolution.status, mentioned


def met(family, found):
    """Return True when a reading is what the family expects."""
    if family == "diagnoses_list":
        return found == "list"
    return found not in ANSWERED


def run_test(archive, held_out, vocab, digests):
    """Read every held-out question and return the aggregate result."""
    started = time.monotonic()
    readings = {family: collections.Counter() for family in FAMILIES}
    correct = collections.Counter()
    scoped = 0
    for hadm, record in archive.records("qa_event.jsonl"):
        if hadm not in held_out:
            continue
        if record.get("event_type") not in EVENT_TYPES:
            raise HeldOutError("event_type_unexpected")
        family = family_of(record["event_type"], record.get("q") or "")
        found, mentioned = reading(record.get("q") or "", vocab)
        readings[family][found] += 1
        correct[family] += met(family, found)
        scoped += mentioned
    families = {family: {"questions": sum(found.values()),
                         "correct": correct[family],
                         "readings": dict(sorted(found.items()))}
                for family, found in readings.items() if found}
    document = {
        "test": TEST_VERSION,
        "rules": {"interpretation": interpret.RULES_VERSION,
                  "resolution": resolve.RULES_VERSION,
                  "answer": answer.RULES_VERSION},
        "inputs": digests,
        "questions": sum(entry["questions"] for entry in families.values()),
        "correct": sum(entry["correct"] for entry in families.values()),
        "answered_out_of_scope": sum(
            count for family, found in readings.items()
            if family != "diagnoses_list"
            for reading_, count in found.items() if reading_ in ANSWERED),
        "questions_with_scope_phrases": scoped,
        "families": families}
    document["summary_sha256"] = hashlib.sha256(json.dumps(
        document, sort_keys=True, separators=(",", ":")).encode(
            "utf-8")).hexdigest()
    document["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return document


def load_partition(plan, held):
    """Return the held-out admissions of the pinned partition record."""
    if plan["partition"] is None:
        raise HeldOutError("partition_not_pinned")
    try:
        document = json.loads(read_pinned(plan["partition"], "partitions",
                                          held).decode("utf-8"))
        if (document.get("schema") != SCHEMA
                or document.get("version") != PARTITION_VERSION
                or document["source"]["sha256"] != plan["archive"]["sha256"]):
            raise HeldOutError("partition_unexpected")
        return frozenset(document["partitions"]["held_out"]["hadm_ids"])
    except (KeyError, TypeError, UnicodeDecodeError, ValueError):
        raise HeldOutError("partition_unexpected") from None


def run_held_out(plan=None):
    """Run the held-out test on the pinned partition; return the result."""
    plan = plan or real_plan()
    held = []
    held_out = load_partition(plan, held)
    archive = open_archive(read_pinned(plan["archive"], "instr_archive",
                                       held))
    vocab = resolve.load()
    try:
        mapping.confirm_unchanged(held)
    except mapping.MappingError as error:
        raise HeldOutError(str(error)) from None
    return run_test(archive, held_out, vocab, {
        "archive": plan["archive"]["sha256"],
        "partition": plan["partition"]["sha256"],
        "vocabulary_manifest": vocab.digest})


# Synthetic Default Mode (Invented Fixtures Only)

# Invented patients and admissions: (patient, admission, split files). The
# first and fourth patients stand for patients of our case sets.
SYNTHETIC_COHORT = ((901, 9101, ("train",)), (902, 9102, ("train",)),
                    (903, 9103, ("val", "test")), (904, 9104, ("val",)),
                    (905, 9105, ("val",)), (906, 9106, ()))
SYNTHETIC_OURS = frozenset((901, 904))
SYNTHETIC_QUESTIONS = (
    (9103, "diagnoses_icd", "Can you list the diagnoses billed for this "
                            "patient?"),
    (9103, "diagnoses_icd", "What were the top three billed diagnoses of "
                            "the patient?"),
    (9103, "labevents", "What was the last Synthetic analyte measurement "
                        "during the first 24 hours?"),
    (9103, "prescriptions", "Was the patient prescribed with any synthetic "
                            "drug during the admission?"),
    (9103, "patient_demographics", "What was the gender of the patient?"),
    (9102, "diagnoses_icd", "What were the billed diagnoses of the "
                            "patient?"),
)
SYNTHETIC_EXPECTED = {"questions": 5, "correct": 5,
                      "answered_out_of_scope": 0}


def synthetic_files(cohort=SYNTHETIC_COHORT, questions=SYNTHETIC_QUESTIONS):
    """Return the member texts of an invented archive."""
    header = "subject_id,hadm_id,stay_id\n"
    files = {"cohort.csv": header + "".join(
        "%d,%d,\n" % (patient, hadm) for patient, hadm, _ in cohort)}
    for split in ("train", "val", "test"):
        files["cohort_%s.csv" % split] = header + "".join(
            "%d,%d,\n" % (patient, hadm)
            for patient, hadm, splits in cohort if split in splits)
    files["qa_event.jsonl"] = "".join(
        json.dumps({"hadm_id": hadm, "q": question, "a": "",
                    "event_type": event}) + "\n"
        for hadm, event, question in questions)
    files["qa_note.jsonl"] = "".join(
        json.dumps({"hadm_id": hadm, "q": "", "a": ""}) + "\n"
        for hadm in sorted({hadm for hadm, _, _ in questions}))
    return files


def archive_bytes(files, sums=None):
    """Return a zip of member texts with its SHA256SUMS (computed unless
    given)."""
    encoded = {name: text.encode("utf-8") for name, text in files.items()}
    if sums is None:
        sums = "".join("%s  %s\n" % (hashlib.sha256(data).hexdigest(), name)
                       for name, data in sorted(encoded.items()))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in sorted(encoded.items()):
            bundle.writestr("synthetic-instr-1.0.0/" + name, data)
        bundle.writestr("synthetic-instr-1.0.0/SHA256SUMS.txt", sums)
    return buffer.getvalue()


def run_synthetic():
    """Partition an invented archive and run the test on it in memory."""
    archive = open_archive(archive_bytes(synthetic_files()))
    document = partition_document(archive, SYNTHETIC_OURS,
                                  {"name": "synthetic"}, {})
    held_out = frozenset(document["partitions"]["held_out"]["hadm_ids"])
    result = run_test(archive, held_out, suite.synthetic_vocabulary(),
                      {"mode": "synthetic"})
    return document, result


# Command Line

def report_partition(document, pin=None):
    """Print the partition counts and the record pin."""
    line = "%-52s %s"
    for name, entry in document["partitions"].items():
        print(line % (name, "admissions %d, patients %d, event questions "
                            "%d, note questions %d" % (
                                entry["admissions"], entry["patients"],
                                sum(entry["event_questions"].values()),
                                entry["note_questions"])))
    for key, value in document["excluded"].items():
        print(line % ("excluded " + key, value))
    if pin:
        print(line % ("partition record", "%d bytes, sha256 %s" % pin))


def report_test(document):
    """Print the held-out result: aggregate counts and digests only."""
    line = "%-44s %s"
    questions, correct = document["questions"], document["correct"]
    print(line % ("test", document["test"]))
    print(line % ("questions", questions))
    print(line % ("as expected", "%d (%.1f%%)" % (
        correct, 100.0 * correct / questions if questions else 0.0)))
    print(line % ("out-of-scope questions answered",
                  document["answered_out_of_scope"]))
    print(line % ("questions with scope phrases",
                  document["questions_with_scope_phrases"]))
    for family, entry in document["families"].items():
        print(line % (family, "%d questions, %d as expected" % (
            entry["questions"], entry["correct"])))
        for found, count in entry["readings"].items():
            print(line % ("  " + found, count))
    for key, value in document["rules"].items():
        print(line % ("rules " + key, value))
    for key, value in document["inputs"].items():
        print(line % ("input " + key, value))
    print(line % ("summary_sha256", document["summary_sha256"]))
    print(line % ("elapsed_seconds", document["elapsed_seconds"]))


def main(argv=None):
    """Run the synthetic default mode or an operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="use the pinned dataset archive; prints "
                             "aggregate counts only")
    parser.add_argument("--partition", action="store_true",
                        help="publish the partition record (writes one new "
                             "protected directory)")
    parser.add_argument("--held-out", action="store_true",
                        help="run the test on the held-out partition "
                             "(writes nothing)")
    args = parser.parse_args(argv)
    if not args.operator_real and (args.partition or args.held_out):
        print("FAILED: --partition and --held-out need --operator-real")
        return 2
    if args.operator_real and args.partition == args.held_out:
        print("FAILED: give exactly one of --partition and --held-out")
        return 2
    try:
        if args.operator_real and args.partition:
            document, pin = run_partition()
            report_partition(document, pin)
        elif args.operator_real:
            report_test(run_held_out())
        else:
            document, result = run_synthetic()
            report_partition(document)
            report_test(result)
            if any(result[key] != value
                   for key, value in SYNTHETIC_EXPECTED.items()):
                print("FAILED: synthetic_result_unexpected")
                return 1
    except (HeldOutError, readers.ReaderError, registry.RegistryError,
            resolve.ResolutionError) as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
