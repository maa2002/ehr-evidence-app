#!/usr/bin/env python3
"""Case-set preparation: identity, outputs, case facts, admission (30.1).

One run in the GNN environment (CUDA) prepares one case set of the
registry the way Phase 3 prepared the development pilot, from the case
set's pinned selection record and source snapshot:

1. identity mapping: every person of the snapshot is mapped to its graph
   and every visit node to a same-person source visit by the Step 17 rules
   (gnn.mapping), and each mapped case must be exactly the drawn case;
2. model outputs: the pinned bridge regenerates the prediction and drivers
   of every case (gnn.outputs), and the three-record canonical prefix must
   agree with the immutable canonical sample in the same run;
3. case facts: the single-graph prediction is recomputed and must
   reproduce the published label, and its stability inside batches of 64
   and 128 is measured by the run-binding procedure (gnn.binding);
4. admission: label evidence (layers A to C), driver tags from the raw
   graph, and typed admission with restrictions, by the Phase 3 closure
   rules (gnn.admission).

The upstream partition of each case is re-derived from the split replay
verified against the run-binding record and must be the case set's own.
Every input is pinned; the upstream code runs only from its verified bytes
with the tracker offline, the network refused, forbidden imports blocked
and training, data loaders and optimisers refused.

Outputs (one new protected directory, never overwritten):
  mapping.json    patient-bearing identity mapping (Step 17 schema);
  outputs.json    patient-bearing bridge records (Step 16 schema);
  facts.json      patient-bearing single-graph and batch facts per case;
  closure.json    patient-bearing case admission (closure case schema);
  agreement.json  canonical-prefix agreement report;
  summary.json    aggregate counts and file digests only.

Usage:
    python -m ehr_evidence_app.casesets.prepare
    python -m ehr_evidence_app.casesets.prepare --operator-real --case-set L
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import hashlib
import importlib
import os
import sys

import ehr_evidence_app
from ehr_evidence_app.casesets import registry, selection
from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.gnn import admission, binding, mapping, parity
from ehr_evidence_app.gnn import outputs as pmo
from ehr_evidence_app.gnn import provenance as amp
from ehr_evidence_app.storage import policy as guard

CLOSURE_SCHEMA = registry.CLOSURE_SCHEMA
FACTS_SCHEMA = "case-set-facts"
SUMMARY_SCHEMA = "case-set-preparation-summary"
SCHEMA_VERSION = "1.0"
PARTIAL = ".partial"
NAMES = {"mapping": "mapping.json", "outputs": "outputs.json",
         "facts": "facts.json", "closure": "closure.json",
         "agreement": "agreement.json", "summary": "summary.json"}
BUILDER = "ehr_evidence_app.casesets.prepare %s" % ehr_evidence_app.__version__
SOURCE_ROLES = pmo.SOURCE_ROLES
PARSED_ROLES = ("canonical_explanations", "step19_summary", "label_vocab",
                "concept_vocab")
BYTE_ROLES = ("kg_concepts",)
STREAM_ROLES = ("graph", "checkpoint")
ASSET_ROLES = SOURCE_ROLES + PARSED_ROLES + BYTE_ROLES + STREAM_ROLES
EDGE_TYPES = admission.EDGE_TYPES
QUARANTINE_REASONS = ("identity_not_verified",
                      "label_correspondence_not_established",
                      "single_graph_not_reproduced")


class PreparationError(Exception):
    """A fail-closed precondition failure carrying a finding code only."""


def real_plan(label):
    """Return the pinned inputs and the fixed protected output."""
    assets = pmo.real_plan()["assets"]
    return {
        "label": label,
        "upstream_root": pmo.UPSTREAM_ROOT,
        "assets": {name: dict(assets[name]) for name in ASSET_ROLES},
        "selection": registry.pinned(label, "selection"),
        "snapshot_manifest": registry.pinned(label, "snapshot_manifest"),
        "snapshot_dir": registry.snapshot_dir(label),
        "binding_summary": dict(
            admission.real_plan()["inputs"]["binding_summary"]),
        "output_dir": registry.prepared_dir(label),
        "protected_root": registry.PROTECTED_ROOT,
    }


# --------------------------------------------------------------------------
# Pinned small inputs
# --------------------------------------------------------------------------

def snapshot_entries(manifest, label, reference, size):
    """Return the manifest's file records after checking its run state."""
    selection_block = (manifest or {}).get("selection") \
        if isinstance(manifest, dict) else None
    case_set = (selection_block or {}).get("case_set") or {}
    if (not isinstance(manifest, dict)
            or manifest.get("status") != "complete"
            or manifest.get("run_state") != "finished"
            or manifest.get("promoted") is not True
            or manifest.get("version_label") != label):
        raise PreparationError("snapshot_not_complete")
    if (case_set.get("label") != label
            or (case_set.get("selection_record") or {}).get("sha256")
            != reference["sha256"]
            or selection_block.get("cases_requested") != size):
        raise PreparationError("snapshot_selection_mismatch")
    return {entry["file"]: entry for entry in manifest.get("files") or []
            if isinstance(entry, dict) and isinstance(entry.get("file"), str)}


def snapshot_rows(plan, entries, held):
    """Read persons.csv and visits.csv through their manifest records."""
    columns = {"persons.csv": ("person_id", "subject_id"),
               "visits.csv": ("visit_occurrence_id", "person_id")}
    rows, files = {}, {}
    for name, needed in columns.items():
        entry = entries.get(name)
        if (not isinstance(entry, dict) or entry.get("present") is not True
                or not isinstance(entry.get("sha256"), str)
                or not pmo.strict_int(entry.get("size_bytes"))):
            raise PreparationError("snapshot_file_unrecorded")
        pinned = {"path": plan["snapshot_dir"] + "\\" + name,
                  "size_bytes": entry["size_bytes"],
                  "sha256": entry["sha256"].lower()}
        try:
            data = pmo.verify_read(pinned, name, held)
            rows[name] = mapping.parse_rows(data, needed)
        except pmo.Step20Error as error:
            raise PreparationError(str(error)) from None
        except (mapping.MappingError, UnicodeDecodeError, ValueError):
            raise PreparationError("snapshot_file_unreadable") from None
        files[name] = {"sha256": pinned["sha256"],
                       "size_bytes": pinned["size_bytes"]}
    return rows["persons.csv"], rows["visits.csv"], files


# --------------------------------------------------------------------------
# Pure core
# --------------------------------------------------------------------------

def mapping_records(persons, visits, metadata, cases):
    """Return (records in case order, summary) of the identity mapping.

    Every snapshot person is mapped by the Step 17 rules; each case's
    record must name exactly the drawn graph position and bridge index.
    """
    records, findings = mapping.build_records(persons, visits, metadata)
    summary = mapping.summarise(records, metadata,
                                mapping.bridge_positions(metadata), findings)
    if len(records) != len(cases):
        raise PreparationError("snapshot_person_count_mismatch")
    by_subject = {record["subject_id"]: record for record in records}
    ordered = []
    for number, index, position, subject in cases:
        record = by_subject.get(subject)
        if record is None:
            raise PreparationError("snapshot_subject_missing")
        if record["identity_status"] != mapping.STATUS_QUARANTINED and (
                record["patient_index"] != index
                or record["graph_position"] != position):
            raise PreparationError("mapping_case_mismatch")
        ordered.append(record)
    return ordered, summary


def plain_graph(graph):
    """Return the visit count and per-domain node vocabulary and edges."""
    entry = {"visit_count": int(graph["visit"].x.size(0))}
    for domain, edge_type in EDGE_TYPES.items():
        values = graph[domain].x
        nodes = values[:, 0].long().tolist() if values.numel() else []
        edges = graph[edge_type].edge_index
        entry[domain] = {"node_vocab": nodes,
                         "edges": list(zip(edges[0].tolist(),
                                           edges[1].tolist()))}
    return entry


def attachment_classes(plain, record):
    """Return per-domain attachment classes of a record's drivers.

    The rule is the Phase 3 closure's: the raw graph's visit-to-concept
    edges of each driver's concept node, with the target the last visit.
    """
    last = plain["visit_count"] - 1
    result = {}
    for domain in pmo.DOMAINS:
        local = {}
        for node, value in enumerate(plain[domain]["node_vocab"]):
            if value in local:
                raise PreparationError("graph_concept_node_repeated")
            local[value] = node
        reached = collections.defaultdict(set)
        for visit, node in plain[domain]["edges"]:
            reached[node].add(visit)
        classes = []
        for driver in record["drivers"][domain]:
            node = local.get(driver["vocab_index"])
            visits = None if node is None else reached.get(node, set())
            classes.append(admission.attachment(visits, last))
        result[domain] = classes
    return result


def case_fact(row, partition):
    """Return one case's single-graph and batch facts."""
    return {"upstream_partition": partition,
            "single_graph_reproduces_published":
                bool(row["reproduces_published"]),
            "batch": {key: {"argmax_unchanged":
                                bool(cell["argmax_unchanged"]),
                            "selected_label_rank": cell["selected_rank"]}
                      for key, cell in sorted(row["batches"].items())}}


def case_entry(number, case, record, output, fact, tags, label_ok, layer_c):
    """Return one closure case entry (the Phase 3 closure case schema)."""
    correspondence = label_ok and layer_c
    batch_sensitive = not all(cell["argmax_unchanged"]
                              for cell in fact["batch"].values())
    excluded = sum(entry["target_outcome_concept"]
                   for entries in tags.values() for entry in entries)
    eligible = sum(entry["narration_eligible"]
                   for entries in tags.values() for entry in entries)
    target_attached = any(
        entry["narration_eligible"]
        and entry["attachment"] in (admission.TARGET_ONLY,
                                    admission.TARGET_AND_EARLIER)
        for entries in tags.values() for entry in entries)
    reasons = []
    if record.get("identity_status") != mapping.STATUS_VERIFIED:
        reasons.append("identity_not_verified")
    if not correspondence:
        reasons.append("label_correspondence_not_established")
    if not fact["single_graph_reproduces_published"]:
        reasons.append("single_graph_not_reproduced")
    restrictions = []
    if batch_sensitive:
        restrictions.append("batch_sensitive_prediction")
    if target_attached:
        restrictions.append("target_attached_drivers_present")
    if excluded:
        restrictions.append("target_outcome_entries_excluded")
    admitted = not reasons
    return {
        "case_position": number - 1,
        "patient_index": case[1],
        "graph_position": case[2],
        "mapping_record_sha256": amp.digest(record),
        "output_record_sha256": amp.digest(output),
        "admission": {"status": (admission.ADMITTED if admitted
                                 else admission.QUARANTINED),
                      "scope": admission.CONFIG["admission_scope"],
                      "restrictions": restrictions,
                      "quarantine_reasons": reasons},
        "label_evidence": {"A": "established", "B": label_ok,
                           "C": "verified" if layer_c else "not_verified",
                           "correspondence": ("established" if correspondence
                                              else "not_established")},
        "prediction": {"definition": "single_graph_bridge",
                       "single_graph_reproduces_published":
                           fact["single_graph_reproduces_published"],
                       "batch_sensitive": batch_sensitive},
        "upstream_partition": fact["upstream_partition"],
        "drivers": tags,
        "narration_eligible_drivers": eligible,
        "excluded_driver_entries": excluded,
        "explanation_eligible": admitted and eligible > 0,
    }


def aggregate(entries, mapping_summary, report):
    """Return aggregate counts over the case entries."""
    reasons = collections.Counter(
        reason for entry in entries
        for reason in entry["admission"]["quarantine_reasons"])
    restrictions = collections.Counter(
        code for entry in entries
        for code in entry["admission"]["restrictions"])
    partitions = collections.Counter(entry["upstream_partition"]
                                     for entry in entries)
    return {
        "cases": len(entries),
        "admitted": sum(entry["admission"]["status"] == admission.ADMITTED
                        for entry in entries),
        "quarantine_reasons": {reason: reasons.get(reason, 0)
                               for reason in QUARANTINE_REASONS},
        "restrictions": {code: restrictions.get(code, 0)
                         for code in vocab.RESTRICTIONS},
        "explanation_eligible": sum(entry["explanation_eligible"]
                                    for entry in entries),
        "narration_eligible_drivers": sum(
            entry["narration_eligible_drivers"] for entry in entries),
        "excluded_driver_entries": sum(entry["excluded_driver_entries"]
                                       for entry in entries),
        "partitions": {name: partitions.get(name, 0)
                       for name in registry.PARTITIONS},
        "identity": {key: mapping_summary[key] for key in (
            "verified", "verified_with_repeated_visit_nodes", "quarantined",
            "visit_nodes_verified")},
        "canonical_agreement": report["status"],
    }


def build_records(label, value, cases, mapping_ordered, mapping_summary,
                  outputs, rows, plains, report, label_vocab, inverse,
                  layer_c, partition_of, refs):
    """Return {name: document} for every published file of a case set."""
    entries, facts = [], []
    for case, record, output, row in zip(cases, mapping_ordered, outputs,
                                         rows):
        partition = partition_of[case[1]]
        if partition != value["partition"]:
            raise PreparationError("case_partition_mismatch")
        fact = case_fact(row, partition)
        classes = attachment_classes(plains[case[1]], output)
        tags, mismatches = admission.tag_drivers(output, classes, inverse)
        if mismatches:
            raise PreparationError("driver_decode_mismatch")
        label_ok = admission.layer_b(output, label_vocab)
        entries.append(case_entry(case[0], case, record, output, fact, tags,
                                  label_ok, layer_c))
        facts.append(dict(fact, case_position=case[0] - 1,
                          patient_index=case[1]))
    counts = aggregate(entries, mapping_summary, report)
    documents = {
        "mapping": {"schema": mapping.SCHEMA,
                    "schema_version": mapping.SCHEMA_VERSION,
                    "step": "17", "script_version": mapping.VERSION,
                    "scope": {"source_pilot_version": label,
                              "case_set": label,
                              "persons_in_scope": len(mapping_ordered)},
                    "inputs": {name: refs[name] for name in (
                        "graph", "explain_patient", "snapshot_manifest",
                        "snapshot_files")},
                    "summary": mapping_summary, "phase4_eligible": False,
                    "records": mapping_ordered},
        "outputs": outputs,
        "facts": {"schema": FACTS_SCHEMA, "schema_version": SCHEMA_VERSION,
                  "case_set": label, "batch_sizes": list(binding.BATCH_SIZES),
                  "companion_seed": binding.COMPANION_SEED,
                  "patient_bearing": True, "cases": facts},
        "agreement": report,
    }
    body = {"schema": CLOSURE_SCHEMA, "schema_version": SCHEMA_VERSION,
            "case_set": label, "status": "closed", "spec": value,
            "patient_bearing": True, "builder": BUILDER,
            "admission_rule": admission.CONFIG["admission_scope"],
            "inputs": dict(refs, mapping_file_sha256=hashlib.sha256(
                binding.encode(documents["mapping"])).hexdigest(),
                outputs_file_sha256=hashlib.sha256(
                    pmo.encode(outputs, sort_keys=False)).hexdigest()),
            "cases": entries, "counts": counts}
    documents["closure"] = dict(body, preparation={
        "fingerprint_sha256": amp.digest(body)})
    return documents, counts


def encode_files(documents, counts, label):
    """Return {file name: bytes}, with the summary pinning every file."""
    files = {NAMES["outputs"]: pmo.encode(documents["outputs"],
                                          sort_keys=False)}
    for name in ("mapping", "facts", "closure", "agreement"):
        files[NAMES[name]] = binding.encode(documents[name])
    summary = {"schema": SUMMARY_SCHEMA, "schema_version": SCHEMA_VERSION,
               "case_set": label, "counts": counts,
               "files": {name: {"size_bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest()}
                         for name, data in sorted(files.items())}}
    files[NAMES["summary"]] = binding.encode(summary)
    return files, summary


# --------------------------------------------------------------------------
# Real runtime (GNN interpreter, CUDA)
# --------------------------------------------------------------------------

class TorchRuntime:
    """Runs the upstream-dependent sequence once for one case set."""

    def run(self, plan, bound, held, selection_items, step19, canonical,
            vocabularies, trips):
        """Return plain metadata, structures, records, report and rows."""
        torch = importlib.import_module("torch")
        indices = [item["patient_index"] for item in selection_items]
        with pmo.torch_tripwires(torch, trips):
            pmo.require_cuda(torch)
            bridge = pmo.pinned_bridge(plan, bound)
            pmo.check_upstream_modules(plan, trips)
            trips.check()
            pmo.confirm_inputs(held)
            graphs = bound["streams"]["graph"].load(pmo.torch_graph_loader)
            pmo.confirm_inputs(held)
            pmo.check_selection_graphs(graphs, selection_items)
            metadata = [mapping.graph_metadata(graph) for graph in graphs]
            filtered = [graph for graph in graphs
                        if parity.is_diagnosis_graph(graph)]
            plains = {index: plain_graph(filtered[index])
                      for index in indices}
            del filtered
            processed = bridge.preprocess(graphs)
            pmo.check_preprocessed(graphs, processed, step19)
            del graphs
            model = bridge.load_model(pmo.DEVICE, vocabularies)
            pmo.confirm_inputs(held)
            pmo.prepare_model(model)
            union = sorted(set(indices) | set(pmo.CANONICAL_PREFIX))
            # The bridge moves a graph to the device in place, so records are
            # generated from clones and the batch measurement below starts
            # from the untouched preprocessed collection, as in the run
            # binding.
            view = list(processed)
            for index in union:
                view[index] = processed[index].clone()
            records = pmo.generate(bridge, model, view, union)
            del view
            report = pmo.compare_canonical(
                [records[index] for index in pmo.CANONICAL_PREFIX],
                canonical)
            published = {index: records[index]["predicted_diagnosis"][
                "label_index"] for index in indices}
            batch = binding.TorchRuntime()
            rows = batch.batch_rows(model, processed, selection_items,
                                    published, torch.device(pmo.DEVICE))
            pmo.check_model(model)
            versions = batch.versions()
        pmo.check_upstream_modules(plan, trips)
        trips.check()
        return {"metadata": metadata, "plains": plains,
                "records": [records[index] for index in indices],
                "report": report, "rows": rows, "versions": versions}


def verify_assets(plan, stack):
    """Verify every pinned upstream asset; parse only the verified bytes."""
    held, raw = [], {}
    for name in SOURCE_ROLES + PARSED_ROLES + BYTE_ROLES:
        raw[name] = pmo.verify_read(plan["assets"][name], name, held)
    streams = {name: pmo.verify_stream(plan["assets"][name], name, held,
                                       stack)
               for name in STREAM_ROLES}
    pmo.confirm_inputs(held)
    documents = {}
    for name in PARSED_ROLES:
        try:
            documents[name] = mapping.parse_json(raw[name])
        except (UnicodeDecodeError, ValueError):
            raise PreparationError("input_not_json:%s" % name) from None
    bound = {"bytes": {name: raw[name] for name in SOURCE_ROLES + BYTE_ROLES},
             "streams": streams}
    return held, documents, bound


def output_preconditions(plan):
    """Refuse an existing, unprotected, upstream or overlapping output."""
    inputs = [entry["path"] for entry in plan["assets"].values()]
    inputs += [plan["selection"]["path"], plan["snapshot_manifest"]["path"],
               plan["binding_summary"]["path"]]
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        if guard.is_within(path, plan["upstream_root"]):
            raise PreparationError("output_is_upstream")
        for input_path in inputs:
            if (guard.is_within(input_path, path)
                    or guard.is_within(path, os.path.dirname(input_path))):
                raise PreparationError("output_overlaps_input")
        if os.path.lexists(path):
            raise PreparationError("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise PreparationError(problem)


def publish(plan, files, held):
    """Write every file through a new .partial directory and rename it."""
    partial = plan["output_dir"] + PARTIAL
    os.makedirs(os.path.dirname(plan["output_dir"]), exist_ok=True)
    os.mkdir(partial)
    try:
        for name, data in sorted(files.items()):
            with open(os.path.join(partial, name), "xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            pmo.confirm_inputs(held, rehash=True)
        except pmo.Step20Error as error:
            raise PreparationError(str(error)) from None
        try:
            os.rename(partial, plan["output_dir"])
        except OSError:
            raise PreparationError("output_rename_failed") from None
    except BaseException:
        for name in files:
            target = os.path.join(partial, name)
            if os.path.lexists(target):
                os.remove(target)
        if os.path.isdir(partial):
            os.rmdir(partial)
        raise
    return {name: hashlib.sha256(data).hexdigest()
            for name, data in sorted(files.items())}


def prepare(plan, runtime):
    """Verify, run, build and publish; return (summary, digests)."""
    label = plan["label"]
    value = registry.spec(label)
    problem = pmo.pinned_hash_problem(plan)
    if problem:
        raise PreparationError(problem)
    output_preconditions(plan)
    try:
        cases, reference = registry.read_selection(label, plan["selection"])
    except registry.RegistryError as error:
        raise PreparationError(str(error)) from None
    held = []
    try:
        manifest_bytes = pmo.verify_read(plan["snapshot_manifest"],
                                         "snapshot_manifest", held)
        summary_bytes = pmo.verify_read(plan["binding_summary"],
                                        "binding_summary", held)
    except pmo.Step20Error as error:
        raise PreparationError(str(error)) from None
    try:
        manifest = mapping.parse_json(manifest_bytes)
        binding_summary = mapping.parse_json(summary_bytes)
    except (UnicodeDecodeError, ValueError):
        raise PreparationError("input_not_json") from None
    entries = snapshot_entries(manifest, label, reference, value["size"])
    persons, visits, snapshot_files = snapshot_rows(plan, entries, held)
    try:
        facts = selection.split_facts(binding_summary)
    except selection.SelectionError as error:
        raise PreparationError(str(error)) from None
    checks = binding_summary["checks"]
    layer_c = (checks.get("pos_weight_bitwise_equal") is True
               and checks.get("reversed_label_control_failed") is True)
    selection_items = [{"patient_index": case[1], "graph_position": case[2],
                        "subject_id": case[3]} for case in cases]
    with contextlib.ExitStack() as stack:
        try:
            asset_held, documents, bound = verify_assets(plan, stack)
        except pmo.Step20Error as error:
            raise PreparationError(str(error)) from None
        held.extend(asset_held)
        try:
            step19 = pmo.check_step19(documents["step19_summary"], plan)
            canonical = pmo.canonical_prefix(
                documents["canonical_explanations"])
            label_vocab, inverse = admission.check_vocabularies(
                documents["label_vocab"], documents["concept_vocab"])
        except (pmo.Step20Error, admission.AdmissionError) as error:
            raise PreparationError(str(error)) from None
        vocabularies = {name: documents[name]
                        for name in ("label_vocab", "concept_vocab")}
        trips = pmo.Tripwires()
        try:
            with pmo.isolated(trips):
                run = runtime.run(plan, bound, held, selection_items, step19,
                                  canonical, vocabularies, trips)
            trips.check()
        except pmo.Step20Error as error:
            raise PreparationError(trips.hits[0] if trips.hits
                                   else str(error)) from None
        if run["report"]["status"] != "passed":
            raise PreparationError("canonical_agreement_failed")
        try:
            partitions = selection.verified_partitions(
                len(mapping.bridge_positions(run["metadata"])), facts)
        except selection.SelectionError as error:
            raise PreparationError(str(error)) from None
        partition_of = {index: name for name, items in partitions.items()
                        for index in items}
        ordered, mapping_summary = mapping_records(
            persons, visits, run["metadata"], cases)
        refs = {"graph": {"sha256": plan["assets"]["graph"]["sha256"].lower(),
                          "size_bytes": plan["assets"]["graph"]["size_bytes"]},
                "explain_patient": {"sha256": plan["assets"][
                    "explain_patient"]["sha256"].lower()},
                "checkpoint": {"sha256": plan["assets"]["checkpoint"][
                    "sha256"].lower()},
                "selection": reference,
                "snapshot_manifest": {
                    "sha256": plan["snapshot_manifest"]["sha256"].lower(),
                    "size_bytes": len(manifest_bytes)},
                "snapshot_files": snapshot_files,
                "binding_summary": {
                    "sha256": plan["binding_summary"]["sha256"].lower()},
                "runtime": run["versions"]}
        records = [pmo.normalise_record(record) for record in run["records"]]
        for record in records:
            problem = pmo.schema_problem(record)
            if problem:
                raise PreparationError(problem)
        documents_out, counts = build_records(
            label, value, cases, ordered, mapping_summary, records,
            run["rows"], run["plains"], run["report"], label_vocab, inverse,
            layer_c, partition_of, refs)
        files, summary = encode_files(documents_out, counts, label)
        digests = publish(plan, files, held)
    return summary, digests


def run_real(label, plan=None, runtime=None):
    """Operator-only real run in the GNN interpreter (CUDA)."""
    try:
        registry.load_config()
        registry.spec(label)
        plan = plan or real_plan(label)
    except registry.RegistryError as error:
        raise PreparationError(str(error)) from None
    if guard.fixed_policy_problem():
        raise PreparationError("fixed_policy_rejected")
    problem = pmo.forbidden_preloaded_problem()
    if problem:
        raise PreparationError(problem)
    return prepare(plan, runtime or TorchRuntime())


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

def synthetic_inputs(count=3):
    """Return invented pure-core inputs for a small case set."""
    metadata = selection.synthetic_metadata()
    positions = mapping.bridge_positions(metadata)
    graph_of = {index: position for position, index in positions.items()}
    facts = selection.synthetic_facts(metadata)
    pool = selection.verified_partitions(facts["graphs_filtered"],
                                         facts)["test"]
    chosen = [index for index in sorted(pool)
              if metadata[graph_of[index]]["visit_node_count"] >= 2
              and metadata[graph_of[index]]["subject_id"]][:count]
    cases = [(number, index, graph_of[index],
              metadata[graph_of[index]]["subject_id"])
             for number, index in enumerate(chosen, start=1)]
    persons = [{"person_id": str(7700000 + case[0]),
                "subject_id": str(case[3])} for case in cases]
    visits = [{"visit_occurrence_id": str(visit),
               "person_id": str(7700000 + case[0])}
              for case in cases
              for visit in metadata[case[2]]["hadm_ids"]]
    plains, records, rows = {}, [], []
    for case in cases:
        visit_count = metadata[case[2]]["visit_node_count"]
        last = visit_count - 1
        plains[case[1]] = {
            "visit_count": visit_count,
            "condition": {"node_vocab": [0, 1],
                          "edges": [(0, 0), (last, 0), (last, 1)]},
            "procedure": {"node_vocab": [0], "edges": [(0, 0)]},
            "drug": {"node_vocab": [0], "edges": [(last, 0)]}}
        records.append({
            "patient_index": case[1],
            "predicted_diagnosis": {"label_index": 1, "concept_id": 9002,
                                    "name": "SYNTHETIC LABEL", "logit": 0.5,
                                    "probability": 0.6224593312018546},
            "drivers": {
                "condition": [{"vocab_index": 0, "concept_id": 1001,
                               "name": "SYNTHETIC A", "score": 0.25},
                              {"vocab_index": 1, "concept_id": 1002,
                               "name": "SYNTHETIC B", "score": 0.0}],
                "procedure": [{"vocab_index": 0, "concept_id": 2001,
                               "name": "SYNTHETIC C", "score": 0.125}],
                "drug": [{"vocab_index": 0, "concept_id": 3001,
                          "name": "SYNTHETIC D", "score": 0.5}]}})
        rows.append({"reproduces_published": True, "batches": {
            str(size): {"argmax_unchanged": case[0] != 2,
                        "selected_shift": 0.0, "max_shift": 0.0,
                        "selected_rank": 1}
            for size in binding.BATCH_SIZES}})
    label_vocab = [9001, 9002]
    inverse = {"condition": {0: 1001, 1: 1002}, "procedure": {0: 2001},
               "drug": {0: 3001}}
    return {"metadata": metadata, "cases": cases, "persons": persons,
            "visits": visits, "plains": plains, "records": records,
            "rows": rows, "label_vocab": label_vocab, "inverse": inverse}


SYNTHETIC_SPEC = {"purpose": "development", "partition": "test", "size": 3,
                  "seed": 7, "min_visit_nodes": 2, "excluded_case_sets": [],
                  "statement": "Invented case set."}


def run_synthetic():
    """Build an invented case-set record in memory; nothing is written."""
    inputs = synthetic_inputs()
    ordered, mapping_summary = mapping_records(
        inputs["persons"], inputs["visits"], inputs["metadata"],
        inputs["cases"])
    partition_of = {case[1]: "test" for case in inputs["cases"]}
    documents, counts = build_records(
        "synthetic-3-v1", SYNTHETIC_SPEC, inputs["cases"], ordered,
        mapping_summary, inputs["records"], inputs["rows"], inputs["plains"],
        {"status": "passed", "compared_records": 3}, inputs["label_vocab"],
        inputs["inverse"], True, partition_of, {
            "graph": {}, "explain_patient": {}, "snapshot_manifest": {},
            "snapshot_files": {}})
    files, summary = encode_files(documents, counts, "synthetic-3-v1")
    return documents, summary, files


def report(summary, digests=None):
    """Print aggregate counts only."""
    counts = summary["counts"]
    lines = [("case set", summary["case_set"]),
             ("cases", counts["cases"]), ("admitted", counts["admitted"])]
    lines += [("quarantined %s" % reason, count)
              for reason, count in counts["quarantine_reasons"].items()]
    lines += [("restriction %s" % code, count)
              for code, count in counts["restrictions"].items()]
    lines += [("explanation eligible", counts["explanation_eligible"]),
              ("narration-eligible drivers",
               counts["narration_eligible_drivers"]),
              ("excluded driver entries", counts["excluded_driver_entries"]),
              ("partitions", ", ".join("%s %d" % item for item in
                                       counts["partitions"].items())),
              ("identity", ", ".join("%s %d" % item for item in
                                     counts["identity"].items())),
              ("canonical agreement", counts["canonical_agreement"])]
    lines += sorted((digests or {}).items())
    for name, value in lines:
        print("%-52s %s" % (name, value))


def main(argv=None):
    """Run the synthetic default mode or the operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="prepare a real case set (GNN interpreter, "
                             "CUDA); writes one new protected directory")
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
            _, summary, _ = run_synthetic()
            report(summary)
    except (PreparationError, registry.RegistryError,
            selection.SelectionError) as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
