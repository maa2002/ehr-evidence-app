# 0007. Rule-Based Question Interpretation and Answers

Status: accepted (2026-09-26).

## Context

Phase 5 turns a free-text question about the active case into one of two
tasks, a condition list or a condition presence check, and answers it from
the case's evidence bundle. The interpretation could be made by a language
model or by fixed rules. Answers must be checkable against their evidence,
reproducible, and never widen the scope or the claims the bundle supports.
The measurement of this layer must not be tuned on the data it is later
judged by.

## Decision

1. **Deterministic interpretation.** `qa.interpret` reads a question with
   fixed frames and rules; no model is involved. Rules for requests outside
   the supported tasks (explanations, other patients, treatment, prognosis,
   diagnosis requests, other data, dates, times, order and counts) apply to
   the words outside the condition phrase. Inside the phrase they apply
   only when the phrase names no condition, because condition names contain
   such words. The phrase is resolved by its longest candidate that names
   something.
2. **The scope never changes silently.** A question naming another scope,
   two scopes or a missing visit gets a clarification (`qa.scope`).
3. **Answers from templates over the verified bundle.** `qa.answer` reads
   the case's QA bundle through the pinned manifest, builds list and
   presence answers from fixed templates, cites the bundle's records and
   coverage record, and checks the citations before release. "Not
   recorded" needs complete coverage and always says it is not a statement
   of clinical absence. Dated and frequency questions are not answered.
4. **Measured on development data only.** `qa.suite` scores the pipeline
   against an independent oracle, computed from the source snapshot and the
   vocabulary tables, on development case sets; it refuses the evaluation
   case set. A vocabulary sweep checks every standard condition name.
5. **A held-out wording test.** Wording written by others is measured on
   MIMIC-IV-Ext-Instr (work item 34.2), never used to change the rules.

## Consequences

- The same question about the same bundle always gets the same response;
  every measurement records the rule versions it used.
- Phrasings outside the frames get a clarification rather than a guess;
  supporting a new phrasing is a versioned rule change, measured by the
  suite and the held-out test.
- The language model of Phase 7 may only rephrase an answer, and every
  rephrasing passes the Phase 6 checks against the same bundle.
- The development results measure agreement with an independent
  computation on the developers' own wording; wording robustness is
  measured separately, and clinical correctness is out of scope.
