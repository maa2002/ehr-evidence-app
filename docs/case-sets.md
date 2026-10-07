# Case Sets

The development pilot (`pilot-10-v1`) was an engineering sample: the first
ten qualifying persons by identifier. Evaluation needs cases drawn by a
recorded rule from a known upstream partition, prepared by the same
pipeline. Work item 30.1 adds separately versioned case sets for that.
Command examples assume the session set-up in
[development.md](development.md#session-set-up).

## Registry

`ehr_evidence_app.casesets.registry` fixes every case set before anything is
drawn; `config/case_sets.json` mirrors it exactly and loading refuses a
mismatch.

| Case Set | Purpose | Upstream Partition | Size | Seed |
| :--- | :---: | :---: | :---: | ---: |
| `dev-30-v1` | Development: Building and Tuning the Application | Validation | 30 | 20260926 |
| `eval-100-v1` | Evaluation: Held for the Final Evaluation, Never Used for Development | Test | 100 | 20260927 |

Both exclude the pilot's cases. The two sets come from different
partitions, so they cannot overlap, and the evaluation set never meets the
upstream training or validation partitions. The label records the size;
changing a size, seed or rule is a new version, never an edit.

## Draw Rule

A case is eligible when:

- its graph lies in the named upstream partition, by the split replay
  verified against the run-binding record's partition digests;
- the graph carries `y_diagnosis` (the bridge filter);
- the graph has a readable subject and at least two visit nodes, each with a
  readable visit identifier, so a pre-target history exists;
- it is not a case of an excluded case set.

Eligible cases are ranked by the SHA-256 of `<label>:<seed>:<patient_index>`
and the first *size* are taken; case numbers follow ascending
`patient_index`. The rank depends on nothing but the fixed label, seed and
index, so anyone can repeat the draw. Cases that later fail admission are
reported with their reasons, never replaced, so the sample is not shaped by
the pipeline's own outcomes.

## Drawn Case Sets

| Case Set | Partition Size | Eligible | Excluded (Pilot) | Selected | Visit Nodes (Min / Median / Max / Total) |
| :--- | :---: | :---: | :---: | :---: | ---: |
| `dev-30-v1` | 10,016 | 10,016 | 0 | 30 | 2 / 3 / 14 / 124 |
| `eval-100-v1` | 15,024 | 15,022 | 2 | 100 | 2 / 3 / 29 / 437 |

Every partition replay matched the run-binding digests. The two excluded
evaluation-partition cases are exactly the pilot's two test-partition
cases. The selection records are pinned in `casesets.registry`.

## Stages

Each stage publishes one new protected record per case set and never
overwrites one. After a stage runs, its record's size and SHA-256 are pinned
in the registry, and the next stage reads the record only through that pin.

| Stage | Module | Runs In | Record (Under `protected-data`) |
| :--- | :---: | :---: | ---: |
| Selection | `casesets.selection` | GNN Environment, CPU | `case-sets\<label>` |
| Source Extraction | `sources.pilot --case-set` | Application Environment, Database Credentials | `source-casesets\<label>` |
| Preparation: Identity Mapping, Model Outputs, Case Facts, Admission | `casesets.prepare` | GNN Environment, CUDA | `case-preparation\<label>` |
| Event Linkage | `gnn.linkage --case-set` | GNN Environment, CPU | `evidence\event-linkage-<label>` |
| Evidence Bundles | `evidence.bundles --case-set` | Application Environment | `evidence-bundles\<label>` |
| Raw Records | `sources.mimic` (Every Case Set at Once) | Application Environment | `source-records\<label>` |
| Record Bundles | `evidence.record_bundles --case-set` | Application Environment | `evidence-bundles\<label>-records-v1` |

The selection reads the pinned graph collection on the CPU for subjects,
diagnosis-label presence and visit identifiers only; no model is loaded.
Its record holds, per case, the bridge index, graph position, subject and
visit-node count (patient-bearing); its summary holds aggregate counts by
skip reason, visit-node statistics and the verified partition digests.

The extraction runs the same bounded, read-only, single-transaction query
as the pilot, with one difference: the cohort is exactly the subjects of the
pinned selection record, matched on the person table's identity source
value. Its manifest records the selection record's digest and the case
set's own purpose statement, and `--verify-only` also checks that the
snapshot records the currently pinned selection.

The preparation does, in one run, what Phase 3 did for the pilot across
several steps. It loads the graphs, the preprocessed collection and the
model once and runs the pinned bridge in the same isolation as
`gnn.outputs`. For every case:

- the identity mapping follows the Step 17 rules, and each mapped case must
  be exactly the drawn case;
- the bridge record is regenerated, while the canonical three-record prefix
  must agree with the immutable canonical sample in the same run;
- the single-graph prediction is recomputed and must reproduce the
  published label, and its stability in batches of 64 and 128 is measured
  as in the run binding;
- label evidence, driver tags from the raw graph and typed admission follow
  the Phase 3 closure rules, and the replayed partition must be the case
  set's own.

It publishes the mapping, the bridge records, the case facts, the
agreement report and a closure record in the closure case schema, so the
evidence readers, the linkage and the bundle builder read a case set
exactly as they read the pilot. Cases that fail admission stay in the
record with typed reasons and are never replaced.

## Results

Every stage record of both case sets is published and pinned in
`casesets.registry`.

**Source snapshots.** Every table complete, no null visit links and no
concept without metadata.

| Case Set | Persons | Visits | Conditions | Procedures | Drug Exposures |
| :--- | :---: | :---: | :---: | :---: | ---: |
| `dev-30-v1` | 30 | 116 | 1,495 | 211 | 5,310 |
| `eval-100-v1` | 100 | 407 | 4,801 | 624 | 14,437 |

**Preparation.** Label correspondence was established and the single-graph
prediction reproduced for every case, and the canonical-prefix agreement
passed in both runs. Every quarantined case has repeated visit nodes: one
source visit at more than one graph position. The Phase 3 rules do not
admit such cases, because target and history cannot be kept apart.

| Case Set | Admitted | Quarantined (Repeated Visit Nodes) | Explanation-Eligible | Batch-Sensitive | Target-Outcome Entries Excluded (Cases) |
| :--- | :---: | :---: | :---: | :---: | ---: |
| `dev-30-v1` | 26 of 30 | 4 | 26 | 7 | 18 (10) |
| `eval-100-v1` | 92 of 100 | 8 | 91 | 23 | 95 (38) |

**Event linkage** (admitted cases). Every graph edge is backed by recorded
events, every driver links, and no in-vocabulary event lacks an edge.

| Case Set | Condition Edges | Procedure Edges | Drug Edges | Drivers Verified |
| :--- | :---: | :---: | :---: | ---: |
| `dev-30-v1` | 867 / 867 | 143 / 143 | 1,132 / 1,132 | 596 |
| `eval-100-v1` | 3,340 / 3,340 | 511 / 511 | 4,225 / 4,225 | 2,117 |

**Evidence bundles.** All published names equal the names in the
published outputs (604 and 2,115). The largest bundle is 108 KB, within
the 1 MB limit.

| Measure | `dev-30-v1` | `eval-100-v1` |
| :--- | :---: | ---: |
| QA Bundles (History / Selected Visit / All Visits) | 135 (26 / 83 / 26) | 519 (92 / 335 / 92) |
| QA Coverage Complete / Complete and Empty | 134 / 1 | 515 / 4 |
| Condition Records in the Pre-Target History | 682 | 2,588 |
| Final-Admission Condition Records Excluded From the History | 272 | 1,126 |
| Unmapped Condition Records Kept (in the History) | 80 (51) | 297 (203) |
| Explanation Bundles, Eligible | 26, 26 | 92, 91 |
| Attributions (All Narratable) | 578 | 2,023 |
| Linked History Events | 692 | 2,612 |
| Final-Admission Context Events | 325 | 1,367 |
| Final-Admission Diagnoses Withheld | 82 | 267 |

## Commands

```powershell
& $AppPython -m ehr_evidence_app.casesets.selection
& $GnnPython -m ehr_evidence_app.casesets.selection --operator-real --case-set dev-30-v1
& $GnnPython -m ehr_evidence_app.casesets.selection --operator-real --case-set eval-100-v1
```

The extraction needs database credentials in the process environment
(`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`), as described in
[source-data.md](source-data.md):

```powershell
& $AppPython -m ehr_evidence_app.sources.pilot --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.sources.pilot --case-set eval-100-v1
& $AppPython -m ehr_evidence_app.sources.pilot --case-set dev-30-v1 --verify-only
```

The remaining stages need no credentials; each reads the previous stage's
pinned record:

```powershell
& $GnnPython -m ehr_evidence_app.casesets.prepare --operator-real --case-set dev-30-v1
& $GnnPython -m ehr_evidence_app.gnn.linkage --operator-real --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.evidence.bundles --operator-real --case-set dev-30-v1
```
