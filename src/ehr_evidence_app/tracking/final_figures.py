"""Keep the final evaluation's figures in the tracker, as aggregates only
(ADR 0018, ADR 0030).

The figures are read from the final evaluation's protected aggregate
records (ADR 0029): the report of the final run (each metric of ADR 0016
by stratum, in both conditions, with the paired difference and its
interval), the intervals by patient added after the figures, the prose
rubric's aggregates and the owner's agreement, and the figures of the
questions typed end to end and of the held-out read. Of the ratings,
only their aggregates are read: never a mark, a text, or the rating
model, which only the protected records name. The freeze record and the
run's configuration give the version, and every record must name the
same freeze and run.

The figures are written once as a manifest under the workspace's
outputs\\lineage, named by a digest of its content: the same figures give
the same file, and a different one in its place is refused. The manifest
becomes one visible tracker task of the kind "Final evaluation", named by
its case set and commit, with its configuration fields, its scalars and
lineage tags naming the manifest's path and checksum; it is read back and
marked completed. An index beside the manifest keeps its task, so it is
sent once.

The default mode checks a manifest and its task built from invented
records. --operator-real writes the manifest from the protected records
and checks its task offline. --operator-remote also creates the task; it
needs the tracker's configuration in the process environment, which this
module never reads or prints.

Usage:
    python -m ehr_evidence_app.tracking.final_figures
    python -m ehr_evidence_app.tracking.final_figures --operator-real \\
        --run <run identifier>
    python -m ehr_evidence_app.tracking.final_figures --operator-remote \\
        --run <run identifier>
"""

import argparse
import hashlib
import json
import ntpath
import os
import re
import sys
import time

from ehr_evidence_app.assessment import freeze as frozen
from ehr_evidence_app.tracking import connection as checker
from ehr_evidence_app.tracking import controlled as tracking
from ehr_evidence_app.tracking import measurements

SCHEMA = "final-figures"
SCHEMA_VERSION = "1.0"
KIND = "final_figures"
PROTECTED = tracking.WORKSPACE_ROOT + "\\protected-data\\assessment"
LINEAGE_DIR = tracking.WORKSPACE_ROOT + "\\outputs\\lineage"
MANIFEST_PREFIX = "final-figures-"
INDEX_NAME = "final-figures-tasks.json"
OFFLINE_PROJECT = "offline-validation"
RUN = re.compile(r"^(?P<case_set>[a-z]+-[0-9]{1,4}-v[0-9]{1,3})-"
                 r"(?P<date>20[0-9]{2}[01][0-9][0-3][0-9])T[0-9]{6}Z$")
DIGITS = 6
# The report's strata, as the tracker names them.
STRATA = {"list": "lists", "empty list": "empty lists",
          "presence, recorded": "presence recorded",
          "presence, recorded elsewhere": "presence not recorded",
          "explanation": "explanations", "all": "all"}
# Each metric of ADR 0016 in the report: its name in the tracker, and the
# part a precision, recall or F1 is of.
METRICS = {
    "exact_match": ("exact match", None),
    "condition_precision": ("precision", "conditions"),
    "condition_recall": ("recall", "conditions"),
    "condition_f1": ("F1", "conditions"),
    "scoped_accuracy": ("scoped accuracy", None),
    "coverage": ("coverage", None),
    "selective_accuracy": ("selective accuracy", None),
    "correct_answer_yield": ("correct answer yield", None),
    "over_abstention": ("over-abstention", None),
    "unsupported_answer_rate": ("unsupported answer rate", None),
    "citation_resolution": ("citation resolution", None),
    "claim_support": ("claim support", None),
    "unsupported_claim_rate": ("unsupported claim rate", None),
    "record_precision": ("precision", "records"),
    "record_recall": ("recall", "records"),
    "record_f1": ("F1", "records"),
    "preservation": ("preservation", None),
    "driver_precision": ("precision", "drivers"),
    "driver_recall": ("recall", "drivers"),
    "driver_f1": ("F1", "drivers"),
    "evidence_consistent_yield": ("evidence-consistent yield", None),
    "line_completeness": ("line completeness", None)}
CONDITIONS = ("baseline", "controlled")
COMPARED = ("difference", "lower", "upper")
# The prose rubric's kinds of text and its items judged one by one.
KINDS = {"answer": "answers", "explanation": "explanations"}
ITEMS = ("identity", "evidence", "history", "wording")
# The condition questions' intents typed end to end; the questions about
# the record are counted by intent.
INTENTS = {"list": "lists", "presence": "presence", "refuse": "refuse",
           "clarify": "clarify"}
DELIVERED = ("list", "presence")
ASKED = ("refuse", "clarify")
# The held-out read's families, as the tracker names them.
FAMILIES = {"diagnoses_list": "diagnosis lists",
            "diagnoses_ranking": "diagnosis rankings",
            "patient_demographics": "demographics",
            "admission_info": "admission information",
            "transfers": "transfers", "procedureevents": "procedures",
            "labevents": "lab results", "prescriptions": "prescriptions",
            "microbiologyevents": "microbiology"}
INSTR_TEST = "instr-held-out-reading-v2"


class FiguresError(Exception):
    """Figures that cannot be kept or sent; carries a short code."""


class ManifestConflict(Exception):
    """A different manifest already stands at the same path."""


# --------------------------------------------------------------------------
# The protected records
# --------------------------------------------------------------------------

def source_paths(run, freeze_digest, question_set,
                 protected=PROTECTED, freeze_name=frozen.RECORD_NAME):
    """Return the path of each protected record the figures come from."""
    short = freeze_digest[:16]
    return {
        "freeze": ntpath.join(protected, "freeze", freeze_name),
        "run": ntpath.join(protected, "paired", run, "run.json"),
        "report": ntpath.join(protected, "reports", run + ".json"),
        "by_patient": ntpath.join(protected, "reports",
                                  run + "-by-patient.json"),
        "end_to_end": ntpath.join(protected, "reports",
                                  "end-to-end-%s-%s.json" % (question_set,
                                                             short)),
        "ratings": ntpath.join(protected, "ratings", run, "ratings.json"),
        "agreement": ntpath.join(protected, "ratings", run,
                                 "owner-agreement.json"),
        "instr": ntpath.join(protected, "instr",
                             "%s-%s.json" % (INSTR_TEST, short))}


def read_json(path):
    """Return (document, SHA-256) of a JSON record."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        return json.loads(data.decode("utf-8")), hashlib.sha256(
            data).hexdigest()
    except FileNotFoundError:
        raise FiguresError("source_missing") from None
    except (OSError, UnicodeDecodeError, ValueError):
        raise FiguresError("source_unreadable") from None


def read_sources(run, protected=PROTECTED, freeze_name=frozen.RECORD_NAME):
    """Return ({name: document}, {name: SHA-256}) of the final
    evaluation's records of a run; the ratings are kept as their
    aggregates alone."""
    if not isinstance(run, str) or not RUN.fullmatch(run):
        raise FiguresError("run_invalid")
    freeze_path = ntpath.join(protected, "freeze", freeze_name)
    freeze, freeze_sha256 = read_json(freeze_path)
    end_to_end_name = None
    reports = ntpath.join(protected, "reports")
    if os.path.isdir(reports):
        found = [name for name in os.listdir(reports)
                 if name.startswith("end-to-end-")
                 and name.endswith("-%s.json" % freeze_sha256[:16])]
        if len(found) == 1:
            end_to_end_name = found[0]
    if end_to_end_name is None:
        raise FiguresError("source_missing")
    question_set = end_to_end_name[len("end-to-end-"):-len(
        "-%s.json" % freeze_sha256[:16])]
    paths = source_paths(run, freeze_sha256, question_set, protected,
                         freeze_name)
    documents, digests = {"freeze": freeze}, {"freeze": freeze_sha256}
    for name, path in paths.items():
        if name == "freeze":
            continue
        documents[name], digests[name] = read_json(path)
    documents["ratings"] = {"run": documents["ratings"].get("run"),
                            "document": documents["ratings"].get(
                                "document")}
    return documents, digests


def check_sources(documents, freeze_sha256):
    """Check that every record names the same freeze and run, and is of
    its kind."""
    run = documents["run"]
    try:
        named = {run["freeze_sha256"],
                 documents["end_to_end"]["freeze_sha256"],
                 documents["by_patient"]["freeze_sha256"],
                 documents["instr"]["inputs"]["freeze"]}
        runs = {run["run"], documents["by_patient"]["run"],
                documents["ratings"]["run"]}
        kinds = (run["schema"], documents["by_patient"]["schema"],
                 documents["end_to_end"]["schema"],
                 documents["instr"]["test"], documents["freeze"]["schema"])
    except (KeyError, TypeError):
        raise FiguresError("source_unexpected") from None
    if named != {freeze_sha256} or len(runs) != 1:
        raise FiguresError("sources_disagree")
    if kinds != ("paired-run", "intervals-by-patient",
                 "end-to-end-final-figures", INSTR_TEST,
                 "assessment-freeze"):
        raise FiguresError("source_unexpected")


# --------------------------------------------------------------------------
# The figures
# --------------------------------------------------------------------------

def rounded(value):
    """Return a figure to six decimals."""
    return round(float(value), DIGITS)


class Scalars:
    """The task's scalars, each (metric, series) once."""

    def __init__(self):
        """Start with none."""
        self.values = {}

    def add(self, metric, series, value):
        """Keep one scalar; a figure that is not applicable is left out."""
        if value is None:
            return
        if (metric, series) in self.values:
            raise FiguresError("figure_twice")
        self.values[(metric, series)] = (rounded(value) if isinstance(
            value, float) else value)

    def nested(self):
        """Return {metric: {series: value}}."""
        found = {}
        for (metric, series), value in sorted(self.values.items()):
            found.setdefault(metric, {})[series] = value
        return found


def comparison_scalars(scalars, report, by_patient):
    """Add each stratum's metrics in both conditions, the paired difference
    with its interval, and the interval by patient."""
    for key, stratum in STRATA.items():
        entry = report.get("strata", {}).get(key)
        if entry is None:
            raise FiguresError("stratum_missing")
        for name, figures in entry["metrics"].items():
            if name not in METRICS:
                raise FiguresError("metric_unknown")
            metric, part = METRICS[name]
            prefix = stratum if part is None else "%s: %s" % (stratum, part)
            for condition in CONDITIONS:
                scalars.add(metric, "%s: %s" % (prefix, condition),
                            figures["values"][condition])
            for field in COMPARED:
                scalars.add(metric, "%s: %s" % (prefix, field),
                            (figures["comparison"] or {}).get(field))
        for name, figures in by_patient.get("strata", {}).get(
                key, {}).items():
            if name not in METRICS or METRICS[name][1] is not None:
                raise FiguresError("metric_unknown")
            lower, upper = figures["by_patient"]
            metric = METRICS[name][0]
            scalars.add(metric, "%s: patient-level lower" % stratum, lower)
            scalars.add(metric, "%s: patient-level upper" % stratum, upper)


def rubric_scalars(scalars, ratings, agreement):
    """Add the rubric's shares by kind and condition, its paired
    difference, and the owner's agreement with the rater."""
    document = ratings.get("document") or {}
    for kind, group in KINDS.items():
        entry = document.get(kind)
        if entry is None:
            raise FiguresError("rubric_missing")
        for condition in CONDITIONS:
            counts = entry[condition]
            if not counts["texts"]:
                continue
            for item in ITEMS:
                rated = counts["wording_rated" if item == "wording"
                               else "texts"]
                if rated:
                    scalars.add(item, "%s: %s" % (group, condition),
                                counts[item] / rated)
            scalars.add("rubric pass rate", "%s: %s" % (group, condition),
                        counts["all_four"] / counts["texts"])
        for field in COMPARED:
            scalars.add("rubric pass rate", "%s: %s" % (group, field),
                        (entry.get("all_four_compared") or {}).get(field))
        marks = agreement.get(kind) or {}
        total = sum(count for owner in marks.values()
                    for count in owner.values())
        if total:
            agreed = sum(owner.get(mark, 0) for mark, owner in marks.items())
            scalars.add("owner agreement", group, agreed / total)


def stage_total(stages, stage, verdict=None):
    """Return a stage's questions, or those of one verdict."""
    verdicts = stages.get(stage) or {}
    return (sum(verdicts.values()) if verdict is None
            else verdicts.get(verdict, 0))


def typed_scalars(scalars, end_to_end):
    """Add the questions typed end to end: the condition questions by
    intent, and the questions about the record by intent."""
    conditions = end_to_end.get("conditions") or {}
    if set(conditions) != set(INTENTS):
        raise FiguresError("intents_unexpected")
    for intent, part in INTENTS.items():
        series = "condition questions: %s" % part
        stages = conditions[intent]
        scalars.add("questions", series, stage_total(stages,
                                                     "interpretation"))
        scalars.add("read right", series, stage_total(
            stages, "interpretation", "right"))
        if intent in DELIVERED:
            scalars.add("delivered correctly", series, stage_total(
                stages, "delivery", "correct"))
    records = end_to_end.get("records") or {}
    if not set(ASKED) <= set(records):
        raise FiguresError("intents_unexpected")
    answered = [stages for group, stages in records.items()
                if group not in ASKED]
    series = "record questions: answer"
    scalars.add("questions", series, sum(stage_total(
        stages, "interpretation") for stages in answered))
    scalars.add("read right", series, sum(stage_total(
        stages, "interpretation", "right") for stages in answered))
    scalars.add("delivered correctly", series, sum(stage_total(
        stages, "delivery", "correct") for stages in answered))
    for intent in ASKED:
        series = "record questions: %s" % intent
        scalars.add("questions", series, stage_total(records[intent],
                                                     "interpretation"))
        scalars.add("read right", series, stage_total(
            records[intent], "interpretation", "right"))


def held_out_scalars(scalars, instr):
    """Add the held-out read's questions and those read as expected, by
    family and in all."""
    families = instr.get("families") or {}
    if set(families) != set(FAMILIES):
        raise FiguresError("families_unexpected")
    for family, series in FAMILIES.items():
        scalars.add("questions", series, families[family]["questions"])
        scalars.add("read as expected", series,
                    families[family]["as_expected"])
    scalars.add("questions", "all", instr["questions"])
    scalars.add("read as expected", "all", instr["as_expected"])


def configuration_of(documents, explanation_config):
    """Return the task's configuration fields: the case set, the commit and
    the date of the run, the model's configuration and the versions the
    freeze names, the frozen driver limit, the freeze, the question set,
    the rubric and the baseline."""
    run, versions = documents["run"], documents["freeze"]["versions"]
    data = explanation_config
    if hashlib.sha256(data).hexdigest() != versions.get(
            "explanation_sha256"):
        raise FiguresError("configuration_not_frozen")
    try:
        drivers = json.loads(data.decode("utf-8"))["drivers"]["limit"]
        writer, limits = run["writer"], run["limits"]
        date = RUN.fullmatch(run["run"]).group("date")
        configuration = {
            "case_set": run["case_set"], "commit": run["commit"],
            "measured_on": "%s-%s-%s" % (date[:4], date[4:6], date[6:]),
            "precision": writer["precision"], "decoder": writer["decoder"],
            "model_repository": writer["model"],
            "model_revision": writer["revision"],
            "prompt_version": writer["prompt_version"].replace("prompt-",
                                                               "", 1),
            "projection_version": writer["projection_version"],
            "shape_version": writer["shape_version"],
            "candidate_contract": versions["candidate_contract"],
            "explanation_template": versions["explanation_template"],
            "wording_rules": versions["wording_rules"],
            "drivers": drivers,
            "max_prompt_tokens": limits["max_prompt_tokens"],
            "max_output_tokens": limits["max_output_tokens"],
            "max_request_seconds": limits["max_request_seconds"],
            "freeze": documents["freeze"]["version"],
            "question_set": documents["end_to_end"]["question_set"],
            "rubric": versions["prose_rubric"],
            "baseline": run["baseline"]["version"]}
    except (AttributeError, KeyError, TypeError, ValueError):
        raise FiguresError("configuration_unexpected") from None
    try:
        tracking.build_configuration(configuration)
    except tracking.UnsafeMetadata:
        raise FiguresError("configuration_refused") from None
    return configuration


def build_manifest(documents, digests, explanation_config):
    """Return the manifest of the final evaluation's figures."""
    check_sources(documents, digests["freeze"])
    scalars = Scalars()
    comparison_scalars(scalars, documents["report"],
                       documents["by_patient"])
    rubric_scalars(scalars, documents["ratings"], documents["agreement"])
    typed_scalars(scalars, documents["end_to_end"])
    held_out_scalars(scalars, documents["instr"])
    return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
            "configuration": configuration_of(documents,
                                              explanation_config),
            "requests": documents["run"]["requests"],
            "sources": dict(sorted(digests.items())),
            "scalars": scalars.nested()}


def triples(manifest):
    """Return a manifest's scalars as (metric, series, value), sorted."""
    return [(metric, series, value)
            for metric, values in sorted(manifest["scalars"].items())
            for series, value in sorted(values.items())]


def check_scalars(manifest):
    """Check every scalar against the tracker's allowlist and bounds;
    return how many there are."""
    try:
        return len(tracking.build_scalar_events(
            "offline", triples(manifest),
            tracking.TASK_KINDS[KIND]["scalars"]))
    except tracking.UnsafeMetadata:
        raise FiguresError("scalars_refused") from None


# --------------------------------------------------------------------------
# The manifest, its task and its index
# --------------------------------------------------------------------------

def manifest_bytes(manifest):
    """Return a manifest's deterministic bytes."""
    return (json.dumps(manifest, sort_keys=True, indent=1)
            + "\n").encode("utf-8")


def checksum_of(manifest):
    """Return the SHA-256 of a manifest's bytes."""
    return hashlib.sha256(manifest_bytes(manifest)).hexdigest()


def manifest_path(manifest, directory=LINEAGE_DIR):
    """Return the path a manifest is kept at, named by its digest."""
    return ntpath.join(directory, "%s%s.json" % (
        MANIFEST_PREFIX, checksum_of(manifest)[:16]))


def write_manifest(manifest, directory=LINEAGE_DIR):
    """Write a manifest once; the same manifest leaves its file as it is,
    and a different file in its place is refused. Return its path."""
    path = manifest_path(manifest, directory)
    data = manifest_bytes(manifest)
    os.makedirs(directory, exist_ok=True)
    try:
        with open(path, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        with open(path, "rb") as handle:
            if handle.read() != data:
                raise ManifestConflict(path) from None
    return path


def lineage_of(manifest, path, checksum):
    """Return the controlled lineage of a manifest at path with the given
    SHA-256."""
    configuration = manifest["configuration"]
    return {
        "code_revision": configuration["commit"],
        "case_set_version": configuration["case_set"],
        "llm_configuration_fingerprint": measurements.fingerprint(
            configuration, measurements.MODEL_FIELDS),
        "prompt_version": configuration["prompt_version"],
        "evidence_schema_version": configuration["projection_version"],
        "validation_configuration_fingerprint": measurements.fingerprint(
            configuration, measurements.VALIDATION_FIELDS),
        "evaluation_outputs": {"requests": manifest["requests"]},
        "protected_reference": path,
        "protected_checksum": checksum,
    }


def build_payload(project_id, manifest, path, checksum):
    """Return the controlled task of a manifest, checked by
    tracking.controlled."""
    payload = tracking.build_task_payload(
        project_id, lineage_of(manifest, path, checksum), {}, kind=KIND,
        configuration=manifest["configuration"])
    payload["tags"].append("provenance:final-figures")
    return payload


def read_index(directory=LINEAGE_DIR):
    """Return the task of each manifest already sent, by name."""
    path = ntpath.join(directory, INDEX_NAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            index = json.load(handle)
    except (OSError, ValueError):
        raise FiguresError("index_invalid") from None
    if not isinstance(index, dict):
        raise FiguresError("index_invalid")
    tasks = index.get("tasks")
    if index.get("schema_version") != SCHEMA_VERSION or not isinstance(
            tasks, dict) or not all(
            isinstance(name, str) and name.startswith(MANIFEST_PREFIX)
            and checker.safe_id(task_id) for name, task_id in tasks.items()):
        raise FiguresError("index_invalid")
    return tasks


def write_index(tasks, directory=LINEAGE_DIR):
    """Replace the index with the task of each manifest sent."""
    path = ntpath.join(directory, INDEX_NAME)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump({"schema_version": SCHEMA_VERSION, "tasks": tasks},
                  handle, sort_keys=True, indent=1)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)


def explanation_config_bytes():
    """Return the bytes of the repository's explanation configuration."""
    with open(os.path.join(checker.REPO_ROOT, "config", "explanation.json"),
              "rb") as handle:
        return handle.read()


def prepared(run, protected=PROTECTED, directory=LINEAGE_DIR,
             explanation_config=None):
    """Write the manifest of a run's figures; return (path, manifest)."""
    documents, digests = read_sources(run, protected)
    manifest = build_manifest(documents, digests, explanation_config
                              or explanation_config_bytes())
    check_scalars(manifest)
    return write_manifest(manifest, directory), manifest


def offline_result(run, protected=PROTECTED, directory=LINEAGE_DIR,
                   explanation_config=None):
    """Write the manifest and check its task offline."""
    try:
        path, manifest = prepared(run, protected, directory,
                                  explanation_config)
        payload = build_payload(OFFLINE_PROJECT, manifest, path,
                                checksum_of(manifest))
        sent = read_index(directory)
    except FiguresError as error:
        return {"status": "FAIL", "mode": "offline", "finding": str(error)}
    except ManifestConflict:
        return {"status": "FAIL", "mode": "offline",
                "finding": "manifest_conflict"}
    except tracking.UnsafeMetadata:
        return {"status": "FAIL", "mode": "offline",
                "finding": "metadata_refused"}
    return {"status": "PASS", "mode": "offline", "task": payload["name"],
            "scalars": len(triples(manifest)),
            "sent": ntpath.basename(path) in sent, "artifact_upload": False}


def remote_result(run, environ=None, adapter_factory=checker.SdkAdapter,
                  protected=PROTECTED, directory=LINEAGE_DIR,
                  explanation_config=None, lstat=os.lstat):
    """Create, read back and complete the task of the manifest, unless it
    was sent before, and keep it in the index. environ, adapter_factory,
    protected, directory, explanation_config and lstat are injectable for
    tests."""
    env = os.environ if environ is None else environ
    started = time.monotonic()
    try:
        if checker.environment_problems(env, lstat=lstat):
            return {"status": "FAIL", "mode": "operator_remote",
                    "finding": "environment_invalid"}
        path, manifest = prepared(run, protected, directory,
                                  explanation_config)
        sent = read_index(directory)
        name = ntpath.basename(path)
        if name in sent:
            return {"status": "PASS", "mode": "operator_remote",
                    "created": 0, "task_ids": [sent[name]],
                    "artifact_upload": False}
        with checker.quiet_sdk():
            adapter = adapter_factory()
            try:
                project_id = checker.connect_project(adapter)
                payload = build_payload(project_id, manifest, path,
                                        checksum_of(manifest))
                task_id, findings = checker.create_controlled_task(
                    adapter, project_id, payload, KIND, triples(manifest))
                if findings:
                    return {"status": "FAIL", "mode": "operator_remote",
                            "findings": sorted(set(findings)),
                            "task_id": task_id, "created": 1}
                sent[name] = task_id
                write_index(sent, directory)
            except checker.ProbeFailure as failure:
                return {"status": "FAIL", "mode": "operator_remote",
                        "finding": failure.finding[1], "created": 0}
        return {"status": "PASS", "mode": "operator_remote", "created": 1,
                "task_ids": [task_id], "scalars": len(triples(manifest)),
                "artifact_upload": False,
                "elapsed_seconds": round(time.monotonic() - started, 3)}
    except FiguresError as error:
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
                                                  "final_figures_failed")}


# --------------------------------------------------------------------------
# Synthetic default mode (invented values only)
# --------------------------------------------------------------------------

INVENTED_RUN = "invented-16-v1-20260101T000000Z"
INVENTED_FREEZE = "f" * 64
INVENTED_RATER = "invented rating model"
INVENTED_CONFIG = (b'{"drivers": {"selection": "highest_eligible_attribution",'
                   b' "limit": 5, "frozen": true}}')


def invented_figures(entry_values, compared=(0.1, 0.05, 0.15)):
    """Return one invented metric's figures in the report's form."""
    baseline, controlled = entry_values
    difference, lower, upper = compared
    return {"values": {"baseline": baseline, "controlled": controlled},
            "comparison": {"difference": difference, "lower": lower,
                           "upper": upper}}


def invented_documents():
    """Return ({name: document}, {name: SHA-256}) of invented records of
    every kind, the ratings holding marks and the rating model's name."""
    freeze = {"schema": "assessment-freeze", "version": "freeze-v3",
              "commit": "0123abc",
              "versions": {"candidate_contract": "1.1",
                           "explanation_template": "explanation-template-v3",
                           "wording_rules": "wording-rules-v5",
                           "prose_rubric": "prose-rubric-v4",
                           "explanation_sha256": hashlib.sha256(
                               INVENTED_CONFIG).hexdigest()}}
    run = {"schema": "paired-run", "run": INVENTED_RUN,
           "case_set": "invented-16-v1", "commit": "0123abc",
           "freeze_sha256": INVENTED_FREEZE, "requests": 7,
           "baseline": {"version": "baseline-v1"},
           "limits": {"max_prompt_tokens": 16384, "max_output_tokens": 768,
                      "max_request_seconds": 180},
           "writer": {"precision": "bf16-mixed", "decoder": "xgrammar",
                      "model": "google/medgemma-1.5-4b-it",
                      "revision": "0" * 40, "prompt_version": "prompt-v9",
                      "projection_version": "projection-v5",
                      "shape_version": "candidate-shape-v4"}}
    strata = {key: {"requests": 1, "metrics": {
        "exact_match": invented_figures((0.5, 0.75), (0.25, -0.1, 0.6)),
        "condition_precision": invented_figures((0.9, 1.0)),
        "coverage": invented_figures((1.0, 0.75), (-0.25, -0.6, 0.1))}}
        for key in STRATA}
    strata["empty list"]["metrics"]["condition_precision"] = {
        "values": {"baseline": 0.0, "controlled": None},
        "comparison": None}
    report = {"strata": strata}
    by_patient = {"schema": "intervals-by-patient", "run": INVENTED_RUN,
                  "freeze_sha256": INVENTED_FREEZE,
                  "strata": {"all": {"coverage": {
                      "difference": -0.25, "newcombe": [-0.6, 0.1],
                      "by_patient": [-0.65, 0.12]}}}}
    counts = {"texts": 4, "identity": 4, "evidence": 3, "history": 4,
              "wording": 4, "wording_rated": 4, "all_four": 3}
    ratings = {"run": INVENTED_RUN, "rater": {"model": INVENTED_RATER},
               "marks": {"x-001#00001|baseline": "pass"},
               "document": {kind: {"baseline": counts,
                                   "controlled": dict(counts, all_four=4,
                                                      evidence=4),
                                   "all_four_compared": {
                                       "difference": 0.25, "lower": -0.2,
                                       "upper": 0.7}} for kind in KINDS}}
    agreement = {"answer": {"pass": {"pass": 3, "fail": 1}},
                 "explanation": {}, "marked": 4, "unmarked": 0}
    stages = {"interpretation": {"right": 2, "declined": 1},
              "delivery": {"correct": 2}}
    end_to_end = {"schema": "end-to-end-final-figures",
                  "question_set": "final-v2",
                  "freeze_sha256": INVENTED_FREEZE,
                  "conditions": {intent: stages for intent in INTENTS},
                  "records": {"record_values lab": stages,
                              "refuse": {"interpretation": {"right": 1}},
                              "clarify": {"interpretation": {"right": 1,
                                                             "answered": 1}}}}
    instr = {"test": INSTR_TEST, "inputs": {"freeze": INVENTED_FREEZE},
             "questions": 18, "as_expected": 9,
             "families": {family: {"questions": 2, "as_expected": 1}
                          for family in FAMILIES}}
    documents = {"freeze": freeze, "run": run, "report": report,
                 "by_patient": by_patient, "ratings": ratings,
                 "agreement": agreement, "end_to_end": end_to_end,
                 "instr": instr}
    digests = {name: hashlib.sha256(json.dumps(
        document, sort_keys=True).encode("utf-8")).hexdigest()
        for name, document in documents.items()}
    digests["freeze"] = INVENTED_FREEZE
    return documents, digests


def run_synthetic():
    """Return (scalars checked, problems) of invented records: the
    manifest, its task and what the checks refuse. Nothing is written."""
    problems = []
    documents, digests = invented_documents()
    manifest = build_manifest(documents, digests, INVENTED_CONFIG)
    count = check_scalars(manifest)
    payload = build_payload("invented-project", manifest,
                            manifest_path(manifest), checksum_of(manifest))
    if payload["name"] != "Final evaluation: invented-16-v1, 0123abc":
        problems.append("the task is misnamed")
    scalars = manifest["scalars"]
    if scalars["coverage"].get("all: difference") != -0.25:
        problems.append("a signed difference is not kept")
    if scalars["coverage"].get("all: patient-level lower") != -0.65:
        problems.append("the interval by patient is not kept")
    if "empty lists: conditions: controlled" in scalars["precision"]:
        problems.append("a figure that is not applicable is sent")
    if scalars["owner agreement"] != {"answers": 0.75}:
        problems.append("the owner's agreement is wrong")
    if scalars["questions"]["record questions: answer"] != 3:
        problems.append("the questions about the record are miscounted")
    text = json.dumps([manifest, payload])
    if INVENTED_RATER in text or "x-001" in text:
        problems.append("a mark or the rating model leaves the records")
    for change, code in (
            ({"report": {"strata": {}}}, "stratum_missing"),
            ({"instr": dict(documents["instr"], inputs={
                "freeze": "0" * 64})}, "sources_disagree"),
            ({"report": {"strata": dict(documents["report"]["strata"], all={
                "requests": 1, "metrics": {"novel_metric": invented_figures(
                    (0.1, 0.2))}})}}, "metric_unknown")):
        try:
            build_manifest(dict(documents, **change), digests,
                           INVENTED_CONFIG)
            problems.append("the manifest accepted %s" % code)
        except FiguresError as error:
            if str(error) != code:
                problems.append("%s gave %s" % (code, error))
    try:
        build_manifest(documents, digests, INVENTED_CONFIG + b" ")
        problems.append("an explanation configuration not frozen is kept")
    except FiguresError:
        pass
    return count, problems


def main(argv=None):
    """Run the default mode on invented values, or an operator mode on the
    final evaluation's records; prints counts, codes and task IDs only."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="write the manifest and check its task "
                             "offline")
    parser.add_argument("--operator-remote", action="store_true",
                        help="also create its task, once")
    parser.add_argument("--run", default=None)
    args = parser.parse_args(argv)
    if args.operator_real and args.operator_remote:
        print("FAILED: give one of --operator-real and --operator-remote")
        return 2
    if (args.operator_real or args.operator_remote) and not args.run:
        print("FAILED: an operator mode needs --run")
        return 2
    if args.operator_remote:
        result = remote_result(args.run)
    elif args.operator_real:
        result = offline_result(args.run)
    else:
        count, problems = run_synthetic()
        for problem in problems:
            print("PROBLEM: %s" % problem)
        print("scalars checked: %d" % count)
        print("RESULT: %s" % ("FAIL" if problems else "PASS"))
        return 1 if problems else 0
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
