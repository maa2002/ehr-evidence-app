# 0027. Automatic rating of the prose rubric

Status: accepted (2026-10-03), by the project owner, before any request
of `eval-100-v1` is written. Amends decision 9 of
[ADR 0016](0016-evaluation-metrics.md) (the prose rubric).

## Context

Decision 9 of ADR 0016 had a rater read every explanation of both
conditions, and a sample of 50 answers per condition, and mark each pass
or fail on four items without seeing which condition wrote it:
prediction and concept identity, evidence, history, and attribution
wording. With 91 explanation-eligible cases in `eval-100-v1`
([case-sets.md](../case-sets.md)) that is up to 282 texts, more reading
than the owner, who was to rate them, has time for.

Three of the four items need no reader. A writer never types a fact
([ADR 0011](0011-validation-design.md), decision 1): the prediction,
drivers, conditions, records, numbers and dates a text states are slot
values rendered from the references it cites, so code can compare each
of them with the request's reference. Only the attribution wording, the
words a writer chose around its slots, has to be read. The runtime's
wording rules cannot rate it: the controlled condition releases only
what they pass, so they would measure the gate against itself, and they
are patterns, which miss what is said in words no pattern knows
(`validation.language`).

## Decision

1. **What is rated.** Every explanation, and every answer to a condition
   question, of both conditions, whether the model or a template worded
   it. Rating by code and by the rater takes no one's time, so no sample
   is drawn. Answers about the whole record are worded by the templates
   in both conditions (ADR 0016, decision 11) and are not rated.
2. **Identity, evidence and history, by code.** Each is judged against
   the request's reference, independently of the runtime's validators.
   Prediction and concept identity passes when every prediction, driver,
   condition and record the text names is the reference's, with its
   label, score, rank or concept; evidence, when every factual statement
   is supported by the records it cites (ADR 0016, decision 7); history,
   when every date, count, visit and recurrence it states, and every
   history and context line, is the reference's.
3. **Attribution wording, by an automatic rater.** A text fails when any
   sentence states or implies, of a driver, a record or the prediction:
   that it caused or led to something, or the direction of an effect; a
   share or percentage of the prediction; that it alone accounts for it;
   a diagnosis or a clinical status; a risk; a treatment or other
   advice; or certainty beyond what the record and the prediction state.
   A sentence that denies such a reading ("attribution does not mean
   causation") passes. The rater is not the evaluated model and is never
   given the runtime's wording rules. It runs on the workstation with
   fixed settings and reads one text at a time, in an order shuffled
   with a fixed seed, given only the text and these criteria: never the
   condition, the case or the reference. Its identity, version, settings
   and instructions are frozen with the evaluated version and recorded
   with the results.
4. **The rater checked before use.** On the development case sets only,
   the rater marks texts known to pass (template-worded answers and
   explanations, and the same texts with a correct negation added) and
   the same texts with one fault planted per class of decision 3, in
   forms the runtime's wording rules name and in paraphrases they do
   not. For each class, the faults it catches and the passing texts it
   fails are reported. Its instructions may be revised on development
   data until they are frozen; the check of the frozen rater is the one
   reported.
5. **The owner's check.** After the rating, and before the conditions
   are unsealed, the owner rates attribution wording, blind, on 30 texts
   drawn with a fixed seed: up to 15 that the rater failed and the rest
   from those it passed, shuffled together, shown without the rater's
   marks or the conditions. Both sets of marks are reported, per
   stratum. The rater's marks are the ones scored, and none is changed
   afterwards.
6. **Reporting.** For each item and for all four together, the share of
   rated texts that pass, by task and condition, with the paired
   comparison of ADR 0016 (decision 10) over the requests that have a
   rated text in both conditions; requests with a rated text in one
   condition only are counted apart.

## Consequences

- Decision 9 of ADR 0016 reads as amended here; its other decisions are
  unchanged.
- Work item 59 scores identity, evidence and history with the
  explanation and evidence metrics, and runs the rater and its check,
  each with hand-worked examples, before the final evaluation runs.
- The wording marks are an automatic rater's, with a measured catch
  rate and the owner's check of 30 texts, not a clinician's reading of
  every text; the report says so.
- The runtime is unchanged: no judge releases or withholds anything
  (ADR 0011, decision 2). The rater only measures.
