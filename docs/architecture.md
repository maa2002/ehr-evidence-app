# Architecture

## Purpose

The EHR evidence application adds a controlled natural-language layer to an
existing patient-journey model: the E-TRANS heterogeneous graph neural
network (GNN), trained on MIMIC-IV 3.1 mapped to the OMOP Common Data Model.
For one selected patient it supports two capabilities:

1. **Scoped recorded-condition QA.** List the conditions recorded within a
   permitted scope, or check whether a named condition is recorded there.
2. **Prediction explanation.** Explain an existing GNN diagnosis prediction
   from its gradient attribution and the patient's recorded events.

Every answer names the patient and the scope, states coverage and cites
readable evidence with provenance. Each request ends in exactly one outcome:
success, clarification, abstention, validation rejection or operational
error. A negative answer says that a condition is *not recorded within the
selected scope*; it never implies clinical absence.

Out of scope: retraining or modifying the GNN, changing the OMOP
transformation, clinical diagnosis or treatment, and prospective or
production deployment.

## Data flow

```text
MIMIC-IV 3.1 --(OHDSI ETL, upstream)--> OMOP CDM in PostgreSQL (read-only)
                                                |
                  +-----------------------------+------------------------+
                  |                                                      |
        source extracts (sources)                    inherited E-TRANS GNN (gnn)
        staged exports, bounded pilot                graphs, checkpoint, bridge
                  |                                                      |
                  |                    offline predictions + attributions,
                  |                    identity mapping, provenance, closure
                  |                                                      |
                  +------------------> evidence objects <----------------+
                                              |
                              question interpretation and scope
                                              |
                              LLM generation (QA / explanation)
                                              |
                              validation and release gate
                                              |
                              grounded answer (API / interface)
```

The GNN is used exactly as inherited. Its predictions and attributions are
regenerated offline through its own bridge code, bound to verified patient
and visit identities and stored as protected, versioned records. Later layers
consume those records; they never recompute or alter a prediction or an
attribution.

## Implementation status

| Layer | Status |
| --- | --- |
| Storage, data handling and repository quality gates | implemented |
| Source preparation: staged exports, bounded read-only pilot | implemented |
| Experiment tracking and lineage foundations | implemented |
| Inherited GNN integration | implemented and closed for the 10-person development pilot |
| Evidence contracts, records, event linkage and bundles | implemented for the development pilot and the development and evaluation case sets |
| Question interpretation, concept and scope resolution, scoped answers | implemented (rule-based); held-out and written wording tests done |
| LLM generation, validation | planned |
| API, interface and evaluation | planned |

See [roadmap.md](roadmap.md) for the planned work.

## Package layout

All code is in `src/ehr_evidence_app`. Every module with a command-line
interface runs as `python -m ehr_evidence_app.<package>.<module>`.

| Package | Responsibility | Modules |
| --- | --- | --- |
| `storage` | Fixed storage roots, destination and path checks, process-local destination setup, runtime-volume preflight | `policy`, `destinations`, `runtime` |
| `sources` | Staging of the legacy OMOP exports; bounded read-only extraction from the OMOP database | `exports`, `pilot` |
| `gnn` | Inherited GNN baseline, asset validation, identity mapping, preprocessing parity, offline outputs, provenance, split membership, run binding, Phase 3 closure, driver-to-event linkage | `baseline`, `assets`, `mapping`, `parity`, `outputs`, `provenance`, `split`, `binding`, `admission`, `linkage` |
| `evidence` | Request and response contracts, evidence records, snapshot and case readers, readable names, scoped retrieval, claim policy, QA and explanation bundles | `vocabulary`, `contracts`, `schema`, `readers`, `concepts`, `retrieval`, `policy`, `bundles` |
| `casesets` | Case-set registry, seeded case-set selection, case-set preparation | `registry`, `selection`, `prepare` |
| `qa` | Condition vocabulary, question interpretation, concept resolution and record matching, scope binding, scoped answers, development suite, held-out and written wording tests | `vocabulary`, `resolve`, `interpret`, `scope`, `answer`, `suite`, `heldout`, `written` |
| `tracking` | Tracker policy, ClearML connection checks, controlled metadata tracking, input registration, lineage records | `policy`, `connection`, `controlled`, `registration`, `lineage` |
| `environment` | Verification of the application environment; capture of the GNN environment | `app`, `gnn` |
| `quality` | Publication audit of repository content and Git history; release metadata checks | `publication`, `release` |

Tests mirror the package layout under `tests/`; see
[development.md](development.md) for conventions.

## Configuration

Configuration files in `config/` may narrow a policy but never widen it;
fixed roots and allowlists live in code.

| File | Read by | Contents |
| --- | --- | --- |
| `paths.json` | `storage.*`, `environment.app`, `scripts/setup-destinations.ps1` | Destinations and process environment variables |
| `publishable_policy.json` | `quality.publication` | Publication audit policy |
| `source_exports.json` | `sources.exports` | Legacy export selection and profiling rules |
| `source_pilot.json` | `sources.pilot` | Pilot selection, tables, connection policy and timeouts |
| `inherited_baseline.json` | `gnn.baseline`, `gnn.assets`, `gnn.split` | Pinned inherited assets and expected reported metrics |
| `checkpoint_compatibility_probe.json` | `gnn.assets` | Recorded checkpoint head widths |
| `model_provenance.json` | `gnn.provenance` | Provenance sidecar schema and fixed flags |
| `upstream_split_membership.json` | `gnn.split` | Split sidecar schema, argument allowlist and flags |
| `phase3_closure.json` | `gnn.admission` | Closure policy: admission scope, label layers, driver tags, claim policy, superseded flags |
| `evidence_policy.json` | `evidence.policy`, `evidence.bundles` | Evidence claim policy extending the closure policy for linked events |
| `execution_limits.json` | `evidence.contracts`, `evidence.bundles` | Request and bundle execution limits, within fixed ceilings |
| `case_sets.json` | `casesets.registry`, `casesets.selection`, `sources.pilot` | Case-set purposes, partitions, sizes, seeds, eligibility and draw rule |
| `clearml_policy.json` | `tracking.policy` | Tracker payload and storage policy |
| `clearml_connection.json` | `tracking.connection` | Hosted ClearML declaration |
| `lineage_schema.json` | `tracking.lineage` | Lineage chain schema |
| `upstream_references.json` | `environment.app` | Logical upstream roles and paths |

## Locations

| Location | Contents | In Git |
| --- | --- | --- |
| `G:\Industry Dataset\ehr-evidence-app` | This repository: code, configuration, documentation, tests | yes |
| `G:\Industry Dataset\ehr-evidence-workspace` | Private workspace: protected data, outputs, verification records, archive | no |
| `G:\Industry Dataset\ehr-evidence-runtime` | Disposable environments, caches and temporary files | no |
| `G:\Industry Dataset\standardized-omop-gnn-7B82` | Inherited upstream GNN tree, read-only | no |
| Local PostgreSQL, database `mimiciv_omop` | OMOP CDM source database, read-only | no |
| `C:\omop_export` | Legacy OMOP CSV exports, read-only | no |
| External archive disk | Raw MIMIC-IV 3.1 files and Athena vocabularies (cold archive; the Athena directory is a read-only vocabulary source) | no |

The storage decision is recorded in
[decisions/0001-single-volume-storage.md](decisions/0001-single-volume-storage.md);
the rules are in [data-handling.md](data-handling.md).

## Design rules

- **Fail closed.** Every precondition is checked before anything is written.
  A missing, changed, linked or ambiguous input stops the run with a finding
  code; nothing is guessed or silently repaired.
- **Verify before use.** Inputs are pinned by SHA-256 (and size where
  stable), walked for links and reparse points, read once as bounded bytes
  and parsed only from the verified bytes.
- **Never overwrite.** Outputs go to a new versioned directory through a
  `.partial` sibling and an atomic rename. An existing output is refused.
  A correction is a new version; earlier versions stay byte-identical.
- **Inherited values are immutable.** Upstream predictions, attributions and
  metrics are never edited. Derived facts live in separate, labelled records.
- **Explicit real-data modes.** A module's default mode runs invented
  fixtures. Reading protected data requires an explicit flag.
- **Aggregate-only output.** Console output, logs and tracker payloads carry
  statuses, counts, hashes and finding codes only; unexpected exceptions
  print their type name only.

## Work-item identifiers

Numbers in module docstrings, record schemas and artefact names refer to
work items in the [roadmap](roadmap.md). For example, `step-17-pilot10-v1`
is the identity mapping produced by work item 17 for the 10-person pilot.

| Work item | Module | Protected artefact (workspace-relative) |
| --- | --- | --- |
| 0.3.1 | `sources.exports` | `protected-data\source-exports\legacy-export-20260901-v1` |
| 0.3.2 | `sources.pilot` | `protected-data\source-pilot\pilot-10-v1` |
| 1 | `gnn.baseline` | `outputs\baselines\inherited-baseline-v1.json` |
| 6 | `environment.gnn` | `outputs\environments\gnn-environment.json` |
| 13 | `tracking.registration` | `outputs\lineage\inherited-input-registration-v1.json` |
| 15 | `gnn.assets` | `outputs\asset-validation\source-assets-v1.json` |
| 17 | `gnn.mapping` | `protected-data\identity-mappings\step-17-pilot10-v1` |
| 19 | `gnn.parity` | `protected-data\verification\step-19-preprocessing-parity-final` |
| 20 | `gnn.outputs` | `protected-data\model-outputs\step-20-pilot10-v1` |
| 21 | `gnn.provenance` | `protected-data\model-provenance\step-21-pilot10-v1` |
| 22 | `gnn.split` | `protected-data\model-provenance\step-22-pilot10-v2` |
| Phase 3 closure | `gnn.binding` | `protected-data\verification\inherited-run-binding-v1` |
| Phase 3 closure | `gnn.admission` | `protected-data\model-provenance\phase3-closure-v1` |
| 28 | `gnn.linkage` | `protected-data\evidence\event-linkage-pilot10-v1` |
| 29, 30 | `evidence.bundles` | `protected-data\evidence-bundles\pilot10-v1` |
| 30.1 | `casesets.selection` | `protected-data\case-sets\<case set>` |
| 30.1 | `sources.pilot --case-set` | `protected-data\source-casesets\<case set>` |
| 30.1 | `casesets.prepare` | `protected-data\case-preparation\<case set>` |
| 30.1 | `gnn.linkage --case-set` | `protected-data\evidence\event-linkage-<case set>` |
| 30.1 | `evidence.bundles --case-set` | `protected-data\evidence-bundles\<case set>` |
| 32.1 | `qa.vocabulary` | `protected-data\vocabularies\condition-vocabulary-athena-20260831-v1` |
| 34.2 | copied unchanged | `protected-data\question-sets\mimic-iv-ext-instr` |
| 34.2 | `qa.heldout` | `protected-data\question-sets\mimic-iv-ext-instr-partitions-v1` |
| 34.3 | `qa.written` | `protected-data\question-sets\written-v1` |

Work items 16 (bridge output contract) and 18 (target and history policy)
are specifications; they are described in
[inherited-gnn.md](inherited-gnn.md). Work items 23 to 27 are code and
contracts without a protected artefact of their own; they are described in
[evidence.md](evidence.md). The other Phase 5 work items (31 to 34.1) have
no protected artefact; they are described in [qa.md](qa.md).
