"""Keep the generation's measurements in the tracker, as aggregates only
(ADR 0018).

A measurement is one benchmark run of the model on a development case set
(generation.measure). Its record holds the run's configuration fields
(the case set, the commit, the date, the precision, the versions, the
number of drivers and the limits) and its scalars: by stratum, the
requests, those released as the model wrote them and their share, the
scores against the correct documents and the seconds, tokens and memory
of the model's attempts. It holds nothing of a request, a document or a
response. generation.measure --record writes the record of a run, and the
runs of Phase 7 (config/measurement_history.json, the aggregates of the
execution log) are written as records marked backfilled. A record is
written once, under the workspace's outputs\\measurements, named by a
digest of its content.

Each record becomes one visible tracker task of the kind "Generation
measurement", named by its case set, precision, version and commit, with
its configuration fields and scalars and lineage tags naming the record's
path and checksum; it is read back and marked completed. An index beside
the records keeps the task of each record, so a record is sent once.

The default mode checks records and tasks built from invented values.
--operator-real writes the history's records and checks the task of every
record offline. --operator-remote also creates the tasks of the records
not yet sent; it needs the tracker's configuration in the process
environment, which this module never reads or prints.

Usage:
    python -m ehr_evidence_app.tracking.measurements
    python -m ehr_evidence_app.tracking.measurements --operator-real
    python -m ehr_evidence_app.tracking.measurements --operator-remote
"""

import argparse
import hashlib
import json
import ntpath
import os
import subprocess
import sys
import time

from ehr_evidence_app.tracking import connection as checker
from ehr_evidence_app.tracking import controlled as tracking

SCHEMA_VERSION = "1.0"
MEASUREMENTS_DIR = tracking.WORKSPACE_ROOT + "\\outputs\\measurements"
RECORD_PREFIX = "measurement-"
INDEX_NAME = "tracker-tasks.json"
HISTORY_PATH = os.path.join(checker.REPO_ROOT, "config",
                            "measurement_history.json")
SOURCES = ("run", "backfill")
RECORD_FIELDS = ("schema_version", "source", "assembled", "configuration",
                 "scalars")
REQUIRED_CONFIGURATION = ("case_set", "commit", "measured_on", "precision",
                          "prompt_version", "projection_version",
                          "shape_version", "drivers", "max_prompt_tokens",
                          "max_output_tokens", "max_request_seconds")
# The strata the benchmark draws, and the unions of some of them.
STRATA = ("lists", "empty lists", "presence recorded",
          "presence not recorded", "explanations")
UNIONS = {"all lists": ("lists", "empty lists"),
          "presence": ("presence recorded", "presence not recorded"),
          "all": STRATA}
COUNTED = ("released", "released within limits", "outcome right")
# The configuration a model's attempt depends on, and what checks it, each
# folded into one fingerprint of the fields a record gives.
MODEL_FIELDS = ("model_repository", "model_revision", "precision", "decoder",
                "drivers", "max_prompt_tokens", "max_output_tokens",
                "max_request_seconds", "prompt_version", "projection_version",
                "shape_version")
VALIDATION_FIELDS = ("shape_version", "candidate_contract",
                     "explanation_template", "wording_rules")
OFFLINE_PROJECT = "offline-validation"


class MeasurementError(Exception):
    """A record that cannot be kept or sent; carries a short code."""


class RecordConflict(Exception):
    """A different record already stands at the same path."""


# Records

def triples(record):
    """Return a record's scalars as (metric, series, value), sorted."""
    return [(metric, series, value)
            for metric, values in sorted(record["scalars"].items())
            for series, value in sorted(values.items())]


def with_rates(scalars):
    """Return scalars with the release rate of every series that has both
    its requests and its released, to six decimals."""
    found = {metric: dict(values) for metric, values in scalars.items()}
    requests, released = found.get("requests", {}), found.get("released",
                                                              {})
    rates = {series: round(released[series] / requests[series], 6)
             for series in requests
             if series in released and requests[series]}
    if rates:
        found["release rate"] = dict(found.get("release rate", {}), **rates)
    return found


def check_configuration(configuration):
    """Check a record's configuration fields: every required one, both or
    neither of the lower limits, each in its permitted form; raise
    MeasurementError with a code when they do not hold."""
    if not isinstance(configuration, dict) or any(
            field not in configuration for field in REQUIRED_CONFIGURATION):
        raise MeasurementError("configuration_incomplete")
    within = [field in configuration
              for field in ("within_output_tokens", "within_seconds")]
    if any(within) and not all(within):
        raise MeasurementError("configuration_incomplete")
    try:
        tracking.build_configuration(configuration)
    except tracking.UnsafeMetadata:
        raise MeasurementError("configuration_refused") from None
    return configuration


def check_record(record):
    """Check a record's form, its configuration and its scalars, and that
    its counts agree; raise MeasurementError with a code when it does not
    hold."""
    if not isinstance(record, dict) or set(record) != set(RECORD_FIELDS):
        raise MeasurementError("record_fields")
    if record["schema_version"] != SCHEMA_VERSION:
        raise MeasurementError("record_version")
    if record["source"] not in SOURCES or not isinstance(
            record["assembled"], bool) or (
            record["assembled"] and record["source"] != "backfill"):
        raise MeasurementError("record_source")
    configuration = check_configuration(record["configuration"])
    scalars = record["scalars"]
    if not isinstance(scalars, dict) or not all(
            isinstance(values, dict) and values
            for values in scalars.values()):
        raise MeasurementError("scalars_malformed")
    try:
        tracking.build_scalar_events("record", triples(record))
    except tracking.UnsafeMetadata:
        raise MeasurementError("scalars_refused") from None
    if ("within_seconds" in configuration) != (
            "released within limits" in scalars):
        raise MeasurementError("limits_inconsistent")
    requests = scalars.get("requests", {})
    if not requests or set(scalars.get("released", {})) != set(requests):
        raise MeasurementError("counts_incomplete")
    for metric in COUNTED:
        for series, value in scalars.get(metric, {}).items():
            if series not in requests or value > requests[series]:
                raise MeasurementError("counts_inconsistent")
    for metric in ("requests",) + COUNTED:
        values = scalars.get(metric, {})
        for union, parts in UNIONS.items():
            if union not in values:
                continue
            given = sum(values.get(part, 0) for part in parts)
            if values[union] < given or (all(
                    part in values for part in parts)
                    and values[union] != given):
                raise MeasurementError("totals_inconsistent")
    if with_rates(scalars).get("release rate") != scalars.get(
            "release rate"):
        raise MeasurementError("rates_inconsistent")
    return record


def build_record(configuration, scalars, source="run", assembled=False):
    """Return a checked record of a run's configuration and its scalars,
    with the release rates derived from the counts."""
    return check_record({"schema_version": SCHEMA_VERSION, "source": source,
                         "assembled": assembled,
                         "configuration": dict(configuration),
                         "scalars": with_rates(scalars)})


def record_bytes(record):
    """Return a record as deterministic JSON bytes."""
    return (json.dumps(record, sort_keys=True, indent=1)
            + "\n").encode("utf-8")


def record_path(record, directory=MEASUREMENTS_DIR):
    """Return where a record is written: named by a digest of its bytes,
    so that the same record always gives the same file."""
    return ntpath.join(directory, "%s%s.json" % (
        RECORD_PREFIX, hashlib.sha256(record_bytes(record)).hexdigest()[:16]))


def write_record(record, directory=MEASUREMENTS_DIR):
    """Write a checked record once and return its path: an identical one
    is left as it is, and a different one is never replaced."""
    check_record(record)
    data, path = record_bytes(record), record_path(record, directory)
    if os.path.exists(path):
        with open(path, "rb") as handle:
            if handle.read() == data:
                return path
        raise RecordConflict(path)
    os.makedirs(directory, exist_ok=True)
    partial = path + ".partial"
    with open(partial, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.rename(partial, path)
    return path


def read_records(directory=MEASUREMENTS_DIR):
    """Return (path, record) of every record in a directory, by name; a
    record that does not check, or does not stand at its own name, fails
    them all."""
    if not os.path.isdir(directory):
        return []
    found = []
    for name in sorted(os.listdir(directory)):
        if not (name.startswith(RECORD_PREFIX) and name.endswith(".json")):
            continue
        path = ntpath.join(directory, name)
        with open(path, "rb") as handle:
            data = handle.read()
        try:
            record = check_record(json.loads(data.decode("utf-8")))
        except (ValueError, MeasurementError):
            raise MeasurementError("record_invalid") from None
        if record_bytes(record) != data or record_path(
                record, directory) != path:
            raise MeasurementError("record_invalid")
        found.append((path, record))
    return found


def load_history(path=HISTORY_PATH):
    """Return the records of the recorded runs, each marked backfilled;
    the history's entry names, for its readers, are not kept."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            history = json.load(handle)
        if history["schema_version"] != SCHEMA_VERSION:
            raise MeasurementError("history_version")
        records = []
        for run in history["runs"]:
            if set(run) - {"entry", "assembled", "configuration",
                           "scalars"}:
                raise MeasurementError("history_fields")
            records.append(build_record(
                dict(history["defaults"], **run["configuration"]),
                run["scalars"], "backfill", run.get("assembled", False)))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise MeasurementError("history_invalid") from None
    return records


# Tasks

def fingerprint(configuration, names):
    """Return the SHA-256 of the named configuration fields a record gives,
    in their given order."""
    text = "\n".join("%s=%s" % (name, configuration[name])
                     for name in names if name in configuration)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def totals(record):
    """Return (requests, released) of a record: of every request where it
    gives them, else summed over the strata it gives."""
    scalars = record["scalars"]
    if "all" in scalars["requests"]:
        return scalars["requests"]["all"], scalars["released"]["all"]
    return tuple(sum(scalars[metric].get(stratum, 0) for stratum in STRATA)
                 for metric in ("requests", "released"))


def tracker_outputs(record):
    """Return the aggregates a task's lineage names: the requests, the
    share released as the model wrote them, the share that fell back and,
    where the record gives it, the median seconds as latency_ms."""
    requests, released = totals(record)
    outputs = {"requests": requests}
    if requests:
        outputs["model_written_rate"] = round(released / requests, 6)
        outputs["fallback_rate"] = round(1 - released / requests, 6)
    median = record["scalars"].get("seconds", {}).get("all: median")
    if median is not None:
        outputs["latency_ms"] = round(median * 1000, 3)
    return outputs


def lineage_of(record, path, checksum):
    """Return the controlled lineage of a record at path with the given
    SHA-256."""
    configuration = record["configuration"]
    return {
        "code_revision": configuration["commit"],
        "case_set_version": configuration["case_set"],
        "llm_configuration_fingerprint": fingerprint(configuration,
                                                     MODEL_FIELDS),
        "prompt_version": configuration["prompt_version"],
        "evidence_schema_version": configuration["projection_version"],
        "validation_configuration_fingerprint": fingerprint(
            configuration, VALIDATION_FIELDS),
        "evaluation_outputs": tracker_outputs(record),
        "protected_reference": path,
        "protected_checksum": checksum,
    }


def build_payload(project_id, record, path, checksum):
    """Return the controlled task of a record, checked by
    tracking.controlled: a visible measurement task with its
    configuration fields, marked as run, backfilled or assembled."""
    payload = tracking.build_task_payload(
        project_id, lineage_of(record, path, checksum), {},
        kind="measurement", configuration=record["configuration"])
    payload["tags"].append("provenance:measurement")
    if record["source"] == "backfill":
        payload["tags"].append("provenance:backfilled")
    if record["assembled"]:
        payload["tags"].append("provenance:assembled")
    return payload


def checksum_of(record):
    """Return the SHA-256 of a record's bytes."""
    return hashlib.sha256(record_bytes(record)).hexdigest()


def read_index(directory=MEASUREMENTS_DIR):
    """Return the task of each record already sent, by record name."""
    path = ntpath.join(directory, INDEX_NAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            index = json.load(handle)
    except (OSError, ValueError):
        raise MeasurementError("index_invalid") from None
    if not isinstance(index, dict) or index.get(
            "schema_version") != SCHEMA_VERSION:
        raise MeasurementError("index_invalid")
    tasks = index.get("tasks")
    if not isinstance(tasks, dict) or not all(
            isinstance(name, str) and name.startswith(RECORD_PREFIX)
            and checker.safe_id(task_id) for name, task_id in tasks.items()):
        raise MeasurementError("index_invalid")
    return tasks


def write_index(tasks, directory=MEASUREMENTS_DIR):
    """Replace the index with the task of each record sent."""
    path = ntpath.join(directory, INDEX_NAME)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump({"schema_version": SCHEMA_VERSION, "tasks": tasks},
                  handle, sort_keys=True, indent=1)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def prepared(directory=MEASUREMENTS_DIR, history_path=HISTORY_PATH):
    """Write the records of the history, and return (path, record) of every
    record in the directory."""
    for record in load_history(history_path):
        write_record(record, directory)
    return read_records(directory)


def offline_result(directory=MEASUREMENTS_DIR, history_path=HISTORY_PATH):
    """Write the history's records and check the task of every record."""
    try:
        found = prepared(directory, history_path)
        for path, record in found:
            build_payload(OFFLINE_PROJECT, record, path, checksum_of(record))
        sent = read_index(directory)
    except MeasurementError as error:
        return {"status": "FAIL", "mode": "offline", "finding": str(error)}
    except RecordConflict:
        return {"status": "FAIL", "mode": "offline",
                "finding": "record_conflict"}
    except tracking.UnsafeMetadata:
        return {"status": "FAIL", "mode": "offline",
                "finding": "metadata_refused"}
    names = {ntpath.basename(path) for path, _ in found}
    return {"status": "PASS", "mode": "offline", "records": len(found),
            "backfilled": sum(record["source"] == "backfill"
                              for _, record in found),
            "sent": len(names & set(sent)),
            "to_send": len(names - set(sent)), "artifact_upload": False}


def remote_result(environ=None, adapter_factory=checker.SdkAdapter,
                  directory=MEASUREMENTS_DIR, history_path=HISTORY_PATH,
                  lstat=os.lstat):
    """Create, read back and complete the task of every record not yet
    sent, in order, and keep each in the index; the first finding stops.
    environ, adapter_factory, directory, history_path and lstat are
    injectable for tests."""
    env = os.environ if environ is None else environ
    started = time.monotonic()
    created = []
    try:
        if checker.environment_problems(env, lstat=lstat):
            return {"status": "FAIL", "mode": "operator_remote",
                    "finding": "environment_invalid"}
        found = prepared(directory, history_path)
        sent = read_index(directory)
        pending = [(path, record) for path, record in found
                   if ntpath.basename(path) not in sent]
        if not pending:
            return {"status": "PASS", "mode": "operator_remote",
                    "records": len(found), "created": 0,
                    "task_ids": [], "artifact_upload": False}
        with checker.quiet_sdk():
            adapter = adapter_factory()
            try:
                project_id = checker.connect_project(adapter)
                for path, record in pending:
                    payload = build_payload(project_id, record, path,
                                            checksum_of(record))
                    task_id, findings = checker.create_controlled_task(
                        adapter, project_id, payload, "measurement",
                        triples(record))
                    if findings:
                        return {"status": "FAIL", "mode": "operator_remote",
                                "findings": sorted(set(findings)),
                                "record": ntpath.basename(path),
                                "task_id": task_id, "created": created}
                    sent[ntpath.basename(path)] = task_id
                    write_index(sent, directory)
                    created.append(task_id)
            except checker.ProbeFailure as failure:
                return {"status": "FAIL", "mode": "operator_remote",
                        "finding": failure.finding[1], "created": created}
        return {"status": "PASS", "mode": "operator_remote",
                "records": len(found), "created": len(created),
                "task_ids": created, "artifact_upload": False,
                "elapsed_seconds": round(time.monotonic() - started, 3)}
    except MeasurementError as error:
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": str(error), "created": created}
    except RecordConflict:
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": "record_conflict", "created": created}
    except tracking.UnsafeMetadata:
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": "metadata_refused", "created": created}
    except Exception as error:  # noqa: BLE001 - reported by code only
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": checker.SDK_ERRORS.get(type(error).__name__,
                                                  "measurements_failed"),
                "created": created}


# The Commit a Run Measures

def code_commit(repo=checker.REPO_ROOT, run=subprocess.run):
    """Return the short commit of the checked-out code, which a record
    names; the tracked files must be as committed, or the run would not
    be the commit's. run is injectable for tests."""
    def git(*arguments):
        """Return the output of one git command in the repository."""
        try:
            done = run(("git", "-C", repo) + arguments, capture_output=True,
                       text=True, timeout=60, check=False)
        except (OSError, subprocess.SubprocessError):
            raise MeasurementError("commit_unknown") from None
        if done.returncode != 0:
            raise MeasurementError("commit_unknown")
        return done.stdout.strip()
    if git("status", "--porcelain", "--untracked-files=no"):
        raise MeasurementError("working_tree_modified")
    commit = git("rev-parse", "--short=7", "HEAD")
    if not tracking.CONFIGURATION_FIELDS["commit"].fullmatch(commit):
        raise MeasurementError("commit_unknown")
    return commit


# Synthetic Default Mode (Invented Values Only)

def invented_record():
    """Return a record of invented values in every permitted part."""
    return build_record({
        "case_set": "invented-16-v1", "commit": "0123abc",
        "measured_on": "2026-09-28", "precision": "bf16-mixed",
        "decoder": "xgrammar", "prompt_version": "v9",
        "projection_version": "projection-v5",
        "shape_version": "candidate-shape-v4", "drivers": 5,
        "per_group": 8, "max_prompt_tokens": 16384,
        "max_output_tokens": 768, "max_request_seconds": 180,
        "within_output_tokens": 512, "within_seconds": 120}, {
        "requests": {"lists": 4, "empty lists": 1, "all lists": 5,
                     "explanations": 2, "all": 7},
        "released": {"lists": 3, "empty lists": 1, "all lists": 4,
                     "explanations": 1, "all": 5},
        "released within limits": {"lists": 2, "all": 3},
        "outcome right": {"lists": 4},
        "F1": {"lists: conditions": 0.75, "explanations: drivers": 1.0},
        "seconds": {"all: median": 12.5, "all: total": 90.0}})


def run_synthetic(directory=None):
    """Return (records checked, problems) of invented records: their form,
    their task and what the checks refuse. No file is written unless a
    directory is given."""
    problems = []
    record = invented_record()
    path = record_path(record)
    payload = build_payload("invented-project", record, path,
                            checksum_of(record))
    if payload["name"] != ("Generation measurement: invented-16-v1, "
                           "bf16-mixed, prompt v9, 0123abc"):
        problems.append("the task is misnamed")
    if tracker_outputs(record) != {"requests": 7,
                                   "model_written_rate": 0.714286,
                                   "fallback_rate": 0.285714,
                                   "latency_ms": 12500.0}:
        problems.append("the lineage aggregates are wrong")
    if record["scalars"]["release rate"]["all"] != 0.714286:
        problems.append("a release rate is wrong")
    for change in ({"released": {"lists": 5, "empty lists": 1,
                                 "all lists": 6, "explanations": 1,
                                 "all": 7}},
                   {"released": {"lists": 3}},
                   {"requests": dict(record["scalars"]["requests"],
                                     all=8)},
                   {"F1": {"lists: patient": 0.5}},
                   {"release rate": {"all": 0.9}}):
        try:
            check_record(dict(record, scalars=dict(record["scalars"],
                                                   **change)))
            problems.append("the record accepted %s" % sorted(change)[0])
        except MeasurementError:
            pass
    for change in ({"commit": "HEAD"}, {"notes": "free text"},
                   {"prompt_version": "prompt-v9"}):
        try:
            check_record(dict(record, configuration=dict(
                record["configuration"], **change)))
            problems.append("the record accepted %s" % sorted(change)[0])
        except MeasurementError:
            pass
    text = json.dumps(payload)
    if "invented-16-v1" not in text or "free text" in text:
        problems.append("the task does not hold the configuration alone")
    count = 1
    try:
        history = load_history()
        count += len(history)
        if len({record_path(item) for item in history}) != len(history):
            problems.append("two runs of the history give one record")
    except (OSError, ValueError, KeyError, MeasurementError):
        problems.append("the history does not check")
    return count, problems


def main(argv=None):
    """Run the default mode on invented values, or an operator mode on the
    workspace's records; prints counts, codes and task IDs only."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="write the history's records and check every "
                             "record's task offline")
    parser.add_argument("--operator-remote", action="store_true",
                        help="also create the tasks of the records not yet "
                             "sent")
    args = parser.parse_args(argv)
    if args.operator_real and args.operator_remote:
        print("FAILED: give one of --operator-real and --operator-remote")
        return 2
    if args.operator_remote:
        result = remote_result()
    elif args.operator_real:
        result = offline_result()
    else:
        count, problems = run_synthetic()
        for problem in problems:
            print("PROBLEM: %s" % problem)
        print("records checked: %d" % count)
        print("RESULT: %s" % ("FAIL" if problems else "PASS"))
        return 1 if problems else 0
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
