# 0024. No Fine-Tuning

Status: accepted (2026-09-30), by the project owner. Closes decision 6 of
[ADR 0012](0012-generation-design.md) and decision 3 of
[ADR 0008](0008-question-set-partitions.md).

## Context

ADR 0012 (decision 6) left fine-tuning to Phase 9, "if the development
measurement calls for it", and ADR 0008 (decision 3) kept the training
and validation partitions of MIMIC-IV-Ext-Instr untouched until then. The
development measurement is complete: with the frozen configuration
([ADR 0015](0015-generation-configuration.md),
[ADR 0017](0017-bounded-prompt-pass.md)) the model's candidates are
released for 385 of the 430 requests of `dev-30-v1` (89.5%) and 161 of
the 182 of `pilot-10-v1` (88.5%). It does not call for fine-tuning:

- **It cannot make an answer more correct.** Every answer passes the
  release gate or is replaced by the template's, which passes the same
  gate; fine-tuning could only raise how often the model's own answer is
  the one released.
- **The gain is bounded.** Of the 45 development requests that fall
  back, 10 are lists whose correct document is longer than the frozen
  limit of 768 output tokens; at most 35 remain, 8.1 points.
- **The data does not fit the task.** MIMIC-IV-Ext-Instr pairs questions
  with free-text answers from MIMIC-IV tables. Here the rules read the
  question and the model writes a structured document that cites the
  records of a bundle. The only documents in the task's shape are the
  templates' own, and a model trained on them would learn to copy the
  templates.
- **The cost.** The full-precision weights (8,202 MiB) do not fit the
  GPU (8,188 MiB), so training would run on the 4-bit weights, the
  precision ADR 0015 did not keep (40.0% released against 73.0%); the
  Phase 7 measurement would be repeated and the configuration frozen
  again; and trained weights would be credentialed data, never published
  or shared (ADR 0008, decision 5).

## Decision

1. The model is not fine-tuned. MedGemma 1.5 4B IT is used as pinned,
   in the frozen configuration.
2. The training and validation partitions of MIMIC-IV-Ext-Instr are not
   used; the held-out partition keeps its use as a wording test.
3. Fine-tuning is future work: a fine-tuned model would be a new version,
   measured on the development case sets and evaluated anew.

## Consequences

- Phase 9 evaluates the pinned model in the frozen configuration, and
  counts each fallback as its failure (ADR 0012, decision 4).
- The report states that no fine-tuning was done, and why.
