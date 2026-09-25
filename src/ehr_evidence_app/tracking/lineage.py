#!/usr/bin/env python3
"""Validate deterministic lineage chains (Step 14)."""
import argparse
import json
import ntpath
import os
import re
import sys
import stat

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
SCHEMA_PATH = os.path.join(REPO_ROOT, "config", "lineage_schema.json")
PROTECTED_ROOT = r"G:\Industry Dataset\ehr-evidence-workspace"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")
SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
RESERVED = re.compile(r"(?i)^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$")
FORBIDDEN_TOKEN = re.compile(r"(?i)(?:patient|subject|mrn|record|visit|encounter|prompt|prompts|response|responses|attribution|attributions|prediction|predictions|token|password|secret|api|access|private|key|credential|clinical|explanation)")
VENDOR_SECRET = re.compile(r"(?i)(?:AKIA|ASIA)[0-9A-Z]{16}|hf_[A-Za-z0-9]{8,}|xox[abposr]-[A-Za-z0-9-]{8,}|eyJ[A-Za-z0-9_-]{10,}|-{5}BEGIN[A-Z ]*PRIVATE KEY")


def synthetic_chain():
    """Return one fixed synthetic complete chain."""
    types = ("inherited_input", "evidence_preparation", "generation_configuration",
             "evaluation_output", "application_version")
    nodes = []
    previous = []
    for index, node_type in enumerate(types, 1):
        node = {"record_id": "SYNTH-LINEAGE-%02d" % index,
                "chain_id": "SYNTH-CHAIN-01", "node_type": node_type,
                "version": "fixture-v1", "status": "completed",
                "predecessors": list(previous)}
        if node_type != "application_version":
            node["protected_reference"] = PROTECTED_ROOT + r"\outputs\lineage\synthetic-%02d.json" % index
            node["protected_checksum"] = "a" * 64
        nodes.append(node)
        previous = [node["record_id"]]
    return {"schema_version": "1.0", "chain_id": "SYNTH-CHAIN-01", "nodes": nodes}


def _finding(code, node_id=None, stage="record"):
    """Return a bounded sanitized finding record."""
    safe_id = (node_id if isinstance(node_id, str) and SAFE_ID.fullmatch(node_id)
               and not _forbidden_text(node_id) else None)
    safe_stage = stage if isinstance(stage, str) and stage in (
        "inherited_input", "evidence_preparation", "generation_configuration",
        "evaluation_output", "application_version", "record") else "record"
    return {"code": code, "node_id": safe_id, "stage": safe_stage}


def _forbidden_text(value):
    """Return whether a safe-looking token contains forbidden content."""
    return isinstance(value, str) and (FORBIDDEN_TOKEN.search(value) or VENDOR_SECRET.search(value))


def _protected_path_valid(path):
    """Validate one bounded canonical child path of the protected root."""
    if (not isinstance(path, str) or len(path) > 512 or "/" in path
            or any(ord(char) < 32 for char in path)):
        return False
    drive, tail = ntpath.splitdrive(path)
    root_drive = ntpath.splitdrive(PROTECTED_ROOT)[0].lower()
    if (drive.lower() != root_drive or not tail.startswith("\\")
            or path.count(":") != 1):
        return False
    parts = tail[1:].split("\\")
    if not parts or any(not part or part in (".", "..") for part in parts):
        return False
    canonical = ntpath.normpath(path)
    root = ntpath.normpath(PROTECTED_ROOT)
    try:
        if ntpath.normcase(ntpath.commonpath((canonical, root))) != ntpath.normcase(root):
            return False
    except ValueError:
        return False
    if ntpath.normcase(canonical) == ntpath.normcase(root):
        return False
    for part in parts:
        if (len(part) > 96 or part.endswith((".", " ")) or RESERVED.fullmatch(part)
                or any(char in part for char in '<>"|?*:') or _forbidden_text(part)):
            return False
    return True


def validate_schema(schema):
    """Validate the committed schema contract without echoing values."""
    expected = {"schema_version", "step", "node_types", "required_order",
                "statuses", "protected_root", "required_fields",
                "reference_required_node_types", "notes"}
    if not isinstance(schema, dict) or set(schema) != expected:
        return [_finding("schema_shape")]
    if schema.get("schema_version") != "1.0" or schema.get("step") != "14":
        return [_finding("schema_version_invalid")]
    if schema.get("protected_root") != PROTECTED_ROOT:
        return [_finding("schema_root_invalid")]
    if (schema.get("required_order") != schema.get("node_types")
            or len(schema["node_types"]) != 5
            or set(schema.get("statuses", [])) != {"defined", "prepared", "completed", "failed", "abstained"}
            or set(schema.get("reference_required_node_types", [])) != set(schema["node_types"][:-1])):
        return [_finding("schema_order_invalid")]
    if schema.get("required_fields") != ["record_id", "chain_id", "node_type", "version", "status", "predecessors"]:
        return [_finding("schema_fields_invalid")]
    return []


def validate_record(record, schema):
    """Return deterministic sanitized findings for one lineage record."""
    findings = validate_schema(schema)
    if findings:
        return findings
    if not isinstance(record, dict) or set(record) != {"schema_version", "chain_id", "nodes"}:
        return [_finding("record_shape")]
    if (record["schema_version"] != schema["schema_version"]
            or not isinstance(record["chain_id"], str)
            or not SAFE_ID.fullmatch(record["chain_id"])
            or _forbidden_text(record["chain_id"])):
        return [_finding("record_identity")]
    nodes = record["nodes"]
    if not isinstance(nodes, list) or len(nodes) != len(schema["required_order"]):
        return [_finding("node_count")]
    ids = set()
    by_type = {}
    findings = []
    for node in nodes:
        if not isinstance(node, dict) or set(node) - set(schema["required_fields"] + ["protected_reference", "protected_checksum"]):
            findings.append(_finding("node_shape"))
            continue
        node_id = node.get("record_id")
        node_type = node.get("node_type")
        stage = node_type if isinstance(node_type, str) else "record"
        valid_id = isinstance(node_id, str) and SAFE_ID.fullmatch(node_id)
        if not valid_id or node_id in ids or _forbidden_text(node_id):
            findings.append(_finding("node_id_invalid", node_id, stage))
        if valid_id:
            ids.add(node_id)
        valid_type = isinstance(node_type, str) and node_type in schema["node_types"]
        if not valid_type or node_type in by_type:
            findings.append(_finding("node_type_invalid", node_id, stage))
        if valid_type:
            by_type[node_type] = node
        if node.get("chain_id") != record["chain_id"]:
            findings.append(_finding("chain_id_mismatch", node_id, stage))
        if (not isinstance(node.get("version"), str)
                or not VERSION.fullmatch(node["version"])
                or _forbidden_text(node["version"])):
            findings.append(_finding("version_invalid", node_id, stage))
        if node.get("status") not in schema["statuses"]:
            findings.append(_finding("status_invalid", node_id, stage))
        if node_type == "application_version" and node.get("status") not in ("completed", "failed", "abstained"):
            findings.append(_finding("terminal_status_invalid", node_id, stage))
        predecessors = node.get("predecessors")
        if not isinstance(predecessors, list) or any(not isinstance(item, str) for item in predecessors):
            findings.append(_finding("predecessors_invalid", node_id, stage))
        requires_reference = node_type in schema["reference_required_node_types"]
        if requires_reference and "protected_reference" not in node:
            findings.append(_finding("protected_reference_missing", node_id, stage))
        if "protected_checksum" in node and "protected_reference" not in node:
            findings.append(_finding("checksum_without_reference", node_id, stage))
        if "protected_reference" in node and (not _protected_path_valid(node["protected_reference"])
                                               or not SHA256.fullmatch(node.get("protected_checksum", ""))):
            findings.append(_finding("protected_reference_invalid", node_id, stage))
    required = schema["required_order"]
    if [node.get("node_type") for node in nodes if isinstance(node, dict)] != required:
        findings.append(_finding("serialized_order_invalid"))
    if set(by_type) != set(required):
        findings.append(_finding("required_node_type_missing"))
    for index, node_type in enumerate(required):
        node = by_type.get(node_type)
        expected = [] if index == 0 else [by_type[required[index - 1]].get("record_id")] if required[index - 1] in by_type else None
        if node is not None and expected is not None and node.get("predecessors") != expected:
            findings.append(_finding("predecessor_edge_invalid", node.get("record_id"), node_type))
    unique = {(item["code"], item["node_id"], item["stage"]): item for item in findings}
    return sorted(unique.values(), key=lambda item: (item["code"], item["node_id"] or ""))


def main(argv=None):
    """Validate a supplied record or the fixed synthetic chain."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record")
    args = parser.parse_args(argv)
    try:
        with open(SCHEMA_PATH, encoding="utf-8") as handle:
            schema = json.load(handle)
        if args.record:
            info = os.lstat(args.record)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 64 * 1024:
                raise OSError("record file is not a bounded regular file")
            with open(args.record, encoding="utf-8") as handle:
                record = json.load(handle)
        else:
            record = synthetic_chain()
        findings = validate_record(record, schema)
        result = {"status": "PASS" if not findings else "FAIL",
                  "finding_codes": sorted({item["code"] for item in findings}),
                  "findings": findings,
                  "nodes": len(record.get("nodes", [])) if isinstance(record, dict) else 0}
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if not findings else 1
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        print(json.dumps({"status": "OPERATIONAL", "finding_codes": ["operational_failure"], "findings": [_finding("operational_failure")], "nodes": 0}, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    sys.exit(main())
