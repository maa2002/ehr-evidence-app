#!/usr/bin/env python3
"""Protected model-output provenance sidecar (Step 21).

Links each of the ten Step 20 bridge outputs to its verified Step 17
patient/visit mapping record, the Step 18 target/history rule, the Step 19
preprocessing identity and the exact graph, checkpoint, vocabulary and
source assets recorded by the Step 20 manifest. Everything written is
separate derived metadata: inherited predicted_diagnosis and drivers values
are never copied, changed or regenerated, and no Step 20 or canonical file
is written.

Every pinned input is read once as bounded bytes whose path, size and
SHA-256 must match before anything is parsed; the inputs are re-hashed
before publication. Each Step 20 record must match exactly one verified
Step 17 record by unique patient_index, with consistent graph position,
unique person/subject/visit identities and contiguous verified visit
nodes. Any missing, duplicate, conflicting or unresolved critical link
rejects the whole publication; no case is dropped or renumbered.

A deterministic preparation fingerprint (SHA-256 over canonical JSON of
the whole sidecar body, no timestamps) changes whenever an input, the
selection, the policy or a setting changes.

The default mode runs invented fixtures only and reads or writes nothing
protected. The operator-only mode (--operator-real) is gated closed
(REAL_MODE_ENABLED is False) until the operator opens it after review.
No graph, checkpoint or model is loaded and no network or tracker is used.
Console output carries status, counts, finding codes and digests only.

Usage:
    python -m ehr_evidence_app.gnn.provenance
    python -m ehr_evidence_app.gnn.provenance --operator-real
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys

from ehr_evidence_app.gnn import mapping as mapping
from ehr_evidence_app.storage import policy as guard
from ehr_evidence_app.gnn import outputs as pmo

VERSION = "21.0"
SCHEMA = "step21-model-provenance"
SCHEMA_VERSION = "1.0"
OUTPUT_VERSION = "step-21-pilot10-v1"
OUTPUT_NAME = "provenance.json"
PARTIAL = ".partial"
PROTECTED_ROOT = pmo.PROTECTED_ROOT
OUTPUT_DIR = PROTECTED_ROOT + "\\model-provenance\\" + OUTPUT_VERSION
REPO_ROOT = mapping.REPO_ROOT
CONFIG_PATH = os.path.join(REPO_ROOT, "config", "model_provenance.json")
PILOT_RECORDS = pmo.PILOT_RECORDS
VERSION_PREFIX = "step21-prep-"
VERSION_HEX_CHARS = 16
# Opened only by the operator after review.
REAL_MODE_ENABLED = False

PARSED_ROLES = ("step17_mapping", "step19_summary", "step20_outputs",
                "step20_agreement", "step20_manifest")
HASHED_ROLES = ("step17_rules_doc", "step18_policy", "step13_registration",
                "step20_wrapper_current")
INPUT_ROLES = PARSED_ROLES + HASHED_ROLES
STEP20_FILES = {"step20_outputs": pmo.OUTPUTS_NAME,
                "step20_agreement": pmo.AGREEMENT_NAME}
MODEL_VERSION_ROLES = ("graph", "checkpoint", "concept_vocab", "label_vocab",
                       "explain_patient", "model")

# Source hashes stated by the pinned Step 18 policy document.
POLICY_SOURCES = {
    "build_patient_graphs": "175aaf7335b72c41ecc262d88203e800"
                            "0323fe60451c92e419effd47111d1f70",
    "train": "eb412e5beff75371d30de509ad7d8605"
             "f727bf9e25815d15d07cdaebebdea6df",
    "explain_patient": "1799f536c69d2312a4277f867f7836d7"
                       "cd56ddc54a448f24924597199a3b87d9",
    "model": "558e1b83ccf7667896a8736b4916e2cd"
             "230a9878d83bb9dc7f806a7acf75cfc6",
}
# The Step 20 run used these wrapper bytes; they were not archived.
EXECUTED_WRAPPER_SHA256 = ("fe37ac0a3fa81e4abeff4e3542e05de5"
                           "109085f75efede0fecdd53c44323c9ab")

REQUIRED_FLAGS = {
    "phase4_eligible": False,
    "history_only_claim_allowed": False,
    "source_event_linkage": "absent",
    "upstream_split_membership": "unknown",
    "unseen_patient_status": "not_inferred",
    "label_index_correspondence": "not_established",
    "pre_target_only_input": False,
    "history_attribution_assigned": False,
    "inherited_values_copied": False,
    "step20_outputs_modified": False,
}
TARGET_VISIT_INPUTS = ["final-visit visit.x features",
                       "final-visit procedure edges",
                       "final-visit drug edges"]

LIMITATIONS = [
    "Development pilot of the ten verified Step 17 pilot-10-v1 records "
    "only; not an evaluation cohort.",
    "Derived metadata only: inherited prediction and driver values are "
    "referenced by record digest and never copied or changed.",
    "The label_index-to-concept correspondence is not established.",
    "Model inputs still include the final (target) visit's visit.x features "
    "and procedure and drug edges; no pre-target-only input is claimed.",
    "No source event linkage exists. Concept attributions are not assigned "
    "to history visits and no history-only explanation is claimed.",
    "Upstream split membership and unseen-patient status are unknown and "
    "are not inferred.",
    "The Step 20 wrapper bytes that were executed were not archived and "
    "are not retrievable; the current wrapper source has a different hash "
    "after a post-run synthetic-fixture identifier refactor, and the "
    "current path does not resolve to the executed hash.",
]


class Step21Error(Exception):
    """A fail-closed Step 21 failure carrying a finding code only."""


# Plan and Configuration

def step20_inventory(assets):
    """Return the asset inventory exactly as the Step 20 manifest holds it."""
    inventory = {}
    for name, entry in sorted(assets.items()):
        inventory[name] = {"path": entry["path"],
                           "sha256": entry["sha256"].lower()}
        if "size_bytes" in entry:
            inventory[name]["size_bytes"] = entry["size_bytes"]
    return inventory


def real_plan():
    """Return the pinned inputs, expected Step 20 assets and fixed output."""
    step20 = pmo.OUTPUT_DIR + "\\"
    return {
        "inputs": {
            "step17_mapping": {
                "path": mapping.OUTPUT_DIR + "\\" + mapping.OUTPUT_NAME,
                "size_bytes": 17782,
                "sha256": "d12ddb031dc6f8f5e8c271af70a90d71"
                          "61073d2ef48dfc1de2cc3ac31dada18d"},
            "step17_rules_doc": {
                "path": os.path.join(mapping.V1_SOURCE_ARCHIVE, "docs",
                                     "PATIENT_VISIT_MAPPING.md"),
                "sha256": "c4835b72d744e007c7b56f59ba595528"
                          "bd990cfabeffc2fb175e0fa0d43a9487"},
            "step18_policy": {
                "path": os.path.join(mapping.V1_SOURCE_ARCHIVE, "docs",
                                     "TARGET_HISTORY_POLICY.md"),
                "sha256": "795482be9fe415ba5daf5f0132194fb8"
                          "01c7bb59530d0224f39473432166db9c"},
            "step19_summary": {
                "path": PROTECTED_ROOT + "\\verification\\"
                        "step-19-preprocessing-parity-final\\summary.json",
                "sha256": "4472843c61b5196cb3d999d71eec2ef1"
                          "5fbe75aee7f9a27b03712ba42f470b47"},
            "step20_outputs": {
                "path": step20 + pmo.OUTPUTS_NAME,
                "sha256": "3bb25c85e1a2e1d3b289d7aa54fb6a01"
                          "5a131195608f7e6d09cb1b15f2fecc8d"},
            "step20_agreement": {
                "path": step20 + pmo.AGREEMENT_NAME,
                "sha256": "5c8d0cb7dc71c21c0b5313ca5f03c505"
                          "7130e31a8b27489fc4135d07785c2c8c"},
            "step20_manifest": {
                "path": step20 + pmo.MANIFEST_NAME,
                "sha256": "bb774842654c9407a622b74fd9695644"
                          "44e71fee325a54e17adb0f479a4962ea"},
            "step20_wrapper_current": {
                "path": os.path.join(mapping.V1_SOURCE_ARCHIVE, "scripts",
                                     "prepare_model_outputs.py"),
                "sha256": "d0a3218c5f0e8ca08eb60684518d9a9d"
                          "656a01f0b781bb0d68be693caae1f077"},
            "step13_registration": {
                "path": guard.PRIVATE_ROOT + "\\outputs\\lineage\\"
                        "inherited-input-registration-v1.json",
                "sha256": "a9bb00b44a7416b6e412ea7ec82280e7"
                          "efff88317ea63fadb0afbf7be2d5ad1f"},
        },
        "step20_assets": step20_inventory(pmo.real_plan()["assets"]),
        "step20_output_version": pmo.OUTPUT_VERSION,
        "policy_sources": dict(POLICY_SOURCES),
        "executed_wrapper_sha256": EXECUTED_WRAPPER_SHA256,
        "config_path": CONFIG_PATH,
        "output_dir": OUTPUT_DIR,
        "protected_root": PROTECTED_ROOT,
    }


def pinned_hash_problem(plan):
    """Return a code for the first pin that is not 64 lowercase-able hex."""
    pins = {name: entry.get("sha256")
            for name, entry in plan["inputs"].items()}
    pins.update({"policy:" + name: value
                 for name, value in plan["policy_sources"].items()})
    pins["executed_wrapper"] = plan["executed_wrapper_sha256"]
    for name, digest in sorted(pins.items()):
        if not is_hex64(digest):
            return "pinned_hash_malformed:%s" % name
    if (set(plan["inputs"]) != set(INPUT_ROLES)
            or set(plan["policy_sources"]) != set(POLICY_SOURCES)):
        return "pinned_roles_unexpected"
    return None


def is_hex64(value):
    """Return True for a 64-character hexadecimal string."""
    return (isinstance(value, str) and len(value) == 64
            and all(char in "0123456789abcdef" for char in value.lower()))


def expected_config():
    """Return the configuration values this script version implements."""
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "step": "21",
        "output_version": OUTPUT_VERSION, "output_name": OUTPUT_NAME,
        "records": PILOT_RECORDS,
        "fingerprint": {
            "algorithm": "sha256",
            "canonical_json": {"sort_keys": True, "separators": [",", ":"],
                               "ensure_ascii": True, "allow_nan": False},
            "excluded_top_level_keys": ["preparation"],
            "version_prefix": VERSION_PREFIX,
            "version_hex_chars": VERSION_HEX_CHARS},
        "required_flags": dict(REQUIRED_FLAGS)}


def load_config(data):
    """Parse the versioned config bytes; fail unless it matches this code."""
    try:
        config = mapping.parse_json(data)
    except (UnicodeDecodeError, ValueError):
        raise Step21Error("config_not_json") from None
    if not isinstance(config, dict):
        raise Step21Error("config_mismatch")
    expected = expected_config()
    if {key: config.get(key) for key in expected} != expected:
        raise Step21Error("config_mismatch")
    return {"path": os.path.relpath(CONFIG_PATH, REPO_ROOT),
            "sha256": hashlib.sha256(data).hexdigest(),
            "schema": SCHEMA, "schema_version": SCHEMA_VERSION}


def read_config_bytes(path):
    """Read the repository config (never protected data) as bytes."""
    try:
        with open(path, "rb") as handle:
            return handle.read(mapping.MAX_SMALL_BYTES)
    except OSError:
        raise Step21Error("config_unreadable") from None


# Canonical JSON and Fingerprint

def canonical_json(value):
    """Return the canonical UTF-8 JSON bytes used for every digest."""
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise Step21Error("value_not_canonical_json") from None
    return text.encode("utf-8")


def digest(value):
    """Return the SHA-256 hex digest of a value's canonical JSON."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def fingerprint(body):
    """Return (fingerprint, preparation version) of a sidecar body.

    The body excludes the preparation block itself and holds no time or
    machine-dependent value, so equal inputs and settings always give the
    same fingerprint and any change gives a different one.
    """
    value = digest(body)
    return value, VERSION_PREFIX + value[:VERSION_HEX_CHARS]


# Input Verification

def check_bytes(plan, raw):
    """Fail closed unless every pinned role's bytes match size and SHA-256."""
    if set(raw) != set(INPUT_ROLES):
        raise Step21Error("input_roles_unexpected")
    for name in INPUT_ROLES:
        entry = plan["inputs"][name]
        data = raw[name]
        if not isinstance(data, bytes):
            raise Step21Error("input_identity:input_not_bytes:%s" % name)
        if "size_bytes" in entry and len(data) != entry["size_bytes"]:
            raise Step21Error("input_identity:input_size_mismatch:%s" % name)
        if hashlib.sha256(data).hexdigest() != entry["sha256"].lower():
            raise Step21Error("input_identity:input_hash_mismatch:%s" % name)


def read_inputs(plan):
    """Read every pinned input once through the storage guards; return bytes.

    Returns (raw bytes by role, held signatures). Size and SHA-256 are
    checked on the exact bytes read before anything is parsed.
    """
    raw, held = {}, []
    for name in INPUT_ROLES:
        entry = plan["inputs"][name]
        try:
            data, signature = mapping.read_verified(
                entry["path"], name, "input_hash_mismatch:%s" % name,
                sha256=entry["sha256"], size=entry.get("size_bytes"))
        except mapping.MappingError as error:
            raise Step21Error("input_identity:%s" % error) from None
        raw[name] = data
        held.append((entry["path"], signature, name,
                     entry["sha256"].lower()))
    return raw, held


def confirm_inputs(held):
    """Re-check every held input's signature and SHA-256 (Step 20 rules)."""
    try:
        pmo.confirm_inputs(held, rehash=True)
    except pmo.Step20Error as error:
        raise Step21Error(str(error)) from None


def parse_documents(raw):
    """Parse the JSON roles from their already verified bytes."""
    documents = {}
    for name in PARSED_ROLES:
        try:
            documents[name] = mapping.parse_json(raw[name])
        except (UnicodeDecodeError, ValueError):
            raise Step21Error("input_not_json:%s" % name) from None
    return documents


# Step 20 Manifest, Agreement and Step 19 Checks

def check_manifest(manifest, plan, raw):
    """Verify the Step 20 manifest against its files, assets and flags.

    The manifest must describe exactly the pinned outputs and agreement
    bytes, the exact Step 20 pinned asset inventory (including the pinned
    Step 17 mapping and Step 19 summary) and the Step 20 limitations.
    """
    if not isinstance(manifest, dict):
        raise Step21Error("step20_manifest_unexpected")
    expected = {
        "schema": pmo.MANIFEST_SCHEMA, "schema_version": pmo.SCHEMA_VERSION,
        "step": "20", "script_version": pmo.VERSION,
        "output_version": plan["step20_output_version"],
        "status": "published", "canonical_agreement": "passed",
        "phase4_eligible": False,
        "label_index_correspondence": "not_established",
        "source_event_linkage": "absent",
        "history_only_claim_allowed": False,
        "upstream_split_membership": "unknown",
        "pre_target_only_input": False,
        "derived_evidence_attached": False,
        "canonical_artefacts_modified": False,
        "target_visit_inputs_remaining": TARGET_VISIT_INPUTS}
    for key, value in expected.items():
        if manifest.get(key) != value or type(manifest.get(key)) is not type(
                value):
            raise Step21Error("step20_manifest_field:%s" % key)
    files = manifest.get("files")
    if not isinstance(files, dict) or set(files) != set(
            STEP20_FILES.values()):
        raise Step21Error("step20_manifest_files_unexpected")
    for role, name in STEP20_FILES.items():
        entry = files[name]
        data = raw[role]
        if (not isinstance(entry, dict)
                or entry.get("sha256") != hashlib.sha256(data).hexdigest()
                or entry.get("size_bytes") != len(data)):
            raise Step21Error("step20_manifest_file_mismatch:%s" % name)
    if manifest.get("assets") != plan["step20_assets"]:
        raise Step21Error("step20_asset_inventory_mismatch")
    for role in ("step17_mapping", "step19_summary"):
        pinned = plan["inputs"][role]
        recorded = plan["step20_assets"].get(role) or {}
        if (recorded.get("path") != pinned["path"]
                or recorded.get("sha256") != pinned["sha256"].lower()):
            raise Step21Error("step20_upstream_reference_mismatch:%s" % role)
    counts = manifest.get("counts") or {}
    selection = manifest.get("selection") or {}
    if (counts.get("published_records") != PILOT_RECORDS
            or selection.get("records") != PILOT_RECORDS
            or selection.get("identity_status") != "verified"):
        raise Step21Error("step20_manifest_counts_unexpected")
    settings = manifest.get("settings")
    if (not isinstance(settings, dict)
            or settings.get("top_k") != pmo.TOP_K
            or settings.get("device") != pmo.DEVICE
            or settings.get("cpu_fallback") is not False
            or settings.get("tolerance") != {"atol": pmo.ATOL,
                                             "rtol": pmo.RTOL}
            or not isinstance(manifest.get("runtime"), dict)):
        raise Step21Error("step20_manifest_settings_unexpected")


def check_agreement(report):
    """Require a passed aggregate canonical agreement with no mismatch."""
    counts = report.get("mismatch_counts") if isinstance(report, dict) \
        else None
    if (not isinstance(counts, dict)
            or report.get("schema") != pmo.AGREEMENT_SCHEMA
            or report.get("status") != "passed"
            or report.get("compared_records") != len(pmo.CANONICAL_PREFIX)
            or set(counts) != set(pmo.MISMATCH_CODES)
            or any(value != 0 for value in counts.values())):
        raise Step21Error("step20_agreement_unexpected")
    return {"status": "passed",
            "compared_records": report["compared_records"],
            "compared_values": report.get("compared_values")}


def check_step19(summary, plan):
    """Return the Step 19 digest and counts, checked against Step 20 assets."""
    try:
        return pmo.check_step19(summary, {"assets": plan["step20_assets"]})
    except pmo.Step20Error as error:
        raise Step21Error(str(error)) from None


def check_policy_sources(plan, mapping_document):
    """Cross-check the Step 18 policy source hashes with recorded inputs."""
    sources = plan["policy_sources"]
    assets = plan["step20_assets"]
    inputs = mapping_document.get("inputs") or {}
    builder = (inputs.get("builder") or {}).get("sha256")
    if (assets["explain_patient"]["sha256"] != sources["explain_patient"]
            or assets["model"]["sha256"] != sources["model"]
            or not isinstance(builder, str)
            or builder.lower() != sources["build_patient_graphs"]):
        raise Step21Error("policy_source_mismatch")


# Step 17 Records and the Step 20 Output Bijection

def check_mapping_document(document, plan):
    """Validate the Step 17 mapping as Step 20 selected it; return records.

    Uses the Step 20 selection rules (schema, pilot label, pinned graph and
    bridge hashes, ten verified records without findings or repeats, unique
    patient_index and graph_position) and returns the full records.
    """
    try:
        pmo.select_pilot(document, {"assets": plan["step20_assets"]})
    except pmo.Step20Error as error:
        raise Step21Error(str(error)) from None
    if document.get("step") != "17":
        raise Step21Error("mapping_schema_unexpected")
    return document["records"]


def check_visit_nodes(record):
    """Return a code when visit nodes are not contiguous unique verified links.

    Node positions must be exactly 0..N-1 in order, each hadm_id must equal
    its verified visit_occurrence_id and no source visit may appear twice;
    a target visit also mapped at an earlier position is a target/history
    identity conflict.
    """
    nodes = record["visit_nodes"]
    seen = set()
    for position, node in enumerate(nodes):
        if node.get("node_position") != position or not pmo.strict_int(
                node.get("node_position")):
            return "visit_node_position_invalid"
        visit = node.get("visit_occurrence_id")
        if not pmo.strict_int(visit) or node.get("hadm_id") != visit \
                or not pmo.strict_int(node.get("hadm_id")):
            return "visit_node_identity_invalid"
        if visit in seen:
            if position == len(nodes) - 1:
                return "target_history_conflict"
            return "visit_node_repeated"
        seen.add(visit)
    return None


def check_identities(records, step19):
    """Fail closed on cross-person, repeated or inconsistent identities.

    person_id, subject_id and every visit_occurrence_id must belong to one
    record only. Ordered by patient_index, graph_position must increase and
    the number of filtered-out graphs before it (graph_position minus
    patient_index) must never decrease; both stay within Step 19 counts.
    """
    persons, subjects, visits = set(), set(), set()
    for record in records:
        if not pmo.strict_int(record.get("person_id")):
            raise Step21Error("mapping_person_invalid")
        if record["person_id"] in persons:
            raise Step21Error("cross_person_identity")
        if record["subject_id"] in subjects:
            raise Step21Error("cross_person_identity")
        persons.add(record["person_id"])
        subjects.add(record["subject_id"])
        problem = check_visit_nodes(record)
        if problem:
            raise Step21Error(problem)
        for node in record["visit_nodes"]:
            if node["visit_occurrence_id"] in visits:
                raise Step21Error("cross_person_visit_link")
            visits.add(node["visit_occurrence_id"])
    ordered = sorted(records, key=lambda item: item["patient_index"])
    previous = None
    for record in ordered:
        index, position = record["patient_index"], record["graph_position"]
        if (index >= step19["graphs_filtered"]
                or position >= step19["graphs_total"]):
            raise Step21Error("graph_link_out_of_range")
        if previous is not None and (
                position <= previous[1]
                or position - index < previous[1] - previous[0]):
            raise Step21Error("graph_link_inconsistent")
        previous = (index, position)


def check_outputs(outputs, records):
    """Return [(output, record)] for an exact output-to-mapping bijection.

    outputs.json must hold exactly ten bridge-schema records in strictly
    ascending patient_index order, each matching exactly one Step 17 record
    and every Step 17 record matched once.
    """
    if not isinstance(outputs, list) or len(outputs) != PILOT_RECORDS:
        raise Step21Error("output_record_count")
    for output in outputs:
        if pmo.schema_problem(output):
            raise Step21Error("output_schema_invalid")
    indices = [output["patient_index"] for output in outputs]
    if len(set(indices)) != len(indices):
        raise Step21Error("output_patient_index_repeated")
    if indices != sorted(indices):
        raise Step21Error("output_order_unexpected")
    by_index = {}
    for record in records:
        by_index.setdefault(record["patient_index"], []).append(record)
    pairs = []
    for output in outputs:
        matches = by_index.get(output["patient_index"], [])
        if len(matches) != 1:
            raise Step21Error("output_without_unique_mapping")
        pairs.append((output, matches[0]))
    if set(indices) != set(by_index):
        raise Step21Error("mapping_without_output")
    return pairs


# Derived Records and Sidecar

def mapped_visit(node):
    """Return one position's verified mapped source visit."""
    return {"position": node["node_position"],
            "visit_occurrence_id": node["visit_occurrence_id"],
            "hadm_id": node["hadm_id"], "link_status": node["link_status"]}


def derive_record(output, record, mapping_ref):
    """Return the derived provenance for one Step 20 output record.

    Target is position N-1 and pre-target history positions 0..N-2 in
    Step 17 order (explicitly empty when N=1). The Step 20 record is
    referenced only by its canonical digest; no prediction or driver value
    is copied.
    """
    nodes = record["visit_nodes"]
    count = len(nodes)
    history = [mapped_visit(node) for node in nodes[:-1]]
    return {
        "patient_index": output["patient_index"],
        "graph_position": record["graph_position"],
        "patient_identity": {"person_id": record["person_id"],
                             "subject_id": record["subject_id"],
                             "identity_status": record["identity_status"]},
        "visit_node_count": count,
        "target": dict(mapped_visit(nodes[-1]), rule="position N-1"),
        "pre_target_history": {"rule": "positions 0..N-2 in Step 17 order",
                               "count": len(history),
                               "empty": not history,
                               "visits": history},
        "step17_mapping": dict(mapping_ref),
        "step20_record": {"sha256": digest(output),
                          "digest_rule": "SHA-256 of canonical JSON"},
        "phase4_eligible": False,
        "history_only_claim_allowed": False,
        "history_attribution_assigned": False,
    }


def input_refs(plan, raw):
    """Return path, size and SHA-256 of every pinned input."""
    return {name: {"path": plan["inputs"][name]["path"],
                   "size_bytes": len(raw[name]),
                   "sha256": hashlib.sha256(raw[name]).hexdigest()}
            for name in INPUT_ROLES}


def build_body(plan, raw, documents, config_ref):
    """Verify every link and return the sidecar body (no preparation block).

    raw must already have passed check_bytes. Raises Step21Error on the
    first missing, duplicate, conflicting or unresolved critical identity
    or provenance link.
    """
    manifest = documents["step20_manifest"]
    check_manifest(manifest, plan, raw)
    agreement = check_agreement(documents["step20_agreement"])
    step19 = check_step19(documents["step19_summary"], plan)
    mapping_document = documents["step17_mapping"]
    records = check_mapping_document(mapping_document, plan)
    check_policy_sources(plan, mapping_document)
    check_identities(records, step19)
    pairs = check_outputs(documents["step20_outputs"], records)
    refs = input_refs(plan, raw)
    mapping_ref = {"schema": mapping_document["schema"],
                   "schema_version": mapping_document["schema_version"],
                   "script_version": mapping_document.get("script_version"),
                   "sha256": refs["step17_mapping"]["sha256"]}
    derived = [derive_record(output, record, mapping_ref)
               for output, record in pairs]
    assets = plan["step20_assets"]
    current = refs["step20_wrapper_current"]
    body = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "step": "21",
        "script_version": VERSION, "output_version": OUTPUT_VERSION,
        "status": "published", "config": dict(config_ref),
        "inputs": refs,
        "step13_registration": dict(refs["step13_registration"]),
        "step17_mapping": dict(
            mapping_ref, rules_doc=refs["step17_rules_doc"],
            records=len(records),
            scope=(mapping_document.get("scope") or {}).get(
                "source_pilot_version")),
        "step18_policy": {
            "document": refs["step18_policy"],
            "target_rule": ("final ordered graph visit at position N-1, "
                            "resolved through the Step 17 visit node"),
            "history_rule": ("positions 0..N-2 in Step 17 order; explicit "
                             "empty history when N=1"),
            "pinned_sources": dict(plan["policy_sources"])},
        "step19_preprocessing": {
            "summary_sha256": refs["step19_summary"]["sha256"],
            "model_input_digest": step19["digest"],
            "graphs_total": step19["graphs_total"],
            "graphs_filtered": step19["graphs_filtered"]},
        "step20": {
            "output_version": manifest["output_version"],
            "outputs_sha256": refs["step20_outputs"]["sha256"],
            "agreement_sha256": refs["step20_agreement"]["sha256"],
            "manifest_sha256": refs["step20_manifest"]["sha256"],
            "manifest_schema": manifest["schema"],
            "manifest_schema_version": manifest["schema_version"],
            "script_version": manifest["script_version"],
            "canonical_agreement": agreement,
            "settings": copy.deepcopy(manifest["settings"]),
            "runtime": copy.deepcopy(manifest["runtime"]),
            "selection": copy.deepcopy(manifest["selection"]),
            "asset_inventory": copy.deepcopy(assets),
            "wrapper": {
                "executed": {"sha256": plan["executed_wrapper_sha256"],
                             "bytes_available": False,
                             "retrievable": False},
                "current_source": dict(current),
                "current_source_resolves_to_executed_hash": (
                    current["sha256"] == plan["executed_wrapper_sha256"]),
                "limitation": LIMITATIONS[-1]}},
        "model_versions": {name: copy.deepcopy(assets[name])
                           for name in MODEL_VERSION_ROLES},
        "counts": {
            "records": len(derived),
            "visit_nodes": sum(item["visit_node_count"]
                               for item in derived),
            "empty_history_records": sum(
                1 for item in derived
                if item["pre_target_history"]["empty"])},
        "target_visit_inputs_remaining": list(TARGET_VISIT_INPUTS),
        "limitations": list(LIMITATIONS),
        "records": derived,
    }
    body.update(REQUIRED_FLAGS)
    return body


def build_sidecar(plan, raw, config_ref):
    """Return the complete sidecar with its preparation fingerprint."""
    check_bytes(plan, raw)
    body = build_body(plan, raw, parse_documents(raw), config_ref)
    value, version = fingerprint(body)
    sidecar = dict(body)
    sidecar["preparation"] = {
        "fingerprint_sha256": value, "version": version,
        "rule": ("SHA-256 of canonical JSON (sorted keys, compact "
                 "separators, ASCII, no NaN) of the sidecar without this "
                 "block; no wall-clock or machine-dependent value")}
    return sidecar


# Publication

def output_preconditions(plan):
    """Refuse an existing, unprotected or input-overlapping output."""
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        for entry in plan["inputs"].values():
            if (guard.is_within(entry["path"], path)
                    or guard.is_within(path, os.path.dirname(entry["path"]))):
                raise Step21Error("output_overlaps_input")
        if os.path.lexists(path):
            raise Step21Error("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise Step21Error(problem)


def encode(sidecar):
    """Return strict JSON bytes (sorted keys, no NaN) with a final newline."""
    try:
        text = json.dumps(sidecar, indent=2, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        raise Step21Error("sidecar_not_serialisable") from None
    return (text + "\n").encode("utf-8")


def publish(plan, sidecar, held):
    """Write the sidecar through a new .partial directory and rename it.

    Every input is re-hashed after the partial write and before the
    rename, so a changed input publishes nothing. If the write, the
    re-check or the final rename fails, only this run's own partial
    directory is removed and no final output exists. Returns the SHA-256.
    """
    output_preconditions(plan)
    data = encode(sidecar)
    partial = plan["output_dir"] + PARTIAL
    os.makedirs(os.path.dirname(plan["output_dir"]), exist_ok=True)
    os.mkdir(partial)
    try:
        with open(os.path.join(partial, OUTPUT_NAME), "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        confirm_inputs(held)
        try:
            os.rename(partial, plan["output_dir"])
        except OSError:
            raise Step21Error("output_rename_failed") from None
    except BaseException:
        remove_partial(partial)
        raise
    return hashlib.sha256(data).hexdigest()


def remove_partial(partial):
    """Delete this run's own partial directory after a failure."""
    target = os.path.join(partial, OUTPUT_NAME)
    if os.path.lexists(target):
        os.remove(target)
    if os.path.isdir(partial):
        os.rmdir(partial)


def attach(plan):
    """Verify every input, build the sidecar and publish it atomically."""
    problem = pinned_hash_problem(plan)
    if problem:
        raise Step21Error(problem)
    output_preconditions(plan)
    try:
        config_data, config_signature = mapping.read_verified(
            plan["config_path"], "config", "config_unreadable")
    except mapping.MappingError as error:
        raise Step21Error("input_identity:%s" % error) from None
    config_ref = load_config(config_data)
    raw, held = read_inputs(plan)
    held.append((plan["config_path"], config_signature, "config",
                 hashlib.sha256(config_data).hexdigest()))
    sidecar = build_sidecar(plan, raw, config_ref)
    confirm_inputs(held)
    sidecar_sha = publish(plan, sidecar, held)
    return summary(sidecar, sidecar_sha)


def summary(sidecar, sidecar_sha=None):
    """Return the sanitised console summary: counts, flags and digests."""
    result = {"status": sidecar["status"],
              "records": sidecar["counts"]["records"],
              "visit_nodes": sidecar["counts"]["visit_nodes"],
              "empty_history_records":
                  sidecar["counts"]["empty_history_records"],
              "phase4_eligible": sidecar["phase4_eligible"],
              "preparation_version": sidecar["preparation"]["version"],
              "fingerprint_sha256":
                  sidecar["preparation"]["fingerprint_sha256"]}
    if sidecar_sha:
        result["sidecar_sha256"] = sidecar_sha
    return result


def run_real(plan=None):
    """Operator-only real run; gated closed until the operator opens it."""
    if not REAL_MODE_ENABLED:
        raise Step21Error("real_mode_gated_closed")
    if guard.fixed_policy_problem():
        raise Step21Error("fixed_policy_rejected")
    return attach(plan or real_plan())


# Synthetic Default Mode (Invented Fixtures Only)

SYNTHETIC_PILOT = (1, 3, 4, 6, 7, 8, 10, 11, 12, 13)
SYNTHETIC_PERSON_BASE = 7700000
SYNTHETIC_SUBJECT_BASE = 9900000
SYNTHETIC_VISIT_BASE = 8800000


def synthetic_hash(name):
    """Return an invented SHA-256 for a named invented asset."""
    return hashlib.sha256(("synthetic:" + name).encode()).hexdigest()


def synthetic_step20_plan():
    """Return an invented Step 20 plan in the shape of pmo.real_plan()."""
    names = (pmo.SOURCE_ROLES + pmo.PARSED_ROLES + pmo.BYTE_ROLES
             + pmo.STREAM_ROLES + pmo.HASHED_ROLES)
    assets = {name: {"path": "W:\\synthetic\\step20\\%s" % name,
                     "sha256": synthetic_hash(name)} for name in names}
    for name in ("graph", "checkpoint", "concept_vocab", "label_vocab"):
        assets[name]["size_bytes"] = 1000 + len(name)
    return {"assets": assets}


def synthetic_mapping_record(index, position, count):
    """Return one invented verified Step 17 record with count visit nodes."""
    nodes = []
    for node in range(count):
        visit = SYNTHETIC_VISIT_BASE + position * 10 + node
        nodes.append({"node_position": node, "hadm_id": visit,
                      "visit_occurrence_id": visit,
                      "link_status": mapping.STATUS_VERIFIED,
                      "repeated_visit_node": False})
    return {"person_id": SYNTHETIC_PERSON_BASE + position,
            "subject_id": SYNTHETIC_SUBJECT_BASE + position,
            "graph_position": position, "patient_index": index,
            "visit_nodes": nodes, "repeated_visit_occurrence_ids": [],
            "findings": [], "phase4_eligible": False,
            "identity_status": mapping.STATUS_VERIFIED,
            "source_row": 0}


def synthetic_output(index):
    """Return an invented Step 16/20 bridge-shaped record."""
    return {"patient_index": index,
            "predicted_diagnosis": {
                "label_index": index % 5, "concept_id": 990000100 + index,
                "name": "SYNTHETIC LABEL", "logit": 0.25 + index / 100.0,
                "probability": 0.5 + index / 1000.0},
            "drivers": {
                "condition": [{"vocab_index": 1, "concept_id": 990000201,
                               "name": "SYNTHETIC CONDITION",
                               "score": 0.125 + index / 1000.0}],
                "procedure": [{"vocab_index": 2, "concept_id": None,
                               "name": "", "score": 0.0625}],
                "drug": []}}


def synthetic_documents(step20_plan):
    """Return invented Step 17, 19 and 20 documents (not yet encoded)."""
    assets = step20_plan["assets"]
    records = []
    for number, index in enumerate(SYNTHETIC_PILOT):
        # One filtered-out graph before every third selected record.
        position = index + number // 3
        record = synthetic_mapping_record(index, position, 1 + number % 4)
        record["source_row"] = number + 1
        records.append(record)
    mapping_document = {
        "schema": mapping.SCHEMA, "schema_version": mapping.SCHEMA_VERSION,
        "step": "17", "script_version": mapping.VERSION,
        "scope": {"source_pilot_version": mapping.PILOT_LABEL},
        "inputs": {"graph": {"sha256": assets["graph"]["sha256"]},
                   "bridge": {"sha256": assets["explain_patient"]["sha256"]},
                   "builder": {"sha256": synthetic_hash("builder")}},
        "phase4_eligible": False, "records": list(reversed(records))}
    step19 = {"status": "passed", "phase4_eligible": False,
              "graph_sha256": assets["graph"]["sha256"],
              "source_sha256": {
                  "explain_patient": assets["explain_patient"]["sha256"],
                  "model": assets["model"]["sha256"]},
              "counts": {"graphs_total": 24, "graphs_filtered": 20},
              "model_input_digest": synthetic_hash("model-input")}
    outputs = [synthetic_output(index) for index in SYNTHETIC_PILOT]
    canonical = [synthetic_output(index) for index in pmo.CANONICAL_PREFIX]
    agreement = pmo.compare_canonical(canonical, copy.deepcopy(canonical))
    return {"step17_mapping": mapping_document, "step19_summary": step19,
            "step20_outputs": outputs, "step20_agreement": agreement}


def synthetic_bundle(documents=None, step20_plan=None, extra=None,
                     input_dir="W:\\synthetic\\inputs"):
    """Return (plan, raw bytes) for a consistent invented preparation.

    The Step 20 manifest is built by the Step 20 manifest builder over an
    invented asset plan whose Step 17 and Step 19 entries are the paths and
    hashes of the invented documents, exactly as the real run records
    them. input_dir only names where tests may write the invented bytes.
    """
    step20_plan = copy.deepcopy(step20_plan or synthetic_step20_plan())
    documents = copy.deepcopy(documents or synthetic_documents(step20_plan))
    extra = dict(extra or {})
    raw = {"step17_mapping": encode(documents["step17_mapping"]),
           "step19_summary": encode(documents["step19_summary"]),
           "step20_outputs": pmo.encode(documents["step20_outputs"],
                                        sort_keys=False),
           "step20_agreement": pmo.encode(documents["step20_agreement"],
                                          sort_keys=True),
           "step17_rules_doc": extra.get("step17_rules_doc",
                                         b"# synthetic mapping rules\n"),
           "step18_policy": extra.get("step18_policy",
                                      b"# synthetic target/history policy\n"),
           "step13_registration": extra.get("step13_registration",
                                            b"{\"synthetic\": true}\n"),
           "step20_wrapper_current": extra.get(
               "step20_wrapper_current", b"# synthetic current wrapper\n")}
    paths = {name: os.path.join(input_dir, name + ".bin")
             for name in INPUT_ROLES}
    for role in ("step17_mapping", "step19_summary"):
        step20_plan["assets"][role].update(
            path=paths[role], sha256=hashlib.sha256(raw[role]).hexdigest())
    manifest = pmo.build_manifest(step20_plan,
                                  {"torch": "synthetic", "cuda": "synthetic"},
                                  documents["step20_agreement"])
    manifest["files"] = {
        name: {"size_bytes": len(raw[role]),
               "sha256": hashlib.sha256(raw[role]).hexdigest()}
        for role, name in STEP20_FILES.items()}
    manifest.update(extra.get("manifest_overrides", {}))
    raw["step20_manifest"] = pmo.encode(manifest, sort_keys=True)
    inputs = {name: {"path": paths[name],
                     "sha256": hashlib.sha256(raw[name]).hexdigest()}
              for name in INPUT_ROLES}
    inputs["step17_mapping"]["size_bytes"] = len(raw["step17_mapping"])
    plan = {"inputs": inputs,
            "step20_assets": step20_inventory(step20_plan["assets"]),
            "step20_output_version": pmo.OUTPUT_VERSION,
            "policy_sources": {
                "build_patient_graphs": synthetic_hash("builder"),
                "train": synthetic_hash("train"),
                "explain_patient": step20_plan["assets"][
                    "explain_patient"]["sha256"],
                "model": step20_plan["assets"]["model"]["sha256"]},
            "executed_wrapper_sha256": synthetic_hash("executed-wrapper"),
            "config_path": CONFIG_PATH,
            "output_dir": "W:\\synthetic\\protected\\model-provenance\\out",
            "protected_root": "W:\\synthetic\\protected"}
    return plan, raw


def run_synthetic():
    """Build two invented preparations; nothing protected is read or written.

    The second changes one Step 20 setting, so its fingerprint and version
    must differ visibly from the first.
    """
    config_ref = load_config(read_config_bytes(CONFIG_PATH))
    plan, raw = synthetic_bundle()
    first = build_sidecar(plan, raw, config_ref)
    again = build_sidecar(plan, raw, config_ref)
    if first["preparation"] != again["preparation"]:
        raise Step21Error("fingerprint_not_deterministic")
    step20_plan = synthetic_step20_plan()
    changed_plan, changed_raw = synthetic_bundle(step20_plan=step20_plan)
    manifest = json.loads(changed_raw["step20_manifest"])
    manifest["settings"]["graph_indexing"] = "synthetic alternative"
    changed_raw["step20_manifest"] = pmo.encode(manifest, sort_keys=True)
    changed_plan["inputs"]["step20_manifest"]["sha256"] = hashlib.sha256(
        changed_raw["step20_manifest"]).hexdigest()
    second = build_sidecar(changed_plan, changed_raw, config_ref)
    if (second["preparation"]["version"]
            == first["preparation"]["version"]):
        raise Step21Error("fingerprint_not_distinct")
    result = summary(first)
    result["status"] = "passed"
    result["alternative_preparation_version"] = second["preparation"][
        "version"]
    return result


SUMMARY_KEYS = ("records", "visit_nodes", "empty_history_records",
                "phase4_eligible", "preparation_version",
                "alternative_preparation_version", "fingerprint_sha256",
                "sidecar_sha256")


def main(argv=None):
    """Run synthetic mode by default or the explicit operator-real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="operator only: publish the protected sidecar")
    args = parser.parse_args(argv)
    try:
        result = run_real() if args.operator_real else run_synthetic()
    except (Step21Error, mapping.MappingError, pmo.Step20Error) as error:
        print("status: failed (%s)" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - message may carry values
        print("status: failed (%s, message withheld)" % type(error).__name__)
        return 1
    print("status: %s" % result["status"])
    for key in SUMMARY_KEYS:
        if key in result:
            value = result[key]
            if isinstance(value, bool):
                value = str(value).lower()
            print("%s: %s" % (key, value))
    return 0


if __name__ == "__main__":
    sys.exit(main())
