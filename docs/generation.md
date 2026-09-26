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
| 42 | `generation.runtime`, `generation.worker` | The pinned model, the worker in its own environment on G:, and the backend that reaches it | done; measured |
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
- a wording is one line of letters, spaces and plain punctuation, holding
  each placeholder of its kind exactly once, in any order, and no other;
- a scope with no records admits only "not recorded".

The wordings, and an explanation's history and context, are defined once
under `$defs` and referenced, so the schema stays small however many
drivers a prediction has. The first runs of the model showed why the
wording is this narrow: allowed any characters, a wording that had lost a
placeholder ran on into markup until the output limit. Bounding the length
of a wording's runs in the schema made each request's grammar take 10 to
35 seconds to compile, so the worker bounds them instead: output that runs
150 tokens without a double quote is stopped and cut off, and so falls
back.

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

## The model and its worker

MedGemma 1.5 4B IT (`google/medgemma-1.5-4b-it`) at revision
`91850547d9f0b2fdd21aa7c5f4f3d1a8a52c243b` is stored in
`G:\Industry Dataset\ehr-evidence-runtime\models\huggingface\google--medgemma-1.5-4b-it`.
`config/generation.json` pins every one of its 15 files by size and
SHA-256, and `generation.runtime` checks them all before the worker
starts; a missing or changed file stops it.

`generation.worker` runs in the model environment
([environments.md](environments.md#model-environment)), never in the
application's. It loads the model once and answers one request at a time,
one JSON line in and one out on its standard input and output, with no
network port:

| Request | Reply |
| --- | --- |
| `count` a prompt (with its chat template) or a text | its tokens |
| `generate` from a prompt, a schema and an output limit | the text, prompt and output tokens, whether it finished, seconds and peak GPU memory; or `resource_exhausted` or `backend_failed` |
| `stop` | the worker ends |

The worker runs with Hugging Face offline and without telemetry, and
with every cache, home and temporary directory on G:. Prompts and output
travel only through the pipes; its standard error carries status lines
and exception type names, which the backend reads and discards, and
anything a library prints goes there too. `WorkerBackend` starts it when
first needed and waits for each reply within the time limit; a worker that
times out is stopped and the next request starts a new one.

The 4,300,079,472 parameters are in bfloat16 and run in full precision
(ADR 0012, decision 2): the language layers, final norm and rotary
embeddings, 6,121 MiB, are on the GPU; the image encoder (801 MiB), which
a text request never runs, and the token embedding table (1,280 MiB),
which Gemma also uses as its output layer, stay in system memory and run
on the processor. Decoding is greedy and constrained by the request's
schema with xgrammar.

## Measurement on this laptop

Milestone 2 measured the worker on the RTX 4060 Laptop GPU (8,188 MiB, of
which the desktop held 527 MiB) with invented requests only, and counted
the prompt tokens of the development case set:

| Measure | Result |
| --- | --- |
| Loading | 12 to 16 s; 6,121 MiB of weights on the GPU |
| Invented requests (5 lists, 9 presence checks, 1 explanation) | prompts of 2,749 to 5,604 tokens; outputs of 41 to 390 tokens in 5.9 to 48.9 s, 6.9 to 8.4 tokens a second; peak GPU memory 6,710 to 7,330 MiB |
| Their outcomes | 7 of 15 released as the model wrote them; 7 withheld by the gate for a fact their records do not support (such as one condition listed twice); 1 stopped as a string that ran on |
| One decoding step | 105.6 ms, of which the output layer on the processor takes 37 ms; a 1,209-token prompt is read in 0.7 s |
| `dev-30-v1` prompt tokens | lists and presence checks: median 6,079, 90th percentile 12,192, largest 37,418; explanations: median 16,518, 90th percentile 22,642, largest 38,798 |

In full precision the model fits and writes correct documents, at about
eight tokens a second. Under the configured limits (16,384 prompt tokens,
768 output tokens, 120 seconds) most explanations would fall back, since
their prompts exceed the budget and a complete explanation of some twenty
drivers runs to thousands of tokens, and so would lists of many
conditions; short lists and most presence checks fit. How to proceed
(faster decoding in full precision, other limits, or other precision) is
decided with the project owner before milestone 3 measures the
development cases.

## Commands

The default modes run the invented fixtures, or check the runtime's
configuration:

```powershell
& $AppPython -m ehr_evidence_app.generation.shape
& $AppPython -m ehr_evidence_app.generation.prompt
& $AppPython -m ehr_evidence_app.generation.adapter
& $AppPython -m ehr_evidence_app.generation.runtime
```

The operator check verifies the model files, loads the model through the
worker, writes a candidate for each invented request and prints tokens,
seconds, peak GPU memory and outcomes; with `--case-set` it also counts
the prompt tokens of a development case set's requests, as aggregates:

```powershell
& $AppPython -m ehr_evidence_app.generation.runtime --operator-check
& $AppPython -m ehr_evidence_app.generation.runtime --operator-check --case-set dev-30-v1
```

The operator mode runs the scripted backend on a development case set and
prints aggregate counts only; it refuses the evaluation case set:

```powershell
& $AppPython -m ehr_evidence_app.generation.adapter --operator-real --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.generation.adapter --operator-real --case-set pilot-10-v1
```
