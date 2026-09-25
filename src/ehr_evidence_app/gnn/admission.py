#!/usr/bin/env python3
"""Phase 3 closure record: typed admission of the development pilot.

Consumes the published Phase 3 records of the ten development-pilot cases
(identity mapping, preprocessing parity, offline outputs, provenance, split
membership, executed-wrapper recovery) and the run-binding verification,
and writes one new closure record. It supersedes the untyped
phase4_eligible flags and the flags that later evidence contradicts,
without editing any earlier record. Per case it records:

* typed admission to evidence-object construction;
* three label-evidence layers: A (rule, from the pinned sources),
  B (decode of the published record) and C (run binding);
* the prediction definition (single-graph bridge) and whether the
  prediction changes when the graph is evaluated inside a batch;
* the upstream partition, with the held-out definition;
* per-driver tags: score class, visit attachment of the driver's concept
  node in the raw patient graph, whether the entry is a target-visit
  diagnosis, and narration eligibility. No driver value (vocabulary
  index, concept, name or score) is copied.

The record also carries the claim policy and the map of superseded flags,
fixed in code and mirrored exactly in config/phase3_closure.json.

The raw graphs are read through their verified handle only to classify
driver attachments; nothing is trained, evaluated or explained, and no
upstream code runs.

Outputs (one new protected directory, never overwritten):
  closure.json  patient-bearing per-case record;
  summary.json  aggregate counts, statuses and digests only.

Usage:
    python -m ehr_evidence_app.gnn.admission
    python -m ehr_evidence_app.gnn.admission --operator-real
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import copy
import hashlib
import math
import os
import sys

from ehr_evidence_app.gnn import binding
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.gnn import outputs as pmo
from ehr_evidence_app.gnn import provenance as amp
from ehr_evidence_app.gnn import split as usm
from ehr_evidence_app.storage import policy as guard

SCHEMA = "phase3-closure"
SUMMARY_SCHEMA = "phase3-closure-summary"
SCHEMA_VERSION = "1.0"
OUTPUT_VERSION = "phase3-closure-v1"
PROTECTED_ROOT = pmo.PROTECTED_ROOT
OUTPUT_DIR = PROTECTED_ROOT + "\\model-provenance\\" + OUTPUT_VERSION
CLOSURE_NAME = "closure.json"
SUMMARY_NAME = "summary.json"
PARTIAL = ".partial"
CONFIG_PATH = os.path.join(mapping.REPO_ROOT, "config", "phase3_closure.json")
VERSION_PREFIX = "phase3-closure-prep-"
VERSION_HEX_CHARS = 16
DOMAINS = pmo.DOMAINS
EDGE_TYPES = {"condition": ("visit", "has_condition", "condition"),
              "procedure": ("visit", "has_procedure", "procedure"),
              "drug": ("visit", "has_drug", "drug")}
TARGET_ONLY = "target_only"
TARGET_AND_EARLIER = "target_and_earlier"
EARLIER_ONLY = "earlier_only"
NOT_ATTACHED = "not_attached"
NOT_A_NODE = "not_a_patient_node"
ATTACHMENTS = (TARGET_ONLY, TARGET_AND_EARLIER, EARLIER_ONLY, NOT_ATTACHED,
               NOT_A_NODE)
POSITIVE = "positive"
ZERO = "zero"
ADMITTED = "admitted"
QUARANTINED = "quarantined"

PARSED_ROLES = ("mapping", "outputs", "provenance", "split_v2",
                "wrapper_recovery", "binding_summary", "binding_cases",
                "parity", "label_vocab", "concept_vocab")
HASHED_ROLES = ("manifest", "agreement", "split_v1", "wrapper_executed")
STREAM_ROLES = ("graph",)
INPUT_ROLES = PARSED_ROLES + HASHED_ROLES + STREAM_ROLES
IDENTITY_ROLES = ("explain_patient", "model", "train",
                  "build_patient_graphs", "checkpoint", "metrics")

CONFIG = {
    "schema_version": "1.0",
    "closure_version": OUTPUT_VERSION,
    "admission_scope": "evidence_object_construction",
    "prediction_definition": (
        "argmax of the inherited bridge's single-graph diagnosis logits "
        "(one graph per forward pass)"),
    "held_out_definition": (
        "outside the upstream training partition only; the label vocabulary "
        "and the feature normalisation statistics were computed over all "
        "patients"),
    "label_layers": {
        "A": ("rule: output position i, training target i and label_vocab[i] "
              "share one axis in the pinned graph builder, trainer, model and "
              "bridge"),
        "B": ("decode: concept_id equals label_vocab[label_index] in the "
              "published output record"),
        "C": ("run binding: the checkpoint's saved pos_weight reproduces "
              "exactly from the current labels in the current label order "
              "under the replayed split, and a reversed label order does "
              "not")},
    "driver_tags": {
        "attachment": list(ATTACHMENTS),
        "score_class": [POSITIVE, ZERO],
        "attachment_rule": (
            "visits whose visit-to-concept edges reach the driver's concept "
            "node in the raw (unpreprocessed) patient graph; the target is "
            "the final visit"),
        "target_outcome_rule": (
            "a condition driver attached only to the target visit is a "
            "target-visit diagnosis and is excluded"),
        "narration_rule": ("positive score, a patient node, and not a "
                           "target-visit diagnosis")},
    "claim_policy": {
        "allowed": [
            "selected_label_as_one_output_of_multilabel_prediction",
            "raw_logit_and_uncalibrated_score",
            "positive_drivers_as_unsigned_local_sensitivity",
            "driver_visit_tag_to_restrict_or_disclose",
            "upstream_partition_with_held_out_definition",
            "recorded_history_within_verified_scope"],
        "prohibited": [
            "selected_label_as_only_diagnosis",
            "score_as_clinical_risk",
            "reported_auroc_for_single_graph_predictions",
            "causal_directional_or_percentage_attribution",
            "target_attached_driver_as_history",
            "prospective_or_pre_target_only_prediction",
            "attribution_tied_to_date_visit_or_recurrence",
            "zero_score_target_diagnosis_use",
            "event_level_dated_frequency_or_progression_claims"]},
    "supersedes": [
        {"flag": "label_index_correspondence",
         "records": ["step-20-pilot10-v1", "step-21-pilot10-v1"],
         "recorded": "not_established", "current": "established",
         "evidence": ["label_layers", "binding_summary"]},
        {"flag": ("wrapper.executed.bytes_available, "
                  "wrapper.executed.retrievable"),
         "records": ["step-21-pilot10-v1"], "recorded": "false",
         "current": "recovered byte-exact",
         "evidence": ["wrapper_recovery", "wrapper_executed"]},
        {"flag": "upstream_split_membership",
         "records": ["step-20-pilot10-v1", "step-21-pilot10-v1",
                     "step-22-pilot10-v2"],
         "recorded": "unknown / unavailable",
         "current": "verified by run binding",
         "evidence": ["binding_summary", "binding_cases"]},
        {"flag": "phase4_eligible",
         "records": ["step-17-pilot10-v1",
                     "step-19-preprocessing-parity-final",
                     "step-20-pilot10-v1", "step-21-pilot10-v1",
                     "step-22-pilot10-v2"],
         "recorded": "false (untyped)",
         "current": "typed per-case admission", "evidence": []},
        {"flag": "unseen_patient_status",
         "records": ["step-21-pilot10-v1", "step-22-pilot10-v2"],
         "recorded": "not_inferred",
         "current": ("upstream partition known; held out defined by "
                     "held_out_definition"),
         "evidence": ["binding_cases"]},
        {"flag": "source_event_linkage",
         "records": ["step-20-pilot10-v1", "step-21-pilot10-v1"],
         "recorded": "absent",
         "current": ("absent; restricts event-level claims and does not "
                     "block cases"),
         "evidence": []},
        {"flag": "pre_target_only_input",
         "records": ["step-20-pilot10-v1", "step-21-pilot10-v1"],
         "recorded": "false", "current": "unchanged", "evidence": []},
        {"flag": "history_only_claim_allowed",
         "records": ["step-20-pilot10-v1", "step-21-pilot10-v1"],
         "recorded": "false", "current": "unchanged", "evidence": []}],
}

LIMITATIONS = [
    "Development pilot of ten verified cases; not an evaluation cohort.",
    "Admission is to evidence-object construction under the claim policy; "
    "it is not a statement of clinical validity.",
    "Model inputs include the target visit's features and its procedure and "
    "drug edges, and the sequence model has no causal mask; no prospective "
    "or pre-target-only claim is allowed.",
    "Source-event linkage is absent: attributions stay concept-level and are "
    "never tied to an event, a date or a visit.",
    "Driver scores are unsigned local sensitivities of one selected label; "
    "they are not causal, directional or percentage contributions.",
    "The reported AUROC describes batched evaluation, not single-graph "
    "bridge predictions.",
]


class AdmissionError(Exception):
    """A fail-closed precondition failure carrying a finding code only."""


# --------------------------------------------------------------------------
# Plan and configuration
# --------------------------------------------------------------------------

# Published inherited-run-binding-v1 record (ehr_evidence_app.gnn.binding).
BINDING_SUMMARY_SHA256 = ("c6dd79cfefe35327b6b56b8812a1d25a"
                          "320d742ced48506c2cd40a8f63415f3b")
BINDING_SUMMARY_SIZE = 7242
BINDING_CASES_SHA256 = ("f666866937dd2f0484b46af23d915231"
                        "ac0785356ae660b71918d6ed88219036")
BINDING_CASES_SIZE = 4228


def real_plan():
    """Return the pinned inputs, identities and the fixed protected output."""
    step20 = pmo.real_plan()["assets"]
    step21 = amp.real_plan()
    step22 = usm.real_plan()["inputs"]
    models = PROTECTED_ROOT + "\\model-provenance\\"
    recovery = models + "step-20-executed-wrapper-recovery-v1\\"
    run_binding = binding.OUTPUT_DIR + "\\"
    return {
        "inputs": {
            "mapping": dict(step20["step17_mapping"]),
            "outputs": dict(step21["inputs"]["step20_outputs"]),
            "manifest": dict(step21["inputs"]["step20_manifest"]),
            "agreement": dict(step21["inputs"]["step20_agreement"]),
            "provenance": dict(step22["step21_provenance"]),
            "split_v1": {
                "path": models + "step-22-pilot10-v1\\split-membership.json",
                "size_bytes": 19387,
                "sha256": "e54f75b2de59880815e7a5644ffea0b1"
                          "e1780b19ec7b370b64cb18584885378f"},
            "split_v2": {
                "path": usm.OUTPUT_DIR + "\\" + usm.OUTPUT_NAME,
                "size_bytes": 18198,
                "sha256": "5e0e0be24e21baace3a4bb6224cc8c34"
                          "c5d5a812e3c7737d7467d187894b2a70"},
            "wrapper_recovery": {
                "path": recovery + "recovery-manifest.json",
                "size_bytes": 1826,
                "sha256": "91bc0d2a92c1045ab4602b22539e9fdb"
                          "802784b79b01686e7058edabb9f1923f"},
            "wrapper_executed": {
                "path": recovery + "prepare_model_outputs.executed.py",
                "size_bytes": 69864,
                "sha256": "fe37ac0a3fa81e4abeff4e3542e05de5"
                          "109085f75efede0fecdd53c44323c9ab"},
            "binding_summary": {
                "path": run_binding + binding.SUMMARY_NAME,
                "size_bytes": BINDING_SUMMARY_SIZE,
                "sha256": BINDING_SUMMARY_SHA256},
            "binding_cases": {
                "path": run_binding + binding.CASES_NAME,
                "size_bytes": BINDING_CASES_SIZE,
                "sha256": BINDING_CASES_SHA256},
            "parity": dict(step20["step19_summary"]),
            "label_vocab": dict(step20["label_vocab"]),
            "concept_vocab": dict(step20["concept_vocab"]),
            "graph": dict(step20["graph"]),
        },
        "identities": {
            "explain_patient": step20["explain_patient"]["sha256"],
            "model": step20["model"]["sha256"],
            "train": step22["train_source"]["sha256"],
            "build_patient_graphs": amp.POLICY_SOURCES[
                "build_patient_graphs"],
            "checkpoint": step20["checkpoint"]["sha256"],
            "metrics": step22["metrics"]["sha256"]},
        "config_path": CONFIG_PATH,
        "output_dir": OUTPUT_DIR,
        "protected_root": PROTECTED_ROOT,
    }


def is_hex64(value):
    """Return True for a 64-character hexadecimal string."""
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdefABCDEF" for c in value))


def plan_problem(plan):
    """Return a finding code when an input or identity is not pinned."""
    inputs = plan.get("inputs") if isinstance(plan, dict) else None
    if not isinstance(inputs, dict) or set(inputs) != set(INPUT_ROLES):
        return "plan_roles_unexpected"
    for name in INPUT_ROLES:
        entry = inputs[name]
        if (not isinstance(entry, dict) or not isinstance(entry.get("path"),
                                                          str)
                or not is_hex64(entry.get("sha256"))):
            return "plan_pin_missing:%s" % name
    identities = plan.get("identities")
    if (not isinstance(identities, dict)
            or set(identities) != set(IDENTITY_ROLES)
            or not all(is_hex64(identities[name])
                       for name in IDENTITY_ROLES)):
        return "plan_identities_unexpected"
    return None


def load_config(data):
    """Parse the config bytes; fail unless they match the implemented values.

    Returns the config reference (repository path and SHA-256).
    """
    try:
        config = mapping.parse_json(data)
    except (UnicodeDecodeError, ValueError):
        raise AdmissionError("config_not_json") from None
    if not isinstance(config, dict):
        raise AdmissionError("config_mismatch")
    if {key: config.get(key) for key in CONFIG} != CONFIG:
        raise AdmissionError("config_mismatch")
    return {"path": os.path.relpath(CONFIG_PATH, mapping.REPO_ROOT),
            "sha256": hashlib.sha256(data).hexdigest()}


# --------------------------------------------------------------------------
# Input verification
# --------------------------------------------------------------------------

def output_preconditions(plan):
    """Refuse an existing, unprotected or input-overlapping output."""
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        for entry in plan["inputs"].values():
            if (guard.is_within(entry["path"], path)
                    or guard.is_within(path, os.path.dirname(entry["path"]))):
                raise AdmissionError("output_overlaps_input")
        if os.path.lexists(path):
            raise AdmissionError("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise AdmissionError(problem)


def verify_inputs(plan, stack):
    """Verify every pin, then parse JSON only from the verified bytes.

    Returns (held identities, raw bytes, parsed documents, streams).
    """
    held, raw = [], {}
    try:
        config_data = pmo.verify_read(
            {"path": plan["config_path"],
             "sha256": hashlib.sha256(read_config(plan)).hexdigest()},
            "config", held)
        for name in PARSED_ROLES:
            raw[name] = pmo.verify_read(plan["inputs"][name], name, held)
        for name in HASHED_ROLES:
            pmo.verify_hashed(plan["inputs"][name], name, held)
        streams = {name: pmo.verify_stream(plan["inputs"][name], name,
                                           held, stack)
                   for name in STREAM_ROLES}
        pmo.confirm_inputs(held)
    except pmo.Step20Error as error:
        raise AdmissionError(str(error)) from None
    config_ref = load_config(config_data)
    documents = {}
    for name in PARSED_ROLES:
        try:
            documents[name] = mapping.parse_json(raw[name])
        except (UnicodeDecodeError, ValueError):
            raise AdmissionError("input_not_json:%s" % name) from None
    return held, raw, documents, streams, config_ref


def read_config(plan):
    """Read the repository config once as a bounded regular file."""
    try:
        data, _ = mapping.read_verified(plan["config_path"], "config",
                                        "config_unreadable")
    except mapping.MappingError as error:
        raise AdmissionError("input_identity:%s" % error) from None
    return data


# --------------------------------------------------------------------------
# Record checks
# --------------------------------------------------------------------------

def select_pilot(document, plan):
    """Return the verified pilot selection (identity mapping rules)."""
    assets = {"graph": plan["inputs"]["graph"],
              "explain_patient": {"sha256": plan["identities"][
                  "explain_patient"]}}
    try:
        return pmo.select_pilot(document, {"assets": assets})
    except pmo.Step20Error as error:
        raise AdmissionError(str(error)) from None


def mapping_records(document):
    """Return mapping records keyed by patient_index."""
    return {record["patient_index"]: record
            for record in document["records"]}


def check_outputs(document, selection):
    """Return the output records in selection order after schema checks."""
    if not isinstance(document, list) or len(document) != len(selection):
        raise AdmissionError("outputs_record_count")
    for record, item in zip(document, selection):
        if pmo.schema_problem(record):
            raise AdmissionError("outputs_record_invalid")
        if record["patient_index"] != item["patient_index"]:
            raise AdmissionError("outputs_selection_mismatch")
    return list(document)


def check_provenance(document, raw, outputs, selection, plan):
    """Return provenance records after checking their bindings.

    The sidecar must be the published provenance sidecar with an intact
    preparation fingerprint, bound to the pinned mapping and outputs, and
    hold one record per selected case whose output digest matches.
    """
    if (not isinstance(document, dict) or document.get("schema") != amp.SCHEMA
            or document.get("status") != "published"):
        raise AdmissionError("provenance_unexpected")
    body = {key: value for key, value in document.items()
            if key != "preparation"}
    if (document.get("preparation") or {}).get(
            "fingerprint_sha256") != amp.digest(body):
        raise AdmissionError("provenance_fingerprint_mismatch")
    step20 = document.get("step20") or {}
    if (step20.get("outputs_sha256") != plan["inputs"]["outputs"]["sha256"]
            or (document.get("step17_mapping") or {}).get("sha256")
            != plan["inputs"]["mapping"]["sha256"]):
        raise AdmissionError("provenance_binding_mismatch")
    records = document.get("records")
    if not isinstance(records, list) or len(records) != len(selection):
        raise AdmissionError("provenance_record_count")
    for record, output, item in zip(records, outputs, selection):
        if (not isinstance(record, dict)
                or record.get("patient_index") != item["patient_index"]
                or record.get("graph_position") != item["graph_position"]
                or (record.get("step20_record") or {}).get("sha256")
                != amp.digest(output)):
            raise AdmissionError("provenance_record_mismatch")
    return records


def check_wrapper(document, provenance, plan):
    """Return the recovered executed-wrapper facts after cross-checks."""
    wrapper = ((provenance.get("step20") or {}).get("wrapper")) or {}
    executed = (document.get("executed") or {}) if isinstance(
        document, dict) else {}
    current = document.get("current_source") or {}
    diff = document.get("diff") or {}
    expected = plan["inputs"]["wrapper_executed"]["sha256"].lower()
    if (document.get("schema") != "step20-executed-wrapper-recovery"
            or executed.get("sha256") != expected
            or (wrapper.get("executed") or {}).get("sha256") != expected
            or current.get("sha256")
            != (wrapper.get("current_source") or {}).get("sha256")
            or diff.get("applies_to_executed_and_reproduces_current")
            is not True):
        raise AdmissionError("wrapper_recovery_unexpected")
    return {"executed_sha256": expected,
            "current_source_sha256": current["sha256"],
            "recovery_manifest_sha256":
                plan["inputs"]["wrapper_recovery"]["sha256"].lower()}


def check_split_v2(document, plan):
    """Return the published split sidecar's case status counts."""
    counts = (document.get("counts") or {}) if isinstance(document,
                                                          dict) else {}
    if (document.get("schema") != usm.SCHEMA
            or document.get("status") != "published"
            or ((document.get("bindings") or {}).get("step21") or {}).get(
                "sha256") != plan["inputs"]["provenance"]["sha256"].lower()):
        raise AdmissionError("split_sidecar_unexpected")
    return {key: counts.get(key) for key in usm.STATUSES}


def check_binding(summary, cases, raw, plan, selection):
    """Return per-case binding facts after checking the verification.

    The summary must be a verified run-binding record whose checks all
    passed, bound to the pinned graph, checkpoint, metrics, mapping and
    outputs, and whose cases file is exactly the pinned cases bytes.
    """
    if (not isinstance(summary, dict) or summary.get("schema")
            != binding.SCHEMA or summary.get("status") != binding.VERIFIED):
        raise AdmissionError("binding_not_verified")
    checks = summary.get("checks")
    if not isinstance(checks, dict) or not checks or not all(
            value is True for value in checks.values()):
        raise AdmissionError("binding_not_verified")
    body = {key: value for key, value in summary.items()
            if key != "preparation"}
    if (summary.get("preparation") or {}).get(
            "fingerprint_sha256") != binding.digest(body):
        raise AdmissionError("binding_fingerprint_mismatch")
    inputs = summary.get("inputs") or {}
    expected = {"graph": plan["inputs"]["graph"]["sha256"],
                "checkpoint": plan["identities"]["checkpoint"],
                "metrics": plan["identities"]["metrics"],
                "mapping": plan["inputs"]["mapping"]["sha256"],
                "outputs": plan["inputs"]["outputs"]["sha256"]}
    for role, digest_value in expected.items():
        if (inputs.get(role) or {}).get("sha256") != digest_value.lower():
            raise AdmissionError("binding_input_mismatch:%s" % role)
    if ((summary.get("cases_file") or {}).get("sha256")
            != hashlib.sha256(raw["binding_cases"]).hexdigest()):
        raise AdmissionError("binding_cases_mismatch")
    if (not isinstance(cases, dict) or cases.get("schema")
            != binding.CASES_SCHEMA or not isinstance(cases.get("cases"),
                                                       list)
            or len(cases["cases"]) != len(selection)):
        raise AdmissionError("binding_cases_unexpected")
    facts = []
    for case, item in zip(cases["cases"], selection):
        batch = case.get("batch") if isinstance(case, dict) else None
        if (case.get("patient_index") != item["patient_index"]
                or case.get("graph_position") != item["graph_position"]
                or case.get("upstream_partition") not in binding.PARTITIONS
                or not isinstance(batch, dict)
                or set(batch) != {str(size) for size in binding.BATCH_SIZES}
                or type(case.get("single_graph_reproduces_published"))
                is not bool):
            raise AdmissionError("binding_case_mismatch")
        facts.append({
            "upstream_partition": case["upstream_partition"],
            "single_graph_reproduces_published":
                case["single_graph_reproduces_published"],
            "batch_sensitive": not all(
                cell.get("argmax_unchanged") is True
                for cell in batch.values())})
    layer_c = (checks.get("pos_weight_bitwise_equal") is True
               and checks.get("reversed_label_control_failed") is True)
    return facts, layer_c


def check_vocabularies(label_vocab, concept_vocab):
    """Return (label_vocab, inverse concept maps) after shape checks."""
    if not isinstance(label_vocab, list) or not label_vocab:
        raise AdmissionError("label_vocab_unexpected")
    if (not isinstance(concept_vocab, dict)
            or not all(isinstance(concept_vocab.get(domain), dict)
                       for domain in DOMAINS)):
        raise AdmissionError("concept_vocab_unexpected")
    inverse = {}
    for domain in DOMAINS:
        table = {}
        for concept, index in concept_vocab[domain].items():
            try:
                table[int(index)] = int(concept)
            except (TypeError, ValueError):
                raise AdmissionError("concept_vocab_unexpected") from None
        inverse[domain] = table
    return label_vocab, inverse


def layer_b(record, label_vocab):
    """Return True when the published concept decodes from label_index."""
    prediction = record["predicted_diagnosis"]
    index = prediction["label_index"]
    if not 0 <= index < len(label_vocab):
        return False
    try:
        return prediction["concept_id"] == int(label_vocab[index])
    except (TypeError, ValueError):
        return False


# --------------------------------------------------------------------------
# Driver tags
# --------------------------------------------------------------------------

def attachment(visits, last):
    """Classify the visits a concept node attaches to (target is last)."""
    if visits is None:
        return NOT_A_NODE
    if not visits:
        return NOT_ATTACHED
    if visits == {last}:
        return TARGET_ONLY
    if last in visits:
        return TARGET_AND_EARLIER
    return EARLIER_ONLY


def score_class(score):
    """Return the score class of a validated, finite, unsigned score."""
    if type(score) is not float or not math.isfinite(score) or score < 0.0:
        raise AdmissionError("driver_score_invalid")
    return POSITIVE if score > 0.0 else ZERO


def tag_drivers(record, attachments, inverse):
    """Return per-domain driver tags and decode mismatches for one record.

    attachments holds, per domain, one attachment class per driver in the
    record's order. Only classes and flags are returned; no driver value is
    copied.
    """
    tags, mismatches = {}, 0
    for domain in DOMAINS:
        drivers = record["drivers"][domain]
        classes = attachments.get(domain)
        if not isinstance(classes, list) or len(classes) != len(drivers):
            raise AdmissionError("driver_attachment_count")
        entries = []
        for position, (driver, kind) in enumerate(zip(drivers, classes)):
            if kind not in ATTACHMENTS:
                raise AdmissionError("driver_attachment_invalid")
            if inverse[domain].get(driver["vocab_index"]) != driver[
                    "concept_id"]:
                mismatches += 1
            score = score_class(driver["score"])
            outcome = domain == "condition" and kind == TARGET_ONLY
            entries.append({
                "position": position, "score_class": score,
                "attachment": kind, "target_outcome_concept": outcome,
                "narration_eligible": (score == POSITIVE and not outcome
                                       and kind not in (NOT_A_NODE,
                                                        NOT_ATTACHED))})
        tags[domain] = entries
    return tags, mismatches


# --------------------------------------------------------------------------
# Graph runtime (GNN interpreter only)
# --------------------------------------------------------------------------

class GraphRuntime:
    """Reads the raw graphs through their verified handle (CPU only)."""

    def attachments(self, streams, selection, outputs, step19):
        """Return, per selected case, driver attachment classes by domain."""
        graphs = streams["graph"].load(pmo.torch_graph_loader)
        if not isinstance(graphs, list) or len(graphs) != step19[
                "graphs_total"]:
            raise AdmissionError("graph_count_mismatch")
        try:
            pmo.check_selection_graphs(graphs, selection)
        except pmo.Step20Error as error:
            raise AdmissionError(str(error)) from None
        filtered = [graph for graph in graphs
                    if hasattr(graph, "y_diagnosis")]
        if len(filtered) != step19["graphs_filtered"]:
            raise AdmissionError("graph_count_mismatch")
        result = []
        for item, record in zip(selection, outputs):
            graph = filtered[item["patient_index"]]
            last = int(graph["visit"].x.size(0)) - 1
            per_domain = {}
            for domain in DOMAINS:
                values = graph[domain].x
                local = ({int(value): node for node, value in enumerate(
                    values[:, 0].long().tolist())}
                    if values.numel() else {})
                edges = graph[EDGE_TYPES[domain]].edge_index
                reached = collections.defaultdict(set)
                for source, target in zip(edges[0].tolist(),
                                          edges[1].tolist()):
                    reached[target].add(source)
                classes = []
                for driver in record["drivers"][domain]:
                    node = local.get(driver["vocab_index"])
                    visits = None if node is None else reached.get(node,
                                                                   set())
                    classes.append(attachment(visits, last))
                per_domain[domain] = classes
            result.append(per_domain)
        return result


# --------------------------------------------------------------------------
# Closure record
# --------------------------------------------------------------------------

def input_refs(plan, raw):
    """Return path, size and SHA-256 of every pinned input."""
    refs = {}
    for name in INPUT_ROLES:
        entry = plan["inputs"][name]
        ref = {"path": entry["path"], "sha256": entry["sha256"].lower()}
        if name in raw:
            ref["size_bytes"] = len(raw[name])
        elif "size_bytes" in entry:
            ref["size_bytes"] = entry["size_bytes"]
        refs[name] = ref
    return refs


def evidence_refs(roles, refs):
    """Resolve supersession evidence roles to input digests."""
    resolved = []
    for role in roles:
        if role == "label_layers":
            resolved.append({"role": role, "record": "this closure record"})
        else:
            resolved.append({"role": role, "sha256": refs[role]["sha256"]})
    return resolved


def build_case(position, item, record, output, provenance_record, fact,
               tags, label_ok, layer_c):
    """Return one per-case closure entry."""
    correspondence = label_ok and layer_c
    excluded = sum(entry["target_outcome_concept"]
                   for entries in tags.values() for entry in entries)
    eligible = sum(entry["narration_eligible"]
                   for entries in tags.values() for entry in entries)
    target_attached = any(
        entry["narration_eligible"]
        and entry["attachment"] in (TARGET_ONLY, TARGET_AND_EARLIER)
        for entries in tags.values() for entry in entries)
    restrictions = []
    if fact["batch_sensitive"]:
        restrictions.append("batch_sensitive_prediction")
    if target_attached:
        restrictions.append("target_attached_drivers_present")
    if excluded:
        restrictions.append("target_outcome_entries_excluded")
    admitted = (correspondence and fact["single_graph_reproduces_published"]
                and record.get("identity_status") == mapping.STATUS_VERIFIED)
    return {
        "case_position": position,
        "patient_index": item["patient_index"],
        "graph_position": item["graph_position"],
        "mapping_record_sha256": amp.digest(record),
        "output_record_sha256": amp.digest(output),
        "provenance_record_sha256": amp.digest(provenance_record),
        "admission": {"status": ADMITTED if admitted else QUARANTINED,
                      "scope": CONFIG["admission_scope"],
                      "restrictions": restrictions},
        "label_evidence": {"A": "established", "B": label_ok,
                           "C": "verified" if layer_c else "not_verified",
                           "correspondence": ("established" if correspondence
                                              else "not_established")},
        "prediction": {
            "definition": "single_graph_bridge",
            "single_graph_reproduces_published":
                fact["single_graph_reproduces_published"],
            "batch_sensitive": fact["batch_sensitive"]},
        "upstream_partition": fact["upstream_partition"],
        "drivers": tags,
        "narration_eligible_drivers": eligible,
        "excluded_driver_entries": excluded,
        "explanation_eligible": admitted and eligible > 0,
    }


def aggregate(cases):
    """Return aggregate counts over the per-case entries."""
    table = {domain: {kind: {POSITIVE: 0, ZERO: 0} for kind in ATTACHMENTS}
             for domain in DOMAINS}
    for case in cases:
        for domain, entries in case["drivers"].items():
            for entry in entries:
                table[domain][entry["attachment"]][entry["score_class"]] += 1
    return {
        "cases": len(cases),
        "admitted": sum(case["admission"]["status"] == ADMITTED
                        for case in cases),
        "label_layer_b_matches": sum(case["label_evidence"]["B"] is True
                                     for case in cases),
        "label_correspondence_established": sum(
            case["label_evidence"]["correspondence"] == "established"
            for case in cases),
        "explanation_eligible": sum(case["explanation_eligible"]
                                    for case in cases),
        "batch_sensitive": sum(case["prediction"]["batch_sensitive"]
                               for case in cases),
        "upstream_partition": {name: sum(case["upstream_partition"] == name
                                         for case in cases)
                               for name in binding.PARTITIONS},
        "excluded_target_outcome_entries": sum(
            case["excluded_driver_entries"] for case in cases),
        "narration_eligible_drivers": sum(
            case["narration_eligible_drivers"] for case in cases),
        "driver_tags": table,
    }


def build_closure(plan, raw, documents, attachments, config_ref):
    """Return (closure, summary) after every cross-check has passed."""
    selection = select_pilot(documents["mapping"], plan)
    records = mapping_records(documents["mapping"])
    outputs = check_outputs(documents["outputs"], selection)
    provenance = check_provenance(documents["provenance"], raw, outputs,
                                  selection, plan)
    wrapper = check_wrapper(documents["wrapper_recovery"],
                            documents["provenance"], plan)
    split_counts = check_split_v2(documents["split_v2"], plan)
    facts, layer_c = check_binding(documents["binding_summary"],
                                   documents["binding_cases"], raw, plan,
                                   selection)
    label_vocab, inverse = check_vocabularies(documents["label_vocab"],
                                              documents["concept_vocab"])
    if len(attachments) != len(selection):
        raise AdmissionError("driver_attachment_count")
    cases, mismatches = [], 0
    for position, item in enumerate(selection):
        tags, wrong = tag_drivers(outputs[position], attachments[position],
                                  inverse)
        mismatches += wrong
        cases.append(build_case(
            position, item, records[item["patient_index"]],
            outputs[position], provenance[position], facts[position], tags,
            layer_b(outputs[position], label_vocab), layer_c))
    if mismatches:
        raise AdmissionError("driver_decode_mismatch")
    refs = input_refs(plan, raw)
    counts = aggregate(cases)
    body = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "version": OUTPUT_VERSION, "status": "closed",
        "patient_bearing": True, "config": config_ref, "inputs": refs,
        "admission_scope": CONFIG["admission_scope"],
        "prediction_definition": CONFIG["prediction_definition"],
        "held_out_definition": CONFIG["held_out_definition"],
        "label_layers": dict(
            CONFIG["label_layers"],
            A_sources={name: plan["identities"][name].lower()
                       for name in ("build_patient_graphs", "train",
                                    "model", "explain_patient")}),
        "driver_tags": copy.deepcopy(CONFIG["driver_tags"]),
        "claim_policy": copy.deepcopy(CONFIG["claim_policy"]),
        "supersedes": [dict(entry, evidence=evidence_refs(entry["evidence"],
                                                          refs))
                       for entry in CONFIG["supersedes"]],
        "executed_wrapper": wrapper,
        "earlier_split_sidecar_case_statuses": split_counts,
        "run_binding": {
            "summary_sha256": refs["binding_summary"]["sha256"],
            "cases_sha256": refs["binding_cases"]["sha256"],
            "preparation": documents["binding_summary"]["preparation"][
                "version"]},
        "counts": counts, "limitations": list(LIMITATIONS), "cases": cases}
    value = amp.digest(body)
    closure = dict(body, preparation={
        "fingerprint_sha256": value,
        "version": VERSION_PREFIX + value[:VERSION_HEX_CHARS]})
    summary = {
        "schema": SUMMARY_SCHEMA, "schema_version": SCHEMA_VERSION,
        "version": OUTPUT_VERSION, "status": "closed",
        "closure_file": {"name": CLOSURE_NAME,
                         "sha256": hashlib.sha256(
                             binding.encode(closure)).hexdigest()},
        "preparation": closure["preparation"], "counts": counts,
        "inputs": {name: ref["sha256"] for name, ref in refs.items()},
        "claim_policy": copy.deepcopy(CONFIG["claim_policy"])}
    return closure, summary


def remove_partial(partial):
    """Delete this run's own partial directory after a failure."""
    for name in (CLOSURE_NAME, SUMMARY_NAME):
        target = os.path.join(partial, name)
        if os.path.lexists(target):
            os.remove(target)
    if os.path.isdir(partial):
        os.rmdir(partial)


def publish(plan, closure, summary, held):
    """Write both files through a new .partial directory and rename it."""
    output_preconditions(plan)
    files = {CLOSURE_NAME: binding.encode(closure),
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
            raise AdmissionError(str(error)) from None
        try:
            os.rename(partial, plan["output_dir"])
        except OSError:
            raise AdmissionError("output_rename_failed") from None
    except BaseException:
        remove_partial(partial)
        raise
    return {name: hashlib.sha256(data).hexdigest()
            for name, data in files.items()}


def run_real(plan=None, runtime_factory=GraphRuntime):
    """Operator-only real run in the GNN interpreter (CPU is enough)."""
    plan = plan or real_plan()
    problem = guard.fixed_policy_problem() or plan_problem(plan)
    if problem:
        raise AdmissionError(problem)
    output_preconditions(plan)
    runtime = runtime_factory()
    trips = pmo.Tripwires()
    with contextlib.ExitStack() as stack:
        held, raw, documents, streams, config_ref = verify_inputs(plan,
                                                                  stack)
        selection = select_pilot(documents["mapping"], plan)
        outputs = check_outputs(documents["outputs"], selection)
        try:
            step19 = pmo.check_step19(documents["parity"], {"assets": {
                "graph": plan["inputs"]["graph"],
                "explain_patient": {"sha256": plan["identities"][
                    "explain_patient"]},
                "model": {"sha256": plan["identities"]["model"]}}})
            with pmo.tracker_offline(), pmo.network_blocked(trips), \
                    pmo.silenced():
                attachments = runtime.attachments(streams, selection,
                                                  outputs, step19)
            trips.check()
        except pmo.Step20Error as error:
            raise AdmissionError(str(error)) from None
        closure, summary = build_closure(plan, raw, documents, attachments,
                                         config_ref)
        digests = publish(plan, closure, summary, held)
    return summary, digests


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

def synthetic_attachments(outputs):
    """Return invented attachment classes cycling through every class."""
    cycle = (EARLIER_ONLY, TARGET_AND_EARLIER, TARGET_ONLY, EARLIER_ONLY)
    return [{domain: [cycle[(number + position) % len(cycle)]
                      for position in range(len(record["drivers"][domain]))]
             for domain in DOMAINS}
            for number, record in enumerate(outputs)]


SYNTHETIC_LABEL_BASE = 990000000
SYNTHETIC_GRAPH = b"synthetic graph collection\n"
SYNTHETIC_EXECUTED_WRAPPER = b"# synthetic executed wrapper\n"
SYNTHETIC_SPLIT_V1 = b'{"synthetic": "superseded split sidecar"}\n'


def sha256_bytes(data):
    """Return the SHA-256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def synthetic_bundle():
    """Return (plan, raw, documents, attachments, files) for an invented closure.

    Every pinned digest is the SHA-256 of the invented bytes in files, so the
    bundle can also be written to scratch and verified end to end.
    """
    step20_plan = amp.synthetic_step20_plan()
    step20_plan["assets"]["graph"]["sha256"] = sha256_bytes(SYNTHETIC_GRAPH)
    invented = amp.synthetic_documents(step20_plan)
    for record in invented["step20_outputs"]:
        prediction = record["predicted_diagnosis"]
        prediction["concept_id"] = (SYNTHETIC_LABEL_BASE
                                    + prediction["label_index"])
    plan, raw = amp.synthetic_bundle(documents=invented,
                                     step20_plan=step20_plan)
    plan["executed_wrapper_sha256"] = sha256_bytes(SYNTHETIC_EXECUTED_WRAPPER)
    config = amp.load_config(amp.read_config_bytes(amp.CONFIG_PATH))
    provenance = amp.build_sidecar(plan, raw, config)
    outputs = mapping.parse_json(raw["step20_outputs"])
    selection = pmo.select_pilot(mapping.parse_json(raw["step17_mapping"]),
                                 step20_plan)
    files = {"mapping": raw["step17_mapping"],
             "outputs": raw["step20_outputs"],
             "parity": raw["step19_summary"],
             "provenance": amp.encode(provenance),
             "manifest": raw["step20_manifest"],
             "agreement": raw["step20_agreement"],
             "split_v1": SYNTHETIC_SPLIT_V1,
             "wrapper_executed": SYNTHETIC_EXECUTED_WRAPPER,
             "graph": SYNTHETIC_GRAPH}
    identities = {
        "explain_patient": step20_plan["assets"]["explain_patient"]["sha256"],
        "model": step20_plan["assets"]["model"]["sha256"],
        "train": amp.synthetic_hash("train"),
        "build_patient_graphs": amp.synthetic_hash("builder"),
        "checkpoint": step20_plan["assets"]["checkpoint"]["sha256"],
        "metrics": amp.synthetic_hash("metrics")}
    recovery = {"schema": "step20-executed-wrapper-recovery",
                "executed": {"sha256": plan["executed_wrapper_sha256"]},
                "current_source": {"sha256": provenance["step20"]["wrapper"][
                    "current_source"]["sha256"]},
                "diff": {"applies_to_executed_and_reproduces_current": True}}
    split_v2 = {"schema": usm.SCHEMA, "status": "published",
                "bindings": {"step21": {"sha256": sha256_bytes(
                    files["provenance"])}},
                "counts": {"verified": 0, "unknown": 0, "unavailable": 10}}
    cases = {"schema": binding.CASES_SCHEMA, "cases": [
        {"patient_index": item["patient_index"],
         "graph_position": item["graph_position"],
         "upstream_partition": binding.PARTITIONS[number % 3],
         "single_graph_reproduces_published": True,
         "batch": {str(size): {"argmax_unchanged": number != 4,
                               "selected_label_rank": 1 + (number == 4)}
                   for size in binding.BATCH_SIZES}}
        for number, item in enumerate(selection)]}
    files["binding_cases"] = binding.encode(cases)
    summary_body = {
        "schema": binding.SCHEMA, "status": binding.VERIFIED,
        "checks": binding.evaluate_binding(binding.synthetic_run())[1],
        "inputs": {"graph": {"sha256": sha256_bytes(SYNTHETIC_GRAPH)},
                   "checkpoint": {"sha256": identities["checkpoint"]},
                   "metrics": {"sha256": identities["metrics"]},
                   "mapping": {"sha256": sha256_bytes(files["mapping"])},
                   "outputs": {"sha256": sha256_bytes(files["outputs"])}},
        "cases_file": {"sha256": sha256_bytes(files["binding_cases"])}}
    value = binding.digest(summary_body)
    binding_summary = dict(summary_body, preparation={
        "fingerprint_sha256": value,
        "version": binding.VERSION_PREFIX + value[:16]})
    label_vocab = [SYNTHETIC_LABEL_BASE + index for index in range(8)]
    concept_vocab = {domain: {} for domain in DOMAINS}
    for record in outputs:
        for domain in DOMAINS:
            for driver in record["drivers"][domain]:
                if driver["concept_id"] is not None:
                    concept_vocab[domain][str(driver["concept_id"])] = (
                        driver["vocab_index"])
    files.update({
        "wrapper_recovery": binding.encode(recovery),
        "split_v2": binding.encode(split_v2),
        "binding_summary": binding.encode(binding_summary),
        "label_vocab": binding.encode(label_vocab),
        "concept_vocab": binding.encode(concept_vocab)})
    inputs = {name: {"path": "W:\\synthetic\\inputs\\%s.bin" % name,
                     "sha256": sha256_bytes(files[name])}
              for name in INPUT_ROLES}
    admission_plan = {"inputs": inputs, "identities": identities,
                      "config_path": CONFIG_PATH,
                      "output_dir": "W:\\synthetic\\protected\\out",
                      "protected_root": "W:\\synthetic\\protected"}
    raw_parsed = {name: files[name] for name in PARSED_ROLES}
    documents = {name: mapping.parse_json(data)
                 for name, data in raw_parsed.items()}
    attachments = synthetic_attachments(outputs)
    return admission_plan, raw_parsed, documents, attachments, files


def run_synthetic():
    """Build an invented closure record; nothing is read or written."""
    plan, raw, documents, attachments, _ = synthetic_bundle()
    with open(CONFIG_PATH, "rb") as handle:
        config_ref = load_config(handle.read())
    closure, summary = build_closure(plan, raw, documents, attachments,
                                     config_ref)
    if summary["counts"]["admitted"] != summary["counts"]["cases"]:
        raise AdmissionError("synthetic_expectation_failed")
    return closure, summary


# --------------------------------------------------------------------------
# Console
# --------------------------------------------------------------------------

def report(summary, digests=None):
    """Print the sanitised aggregate summary."""
    counts = summary["counts"]
    print("Status             : %s" % summary["status"])
    print("cases admitted     : %d/%d" % (counts["admitted"], counts["cases"]))
    print("label layer B      : %d/%d; correspondence established %d" % (
        counts["label_layer_b_matches"], counts["cases"],
        counts["label_correspondence_established"]))
    print("explanation ready  : %d; batch-sensitive %d" % (
        counts["explanation_eligible"], counts["batch_sensitive"]))
    partitions = counts["upstream_partition"]
    print("upstream partition : train %d, validation %d, test %d" % (
        partitions["train"], partitions["validation"], partitions["test"]))
    print("excluded entries   : %d target-visit diagnoses" % counts[
        "excluded_target_outcome_entries"])
    for domain in DOMAINS:
        row = counts["driver_tags"][domain]
        print("%-19s: target-only %d, target+earlier %d, earlier-only %d, "
              "zero-score %d, not a node %d" % (
                  domain + " drivers",
                  row[TARGET_ONLY][POSITIVE], row[TARGET_AND_EARLIER][POSITIVE],
                  row[EARLIER_ONLY][POSITIVE],
                  sum(cell[ZERO] for cell in row.values()),
                  sum(row[NOT_A_NODE].values()) + sum(
                      row[NOT_ATTACHED].values())))
    print("preparation        : %s" % summary["preparation"]["version"])
    for name, value in sorted((digests or {}).items()):
        print("%-19s: %s" % (name, value))


def main(argv=None):
    """Run the synthetic default mode or the operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="build the real closure record (GNN "
                             "interpreter); writes one new protected record")
    args = parser.parse_args(argv)
    try:
        if args.operator_real:
            summary, digests = run_real()
            report(summary, digests)
        else:
            _, summary = run_synthetic()
            report(summary)
    except AdmissionError as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
