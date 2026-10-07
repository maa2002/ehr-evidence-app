# 0018. The Tracker as the Experiment Record (Phase 7)

Status: accepted (2026-09-28). Amends the tracker policy
(`config/clearml_policy.json`, `model_artefacts`) and the connection
declaration (`config/clearml_connection.json`); the data rules are
unchanged.

## Context

The tracker held two lineage tasks, the inherited input registration and
the runtime aggregates. Each was archived once read back, both carried the
generic name of their kind, and each carried its lineage as tags only: no
configuration field and no scalar. None of the Phase 7 measurements, which
chose the precision, K, the limits and the prompt, was in the tracker, and
neither was the model. The project owner reviewed the project in the web
application, found nothing new in view, and asked that the tracker be used
as the project's experiment record for its members (two or three people),
with the model uploaded to it, the model still run on the development
laptop, and no data uploaded.

## Decision

1. **Lineage stays visible.** A task is of one fixed kind with a fixed
   type: the inherited input registration, the runtime aggregates, or a
   generation measurement. A kind may be named after some of its
   configuration fields in their permitted forms, such as
   `Generation measurement: dev-30-v1, bf16-mixed, prompt v9, 87027bd`.
   A lineage task is read back and then marked completed, so that it
   reads as a finished record and cannot be edited in the web application
   without a reset. Only the probe of the controlled wrapper is archived.
2. **Configuration fields and scalars from allowlists.** A task may carry
   configuration fields, each from a fixed list and in one permitted form
   (a case set's name, a commit, a date, the precision, the decoder, the
   model and its revision, the versions, the number of drivers, the
   sample and the limits), and scalars, each a fixed metric (requests,
   released, released within limits, outcome right, release rate,
   precision, recall, F1, seconds, output and prompt tokens, tokens a
   second, peak GPU memory) under a fixed series (a stratum of the
   benchmark, a union of strata, or all requests, alone or with a fixed
   part such as `conditions` or `median`), with a bounded value.
   `tracking.controlled` checks both before any request; nothing is free
   text.
3. **Measurements as experiments.** `generation.measure --record` keeps
   each benchmark run as a record in the workspace: its configuration and,
   by stratum, its counts, release rates, scores and measures, and nothing
   of a request. The tracked files must be as committed before and after
   the run, so that a record names the commit that ran.
   `tracking.measurements` sends each record once as a measurement task.
   The runs since the structured evidence of ADR 0014 are backfilled from
   the aggregates of the execution log (`config/measurement_history.json`,
   17 runs), each at its commit and marked backfilled; the development
   result of the configuration, assembled from runs whose prompts are
   those of `87027bd` byte for byte, is marked assembled. A value the log
   does not give for one stratum is left out, never inferred.
4. **The model in the tracker.** MedGemma 1.5 4B IT, the one listed
   model, is uploaded to the tracker's files server at its pinned
   revision, every file checked first against its size and SHA-256 in
   `config/generation.json`, with the notice its licence (the Health AI
   Developer Foundations terms) requires, a copy of the licence agreement
   that the project owner saves in the staging directory, and a manifest
   naming each file's kind, size, SHA-256 and provenance; it is
   registered once per revision in the project's model registry. It is
   published pretrained by its developer and was never trained on the
   project's data; no file of it is modified. The laptop keeps running
   the model from the runtime root; the tracker's copy is the project's
   shared reference. The GNN checkpoints and anything else derived from
   patient data stay excluded.
5. **No data.** Records, prompts, responses, explanations, identifiers
   and data files never reach the tracker, as before; a task carries
   references to workspace files and their checksums, configuration
   fields and aggregates only.

## Consequences

- The policy's `model_artefacts` lists the one model, and the connection
  declaration enables its files through `tracking.models`; the policy
  checker and the declaration checker hold both to exactly these values.
- The tracker holds a visible, completed task for each measurement, the
  registration and each set of runtime aggregates, with configuration
  columns and scalars the web application can compare and sort, and the
  model, about 8.6 GB of the 100 GB of storage the hosted service's free
  tier gives.
- Whoever can open the workspace can download the model; the licence's
  terms bind them, and its agreement and notice are beside the files. The
  workspace stays limited to the project's members.
- A measurement task is a record: a correction is a new record and a new
  task, never an edit. The tasks archived before this decision stay in the
  archive, where the web application can restore them.
