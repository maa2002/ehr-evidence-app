# Experiment Tracking and Lineage

ClearML is the project's experiment record; the protected workspace stays
the authority for everything patient-bearing. Tracker payloads are metadata,
aggregates and the files of the one listed pretrained model
([ADR 0018](decisions/0018-tracker-as-experiment-record.md)), and every path
into the tracker goes through a fixed allowlist. Command examples assume the
session set-up in [development.md](development.md#session-set-up).

## Policy

`config/clearml_policy.json` selects ClearML (the SDK is in the application
lock) and fixes what may reach a tracker. `ehr_evidence_app.tracking.policy`
checks it read-only, without importing the SDK or contacting a service.

| Permitted Tracker Payloads | Never Sent to a Tracker |
| :--- | :--- |
| Non-Patient Run Metadata and Version Identifiers | Patient Records and Identifiers |
| Code, Configuration, Evidence-Schema and Asset Fingerprints | Raw or Derived Clinical Content |
| Aggregate Evaluation, Validation and Operational Metrics | Prompts, Model Responses and Attribution Text |
| References to Protected Workspace Files and Their Checksums | Case-Level Outputs |
| The Listed Pretrained Model's Files, With Its Licence Notice, Agreement Copy and Manifest | Credentials, Tokens and Data Files |
| | Models Trained on the Project's Data, Such as the GNN Checkpoints |

A case-level item is represented in the tracker only by its protected
workspace reference and checksum. Tracker metadata may be cached in the
runtime root (`clearml_cache`); protected artefacts and any future local
server volumes belong in the workspace. The verifier fails when the policy
holds a concrete remote setting, permits a patient-bearing or credential
category, contains a secret-shaped value or patient-identifier assignment,
places protected artefacts outside the workspace or on the runtime root, or
misdescribes a lineage field. Exit codes: `0` pass, `1` fail, `2`
operational failure.

The policy defines the lineage fields later records must carry:
`code_revision`, `case_set_version`, `llm_configuration`, `prompt_version`,
`evidence_schema_version`, `validation_configuration` and
`evaluation_outputs`.

## Connection

`config/clearml_connection.json` declares the hosted target: the web
(`https://app.clear.ml`), API (`https://api.clear.ml`) and files
(`https://files.clear.ml`) servers, the workspace and the project
`ehr-evidence-app`. Remote payloads are metadata, save the listed model's
files, and there is no offline fallback. The declaration holds no local
path, account, key or configuration content.

`ehr_evidence_app.tracking.connection` has four modes:

| Mode | Behaviour |
| :--- | :--- |
| *(Default)* | Checks the Declaration and the Policy Offline; No SDK Import, No Environment Read, No Network |
| `--preflight` | Loads the SDK Configuration Named by `CLEARML_CONFIG_FILE`, Compares Its Hosts With the Declaration Before Any Request, Authenticates, Confirms the Workspace and Looks Up the Project; Creates Nothing |
| `--synthetic-task` | Preflight, Then Creates One Synthetic Metadata-Only Task, Reads It Back to Confirm No Captured Script, Repository, Diff or Package Content, and Archives It |
| `--controlled-tracking-probe` | Preflight, Then Submits One Fixed, Validated Metadata Payload Through the Controlled-Tracking Wrapper, Reads It Back and Archives It |

The live modes use only the SDK's session and configuration classes;
`Task.init` and `Task.create` are never called, so framework, argument,
stream, resource, repository and package capture never start. Requests use a
synthetic worker name. The configuration file is inspected with `lstat` only
and its path is never printed, copied or hashed.

Fail-closed rules include: `CLEARML_CONFIG_FILE` unset, relative, missing,
linked or inside the repository; offline mode still set; any variable that
overrides hosts, keys, the configuration file, TLS verification or
verbosity; a cache directory outside the runtime root; a host that differs
from the declaration; authentication, workspace or project mismatches; and a
read-back task that shows captured content.

Output carries rule IDs, declared public values, HTTP status codes, exception
class names, timings and the created task ID only.

```powershell
& $AppPython -m ehr_evidence_app.tracking.policy
& $AppPython -m ehr_evidence_app.tracking.connection
```

Live checks run in a dedicated PowerShell process, because the setup script
declares offline mode for ordinary work:

```powershell
. .\scripts\setup-destinations.ps1 -Quiet
$env:CLEARML_CONFIG_FILE = '<existing local ClearML configuration file>'
Remove-Item Env:CLEARML_OFFLINE_MODE
& $AppPython -m ehr_evidence_app.tracking.connection --preflight
```

Use a process-level timeout: the SDK retries unreachable hosts.

## Controlled Tracking

`ehr_evidence_app.tracking.controlled` is a pure validator that builds task
metadata from fixed task fields, the lineage schema and an explicit scalar
hyperparameter allowlist. It does not import ClearML, read the environment or
touch credentials.

- **Lineage tags:** `code_revision`, `case_set_version`,
  `llm_configuration_fingerprint`, `prompt_version`,
  `evidence_schema_version`, `validation_configuration_fingerprint`,
  aggregate `evaluation_outputs`, `protected_reference` and
  `protected_checksum`. Fingerprints and checksums are SHA-256 hex strings.
- **Evaluation outputs:** finite aggregates named `accuracy`, `macro_f1`,
  `micro_f1`, `coverage`, `macro_auroc`, `macro_auprc`,
  `model_written_rate` or `fallback_rate` (bounded to 0..1), `best_epoch`,
  `history_length` or `requests` (whole numbers) or `latency_ms`
  (bounded). The runtime aggregates' three names were added with the
  project owner's approval (2026-09-28).
- **Protected references:** canonical absolute paths inside the workspace;
  traversal, alternate streams, device syntax, ambiguous or content-bearing
  components and over-long paths are rejected. References are never opened.
- **Hyperparameters:** `learning_rate`, `batch_size`, `epochs` and
  `weight_decay` only, as finite numeric scalars.
- **Kinds:** a task is a probe, an inherited input registration, a set of
  runtime aggregates, a generation measurement or the final evaluation's
  figures, each with a fixed name and type. The aggregates and measurements are named after some of their
  configuration fields, such as `Generation measurement: dev-30-v1,
  bf16-mixed, prompt v9, 87027bd`. Only a probe is archived; a lineage
  task is read back and marked completed, so it stays in view and cannot
  be edited in the web application without a reset
  ([ADR 0018](decisions/0018-tracker-as-experiment-record.md)).
- **Configuration fields:** a section `Configuration` of fields from a
  fixed list, each in one form: `case_set`, `commit`, `measured_on`,
  `precision`, `decoder`, `model_repository`, `model_revision`,
  `prompt_version` (its number, such as `v9`), `projection_version`,
  `shape_version`, `candidate_contract`, `explanation_template`,
  `wording_rules`, `drivers`, `per_group`, the limits and the lower limits
  a run was also counted within.
- **Scalars:** a fixed metric (`requests`, `released`, `released within
  limits`, `outcome right`, `release rate`, `model written rate`,
  `fallback rate`, `precision`, `recall`, `F1`, `seconds`, `output tokens`,
  `prompt tokens`, `tokens a second`, `peak GPU MiB`) under a fixed series:
  a stratum of the benchmark (`lists`, `empty lists`, `presence recorded`,
  `presence not recorded`, `explanations`), a union (`all lists`,
  `presence`, `all`), alone or with a part (`conditions`, `records`,
  `drivers`, `history lines`, `context lines`, `median`, `p90`,
  `smallest`, `largest`, `total`), such as `F1` / `lists: conditions`.
  Values are bounded; counts are whole numbers and shares lie between 0
  and 1. The final evaluation's figures have names, series and
  configuration fields of their own
  ([ADR 0030](decisions/0030-final-figures-in-the-tracker.md)); there, a
  difference or the limit of an interval lies between -1 and 1, and a
  task holds up to 1,000 scalars, where other kinds hold 400.

Unknown fields, free-text comments or tags, secrets, patient identifiers and
content-like fields (prompts, responses, attributions, predictions,
explanations) fail before any request.

## Inherited Input Registration

`ehr_evidence_app.tracking.registration` reads only the pinned baseline
record, verifies its digest, the canonical `diag_kg` identity, asset roles,
sizes, checksums and metric provenance, and writes the deterministic
registration manifest
`outputs\lineage\inherited-input-registration-v2.json`. Version 1 was
written before the workspace moved to G: and names the baseline at its
former path; it stays as written, since the model provenance record pins
it, and version 2 names the baseline where it now is. Every registered
input is labelled `reused_inherited` and `not_executed_by_downstream`;
documented metrics stay `inherited_reported`. An identical existing manifest
is left untouched and a different one is refused
(`registration_conflict`). `--operator-remote` also creates one
metadata-only task with protected references, checksums and aggregate
metadata, reads it back and marks it completed; the task stays in view. No
model, graph, explanation, vocabulary or source file is uploaded.

```powershell
& $AppPython -m ehr_evidence_app.tracking.registration
```

## Runtime Aggregates

`ehr_evidence_app.tracking.aggregates` describes the protected runtime
records of one case set (`generation.records`) to the tracker as
aggregates only. The records must all have been written under one
configuration. Their counts and measures are written as a deterministic
manifest, `outputs\lineage\runtime-aggregates-<digest>.json`, named by a
digest of the case set and of the records' identities and responses: the
same records give the same file, an identical one is left untouched and a
different one is refused (`manifest_conflict`). The task names the
application version, the case set, the fingerprints of the model's
configuration (the model, the limits and the driver selection) and of the
validators' versions, the prompt's version by its number and the
projection's version, the manifest's path and checksum, and four
aggregates: `requests`, `model_written_rate` and `fallback_rate` (of the
responses written from evidence) and `latency_ms` (the median of the
model's attempts). The task, named by its case set and prompt version,
also carries the versions as configuration fields and, as scalars, the
requests by task, the two shares, and the median, 90th percentile and
largest of the model's seconds, tokens and peak GPU memory. No record,
prompt, response, identifier or file is uploaded. `--operator-real` writes
the manifest and checks the task offline; `--operator-remote` also creates
it, reads it back and marks it completed, as the registration does.

```powershell
& $AppPython -m ehr_evidence_app.tracking.aggregates
& $AppPython -m ehr_evidence_app.tracking.aggregates --operator-real --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.tracking.aggregates --operator-remote --case-set dev-30-v1
```

## Measurements

Each benchmark run of the generation is kept as a measurement: a record in
the workspace, `outputs\measurements\measurement-<digest>.json`, written
once and named by a digest of its content, and a task of the kind
`Generation measurement`. A record holds the run's configuration fields
and, by stratum, the requests, those released as the model wrote them
and their release rate, those released within a lower limit, how many
chose the right outcome, the precision, recall and F1 of each part scored
against the correct documents, and the seconds, tokens, tokens a second
and peak GPU memory of the model's attempts; nothing of a request, a
document or a response. `generation.measure --record` writes the record
of a run ([generation.md](generation.md#commands)); the tracked files
must be as committed, before and after the run, so that the record names
the commit that ran.

`config/measurement_history.json` holds the runs of Phase 7 since the
structured evidence of [ADR 0014](decisions/0014-structured-evidence.md),
as the execution log records them: 17 runs, each at its commit. They are
written as records marked backfilled; the development result of the
configuration, assembled from runs whose prompts are those of `87027bd`
byte for byte, is also marked assembled. A value the log does not give
for one stratum is left out, never inferred.

`ehr_evidence_app.tracking.measurements` sends each record once. Its task
carries the configuration fields and the scalars, tags marking it as a
measurement and, where so, backfilled or assembled, and lineage tags
naming the commit, the case set, fingerprints of the configuration, the
record's path and checksum, and its requests, release rate and fallback
rate. An index beside the records, `tracker-tasks.json`, keeps the task
of each record, so a record is never sent twice; a correction is a new
record and a new task. `--operator-real` writes the history's records and
checks every task offline; `--operator-remote` also creates the tasks of
the records not yet sent, reads each back and marks it completed, and
stops at the first finding.

```powershell
& $AppPython -m ehr_evidence_app.tracking.measurements
& $AppPython -m ehr_evidence_app.tracking.measurements --operator-real
& $AppPython -m ehr_evidence_app.tracking.measurements --operator-remote
```

In the web application, the project's experiments table can show the
configuration fields (such as `case_set`, `precision`, `prompt_version`
and `commit`) and the last value of any scalar (such as `release rate` /
`all`) as columns, and compares selected tasks side by side.

## The Final Evaluation's Figures

`ehr_evidence_app.tracking.final_figures` keeps the final evaluation's
figures ([final-figures.md](final-figures.md)) in the tracker
([ADR 0030](decisions/0030-final-figures-in-the-tracker.md)). It reads the
final evaluation's protected aggregate records: the report of the run,
the intervals by patient, the ratings' aggregates and the owner's
agreement, the end-to-end figures and the held-out read, each of which
must name the same freeze and run. It never reads the ratings' marks or
the rating model. The figures are written once as a manifest,
`outputs\lineage\final-figures-<digest>.json`, named by a digest of its
content: the same figures give the same file, an identical one is left
untouched and a different one is refused (`manifest_conflict`). The task,
`Final evaluation: <case set>, <commit>`, carries the configuration of
the version evaluated (the model, the versions, the limits, the driver
limit as frozen, the freeze, the question set, the rubric and the
baseline), lineage tags naming the manifest's path and checksum, and its
scalars: each metric of ADR 0016 by stratum in both conditions, with its
paired difference, interval and interval by patient; the prose rubric
and the owner's agreement; the questions typed end to end; and the
held-out read by family. An index beside the manifest,
`final-figures-tasks.json`, keeps the task, so it is sent once.
`--operator-real` writes the manifest and checks the task offline;
`--operator-remote` also creates it, reads it back and marks it
completed.

```powershell
& $AppPython -m ehr_evidence_app.tracking.final_figures
& $AppPython -m ehr_evidence_app.tracking.final_figures --operator-real --run <run identifier>
& $AppPython -m ehr_evidence_app.tracking.final_figures --operator-remote --run <run identifier>
```

## The Model

`ehr_evidence_app.tracking.models` keeps MedGemma 1.5 4B IT, the one model
the policy lists, in the tracker. It is published pretrained by its
developer and was never trained on the project's data; the laptop keeps
running it from the runtime root, and the tracker's copy is the project's
shared reference. Its licence, the Health AI Developer Foundations terms,
requires a copy of the agreement and a notice to go with it.

`--operator-real` checks every file of the model against its size and
SHA-256 in `config/generation.json` and writes the staging directory
`outputs\model-staging\google--medgemma-1.5-4b-it\<revision>`: a
`NOTICE` with the licence's notice and that no file was modified, and a
`MANIFEST.json` naming the model's repository, revision and licence and
each file's name, kind, size, SHA-256 and provenance, with the
confirmation that it holds nothing patient-derived or secret. The project
owner first saves a copy of the agreement there, as one of
`HAI-DEF-terms-of-use.pdf`, `.html` or `.txt`; without it nothing is
staged. `--operator-remote` then uploads the model's files, the notice,
the agreement and, last, the manifest to the files server under
`ehr-evidence-app/models/medgemma-1.5-4b-it/<revision>/`, checking the
size the server holds for each upload, asked for without content coding
since the server may compress a reply; a file already there at its size
is not sent again.
It registers the revision once in the project's model registry, as
`MedGemma 1.5 4B IT` with the manifest's address, the tags `pretrained`,
`licence:health-ai-developer-foundations` and `revision:<revision>`, and
metadata (repository, revision, licence, files, size and the manifest's
SHA-256), and reads it back. The upload is about 8.6 GB. The GNN
checkpoints and anything else derived from patient data are never
uploaded.

```powershell
& $AppPython -m ehr_evidence_app.tracking.models
& $AppPython -m ehr_evidence_app.tracking.models --operator-real
& $AppPython -m ehr_evidence_app.tracking.models --operator-remote
```

## Lineage Records

`config/lineage_schema.json` defines a five-stage chain:

```text
inherited_input -> evidence_preparation -> generation_configuration -> evaluation_output -> application_version
```

Each node has a stable ID, chain ID, type, version, status (`defined`,
`prepared`, `completed`, `failed` or `abstained`) and predecessor IDs.
Protected references and checksums identify private outputs without
embedding their content. `ehr_evidence_app.tracking.lineage` validates a
record (or a built-in synthetic chain) and reports sanitised finding codes
for missing or duplicate nodes, invalid types or statuses, broken
predecessors, cycles and bad references.

```powershell
& $AppPython -m ehr_evidence_app.tracking.lineage
& $AppPython -m ehr_evidence_app.tracking.lineage --record <lineage-record.json>
```

Real lineage records are produced once evidence preparation, generation and
evaluation exist.
