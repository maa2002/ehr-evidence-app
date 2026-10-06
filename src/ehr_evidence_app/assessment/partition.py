"""The downstream partition of the case sets (work item 53; ADR 0028).

The final evaluation's cases must be kept apart from everything the
application was built and tuned on. Cases are partitioned by case set:

* development: the development pilot (pilot-10-v1) and dev-30-v1, on
  which the rules, prompts and checks were built and measured;
* final: eval-100-v1, held for the final evaluation.

A person belongs to one partition: a person found in both stops the
record (person_in_both). Every request about a case, paraphrases
included, goes with its case set and so with its partition
(partition_of). Each case's upstream partition, the GNN's own split as
the case set's closure record holds it, is kept beside the case: only a
case of the upstream test partition may be called unseen by the GNN, and
the record counts those cases, so that no claim of unseen patients goes
beyond them. A case set drawn from one upstream partition must hold only
cases of it (partition_mismatch).

The record holds persons and subjects, so it is protected. It is
published once, as a new directory
protected-data\\assessment\\downstream-partition-v1, and its size and
SHA-256 are then pinned here. The closure records are read through their
pins, for their cases' identities and upstream partitions only; nothing
else of a case set is read. Only aggregate counts are printed.

Usage:
    python -m ehr_evidence_app.assessment.partition
    python -m ehr_evidence_app.assessment.partition --operator-real
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
import typing

import ehr_evidence_app
from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import readers
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.storage import policy as guard

SCHEMA = "downstream-partition"
SCHEMA_VERSION = "1.0"
VERSION = "downstream-partition-v1"
PROTECTED_ROOT = guard.PRIVATE_ROOT + "\\protected-data"
OUTPUT_DIR = PROTECTED_ROOT + "\\assessment\\" + VERSION
RECORD_NAME = "partition.json"
# (size in bytes, SHA-256) of the published record.
PIN = (21322,
       "1718de367b822d2a03a82c8bfc82be28a032db14331a916e481dc756101f5b0c")
PARTITIONS = {"development": (readers.PILOT_LABEL, "dev-30-v1"),
              "final": ("eval-100-v1",)}
# The only upstream partition whose cases the GNN was neither trained nor
# tuned on.
UNSEEN = "test"
RULES = (
    "A case set belongs to one partition: development (the pilot and "
    "dev-30-v1) or final (eval-100-v1).",
    "A person belongs to one partition; a person in both stops the record.",
    "Every request about a case, paraphrases included, goes with its case "
    "set's partition.",
    "A case set drawn from an upstream partition holds only cases of it.",
    "Only a case of the upstream test partition may be called unseen by "
    "the GNN.",
)
BUILDER = "ehr_evidence_app.assessment.partition %s" % (
    ehr_evidence_app.__version__)
PARTIAL = ".partial"


class PartitionError(Exception):
    """A fail-closed failure carrying a finding code only."""


class Member(typing.NamedTuple):
    """What the partition reads of one case."""

    case_number: int
    subject_id: int
    person_id: int
    upstream_partition: str
    admitted: bool


def partition_of(case_set):
    """Return the partition of a case set's cases and of every request
    about them."""
    for name, labels in PARTITIONS.items():
        if case_set in labels:
            return name
    raise PartitionError("case_set_unknown")


def drawn_from(case_set):
    """Return the upstream partition a case set was drawn from, or None for
    the pilot, which was not drawn from one."""
    if case_set == readers.PILOT_LABEL:
        return None
    return registry.spec(case_set)["partition"]


def members(case_set):
    """Return the Members of a readers.CaseSet, in case order."""
    return tuple(Member(case.case_number, case.subject_id, case.person_id,
                        case.upstream_partition, case.admitted)
                 for case in case_set.cases)


def check(case_sets):
    """Raise PartitionError when the case sets break a rule.

    case_sets maps each label to (its drawn-from partition, its Members).
    """
    persons = collections.defaultdict(set)
    for label, (source, found) in case_sets.items():
        partition = partition_of(label)
        numbers = [member.case_number for member in found]
        if len(numbers) != len(set(numbers)):
            raise PartitionError("case_repeated")
        if source is not None and any(
                member.upstream_partition != source for member in found):
            raise PartitionError("partition_mismatch")
        persons[partition].update(member.subject_id for member in found)
    if persons["development"] & persons["final"]:
        raise PartitionError("person_in_both")


def summary(case_sets):
    """Return the aggregate counts of the case sets: nothing of a case."""
    found = {"case_sets": {}, "persons": {}}
    persons = collections.defaultdict(set)
    for label, (source, cases) in sorted(case_sets.items()):
        upstream = collections.Counter(member.upstream_partition
                                       for member in cases)
        found["case_sets"][label] = {
            "partition": partition_of(label), "drawn_from": source,
            "cases": len(cases),
            "admitted": sum(member.admitted for member in cases),
            "upstream": dict(sorted(upstream.items())),
            "unseen_by_gnn": upstream.get(UNSEEN, 0),
            "admitted_unseen_by_gnn": sum(
                member.admitted and member.upstream_partition == UNSEEN
                for member in cases)}
        persons[partition_of(label)].update(member.subject_id
                                            for member in cases)
    for name in PARTITIONS:
        found["persons"][name] = len(persons[name])
    found["persons"]["in_both"] = len(persons["development"]
                                      & persons["final"])
    return found


def partition_document(case_sets, digests):
    """Return the partition record of the case sets, after checking them.

    digests maps each label to the digests of what was read for it.
    """
    check(case_sets)
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "version": VERSION, "builder": BUILDER, "rules": list(RULES),
        "partitions": {name: list(labels)
                       for name, labels in PARTITIONS.items()},
        "case_sets": {label: {
            "partition": partition_of(label), "drawn_from": source,
            "read": digests[label],
            "cases": [dict(member._asdict(), unseen_by_gnn=(
                member.upstream_partition == UNSEEN))
                for member in found]}
            for label, (source, found) in sorted(case_sets.items())},
        "summary": summary(case_sets)}


def record_bytes(document):
    """Return the canonical bytes of a record."""
    return (json.dumps(document, sort_keys=True, separators=(",", ":"))
            + "\n").encode("utf-8")


def output_preconditions(plan):
    """Refuse an existing or unprotected output."""
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        if os.path.lexists(path):
            raise PartitionError("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise PartitionError(problem)


def publish(plan, data):
    """Write the record through a new .partial directory and rename it."""
    partial = plan["output_dir"] + PARTIAL
    target = os.path.join(partial, RECORD_NAME)
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
            raise PartitionError("output_rename_failed") from None
    except BaseException:
        if os.path.lexists(target):
            os.remove(target)
        if os.path.isdir(partial):
            os.rmdir(partial)
        raise


def real_plan():
    """Return the output of the partition record and, once published, its
    pinned path."""
    return {"output_dir": OUTPUT_DIR, "protected_root": PROTECTED_ROOT,
            "record": None if PIN is None else {
                "path": OUTPUT_DIR + "\\" + RECORD_NAME,
                "size_bytes": PIN[0], "sha256": PIN[1]}}


def read_record(plan=None):
    """Return the published partition record, read through its pin."""
    plan = plan or real_plan()
    entry = plan["record"]
    if entry is None:
        raise PartitionError("record_not_pinned")
    try:
        with open(entry["path"], "rb") as handle:
            data = handle.read(entry["size_bytes"] + 1)
    except OSError:
        raise PartitionError("record_unreadable") from None
    if (len(data) != entry["size_bytes"]
            or hashlib.sha256(data).hexdigest() != entry["sha256"]):
        raise PartitionError("record_pin_mismatch")
    document = json.loads(data)
    if (document.get("schema"), document.get("version")) != (SCHEMA,
                                                             VERSION):
        raise PartitionError("record_unexpected")
    return document


def read_case_sets():
    """Return (case sets, digests) of every partitioned case set, read
    through the pinned closure and mapping records."""
    case_sets, digests = {}, {}
    for labels in PARTITIONS.values():
        for label in labels:
            found = readers.read_cases(readers.real_plan(
                None if label == readers.PILOT_LABEL else label))
            case_sets[label] = (drawn_from(label), members(found))
            digests[label] = {"closure_sha256": found.closure_sha256,
                              "mapping_sha256": found.mapping_sha256}
    return case_sets, digests


def run_partition(plan=None):
    """Build and publish the partition record; return it with its pin."""
    plan = plan or real_plan()
    if guard.fixed_policy_problem():
        raise PartitionError("fixed_policy_rejected")
    output_preconditions(plan)
    case_sets, digests = read_case_sets()
    document = partition_document(case_sets, digests)
    data = record_bytes(document)
    publish(plan, data)
    return document, (len(data), hashlib.sha256(data).hexdigest())


# Default Mode: Invented Case Sets

def synthetic_case_sets():
    """Return invented case sets: the pilot from two upstream partitions,
    a development set from validation and a final set from test."""
    def made(start, partitions):
        """Return admitted Members numbered from 1, their persons from
        start."""
        return tuple(Member(number, 9000 + start + number,
                            7000 + start + number, partition, True)
                     for number, partition in enumerate(partitions, 1))
    final = made(20, ("test",) * 4)
    return {readers.PILOT_LABEL: (None, made(0, ("train", "test"))),
            "dev-30-v1": ("validation", made(10, ("validation",) * 3)),
            "eval-100-v1": ("test", final[:3] + (final[3]._replace(
                admitted=False),))}


def refused(case_sets):
    """Return the finding code that stops a record of the case sets, or
    None."""
    try:
        check(case_sets)
    except PartitionError as error:
        return str(error)
    return None


def run_synthetic():
    """Return (summary, problems) of the invented case sets and of their
    broken variants."""
    problems = []
    case_sets = synthetic_case_sets()
    digests = {label: {"closure_sha256": "0" * 64,
                       "mapping_sha256": "0" * 64} for label in case_sets}
    document = partition_document(case_sets, digests)
    found = document["summary"]
    if found["persons"] != {"development": 5, "final": 4, "in_both": 0}:
        problems.append("the persons are miscounted")
    if found["case_sets"]["eval-100-v1"]["admitted_unseen_by_gnn"] != 3:
        problems.append("the unseen cases are miscounted")
    if found["case_sets"][readers.PILOT_LABEL]["unseen_by_gnn"] != 1:
        problems.append("the pilot's test case is not counted apart")
    shared = dict(case_sets)
    source, cases = shared["eval-100-v1"]
    shared["eval-100-v1"] = (source, cases + (cases[0]._replace(
        case_number=99, subject_id=case_sets["dev-30-v1"][1][0].subject_id),))
    if refused(shared) != "person_in_both":
        problems.append("a person in both partitions is not refused")
    mixed = dict(case_sets)
    source, cases = mixed["dev-30-v1"]
    mixed["dev-30-v1"] = (source, cases[:-1] + (cases[-1]._replace(
        upstream_partition="test"),))
    if refused(mixed) != "partition_mismatch":
        problems.append("a case from another upstream partition is not "
                        "refused")
    repeated = dict(case_sets)
    source, cases = repeated["dev-30-v1"]
    repeated["dev-30-v1"] = (source, cases + cases[:1])
    if refused(repeated) != "case_repeated":
        problems.append("a repeated case is not refused")
    try:
        partition_of("other-5-v1")
        problems.append("an unknown case set has a partition")
    except PartitionError:
        pass
    return found, problems


def report(found, pin=None):
    """Print aggregate counts only."""
    for label, counts in found["case_sets"].items():
        print("%s (%s; drawn from %s): %d cases, %d admitted; upstream %s; "
              "unseen by the GNN %d (%d admitted)" % (
                  label, counts["partition"], counts["drawn_from"] or
                  "no partition", counts["cases"], counts["admitted"],
                  ", ".join("%s %d" % item
                            for item in counts["upstream"].items()),
                  counts["unseen_by_gnn"],
                  counts["admitted_unseen_by_gnn"]))
    persons = found["persons"]
    print("persons: development %d, final %d, in both %d" % (
        persons["development"], persons["final"], persons["in_both"]))
    if pin:
        print("record: %d bytes, SHA-256 %s" % pin)


def main(argv=None):
    """Run the synthetic default mode or the operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="publish the partition record of the case "
                             "sets (writes one new protected directory); "
                             "prints aggregate counts only")
    args = parser.parse_args(argv)
    try:
        if args.operator_real:
            document, pin = run_partition()
            report(document["summary"], pin)
        else:
            found, problems = run_synthetic()
            report(found)
            for problem in problems:
                print("FAILED: %s" % problem)
            if problems:
                return 1
    except (PartitionError, readers.ReaderError,
            registry.RegistryError) as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
