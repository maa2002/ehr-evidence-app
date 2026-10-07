#!/usr/bin/env python3
"""Protected upstream split-membership sidecar (Step 22).

Recovers, for each Step 21 provenance record, which upstream partition
(train, validation or test) its filtered graph position fell into during
the canonical diagnosis OMOP training run, by replaying only the pinned
index-splitting behaviour over positions 0..n-1:

    rng = np.random.default_rng(seed); idx = rng.permutation(n)
    test = idx[:int(n * test_frac)]
    validation = idx[n_test:n_test + int(n * val_frac)]
    train = idx[n_test + n_val:]

n is the Step 19 y_diagnosis-filtered collection size (stored order, the
order Step 17 patient_index refers to). seed, val_frac and test_frac come
only from the explicit allowlist of saved arguments that the canonical
checkpoint (args) and the pinned metrics file (args) both record and agree
on. Parser defaults, documentation, published counts, numeric index
ranges and downstream partition fields are never used as evidence.

The saved graphs_path must be the exact absolute canonical path under the
pinned upstream root. A path string does not prove which bytes training
read, so exact verified membership also needs two run-attributed evidence
artifacts bound to the exact checkpoint and metrics hashes: a
random-generator identity and a graph-byte binding naming the pinned graph
SHA-256. Neither is pinned, so every case is unavailable with a null
partition. Missing evidence gives unavailable; conflicting evidence gives
unknown. The downstream Step 17/19/21 graph chain is reported separately
from this historical training-input identity.

Separately, expected configuration counts are computed from the agreed
saved fractions and the pinned Step 19 filtered count when that evidence
is consistent. They are expected counts for the pinned collection, not
historically verified partition counts, and never set a case partition.

Every case is kept, linked to its exact Step 21 record digest;
phase4_eligible stays false and upstream_training_exposure and
unseen_patient_status stay not_inferred.

Every pinned input is read once as bounded bytes whose path, size and
SHA-256 must match before anything is parsed, and is re-hashed before
publication. Integrity or unsafe-path failures publish nothing. The
upstream training module and model are never imported or run; operator
real mode loads the hash-verified checkpoint bytes only through
torch.load(weights_only=True, map_location="cpu") with no fallback.

The default mode runs invented fixtures only and reads or writes nothing
protected. The operator-only mode (--operator-real) is gated closed
(REAL_MODE_ENABLED is False) until the operator opens it after review.
Console output carries status, counts, finding codes and digests only.

Usage:
    python -m ehr_evidence_app.gnn.split
    python -m ehr_evidence_app.gnn.split --operator-real
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import io
import json
import math
import os
import sys

from ehr_evidence_app.gnn import provenance as amp
from ehr_evidence_app.gnn import mapping as mapping
from ehr_evidence_app.storage import policy as guard
from ehr_evidence_app.gnn import outputs as pmo

VERSION = "22.1"
SCHEMA = "step22-upstream-split-membership"
SCHEMA_VERSION = "1.1"
OUTPUT_VERSION = "step-22-pilot10-v2"
OUTPUT_NAME = "split-membership.json"
PARTIAL = ".partial"
PROTECTED_ROOT = pmo.PROTECTED_ROOT
OUTPUT_DIR = PROTECTED_ROOT + "\\model-provenance\\" + OUTPUT_VERSION
REPO_ROOT = mapping.REPO_ROOT
UPSTREAM_ROOT = mapping.UPSTREAM_ROOT
CONFIG_PATH = os.path.join(REPO_ROOT, "config",
                           "upstream_split_membership.json")
VERSION_PREFIX = "step22-prep-"
VERSION_HEX_CHARS = 16
# Opened only by the operator after review.
REAL_MODE_ENABLED = False

PARSED_ROLES = ("step21_provenance", "step17_mapping", "step19_summary")
EVIDENCE_ROLES = ("train_source", "checkpoint", "metrics")
REQUIRED_ROLES = PARSED_ROLES + EVIDENCE_ROLES
OPTIONAL_ROLES = ("rng_provenance", "run_graph_binding")
ALL_ROLES = REQUIRED_ROLES + OPTIONAL_ROLES

# Published Step 21 provenance sidecar (protected model-provenance record).
STEP21_SHA256 = ("132e27cde28150550b03c26fe0c932ec"
                 "824daf8fc08a207f4325d982aaa1b655")
# Canonical diag_kg metrics identity (config/inherited_baseline.json).
METRICS_SHA256 = ("c4382ca881bbc728dbc94236c262e660"
                  "01c0e97e7bcb6e1fcbe6a0d4166b7a9d")
METRICS_SIZE = 7752
TRAIN_SIZE = 30887
GRAPH_REFERENCE = "data\\processed\\patient_graphs.pt"
CANONICAL_GRAPHS_PATH = UPSTREAM_ROOT + "\\" + GRAPH_REFERENCE

VERIFIED = "verified"
UNKNOWN = "unknown"
UNAVAILABLE = "unavailable"
STATUSES = (VERIFIED, UNKNOWN, UNAVAILABLE)
# Informational codes rank as verified; any gap or conflict outranks them.
STATUS_RANK = {VERIFIED: 0, UNAVAILABLE: 1, UNKNOWN: 2}
PARTITIONS = ("train", "validation", "test")
# Expected configuration counts use their own success value so they are
# never read as historically verified partition counts.
COMPUTED = "computed"
COUNT_STATUSES = (COMPUTED, UNKNOWN, UNAVAILABLE)

# Explicit allowlist of saved arguments and their exact saved types.
REQUIRED_ARGS = collections.OrderedDict((
    ("task", str), ("data_source", str), ("no_kg", bool),
    ("graphs_path", str), ("seed", int), ("val_frac", float),
    ("test_frac", float)))
EXPECTED_RUN = {"task": "diagnosis", "data_source": "omop", "no_kg": False}
METRICS_IDENTITY_KEYS = ("task", "data_source", "no_kg")

RNG_SCHEMA = "canonical-run-rng-provenance"
RNG_ATTRIBUTION = "original_canonical_run_artifact"
BIT_GENERATOR = "PCG64"
GRAPH_BINDING_SCHEMA = "canonical-run-graph-binding"
GRAPH_BINDING_ATTRIBUTIONS = ("original_canonical_run_artifact",
                              "approved_immutable_run_manifest")
RUN_KEYS = ("checkpoint_sha256", "metrics_sha256")
# Key carried by invented evidence artifacts; refused unless the runtime
# is an explicitly synthetic one.
SYNTHETIC_KEY = "synthetic"

GRAPHS_PATH_RULE = {
    "accepted_form": "exact_absolute_canonical_path_under_pinned_upstream_"
                     "root",
    "reference": GRAPH_REFERENCE,
    "separators_normalised": True,
    "case_insensitive": True,
    "relative_path_status": "unavailable_without_run_recorded_working_"
                            "directory"}
# Characters and Windows names that make a path unsafe or aliased.
UNSAFE_PATH_CHARS = frozenset('<>"|?*')
RESERVED_PATH_NAMES = frozenset(
    ["con", "prn", "aux", "nul"] + ["com%d" % i for i in range(1, 10)]
    + ["lpt%d" % i for i in range(1, 10)])

# Step 17 join rule for patient_index (the y_diagnosis-filtered order).
FILTER_RULE = ("position in the pinned graph list after keeping graphs "
               "with y_diagnosis, in stored order")

# Exact pinned split function, including its logging statement; the
# comparison ignores whitespace, so line wrapping here is not significant.
SPLIT_SOURCE = """def split_graphs(graphs, val_frac, test_frac, seed):
    rng = np.random.default_rng(seed)
    n = len(graphs)
    idx = rng.permutation(n)
    n_test = int(n * test_frac)
    n_val = int(n * val_frac)
    test_idx = idx[:n_test]
    val_idx = idx[n_test:n_test + n_val]
    train_idx = idx[n_test + n_val:]
    train = [graphs[i] for i in train_idx]
    val = [graphs[i] for i in val_idx]
    test = [graphs[i] for i in test_idx]
    log.info("Split: train=%d  val=%d  test=%d", len(train), len(val),
             len(test))
    return train, val, test"""
# Pinned-source fragments that must be present (whitespace-insensitive).
SOURCE_SNIPPETS = collections.OrderedDict((
    ("filter", 'graphs = [g for g in graphs if hasattr(g, "y_diagnosis")]'),
    ("loader", "load_omop_diagnosis_data(args)"),
    ("split_call",
     "split_graphs(graphs, args.val_frac, args.test_frac, args.seed)"),
    ("split", SPLIT_SOURCE),
    ("checkpoint_args", '"args": vars(args)'),
    ("metrics_args", '"args": serializable_args')))

# Fields that could carry a downstream partition; never used as evidence.
DOWNSTREAM_PARTITION_KEYS = frozenset((
    "partition", "split", "split_membership", "upstream_split",
    "upstream_partition", "upstream_split_membership", "dataset_split",
    "is_train", "is_validation", "is_test"))

REQUIRED_FLAGS = {
    "phase4_eligible": False,
    "upstream_training_exposure": "not_inferred",
    "unseen_patient_status": "not_inferred",
    "downstream_partition_used": False,
    "defaults_substituted": False,
    "inherited_values_copied": False,
    "step21_modified": False,
}

LIMITATIONS = [
    "Development pilot of the Step 21 records only; not an evaluation "
    "cohort.",
    "An exact upstream partition assignment is not a claim about actual "
    "gradient exposure, general patient novelty or Phase 4 eligibility.",
    "Membership is replayed only from saved checkpoint and metrics "
    "arguments; parser defaults, documentation, published counts, index "
    "ranges and downstream partitions are never used.",
    "Verified membership requires a run-attributed random-generator "
    "identity; neither pinned writer records the NumPy or runtime version, "
    "so without a separate original run artifact every case is "
    "unavailable.",
    "The saved graphs_path is accepted only as the exact absolute "
    "canonical path under the pinned upstream root; a path string does "
    "not prove which graph bytes training read.",
    "Without an original run artifact or approved immutable-run manifest "
    "binding the checkpoint and metrics to the graph SHA-256, historical "
    "graph-byte binding is unavailable; the Step 17, 19 and 21 graph "
    "chain is a downstream check only, not a training-time digest.",
    "Expected configuration counts are computed from the saved fractions "
    "for the pinned Step 19 collection; they are not historically "
    "verified canonical-run partition counts and never set a case "
    "partition.",
]


class Step22Error(Exception):
    """A fail-closed Step 22 failure carrying a finding code only."""


# Plan and Configuration

def real_plan():
    """Return the pinned inputs, graph identity and fixed protected output."""
    step20 = pmo.real_plan()["assets"]
    step21 = amp.real_plan()["inputs"]
    runs = UPSTREAM_ROOT + "\\data\\runs\\diag_kg\\"
    return {
        "inputs": {
            "step21_provenance": {
                "path": amp.OUTPUT_DIR + "\\" + amp.OUTPUT_NAME,
                "sha256": STEP21_SHA256},
            "step17_mapping": dict(step21["step17_mapping"]),
            "step19_summary": dict(step21["step19_summary"]),
            "train_source": {
                "path": UPSTREAM_ROOT + "\\src\\gnn\\train.py",
                "size_bytes": TRAIN_SIZE,
                "sha256": amp.POLICY_SOURCES["train"]},
            "checkpoint": dict(step20["checkpoint"]),
            "metrics": {"path": runs + "results\\metrics.json",
                        "size_bytes": METRICS_SIZE,
                        "sha256": METRICS_SHA256},
            # No run-attributed RNG or graph-byte artifact is known; the
            # operator may pin one only after verifying it is an
            # original run artifact or approved immutable-run manifest.
            "rng_provenance": None,
            "run_graph_binding": None,
        },
        "graph": {"sha256": step20["graph"]["sha256"],
                  "reference": GRAPH_REFERENCE,
                  "canonical_path": CANONICAL_GRAPHS_PATH},
        "config_path": CONFIG_PATH,
        "output_dir": OUTPUT_DIR,
        "protected_root": PROTECTED_ROOT,
    }


def present_roles(plan):
    """Return the roles pinned by the plan, in fixed order."""
    return tuple(role for role in ALL_ROLES
                 if plan["inputs"].get(role) is not None)


def pinned_hash_problem(plan):
    """Return a code for unexpected roles or a malformed pinned hash."""
    inputs = plan["inputs"]
    if set(inputs) != set(ALL_ROLES):
        return "pinned_roles_unexpected"
    for role in REQUIRED_ROLES:
        if not isinstance(inputs[role], dict):
            return "pinned_input_missing:%s" % role
    pins = {role: inputs[role].get("sha256") for role in present_roles(plan)}
    pins["graph"] = plan["graph"].get("sha256")
    for name, value in sorted(pins.items()):
        if not amp.is_hex64(value):
            return "pinned_hash_malformed:%s" % name
    canonical = plan["graph"].get("canonical_path")
    reference = plan["graph"].get("reference")
    if (not isinstance(canonical, str) or not isinstance(reference, str)
            or unsafe_path(reference)
            or graph_path_form(canonical, canonical, reference)[0]
            != "canonical_absolute"
            or not canonical.casefold().endswith(
                "\\" + reference.casefold())):
        return "pinned_graph_path_malformed"
    return None


def expected_config():
    """Return the configuration values this script version implements."""
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "step": "22",
        "output_version": OUTPUT_VERSION, "output_name": OUTPUT_NAME,
        "statuses": list(STATUSES), "partitions": list(PARTITIONS),
        "count_statuses": list(COUNT_STATUSES),
        "required_saved_arguments": {name: kind.__name__ for name, kind
                                     in REQUIRED_ARGS.items()},
        "saved_argument_sources": ["checkpoint.args", "metrics.args"],
        "expected_run": dict(EXPECTED_RUN),
        "graphs_path_rule": dict(GRAPHS_PATH_RULE),
        "rng_provenance": {"schema": RNG_SCHEMA,
                           "attribution": RNG_ATTRIBUTION,
                           "bit_generator": BIT_GENERATOR},
        "run_graph_binding": {"schema": GRAPH_BINDING_SCHEMA,
                              "attributions":
                                  list(GRAPH_BINDING_ATTRIBUTIONS)},
        "checkpoint_loading": {"weights_only": True, "map_location": "cpu",
                               "fallback": None},
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
        raise Step22Error("config_not_json") from None
    if not isinstance(config, dict):
        raise Step22Error("config_mismatch")
    expected = expected_config()
    if {key: config.get(key) for key in expected} != expected:
        raise Step22Error("config_mismatch")
    return {"path": os.path.relpath(CONFIG_PATH, REPO_ROOT),
            "sha256": hashlib.sha256(data).hexdigest(),
            "schema": SCHEMA, "schema_version": SCHEMA_VERSION}


def read_config_bytes(path):
    """Read the repository config (never protected data) as bytes."""
    try:
        with open(path, "rb") as handle:
            return handle.read(mapping.MAX_SMALL_BYTES)
    except OSError:
        raise Step22Error("config_unreadable") from None


# Canonical JSON, Fingerprint and Findings

def canonical_json(value):
    """Return the canonical UTF-8 JSON bytes used for every digest."""
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise Step22Error("value_not_canonical_json") from None
    return text.encode("utf-8")


def digest(value):
    """Return the SHA-256 hex digest of a value's canonical JSON."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def fingerprint(body):
    """Return (fingerprint, preparation version) of a sidecar body."""
    value = digest(body)
    return value, VERSION_PREFIX + value[:VERSION_HEX_CHARS]


def note(findings, status, code):
    """Record a finding code, keeping the strongest status for repeats."""
    if STATUS_RANK[status] > STATUS_RANK.get(findings.get(code), -1):
        findings[code] = status


def overall(findings, base=VERIFIED):
    """Return unknown on a conflict, else unavailable on a gap, else base."""
    status = base
    for value in findings.values():
        if STATUS_RANK[value] > STATUS_RANK[status]:
            status = value
    return status


# Input Verification (Fail Closed)

def check_bytes(plan, raw):
    """Fail closed unless every pinned role's bytes match size and SHA-256."""
    roles = present_roles(plan)
    if set(raw) != set(roles):
        raise Step22Error("input_roles_unexpected")
    for name in roles:
        entry = plan["inputs"][name]
        data = raw[name]
        if not isinstance(data, bytes):
            raise Step22Error("input_identity:input_not_bytes:%s" % name)
        if "size_bytes" in entry and len(data) != entry["size_bytes"]:
            raise Step22Error("input_identity:input_size_mismatch:%s" % name)
        if hashlib.sha256(data).hexdigest() != entry["sha256"].lower():
            raise Step22Error("input_identity:input_hash_mismatch:%s" % name)


def read_inputs(plan):
    """Read every pinned input once through the storage guards; return bytes."""
    raw, held = {}, []
    for name in present_roles(plan):
        entry = plan["inputs"][name]
        try:
            data, signature = mapping.read_verified(
                entry["path"], name, "input_hash_mismatch:%s" % name,
                sha256=entry["sha256"], size=entry.get("size_bytes"))
        except mapping.MappingError as error:
            raise Step22Error("input_identity:%s" % error) from None
        raw[name] = data
        held.append((entry["path"], signature, name,
                     entry["sha256"].lower()))
    return raw, held


def confirm_inputs(held):
    """Re-check every held input's signature and SHA-256."""
    try:
        pmo.confirm_inputs(held, rehash=True)
    except pmo.Step20Error as error:
        raise Step22Error(str(error)) from None


def parse_required(raw, name):
    """Parse one fail-closed JSON role from its verified bytes."""
    try:
        return mapping.parse_json(raw[name])
    except (UnicodeDecodeError, ValueError):
        raise Step22Error("input_not_json:%s" % name) from None


def check_step21(document, plan):
    """Verify the immutable Step 21 sidecar; return its records.

    Schema, published status, required flags, preparation fingerprint and
    the Step 17/19 digests it is bound to must all match; any difference
    fails closed because the cases could not be bound to Step 21.
    """
    if not isinstance(document, dict):
        raise Step22Error("step21_unexpected")
    expected = {"schema": amp.SCHEMA, "schema_version": amp.SCHEMA_VERSION,
                "step": "21", "output_version": amp.OUTPUT_VERSION,
                "status": "published"}
    expected.update(amp.REQUIRED_FLAGS)
    for key, value in expected.items():
        if (document.get(key) != value
                or type(document.get(key)) is not type(value)):
            raise Step22Error("step21_field:%s" % key)
    preparation = document.get("preparation")
    body = {key: value for key, value in document.items()
            if key != "preparation"}
    try:
        value, version = amp.fingerprint(body)
    except amp.Step21Error:
        raise Step22Error("step21_fingerprint_mismatch") from None
    if (not isinstance(preparation, dict)
            or preparation.get("fingerprint_sha256") != value
            or preparation.get("version") != version):
        raise Step22Error("step21_fingerprint_mismatch")
    for role in ("step17_mapping", "step19_summary"):
        recorded = nested(document, "inputs", role, "sha256")
        if recorded != plan["inputs"][role]["sha256"].lower():
            raise Step22Error("step21_binding_mismatch:%s" % role)
    records = document.get("records")
    counts = document.get("counts")
    if (not isinstance(records, list) or not isinstance(counts, dict)
            or counts.get("records") != len(records)):
        raise Step22Error("step21_records_invalid")
    for record in records:
        if (isinstance(record, dict)
                and record.get("phase4_eligible") is not False):
            raise Step22Error("step21_phase4_flag")
    return records


def check_step17(document):
    """Return the Step 17 records; fail closed on a foreign document."""
    if (not isinstance(document, dict)
            or document.get("schema") != mapping.SCHEMA
            or document.get("step") != "17"
            or not isinstance(document.get("records"), list)):
        raise Step22Error("step17_unexpected")
    if document.get("phase4_eligible") is not False or any(
            isinstance(record, dict)
            and record.get("phase4_eligible") is not False
            for record in document["records"]):
        raise Step22Error("step17_phase4_flag")
    return document["records"]


def check_step19(document):
    """Return the Step 19 collection counts; fail closed when unusable."""
    counts = document.get("counts") if isinstance(document, dict) else None
    if (not isinstance(counts, dict)
            or not pmo.strict_int(counts.get("graphs_total"), 1)
            or not pmo.strict_int(counts.get("graphs_filtered"), 1)
            or counts["graphs_filtered"] > counts["graphs_total"]):
        raise Step22Error("step19_summary_unexpected")
    return {"graphs_total": counts["graphs_total"],
            "graphs_filtered": counts["graphs_filtered"]}


# Run-Level Evidence (Unknown on Conflict, Unavailable on Gaps)

def nested(document, *keys):
    """Return a nested value, or None when any level is absent."""
    value = document
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def same_hash(value, expected):
    """Return True when value is a string equal to expected, ignoring case."""
    return isinstance(value, str) and value.lower() == expected.lower()


def compact(text):
    """Return text with every whitespace character removed."""
    return "".join(text.split())


def merge(findings, local):
    """Copy one section's finding codes into a wider finding set."""
    for code, status in local.items():
        note(findings, status, code)


def check_graph_chain(plan, documents, findings):
    """Return the downstream graph-chain block; note any conflict.

    The pinned graph must be the one Step 17, 19 and 21 record. This shows
    only that the downstream records agree on one graph; it is not
    evidence of which bytes the canonical training run read.
    """
    local = {}
    graph = plan["graph"]["sha256"]
    for value in (nested(documents["step21_provenance"], "model_versions",
                         "graph", "sha256"),
                  documents["step19_summary"].get("graph_sha256"),
                  nested(documents["step17_mapping"], "inputs", "graph",
                         "sha256")):
        if not same_hash(value, graph):
            note(local, UNKNOWN, "graph_identity_conflict")
    merge(findings, local)
    return {"status": overall(local), "reason_codes": sorted(local),
            "graph_sha256": graph.lower(),
            "sources": ["step17_mapping", "step19_summary",
                        "step21_provenance"],
            "is_training_input_evidence": False}


def check_identities(plan, raw, documents, findings):
    """Cross-check checkpoint, source and filter identities.

    The pinned checkpoint and train source must be the ones Step 21
    records; the pinned source must contain the filter, loader, split and
    writer fragments; and the Step 17 patient_index rule and Step 19
    counts and digest must be the ones Step 21 used. Any difference is a
    conflict.
    """
    step21 = documents["step21_provenance"]
    step17 = documents["step17_mapping"]
    step19 = documents["step19_summary"]
    if not same_hash(nested(step21, "model_versions", "checkpoint",
                            "sha256"),
                     plan["inputs"]["checkpoint"]["sha256"]):
        note(findings, UNKNOWN, "checkpoint_identity_conflict")
    if not same_hash(nested(step21, "step18_policy", "pinned_sources",
                            "train"),
                     plan["inputs"]["train_source"]["sha256"]):
        note(findings, UNKNOWN, "source_identity_conflict")
    try:
        source = compact(raw["train_source"].decode("utf-8"))
    except UnicodeDecodeError:
        source = ""
    for name, snippet in SOURCE_SNIPPETS.items():
        if compact(snippet) not in source:
            note(findings, UNKNOWN, "source_%s_text_mismatch" % name)
    if nested(step17, "join_rules", "patient_index") != FILTER_RULE:
        note(findings, UNKNOWN, "filter_identity_conflict")
    recorded = step21.get("step19_preprocessing") or {}
    counts = step19.get("counts") or {}
    if (recorded.get("graphs_filtered") != counts.get("graphs_filtered")
            or recorded.get("graphs_total") != counts.get("graphs_total")
            or recorded.get("model_input_digest")
            != step19.get("model_input_digest")):
        note(findings, UNKNOWN, "filter_identity_conflict")
    if (step19.get("status") != "passed"
            or step19.get("phase4_eligible") is not False):
        note(findings, UNKNOWN, "step19_status_conflict")


def load_checkpoint(raw, runtime, findings):
    """Return the safely loaded checkpoint container, or None.

    Only runtime["checkpoint_loader"] is used (torch.load with
    weights_only=True on CPU in real mode). A missing loader or any load
    failure is unavailable; there is never an unsafe fallback.
    """
    loader = runtime.get("checkpoint_loader")
    if loader is None:
        note(findings, UNAVAILABLE, "checkpoint_loader_unavailable")
        return None
    try:
        return loader(raw["checkpoint"])
    except Exception:  # noqa: BLE001 - message may carry values
        note(findings, UNAVAILABLE, "checkpoint_safe_load_failed")
        return None


def parse_metrics(raw, findings):
    """Return the parsed metrics document, or None when not JSON."""
    try:
        return mapping.parse_json(raw["metrics"])
    except (UnicodeDecodeError, ValueError):
        note(findings, UNAVAILABLE, "metrics_not_json")
        return None


def saved_values(source, container, findings):
    """Return the allowlisted saved arguments of one source, or None.

    A missing or null value is unavailable and never replaced by a parser
    default; a value of the wrong saved type is a conflict.
    """
    args = container.get("args") if isinstance(container, dict) else None
    if not isinstance(args, dict):
        note(findings, UNAVAILABLE, "saved_args_unavailable:%s" % source)
        return None
    values = {}
    for key, kind in REQUIRED_ARGS.items():
        value = args.get(key)
        if value is None:
            note(findings, UNAVAILABLE,
                 "saved_arg_missing:%s:%s" % (source, key))
        elif type(value) is not kind:
            note(findings, UNKNOWN,
                 "saved_arg_type_invalid:%s:%s" % (source, key))
        else:
            values[key] = value
    return values


def check_metrics_identity(metrics, values, findings):
    """Require the metrics top-level run identity to match its saved args."""
    if not isinstance(metrics, dict):
        return
    for key in METRICS_IDENTITY_KEYS:
        value = metrics.get(key)
        if value is None:
            note(findings, UNAVAILABLE, "metrics_run_identity_missing:%s"
                 % key)
        elif values is not None and key in values and (
                type(value) is not type(values[key])
                or value != values[key]):
            note(findings, UNKNOWN, "metrics_run_identity_conflict:%s"
                 % key)


def agree(first, second, findings):
    """Return the saved values both sources hold equally; note conflicts."""
    if first is None or second is None:
        return {}
    agreed = {}
    for key in REQUIRED_ARGS:
        if key in first and key in second:
            if first[key] == second[key]:
                agreed[key] = first[key]
            else:
                note(findings, UNKNOWN, "saved_arg_conflict:%s" % key)
    return agreed


def unsafe_path(value):
    """Return True for a path form that is unsafe or a Windows alias.

    Rejects empty or control characters, wildcards, UNC and device
    namespaces, current-drive rooted and drive-relative forms, alternate
    data streams, empty, dot and traversal segments, trailing dots or
    spaces, 8.3 short-name markers and reserved device names.
    """
    text = value.replace("/", "\\")
    if not text or any(ord(char) < 32 or char in UNSAFE_PATH_CHARS
                       for char in text):
        return True
    if text.startswith("\\"):
        return True
    body = text
    if len(text) >= 2 and text[1] == ":":
        if (not (text[0].isascii() and text[0].isalpha())
                or text[2:3] != "\\"):
            return True
        body = text[3:]
    if ":" in body:
        return True
    for part in body.split("\\"):
        if (part in ("", ".", "..") or part[-1] in ". " or "~" in part
                or part.split(".")[0].rstrip(" ").casefold()
                in RESERVED_PATH_NAMES):
            return True
    return False


def graph_path_form(value, canonical, reference):
    """Return (form, status, code) of a saved graphs_path string.

    Only the exact absolute canonical path under the pinned upstream root
    is accepted, with separators normalised and compared
    case-insensitively as Windows does. The exact relative reference
    would need a run-recorded working directory, which is not pinned, so
    it is a gap. Unsafe forms and any other path, including a different
    root with the same suffix, are conflicts.
    """
    if not isinstance(value, str) or unsafe_path(value):
        return "unsafe", UNKNOWN, "graph_path_unsafe_form"
    text = value.replace("/", "\\")
    if text[1:3] == ":\\":
        if text.casefold() == canonical.casefold():
            return "canonical_absolute", VERIFIED, None
        return "noncanonical", UNKNOWN, "graph_path_noncanonical"
    if text.casefold() == reference.casefold():
        return ("canonical_relative", UNAVAILABLE,
                "graph_path_working_directory_unavailable")
    return "noncanonical", UNKNOWN, "graph_path_noncanonical"


def check_run_identity(agreed, plan, n, findings):
    """Require the agreed values to identify the canonical diagnosis run."""
    for key, value in EXPECTED_RUN.items():
        if key in agreed and agreed[key] != value:
            note(findings, UNKNOWN, "run_%s_mismatch" % key)
    if "seed" in agreed and agreed["seed"] < 0:
        note(findings, UNKNOWN, "saved_seed_invalid")
    fractions = [agreed[key] for key in ("val_frac", "test_frac")
                 if key in agreed]
    if any(not math.isfinite(value) or not 0.0 <= value <= 1.0
           for value in fractions):
        note(findings, UNKNOWN, "split_fraction_invalid")
    elif len(fractions) == 2 and (int(n * agreed["test_frac"])
                                  + int(n * agreed["val_frac"]) > n):
        note(findings, UNKNOWN, "split_fraction_invalid")


def check_saved_args(plan, raw, runtime, n, findings):
    """Return (agreed saved arguments, sidecar summary).

    The agreed mapping may be partial; callers check completeness.
    """
    container = load_checkpoint(raw, runtime, findings)
    checkpoint_values = saved_values("checkpoint", container, findings)
    del container
    metrics = parse_metrics(raw, findings)
    metrics_values = saved_values("metrics", metrics, findings)
    check_metrics_identity(metrics, metrics_values, findings)
    agreed = agree(checkpoint_values, metrics_values, findings)
    check_run_identity(agreed, plan, n, findings)
    complete = set(agreed) == set(REQUIRED_ARGS)
    summary = {
        "allowlist": list(REQUIRED_ARGS),
        "sources": ["checkpoint.args", "metrics.args"],
        "complete_and_agreeing": complete,
        "values": ({key: value for key, value in agreed.items()
                    if key != "graphs_path"} if complete else None),
        "defaults_substituted": False}
    return dict(agreed), summary


def check_graphs_path(agreed, core, plan, findings):
    """Return the saved graphs_path summary; note gaps and conflicts."""
    graph = plan["graph"]
    value = agreed.get("graphs_path")
    summary = {"canonical_path": graph["canonical_path"],
               "reference": graph["reference"],
               "rule": dict(GRAPHS_PATH_RULE),
               "sha256": None, "form": None}
    if value is None:
        # Already a saved-argument gap or conflict; mirror it here.
        status = UNAVAILABLE
        if any(kind == UNKNOWN and code.endswith(":graphs_path")
               for code, kind in core.items()):
            status = UNKNOWN
        note(findings, status, "saved_graphs_path_unusable")
        summary["status"] = status
        return summary
    form, status, code = graph_path_form(value, graph["canonical_path"],
                                         graph["reference"])
    if code:
        note(findings, status, code)
    summary.update(sha256=hashlib.sha256(value.encode("utf-8")).hexdigest(),
                   form=form, status=status)
    return summary


def parse_optional(raw, name):
    """Return a parsed optional evidence artifact, or None when not JSON."""
    try:
        return mapping.parse_json(raw[name])
    except (UnicodeDecodeError, ValueError):
        return None


def run_matches(run, plan):
    """Return True when an artifact names the pinned checkpoint and metrics."""
    return all(same_hash(run[key], plan["inputs"][role]["sha256"])
               for key, role in zip(RUN_KEYS, ("checkpoint", "metrics")))


def synthetic_refused(document, runtime):
    """Return True for an invented artifact outside a synthetic runtime."""
    return (SYNTHETIC_KEY in document
            and runtime.get("synthetic_evidence_allowed") is not True)


def check_graph_binding(plan, raw, runtime, findings):
    """Return the historical graph-byte binding summary; note problems.

    Only a pinned original run artifact or approved immutable-run manifest
    that names the exact checkpoint and metrics hashes and the pinned
    graph SHA-256 counts. The Step 17/19/21 graph values, a matching path,
    file times or the current environment are never a training-time
    digest. The code checks binding and completeness only; originality is
    confirmed by the operator before pinning.
    """
    summary = {"status": UNAVAILABLE, "recorded": None,
               "downstream_chain_used_as_training_digest": False}
    local = {}
    if plan["inputs"].get("run_graph_binding") is None:
        note(local, UNAVAILABLE, "run_graph_binding_absent")
    else:
        document = parse_optional(raw, "run_graph_binding")
        run = nested(document, "run")
        if (not isinstance(document, dict)
                or document.get("schema") != GRAPH_BINDING_SCHEMA
                or document.get("attribution")
                not in GRAPH_BINDING_ATTRIBUTIONS
                or not isinstance(run, dict)
                or not all(isinstance(run.get(key), str)
                           for key in RUN_KEYS)
                or not isinstance(document.get("graph_sha256"), str)):
            note(local, UNAVAILABLE, "run_graph_binding_incomplete")
        else:
            summary["recorded"] = {
                "attribution": document["attribution"],
                "graph_sha256": document["graph_sha256"].lower()}
            if not run_matches(run, plan):
                note(local, UNKNOWN, "run_graph_binding_run_conflict")
            if not same_hash(document["graph_sha256"],
                             plan["graph"]["sha256"]):
                note(local, UNKNOWN, "run_graph_binding_graph_conflict")
            if synthetic_refused(document, runtime):
                note(local, UNKNOWN, "run_graph_binding_synthetic")
    merge(findings, local)
    summary["status"] = overall(local)
    return summary


def check_rng(plan, raw, runtime, findings):
    """Return the RNG provenance summary; note gaps and conflicts.

    Only a pinned artifact that names the exact canonical checkpoint and
    metrics hashes counts as run-attributed evidence. The replay runtime
    must use the recorded generator and NumPy version; the currently
    installed version alone is never historical evidence.
    """
    replay = runtime.get("rng")
    summary = {"status": UNAVAILABLE, "recorded": None,
               "replay_numpy_version": (replay or {}).get("numpy_version"),
               "required_bit_generator": BIT_GENERATOR}
    local = {}
    if plan["inputs"].get("rng_provenance") is None:
        note(local, UNAVAILABLE, "rng_provenance_absent")
    else:
        document = parse_optional(raw, "rng_provenance")
        run = nested(document, "run")
        if (not isinstance(document, dict)
                or document.get("schema") != RNG_SCHEMA
                or document.get("attribution") != RNG_ATTRIBUTION
                or not isinstance(run, dict)
                or not all(isinstance(document.get(key), str) and
                           document.get(key)
                           for key in ("numpy_version", "bit_generator"))
                or not all(isinstance(run.get(key), str)
                           for key in RUN_KEYS)):
            note(local, UNAVAILABLE, "rng_provenance_incomplete")
        else:
            summary["recorded"] = {
                "numpy_version": document["numpy_version"],
                "bit_generator": document["bit_generator"]}
            if not run_matches(run, plan):
                note(local, UNKNOWN, "rng_provenance_run_conflict")
            if synthetic_refused(document, runtime):
                note(local, UNKNOWN, "rng_provenance_synthetic")
            if document["bit_generator"] != BIT_GENERATOR:
                note(local, UNKNOWN, "rng_generator_conflict")
            if replay is None:
                note(local, UNAVAILABLE, "rng_replay_runtime_unavailable")
            elif replay.get("bit_generator") != BIT_GENERATOR:
                note(local, UNKNOWN, "rng_generator_conflict")
            elif replay.get("numpy_version") != document["numpy_version"]:
                note(local, UNKNOWN, "rng_replay_runtime_mismatch")
    merge(findings, local)
    summary["status"] = overall(local)
    return summary


# Exact Split Replay Over Filtered Positions

def split_positions(n, val_frac, test_frac, seed, default_rng):
    """Replay the pinned split_graphs slicing over positions 0..n-1.

    Mirrors the pinned source statement by statement, with the filtered
    position standing in for each graph: test first, validation next,
    train as the remainder. Returns plain int lists.
    """
    rng = default_rng(seed)
    idx = rng.permutation(n)
    n_test = int(n * test_frac)
    n_val = int(n * val_frac)
    test_idx = idx[:n_test]
    val_idx = idx[n_test:n_test + n_val]
    train_idx = idx[n_test + n_val:]
    return {"order": [int(value) for value in idx],
            "train": [int(value) for value in train_idx],
            "validation": [int(value) for value in val_idx],
            "test": [int(value) for value in test_idx]}


def partition_problem(n, val_frac, test_frac, replay):
    """Return a code unless the replay is an exact floor-count partition."""
    order = replay["order"]
    if sorted(order) != list(range(n)):
        return "replay_not_permutation"
    n_test = int(n * test_frac)
    n_val = int(n * val_frac)
    sizes = {"test": n_test, "validation": n_val,
             "train": n - n_test - n_val}
    if any(len(replay[name]) != sizes[name] for name in PARTITIONS):
        return "replay_count_mismatch"
    sets = {name: set(replay[name]) for name in PARTITIONS}
    for number, first in enumerate(PARTITIONS):
        for second in PARTITIONS[number + 1:]:
            if sets[first] & sets[second]:
                return "replay_partition_overlap"
    if set().union(*sets.values()) != set(range(n)):
        return "replay_coverage_incomplete"
    if replay["test"] + replay["validation"] + replay["train"] != order:
        return "replay_slice_order_mismatch"
    return None


def reconstruct(n, args, default_rng, findings):
    """Replay the split twice; return it only when exact and deterministic."""
    try:
        first = split_positions(n, args["val_frac"], args["test_frac"],
                                args["seed"], default_rng)
        second = split_positions(n, args["val_frac"], args["test_frac"],
                                 args["seed"], default_rng)
    except Exception:  # noqa: BLE001 - message may carry values
        note(findings, UNKNOWN, "replay_failed")
        return None
    if first != second:
        note(findings, UNKNOWN, "replay_not_deterministic")
        return None
    problem = partition_problem(n, args["val_frac"], args["test_frac"],
                                first)
    if problem:
        note(findings, UNKNOWN, problem)
        return None
    return first


def reconstruction_record(n, replay):
    """Return the global counts and deterministic partition digests."""
    if replay is None:
        return None, None
    counts = {"total": n}
    counts.update({name: len(replay[name]) for name in PARTITIONS})
    digests = {"permutation_sha256": digest(replay["order"])}
    digests.update({name + "_sha256": digest(sorted(replay[name]))
                    for name in PARTITIONS})
    return counts, digests


def expected_counts(n, agreed, basis, collection):
    """Return the expected configuration-count block.

    basis holds the saved-argument, run-identity, source, filter, graph
    chain and training-input conflict findings; RNG and graph-byte gaps
    are not part of it. When basis is clean, the pinned floor rule is
    applied to the agreed saved fractions and the pinned Step 19 filtered
    count. These are expected counts for that collection, not
    historically verified canonical-run partition counts, and they never
    assign a case partition.
    """
    local = dict(basis)
    for key in ("val_frac", "test_frac"):
        if key not in agreed:
            note(local, UNAVAILABLE, "saved_fraction_unavailable")
    status = overall(local)
    counts = None
    if status == VERIFIED:
        n_test = int(n * agreed["test_frac"])
        n_val = int(n * agreed["val_frac"])
        counts = {"total": n, "test": n_test, "validation": n_val,
                  "train": n - n_test - n_val}
        status = COMPUTED
    return {
        "status": status,
        "reason_codes": sorted(local),
        "basis": ("saved val_frac and test_frac agreed by checkpoint.args "
                  "and metrics.args, with the pinned floor rule, applied "
                  "to the pinned Step 19 graphs_filtered count"),
        "collection": dict(collection),
        "counts": counts,
        "historically_verified": False,
        "assigns_case_partitions": False,
        "interpretation": ("expected counts for the pinned Step 19 "
                           "collection; not historically verified "
                           "canonical-run partition counts")}


# Cases

def strict_index(record):
    """Return a record's patient_index when it is a plain int, else None."""
    if isinstance(record, dict) and type(record.get("patient_index")) is int:
        return record["patient_index"]
    return None


def index_problem(record, n, index_counts):
    """Return (status, code) when a Step 21 record's index is unusable."""
    if not isinstance(record, dict):
        return UNKNOWN, "case_record_invalid"
    value = record.get("patient_index")
    if value is None:
        return UNAVAILABLE, "case_index_missing"
    if type(value) is not int:
        return UNKNOWN, "case_index_invalid"
    if value < 0:
        return UNKNOWN, "case_index_negative"
    if value >= n:
        return UNKNOWN, "case_index_out_of_range"
    if index_counts[value] > 1:
        return UNKNOWN, "case_index_duplicate"
    return None


def check_case_mapping(record, index, step17_by_index, findings):
    """Return the unique consistent Step 17 record, noting any problem."""
    matches = step17_by_index.get(index, [])
    if not matches:
        note(findings, UNAVAILABLE, "step17_mapping_missing")
        return None
    if len(matches) > 1:
        note(findings, UNKNOWN, "step17_mapping_duplicate")
        return None
    match = matches[0]
    if (match.get("identity_status") != mapping.STATUS_VERIFIED
            or match.get("findings") != []):
        note(findings, UNKNOWN, "step17_mapping_not_verified")
    identity = record.get("patient_identity")
    identity = identity if isinstance(identity, dict) else {}
    pairs = ((record.get("graph_position"), match.get("graph_position")),
             (identity.get("person_id"), match.get("person_id")),
             (identity.get("subject_id"), match.get("subject_id")))
    for mine, theirs in pairs:
        if (type(mine) is not int or type(theirs) is not int
                or mine != theirs):
            note(findings, UNKNOWN, "step17_mapping_conflict")
    return match


def downstream_fields(record):
    """Return True when a record carries any downstream partition field."""
    return isinstance(record, dict) and bool(
        DOWNSTREAM_PARTITION_KEYS & set(record))


def case_entry(position, record, context):
    """Return the one output entry for one Step 21 record.

    The case is linked by the exact Step 21 record digest and its own
    patient_index; the partition comes only from the verified replay and
    is null unless the case and the run are both verified.
    """
    findings = {}
    index = strict_index(record)
    problem = index_problem(record, context["n"], context["index_counts"])
    match = None
    if problem:
        note(findings, *problem)
    else:
        match = check_case_mapping(record, index, context["step17_by_index"],
                                   findings)
    if isinstance(record, dict) and nested(
            record, "step17_mapping", "sha256") != context["step17_sha256"]:
        note(findings, UNKNOWN, "step17_binding_conflict")
    if downstream_fields(record) or downstream_fields(match):
        note(findings, VERIFIED, "downstream_partition_field_ignored")
    index_verified = index is not None and overall(findings) == VERIFIED
    status = overall(findings, base=context["run_status"])
    partition = None
    if status == VERIFIED:
        partition = context["membership"].get(index)
    if status == VERIFIED and partition is None:
        note(findings, UNKNOWN, "partition_not_found")
        status = UNKNOWN
    return {
        "case_position": position,
        "step21_record_sha256": digest(record),
        "patient_index": index,
        "patient_index_verified": index_verified,
        "status": status,
        "partition": partition,
        "reason_codes": sorted(set(findings) | set(context["run_codes"])),
        "evidence": {
            "step21_provenance_sha256": context["step21_sha256"],
            "step17_mapping_sha256": context["step17_sha256"],
            "step17_record_sha256": (digest(match) if match is not None
                                     else None),
            "reconstruction": "split_reconstruction"},
        "upstream_training_exposure": "not_inferred",
        "unseen_patient_status": "not_inferred",
        "phase4_eligible": False,
    }


def build_cases(records, step17_records, context):
    """Return one entry per Step 21 record, in Step 21 order."""
    index_counts = collections.Counter(
        strict_index(record) for record in records
        if strict_index(record) is not None)
    by_index = {}
    for record in step17_records:
        if strict_index(record) is not None:
            by_index.setdefault(record["patient_index"], []).append(record)
    context = dict(context, index_counts=index_counts,
                   step17_by_index=by_index)
    return [case_entry(position, record, context)
            for position, record in enumerate(records)]


def case_counts(cases):
    """Return aggregate case counts by status and verified partition."""
    counts = {"cases": len(cases)}
    counts.update({status: sum(1 for case in cases
                               if case["status"] == status)
                   for status in STATUSES})
    counts["verified_by_partition"] = {
        name: sum(1 for case in cases if case["partition"] == name)
        for name in PARTITIONS}
    return counts


# Sidecar

def input_refs(plan, raw):
    """Return path, size and SHA-256 of every role (null when absent)."""
    refs = {}
    for name in ALL_ROLES:
        if plan["inputs"].get(name) is None:
            refs[name] = None
        else:
            refs[name] = {"path": plan["inputs"][name]["path"],
                          "size_bytes": len(raw[name]),
                          "sha256": hashlib.sha256(raw[name]).hexdigest()}
    return refs


def build_body(plan, raw, config_ref, runtime):
    """Verify inputs, classify the evidence and return the sidecar body."""
    documents = {name: parse_required(raw, name) for name in PARSED_ROLES}
    records = check_step21(documents["step21_provenance"], plan)
    step17_records = check_step17(documents["step17_mapping"])
    step19 = check_step19(documents["step19_summary"])
    n = step19["graphs_filtered"]
    refs = input_refs(plan, raw)
    # core: evidence shared by expected counts and membership.
    core = {}
    chain = check_graph_chain(plan, documents, core)
    check_identities(plan, raw, documents, core)
    agreed, saved = check_saved_args(plan, raw, runtime, n, core)
    # upstream: historical training-input identity (path and graph bytes).
    upstream = {}
    graphs_path = check_graphs_path(agreed, core, plan, upstream)
    graph_binding = check_graph_binding(plan, raw, runtime, upstream)
    # Expected counts need core evidence and no training-input conflict;
    # historical gaps (graph bytes, working directory, RNG) do not apply.
    basis = dict(core)
    merge(basis, {code: status for code, status in upstream.items()
                  if status == UNKNOWN})
    expected = expected_counts(n, agreed, basis, {
        "graphs_filtered": n,
        "step19_summary_sha256": refs["step19_summary"]["sha256"],
        "model_input_digest": documents["step19_summary"].get(
            "model_input_digest")})
    findings = dict(core)
    merge(findings, upstream)
    rng = check_rng(plan, raw, runtime, findings)
    replay = None
    if overall(findings) == VERIFIED:
        if set(agreed) != set(REQUIRED_ARGS):
            note(findings, UNAVAILABLE, "saved_args_incomplete")
        else:
            replay = reconstruct(n, agreed, runtime["rng"]["default_rng"],
                                 findings)
    run_status = overall(findings)
    if run_status != VERIFIED:
        replay = None
    membership = {}
    if replay is not None:
        for name in PARTITIONS:
            membership.update((value, name) for value in replay[name])
    step21 = documents["step21_provenance"]
    context = {"n": n, "membership": membership, "run_status": run_status,
               "run_codes": sorted(findings),
               "step17_sha256": refs["step17_mapping"]["sha256"],
               "step21_sha256": refs["step21_provenance"]["sha256"]}
    cases = build_cases(records, step17_records, context)
    counts, digests = reconstruction_record(n, replay)
    body = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "step": "22",
        "script_version": VERSION, "output_version": OUTPUT_VERSION,
        "status": "published", "config": dict(config_ref),
        "inputs": refs,
        "bindings": {
            "step21": {"sha256": refs["step21_provenance"]["sha256"],
                       "preparation": dict(step21["preparation"]),
                       "records": len(records)},
            "step17_mapping": {"sha256": refs["step17_mapping"]["sha256"]},
            "step19_summary": dict(
                step19, sha256=refs["step19_summary"]["sha256"],
                model_input_digest=documents["step19_summary"].get(
                    "model_input_digest")),
            "train_source": {"sha256": refs["train_source"]["sha256"]},
            "graph": {"sha256": plan["graph"]["sha256"].lower(),
                      "reference": plan["graph"]["reference"],
                      "canonical_path": plan["graph"]["canonical_path"]},
            "checkpoint": {"sha256": refs["checkpoint"]["sha256"],
                           "size_bytes": refs["checkpoint"]["size_bytes"]},
            "metrics": {"sha256": refs["metrics"]["sha256"],
                        "size_bytes": refs["metrics"]["size_bytes"]}},
        "source_rule": {
            "filter": ("keep graphs with hasattr(g, 'y_diagnosis') in "
                       "stored order; patient_index is the filtered "
                       "position"),
            "permutation": ("numpy.random.default_rng(seed)"
                            ".permutation(n) over filtered positions"),
            "slices": ("test = idx[:int(n * test_frac)]; validation = "
                       "next int(n * val_frac); train = remainder"),
            "upstream_code_executed": False},
        "downstream_graph_chain": chain,
        "upstream_training_input": {
            "status": overall(upstream),
            "reason_codes": sorted(upstream),
            "graphs_path": graphs_path,
            "graph_byte_binding": graph_binding},
        "expected_configuration_counts": expected,
        "split_reconstruction": {
            "status": run_status,
            "reason_codes": sorted(findings),
            "meaning": ("actual partition integrity of the replayed "
                        "canonical-run split; null unless run-attributed "
                        "evidence verifies it"),
            "saved_arguments": saved,
            "rng_provenance": rng,
            "collection_size": n,
            "counts": counts,
            "pairwise_disjoint": True if replay is not None else None,
            "full_coverage": True if replay is not None else None,
            "partition_digests": digests,
            "digest_rule": ("SHA-256 of canonical JSON of each partition's "
                            "ascending filtered positions; the permutation "
                            "digest keeps replay order")},
        "counts": case_counts(cases),
        "limitations": list(LIMITATIONS),
        "cases": cases,
    }
    body.update(REQUIRED_FLAGS)
    return body


def build_sidecar(plan, raw, config_ref, runtime):
    """Return the complete sidecar with its preparation fingerprint."""
    check_bytes(plan, raw)
    body = build_body(plan, raw, config_ref, runtime)
    value, version = fingerprint(body)
    sidecar = dict(body)
    sidecar["preparation"] = {
        "fingerprint_sha256": value, "version": version,
        "rule": ("SHA-256 of canonical JSON (sorted keys, compact "
                 "separators, ASCII, no NaN) of the sidecar without this "
                 "block; no wall-clock or host value; the replay NumPy "
                 "version is included as runtime evidence")}
    return sidecar


# Publication

def output_preconditions(plan):
    """Refuse an existing, unprotected or input-overlapping output."""
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        for name in present_roles(plan):
            entry = plan["inputs"][name]
            if (guard.is_within(entry["path"], path)
                    or guard.is_within(path, os.path.dirname(entry["path"]))):
                raise Step22Error("output_overlaps_input")
        if os.path.lexists(path):
            raise Step22Error("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise Step22Error(problem)


def encode(sidecar):
    """Return strict JSON bytes (sorted keys, no NaN) with a final newline."""
    try:
        text = json.dumps(sidecar, indent=2, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        raise Step22Error("sidecar_not_serialisable") from None
    return (text + "\n").encode("utf-8")


def publish(plan, sidecar, held):
    """Write the sidecar through a new .partial directory and rename it.

    Inputs are re-hashed after the partial write and before the rename.
    On any failure only this run's own partial directory is removed, so
    no final or partial path remains. Returns the sidecar SHA-256.
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
            raise Step22Error("output_rename_failed") from None
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


def attach(plan, runtime):
    """Verify every input, build the sidecar and publish it atomically."""
    problem = pinned_hash_problem(plan)
    if problem:
        raise Step22Error(problem)
    output_preconditions(plan)
    try:
        config_data, config_signature = mapping.read_verified(
            plan["config_path"], "config", "config_unreadable")
    except mapping.MappingError as error:
        raise Step22Error("input_identity:%s" % error) from None
    config_ref = load_config(config_data)
    raw, held = read_inputs(plan)
    held.append((plan["config_path"], config_signature, "config",
                 hashlib.sha256(config_data).hexdigest()))
    sidecar = build_sidecar(plan, raw, config_ref, runtime)
    confirm_inputs(held)
    sidecar_sha = publish(plan, sidecar, held)
    return summary(sidecar, sidecar_sha)


def summary(sidecar, sidecar_sha=None):
    """Return the sanitised console summary: status, counts and codes."""
    counts = sidecar["counts"]
    reconstruction = sidecar["split_reconstruction"]
    result = {"status": sidecar["status"], "cases": counts["cases"],
              "verified": counts[VERIFIED], "unknown": counts[UNKNOWN],
              "unavailable": counts[UNAVAILABLE],
              "reconstruction_status": reconstruction["status"],
              "expected_counts_status":
                  sidecar["expected_configuration_counts"]["status"],
              "upstream_training_input_status":
                  sidecar["upstream_training_input"]["status"],
              "downstream_graph_chain_status":
                  sidecar["downstream_graph_chain"]["status"],
              "reason_codes": ",".join(reconstruction["reason_codes"])
                              or "none",
              "phase4_eligible": sidecar["phase4_eligible"],
              "preparation_version": sidecar["preparation"]["version"],
              "fingerprint_sha256":
                  sidecar["preparation"]["fingerprint_sha256"]}
    if sidecar_sha:
        result["sidecar_sha256"] = sidecar_sha
    return result


def numpy_runtime():
    """Return the replay runtime of the running NumPy, or None."""
    try:
        import numpy
    except ImportError:
        return None
    return {"numpy_version": numpy.__version__,
            "bit_generator": type(numpy.random.default_rng(0)
                                  .bit_generator).__name__,
            "default_rng": numpy.random.default_rng}


def real_runtime():
    """Return the operator runtime: safe torch loader and NumPy replay."""
    try:
        import torch
    except ImportError:
        loader = None
    else:
        def loader(data):
            """Load verified checkpoint bytes with weights_only on CPU."""
            return torch.load(io.BytesIO(data), weights_only=True,
                              map_location="cpu")
    return {"checkpoint_loader": loader, "rng": numpy_runtime()}


def run_real(plan=None, runtime=None):
    """Operator-only real run; gated closed until the operator opens it."""
    if not REAL_MODE_ENABLED:
        raise Step22Error("real_mode_gated_closed")
    if guard.fixed_policy_problem():
        raise Step22Error("fixed_policy_rejected")
    return attach(plan or real_plan(), runtime or real_runtime())


# Synthetic Default Mode (Invented Fixtures Only)

SYNTHETIC_MARKER = "SYNTHETIC_STEP22_FIXTURE"
SYNTHETIC_SEED = 20260925
SYNTHETIC_UPSTREAM_ROOT = "W:\\synthetic\\upstream"
SYNTHETIC_GRAPHS_PATH = SYNTHETIC_UPSTREAM_ROOT + "\\" + GRAPH_REFERENCE
SYNTHETIC_ARGS = {"task": "diagnosis", "data_source": "omop",
                  "no_kg": False,
                  "graphs_path": SYNTHETIC_GRAPHS_PATH,
                  "seed": SYNTHETIC_SEED, "val_frac": 0.1,
                  "test_frac": 0.15, "epochs": 3,
                  "synthetic": SYNTHETIC_MARKER}


def synthetic_train_source():
    """Return invented train.py stand-in bytes with the pinned fragments."""
    lines = ["# " + SYNTHETIC_MARKER + ": invented stand-in, not upstream",
             "import numpy as np", "", SPLIT_SOURCE, "",
             "def synthetic_loader(graphs):",
             "    " + SOURCE_SNIPPETS["filter"],
             "    return graphs", "",
             "def synthetic_main(args):",
             "    graphs = " + SOURCE_SNIPPETS["loader"],
             "    splits = " + SOURCE_SNIPPETS["split_call"],
             "    ckpt = {" + SOURCE_SNIPPETS["checkpoint_args"] + "}",
             "    results = {" + SOURCE_SNIPPETS["metrics_args"] + "}",
             "    return splits, ckpt, results", ""]
    return "\n".join(lines).encode("utf-8")


def synthetic_evidence():
    """Return invented checkpoint and metrics documents that agree."""
    checkpoint = {"synthetic": SYNTHETIC_MARKER, "epoch": 1,
                  "args": dict(SYNTHETIC_ARGS)}
    metrics = {"synthetic": SYNTHETIC_MARKER, "task": "diagnosis",
               "data_source": "omop", "no_kg": False,
               "args": dict(SYNTHETIC_ARGS)}
    return checkpoint, metrics


def synthetic_checkpoint_loader(data):
    """Stand in for torch.load(weights_only=True) on invented JSON bytes."""
    return json.loads(data.decode("utf-8"))


def synthetic_runtime(rng="numpy"):
    """Return an invented runtime; rng is 'numpy', None or a dict.

    Only this runtime accepts invented evidence artifacts; the operator
    runtime refuses them.
    """
    return {"checkpoint_loader": synthetic_checkpoint_loader,
            "rng": numpy_runtime() if rng == "numpy" else rng,
            "synthetic_evidence_allowed": True}


def refingerprint(sidecar):
    """Recompute a (synthetic) Step 21 sidecar's preparation block."""
    body = {key: value for key, value in sidecar.items()
            if key != "preparation"}
    value, version = amp.fingerprint(body)
    sidecar["preparation"].update(fingerprint_sha256=value, version=version)


def synthetic_bundle(evidence_change=None, rng_change=None, rng_absent=False,
                     train_source=None, step17_change=None,
                     step21_change=None, numpy_version=None,
                     binding_change=None, binding_absent=False,
                     input_dir="W:\\synthetic\\inputs"):
    """Return (plan, raw bytes) for a consistent invented Step 22 run.

    A real-shaped invented Step 21 sidecar is built by the Step 21 builder
    over invented Step 17/19/20 documents whose checkpoint and train
    source identities are the invented bytes below. Optional changes are
    applied to the invented Step 17 document or Step 21 sidecar, which are
    then re-bound and re-fingerprinted as a consistent chain. The invented
    RNG and graph-binding artifacts carry the synthetic marker, so only a
    synthetic runtime accepts them.
    """
    checkpoint, metrics = synthetic_evidence()
    if evidence_change:
        evidence_change(checkpoint, metrics)
    checkpoint_bytes = amp.encode(checkpoint)
    metrics_bytes = amp.encode(metrics)
    train_bytes = train_source or synthetic_train_source()
    step20_plan = amp.synthetic_step20_plan()
    step20_plan["assets"]["checkpoint"].update(
        sha256=hashlib.sha256(checkpoint_bytes).hexdigest(),
        size_bytes=len(checkpoint_bytes))
    docs = amp.synthetic_documents(step20_plan)
    docs["step17_mapping"]["join_rules"] = {"patient_index": FILTER_RULE}
    step21_plan, step21_raw = amp.synthetic_bundle(documents=docs,
                                                   step20_plan=step20_plan)
    step21_plan["policy_sources"]["train"] = hashlib.sha256(
        train_bytes).hexdigest()
    step21 = amp.build_sidecar(step21_plan, step21_raw, amp.load_config(
        amp.read_config_bytes(amp.CONFIG_PATH)))
    step17_bytes = step21_raw["step17_mapping"]
    if step17_change:
        document = json.loads(step17_bytes)
        step17_change(document)
        step17_bytes = amp.encode(document)
        sha = hashlib.sha256(step17_bytes).hexdigest()
        step21["inputs"]["step17_mapping"].update(
            sha256=sha, size_bytes=len(step17_bytes))
        step21["step17_mapping"]["sha256"] = sha
        for record in step21["records"]:
            record["step17_mapping"]["sha256"] = sha
    if step21_change:
        step21_change(step21)
    refingerprint(step21)
    raw = {"step21_provenance": amp.encode(step21),
           "step17_mapping": step17_bytes,
           "step19_summary": step21_raw["step19_summary"],
           "train_source": train_bytes,
           "checkpoint": checkpoint_bytes,
           "metrics": metrics_bytes}
    if not rng_absent:
        replay = numpy_runtime() or {}
        document = {
            "schema": RNG_SCHEMA, "schema_version": "1.0",
            "attribution": RNG_ATTRIBUTION, "synthetic": SYNTHETIC_MARKER,
            "run": {"checkpoint_sha256":
                        hashlib.sha256(checkpoint_bytes).hexdigest(),
                    "metrics_sha256":
                        hashlib.sha256(metrics_bytes).hexdigest()},
            "numpy_version": (numpy_version or replay.get("numpy_version")
                              or "synthetic-numpy-unavailable"),
            "bit_generator": BIT_GENERATOR}
        if rng_change:
            rng_change(document)
        raw["rng_provenance"] = amp.encode(document)
    graph_sha = step20_plan["assets"]["graph"]["sha256"]
    if not binding_absent:
        document = {
            "schema": GRAPH_BINDING_SCHEMA, "schema_version": "1.0",
            "attribution": "approved_immutable_run_manifest",
            SYNTHETIC_KEY: SYNTHETIC_MARKER,
            "run": {"checkpoint_sha256":
                        hashlib.sha256(checkpoint_bytes).hexdigest(),
                    "metrics_sha256":
                        hashlib.sha256(metrics_bytes).hexdigest()},
            "graph_sha256": graph_sha}
        if binding_change:
            binding_change(document)
        raw["run_graph_binding"] = amp.encode(document)
    inputs = {name: None for name in ALL_ROLES}
    for name, data in raw.items():
        inputs[name] = {"path": os.path.join(input_dir, name + ".bin"),
                        "sha256": hashlib.sha256(data).hexdigest()}
    inputs["checkpoint"]["size_bytes"] = len(checkpoint_bytes)
    plan = {"inputs": inputs,
            "graph": {"sha256": graph_sha, "reference": GRAPH_REFERENCE,
                      "canonical_path": SYNTHETIC_GRAPHS_PATH},
            "config_path": CONFIG_PATH,
            "output_dir": "W:\\synthetic\\protected\\model-provenance\\out",
            "protected_root": "W:\\synthetic\\protected"}
    return plan, raw


def run_synthetic():
    """Build two invented preparations; nothing protected is read or written.

    The first has invented run-attributed RNG and graph-byte evidence and
    must verify every case. The second, shaped like the real plan, has
    neither: it must leave every case unavailable with a null partition
    and null partition integrity while still computing expected
    configuration counts. Their fingerprints must differ.
    """
    runtime = synthetic_runtime()
    if runtime["rng"] is None:
        raise Step22Error("synthetic_replay_numpy_unavailable")
    config_ref = load_config(read_config_bytes(CONFIG_PATH))
    plan, raw = synthetic_bundle()
    first = build_sidecar(plan, raw, config_ref, runtime)
    again = build_sidecar(plan, raw, config_ref, runtime)
    if first["preparation"] != again["preparation"]:
        raise Step22Error("fingerprint_not_deterministic")
    if first["counts"][VERIFIED] != first["counts"]["cases"]:
        raise Step22Error("synthetic_not_verified")
    plan, raw = synthetic_bundle(rng_absent=True, binding_absent=True)
    second = build_sidecar(plan, raw, config_ref, runtime)
    reconstruction = second["split_reconstruction"]
    if (second["counts"][UNAVAILABLE] != second["counts"]["cases"]
            or any(case["partition"] is not None
                   for case in second["cases"])
            or reconstruction["counts"] is not None
            or reconstruction["pairwise_disjoint"] is not None):
        raise Step22Error("synthetic_absent_evidence_not_unavailable")
    # Same invented collection and fractions: the expected counts must
    # equal the first run's verified replay counts.
    if (second["expected_configuration_counts"]["status"] != COMPUTED
            or second["expected_configuration_counts"]["counts"]
            != first["split_reconstruction"]["counts"]):
        raise Step22Error("synthetic_expected_counts_not_computed")
    if second["preparation"]["version"] == first["preparation"]["version"]:
        raise Step22Error("fingerprint_not_distinct")
    result = summary(first)
    result["status"] = "passed"
    result["alternative_reconstruction_status"] = reconstruction["status"]
    result["alternative_expected_counts_status"] = second[
        "expected_configuration_counts"]["status"]
    result["alternative_preparation_version"] = second["preparation"][
        "version"]
    return result


SUMMARY_KEYS = ("cases", "verified", "unknown", "unavailable",
                "reconstruction_status", "expected_counts_status",
                "upstream_training_input_status",
                "downstream_graph_chain_status", "reason_codes",
                "alternative_reconstruction_status",
                "alternative_expected_counts_status", "phase4_eligible",
                "preparation_version", "alternative_preparation_version",
                "fingerprint_sha256", "sidecar_sha256")


def main(argv=None):
    """Run synthetic mode by default or the explicit operator-real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="operator only: publish the protected sidecar")
    args = parser.parse_args(argv)
    try:
        result = run_real() if args.operator_real else run_synthetic()
    except (Step22Error, amp.Step21Error, mapping.MappingError,
            pmo.Step20Error) as error:
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
