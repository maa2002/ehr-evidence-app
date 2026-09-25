# 0004. Evidence contracts, scopes and bundle-local references

Status: accepted (2026-09-26).

## Context

Phase 5 to 7 (question interpretation, validation, generation) need stable,
checkable inputs and outputs before any language model exists. Their failure
modes are known in advance: an answer drawn from the wrong patient or scope,
a negative answer read as clinical absence, invented names, citations that
resolve in another case's evidence, and outcomes that blur together (for
example an error that still releases text). The pilot snapshot also holds
records without a standard concept (concept 0): 29 condition and 385 drug
records.

## Decision

1. Requests, responses and evidence records are strict, immutable models
   that refuse unknown fields. Every value later code branches on is a
   closed vocabulary. JSON Schemas are committed and a test fails on drift.
2. A response has exactly one of eight outcomes. Only answer, not-recorded,
   explanation, clarification and abstention release text; every outcome
   other than an answer states a reason code from its own closed list;
   answers and explanations cite claims from their evidence bundle.
3. Evidence is referenced by bundle-local identifiers (`V`, `E`, `P`, `A`,
   `C`). A response is bound to one bundle by identifier and SHA-256, and
   the bundle to one case and one scope; a citation outside it is refused.
4. QA supports three explicit scopes: the pre-target history (default), one
   selected visit, and all case visits. Explanations use the pre-target
   history only.
5. Records without a standard concept are kept with their source code and
   disclosed through coverage counts and a reason code; they are neither
   dropped nor named.
6. Names come from pinned tables and state their source; a missing name is
   reported as unavailable, never guessed.
7. Execution limits have fixed ceilings in code; configuration may lower
   them only.

## Consequences

- Phase 6 validators can check structure, references and scope mechanically
  before judging content.
- A not-recorded answer always means not recorded within the selected scope,
  with the coverage statement that defines completeness.
- Bundle identifiers and digests make every answer reproducible from a
  published bundle set.
- Adding a task, scope, outcome or reason code is a versioned change to the
  vocabulary and the committed schemas.
