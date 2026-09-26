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

- 31 Interpret tasks
- 32 Resolve concepts
- 33 Resolve scope
- 34 Enforce QA sufficiency

## Phase 6. Validation

- 35 Check evidence before generation
- 36 Check structure and references
- 37 Check QA support
- 38 Check prediction and attribution preservation
- 39 Check history and language
- 40 Compose the release gate

## Phase 7. LLM and operations

- 41 Define the generation adapter
- 42 Implement the initial LLM runtime
- 43 Enforce execution limits
- 44 Complete controlled QA
- 45 Complete controlled explanations
- 46 Add protected runtime records

## Phase 8. API and interface

- 47 Add FastAPI operations
- 48 Configure the access boundary
- 49 Add the case and scope selector
- 50 Add the QA panel
- 51 Add the explanation panel
- 52 Add the evidence viewer

## Phase 9. Evaluation

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
