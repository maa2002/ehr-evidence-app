#!/usr/bin/env python3
"""Bounded read-only OMOP source pilot extraction (Step 0.3.2).

Standard library only. Supplements the filtered legacy exports of Step 0.3.1
with native event identifiers and verified selected-person coverage.

A deterministic development-only pilot is selected: the first N person_id
values, ordered numerically, among persons with at least two distinct visits
of concept 9201, 9203 or 262. N defaults to 10 and is an engineering choice,
not a final evaluation cohort and not a clinical quality target. For exactly
those persons, every visit and every condition, procedure and drug row is
extracted with its native primary key. No visit-type, date or nonzero-concept
filter is applied to the extracted rows; null visit links and repeated events
are preserved.

Every extract, every independent row count and the observed transaction
metadata run in ONE repeatable-read read-only transaction, driven by a single
generated SQL file executed with psql -X -w and ON_ERROR_STOP. Read-only is
forced twice: PGOPTIONS sets default_transaction_read_only and the transaction
itself is opened READ ONLY. The observed settings are read back and checked.
There is no DDL, no DML, no server-side output file and no raw database file
access; psql \\copy writes client-side into the protected version directory
in the workspace only.

Credentials come from the process environment (PGHOST, PGPORT, PGDATABASE,
PGUSER, PGPASSWORD). No credential file is opened and no credential value is
printed, logged, passed as an argument or stored in a manifest. Console output
carries status, table names and aggregate counts only: never a person_id, a
subject_id, a row value or raw psql output.

Destination checks (ehr_evidence_app.storage.policy) run before any write
or connection: every destination in the version directory, including
.partial names, the
source policy in the configuration, the connection settings (PGHOSTADDR,
service and conninfo redirects are refused) and the psql path. psql runs
from an allow-listed environment whose PGOPTIONS is always the forced one.

A case set of ehr_evidence_app.casesets.registry is extracted the same way,
with one difference: the cohort is exactly the subjects of the case set's
pinned selection record (matched on the person table's identity source
value), and the version is written under the case-set snapshot root with the
case-set label. The manifest records the selection record's digest and the
case set's own purpose statement.

Usage:
    python -m ehr_evidence_app.sources.pilot [--version-label LABEL] [--cases N]
    python -m ehr_evidence_app.sources.pilot --verify-only [--version-label L]
    python -m ehr_evidence_app.sources.pilot --case-set LABEL [--verify-only]
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

from ehr_evidence_app.casesets import registry
from ehr_evidence_app.storage import policy as guard

VERSION = "0.3.2"
REPO_ROOT = guard.REPO_ROOT
DEFAULT_CONFIG = os.path.join(REPO_ROOT, "config", "source_pilot.json")
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CREDENTIAL_FILE_VARS = ("PGPASSFILE", "PGSERVICE", "PGSERVICEFILE")
PARTIAL = ".partial"
CONCEPT_KEY = "referenced_concepts"
COHORT_KEY = "selected_persons"

# Findings that are preserved and reported but are not validation failures.
TOLERATED_FINDINGS = ("null_visit_links", "missing_labels")
FAILING_FINDINGS = ("blank_primary_keys", "duplicate_primary_keys",
                    "blank_person_ids", "rows_person_not_selected",
                    "visit_links_unresolved", "visit_person_mismatches",
                    "blank_identity_values", "duplicate_identity_values")


def read_json(path):
    """Read a JSON document from disk."""
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def load_config(path):
    """Read the pilot configuration from disk."""
    return read_json(path)


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


def is_identifier(value):
    """Return True for a plain lower-case SQL identifier."""
    return bool(isinstance(value, str) and IDENTIFIER.match(value))


def sanitise_error(error):
    """Describe a failure without repeating file content or field values."""
    if isinstance(error, OSError):
        return "%s: %s" % (type(error).__name__, error.strerror or "OS error")
    return "%s (message withheld to avoid exposing row content)" % type(
        error).__name__


def field(row, column):
    """Return a stripped field value, treating a missing column as blank."""
    return (row.get(column) or "").strip()


# --------------------------------------------------------------------------
# Configuration and destination checks
# --------------------------------------------------------------------------

def all_specs(config):
    """Return every table spec, including the referenced-concept extract."""
    specs = list(config["tables"].items())
    specs.append((CONCEPT_KEY, config["concept_table"]))
    return specs


def output_names(spec):
    """Return the output column names expected in an extracted CSV."""
    return [out for _, out in spec["columns"]]


def config_problem(config):
    """Return a reason string when the configuration is unsafe to use.

    Every name interpolated into SQL must be a plain identifier, and every
    validation column must actually be produced by the extract, so a mistyped
    configuration fails here instead of silently skipping a check.
    """
    names = [config["schema"]]
    for _, spec in all_specs(config):
        names.append(spec["source_table"])
        names.extend(spec.get("order_by", []))
        names.extend(spec.get("concept_columns", []))
        for source, out in spec["columns"]:
            names.extend([source, out])
        outputs = output_names(spec)
        for key in ("primary_key", "person_column", "visit_column",
                    "identity_column", "label_column"):
            column = spec.get(key)
            if column is None:
                continue
            names.append(column)
            if column not in outputs:
                return "configured %s is not an extracted column" % key
    for name in names:
        if not is_identifier(name):
            return "configuration contains an unsafe SQL identifier"
    for value in config["visit_concept_ids"]:
        if not isinstance(value, int) or isinstance(value, bool):
            return "visit_concept_ids must be integers"
    if not isinstance(config["min_qualifying_visits"], int):
        return "min_qualifying_visits must be an integer"
    return None


def cases_problem(config, cases):
    """Return a reason string when the requested case count is out of range."""
    if not isinstance(cases, int) or isinstance(cases, bool):
        return "case count must be an integer"
    low = int(config["cases_min"])
    high = int(config["cases_max"])
    if cases < low or cases > high:
        return "case count must be between %d and %d" % (low, high)
    return None


def written_names(config):
    """Return every fixed file name this tool may write in a version folder."""
    names = [spec["file"] for _, spec in all_specs(config)]
    names += [config["counts_file"], config["metadata_file"],
              config["sql_name"], config["stdout_log_name"],
              config["stderr_log_name"], config["manifest_name"],
              config["verification_report_name"]]
    return names


def data_names(config):
    """Return the extracted CSV names promoted only after validation."""
    return [spec["file"] for _, spec in all_specs(config)] + [
        config["counts_file"], config["metadata_file"]]


def hashed_names(config):
    """Return every completed file that is hashed and sized in the manifest."""
    return data_names(config) + [config["sql_name"],
                                 config["stdout_log_name"],
                                 config["stderr_log_name"]]


def destination_problem(version_dir, config):
    """Return a reason string when the destination is unsafe to use."""
    root = config["policy"]["required_destination_root"]
    if not is_within(version_dir, root, strict=True):
        return "destination is not inside %s" % root
    if is_within(version_dir, REPO_ROOT):
        return "destination is inside the source repository"
    if "'" in version_dir or '"' in version_dir:
        return "destination path contains a quote character"
    return None


def planned_destinations(config, version_dir):
    """Return every path this tool may create or write, with its kind.

    Covers the version directory and every fixed file name, each with its
    .partial name, so extraction and verify-only check all of them, existing
    or not, before anything is written.
    """
    entries = [(version_dir, "directory")]
    for name in written_names(config):
        path = os.path.join(version_dir, name)
        entries += [(path, "file"), (path + PARTIAL, "file")]
    return entries


def source_config_problem(config):
    """Return a reason string when the configuration relaxes fixed policy.

    Configured destination roots must stay inside the fixed private root,
    and the configured hosts, database and timeouts must not widen the fixed
    read-only source policy.
    """
    policy = config["policy"]
    for label, value in (
            ("policy.required_destination_root",
             policy["required_destination_root"]),
            ("destination_root", config["destination_root"])):
        problem = guard.configured_root_problem(value, label)
        if problem:
            return problem
    timeouts = {name: config[name] for name in (
        "statement_timeout_ms", "lock_timeout_ms",
        "idle_in_transaction_timeout_ms", "connect_timeout_seconds",
        "subprocess_timeout_seconds")}
    return guard.source_policy_problem(policy["allowed_hosts"],
                                       policy["required_database"], timeouts)


def connection_problem(config, psql_path):
    """Return a reason string when the environment is unsafe to connect with.

    Refuses redirect variables (PGHOSTADDR, PGSERVICE, PGSERVICEFILE), a
    non-loopback PGHOST, a PGDATABASE that is not the plain required name,
    an out-of-range PGPORT, missing PGUSER/PGPASSWORD and an unsafe psql
    path. Only the presence of PGPASSWORD is examined; its value is never
    read into a report, an argument or a log.
    """
    policy = config["policy"]
    return (guard.connection_problem(os.environ, policy["allowed_hosts"],
                                     policy["required_database"])
            or guard.executable_problem(psql_path))


# --------------------------------------------------------------------------
# SQL generation: one transaction, one shared cohort CTE, client-side \copy
# --------------------------------------------------------------------------

def qualified(config, table):
    """Return the schema-qualified name of a source table."""
    return "%s.%s" % (config["schema"], table)


def identity_source_column(config):
    """Return the person-table source column behind the identity column."""
    spec = config["tables"]["person"]
    for source, out in spec["columns"]:
        if out == spec["identity_column"]:
            return source
    raise ValueError("the person table has no identity source column")


def cohort_cte(config, cases, subjects=None):
    """Return the cohort CTE shared by every statement.

    Without subjects this is the deterministic development-pilot rule. With
    subjects (a case-set selection) the cohort is exactly the persons whose
    identity source value is one of the subject identifiers; each must be a
    strict positive integer and is rendered as quoted decimal text, so no
    other text can reach the SQL.
    """
    if subjects is not None:
        if (not isinstance(subjects, tuple) or not subjects
                or len(subjects) != cases
                or len(set(subjects)) != len(subjects)
                or not all(type(value) is int and value > 0
                           for value in subjects)):
            raise ValueError("the explicit cohort is invalid")
        values = ", ".join("'%d'" % value for value in subjects)
        return ("WITH cohort AS (SELECT p.person_id FROM %s p WHERE "
                "p.%s IN (%s))"
                % (qualified(config, config["tables"]["person"][
                    "source_table"]), identity_source_column(config),
                   values))
    concepts = ", ".join(str(int(v)) for v in config["visit_concept_ids"])
    return ("WITH cohort AS (SELECT v.person_id FROM %s v WHERE "
            "v.visit_concept_id IN (%s) GROUP BY v.person_id HAVING "
            "count(DISTINCT v.visit_occurrence_id) >= %d ORDER BY "
            "v.person_id LIMIT %d)"
            % (qualified(config, "visit_occurrence"), concepts,
               int(config["min_qualifying_visits"]), int(cases)))


def person_filter(spec):
    """Return the unjoined selected-person predicate for a source table."""
    return "t.%s IN (SELECT person_id FROM cohort)" % spec["person_column"]


def table_query(config, spec, cases, subjects=None):
    """Return the extraction query for one source table.

    The cohort is applied as a semi-join predicate, never as a join, so the
    extracted row count cannot multiply or drop source rows.
    """
    columns = ", ".join("t.%s AS %s" % (source, out)
                        for source, out in spec["columns"])
    order = ", ".join("t.%s" % column for column in spec["order_by"])
    return ("%s SELECT %s FROM %s t WHERE %s ORDER BY %s"
            % (cohort_cte(config, cases, subjects), columns,
               qualified(config, spec["source_table"]), person_filter(spec),
               order))


def referenced_cte(config):
    """Return the CTE listing every concept id referenced by pilot rows."""
    branches = []
    for _, spec in config["tables"].items():
        for column in spec.get("concept_columns", []):
            branches.append(
                "SELECT DISTINCT t.%s AS concept_id FROM %s t WHERE %s AND "
                "t.%s IS NOT NULL"
                % (column, qualified(config, spec["source_table"]),
                   person_filter(spec), column))
    return "referenced AS (%s)" % " UNION ".join(branches)


def concept_query(config, cases, subjects=None):
    """Return the referenced-concept metadata query.

    A LEFT JOIN on the concept primary key keeps every referenced id exactly
    once: metadata that is absent stays explicitly blank instead of removing
    the concept or the events that reference it.
    """
    spec = config["concept_table"]
    columns = ["r.concept_id AS concept_id"]
    columns += ["c.%s AS %s" % (source, out)
                for source, out in spec["columns"][1:]]
    return ("%s, %s SELECT %s FROM referenced r LEFT JOIN %s c ON "
            "c.concept_id = r.concept_id ORDER BY r.concept_id"
            % (cohort_cte(config, cases, subjects), referenced_cte(config),
               ", ".join(columns), qualified(config, spec["source_table"])))


def counts_query(config, cases, subjects=None):
    """Return the independent row counts over unjoined source tables."""
    parts = ["(SELECT count(*) FROM cohort) AS %s" % COHORT_KEY]
    for name, spec in config["tables"].items():
        parts.append("(SELECT count(*) FROM %s t WHERE %s) AS %s"
                     % (qualified(config, spec["source_table"]),
                        person_filter(spec), name))
    parts.append("(SELECT count(*) FROM referenced) AS %s" % CONCEPT_KEY)
    return ("%s, %s SELECT %s" % (cohort_cte(config, cases, subjects),
                                  referenced_cte(config), ", ".join(parts)))


def metadata_query():
    """Return the observed database, server and transaction settings.

    pg_current_snapshot() identifies the snapshot every later statement in
    this transaction observes and is a pure read: unlike pg_export_snapshot()
    it writes no server-side snapshot file. pg_current_xact_id_if_assigned()
    is recorded as observed metadata only; PostgreSQL can assign a transaction
    id without any update, so it is neither proof of a write nor of its
    absence. current_schema() is the session search-path default, not the
    queried schema: every query is qualified with the configured schema.
    """
    return ("SELECT current_database() AS database_name, current_schema() AS "
            "current_schema, current_setting('server_version') AS "
            "server_version, current_setting('server_version_num') AS "
            "server_version_num, version() AS server_version_full, "
            "current_setting('transaction_isolation') AS "
            "transaction_isolation, current_setting('transaction_read_only') "
            "AS transaction_read_only, "
            "current_setting('default_transaction_read_only') AS "
            "default_transaction_read_only, "
            "current_setting('statement_timeout') AS statement_timeout, "
            "pg_current_snapshot()::text AS snapshot_id, "
            "pg_current_xact_id_if_assigned()::text AS assigned_xact_id, "
            "to_char(now() AT TIME ZONE 'UTC', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS snapshot_time_utc")


def copy_line(query, path):
    r"""Return a one-line client-side \copy writing into a protected file."""
    return ("\\copy (%s) TO '%s' WITH (FORMAT csv, HEADER true, "
            "ENCODING 'UTF8')" % (query, path.replace("\\", "/")))


def copy_targets(config, version_dir, suffix):
    """Map each extract to its protected client-side output path."""
    targets = {name: os.path.join(version_dir, spec["file"] + suffix)
               for name, spec in all_specs(config)}
    targets[config["counts_file"]] = os.path.join(
        version_dir, config["counts_file"] + suffix)
    targets[config["metadata_file"]] = os.path.join(
        version_dir, config["metadata_file"] + suffix)
    return targets


def build_sql(config, cases, targets, subjects=None):
    """Assemble the single read-only transaction executed by psql.

    Statement order matters: the metadata select runs first, so the recorded
    snapshot identifier is the snapshot that every later extract and count in
    this repeatable-read transaction observes.
    """
    lines = [
        "-- Step 0.3.2 bounded read-only OMOP source pilot. Generated file.",
        "-- Read-only: no DDL, no DML, no server-side output file and no",
        "-- raw database file access. Every \\copy below is client-side and",
        "-- writes into the protected version directory in the workspace.",
        "-- All statements share one repeatable-read read-only transaction",
        "-- and one deterministic cohort CTE.",
        "\\set ON_ERROR_STOP on",
        "\\pset pager off",
        "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;",
        "SET LOCAL statement_timeout = %d;" % int(
            config["statement_timeout_ms"]),
        "SET LOCAL lock_timeout = %d;" % int(config["lock_timeout_ms"]),
        "SET LOCAL idle_in_transaction_session_timeout = %d;" % int(
            config["idle_in_transaction_timeout_ms"]),
        copy_line(metadata_query(), targets[config["metadata_file"]]),
        copy_line(counts_query(config, cases, subjects),
                  targets[config["counts_file"]]),
    ]
    for name, spec in config["tables"].items():
        lines.append(copy_line(table_query(config, spec, cases, subjects),
                               targets[name]))
    lines.append(copy_line(concept_query(config, cases, subjects),
                           targets[CONCEPT_KEY]))
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------

def psql_environment(config):
    """Return the controlled psql environment with forced read-only options.

    Built from an allow-list: basic Windows process variables and the
    validated PGHOST, PGPORT, PGDATABASE, PGUSER and PGPASSWORD, then the
    forced PGOPTIONS, connect timeout, client encoding and application name.
    Inherited PGOPTIONS, PGHOSTADDR, service and credential-file variables
    are never passed on, so authentication can only come from PGPASSWORD.
    No credential value is added, copied or logged here.
    """
    forced = {
        "PGOPTIONS": (
            "-c default_transaction_read_only=on -c statement_timeout=%d "
            "-c lock_timeout=%d -c idle_in_transaction_session_timeout=%d"
            % (int(config["statement_timeout_ms"]),
               int(config["lock_timeout_ms"]),
               int(config["idle_in_transaction_timeout_ms"]))),
        "PGCONNECT_TIMEOUT": str(int(config["connect_timeout_seconds"])),
        "PGCLIENTENCODING": "UTF8",
        "PGAPPNAME": "ehr-evidence-source-pilot"}
    return guard.controlled_psql_environment(os.environ, forced)


def write_bytes(path, data):
    """Write raw bytes to a private log file inside the version directory."""
    with open(path, "wb") as handle:
        handle.write(data or b"")


def check_connection(config, psql_path):
    """Return why a read-only connection cannot be opened, else None.

    Runs SELECT 1 with the extraction's fixed flags and controlled
    environment (no password prompt, forced read-only options) before any
    directory exists, so a wrong user or password, or a stopped server,
    leaves nothing behind. psql's output is discarded: it can name the user.
    """
    command = [psql_path, "-X", "-w", "-q", "-A", "-t", "-v",
               "ON_ERROR_STOP=1", "-c", "SELECT 1"]
    try:
        completed = subprocess.run(
            command, env=psql_environment(config),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=int(config["connect_timeout_seconds"]) + 30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "the database connection check could not complete"
    if completed.returncode != 0 or completed.stdout.strip() != b"1":
        return ("the database connection failed: check PGUSER, PGPASSWORD "
                "and that the server is running; nothing was written")
    return None


def run_psql(config, version_dir, sql_path, options):
    """Execute the generated SQL once and capture its output privately.

    Neither psql stdout nor psql stderr is echoed or returned: the text can
    quote row values. Both streams are written to fixed log files inside the
    version directory and only the exit status is reported.
    """
    stdout_log = os.path.join(version_dir, config["stdout_log_name"])
    stderr_log = os.path.join(version_dir, config["stderr_log_name"])
    command = [options["psql"], "-X", "-w", "-v", "ON_ERROR_STOP=1",
               "-f", sql_path]
    record = {"exit_code": None, "stdout_log": stdout_log,
              "stderr_log": stderr_log,
              "timeout_seconds": options["subprocess_timeout"]}
    try:
        completed = subprocess.run(
            command, cwd=version_dir, env=psql_environment(config),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=options["subprocess_timeout"], check=False)
    except subprocess.TimeoutExpired as expired:
        write_bytes(stdout_log, expired.stdout)
        write_bytes(stderr_log, expired.stderr)
        record["run_state"] = "timed-out"
        record["reason"] = ("psql exceeded the %s second subprocess timeout"
                            % options["subprocess_timeout"])
        return record
    write_bytes(stdout_log, completed.stdout)
    write_bytes(stderr_log, completed.stderr)
    record["exit_code"] = completed.returncode
    if completed.returncode == 0:
        record["run_state"] = "finished"
    else:
        record["run_state"] = "failed"
        record["reason"] = ("psql exited with code %d; see the private stderr "
                            "log in the version directory"
                            % completed.returncode)
    return record


# --------------------------------------------------------------------------
# Local validation: parses the written files only, never the database
# --------------------------------------------------------------------------

def validation_order(config):
    """Return table specs ordered so cohort and visit context exist first."""
    items = list(config["tables"].items())
    items.sort(key=lambda item: (0 if item[1].get("defines_cohort") else
                                 1 if item[1].get("defines_visits") else 2))
    return items + [(CONCEPT_KEY, config["concept_table"])]


def read_single_row(path):
    """Return a one-row CSV as a dictionary, or None when it is malformed."""
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        return None
    return rows[0] if len(rows) == 1 else None


def expected_count(counts, key):
    """Return one independent source count as an integer, or None."""
    if not counts:
        return None
    raw = (counts.get(key) or "").strip()
    try:
        return int(raw)
    except ValueError:
        return None


def validate_table(path, spec, context, expected):
    """Parse one extracted CSV and record identity and linkage findings.

    Counts only: no identifier, field value or row leaves this function. Null
    visit links and missing concept labels are counted and preserved, not
    treated as failures.
    """
    record = {"file": os.path.basename(path), "rows_parsed": 0,
              "expected_rows": expected, "status": "incomplete",
              "failures": [], "missing_required_headers": []}
    findings = {name: 0 for name in TOLERATED_FINDINGS + FAILING_FINDINGS}
    keys = set()
    identities = set()
    primary = spec["primary_key"]
    person_column = spec.get("person_column")
    visit_column = spec.get("visit_column")
    identity_column = spec.get("identity_column")
    label_column = spec.get("label_column")
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = list(reader.fieldnames or [])
        record["headers"] = headers
        record["missing_required_headers"] = [
            column for column in output_names(spec) if column not in headers]
        if record["missing_required_headers"]:
            record.update(findings)
            record["failures"] = ["missing_required_headers"]
            return record
        for row in reader:
            record["rows_parsed"] += 1
            key = field(row, primary)
            if not key:
                findings["blank_primary_keys"] += 1
            elif key in keys:
                findings["duplicate_primary_keys"] += 1
            else:
                keys.add(key)
            person = field(row, person_column) if person_column else ""
            if person_column and not person:
                findings["blank_person_ids"] += 1
            if spec.get("defines_cohort"):
                if person:
                    context["selected"].add(person)
                identity = field(row, identity_column)
                if not identity:
                    findings["blank_identity_values"] += 1
                elif identity in identities:
                    findings["duplicate_identity_values"] += 1
                else:
                    identities.add(identity)
            elif person and person not in context["selected"]:
                findings["rows_person_not_selected"] += 1
            if spec.get("defines_visits") and key and person:
                context["visit_owner"][key] = person
            if visit_column:
                link = field(row, visit_column)
                if not link:
                    findings["null_visit_links"] += 1
                else:
                    owner = context["visit_owner"].get(link)
                    if owner is None:
                        findings["visit_links_unresolved"] += 1
                    elif owner != person:
                        findings["visit_person_mismatches"] += 1
            if label_column and not field(row, label_column):
                findings["missing_labels"] += 1
    record.update(findings)
    failures = [name for name in FAILING_FINDINGS if findings[name]]
    if expected is None:
        failures.append("no_independent_source_count")
    elif record["rows_parsed"] != expected:
        failures.append("row_count_differs_from_source_count")
    record["failures"] = failures
    record["status"] = "incomplete" if failures else "complete"
    return record


def transaction_failures(metadata, config):
    """Return reasons why the observed transaction was not a read-only one.

    Read-only status rests on the observed transaction settings and the
    reviewed SQL. assigned_xact_id is kept as metadata and is not judged here.
    """
    if metadata is None:
        return ["transaction metadata is missing or malformed"]
    reasons = []
    required = config["policy"]["required_database"]
    if (metadata.get("database_name") or "").strip() != required:
        reasons.append("database name is not %s" % required)
    isolation = (metadata.get("transaction_isolation") or "").strip().lower()
    if isolation != "repeatable read":
        reasons.append("transaction isolation was not repeatable read")
    if (metadata.get("transaction_read_only") or "").strip().lower() != "on":
        reasons.append("transaction was not read only")
    default_ro = (metadata.get("default_transaction_read_only") or "").strip()
    if default_ro.lower() != "on":
        reasons.append("default_transaction_read_only was not on")
    if not (metadata.get("snapshot_id") or "").strip():
        reasons.append("no transaction snapshot identifier was recorded")
    return reasons


def validate_snapshot(version_dir, config, cases, suffix=""):
    """Validate an extracted snapshot from its files alone; no database.

    Returns the per-table records and a summary whose status is 'complete'
    only when every table passes, the observed transaction was read-only and
    repeatable read, and the selected-person count matches the request.
    """
    counts = read_single_row(os.path.join(
        version_dir, config["counts_file"] + suffix))
    metadata = read_single_row(os.path.join(
        version_dir, config["metadata_file"] + suffix))
    context = {"selected": set(), "visit_owner": {}}
    records = []
    for name, spec in validation_order(config):
        path = os.path.join(version_dir, spec["file"] + suffix)
        try:
            record = validate_table(path, spec, context,
                                    expected_count(counts, name))
        except OSError as error:
            record = {"file": spec["file"], "rows_parsed": 0,
                      "expected_rows": expected_count(counts, name),
                      "status": "incomplete", "failures": ["unreadable"],
                      "reason": sanitise_error(error)}
        record["table"] = name
        records.append(record)

    cohort_reported = expected_count(counts, COHORT_KEY)
    parsed_persons = len(context["selected"])
    summary = {
        "cases_requested": cases,
        "queried_schema": config["schema"],
        "selected_persons_reported": cohort_reported,
        "selected_persons_parsed": parsed_persons,
        "source_counts": counts or {},
        "transaction": metadata or {},
        "transaction_failures": transaction_failures(metadata, config),
        "selection_failures": [],
        "null_visit_links_total": sum(r.get("null_visit_links", 0)
                                      for r in records),
        "concepts_without_metadata": sum(r.get("missing_labels", 0)
                                         for r in records),
    }
    if cohort_reported is None:
        summary["selection_failures"].append(
            "no independent selected-person count")
    elif cohort_reported != cases:
        summary["selection_failures"].append(
            "selected persons (%d) differ from the requested case count (%d)"
            % (cohort_reported, cases))
    if cohort_reported is not None and parsed_persons != cohort_reported:
        summary["selection_failures"].append(
            "parsed person rows (%d) differ from the selected-person count"
            % parsed_persons)
    failed = [r["table"] for r in records if r["status"] != "complete"]
    summary["failed_tables"] = failed
    summary["status"] = "complete" if not (
        failed or summary["transaction_failures"]
        or summary["selection_failures"]) else "incomplete"
    return records, summary


# --------------------------------------------------------------------------
# Manifest, promotion and reporting
# --------------------------------------------------------------------------

def hash_files(version_dir, names, chunk):
    """Return size and SHA256 for each expected completed file."""
    entries = []
    for name in names:
        path = os.path.join(version_dir, name)
        if os.path.isfile(path):
            entries.append({"file": name, "path": path, "present": True,
                            "size_bytes": os.path.getsize(path),
                            "sha256": sha256_file(path, chunk)})
        else:
            entries.append({"file": name, "path": path, "present": False})
    return entries


def promote(version_dir, config, suffix):
    """Rename validated partial extracts to their final protected names."""
    for name in data_names(config):
        os.replace(os.path.join(version_dir, name + suffix),
                   os.path.join(version_dir, name))


def case_set_selection(case_set, cases):
    """Return the manifest selection block of a case-set extraction."""
    value = case_set["spec"]
    return {
        "cases_requested": cases,
        "rule": ("exactly the %d subjects of case-set selection %s, matched "
                 "on the person table's identity source value"
                 % (cases, case_set["label"])),
        "case_set": {"label": case_set["label"],
                     "purpose": value["purpose"],
                     "partition": value["partition"],
                     "selection_record": case_set["selection_record"]},
        "purpose": value["statement"]}


def build_meta(config, config_path, version_dir, label, cases, psql_path,
               case_set=None):
    """Assemble the provenance block recorded in every manifest.

    No credential value is included: the recorded connection facts are the
    validated loopback host, the port, the required database name and the
    psql path. A case-set extraction records its selection record and
    replaces the pilot's selection text and purpose limitation with the
    case set's own.
    """
    chunk = int(config["chunk_bytes"])
    script = os.path.abspath(__file__)
    selection = {
        "cases_requested": cases,
        "visit_concept_ids": config["visit_concept_ids"],
        "min_qualifying_visits": config["min_qualifying_visits"],
        "rule": ("the first %d person_id values, ordered numerically, "
                 "among persons with at least %d distinct visits whose "
                 "visit_concept_id is in %s"
                 % (cases, config["min_qualifying_visits"],
                    config["visit_concept_ids"])),
        "purpose": config["purpose_note"]}
    scope = config["scope"]
    limitations = config["limitations"]
    if case_set is not None:
        selection = case_set_selection(case_set, cases)
        scope = dict(scope, selection=selection["rule"])
        limitations = [selection["purpose"]] + list(limitations[1:])
    return {
        "version_label": label,
        "version_directory": version_dir,
        "extraction_script": {"path": script, "version": VERSION,
                              "sha256": sha256_file(script, chunk)},
        "configuration": {"path": os.path.abspath(config_path),
                          "sha256": sha256_file(config_path, chunk)},
        "selection": selection,
        "connection": {
            "host": os.environ.get("PGHOST", ""),
            "port": os.environ.get("PGPORT", ""),
            "database": os.environ.get("PGDATABASE", ""),
            "queried_schema": config["schema"],
            "schema_note": ("every query is qualified with queried_schema; "
                            "current_schema in the transaction metadata is "
                            "the session search-path default only"),
            "loopback_only": True,
            "psql_executable": psql_path,
            "credentials": config["policy"]["credentials"]},
        "scope": scope,
        "coverage": config["coverage"],
        "limitations": limitations}


def build_manifest(meta, run_state, status, extras=None):
    """Assemble the manifest for the current run state."""
    manifest = {"step": "0.3.2", "generated_utc": now_utc(),
                "run_state": run_state, "status": status}
    manifest.update(meta)
    manifest.update(extras or {})
    return manifest


def write_json(path, payload):
    """Write JSON through a partial file inside the version directory."""
    partial = path + PARTIAL
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
    os.replace(partial, path)


def report(records, summary, manifest, manifest_path, problems=()):
    """Print status, table names and aggregate counts only, never content.

    The bounded coverage statement is printed only for a complete result; any
    failed validation or integrity check states that coverage is not
    established for this version.
    """
    for record in records:
        print("%-22s %-10s rows=%-9s source_count=%-9s %s"
              % (record["table"], record["status"],
                 record.get("rows_parsed", "n/a"),
                 record.get("expected_rows", "n/a"),
                 ",".join(record.get("failures", [])) or "-"))
    print("")
    print("Version directory     : %s" % manifest["version_directory"])
    print("Report                : %s" % manifest_path)
    print("Run state             : %s" % manifest["run_state"])
    print("Cases requested       : %s" % summary["cases_requested"])
    print("Selected persons      : %s" % summary["selected_persons_reported"])
    print("Null visit links      : %s" % summary["null_visit_links_total"])
    print("Concepts w/o metadata : %s" % summary["concepts_without_metadata"])
    for reason in summary["transaction_failures"]:
        print("    transaction: %s" % reason)
    for reason in summary["selection_failures"]:
        print("    selection: %s" % reason)
    for reason in problems:
        print("    integrity: %s" % reason)
    print("Identifiers are never printed; the person mapping stays in the "
          "protected version directory.")
    if manifest["status"] == "complete":
        print("Coverage is complete only within the queried OMOP tables for "
              "the selected persons at this snapshot. It is NOT a statement "
              "of clinical absence or global EHR completeness.")
    else:
        print("Coverage is NOT established for this version: validation or "
              "integrity checks did not pass.")
    print("RESULT: %s" % manifest["status"].upper())


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def extract(config, args, version_dir, label, cases, psql_path,
            case_set=None):
    """Run the bounded extraction into a new version directory.

    The version directory is created only after containment checks pass, an
    explicitly incomplete manifest is written before psql starts, and the
    extracts are promoted from their partial names only once psql has finished
    and local validation has succeeded. A case-set extraction selects exactly
    the subjects of its verified selection record.
    """
    subjects = case_set["subjects"] if case_set else None
    if os.path.exists(version_dir):
        print("FAILED: the version directory already exists; extraction "
              "never overwrites a version. Choose a new --version-label or "
              "use --verify-only.")
        return 1
    problem = (connection_problem(config, psql_path)
               or check_connection(config, psql_path))
    if problem:
        print("FAILED: %s" % problem)
        return 1

    os.makedirs(version_dir)
    manifest_path = os.path.join(version_dir, config["manifest_name"])
    meta = build_meta(config, args.config, version_dir, label, cases,
                      psql_path, case_set)
    write_json(manifest_path, build_manifest(
        meta, "initial", "incomplete",
        {"note": "extraction has not started; nothing in this directory is "
                 "validated"}))

    sql_path = os.path.join(version_dir, config["sql_name"])
    targets = copy_targets(config, version_dir, PARTIAL)
    with open(sql_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(build_sql(config, cases, targets, subjects))
    meta["generated_sql"] = {
        "path": sql_path,
        "sha256": sha256_file(sql_path, int(config["chunk_bytes"]))}
    write_json(manifest_path, build_manifest(
        meta, "in-progress", "incomplete",
        {"note": "psql is running or was interrupted; extracts are partial"}))

    try:
        execution = run_psql(config, version_dir, sql_path,
                             {"psql": psql_path,
                              "subprocess_timeout": args.subprocess_timeout})
    except KeyboardInterrupt:
        execution = {"run_state": "interrupted", "exit_code": None,
                     "reason": "interrupted before psql finished"}
    meta["execution"] = execution
    if execution["run_state"] != "finished":
        write_json(manifest_path, build_manifest(
            meta, execution["run_state"], "incomplete",
            {"note": execution.get("reason", "psql did not finish"),
             "files": hash_files(version_dir, hashed_names(config),
                                 int(config["chunk_bytes"]))}))
        print("FAILED: %s" % execution.get("reason", "psql did not finish"))
        print("The version directory is marked incomplete and its partial "
              "extracts were left in place for inspection.")
        return 1

    records, summary = validate_snapshot(version_dir, config, cases, PARTIAL)
    if summary["status"] == "complete":
        promote(version_dir, config, PARTIAL)
        final_names = {name: spec["file"] for name, spec in all_specs(config)}
        for record in records:
            record["file"] = final_names[record["table"]]
    files = hash_files(version_dir, hashed_names(config),
                       int(config["chunk_bytes"]))
    manifest = build_manifest(
        meta, "finished", summary["status"],
        {"validation": summary, "tables": records, "files": files,
         "promoted": summary["status"] == "complete"})
    write_json(manifest_path, manifest)
    report(records, summary, manifest, manifest_path)
    if summary["status"] != "complete":
        print("Partial extracts were NOT promoted; the version is incomplete.")
    return 0 if summary["status"] == "complete" else 1


def hash_record_valid(entry):
    """Return True for a complete recorded SHA256 and size entry."""
    size = entry.get("size_bytes")
    digest = entry.get("sha256")
    return (entry.get("present") is True and isinstance(digest, str)
            and bool(HEX64.match(digest)) and isinstance(size, int)
            and not isinstance(size, bool) and size >= 0)


def integrity_problems(recorded, config, version_dir, config_sha):
    """Compare a recorded manifest with the configuration and files on disk.

    Requires a completed, promoted run, the current configuration SHA256,
    and exactly one complete hash/size record for every expected file and
    every file present (other than the manifest and verification report).
    Unexpected files are named generically. Returns (problems, changed).
    """
    if not isinstance(recorded, dict):
        return ["manifest.json is missing or malformed"], []
    problems = []
    changed = []
    if (recorded.get("status") != "complete"
            or recorded.get("run_state") != "finished"
            or recorded.get("promoted") is not True):
        problems.append("manifest does not record a completed, promoted run")
    configuration = recorded.get("configuration")
    if (not isinstance(configuration, dict)
            or configuration.get("sha256") != config_sha):
        problems.append("current configuration SHA256 differs from the "
                        "recorded configuration")
    entries = recorded.get("files")
    if not isinstance(entries, list):
        return problems + ["manifest has no file hash list"], changed
    records = {}
    for entry in entries:
        name = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(name, str) or name in records:
            problems.append("manifest file list has a malformed or duplicate "
                            "entry")
            continue
        records[name] = entry
    expected = set(hashed_names(config))
    skipped = {config["manifest_name"], config["verification_report_name"]}
    present = {name for name in os.listdir(version_dir)
               if name not in skipped
               and os.path.isfile(os.path.join(version_dir, name))}
    chunk = int(config["chunk_bytes"])
    for name in sorted(expected | present | set(records)):
        entry = records.get(name)
        path = os.path.join(version_dir, name)
        if name not in expected:
            problems.append("an unexpected file is present or recorded")
        elif entry is None or not hash_record_valid(entry):
            problems.append("no complete hash/size record for %s" % name)
        elif name not in present:
            problems.append("recorded file is missing: %s" % name)
        elif (entry["size_bytes"] != os.path.getsize(path)
              or entry["sha256"] != sha256_file(path, chunk)):
            problems.append("file changed since the manifest: %s" % name)
            changed.append(name)
    return problems, changed


def verify_only(config, args, version_dir, label, cases, case_set=None):
    """Re-validate a completed snapshot locally without any database access.

    Success requires a valid completed manifest whose hash/size records match
    every file on disk and whose configuration SHA256 matches the current
    configuration, plus a passing re-validation of the files. A case-set
    snapshot must also record the currently pinned selection record. The
    manifest is never rewritten; a separate verification report is written
    beside it.
    """
    manifest_path = os.path.join(version_dir, config["manifest_name"])
    if not os.path.isdir(version_dir):
        print("FAILED: the version directory does not exist")
        return 1
    try:
        recorded = read_json(manifest_path)
    except (OSError, ValueError):
        recorded = None
    problems, changed = integrity_problems(
        recorded, config, version_dir,
        sha256_file(args.config, int(config["chunk_bytes"])))
    selection = recorded.get("selection") if isinstance(
        recorded, dict) else None
    if case_set is not None:
        recorded_set = (selection or {}).get("case_set") or {}
        if (recorded_set.get("label") != case_set["label"]
                or (recorded_set.get("selection_record") or {}).get("sha256")
                != case_set["selection_record"]["sha256"]):
            problems.append("manifest does not record the pinned case-set "
                            "selection")
    if args.cases is None and isinstance(selection, dict):
        recorded_cases = selection.get("cases_requested")
        if cases_problem(config, recorded_cases) is None:
            cases = recorded_cases
        else:
            problems.append("manifest records an invalid case count")

    records, summary = validate_snapshot(version_dir, config, cases)
    files = hash_files(version_dir, hashed_names(config),
                       int(config["chunk_bytes"]))
    status = ("complete" if summary["status"] == "complete" and not problems
              else "incomplete")
    report_path = os.path.join(version_dir,
                               config["verification_report_name"])
    payload = {"step": "0.3.2", "mode": "verify-only",
               "generated_utc": now_utc(), "status": status,
               "version_label": label, "version_directory": version_dir,
               "recorded_manifest_status": (
                   recorded.get("status") if isinstance(recorded, dict)
                   else None),
               "integrity_problems": problems,
               "files_changed_since_manifest": changed,
               "validation": summary, "tables": records, "files": files}
    write_json(report_path, payload)
    manifest = {"version_directory": version_dir, "run_state": "verify-only",
                "status": status}
    report(records, summary, manifest, report_path, problems)
    return 0 if status == "complete" else 1


def build_parser():
    """Build the command-line parser for the source pilot tool."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--dest-root", help="override the destination root")
    parser.add_argument("--version-label", help="explicit version directory")
    parser.add_argument("--cases", type=int,
                        help="number of pilot persons (bounded by the config)")
    parser.add_argument("--psql", help="override the psql executable path")
    parser.add_argument("--subprocess-timeout", type=int,
                        help="seconds allowed for the whole psql run")
    parser.add_argument("--verify-only", action="store_true",
                        help="re-validate an existing snapshot locally")
    parser.add_argument("--case-set",
                        help="extract exactly the subjects of this "
                             "case set's pinned selection record")
    return parser


def case_set_cohort(label, entry=None):
    """Return the case-set block for an extraction of one case set.

    The committed registry must mirror the code, and the selection record
    is read through its pin; its subjects are returned in ascending order.
    """
    registry.load_config()
    value = registry.spec(label)
    cases, reference = registry.read_selection(label, entry)
    return {"label": label, "spec": value, "selection_record": reference,
            "subjects": tuple(sorted(case[3] for case in cases))}


def run(args):
    """Prepare or verify one bounded read-only source pilot snapshot.

    Configuration policy and every destination path are checked before
    either mode writes anything or attempts a connection.
    """
    config = load_config(args.config)
    case_set = None
    if args.case_set is not None:
        if args.cases is not None or args.version_label not in (
                None, args.case_set):
            print("FAILED: --case-set fixes the case count and the version "
                  "label")
            return 1
        try:
            case_set = case_set_cohort(args.case_set)
        except registry.RegistryError as error:
            print("FAILED: case-set selection: %s" % error)
            return 1
        cases = len(case_set["subjects"])
        label = args.case_set
        default_root = registry.SNAPSHOT_ROOT
    else:
        cases = config["cases"] if args.cases is None else args.cases
        label = args.version_label or config["version_label"]
        default_root = config["destination_root"]
    if args.subprocess_timeout is None:
        args.subprocess_timeout = int(config["subprocess_timeout_seconds"])
    psql_path = args.psql or config["psql_executable"]

    problem = (config_problem(config) or source_config_problem(config)
               or cases_problem(config, cases))
    if problem:
        print("FAILED: %s" % problem)
        return 1
    for component in [label] + written_names(config):
        if not is_safe_component(component):
            print("FAILED: unsafe path component in the requested selection")
            return 1
    if args.subprocess_timeout <= 0:
        print("FAILED: the subprocess timeout must be a positive number of "
              "seconds")
        return 1
    raw_root = args.dest_root or default_root
    problem = guard.path_problem(raw_root)
    if problem:
        print("FAILED: destination root: %s" % problem)
        return 1
    version_dir = os.path.abspath(os.path.join(raw_root, label))
    problem = (guard.destinations_problem(planned_destinations(config,
                                                               version_dir))
               or destination_problem(version_dir, config))
    if problem:
        print("FAILED: %s" % problem)
        return 1

    if args.verify_only:
        return verify_only(config, args, version_dir, label, cases, case_set)
    return extract(config, args, version_dir, label, cases, psql_path,
                   case_set)


def main(argv=None):
    """Run the tool, sanitising any top-level failure."""
    try:
        return run(build_parser().parse_args(argv))
    except KeyboardInterrupt:
        print("FAILED: interrupted before completion")
        return 1
    except Exception as error:  # sanitised: text may quote row content
        print("FAILED: %s" % sanitise_error(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
