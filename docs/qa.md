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
how they are measured),
[ADR 0008](decisions/0008-question-set-partitions.md) (partitions of the
external question set),
[ADR 0009](decisions/0009-rules-version-2.md) (rules version 2) and
[ADR 0010](decisions/0010-fail-safes.md) (fail-safes, rules versions 3
and 4). Command
examples assume the session set-up in
[development.md](development.md#session-set-up).

## Modules

| Work item | Module | Role |
| --- | --- | --- |
| 31 | `qa.interpret` | Question interpretation: task, condition phrase, out-of-scope rules |
| 32 | `qa.resolve` | Condition term resolution and record matching |
| 32.1 | `qa.vocabulary` | Condition vocabulary built from the pinned Athena release |
| 33 | `qa.scope` | Scope phrases and binding to the active scope |
| 34 | `qa.answer` | List and presence answers from a verified QA bundle |
| 36 | `qa.templates` | Answers, refusals and clarifying questions written as candidates |
| 34.1 | `qa.suite` | Development question suite with an independent oracle; vocabulary sweep |
| 34.2 | `qa.heldout` | Partitions of MIMIC-IV-Ext-Instr; held-out wording test |
| 34.3 | `qa.written` | Written wording test: template, case overview, scoring by intent and family |

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
6. The bundle is checked before any text is written: one that does not
   fit the request is rejected, and coverage that is not complete gives
   an abstention (`coverage_incomplete`).
7. The list or the presence answer is written as a candidate, checked
   and rendered; a candidate that fails a check is a validation
   rejection. Refusals and clarifying questions are written and checked
   the same way.

| Outcome | When | Reason codes |
| --- | --- | --- |
| `answer` | A list of a scope with records; a presence check with a matching record | `unmapped_records_in_scope` when the scope holds records without a standard concept |
| `not_recorded` | A list of an empty scope; a presence check with complete coverage and no match | as for `answer` |
| `clarification` | The question is unclear or needs a choice the user has to make | `task_ambiguous`, `concept_unknown`, `concept_ambiguous`, `concept_too_broad`, `scope_ambiguous`, `visit_ambiguous`, `case_ambiguous`, `prediction_selection_needed` |
| `abstention` | The request is outside the supported tasks, or coverage is incomplete | `task_unsupported`, `coverage_incomplete` |
| `case_rejection` | The case or scope has no bundle, or its bundle does not fit the request | `case_not_admitted`, `scope_invalid`, `identity_inconsistent`, `history_inconsistent`, `evidence_inconsistent` |
| `validation_rejection` | A candidate fails a check | `structure_invalid`, `reference_unresolved`, `scope_violated`, `claim_unsupported`, `prohibited_wording`, `date_unsupported` |
| `operational_error` | A limit is exceeded | `limit_exceeded` |

A `not_recorded` answer always says that the condition is not recorded
within the selected scope, and that this is not a statement of clinical
absence.

## Interpretation

The question is normalised as the vocabulary is: accents removed, case
folded, "&" read as "and", possessives and symbols dropped, dots inside
codes kept. Scope phrases are set aside (see [Scope](#scope)). This
section describes rules version 4 (`question-interpretation-v4`);
release 0.6.0 had version 1 and release 0.7.0 version 2, and version 3
was measured but not released.

- **Presence frames** give a presence check and the condition phrase:
  "is X recorded", "was X documented", "does the patient have X", "has the
  patient been diagnosed with X", "any history of X", "is there any record
  of X", "check whether X is recorded", "hx of X", "is X on her problem
  list", "is X among them", "X is documented", "was the patient ever
  diagnosed with X". The phrase runs to the last reporting word, so
  "Abnormal fetal heart beat noted before labor" stays whole. Negative
  wordings ("is there no record of X", "is X not recorded", "does the
  patient not have X", "X or not") check X, and the answer states
  whether X is recorded.
- **List frames** give a list: "list the conditions", "what diagnoses are
  recorded", "what has the patient been diagnosed with", "show me the past
  medical history", "how many diagnoses were coded", "what is on her
  problem list", "show me the PMH", "give me a list of her diagnoses".
  "Which X diagnoses are recorded" checks X.
- **A short phrase on its own** (up to six words, not starting with a
  question word or a request verb such as "diagnose" or "treat") is a
  presence check.

The words outside the condition phrase are checked for requests outside
the supported tasks:

| Rule | Outcome | Examples |
| --- | --- | --- |
| Explanation of a prediction | clarification, `prediction_selection_needed` | "Why did the model predict sepsis?" |
| Another patient | clarification, `case_ambiguous` | "Is diabetes recorded for patient 12?", "Does the patient in bed 4 have COPD?" |
| Treatment or management | abstention, `task_unsupported` | "How should heart failure be treated?" |
| Prognosis or risk | abstention, `task_unsupported` | "Will the patient survive?", "Will her kidney disease get worse?" |
| A diagnosis to be made | abstention, `task_unsupported` | "What could the patient have?" |
| Other data | abstention, `task_unsupported` | "What medications is he taking?", "Show the lab results", "Did she undergo surgery?" |
| Dates, times, order or counts | abstention, `task_unsupported` | "Was AKI recorded after the transfer?", "Is diabetes documented more than twice?" |
| A ranking of conditions | abstention, `task_unsupported` | "What were the top three diagnoses?", "What is her main diagnosis?" |
| Only some of the conditions | abstention, `task_unsupported` | "Give me a few of the diagnoses", "List some of her diagnoses" |

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

A question with a list request and a presence check, in two sentences or
in two clauses of one, gets `task_ambiguous` before any out-of-scope
rule is applied, since a condition name may hold such words; so does a
question with neither. A phrase that is only a pronoun ("is it recorded")
gets `concept_unknown`. A phrase that is only a word for conditions ("are
any diagnoses recorded") asks for the list.

### Fail-safes

Since rules version 3, a list or a presence check is released only when
every word of the question is accounted for: by a question frame, the
condition phrase, a scope phrase, or a fixed list of harmless words
(politeness, reporting words such as "documented" or "billed", "ever",
"at any point"). Otherwise the first fail-safe that applies gives a
clarification:

| Fail-safe | Clarification | Examples |
| --- | --- | --- |
| A visit, stay or time named in words that are not a scope phrase | `scope_ambiguous`, naming the selected scope | "Was X coded in the admission before the last one?", "Is X recorded now?", "What are his current diagnoses?" |
| An exclusion or filter; in a list, also a negation | `task_ambiguous` | "List the conditions except diabetes", "Which conditions are not recorded?" |
| Any other word | `task_ambiguous` | "List her cardiac conditions", "Is X recorded in his family history?" |
| Two presence checks in one message | `task_ambiguous` | "Is X recorded? Is Y recorded?" |

The question words removed from the ends of the condition phrase are
checked for visits, times and exclusions as well. The condition phrase
itself is not checked: it has to resolve to a condition, or the
question gets a clarification anyway. Version 3 also stopped many
harmless questions that named the selected scope in words of their
own; version 4 reads those words as scope phrases (see [Scope](#scope))
and accepts more harmless words.

## Scope

A question is answered within the active case and scope chosen in the
case selector. Recognised scope phrases:

| Scope | Phrases |
| --- | --- |
| The active scope, whichever it is | "the selected part of the record", "the selected time frame", "the chosen period" |
| Pre-target history | "before (prior to) the final (last, latest, most recent, current, this) admission", "before the current, final admission", "before admission", "previous (prior, earlier, past) admissions", "a prior hospitalisation", "pre-target history" |
| One visit | "visit 2", "admission number 3", "admission two", "visit 2 of 4", "the second admission", "the final (last, current, present) admission", "the current, final admission", "this admission", "her hospital stay" |
| All case visits | "in any admission", "across all admissions", "across (throughout) her admissions", "all of her admissions, including the current one", "in the entire record" |

An admission may also be called a visit, stay, encounter or
hospitalisation, with "hospital", "inpatient" or "ICU" before it; a
phrase may start with "at the time of" or "during the course of", and
name the admissions as "the patient's". "This admission", and an
admission named without a number or order ("her hospital stay", "the
admission"), name the selected visit when a visit is selected, and the
final admission otherwise; "the current admission" is always the final
one. A visit, stay or time named in other words gets a clarification
(see [Fail-safes](#fail-safes)).

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
suggestions, never a guess: names that begin with it or contain it,
or, when there are none, the names of the closest spellings of its
unknown words (version 2). The rules are in
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

Answers are written as candidates by fixed templates (`qa.templates`):
every fact is a slot filled from the bundle, the resolution or the
interpretation, and every refusal and clarifying question is an
approved notice. The candidate is checked for structure and references,
for the support of its statements by their cited records and against
the wording rules, and the released text and claims are rendered from
it, one statement per line; a candidate that fails a check is withheld
as a validation rejection (see [validation.md](validation.md)).

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
| `ranking`, `two_requests` | Fixed requests (suite version 2) | An abstention, or `task_ambiguous` |
| `misspelling` | A recorded concept's name with one letter dropped (suite version 2) | `concept_unknown`, suggesting the concept |
| `presence_negated` | A concept of the scope, asked in a negative wording (suite version 3) | Its records, or `not_recorded` |
| `scope_unrecognised`, `count_list`, `qualified`, `unexplained`, `two_conditions` | A fixed request for each fail-safe (suite version 3) | `scope_ambiguous`, an abstention, or `task_ambiguous` |

A response is correct when its outcome, reason codes, task and scope are
as expected, it returns exactly the expected records, a list names exactly
the expected groups, and its claims cite exactly the records it reports.
Questions are drawn in a fixed order from the suite version, so every run
asks the same questions. The suite prints aggregate counts and digests
only and writes nothing. It refuses the evaluation case set, which is held
for Phase 9.

Results (suite version 1 with the rules of release 0.6.0, and each
later suite version with the rule version of the same number):

| Suite | Case set | Admitted cases | Questions | Correct | Presence records (expected, returned, correct) | Unscored description matches | Summary SHA-256 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v1 | `dev-30-v1` | 26 | 1,550 | 1,550 | 892, 892, 892 | 21 records in 6 questions | `c5374924494c088f9eb17434e24f836ea11f46b634d3df0d8fa16f5cc4a482d9` |
| v1 | `pilot-10-v1` | 10 | 648 | 648 | 368, 368, 368 | none | `d3da7b6cecce15b247304c054984a06bbd078c07609a8fc3679b8d7cef70cacb` |
| v2 | `dev-30-v1` | 26 | 1,628 | 1,628 | 919, 919, 919 | 15 records in 5 questions | `af9f5e5c86bd9415a099bee5b143552019eae182faf6405c1afc96b1a24413a6` |
| v2 | `pilot-10-v1` | 10 | 678 | 678 | 389, 389, 389 | none | `61a38f7a8aabcb9ee4596ee1527c54733478f9a079c5a8ae87c4345cc434c634` |
| v3 | `dev-30-v1` | 26 | 1,892 | 1,892 | 1,089, 1,089, 1,089 | 10 records in 5 questions | `83b82783ffab9597ee2d8d4d9ff4e651a1c6452cc8e346be1d9cc81fc3fe4588` |
| v3 | `pilot-10-v1` | 10 | 784 | 784 | 439, 439, 439 | 3 records in 1 question | `17f0671a9385d20b7524e1c44f65c6919130a77e5d0d3678e50dfb381c7f9a89` |
| v4 | `dev-30-v1` | 26 | 1,892 | 1,892 | 1,169, 1,169, 1,169 | 10 records in 7 questions | `fce42eb09ef0c9e0660d565a65eebd3fa0f06da4a01ab9032e7384a8cf9aaf28` |
| v4 | `pilot-10-v1` | 10 | 784 | 784 | 447, 447, 447 | 2 records in 2 questions | `04d33370291c796a955563392abcd7864f4be29d5babc8c88a27aa4c1643c055` |

The vocabulary sweep asks about every standard condition by name in each
of the suite's seven presence wordings, and reads no patient data. Of
112,693 standard conditions, 87 share their name with another concept and
3 are too broad; for the other 112,603, all 788,221 questions resolve to
the concept asked about, with each rule version.

With the answers written as candidates (`qa-answer-v5`), suite version 4
still gives 1,892 of 1,892 on `dev-30-v1` and 784 of 784 on the pilot
(summary SHA-256
`71582efeb765ca1202a5351c4cb619128c5b00d9baffeea82f834995ad398a4a` and
`178614e2c8eb056dc291f4749c58f1f222138667b9773d960a3f7f2545ced925`).

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

Rules version 2 refuses rankings, a change made because of this result,
so this set no longer tests version 2 blind. As a follow-up only, with
rules version 2: 31,985 of 32,392 questions as expected (98.7%); 236
ranking questions answered with a list, all of them asking for a number
of diagnoses without a ranking word ("list the three diagnoses"); no
question about other data answered (summary SHA-256
`3c4274ef04d122ff57bd0dfbcc12b61b68b2b29a44b036d32eb8eff5b31ec405`).
With rules versions 3 and 4, again as follow-ups only: 32,117 of 32,392
as expected (99.2%) with each; no ranking question, no list of a number
of conditions and no question about other data answered; 781 of the
1,056 all-diagnoses questions read as lists, down from 885 with version
2, the others ending in a clarification (summary SHA-256 for version 4
`a6f6ec62a471ef1d513b433dd6ff4e4ea024e3301bfcae720dd1a11be8d1103b`,
for version 3
`7440fa00ab94ae70868a21f8f3e0cd62fb3c4f8566bcfbcd3ba14c3f36406731`).

## Written wording test

Work item 34.3 measures presence-check wording, which the dataset above
does not contain, and the other intents on new wording: questions worded
by a writer who has not read the [Interpretation](#interpretation) rules,
about development cases, scored once with `qa.written`. Each set is
scored once, and a later rule version is measured on a new set. The
questions file of a set is
`protected-data\question-sets\<set>\questions.csv` (UTF-8, opens in a
spreadsheet), one row per question:

| Column | Content |
| --- | --- |
| `id` | A unique label, such as `q001` |
| `case_set` | `dev-30-v1` (or `pilot-10-v1`) |
| `case` | The case number |
| `scope` | The scope imagined as selected: `history`, `all` or `visit N` |
| `question` | The question, as it would be typed |
| `intent` | `list` (the recorded conditions), `presence` (whether one condition is recorded), `refuse` (something the application should not answer, such as a treatment, a laboratory value or a number of conditions) or `clarify` (a question it should answer with a clarifying question: one naming another part of the record or a visit the case lacks, two requests or two conditions at once, a list of only some conditions, or one too vague to act on) |
| `concept` | For `presence`, the OMOP concept ID of the condition meant; empty otherwise |
| `family` | Optional: a label that groups the results |

The overview `case-overview-dev-30-v1.csv` in the same folder lists each
case's recorded conditions by scope with their concept IDs; concepts that
are not recorded can be looked up on Athena.

The score compares every response with the development suite's oracle. A
list is correct when it cites exactly the scope's records. A presence
check is understood when the resolved concept is the concept meant, an
ancestor or a descendant of it, and exact when the answer also returns
exactly the records of the concept meant. A refusal is correct when the
response is a clarification or an abstention, and a clarify question when
it is a clarification. The file's SHA-256 is recorded with the result.

### Results

Set `written-v1`, scored with the rules of release 0.6.0, has 198
questions over all 26 admitted cases of
`dev-30-v1` and 11 scopes, in nine families. Presence checks ask about a
condition recorded in the scope, a broader condition of a recorded one,
or a common condition that is not recorded; about half the lists and
presence checks name the scope in words (questions file SHA-256
`3e1be93864d0b9e0988c0ecb30d9ce0a064b2e348abda4396c94d3d1df99bb23`,
summary SHA-256
`c3200af8b35047f0a6f1bc7dc76f83041234e20899266a5bb3121266037bcc33`):

| Family | Questions | As expected | Otherwise |
| --- | --- | --- | --- |
| List | 23 | 17 lists citing exactly the scope's records | 6 clarifications or abstentions |
| Presence, recorded condition | 45 | 25 exact | 1 related concept; 19 clarifications or abstentions |
| Presence, broader condition | 20 | 9 exact | 11 clarifications or abstentions |
| Presence, condition not recorded | 45 | 33 exact | 12 clarifications or abstentions |
| Refuse | 40 | 38 refused | 2 answered as condition questions |
| Clarify: another part of the record | 10 | 7 clarifications | 3 answered within the selected scope |
| Clarify: two requests at once | 6 | 2 clarifications | 4 answered for one request |
| Clarify: too vague | 5 | 4 clarifications | 1 abstention |
| Clarify: a visit the case lacks | 4 | 4 clarifications | none |
| All | 198 | 139 (70.2%) | 1 related-concept answer; 9 wrong answers; 49 clarifications or abstentions instead of an answer |

Every presence answer was about the condition meant, and every list that
was given cited exactly the scope's records. Of the 42 presence checks
that got no answer, 32 were asked for a condition wording that is not a
name or synonym in the vocabulary, and got a clarification. The 9 wrong
answers come from three gaps: scope wordings that are not recognised
(the question's own scope is replaced by the selected one), two requests
joined without a list word in each part, and two requests to refuse that
read as condition questions.

Set `written-v2` was worded after rules version 2 was fixed, in the same
nine families with new cases, scopes and conditions, and scored once with
rules version 2 (questions file SHA-256
`f7b9e11b10cb1d9c00c94bb8267f9d7f3ddf976dd0014c6081b0622aebc0e3f0`,
summary SHA-256
`ae0d2ffbfe2d09d0f1dcec394e9394535b000467091d606ae5bcdd495c9cfe93`):

| Family | Questions | As expected | Otherwise |
| --- | --- | --- | --- |
| List | 24 | 17 lists citing exactly the scope's records | 7 clarifications or abstentions |
| Presence, recorded condition | 45 | 25 exact | 20 clarifications or abstentions |
| Presence, broader condition | 20 | 9 exact | 11 clarifications or abstentions |
| Presence, condition not recorded | 45 | 36 exact | 9 clarifications or abstentions |
| Refuse | 40 | 40 refused | none |
| Clarify: another part of the record | 10 | 7 clarifications | 3 answered within the selected scope |
| Clarify: two requests at once | 6 | 6 clarifications | none |
| Clarify: too vague | 5 | 4 clarifications | 1 abstention |
| Clarify: a visit the case lacks | 3 | 3 clarifications | none |
| All | 198 | 147 (74.2%) | 3 wrong answers; 48 clarifications or abstentions instead of an answer |

Every presence answer was again about the condition meant. The wrong
answers fell from 9 to 3, all scope wordings the rules still do not
recognise. Two presence checks named their scope in words the rules read
as a different scope, and got a clarification.

Sets `written-v3` and `written-v4` add four families for the
fail-safes: lists of only some conditions and two conditions at once
(to clarify), lists of a number of conditions (to refuse) and presence
checks asked in a negative way. `written-v3` was worded after rules
version 3 was fixed and scored once with it (questions file SHA-256
`5c3051312e29a2600b09611fef02fc9bb4b00f9e499dfe34076085af33c490d8`,
summary SHA-256
`0f2655541281c5b50db76737ba57a653d4336ebc5f1cc81264c2412d9f95d9be`).
Scoring the same questions with each rule version shows what version 3
changed:

| Rules on `written-v3` | As expected | Wrong answers | Lists | Presence exact | Refused | Clarified |
| --- | --- | --- | --- | --- | --- | --- |
| Version 2 | 151 of 228 (66.2%) | 9 | 18 of 25 | 58 of 116 | 45 of 48 | 30 of 39 |
| Version 3, scored once | 105 of 228 (46.1%) | 1 | 3 of 25 | 19 of 116 | 47 of 48 | 36 of 39 |
| Version 4, development | 148 of 228 (64.9%) | 0 | 12 of 25 | 54 of 116 | 48 of 48 | 34 of 39 |

Version 3 removed 8 of the 9 wrong answers of version 2, but its
fail-safes also stopped 55 questions that version 2 answered correctly:
32 through the list of harmless words and 23 through the check for
unrecognised visits, most of them naming the selected scope in words
of their own ("the selected part of the record", "the current, final
admission"). Version 4 was written to read those words, so on this set,
which informed it, its result is a development figure only.

Set `written-v4` was worded after rules version 4 was fixed, in the same
thirteen families with new cases, scopes and conditions, and scored once
with rules version 4 (questions file SHA-256
`662a1b4525c24aca885e609bf4f7149de58124238aa70309a0882bbfb71e84b2`,
summary SHA-256
`9fc9b0867f25d3f0bc57ec3202d0c59b8492a89b497e8bc4fa272c99e849165e`):

| Family | Questions | As expected | Otherwise |
| --- | --- | --- | --- |
| List | 24 | 10 lists citing exactly the scope's records | 14 clarifications or abstentions |
| Presence, recorded condition | 45 | 24 exact | 21 clarifications or abstentions |
| Presence, broader condition | 20 | 11 exact | 9 clarifications or abstentions |
| Presence, condition not recorded | 44 | 23 exact | 21 clarifications or abstentions |
| Presence, asked in a negative way | 8 | 2 exact | 6 clarifications or abstentions |
| Refuse | 40 | 38 refused | 2 answered as condition questions |
| Refuse: a number of conditions | 6 | 6 refused | none |
| Clarify: another part of the record | 10 | 5 clarifications | 5 abstentions |
| Clarify: two requests at once | 6 | 6 clarifications | none |
| Clarify: two conditions at once | 6 | 6 clarifications | none |
| Clarify: a list of only some conditions | 8 | 8 clarifications | none |
| Clarify: too vague | 5 | 4 clarifications | 1 abstention |
| Clarify: a visit the case lacks | 4 | 4 clarifications | none |
| All | 226 | 147 (65.0%) | 2 wrong answers; 77 clarifications or abstentions instead of an answer |

Every presence answer was about the condition meant and every list that
was given was exact. On the same questions, rules version 2 gives 154
as expected (68.1%) and 9 wrong answers: three lists of only some
conditions, two messages with two requests and two lists of a number of
conditions were answered, and so were the same two refusals. Version 3
gives 102 (45.1%) and 2 wrong answers. Version 4 keeps the fail-safes'
gain at a net cost of 7: 19 lists and presence checks that version 2
answered correctly now get a clarification, 10 of them for a visit named
in words that are still not recognised, and 3 questions that should get
a clarification get an abstention; 8 presence checks that version 2
could not read, and the 7 questions it answered wrongly, are now as
expected.

## Limits

- One condition per question. Dates, times, the order of events, counts,
  rankings and lists of a number of conditions are not answered.
- Unusual wording gets a clarification rather than a guess; so do
  abbreviations, everyday terms and misspellings that are not in the
  vocabulary, the last with suggestions of the closest spellings (see
  the written test results).
- The fail-safes ask again whenever a word is not accounted for, so
  some harmless questions get a clarification: a visit or time named in
  words the rules do not recognise, even when it is the selected scope,
  or a harmless word missing from the fixed list.
- A request that should be refused can still read as a condition
  question when its words name a condition (2 of 226 in the last
  written set).
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
& $AppPython -m ehr_evidence_app.qa.written --operator-real --score --set written-v2
& $AppPython -m ehr_evidence_app.qa.written --operator-real --score --set written-v3
& $AppPython -m ehr_evidence_app.qa.written --operator-real --score --set written-v4
```

The vocabulary build and the partition record each write one new
protected directory and refuse an existing one; the template and the
overview write one new protected file each. The suite, the sweep, the
held-out test and the score write nothing.
