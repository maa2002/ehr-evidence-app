# 0014. Structured evidence and syntax-only decoding (Phase 7)

Status: accepted (2026-09-27). Amends [ADR 0012](0012-generation-design.md),
decision 1, and [ADR 0013](0013-compact-generation.md), decisions 1, 6
and 7. Amended by [ADR 0015](0015-generation-configuration.md): the
worked examples disclose a record without a standard concept exactly when
the request's view has one (decision 3).

## Context

With the compact representation of ADR 0013 the model's output no longer
ran on or broke its format, yet the gate released only 3 of 33 sampled
development requests written in BF16 and 4 in NF4. What failed was reading
the evidence: finding the records that match a condition, grouping
records by condition, counting them, keeping each driver's records apart.
Those are deterministic operations the application already performs: the
question-answering rules of Phase 5 decide what the evidence says, and
generation decides how it is said. The model should not fail at
bookkeeping the rest of the application does, but it must still write
the complete candidate and be able to be wrong, and the unchanged
validator must catch what it gets wrong. The project owner decided the
points below after a review of the measurements.

## Decision

1. **Evidence structured for its task.** The canonical evidence bundle
   remains complete and unchanged for provenance and validation.
   Generation reads a deterministic task-specific projection holding all
   the evidence its request needs, without serialization redundancy or
   unrelated fields (`projection-v2`):
   - a list: the scope's records grouped by condition, in the order of
     each condition's first record, each record as [reference, visit
     number];
   - a presence check: the records that match the condition asked about,
     as the resolver matches them, grouped by condition and match kind,
     with the coverage status; an empty set when none matches;
   - an explanation: the case's restrictions with the approved notice
     that discloses each, and one block per selected driver, with its
     rank, reference and attribution score, its own linked records in
     pre-target visits and its records in the final admission. The
     attribution belongs to the concept, not to a visit; the block only
     joins it to the concept's verified occurrences.

   The projection states no outcome and no name: the model decides the
   outcome and which records each statement cites, and writes every
   wording.
2. **Counts and visits only if they prove a failure.** The projection
   gains counts and sets of visits only if a measurement shows them to be
   a material class of error: if, in either precision, at least 3 of the
   33 sampled requests are withheld for counts or visits alone, with their
   references right. The run with them is then repeated on the same
   requests, as an ablation of the evidence representation. The test was
   met at the first measurement (3 requests in each precision, all of
   them explanations), so the projection gives each group's and driver's
   counts and visits, and the totals (`projection-v3`); the model still
   restates them.
3. **Syntax-only decoding.** The grammar enforces the contract's syntax
   and nothing that depends on the evidence (`candidate-shape-v4`): the
   document's fields, types and rows; the contract's closed vocabularies
   (outcomes, reason codes, the notices an answer of the task may hold,
   match kinds); the formats and ranges of references, visit numbers,
   counts, ranks, scores and concepts, taken from the candidate contract;
   and the wording format. Which records, visits and drivers exist, the
   prediction's and drivers' values, and which outcome and notices a
   request calls for are left to the prompt's grounding rules and the
   release gate. This replaces the evidence-bound schema of ADR 0012
   (decision 1) and the constraints of ADR 0013 (decision 6) that settled
   an answer's substance: a list of a scope with records admitting only
   an answer, the number of drivers not described fixed, and the notice
   on them admitted only when some are. The prompt offers every reply a
   task can have, each with the condition on the view that calls for it;
   its list and presence examples include a record without a standard
   concept and its disclosure, and a presence check is shown both its
   answer and its "not recorded" (`prompt-v7`).
4. **Scores as the response shows them.** A score or logit is right when
   it reads as the evidence's value in the response, to three significant
   figures, the precision the response shows; the template still states
   the exact value (`explanation-template-v3`). Restating a
   sixteen-figure value exactly is bookkeeping, not grounding, and no
   released text can differ from the one the exact value renders.
5. **Explanation completeness is measured, not required.** A history or
   context line that is stated must cite all of its driver's records,
   since it states their count; none is required. The rule of ADR 0013,
   decision 7, that every described driver state its history and context
   is withdrawn. How many of the available lines the model states is
   reported beside the release rate, in development measurement and in
   Phase 9. Mutation suite version 4 turns the two omission classes into
   controls that must be released.
6. **When the validator may change.** Until the final configuration is
   locked, a model output may expose a missing validation class. A new
   rule must answer a project requirement rather than one sentence, comes
   with a mutation test and a new version, and never weakens an existing
   check to raise the release rate. The validator is frozen together
   with the prompt, the evidence selection and the configuration before
   the final evaluation.
7. **Parity in Phase 9.** The baseline and the controlled condition read
   the same projection and write the same document under the same
   syntax-only grammar, with the same model, precision, decoding, K and
   limits. The controlled condition adds the prompt's grounding rules,
   the checks before generation and the release gate.
8. **K is 5 until frozen.** An explanation describes at most 5 drivers,
   which keeps every development explanation within the output limit. K
   is chosen from development measurements and frozen before the final
   evaluation; `eval-100-v1` is not used to choose it.
9. **Precision by a paired comparison.** BF16 with the mixed placement
   stays until NF4 is shown non-inferior on the full development case
   set: the 95% interval of the paired difference in release rate (NF4
   minus BF16; Newcombe's method 10) must lie above minus 5 percentage
   points. The exact McNemar test is reported; a difference that is not
   significant is never read as equivalence.

## Consequences

- The projections are 4% of the bundles' size, and the correct answer of
  every development request is still derived from its projection alone,
  counts and notices included.
- The schema of a task is the same for every request, so a model can now
  cite a record the bundle does not hold, state a wrong score or choose
  the wrong outcome; the gate withholds each of them.
- On the 33 sampled development requests, written in both precisions and
  paired, the gate released 9 (BF16) and 8 (NF4) with `projection-v2`,
  12 and 11 once counts and visits were given (`projection-v3`), 21 and
  18 once the worked examples disclosed a record without a standard
  concept (`prompt-v6`), and 26 and 17 with the notice of each restriction
  and a "not recorded" example (`projection-v4`, `prompt-v7`). The
  versions were tuned on these requests, so their last rates are
  optimistic ([generation.md](../generation.md)).
- At `prompt-v7` BF16 released every request NF4 did and 9 more (NF4
  minus BF16 -27.3 points, 95% interval -41.2 to -11.6, exact McNemar p
  0.004). The precision, K and the limits are decided on the whole of
  `dev-30-v1`, as decision 9 sets out.
