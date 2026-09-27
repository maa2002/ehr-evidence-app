# 0013. Compact generation and driver selection (Phase 7)

Status: accepted (2026-09-27). Amends [ADR 0012](0012-generation-design.md),
decision 1: the model reads a compact projection of the bundle, and an
explanation describes the selected drivers. Amended by
[ADR 0014](0014-structured-evidence.md): the projection is structured for
its task (decision 1), the schema enforces syntax only (decision 6), and
an explanation's history and context are measured, not required
(decision 7).

## Context

The second milestone of Phase 7 measured MedGemma 1.5 4B IT in BF16 with
the mixed GPU/CPU placement of [ADR 0012](0012-generation-design.md): its
language layers on the GPU, its token embedding table and output layer on
the processor, so it is not GPU-resident. It writes about eight tokens a
second. The development prompts, holding each complete canonical bundle,
had a median of 6,079 tokens for lists and 16,518 for explanations, and a
complete candidate wrote a sentence for every diagnosis and a paragraph
for every driver. Under the configured limits (16,384 prompt tokens, 768
output tokens, 120 seconds) most explanations and long lists would have
fallen back. The project owner directed that this be treated as a finding
about the prompt and runtime design, not as a reason to raise the limits
for a verbose representation.

## Decision

1. **A compact generation projection.** The model reads a deterministic
   projection of the canonical bundle (`projection-v1`), not the bundle:
   serialization redundancy and fields the task does not use (names,
   dates, native identifiers, visit and record metadata, provenance) are
   left out, and records keep their references. The canonical bundle stays
   unchanged for provenance and validation, and the release gate checks
   every candidate against it. The projection is shown to hold everything
   its task needs: the correct answer is derived from it alone for every
   request of the development case sets.
2. **Compact output.** Lists stay complete: every condition is stated,
   as a row of its references and facts under one wording shared by all
   items, not as prose per diagnosis. Explanations are concise: one
   wording each for the driver, history and context lines, each driver a
   row.
3. **Driver selection.** An explanation describes the eligible drivers
   with the highest attribution scores, highest first, ties in the
   bundle's order, at most a configured number K
   (`config/explanation.json`, `explanation-template-v2`). A notice
   states how many more are kept in the evidence; the gate requires
   exactly the selected drivers and that number. Every attribution stays
   in the evidence bundle for inspection. K is measured over candidate
   values on the development case sets and frozen before the final
   evaluation; until then it is 10, marked not frozen.
4. **Limits after re-measurement.** The limits change only after the
   token distributions are measured again on the compact representation.
   A request still over a limit falls back to the template and is an
   operational `limit_exceeded` case: never insufficient evidence, and
   never a success of the model.
5. **A precision comparison.** BF16 with the mixed placement, the
   configured precision, is benchmarked against 4-bit NF4, which is
   GPU-resident, on a deterministic, stratified sample of development
   requests: GPU memory, latency, tokens a second and the gate's pass
   rate. The precision changes only by the project owner's decision.
6. **The order and style the model writes in.** The first benchmark of
   the compact representation (`candidate-shape-v2`, `prompt-v2`)
   released 4 of 33 sampled development requests in either precision.
   Diagnosed by field on the development requests, and by reading the
   model's output on invented requests, the failures had three causes: a
   wording that lost a placeholder could not be closed and ran on until
   the runaway guard stopped it (the model began a list's summary with an
   empty list's "No conditions are recorded", or typed a condition's name
   for its placeholder); the model wrote counts before listing what they
   count; and it copied example notices the request did not admit. So
   (`candidate-shape-v3`, `prompt-v3`):
   - a row lists its records before what is counted from them, and a
     summary follows its items;
   - every wording begins with its subject's placeholder (the condition,
     the number of conditions, the label, the rank or the driver); only an
     empty list's begins freely;
   - the schema admits only the replies a request can have: a list of a
     scope with records is an answer, the notice on drivers not described
     only when some are, and their number as stated in the view;
   - the prompt offers only those replies and their example wordings; its
     list and presence examples group several records of one concept into
     one item, and an explanation's example leaves drivers out exactly
     when the request does;
   - documents in prompts, and counted against the output limit, are
     written as the runtime writes JSON, with a space after each comma and
     colon.
7. **Two gaps in the gate closed.** On invented requests the gate
   released a list whose summary the model worded "No conditions are
   recorded within {scope} ({records}): {groups}". The wording rules now
   read a wording with its statement's kind (`wording-rules-v4`): a
   statement that something is recorded may hold a negation only where it
   denies a causal, diagnostic or risk reading, and a statement that
   something is not recorded must negate every "recorded" it holds. On
   development requests the gate released explanations that left a
   driver's history out: an explanation now states each described
   driver's history and context exactly when it has such records, as a
   list names every condition.

## Consequences

- On `dev-30-v1` the projections are 5% of the bundles' size, and the
  prompts' median falls from 6,079 tokens to 1,769 for lists (largest
  5,217) and from 16,518 to 2,272 for explanations with K = 10 (largest
  2,454). Written as the runtime writes them, the template documents have
  a median of 292 output tokens for lists (largest 1,950), 85 for presence
  checks and 642 for explanations with K = 10 (largest 838). Under the
  configured limits every prompt fits, and the longest lists and some
  explanations with K = 10 exceed the output limit.
- The explanation contract changes: the drivers described are the
  selected ones, in score order, and a new approved notice states the
  drivers not described (candidate contract 1.1). Mutation suite version
  3 gains the classes for them, for an explanation's completeness and for
  a statement's polarity.
- Each fallback reason has a category for records and Phase 9
  (`limit_exceeded` for prompts or outputs over their limit or cut off).
- On 33 sampled development requests the gate released 4 written by the
  model in BF16 with the mixed placement and 4 in NF4 with `prompt-v2`,
  and, as it now stands, 3 and 4 with `prompt-v3`
  ([generation.md](../generation.md)). With `prompt-v3` no output ran on
  or broke its format; what remains are facts the gate does not support,
  above all in presence checks, where the model answered "recorded" for
  every condition recorded elsewhere. The gate withheld all of them. NF4 writes lists and presence checks
  about 1.7 times as fast, in a little over half the GPU memory;
  explanations run at 7 to 8 tokens a second in either precision. The
  precision, K and the limits stay as configured until the project owner
  decides.
