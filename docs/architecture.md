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

## Data Flow

```text
MIMIC-IV 3.1 --(OHDSI ETL, Upstream)--> OMOP CDM in PostgreSQL (Read-Only)
     |
     +--(Raw Hospital and ICU Files, Read-Only)--> Record Extracts (sources.mimic)
                                                |
                  +-----------------------------+------------------------+
                  |                                                      |
        Source Extracts (sources)                    Inherited E-TRANS GNN (gnn)
        Staged Exports, Bounded Pilot                Graphs, Checkpoint, Bridge
                  |                                                      |
                  |                    Offline Predictions + Attributions,
                  |                    Identity Mapping, Provenance, Closure
                  |                                                      |
                  +------------------> Evidence Objects <----------------+
                                              |
                              Question Interpretation and Scope
                                              |
                              LLM Generation (QA / Explanation)
                                              |
                              Validation and Release Gate
                                              |
                              Grounded Answer (API / Interface)
```

The GNN is used exactly as inherited. Its predictions and attributions are
regenerated offline through its own bridge code, bound to verified patient
and visit identities and stored as protected, versioned records. Later layers
consume those records; they never recompute or alter a prediction or an
attribution.

## Implementation Status

| Layer | Status |
| :--- | :--- |
| Storage, Data Handling and Repository Quality Gates | Implemented |
| Source Preparation: Staged Exports, Bounded Read-Only Pilot | Implemented |
| Experiment Tracking and Lineage Foundations | Implemented |
| Inherited GNN Integration | Implemented and Closed for the 10-Person Development Pilot |
| Evidence Contracts, Records, Event Linkage and Bundles | Implemented for the Development Pilot and the Development and Evaluation Case Sets |
| Question Interpretation, Concept and Scope Resolution, Scoped Answers | Implemented (Rule-Based); Held-Out and Written Wording Tests Done |
| Validation and the Release Gate | Implemented; Mutation Suite Version 4 Covers Model-Written Candidates, Driver Selection and Scores as Shown |
| LLM Generation | Implemented (Release 0.9.0): The Compact Output Shape With Syntax-Only Decoding, the Model's Structured View of the Evidence, the Prompt, Adapter, Limits and Fallback, the Pinned Model's Worker in Its Own Environment, Protected Runtime Records, the Log Audit and Tracker Aggregates; Measured on the Development Case Sets; Not Fine-Tuned ([ADR 0024](decisions/0024-no-fine-tuning.md)) |
| API and Interface | Implemented (Release 0.10.0): The Local API, Its Access Boundary, the Interface's Page and Its Operations, the Launcher and Sharing Through the Tunnel; Closed by an Operator Run on `dev-30-v1` With the Model |
| The Whole Record | Implemented: Raw Record Extracts, Record Bundles, the Medication and Procedure Vocabulary, Reading of Questions About the Record, Template Answers and Their Support Checks ([ADR 0021](decisions/0021-the-whole-record.md)), Tested at Their Edges ([ADR 0022](decisions/0022-record-edge-cases.md)) and Against Each Other, With Compound Mistakes and Junk Input ([ADR 0023](decisions/0023-consistency-and-junk-input.md)); What a Medication Was Given for Is Never Stated ([ADR 0026](decisions/0026-what-a-medication-was-given-for.md)) |
| Evaluation | Built and Frozen (Release 0.11.2; [ADR 0028](decisions/0028-final-evaluation-design.md), [ADR 0029](decisions/0029-the-freeze.md)): The Partition of the Case Sets, the Paired Statistics, the Metrics, the References, the Baseline, the Paired Runner, the Scoring of a Run, the End-to-End Assessment, the Prose Rubric, the Held-Out Read of MIMIC-IV-Ext-Instr and the Freeze; the Metrics Fixed ([ADR 0016](decisions/0016-evaluation-metrics.md)), the Prose Rubric Rated by Code and an Automatic Rater ([ADR 0027](decisions/0027-automatic-prose-rating.md)); the Final Run Done, Its Figures in [final-figures.md](final-figures.md) |
| Acceptance and Handover | Done (Release 1.0.0): The [Acceptance Matrix](acceptance.md), the Reconstruction and Restart Check ([development.md](development.md#reconstruction-and-restart)) and the Release of the Evaluated Version |

See [roadmap.md](roadmap.md) for the planned work.

## Package Layout

All code is in `src/ehr_evidence_app`. Every module with a command-line
interface runs as `python -m ehr_evidence_app.<package>.<module>`.

| Package | Responsibility | Modules |
| :--- | :---: | :--- |
| `storage` | Fixed Storage Roots, Destination and Path Checks, Process-Local Destination Setup, Runtime-Volume Preflight | `policy`, `destinations`, `runtime` |
| `sources` | Staging of the Legacy OMOP Exports; Bounded Read-Only Extraction From the OMOP Database; the Case Sets' Raw MIMIC-IV Records | `exports`, `pilot`, `mimic` |
| `gnn` | Inherited GNN Baseline, Asset Validation, Identity Mapping, Preprocessing Parity, Offline Outputs, Provenance, Split Membership, Run Binding, Phase 3 Closure, Driver-to-Event Linkage | `baseline`, `assets`, `mapping`, `parity`, `outputs`, `provenance`, `split`, `binding`, `admission`, `linkage` |
| `evidence` | Request and Response Contracts, Evidence Records, Snapshot and Case Readers, Readable Names, Scoped Retrieval, Claim Policy, QA and Explanation Bundles, Record Bundles | `vocabulary`, `contracts`, `schema`, `readers`, `concepts`, `retrieval`, `policy`, `bundles`, `record`, `record_bundles` |
| `casesets` | Case-Set Registry, Seeded Case-Set Selection, Case-Set Preparation | `registry`, `selection`, `prepare` |
| `qa` | Condition Vocabulary, Question Interpretation, Concept Resolution and Record Matching, Scope Binding, Template Candidates, Scoped Answers, Development Suite, Held-Out and Written Wording Tests; Questions About the Whole Record, Their Terms, Vocabulary and Answers | `vocabulary`, `resolve`, `interpret`, `scope`, `templates`, `answer`, `suite`, `heldout`, `written`, `record_vocabulary`, `record_interpret`, `record_resolve`, `record_answer` |
| `validation` | Candidate Contract, Checks Before Generation, Structure and Reference Checks, Support of QA Statements and of Answers About the Whole Record, How Record Facts Are Shown, Wording Rules, Template Explanation and Its Preservation Checks, Release Gate, Mutation Suite | `candidate`, `evidence`, `structure`, `support`, `record_support`, `record_text`, `language`, `explanation`, `gate`, `mutations` |
| `generation` | Candidates Written by the Language Model: The Document Shape and Its Schema, the Model's Compact View of the Evidence, the Prompt, the Adapter With Its Limits and the Template Fallback, the Pinned Model's Runtime and Its Worker (Which Runs in the Model Environment), Development Measurement, Protected Runtime Records | `shape`, `projection`, `prompt`, `adapter`, `runtime`, `worker`, `measure`, `records` |
| `api` | The Application's Operations for the Interface, Served as a Local HTTP API Behind Its Access Boundary; the Launcher | `service`, `app`, `boundary`, `launch` |
| `interface` | The Page Over the API, a Conversation About a Selected Patient With the Evidence Beside It; Its Operations, What It Shows, Its Jobs, Names, Client, Access Checks and Server; the Secret Link and the Tunnel's Parts | `app`, `views`, `jobs`, `names`, `labels`, `client`, `gate`, `serve`, `share`, `static` |
| `tracking` | Tracker Policy, ClearML Connection Checks, Controlled Tracking, Input Registration, Runtime Aggregates, Measurements, the Final Evaluation's Figures, the Pinned Model in the Tracker, Lineage Records | `policy`, `connection`, `controlled`, `registration`, `aggregates`, `measurements`, `final_figures`, `models`, `lineage` |
| `assessment` | The Final Evaluation (Phase 9): The Downstream Partition of the Case Sets, the Independent References (Conditions, Explanations and the Whole Record), the Baseline Condition, the Paired Runner, the Metrics, What Each Condition Wrote Read for Them, the Paired Comparison's Statistics, the Scoring of a Run, the End-to-End Assessment of Questions and a Generated Suite of Questions About the Record, the Prose Rubric, the Held-Out Read of MIMIC-IV-Ext-Instr, the Freeze | `partition`, `references`, `record_references`, `baseline`, `runner`, `metrics`, `stated`, `statistics`, `report`, `end_to_end`, `record_suite`, `rubric`, `instr`, `freeze` |
| `environment` | Verification of the Application Environment; Capture of the GNN Environment | `app`, `gnn` |
| `quality` | Publication Audit of Repository Content and Git History; Release Metadata Checks | `publication`, `release` |

Tests mirror the package layout under `tests/`; see
[development.md](development.md) for conventions.

## Configuration

Configuration files in `config/` may narrow a policy but never widen it;
fixed roots and allowlists live in code.

| File | Read By | Contents |
| :--- | :---: | :--- |
| `paths.json` | `storage.*`, `environment.app`, `scripts/setup-destinations.ps1` | Destinations and Process Environment Variables |
| `publishable_policy.json` | `quality.publication` | Publication Audit Policy |
| `source_exports.json` | `sources.exports` | Legacy Export Selection and Profiling Rules |
| `source_pilot.json` | `sources.pilot` | Pilot Selection, Tables, Connection Policy and Timeouts |
| `inherited_baseline.json` | `gnn.baseline`, `gnn.assets`, `gnn.split` | Pinned Inherited Assets and Expected Reported Metrics |
| `checkpoint_compatibility_probe.json` | `gnn.assets` | Recorded Checkpoint Head Widths |
| `model_provenance.json` | `gnn.provenance` | Provenance Sidecar Schema and Fixed Flags |
| `upstream_split_membership.json` | `gnn.split` | Split Sidecar Schema, Argument Allowlist and Flags |
| `phase3_closure.json` | `gnn.admission` | Closure Policy: Admission Scope, Label Layers, Driver Tags, Claim Policy, Superseded Flags |
| `evidence_policy.json` | `evidence.policy`, `evidence.bundles` | Evidence Claim Policy Extending the Closure Policy for Linked Events |
| `execution_limits.json` | `evidence.contracts`, `evidence.bundles` | Request, Bundle and Generation Execution Limits, Within Fixed Ceilings |
| `generation.json` | `generation.runtime` | The Pinned Language Model (Repository, Revision, Every File's Size and SHA-256) and Its Worker's Settings, Precision Included |
| `explanation.json` | `validation.explanation` | How an Explanation Selects the Drivers It Describes, and How Many |
| `case_sets.json` | `casesets.registry`, `casesets.selection`, `sources.pilot` | Case-Set Purposes, Partitions, Sizes, Seeds, Eligibility and Draw Rule |
| `clearml_policy.json` | `tracking.policy` | Tracker Payload and Storage Policy |
| `clearml_connection.json` | `tracking.connection` | Hosted ClearML Declaration |
| `measurement_history.json` | `tracking.measurements` | The Phase 7 Generation Measurements, as the Aggregates the Execution Log Records |
| `lineage_schema.json` | `tracking.lineage` | Lineage Chain Schema |
| `upstream_references.json` | `environment.app` | Logical Upstream Roles and Paths |

## Locations

| Location | Contents | In Git |
| :--- | :---: | :--- |
| `G:\Industry Dataset\ehr-evidence-app` | This Repository: Code, Configuration, Documentation, Tests | Yes |
| `G:\Industry Dataset\ehr-evidence-workspace` | Private Workspace: Protected Data, Outputs, Verification Records, Archive | No |
| `G:\Industry Dataset\ehr-evidence-runtime` | Disposable Environments, Caches and Temporary Files | No |
| `G:\Industry Dataset\standardized-omop-gnn-7B82` | Inherited Upstream GNN Tree, Read-Only | No |
| Local PostgreSQL, Database `mimiciv_omop` | OMOP CDM Source Database, Read-Only | No |
| `C:\omop_export` | Legacy OMOP CSV Exports, Read-Only | No |
| External Archive Disk | Raw MIMIC-IV 3.1 Files and Athena Vocabularies (Cold Archive; the Athena Directory Is a Read-Only Vocabulary Source) | No |

The storage decision is recorded in
[decisions/0001-single-volume-storage.md](decisions/0001-single-volume-storage.md);
the rules are in [data-handling.md](data-handling.md).

## Design Rules

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
  print their type name only. The one other payload is the listed
  pretrained model's files, with its licence
  ([ADR 0018](decisions/0018-tracker-as-experiment-record.md)).

## Work-Item Identifiers

Numbers in module docstrings, record schemas and artefact names refer to
work items in the [roadmap](roadmap.md). For example, `step-17-pilot10-v1`
is the identity mapping produced by work item 17 for the 10-person pilot.

| Work Item | Module | Protected Artefact (Workspace-Relative) |
| :--- | :---: | :--- |
| 0.3.1 | `sources.exports` | `protected-data\source-exports\legacy-export-20260901-v1` |
| 0.3.2 | `sources.pilot` | `protected-data\source-pilot\pilot-10-v1` |
| 1 | `gnn.baseline` | `outputs\baselines\inherited-baseline-v1.json` |
| 6 | `environment.gnn` | `outputs\environments\gnn-environment.json` |
| 13 | `tracking.registration` | `outputs\lineage\inherited-input-registration-v2.json` (Version 1, Pinned by Work Item 21, Names the Baseline at Its Path Before the Move to G:) |
| 15 | `gnn.assets` | `outputs\asset-validation\source-assets-v1.json` |
| 17 | `gnn.mapping` | `protected-data\identity-mappings\step-17-pilot10-v1` |
| 19 | `gnn.parity` | `protected-data\verification\step-19-preprocessing-parity-final` |
| 20 | `gnn.outputs` | `protected-data\model-outputs\step-20-pilot10-v1` |
| 21 | `gnn.provenance` | `protected-data\model-provenance\step-21-pilot10-v1` |
| 22 | `gnn.split` | `protected-data\model-provenance\step-22-pilot10-v2` |
| Phase 3 Closure | `gnn.binding` | `protected-data\verification\inherited-run-binding-v1` |
| Phase 3 Closure | `gnn.admission` | `protected-data\model-provenance\phase3-closure-v1` |
| 28 | `gnn.linkage` | `protected-data\evidence\event-linkage-pilot10-v1` |
| 29, 30 | `evidence.bundles` | `protected-data\evidence-bundles\pilot10-v1` |
| 30.1 | `casesets.selection` | `protected-data\case-sets\<case set>` |
| 30.1 | `sources.pilot --case-set` | `protected-data\source-casesets\<case set>` |
| 30.1 | `casesets.prepare` | `protected-data\case-preparation\<case set>` |
| 30.1 | `gnn.linkage --case-set` | `protected-data\evidence\event-linkage-<case set>` |
| 30.1 | `evidence.bundles --case-set` | `protected-data\evidence-bundles\<case set>` |
| 32.1 | `qa.vocabulary` | `protected-data\vocabularies\condition-vocabulary-athena-20260831-v1` |
| 34.2 | Copied Unchanged | `protected-data\question-sets\mimic-iv-ext-instr` |
| 34.2 | `qa.heldout` | `protected-data\question-sets\mimic-iv-ext-instr-partitions-v1` |
| 34.3 | `qa.written` | `protected-data\question-sets\written-v1` to `written-v3`, One Set per Rule Version |
| 52.1 | `sources.mimic` | `protected-data\source-records\<case set>` |
| 52.2 | `qa.record_vocabulary` | `protected-data\vocabularies\record-vocabulary-athena-20260831-v1` |
| 52.8 | `qa.record_vocabulary` (Version 2: Each Drug Class's Ingredients) | `protected-data\vocabularies\record-vocabulary-athena-20260831-v2` |
| 52.3 | `evidence.record_bundles` | `protected-data\evidence-bundles\<case set>-records-v1` |

Work items 16 (bridge output contract) and 18 (target and history policy)
are specifications; they are described in
[inherited-gnn.md](inherited-gnn.md). Work items 23 to 27 are code and
contracts without a protected artefact of their own; they are described in
[evidence.md](evidence.md). The other Phase 5 work items (31 to 34.1) have
no protected artefact; they are described in [qa.md](qa.md). Phase 6 work
items have none either; they are described in
[validation.md](validation.md). Phase 7 work items are described in
[generation.md](generation.md); their runtime records come with work
item 46.
