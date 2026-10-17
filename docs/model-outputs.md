# Model Outputs and Provenance

The inherited model's predictions and attributions for the 10-person
development pilot are regenerated offline, bound to verified identities and
stored as protected, versioned records. This document describes those
records, what later evidence has changed about them, and which claims they
support. Model behaviour is described in [inherited-gnn.md](inherited-gnn.md).
Command examples assume the session set-up in
[development.md](development.md#session-set-up).

## Records

All paths are under `G:\Industry Dataset\ehr-evidence-workspace\protected-data`.

| Record | Directory | Module | State |
| :--- | :---: | :---: | :--- |
| Offline Outputs | `model-outputs\step-20-pilot10-v1` | `gnn.outputs` | Published, Immutable |
| Executed Wrapper Recovery | `model-provenance\step-20-executed-wrapper-recovery-v1` | Recovery Record | Published, Immutable |
| Provenance Sidecar | `model-provenance\step-21-pilot10-v1` | `gnn.provenance` | Published, Immutable; Some Flags Superseded |
| Split Membership, First Attempt | `model-provenance\step-22-pilot10-v1` | `gnn.split` | Superseded (Run Without PyTorch) |
| Split Membership | `model-provenance\step-22-pilot10-v2` | `gnn.split` | Published, Immutable; Result Superseded |
| Run Binding | `verification\inherited-run-binding-v1` | `gnn.binding` | Published, Immutable |
| Phase 3 Closure | `model-provenance\phase3-closure-v1` | `gnn.admission` | Published, Immutable; Current Authority for Phase 4 |
| Driver-to-Event Linkage | `evidence\event-linkage-pilot10-v1` | `gnn.linkage` | Published, Immutable ([evidence.md](evidence.md#event-linkage)) |
| Evidence Bundles, Version 1 | `evidence-bundles\pilot10-v1` | `evidence.bundles` | Published, Immutable ([evidence.md](evidence.md#published-records)) |
| Evidence Bundles, Version 2 | `evidence-bundles\pilot10-v2` | `evidence.bundles` | Published, Immutable; Schema 1.1, the One the Application Reads ([evidence.md](evidence.md#published-records)) |

Published records are never edited. A correction is a new version or a new
record that references the old one.

The real-data modes of `gnn.outputs`, `gnn.provenance` and `gnn.split` are
disabled in committed code by module constants (`BRIDGE_INTERFACE_CONFIRMED`,
`REAL_MODE_ENABLED`). They produced the records above and are kept for
reproducibility and review. `gnn.binding` and `gnn.admission` take
`--operator-real`; their outputs exist, so a re-run refuses before loading
anything. Every default mode runs invented fixtures:

```powershell
& $AppPython -m ehr_evidence_app.gnn.outputs
& $AppPython -m ehr_evidence_app.gnn.provenance
& $AppPython -m ehr_evidence_app.gnn.split
& $AppPython -m ehr_evidence_app.gnn.binding
& $AppPython -m ehr_evidence_app.gnn.admission
```

## Offline Outputs

`ehr_evidence_app.gnn.outputs` wraps the pinned bridge for exactly the ten
verified records of the identity mapping.

- **Selection.** The mapping must have the pinned schema, pilot label, graph
  and bridge hashes and exactly ten `verified` records without findings or
  repeated visits. Each selection is checked against the raw graph list:
  the graph at `graph_position` must pass the bridge filter, sit at
  `patient_index` and carry the mapped `subject_id`.
- **Inputs.** Bridge and model sources, checkpoint, graphs, vocabularies,
  documentation, the canonical explanation sample, the identity mapping, the
  concept names and the parity summary, each pinned by SHA-256. Sources and
  small files are executed or parsed only from their verified bytes; the
  graph and checkpoint are deserialised through the verified open handles,
  which are re-hashed around each load.
- **Generation.** CUDA is required (no CPU fallback). The full graph list is
  loaded and preprocessed once, checked against the parity summary, and the
  model is loaded once and put in evaluation mode. Records are produced with
  `top_k = 10` for the pilot indices and the canonical prefix (indices 0 to 2).
- **Canonical agreement.** The regenerated prefix must match the immutable
  upstream `explanations.json` sample: identical structure, types and
  non-float values; floats within `atol = rtol = 1e-6`. Agreement passed with
  zero mismatches before anything was published.
- **Isolation.** Upstream code runs with the tracker offline, network access
  refused, training, ETL and tracker imports refused, data loaders,
  training mode and optimisers disabled, and logging, standard streams and
  file descriptors 1 and 2 discarded.

Files: `outputs.json` (the ten bridge records, patient-bearing),
`canonical-agreement.json` (aggregate) and `manifest.json` (settings,
runtime versions, asset inventory, file hashes, flags; no patient content).

## Executed Wrapper Identity

The published outputs were produced by wrapper bytes with SHA-256
`fe37ac0a…44323c9ab`. The last revision of that wrapper before 0.3.0
(`d0a3218c…caae1f077`) differs only in three synthetic-fixture constants,
edited after the run. Reversing that edit reproduces the executed bytes
exactly; they are stored with the diff and a recovery manifest in the
wrapper recovery record. Real-mode logic is identical, so no re-run is
needed. The packaged module `ehr_evidence_app.gnn.outputs` carries the same
logic adapted to the package layout and storage anchor; the pre-0.3.0 bytes
remain in the private workspace archive, where historical records pin them.

## Provenance Sidecar

`ehr_evidence_app.gnn.provenance` links each output to its identity-mapping
record, the target and history rule, the preprocessing identity and the
exact assets recorded by the outputs manifest. It copies no inherited value.

Before publishing it verifies the outputs manifest and agreement, the
parity summary, the mapping (one person per identity, visit nodes exactly
0 to *N*-1, no repeated source visit, target not repeated in history,
consistent `graph_position` ordering) and a one-to-one match between the ten
outputs and the ten mapping records.

Each record holds `patient_index`, `graph_position`, the person identity, the
visit-node count *N*, the target at *N*-1 with its source visit, the
pre-target history 0 to *N*-2 (explicitly empty when *N* = 1), the mapping
digest and the SHA-256 of the corresponding output record. The sidecar
fingerprint (`preparation.fingerprint_sha256`) is the SHA-256 of its
canonical JSON without the `preparation` block; equal inputs give an equal
fingerprint.

## Split Membership

`ehr_evidence_app.gnn.split` replays the pinned `split_graphs` function over
filtered positions (`numpy.random.default_rng(seed).permutation(n)`; test,
then validation, then training) and records, per case, `verified`, `unknown`
(conflicting evidence) or `unavailable` (missing evidence). Saved arguments
come from the checkpoint (`weights_only=True`) and the metrics file; parser
defaults are never used. A verified result additionally required two run
artefacts that do not exist: an RNG provenance record and a graph-binding
record. Every pilot case in `step-22-pilot10-v2` is therefore `unavailable`,
while the expected configuration counts (15,024 test, 10,016 validation,
75,123 training) are computed. The v1 attempt ran in an interpreter without
PyTorch and recorded the saved arguments as unavailable.

The run-binding evidence in [inherited-gnn.md](inherited-gnn.md#run-binding-and-split-membership)
supersedes this result: membership is 8 training, 0 validation, 2 test.

## Flags in Published Records

| Flag in Published Records | Recorded Value | Current Evidence |
| :--- | :---: | :--- |
| `label_index_correspondence` | `not_established` | Established by Label Layers A to C |
| Executed Wrapper `bytes_available`, `retrievable` | `false` | Recovered Byte-Exact |
| `upstream_split_membership` | `unknown` / `unavailable` | 8 Training, 0 Validation, 2 Test |
| `phase4_eligible` | `false` for Every Record | Replaced by Typed Per-Case Admission in the Closure Record |
| `source_event_linkage` | `absent` | Linked by `event-linkage-pilot10-v1`: Every Graph Edge and Driver Verified |
| `pre_target_only_input` | `false` | Unchanged: Target-Visit Inputs Remain |
| `history_only_claim_allowed` | `false` | Unchanged |
| `unseen_patient_status` | `not_inferred` | Partition Known; "held out" Must Be Defined per Claim |

## Phase 4 Admission

All ten pilot cases may enter evidence-object construction, subject to the
claim policy below. Per case, the admissible inputs are:

- the selected label with its label-decoding evidence;
- the raw logit and the uncalibrated sigmoid score;
- drivers with a score above zero, each with its visit tag (target only,
  target and earlier, earlier only);
- the verified identity mapping: person, target visit and pre-target
  history visits;
- the upstream partition, with the definition that "held out" means outside
  the training partition only (label vocabulary and feature normalisation
  were computed over all patients).

Excluded: the zero-score condition entries attached only to the target
visit (5 across the pilot). They never reach a language model.

### Closure Record

`ehr_evidence_app.gnn.admission` writes this admission as a record, so Phase 4
consumes a typed, versioned decision rather than documentation. It verifies
the whole published chain (identity mapping, outputs with their manifest and
agreement, provenance fingerprint and bindings, both split sidecars, the
wrapper recovery record, the run-binding record and its cases file) and reads
the raw graphs through their verified handle only to classify driver
attachments. Per case it records typed admission with case-level
restrictions (`batch_sensitive_prediction`, `target_attached_drivers_present`,
`target_outcome_entries_excluded`), the three label-evidence layers, the
prediction definition, the upstream partition and one tag per driver (score
class, attachment, target-visit diagnosis, narration eligibility) with no
driver value copied. The claim policy and the map of superseded flags are
fixed in code and mirrored in `config/phase3_closure.json`; the module
refuses to run if they differ.

Published record: `protected-data\model-provenance\phase3-closure-v1`, with
`closure.json` (patient-bearing) and `summary.json` (aggregate). Its counts:

| Measure | Result |
| :--- | :--- |
| Cases Admitted | 10 of 10 |
| Label Layer B / Correspondence Established | 10 / 10 |
| Explanation-Eligible Cases | 10 |
| Batch-Sensitive Predictions | 2 |
| Upstream Partition (Train / Validation / Test) | 8 / 0 / 2 |
| Excluded Target-Visit Diagnoses | 5 |

```powershell
& $GnnPython -m ehr_evidence_app.gnn.admission --operator-real
```

## Claim Policy

| Allowed | Prohibited |
| :--- | :--- |
| The Selected Label, Described as One Selected Output of a Multi-Label Prediction | Calling the Selected Label the Only Diagnosis, or the Score a Clinical Risk |
| The Raw Logit and an Uncalibrated Score | Citing the Reported AUROC for Single-Graph Bridge Predictions |
| Drivers with a Score Above Zero, Described as Unsigned Local Sensitivity | Causal, Directional or Percentage Attribution |
| A Driver's Visit Tag, Used Only to Restrict or Disclose | Presenting Target-Attached Drivers as History |
| The Upstream Partition, with the Explicit Definition Above | Prospective or Pre-Target-Only Claims |
| Recorded History, Stated Within Its Verified Scope | Tying an Attribution to a Date, a Visit or a Recurrence |
| | Any Use of the Zero-Score Target Diagnoses |
| | Dated, Per-Visit, Frequency or Progression Claims About Events (No Event Linkage) |

This is the policy as published in the closure record. Phase 4 extends it,
as a separate versioned policy, for events the linkage record ties to a
driver: see [evidence.md](evidence.md#claim-policy).
