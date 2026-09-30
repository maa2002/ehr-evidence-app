# Roadmap

Work is organised in phases of numbered work items. The numbers are stable
identifiers: they appear in module docstrings, record schemas and artefact
names (for example `step-17-pilot10-v1`) and are never reused or renumbered.
A work item may be split into sub-items such as 0.3.1 and 0.3.2.

## Completed

| Phase | Work items | Outcome |
| --- | --- | --- |
| 0. Workspace and source preparation | 0.1 to 0.4 | Storage roots and destinations; staged legacy exports; bounded read-only source pilot; path and source checks |
| 1. Repository and environments | 1 to 8 | Inherited baseline record; repository structure and content exclusions; GNN environment capture; application environment |
| 2. Experiment tracking | 9 to 14 | Tracker policy; ClearML connection checks; controlled metadata tracking; inherited input registration; lineage schema |
| 3. GNN integration | 15 to 22 | Asset validation; bridge output contract; identity mapping; target and history policy; preprocessing parity; offline outputs; provenance; split membership |
| 4. Record evidence | 23 to 30.1 | Request, response and evidence contracts; snapshot and case readers; readable names; scoped retrieval; driver-to-event linkage and evidence claim policy; QA and explanation bundles for the pilot, a development set and an evaluation set |

Phase 3 is closed by a run-binding verification and a typed case-admission
record; see [decisions/0002-phase-3-closure.md](decisions/0002-phase-3-closure.md).
Evidence from Phase 3 enters Phase 4 only through that record. Phase 4 is
described in [evidence.md](evidence.md) and decided in
[ADR 0004](decisions/0004-evidence-contracts.md) and
[ADR 0005](decisions/0005-event-linkage-and-claim-policy.md).

## Phase 4. Record evidence

- 23 Define configuration and response contracts (done)
- 24 Define shared evidence contracts (done)
- 25 Implement condition and visit readers (done)
- 26 Implement readable mappings (done)
- 27 Implement scoped retrieval (done)
- 28 Link attributions to recorded events (done; attribution stays
  concept-level and is never assigned to an event, a date or a visit)
- 29 Build versioned QA bundles (done for the development pilot)
- 30 Build explanation bundles (done for the development pilot)
- 30.1 Generalise case preparation to separately versioned development and
  evaluation case sets (done: `dev-30-v1` and `eval-100-v1`; see
  [case-sets.md](case-sets.md))

## Phase 5. Natural-language QA

Described in [qa.md](qa.md) and decided in
[ADR 0006](decisions/0006-condition-vocabulary.md),
[ADR 0007](decisions/0007-rule-based-qa.md),
[ADR 0008](decisions/0008-question-set-partitions.md),
[ADR 0009](decisions/0009-rules-version-2.md) and
[ADR 0010](decisions/0010-fail-safes.md).

- 31 Interpret tasks (done; rule-based)
- 32 Resolve concepts (done)
- 32.1 Build the condition vocabulary from the pinned Athena release (done)
- 33 Resolve scope (done)
- 34 Enforce QA sufficiency (done)
- 34.1 Measure the pipeline with a development suite and an independent
  oracle (done: 1,550 of 1,550 questions on `dev-30-v1`, 648 of 648 on
  the pilot)
- 34.2 Measure interpretation on held-out wording from MIMIC-IV-Ext-Instr
  (done: partitions fixed; 31,235 of 32,392 held-out questions as
  expected, no question about other data answered, 987 ranking questions
  answered with an unranked list)
- 34.3 Measure presence-check wording on questions worded by a writer who
  has not read the rules (done: 139 of 198 questions as expected; every
  presence answer about the condition meant; 9 wrong answers from scope
  wordings, two requests at once and two refusals)
- 34.4 Close the gaps with rules version 2 and spelling suggestions (done:
  measured on a new written set, 147 of 198 as expected and 3 wrong
  answers; [ADR 0009](decisions/0009-rules-version-2.md))
- 34.5 Add fail-safes with rules version 4 (done: measured on a new
  written set, 147 of 226 as expected and 2 wrong answers, against 9
  with version 2 on the same questions;
  [ADR 0010](decisions/0010-fail-safes.md))

## Phase 6. Validation

Described in [validation.md](validation.md) and decided in
[ADR 0011](decisions/0011-validation-design.md). Complete.

- 35 Check evidence before generation (done: `validation.evidence`)
- 36 Check structure and references (done: the candidate contract and
  `validation.structure`; QA responses are rendered from checked
  candidates)
- 37 Check QA support (done: `validation.support`)
- 38 Check prediction and attribution preservation (done: the template
  explanation and its checks in `validation.explanation`)
- 39 Check history and language (done: `validation.language`, and the
  history statements of explanations)
- 40 Compose the release gate (done: `validation.gate`)
- 40.1 Test the checks with a mutation suite (done: every mutant of 40
  error classes detected on the development case sets, every original
  released unchanged)

## Phase 7. LLM and operations

Described in [generation.md](generation.md) and decided in
[ADR 0012](decisions/0012-generation-design.md),
[ADR 0013](decisions/0013-compact-generation.md),
[ADR 0014](decisions/0014-structured-evidence.md) and
[ADR 0015](decisions/0015-generation-configuration.md). Complete in
release 0.9.0: the language model writes the complete candidate of every
response from evidence, from a deterministic view of the evidence
structured for its task, and the templates are its fallback. In
milestone 3, on
the whole development set BF16 with the mixed placement released 314 of
430 requests and NF4 172, so the precision stays BF16; K = 5 and the
limits (16,384 prompt tokens, 768 output tokens, 180 seconds) are frozen
(ADR 0015); and after one bounded pass on the prompt (ADR 0017) the
model's candidates are released for 385 of the 430 development requests
and 161 of the 182 pilot requests. Milestone 4 added the protected
runtime records, the tracker aggregates and the log audit, and released
0.9.0; [ADR 0016](decisions/0016-evaluation-metrics.md) fixes the
metrics of Phase 9.

- 41 Define the generation adapter (done: `generation.shape`,
  `generation.projection`, `generation.prompt` and `generation.adapter`;
  the compact document, the structured view of the evidence and
  syntax-only decoding; the release gate refuses the mistakes a model can
  make, shown by mutation suite version 4)
- 42 Implement the initial LLM runtime (done: `generation.runtime` and
  `generation.worker`; MedGemma 1.5 4B IT, verified on G:, runs in its own
  environment in BF16 with a mixed GPU/CPU placement, or in 4-bit NF4 on
  the GPU; both benchmarked on development requests)
- 43 Enforce execution limits (done: prompt, output, time, attempt and
  concurrency limits and the fallback, each reason with its category)
- 44 Complete controlled QA (done: the model writes list and presence
  answers through the adapter; measured on the development case sets)
- 45 Complete controlled explanations (done: the model writes
  explanations of the 5 selected drivers through the adapter)
- 46 Add protected runtime records (done: one protected record per
  response, the tracker aggregates and the log audit)
- 46.1 Measure the model on the development case sets (done: token
  distributions, the benchmark with its scores, the paired precision
  comparison, the configuration frozen for the final evaluation and the
  bounded prompt pass)
- 46.2 Keep the experiments in the tracker (done:
  [ADR 0018](decisions/0018-tracker-as-experiment-record.md); lineage tasks
  stay visible and completed, with configuration fields and scalars from
  allowlists; each benchmark run is a measurement task, the Phase 7 runs
  backfilled from the execution log; the pinned model uploaded with its
  licence notice, agreement and manifest, and registered)

## Phase 8. API and interface

Described in [interface.md](interface.md) and decided in
[ADR 0019](decisions/0019-api-and-interface.md) and
[ADR 0020](decisions/0020-interface-as-one-page.md). In progress: the
API, its boundary, the interface, the launcher and sharing through the
tunnel are in place; the link through the tunnel and release 0.10.0
follow.

- 47 Add FastAPI operations (done: `api.service` and `api.app`)
- 48 Configure the access boundary (done: `api.boundary`,
  `interface.gate`, and sharing through the Cloudflare tunnel with a
  secret link, `interface.share` and `api.launch --share`)
- 49 Add the case and scope selector (done: the sidebar's cohorts and
  patients and the composer's scope, `interface.app` and
  `interface.names`)
- 50 Add the QA panel (done: the conversation)
- 51 Add the explanation panel (done: the explanation in the
  conversation)
- 52 Add the evidence viewer (done: the evidence panel)
- 52.1 to 52.7 Make the whole record answerable, at the owner's request
  ([ADR 0021](decisions/0021-the-whole-record.md); done): the case sets'
  raw MIMIC-IV records (`sources.mimic`), the medication and procedure
  vocabulary (`qa.record_vocabulary`), record bundles
  (`evidence.record_bundles`), reading of questions about the record and
  their terms (`qa.record_interpret`, `qa.record_resolve`), template
  answers (`qa.record_answer`), their rendering and support checks
  (`validation.record_text`, `validation.record_support`), and the whole
  record in the evidence panel
- 52.8 Test the whole record at its edges
  ([ADR 0022](decisions/0022-record-edge-cases.md); done): an invented
  record built for its edges, every question in every scope, the answers'
  checks broken one fact at a time, and the same on the development case
  sets; medications without a standard concept matched by drug class
  (record vocabulary version 2), and the faults the review found fixed
- 52.9 Test answers against each other, compound mistakes and junk input
  ([ADR 0023](decisions/0023-consistency-and-junk-input.md); done): the
  same question reworded, nested scopes compared, answers wrong in two
  facts or wrong in a way that agrees with itself, junk questions and
  malformed requests, on invented data and the development case sets;
  polite openings, statement forms, stays in time order, unpaired
  surrogates and the framework's refusals fixed

## Phase 9. Evaluation

The model is evaluated as pinned, without fine-tuning
([ADR 0024](decisions/0024-no-fine-tuning.md)).
The metrics, their denominators and empty-set conventions, the prose
rubric and the paired comparison are fixed in
[ADR 0016](decisions/0016-evaluation-metrics.md), agreed by the project
owner before any request of `eval-100-v1` is written.

- 53 Partition downstream cases
- 54 Prepare independent QA references
- 55 Prepare explanation expectations and challenges
- 56 Implement the baseline condition
- 57 Implement the paired runner
- 58 Calculate QA and abstention metrics
- 59 Calculate explanation and evidence metrics
- 60 Assess end-to-end QA
- 61 Produce comparative results

The upstream partition of every case is recoverable (see
[inherited-gnn.md](inherited-gnn.md#run-binding-and-split-membership)), so
evaluation cases can be drawn from the upstream test partition when a
held-out claim is needed.

## Phase 10. Acceptance and handover

- 62 Complete the acceptance matrix
- 63 Verify reconstruction and restart
- 64 Freeze and execute the final evaluation
- 65 Demonstrate and release
