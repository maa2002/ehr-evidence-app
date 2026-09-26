# Natural-language QA

Phase 5 answers one question about the active case and scope from that
case's verified QA bundle. Interpretation, resolution and answers follow
fixed rules; no model is involved, so the same question about the same
bundle always gets the same response. The language model of Phase 7 may
only rephrase an answer, behind the checks of Phase 6.

Decisions are recorded in
[ADR 0006](decisions/0006-condition-vocabulary.md) (condition vocabulary,
term resolution and matching),
[ADR 0007](decisions/0007-rule-based-qa.md) (interpretation, answers and
how they are measured) and
[ADR 0008](decisions/0008-question-set-partitions.md) (partitions of the
external question set). Command examples assume the session set-up in
[development.md](development.md#session-set-up).

## Modules

| Work item | Module | Role |
| --- | --- | --- |
| 31 | `qa.interpret` | Question interpretation: task, condition phrase, out-of-scope rules |
| 32 | `qa.resolve` | Condition term resolution and record matching |
| 32.1 | `qa.vocabulary` | Condition vocabulary built from the pinned Athena release |
| 33 | `qa.scope` | Scope phrases and binding to the active scope |
| 34 | `qa.answer` | List and presence answers from a verified QA bundle |
| 34.1 | `qa.suite` | Development question suite with an independent oracle; vocabulary sweep |
| 34.2 | `qa.heldout` | Partitions of MIMIC-IV-Ext-Instr; held-out wording test |
| 34.3 | `qa.written` | Written wording test: template, case overview, scoring |

## From question to response

Every question ends in exactly one response:

1. A question longer than the execution limit is an operational error
   (`limit_exceeded`).
2. Interpretation gives a task (a condition list or a presence check), or
   ends the question with a clarification or an abstention.
3. A case without bundles is rejected (`case_not_admitted`).
4. The request is bound to the active scope; a question naming another
   scope gets a clarification.
5. The case's QA bundle for the scope is read from the pinned bundle set
   and verified against its manifest. A changed bundle stops the request;
   a scope without a bundle is rejected (`scope_invalid`).
6. Coverage that is not complete gives an abstention
   (`coverage_incomplete`).
7. The list or the presence answer is built, and its claims are checked
   against the bundle with the citation rules before release.

| Outcome | When | Reason codes |
| --- | --- | --- |
| `answer` | A list of a scope with records; a presence check with a matching record | `unmapped_records_in_scope` when the scope holds records without a standard concept |
| `not_recorded` | A list of an empty scope; a presence check with complete coverage and no match | as for `answer` |
| `clarification` | The question is unclear or needs a choice the user has to make | `task_ambiguous`, `concept_unknown`, `concept_ambiguous`, `concept_too_broad`, `scope_ambiguous`, `visit_ambiguous`, `case_ambiguous`, `prediction_selection_needed` |
| `abstention` | The request is outside the supported tasks, or coverage is incomplete | `task_unsupported`, `coverage_incomplete` |
| `case_rejection` | The case or scope has no bundle | `case_not_admitted`, `scope_invalid` |
| `operational_error` | A limit is exceeded | `limit_exceeded` |

A `not_recorded` answer always says that the condition is not recorded
within the selected scope, and that this is not a statement of clinical
absence.

## Interpretation

The question is normalised as the vocabulary is: accents removed, case
folded, "&" read as "and", possessives and symbols dropped, dots inside
codes kept. Scope phrases are set aside (see [Scope](#scope)).

- **Presence frames** give a presence check and the condition phrase:
  "is X recorded", "was X documented", "does the patient have X", "has the
  patient been diagnosed with X", "any history of X", "is there any record
  of X", "check whether X is recorded", "X is documented". The phrase runs
  to the last reporting word, so "Abnormal fetal heart beat noted before
  labor" stays whole.
- **List frames** give a list: "list the conditions", "what diagnoses are
  recorded", "what has the patient been diagnosed with", "show me the past
  medical history", "how many diagnoses were coded". "Which X diagnoses are
  recorded" checks X.
- **A short phrase on its own** (up to six words, not starting with a
  question word or a request verb such as "diagnose" or "treat") is a
  presence check.

The words outside the condition phrase are checked for requests outside
the supported tasks:

| Rule | Outcome | Examples |
| --- | --- | --- |
| Explanation of a prediction | clarification, `prediction_selection_needed` | "Why did the model predict sepsis?" |
| Another patient | clarification, `case_ambiguous` | "Is diabetes recorded for patient 12?" |
| Treatment or management | abstention, `task_unsupported` | "How should heart failure be treated?" |
| Prognosis or risk | abstention, `task_unsupported` | "Will the patient survive?" |
| A diagnosis to be made | abstention, `task_unsupported` | "What could the patient have?" |
| Other data | abstention, `task_unsupported` | "What medications is he taking?", "Show the lab results" |
| Dates, times, order or counts | abstention, `task_unsupported` | "Was AKI recorded after the surgery?", "Is diabetes documented more than twice?" |

The same words inside a condition phrase are part of condition names
("Poisoning by drug", "Cirrhosis following cardiac procedure", "Hitting
other person"). A rule matched only inside the phrase applies when the
phrase names no condition, so "Is a chest X-ray recorded?" gets the
other-data abstention while "Is plain X-ray of hand normal recorded?"
is a presence check. Data words that form a condition ("drug abuse",
"adverse drug reaction") never trigger the other-data rule.

The condition phrase comes with its candidates: the phrase as asked, then
with one question word at a time removed from its start ("a", "the",
"history of", "ICD code") and then from its end ("recorded", "in the
chart", "present"). The longest candidate that names something is
resolved, so "Cough reflex present" is a name while "Is heart failure
present?" asks about heart failure.

A question with a list request and a presence check, or with neither,
gets `task_ambiguous`. A phrase that is only a pronoun ("is it recorded")
gets `concept_unknown`. A phrase that is only a word for conditions ("are
any diagnoses recorded") asks for the list.

## Scope

A question is answered within the active case and scope chosen in the
case selector. Recognised scope phrases:

| Scope | Phrases |
| --- | --- |
| Pre-target history | "before (prior to) the final (last, latest, most recent, current) admission", "previous (prior, earlier, past) admissions", "pre-target history" |
| One visit | "visit 2", "admission number 3", "the second admission", "the final (last) admission" |
| All case visits | "in any admission", "across all admissions", "in the entire record" |

No phrase, or a phrase naming the active scope, keeps the active scope. A
phrase naming another scope, two different scopes, or a visit the case
does not have gets a clarification (`scope_ambiguous` or
`visit_ambiguous`); the scope never changes silently. "Ever" and "at any
time" are not scope phrases: the answer states its scope.

## Resolution and matching

A condition phrase resolves, by fixed rules in order, as an ICD-9-CM or
ICD-10-CM code, the name of standard condition concepts, or an English
synonym (with a singular retry and acronym synonyms). Several concepts
resolve to one only when it is an ancestor of all the others; otherwise
the phrase is ambiguous. A concept whose descendants cover more than a
quarter of all standard conditions is too broad. An unknown phrase gets
suggestions, never a guess. The rules are in
[ADR 0006](decisions/0006-condition-vocabulary.md) and `qa.resolve`.

A record matches a resolved phrase, and the answer states how:

| Match | Rule |
| --- | --- |
| exact concept | The record's standard concept is the resolved concept |
| more specific concept | It descends from the resolved concept in the SNOMED hierarchy |
| ICD code | For a code phrase, the record's ICD source code equals it |
| more specific ICD code | For a code phrase, the record's ICD source code starts with it |
| ICD description | A record without a standard concept whose ICD description contains the name or a synonym of the concept or a descendant, not negated |

## Answers

Answer text comes from fixed templates.

- **List.** Every distinct condition of the scope, with its number of
  records and its visits; records without a standard concept are listed by
  ICD code and counted. The list states that it is complete for the
  recorded OMOP condition records of the case at the recorded snapshot.
- **Presence.** Every matching record, grouped by condition and match
  kind, with its visits. With complete coverage and no match, the answer
  is `not_recorded` and cites the coverage record; when the scope holds
  records without a standard concept, it says that their ICD codes and
  descriptions were checked.

Claims cite the bundle's own references: records (`E`) for what is
recorded, the coverage record (`C1`) for what is not. A claim cites at
most 50 records, and a response carries at most 200 claims. Before
release, every response is checked against its bundle by identifier and
digest with the citation rules (`evidence.schema.citation_problems`).

## Development suite

`qa.suite` asks generated questions about every admitted case of a
development case set, in every scope, through the whole pipeline, and
scores each response against an oracle. The oracle reads neither bundles
nor the pipeline. It computes the expected response from the verified
source snapshot, the case set's visits and the vocabulary tables:

- a record belongs to a scope when its visit does;
- a list covers every record of the scope, one group per standard concept
  (per source code without one);
- a concept matches itself and its descendants; an ICD code matches source
  codes that equal it or start with it;
- matches of records without a standard concept come from the ICD
  description heuristic, which has no independent reference; they are
  counted apart and not scored.

| Family | Questions | Expected |
| --- | --- | --- |
| `list` | One list per scope | Every record, grouped |
| `presence_name`, `presence_synonym` | Concepts of the scope, by name or a synonym that names only them | Their records and those of descendants |
| `presence_ancestor` | The most specific broader concept of a recorded one | Every record it covers |
| `presence_code` | An ICD code of the scope | Records whose code equals or extends it |
| `presence_absent` | Concepts recorded elsewhere in the case set, matching nothing in the scope | `not_recorded` |
| `scope_same`, `scope_other` | A phrase naming the active scope, or another one | The answer, or `scope_ambiguous` |
| `visit_missing` | A visit the case does not have | `visit_ambiguous` |
| `unsupported`, `explanation`, `other_patient` | Fixed requests | Their abstention or clarification |
| `unknown_term`, `too_broad`, `ambiguous_term` | Invented, broad and ambiguous terms | Their clarification |

A response is correct when its outcome, reason codes, task and scope are
as expected, it returns exactly the expected records, a list names exactly
the expected groups, and its claims cite exactly the records it reports.
Questions are drawn in a fixed order from the suite version, so every run
asks the same questions. The suite prints aggregate counts and digests
only and writes nothing. It refuses the evaluation case set, which is held
for Phase 9.

Results (suite `qa-development-suite-v1`; rules
`question-interpretation-v1`, `condition-resolution-v1`, `qa-answer-v1`):

| Case set | Admitted cases | Questions | Correct | Presence records (expected, returned, correct) | Unscored description matches | Summary SHA-256 |
| --- | --- | --- | --- | --- | --- | --- |
| `dev-30-v1` | 26 | 1,550 | 1,550 | 892, 892, 892 | 21 records in 6 questions | `c5374924494c088f9eb17434e24f836ea11f46b634d3df0d8fa16f5cc4a482d9` |
| `pilot-10-v1` | 10 | 648 | 648 | 368, 368, 368 | none | `d3da7b6cecce15b247304c054984a06bbd078c07609a8fc3679b8d7cef70cacb` |

The vocabulary sweep asks about every standard condition by name in each
of the suite's seven presence wordings, and reads no patient data. Of
112,693 standard conditions, 87 share their name with another concept and
3 are too broad; for the other 112,603, all 788,221 questions resolve to
the concept asked about.

These results show that interpretation, scope binding, resolution,
matching, coverage and citations agree with an independent computation on
real records. The wording comes from the suite's own paraphrases, written
with the rules, so they say nothing about other people's wording; that is
the held-out test below. They are not a measure of clinical correctness.

## Held-out wording test

Work item 34.2 measures interpretation on questions written by others:
MIMIC-IV-Ext-Instr 1.0.0 (PhysioNet, credentialed), a dataset built to
instruction-tune language models, with question and answer pairs
generated from MIMIC-IV admissions by fixed templates and paraphrased by
GPT-3.5. It is a held-out test: the rules are never changed to fit it, and
only aggregate counts leave the machine. `qa.heldout` reads the archive,
copied unchanged into `protected-data\question-sets\mimic-iv-ext-instr`
and pinned by SHA-256, and checks every member against the archive's own
checksums.

### Partitions

Before anything was measured, one record fixed every use of the dataset
([ADR 0008](decisions/0008-question-set-partitions.md)). The archive's
split is patient-level, but its validation file holds the whole test set,
5,755 admissions are in no split (183 of their patients are test
patients), and 26 patients of our case sets are in its training split.

| Partition | Rule | Admissions | Patients | Structured questions | Note questions |
| --- | --- | --- | --- | --- | --- |
| held-out | The test admissions | 5,400 | 4,309 | 32,392 | 10,791 |
| validation | Validation admissions outside the test set | 572 | 473 | 3,431 | 1,143 |
| training | Training admissions | 47,749 | 38,273 | 286,401 | 95,462 |

Admissions in no split are excluded, and the 37 training admissions of
patients in our case sets are removed. Only the held-out partition is
read here; validation and training stay untouched until the Phase 7 and
Phase 9 plans decide their use, for example for a fine-tuned comparison
model. Record `mimic-iv-ext-instr-partitions-v1`: 485,743 bytes, SHA-256
`4bfa37dd40235d5cbee6d3608ea7e20c20e17bbc26821c0b05186f9a14144f95`.

### What is tested

The structured questions come from public templates. The diagnosis
questions come from four list templates: all billed diagnoses, the first,
the top three and the top five; none asks whether one condition is
recorded. The other event types ask about laboratory results,
microbiology, prescriptions, procedures, transfers, demographics and
admission details. So:

- an all-diagnoses question must be read as a condition list;
- a ranking question must not be answered: billing order is not in the
  evidence, and an unordered list would not answer it;
- any other question must not be answered.

A diagnosis question counts as a ranking question when it holds a ranking
word ("first", "top", "primary", "three", "five" and similar). 25.4% of
them hold none, in line with the one-in-four draw of the all-diagnoses
template.

### Results

Test `instr-held-out-wording-v1` with the rules of release 0.6.0
(summary SHA-256
`f15451d156f55db342d1e142a2db9557499bf09bfc27aca24e3177fe91e82a93`):

| Questions | Number | As expected | Readings |
| --- | --- | --- | --- |
| All diagnoses | 1,056 | 886 (83.9%) | 886 lists; 170 clarifications (96 unrecognised, 74 unknown condition phrases) |
| Ranking of diagnoses | 3,101 | 2,114 (68.2%) | 2,114 clarifications; 987 answered with an unranked list |
| Other data (seven event types) | 28,235 | 28,235 (100%) | 15,367 abstentions, 12,868 clarifications |
| All | 32,392 | 31,235 (96.4%) | 987 answered out of scope, all ranking questions |

No question about other data was answered as a condition question. The
all-diagnoses questions that were not read as lists ended in a
clarification, never in a wrong answer. The fault is in ranking questions
that also use a list word ("list the top three diagnoses"): the ranking is
ignored and the whole list is returned.

## Written wording test

Work item 34.3 (open) measures presence-check wording, which the dataset
above does not contain: about forty questions written in their own words
by someone who has not read the [Interpretation](#interpretation) rules,
about development cases, scored once with `qa.written`. The questions
file is `protected-data\question-sets\written-v1\questions.csv` (UTF-8,
opens in a spreadsheet), one row per question:

| Column | Content |
| --- | --- |
| `id` | A unique label, such as `q01` |
| `case_set` | `dev-30-v1` (or `pilot-10-v1`) |
| `case` | The case number |
| `scope` | The scope imagined as selected: `history`, `all` or `visit N` |
| `question` | The question, as it would be typed |
| `intent` | `list` (the recorded conditions), `presence` (whether one condition is recorded) or `refuse` (something the application should not answer, such as a treatment or a laboratory value) |
| `concept` | For `presence`, the OMOP concept ID of the condition meant; empty otherwise |

The overview `case-overview-dev-30-v1.csv` in the same folder lists each
case's recorded conditions by scope with their concept IDs; concepts that
are not recorded can be looked up on Athena. Questions should be worded
freely (abbreviations, everyday terms, typing as usual) rather than
copying names. A useful mix is about ten lists, twenty presence checks
(about half recorded) and ten requests to refuse, over different scopes.

The score compares every response with the development suite's oracle. A
list is correct when it cites exactly the scope's records. A presence
check is understood when the resolved concept is the concept meant, an
ancestor or a descendant of it, and exact when the answer also returns
exactly the records of the concept meant. A refusal is correct when the
response is a clarification or an abstention. The file's SHA-256 is
recorded with the result.

## Limits

- One condition per question. Dates, times, the order of events and
  counts are not answered, but a ranking question that also asks for a
  list ("list the top three diagnoses") is answered with the whole
  unranked list (see the held-out results).
- Negated questions ("is heart failure not recorded") and unusual wording
  get a clarification rather than a guess.
- Answers report recorded condition records, never clinical status,
  severity or current state.
- ICD description matches of records without a standard concept are a
  heuristic; they are labelled as such in every answer.

## Commands

```powershell
& $AppPython -m ehr_evidence_app.qa.vocabulary
& $AppPython -m ehr_evidence_app.qa.vocabulary --operator-real
& $AppPython -m ehr_evidence_app.qa.suite
& $AppPython -m ehr_evidence_app.qa.suite --operator-real --vocabulary-sweep
& $AppPython -m ehr_evidence_app.qa.suite --operator-real --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.qa.suite --operator-real --case-set pilot-10-v1
& $AppPython -m ehr_evidence_app.qa.heldout
& $AppPython -m ehr_evidence_app.qa.heldout --operator-real --partition
& $AppPython -m ehr_evidence_app.qa.heldout --operator-real --held-out
& $AppPython -m ehr_evidence_app.qa.written
& $AppPython -m ehr_evidence_app.qa.written --operator-real --template
& $AppPython -m ehr_evidence_app.qa.written --operator-real --overview dev-30-v1
& $AppPython -m ehr_evidence_app.qa.written --operator-real --score
```

The vocabulary build and the partition record each write one new
protected directory and refuse an existing one; the template and the
overview write one new protected file each. The suite, the sweep, the
held-out test and the score write nothing.
