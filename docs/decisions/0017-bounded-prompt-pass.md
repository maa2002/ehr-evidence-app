# 0017. A Bounded Prompt Pass Before the Lock (Phase 7)

Status: accepted (2026-09-28). Amends
[ADR 0015](0015-generation-configuration.md): explanations read
`projection-v5` with `prompt-v9`; lists and presence checks are
unchanged.

## Context

With the configuration of ADR 0015 the model's candidates were released
for 377 of the 430 requests of `dev-30-v1` and 157 of the 182 of
`pilot-10-v1`. Lists (36 of the 53 development failures) and explanations
(13, half of them) were what remained. I allowed one bounded pass on the
prompt before the final lock, with the development
benchmark scoring documents by precision, recall and F1 and naming what
the rows of a withheld document are.

## Decision

1. **Rules fixed before any run.** Explanations first, then lists; the
   precision, K and the limits stay frozen, and every presence prompt
   stays the same byte for byte. The failures are diagnosed on
   `dev-30-v1` only, at most two candidates are tried per task, on
   `dev-30-v1` only, and a task's candidate is kept only if it releases
   more of that task on `dev-30-v1` than `prompt-v8` and, run once on
   `pilot-10-v1`, at least as many there. No pilot result informs a
   change; `eval-100-v1` is not used.
2. **The diagnosis.** At `prompt-v8` the withheld explanations stated a
   context line for drivers that have none, 12 times with the driver's own
   history records and 4 times with another driver's, and 7 history lines
   cited part of their records; the view gave an empty context as `[]`
   and a history as `[reference, visit number]` pairs. The withheld lists
   wrote a condition's records of one visit as an item of its own (75
   rows, one visit of several) and repeated conditions (76 rows), and 51
   conditions were never named; the view gave a list's records as the
   same pairs.
3. **Explanations: kept.** A driver's history and context are given as
   the reply states them, their references, visit numbers and number,
   and `null` when the driver has none (`projection-v5`, `prompt-v9`).
   On `dev-30-v1` the explanations released rose from 13 to 21 of 26 and
   on `pilot-10-v1` from 4 to 8 of 10.
4. **Lists: not kept.** A list's groups given the same way, with the task
   saying that a group is one item however many visits its records lie
   in, released 94 of 134 lists on `dev-30-v1` against 98. It named more
   conditions (3 left unnamed against 51) and raised the conditions' F1
   from 0.783 to 0.830, but the model still split a condition's records
   by visit (66 rows) and misstated the number of records without a
   standard concept in 12 lists, which invented lists with up to three
   such records did not reproduce. With no cause to test, no second
   candidate was run, and lists read the view of `prompt-v8`.

## Consequences

- The configuration for the final evaluation reads `projection-v5` and
  `prompt-v9` with `candidate-shape-v4`; the precision, K and the limits
  of ADR 0015 are unchanged. Every list and presence prompt of both
  development case sets is the same as under `prompt-v8`, and the
  correct answer of every development request is still derived from its
  projection alone.
- The model's candidates are released for 385 of the 430 requests of
  `dev-30-v1` (89.5%) and 161 of the 182 of `pilot-10-v1` (88.5%).
- Both development case sets have now shaped the configuration:
  `dev-30-v1` through every version, `pilot-10-v1` through the checks of
  `prompt-v8` and `prompt-v9`. The final evaluation's requests and
  references are its first unseen measure.
