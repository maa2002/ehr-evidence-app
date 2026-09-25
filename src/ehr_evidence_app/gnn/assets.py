#!/usr/bin/env python3
"""Validate inherited GNN asset identities and safe structural metadata (Step 15).

Standard library only. The committed Step 1 configuration is the identity
authority: its exact bytes must match the approved SHA-256, and its canonical
upstream root, run, eleven roles, relative paths, sizes and digests are
validated before any upstream asset is opened. Each asset is then read once
as a regular, non-link file whose path is walked from the drive root without
following links; a change in size or modification time between the walk,
the open and the end of the read fails. The checkpoint, patient graphs,
documentation and source files are only byte-hashed. Only the four JSON
roles (label vocabulary, concept vocabulary, canonical metrics and canonical
explanations) are parsed, from the same bounded bytes that were hashed and
only after those bytes match the approved identity.

The pinned checkpoint compatibility probe (config/checkpoint_compatibility_
probe.json) is validated against an exact schema. Equal checkpoint head
widths and label vocabulary length establish dimensional compatibility only;
they do not establish that output index i is label vocabulary entry i.

The record holds roles, relative paths, sizes, digests, counts and finding
codes only. No patient row, label, concept, explanation value or tensor value
is printed or written, and nothing is uploaded.

--write stores the record at the fixed protected path. An identical existing
record is left unchanged; a different one is never replaced and the run
exits 2 with output_conflict.

Usage:
    python -m ehr_evidence_app.gnn.assets [--write]
"""
import argparse
import hashlib
import json
import math
import ntpath
import os
import re
import stat
import sys

from ehr_evidence_app.storage import policy as guard

REPO_ROOT = guard.REPO_ROOT
CONFIG_PATH = os.path.join(REPO_ROOT, "config", "inherited_baseline.json")
PROBE_PATH = os.path.join(REPO_ROOT, "config",
                          "checkpoint_compatibility_probe.json")
UPSTREAM_ROOT = guard.POLICY_ANCHOR + "\\standardized-omop-gnn-7B82"
OUTPUT_PATH = (guard.PRIVATE_ROOT
               + "\\outputs\\asset-validation\\source-assets-v1.json")
APPROVED_CONFIG_SHA256 = "a60eeeb5e77fa5ed0a631e84dcdc025c463f6399b9083487672e507d7f353241"

CANONICAL_RUN = "diag_kg"
CONFIG_STEP = "1"
MAX_CONFIG_BYTES = 256 * 1024
MAX_PROBE_BYTES = 64 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 8 * 1024 * 1024

ROLES = ("documentation", "source_explain_patient", "source_train",
         "source_model", "source_build_patient_graphs",
         "canonical_checkpoint", "canonical_metrics",
         "canonical_explanations", "patient_graphs", "concept_vocab",
         "label_vocab")
EXPECTED_ROLES = frozenset(ROLES)
RUN_ROLES = ("canonical_checkpoint", "canonical_metrics",
             "canonical_explanations")
JSON_ROLES = ("label_vocab", "concept_vocab", "canonical_metrics",
              "canonical_explanations")
HASH_ONLY_ROLES = tuple(role for role in ROLES if role not in JSON_ROLES)

EXPECTED_LABEL_COUNT = 275
CONCEPT_DOMAIN_COUNT = 3
MAX_CONCEPT_ENTRIES = 250000
MAX_EXPLANATIONS = 10000
MAX_EXPLANATION_KEYS = 64

# Canonical diag_kg identity approved in Step 1; configuration cannot relax it.
METRIC_FIELDS = ("task", "data_source", "no_kg", "best_epoch", "best_val",
                 "test", "history")
CANONICAL_IDENTITY = (("task", "diagnosis"), ("data_source", "omop"),
                      ("no_kg", False))
BEST_EPOCH = 42
HISTORY_LENGTH = 50
AUROC_MACRO = 0.9062469508269994
AUPRC_MACRO = 0.31301594151588924
CONFIG_METRICS = {"expected_best_epoch": BEST_EPOCH,
                  "reported_test_values": {"macro_auroc": AUROC_MACRO,
                                           "macro_auprc": AUPRC_MACRO}}

PROBE_KEYS = frozenset(("schema_version", "step", "record_type", "purpose",
                        "canonical_run", "checkpoint_role",
                        "checkpoint_sha256", "checkpoint_size_bytes",
                        "output_heads", "label_count", "label_order_status",
                        "limitations"))
PROBE_FIXED = (("schema_version", "1.0"), ("step", "15"),
               ("record_type", "checkpoint_compatibility_probe"),
               ("canonical_run", CANONICAL_RUN),
               ("checkpoint_role", "canonical_checkpoint"),
               ("label_order_status", "dimensional_compatibility_only"))
PROBE_HEADS = ("graph_head", "seq_head")

HEX64 = re.compile(r"^[0-9A-Fa-f]{64}$")
SAFE_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class AssetProblem(Exception):
    """A read failure carrying a sanitized finding code only."""

    def __init__(self, code):
        """Store the finding code (never a path or content)."""
        super(AssetProblem, self).__init__(code)
        self.code = code


# --------------------------------------------------------------------------
# Small type checks
# --------------------------------------------------------------------------

def is_count(value):
    """Return True for a non-negative integer that is not a boolean."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def is_scalar(value):
    """Return True for text, a non-boolean integer or a finite float."""
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, (str, int))


def is_exact(value, required):
    """Return True when value has exactly the type and value of required."""
    return type(value) is type(required) and value == required


# --------------------------------------------------------------------------
# Bounded, link-free reads
# --------------------------------------------------------------------------

def link_walk_problem(path):
    """Return a finding code when path is not a plain regular file, else None.

    Walks from the drive root with lstat so no link is followed: every
    component must exist and none may be a symlink, junction or other
    reparse point; every ancestor must be a directory and the leaf a
    regular file.
    """
    if guard.path_problem(path):
        return "path_invalid"
    current = path[:3]
    parts = path[3:].split("\\")
    info = None
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except OSError:
            return "unavailable"
        if guard.is_link_like(info):
            return "link_rejected"
        if index < len(parts) - 1 and not stat.S_ISDIR(info.st_mode):
            return "unavailable"
    if not stat.S_ISREG(info.st_mode):
        return "link_rejected"
    return None


def read_regular_file(path, limit=None):
    """Read one regular non-link file once; return (size, SHA-256, bytes).

    The SHA-256 is uppercase hex. With a limit the exact bytes read are
    returned (and a larger file is refused); without one they are streamed
    into the hash and discarded, and None is returned in their place. The
    file's size and modification time must agree across the lstat before
    opening, the fstat of the open handle and the lstat after reading.
    """
    problem = link_walk_problem(path)
    if problem:
        raise AssetProblem(problem)
    try:
        before = os.lstat(path)
        if limit is not None and before.st_size > limit:
            raise AssetProblem("too_large")
        digest = hashlib.sha256()
        count = 0
        kept = [] if limit is not None else None
        with open(path, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_size, opened.st_mtime_ns)
                    != (before.st_size, before.st_mtime_ns)):
                raise AssetProblem("changed")
            while True:
                block = handle.read(CHUNK_BYTES)
                if not block:
                    break
                count += len(block)
                if limit is not None and count > limit:
                    raise AssetProblem("too_large")
                digest.update(block)
                if kept is not None:
                    kept.append(block)
        after = os.lstat(path)
    except OSError:
        raise AssetProblem("unavailable")
    if guard.is_link_like(after) or not stat.S_ISREG(after.st_mode):
        raise AssetProblem("link_rejected")
    if ((before.st_size, before.st_mtime_ns)
            != (after.st_size, after.st_mtime_ns) or count != after.st_size):
        raise AssetProblem("changed")
    data = b"".join(kept) if kept is not None else None
    return count, digest.hexdigest().upper(), data


def reject_constant(name):
    """Refuse the non-standard JSON constants NaN, Infinity and -Infinity."""
    raise ValueError("non-finite JSON constant")


def unique_object(pairs):
    """Build a JSON object, refusing duplicate keys."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def parse_json_bytes(raw, strict=True):
    """Parse bytes already read and verified; never reopen a path.

    Duplicate keys are always refused. strict also refuses NaN/Infinity;
    inherited upstream JSON is parsed with strict=False because Python's
    json.dump may write those constants, and each structural check rejects
    non-finite values wherever it requires a scalar.
    """
    extra = {"parse_constant": reject_constant} if strict else {}
    return json.loads(raw.decode("utf-8-sig"),
                      object_pairs_hook=unique_object, **extra)


# --------------------------------------------------------------------------
# Configuration authority (checked before any upstream asset is opened)
# --------------------------------------------------------------------------

def relative_path_problem(relative):
    """Return True when an upstream-relative path is not plain and safe.

    Absolute, rooted, UNC, drive or drive-relative paths, forward slashes,
    empty, '.', '..' or .git components, streams, reserved names and any
    spelling Windows would normalise differently are refused.
    """
    if not isinstance(relative, str) or not relative:
        return True
    if "/" in relative or ":" in relative or relative.startswith("\\"):
        return True
    for part in relative.split("\\"):
        if guard.component_problem(part) or part.lower() == ".git":
            return True
    return guard.path_problem(UPSTREAM_ROOT + "\\" + relative) is not None


def config_findings(config):
    """Return finding codes for the configuration; empty when it is usable.

    Scope (step, run, upstream root, metric expectations), the exact role
    set and each asset's path, size and SHA-256 type are checked lexically.
    """
    if not isinstance(config, dict):
        return ["configuration_scope_mismatch"]
    metrics = config.get("metrics")
    reported = metrics.get("reported_test_values") if isinstance(
        metrics, dict) else None
    expected = CONFIG_METRICS["reported_test_values"]
    if (not is_exact(config.get("step"), CONFIG_STEP)
            or not is_exact(config.get("canonical_run"), CANONICAL_RUN)
            or not is_exact(config.get("upstream_root"), UPSTREAM_ROOT)
            or guard.path_problem(UPSTREAM_ROOT)
            or not isinstance(metrics, dict)
            or not is_exact(metrics.get("expected_best_epoch"), BEST_EPOCH)
            or not isinstance(reported, dict) or set(reported) != set(expected)
            or not all(is_exact(reported[name], value)
                       for name, value in expected.items())):
        return ["configuration_scope_mismatch"]
    entries = config.get("assets")
    if (not isinstance(entries, list) or len(entries) != len(ROLES)
            or not all(isinstance(item, dict) for item in entries)):
        return ["asset_role_set_invalid"]
    roles = [item.get("role") for item in entries]
    if (not all(isinstance(role, str) for role in roles)
            or len(set(roles)) != len(roles) or set(roles) != EXPECTED_ROLES):
        return ["asset_role_set_invalid"]
    findings = []
    paths = set()
    run_prefix = ntpath.normcase("data\\runs\\%s\\" % CANONICAL_RUN)
    for item in entries:
        role = item["role"]
        relative = item.get("relative_path")
        if relative_path_problem(relative):
            findings.append("asset_path_invalid:%s" % role)
        else:
            key = ntpath.normcase(relative)
            if key in paths or (role in RUN_ROLES
                                and not key.startswith(run_prefix)):
                findings.append("asset_path_invalid:%s" % role)
            paths.add(key)
        size = item.get("expected_size_bytes")
        if not is_count(size) or (role in JSON_ROLES and size > MAX_JSON_BYTES):
            findings.append("asset_size_invalid:%s" % role)
        digest = item.get("expected_sha256")
        if not isinstance(digest, str) or not HEX64.fullmatch(digest):
            findings.append("asset_sha256_invalid:%s" % role)
    return findings


def load_config():
    """Read the fixed configuration once; return (config, digest, findings)."""
    try:
        _, digest, raw = read_regular_file(CONFIG_PATH, MAX_CONFIG_BYTES)
    except AssetProblem:
        return None, None, ["configuration_unavailable"]
    if digest.lower() != APPROVED_CONFIG_SHA256.lower():
        return None, digest, ["configuration_identity_mismatch"]
    try:
        config = parse_json_bytes(raw)
    except (UnicodeDecodeError, ValueError):
        return None, digest, ["configuration_invalid"]
    return config, digest, config_findings(config)


# --------------------------------------------------------------------------
# Structural checks on verified JSON bytes
# --------------------------------------------------------------------------

def label_vocab_summary(data):
    """Return (summary, findings) for a sequence of scalar labels."""
    if not isinstance(data, list) or not all(is_scalar(item) for item in data):
        return {"status": "invalid", "entry_count": None}, ["label_vocab_invalid"]
    if len(data) != EXPECTED_LABEL_COUNT:
        return ({"status": "invalid", "entry_count": len(data)},
                ["label_count_mismatch"])
    return {"status": "valid", "entry_count": len(data)}, []


def concept_vocab_summary(data):
    """Return (summary, findings) for exactly three bounded scalar mappings.

    Only the safe domain key names and per-domain entry counts are kept.
    """
    invalid = ({"status": "invalid", "domain_count": None,
                "domain_entry_counts": None}, ["concept_vocab_invalid"])
    if not isinstance(data, dict) or len(data) != CONCEPT_DOMAIN_COUNT:
        return invalid
    counts = {}
    for name, mapping in data.items():
        if (not SAFE_KEY.fullmatch(name) or not isinstance(mapping, dict)
                or len(mapping) > MAX_CONCEPT_ENTRIES
                or not all(is_scalar(value) for value in mapping.values())):
            return invalid
        counts[name] = len(mapping)
    return {"status": "valid", "domain_count": len(counts),
            "domain_entry_counts": counts}, []


def metrics_summary(data):
    """Return (summary, findings) for the canonical diag_kg metrics object."""
    if (not isinstance(data, dict)
            or any(name not in data for name in METRIC_FIELDS)
            or not isinstance(data["test"], dict)
            or not isinstance(data["history"], list)):
        return {"status": "invalid"}, ["metrics_invalid"]
    test = data["test"]
    if (not all(is_exact(data[name], value)
                for name, value in CANONICAL_IDENTITY)
            or not is_exact(data["best_epoch"], BEST_EPOCH)
            or len(data["history"]) != HISTORY_LENGTH
            or not is_exact(test.get("auroc_macro"), AUROC_MACRO)
            or not is_exact(test.get("auprc_macro"), AUPRC_MACRO)):
        return {"status": "invalid"}, ["metrics_identity_mismatch"]
    return {"status": "valid", "best_epoch": BEST_EPOCH,
            "history_length": HISTORY_LENGTH}, []


def explanations_summary(data):
    """Return (summary, findings) for a bounded sequence of safe objects.

    Every element must be a non-empty object whose keys are plain
    identifiers; only the element count is kept, never keys or values.
    """
    if (not isinstance(data, list) or not data
            or len(data) > MAX_EXPLANATIONS):
        return ({"status": "invalid", "entry_count": None},
                ["explanations_invalid"])
    for item in data:
        if (not isinstance(item, dict) or not item
                or len(item) > MAX_EXPLANATION_KEYS
                or not all(SAFE_KEY.fullmatch(key) for key in item)):
            return ({"status": "invalid", "entry_count": None},
                    ["explanations_invalid"])
    return {"status": "valid", "entry_count": len(data)}, []


SUMMARIES = {"label_vocab": label_vocab_summary,
             "concept_vocab": concept_vocab_summary,
             "canonical_metrics": metrics_summary,
             "canonical_explanations": explanations_summary}


def not_evaluated(role):
    """Return the structure placeholder for unverified or unread JSON bytes."""
    summary = {"status": "not_evaluated"}
    if role in ("label_vocab", "canonical_explanations"):
        summary["entry_count"] = None
    elif role == "concept_vocab":
        summary.update(domain_count=None, domain_entry_counts=None)
    return summary


# --------------------------------------------------------------------------
# Checkpoint compatibility probe
# --------------------------------------------------------------------------

def probe_findings(probe, checkpoint_entry, checkpoint_observed):
    """Return findings for the pinned checkpoint probe record.

    The probe must have exactly the documented keys and types, name the
    canonical run and checkpoint role, match the configured and observed
    checkpoint size and SHA-256, and record graph_head, seq_head and
    label_count of 275 with dimensional_compatibility_only status.
    """
    heads = probe.get("output_heads") if isinstance(probe, dict) else None
    limitations = probe.get("limitations") if isinstance(probe, dict) else None
    if (not isinstance(probe, dict) or set(probe) != PROBE_KEYS
            or not all(isinstance(probe[name], str)
                       for name, _ in PROBE_FIXED)
            or not isinstance(probe["purpose"], str)
            or not probe["purpose"].strip()
            or not isinstance(probe["checkpoint_sha256"], str)
            or not HEX64.fullmatch(probe["checkpoint_sha256"])
            or not is_count(probe["checkpoint_size_bytes"])
            or not is_count(probe["label_count"])
            or not isinstance(heads, dict) or set(heads) != set(PROBE_HEADS)
            or not all(is_count(heads[name]) for name in PROBE_HEADS)
            or not isinstance(limitations, list) or not limitations
            or not all(isinstance(item, str) and item.strip()
                       for item in limitations)):
        return ["checkpoint_probe_schema_invalid"]
    digest = probe["checkpoint_sha256"].upper()
    size = probe["checkpoint_size_bytes"]
    mismatch = (
        any(probe[name] != value for name, value in PROBE_FIXED)
        or digest != checkpoint_entry["expected_sha256"].upper()
        or size != checkpoint_entry["expected_size_bytes"]
        or (checkpoint_observed is not None
            and (digest, size) != (checkpoint_observed["sha256"],
                                   checkpoint_observed["size_bytes"]))
        or probe["label_count"] != EXPECTED_LABEL_COUNT
        or any(heads[name] != EXPECTED_LABEL_COUNT for name in PROBE_HEADS))
    return ["checkpoint_compatibility_mismatch"] if mismatch else []


def check_probe(checkpoint_entry, checkpoint_observed):
    """Read and validate the probe; return (summary, findings)."""
    try:
        _, _, raw = read_regular_file(PROBE_PATH, MAX_PROBE_BYTES)
        probe = parse_json_bytes(raw)
    except AssetProblem:
        return {"status": "invalid"}, ["checkpoint_probe_unavailable"]
    except (UnicodeDecodeError, ValueError):
        return {"status": "invalid"}, ["checkpoint_probe_schema_invalid"]
    findings = probe_findings(probe, checkpoint_entry, checkpoint_observed)
    if findings:
        return {"status": "invalid"}, findings
    return {"status": "valid",
            "graph_head": probe["output_heads"]["graph_head"],
            "seq_head": probe["output_heads"]["seq_head"],
            "label_count": probe["label_count"],
            "label_order_status": probe["label_order_status"]}, []


# --------------------------------------------------------------------------
# Record
# --------------------------------------------------------------------------

def failed_record(config_digest, findings):
    """Return the record for a configuration failure; no asset was opened."""
    return {"record_type": "source_asset_validation", "schema_version": "1.0",
            "step": "15", "status": "FAIL", "canonical_run": CANONICAL_RUN,
            "configuration_sha256": config_digest, "asset_count": 0,
            "assets": [], "label_count": None,
            "findings": sorted(set(findings))}


def validate_assets():
    """Validate the fixed configuration and assets; return sanitized metadata.

    The configuration, upstream root, probe path and approved digest are
    module constants; callers cannot redirect them.
    """
    config, config_digest, findings = load_config()
    if findings:
        return failed_record(config_digest, findings)
    entries = {item["role"]: item for item in config["assets"]}
    assets = []
    observed = {}
    structures = {}
    for role in ROLES:
        entry = entries[role]
        path = UPSTREAM_ROOT + "\\" + entry["relative_path"]
        limit = MAX_JSON_BYTES if role in JSON_ROLES else None
        try:
            size, digest, raw = read_regular_file(path, limit)
        except AssetProblem as problem:
            code = {"link_rejected": "asset_link_rejected",
                    "changed": "asset_changed",
                    "too_large": "asset_identity_mismatch"}.get(
                        problem.code, "asset_unavailable")
            findings.append("%s:%s" % (code, role))
            continue
        observed[role] = {"size_bytes": size, "sha256": digest}
        assets.append({"role": role, "relative_path": entry["relative_path"],
                       "size_bytes": size, "sha256": digest,
                       "content_handling": ("bounded_json_structure_checked"
                                            if role in JSON_ROLES
                                            else "bytes_hashed_only")})
        if (size != entry["expected_size_bytes"]
                or digest != entry["expected_sha256"].upper()):
            findings.append("asset_identity_mismatch:%s" % role)
            continue
        if role in JSON_ROLES:
            try:
                data = parse_json_bytes(raw, strict=False)
            except (UnicodeDecodeError, ValueError):
                data = None
            structures[role], problems = SUMMARIES[role](data)
            findings.extend(problems)
    for role in JSON_ROLES:
        structures.setdefault(role, not_evaluated(role))
    probe, problems = check_probe(entries["canonical_checkpoint"],
                                  observed.get("canonical_checkpoint"))
    findings.extend(problems)
    label_count = structures["label_vocab"]["entry_count"]
    dimension_match = (probe["status"] == "valid"
                       and structures["label_vocab"]["status"] == "valid"
                       and probe["graph_head"] == probe["seq_head"]
                       == probe["label_count"] == label_count)
    return {"record_type": "source_asset_validation", "schema_version": "1.0",
            "step": "15", "status": "FAIL" if findings else "PASS",
            "canonical_run": CANONICAL_RUN,
            "configuration_sha256": config_digest,
            "asset_count": len(assets), "assets": assets,
            "hash_only_roles": list(HASH_ONLY_ROLES),
            "label_count": label_count,
            "label_vocab": structures["label_vocab"],
            "concept_vocab": structures["concept_vocab"],
            "canonical_metrics": structures["canonical_metrics"],
            "canonical_explanations": structures["canonical_explanations"],
            "checkpoint_probe": probe,
            "label_dimension_match": dimension_match,
            "label_index_correspondence": "not_established",
            "artifact_upload": False,
            "findings": sorted(set(findings))}


def serialise(record):
    """Return the deterministic JSON text of a validation record."""
    return json.dumps(record, indent=2, sort_keys=True) + "\n"


class RecordConflict(Exception):
    """An existing record at the output path differs from the new one."""


def write_record(record, path=OUTPUT_PATH):
    """Write the deterministic validation record without replacing any file.

    An identical existing record is left untouched ("unchanged"). A
    different existing record is never replaced: RecordConflict is raised
    and a new version must be published instead. New bytes go to an
    exclusively created .partial file that is fsynced and then renamed;
    os.rename on Windows refuses an existing destination, so a record that
    appears meanwhile is not replaced either. The partial file this call
    created is removed on any failure. Returns "written" or "unchanged".
    """
    payload = serialise(record).encode("utf-8")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.lexists(path):
        with open(path, "rb") as handle:
            if handle.read() == payload:
                return "unchanged"
        raise RecordConflict("output_conflict")
    partial = path + ".partial"
    created = False
    try:
        with open(partial, "xb") as handle:
            created = True
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(partial, path)
    except BaseException:
        if created and os.path.lexists(partial):
            os.remove(partial)
        raise
    return "written"


def main(argv=None):
    """Validate inherited assets and optionally write the protected record."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        record = validate_assets()
        summary = {"status": record["status"],
                   "asset_count": record["asset_count"],
                   "label_count": record["label_count"],
                   "findings": record["findings"]}
        if args.write:
            summary["record"] = write_record(record, OUTPUT_PATH)
        print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
        return 0 if record["status"] == "PASS" else 1
    except RecordConflict:
        print(json.dumps({"status": "OPERATIONAL",
                          "findings": ["output_conflict"]},
                         separators=(",", ":")))
        return 2
    except (OSError, ValueError, TypeError, KeyError):
        print(json.dumps({"status": "OPERATIONAL",
                          "findings": ["operational_failure"]},
                         separators=(",", ":")))
        return 2


if __name__ == "__main__":
    sys.exit(main())
