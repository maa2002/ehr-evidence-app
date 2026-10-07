# Data Handling

This project processes credentialed clinical data (MIMIC-IV). Patient-bearing
content never enters Git, console output, logs or the experiment tracker.
This document describes where data lives and the controls that keep it
there. Command examples assume the session set-up in
[development.md](development.md#session-set-up).

## Data Classes

| Class | Examples | Where It May Live |
| :--- | :---: | :--- |
| Protected | Source Exports and Extracts, the Case Sets' Raw MIMIC-IV Records (`protected-data\source-records`), Identity Mappings, Model Outputs, Provenance and Split Sidecars, Prepared Evidence and Record Bundles, External Question Sets and Their Partitions, Written Test Questions, Runtime Records of the Generation (`protected-data\runtime-records`) | Workspace `protected-data\` Only |
| Private, Non-Patient | Verification Summaries, Environment Manifests, Lineage Manifests, Asset-Validation Records, Archives | Workspace `outputs\`, `records\`, `archive\` |
| Disposable | Environments, Package and Bytecode Caches, Temporary Files, Test Temp | Runtime Root |
| Publishable | Code, Configuration, Documentation, Synthetic Test Fixtures | This Repository |
| Secret | Database Credentials, Tracker Credentials | Process Environment or an Untracked Local File; Never Stored by This Project |

Aggregate verification records (statuses, counts, hashes) may be copied into
reports. Anything that identifies a person, a visit or an event, and any
per-patient value, may not.

The raw MIMIC-IV files in the cold archive are read in place, read-only,
by the record extraction alone (`sources.mimic`); only rows of the case
sets' persons are kept, without free-text comments or the identifiers of
staff and orders.

The experiment tracker holds metadata, aggregates and the files of the one
listed pretrained model, MedGemma 1.5 4B IT, with its licence notice and
agreement; no model trained on the project's data
([experiment-tracking.md](experiment-tracking.md);
[ADR 0018](decisions/0018-tracker-as-experiment-record.md)).

## Storage Layout

All project storage sits under one anchor on an internal NTFS volume:

| Root | Path | Role |
| :--- | :---: | :--- |
| Policy Anchor | `G:\Industry Dataset` | Fixed in Code; Every Configured Destination Must Lie Inside It |
| Private Workspace | `G:\Industry Dataset\ehr-evidence-workspace` | Authoritative Store for Protected Data, Outputs, Logs, Verification Records and ClearML Configuration |
| Runtime Root | `G:\Industry Dataset\ehr-evidence-runtime` | Disposable Environments, Caches and Temporary Files Only |
| Repository | `G:\Industry Dataset\ehr-evidence-app` | Publishable Content Only |

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
| :--- | :---: | :---: | :--- |
| `prepared_data` | Workspace | `protected-data\prepared` | Prepared Evidence |
| `outputs` | Workspace | `outputs` | Generated Responses, Evaluation and Audit Artefacts |
| `logs` | Workspace | `outputs\logs` | Application Run Logs |
| `verification` | Workspace | `outputs\verification` | Verification Reports |
| `test_scratch` | Workspace | `test-scratch` | Scratch for Synthetic Tests of Workspace-Only Rules |
| `clearml_local` | Workspace | `clearml` | Local ClearML Storage and Configuration-File Destination |
| `temp` | Runtime | `temp` | `TEMP`, `TMP`, `TMPDIR` |
| `pytest_temp`, `pytest_cache` | Runtime | `temp\pytest`, `cache\pytest` | pytest Base Temp and Cache |
| `pycache` | Runtime | `cache\pycache` | `PYTHONPYCACHEPREFIX` |
| `uv_cache`, `pip_cache`, `xdg_cache` | Runtime | `cache\uv`, `cache\pip`, `cache\xdg` | Package and Tool Caches |
| `hf_home`, `hf_hub`, `hf_assets`, `hf_datasets`, `torch_home` | Runtime | `cache\huggingface\...`, `cache\torch` | Model-Library Caches |
| `conda_pkgs`, `conda_envs`, `venvs`, `uv_project_env` | Runtime | `cache\conda-pkgs`, `environments\...` | Downstream Environments |
| `llm_env`, `python_installs` | Runtime | `environments\uv\ehr-evidence-llm`, `python` | The Model Environment and Its uv-Managed Interpreter (`UV_PYTHON_INSTALL_DIR`) |
| `cuda_cache` | Runtime | `cache\cuda` | CUDA Compute Cache (`CUDA_CACHE_PATH`), Which Otherwise Defaults to the User Profile on C: |
| `models` | Runtime | `models` | Downloaded Model Assets |
| `clearml_cache` | Runtime | `cache\clearml` | ClearML Cache |

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

## Path Checks

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

## Runtime Storage Preflight

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

## Repository Content Controls

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

The only files that are not text are the documentation's images, and
only in `docs/images`: each must be one complete PNG or GIF image of the
type its name says, within 1 MiB, with no block that can carry text or
metadata (text chunks, comments, colour profiles, Exif or private
blocks), so that nothing but pixels is published. The images show
invented patients only.

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

## Output Rules

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
