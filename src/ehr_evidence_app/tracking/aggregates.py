"""Describe the runtime records to the tracker, as aggregates only
(Phase 7, work item 46; ADR 0012, decision 5).

The protected runtime records of one case set, all written under one
configuration (generation.records), are summed into counts and measures
and written as a deterministic manifest under the workspace's
outputs\\lineage. A controlled, metadata-only tracker task, named "Runtime
aggregates" and left visible (ADR 0018), then names the application
version, the case set, the fingerprints of the model's and the
validators' configuration, the prompt and projection versions and the
manifest's path and checksum, and four aggregates: the requests, the share
of responses written from evidence that the model wrote, the share that
fell back, and the median seconds of the model's attempts as latency_ms.
It also carries the versions as configuration fields and, as scalars, the
requests by task, the two shares and the median, 90th percentile and
largest of the model's seconds, tokens and GPU memory. Nothing else leaves
the workspace: no record, prompt, response, identifier or file.

The default mode builds the manifest and the task from invented records
and checks them. --operator-real writes the manifest of one case set's
records and checks its task offline. --operator-remote also creates the
task and reads it back, as tracking.registration does; it needs the
tracker's configuration in the process environment, which this module
never reads or prints.

Usage:
    python -m ehr_evidence_app.tracking.aggregates
    python -m ehr_evidence_app.tracking.aggregates --operator-real \\
        --case-set dev-30-v1
    python -m ehr_evidence_app.tracking.aggregates --operator-remote \\
        --case-set dev-30-v1
"""

import argparse
import hashlib
import json
import ntpath
import os
import sys
import time

from ehr_evidence_app.tracking import connection as checker
from ehr_evidence_app.tracking import controlled as tracking

SCHEMA_VERSION = "1.0"
LINEAGE_DIR = tracking.WORKSPACE_ROOT + "\\outputs\\lineage"
MANIFEST_PREFIX = "runtime-aggregates-"
# The configuration digests a model's attempt depends on, and the versions
# of what checks a response, each folded into one fingerprint.
MODEL_CONFIGURATION = ("generation_sha256", "execution_limits_sha256",
                       "explanation_sha256")
VALIDATION_CONFIGURATION = ("candidate_contract", "shape_version",
                            "question_interpretation", "condition_resolution",
                            "qa_answer", "explanation_template",
                            "wording_rules")
OFFLINE_PROJECT = "offline-validation"


class AggregateError(Exception):
    """Records that cannot be described; carries a short code."""


class ManifestConflict(Exception):
    """A different manifest already stands at the same path."""


def fingerprint(versions, names):
    """Return the SHA-256 of some named versions, in their given order."""
    text = "\n".join("%s=%s" % (name, versions[name]) for name in names)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest_of(found, case_set):
    """Return the manifest of one case set's records: their number, a
    digest of their identities and responses, the one configuration they
    were written under, and their aggregates."""
    from ehr_evidence_app.generation import records
    chosen = sorted((record for record in found
                     if record.case.case_set == case_set),
                    key=lambda record: record.record_id)
    if not chosen:
        raise AggregateError("no_records")
    configurations = {json.dumps(record.versions, sort_keys=True)
                      for record in chosen}
    if len(configurations) != 1:
        raise AggregateError("records_of_several_configurations")
    identities = "\n".join([case_set] + ["%s %s" % (
        record.record_id, record.response_sha256) for record in chosen])
    return {"schema_version": SCHEMA_VERSION, "case_set": case_set,
            "records": len(chosen),
            "records_sha256": hashlib.sha256(
                identities.encode("utf-8")).hexdigest(),
            "versions": chosen[0].versions,
            "aggregates": records.aggregate(chosen)}


def manifest_bytes(manifest):
    """Return a manifest as deterministic JSON bytes."""
    return (json.dumps(manifest, sort_keys=True, indent=1)
            + "\n").encode("utf-8")


def manifest_path(manifest, directory=LINEAGE_DIR):
    """Return where a manifest is written: named by its records' digest,
    so that the same records always give the same file."""
    return ntpath.join(directory, "%s%s.json" % (
        MANIFEST_PREFIX, manifest["records_sha256"][:16]))


def write_manifest(manifest, path):
    """Write a manifest once: an identical one is left as it is, and a
    different one is never replaced."""
    data = manifest_bytes(manifest)
    if os.path.exists(path):
        with open(path, "rb") as handle:
            if handle.read() == data:
                return path
        raise ManifestConflict(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    partial = path + ".partial"
    with open(partial, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.rename(partial, path)
    return path


def tracker_outputs(aggregates):
    """Return the aggregates the tracker receives: the requests; of the
    responses written from evidence, the share the model wrote and the
    share that fell back; and the median seconds of the model's attempts
    as latency_ms. A share without responses written from evidence, and a
    latency without attempts, are left out."""
    outputs = {"requests": aggregates["requests"]}
    written = aggregates["written_from_evidence"]
    if written:
        outputs["model_written_rate"] = round(
            aggregates["written_by_the_model"] / written, 6)
        outputs["fallback_rate"] = round(
            sum(aggregates["fallbacks"].values()) / written, 6)
    if aggregates["seconds"]:
        outputs["latency_ms"] = round(
            aggregates["seconds"]["median"] * 1000, 3)
    return outputs


def lineage_of(manifest, path, checksum, case_set_version=None):
    """Return the controlled lineage of a manifest at path with the given
    SHA-256; case_set_version names the case set (its own name unless
    given)."""
    versions = manifest["versions"]
    return {
        "code_revision": versions["application"],
        "case_set_version": case_set_version or manifest["case_set"],
        "llm_configuration_fingerprint": fingerprint(versions,
                                                     MODEL_CONFIGURATION),
        # The tracker refuses the word itself; the version is its number.
        "prompt_version": versions["prompt_version"].split("-", 1)[-1],
        "evidence_schema_version": versions["projection_version"],
        "validation_configuration_fingerprint": fingerprint(
            versions, VALIDATION_CONFIGURATION),
        "evaluation_outputs": tracker_outputs(manifest["aggregates"]),
        "protected_reference": path,
        "protected_checksum": checksum,
    }


def configuration_of(manifest, case_set_version=None):
    """Return the configuration fields of a manifest's records: the case
    set and the versions that wrote and checked their responses."""
    versions = manifest["versions"]
    return {"case_set": case_set_version or manifest["case_set"],
            "prompt_version": versions["prompt_version"].split("-", 1)[-1],
            "projection_version": versions["projection_version"],
            "shape_version": versions["shape_version"],
            "candidate_contract": versions["candidate_contract"],
            "explanation_template": versions["explanation_template"],
            "wording_rules": versions["wording_rules"]}


TASK_STRATA = (("condition_list", "all lists"),
               ("condition_presence", "presence"),
               ("prediction_explanation", "explanations"))
MEASURES = (("seconds", "seconds"), ("output_tokens", "output tokens"),
            ("prompt_tokens", "prompt tokens"),
            ("peak_gpu_mib", "peak GPU MiB"))


def scalars_of(manifest):
    """Return a manifest's scalars: the requests, overall and by task; the
    shares the model wrote and that fell back; and the median, 90th
    percentile and largest of the model's measures."""
    totals = manifest["aggregates"]
    scalars = [("requests", "all", totals["requests"])]
    scalars += [("requests", stratum, totals["tasks"][task])
                for task, stratum in TASK_STRATA if task in totals["tasks"]]
    outputs = tracker_outputs(totals)
    if "model_written_rate" in outputs:
        scalars += [("model written rate", "all",
                     outputs["model_written_rate"]),
                    ("fallback rate", "all", outputs["fallback_rate"])]
    for name, metric in MEASURES:
        if totals[name]:
            scalars += [(metric, "all: %s" % part, totals[name][part])
                        for part in ("median", "p90", "largest")]
    return scalars


def build_payload(project_id, manifest, path, checksum,
                  case_set_version=None):
    """Return the controlled task of a manifest, checked by
    tracking.controlled: a visible runtime aggregates task with its
    configuration fields."""
    payload = tracking.build_task_payload(
        project_id, lineage_of(manifest, path, checksum, case_set_version),
        {}, kind="runtime_aggregates",
        configuration=configuration_of(manifest, case_set_version))
    payload["tags"].append("provenance:runtime_aggregates")
    return payload


def read_records():
    """Return every protected runtime record."""
    from ehr_evidence_app.generation import records
    return [records.read(path) for path in records.paths()]


def prepared(case_set, found=None, directory=LINEAGE_DIR):
    """Write the manifest of a case set's records; return (manifest, its
    path, its checksum)."""
    manifest = manifest_of(read_records() if found is None else found,
                           case_set)
    path = write_manifest(manifest, manifest_path(manifest, directory))
    return manifest, path, hashlib.sha256(manifest_bytes(
        manifest)).hexdigest()


def summary(manifest, path):
    """Return what a result reports: counts, the aggregates and names
    only."""
    return {"case_set": manifest["case_set"], "records": manifest["records"],
            "evaluation_outputs": tracker_outputs(manifest["aggregates"]),
            "manifest": ntpath.basename(path), "artifact_upload": False}


def offline_result(case_set, found=None, directory=LINEAGE_DIR):
    """Write a case set's manifest and check its task offline."""
    try:
        manifest, path, checksum = prepared(case_set, found, directory)
        payload = build_payload(OFFLINE_PROJECT, manifest, path, checksum)
    except AggregateError as error:
        return {"status": "FAIL", "mode": "offline", "finding": str(error)}
    except ManifestConflict:
        return {"status": "FAIL", "mode": "offline",
                "finding": "manifest_conflict"}
    except tracking.UnsafeMetadata:
        return {"status": "FAIL", "mode": "offline",
                "finding": "metadata_refused"}
    return dict(summary(manifest, path), status="PASS",
                mode="offline")


def remote_result(case_set, environ=None, adapter_factory=checker.SdkAdapter,
                  found=None, directory=LINEAGE_DIR, lstat=os.lstat):
    """Create and read back one metadata-only task describing a case set's
    records, with its configuration fields and scalars; it stays visible
    (ADR 0018). environ, adapter_factory, found, directory and lstat are
    injectable for tests."""
    env = os.environ if environ is None else environ
    started = time.monotonic()
    try:
        if checker.environment_problems(env, lstat=lstat):
            return {"status": "FAIL", "mode": "operator_remote",
                    "finding": "environment_invalid"}
        manifest, path, checksum = prepared(case_set, found, directory)
        with checker.quiet_sdk():
            adapter = adapter_factory()
            try:
                project_id = checker.connect_project(adapter)
                payload = build_payload(project_id, manifest, path, checksum)
                task_id, findings = checker.create_controlled_task(
                    adapter, project_id, payload, "runtime_aggregates",
                    scalars_of(manifest))
                if findings:
                    return {"status": "FAIL", "mode": "operator_remote",
                            "findings": sorted(set(findings)),
                            "task_id": task_id}
                return dict(summary(manifest, path),
                            status="PASS", mode="operator_remote",
                            task_id=task_id, elapsed_seconds=round(
                                time.monotonic() - started, 3))
            except checker.ProbeFailure as failure:
                return {"status": "FAIL", "mode": "operator_remote",
                        "finding": failure.finding[1]}
    except AggregateError as error:
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": str(error)}
    except ManifestConflict:
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": "manifest_conflict"}
    except tracking.UnsafeMetadata:
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": "metadata_refused"}
    except Exception as error:  # noqa: BLE001 - reported by code only
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": checker.SDK_ERRORS.get(type(error).__name__,
                                                  "aggregates_failed")}


# Synthetic Default Mode (Invented Records Only)

def invented_records():
    """Return the records of the invented requests, written by a scripted
    backend, in memory."""
    import datetime
    import uuid
    from ehr_evidence_app.generation import adapter, records, shape
    from ehr_evidence_app.qa import suite
    vocabulary = suite.synthetic_vocabulary()
    pairs = shape.synthetic_contexts()
    writer = records.scripted_writer(pairs)
    found_versions = records.versions()
    moment = datetime.datetime(2026, 9, 28, tzinfo=datetime.timezone.utc)
    found = []
    for number, (context, template) in enumerate(pairs):
        written = adapter.write(writer, context, template,
                                adapter.gate_release(
                                    context, vocabulary if context.task !=
                                    "prediction_explanation" else None))
        found.append(records.build(
            written.response, moment, uuid.UUID(int=number + 1).hex,
            found_versions, context.request,
            bundle_sha256=context.bundle_sha256, written=written,
            backend=writer.backend.describe()))
    return found


def run_synthetic():
    """Return the problems of the manifest and task of invented records;
    the task is checked with a workspace path that is never opened."""
    problems = []
    found = invented_records()
    case_set = found[0].case.case_set
    manifest = manifest_of(found, case_set)
    path = manifest_path(manifest)
    checksum = hashlib.sha256(manifest_bytes(manifest)).hexdigest()
    payload = build_payload("invented-project", manifest, path, checksum,
                            case_set_version="invented-16-v1")
    outputs = tracker_outputs(manifest["aggregates"])
    if set(outputs) != {"requests", "model_written_rate", "fallback_rate",
                        "latency_ms"}:
        problems.append("the invented records did not give every aggregate")
    if outputs["model_written_rate"] != 1.0 or outputs["fallback_rate"]:
        problems.append("the scripted model's share is wrong")
    text = json.dumps(payload)
    for record in found:
        for value in (record.request_id, record.response.text,
                      record.generation and record.generation.output):
            if value and value in text:
                problems.append("the task holds a record's content")
    if manifest_of(list(reversed(found)), case_set) != manifest:
        problems.append("the manifest depends on the records' order")
    for change in ({"prompt_version": "prompt-v9"},
                   {"evaluation_outputs": dict(outputs, rows=1)}):
        lineage = dict(lineage_of(manifest, path, checksum,
                                  "invented-16-v1"), **change)
        try:
            tracking.build_task_payload("invented-project", lineage, {})
            problems.append("the tracker accepted %s" % sorted(change)[0])
        except tracking.UnsafeMetadata:
            pass
    try:
        manifest_of(found, "dev-30-v1")
        problems.append("a case set without records was described")
    except AggregateError:
        pass
    return len(found), problems


def main(argv=None):
    """Run the default mode on invented records, or one of the operator
    modes on a case set's protected records; prints counts only."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="write the manifest of a case set's records "
                             "and check its task offline")
    parser.add_argument("--operator-remote", action="store_true",
                        help="also create the task and read it back")
    parser.add_argument("--case-set", help="the case set of the records")
    args = parser.parse_args(argv)
    modes = args.operator_real + args.operator_remote
    if modes > 1 or bool(modes) != bool(args.case_set):
        print("FAILED: give one of --operator-real and --operator-remote "
              "with --case-set")
        return 2
    if args.operator_remote:
        result = remote_result(args.case_set)
    elif args.operator_real:
        result = offline_result(args.case_set)
    else:
        count, problems = run_synthetic()
        for problem in problems:
            print("PROBLEM: %s" % problem)
        print("invented records described: %d" % count)
        print("RESULT: %s" % ("FAIL" if problems else "PASS"))
        return 1 if problems else 0
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
