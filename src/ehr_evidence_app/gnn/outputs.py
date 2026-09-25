#!/usr/bin/env python3
"""Offline inherited model outputs for the Step 17 pilot records (Step 20).

Wraps the pinned inherited prediction/attribution bridge for exactly the ten
verified records of the Step 17 pilot-10-v1 identity mapping and publishes
their bridge-shaped records into one new protected, versioned directory.
Before publication the regenerated canonical prefix (the first three bridge
records, num_patients=3, top_k=10) must agree with the immutable canonical
explanations sample: non-float values, structure and order exactly, floats
finite and within the fixed atol=1e-6, rtol=1e-6.

One run loads the full graph collection once, preprocesses it once with the
pinned bridge before any selection, loads the model/checkpoint once, calls
eval(), requires CUDA (no CPU fallback) and indexes the preprocessed list
directly for the union of the pilot and canonical-prefix indices. No
training, ETL, graph building or DataLoader is used. Canonical-prefix-only
records stay in memory. The canonical sample is never written.

The default mode runs invented fixtures only and reads no protected data.
The operator-only mode (--operator-real) verifies the fixed storage policy,
    path and link boundaries and every pinned SHA-256 before any
    deserialisation, binds every reopened input to verified bytes or open
    handles, and re-checks the inputs during and after the run. Upstream
imports, loads, inference, attribution and comparison run with Python
logging disabled, Python stdout/stderr sunk, OS file descriptors 1/2
redirected to the null device, the tracker offline, the network refused and
training/ETL/tracker/DataLoader tripwires armed; everything is restored in
finally. Console output carries status, counts and finding codes only.

PinnedBridge wires the operator-confirmed explain_patient entry points:
    _vocab_size, _invert, load_concept_names, torch.load(map_location=device,
    weights_only=False) and build_model for the single model load, then main's
    per-record loop (forward under no_grad, argmax, label_vocab lookup,
    model.explain with top_k and its returned logit/probability) packaged into
    the exact Step 16 record. The real
mode stays gated closed (BRIDGE_INTERFACE_CONFIRMED is False) until the
synthetic suite for this adapter has passed and the operator opens it.

Usage:
    python -m ehr_evidence_app.gnn.outputs
    python -m ehr_evidence_app.gnn.outputs --operator-real
"""

from __future__ import annotations

import argparse
import builtins
import contextlib
import ctypes
import gc
import hashlib
import importlib
import importlib.abc
import importlib.util
import io
import json
import logging
import math
import os
import socket
import sys
import types

import numpy as np

from ehr_evidence_app.gnn import mapping as mapping
from ehr_evidence_app.storage import policy as guard
from ehr_evidence_app.gnn import parity as parity

VERSION = "20.0"
MANIFEST_SCHEMA = "step20-offline-model-outputs-manifest"
AGREEMENT_SCHEMA = "step20-canonical-agreement"
SCHEMA_VERSION = "1.0"
OUTPUT_VERSION = "step-20-pilot10-v1"
UPSTREAM_ROOT = mapping.UPSTREAM_ROOT
PROTECTED_ROOT = guard.PRIVATE_ROOT + "\\protected-data"
OUTPUT_DIR = PROTECTED_ROOT + "\\model-outputs\\" + OUTPUT_VERSION
PARTIAL = ".partial"
OUTPUTS_NAME = "outputs.json"
MANIFEST_NAME = "manifest.json"
AGREEMENT_NAME = "canonical-agreement.json"

# Fixed before any run; never changed after seeing results.
ATOL = 1e-6
RTOL = 1e-6
TOP_K = 10
DEVICE = "cuda"
CANONICAL_PREFIX = (0, 1, 2)
PILOT_RECORDS = 10
# True only while PinnedBridge wires exactly the operator-confirmed
# explain_patient entry points (build_model, load_concept_names,
# _vocab_size, _invert, preprocess and the per-record main loop).
BRIDGE_INTERFACE_CONFIRMED = False

SOURCE_ROLES = ("explain_patient", "model")
# The vocabularies are small pinned JSON documents: only their verified
# bytes are parsed and handed to the bridge's own helpers.
VOCAB_ROLES = ("label_vocab", "concept_vocab")
PARSED_ROLES = ("step17_mapping", "canonical_explanations",
                "step19_summary") + VOCAB_ROLES
BYTE_ROLES = ("kg_concepts",)
STREAM_ROLES = ("graph", "checkpoint")
HASHED_ROLES = ("documentation",)

RECORD_KEYS = frozenset(("patient_index", "predicted_diagnosis", "drivers"))
PREDICTION_KEYS = frozenset(("label_index", "concept_id", "name", "logit",
                             "probability"))
DOMAINS = ("condition", "procedure", "drug")
DRIVER_KEYS = frozenset(("vocab_index", "concept_id", "name", "score"))
MISMATCH_CODES = ("length_mismatch", "missing_field", "extra_field",
                  "type_mismatch", "non_finite", "value_mismatch",
                  "float_mismatch")

STD_FDS = (1, 2)
TRACKER_ENV = {"CLEARML_OFFLINE_MODE": "1"}
FORBIDDEN_IMPORTS = {
    "training": (("train",), ("src.gnn.train",)),
    "etl": (("build_patient_graphs", "preprocess"),
            ("src.preprocess", "src.etl")),
    "tracker": (("clearml", "wandb", "mlflow", "comet_ml", "neptune"), ()),
}

LIMITATIONS = [
    "Records are the inherited bridge's output for the ten verified Step 17 "
    "pilot-10-v1 records only: a development pilot, not an evaluation "
    "cohort.",
    "The label_index-to-concept correspondence is not established; the "
    "predicted concept_id and name are unverified.",
    "probability is sigmoid(logit) and is not a calibrated clinical risk.",
    "Driver scores are unsigned concept-level sensitivity magnitudes for the "
    "selected label. They carry no visit or event and are not causes, "
    "directions, percentages or history-only evidence.",
    "Model inputs still include the final (target) visit's visit.x features "
    "and its procedure and drug edges; only final-visit condition edges are "
    "removed. No pre-target-only input or pre-observation forecast is "
    "claimed.",
    "No source event linkage exists and no derived patient, history, target "
    "or event evidence is attached.",
    "Upstream split membership is unknown.",
    "Canonical agreement covers only the three-record canonical prefix; the "
    "pilot records have no independent canonical reference.",
]


class Step20Error(Exception):
    """A fail-closed Step 20 failure carrying a finding code only."""


class AgreementError(Step20Error):
    """Canonical agreement failed; carries the aggregate report only."""

    def __init__(self, report):
        """Keep the aggregate report for sanitised console output."""
        super().__init__("canonical_agreement_failed")
        self.report = report


def real_plan():
    """Return the pinned assets and the fixed protected output."""
    data = UPSTREAM_ROOT + "\\data\\"
    gnn = UPSTREAM_ROOT + "\\src\\gnn\\"
    return {
        "upstream_root": UPSTREAM_ROOT,
        "assets": {
            "explain_patient": {
                "path": gnn + "explain_patient.py",
                "sha256": "1799f536c69d2312a4277f867f7836d7"
                          "cd56ddc54a448f24924597199a3b87d9"},
            "model": {
                "path": gnn + "model.py",
                "sha256": "558e1b83ccf7667896a8736b4916e2cd"
                          "230a9878d83bb9dc7f806a7acf75cfc6"},
            "graph": mapping.real_plan()["graph"],
            "checkpoint": {
                "path": data + "runs\\diag_kg\\checkpoints\\best_model.pt",
                "size_bytes": 19523207,
                "sha256": "5785e247d9a41d1d1534474d9771265e"
                          "a0918767607c44b5db9ea364d5326fe1"},
            "concept_vocab": {
                "path": data + "processed\\concept_vocab.json",
                "size_bytes": 449469,
                "sha256": "4ac38fc78a194abfd731cc38f178a83b"
                          "b1c3c67782aa2734576fa41dbcc67a4e"},
            "label_vocab": {
                "path": data + "processed\\label_vocab.json",
                "size_bytes": 2832,
                "sha256": "32c1e2dfe9fc89363f56d0b9a84edf82"
                          "8173ec1286dbc597cdb8ff10e1993a57"},
            "documentation": {
                "path": UPSTREAM_ROOT + "\\GNN_Replication_Documentation.md",
                "size_bytes": 14277,
                "sha256": "c825b55a6f5a5a48c4f99b6511cbf883"
                          "06c5ca54d9253450dd76d07d29042e26"},
            "canonical_explanations": {
                "path": data + "runs\\diag_kg\\explanations.json",
                "size_bytes": 11665,
                "sha256": "1d28152a4491860e0aa4bd12b0e24a6e"
                          "ec960cae1e1dde24a5fdedbbb13c41a7"},
            "step17_mapping": {
                "path": mapping.OUTPUT_DIR + "\\" + mapping.OUTPUT_NAME,
                "size_bytes": 17782,
                "sha256": "d12ddb031dc6f8f5e8c271af70a90d71"
                          "61073d2ef48dfc1de2cc3ac31dada18d"},
            "kg_concepts": {
                "path": PROTECTED_ROOT + "\\source-exports\\"
                        "legacy-export-20260901-v1\\kg_concepts.csv",
                "size_bytes": 3713011,
                "sha256": "d7b75fe631e721b91a026505de2fe61d"
                          "8f3ecdaa5606c8587332a79fdccdd52d"},
            "step19_summary": {
                "path": PROTECTED_ROOT + "\\verification\\"
                        "step-19-preprocessing-parity-final\\summary.json",
                "sha256": "4472843c61b5196cb3d999d71eec2ef1"
                          "5fbe75aee7f9a27b03712ba42f470b47"},
        },
        "output_dir": OUTPUT_DIR,
        "protected_root": PROTECTED_ROOT,
    }


def pinned_hash_problem(plan):
    """Return a code for the first pinned SHA-256 that is not 64 hex digits.

    A malformed pin could never match, so the run refuses to start.
    """
    for name, entry in sorted(plan["assets"].items()):
        digest = entry.get("sha256")
        if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef"
                       for char in digest.lower())):
            return "pinned_hash_malformed:%s" % name
    return None


def check_fixed_settings(atol=ATOL, rtol=RTOL, top_k=TOP_K, device=DEVICE):
    """Fail closed if a pre-decided tolerance or generation setting moved."""
    if (atol, rtol) != (1e-6, 1e-6):
        raise Step20Error("tolerance_changed")
    if top_k != 10 or device != "cuda" or CANONICAL_PREFIX != (0, 1, 2):
        raise Step20Error("settings_changed")


# --------------------------------------------------------------------------
# Pinned inputs: identity before deserialisation, re-checked afterwards
# --------------------------------------------------------------------------

def verify_hashed(entry, name, held):
    """Stream-hash a pinned asset without parsing it; hold its signature.

    The path must be a plain regular file with no link or reparse point;
    size (when pinned) and SHA-256 must match and the size and modification
    time must be unchanged across hashing.
    """
    try:
        before = mapping.regular_file_info(entry["path"])
        if ("size_bytes" in entry
                and before.st_size != entry["size_bytes"]):
            raise Step20Error("input_identity:input_size_mismatch:%s" % name)
        if mapping.sha256_file(entry["path"]) != entry["sha256"].lower():
            raise Step20Error("input_identity:input_hash_mismatch:%s" % name)
        after = mapping.regular_file_info(entry["path"])
    except mapping.MappingError as error:
        raise Step20Error("input_identity:%s" % error) from None
    signature = (before.st_size, before.st_mtime_ns)
    if (after.st_size, after.st_mtime_ns) != signature:
        raise Step20Error("input_identity:input_changed_during_hash:%s"
                          % name)
    held.append((entry["path"], signature, name, entry["sha256"].lower()))


def verify_read(entry, name, held):
    """Read a pinned small input once and verify those exact bytes."""
    try:
        data, signature = mapping.read_verified(
            entry["path"], name, "input_hash_mismatch:%s" % name,
            sha256=entry["sha256"], size=entry.get("size_bytes"))
    except mapping.MappingError as error:
        raise Step20Error("input_identity:%s" % error) from None
    held.append((entry["path"], signature, name, entry["sha256"].lower()))
    return data


def _sha256_handle(handle):
    """Return the SHA-256 of one seekable binary handle and rewind it."""
    digest = hashlib.sha256()
    handle.seek(0)
    for block in iter(lambda: handle.read(mapping.CHUNK_BYTES), b""):
        digest.update(block)
    handle.seek(0)
    return digest.hexdigest()


class VerifiedStream:
    """Keep one pinned file open and load only through that verified handle."""

    def __init__(self, entry, name, handle):
        """Bind the expected identity and already-open binary handle."""
        self.path = entry["path"]
        self.name = name
        self.expected = entry["sha256"].lower()
        self.size = entry.get("size_bytes")
        self.handle = handle

    def verify(self):
        """Re-hash the exact open file object and rewind it for its loader."""
        try:
            before = os.fstat(self.handle.fileno())
            digest = _sha256_handle(self.handle)
            after = os.fstat(self.handle.fileno())
        except (OSError, ValueError):
            raise Step20Error("input_identity:input_handle_invalid:%s"
                              % self.name) from None
        signature = (before.st_size, before.st_mtime_ns)
        if (after.st_size, after.st_mtime_ns) != signature:
            raise Step20Error("input_identity:input_changed_during_read:%s"
                              % self.name)
        if self.size is not None and before.st_size != self.size:
            raise Step20Error("input_identity:input_size_mismatch:%s"
                              % self.name)
        if digest != self.expected:
            raise Step20Error("input_identity:input_hash_mismatch:%s"
                              % self.name)

    def load(self, loader, *args, **kwargs):
        """Call a loader on this verified handle and verify it again after."""
        self.verify()
        try:
            value = loader(self.handle, *args, **kwargs)
        finally:
            self.verify()
        return value


def verify_stream(entry, name, held, stack):
    """Open, hash and retain one stream used later for deserialisation."""
    try:
        before = mapping.regular_file_info(entry["path"])
        handle = stack.enter_context(open(entry["path"], "rb"))
        opened = os.fstat(handle.fileno())
    except mapping.MappingError as error:
        raise Step20Error("input_identity:%s" % error) from None
    except OSError:
        raise Step20Error("input_identity:input_open_failed:%s" % name) \
            from None
    signature = (before.st_size, before.st_mtime_ns)
    if (opened.st_size, opened.st_mtime_ns) != signature:
        raise Step20Error("input_identity:input_changed_during_read:%s" % name)
    stream = VerifiedStream(entry, name, handle)
    stream.verify()
    held.append((entry["path"], signature, name, entry["sha256"].lower()))
    return stream


def verify_inputs(plan, stack):
    """Verify every pinned identity, then parse only the verified bytes.

    Returns held identities, parsed documents and content-bound inputs. No
    JSON is parsed until every asset's path, size and SHA-256 has passed.
    """
    held = []
    raw = {}
    for name in SOURCE_ROLES + PARSED_ROLES + BYTE_ROLES:
        raw[name] = verify_read(plan["assets"][name], name, held)
    for name in HASHED_ROLES:
        verify_hashed(plan["assets"][name], name, held)
    streams = {name: verify_stream(plan["assets"][name], name, held,
                                   stack)
               for name in STREAM_ROLES}
    confirm_inputs(held)
    documents = {}
    for name in PARSED_ROLES:
        try:
            documents[name] = mapping.parse_json(raw[name])
        except (UnicodeDecodeError, ValueError):
            raise Step20Error("input_not_json:%s" % name) from None
    bound = {"bytes": {name: raw[name]
                       for name in SOURCE_ROLES + BYTE_ROLES},
             "streams": streams}
    return held, documents, bound


def confirm_inputs(held, rehash=False):
    """Fail closed if a pinned path changed; optionally re-check its hash."""
    try:
        mapping.confirm_unchanged([item[:3] for item in held])
    except mapping.MappingError as error:
        raise Step20Error("input_identity:%s" % error) from None
    if rehash:
        for path, _, name, expected in held:
            try:
                before = mapping.regular_file_info(path)
                digest = mapping.sha256_file(path)
                after = mapping.regular_file_info(path)
            except mapping.MappingError as error:
                raise Step20Error("input_identity:%s" % error) from None
            if (before.st_size, before.st_mtime_ns) != (
                    after.st_size, after.st_mtime_ns):
                raise Step20Error(
                    "input_identity:input_changed_during_hash:%s" % name)
            if digest != expected:
                raise Step20Error("input_identity:input_hash_mismatch:%s"
                                  % name)


# --------------------------------------------------------------------------
# Step 17 selection and Step 19 parity record
# --------------------------------------------------------------------------

def strict_int(value, minimum=0):
    """Return True for a plain int (not bool) of at least minimum."""
    return type(value) is int and value >= minimum


def check_mapping_record(record):
    """Return a finding code for a Step 17 record not safe to select."""
    if not isinstance(record, dict):
        return "mapping_record_invalid"
    if record.get("phase4_eligible") is not False:
        return "mapping_phase4_flag"
    if record.get("identity_status") != mapping.STATUS_VERIFIED:
        return "mapping_record_not_verified"
    if record.get("findings") != []:
        return "mapping_record_has_findings"
    if record.get("repeated_visit_occurrence_ids") != []:
        return "mapping_record_repeated_visits"
    if not strict_int(record.get("patient_index")):
        return "mapping_patient_index_invalid"
    if (not strict_int(record.get("graph_position"))
            or record["graph_position"] < record["patient_index"]
            or not strict_int(record.get("subject_id"))):
        return "mapping_graph_link_invalid"
    nodes = record.get("visit_nodes")
    if not isinstance(nodes, list) or not nodes:
        return "mapping_visit_nodes_invalid"
    for node in nodes:
        if not isinstance(node, dict):
            return "mapping_visit_nodes_invalid"
        if node.get("repeated_visit_node") is not False:
            return "mapping_record_repeated_visits"
        if (node.get("link_status") != mapping.STATUS_VERIFIED
                or not strict_int(node.get("visit_occurrence_id"))):
            return "mapping_visit_nodes_invalid"
    return None


def select_pilot(document, plan):
    """Return the ten verified Step 17 selections sorted by patient_index.

    The document must be the pinned pilot-10-v1 identity mapping built
    against the pinned graph and bridge, with phase4_eligible false. Exactly
    ten records, all verified with no findings, quarantine or repeated
    visit nodes and unique non-null patient_index values, are required;
    anything else fails closed. Each selection keeps patient_index,
    graph_position and subject_id in memory only.
    """
    assets = plan["assets"]
    inputs = document.get("inputs") if isinstance(document, dict) else None
    if (not isinstance(inputs, dict)
            or document.get("schema") != mapping.SCHEMA
            or document.get("schema_version") != mapping.SCHEMA_VERSION
            or (document.get("scope") or {}).get("source_pilot_version")
            != mapping.PILOT_LABEL):
        raise Step20Error("mapping_schema_unexpected")
    if document.get("phase4_eligible") is not False:
        raise Step20Error("mapping_phase4_flag")
    for role, name in (("graph", "graph"), ("bridge", "explain_patient")):
        digest = (inputs.get(role) or {}).get("sha256")
        if (not isinstance(digest, str)
                or digest.lower() != assets[name]["sha256"].lower()):
            raise Step20Error("mapping_pinned_identity_mismatch")
    records = document.get("records")
    if not isinstance(records, list) or len(records) != PILOT_RECORDS:
        raise Step20Error("mapping_record_count")
    selection = []
    for record in records:
        problem = check_mapping_record(record)
        if problem:
            raise Step20Error(problem)
        selection.append({"patient_index": record["patient_index"],
                          "graph_position": record["graph_position"],
                          "subject_id": record["subject_id"]})
    indices = [item["patient_index"] for item in selection]
    if len(set(indices)) != PILOT_RECORDS:
        raise Step20Error("mapping_patient_index_repeated")
    if len({item["graph_position"] for item in selection}) != PILOT_RECORDS:
        raise Step20Error("mapping_graph_link_invalid")
    return sorted(selection, key=lambda item: item["patient_index"])


def check_selection_graphs(graphs, selection):
    """Confirm each selection against the raw pinned graph list.

    The graph at graph_position must carry y_diagnosis, sit at bridge
    position patient_index under the y_diagnosis filter and carry the
    mapped subject_id; patient_index is never trusted alone.
    """
    if not isinstance(graphs, (list, tuple)):
        raise Step20Error("graph_collection_not_list")
    wanted = {item["graph_position"]: item for item in selection}
    last = max(wanted)
    if last >= len(graphs):
        raise Step20Error("mapping_graph_position_mismatch")
    bridge_index = 0
    for position in range(last + 1):
        graph = graphs[position]
        kept = parity.is_diagnosis_graph(graph)
        item = wanted.get(position)
        if item is not None:
            if not kept or bridge_index != item["patient_index"]:
                raise Step20Error("mapping_graph_position_mismatch")
            subject = mapping.canonical_int(getattr(graph, "subject_id",
                                                    None))
            if subject != item["subject_id"]:
                raise Step20Error("mapping_subject_mismatch")
        if kept:
            bridge_index += 1


def check_step19(document, plan):
    """Return the approved Step 19 counts and model-input digest.

    The summary must have passed for the same pinned graph, bridge and
    model with phase4_eligible false.
    """
    assets = plan["assets"]
    if not isinstance(document, dict):
        raise Step20Error("step19_summary_unexpected")
    sources = document.get("source_sha256") or {}
    counts = document.get("counts") or {}
    digest = document.get("model_input_digest")
    if (document.get("status") != "passed"
            or document.get("phase4_eligible") is not False
            or document.get("graph_sha256")
            != assets["graph"]["sha256"].lower()
            or sources.get("explain_patient")
            != assets["explain_patient"]["sha256"].lower()
            or sources.get("model") != assets["model"]["sha256"].lower()
            or not isinstance(digest, str) or len(digest) != 64
            or not strict_int(counts.get("graphs_total"), 1)
            or not strict_int(counts.get("graphs_filtered"), 1)):
        raise Step20Error("step19_summary_unexpected")
    return {"graphs_total": counts["graphs_total"],
            "graphs_filtered": counts["graphs_filtered"],
            "digest": digest}


def check_preprocessed(graphs, processed, step19):
    """Confirm the single full preprocess matches the Step 19 parity record.

    Collection sizes must equal the Step 19 counts and the model-input
    digest of the preprocessed list must equal the approved digest.
    """
    if not isinstance(processed, list):
        raise Step20Error("preprocess_result_not_list")
    if (len(graphs) != step19["graphs_total"]
            or len(processed) != step19["graphs_filtered"]):
        raise Step20Error("preprocess_count_mismatch")
    try:
        digest = parity.digest_inputs(parity.collection_inputs(processed))
    except parity.ParityError:
        raise Step20Error("preprocess_inputs_invalid") from None
    if digest != step19["digest"]:
        raise Step20Error("preprocess_digest_mismatch")


# --------------------------------------------------------------------------
# Bridge record schema and canonical comparison
# --------------------------------------------------------------------------

def finite_float(value):
    """Return True for a finite plain float."""
    return type(value) is float and math.isfinite(value)


def schema_problem(record, top_k=TOP_K):
    """Return a finding code when a record breaks the bridge schema.

    The shape is the Step 16 bridge record: patient_index, one
    predicted_diagnosis and per-domain driver lists of at most top_k
    entries with finite unsigned scores.
    """
    if not isinstance(record, dict) or set(record) != RECORD_KEYS:
        return "record_schema_invalid"
    if not strict_int(record["patient_index"]):
        return "record_schema_invalid"
    prediction = record["predicted_diagnosis"]
    if not isinstance(prediction, dict) or set(prediction) != PREDICTION_KEYS:
        return "record_schema_invalid"
    if (not strict_int(prediction["label_index"])
            or not strict_int(prediction["concept_id"], -2 ** 63)
            or not isinstance(prediction["name"], str)):
        return "record_schema_invalid"
    if not (finite_float(prediction["logit"])
            and finite_float(prediction["probability"])
            and 0.0 <= prediction["probability"] <= 1.0):
        return "record_non_finite"
    drivers = record["drivers"]
    if not isinstance(drivers, dict) or set(drivers) != set(DOMAINS):
        return "record_schema_invalid"
    for domain in DOMAINS:
        entries = drivers[domain]
        if not isinstance(entries, list) or len(entries) > top_k:
            return "record_schema_invalid"
        for entry in entries:
            if (not isinstance(entry, dict) or set(entry) != DRIVER_KEYS
                    or not strict_int(entry["vocab_index"])
                    or not (entry["concept_id"] is None or strict_int(
                        entry["concept_id"], -2 ** 63))
                    or not isinstance(entry["name"], str)):
                return "record_schema_invalid"
            if not finite_float(entry["score"]) or entry["score"] < 0.0:
                return "record_non_finite"
    return None


def normalise_record(record):
    """Return the record exactly as the bridge's JSON output would hold it.

    A JSON round trip keeps every value and field; anything that is not
    JSON-serialisable fails closed.
    """
    try:
        return json.loads(json.dumps(record))
    except (TypeError, ValueError):
        raise Step20Error("record_not_serialisable") from None


def compare_values(candidate, reference, counts, stats, atol, rtol):
    """Recursively compare one regenerated value with its canonical value.

    Objects compare key sets exactly; arrays compare length and order;
    floats must both be finite and close; every other value must have the
    same type and be equal.
    """
    if isinstance(reference, dict):
        if not isinstance(candidate, dict):
            counts["type_mismatch"] += 1
            return
        counts["missing_field"] += len(set(reference) - set(candidate))
        counts["extra_field"] += len(set(candidate) - set(reference))
        for key in reference:
            if key in candidate:
                compare_values(candidate[key], reference[key], counts, stats,
                               atol, rtol)
        return
    if isinstance(reference, list):
        if not isinstance(candidate, list):
            counts["type_mismatch"] += 1
            return
        if len(candidate) != len(reference):
            counts["length_mismatch"] += 1
        for item, expected in zip(candidate, reference):
            compare_values(item, expected, counts, stats, atol, rtol)
        return
    stats["values"] += 1
    if type(candidate) is not type(reference):
        counts["type_mismatch"] += 1
    elif type(reference) is float:
        if not (math.isfinite(candidate) and math.isfinite(reference)):
            counts["non_finite"] += 1
            return
        error = abs(candidate - reference)
        stats["max_abs_error"] = max(stats["max_abs_error"], error)
        if error > atol + rtol * abs(reference):
            counts["float_mismatch"] += 1
    elif candidate != reference:
        counts["value_mismatch"] += 1


def compare_canonical(regenerated, canonical, atol=ATOL, rtol=RTOL):
    """Return the aggregate agreement report of regenerated vs canonical.

    The report holds only the compared counts, per-category mismatch counts
    and the maximum absolute float error; it holds no index, ID, score or
    row data. Tolerances are fixed and cannot be overridden.
    """
    check_fixed_settings(atol, rtol)
    counts = dict.fromkeys(MISMATCH_CODES, 0)
    stats = {"values": 0, "max_abs_error": 0.0}
    compare_values(regenerated, canonical, counts, stats, atol, rtol)
    return {
        "schema": AGREEMENT_SCHEMA, "schema_version": SCHEMA_VERSION,
        "status": "passed" if not any(counts.values()) else "failed",
        "compared_records": len(canonical),
        "compared_values": stats["values"],
        "mismatch_counts": counts,
        "max_abs_error": stats["max_abs_error"],
        "tolerance": {"atol": ATOL, "rtol": RTOL},
    }


def canonical_prefix(document):
    """Validate the canonical sample as the three-record canonical prefix."""
    if not isinstance(document, list) or len(document) != len(
            CANONICAL_PREFIX):
        raise Step20Error("canonical_prefix_unexpected")
    for record in document:
        if schema_problem(record):
            raise Step20Error("canonical_schema_invalid")
    if [record["patient_index"] for record in document] != list(
            CANONICAL_PREFIX):
        raise Step20Error("canonical_prefix_unexpected")
    return document


# --------------------------------------------------------------------------
# Isolation: silencing, network, tracker and tripwires
# --------------------------------------------------------------------------

class _DiscardSink:
    """Internal text stream that accepts and drops everything written."""

    encoding = "utf-8"

    def write(self, text):
        """Discard the text and report it as written."""
        return len(text)

    def writelines(self, lines):
        """Discard every line."""

    def flush(self):
        """Nothing is buffered, so there is nothing to flush."""

    def isatty(self):
        """The sink is never a terminal."""
        return False

    def writable(self):
        """The sink accepts writes."""
        return True


def flush_native_streams():
    """Best-effort flush of C runtime stdio buffers into the null device.

    Called before descriptors 1/2 are restored so buffered native output
    is not written to the console later. Other C runtimes' buffers are
    outside this flush.
    """
    names = ("ucrtbase", "msvcrt") if os.name == "nt" else (None,)
    for name in names:
        with contextlib.suppress(Exception):
            ctypes.CDLL(name).fflush(None)


@contextlib.contextmanager
def silenced():
    """Silence Python logging, sys streams and OS descriptors 1 and 2.

    Pending Python output is flushed first; descriptors 1/2 are duplicated,
    pointed at the null device and restored in finally (after a best-effort
    C runtime flush), together with sys.stdout/sys.stderr, the
    logging-disable level and the root logger's handlers, level and
    disabled flag (handlers created inside are closed).
    """
    root = logging.getLogger()
    saved_level = logging.root.manager.disable
    saved_streams = (sys.stdout, sys.stderr)
    saved_handlers = list(root.handlers)
    saved_root = (root.level, root.disabled)
    for stream in saved_streams:
        with contextlib.suppress(AttributeError, OSError, ValueError):
            stream.flush()
    devnull = os.open(os.devnull, os.O_WRONLY)
    saved_fds = []
    try:
        for fd in STD_FDS:
            saved_fds.append((fd, os.dup(fd)))
    except OSError:
        for _, copy in saved_fds:
            os.close(copy)
        os.close(devnull)
        raise Step20Error("fd_redirect_unavailable") from None
    try:
        for fd, _ in saved_fds:
            os.dup2(devnull, fd)
        logging.disable(logging.CRITICAL)
        sys.stdout = sys.stderr = _DiscardSink()
        yield
    finally:
        sys.stdout, sys.stderr = saved_streams
        flush_native_streams()
        for fd, copy in saved_fds:
            os.dup2(copy, fd)
            os.close(copy)
        os.close(devnull)
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_root[0])
        root.disabled = saved_root[1]
        logging.disable(saved_level)


class Tripwires:
    """Records tripwire hits; hits are fatal even if upstream swallows them."""

    def __init__(self):
        """Start with no hits."""
        self.hits = []

    def trip(self, code):
        """Record a hit and raise it."""
        self.hits.append(code)
        raise Step20Error(code)

    def check(self):
        """Fail closed on the first recorded hit."""
        if self.hits:
            raise Step20Error(self.hits[0])


class _ImportBlocker(importlib.abc.MetaPathFinder):
    """Meta-path finder refusing training, ETL/builder and tracker modules."""

    def __init__(self, trips):
        """Bind the tripwire recorder."""
        self.trips = trips

    def find_spec(self, fullname, path=None, target=None):
        """Refuse a forbidden module; defer everything else."""
        category = forbidden_category(fullname)
        if category:
            self.trips.hits.append("forbidden_import:%s" % category)
            raise ImportError("import refused by the Step 20 tripwire")
        return None


def forbidden_category(name):
    """Return the forbidden category of a module name, else None."""
    top = name.split(".")[0]
    for category, (tops, prefixes) in FORBIDDEN_IMPORTS.items():
        if top in tops or any(name == prefix or name.startswith(prefix + ".")
                              for prefix in prefixes):
            return category
    return None


@contextlib.contextmanager
def network_blocked(trips):
    """Refuse socket connections and name lookups inside the block."""
    def refuse(*args, **kwargs):
        """Record and raise instead of opening a network connection."""
        trips.trip("network_refused")
    saved = (socket.socket.connect, socket.socket.connect_ex,
             socket.create_connection, socket.getaddrinfo)
    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse
    socket.create_connection = refuse
    socket.getaddrinfo = refuse
    try:
        yield
    finally:
        (socket.socket.connect, socket.socket.connect_ex,
         socket.create_connection, socket.getaddrinfo) = saved


@contextlib.contextmanager
def tracker_offline():
    """Force the tracker offline inside the block; restore the environment."""
    saved = {name: os.environ.get(name) for name in TRACKER_ENV}
    os.environ.update(TRACKER_ENV)
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def forbidden_preloaded_problem(modules=None):
    """Return a code when a forbidden module is already imported.

    A preloaded module would bypass the import finder, so the real run
    (a fresh process) refuses to start in that case.
    """
    names = list(sys.modules if modules is None else modules)
    if any(forbidden_category(name) for name in names):
        return "forbidden_module_preloaded"
    return None


@contextlib.contextmanager
def imports_blocked(trips):
    """Install the forbidden-import finder for the duration of the block."""
    blocker = _ImportBlocker(trips)
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            sys.meta_path.remove(blocker)


@contextlib.contextmanager
def isolated(trips):
    """Tracker offline, network refused, forbidden imports blocked, silent."""
    with tracker_offline(), network_blocked(trips), imports_blocked(trips), \
            silenced():
        yield


@contextlib.contextmanager
def torch_tripwires(torch, trips):
    """Refuse DataLoader use, training mode and optimiser creation.

    Module.train(False) (used by eval()) stays allowed; the originals are
    restored in finally.
    """
    loader = torch.utils.data.DataLoader
    module = torch.nn.Module
    optimizer = torch.optim.Optimizer
    saved = (loader.__init__, module.train, optimizer.__init__)
    original_train = saved[1]

    def refuse_loader(self, *args, **kwargs):
        """Refuse any DataLoader construction."""
        trips.trip("dataloader_used")

    def guarded_train(self, mode=True):
        """Refuse switching into training mode."""
        if mode:
            trips.trip("training_mode_used")
        return original_train(self, mode)

    def refuse_optimizer(self, *args, **kwargs):
        """Refuse any optimiser construction."""
        trips.trip("optimizer_created")

    loader.__init__ = refuse_loader
    module.train = guarded_train
    optimizer.__init__ = refuse_optimizer
    try:
        yield
    finally:
        loader.__init__, module.train, optimizer.__init__ = saved


def check_upstream_modules(plan, trips):
    """Record a hit for any loaded upstream module other than the pinned two.

    Pinned bridge and model files are the only upstream code allowed.
    """
    pinned = {os.path.normcase(os.path.abspath(plan["assets"][name]["path"]))
              for name in SOURCE_ROLES}
    for module in list(sys.modules.values()):
        path = getattr(module, "__file__", None)
        if not isinstance(path, str):
            continue
        full = os.path.normcase(os.path.abspath(path))
        if guard.is_within(full, plan["upstream_root"]) and full not in pinned:
            trips.hits.append("unpinned_upstream_import")


def require_cuda(torch):
    """Fail closed unless CUDA is available; there is no CPU fallback."""
    try:
        available = (bool(torch.cuda.is_available())
                     and int(torch.cuda.device_count()) >= 1)
    except Exception:  # noqa: BLE001 - any probe failure means unavailable
        available = False
    if not available:
        raise Step20Error("cuda_unavailable")


def prepare_model(model):
    """Call eval() and confirm eval mode and CUDA placement of every part."""
    model.eval()
    check_model(model)


def check_model(model):
    """Fail closed if any module is training or any parameter is off CUDA."""
    modules = list(model.modules()) if hasattr(model, "modules") else [model]
    if any(getattr(item, "training", True) for item in modules):
        raise Step20Error("model_not_eval")
    parameters = list(model.parameters())
    if not parameters:
        raise Step20Error("model_has_no_parameters")
    if not all(getattr(item, "is_cuda", False) for item in parameters):
        raise Step20Error("model_not_on_cuda")


# --------------------------------------------------------------------------
# Pinned bridge adapter (real mode)
# --------------------------------------------------------------------------

def package_record(patient_index, label_index, concept_id, logit,
                   probability, explanation, inverse, names):
    """Return one Step 16 bridge record in the bridge's key order.

    Each domain's drivers are the (vocab_index, score) pairs returned by
    model.explain, in returned order, with concept_id from that domain's
    inverse vocabulary (null when absent) and name from the concept-name
    map ("" when absent). Values are kept, not rescaled or re-sorted.
    """
    if not isinstance(explanation, dict) or not set(DOMAINS) <= set(
            explanation):
        raise Step20Error("bridge_explanation_unexpected")
    drivers = {}
    for domain in DOMAINS:
        entries = []
        for vocab_index, score in explanation[domain]:
            index = int(vocab_index)
            driver_concept = inverse[domain].get(index)
            entries.append({
                "vocab_index": index,
                "concept_id": driver_concept,
                "name": ("" if driver_concept is None
                         else names.get(driver_concept, "")),
                "score": float(score)})
        drivers[domain] = entries
    return {"patient_index": patient_index,
            "predicted_diagnosis": {
                "label_index": label_index,
                "concept_id": concept_id,
                "name": names.get(concept_id, ""),
                "logit": logit,
                "probability": probability},
             "drivers": drivers}


@contextlib.contextmanager
def verified_text_open(path, data):
    """Serve one verified UTF-8 byte string to an upstream text helper."""
    original = builtins.open
    expected = os.path.normcase(os.path.abspath(path))

    def open_verified(file, *args, **kwargs):
        """Return verified in-memory text for the one pinned path."""
        try:
            requested = os.path.normcase(os.path.abspath(os.fspath(file)))
        except TypeError:
            requested = None
        if requested != expected:
            return original(file, *args, **kwargs)
        mode = kwargs.get("mode", args[0] if args else "r")
        if "b" in mode or any(flag in mode for flag in "wax+"):
            raise Step20Error("verified_text_mode_rejected")
        return io.StringIO(data.decode("utf-8", errors="replace"),
                           newline="")

    builtins.open = open_verified
    try:
        yield
    finally:
        builtins.open = original


def module_from_verified_source(name, path, source):
    """Execute one module directly from its already hash-verified bytes."""
    module = types.ModuleType(name)
    module.__file__ = path
    module.__package__ = name.rpartition(".")[0]
    module.__spec__ = importlib.util.spec_from_loader(name, loader=None,
                                                      origin=path)
    try:
        code = compile(source, path, "exec")
        exec(code, module.__dict__)
    except (SyntaxError, UnicodeError):
        raise Step20Error("verified_source_invalid:%s" % name) from None
    return module


class PinnedBridge:
    """Adapter over the imported pinned explain_patient module.

    It calls only the operator-confirmed entry points of the pinned
    source: preprocess(graphs) (Step 19), _vocab_size, _invert,
    load_concept_names and build_model, and it repeats main's per-record
    loop (forward under no_grad, argmax, label_vocab lookup and
    model.explain). The upstream source is never modified.
    """

    def __init__(self, explain, plan, torch, verified):
        """Keep the pinned module, plan, torch and bound verified inputs."""
        self.explain_module = explain
        self.plan = plan
        self.torch = torch
        self.verified = verified
        self.label_vocab = None
        self.inverse = None
        self.names = None

    def preprocess(self, graphs):
        """Run the pinned full-collection bridge preprocessing once."""
        return self.explain_module.preprocess(graphs)

    def load_model(self, device, vocabularies):
        """Rebuild the pinned model once, as explain_patient.main does.

        The verified label and concept vocabularies give the label count,
        per-domain vocabulary sizes and inverse maps (through the bridge's
        _vocab_size and _invert); build_model receives its required cond,
        proc and drug aliases. Names come from load_concept_names served the
        verified concept CSV bytes. The pinned checkpoint is loaded from its
        verified open handle with
        torch.load(map_location=device, weights_only=False) and passed to
        build_model, which loads its model_state and calls eval().
        """
        module, torch = self.explain_module, self.torch
        label_vocab = vocabularies["label_vocab"]
        concept_vocab = vocabularies["concept_vocab"]
        vocab_sizes = {
            short: module._vocab_size(concept_vocab, domain)
            for short, domain in (("cond", "condition"),
                                  ("proc", "procedure"),
                                  ("drug", "drug"))}
        inverse = {domain: module._invert(concept_vocab, domain)
                   for domain in DOMAINS}
        concepts_path = self.plan["assets"]["kg_concepts"]["path"]
        with verified_text_open(
                concepts_path, self.verified["bytes"]["kg_concepts"]):
            names = module.load_concept_names(concepts_path)
        target = torch.device(device)
        checkpoint = self.verified["streams"]["checkpoint"].load(
            torch.load, map_location=target, weights_only=False)
        model = module.build_model(checkpoint, len(label_vocab), vocab_sizes,
                                   target)
        del checkpoint
        self.label_vocab, self.inverse, self.names = (label_vocab, inverse,
                                                      names)
        return model

    def explain(self, model, graph, patient_index, top_k, device):
        """Return one bridge record for one directly indexed graph.

        Repeats main's loop body: move the graph to the device, take the
        diagnosis logits under no_grad, select the argmax label, look it up
        in label_vocab and call model.explain for that label with top_k.
        """
        if self.label_vocab is None:
            raise Step20Error("bridge_model_not_loaded")
        torch = self.torch
        g = graph.to(torch.device(device))
        with torch.no_grad():
            logits = model(g, task="diagnosis")["diagnosis"][0]
        label_index = int(torch.argmax(logits).item())
        concept_id = int(self.label_vocab[label_index])
        logit = logits[label_index]
        explanation = model.explain(g, label_idx=label_index,
                                    task="diagnosis", top_k=top_k)
        try:
            explained_logit = float(explanation["logit"])
            explained_probability = float(explanation["probability"])
        except (KeyError, TypeError, ValueError):
            raise Step20Error("bridge_explanation_unexpected") from None
        return package_record(patient_index, label_index, concept_id,
                              explained_logit, explained_probability,
                              explanation, self.inverse, self.names)


def pinned_bridge(plan, verified):
    """Execute the bridge and model only from their verified source bytes."""
    source = verified["bytes"]
    saved_path = list(sys.path)
    sentinel = object()
    saved_model = sys.modules.get("model", sentinel)
    try:
        model = module_from_verified_source(
            "model", plan["assets"]["model"]["path"], source["model"])
        sys.modules["model"] = model
        explain = module_from_verified_source(
            "step20_upstream_explain_patient",
            plan["assets"]["explain_patient"]["path"],
            source["explain_patient"])
    finally:
        sys.path[:] = saved_path
        if saved_model is sentinel:
            sys.modules.pop("model", None)
        else:
            sys.modules["model"] = saved_model
    return PinnedBridge(explain, plan, import_torch(), verified)


def import_torch():
    """Import torch lazily (omopgnn only; the app environment excludes it)."""
    return importlib.import_module("torch")


def torch_graph_loader(handle):
    """Deserialise the pinned graph list once from its verified handle."""
    import torch  # Imported lazily: the app environment excludes torch.
    return torch.load(handle, map_location="cpu", weights_only=False)


def real_runtime():
    """Return the real torch import, graph loader and bridge factory."""
    return types.SimpleNamespace(import_torch=import_torch,
                                 graph_loader=torch_graph_loader,
                                 bridge_factory=pinned_bridge)


# --------------------------------------------------------------------------
# Generation, manifest and atomic publication
# --------------------------------------------------------------------------

def generate(bridge, model, processed, indices):
    """Return {index: normalised bridge record} by direct list indexing."""
    records = {}
    for index in indices:
        record = normalise_record(bridge.explain(model, processed[index],
                                                 index, TOP_K, DEVICE))
        problem = schema_problem(record)
        if problem:
            raise Step20Error(problem)
        if record["patient_index"] != index:
            raise Step20Error("record_index_mismatch")
        records[index] = record
    return records


def run_isolated(plan, runtime, documents, held, bound=None):
    """Run the upstream phase in isolation; return (outputs, report, info).

    Order: torch import, tripwires, CUDA check, bridge import, one graph
    load, selection check on the raw list, one full preprocess, Step 19
    parity digest, one model/checkpoint load with eval(), direct-index
    generation for the pilot and canonical-prefix union and the canonical
    comparison. Inputs are re-confirmed around each load. Only the ten
    pilot records are returned; canonical-prefix-only records are dropped.
    """
    selection = select_pilot(documents["step17_mapping"], plan)
    canonical = canonical_prefix(documents["canonical_explanations"])
    step19 = check_step19(documents["step19_summary"], plan)
    pilot = [item["patient_index"] for item in selection]
    union = sorted(set(pilot) | set(CANONICAL_PREFIX))
    vocabularies = {name: documents.get(name) for name in VOCAB_ROLES}
    trips = Tripwires()
    try:
        with isolated(trips):
            records, report, runtime_info = upstream_phase(
                plan, runtime, held, trips, selection, step19, canonical,
                union, vocabularies, bound)
    except Exception:
        if trips.hits:
            raise Step20Error(trips.hits[0]) from None
        raise
    trips.check()
    confirm_inputs(held, rehash=bool(held))
    outputs = [records[index] for index in pilot]
    for index in set(union) - set(pilot):
        del records[index]
    if report["status"] != "passed":
        raise AgreementError(report)
    return outputs, report, runtime_info


def upstream_phase(plan, runtime, held, trips, selection, step19, canonical,
                   union, vocabularies, bound):
    """Run the isolated upstream sequence; return (records, report, info).

    Called only inside isolated(); a tripwire hit swallowed by upstream
    code still fails the run afterwards. vocabularies holds the verified
    label and concept vocabularies for the bridge's model load.
    """
    torch = runtime.import_torch()
    with torch_tripwires(torch, trips):
        require_cuda(torch)
        bridge = runtime.bridge_factory(plan, bound)
        check_upstream_modules(plan, trips)
        trips.check()
        confirm_inputs(held)
        if bound is None:
            graphs = runtime.graph_loader(plan["assets"]["graph"]["path"])
        else:
            graphs = bound["streams"]["graph"].load(runtime.graph_loader)
        confirm_inputs(held)
        check_selection_graphs(graphs, selection)
        processed = bridge.preprocess(graphs)
        check_preprocessed(graphs, processed, step19)
        del graphs
        model = bridge.load_model(DEVICE, vocabularies)
        confirm_inputs(held)
        prepare_model(model)
        records = generate(bridge, model, processed, union)
        check_model(model)
        del processed, model
        gc.collect()
        report = compare_canonical(
            [records[index] for index in CANONICAL_PREFIX], canonical)
        runtime_info = {
            "torch": str(getattr(torch, "__version__", "unknown")),
            "cuda": str(getattr(getattr(torch, "version", None), "cuda",
                                "unknown"))}
    check_upstream_modules(plan, trips)
    trips.check()
    return records, report, runtime_info


def build_manifest(plan, runtime_info, report):
    """Return the manifest: settings, assets and limitations only.

    It carries no patient index, ID, score or record content.
    """
    assets = {}
    for name, entry in sorted(plan["assets"].items()):
        assets[name] = {"path": entry["path"],
                        "sha256": entry["sha256"].lower()}
        if "size_bytes" in entry:
            assets[name]["size_bytes"] = entry["size_bytes"]
    return {
        "schema": MANIFEST_SCHEMA, "schema_version": SCHEMA_VERSION,
        "step": "20", "script_version": VERSION,
        "output_version": OUTPUT_VERSION, "status": "published",
        "settings": {
            "top_k": TOP_K, "device": DEVICE, "cpu_fallback": False,
            "model_mode": "eval", "graph_collection_loads": 1,
            "full_collection_preprocess_runs": 1, "checkpoint_loads": 1,
            "graph_indexing": ("direct index into the single preprocessed "
                               "full collection"),
            "canonical_prefix": ("first three bridge records "
                                 "(num_patients=3, top_k=10)"),
            "tolerance": {"atol": ATOL, "rtol": RTOL},
            "tolerance_fixed_before_run": True,
            "tracker": "offline", "network": "blocked",
            "training_etl_graph_builder_dataloader": "not used"},
        "runtime": runtime_info,
        "assets": assets,
        "selection": {
            "source": "Step 17 pilot-10-v1 identity mapping",
            "records": PILOT_RECORDS, "identity_status": "verified",
            "order": "ascending bridge record index"},
        "counts": {"published_records": PILOT_RECORDS,
                   "canonical_records_compared": report["compared_records"]},
        "canonical_agreement": report["status"],
        "phase4_eligible": False,
        "label_index_correspondence": "not_established",
        "source_event_linkage": "absent",
        "history_only_claim_allowed": False,
        "upstream_split_membership": "unknown",
        "pre_target_only_input": False,
        "target_visit_inputs_remaining": [
            "final-visit visit.x features", "final-visit procedure edges",
            "final-visit drug edges"],
        "derived_evidence_attached": False,
        "canonical_artefacts_modified": False,
        "limitations": list(LIMITATIONS),
    }


def output_preconditions(plan):
    """Refuse an existing, unprotected or upstream output or partial path."""
    canonical = plan["assets"]["canonical_explanations"]["path"]
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        if (guard.is_within(path, plan["upstream_root"])
                or guard.is_within(canonical, path)
                or guard.is_within(path, os.path.dirname(canonical))):
            raise Step20Error("output_is_upstream")
        if os.path.lexists(path):
            raise Step20Error("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise Step20Error(problem)


def encode(payload, sort_keys):
    """Return strict JSON bytes (no NaN/Infinity) with a final newline."""
    try:
        text = json.dumps(payload, indent=2, sort_keys=sort_keys,
                          allow_nan=False)
    except ValueError:
        raise Step20Error("record_non_finite") from None
    return (text + "\n").encode("utf-8")


def publish(plan, outputs, report, manifest):
    """Publish the three files atomically as one new directory.

    They are written into a new protected partial sibling and renamed into
    place; an existing final or partial directory is refused. Bridge record
    key order is kept. Returns {file name: SHA-256}.
    """
    for record in outputs:
        if schema_problem(record):
            raise Step20Error("record_schema_invalid")
    output_preconditions(plan)
    files = {OUTPUTS_NAME: encode(outputs, sort_keys=False),
             AGREEMENT_NAME: encode(report, sort_keys=True)}
    manifest = dict(manifest)
    manifest["files"] = {name: {"size_bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest()}
                         for name, data in sorted(files.items())}
    files[MANIFEST_NAME] = encode(manifest, sort_keys=True)
    partial = plan["output_dir"] + PARTIAL
    os.makedirs(os.path.dirname(plan["output_dir"]), exist_ok=True)
    os.mkdir(partial)
    for name in (OUTPUTS_NAME, AGREEMENT_NAME, MANIFEST_NAME):
        with open(os.path.join(partial, name), "xb") as handle:
            handle.write(files[name])
            handle.flush()
            os.fsync(handle.fileno())
    os.rename(partial, plan["output_dir"])
    return {name: hashlib.sha256(data).hexdigest()
            for name, data in sorted(files.items())}


def prepare_outputs(plan, runtime):
    """Verify, generate, compare and publish; return a sanitised summary."""
    check_fixed_settings()
    problem = pinned_hash_problem(plan)
    if problem:
        raise Step20Error(problem)
    output_preconditions(plan)
    with contextlib.ExitStack() as stack:
        held, documents, bound = verify_inputs(plan, stack)
        outputs, report, runtime_info = run_isolated(
            plan, runtime, documents, held, bound)
        manifest = build_manifest(plan, runtime_info, report)
        digests = publish(plan, outputs, report, manifest)
    return {"status": "published", "report": report, "digests": digests,
            "pilot_records": len(outputs)}


def run_real(plan=None, runtime=None):
    """Operator-only real run; gated closed until the bridge is confirmed."""
    if not BRIDGE_INTERFACE_CONFIRMED:
        raise Step20Error("bridge_interface_unconfirmed")
    if guard.fixed_policy_problem():
        raise Step20Error("fixed_policy_rejected")
    problem = forbidden_preloaded_problem()
    if problem:
        raise Step20Error(problem)
    return prepare_outputs(plan or real_plan(), runtime or real_runtime())


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

SYNTHETIC_SUBJECT_ID_BASE = 9900000
SYNTHETIC_PERSON_ID_BASE = 7700000
SYNTHETIC_VISIT_ID_BASE = 8800000


class SyntheticModule:
    """Invented stand-in for torch.nn.Module."""

    def __init__(self):
        """Start in training mode, as torch modules do."""
        self.training = True

    def train(self, mode=True):
        """Set the training flag."""
        self.training = bool(mode)
        return self

    def eval(self):
        """Switch to evaluation mode through train(False)."""
        return self.train(False)


class SyntheticDataLoader:
    """Invented stand-in for torch.utils.data.DataLoader."""

    def __init__(self, *args, **kwargs):
        """Accept anything (the tripwire replaces this)."""


class SyntheticOptimizer:
    """Invented stand-in for torch.optim.Optimizer."""

    def __init__(self, *args, **kwargs):
        """Accept anything (the tripwire replaces this)."""


class SyntheticModel(SyntheticModule):
    """Invented model whose parameters report a device placement."""

    def __init__(self, on_cuda=True):
        """Create one invented parameter on the requested device."""
        super().__init__()
        self.params = [types.SimpleNamespace(is_cuda=on_cuda)]

    def modules(self):
        """Return this module only."""
        return [self]

    def parameters(self):
        """Return the invented parameters."""
        return list(self.params)


def synthetic_torch(cuda=True):
    """Return an invented torch-like namespace (torch is never imported)."""
    return types.SimpleNamespace(
        __version__="synthetic",
        version=types.SimpleNamespace(cuda="synthetic"),
        cuda=types.SimpleNamespace(is_available=lambda: cuda,
                                   device_count=lambda: 1 if cuda else 0),
        utils=types.SimpleNamespace(data=types.SimpleNamespace(
            DataLoader=SyntheticDataLoader)),
        nn=types.SimpleNamespace(Module=SyntheticModule),
        optim=types.SimpleNamespace(Optimizer=SyntheticOptimizer))


def synthetic_collection(count=16, skipped=(3, 9)):
    """Return an invented raw graph list; skipped graphs lack y_diagnosis."""
    graphs = []
    for position in range(count):
        visits = 2 + position % 3
        x = (np.arange(visits * 6, dtype=np.float32).reshape(visits, 6)
             + np.float32(position))
        nodes = {parity.VISIT: types.SimpleNamespace(x=x)}
        links = {(parity.VISIT, parity.CONDITION_RELATION, "condition"):
                 types.SimpleNamespace(edge_index=np.array(
                     [list(range(visits)), [0] * visits], dtype=np.int64))}
        nodes["condition"] = types.SimpleNamespace(
            x=np.ones((1, 2), dtype=np.float32))
        graph = parity.SyntheticGraph(nodes, links,
                                      diagnosis=position not in skipped)
        graph.subject_id = SYNTHETIC_SUBJECT_ID_BASE + position
        graphs.append(graph)
    return graphs


def synthetic_record(graph, index, top_k):
    """Return an invented bridge-shaped record derived from graph values."""
    base = float(np.abs(graph[parity.VISIT].x).sum()) / 100.0
    logit = math.sin(base) * 3.0
    drivers = {}
    for number, domain in enumerate(DOMAINS):
        entries = [{"vocab_index": k,
                    "concept_id": None if k == 2 else 990000000 + k,
                    "name": "" if k == 2 else "SYNTHETIC %s %d" % (
                        domain.upper(), k),
                    "score": abs(math.cos(base + k + number))}
                   for k in range(min(3, top_k))]
        drivers[domain] = sorted(entries, key=lambda item: -item["score"])
    return {"patient_index": index,
            "predicted_diagnosis": {
                "label_index": int(base) % 7,
                "concept_id": 990000100 + int(base) % 7,
                "name": "SYNTHETIC LABEL", "logit": logit,
                "probability": 1.0 / (1.0 + math.exp(-logit))},
            "drivers": drivers}


class SyntheticBridge:
    """Invented bridge recording its calls (no upstream code)."""

    def __init__(self, on_cuda=True):
        """Start with an empty call log."""
        self.calls = []
        self.on_cuda = on_cuda

    def preprocess(self, graphs):
        """Invented full-collection preprocessing (reference formula)."""
        self.calls.append("preprocess")
        return parity.synthetic_reference_preprocess(graphs)

    def load_model(self, device, vocabularies=None):
        """Return an invented model; records the requested device."""
        self.calls.append("load_model:%s" % device)
        return SyntheticModel(self.on_cuda)

    def explain(self, model, graph, patient_index, top_k, device):
        """Return an invented record for one directly indexed graph."""
        self.calls.append("explain")
        return synthetic_record(graph, patient_index, top_k)


def synthetic_plan():
    """Return an invented plan whose hashes are of invented bytes."""
    def fake(name):
        """Return an invented asset entry."""
        return {"path": "W:\\synthetic\\%s" % name,
                "sha256": hashlib.sha256(name.encode()).hexdigest()}
    return {"upstream_root": "W:\\synthetic\\upstream",
            "assets": {name: fake(name) for name in
                       SOURCE_ROLES + PARSED_ROLES + BYTE_ROLES
                       + STREAM_ROLES + HASHED_ROLES},
            "output_dir": "W:\\synthetic\\out",
            "protected_root": "W:\\synthetic"}


def synthetic_documents(plan, graphs, bridge=None):
    """Return invented Step 17, canonical and Step 19 documents."""
    bridge = bridge or SyntheticBridge()
    kept = [position for position, graph in enumerate(graphs)
            if parity.is_diagnosis_graph(graph)]
    records = []
    for index in (1, 3, 4, 6, 7, 8, 10, 11, 12, 13):
        position = kept[index]
        records.append({
            "person_id": SYNTHETIC_PERSON_ID_BASE + position,
            "subject_id": graphs[position].subject_id,
            "graph_position": position, "patient_index": index,
            "visit_nodes": [{"node_position": 0,
                             "hadm_id": SYNTHETIC_VISIT_ID_BASE + position,
                             "visit_occurrence_id": (
                                 SYNTHETIC_VISIT_ID_BASE + position),
                             "link_status": "verified",
                             "repeated_visit_node": False}],
            "repeated_visit_occurrence_ids": [], "findings": [],
            "phase4_eligible": False, "identity_status": "verified",
            "source_row": len(records) + 1})
    processed = parity.synthetic_reference_preprocess(graphs)
    canonical = [normalise_record(synthetic_record(processed[index], index,
                                                   TOP_K))
                 for index in CANONICAL_PREFIX]
    assets = plan["assets"]
    return {
        "step17_mapping": {
            "schema": mapping.SCHEMA,
            "schema_version": mapping.SCHEMA_VERSION,
            "scope": {"source_pilot_version": mapping.PILOT_LABEL},
            "inputs": {"graph": {"sha256": assets["graph"]["sha256"]},
                       "bridge": {"sha256":
                                  assets["explain_patient"]["sha256"]}},
            "phase4_eligible": False,
            "records": list(reversed(records))},
        "canonical_explanations": canonical,
        "step19_summary": {
            "status": "passed", "phase4_eligible": False,
            "graph_sha256": assets["graph"]["sha256"],
            "source_sha256": {
                "explain_patient": assets["explain_patient"]["sha256"],
                "model": assets["model"]["sha256"]},
            "counts": {"graphs_total": len(graphs),
                       "graphs_filtered": len(kept)},
            "model_input_digest": parity.digest_inputs(
                parity.collection_inputs(processed))},
    }


def synthetic_runtime(bridge=None, torch=None, graphs=None):
    """Return an invented runtime: torch stand-in, loader and bridge."""
    bridge = bridge or SyntheticBridge()
    torch = torch or synthetic_torch()
    loads = []

    def loader(source):
        """Record a load and return the invented raw collection."""
        loads.append(getattr(source, "name", source))
        return graphs if graphs is not None else synthetic_collection()
    return types.SimpleNamespace(import_torch=lambda: torch,
                                 graph_loader=loader,
                                 bridge_factory=lambda plan, verified=None:
                                 bridge,
                                 bridge=bridge, loads=loads)


def run_synthetic():
    """Run selection, generation and comparison on invented fixtures.

    Nothing protected is read and nothing is written.
    """
    plan = synthetic_plan()
    runtime = synthetic_runtime()
    documents = synthetic_documents(plan, synthetic_collection())
    outputs, report, runtime_info = run_isolated(plan, runtime, documents,
                                                 held=[])
    build_manifest(plan, runtime_info, report)
    return {"status": "passed", "report": report,
            "pilot_records": len(outputs)}


def report_lines(summary_or_report):
    """Return sanitised console lines: status, counts and mismatch codes."""
    report = summary_or_report.get("report", summary_or_report)
    lines = ["compared_records: %d" % report["compared_records"],
             "compared_values: %d" % report["compared_values"]]
    for code, count in sorted(report["mismatch_counts"].items()):
        lines.append("mismatch %s: %d" % (code, count))
    return lines


def main(argv=None):
    """Run synthetic mode by default or the explicit operator-real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="operator only: prepare the protected outputs")
    args = parser.parse_args(argv)
    try:
        summary = run_real() if args.operator_real else run_synthetic()
    except AgreementError as error:
        print("status: failed (%s)" % error)
        for line in report_lines(error.report):
            print(line)
        return 1
    except (Step20Error, mapping.MappingError) as error:
        print("status: failed (%s)" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - message may carry values
        print("status: failed (%s, message withheld)" % type(error).__name__)
        return 1
    print("status: %s" % summary["status"])
    print("pilot_records: %d" % summary["pilot_records"])
    for line in report_lines(summary):
        print(line)
    if args.operator_real:
        for name, digest in summary["digests"].items():
            print("%s sha256: %s" % (name, digest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
