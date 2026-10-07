# 0002. Phase 3 Closure and Phase 4 Admission

Status: accepted (2026-09-25).

## Context

Phase 3 bound the inherited GNN to verified identities for the 10-person
development pilot and published immutable records (identity mapping,
preprocessing parity, offline outputs, provenance, split membership). Those
records mark every case `phase4_eligible: false` and several facts as
unknown. An independent review of the records, the inherited source and the
checkpoint found:

- **Confirmed.** Pinned identities of all inherited assets and records; the
  identity chain (10 persons, 38 visit nodes, 10 targets, 28 history links,
  no target in its own history); the label rule and decode; preprocessing
  parity and the model rebuild; target-visit inputs without a causal mask.
- **Recovered.** The exact bytes of the wrapper that produced the outputs
  (previously recorded as not retrievable), and the binding between the
  checkpoint, graph labels, label order and split (previously unknown), from
  the checkpoint's saved `pos_weight` and metric reproduction. Pilot
  membership: 8 training, 0 validation, 2 test.
- **New model limitations.** Zero-score target-visit diagnoses can appear in
  driver lists; most drug drivers attach to the target admission;
  predictions depend on batch composition; the output is a final-admission
  prediction with target-admission context, not a next-visit prediction.
- **Record defects.** Untyped `phase4_eligible: false` flags, a
  `label_index_correspondence: not_established` flag contradicted by the
  evidence, and a policy rule that blocked whole cases for missing event
  linkage although only specific claims need it.

Details and figures are in [inherited-gnn.md](../inherited-gnn.md) and
[model-outputs.md](../model-outputs.md).

## Decision

1. Published records stay byte-identical. Findings that change their meaning
   are recorded in new, versioned records that reference them; nothing is
   re-run or edited in place.
2. A run-binding verification records, as aggregates only, the split replay,
   the `pos_weight` match, the metric reproduction, its negative controls
   and the batch-sensitivity diagnostic.
3. A closure record replaces the untyped eligibility flag with typed,
   per-case admission, three label-evidence layers, per-driver visit tags
   and a claim policy, and maps each superseded flag to the evidence that
   supersedes it.
4. All ten pilot cases are admitted to evidence-object construction. The
   zero-score target-visit condition entries are excluded from anything a
   language model sees.
5. Missing event linkage restricts claims (dated, per-visit, frequency and
   progression statements about events); it does not block a case.
6. Prospective, pre-target-only, causal, directional and percentage claims
   remain prohibited. The reported AUROC is not cited for single-graph
   bridge predictions.

## Consequences

- Decisions 2 and 3 are implemented by `ehr_evidence_app.gnn.binding` and
  `ehr_evidence_app.gnn.admission`; their records are
  `inherited-run-binding-v1` and `phase3-closure-v1`.
- Phase 4 consumes Phase 3 only through the closure record and the claim
  policy in [model-outputs.md](../model-outputs.md#claim-policy).
- Evidence bundles must carry driver visit tags and must drop the excluded
  entries before any generation step.
- Written descriptions of the model (including the project report) should
  call it a final-admission diagnosis prediction that includes
  target-admission context.
- The pilot remains a development sample; evaluation needs a larger,
  separately versioned case set prepared by the same pipeline.
