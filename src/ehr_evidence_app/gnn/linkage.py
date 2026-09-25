#!/usr/bin/env python3
"""Driver-to-event linkage for the admitted cases (work item 28).

For every admitted case, each published driver is a concept node of the
patient's raw graph. Its visit-to-concept edges say which visits the model
saw the concept in. This module links each such edge to the source events
of the same person, visit and concept in the bounded source snapshot, and
records, per driver, the attached visits with the native identifiers of the
events behind each edge.

Link status per driver:

* verified: every edge is backed by at least one source event;
* partial: some edges are, some are not;
* unlinked: no edge is;
* no_edges: the concept node has no visit edge (it attaches to nothing);
* not_a_patient_node: the driver's vocabulary index is not a node of the
  patient's graph.

The record also checks that the graph is complete for the snapshot: every
source event in a case visit whose concept is in the model's vocabulary
must appear as an edge. Scores are not read, copied or divided; linkage is
concept-level context for attributions, never a per-event attribution.

Inputs are pinned by SHA-256 (graphs, concept vocabulary, published
outputs, closure record, identity mapping, snapshot manifest and files).
The graphs are deserialised through their verified open handle on the CPU;
no model is loaded and no upstream code runs.

Outputs (one new protected directory, never overwritten):
  linkage.json  patient-bearing: native visit and event identifiers;
  summary.json  aggregate counts and statuses only.

A registered case set is linked the same way from its own pinned records
(--case-set), into its own versioned directory.

Usage:
    python -m ehr_evidence_app.gnn.linkage
    python -m ehr_evidence_app.gnn.linkage --operator-real
    python -m ehr_evidence_app.gnn.linkage --operator-real --case-set L
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import copy
import hashlib
import os
import sys

from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import readers
from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.gnn import binding
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.gnn import outputs as pmo
from ehr_evidence_app.gnn import provenance as amp
from ehr_evidence_app.storage import policy as guard

SCHEMA = "driver-event-linkage"
SUMMARY_SCHEMA = "driver-event-linkage-summary"
SCHEMA_VERSION = "1.0"
OUTPUT_VERSION = "event-linkage-pilot10-v1"
PROTECTED_ROOT = pmo.PROTECTED_ROOT
OUTPUT_DIR = PROTECTED_ROOT + "\\evidence\\" + OUTPUT_VERSION
LINKAGE_NAME = "linkage.json"
SUMMARY_NAME = "summary.json"
PARTIAL = ".partial"
VERSION_PREFIX = "linkage-prep-"
EDGE_RELATIONS = {"condition": "has_condition", "procedure": "has_procedure",
                  "drug": "has_drug"}
LINK_RULE = ("an edge is linked when the source snapshot holds at least one "
             "event of the same person, visit and concept in that domain")


class LinkageError(Exception):
    """A fail-closed precondition failure carrying a finding code only."""


def real_plan(case_set=None):
    """Return the pinned inputs and the fixed protected output.

    Without a case set this is the development pilot; with one, the
    outputs, snapshot and closure are the case set's pinned records.
    """
    assets = pmo.real_plan()["assets"]
    plan = {
        "graph": dict(assets["graph"]),
        "concept_vocab": dict(assets["concept_vocab"]),
        "outputs": dict(amp.real_plan()["inputs"]["step20_outputs"]),
        "readers": readers.real_plan(),
        "version": OUTPUT_VERSION,
        "output_dir": OUTPUT_DIR,
        "protected_root": PROTECTED_ROOT,
    }
    if case_set is not None:
        plan.update(outputs=registry.pinned(case_set, "prepared_outputs"),
                    readers=readers.real_plan(case_set),
                    version="event-linkage-" + case_set,
                    output_dir=registry.linkage_dir(case_set))
    return plan


# --------------------------------------------------------------------------
# Pure core (no torch)
# --------------------------------------------------------------------------

def inverse_vocabulary(concept_vocab):
    """Return {domain: {vocab_index: concept_id}} from the concept vocab."""
    inverse = {}
    for domain in vocab.DOMAINS:
        table = {}
        for concept_id, index in (concept_vocab.get(domain) or {}).items():
            number = mapping.canonical_int(concept_id)
            position = mapping.canonical_int(index)
            if number is None or position is None or position in table:
                raise LinkageError("concept_vocab_invalid")
            table[position] = number
        inverse[domain] = table
    return inverse


def event_index(snapshot, person_id):
    """Return {(domain, visit_id, concept_id): sorted native ids}."""
    index = collections.defaultdict(list)
    for domain in vocab.DOMAINS:
        for event in snapshot.events[domain]:
            if event.person_id == person_id:
                index[(domain, event.visit_occurrence_id,
                       event.concept_id)].append(event.native_id)
    return {key: tuple(sorted(ids)) for key, ids in index.items()}


def link_status(visits):
    """Return a driver's link status from its attached visit entries."""
    if not visits:
        return "no_edges"
    linked = sum(bool(entry["event_ids"]) for entry in visits)
    if linked == len(visits):
        return "verified"
    return "partial" if linked else "unlinked"


def link_case(case, graph, record, inverse, snapshot):
    """Return the linkage entry of one case.

    graph is {"visit_count": N, domain: {"node_vocab": [...],
    "edges": [(visit_position, node), ...]}}; record is the published
    output record of the case.
    """
    if graph["visit_count"] != len(case.visit_ids):
        raise LinkageError("graph_visit_count_mismatch")
    index = event_index(snapshot, case.person_id)
    final = len(case.visit_ids) - 1
    case_visits = set(case.visit_ids)
    domains = {}
    for domain in vocab.DOMAINS:
        nodes = graph[domain]["node_vocab"]
        node_of = {}
        for node, position in enumerate(nodes):
            if position in node_of:
                raise LinkageError("graph_concept_node_repeated")
            node_of[position] = node
        attached = collections.defaultdict(set)
        edge_keys = set()
        verified = 0
        for visit_position, node in graph[domain]["edges"]:
            if not 0 <= visit_position <= final or not 0 <= node < len(nodes):
                raise LinkageError("graph_edge_invalid")
            concept_id = inverse[domain].get(nodes[node])
            if concept_id is None:
                raise LinkageError("graph_concept_outside_vocabulary")
            visit_id = case.visit_ids[visit_position]
            if (visit_id, concept_id) in edge_keys:
                continue
            edge_keys.add((visit_id, concept_id))
            attached[node].add(visit_position)
            verified += bool(index.get((domain, visit_id, concept_id)))
        known = set(inverse[domain].values())
        missing = sum(
            1 for (event_domain, visit_id, concept_id) in index
            if event_domain == domain and visit_id in case_visits
            and concept_id in known
            and (visit_id, concept_id) not in edge_keys)
        drivers = []
        for position, driver in enumerate(record["drivers"][domain]):
            if inverse[domain].get(driver["vocab_index"]) != driver[
                    "concept_id"]:
                raise LinkageError("driver_decode_mismatch")
            node = node_of.get(driver["vocab_index"])
            visits = []
            for visit_position in sorted(attached.get(node, ())):
                visit_id = case.visit_ids[visit_position]
                visits.append({
                    "position": visit_position,
                    "visit_occurrence_id": visit_id,
                    "role": ("final_admission" if visit_position == final
                             else "pre_target"),
                    "event_ids": list(index.get(
                        (domain, visit_id, driver["concept_id"]), ()))})
            drivers.append({
                "position": position, "node_found": node is not None,
                "link_status": (link_status(visits) if node is not None
                                else "not_a_patient_node"),
                "visits": visits})
        domains[domain] = {"edges": len(edge_keys),
                           "edges_verified": verified,
                           "in_vocabulary_events_without_edge": missing,
                           "drivers": drivers}
    return {"case_number": case.case_number,
            "patient_index": case.patient_index,
            "graph_position": case.graph_position,
            "output_record_sha256": case.output_record_sha256,
            "domains": domains}


def aggregate(entries):
    """Return aggregate counts over the case entries."""
    counts = {"cases": len(entries), "domains": {}}
    for domain in vocab.DOMAINS:
        statuses = collections.Counter()
        edges = verified = missing = 0
        for entry in entries:
            block = entry["domains"][domain]
            edges += block["edges"]
            verified += block["edges_verified"]
            missing += block["in_vocabulary_events_without_edge"]
            statuses.update(driver["link_status"]
                            for driver in block["drivers"])
        counts["domains"][domain] = {
            "edges": edges, "edges_verified": verified,
            "in_vocabulary_events_without_edge": missing,
            "drivers": {state: statuses.get(state, 0)
                        for state in vocab.LINK_STATES}}
    return counts


def overall_status(counts):
    """Return verified only when every edge and driver link verifies."""
    for block in counts["domains"].values():
        drivers = block["drivers"]
        if (block["edges_verified"] != block["edges"]
                or block["in_vocabulary_events_without_edge"]
                or drivers["partial"] or drivers["unlinked"]
                or drivers["not_a_patient_node"]):
            return "partial"
    return "verified"


def build_record(plan, entries, input_refs):
    """Return (linkage record, summary) for the case entries."""
    counts = aggregate(entries)
    version = plan.get("version", OUTPUT_VERSION)
    body = {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
            "version": version, "status": overall_status(counts),
            "patient_bearing": True, "case_set": plan["readers"]["label"],
            "rule": LINK_RULE, "inputs": input_refs, "cases": entries,
            "counts": counts}
    value = amp.digest(body)
    record = dict(body, preparation={
        "fingerprint_sha256": value,
        "version": VERSION_PREFIX + value[:16]})
    summary = {"schema": SUMMARY_SCHEMA, "schema_version": SCHEMA_VERSION,
               "version": version, "status": record["status"],
               "counts": counts, "preparation": record["preparation"],
               "linkage_file": {"name": LINKAGE_NAME,
                                "sha256": hashlib.sha256(
                                    binding.encode(record)).hexdigest()}}
    return record, summary


# --------------------------------------------------------------------------
# Real runtime (GNN interpreter)
# --------------------------------------------------------------------------

class GraphRuntime:
    """Extracts plain per-case graph structure from the verified graphs."""

    def case_graphs(self, stream, cases):
        """Return {patient_index: plain graph} for the admitted cases."""
        graphs = stream.load(pmo.torch_graph_loader)
        if not isinstance(graphs, list):
            raise LinkageError("graph_collection_not_list")
        selection = [{"patient_index": case.patient_index,
                      "graph_position": case.graph_position,
                      "subject_id": case.subject_id} for case in cases]
        try:
            pmo.check_selection_graphs(graphs, selection)
        except pmo.Step20Error as error:
            raise LinkageError(str(error)) from None
        plain = {}
        for case in cases:
            graph = graphs[case.graph_position]
            entry = {"visit_count": int(graph["visit"].x.size(0))}
            for domain, relation in EDGE_RELATIONS.items():
                values = graph[domain].x
                nodes = (values[:, 0].long().tolist() if values.numel()
                         else [])
                edges = graph[("visit", relation, domain)].edge_index
                entry[domain] = {"node_vocab": nodes,
                                 "edges": list(zip(edges[0].tolist(),
                                                   edges[1].tolist()))}
            plain[case.patient_index] = entry
        return plain


def output_preconditions(plan, input_paths):
    """Refuse an existing, unprotected or input-overlapping output."""
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        for input_path in input_paths:
            if (guard.is_within(input_path, path)
                    or guard.is_within(path, os.path.dirname(input_path))):
                raise LinkageError("output_overlaps_input")
        if os.path.lexists(path):
            raise LinkageError("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise LinkageError(problem)


def input_paths(plan):
    """Return every pinned input path."""
    reader_plan = plan["readers"]
    return [plan["graph"]["path"], plan["concept_vocab"]["path"],
            plan["outputs"]["path"], reader_plan["manifest"]["path"],
            reader_plan["closure"]["path"], reader_plan["mapping"]["path"],
            reader_plan["snapshot_dir"] + "\\visits.csv"]


def publish(plan, record, summary, held):
    """Write both files through a new .partial directory and rename it."""
    files = {LINKAGE_NAME: binding.encode(record),
             SUMMARY_NAME: binding.encode(summary)}
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
            raise LinkageError(str(error)) from None
        try:
            os.rename(partial, plan["output_dir"])
        except OSError:
            raise LinkageError("output_rename_failed") from None
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


def run_real(plan=None, runtime_factory=GraphRuntime):
    """Operator-only real run in the GNN interpreter (CPU is enough)."""
    plan = plan or real_plan()
    if guard.fixed_policy_problem():
        raise LinkageError("fixed_policy_rejected")
    output_preconditions(plan, input_paths(plan))
    try:
        snapshot = readers.read_snapshot(plan["readers"])
        case_set = readers.read_cases(plan["readers"])
    except readers.ReaderError as error:
        raise LinkageError(str(error)) from None
    cases = [case for case in case_set.cases if case.admitted]
    trips = pmo.Tripwires()
    with contextlib.ExitStack() as stack:
        held = []
        try:
            vocab_bytes = pmo.verify_read(plan["concept_vocab"],
                                          "concept_vocab", held)
            outputs_bytes = pmo.verify_read(plan["outputs"], "outputs", held)
            stream = pmo.verify_stream(plan["graph"], "graph", held, stack)
            pmo.confirm_inputs(held)
        except pmo.Step20Error as error:
            raise LinkageError(str(error)) from None
        inverse = inverse_vocabulary(mapping.parse_json(vocab_bytes))
        outputs = {record["patient_index"]: record
                   for record in mapping.parse_json(outputs_bytes)}
        for case in cases:
            record = outputs.get(case.patient_index)
            if record is None or amp.digest(record) != (
                    case.output_record_sha256):
                raise LinkageError("output_record_mismatch")
        try:
            with pmo.tracker_offline(), pmo.network_blocked(trips), \
                    pmo.silenced():
                graphs = runtime_factory().case_graphs(stream, cases)
            trips.check()
        except pmo.Step20Error as error:
            raise LinkageError(str(error)) from None
        entries = [link_case(case, graphs[case.patient_index],
                             outputs[case.patient_index], inverse, snapshot)
                   for case in cases]
        refs = {name: {"path": entry["path"],
                       "sha256": entry["sha256"].lower()}
                for name, entry in (("graph", plan["graph"]),
                                    ("concept_vocab", plan["concept_vocab"]),
                                    ("outputs", plan["outputs"]))}
        refs.update({"snapshot_manifest": snapshot.manifest_sha256,
                     "closure_record": case_set.closure_sha256,
                     "identity_mapping": case_set.mapping_sha256})
        record, summary = build_record(plan, entries, refs)
        digests = publish(plan, record, summary, held)
    return summary, digests


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

SYNTHETIC_PERSON_ID = 77
SYNTHETIC_SUBJECT_ID = 99


def synthetic_fixture():
    """Return (plan, case, graph, record, inverse, snapshot), invented."""
    Event = readers.SourceEvent
    case = readers.Case(
        case_number=1, patient_index=4, graph_position=5,
        person_id=SYNTHETIC_PERSON_ID, subject_id=SYNTHETIC_SUBJECT_ID,
        visit_ids=(801, 802, 803), admitted=True,
        explanation_eligible=True, restrictions=(), upstream_partition="test",
        output_record_sha256="0" * 64, batch_sensitive=False,
        single_graph_reproduces_published=True,
        label_correspondence="established", drivers={})

    def event(domain, native_id, visit_id, concept_id):
        """Return one invented event."""
        return Event(domain=domain, native_id=native_id,
                     person_id=SYNTHETIC_PERSON_ID,
                     visit_occurrence_id=visit_id, concept_id=concept_id,
                     start_date=None, start_datetime=None, end_date=None,
                     end_datetime=None, source_value=None,
                     source_concept_id=None)

    events = {"condition": (event("condition", 1, 801, 1001),
                            event("condition", 2, 803, 1001),
                            event("condition", 3, 803, 1002)),
              "procedure": (event("procedure", 4, 802, 2001),),
              "drug": (event("drug", 5, 803, 3001),
                       event("drug", 6, 803, 3001),
                       event("drug", 7, 801, 0))}
    snapshot = readers.Snapshot(label="synthetic-1-v1",
                                manifest_sha256="1" * 64,
                                concepts_sha256="2" * 64, complete=True,
                                visits={}, events=events, concepts={})
    inverse = {"condition": {0: 1001, 1: 1002}, "procedure": {0: 2001},
               "drug": {0: 3001}}
    graph = {"visit_count": 3,
             "condition": {"node_vocab": [0, 1],
                           "edges": [(0, 0), (2, 0), (2, 1)]},
             "procedure": {"node_vocab": [0], "edges": [(1, 0)]},
             "drug": {"node_vocab": [0], "edges": [(2, 0)]}}
    record = {"drivers": {
        "condition": [{"vocab_index": 0, "concept_id": 1001},
                      {"vocab_index": 1, "concept_id": 1002}],
        "procedure": [{"vocab_index": 0, "concept_id": 2001}],
        "drug": [{"vocab_index": 0, "concept_id": 3001}]}}
    plan = {"readers": {"label": "synthetic-1-v1"}}
    return plan, case, graph, record, inverse, snapshot


def run_synthetic():
    """Link an invented case; expect every edge and driver verified."""
    plan, case, graph, record, inverse, snapshot = synthetic_fixture()
    entry = link_case(case, graph, record, inverse, snapshot)
    linked, summary = build_record(plan, [entry], {"synthetic": True})
    broken = copy.deepcopy(graph)
    broken["drug"]["edges"].append((0, 0))
    partial_entry = link_case(case, broken, record, inverse, snapshot)
    partial, _ = build_record(plan, [partial_entry], {"synthetic": True})
    if linked["status"] != "verified" or partial["status"] != "partial":
        raise LinkageError("synthetic_expectation_failed")
    return summary, partial


def report(summary, digests=None):
    """Print the aggregate summary."""
    print("Status             : %s" % summary["status"])
    print("Cases              : %d" % summary["counts"]["cases"])
    for domain in vocab.DOMAINS:
        block = summary["counts"]["domains"][domain]
        drivers = block["drivers"]
        print("%-19s: edges %d/%d verified, missing in-vocabulary edges %d; "
              "drivers verified %d, partial %d, unlinked %d, no edges %d, "
              "not a node %d" % (
                  domain, block["edges_verified"], block["edges"],
                  block["in_vocabulary_events_without_edge"],
                  drivers["verified"], drivers["partial"],
                  drivers["unlinked"], drivers["no_edges"],
                  drivers["not_a_patient_node"]))
    print("preparation        : %s" % summary["preparation"]["version"])
    for name, value in sorted((digests or {}).items()):
        print("%-19s: %s" % (name, value))


def main(argv=None):
    """Run the synthetic default mode or the operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="link the real admitted cases (GNN "
                             "interpreter); writes one new protected record")
    parser.add_argument("--case-set",
                        help="link a registered case set instead of the "
                             "development pilot (with --operator-real)")
    args = parser.parse_args(argv)
    if args.case_set is not None and not args.operator_real:
        print("FAILED: --case-set needs --operator-real")
        return 2
    try:
        if args.operator_real:
            try:
                plan = real_plan(args.case_set)
            except registry.RegistryError as error:
                raise LinkageError(str(error)) from None
            summary, digests = run_real(plan)
            report(summary, digests)
        else:
            summary, partial = run_synthetic()
            report(summary)
            print("broken control     : %s" % partial["status"])
    except LinkageError as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    return 0 if summary["status"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main())
