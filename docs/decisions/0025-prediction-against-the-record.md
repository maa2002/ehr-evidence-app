# 0025. The Predicted Diagnosis Against the Record

Status: accepted (2026-09-30), at the project owner's request. Adds to
[ADR 0019](0019-api-and-interface.md) (decision 5: the prediction, the
drivers and the diagnoses apart).

## Context

The explanation shows the diagnosis the GNN model predicted for the
final admission and what its score was sensitive to. The project owner
found it hard to say what the prediction means to a reader. A reader can
tell at once when the page also says whether that diagnosis is recorded
for the final admission: the record holds the answer, and the evidence
of the final admission's scope already shows it to anyone who asks.

The final admission's diagnoses are what the model predicts. The claim
policy keeps them out of any evidence given to a language model
([inherited-gnn.md](../inherited-gnn.md#model-limitations)), and the
explanation leaves them out; nothing here changes that.

## Decision

1. **The check.** The API's evidence of a prediction holds `in_record`:
   the final admission's visit number, the coverage of its records, and
   those of its records that match the predicted diagnosis, with how,
   by the condition rules' matching (`qa.resolve.match`) over that
   visit's QA bundle: the diagnosis itself, a more specific concept, or a
   record without a standard concept under an ICD code whose description
   names it.
2. **What the page says**, beside the prediction under In the Record:
   recorded, with the number of records of the diagnosis itself; recorded
   under an ICD code whose description names it; not recorded as such,
   but a more specific diagnosis is, with its names; not recorded, only
   when that visit's records are complete, and never as clinical absence;
   or that it cannot be told.
3. **Apart.** The check is not part of the explanation, its checks or
   its measurement, and never reaches the language model; the
   explanation bundle, the prompt and the gate are unchanged. It is a
   statement about this case's record, never a measure of the model's
   accuracy.

## Consequences

- A reader sees whether this prediction matches what was recorded for
  the final admission, and in which way.
- A diagnosis recorded only as a more specific one is not the model's
  label as such; the page says so rather than counting it as recorded.
