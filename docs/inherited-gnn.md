# Inherited GNN

The prediction model is inherited from an earlier project and used as-is:
the E-TRANS patient-journey GNN, replicated on MIMIC-IV 3.1 in the OMOP CDM.
This project never retrains, fine-tunes or edits it. This document records
what the model is, how this project binds to it, and what its outputs can and
cannot support. Command examples assume the session set-up in
[development.md](development.md#session-set-up).

## Model

- **Graph.** One heterogeneous graph per patient: one node per visit and one
  node per distinct patient concept in each of the condition, procedure and
  drug domains, with visit-to-concept edges and temporal visit edges. Visits
  are ordered by `visit_index`.
- **Network.** `PatientGNN`: concept embeddings initialised from frozen
  knowledge-graph embeddings, heterogeneous graph transformer (`HGTConv`)
  layers, then a graph transformer and a visit-sequence transformer whose
  logits are mixed as `0.8 * graph + 0.2 * sequence`.
- **Task.** Multi-label diagnosis over 275 condition labels, with a
  class-weighted loss (`pos_weight` computed from the training partition and
  capped at 50).
- **Canonical run.** `diag_kg` (OMOP data with knowledge-graph embeddings):
  best epoch 42 of 50. Reported test macro AUROC `0.9062469508269994` and
  macro AUPRC `0.31301594151588924`.
- **Data.** 100,163 patient graphs; split with seed 42 into 75,123 training,
  10,016 validation and 15,024 test graphs.

The upstream tree is `G:\Industry Dataset\standardized-omop-gnn-7B82`. It has
no Git metadata, so SHA-256 identities of the pinned files stand in for a
revision.

## Pinned Identity

`config/inherited_baseline.json` pins eleven upstream files by size and
SHA-256: the replication documentation, the four source files
(`explain_patient.py`, `train.py`, `model.py`, `build_patient_graphs.py`),
the canonical checkpoint, metrics and explanation sample, the patient graphs
and the concept and label vocabularies.

- `ehr_evidence_app.gnn.baseline` hashes those files without importing or
  running upstream code and writes a deterministic identity record
  (`outputs\baselines\inherited-baseline-v1.json`). Only whitelisted metric
  fields are read; the checkpoint, graphs and explanations are hashed, never
  deserialised. Reported metrics are labelled `inherited_reported`.
- `ehr_evidence_app.gnn.assets` re-validates the assets against the pinned
  configuration (its own bytes are pinned in code), parses the JSON roles
  from the verified bytes and checks their shape: 275 labels, three concept
  domains, the canonical metrics identity and a checkpoint compatibility
  probe with 275-way heads. `--write` stores the record
  (`outputs\asset-validation\source-assets-v1.json`).

```powershell
& $AppPython -m ehr_evidence_app.gnn.assets          # validate; prints the record, writes nothing
& $AppPython -m ehr_evidence_app.gnn.baseline --output "G:\Industry Dataset\ehr-evidence-workspace\outputs\baselines\inherited-baseline-<version>.json"
```

Both records are deterministic. Re-running against an existing output leaves
an identical record untouched and refuses a different one, so a changed
record always gets a new version name. The published records
(`inherited-baseline-v1.json`, `source-assets-v1.json`) predate the storage
move and record the previous absolute paths; SHA-256 identities are
unchanged.

## Bridge Output Contract

The upstream bridge (`src\gnn\explain_patient.py`) loads the checkpoint, runs
the model on one patient graph at a time and writes one record per selected
patient:

| Field | Meaning |
| :--- | ---: |
| `patient_index` | Position in the Bridge-Filtered Graph List; Not a Person or Visit Identifier |
| `predicted_diagnosis.label_index` | Argmax Over the 275 Diagnosis Logits |
| `predicted_diagnosis.concept_id` | `label_vocab[label_index]` |
| `predicted_diagnosis.name` | Readable Concept Name, or `""` |
| `predicted_diagnosis.logit` | Raw Logit of the Selected Label |
| `predicted_diagnosis.probability` | `sigmoid(logit)`; Not a Calibrated Risk |
| `drivers.condition` / `.procedure` / `.drug` | Up to `top_k` (Default 10) Entries per Domain |
| Driver `vocab_index`, `concept_id`, `name` | Concept Identity (`concept_id` May Be `null`, `name` May Be `""`) |
| Driver `score` | Sum of Absolute Embedding Gradients for the Selected Label |

Semantics:

- The selected label is one output of a multi-label prediction, not the only
  diagnosis the model predicts.
- Driver scores are unsigned local sensitivities. They carry no direction,
  cause, clinical reasoning or percentage contribution, and are not
  normalised across domains or records.
- A driver is a concept node the prediction is sensitive to, not a recorded
  event: it carries no visit, date or event identifier.
- `top_k` selects within each domain independently and is not recorded in
  the output.
- A record carries no person, visit, target, event, split, schema or
  provenance fields. Those are attached by the downstream records described
  below and in [model-outputs.md](model-outputs.md).

Synthetic example (all values invented):

```json
{
  "patient_index": 7,
  "predicted_diagnosis": {"label_index": 3, "concept_id": 990000003,
                          "name": "SYNTHETIC LABEL C", "logit": 1.25,
                          "probability": 0.7773},
  "drivers": {
    "condition": [{"vocab_index": 4, "concept_id": 990001004,
                   "name": "SYNTHETIC CONDITION D", "score": 0.0421}],
    "procedure": [],
    "drug": []
  }
}
```

The bridge also prints predictions and drivers to standard output; that
output is patient-bearing and is always discarded by the wrappers here.

## Label Decoding

Whether `concept_id` names the diagnosis the model actually scored rests on
three layers of evidence:

| Layer | Evidence | Result |
| :--- | :---: | ---: |
| A. Rule | Graph Builder, Trainer, Model and Bridge Share One Label Axis: Output Position *i*, Training Target *i* and `label_vocab[i]` | Established From the Pinned Source |
| B. Decode | `concept_id == label_vocab[label_index]` for Every Pilot Output | 10 of 10 |
| C. Run Binding | The Checkpoint's Saved `pos_weight` Reproduces Exactly From the Current Graph Labels in the Current Label Order Under the Replayed Split; a Reversed Order Does Not | 275 of 275 Values; Reversed Order 0 of 105 Exact-Count Values |

Together these establish the label correspondence for the canonical run.
Earlier records that say `label_index_correspondence: not_established`
predate layers B and C; see [model-outputs.md](model-outputs.md).

## Identity Mapping

`ehr_evidence_app.gnn.mapping` links each person of the source pilot
(`pilot-10-v1`) to a graph and each visit node to a source visit:

- `patient_index` is the position after the bridge filter
  (`hasattr(graph, "y_diagnosis")`), always stored with the unfiltered
  `graph_position` and the pinned graph and bridge hashes, never alone;
- person: graph `subject_id` equals the pilot `subject_id` by exact integer
  round trip, giving one `person_id`;
- visit: the graph `hadm_id` at each visit-node position equals a pilot
  `visit_occurrence_id` of the same person;
- never used: `visit_index`, dates, `hadm_source_id`, concept identifiers or
  positional guesses.

Statuses are `verified`, `verified_with_repeated_visit_nodes` or
`quarantined` with finding codes; a person without a unique match is still
recorded, quarantined. All 10 pilot persons are `verified`: 38 visit nodes,
10 targets and 28 history links, with no target repeated in its own history.
The mapping is `protected-data\identity-mappings\step-17-pilot10-v1\mapping.json`.

## Target and History

- **Target.** The final ordered visit of a graph, position *N*-1, resolved
  through its identity-mapping entry. It is the visit whose conditions form
  the diagnosis label `y_diagnosis`.
- **Pre-target history.** Positions 0 to *N*-2, each resolved through its own
  mapping entry, in graph order. With *N* = 1 the history is explicitly empty
  and is never filled from the target.
- **Separation.** The target's conditions, procedures, drugs, visit
  features, mortality and readmission values and label are never presented
  as history. If the target's source visit also appears at an earlier
  position, target and history cannot be separated and the case is blocked.
- **Attribution is concept-level.** A driver score is never split among, or
  assigned to, the visits in which its concept appears. A concept being
  present in history does not make its score a history score.
- **Event linkage.** The identity mapping links persons and visits, not
  events. Missing event linkage blocks the claims that need it (dated,
  per-visit, frequency and progression statements about events); it does not
  block a case. For the development pilot, the Phase 4 linkage record ties
  every driver's graph edges to recorded events, and such claims must cite
  those events (see [evidence.md](evidence.md#event-linkage)).

## Preprocessing Parity

`ehr_evidence_app.gnn.parity` confirms that the training loader and the
bridge produce identical model inputs, and that both match an independent
formula oracle over the full graph list:

1. keep graphs with `y_diagnosis`, in stored order;
2. apply `log1p` to visit feature column 3 and z-score columns 4 and 5 with
   the mean and unbiased standard deviation over every visit row;
3. remove only the `has_condition` edges whose source is the final visit.

Bridge against training loader must match exactly; each against the oracle
within `atol = rtol = 1e-6`. Graph edge attributes are compared as an
invariant; the model does not read them. The real run writes an aggregate
summary (status, hashes, counts, model-input digest, tolerances); the
approved summary is
`protected-data\verification\step-19-preprocessing-parity-final\summary.json`.
Parity covers the preprocessed graphs, not batch effects (see below).

```powershell
$GnnPython = 'C:\path\to\omopgnn\python.exe'   # existing omopgnn interpreter
& $AppPython -m ehr_evidence_app.gnn.parity                    # synthetic fixture
& $GnnPython -m ehr_evidence_app.gnn.parity --operator-real    # real data, aggregate output
```

## Run Binding and Split Membership

The checkpoint stores its training arguments, including the 275
`pos_weight` values computed from the training partition's label counts.
Replaying the pinned split (`numpy.random.default_rng(42).permutation`, test
first, then validation, then training; NumPy 1.26.4) over the current graphs
reproduces:

- `pos_weight`: 275 of 275 values bit-for-bit, including all 105 values below
  the cap, which encode exact label counts;
- stored validation and test metrics from a batched re-evaluation: AUROC
  within 1e-8, AUPRC within 2e-7, recall and precision exactly.

Controls fail as expected: seed 43 matches 0 of the 105 exact-count values
and misses test AUROC by about 3e-2; a reversed label order matches 0 of 105.
The checkpoint, graph labels, label order and split are therefore bound to
one another. Pilot membership: 8 training, 0 validation, 2 test.

`ehr_evidence_app.gnn.binding` records this verification. It runs the
pinned trainer's own `split_graphs` over the bridge-filtered positions (and
checks it against an independent NumPy replay), replays the trainer's
`pos_weight` formula, re-evaluates the checkpoint with the trainer's
`run_epoch` (batch size 128, no shuffle, CUDA) against the stored validation
and test metrics with a fixed tolerance of `1e-6`, runs both controls, and
measures the batch sensitivity described below. Upstream code runs from
verified bytes with training mode, optimisers, network access and tracker or
ETL imports refused. The published record is
`protected-data\verification\inherited-run-binding-v1`: `summary.json`
(aggregate only) and `cases.json` (per pilot case: upstream partition,
single-graph reproduction, batch stability).

```powershell
& $AppPython -m ehr_evidence_app.gnn.binding                   # invented fixtures
& $GnnPython -m ehr_evidence_app.gnn.binding --operator-real   # real data, CUDA
```

The earlier split sidecar (`step-22-pilot10-v2`) reports every pilot case as
`unavailable`, because its rules accepted only two run artefacts that do not
exist (an RNG provenance record and a graph-binding record). The run-binding
record supersedes that result.

## Model Limitations

These are properties of the inherited model. They restrict claims; they do
not stop cases from being used.

1. **Target-visit inputs, no causal mask.** Preprocessing removes only the
   final visit's condition edges. The final visit's features (length of stay,
   age, gender, laboratory summary), procedure edges and drug edges remain
   model inputs, and the sequence transformer runs without a causal mask. The
   output is a final-admission diagnosis prediction made with that
   admission's context; it is not prospective and not pre-target-only.
2. **Zero-score target diagnoses in driver lists.** A condition recorded only
   at the target visit loses its only edge in preprocessing, so its gradient
   is exactly zero, yet per-domain top-*k* can still list it. The pilot
   outputs contain 5 such condition entries, all target-visit-only
   diagnoses. They reveal target labels and must be excluded from any
   evidence given to a language model.
3. **Target-attached drivers.** Drivers are tagged by the visits their concept
   attaches to:

   | Domain | Target only | Target and earlier | Earlier only | Zero score |
   | --- | --- | --- | --- | --- |
   | Condition | 0 | 31 | 46 | 5 |
   | Drug | 45 | 24 | 16 | 0 |
   | Procedure | 1 | 1 | 24 | 0 |

   Most drug drivers reflect treatment during the target admission. Without
   the tag, an explanation would present that treatment as history.
4. **Batch-dependent predictions.** The model's visit structural encodings
   (`_metapath_rw_se`) are normalised by maxima over the whole batch.
   Training used batches of 64 and evaluation batches of 128; the bridge
   runs one graph at a time. Placed in a batch of
   64 or 128, the top label changes for 2 of the 10 pilot patients (selected
   label logit shift: median about 0.3 to 0.4, maximum 2.65; the originally
   selected label stays within the top two). The reported AUROC describes
   batched evaluation, not single-graph bridge predictions.

**Terminology.** Describe the output as a *final-admission diagnosis
prediction that includes target-admission context*, not a "next-visit"
prediction.

## Environment

Real-data modes of `gnn.mapping`, `gnn.parity`, `gnn.outputs`,
`gnn.provenance` and `gnn.split` run in the existing GNN interpreter
(`omopgnn`: Python 3.12.8, PyTorch 2.11.0+cu128, PyG 2.8.0.post1, NumPy
1.26.4). Nothing is ever installed into it; see
[environments.md](environments.md).
