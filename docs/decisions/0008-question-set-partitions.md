# 0008. Partitions of the MIMIC-IV-Ext-Instr question set

Status: accepted (2026-09-26).

## Context

MIMIC-IV-Ext-Instr 1.0.0 (PhysioNet, credentialed) was chosen as the
held-out wording test of Phase 5 (work item 34.2). It was built to
instruction-tune language models, so it may also serve later work, such
as a fine-tuned comparison model in Phase 9. Using it for testing now must
not spoil it for training later, and training on it must never touch the
patients this project evaluates on.

The archive ships a patient-level split: 38,299 training, 4,782
validation and 4,309 test patients. A check of its files (counts only)
found three traps:

- the validation file holds the whole test set (all 5,400 test admissions
  and 572 others), so choosing a model on it would tune on the test
  patients;
- 5,755 admissions (34,522 structured questions) are in no split, and 183
  of their patients are test patients;
- 26 patients of our case sets are in its training split: 17 of the 100
  evaluation patients, 8 development patients and 1 pilot patient.

## Decision

1. A versioned partition record (`mimic-iv-ext-instr-partitions-v1`,
   written once by `qa.heldout`) fixes every use of the dataset before
   anything is measured:
   - held-out: the test admissions;
   - validation: validation admissions outside the test set;
   - training: training admissions;
   - excluded: admissions in no split;
   and every admission of a patient in the pilot, development or
   evaluation case sets is removed from training and validation.
2. The held-out wording test reads only the held-out partition. The rules
   are never changed to fit it; a later rule version measured on it is
   reported as such.
3. Validation and training stay untouched until the Phase 7 and Phase 9
   plans decide their use. The grounded pipeline does not need a
   fine-tuned model: its answers come from the evidence bundles.
4. The archive is copied unchanged into the protected workspace and
   pinned by SHA-256; every member is checked against the archive's own
   checksums. The record holds admission identifiers, so it lives in the
   protected workspace; Git holds the code and the record's digest.
5. Anything derived from the dataset, including the weights of a model
   trained on it, is credentialed data under the same licence: it stays
   in the protected workspace and is never published or shared.

## Consequences

- The held-out results and any later training share one fixed split, and
  no model can be trained on the test patients or on the patients of our
  evaluation set.
- The diagnosis questions of the dataset are lists and rankings only, so
  presence-check wording needs its own test (work item 34.3).
- The dataset's answers come from MIMIC-IV tables, not from our OMOP
  records, so it tests wording, never answer correctness.
