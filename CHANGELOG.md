# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html); 0.y releases may
change interfaces.

## [Unreleased]

### Added

- The final evaluation's figures
  ([docs/final-figures.md](docs/final-figures.md)): the version assessed
  and the run, the paired comparison by stratum with every metric of
  ADR 0016, outcomes, failures and operations, the prose rubric and the
  owner's check, the questions typed end to end, the held-out read of
  MIMIC-IV-Ext-Instr, intervals recomputed by patient as a check added
  after the figures, how to read them, and the record of the protocol.

## [0.11.2] - 2026-10-04

The frozen version of the final evaluation, in place of 0.11.1, whose
freeze was superseded before any result of the final case set was
written ([ADR 0029](docs/decisions/0029-the-freeze.md), amendments).

### Fixed

- The final run, the scoring of a run and its rating read the final case
  set's explanation bundles when the caller has checked the freeze. The
  reader refused every case set but a development one, so the final run
  stopped while it built its requests, before it wrote anything.

### Changed

- The freeze record is `freeze-v3` and the final question set `final-v2`;
  `freeze-v2` and the set registered under it, `final-v1`, are kept and
  no longer read.

## [0.11.1] - 2026-10-04

The frozen version of the final evaluation, in place of 0.11.0, whose
freeze was superseded before anything of the final case set was read
([ADR 0029](docs/decisions/0029-the-freeze.md), amendment).

### Fixed

- The end-to-end assessment answers condition questions with the case
  set's whole record in every mode, as the deployed application does. It
  answered them without it, so a question that the rules for questions
  about the whole record answer was counted as refused. On `written-v4`,
  45 of 226 questions end at other stages, 17 refusals and 4
  clarifications among them answered as questions about the record.
- No test reads the workspace's freeze record or final question sets:
  the freeze's directories are read when called, and a test-wide
  setting points them at a directory that does not exist. The tests of
  the final modes read the workspace's record, so on a frozen checkout
  they could have passed the gate.

### Changed

- The freeze record is `freeze-v2`; the record of 0.11.0, `freeze-v1`,
  is kept and no longer read.

## [0.11.0] - 2026-10-04

The frozen version of the final evaluation (Phase 9, work items 53 to 61,
and the freeze of work item 64). The `assessment` package builds the
final evaluation as [ADR 0028](docs/decisions/0028-final-evaluation-design.md)
designs it: the downstream partition, the independent references, the
baseline condition, the paired runner, the metrics with their paired
comparison, the scoring of a run, the end-to-end assessment of questions
about conditions and about the whole record, the prose rubric rated by
code and by a frozen automatic rater
([ADR 0027](docs/decisions/0027-automatic-prose-rating.md)), and the
held-out read of MIMIC-IV-Ext-Instr. Each was checked on invented data
and the development case sets, the runner and the end-to-end assessment
in trials with the model. The freeze
([ADR 0029](docs/decisions/0029-the-freeze.md)) records this version, and
the final case set is read only under it. Questions about the whole
record read an item's name whole (rules `record-questions-v6`).

### Added

- The final evaluation's package, `assessment` (Phase 9;
  [ADR 0028](docs/decisions/0028-final-evaluation-design.md),
  [docs/assessment.md](docs/assessment.md)):
  - the downstream partition record of the case sets,
    `downstream-partition-v1`: 40 development persons and 100 final ones,
    none in both, and every final case unseen by the GNN;
  - the paired comparison's statistics: Newcombe's method 10, the exact
    McNemar test and the paired bootstrap over cases;
  - the metrics of ADR 0016 over plain references and responses, each
    worked by hand. A disclosure left out counts as an unsupported
    statement. The metrics are checked against the release gate's
    mutation suite: every template and control scores as correct, and
    every mutant withheld for what it states as faulty;
  - the reading of what each condition wrote for the metrics, a cited
    reference the bundle does not hold naming nothing;
  - the independent references (work items 54 and 55): condition lists
    and presence checks from the development suite's oracle, and
    explanations from the published output, closure and linkage
    records. They agree with all 612 template answers and explanations
    of the development case sets;
  - the baseline condition (work item 56): the same requests without
    the prompt's grounding rules 1, 4 and 5, the sufficiency checks and
    the release gate;
  - the paired runner (work item 57): each request written once in
    each condition, one protected record per request, a fallback
    counted as the model's failure. Its 20 invented challenges each
    end as they should;
  - the scoring of a run against the independent references, by
    stratum, with the paired comparison of every metric, each record
    paired under its request's identifier and its number in the run. A
    development trial with the pinned model wrote nine requests of the
    development set in both conditions and scored end to end;
  - the end-to-end assessment of questions (work item 60), stage by
    stage: interpretation, resolution, retrieval and delivery. On a
    development written set it agrees with the written test on every
    condition question, and it can write the answers with the pinned
    model as the deployed application does. Questions about the whole
    record have a format of their own, naming the item meant as the
    references do, and are judged against the whole record's references;
  - the freeze ([ADR 0029](docs/decisions/0029-the-freeze.md)): one
    protected record of the commit, the versions and the digests the
    results rest on, and of the rating model; every operator mode that
    reads the final case set refuses code or configuration other than
    the frozen version's, and the final question sets are registered by
    digest before they are read;
  - the held-out questions of MIMIC-IV-Ext-Instr read once with the
    frozen rules, each family against its expected reading under the
    rules for questions about the whole record; the reading is the
    application's on every edge, help and junk question compared;
  - a generated development suite of questions about the whole record
    (ADR 0016, decision 11): fixed wordings about every kind, naming
    each scope's own items by their records' names, assessed end to
    end. On the development case sets 10,338 of its 11,053 questions
    are read right and 10,320 delivered correctly;
  - the prose rubric of ADR 0027: identity, evidence and history judged
    by code; attribution wording marked by a rating model on the
    workstation, checked on planted faults in two forms per class; and
    the owner's blind check of 30 texts. The rater's instructions were
    revised on development texts and frozen twice, their digest pinned.
    `prose-rubric-v2`, on wordings held out from its revision, failed
    none of 40 passing texts and caught 237 of 320 planted faults,
    missing most paraphrases in explanations. `prose-rubric-v4`, revised
    on both seen sets and checked once on a third, fresh set, failed none
    of 40 passing texts and caught 274 of 320, 148 of the 160
    paraphrases. The operator rates a finished run with the frozen
    instructions and writes the owner's blind sheet, whose marks a
    second step reads back into an agreement record;
  - the references of questions about the whole record (ADR 0016,
    decision 11), from the raw extract and the snapshot. They agree with
    all 14,800 structured template answers of the development case
    sets.
- `/help` in the conversation
  ([docs/interface.md](docs/interface.md#the-interface)): that the
  questions are not a fixed list but must ask for a kind of information
  it shows, then each kind with example questions that go into the box,
  each read by the rules as its kind's question and answered on the
  invented record; any other word after a slash is not a command. The
  welcome, and every response that asks back or declines, name `/help`.

### Changed

- The final evaluation's prose rubric
  ([ADR 0027](docs/decisions/0027-automatic-prose-rating.md), amending
  decision 9 of ADR 0016) is rated without a person reading every text:
  identity, evidence and history by code against the references, and
  attribution wording by an automatic rater that is not the evaluated
  model, checked on planted faults in development texts and by the owner
  on 30 texts. Every explanation and every answer to a condition question
  is rated, instead of a sample of answers.
- A response no longer names its writer, the model or a template, on the
  page; its runtime record still does
  ([docs/interface.md](docs/interface.md#the-interface)).
- Rules `record-answer-v4`: an answer names a medication without a named
  concept by the drug its prescription names, as written, without
  "(as written; no standard concept)" after it.
- The page's accent is green (#6BBF8A) instead of orange, the mark and
  the backdrop's glow included; the buttons filled with it carry dark
  icons, which stand out from it where white would not.
- The tunnel's set-up follows Cloudflare's current dashboard
  ([docs/interface.md](docs/interface.md#sharing-through-the-tunnel)):
  the token is saved without running the install command, which would
  install cloudflared as a service that always runs; the route is a
  published application on a one-level subdomain; Always Use HTTPS sends
  `http://` addresses on to `https://`; and the link reaches the
  supervisor privately, in an issue or a direct message.

### Fixed

- Rules `record-questions-v6`
  ([docs/qa.md](docs/qa.md#names-read-whole)): a question about the
  record reads an item's name whole. Words the reading sets aside, a
  kind's word ("disease", "culture") or a harmless one ("of", "with"),
  were taken out of names too, so that "When was chronic kidney disease
  first recorded?" was asked back, "anxiety disorder" was read as
  anxiety, and a condition named with a word that points to another kind
  was read as a medication or a procedure and could be answered "not
  recorded" though recorded. Of the development cases' condition names
  asked about in every scope, 2,614 of 2,638 are now read whole, where
  1,399 were, and none is answered "not recorded" while recorded, where
  46 were. "All ... values" is read around a name of up to eight words
  with digits ("all the hemoglobin a1c values").
- The paired interval of the development benchmark
  (`generation.measure --paired`) was Newcombe's method 8, not method 10
  as documented. It lacked the continuity correction of the pairs'
  correlation, so its intervals were slightly narrow. It now comes from
  `assessment.statistics`, checked against a published worked example,
  and the precision comparison's intervals are recomputed from the
  recorded pairs: for all 430 requests, -33.0 points, from -37.4 to
  -28.3 where it was -28.4. No conclusion changes.
- Rules `record-questions-v4` and `record-answer-v3`
  ([ADR 0026](docs/decisions/0026-what-a-medication-was-given-for.md);
  [docs/qa.md](docs/qa.md#questions-about-the-whole-record)): a question
  about what medications were given for a condition, a test or a vital
  sign ("what is he taking for his seizures", "seizure medications") gets
  a clarification that the record does not say, and asks about a
  medication by name or a drug class instead (the notice
  `indication_unrecorded`). It was read as a question about a medication
  named after the condition, or, without a kind's word, answered as a
  question about the condition or a vital sign of the same name.
- Rules `record-resolution-v3`: a class word or class name in the
  singular ("an antiepileptic", "a blood thinner") resolves as its plural
  does; it was not found. A fixed class word that the vocabulary also
  names stands for both: "antibiotics" stood only for the ATC classes
  named Antibiotics, local antibiotics of the eye, the skin, the gut and
  the like, and missed the systemic antibacterials, so that "Any
  antibiotics?" could be not recorded for a patient given intravenous
  antibiotics; "antivirals" and "corticosteroids" missed their systemic
  classes the same way.
- Rules `qa-answer-v6` and `record-questions-v5`: a presence question
  whose phrase is a fixed word for a class of medications ("any nsaids")
  is read as one about medications. The condition vocabulary calls
  "Non-steroidal anti-inflammatory overdose" NSAIDs, and "Any nsaids?" was
  answered as whether that overdose is recorded. A class word that is
  also another kind of record's name ("chemotherapy") still asks which is
  meant.

## [0.10.0] - 2026-09-30

API and interface (Phase 8, work items 47 to 52.9). A local API serves the
development case sets behind its access boundary
([ADR 0019](docs/decisions/0019-api-and-interface.md)), and the interface
is one page over it: a conversation about a selected patient, with the
evidence beside it
([ADR 0020](docs/decisions/0020-interface-as-one-page.md)). The whole
record can be asked about, answered by the templates and checked by the
release gate ([ADR 0021](docs/decisions/0021-the-whole-record.md)), and is
tested at its edges, against itself, with compound mistakes and with junk
input ([ADR 0022](docs/decisions/0022-record-edge-cases.md),
[ADR 0023](docs/decisions/0023-consistency-and-junk-input.md)). The
explanation says in plain words what the GNN model's predicted diagnosis
is, where it applies and whether it is recorded for the final admission
([ADR 0025](docs/decisions/0025-prediction-against-the-record.md)). The
model is evaluated as pinned, without fine-tuning
([ADR 0024](docs/decisions/0024-no-fine-tuning.md)). The tracker becomes
the project's experiment record
([ADR 0018](docs/decisions/0018-tracker-as-experiment-record.md)); no data
is uploaded. Sharing through the tunnel is in place; the link waits for
the owner's Cloudflare parts.

### Added

- The whole record can be asked about
  ([ADR 0021](docs/decisions/0021-the-whole-record.md);
  [docs/qa.md](docs/qa.md#questions-about-the-whole-record)):
  medications, procedures, laboratory results, microbiology, vital signs,
  transfers and ICU stays, the visits and the person, in lists, presence
  checks, times, counts, values, flagged results and details, each
  answered by templates from the case's record bundle for the selected
  scope and checked by the release gate. `sources.mimic` extracts the case
  sets' raw MIMIC-IV 3.1 records from pinned files
  ([docs/source-data.md](docs/source-data.md#raw-mimic-iv-records));
  `qa.record_vocabulary` builds the medication and procedure vocabulary
  from the pinned Athena release; `evidence.record` and
  `evidence.record_bundles` hold the whole record of each case and scope
  ([docs/evidence.md](docs/evidence.md#record-bundles));
  `qa.record_interpret`, `qa.record_resolve` and `qa.record_answer`
  read, resolve and answer the questions; `validation.record_text` and
  `validation.record_support` show their facts and check their support
  ([docs/validation.md](docs/validation.md)). The evidence panel shows the
  person, each visit's admission and every kind of record.
- The whole record tested at its edges
  ([ADR 0022](docs/decisions/0022-record-edge-cases.md);
  [docs/qa.md](docs/qa.md#edge-cases-tested)): an invented record built
  for its edges (`tests/qa/record_cases.py`), every one of 135 questions
  in every scope, the answers' checks broken one fact at a time (1,479
  mutants, `tests/validation/test_record_mutations.py`), a randomised
  test of the raw-file reader at every chunk size, and the builder, the
  vocabulary, the rendering, the bundle cache under threads and the
  evidence panel at their edges; the same questions and mutants on the
  development case sets.
- Tests of answers against each other, of compound mistakes and of
  junk input
  ([ADR 0023](docs/decisions/0023-consistency-and-junk-input.md)):
  `tests/qa/test_consistency.py` asks every question in 13 rewordings
  and checks that a larger scope never finds less;
  `tests/validation/test_consistent_mistakes.py` checks that the gate
  withholds answers with two mistakes, answers written from a copy of
  the record with one record misread (so that their counts and
  references agree with each other), and the right answer to another
  question, scope or condition, for answers about the record and for
  condition lists, presence checks and explanations;
  `tests/qa/test_junk_input.py` and `tests/api/test_junk_requests.py`
  ask junk questions of every kind, with protected records written, and
  send malformed requests over HTTP. The same checks ran on the
  development case sets with aggregate output only.
- Record vocabulary version 2 (`record-vocabulary-athena-20260831-v2`):
  each ATC class's ingredients from the pinned Athena hierarchy, every
  version 1 file unchanged byte for byte. A medication without a
  standard concept (45% of the development records) is now found by a
  drug class through the written names of the class's ingredients.
- A "not recorded" about a named condition, medication or procedure
  states how many records of its kind in the scope have no standard
  concept and how they were checked (`record_unmapped`).
- Runtime records name the rule versions of answers about the record.
- `api` package (Phase 8, work items 47 and 48;
  [docs/interface.md](docs/interface.md)): `api.service` answers
  questions about a selected case and scope through `qa.answer`, explains
  a case's prepared prediction through `validation.explanation` and shows
  the evidence of a scope or of the prediction without native
  identifiers, for `dev-30-v1` and `pilot-10-v1` only; each response is
  the gate's, with who wrote it and the seconds it took, and writes its
  protected runtime record. The model loads in the background, the
  templates writing until it is ready, and writes one request at a time.
  `api.app` serves it as a FastAPI application; `api.boundary` keeps it
  on 127.0.0.1 behind a launch token, a Host check and a bounded body,
  refuses cross-origin requests, marks every response not to be cached,
  answers failures with bare codes and logs routes only. The default mode
  checks every route and the boundary over a real loopback connection on
  the invented case set.
- `interface` package (Phase 8, work items 48 to 52;
  [docs/interface.md](docs/interface.md#the-interface)): one page, HTML,
  CSS and JavaScript without a build step or a library, served by the
  interface's own process (`interface.app`, `interface.serve`). The
  sidebar selects the cohort and the patient, shown by a pseudonym beside
  the case's number (`interface.names`); the conversation asks questions
  about the selected case in the selected scope and explains its prepared
  prediction; a panel beside it shows the evidence. Each response shows
  its outcome, its text with each claim's references at the line that
  states it, its scope, who wrote it and the seconds it took; a case
  change starts a new conversation, and a scope change greys the earlier
  responses, labelled with their scope. The explanation shows the
  prediction with its score's fixed meaning, never a percentage, the note
  that attribution is unsigned and not causal, and the recorded
  conditions apart. The evidence: a scope's coverage, visits, records and
  provenance, or every original attribution with its link status. A
  question or an explanation runs as a job the page asks after
  (`interface.jobs`), so that nothing waits on the model; what the page
  shows is shaped and worded in `interface.views`. `interface.client`
  reaches the API on 127.0.0.1 only; `interface.gate` checks the host,
  admits changes from the page's own origin only, refuses websockets,
  sets a content security policy that admits the page's own files only,
  switches the server's access log off and, when the interface is shared,
  admits the tunnel's requests only with the secret link's key or its
  session cookie. The default mode of `interface.app` makes every
  operation of the page over loopback on the invented case set. The page
  is glass panels over a soft backdrop, frostier where less transparency
  is asked for; its menus group the scopes with their visits' dates; the
  evidence panel is resized by its edge; labels, names and the names of
  concepts are in title case.
- `api.launch`: starts the API and the interface on 127.0.0.1 with a fresh
  launch token, and stops both, the model worker included, on Ctrl+C or
  when it ends in any way; each process watches for the launcher by
  polling its standard input's pipe, never reading it, so that no native
  library's start-up waits on the pipe; the invented case set by default,
  the development case sets with `--operator-real`. `--share` also starts the
  Cloudflare tunnel's connector with its token file and checks that the
  public address answers as the gate does.
- `interface.share`
  ([docs/interface.md](docs/interface.md#sharing-through-the-tunnel)):
  `--new-link HOST` makes a new secret link, keeping only its key's
  digest and printing the link once; `--check` checks the link's record,
  the tunnel's token file (by `lstat` only) and cloudflared.
- `tracking.measurements`
  ([docs/experiment-tracking.md](docs/experiment-tracking.md#measurements)):
  each benchmark run of the generation is kept as a record in the
  workspace, written once and named by a digest of its content, holding
  its configuration fields and, by stratum, its counts, release rates,
  scores and measures, and is sent once as a completed, visible task,
  `Generation measurement`, named by its case set, precision, prompt
  version and commit; an index keeps the task of each record.
  `--operator-real` checks every task offline and `--operator-remote`
  sends the records not yet sent.
- `config/measurement_history.json`: the 17 Phase 7 runs since ADR 0014,
  each at its commit, as the execution log records their aggregates; they
  are sent as records marked backfilled, and the development result of the
  configuration is marked assembled.
- `generation.measure --record`: keeps the measurement of a benchmark or
  paired run, refusing tracked files that differ from the commit before
  or after the run.
- `tracking.models`
  ([docs/experiment-tracking.md](docs/experiment-tracking.md#the-model)):
  checks the pinned MedGemma 1.5 4B IT files against
  `config/generation.json`, stages the licence's notice, the owner's copy
  of the agreement and a manifest of every file, uploads them to the
  tracker's files server and registers the revision once in the model
  registry, with its tags and metadata.
- The controlled wrapper's task kinds, names from checked configuration
  fields, configuration fields and scalar metrics, each from a fixed
  allowlist (`tracking.controlled`).

### Added

- The predicted diagnosis against the record
  ([ADR 0025](docs/decisions/0025-prediction-against-the-record.md)):
  the page says whether the predicted diagnosis is recorded for the final
  admission (the diagnosis itself, under an ICD code whose description
  names it, or only as a more specific diagnosis), from the API's check
  of that visit's records with the condition rules' matching. It stands
  apart from the explanation and never reaches the language model.

### Changed

- The page, polished: replies in the interface's own typeface at a
  regular weight; one marker per list item, and a line's references kept
  together in quieter tags; the prediction card with its score and logit
  in its header, In the Record as one tinted line and its fine print
  under a rule; the notes set apart; a shorter welcome, with the
  suggestions that repeat the composer's and the top bar's buttons shown
  only where those show icons alone. The welcome's title is always two
  lines, its description two even sentences, and its four suggestions,
  short labels for the same questions, one row as wide as the composer
  (two by two where the dock is narrow).
- No fine-tuning ([ADR 0024](docs/decisions/0024-no-fine-tuning.md)):
  the pinned model is evaluated in its frozen configuration, and the
  training and validation partitions of MIMIC-IV-Ext-Instr are not used.
- The explanation on the page says what it is for, in plain words: the
  button is Generate the Predicted Diagnosis; the reply opens with what
  the prediction is (the GNN model's prediction of a diagnosis recorded
  for the final admission, not a diagnosis of the patient), shows where
  it applies (the final admission, after the earlier visits, with their
  dates), gives the score in plain words beside its fixed meaning, keeps
  the response's fixed notes apart
  under Notes without repeating the one on attribution, and ends with How
  to Read This, a glossary of its parts. The response itself, the model,
  the prompt and the gate are unchanged.
- Rules `question-interpretation-v5` and `record-questions-v3`: a
  polite opening ("please", "can you tell me", "I'd like to know", "do
  you know if") is removed before a question is read, and a statement
  that something was given, measured or done ("heparin was given?") is
  read as its question form is, not as a condition name.
- A list of ICU stays or transfers must be in time order to pass the
  gate, as the templates write it.
- A request the web framework refuses (a body that cannot be read, an
  unknown route or method) is answered by the API with a bare code and
  by the interface with a code and its meaning, as every other refusal.
- Rules `record-questions-v2`: a negation asks the same presence (a
  filter in a question about a whole kind); two items at once, flags a
  kind does not carry, and a day or the ICU named as a time get a
  clarification; flags are read alike for an item and a whole kind; the
  doses of the medications; "male or female"; verb forms of procedures;
  suggestions for misspellings of the kind a question's verbs name.
- Rules `record-resolution-v2`: whole-word matching of written drug
  names, salts apart; word matches named "the words of its name";
  spelling suggestions for medications and procedures.
- Rules `record-answer-v2`: a value list names its item by its earliest
  record and lists every result of a microbiology test; a removed value
  has no unit; a discharge before the admission gives no length; numbers
  are shown with every recorded digit and no exponent; a record without
  a name says whether it has no standard concept or its name is
  unavailable.
- Checks: every notice is tied to its tasks and every answer must hold
  the notices its task and outcome require; a record answer's notices
  are exactly its question's; a single record is the only one; the
  latest stands in for the highest or lowest only without numbers; a
  field must be of its statement's kind; an empty list states nothing
  else. Wording rules `wording-rules-v5` read record statements with
  their kinds. The condition development suite and the validation
  mutation suite give the same results as before.
- `sources.mimic` refuses a blank line in any table;
  `evidence.record_bundles` refuses two visits of one admission and
  identifiers that are not ASCII digits (the published development
  bundles rebuild byte for byte); `qa.record_answer` refuses a bundle
  file that is not the case and scope its manifest names.
- A question the condition rules refuse as being about other data, times
  or counts is read as a question about the whole record; every question
  about conditions gets the response it got before. The refusals'
  approved texts say what the application now answers.
- Responses cite records of the whole record as R and the person as D1,
  and an answer about the whole record may hold up to 1,000 claims and
  60,000 characters; the committed JSON Schemas add the record bundle's.
- ADR 0016 gains decision 11: questions about the whole record are
  strata of their own in the final evaluation, agreed before any request
  of `eval-100-v1` was written.
- The application's dependencies no longer include Streamlit: the lock
  pins 40 packages instead of 56, and Starlette, Requests and NumPy,
  which the code imports, are declared directly
  ([docs/environments.md](docs/environments.md)).
- The publication audit admits HTML, CSS, JavaScript and SVG in the
  interface's page directory only.
- The inherited input registration and the runtime aggregates are no
  longer archived: each task is read back and marked completed, and the
  aggregates task carries its configuration fields and scalars. Only the
  controlled probe is archived.
- The tracker policy and the connection declaration list the one model
  whose files may be uploaded, and why; models trained on the project's
  data stay excluded.
- `tracking.registration` writes
  `outputs\lineage\inherited-input-registration-v2.json`. Version 1 was
  written before the workspace moved to G: and names the baseline at its
  former path, so the rebuilt manifest differed from it
  (`registration_conflict`); it stays as written, pinned by the model
  provenance record.

### Fixed

- A condition question with a polite opening ("Please, which conditions
  are recorded?", "Can you tell me which conditions are recorded?") was
  read as a question about the whole record: answered by the templates
  under another task, never by the model, or not understood.
- A question holding an unpaired surrogate raised an error in the
  service; it is read, and recorded, with the replacement character.
- A condition question the condition rules could not resolve ("is
  diabetes recorded?") was answered from the whole record when the words
  of procedure names held its phrase, as "not recorded"; words alone no
  longer take a question that names no kind of record, and every
  condition question gets the response it gets without the record.
- "Were any vitals abnormal?" (a flag a kind does not carry, asked of a
  whole kind) raised an error; it now gets a clarification.
- A condition answer could carry a notice of the answers about the whole
  record in place of its own ("not recorded" without the note that it is
  not clinical absence) and pass the checks.
- A value list of an item whose visits' order differs from its records'
  time order was named by another record than its check expected, and
  would have been withheld.
- `tracking.models` measures each upload as the files server holds it:
  the file is asked for without content coding, so that a reply the
  server compresses is not measured by its compressed length, and a reply
  compressed anyway is counted from its decoded bytes, for a small file
  only. An upload found incomplete reports both sizes.

## [0.9.0] - 2026-09-28

LLM and operations (Phase 7, work items 41 to 46.1): MedGemma 1.5 4B IT
writes the complete candidate of every response from evidence, from a
deterministic view of that evidence structured for its task, and the
templates are its fallback; the release gate checks every candidate. With
BF16 on the development laptop, K = 5 and the frozen limits, the model's
candidates are released for 385 of the 430 development requests and 161
of the 182 pilot requests. Every response writes a protected runtime
record, the tracker receives aggregates only, and the metrics of the
final evaluation are fixed before it.

### Added

- `generation` package (Phase 7, work items 41 and 43;
  [ADR 0012](docs/decisions/0012-generation-design.md),
  [ADR 0013](docs/decisions/0013-compact-generation.md),
  [ADR 0014](docs/decisions/0014-structured-evidence.md),
  [docs/generation.md](docs/generation.md)): the compact JSON document a
  candidate is written as, each item a row under one wording for its
  kind, records listed before what is counted from them and every
  wording led by its subject, and each task's JSON Schema, which enforces
  the contract's syntax and nothing that depends on the evidence
  (`generation.shape`, `candidate-shape-v4`); the model's view of the
  evidence, a deterministic projection structured for its task: a list's
  records grouped by condition, a presence check's matching records with
  the coverage status, and one block per selected driver, each with the
  counts and visits derived from its records, a driver's history and
  context as the reply states them, the notice of each of the case's
  restrictions, and no outcome or name (`generation.projection`,
  `projection-v5`), from which the correct
  answer of every development request is derived; the prompt with the
  rules, worked examples on invented data that group records and
  disclose a record without a standard concept exactly when the
  request's view has one, a presence check's in an answer and in "not
  recorded", every reply the task can have and the request's projection
  (`generation.prompt`, `prompt-v9`;
  [ADR 0015](docs/decisions/0015-generation-configuration.md),
  [ADR 0017](docs/decisions/0017-bounded-prompt-pass.md));
  the
  adapter, the one path from a writer's output to a release, under the
  prompt, output, time, attempt and concurrency
  limits, which releases the template candidate with one of eleven
  recorded fallback reasons whenever the model's is not, each reason with
  its category (`limit_exceeded` for a prompt or output over its limit)
  (`generation.adapter`), with a scripted backend and scripted runs on
  the invented fixtures and the development case sets.
- `max_prompt_tokens` in the execution limits (ceiling 131,072;
  configured 16,384).
- The model runtime (work item 42): `config/generation.json` pins
  MedGemma 1.5 4B IT at revision `91850547` with every file's size and
  SHA-256, checked before the worker starts (`generation.runtime`); the
  worker runs in its own environment on G:, loads the model once in one
  of two precisions, BF16 with a mixed GPU/CPU placement (language layers
  on the GPU, image encoder and tied token embedding table in system
  memory) or 4-bit NF4 on the GPU (`worker.precision`), decodes greedily
  under each request's schema with xgrammar, and answers one request at a
  time over its standard input and output, offline (`generation.worker`);
  the operator check loads it, writes the invented requests and counts
  the prompt tokens of a development case set.
- The model environment: `requirements/llm.txt` and the hashed
  `requirements/llm.lock.txt` (45 packages; PyTorch 2.14.0 with CUDA 13.0,
  Transformers 5.17.0, xgrammar 0.2.8, llguidance 1.8.0, bitsandbytes
  0.50.2) on a uv-managed Python 3.12.13; the storage policy routes the
  model environment (`llm_env`), managed interpreters
  (`UV_PYTHON_INSTALL_DIR`) and CUDA's compute cache (`CUDA_CACHE_PATH`)
  to the runtime root.
- Driver selection for explanations (`config/explanation.json`): an
  explanation describes the eligible drivers with the highest attribution
  scores, highest first, at most a configured number (5, frozen for the
  final evaluation), and a new approved notice states
  how many more are kept in the evidence; the gate requires exactly the
  selected drivers and that number.
- Development measurement (`generation.measure`, work item 46.1): the
  prompt and output token distributions of a development case set with
  the model's tokenizer, for each candidate number of drivers; a
  benchmark of the model at one precision on a deterministic, stratified
  sample of development requests, naming by field what went wrong with
  each document, the requests withheld for counts or visits alone, and
  how many of the available history and context lines an explanation
  states; and a paired comparison of BF16 and NF4 on the same requests,
  with Newcombe's interval for the paired difference in release rate, the
  exact McNemar test and non-inferiority at a margin of 5 points. The
  benchmark names a field, notice or reason code as added or left out,
  splits lists and presence checks by whether their scope has records
  without a standard concept, and counts the same outputs under lower
  output and time limits (`--within`). It scores every document against
  the correct one with precision, recall and F1 of conditions, records,
  drivers and history and context lines, counts the right outcomes, and
  names what the rows of a withheld document are. On all 430 requests of
  `dev-30-v1` BF16 with the mixed placement released 314 and NF4 172
  (NF4 minus BF16 -33.0 points, 95% interval -37.4 to -28.4), so the
  precision stays BF16.
- Release-gate checks for the mistakes a language model can make and a
  template cannot: a refusal of a request whose bundle is bound, a
  presence answer about another condition than the one asked about
  (`scope_violated`), a notice stated twice and statements out of their
  answer's order (`structure_invalid`), and a condition named twice for
  one match kind in a presence answer (`claim_unsupported`).

- Protected runtime records (`generation.records`, work item 46):
  `qa.answer.answer` and `validation.explanation.explain` take a recorder
  that writes one record per response under the workspace's
  `protected-data\runtime-records`, never overwritten: the request, the
  evidence's digest, the versions and configuration digests, the
  response and its digest, and for a response written from evidence who
  wrote it, the fallback and its category, the measures and the model's
  output and candidate. Aggregates count requests, outcomes, authors and
  fallbacks only. The log audit names, by rule, secrets, user paths,
  patient identifiers and the content of prompts, views, documents,
  bundles and records found in the routine logs.
- Tracker aggregates of the runtime records (`tracking.aggregates`): the
  records of one case set, written under one configuration, are summed
  into a deterministic manifest under `outputs\lineage` and described by a
  controlled, metadata-only task with the application version, the
  configuration fingerprints, the prompt and projection versions and four
  aggregates; nothing else is uploaded. The controlled tracking
  allowlist admits `requests`, `model_written_rate` and `fallback_rate`.
- The metrics of the final evaluation, with their denominators and
  empty-set conventions, a prose rubric and the paired comparison, are
  fixed before Phase 9
  ([ADR 0016](docs/decisions/0016-evaluation-metrics.md)).

### Changed

- Execution limits: `max_request_seconds` is 180 (was 120), so that output
  within the 768-token limit is not ended by time at BF16's slowest
  measured rate; the prompt, output and time limits and the number of
  drivers an explanation describes are frozen for the final evaluation
  ([ADR 0015](docs/decisions/0015-generation-configuration.md)). After
  one bounded pass on the prompt, which kept a new view of explanations
  and not one of lists
  ([ADR 0017](docs/decisions/0017-bounded-prompt-pass.md)), the model's
  candidates are released for 385 of the 430 requests of `dev-30-v1` and
  161 of the 182 of `pilot-10-v1`.
- Candidate contract version 1.1: the `drivers_omitted` notice and its
  `omitted` slot; the notice introducing the drivers names them as those
  with the highest attribution scores, in order of their scores.
- Template explanation version 3 (`explanation-template-v3`): the
  selected drivers in order of their scores, and the notice on the
  drivers not described; scores and logits are checked as a response
  shows them, to three significant figures; a driver's history or context
  may be left out, and one that is stated must cite all of its records.
- Wording rules version 3 (`wording-rules-v3`): association,
  complication, interpretive, disease-course, certainty and recency
  wording everywhere, and hedged, ranked or counted facts in answers and
  explanations. Version 4 (`wording-rules-v4`) reads a wording with its
  statement's kind: a statement that something is recorded may not be
  negated but to deny a causal, diagnostic or risk reading, and a
  statement that something is not recorded must negate every
  "recorded"; the model negated a list's summary on invented data and the
  gate released it.
- Mutation suite version 4 (`validation-mutations-v4`): version 2 added
  18 classes of model-style mistakes, version 3 the classes of driver
  selection and of a statement's polarity, and version 4 scores wrong in
  their last shown figure, with controls that must be released (a
  driver's history or context left out, scores stated to the figures
  shown); on the development case sets it detects all 24,770 mutants in
  65 classes, and every original and control is released unchanged.
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

### Fixed

- Stopping the model worker joins the threads that read its output and
  closes its pipes, so none is left for the collector (the runtime tests
  raised `ResourceWarning` for them).

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

[Unreleased]: https://github.com/maa2002/ehr-evidence-app/compare/v0.11.2...HEAD
[0.11.2]: https://github.com/maa2002/ehr-evidence-app/compare/v0.11.1...v0.11.2
[0.11.1]: https://github.com/maa2002/ehr-evidence-app/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.7.1...v0.8.0
[0.7.1]: https://github.com/maa2002/ehr-evidence-app/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/maa2002/ehr-evidence-app/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/maa2002/ehr-evidence-app/tree/v0.3.0
