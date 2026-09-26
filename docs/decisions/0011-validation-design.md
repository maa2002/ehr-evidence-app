# 0011. Validation design (Phase 6)

Status: accepted (2026-09-26).

## Context

The project brief requires that nothing reaches a user unless it has been
checked against the evidence (requirement RF-07, acceptance tests T05 to
T07, work items 35 to 40). Answers are written by fixed templates today;
from Phase 7 a language model may reword them. The checks must be in place
first, work without a model, and target the error classes of the brief's
verification lines: invalid identities and histories, malformed, absent,
cross-patient and wrong-scope references, added or omitted diagnoses,
unsupported positives and incomplete negatives, altered predictions and
attributions, unsupported dates and recurrence, and causal, directional,
exclusive or diagnostic wording in any outcome. The response contract
already defines a validation-rejection reason for each of them.

## Decision

1. **Candidates with fixed slots.** A writer produces a candidate: a list of
   statements, each with a kind from a closed list, typed slots for its
   facts (conditions, counts, visit numbers, match kinds, scopes, and later
   predicted labels, probabilities and scores), a wording with one
   placeholder per slot, and the references that support it, plus notices
   from a fixed approved list. Slot values are references and numbers, and
   their text is rendered from the evidence and the vocabulary, so a writer
   never types a fact. The released text and claims are rendered from the
   checked statements only.
2. **Deterministic support checks.** Every condition a statement names must
   match its cited records through the same vocabulary index the resolver
   uses; counts and visit numbers must equal those of the cited records; a
   list's statements must together name every recorded condition of the
   scope and nothing else; "not recorded" must cite complete coverage. For
   explanations, the predicted label, probability, drivers, their order and
   their scores must equal the explanation bundle. A second model as a
   judge is not used; it could only ever add a flag that releases nothing
   on its own.
3. **Versioned wording rules for every outcome.** Prohibited patterns
   (causal or directional attribution, percentage contribution,
   exclusivity, clinical status or advice, certainty) are versioned and
   checked in answers, explanations, clarifications and abstentions. Dates,
   recurrence and frequency are allowed only with cited linked events, as
   the evidence claim policy allows. Correct negated statements, such as
   "attribution does not mean causation", pass.
4. **A mutation suite.** The checks are tested by applying every defined
   error to correct responses built by the templates for the development
   case sets and to synthetic fixtures. Every mutant must be rejected with
   its reason code and every original must pass; results are reported per
   error class. The evaluation case set stays untouched.
5. **A template explanation now.** A deterministic explanation is built
   from the explanation bundles (the predicted diagnosis and its
   probability, the positive drivers in order with their scores, the linked
   events the policy allows, and the notice that attribution is an unsigned
   model sensitivity, not a cause). It gives the explanation checks real
   explanations to test, and a fallback that needs no model.

The work is delivered in four milestones: the contract and the checks
before generation and on structure (items 35 and 36); QA support and
wording (37 and 39); explanations (38 and 39); the release gate, the
mutation suite and release 0.8.0 (40 and 40.1).

## Consequences

- The model of Phase 7 can only reword the words around facts it cannot
  change; the checks target the defined error classes, and their limits,
  such as a reworded sentence that no rule sees, are documented.
- Answer text changes shape: every statement is one line, and every
  statement that cites records is also a claim with the same text.
- A candidate that fails a check is withheld as a validation rejection
  with the check's reason codes; nothing unchecked is released.
