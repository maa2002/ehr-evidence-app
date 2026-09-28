#!/usr/bin/env python3
"""Register the inherited diag_kg baseline as protected metadata only (Step 13).

The default mode validates the pinned baseline record and writes the
deterministic registration manifest. An identical existing manifest is left
untouched; a different one is never replaced (finding registration_conflict).
--operator-remote additionally creates and reads back one metadata-only
ClearML task and marks it completed; it stays visible (ADR 0018), and
nothing else is uploaded.

Usage:
    python -m ehr_evidence_app.tracking.registration [--operator-remote]
"""
import argparse
import hashlib
import json
import os
import sys
import time
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
from ehr_evidence_app.tracking import connection as checker  # noqa: E402
from ehr_evidence_app.tracking import controlled as tracking  # noqa: E402

BASELINE_PATH = r"G:\Industry Dataset\ehr-evidence-workspace\outputs\baselines\inherited-baseline-v1.json"
# Version 1 was written before the workspace moved to G: and names the
# baseline at its former path; it stays as written (gnn.provenance pins
# it), and version 2 names the baseline where it now is.
REGISTRATION_PATH = r"G:\Industry Dataset\ehr-evidence-workspace\outputs\lineage\inherited-input-registration-v2.json"
ROLES = ("documentation", "source_explain_patient", "source_train",
         "source_model", "source_build_patient_graphs", "canonical_checkpoint",
         "canonical_metrics", "canonical_explanations", "concept_vocab",
         "label_vocab")
BASELINE_ROLES = ROLES + ("patient_graphs",)
APPROVED_BASELINE_SHA256 = "FB136BFC851D788B509B6AD996417F3D4F5568E83E585B176935B83F1DA7755E"
MAX_BASELINE_BYTES = 256 * 1024
SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


def sha256_file(path):
    """Return the streamed SHA-256 of one protected metadata file."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256(value):
    """Return True only for a 64-character hexadecimal digest string."""
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def relative_path_problem(value):
    """Return True when an upstream-relative path is not a plain relative path."""
    return (not isinstance(value, str) or not value or "/" in value
            or ":" in value or value.startswith("\\")
            or any(part in ("", ".", "..") for part in value.split("\\")))


def load_baseline(path=BASELINE_PATH):
    """Load and validate the protected Step 1 baseline JSON only."""
    info = os.lstat(path)
    if not os.path.isfile(path) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError("baseline is not a regular non-link file")
    if info.st_size > MAX_BASELINE_BYTES:
        raise ValueError("baseline exceeds the bounded metadata size")
    with open(path, "rb") as handle:
        raw = handle.read(MAX_BASELINE_BYTES + 1)
    if len(raw) > MAX_BASELINE_BYTES:
        raise ValueError("baseline exceeds the bounded metadata size")
    digest = hashlib.sha256(raw).hexdigest().upper()
    if digest != APPROVED_BASELINE_SHA256:
        raise ValueError("baseline identity does not match the approved Step 1 authority")
    baseline = json.loads(raw.decode("utf-8"))
    if not isinstance(baseline, dict):
        raise ValueError("baseline is not a JSON object")
    run = baseline.get("canonical_run")
    if (baseline.get("step") != "1" or not isinstance(run, dict)
            or run.get("name") != "diag_kg"):
        raise ValueError("baseline is not the approved diag_kg Step 1 record")
    execution = baseline.get("downstream_execution")
    if not isinstance(execution, dict) or execution.get("training_run") is not False:
        raise ValueError("baseline claims downstream execution")
    config = baseline.get("configuration")
    tool = baseline.get("capture_tool")
    if (not isinstance(config, dict) or not isinstance(config.get("path"), str)
            or not is_sha256(config.get("sha256"))
            or not isinstance(tool, dict) or not is_sha256(tool.get("sha256"))):
        raise ValueError("baseline configuration identity is invalid")
    assets_list = baseline.get("assets")
    if (not isinstance(assets_list, list) or len(assets_list) != len(BASELINE_ROLES)
            or not all(isinstance(item, dict) for item in assets_list)):
        raise ValueError("baseline asset role set is invalid")
    assets = {item.get("role"): item for item in assets_list}
    if set(assets) != set(BASELINE_ROLES):
        raise ValueError("baseline asset roles are not exact")
    for item in assets_list:
        size = item.get("size_bytes")
        if (relative_path_problem(item.get("relative_path"))
                or not isinstance(size, int) or isinstance(size, bool)
                or size < 0 or not is_sha256(item.get("sha256"))):
            raise ValueError("baseline asset identity is invalid")
    metrics = run.get("reported_metrics")
    if (not isinstance(metrics, dict)
            or metrics.get("provenance") != "inherited_reported"
            or metrics.get("execution_status") != "not_executed_by_downstream"):
        raise ValueError("baseline metric provenance is invalid")
    baseline["_approved_baseline_sha256"] = digest.lower()
    return baseline, assets


def build_registration(baseline, assets, baseline_path=BASELINE_PATH):
    """Build deterministic reused-input metadata without opening upstream files."""
    try:
        metrics = baseline["canonical_run"]["reported_metrics"]
        test = metrics["test"]["values"]
        values = {
            "macro_auroc": test["auroc_macro"],
            "macro_auprc": test["auprc_macro"],
            "best_epoch": metrics["best_epoch"],
            "history_length": metrics["history_length"],
        }
    except (KeyError, TypeError):
        raise ValueError("baseline reported metrics are incomplete")
    if (not all(isinstance(values[name], (int, float)) and
                not isinstance(values[name], bool) for name in values)
            or not all(0 <= values[name] <= 1 for name in ("macro_auroc", "macro_auprc"))
            or not isinstance(values["best_epoch"], int)
            or not isinstance(values["history_length"], int)
            or values["best_epoch"] < 0
            or values["history_length"] <= 0
            or values["best_epoch"] > values["history_length"]):
        raise ValueError("baseline reported metric domains are invalid")
    inputs = []
    for role in ROLES:
        item = assets[role]
        inputs.append({"role": role, "relative_path": item["relative_path"],
                       "size_bytes": item["size_bytes"],
                       "sha256": item["sha256"],
                       "provenance": "reused_inherited",
                       "execution_status": "not_executed_by_downstream"})
    return {"record_type": "inherited_input_registration",
            "schema_version": "1.0", "canonical_run": "diag_kg",
            "provenance": "reused_inherited",
            "execution_status": "not_executed_by_downstream",
            "baseline_reference": baseline_path,
            "baseline_sha256": baseline["_approved_baseline_sha256"],
            "capture_tool_sha256": baseline["capture_tool"]["sha256"],
            "configuration": {
                "path": baseline["configuration"]["path"],
                "sha256": baseline["configuration"]["sha256"],
                "provenance": "reused_inherited"},
            "documented_reported_metrics": values,
            "documented_reported_metrics_provenance": {
                "provenance": "inherited_reported",
                "execution_status": "not_executed_by_downstream"},
            "inputs": inputs,
            "artifact_upload": False,
            "patient_content_loaded": False}


class RegistrationConflict(ValueError):
    """An existing registration manifest differs from the rebuilt one."""


def write_registration(record, path=REGISTRATION_PATH):
    """Write the deterministic protected registration manifest.

    An identical existing manifest is left untouched. A different one is
    never replaced: RegistrationConflict is raised and a new registration
    needs a new version. New bytes go to an exclusively created .partial
    file that is fsynced and then renamed; os.rename on Windows refuses an
    existing destination. The partial file this call created is removed on
    any failure.
    """
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    payload = (json.dumps(record, indent=2, sort_keys=True)
               + "\n").encode("utf-8")
    if os.path.lexists(path):
        with open(path, "rb") as handle:
            if handle.read() == payload:
                return path
        raise RegistrationConflict("registration_conflict")
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
    return path


def build_payload(project_id, record, registration_path=REGISTRATION_PATH):
    """Build an explicit metadata payload pointing to the protected manifest."""
    lineage = {
        "code_revision": "inherited-baseline-v1",
        "case_set_version": "diag-kg",
        "llm_configuration_fingerprint": record["baseline_sha256"],
        "prompt_version": "not-applicable-inherited",
        "evidence_schema_version": "not-applicable-inherited",
        "validation_configuration_fingerprint": record["capture_tool_sha256"],
        "evaluation_outputs": record["documented_reported_metrics"],
        "protected_reference": registration_path,
        "protected_checksum": sha256_file(registration_path),
    }
    payload = tracking.build_task_payload(project_id, lineage, {},
                                          kind="registration")
    payload["tags"].extend(("provenance:inherited_reported",
                             "execution:not_executed_by_downstream"))
    return payload


def offline_result(baseline_path=BASELINE_PATH, registration_path=REGISTRATION_PATH):
    """Validate and write the protected registration manifest offline."""
    baseline, assets = load_baseline(baseline_path)
    record = build_registration(baseline, assets, baseline_path)
    write_registration(record, registration_path)
    return {"status": "PASS", "mode": "offline", "canonical_run": "diag_kg",
            "inputs": len(record["inputs"]), "artifact_upload": False,
            "registration_path": registration_path}


def remote_result(environ=None, adapter_factory=checker.SdkAdapter,
                  baseline_path=BASELINE_PATH, registration_path=REGISTRATION_PATH,
                  lstat=os.lstat):
    """Create and read back one inherited-input metadata task, which stays
    visible in the project (ADR 0018).

    environ, adapter_factory and lstat are injectable for synthetic tests.
    """
    env = os.environ if environ is None else environ
    started = time.monotonic()
    try:
        if checker.environment_problems(env, lstat=lstat):
            return {"status": "FAIL", "mode": "operator_remote"}
        baseline, assets = load_baseline(baseline_path)
        record = build_registration(baseline, assets, baseline_path)
        write_registration(record, registration_path)
        with checker.quiet_sdk():
            adapter = adapter_factory()
            try:
                project_id = checker.connect_project(adapter)
                payload = build_payload(project_id, record, registration_path)
                task_id, findings = checker.create_controlled_task(
                    adapter, project_id, payload, "registration")
                if findings:
                    return {"status": "FAIL", "mode": "operator_remote",
                            "findings": sorted(set(findings)), "task_id": task_id}
                return {"status": "PASS", "mode": "operator_remote", "canonical_run": "diag_kg",
                        "inputs": len(record["inputs"]), "artifact_upload": False,
                        "task_id": task_id, "elapsed_seconds": round(time.monotonic() - started, 3)}
            except checker.ProbeFailure as failure:
                return {"status": "FAIL", "mode": "operator_remote",
                        "finding": failure.finding[1]}
    except RegistrationConflict:
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": "registration_conflict"}
    except Exception as error:
        name = type(error).__name__
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": checker.SDK_ERRORS.get(name, "registration_failed")}


def main(argv=None):
    """Run offline validation by default or the explicit operator probe."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operator-remote", action="store_true")
    args = parser.parse_args(argv)
    if args.operator_remote:
        result = remote_result()
    else:
        try:
            result = offline_result()
        except RegistrationConflict:
            result = {"status": "FAIL", "mode": "offline",
                      "finding": "registration_conflict"}
        except (OSError, ValueError):
            result = {"status": "FAIL", "mode": "offline",
                      "finding": "registration_failed"}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
