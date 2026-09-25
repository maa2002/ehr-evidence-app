#!/usr/bin/env python3
"""Seeded case-set selection from the upstream partitions (work item 30.1).

For one case set of the registry, the pinned graph collection is read once
on the CPU (GNN environment) for the metadata the eligibility rule needs:
subject, diagnosis-label presence and visit identifiers. No model is loaded.
The upstream split is replayed over the bridge-filtered positions and every
partition is checked against the digests in the verified run-binding
record, so the partition of each drawn case is established, not assumed.
Cases of the excluded case sets (the development pilot) are read from their
pinned records and removed. The eligible cases of the named partition are
then drawn by the registry's seeded rule.

Outputs (one new protected directory, never overwritten):
  selection.json  patient-bearing: per case, bridge index, graph position,
                  subject and visit-node count;
  summary.json    aggregate: counts by reason, visit-node statistics and
                  the verified partition digests.

Usage:
    python -m ehr_evidence_app.casesets.selection
    python -m ehr_evidence_app.casesets.selection --operator-real --case-set L
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import os
import statistics
import sys

import ehr_evidence_app
from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import readers
from ehr_evidence_app.gnn import admission, binding, mapping
from ehr_evidence_app.gnn import outputs as pmo
from ehr_evidence_app.gnn import provenance as amp
from ehr_evidence_app.storage import policy as guard

SUMMARY_SCHEMA = "case-set-selection-summary"
SCHEMA_VERSION = "1.0"
PARTIAL = ".partial"
PILOT = readers.PILOT_LABEL
BUILDER = "ehr_evidence_app.casesets.selection %s" % ehr_evidence_app.__version__
SKIP_REASONS = ("excluded_case_set", "subject_unreadable",
                "visit_identifiers_unreadable", "too_few_visit_nodes")


class SelectionError(Exception):
    """A fail-closed precondition failure carrying a finding code only."""


def real_plan(label):
    """Return the pinned inputs and the fixed protected output."""
    return {
        "label": label,
        "graph": dict(pmo.real_plan()["assets"]["graph"]),
        "binding_summary": dict(
            admission.real_plan()["inputs"]["binding_summary"]),
        "readers": readers.real_plan(),
        "output_dir": registry.selection_dir(label),
        "protected_root": registry.PROTECTED_ROOT,
    }


# --------------------------------------------------------------------------
# Pure core
# --------------------------------------------------------------------------

def split_facts(summary):
    """Return the verified split facts of a run-binding summary."""
    if (not isinstance(summary, dict)
            or summary.get("schema") != binding.SCHEMA
            or summary.get("status") != binding.VERIFIED):
        raise SelectionError("binding_not_verified")
    checks = summary.get("checks")
    if (not isinstance(checks, dict)
            or checks.get("split_replay_matches_upstream") is not True
            or not all(value is True for value in checks.values())):
        raise SelectionError("binding_not_verified")
    body = {key: value for key, value in summary.items()
            if key != "preparation"}
    if (summary.get("preparation") or {}).get(
            "fingerprint_sha256") != binding.digest(body):
        raise SelectionError("binding_fingerprint_mismatch")
    split = summary.get("split") or {}
    arguments = summary.get("run_arguments") or {}
    digests = split.get("partition_sha256") or {}
    counts = split.get("counts") or {}
    if (type(arguments.get("seed")) is not int
            or not isinstance(arguments.get("val_frac"), float)
            or not isinstance(arguments.get("test_frac"), float)
            or not pmo.strict_int(split.get("graphs_filtered"), 1)
            or set(digests) != set(registry.PARTITIONS)
            or set(counts) != set(registry.PARTITIONS)):
        raise SelectionError("binding_split_unexpected")
    return {"seed": arguments["seed"], "val_frac": arguments["val_frac"],
            "test_frac": arguments["test_frac"],
            "graphs_filtered": split["graphs_filtered"],
            "partition_sha256": dict(digests), "counts": dict(counts)}


def verified_partitions(filtered, facts):
    """Replay the split and check it against the run-binding digests."""
    if filtered != facts["graphs_filtered"]:
        raise SelectionError("graph_count_mismatch")
    partitions = binding.replay_split(filtered, facts["val_frac"],
                                      facts["test_frac"], facts["seed"])
    for name in registry.PARTITIONS:
        if (len(partitions[name]) != facts["counts"][name]
                or binding.partition_digest(partitions[name])
                != facts["partition_sha256"][name]):
            raise SelectionError("partition_digest_mismatch")
    return partitions


def skip_reason(item, index, excluded, minimum):
    """Return why one graph is ineligible, or None."""
    if index in excluded:
        return "excluded_case_set"
    if item["subject_id"] is None or item["subject_id"] < 1:
        return "subject_unreadable"
    hadm_ids = item["hadm_ids"]
    if (hadm_ids is None or item["visit_node_count"] is None
            or len(hadm_ids) != item["visit_node_count"]
            or any(mapping.canonical_int(value) is None
                   for value in hadm_ids)):
        return "visit_identifiers_unreadable"
    if item["visit_node_count"] < minimum:
        return "too_few_visit_nodes"
    return None


def rank(label, seed, index):
    """Return the seeded rank key of one bridge index."""
    return hashlib.sha256(("%s:%d:%d" % (label, seed, index)).encode(
        "ascii")).hexdigest()


def draw(label, value, metadata, partitions, excluded):
    """Return (selected cases, counts) for one case-set specification."""
    positions = mapping.bridge_positions(metadata)
    graph_of = {index: position for position, index in positions.items()}
    pool = partitions[value["partition"]]
    skipped = collections.Counter()
    eligible = []
    for index in pool:
        item = metadata[graph_of[index]]
        reason = skip_reason(item, index, excluded,
                             value["min_visit_nodes"])
        if reason:
            skipped[reason] += 1
        else:
            eligible.append(index)
    if len(eligible) < value["size"]:
        raise SelectionError("too_few_eligible_cases")
    chosen = sorted(sorted(eligible, key=lambda index: rank(
        label, value["seed"], index))[:value["size"]])
    subjects = [metadata[graph_of[index]]["subject_id"] for index in chosen]
    if len(set(subjects)) != len(subjects):
        raise SelectionError("selected_subject_repeated")
    cases = [{"case_number": number, "patient_index": index,
              "graph_position": graph_of[index],
              "subject_id": metadata[graph_of[index]]["subject_id"],
              "visit_node_count":
                  metadata[graph_of[index]]["visit_node_count"]}
             for number, index in enumerate(chosen, start=1)]
    counts = {"partition_size": len(pool), "eligible": len(eligible),
              "skipped": {reason: skipped.get(reason, 0)
                          for reason in SKIP_REASONS},
              "selected": len(cases)}
    return cases, counts


def visit_statistics(cases):
    """Return aggregate visit-node statistics of the selected cases."""
    counts = [case["visit_node_count"] for case in cases]
    return {"min": min(counts), "median": statistics.median(counts),
            "max": max(counts), "total": sum(counts)}


def build_records(label, value, cases, counts, facts, input_refs):
    """Return (selection document, summary) for a drawn case set."""
    body = {"schema": registry.SELECTION_SCHEMA,
            "schema_version": SCHEMA_VERSION, "case_set": label,
            "spec": value, "draw_rule": registry.DRAW_RULE,
            "eligibility": list(registry.ELIGIBILITY),
            "patient_bearing": True, "builder": BUILDER,
            "inputs": input_refs, "counts": counts, "cases": cases}
    fingerprint = amp.digest(body)
    document = dict(body, preparation={"fingerprint_sha256": fingerprint})
    summary = {"schema": SUMMARY_SCHEMA, "schema_version": SCHEMA_VERSION,
               "case_set": label, "spec": value, "counts": counts,
               "visit_nodes": visit_statistics(cases),
               "partition_sha256": facts["partition_sha256"],
               "partitions_verified": True,
               "selection_file": {
                   "name": registry.SELECTION_NAME,
                   "sha256": hashlib.sha256(
                       binding.encode(document)).hexdigest()}}
    return document, summary


# --------------------------------------------------------------------------
# Real run (GNN interpreter, CPU)
# --------------------------------------------------------------------------

def excluded_indices(value, plan):
    """Return the bridge indices of every case in the excluded sets."""
    excluded = set()
    for name in value["excluded_case_sets"]:
        if name == PILOT:
            try:
                cases = readers.read_cases(plan["readers"]).cases
            except readers.ReaderError as error:
                raise SelectionError(str(error)) from None
            excluded.update(case.patient_index for case in cases)
        else:
            raise SelectionError("excluded_case_set_unsupported")
    return excluded


def output_preconditions(plan):
    """Refuse an existing, unprotected or input-overlapping output."""
    inputs = [plan["graph"]["path"], plan["binding_summary"]["path"],
              plan["readers"]["closure"]["path"]]
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        for input_path in inputs:
            if (guard.is_within(input_path, path)
                    or guard.is_within(path, os.path.dirname(input_path))):
                raise SelectionError("output_overlaps_input")
        if os.path.lexists(path):
            raise SelectionError("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise SelectionError(problem)


def publish(plan, files, held):
    """Write both files through a new .partial directory and rename it."""
    partial = plan["output_dir"] + PARTIAL
    os.makedirs(os.path.dirname(plan["output_dir"]), exist_ok=True)
    os.mkdir(partial)
    try:
        for name, data in files.items():
            with open(os.path.join(partial, name), "xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            pmo.confirm_inputs(held, rehash=True)
        except pmo.Step20Error as error:
            raise SelectionError(str(error)) from None
        try:
            os.rename(partial, plan["output_dir"])
        except OSError:
            raise SelectionError("output_rename_failed") from None
    except BaseException:
        for name in files:
            target = os.path.join(partial, name)
            if os.path.lexists(target):
                os.remove(target)
        if os.path.isdir(partial):
            os.rmdir(partial)
        raise
    return {name: hashlib.sha256(data).hexdigest()
            for name, data in files.items()}


def run_real(label, plan=None, loader=mapping.torch_loader):
    """Operator-only real run in the GNN interpreter (CPU is enough)."""
    try:
        registry.load_config()
        value = registry.spec(label)
    except registry.RegistryError as error:
        raise SelectionError(str(error)) from None
    plan = plan or real_plan(label)
    if guard.fixed_policy_problem():
        raise SelectionError("fixed_policy_rejected")
    output_preconditions(plan)
    held = []
    try:
        summary_bytes = pmo.verify_read(plan["binding_summary"],
                                        "binding_summary", held)
        pmo.confirm_inputs(held)
    except pmo.Step20Error as error:
        raise SelectionError(str(error)) from None
    facts = split_facts(mapping.parse_json(summary_bytes))
    excluded = excluded_indices(value, plan)
    try:
        metadata = mapping.load_graph_metadata(plan["graph"], loader)
    except mapping.MappingError as error:
        raise SelectionError(str(error)) from None
    filtered = len(mapping.bridge_positions(metadata))
    partitions = verified_partitions(filtered, facts)
    cases, counts = draw(label, value, metadata, partitions, excluded)
    refs = {"graph": {"sha256": plan["graph"]["sha256"].lower(),
                      "size_bytes": plan["graph"]["size_bytes"]},
            "binding_summary": {
                "sha256": plan["binding_summary"]["sha256"].lower(),
                "size_bytes": len(summary_bytes)},
            "excluded_case_sets": {
                PILOT: {"closure_sha256":
                        plan["readers"]["closure"]["sha256"].lower()}}
            if PILOT in value["excluded_case_sets"] else {}}
    document, summary = build_records(label, value, cases, counts, facts,
                                      refs)
    files = {registry.SELECTION_NAME: binding.encode(document),
             registry.SUMMARY_NAME: binding.encode(summary)}
    return summary, publish(plan, files, held)


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

SYNTHETIC_GRAPHS = 400
SYNTHETIC_SUBJECT_ID_BASE = 5500000


def synthetic_metadata(count=SYNTHETIC_GRAPHS):
    """Return invented graph metadata with some ineligible graphs."""
    metadata = []
    for position in range(count):
        visits = 1 + position % 4
        hadm = [8800000 + position * 10 + node for node in range(visits)]
        item = {"subject_id": SYNTHETIC_SUBJECT_ID_BASE + position,
                "has_y_diagnosis": position % 50 != 7,
                "hadm_ids": hadm, "visit_node_count": visits}
        if position % 97 == 3:
            item["subject_id"] = None
        metadata.append(item)
    return metadata


def synthetic_facts(metadata, seed=42, val_frac=0.1, test_frac=0.15):
    """Return split facts consistent with the invented metadata."""
    filtered = len(mapping.bridge_positions(metadata))
    partitions = binding.replay_split(filtered, val_frac, test_frac, seed)
    return {"seed": seed, "val_frac": val_frac, "test_frac": test_frac,
            "graphs_filtered": filtered,
            "partition_sha256": {name: binding.partition_digest(items)
                                 for name, items in partitions.items()},
            "counts": {name: len(items)
                       for name, items in partitions.items()}}


SYNTHETIC_SPEC = {"purpose": "evaluation", "partition": "test", "size": 20,
                  "seed": 7, "min_visit_nodes": 2,
                  "excluded_case_sets": [PILOT],
                  "statement": "Invented case set."}


def run_synthetic():
    """Draw an invented case set in memory; nothing is read or written."""
    metadata = synthetic_metadata()
    facts = synthetic_facts(metadata)
    partitions = verified_partitions(facts["graphs_filtered"], facts)
    excluded = set(partitions["test"][:3])
    cases, counts = draw("synthetic-20-v1", SYNTHETIC_SPEC, metadata,
                         partitions, excluded)
    return build_records("synthetic-20-v1", SYNTHETIC_SPEC, cases, counts,
                         facts, {"synthetic": True})


def report(summary, digests=None):
    """Print aggregate counts only."""
    counts = summary["counts"]
    visits = summary["visit_nodes"]
    lines = [("case set", summary["case_set"]),
             ("partition", "%s (%d graphs)" % (summary["spec"]["partition"],
                                               counts["partition_size"])),
             ("eligible", counts["eligible"])]
    lines += [("skipped %s" % reason, count)
              for reason, count in counts["skipped"].items()]
    lines += [("selected", counts["selected"]),
              ("visit nodes", "min %d, median %s, max %d, total %d" % (
                  visits["min"], visits["median"], visits["max"],
                  visits["total"]))]
    lines += sorted((digests or {}).items())
    for name, value in lines:
        print("%-36s %s" % (name, value))


def main(argv=None):
    """Run the synthetic default mode or the operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="draw a real case set (GNN interpreter); "
                             "writes one new protected directory")
    parser.add_argument("--case-set", help="registry label of the case set")
    args = parser.parse_args(argv)
    if args.operator_real != (args.case_set is not None):
        print("FAILED: --operator-real and --case-set go together")
        return 2
    try:
        if args.operator_real:
            summary, digests = run_real(args.case_set)
            report(summary, digests)
        else:
            _, summary = run_synthetic()
            report(summary)
    except (SelectionError, registry.RegistryError) as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
