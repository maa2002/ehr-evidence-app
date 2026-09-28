# Data handling

This project processes credentialed clinical data (MIMIC-IV). Patient-bearing
content never enters Git, console output, logs or the experiment tracker.
This document describes where data lives and the controls that keep it
there. Command examples assume the session set-up in
[development.md](development.md#session-set-up).

## Data classes

| Class | Examples | Where it may live |
| --- | --- | --- |
| Protected | Source exports and extracts, identity mappings, model outputs, provenance and split sidecars, prepared evidence, external question sets and their partitions, written test questions, runtime records of the generation (`protected-data\runtime-records`) | Workspace `protected-data\` only |
| Private, non-patient | Verification summaries, environment manifests, lineage manifests, asset-validation records, archives | Workspace `outputs\`, `records\`, `archive\` |
| Disposable | Environments, package and bytecode caches, temporary files, test temp | Runtime root |
| Publishable | Code, configuration, documentation, synthetic test fixtures | This repository |
| Secret | Database credentials, tracker credentials | Process environment or an untracked local file; never stored by this project |

Aggregate verification records (statuses, counts, hashes) may be copied into
reports. Anything that identifies a person, a visit or an event, and any
per-patient value, may not.

The experiment tracker holds metadata, aggregates and the files of the one
listed pretrained model, MedGemma 1.5 4B IT, with its licence notice and
agreement; no model trained on the project's data
([experiment-tracking.md](experiment-tracking.md);
[ADR 0018](decisions/0018-tracker-as-experiment-record.md)).

## Storage layout

All project storage sits under one anchor on an internal NTFS volume:

| Root | Path | Role |
| --- | --- | --- |
| Policy anchor | `G:\Industry Dataset` | Fixed in code; every configured destination must lie inside it |
| Private workspace | `G:\Industry Dataset\ehr-evidence-workspace` | Authoritative store for protected data, outputs, logs, verification records and ClearML configuration |
| Runtime root | `G:\Industry Dataset\ehr-evidence-runtime` | Disposable environments, caches and temporary files only |
| Repository | `G:\Industry Dataset\ehr-evidence-app` | Publishable content only |

The anchor's access-control list grants access only to the owning user,
Administrators and SYSTEM; everything below inherits it. Drive encryption is
recorded for information but is not required (see
[decisions/0001-single-volume-storage.md](decisions/0001-single-volume-storage.md)).

The roots are constants in `ehr_evidence_app.storage.policy`, not
configuration. Configuration may choose destinations inside a root; it
cannot move or widen one. The workspace and the repository are siblings, so
generated content cannot reach the Git tree through a configured path.

## Destinations

`config/paths.json` is the single source of truth for destinations. Each
entry names a key, a root (`workspace` by default, or `runtime` for the keys
fixed in `RUNTIME_DIRECTORY_KEYS`) and a path inside that root.

| Key | Root | Path | Purpose |
| --- | --- | --- | --- |
| `prepared_data` | workspace | `protected-data\prepared` | Prepared evidence |
| `outputs` | workspace | `outputs` | Generated responses, evaluation and audit artefacts |
| `logs` | workspace | `outputs\logs` | Application run logs |
| `verification` | workspace | `outputs\verification` | Verification reports |
| `test_scratch` | workspace | `test-scratch` | Scratch for synthetic tests of workspace-only rules |
| `clearml_local` | workspace | `clearml` | Local ClearML storage and configuration-file destination |
| `temp` | runtime | `temp` | `TEMP`, `TMP`, `TMPDIR` |
| `pytest_temp`, `pytest_cache` | runtime | `temp\pytest`, `cache\pytest` | pytest base temp and cache |
| `pycache` | runtime | `cache\pycache` | `PYTHONPYCACHEPREFIX` |
| `uv_cache`, `pip_cache`, `xdg_cache` | runtime | `cache\uv`, `cache\pip`, `cache\xdg` | Package and tool caches |
| `hf_home`, `hf_hub`, `hf_assets`, `hf_datasets`, `torch_home` | runtime | `cache\huggingface\...`, `cache\torch` | Model-library caches |
| `conda_pkgs`, `conda_envs`, `venvs`, `uv_project_env` | runtime | `cache\conda-pkgs`, `environments\...` | Downstream environments |
| `llm_env`, `python_installs` | runtime | `environments\uv\ehr-evidence-llm`, `python` | The model environment and its uv-managed interpreter (`UV_PYTHON_INSTALL_DIR`) |
| `cuda_cache` | runtime | `cache\cuda` | CUDA compute cache (`CUDA_CACHE_PATH`), which otherwise defaults to the user profile on C: |
| `models` | runtime | `models` | Downloaded model assets |
| `clearml_cache` | runtime | `cache\clearml` | ClearML cache |

`scripts/setup-destinations.ps1` applies the configuration to the current
PowerShell process: it validates every entry, creates missing directories
and sets the environment variables (`EHR_*`, temp, cache, pytest, uv,
ClearML and others; the script prints them). It changes the process
environment only; a new terminal starts clean. Dot-source it so the
variables stay in the calling session:

```powershell
Set-Location "G:\Industry Dataset\ehr-evidence-app"
. .\scripts\setup-destinations.ps1 -Quiet
```

`PYTHONPYCACHEPREFIX` is read when an interpreter starts, so start Python
after setup.

`ehr_evidence_app.storage.destinations` verifies the result. It first checks
the fixed roots, containment, the complete mandatory variable set and the
running interpreter's bytecode prefix, and stops before any write if one
fails. It then writes and reads back markers, writes temporary files
explicitly into the configured temp directory and exercises the bytecode
cache, and writes `outputs\verification\destination-report.json`.

Each destination is labelled with its evidence class:

- **probe**: an operation was performed against it (project directories,
  explicit temp files, bytecode cache);
- **declaration**: the variable holds the configured value and the directory
  is writable, but the owning tool (Hugging Face, Torch, pip, uv, conda,
  pytest, ClearML) was not run by the verifier.

## Path checks

`ehr_evidence_app.storage.policy` and the matching preflight in the setup
script check every destination before anything is created, written, copied
or connected.

Lexical checks, on the path as given:

- absolute drive path only: no relative, drive-relative, UNC or device
  path, and no forward slashes;
- no empty, `.` or `..` component, no trailing dot or space, no reserved
  character, alternate data stream, reserved device name or 8.3 short name;
- inside the root named for the destination (component-wise), outside the
  repository, and no `.git` component.

On-disk checks, without following links, from the drive root down:

- no existing component may be a symlink, junction or other reparse point;
- no existing ancestor, and no existing directory leaf, may contain a
  `.git` entry;
- ancestors must be directories and an existing leaf must be the expected
  kind. The first missing component ends the walk; nothing is created by a
  check.

The mandatory environment set is fixed in code (`DIRECTORY_VARIABLES`,
`FILE_VARIABLES`, `TEMPLATE_VARIABLES`, `LITERAL_VARIABLES`). Setup and the
verifier refuse a missing or duplicated variable, a variable bound to the
wrong key, a `PYTEST_ADDOPTS` value other than the canonical template, a
`CLEARML_OFFLINE_MODE` other than `1`, and a list-valued path containing `;`
or `,`.

Permitted read-only sources:

- the local PostgreSQL database `mimiciv_omop` (see
  [source-data.md](source-data.md) for the connection policy);
- the installed `psql.exe` and Python interpreters on C:;
- `C:\omop_export`, exactly that directory, as a copy source;
- the Athena vocabulary directory on the archive disk
  (`W:\Industry Dataset\Athena`), exactly that directory, for the four
  pinned vocabulary files that `qa.vocabulary` reads
  ([ADR 0006](decisions/0006-condition-vocabulary.md)).

Any other source must lie under the policy anchor.

## Runtime storage preflight

`ehr_evidence_app.storage.runtime` checks the storage volume with read-only
PowerShell queries:

- the expected disk model, bus and media; fixed NTFS volume; healthy volume
  and disk; at least 10 GiB free;
- a pinned volume identity digest (a versioned SHA-256 over disk and volume
  identifiers; raw GUIDs are never stored);
- plain path components with no reparse point or `.git`;
- a protected access-control list on the anchor and the runtime root that
  grants full control only to SYSTEM, Administrators and the current user.

The setup script and the destination verifier repeat this gate read-only
before they create a runtime directory, write a marker or set a variable.
`--create-root` creates only missing runtime-root components, each with the
restrictive ACL, and removes what it created if a later check fails. The
sanitised record is `outputs\verification\runtime-storage-preflight.json`.

```powershell
& $AppPython -m ehr_evidence_app.storage.runtime
& $AppPython -m ehr_evidence_app.storage.destinations
```

## Repository content controls

Three layers keep protected and generated content out of Git:

1. **Location.** Generated and private content is written to the workspace
   or the runtime root, never inside the repository.
2. **`.gitignore`.** Data, graphs, model weights, serialised arrays, CSV and
   spreadsheet files, logs, JSONL traces, archives, caches, environments,
   tracker directories, secrets and local tool state are ignored. Names that
   are unambiguously generated or private are ignored at any depth; generic
   names such as `data` and `models` only at the root.
3. **Publication audit.** `ehr_evidence_app.quality.publication` checks the
   ignore rules, the candidate files, the staged index and every object
   reachable from any ref or reflog against `config/publishable_policy.json`.

The audit is read-only and inspects only the repository that contains it.
It runs from any checkout, including a CI runner, as long as the checkout
overlaps neither the private workspace nor the runtime root. It runs Git
without a shell, with an explicit
`--git-dir` and `--work-tree`, replacement objects disabled and every
inherited `GIT_*` variable removed. It refuses shallow or grafted history.
Every publishable file and history object must be UTF-8 text within 1 MiB
and is scanned for:

- private keys and token shapes (cloud, source-hosting, model-provider,
  messaging and payment keys; JSON web tokens);
- credential URIs and quoted or unquoted secret assignments;
- machine-specific user paths;
- numeric patient-identifier assignments (`person_id`, `subject_id`,
  `hadm_id`, `patient_index` with two or more digits).

Documented placeholders (`<password>`, `${NAME}`, `SYNTHETIC`, `EXAMPLE`,
`REDACTED` and similar) are exempt from the secret rules. Identifier
assignments are allowed only in declared synthetic fixtures under `tests/`.
Output names scopes, paths, rule IDs and counts only; content, object IDs
and matched values are never printed.

```powershell
& $AppPython -m ehr_evidence_app.quality.publication
```

Exit codes: `0` pass, `1` findings, `2` operational failure (never reported
as a clean audit). The policy can be tightened in configuration but not
weakened; a weakened policy is an operational failure.

## Secrets

- Database credentials are read from `PGUSER` and `PGPASSWORD` in the
  process environment only. Their values are never printed, logged, passed
  as arguments or stored.
- The ClearML configuration file stays at an untracked local path named by
  `CLEARML_CONFIG_FILE` in the process that needs it. Project code inspects
  it with `lstat` only; only the ClearML SDK reads it.
- Tests that need a secret-shaped value assemble it from fragments at run
  time, so no secret-shaped literal appears in the source.

## Output rules

- Console output and logs carry statuses, counts, hashes, finding codes and
  public identifiers of artefacts only. `generation.records --operator-real
  --audit-logs` checks the routine logs for secrets, user paths, patient
  identifiers and the content of prompts, evidence views, documents,
  bundles and records, and names only the rule of each finding.
- Error messages are sanitised: exception text can quote file content, so
  unexpected exceptions are reported by type name only.
- The upstream bridge prints predictions and drivers; wrappers run it with
  Python logging disabled and its output discarded.

## Limitations

- Path checks run at one moment. A concurrent external change between a
  check and a write is not prevented; the checks are a path policy, not an
  access control. The access boundary for the application is planned work.
- Python writes some files during interpreter start-up (for example bytecode
  for imported modules). They are routed only when setup ran before the
  interpreter started.
- Hugging Face, Torch, pip, uv, conda, pytest and ClearML routing is
  configured, not observed, until each tool runs.
- The audit's signatures are heuristics. A secret in an unlisted format, or
  a patient value outside the listed assignment forms, can pass.
