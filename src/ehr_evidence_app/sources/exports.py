#!/usr/bin/env python3
"""Stage and audit the existing core OMOP CSV exports (Step 0.3.1).

Standard library only. Copies the five selected exports once each into an
explicit version directory under the protected workspace, hashing the source
bytes as they stream, re-reading each destination separately to verify size and
SHA256, and checking the source stat before and after the copy. A verified
destination is reused instead of recopied; an existing destination that does
not match is never overwritten. A failed copy stays under a .partial name in
the version directory, and the manifest is replaced with an in-progress record
before any staging starts and updated as each table finishes, so an interrupted
run can never look complete or leave an earlier manifest looking current.

Staged copies are then profiled with csv.DictReader: required headers,
actual parsed row counts and counts of rows missing required identifiers.
Duplicate rows are preserved and counted as they appear. No database
connection, download or source modification. Console output carries table
names, status and aggregate counts only, never identifiers or row content.

Before anything is created or copied, the source directory and every planned
destination (version directory, staged files, manifest and their .partial
names) pass the destination checks in ehr_evidence_app.storage.policy.

A re-run against an existing version re-verifies it and rewrites its
manifest, so --version-label is required: there is no default that could
reach a published, immutable version.

Usage:
    python -m ehr_evidence_app.sources.exports --version-label LABEL
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import os
import sys

from ehr_evidence_app.storage import policy as guard

VERSION = "0.3.1"
REPO_ROOT = guard.REPO_ROOT
DEFAULT_CONFIG = os.path.join(REPO_ROOT, "config", "source_exports.json")


def load_config(path):
    """Read the staging configuration from disk."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def now_utc():
    """Return the current UTC time as an ISO string."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


def sha256_file(path, chunk_bytes):
    """Return the SHA256 of a file read in bounded chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk_bytes), b""):
            digest.update(block)
    return digest.hexdigest()


def stat_snapshot(path):
    """Return size, modification time in ns and an ISO time for a file."""
    info = os.stat(path)
    moment = datetime.datetime.fromtimestamp(info.st_mtime,
                                             datetime.timezone.utc)
    return {"size": info.st_size, "mtime_ns": info.st_mtime_ns,
            "modified_utc": moment.isoformat(timespec="seconds")}


def copy_with_hash(source, partial, chunk_bytes):
    """Stream the source into a partial file, hashing the bytes as read."""
    digest = hashlib.sha256()
    written = 0
    with open(source, "rb") as reader, open(partial, "wb") as writer:
        for block in iter(lambda: reader.read(chunk_bytes), b""):
            digest.update(block)
            writer.write(block)
            written += len(block)
        writer.flush()
        os.fsync(writer.fileno())
    return written, digest.hexdigest()


def profile_csv(path, required_headers, identity_fields):
    """Summarise headers, parsed rows and missing identities in a staged copy.

    Returns counts only: no field value, identifier or row leaves this
    function. Duplicate rows are counted as they appear.
    """
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        present = [f for f in identity_fields if f in headers]
        missing_by_field = {field: 0 for field in present}
        rows = 0
        rows_missing = 0
        for row in reader:
            rows += 1
            blank = [f for f in present if not (row.get(f) or "").strip()]
            for field in blank:
                missing_by_field[field] += 1
            if blank:
                rows_missing += 1
    return {"headers": headers,
            "missing_required_headers": [f for f in required_headers
                                         if f not in headers],
            "identity_fields_absent": [f for f in identity_fields
                                       if f not in headers],
            "rows_parsed": rows,
            "rows_missing_identity": rows_missing,
            "missing_identity_by_field": missing_by_field}


def sanitise_error(error):
    """Describe a failure without repeating file content or field values."""
    if isinstance(error, OSError):
        return "%s: %s" % (type(error).__name__, error.strerror or "OS error")
    return "%s (message withheld to avoid exposing file content)" % type(
        error).__name__


def is_within(path, root, strict=False):
    """Return True when path is under root, strictly beneath it when asked."""
    key = os.path.normcase(os.path.abspath(path))
    base = os.path.normcase(os.path.abspath(root))
    if key.startswith(base + os.sep):
        return True
    return not strict and key == base


def is_safe_component(value):
    """Return True when value is a single safe relative path component."""
    if not value or value in (".", "..") or value.lower() == ".git":
        return False
    if os.path.isabs(value) or ":" in value:
        return False
    if "/" in value or "\\" in value:
        return False
    return os.path.basename(value) == value


def destination_problem(version_dir, source_dir, config):
    """Return a reason string when the destination is unsafe to create."""
    root = config["policy"]["required_destination_root"]
    if not is_within(version_dir, root, strict=True):
        return "destination is not inside %s" % root
    if is_within(version_dir, REPO_ROOT):
        return "destination is inside the source repository"
    if os.path.normcase(version_dir) == os.path.normcase(source_dir):
        return "source and destination are the same directory"
    return None


def planned_destinations(config, version_dir):
    """Return every path staging may create or write, with its kind.

    Covers the version directory, each staged file, the manifest and the
    .partial name of each, so all are checked before anything is written.
    """
    entries = [(version_dir, "directory")]
    names = [config["manifest_name"]] + [
        spec["file"] for spec in config["tables"].values()]
    for name in names:
        path = os.path.join(version_dir, name)
        entries += [(path, "file"), (path + ".partial", "file")]
    return entries


def preflight_problem(config, raw_source, raw_root, version_dir):
    """Return why staging must not start; checked before any write or copy.

    The configured roots must stay inside the fixed private root, the source
    must be the legacy read-only export or lie under the policy anchor, the
    destination root must be a plain drive path, and every planned
    destination must pass the Step 0.4 destination policy.
    """
    for label, value in (
            ("policy.required_destination_root",
             config["policy"]["required_destination_root"]),
            ("destination_root", config["destination_root"])):
        problem = guard.configured_root_problem(value, label)
        if problem:
            return problem
    problem = guard.source_directory_problem(raw_source)
    if problem:
        return problem
    problem = guard.path_problem(raw_root)
    if problem:
        return "destination root: %s" % problem
    return guard.destinations_problem(planned_destinations(config,
                                                           version_dir))


def pending_record(name, spec, reason):
    """Return a record for a table that has not been staged successfully."""
    return {"table": name, "file": spec["file"], "action": "none",
            "copy_status": "unverified", "status": "incomplete",
            "reason": reason}


def verify_existing(record, source, destination, before, previous, chunk):
    """Verify an existing destination against the source without rewriting it.

    Re-reads the destination from disk every time. The source is re-hashed
    unless a prior verified manifest record matches its absolute path, size
    and modification time. The source is re-stat'ed afterwards, so a source
    that changes while it is being verified fails instead of being reused.
    """
    expected = None
    if (previous and previous.get("copy_status") == "verified"
            and previous.get("source_path")
            and os.path.normcase(previous["source_path"]) == os.path.normcase(
                record["source_path"])
            and previous.get("source_size_bytes") == before["size"]
            and previous.get("source_modified_ns") == before["mtime_ns"]):
        expected = previous.get("source_sha256")
    record["source_reread"] = expected is None
    if expected is None:
        expected = sha256_file(source, chunk)
    record.update({"source_sha256": expected,
                   "destination_sha256": sha256_file(destination, chunk),
                   "destination_size_bytes": os.path.getsize(destination),
                   "action": "reused"})
    after = stat_snapshot(source)
    if (after["size"], after["mtime_ns"]) != (before["size"],
                                              before["mtime_ns"]):
        record["action"] = "kept-unchanged"
        record["reason"] = ("source changed while it was being verified; "
                            "nothing was written")
        return False
    if (record["destination_size_bytes"] != before["size"]
            or record["destination_sha256"] != expected):
        record["action"] = "kept-unchanged"
        record["reason"] = ("existing destination does not match the source; "
                            "left unchanged for inspection")
        return False
    return True


def copy_source(record, source, destination, before, chunk):
    """Copy into a partial file and promote it only once fully verified."""
    partial = destination + ".partial"
    written, source_hash = copy_with_hash(source, partial, chunk)
    after = stat_snapshot(source)
    record.update({"source_sha256": source_hash, "source_reread": True,
                   "action": "copied",
                   "partial_path": os.path.abspath(partial)})
    if (after["size"], after["mtime_ns"]) != (before["size"],
                                              before["mtime_ns"]):
        record["reason"] = "source changed during the copy; partial kept"
        return False
    record.update({"destination_sha256": sha256_file(partial, chunk),
                   "destination_size_bytes": os.path.getsize(partial)})
    if (written != before["size"]
            or record["destination_size_bytes"] != before["size"]):
        record["reason"] = "copied size does not match source; partial kept"
        return False
    if record["destination_sha256"] != source_hash:
        record["reason"] = "copied checksum does not match; partial kept"
        return False
    os.replace(partial, destination)
    record.pop("partial_path")
    return True


def stage_table(name, spec, options, previous):
    """Stage one export, reusing a verified copy or making a verified one."""
    source = os.path.join(options["source_dir"], spec["file"])
    destination = os.path.join(options["version_dir"], spec["file"])
    chunk = options["chunk_bytes"]
    before = stat_snapshot(source)
    record = {"table": name, "file": spec["file"],
              "source_path": os.path.abspath(source),
              "destination_path": os.path.abspath(destination),
              "source_size_bytes": before["size"],
              "source_modified_utc": before["modified_utc"],
              "source_modified_ns": before["mtime_ns"],
              "action": "none", "copy_status": "unverified",
              "status": "incomplete"}
    if os.path.normcase(record["source_path"]) == os.path.normcase(
            record["destination_path"]):
        record["reason"] = "source and destination are the same file"
        return record
    if os.path.exists(destination):
        verified = verify_existing(record, source, destination, before,
                                   previous, chunk)
    else:
        verified = copy_source(record, source, destination, before, chunk)
    if not verified:
        return record

    record["copy_status"] = "verified"
    record.update(profile_csv(destination, spec["required_headers"],
                              spec["required_identity_fields"]))
    if record["missing_required_headers"]:
        record["reason"] = "required headers missing from the staged copy"
    else:
        record["status"] = "complete"
    return record


def load_previous(manifest_path):
    """Return the previous manifest records keyed by table name."""
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError):
        return {}
    return {entry.get("table"): entry for entry in manifest.get("tables", [])}


def write_manifest(manifest_path, manifest):
    """Write the manifest through a partial file in the version directory."""
    partial = manifest_path + ".partial"
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2)
        handle.write("\n")
    os.replace(partial, manifest_path)


def report(manifest, manifest_path):
    """Print table status and aggregate counts only, never row content."""
    for record in manifest["tables"]:
        print("%-20s %-10s %-14s rows=%-10s missing_identity_rows=%s"
              % (record["table"], record["status"], record["action"],
                 record.get("rows_parsed", "n/a"),
                 record.get("rows_missing_identity", "n/a")))
        if record["status"] != "complete":
            print("    reason: %s" % record.get("reason", "failed"))
    totals = manifest["totals"]
    print("")
    print("Version directory : %s" % manifest["version_directory"])
    print("Manifest          : %s" % manifest_path)
    print("Run state         : %s" % manifest["run_state"])
    print("Tables complete   : %d of %d" % (totals["tables_complete"],
                                            totals["tables"]))
    print("Bytes verified    : %d" % totals["bytes_verified"])
    print("Rows parsed       : %d" % totals["rows_parsed"])
    print("Rows missing ids  : %d" % totals["rows_missing_identity"])
    print("Integrity is verified against the existing export only. Source "
          "completeness is NOT established; see the manifest.")
    print("RESULT: %s" % manifest["status"].upper())


def build_parser():
    """Build the command-line parser for the staging tool."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--source-dir", help="override the configured source")
    parser.add_argument("--dest-root", help="override the destination root")
    parser.add_argument("--version-label", required=True,
                        help="version directory: a new label, or an "
                        "existing unpublished one to re-verify")
    return parser


def build_manifest(meta, records, run_state):
    """Assemble the manifest for the current run state.

    The status is 'complete' only when the run finished and every table
    reached 'complete'; an in-progress or interrupted run is never complete.
    """
    done = [r for r in records if r["status"] == "complete"]
    finished = run_state == "finished" and len(done) == len(records)
    manifest = {"step": "0.3.1", "generated_utc": now_utc(),
                "run_state": run_state,
                "status": "complete" if finished else "incomplete"}
    manifest.update(meta)
    manifest["totals"] = {
        "tables": len(records), "tables_complete": len(done),
        "bytes_verified": sum(r.get("destination_size_bytes", 0)
                              for r in done),
        "rows_parsed": sum(r.get("rows_parsed", 0) for r in done),
        "rows_missing_identity": sum(r.get("rows_missing_identity", 0)
                                     for r in done)}
    manifest["tables"] = records
    return manifest


def run(args):
    """Stage every configured export and return a process exit code.

    Source and every destination path are checked before the version
    directory is created or any source file is opened.
    """
    config = load_config(args.config)
    chunk = config["chunk_bytes"]
    raw_source = args.source_dir or config["source_dir"]
    source_dir = os.path.abspath(raw_source)
    label = args.version_label
    manifest_name = config["manifest_name"]
    names = [spec["file"] for spec in config["tables"].values()]
    for component in [label, manifest_name] + names:
        if not is_safe_component(component):
            print("FAILED: unsafe path component in the configured selection")
            return 1
    raw_root = args.dest_root or config["destination_root"]
    version_dir = os.path.abspath(os.path.join(raw_root, label))
    problem = (preflight_problem(config, raw_source, raw_root, version_dir)
               or destination_problem(version_dir, source_dir, config))
    if problem:
        print("FAILED: %s" % problem)
        return 1

    os.makedirs(version_dir, exist_ok=True)
    manifest_path = os.path.join(version_dir, manifest_name)
    script = os.path.abspath(__file__)
    meta = {
        "version_label": label, "version_directory": version_dir,
        "source_directory": source_dir,
        "preparation_script": {"path": script, "version": VERSION,
                               "sha256": sha256_file(script, chunk)},
        "configuration": {"path": os.path.abspath(args.config),
                          "sha256": sha256_file(args.config, chunk)},
        "export_sql_reference": config["export_sql_reference"],
        "known_export_semantics": config["known_export_semantics"],
        "completeness": config["completeness"]}

    # Keep the prior records in memory for verified reuse, then replace the
    # stored manifest immediately so an interrupted rerun cannot leave an
    # earlier complete manifest looking current.
    previous = load_previous(manifest_path)
    records = [pending_record(name, spec, "not started")
               for name, spec in config["tables"].items()]
    write_manifest(manifest_path, build_manifest(meta, records, "in-progress"))

    options = {"source_dir": source_dir, "version_dir": version_dir,
               "chunk_bytes": chunk}
    run_state = "finished"
    for index, (name, spec) in enumerate(config["tables"].items()):
        try:
            records[index] = stage_table(name, spec, options,
                                         previous.get(name))
        except KeyboardInterrupt:
            records[index] = pending_record(
                name, spec, "interrupted before completion")
            run_state = "interrupted"
        except Exception as error:  # sanitised: text may quote file content
            records[index] = pending_record(name, spec, sanitise_error(error))
        state = "in-progress" if run_state == "finished" else run_state
        write_manifest(manifest_path, build_manifest(meta, records, state))
        if run_state == "interrupted":
            break

    manifest = build_manifest(meta, records, run_state)
    write_manifest(manifest_path, manifest)
    report(manifest, manifest_path)
    return 0 if manifest["status"] == "complete" else 1


def main(argv=None):
    """Run the staging tool, sanitising any top-level failure."""
    try:
        return run(build_parser().parse_args(argv))
    except KeyboardInterrupt:
        print("FAILED: interrupted before completion")
        return 1
    except Exception as error:  # sanitised: text may quote file content
        print("FAILED: %s" % sanitise_error(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
