# EHR Evidence App

Evidence-grounded question answering over OMOP patient records, and
explanation of an inherited graph-neural-network diagnosis model, for
MIMIC-IV 3.1 mapped to the OMOP Common Data Model.

> Research prototype for retrospective analysis. Not for clinical use.

## What it does

For one selected patient, the application will:

1. answer scoped questions about recorded conditions ("Which conditions are
   recorded before the final admission?", "Is heart failure recorded?"), and
2. explain an existing prediction of the inherited E-TRANS GNN from its
   gradient attribution and the patient's recorded events.

Every answer names the patient and scope, states coverage, cites readable
evidence with provenance, and ends in one explicit outcome: success,
clarification, abstention, validation rejection or operational error.
Predictions and attributions are presented unchanged, as unsigned,
non-causal model sensitivities.

## Status

The storage and data-handling controls, source preparation,
experiment-tracking foundations and the integration of the inherited GNN are
complete and closed for a 10-person development pilot, and evidence
bundles (request and response contracts, scoped condition evidence, and
prediction evidence linked to recorded events) are built for the pilot and
for a 30-case development set and a 100-case evaluation set drawn from the
upstream validation and test partitions. Question
interpretation, language-model generation, validation, the API and
interface, and the evaluation are planned; see the
[roadmap](docs/roadmap.md) and the [changelog](CHANGELOG.md).

## Architecture

```text
OMOP CDM (PostgreSQL, read-only)          inherited E-TRANS GNN (read-only)
        |                                           |
  source extracts                    offline predictions, attributions,
        |                            identity mapping, provenance, closure
        +-------------> evidence objects <----------+
                              |
             question interpretation -> LLM generation
                              |
               validation and release gate -> grounded answer
```

Code is one package, `src/ehr_evidence_app`:

| Package | Responsibility |
| --- | --- |
| `storage` | Fixed storage roots, path checks, destination set-up, volume preflight |
| `sources` | Legacy OMOP export staging; bounded read-only extraction |
| `gnn` | Inherited model identity, validation, identity mapping, parity, outputs, provenance, run binding, Phase 3 closure, event linkage |
| `evidence` | Request and response contracts, evidence records, scoped retrieval, claim policy, QA and explanation bundles |
| `casesets` | Versioned development and evaluation case sets: registry and seeded selection |
| `tracking` | Tracker policy, ClearML connection checks, controlled tracking, lineage |
| `environment` | Application and GNN environment verification |
| `quality` | Publication audit of repository content and history; release checks |

See [docs/architecture.md](docs/architecture.md).

## Getting started

Requirements: Windows 11 with PowerShell 5.1, Git, uv and an existing Python
3.12.8 interpreter. Real-data work additionally needs the inherited GNN tree
and environment and a local OMOP database; tests need neither.

```powershell
Set-Location "G:\Industry Dataset\ehr-evidence-app"
. .\scripts\setup-destinations.ps1 -Quiet
$AppPython = Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe'
$env:PYTHONPATH = Join-Path (Get-Location) 'src'

& $AppPython -m pytest -q
& $AppPython -m ehr_evidence_app.quality.publication
```

Creating the application environment is described in
[docs/environments.md](docs/environments.md); daily workflow and conventions
in [docs/development.md](docs/development.md).

## Data protection

MIMIC-IV is credentialed data. This repository contains code,
configuration, documentation and invented test fixtures only.

- Patient-bearing data and outputs live in a private workspace outside the
  repository, on a permission-restricted volume.
- Console output, logs and tracker payloads carry statuses, counts, hashes
  and finding codes only.
- Credentials come from the process environment or untracked local files and
  are never stored by the project.
- A publication audit checks the working tree, index and full Git history for
  protected paths, secrets, machine paths and patient identifiers.

See [docs/data-handling.md](docs/data-handling.md).

## Documentation

| Document | Contents |
| --- | --- |
| [Architecture](docs/architecture.md) | Purpose, data flow, packages, configuration, design rules |
| [Data handling](docs/data-handling.md) | Storage layout, destinations, path checks, publication audit, secrets |
| [Source data](docs/source-data.md) | OMOP source, staged exports, bounded pilot extraction |
| [Inherited GNN](docs/inherited-gnn.md) | Model, bridge contract, identity mapping, target and history, parity, limitations |
| [Model outputs](docs/model-outputs.md) | Published records, superseded flags, Phase 4 admission, claim policy |
| [Record evidence](docs/evidence.md) | Contracts, evidence records, scopes and coverage, event linkage, bundles, evidence claim policy |
| [Case sets](docs/case-sets.md) | Development and evaluation case sets: registry, draw rule, preparation stages |
| [Experiment tracking](docs/experiment-tracking.md) | ClearML policy and connection, controlled tracking, lineage |
| [Environments](docs/environments.md) | Application and GNN environments |
| [Development](docs/development.md) | Set-up, tests, quality gates, CI, conventions, releases |
| [Roadmap](docs/roadmap.md) | Phases and work items |
| [Decisions](docs/decisions/) | Architecture decision records |
| [Changelog](CHANGELOG.md) | Release history |

## Data and model sources

- MIMIC-IV 3.1 (PhysioNet credentialed access), transformed to OMOP CDM with
  the OHDSI MIMIC ETL. No data are distributed with this repository.
- The E-TRANS patient-journey GNN, replicated on the same data, is used as
  inherited and is not modified.
