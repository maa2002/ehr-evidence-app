# Generation

Phase 7 makes a language model the author of every response from
evidence. MedGemma 1.5 4B IT, run locally, receives a request's complete
evidence bundle and writes the complete candidate: the outcome, the
statements with their wordings, facts and references, the notices and the
reason codes. The candidate is released only through the release gate of
Phase 6 ([validation.md](validation.md)), and when it is not released the
template candidate is, as a recorded fallback.

The design is recorded in
[ADR 0012](decisions/0012-generation-design.md). Command examples assume
the session set-up in [development.md](development.md#session-set-up).

## Work items

| Work item | Module | Role | State |
| --- | --- | --- | --- |
| 41 | `generation.shape`, `generation.prompt`, `generation.adapter` | The document a candidate is written as and its schema; the prompt; the adapter between the application and a writer | done |
| 42 | `generation.runtime` | The model worker and its environment on G: | milestone 2 |
| 43 | `generation.adapter` | Prompt, output, time, attempt and concurrency limits; the fallback | done |
| 44 | `qa.answer` | Answers from evidence written through the adapter | path in place; the model in milestone 3 |
| 45 | `validation.explanation` | Explanations written through the adapter | path in place; the model in milestone 3 |
| 46 | `generation.records` | Protected runtime records, tracker aggregates, the log audit | milestone 4 |
| 46.1 | `generation.measure` | Development measurement of the model | milestone 3 |

## What the model writes

The model writes every response from evidence: list answers, presence
answers and their "not recorded", and explanations. Clarifications,
refusals and abstentions are settled before any evidence is read, from the
question or the checks before generation, and stay the templates'
approved notices.

It writes one JSON document in the shape of its answer
(`candidate-shape-v1`):

| Answer | Document |
| --- | --- |
| List | `outcome`, `reason_codes`; `summary` (wording, `groups`, `records`); `items`, one per condition (wording, `condition` as the reference of a record, `count`, `visits`, `refs`); `unmapped` (wording and count, or null); `notices` |
| Empty list | `outcome`, `reason_codes`, `empty` (wording), `notices` |
| Presence answer | `outcome`, `reason_codes`; `summary` (wording, `matches`); `items` (as a list's, with `match`) |
| Not recorded | `outcome`, `reason_codes`, `absent` (wording), `unmapped`, `notices` |
| Explanation | `outcome`, `reason_codes`; `prediction` (wording, `label`, `score`, `logit`); `drivers`, each with wording, `driver`, `rank`, `score` and its `history` (wording, `count`, `visits`, `refs`) and `context` (wording, `count`, `refs`), or null; `notices` |

A wording holds the placeholders of its statement, such as `{condition}`,
as in [validation.md](validation.md#candidates). The request fills in what
it fixes (its identifiers, case, scope and bundle, and the condition a
presence question asks about), and each statement takes its answer's
place: a list's items after its summary, an explanation's history and
context under their own driver, the notice introducing the drivers after
the prediction and every other notice at the end. Visit numbers are read
as a set.

## The schema

Each request's document has its own JSON Schema, which the model runtime
enforces while the model writes:

- a reference names one of the bundle's records, visits or drivers, and
  history and context cite the bundle's history and final-admission
  records;
- a notice is one an answer of the task may hold, named by its
  identifier; its text is fixed;
- a statement that cites the prediction or a driver states that
  reference's own label, score and logit;
- a count is a whole number no larger than the bundle's records;
- a wording is one line of words with no digit and no placeholder of
  another kind;
- a scope with no records admits only "not recorded".

The schema admits every correct document and many wrong ones. Whether a
document is right is decided by the release gate on the candidate it
becomes. The adapter checks every document against its schema again,
since none is trusted to have been constrained.

## The prompt

The prompt (`prompt-v1`) is two chat messages: the rules every reply
follows, then one worked example on invented data, the task with the
shape of its reply, and the request's complete evidence bundle as JSON.
The bundle is never trimmed; values it leaves out are absent, not lost,
and a test reads the bundle back from the prompt. A presence prompt also
states which of the bundle's concepts count as the condition asked about,
and how (exact, more specific, by ICD code or by ICD description): the
bundle does not carry the vocabulary's hierarchy or descriptions. The
model still finds, groups, counts and cites the records.

Prompts and model output hold patient records; they are never logged.

## The adapter

`generation.adapter.write` is the one path from a writer's output to a
release:

1. The prompt and the schema are built. A prompt over its token budget,
   or a request whose template document already exceeds the output
   budget, ends the attempt before the model runs.
2. The backend writes once, within the time limit and one request at a
   time. Only a failure of the backend itself is tried again, within the
   attempt limit.
3. Output cut off at the token limit is never read. The rest must parse
   as JSON, meet its schema and stand for a candidate, which leaves only
   through the release gate.
4. Anything else releases the template candidate through the same gate
   and records why:

| Fallback | When |
| --- | --- |
| `no_model` | No writer is configured |
| `busy` | Another request is being written |
| `prompt_over_budget` | The prompt exceeds `max_prompt_tokens` |
| `output_over_budget` | The answer's template document already exceeds `max_output_tokens` |
| `timeout` | The backend or its output exceeded `max_request_seconds` |
| `resource_exhausted` | The backend ran out of memory |
| `backend_failed` | The backend failed or crashed |
| `truncated` | Output stopped at the token limit |
| `malformed` | Output that is not JSON, or does not stand for a candidate |
| `schema_violated` | A document that breaks its schema |
| `gate_rejected` | A candidate the gate withholds; the gate's reason codes are kept |

Phase 9 counts every fallback as a failure of the model, never as its
success. Without a writer every answer from evidence is the template's, a
`no_model` fallback, so the application and every check run without the
model, and responses are unchanged.

## Limits

The limits are fixed ceilings in code; `config/execution_limits.json`
may lower them (see [evidence.md](evidence.md#contracts)):

| Limit | Configured | Ceiling | On the generation path |
| --- | --- | --- | --- |
| `max_prompt_tokens` | 16,384 | 131,072 | A longer prompt falls back; set from the memory measurement of milestone 2 |
| `max_output_tokens` | 768 | 4,096 | Output cut off here is never read; raised after measurement, since a complete candidate grows with its answer |
| `max_request_seconds` | 120 | 600 | Later output is not read |
| `max_generation_attempts` | 1 | 3 | Only a failed backend is tried again |
| `max_concurrent_requests` | 1 | 8 | A further request falls back as busy |

## Scripted runs

Without a model, the adapter is run with a scripted backend that returns
each request's template document, and every way a writer can fail. On the
invented fixtures and the development case sets every template answer is
released unchanged when a writer returns it, and every failure ends in its
fallback with exactly the template's response:

| Samples | Requests (lists, presence checks, explanations) | Released as written | Fallbacks as expected |
| --- | --- | --- | --- |
| Invented fixtures | 15 (5, 9, 1) | 15 of 15 | 180 of 180 |
| `dev-30-v1` | 430 (135, 269, 26) | 430 of 430 | 5,160 of 5,160 |
| `pilot-10-v1` | 182 (58, 114, 10) | 182 of 182 | 2,184 of 2,184 |

## The model

MedGemma 1.5 4B IT (`google/medgemma-1.5-4b-it`) at revision
`91850547d9f0b2fdd21aa7c5f4f3d1a8a52c243b` is stored in
`G:\Industry Dataset\ehr-evidence-runtime\models\huggingface\google--medgemma-1.5-4b-it`.
Its 15 files are verified by digest against the revision, and the worker
checks them again when it loads. Its 4,300,079,472 parameters are in
bfloat16 and run in full precision: the image encoder (801 MiB) and the
token embedding table (1,280 MiB, tied to the output layer) stay in
system memory and 6,121 MiB of layers go on the GPU (ADR 0012,
decision 2). The worker, its environment and the measurements come in
milestones 2 and 3.

## Commands

The default modes run the invented fixtures:

```powershell
& $AppPython -m ehr_evidence_app.generation.shape
& $AppPython -m ehr_evidence_app.generation.prompt
& $AppPython -m ehr_evidence_app.generation.adapter
```

The operator mode runs the scripted backend on a development case set and
prints aggregate counts only; it refuses the evaluation case set:

```powershell
& $AppPython -m ehr_evidence_app.generation.adapter --operator-real --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.generation.adapter --operator-real --case-set pilot-10-v1
```
