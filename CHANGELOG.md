# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html); 0.y releases may
change interfaces.

## [Unreleased]

LLM and operations (Phase 7, work items 41 to 46.1), in progress: the
language model writes the complete candidate of every response from
evidence, and the templates are its fallback.

### Added

- `generation` package (Phase 7, work items 41 and 43;
  [ADR 0012](docs/decisions/0012-generation-design.md),
  [docs/generation.md](docs/generation.md)): the JSON document a
  candidate is written as and each request's JSON Schema
  (`generation.shape`, `candidate-shape-v1`); the prompt with the rules,
  a worked example on invented data and the complete bundle
  (`generation.prompt`, `prompt-v1`); the adapter, the one path from a
  writer's output to a release, under the prompt, output, time, attempt
  and concurrency limits, which releases the template candidate with one
  of eleven recorded fallback reasons whenever the model's is not
  (`generation.adapter`), with a scripted backend and scripted runs on
  the invented fixtures and the development case sets.
- `max_prompt_tokens` in the execution limits (ceiling 131,072;
  configured 16,384 until the model is measured).
- The model runtime (work item 42): `config/generation.json` pins
  MedGemma 1.5 4B IT at revision `91850547` with every file's size and
  SHA-256, checked before the worker starts (`generation.runtime`); the
  worker runs in its own environment on G:, loads the model once in full
  precision (language layers on the GPU, image encoder and tied token
  embedding table in system memory), decodes greedily under each
  request's schema with xgrammar, and answers one request at a time over
  its standard input and output, offline (`generation.worker`); the
  operator check loads it, writes the invented requests and counts the
  prompt tokens of a development case set.
- The model environment: `requirements/llm.txt` and the hashed
  `requirements/llm.lock.txt` (44 packages; PyTorch 2.14.0 with CUDA 13.0,
  Transformers 5.17.0, xgrammar 0.2.8, llguidance 1.8.0) on a uv-managed
  Python 3.12.13; the storage policy routes the model environment
  (`llm_env`), managed interpreters (`UV_PYTHON_INSTALL_DIR`) and CUDA's
  compute cache (`CUDA_CACHE_PATH`) to the runtime root.
- Release-gate checks for the mistakes a language model can make and a
  template cannot: a refusal of a request whose bundle is bound, a
  presence answer about another condition than the one asked about
  (`scope_violated`), a notice stated twice and statements out of their
  answer's order (`structure_invalid`), and a condition named twice for
  one match kind in a presence answer (`claim_unsupported`).

### Changed

- Wording rules version 3 (`wording-rules-v3`): association,
  complication, interpretive, disease-course, certainty and recency
  wording everywhere, and hedged, ranked or counted facts in answers and
  explanations.
- Mutation suite version 2 (`validation-mutations-v2`): 18 classes of
  model-style mistakes; on the development case sets it detects all
  23,999 mutants in 58 classes, and every original and control is
  released unchanged.
- `qa.answer.answer` and `validation.explanation.explain` take a writer;
  without one every answer from evidence is the template's, recorded as a
  `no_model` fallback, and responses are unchanged.
- The schema of a wording requires each of its kind's placeholders
  exactly once and allows only letters, spaces and plain punctuation;
  wordings are defined once under `$defs`, and the worker stops output
  that runs 150 tokens without a double quote. The prompt says that
  placeholders are replaced by their facts' text, gives example wordings
  and states that records of one standard concept are one condition. All
  of these follow the model's first runs.

## [0.8.0] - 2026-09-26

Validation (Phase 6, work items 35 to 40.1): every response with text
is written as a candidate whose facts are slots filled from the
evidence, and leaves only through a release gate that checks its
structure and references, the support of QA statements by their
cited records, the preservation of the prediction and drivers in the
new template explanation, and its wording. A mutation suite detects
every mutant of 40 error classes on the development case sets.

### Added

- `validation` package (Phase 6, work items 35 to 40.1;
  [ADR 0011](docs/decisions/0011-validation-design.md)): the candidate
  contract, in which a response is a list of statements with typed
  slots for its facts and approved notices, rendered into the released
  text and claims; checks before generation (`validation.evidence`);
  structure and reference checks (`validation.structure`); support of
  every QA statement by its cited records (`validation.support`, work
  item 37); versioned wording rules for every outcome
  (`validation.language`, `wording-rules-v2`, work item 39); the
  template explanation of a selected prediction, with checks that it
  preserves the prediction, the drivers, their order and scores, and
  cites history only as the evidence claim policy allows
  (`validation.explanation`, work item 38): all 36 development cases
  get an explanation that passes every check; the release gate
  (`validation.gate`, work item 40), through which every response with
  text leaves, withholding a failing candidate as a validation
  rejection and a check that fails to run as an operational error; the
  mutation suite (`validation.mutations`, work item 40.1), which
  detects all 15,558 mutants of 40 error classes on the development
  case sets while every original response passes. The candidate's JSON
  Schema is committed with the other contracts.
- `qa.templates`: the QA answers, refusals and clarifying questions
  written as candidates.

### Changed

- QA responses with text are rendered from candidates checked for
  structure, support and wording (`qa-answer-v5`); a candidate that
  fails a check is withheld as a validation rejection, and a bundle that
  does not fit its request is rejected before any text. Every statement
  is one line, every statement that cites records is also a claim with
  the same text, and scopes read naturally ("within visit 2").

## [0.7.1] - 2026-09-26

Fail-safes in the QA rules (Phase 5, work item 34.5): rules version 4
answers a question only when every word of it is accounted for, and
reads more ways of naming the selected scope. Measured on a new
written set, it keeps 2 wrong answers of 226 where version 2 gives 9
on the same questions.

### Added

- Development suite version 4 with a family for each fail-safe, for
  negative wordings and for the new scope phrasings: 1,892 of 1,892 on
  `dev-30-v1`, 784 of 784 on the pilot; the vocabulary sweep still
  returns the asked concept for all 788,221 questions.
- Written sets `written-v3` and `written-v4`, with four more families:
  lists of only some conditions, lists of a number of conditions, two
  conditions at once and presence checks asked in a negative way.

### Changed

- Rules version 4, with fail-safes
  ([ADR 0010](docs/decisions/0010-fail-safes.md)): a list or a presence
  check is released only when every word of the question is accounted
  for. A visit, stay or time named in unrecognised words, an exclusion
  or filter, any other word outside the question frames and the
  condition phrase, and two presence checks in one message get a
  clarification; a list of only some of the conditions is refused.
  More ways of naming the selected scope are read ("the selected part
  of the record", "visit 2 of 4", "the current, final admission"), and
  "this admission" names the selected visit when a visit is selected.
  Negative wordings ("is there no record of X") check X. On a new
  written set, 147 of 226 questions are handled as expected and 2 get a
  wrong answer, against 154 and 9 with version 2 on the same questions.
  Rules version 3, the fail-safes alone, was measured on the set before
  and not released.

## [0.7.0] - 2026-09-26

Wording tests and rules version 2 (Phase 5, work items 34.2 to
34.4): clean partitions of MIMIC-IV-Ext-Instr and the held-out
wording test, the written wording test, and rules version 2 with
spelling suggestions, measured on a written set worded after the
rules were fixed.

### Added

- `qa.heldout`: a partition record of MIMIC-IV-Ext-Instr fixed before
  any measurement (held-out test admissions; training and validation
  without the test set, admissions in no split or patients of our case
  sets), and the held-out wording test on its test admissions
  ([ADR 0008](docs/decisions/0008-question-set-partitions.md)). With the
  rules of 0.6.0, 31,235 of 32,392 held-out questions are handled as
  expected and no question about other data is answered; 987 ranking
  questions are answered with an unranked list.
- `qa.written`: the written wording test, with a questions template, an
  overview of each case's recorded conditions, and scoring against the
  development suite's oracle by intent (list, presence, refuse, clarify)
  and by an optional family, in named question sets. With the rules of
  0.6.0, 139 of 198 written questions are handled as expected, every
  presence answer is about the condition meant, and 9 questions get a
  wrong answer.
- Spelling suggestions: an unknown condition wording is offered the
  closest spellings in the vocabulary; a suggestion never resolves it.
- Development suite version 2 with ranking, two-request and misspelling
  families: 1,628 of 1,628 on `dev-30-v1`, 678 of 678 on the pilot.
- CI runs the synthetic modes of `qa.heldout` and `qa.written`.

### Changed

- Rules version 2 ([ADR 0009](docs/decisions/0009-rules-version-2.md)):
  rankings of conditions are refused; more scope wordings are read; two
  requests are found across sentences and before the out-of-scope rules;
  the other-patient, prognosis and procedure rules are wider; "hx of",
  problem lists and inclusion wordings are read. On a new written set,
  147 of 198 questions are handled as expected and 3 get a wrong answer.

## [0.6.0] - 2026-09-26

Rule-based natural-language QA over the evidence bundles (Phase 5,
work items 31 to 34.1): question interpretation, a condition
vocabulary from the pinned Athena release, term resolution and
record matching, scope binding and scoped answers, measured by a
development suite against an independent oracle. The held-out
wording test (work item 34.2) is open.

### Added

- `qa.vocabulary`: the condition vocabulary, built from the pinned Athena
  release that was loaded into the OMOP database
  ([ADR 0006](docs/decisions/0006-condition-vocabulary.md)).
- `qa.resolve`: condition term resolution by name, synonym or ICD code,
  with nested-candidate merging, a breadth guard and suggestions for
  unknown terms; record matching as exact, more specific, by ICD code or by
  ICD description.
- Evidence schema 1.1: each record may carry its source concept
  (vocabulary, code and description). Version 2 bundle sets carry it;
  version 1 bundles remain valid.
- `qa.interpret`: a deterministic interpreter that turns a question about
  the active case into a condition list or a condition presence check, or
  ends it with a clarification or abstention. Out-of-scope rules
  (explanations, other patients, treatment, prognosis, diagnosis requests,
  other data, dates, times, order and counts) apply outside the condition
  phrase, and inside it only when the phrase names no condition; the
  phrase is resolved by its longest candidate that names something
  ([ADR 0007](docs/decisions/0007-rule-based-qa.md)).
- `qa.scope`: scope phrases in questions; the active scope applies, and a
  question naming another scope gets a clarification.
- `qa.answer`: list and presence answers from a verified QA bundle, with
  claims citing the bundle's records and "not recorded within the scope"
  only under complete coverage.
- `qa.suite`: a development question suite scored against an independent
  oracle computed from the source snapshot and the vocabulary, for
  development case sets only, and a sweep of every standard condition
  name. `dev-30-v1`: 1,550 of 1,550 questions correct; pilot: 648 of 648;
  sweep: 788,221 of 788,221 questions resolve to the concept asked about.
- Clarification reason codes `concept_unknown`, `concept_too_broad`,
  `case_ambiguous` and `prediction_selection_needed`.
- [Natural-language QA](docs/qa.md) documentation.
- CI runs the synthetic modes of `qa.vocabulary` and `qa.suite`.

### Changed

- The storage policy admits the Athena vocabulary directory as a
  read-only vocabulary source.
- A response names no task when the question could not be interpreted as
  one; answers and explanations still always name theirs.

## [0.5.0] - 2026-09-26

Versioned development and evaluation case sets (work item 30.1), which
complete Phase 4: a 30-case development set and a 100-case evaluation set,
drawn from the upstream validation and test partitions and prepared,
linked and bundled by the same pipeline as the pilot.

### Added

- `casesets.registry`: the case-set registry (`config/case_sets.json`) with
  the development set `dev-30-v1` (upstream validation partition) and the
  evaluation set `eval-100-v1` (upstream test partition), fixed before any
  draw ([case sets](docs/case-sets.md)).
- `casesets.selection`: seeded case-set selection from the upstream
  partitions, verified against the run-binding record.
- `sources.pilot --case-set`: extraction of exactly the subjects of a
  pinned case-set selection.
- Drawn and pinned selections for `dev-30-v1` and `eval-100-v1`.
- `casesets.prepare`: one run per case set for the identity mapping, the
  bridge outputs with the canonical-prefix check, single-graph and batch
  facts, and typed admission, published in the closure case schema.
- `gnn.linkage --case-set` and `evidence.bundles --case-set`: linkage and
  bundles for a registered case set from its pinned records.
- Prepared, linked and bundled case sets: `dev-30-v1` (26 of 30 cases
  admitted) and `eval-100-v1` (92 of 100 admitted); every graph edge of
  both links to recorded events.

### Changed

- The evidence readers accept case-set closure records, bind every case to
  its mapping record by digest, and keep no visits for quarantined cases.
- `sources.pilot` checks the database connection before it creates a
  version directory, so a wrong password leaves nothing behind and the
  label can be reused.

## [0.4.0] - 2026-09-26

Record evidence for the development pilot (Phase 4, work items 23 to 30),
with continuous integration and a release workflow.

### Added

- `evidence`: Phase 4 record evidence for the development pilot.
  Request, response and execution-limit contracts with committed JSON
  Schemas (`docs/schemas`, `config/execution_limits.json`); evidence
  records and bundle types with bundle-local references; pinned readers for
  the source snapshot and the closure record's cases; readable names with a
  stated source; scoped retrieval with explicit coverage; QA and explanation
  bundle sets (`evidence-bundles\pilot10-v1`)
  ([ADR 0004](docs/decisions/0004-evidence-contracts.md)).
- `gnn.linkage`: driver-to-event linkage record
  (`event-linkage-pilot10-v1`); every graph edge and driver of the pilot
  verifies.
- `evidence.policy`: the evidence claim policy (`evidence-policy-v1`,
  `config/evidence_policy.json`), which allows statements citing linked
  events ([ADR 0005](docs/decisions/0005-event-linkage-and-claim-policy.md)).
- Continuous integration on a Windows runner: compile, release-metadata
  check, test suite, synthetic module modes, publication audit and a
  clean-tree check (`.github/workflows/ci.yml`).
- Release workflow: reruns the checks at a version tag and publishes a
  GitHub release from the changelog entry (`.github/workflows/release.yml`).
- `quality.release`: checks that the tag, the package version and the
  newest changelog entry agree, and extracts the release notes.
- Dependabot updates for the pinned workflow actions.

### Changed

- Environment tests skip, instead of creating directories, when the
  project storage layout is absent.
- The publication audit runs from any checkout that overlaps neither the
  private workspace nor the runtime root, so CI can run it.

## [0.3.0] - 2026-09-26

First tagged release: the data-handling foundation, the integration of the
inherited GNN for the development pilot, and the Phase 3 closure.

### Added

- `storage`: fixed storage roots under one policy anchor; lexical and
  on-disk destination checks; process-local destination set-up
  (`scripts/setup-destinations.ps1`) and its verifier; runtime-volume
  preflight with identity pinning and ACL checks.
- `sources`: verified staging of the legacy OMOP CSV exports; bounded,
  read-only, single-snapshot extraction of the development pilot with native
  event identifiers and coverage checks.
- `gnn`: inherited baseline identity record; asset validation; identity
  mapping between graphs and source persons and visits; preprocessing parity
  against an independent oracle; offline model outputs with canonical
  agreement; provenance and split-membership sidecars.
- `gnn.binding`: run-binding verification of the inherited model. Replays
  the pinned split, reproduces the checkpoint's saved `pos_weight` and its
  stored validation and test metrics, runs negative controls and measures
  batch sensitivity.
- `gnn.admission`: the Phase 3 closure record. Typed per-case admission,
  label-evidence layers, per-driver visit tags, the claim policy and the map
  of superseded flags (`config/phase3_closure.json`).
- `tracking`: tracker policy verifier; ClearML connection checks; controlled
  metadata-only tracking; inherited input registration; lineage schema and
  validator.
- `environment`: application environment smoke check; GNN environment
  capture with before-and-after fingerprints.
- `quality`: publication audit of the working tree, index and full history.
- Documentation by subsystem and architecture decision records.

### Changed

Relative to the untagged pre-release development tree:

- All storage moved to one permission-restricted NTFS volume; encryption at
  rest is recorded but no longer required
  ([ADR 0001](docs/decisions/0001-single-volume-storage.md)).
- Tools reorganised into the `ehr_evidence_app` package, run with
  `python -m` ([ADR 0003](docs/decisions/0003-package-layout.md)).
- Missing source-event linkage now restricts specific claims instead of
  blocking whole cases ([ADR 0002](docs/decisions/0002-phase-3-closure.md)).

### Fixed

- The asset-validation record and the inherited-input registration are no
  longer overwritten by a re-run; an identical record is left unchanged and
  a different one is refused.
- The identity mapping pins the configuration its source pilot was actually
  extracted under.
- `sources.exports` requires an explicit version label, so a default can no
  longer re-verify (and rewrite the manifest of) the published version.

[Unreleased]: https://github.com/maa2002/ehr-evidence-app/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/maa2002/ehr-evidence-app/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/maa2002/ehr-evidence-app/tree/v0.3.0
