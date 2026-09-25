# Experiment tracking and lineage

ClearML records run metadata; the protected workspace stays the authority
for everything patient-bearing. Tracker payloads are metadata only, and every
path into the tracker goes through a fixed allowlist. Command examples assume
the session set-up in [development.md](development.md#session-set-up).

## Policy

`config/clearml_policy.json` selects ClearML (the SDK is in the application
lock) and fixes what may reach a tracker. `ehr_evidence_app.tracking.policy`
checks it read-only, without importing the SDK or contacting a service.

| Permitted tracker payloads | Never sent to a tracker |
| --- | --- |
| Non-patient run metadata and version identifiers | Patient records and identifiers |
| Code, configuration, evidence-schema and asset fingerprints | Raw or derived clinical content |
| Aggregate evaluation, validation and operational metrics | Prompts, model responses and attribution text |
| References to protected workspace files and their checksums | Case-level outputs |
| | Credentials, tokens, model files and data files |

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
`ehr-evidence-app`. Remote payloads are metadata only and there is no
offline fallback. The declaration holds no local path, account, key or
configuration content.

`ehr_evidence_app.tracking.connection` has four modes:

| Mode | Behaviour |
| --- | --- |
| *(default)* | Checks the declaration and the policy offline; no SDK import, no environment read, no network |
| `--preflight` | Loads the SDK configuration named by `CLEARML_CONFIG_FILE`, compares its hosts with the declaration before any request, authenticates, confirms the workspace and looks up the project; creates nothing |
| `--synthetic-task` | Preflight, then creates one synthetic metadata-only task, reads it back to confirm no captured script, repository, diff or package content, and archives it |
| `--controlled-tracking-probe` | Preflight, then submits one fixed, validated metadata payload through the controlled-tracking wrapper, reads it back and archives it |

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

## Controlled tracking

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
  `micro_f1`, `coverage` (bounded to 0..1) or `latency_ms` (bounded).
- **Protected references:** canonical absolute paths inside the workspace;
  traversal, alternate streams, device syntax, ambiguous or content-bearing
  components and over-long paths are rejected. References are never opened.
- **Hyperparameters:** `learning_rate`, `batch_size`, `epochs` and
  `weight_decay` only, as finite numeric scalars.

Unknown fields, free-text comments or tags, secrets, patient identifiers and
content-like fields (prompts, responses, attributions, predictions,
explanations) fail before any request. Model artefact upload is not enabled;
it would need a policy amendment, a dedicated staging path and manifest, a
privacy review and an explicit enablement gate.

## Inherited input registration

`ehr_evidence_app.tracking.registration` reads only the pinned baseline
record, verifies its digest, the canonical `diag_kg` identity, asset roles,
sizes, checksums and metric provenance, and writes the deterministic
registration manifest
`outputs\lineage\inherited-input-registration-v1.json`. Every registered
input is labelled `reused_inherited` and `not_executed_by_downstream`;
documented metrics stay `inherited_reported`. An identical existing manifest
is left untouched and a different one is refused
(`registration_conflict`). `--operator-remote` also creates one
metadata-only task with protected references, checksums and aggregate
metadata, reads it back and archives it. No model, graph, explanation,
vocabulary or source file is uploaded.

```powershell
& $AppPython -m ehr_evidence_app.tracking.registration
```

## Lineage records

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
