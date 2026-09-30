# 0012. Generation design (Phase 7)

Status: accepted (2026-09-27); amended by
[ADR 0013](0013-compact-generation.md): the model reads a compact
projection of the bundle, and an explanation describes the selected
drivers; and by [ADR 0014](0014-structured-evidence.md): the projection is
structured for its task, and the schema enforces the contract's syntax
only, with evidence truth left to the prompt and the gate. Decision 6
closed by [ADR 0024](0024-no-fine-tuning.md): no fine-tuning.

## Context

Phase 7 of the project brief adds a language model (work items 41 to 46):
a generation adapter, an initial runtime, execution limits, controlled QA
and explanations, and protected runtime records. Phase 6 already requires
every response with text to be a candidate that leaves only through the
release gate ([ADR 0011](0011-validation-design.md)). The model is
MedGemma 1.5 4B IT, gated behind the Health AI Developer Foundations
terms and run locally on a laptop GPU with 8,188 MiB. Everything the phase
installs, downloads or writes stays on G:.

## Decision

1. **The model writes the complete candidate.** For every response from
   evidence (list and presence answers, "not recorded" included, and
   explanations) the model receives the complete task-specific evidence
   bundle and writes the complete structured candidate: the outcome, the
   statements with their wordings, facts and references, the notices and
   the reason codes. It writes one JSON document in the shape of its
   answer, constrained while it writes by the request's JSON Schema:
   references only to the bundle's records, visits and drivers, notices
   by name, a cited prediction or driver stated with its own values, and
   wordings without digits or another kind's placeholders. The request
   fills in its identifiers, case, scope and bundle and the condition a
   presence question asks about. The release gate validates every claim;
   the templates are the fallback, not the author of successful
   responses. Clarifications, refusals and abstentions are settled before
   any evidence is read and stay approved notices. A presence prompt
   states which of the bundle's concepts count as the condition asked
   about, and how: vocabulary facts (more specific concepts, ICD
   descriptions) the bundle does not carry.
2. **Full precision.** Hugging Face Transformers with bfloat16 weights,
   greedy decoding and a fixed seed; nothing is quantised. The weights
   (8,202 MiB) exceed the GPU's 8,188 MiB, so the image encoder (801 MiB,
   which a text request never runs) and the token embedding table
   (1,280 MiB, tied to the output layer and run on the processor) stay in
   system memory, and 6,121 MiB of layers go on the GPU. Memory and speed
   are measured before anything depends on them; a change of precision is
   decided with the project owner.
3. **A separate worker.** The model runs in its own environment on G:,
   loaded once and answering one request at a time over its standard
   input and output, without a network port. The application reaches it
   only through the adapter and runs without it.
4. **Fallback, counted as a failure.** One attempt per request. A prompt
   over its budget, output over its budget or cut off, a timeout,
   exhausted memory, a backend failure, a document that is malformed or
   breaks its schema, or a candidate the gate withholds releases the
   template candidate, which passes the same gate, and records why.
   Phase 9 counts every fallback as a failure of the model, never as its
   success; records and measurements report the responses the model wrote
   apart from the fallbacks.
5. **Protected records.** Each request writes one protected record in the
   workspace on G:; routine logs hold statuses and counts only, and the
   tracker receives aggregates.
6. **No fine-tuning in Phase 7.** The training and validation partitions
   of MIMIC-IV-Ext-Instr stay untouched. Fine-tuning returns as a Phase 9
   decision if the development measurement calls for it.

The work is delivered in four milestones: the adapter, shape, prompt,
limits and fallback with a scripted backend and no model; the model
environment and worker on G:, with memory and speed measured; the model
writing QA answers and explanations, measured on the development case
sets; the runtime records, the log audit and release 0.9.0.

## Consequences

- A model can make mistakes a template cannot: answering another
  condition, refusing an answerable request, reordering statements,
  stating a notice twice, hedging or ranking facts. The gate refuses them
  (new structure checks and wording rules version 3), and mutation suite
  version 2 shows it: on the development case sets it detects all 23,999
  mutants in 58 classes, while all 620 original responses and every
  control are released unchanged.
- The bundle is never trimmed, so a request whose prompt exceeds its
  budget falls back. A complete candidate grows with its answer, so the
  output limit rises after measurement, within the fixed ceiling of
  4,096 tokens, and longer answers fall back.
- Without a writer every answer from evidence is the template's, a
  recorded `no_model` fallback: the application and every check run
  without the model, and CI needs none.
- In scripted runs on the development case sets every template answer
  is released unchanged when a writer returns it, and every way of
  failing ends in the template's response.
