# Case sets

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

| Case set | Purpose | Upstream partition | Size | Seed |
| --- | --- | --- | --- | --- |
| `dev-30-v1` | Development: building and tuning the application | validation | 30 | 20260926 |
| `eval-100-v1` | Evaluation: held for the final evaluation, never used for development | test | 100 | 20260927 |

Both exclude the pilot's cases. The two sets come from different
partitions, so they cannot overlap, and the evaluation set never meets the
upstream training or validation partitions. The label records the size;
changing a size, seed or rule is a new version, never an edit.

## Draw rule

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

## Stages

Each stage publishes one new protected record per case set and never
overwrites one. After a stage runs, its record's size and SHA-256 are pinned
in the registry, and the next stage reads the record only through that pin.

| Stage | Module | Runs in | Record (under `protected-data`) |
| --- | --- | --- | --- |
| Selection | `casesets.selection` | GNN environment, CPU | `case-sets\<label>` |
| Source extraction | `sources.pilot --case-set` | application environment, database credentials | `source-casesets\<label>` |
| Preparation: identity mapping, model outputs, case facts, admission | planned | GNN environment, CUDA | planned |
| Event linkage | `gnn.linkage` | GNN environment, CPU | planned |
| Evidence bundles | `evidence.bundles` | application environment | planned |

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
