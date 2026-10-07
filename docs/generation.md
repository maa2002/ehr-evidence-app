# Generation

Phase 7 makes a language model the author of every response from
evidence. MedGemma 1.5 4B IT, run locally, reads a deterministic
projection of a request's evidence, structured for its task, and writes
the complete candidate: the outcome, the statements with their wordings,
facts and references, the notices and the reason codes. The canonical
evidence bundle remains complete and unchanged for provenance and
validation. The candidate is released only through the release gate of
Phase 6 ([validation.md](validation.md)), which checks it against the
canonical bundle, and when it is not released the template candidate is,
as a recorded fallback.

The design is recorded in
[ADR 0012](decisions/0012-generation-design.md); the compact
representation and the selection of drivers in
[ADR 0013](decisions/0013-compact-generation.md); and the structured
evidence, syntax-only decoding, the parity of the Phase 9 conditions and
the rule for the precision in
[ADR 0014](decisions/0014-structured-evidence.md); the precision, the
examples that match the request, and the rules that set the limits and
freeze K in [ADR 0015](decisions/0015-generation-configuration.md).
Command examples assume
the session set-up in [development.md](development.md#session-set-up).

## Work Items

| Work Item | Module | Role | State |
| :--- | :---: | :---: | :--- |
| 41 | `generation.shape`, `generation.projection`, `generation.prompt`, `generation.adapter` | The Document a Candidate Is Written as and Its Schema; the Model's View of the Evidence; the Prompt; the Adapter Between the Application and a Writer | Done; Structured Evidence and Syntax-Only Decoding Since Milestone 3 |
| 42 | `generation.runtime`, `generation.worker` | The Pinned Model, the Worker in Its Own Environment on G:, and the Backend That Reaches It | Done; Two Precisions Measured |
| 43 | `generation.adapter` | Prompt, Output, Time, Attempt and Concurrency Limits; the Fallback and Its Category | Done |
| 44 | `qa.answer` | Answers From Evidence Written Through the Adapter | Done; Measured on the Development Case Sets |
| 45 | `validation.explanation` | Explanations of the Selected Drivers Written Through the Adapter | Done; Measured on the Development Case Sets |
| 46 | `generation.records`, `tracking.aggregates` | Protected Runtime Records, Tracker Aggregates, the Log Audit | Done |
| 46.1 | `generation.measure` | Development Measurement: Token Distributions, the Benchmark With Its Scores and the Paired Precision Comparison | Done |

## What the Model Writes

The model writes every response from evidence: list answers, presence
answers and their "not recorded", and explanations. Clarifications,
refusals and abstentions are settled before any evidence is read, from the
question or the checks before generation, and stay the templates'
approved notices.

It writes one compact JSON document in the shape of its answer
(`candidate-shape-v4`). The facts of each item stand in a row, and one
wording serves every item of its kind. A row lists its records before
what is counted from them, and a summary comes after its items, so that
the model counts what it has already written:

| Answer | Document, in the Order It Is Written |
| :--- | :--- |
| List | `outcome`, `reason_codes`; `item_wording`; `items`, One Row per Condition, `[condition, refs, visits, count]`, the Condition Named by the Reference of One of Its Records; `summary` (Wording, `groups`, `records`); `unmapped` (Wording and Count, or null); `notices` |
| Empty List | `outcome`, `reason_codes`, `empty` (Wording), `notices` |
| Presence Answer | `outcome`, `reason_codes`; `item_wording`; `items`, Rows `[condition, match, refs, visits, count]`; `summary` (Wording, `matches`) |
| Not Recorded | `outcome`, `reason_codes`, `absent` (Wording), `unmapped`, `notices` |
| Explanation | `outcome`, `reason_codes`; `prediction` (Wording, `label`, `score`, `logit`); `driver_wording`, `history_wording`, `context_wording`; `drivers`, One Row per Selected Driver, `[driver, rank, score, history, context]`, With History as `[refs, visits, count]` and Context as `[refs, count]`, Either null; `notices`; `omitted`, How Many Eligible Drivers Are Not Described |

A list states every condition and an explanation every selected driver;
only the prose is shared. A wording holds the placeholders of its
statement, such as `{condition}`, as in
[validation.md](validation.md#candidates). The request fills in what it
fixes (its identifiers, case, scope and bundle, and the condition a
presence question asks about), and each statement takes its answer's
place: a list's items after its summary, whatever order the document
writes them in, an explanation's history and context under their own
driver, the notice introducing the drivers after the prediction and
every other notice at the end. Visit numbers are read as a set.

## The Model's View of the Evidence

The model does not read the canonical bundle. It reads a deterministic
projection of it, structured for its task (`generation.projection`,
`projection-v5`), which holds all the evidence the request needs without
serialization redundancy or unrelated fields:

| Task | The Projection Holds |
| :--- | :--- |
| Lists | The Coverage Status, the Number of Records Without a Standard Concept, the Numbers of Conditions and Records, and the Scope's Records Grouped by Condition (a Standard Concept, or the ICD Source Code of Records Without One), in the Order of Each Condition's First Record, Each Record as `[ref, visit]`, Each Group With Its Number of Records and Visit Numbers |
| Presence Checks | The Coverage Status, the Number of Records Without a Standard Concept, the Number of Matching Records, and the Records That Match the Condition Asked About, as the Resolver Matches Them (`qa.resolve.match`), Grouped by Condition and Match Kind in the Same Way, With Their Numbers and Visit Numbers; an Empty Set When None Matches |
| Explanations | The Prediction's Label, Score and Logit; the Case's Restrictions, With the Approved Notice That Discloses Each; One Block per Selected Driver, Highest Score First, With Its Rank, Reference and Attribution Score, Its History (Its Own Linked Records in Pre-Target Visits: Their References, Visit Numbers and Number) and Its Context (Its Records in the Final Admission: Their References and Number), Each null When It Has None, as the Reply States Them; How Many Eligible Drivers Are Not Described |

Names, dates, native identifiers, visit and record metadata and provenance
are left out: the model refers to records by their references, and every
name a response shows is rendered from the canonical bundle. Grouping
records by condition, finding the records that match a condition and
joining a driver to its verified records are deterministic operations of
the application, as in Phase 5; the model is not asked to repeat them.
So are the counts and visits derived from the records: `projection-v2`
left them to the model, and on 33 sampled development requests 3 in each
precision were withheld for counts or visits alone, which met the test
fixed beforehand for giving them (ADR 0014, decision 2). The projection
states no outcome and no name: the model decides the outcome and which
records each statement cites, restates the counts and visits, and writes
every wording. A driver's attribution belongs to its concept, not to a
visit; its block only joins it to the concept's verified occurrences.

The projection holds everything its task needs: `projection.answer`
derives the correct document from the projection alone, and it equals the
template's for every request:

| Samples | Requests Answered From Their Projection Alone | Projection Size, of the Bundles' |
| :--- | :---: | :--- |
| Invented Fixtures | 16 of 16 | 11% |
| `dev-30-v1` | 430 of 430 | 4% |
| `pilot-10-v1` | 182 of 182 | 4% |

## Driver Selection

An explanation describes the eligible drivers with the highest attribution
scores, highest first, ties in the bundle's order, and at most the number
configured in `config/explanation.json` (`explanation-template-v3`; see
[validation.md](validation.md#explanations)). A notice states how many
eligible drivers are not described and kept in the evidence, and every
attribution stays in the evidence bundle for inspection. The number is
measured over candidate values on the development case sets (see
[below](#measurement-on-this-laptop)) and frozen before the final
evaluation, without the evaluation case set: it is 5, frozen since
ADR 0015.

## The Schema

Each task's document has one JSON Schema, which the model runtime
enforces while the model writes. It enforces the contract's syntax, and
nothing that depends on the evidence (`candidate-shape-v4`, ADR 0014):

- the document's fields, their types, and rows of a fixed number of
  values, each in its place;
- the contract's closed vocabularies: the outcomes of the task, the
  reason codes of each outcome, the notices an answer of the task may
  hold, named by their identifiers, and the match kinds;
- the formats and ranges the candidate contract gives references, visit
  numbers, counts, ranks, scores and concepts, read from the contract
  itself;
- a wording of one line of letters, spaces and plain punctuation,
  holding each placeholder of its kind exactly once and no other; it
  begins with its subject's placeholder (the condition, the number of
  conditions, the label, the rank or the driver), the others in any
  order, and only an empty list's wording begins freely.

Which records, visits and drivers exist, the prediction's and drivers'
values, and which outcome and notices a request calls for are left to the
prompt's grounding rules and the release gate. So the model can cite a
record the bundle does not hold, restate a score wrongly or choose the
wrong outcome, and the gate withholds each of them. The schema is the
same for every request of a task, and the same for both conditions of the
Phase 9 comparison.

The wordings, and an explanation's history and context, are defined once
under `$defs` and referenced. The first runs of the model showed why the
wording is this narrow: allowed any characters, a wording that had lost a
placeholder ran on into markup until the output limit. Bounding the length
of a wording's runs in the schema made each request's grammar take 10 to
35 seconds to compile, so the worker bounds them instead: output that runs
150 tokens without a double quote is stopped and cut off, and so falls
back.

The adapter checks every document against its schema again, since none
is trusted to have been constrained; xgrammar does not enforce that a
list's values are distinct, so a repeated visit or notice is found only
then. The runtime writes JSON with a space after each comma and colon, and
every document in a prompt, or counted against a limit, is written the
same way.

## The Prompt

The prompt (`prompt-v9`) is two chat messages: the rules every reply
follows, then worked examples on invented data, the task with the shape
of its reply, and the request's projection as JSON. The rules ask
for facts in their fields only, wordings without facts that begin with
the placeholder named for them, one wording for every item of its kind,
and nothing the evidence does not record. The task offers every reply the
task can have, each with the condition on the view that calls for it (a
list whose "conditions" hold groups is an answer, one whose "conditions"
are empty is "not recorded"), and the example wordings of each. The list
and presence examples group several records of one concept into one
item. They match the request where its evidence decides the shape of
every correct reply (ADR 0015): when the request's view has records
without a standard concept, the example has one too, which its reply
discloses (the invented bundle's last record has lost its standard
concept), and otherwise it has none. A presence check is shown two
examples whatever its evidence, the answer and a "not recorded" for a
condition none of the records match. An explanation's example leaves
drivers out exactly when the request does. Until `prompt-v8` every list
and presence example disclosed such a record, and the model disclosed
one in every "not recorded" it wrote. Since `prompt-v9` the explanation
task describes a driver's history and context as the view gives them,
the reply stating each as it stands and null when the driver has none
(ADR 0017).

Prompts and model output hold patient records; they are never logged.

## The Adapter

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
   and records why, and the reason's category:

| Fallback | Category | When |
| :--- | :---: | :--- |
| `no_model` | `unavailable` | No Writer Is Configured |
| `busy` | `unavailable` | Another Request Is Being Written |
| `prompt_over_budget` | `limit_exceeded` | The Prompt Exceeds `max_prompt_tokens` |
| `output_over_budget` | `limit_exceeded` | The Answer's Template Document Already Exceeds `max_output_tokens` |
| `truncated` | `limit_exceeded` | Output Stopped at the Token Limit |
| `timeout` | `timeout` | The Backend or Its Output Exceeded `max_request_seconds` |
| `resource_exhausted` | `resource_exhausted` | The Backend Ran Out of Memory |
| `backend_failed` | `backend_failed` | The Backend Failed or Crashed |
| `malformed` | `invalid_output` | Output That Is Not JSON, or Does Not Stand for a Candidate |
| `schema_violated` | `invalid_output` | A Document That Breaks Its Schema |
| `gate_rejected` | `validation_rejection` | A Candidate the Gate Withholds; the Gate's Reason Codes Are Kept |

A request over a limit is an operational `limit_exceeded` case: never
insufficient evidence, and never a success of the model. Phase 9 counts
every fallback as a failure of the model. Without a writer every answer
from evidence is the template's, a `no_model` fallback, so the application
and every check run without the model, and responses are unchanged.

## Limits

The limits are fixed ceilings in code; `config/execution_limits.json`
may lower them (see [evidence.md](evidence.md#contracts)):

| Limit | Configured | Ceiling | On the Generation Path |
| :--- | :---: | :---: | :--- |
| `max_prompt_tokens` | 16,384 | 131,072 | A Longer Prompt Falls Back |
| `max_output_tokens` | 768 | 4,096 | Output Cut Off Here Is Never Read |
| `max_request_seconds` | 180 | 600 | Later Output Is Not Read |
| `max_generation_attempts` | 1 | 3 | Only a Failed Backend Is Tried Again |
| `max_concurrent_requests` | 1 | 8 | A Further Request Falls Back as Busy |

The limits change only after the token distributions are measured on the
representation the model reads (ADR 0013). They were set on the
development case sets by the rule of ADR 0015 and are frozen for the
final evaluation, with the time limit raised from 120 seconds so that
output within the output limit is not ended by time at BF16's slowest
measured rate; the measurements are below.

## Scripted Runs

Without a model, the adapter is run with a scripted backend that returns
each request's template document, and every way a writer can fail. On the
invented fixtures and the development case sets every template answer is
released unchanged when a writer returns it, and every failure ends in its
fallback with exactly the template's response:

| Samples | Requests (Lists, Presence Checks, Explanations) | Released as Written | Fallbacks as Expected |
| :--- | :---: | :---: | :--- |
| Invented Fixtures | 16 (5, 9, 2) | 16 of 16 | 192 of 192 |
| `dev-30-v1` | 430 (135, 269, 26) | 430 of 430 | 5,160 of 5,160 |
| `pilot-10-v1` | 182 (58, 114, 10) | 182 of 182 | 2,184 of 2,184 |

## The Model and Its Worker

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
| :--- | :--- |
| `count` a Prompt (With Its Chat Template) or a Text | Its Tokens |
| `generate` From a Prompt, a Schema and an Output Limit | The Text, Prompt and Output Tokens, Whether It Finished, Seconds and Peak GPU Memory; or `resource_exhausted` or `backend_failed` |
| `stop` | The Worker Ends |

The worker runs with Hugging Face offline and without telemetry, and
with every cache, home and temporary directory on G:. Prompts and output
travel only through the pipes; its standard error carries status lines
and exception type names, which the backend reads and discards, and
anything a library prints goes there too. `WorkerBackend` starts it when
first needed and waits for each reply within the time limit; a worker that
times out is stopped and the next request starts a new one.

Decoding is greedy and constrained by the request's schema with xgrammar.
The 4,300,079,472 parameters are loaded in one of two precisions, set by
`worker.precision` in `config/generation.json`:

| Precision | Placement |
| :--- | :--- |
| `bf16-mixed` (Configured) | BF16, Full Precision (ADR 0012, Decision 2), With a Mixed GPU/CPU Placement: The Language Layers, Final Norm and Rotary Embeddings, 6,121 MiB, on the GPU; the Image Encoder (801 MiB), Which a Text Request Never Runs, and the Token Embedding Table (1,280 MiB), Which Gemma Also Uses as Its Output Layer, in System Memory, Run on the Processor. The Model Is Not GPU-Resident. |
| `nf4` | 4-Bit NF4 Weights With BF16 Computation (bitsandbytes), the Whole Model on the GPU |

## Runtime Records

Every response can be traced from one protected record
(`generation.records`, ADR 0012, decision 5). When a recorder is given,
`qa.answer.answer` and `validation.explanation.explain` write the record
of each request they answer, whatever its outcome, under the workspace's
`protected-data\runtime-records`, one JSON file per request in a
directory per UTC day, written once and never overwritten. A record
holds:

| Part | What It Holds |
| :--- | :--- |
| The Request | Its Identifier, Task, Case, Scope and Question, or the Prediction an Explanation Is Of |
| The Evidence | The Bundle's Identifier and SHA-256 |
| The Versions | The Application, Prompt, Shape, Projection, Candidate Contract, Question Interpretation, Condition Resolution, Answer Rules, Explanation Template and Wording Rules, and the SHA-256 of the Execution Limits, the Driver Selection and the Model's Configuration |
| The Response | The Released Response and Its SHA-256 |
| The Generation, for a Response Written From Evidence | Who Wrote It (the Model or the Template), the Fallback and Its Category, the Gate's Reasons, the Backend, the Prompt and Output Tokens, Whether the Output Ended by Itself, the Seconds and Peak GPU Memory, and the Model's Output and Candidate |

A notice, or a request the checks before generation stop, records no
generation. Records hold patient records: they are never logged, printed
or committed, and a recorder refuses a directory outside the protected
data. `--operator-real --aggregate` prints what the records add up to,
counts and measures only: requests by task and outcome, the responses
written from evidence and by the model, fallbacks by reason and category,
and the tokens, seconds and peak memory of the model's attempts. The
tracker receives them as aggregates only (`tracking.aggregates`; see
[experiment-tracking.md](experiment-tracking.md#runtime-aggregates)): the
requests, the shares of responses from evidence the model wrote and that
fell back, and the median latency of its attempts, with the manifest
that holds the counts.

Routine logs hold statuses and counts only. The log audit
(`--operator-real --audit-logs`) reads every file under the workspace's
`outputs\logs` and names, by rule and count, what does not belong there:
the secrets, user paths and patient identifiers of the publication policy
(`config/publishable_policy.json`), and the markers of a prompt, an
evidence view, a candidate document, a bundle or a record; it never
prints what a rule matched. The default mode checks both on invented
requests and logs.

## Measurement on This Laptop

All measurements are on the RTX 4060 Laptop GPU (8,188 MiB, of which the
desktop holds about 527 MiB). Real requests print aggregates only.

### Milestone 2: The Complete Bundle

Milestone 2 measured the worker in BF16 with invented requests, and
counted the prompt tokens of `dev-30-v1` when the prompt held the complete
canonical bundle and the candidate a sentence per item (`prompt-v1`,
`candidate-shape-v1`):

| Measure | Result |
| :--- | :--- |
| Loading | 12 to 16 s; 6,121 MiB of Weights on the GPU |
| Invented Requests (5 Lists, 9 Presence Checks, 1 Explanation) | Prompts of 2,749 to 5,604 Tokens; Outputs of 41 to 390 Tokens in 5.9 to 48.9 s, 6.9 to 8.4 Tokens a Second; Peak GPU Memory 6,710 to 7,330 MiB |
| Their Outcomes | 7 of 15 Released as the Model Wrote Them; 7 Withheld by the Gate for a Fact Their Records Do Not Support (Such as One Condition Listed Twice); 1 Stopped as a String That Ran On |
| One Decoding Step | 105.6 ms, of Which the Output Layer on the Processor Takes 37 ms; a 1,209-Token Prompt Is Read in 0.7 s |
| `dev-30-v1` Prompt Tokens | Lists and Presence Checks: Median 6,079, 90th Percentile 12,192, Largest 37,418; Explanations: Median 16,518, 90th Percentile 22,642, Largest 38,798 |

Under the configured limits most explanations, and lists of many
conditions, would have fallen back. The project owner directed that this
be treated as a finding about the prompt and runtime design, not as a
reason to raise the limits (ADR 0013).

### Milestone 3: The Compact Representation

The token distributions were measured again with the model's tokenizer on
the prompts (`prompt-v3`) and the template documents
(`candidate-shape-v3`) of every development request, the documents
written as the runtime writes JSON, and for explanations at each
candidate number of drivers K. A template document's tokens stand for the
output a correct answer needs; medians of an even number of requests are
rounded up:

| `dev-30-v1` Requests | Prompt Tokens: Median, 90th Percentile, Largest | Template Document Tokens: Median, 90th Percentile, Largest |
| :--- | :---: | :--- |
| Lists (135) | 1,769; 2,434; 5,217 | 292; 652; 1,950 |
| Presence Checks (269) | 2,111; 2,771; 5,569 | 85; 91; 154 |
| Explanations (26), K = 3 | 2,039; 2,051; 2,160 | 348; 360; 469 |
| K = 5 | 2,108; 2,130; 2,249 | 436; 460; 580 |
| K = 8 | 2,206; 2,261; 2,377 | 562; 612; 739 |
| K = 10 | 2,272; 2,329; 2,454 | 642; 704; 838 |
| K = 15 | 2,438; 2,514; 2,772 | 859; 936; 1,215 |

| `pilot-10-v1` Requests | Prompt Tokens: Median, 90th Percentile, Largest | Template Document Tokens: Median, 90th Percentile, Largest |
| :--- | :---: | :--- |
| Lists (58) | 1,728; 2,527; 3,583 | 266; 608; 1,124 |
| Presence Checks (114) | 2,075; 3,165; 3,939 | 85; 91; 108 |
| Explanations (10), K = 3 | 2,043; 2,064; 2,230 | 356; 374; 539 |
| K = 5 | 2,107; 2,127; 2,308 | 437; 455; 637 |
| K = 8 | 2,215; 2,427; 2,622 | 568; 786; 882 |
| K = 10 | 2,278; 2,495; 2,684 | 651; 874; 963 |
| K = 15 | 2,467; 2,676; 2,849 | 892; 1,101; 1,184 |

Every prompt is now within 16,384 tokens, with room to spare (largest
5,569). Output is what the limits turn on: every presence check fits 768
tokens, and so does every explanation up to K = 5; at K = 8 every
`dev-30-v1` explanation fits (largest 739) and the largest pilot ones do
not (largest 882); at K = 10 some of both exceed it; and the longest lists
do not fit (90th percentile 652, largest 1,950). A request over a limit
falls back as `limit_exceeded`. The same measurement of `candidate-shape-v2`,
written without the spaces, was about a tenth lower.

### The Precision Benchmark

Each precision wrote the same 33 requests of `dev-30-v1` under the
configured limits: up to 8 of each task and template outcome, drawn by a
digest of their identifiers (8 lists, 1 empty list, 8 presence checks of
a recorded condition, 8 of a condition recorded elsewhere, and 8
explanations with K = 10). BF16 has the mixed GPU/CPU placement, with the
output layer on the processor; NF4 is GPU-resident. Released means
released by the gate as the model wrote it; everything else fell back to
the template and counts as a failure of the model.

| Version | Precision | Released | Fallbacks | Tokens a Second (Median) | Seconds (Median; Largest) | Peak GPU Memory |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| `prompt-v2`, `candidate-shape-v2` | BF16, Mixed Placement | 4 of 33 | 13 Cut Off by the Runaway Guard, 15 Withheld by the Gate, 1 Breaking Its Schema | Lists 6.3, Presence Checks 5.3 to 5.4, Explanations 8.4 | Lists 23.9, Presence Checks 22.3 to 24.4, Explanations 73.7; 99.0 | 6,439 to 6,600 MiB |
| `prompt-v2`, `candidate-shape-v2` | NF4, GPU-Resident | 4 of 33 | 16 Cut Off, 13 Withheld | Lists and Presence Checks 13.3 to 13.5, Explanations 7.5 | Lists 12.6, Presence Checks 12.8, Explanations 73.2; 75.6 | 3,596 to 3,746 MiB |
| `prompt-v3`, `candidate-shape-v3` | BF16, Mixed Placement | 4 of 33 | 27 Withheld, 2 Breaking Their Schema | 8.0 to 8.4 | Lists 22.2, Presence Checks 18.3 to 21.0, Explanations 76.8; 78.8 | 6,369 to 6,630 MiB |
| `prompt-v3`, `candidate-shape-v3` | NF4, GPU-Resident | 5 of 33 | 27 Withheld, 1 Breaking Its Schema | Lists and Presence Checks 13.3 to 14.1, Explanations 7.3 | Lists 14.0, Presence Checks 8.2 to 11.7, Explanations 56.5; 93.2 | 3,535 to 3,773 MiB |

By task, the requests released as the model wrote them:

| Version, Precision | Lists (8) | Empty List (1) | Presence, Recorded (8) | Presence, Recorded Elsewhere (8) | Explanations (8) |
| :--- | :---: | :---: | :---: | :---: | :--- |
| v2, BF16 | 0 | 1 | 0 | 0 | 3 |
| v2, NF4 | 0 | 1 | 1 | 0 | 2 |
| v3, BF16 | 2 | 1 | 0 | 0 | 1 |
| v3, NF4 | 3 | 1 | 0 | 0 | 1 |

Each version is counted under the gate of its time. The explanation
released in each precision with `prompt-v3` left a driver's history out;
for a while the gate withheld that (ADR 0013, decision 7), and it
releases it again since ADR 0014 (decision 5).

NF4 writes lists and presence checks about 1.7 times as fast as BF16
with the mixed placement, in a little over half the GPU memory. An
explanation runs at 7 to 8 tokens a second in either precision, which
points to its grammar, applied on the processor, rather than the model.
The release rates do not tell the two precisions apart on this
sample.

With `prompt-v2` most failures were wordings that ran on: most of the
lists and presence checks cut off stopped at 171 tokens, their first
wording having begun some 20 tokens into the document and run the 150
tokens after which the runaway guard ends a string. A wording that has
lost a placeholder cannot be closed, so the model wrote on; on invented
requests it began a list's summary with an empty list's "No conditions
are recorded", or typed a condition's name for its placeholder.
`prompt-v3` ended that (ADR 0013, decision 6): no output was cut off or
malformed. What is left, read from each document by field name only
(the benchmark prints no value), are facts the gate does not support:

- presence checks: for every condition recorded elsewhere the model
  answered that it is recorded, and for a recorded condition it added
  records that do not match and left out some that do;
- lists: conditions or records left out, counts and records that do not
  agree, the disclosure of records without a standard concept left out,
  and visits repeated;
- explanations: drivers left out, history or context rows that cite other
  records or miscount them, and notices left out or repeated.

The gate withheld every one of them. Reading the evidence correctly is
what limits the model on tables of this size, not its output format or
its speed.

### Candidate Numbers of Drivers

The same 8 explanations were written in BF16 with each candidate K
(`--task prediction_explanation --drivers K`), and counted under the
gate as it now stands; the runs shared the processor with other checks,
so their timings are not used:

| K | Released as the Model Wrote Them | Output Tokens (Median) |
| :--- | :---: | :--- |
| 3 | 2 of 8 | 349 |
| 5 | 1 of 8 | 423 |
| 8 | 1 of 8 | 541 |
| 10 | 0 of 8 | 636 |

These runs used `candidate-shape-v3` and `prompt-v3`. Fewer drivers were
released more often, but the numbers are too small to choose K by. With
K = 5 or fewer every development explanation fits the output limit; K = 5
was frozen with the limits (ADR 0015).

### Structured Evidence

With the structured view of ADR 0014, syntax-only decoding and K = 5,
the same 33 requests were written in both precisions and compared pair
by pair (`--paired`), each version on its committed code and under the
gate as it now stands:

| Commit | What Changed | BF16 Released | NF4 Released | NF4 Minus BF16, 95% CI; Exact McNemar p |
| :--- | :---: | :---: | :---: | :--- |
| `7f14baf` | `projection-v2`: Records Grouped, Matching Sets, Driver Blocks, No Counts; `prompt-v4`; `candidate-shape-v4` | 9 of 33 | 8 of 33 | -3.0 Points, -12.6 to +6.2; 1.000 |
| `96edab4` | `projection-v3`: Counts and Visits Given; `prompt-v5` | 12 | 11 | -3.0, -11.5 to +5.4; 1.000 |
| `42c9011` | `prompt-v6`: The Examples Disclose a Record Without a Standard Concept | 21 | 18 | -9.1, -19.9 to +2.2; 0.250 |
| `20e02a0` | `projection-v4`: The Notice of Each Restriction; `prompt-v7`: A "not recorded" Example as Well | 26 | 17 | -27.3, -42.1 to -10.4; 0.004 |

By task, BF16 and NF4:

| Commit | Lists (8) | Empty List (1) | Presence, Recorded (8) | Presence, Recorded Elsewhere (8) | Explanations (8) |
| :--- | :---: | :---: | :---: | :---: | :--- |
| `7f14baf` | 3, 3 | 1, 0 | 2, 2 | 3, 3 | 0, 0 |
| `96edab4` | 3, 3 | 1, 0 | 2, 2 | 3, 3 | 3, 3 |
| `42c9011` | 7, 5 | 1, 0 | 7, 7 | 3, 3 | 3, 3 |
| `20e02a0` | 7, 5 | 1, 0 | 7, 5 | 5, 5 | 6, 2 |

With `projection-v2` the test for counts and visits was met: 3 requests
in each precision were withheld for counts or visits alone, all of them
explanations. `projection-v3` gave them, and no request has been withheld
for them alone since. The largest failure of lists and presence checks
was then the disclosure of records without a standard concept, which no
worked example had shown; `prompt-v6` showed it in an answer and
`prompt-v7` in a "not recorded" too. Half the explanations stated the
wrong notices for their restrictions until `projection-v4` gave the
notice of each. With BF16 at `20e02a0`, what is left is one list item
miscounted, one presence answer with a wrong reason code, three "not
recorded" that disclose records without a standard concept their scope
does not have (read at first as disclosures left out; see below), and
two explanations with an added driver or a history row that cites other
records; the model stated 14 of the 19
available history lines and 24 of 24 context lines, and every released
explanation all of its own.

As the task became feasible the precisions parted: at `20e02a0` BF16
released every request NF4 did and 9 more. NF4 repeated visits and
notices, stated notices of restrictions the case does not have, stated
wrong reason codes and answered an empty list with records the bundle
does not hold. Latency and memory at `20e02a0`:

| Precision | Seconds, Median (Largest): Lists, Presence Checks, Explanations | Peak GPU Memory |
| :--- | :---: | :--- |
| BF16, Mixed Placement | 42.4 (83.8); 16.4 to 17.1 (18.1); 65.8 (73.6) | 6,489 to 6,642 MiB |
| NF4, GPU-Resident | 32.4 (64.2); 12.2 to 13.0 (13.9); 48.2 (51.8) | 3,645 to 3,787 MiB |

These versions were tuned on these 33 requests, so their last rates are
optimistic. The precision, K and the limits are decided on the whole of
`dev-30-v1` (ADR 0014, decision 9); `eval-100-v1` is not used.

### The Whole Development Set

All 430 requests of `dev-30-v1` were written in both precisions at
`20e02a0` (`prompt-v7`, K = 5) under the configured limits and compared
pair by pair, as ADR 0014 (decision 9) set out:

| Requests | BF16 Released | NF4 Released | NF4 Minus BF16, 95% CI; Exact McNemar p |
| :--- | :---: | :---: | :--- |
| Lists (134) | 93 | 32 | -45.5 Points, -53.5 to -36.1; Below 0.001 |
| Empty List (1) | 1 | 0 | -100 Points, -100 to +12.2; 1.000 |
| Presence, Recorded (134) | 127 | 55 | -53.7, -61.7 to -44.6; Below 0.001 |
| Presence, Recorded Elsewhere (135) | 80 | 80 | 0.0, the Same 80 Requests; 1.000 |
| Explanations (26) | 13 | 5 | -30.8, -49.8 to -7.6; 0.021 |
| All (430) | 314 (73.0%) | 172 (40.0%) | -33.0, -37.4 to -28.3; Below 0.001 |

BF16 released 144 requests that NF4 did not, and NF4 2 that BF16 did not.
NF4 is not non-inferior, so BF16 with the mixed placement stays
(ADR 0015, decision 1).

The intervals here and in the section above are Newcombe's method 10,
recomputed on 2026-10-03 from the recorded pairs. They were first
computed without the method's continuity correction of the pairs'
correlation (Newcombe's method 8), which made them slightly narrow; no
conclusion changes. Latency and memory:

| Precision | Seconds, Median (Largest): Lists, Presence Checks, Explanations | Tokens a Second, Median: Lists, Presence Checks, Explanations | Peak GPU Memory | Whole Run |
| :--- | :---: | :---: | :---: | :--- |
| BF16, Mixed Placement | 32.9 (87.4); 10.9 to 11.3 (18.7); 64.8 (111.1, and One Over the 120-Second Limit) | 8.7; 8.0; 5.6 | 6,489 to 6,720 MiB | 9,081 s |
| NF4, GPU-Resident | 33.0 (104.8); 12.2 to 12.7 (15.0); 50.9 (104.5) | 7.5; 7.1; 7.4 | 3,645 to 3,864 MiB | 10,014 s |

On the whole set NF4 was no faster for lists and slower for presence
checks than BF16, and faster for explanations only. The processor runs
BF16's output layer, and the grammar for either precision, so timings
vary with its load: on the 33 requests at `20e02a0` BF16 took 42.4
seconds for a list at the median.

What BF16 did not release, by what the benchmark names:

- lists (41): 24 withheld for facts their records do not support (items
  left out, repeated or out of order, their records, counts and visits,
  and the disclosure of records without a standard concept), 1 citing a
  record the bundle does not hold, 2 repeating an item against their
  schema, and 14 at the output limit: 10 whose correct document alone
  exceeds 768 tokens, never written, and 4 cut off at 768;
- presence checks of a recorded condition (7): a wrong reason code;
- presence checks of a condition recorded elsewhere (55): the disclosure
  of records without a standard concept, stated where the scope has none;
- explanations (13): 12 withheld, above all for context rows stated for
  drivers that have none and history rows miscounted or citing other
  records (the benchmark also names rows left out, which the gate
  allows), and 1 over 120 seconds. The model stated 69 of the 79
  available history lines and 56 of 57 context lines, and every
  released explanation all of its own.

The 55 were the same in both precisions. Of the 135 checks answered "not
recorded", 80 have records without a standard concept within their
scope and 55 have none; both precisions released exactly the 80, and
every "not recorded" either wrote was 86 to 88 tokens long, the length
of the reply with the disclosure (the shortest correct presence reply is
46). The model disclosed unmapped records whether the scope had any or
not, as every worked example it was shown did. The earlier reading of
the three such failures on the 33 requests as disclosures left out had
the direction wrong; the benchmark now names a field, notice or reason
code as added or left out. `prompt-v8` shows a disclosure in the
examples exactly when the request's view has records without a standard
concept (ADR 0015, decision 2); the prompts of the 240 requests that
have them are unchanged byte for byte, and those of the other 164 lists
and presence checks change.

The token distributions measured again at `93762df`, the template
documents written as the runtime writes JSON:

| Requests | Prompt Tokens: Median, 90th Percentile, Largest | Template Document Tokens: Median, 90th Percentile, Largest |
| :--- | :---: | :--- |
| `dev-30-v1` Lists (135) | 2,095; 2,732; 4,695 | 292; 652; 1,950 |
| Presence Checks (269) | 2,401; 2,430; 2,541 | 85; 91; 154 |
| Explanations (26), K = 5 | 2,332; 2,356; 2,499 | 436; 460; 580 |
| `pilot-10-v1` Lists (58) | 2,049; 2,642; 3,409 | 266; 608; 1,124 |
| Presence Checks (114) | 2,405; 2,433; 2,457 | 85; 91; 108 |
| Explanations (10), K = 5 | 2,330; 2,356; 2,535 | 437; 455; 637 |

Every prompt fits 16,384 tokens by far. The output and time limits cost
BF16 15 requests; the rule that sets them is fixed in ADR 0015
(decision 3) before the rerun that applies it: BF16 writes every
request of `dev-30-v1` again with `prompt-v8` under 2,048 output tokens
and 300 seconds, and the same outputs are counted under 768 tokens and
180 seconds.

### The Configuration for the Final Evaluation

BF16 wrote every request of `dev-30-v1` and `pilot-10-v1` again at
`f54a152` (`prompt-v8`) under 2,048 output tokens and 300 seconds, and the
same outputs were counted under 768 tokens with 180 and with 120 seconds
(`--within`). `pilot-10-v1` was not used to tune the generation, so its
rates are the less optimistic ones:

| Requests | `dev-30-v1`, 2,048 Tokens and 300 s | 768 Tokens and 180 s | `pilot-10-v1`, 2,048 Tokens and 300 s | 768 Tokens and 180 s |
| :--- | :---: | :---: | :---: | :--- |
| Lists | 99 of 134 | 98 | 40 of 56 | 39 |
| Empty Lists | 1 of 1 | 1 | 2 of 2 | 2 |
| Presence, Recorded | 130 of 134 | 130 | 54 of 56 | 54 |
| Presence, Recorded Elsewhere | 135 of 135 | 135 | 58 of 58 | 58 |
| Explanations | 13 of 26 | 13 | 4 of 10 | 4 |
| All | 378 of 430 | 377 (87.7%; 95% CI 84.2 to 90.5) | 158 of 182 | 157 (86.3%; 80.5 to 90.5) |

768 tokens with 120 seconds released the same as with 180. The larger
output limit adds 1 request of `dev-30-v1`, 0.2 points against the 2 the
rule of ADR 0015 (decision 3) requires, so the output limit stays at 768
tokens and the time limit is 180 seconds; five lists ran past 300 seconds
at this run's speed, and had all five been released the gain would still
have been 1.4 points. Frozen for the final evaluation: BF16 with the
mixed placement, K = 5 and the limits of 16,384 prompt tokens, 768
output tokens and 180 seconds. The prompt, the projection, the shape and
the validator are locked with them before the final evaluation (ADR
0014, decision 6); a change before then is a new version, measured again.

With `prompt-v8` every presence check answered "not recorded" was
released: the 80 of `dev-30-v1` with records without a standard concept
in scope, as before, and the 55 without, none of which had been. Within
768 tokens and 120 seconds, lists rose from 93 to 98 and presence checks
of a recorded condition from 127 to 130. Split by whether the scope has
records without a standard concept: lists 51 of 80 with and 48 of 54
without in `dev-30-v1` (16 of 31 and 24 of 25 in `pilot-10-v1`), presence
checks of a recorded condition 80 of 80 and 50 of 54 (31 of 31 and 23 of
25). The explanations, whose prompts did not change, gave the same
outcomes, findings and output tokens as in the paired run: greedy
decoding reproduces its output.

What the configuration does not release on `dev-30-v1` (53 requests):

- lists (36): at 2,048 tokens, 22 withheld for items repeated, out of
  order or left out and their records, counts and visits, 3 repeating an
  item against their schema, 1 citing a record the bundle does not hold,
  5 over 300 seconds and 4 running to the output limit; at 768 tokens,
  one more whose reply is longer;
- presence checks of a recorded condition (4): an item repeated, with
  records, counts and visits that do not agree;
- explanations (13): 12 withheld, above all for context rows stated for
  drivers that have none (7) and history rows that miscount or cite
  other records, and 1 that runs on.

On `pilot-10-v1` the explanations stated 21 of the 36 available history
lines and 11 of 14 context lines, and those released 13 of 13 and 4 of 5.

This run wrote every task at 5.0 to 5.6 tokens a second (median), where
the paired run's BF16 wrote lists and presence checks at 8.0 to 8.7:
BF16's output layer and the grammar run on the processor, and its speed
follows the processor's state. At the slowest rate measured, 4.8 tokens
a second, 768 tokens take 160 seconds, within the time limit. Medians in
this run: lists 48.9 seconds, presence checks 15.8 to 16.6, explanations
66.5; peak GPU memory 6,480 to 6,905 MiB, with no request out of
memory.

### The Bounded Prompt Pass

One pass on the prompt was made before the lock, by rules fixed first
(ADR 0017): explanations, then lists; the precision, K and the limits
frozen; every presence prompt unchanged; diagnosis and at most two
candidates per task on `dev-30-v1` only; a candidate kept only if it
releases more of its task on `dev-30-v1` than `prompt-v8` and, run once
on `pilot-10-v1`, at least as many there.

The benchmark now scores every document against the correct one: the
conditions (a group of the correct document, named by any of its
records) and the records cited for them, the drivers and their history
and context lines, each as precision, recall and F1 summed over the
stratum, a request without a document stating nothing. For the documents
not released it names what their rows are. At `prompt-v8`, on
`dev-30-v1`:

- explanations: history lines F1 0.784 (precision 0.845, recall 0.732)
  and context lines F1 0.797 (0.716, 0.898); in the withheld ones, a
  context line stated for a driver that has none 18 times, 12 of them
  with the driver's own history records, and 7 history lines citing part
  of their records. The view gave an empty context as `[]` and a history
  as `[reference, visit number]` pairs;
- lists: conditions F1 0.783 (0.949, 0.666) and records F1 0.733 (0.964,
  0.592), recall low above all because 14 lists end at the output limit
  and state nothing (the 10 whose correct document exceeds it, and 4
  that run on); in the withheld ones, 75 rows held a
  condition's records of one visit of several, 76 repeated a condition,
  and 51 conditions were never named.

| Candidate (`dev-30-v1`) | Released | Scores |
| :--- | :---: | :--- |
| Explanations, `prompt-v8` | 13 of 26 | Drivers F1 0.973; History Lines 0.784; Context Lines 0.797 |
| Explanations, a Driver's History and Context as the Reply States Them, null When It Has None | 21 of 26 | Drivers 1.000; History Lines 0.943; Context Lines 0.911 |
| Lists, `prompt-v8` | 98 of 134 | Conditions F1 0.783; Records 0.733 |
| Lists, Groups as References, Visits and Number, One Item per Group Whatever Its Visits | 94 of 134 | Conditions 0.830; Records 0.792 |

The explanation candidate was kept (`projection-v5`, `prompt-v9`): on
`pilot-10-v1`, run once, its explanations were released for 8 of 10
against 4 (drivers F1 1.000, history lines 0.986, context lines 0.929).
The list candidate was not: it named more conditions (3 left unnamed)
and cited records more exactly, but it still split conditions by visit
(66 rows) and misstated the number of records without a standard
concept in 12 lists, and released fewer. Lists whose scope has records
without a standard concept are also the longer ones (median 16 items
against 8 on `dev-30-v1`, 17 against 4 on `pilot-10-v1`), so their lower
release rate may come from their length rather than from those records.

With the configuration kept, the model's candidates are released for
385 of the 430 requests of `dev-30-v1` (89.5%, 95% CI 86.3 to 92.1) and
161 of the 182 of `pilot-10-v1` (88.5%, 83.0 to 92.3): lists 98 and 39,
empty lists 1 and 2, presence checks of a recorded condition 130 and 54,
of a condition recorded elsewhere 135 and 58, explanations 21 and 8.
Every list and presence prompt is the same as under `prompt-v8`, so
their outputs, and these counts, are those measured before; the runs
that scored them reproduced every count. The configuration's scores,
summed over each stratum:

| Requests | `dev-30-v1` | `pilot-10-v1` |
| :--- | :---: | :--- |
| Lists: Conditions | F1 0.783 (Precision 0.949, Recall 0.666) | F1 0.753 (0.953, 0.622) |
| Lists: Records | F1 0.733 (0.964, 0.592) | F1 0.672 (0.933, 0.524) |
| Lists and Empty Lists: Outcome Right | 121 of 135 | 51 of 58 |
| Presence, Recorded: Conditions | F1 0.979 (0.959, 1.000) | F1 0.983 (0.966, 1.000) |
| Presence, Recorded: Records | F1 1.000 | F1 1.000 |
| Presence: Outcome Right | 269 of 269 | 114 of 114 |
| Explanations: Drivers | F1 1.000 | F1 1.000 |
| Explanations: History Lines | F1 0.943 (0.974, 0.915) | F1 0.986 (1.000, 0.972) |
| Explanations: Context Lines | F1 0.911 (0.875, 0.949) | F1 0.929 (0.929, 0.929) |

A list's recall is lowest because the lists that end at the output limit
state nothing. Every presence check chose the right outcome, recorded or
not, and cited exactly the right records; the 6 presence answers withheld
split a condition's records by visit, as the lists do.

## Commands

The default modes run the invented fixtures, or check the runtime's
configuration:

```powershell
& $AppPython -m ehr_evidence_app.generation.shape
& $AppPython -m ehr_evidence_app.generation.projection
& $AppPython -m ehr_evidence_app.generation.prompt
& $AppPython -m ehr_evidence_app.generation.adapter
& $AppPython -m ehr_evidence_app.generation.measure
& $AppPython -m ehr_evidence_app.generation.records
& $AppPython -m ehr_evidence_app.generation.runtime
```

The records' operator modes read the protected runtime records or the
routine logs and print counts only:

```powershell
& $AppPython -m ehr_evidence_app.generation.records --operator-real --aggregate
& $AppPython -m ehr_evidence_app.generation.records --operator-real --audit-logs
```

The operator check verifies the model files, loads the model through the
worker, writes a candidate for each invented request and prints tokens,
seconds, peak GPU memory and outcomes; with `--case-set` it also counts
the prompt tokens of a development case set's requests, as aggregates:

```powershell
& $AppPython -m ehr_evidence_app.generation.runtime --operator-check
& $AppPython -m ehr_evidence_app.generation.runtime --operator-check --case-set dev-30-v1
```

The operator modes read a development case set, print aggregate counts
only, and refuse the evaluation case set. The projection check derives
every answer from its projection alone; the adapter runs the scripted
backend; the measurement counts the token distributions with the model's
tokenizer, or writes a deterministic, stratified sample of the requests
with the model at one precision:

```powershell
& $AppPython -m ehr_evidence_app.generation.projection --operator-real --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.generation.adapter --operator-real --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.generation.measure --tokens --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.generation.measure --benchmark --case-set dev-30-v1 --precision bf16-mixed --per-group 8
& $AppPython -m ehr_evidence_app.generation.measure --benchmark --case-set dev-30-v1 --precision nf4 --per-group 8
& $AppPython -m ehr_evidence_app.generation.measure --benchmark --case-set dev-30-v1 --task prediction_explanation --drivers 5
& $AppPython -m ehr_evidence_app.generation.measure --benchmark --case-set dev-30-v1 --per-group 1000 --output-tokens 2048 --seconds 300 --within 768 120 --within 768 180
& $AppPython -m ehr_evidence_app.generation.measure --paired --case-set dev-30-v1 --per-group 1000
```

`--within` counts the same outputs under lower output and time limits:
greedy decoding writes the same tokens under any output limit they fit,
so one run at the higher limits answers for the lower ones.

`--record`, with `--benchmark` or `--paired`, also keeps each run's
measurement as a record for the tracker: its configuration and, by
stratum, the counts, release rates, scores and measures reported above,
and nothing of a request
([experiment-tracking.md](experiment-tracking.md#measurements);
[ADR 0018](decisions/0018-tracker-as-experiment-record.md)). It keeps one
`--within`, and the tracked files must be as committed before and after
the run, so that the record names the commit that ran:

```powershell
& $AppPython -m ehr_evidence_app.generation.measure --benchmark --case-set dev-30-v1 --per-group 1000 --record
```
