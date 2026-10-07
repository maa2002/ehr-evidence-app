#!/usr/bin/env python3
"""Protected patient and visit identity crosswalk (Step 17).

Maps every selected person of the Step 0.3.2 development source pilot
(pilot-10-v1) to the canonical GNN bridge patient_index, and every graph
visit-node position to a source visit_occurrence_id owned by the same person.
It is an identity mapping only: it does not establish a permitted history, a
prediction target, an input boundary, the source dataset/ETL version, graph
preprocessing parity, label ordering, upstream split membership or Phase 4
eligibility. Every record carries phase4_eligible=false.

Join rules (no other key is ever used):

* graph subject_id == persons.csv subject_id, by strict decimal integer
  round-trip, giving exactly one person_id;
* graph visit hadm_id at each node position == visits.csv
  visit_occurrence_id, whose person_id must equal the mapped person_id.

patient_index is the position in the pinned graph list after keeping only
graphs that carry y_diagnosis, in the stored order. Unresolved critical links
are quarantined, never guessed. A person with no unique graph match is still
written, quarantined, with a null patient_index.

Before deserialisation every small input is hashed and the pinned graph is
stat-ed and hashed exactly once; only then is it loaded once on CPU, and its
size and modification time are compared before and after loading. Only
subject_id, y_diagnosis presence and ordered visit hadm_ids are extracted. No
model or checkpoint is loaded and no inference runs.

The patient-bearing sidecar is written atomically to a fixed protected
directory in the workspace and is never overwritten. Console output and
error messages carry status, counts and finding codes only.

Usage (operator only, omopgnn interpreter):
    python -m ehr_evidence_app.gnn.mapping
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import operator
import os
import stat
import sys

from ehr_evidence_app.storage import policy as guard

VERSION = "17.0"
SCHEMA = "patient-visit-identity-mapping"
SCHEMA_VERSION = "1.0"
REPO_ROOT = guard.REPO_ROOT
UPSTREAM_ROOT = guard.POLICY_ANCHOR + "\\standardized-omop-gnn-7B82"
# Byte-exact copy of the repository as it stood when the pilot-10-v1
# artefacts were produced. Pins to source files of that revision resolve
# here, because the package layout has since moved those files.
V1_SOURCE_ARCHIVE = (guard.PRIVATE_ROOT + "\\archive\\pre-reset-2026-09-25"
                     "\\ehr-evidence-app")
PILOT_LABEL = "pilot-10-v1"
PILOT_DIR = (guard.PRIVATE_ROOT + "\\protected-data\\source-pilot\\"
             + PILOT_LABEL)
OUTPUT_DIR = (guard.PRIVATE_ROOT + "\\protected-data\\identity-mappings\\"
              "step-17-pilot10-v1")
OUTPUT_NAME = "mapping.json"
PARTIAL = ".partial"
CHUNK_BYTES = 8 * 1024 * 1024
MAX_SMALL_BYTES = 64 * 1024 * 1024

STATUS_VERIFIED = "verified"
STATUS_REPEATED = "verified_with_repeated_visit_nodes"
STATUS_QUARANTINED = "quarantined"


class MappingError(Exception):
    """A fail-closed precondition failure carrying a finding code only."""


def real_plan():
    """Return the fixed operator-verified inputs and protected output."""
    return {
        "graph": {"path": UPSTREAM_ROOT + "\\data\\processed\\"
                          "patient_graphs.pt",
                  "size_bytes": 756482261,
                  "sha256": "a650f130e08dcfa472cd50985dfcf9d1"
                            "40772aebca40ac0f9b62e8103049de8c"},
        "builder": {"path": UPSTREAM_ROOT + "\\src\\preprocess\\"
                            "build_patient_graphs.py",
                    "sha256": "175aaf7335b72c41ecc262d88203e800"
                              "0323fe60451c92e419effd47111d1f70"},
        "bridge": {"path": UPSTREAM_ROOT + "\\src\\gnn\\explain_patient.py",
                   "sha256": "1799f536c69d2312a4277f867f7836d7"
                             "cd56ddc54a448f24924597199a3b87d9"},
        # The configuration the pilot-10-v1 snapshot was extracted under;
        # its manifest records exactly this SHA-256.
        "pilot_config": {"path": os.path.join(V1_SOURCE_ARCHIVE, "config",
                                              "source_pilot.json"),
                         "sha256": "781dc6d0be3f30e17d13b76c899c59c5"
                                   "79216449e916ba8806c322c8e008b7f3"},
        "pilot_reader": {"path": os.path.join(V1_SOURCE_ARCHIVE, "scripts",
                                              "prepare_source_pilot.py"),
                         "sha256": "772d3f0f554be9030d6d546eeb126ca2"
                                   "afe6b9383b4b52a31e9a3f91ce7e3a85"},
        "pilot_manifest": {"path": PILOT_DIR + "\\manifest.json",
                           "sha256": "496b6595ddb36175f0f52c24bcfe6412"
                                     "67904a091a174a2ac51d31f94dda5551"},
        "pilot_producer": {"version": "0.3.2",
                           "sha256": "803e5ebfc9a7b023f24b12f9029fd7f4"
                                     "f6db7190f7b35ec8dedd88c335f1f31f"},
        "pilot_dir": PILOT_DIR,
        "pilot_label": PILOT_LABEL,
        "output_dir": OUTPUT_DIR,
        "protected_root": guard.PRIVATE_ROOT + "\\protected-data",
    }


# Identifier and Input Checks

def canonical_int(value):
    """Return value as an int only when it round-trips as strict decimal.

    Strings must equal str(int(value)) exactly (no sign, padding, spaces or
    non-ASCII digits); integer types are accepted, bools and floats are not.
    Scalar tensors are unwrapped with item(). Returns None otherwise.
    """
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (TypeError, ValueError):
            return None
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        try:
            number = int(value)
        except ValueError:
            return None
        return number if str(number) == value else None
    try:
        return operator.index(value)
    except TypeError:
        return None


def sha256_file(path):
    """Return the SHA-256 hex digest of a file read in bounded chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def regular_file_info(path):
    """Return lstat info for a plain regular file under the fixed anchor.

    Every existing component is inspected without following links; any
    symlink, junction or reparse point, or a non-regular leaf, fails closed.
    """
    if guard.path_problem(path) or not guard.is_within(
            path, guard.POLICY_ANCHOR, strict=True):
        raise MappingError("input_path_rejected")
    current = path[:3]
    parts = path[3:].split("\\")
    info = None
    for part in parts:
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except OSError:
            raise MappingError("input_missing") from None
        if guard.is_link_like(info):
            raise MappingError("input_link_rejected")
    if not stat.S_ISREG(info.st_mode):
        raise MappingError("input_not_regular_file")
    return info


def read_verified(path, name, mismatch, sha256=None, size=None):
    """Read a guarded small file once and verify those exact bytes.

    The bounded bytes are size- and hash-checked in memory and returned with
    the (size, mtime_ns) signature that was consistent across the path lstat
    and the open handle's fstat before and after the read. Callers parse only
    the returned bytes, then call confirm_unchanged.
    """
    before = regular_file_info(path)
    with open(path, "rb") as handle:
        opened = os.fstat(handle.fileno())
        data = handle.read(MAX_SMALL_BYTES + 1)
        after = os.fstat(handle.fileno())
    if len(data) > MAX_SMALL_BYTES:
        raise MappingError("input_too_large:%s" % name)
    signature = (before.st_size, before.st_mtime_ns)
    if (len(data) != before.st_size
            or (opened.st_size, opened.st_mtime_ns) != signature
            or (after.st_size, after.st_mtime_ns) != signature):
        raise MappingError("input_changed_during_read:%s" % name)
    if size is not None and len(data) != size:
        raise MappingError(mismatch)
    if (sha256 is not None
            and hashlib.sha256(data).hexdigest() != sha256.lower()):
        raise MappingError(mismatch)
    return data, signature


def confirm_unchanged(held):
    """Fail closed if any held (path, signature, name) input has changed."""
    for path, signature, name in held:
        info = regular_file_info(path)
        if (info.st_size, info.st_mtime_ns) != signature:
            raise MappingError("input_changed_during_read:%s" % name)


def verify_small_input(entry, name, held):
    """Read a pinned small input once, check its hash and return its bytes."""
    data, signature = read_verified(entry["path"], name,
                                    "input_hash_mismatch:%s" % name,
                                    sha256=entry["sha256"])
    held.append((entry["path"], signature, name))
    return data


def output_problem(path, protected_root):
    """Return a finding code when path is not a permitted protected output.

    The path must pass the private-root destination checks and lie
    strictly beneath protected_root, itself strictly inside the private root.
    """
    if not guard.is_within(protected_root, guard.PRIVATE_ROOT, strict=True):
        return "output_not_protected"
    if guard.destination_problem(path, "directory"):
        return "output_path_rejected"
    if not guard.is_within(path, protected_root, strict=True):
        return "output_not_protected"
    return None


def output_preconditions(plan):
    """Refuse an existing or unprotected output or partial directory."""
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        if os.path.lexists(path):
            raise MappingError("output_exists")
        problem = output_problem(path, plan["protected_root"])
        if problem:
            raise MappingError(problem)


# Source Pilot Snapshot

def parse_json(data):
    """Parse a JSON document from already verified bytes."""
    return json.loads(data.decode("utf-8"))


def parse_rows(data, columns):
    """Parse the named CSV columns from verified bytes; all must be present."""
    reader = csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""))
    if not set(columns) <= set(reader.fieldnames or ()):
        raise MappingError("pilot_columns_missing")
    return [{column: row.get(column) or "" for column in columns}
            for row in reader]


def verify_producer(manifest, producer):
    """Require the manifest's historical producer to be the approved one.

    The recorded Step 0.3.2 extraction script version and SHA-256 must equal
    the pinned historical producer. This is a separate role from the current
    approved verifier, whose file hash is checked independently.
    """
    script = manifest.get("extraction_script") or {}
    digest = script.get("sha256")
    if (script.get("version") != producer["version"]
            or not isinstance(digest, str)
            or digest.lower() != producer["sha256"].lower()):
        raise MappingError("pilot_manifest_producer_mismatch")


def verify_pilot(plan, held):
    """Validate the pilot config, manifest and CSVs; return rows and metadata.

    Each file is read once and parsed from its verified bytes. The manifest
    must record a finished, complete, promoted run of the pinned version
    label by the pinned historical producer under the pinned configuration,
    and persons.csv and visits.csv must match their recorded size and
    SHA-256. Every file is confirmed unchanged after parsing.
    """
    config = parse_json(verify_small_input(plan["pilot_config"],
                                           "pilot_config", held))
    tables = config.get("tables", {})
    names = {"persons": tables.get("person", {}).get("file"),
             "visits": tables.get("visit_occurrence", {}).get("file")}
    if names != {"persons": "persons.csv", "visits": "visits.csv"}:
        raise MappingError("pilot_config_tables_unexpected")
    manifest = parse_json(verify_small_input(plan["pilot_manifest"],
                                             "pilot_manifest", held))
    if (manifest.get("status") != "complete"
            or manifest.get("run_state") != "finished"
            or manifest.get("promoted") is not True
            or manifest.get("version_label") != plan["pilot_label"]):
        raise MappingError("pilot_manifest_not_complete")
    verify_producer(manifest, plan["pilot_producer"])
    configuration = manifest.get("configuration") or {}
    if configuration.get("sha256") != plan["pilot_config"]["sha256"].lower():
        raise MappingError("pilot_manifest_config_mismatch")
    recorded = {entry.get("file"): entry for entry in manifest.get("files", [])
                if isinstance(entry, dict)}
    columns = {"persons": ("person_id", "subject_id"),
               "visits": ("visit_occurrence_id", "person_id")}
    files = {}
    rows = {}
    for key, name in names.items():
        entry = recorded.get(name)
        path = os.path.join(plan["pilot_dir"], name)
        if (not entry or entry.get("present") is not True
                or not isinstance(entry.get("sha256"), str)
                or not isinstance(entry.get("size_bytes"), int)):
            raise MappingError("pilot_file_not_recorded")
        data, signature = read_verified(path, key, "pilot_file_hash_mismatch",
                                        sha256=entry["sha256"],
                                        size=entry["size_bytes"])
        held.append((path, signature, key))
        rows[key] = parse_rows(data, columns[key])
        files[key] = {"file": name, "path": path,
                      "size_bytes": len(data), "sha256": entry["sha256"]}
    confirm_unchanged(held)
    selection = manifest.get("selection") or {}
    return {"files": files, "rows": rows,
            "cases_requested": selection.get("cases_requested"),
            "coverage": manifest.get("coverage"),
            "limitations": manifest.get("limitations")}


# Graph Metadata (Pinned Graph Read Once)

def torch_loader(path):
    """Deserialise the pinned graph list once on CPU (omopgnn only)."""
    import torch  # Imported lazily: the app environment excludes torch.
    return torch.load(path, map_location="cpu", weights_only=False)


def as_list(value):
    """Return a plain list for a tensor, list or tuple, else None."""
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (list, tuple)):
        return list(value)
    return None


def graph_metadata(graph):
    """Extract only subject_id, y_diagnosis presence and visit hadm_ids.

    Presence mirrors the bridge filter exactly: hasattr(graph, "y_diagnosis"),
    so an attribute whose value is None still qualifies.
    """
    try:
        visit = graph["visit"]
    except (KeyError, TypeError, AttributeError):
        visit = None
    count = getattr(visit, "num_nodes", None) if visit is not None else None
    hadm = getattr(visit, "hadm_ids", None) if visit is not None else None
    return {"subject_id": canonical_int(getattr(graph, "subject_id", None)),
            "has_y_diagnosis": hasattr(graph, "y_diagnosis"),
            "hadm_ids": as_list(hadm),
            "visit_node_count": canonical_int(count)}


def load_graph_metadata(entry, loader):
    """Hash and stat the pinned graph once, then load it once and extract.

    The size is checked and the file hashed before deserialisation; size and
    modification time must be unchanged across hashing and loading.
    """
    before = regular_file_info(entry["path"])
    if before.st_size != entry["size_bytes"]:
        raise MappingError("input_size_mismatch:graph")
    if sha256_file(entry["path"]) != entry["sha256"].lower():
        raise MappingError("input_hash_mismatch:graph")
    hashed = regular_file_info(entry["path"])
    signature = (before.st_size, before.st_mtime_ns)
    if (hashed.st_size, hashed.st_mtime_ns) != signature:
        raise MappingError("graph_changed_during_hash")
    graphs = loader(entry["path"])
    after = regular_file_info(entry["path"])
    if (after.st_size, after.st_mtime_ns) != signature:
        raise MappingError("graph_changed_during_load")
    if not isinstance(graphs, (list, tuple)):
        raise MappingError("graph_collection_not_list")
    return [graph_metadata(graph) for graph in graphs]


def bridge_positions(metadata):
    """Map graph list position to bridge patient_index (y_diagnosis filter)."""
    positions = {}
    for position, item in enumerate(metadata):
        if item["has_y_diagnosis"]:
            positions[position] = len(positions)
    return positions


# Mapping

def index_by(rows, key_column, value_column):
    """Group canonical value_column values by canonical key_column value."""
    index = {}
    for row in rows:
        key = canonical_int(row[key_column])
        if key is not None:
            index.setdefault(key, []).append(canonical_int(row[value_column]))
    return index


def map_visits(record, item, person_id, visit_owners):
    """Link each graph visit-node position to a same-person source visit."""
    hadm_ids = item["hadm_ids"]
    if hadm_ids is None:
        record["findings"].append("visit_hadm_ids_missing")
        return
    if item["visit_node_count"] is None:
        record["findings"].append("visit_node_count_unavailable")
        return
    if len(hadm_ids) != item["visit_node_count"]:
        record["findings"].append("visit_node_count_mismatch")
        return
    if not hadm_ids:
        record["findings"].append("graph_has_no_visit_nodes")
        return
    positions = {}
    for position, raw in enumerate(hadm_ids):
        visit_id = canonical_int(raw)
        owners = visit_owners.get(visit_id, []) if visit_id is not None else []
        if visit_id is None:
            link = "visit_id_not_round_trip"
        elif not owners:
            link = "visit_link_missing"
        elif len(owners) > 1:
            link = "duplicate_source_visit_key"
        elif owners[0] != person_id:
            link = "visit_owner_conflict"
        else:
            link = STATUS_VERIFIED
            positions.setdefault(visit_id, []).append(position)
        if link != STATUS_VERIFIED:
            record["findings"].append(link)
        record["visit_nodes"].append({
            "node_position": position, "hadm_id": visit_id,
            "visit_occurrence_id": visit_id if link == STATUS_VERIFIED
            else None,
            "link_status": link, "repeated_visit_node": False})
    repeated = sorted(key for key, found in positions.items()
                      if len(found) > 1)
    for node in record["visit_nodes"]:
        if node["visit_occurrence_id"] in repeated:
            node["repeated_visit_node"] = True
    record["repeated_visit_occurrence_ids"] = repeated


def map_person(row, counts, graphs, positions, visit_owners):
    """Build one protected mapping record for one selected source person."""
    person_id = canonical_int(row["person_id"])
    subject_id = canonical_int(row["subject_id"])
    record = {"person_id": person_id, "subject_id": subject_id,
              "graph_position": None, "patient_index": None,
              "visit_nodes": [], "repeated_visit_occurrence_ids": [],
              "findings": [], "phase4_eligible": False}
    if person_id is None:
        record["findings"].append("person_id_not_round_trip")
    elif counts["person"][person_id] > 1:
        record["findings"].append("duplicate_source_person_key")
    if subject_id is None:
        record["findings"].append("subject_id_not_round_trip")
    elif counts["subject"][subject_id] > 1:
        record["findings"].append("subject_maps_to_multiple_persons")
    if record["findings"]:
        return record
    matches = graphs.get(subject_id, [])
    if not matches:
        record["findings"].append("graph_match_missing")
    elif len(matches) > 1:
        record["findings"].append("graph_match_multiple")
    elif matches[0] not in positions:
        record["findings"].append("graph_excluded_by_bridge_filter")
    else:
        record["graph_position"] = matches[0]
        record["patient_index"] = positions[matches[0]]
    return record


def build_records(persons, visits, metadata):
    """Return (records, global findings) for every selected source person."""
    counts = {"person": {}, "subject": {}}
    for row in persons:
        for key, column in (("person", "person_id"),
                            ("subject", "subject_id")):
            value = canonical_int(row[column])
            if value is not None:
                counts[key][value] = counts[key].get(value, 0) + 1
    visit_owners = index_by(visits, "visit_occurrence_id", "person_id")
    graphs = {}
    unreadable = 0
    for position, item in enumerate(metadata):
        if item["subject_id"] is None:
            unreadable += 1
        else:
            graphs.setdefault(item["subject_id"], []).append(position)
    positions = bridge_positions(metadata)
    records = []
    for number, row in enumerate(persons, start=1):
        record = map_person(row, counts, graphs, positions, visit_owners)
        record["source_row"] = number
        if record["patient_index"] is not None:
            map_visits(record, metadata[record["graph_position"]],
                       record["person_id"], visit_owners)
        if record["findings"]:
            record["identity_status"] = STATUS_QUARANTINED
        elif record["repeated_visit_occurrence_ids"]:
            record["identity_status"] = STATUS_REPEATED
        else:
            record["identity_status"] = STATUS_VERIFIED
        records.append(record)
    findings = {
        "graph_subject_not_round_trip": unreadable,
        "source_visit_id_not_round_trip": sum(
            1 for row in visits
            if canonical_int(row["visit_occurrence_id"]) is None),
        "duplicate_source_visit_keys": sum(
            1 for owners in visit_owners.values() if len(owners) > 1),
        "source_visit_owner_not_selected": sum(
            1 for owners in visit_owners.values()
            for owner in owners if owner not in counts["person"])}
    return records, findings


def summarise(records, metadata, positions, findings):
    """Return aggregate status, counts and finding-code counts only."""
    statuses = [record["identity_status"] for record in records]
    codes = {}
    for record in records:
        for code in record["findings"]:
            codes[code] = codes.get(code, 0) + 1
    quarantined = statuses.count(STATUS_QUARANTINED)
    return {
        "status": ("complete_with_quarantine" if quarantined
                   else "complete"),
        "persons_in_scope": len(records),
        "records_written": len(records),
        "verified": statuses.count(STATUS_VERIFIED),
        "verified_with_repeated_visit_nodes": statuses.count(STATUS_REPEATED),
        "quarantined": quarantined,
        "phase4_eligible_records": 0,
        "graph_collection_size": len(metadata),
        "bridge_filtered_size": len(positions),
        "visit_nodes_verified": sum(
            1 for record in records for node in record["visit_nodes"]
            if node["link_status"] == STATUS_VERIFIED),
        "repeated_visit_node_positions": sum(
            1 for record in records for node in record["visit_nodes"]
            if node["repeated_visit_node"]),
        "record_finding_counts": dict(sorted(codes.items())),
        "global_finding_counts": dict(sorted(findings.items()))}


def build_payload(plan, pilot, records, summary):
    """Assemble the protected sidecar manifest (IDs appear only here)."""
    inputs = {name: {"path": plan[name]["path"],
                     "sha256": plan[name]["sha256"].lower()}
              for name in ("graph", "builder", "bridge", "pilot_config",
                           "pilot_reader", "pilot_manifest")}
    inputs["graph"]["size_bytes"] = plan["graph"]["size_bytes"]
    inputs["pilot_files"] = pilot["files"]
    inputs["pilot_reader"]["role"] = ("current approved Step 0.4 source-pilot "
                                      "verifier, hashed on disk")
    inputs["pilot_historical_producer"] = {
        "version": plan["pilot_producer"]["version"],
        "sha256": plan["pilot_producer"]["sha256"].lower(),
        "role": ("historical Step 0.3.2 extraction producer recorded in the "
                 "pilot manifest; distinct from the current verifier")}
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "step": "17", "script_version": VERSION,
        "inputs": inputs,
        "scope": {
            "source_pilot_version": plan["pilot_label"],
            "persons_in_scope": len(records),
            "statement": ("Exactly one record per selected person of the "
                          "development-only source pilot. Not the whole "
                          "graph cohort and not a final evaluation cohort."),
            "not_established": [
                "permitted history", "prediction target", "input boundary",
                "source dataset/ETL version", "graph preprocessing parity",
                "label ordering", "upstream split membership",
                "source event linkage", "Phase 4 eligibility"]},
        "join_rules": {
            "patient_index": ("position in the pinned graph list after "
                              "keeping graphs with y_diagnosis, in stored "
                              "order"),
            "person": ("graph subject_id == persons.csv subject_id by strict "
                       "decimal integer round-trip; exactly one person_id"),
            "visit": ("graph visit hadm_id at each node position == "
                      "visits.csv visit_occurrence_id with the same "
                      "person_id"),
            "repeated_visit_nodes": ("several node positions resolving to "
                                     "one unique same-person source visit "
                                     "are preserved and flagged"),
            "never_used": ["visit_index", "dates", "hadm_source_id",
                           "concept ids", "list positions alone",
                           "guessed offsets"]},
        "source_snapshot_limitation": pilot["coverage"],
        "source_pilot_limitations": pilot["limitations"],
        "summary": summary,
        "phase4_eligible": False,
        "records": records}


def write_output(plan, payload):
    """Write the sidecar atomically via a partial directory; never overwrite.

    Returns the SHA-256 of the written mapping file.
    """
    output_preconditions(plan)
    output_dir = plan["output_dir"]
    partial = output_dir + PARTIAL
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode(
        "utf-8")
    os.makedirs(os.path.dirname(output_dir), exist_ok=True)
    os.mkdir(partial)
    with open(os.path.join(partial, OUTPUT_NAME), "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.rename(partial, output_dir)
    return hashlib.sha256(data).hexdigest()


def build_mapping(plan, loader=torch_loader):
    """Validate every input, build the crosswalk and write the sidecar.

    Returns (summary, mapping SHA-256). Raises MappingError before any write
    when an input identity, path or precondition fails. Every small input is
    read once, parsed from its verified bytes and confirmed unchanged
    immediately before the graph loader is called.
    """
    output_preconditions(plan)
    held = []
    for name in ("builder", "bridge", "pilot_reader"):
        verify_small_input(plan[name], name, held)
    pilot = verify_pilot(plan, held)
    persons = pilot["rows"]["persons"]
    visits = pilot["rows"]["visits"]
    if not persons or len(persons) != pilot["cases_requested"]:
        raise MappingError("pilot_person_count_mismatch")
    confirm_unchanged(held)
    metadata = load_graph_metadata(plan["graph"], loader)
    records, findings = build_records(persons, visits, metadata)
    summary = summarise(records, metadata, bridge_positions(metadata),
                        findings)
    payload = build_payload(plan, pilot, records, summary)
    return summary, write_output(plan, payload)


def report(summary, digest):
    """Print status, aggregate counts and finding codes only."""
    for key in ("persons_in_scope", "records_written", "verified",
                "verified_with_repeated_visit_nodes", "quarantined",
                "phase4_eligible_records", "graph_collection_size",
                "bridge_filtered_size", "visit_nodes_verified",
                "repeated_visit_node_positions"):
        print("%-36s %s" % (key, summary[key]))
    for group in ("record_finding_counts", "global_finding_counts"):
        for code, count in summary[group].items():
            print("finding %-28s %s" % (code, count))
    print("mapping_sha256 %s" % digest)
    print("Identifiers are written only to the protected sidecar.")
    print("RESULT: %s" % summary["status"].upper())


def main(argv=None):
    """Run the operator-only real mapping; takes no arguments."""
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print("FAILED: this command takes no arguments")
        return 2
    try:
        summary, digest = build_mapping(real_plan(), torch_loader)
    except MappingError as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - message may carry values
        print("FAILED: %s (message withheld)" % type(error).__name__)
        return 1
    report(summary, digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
