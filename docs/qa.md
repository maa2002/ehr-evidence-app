# Natural-language QA

Phase 5 answers one question about the active case and scope from that
case's verified QA bundle. Interpretation and resolution follow fixed
rules, and so does which answer a question gets. From Phase 7 the
language model writes each answer from evidence as a complete candidate,
behind the checks of Phase 6, and the templates described here are its
fallback ([generation.md](generation.md)); refusals and clarifying
questions stay the templates' notices. Without the model the same
question about the same bundle always gets the same response.

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
| 52.2 | `qa.record_vocabulary` | Medication and procedure vocabulary built from the pinned Athena release |
| 52.4 | `qa.record_interpret`, `qa.record_resolve` | Reading of questions about the whole record; their terms and the records they match |
| 52.5 | `qa.record_answer` | Answers about the whole record, from its record bundle |

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
| `operational_error` | A limit is exceeded, or a check fails to run | `limit_exceeded`, `validator_failed` |

A `not_recorded` answer always says that the condition is not recorded
within the selected scope, and that this is not a statement of clinical
absence.

## Interpretation

The question is normalised as the vocabulary is: accents removed, case
folded, "&" read as "and", possessives and symbols dropped, dots inside
codes kept. A polite opening is removed: "please" or "kindly", then "can
you tell me", "tell me", "let me know", "I'd like to know (about)", "I
want to know", "I wonder" or "do you know", with the "if" or "whether"
after it, but not before "about" or "more", so "tell me about the
patient" stays as asked. Scope phrases are set aside (see
[Scope](#scope)). This section describes rules version 6
(`question-interpretation-v6`), which adds the conversation and the
sentence verbs below to version 5; version 5 added the polite opening and
the statement form to version 4
([ADR 0023](decisions/0023-consistency-and-junk-input.md)); release
0.6.0 had version 1 and release 0.7.0 version 2, version 3 was measured
but not released, releases 0.7.1 to 0.9.0 had version 4 and releases
0.10.0 to 1.0.0 version 5.

- **Conversation** (version 6): a message that is only a greeting,
  thanks or a request for help ("hi", "hello there", "thanks", "help",
  "can you help me", "what can you do") is not a question about the
  record. It gets `task_ambiguous` with the notice that the question was
  not understood, and is never looked up as a condition's name, as
  "help" was before ("Which condition do you mean? "help" was not found
  as a condition name"). On the page, "help", "what can I ask?" and
  "/help" with any words after it show the help.

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
  presence check, unless it states that something was given, measured or
  done ("heparin was given?"): that is read as its question form is ("was
  heparin given?"). Since version 6 it is a presence check only when it
  holds no verb of a sentence ("is", "are", "was", "have", "can" and the
  like; "may" is left out, as in "May-Thurner syndrome"): a misspelt
  question such as "wich condtions are recordd?" is not understood,
  where it was looked up as a condition named "wich condtions are
  recordd".

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
approved notice. The candidate leaves through the release gate, which
checks its structure and references, the support of its statements by
their cited records and its wording, and renders the released text and
claims from it, one statement per line; a candidate that fails a check
is withheld as a validation rejection (see
[validation.md](validation.md)).

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
With rules version 5 (`question-interpretation-v5`) it gives the same
counts (summary SHA-256
`b659c68d2d0881be8b6da1d7f91466bcc7dc822014cc38825e2e48e034c7de73` and
`79d5e9db8810c10061d000d6a8a7f622c1573a996980fb0d728583a07ff78180`);
run under version 4's name, it gives the two digests above byte for
byte, so nothing but the name of the rules changed. The written wording
sets and the held-out test are not scored again with version 5, since
each is scored once.

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

## Questions about the whole record

The rest of the record can be asked about too
([ADR 0021](decisions/0021-the-whole-record.md)): medications,
procedures, laboratory results, microbiology, vital signs, transfers and
ICU stays, the visits and the person. The condition rules read every
question first, unchanged, so a condition question gets exactly the
response it got before. A question they refuse as being about other
data, times or counts, or holding words they cannot place, and one whose
condition phrase names no condition, is read by `qa.record_interpret`
(rules `record-questions-v7`, which removes a polite opening as the
condition rules do, reads a name whole and, by rule 12, reads when a
visit named by a scope phrase was); so are a dose question, a past
question about death and a question about a first or last result. A
request for advice, a question about the future and a ranking of
conditions keep their refusals: since `qa-answer-v7`, a ranking that
names conditions in the singular as in the plural ("the most important
condition", "the main conditions"). In versions 5 and 6 of the answer
rules (releases 0.10.0 to 1.0.0) the test of that ranking held two stray
control characters for its word boundaries and never matched, so such a
ranking was read as a question about the record, and asked back or
answered (see [Questions from a user test](#questions-from-a-user-test)).
A presence question whose phrase is a fixed word for a
class of medications ("any nsaids", "any antibiotics") is read as one
about medications before its condition is looked for, and the word names
medications rather than a condition of the same name (rules
`qa-answer-v6` and `record-questions-v5`): the condition vocabulary
calls "Non-steroidal anti-inflammatory overdose" NSAIDs, and until these
versions "Any nsaids?" was answered as whether that overdose is
recorded. A class word that is also the name of another kind of record
("chemotherapy", a procedure's too) still asks which is meant.

The record does not say what a medication was given for, so a question
that asks which medications were given for something gets a
clarification that says so and asks about a medication by name or a
drug class instead (version 4): one whose words after "for" or "treat"
name a condition, a test or a vital sign ("what is he taking for his
seizures", "what was given for pain", "is she on anything for blood
pressure"), unless the words before them name an item of another kind,
and one that names medications by a condition ("seizure medications").
Until this version, such a question was read as one about a medication
named after the condition, and asked which medication was meant; asked
without a kind's word, it was answered as a question about the
condition, or about a vital sign of the same name.

| Task | Examples | Answer |
| --- | --- | --- |
| `record_list` | "What medications were given?", "Show me the labs", "Was she in the ICU?" | Every item of the kind within the scope, with its records and visits; each ICU stay and transfer with its times |
| `record_presence` | "Was heparin given?", "Was she on Lasix?", "Any antibiotics?", "Was a chest x-ray done?" | Every matching record, grouped by item and by how it matched; or not recorded within the scope |
| `record_timing` | "When was heparin first given?", "When was hypertension first recorded?" | The first and the last matching record |
| `record_count` | "How many potassium results are there?", "How many medications?" | The number of matching records and their visits, or of a kind's items and records |
| `record_values` | "What was the last potassium?", "Highest creatinine?", "Show the hemoglobin trend", "What dose of heparin was given?", "What doses were given?", "What organisms grew?" | Each matching item's latest, first, highest or lowest value, or its values in time order |
| `record_abnormal` | "Were any labs abnormal?", "Was the potassium high?", "Were any labs low?" | Every result the laboratory flagged abnormal (for microbiology, every result reported resistant) |
| `visit_details` | "How many visits?", "When was she admitted?", "Where was she discharged to?" | The field asked about, for every visit of the scope |
| `person_details` | "How old was the patient?", "What is her race?", "Did the patient die?" | The person's sex or date of death, or the field recorded with each visit of the scope |

The question's frame gives its task, its words or its phrase the kind,
and a selector (last, first, highest, lowest, all) the values it wants.
Flags are read the same way whether the question names an item or a
whole kind: a laboratory result asked about as high or low asks for the
results the laboratory flagged; a vital sign asked about as high or low
gives its highest or lowest value, since vital signs carry no flag; and
a question about abnormal vital signs without high or low, or abnormal
medications or procedures, asks again, since the record flags none of
them. The phrase is resolved within its kind by `qa.record_resolve`
(rules `record-resolution-v3`): a name of the kind's dictionary (the
laboratory and ICU item dictionaries, the medication vocabulary's
ingredients, ATC classes and brand names, the microbiology and care-unit
names), a fixed synonym ("hgb", "wbc", "bp", "blood thinners", "cabg",
"intubated", "e coli", "micu"), or the names holding its words; a phrase
that resolves in no kind is left to the condition rules, and one that
resolves in several asks which is meant, unless the question's verbs
("given", "done", "measured") or its task decide. A question that names
no kind takes one only by a name or a synonym, or by the words of its
names in a kind its verbs or task point to: "is diabetes recorded" is a
condition question the condition rules could not resolve, and it keeps
their response instead of being answered as a procedure whose names hold
the word (as it was until this version). An unknown phrase gets
suggestions of the closest names of the kind its words or verbs name,
never a guess: names that begin with it, else the closest spellings (one
edit, two from eight letters), for medications and procedures too. A
medication phrase that resolves to nothing as written is tried once more
as the plural of its last word (version 3), so that a class word or a
class name in the singular ("an antiepileptic", "a blood thinner")
resolves as its plural does; a phrase that resolves as written keeps its
resolution. A fixed class word that the vocabulary also names stands for
both (version 3). ATC names several classes of local antibiotics
"Antibiotics" (of the eye, the ear, the skin, the gut and so on), and
until this version "antibiotics" stood for those alone, missing the
systemic antibacterials (J01) that its fixed synonym names, so that "Any
antibiotics?" could be not recorded for a patient given intravenous
antibiotics; "antivirals" missed the systemic antivirals (J05) and
"corticosteroids" the systemic corticosteroids (H02) in the same way.

A medication record with a standard concept matches an ingredient, a
brand name (through its ingredients) or an ATC class by its concept's
ancestors. One without a standard concept (45% of the development
records) matches by the drug name written in its prescription: the
phrase itself, the name of a resolved ingredient, or the name of any
ingredient the ATC hierarchy places in a resolved class (the class
ingredients of the record vocabulary's version 2), each as whole words,
so "hydroxyurea" is not urea; a salt written after a drug ("heparin
sodium") is not the ingredient sodium. Over all visits of the 36
development cases, 13 drug-class questions were not recorded in 278
case questions before and 237 now, and 745 records without a standard
concept are found by their written names; "Any opioids?" is not recorded
for 10 cases instead of 17. A "not recorded" about a named
condition, medication or procedure says how many records of the kind in
the scope have no standard concept and how they were checked: their ICD
codes and descriptions, their written drug names, or the words of their
names. Procedures, microbiology and care units match by the words of
their names, and an answer says so ("matched by the words of its name").

Filters by value ("above 5"), times and parts of the record that are not
scope phrases ("yesterday", "on day 2", "in the ICU"), exclusions,
pronouns, two kinds at once and two items at once ("was heparin or
warfarin given") get a clarification. A negation asks the same as the
question without it ("was heparin not given" is a presence check, as for
conditions), except in a question about a whole kind ("which labs were
not done"), where it is a filter. The scope binds as for conditions. The
templates write every answer (`qa.record_answer`, rules
`record-answer-v4`, whose clarification for a question about what a
medication was given for is the notice `indication_unrecorded`): its
facts are references to records, visits and the
person, and every name, value, time, date and age is rendered from them.
A medication without a named concept is named by the drug its
prescription names, as written (version 4; it was followed by "(as
written; no standard concept)" before, in 1,165 of 3,088 answers to
sixteen medication questions in every scope of the development case
sets, whose outcomes are the same with version 4, and whose 13,938
mutants, of 72 answers, are all withheld); any other record without a
name by what it holds, and why it has none.
Values are shown as recorded, every digit and no exponent; a value that
MIMIC-IV removed says so, without a unit; a single record is the only
one, and the highest or lowest of values that are not numbers is the
latest, said as such. A list of values shows the latest 30 of a named
item, the latest 30 of each microbiology test, and the latest of each item of
another whole kind, and counts the rest; an item is named by its earliest
record. A visit whose discharge is recorded before its admission has no
length. Ages are the anchor age plus the years to the visit, so they may
be one more than the age on the day, and ages over 89 are not given;
dates are the shifted dates of MIMIC-IV.

### Edge cases tested

The rules are tested at their edges on an invented record built for
them (`tests/qa/record_cases.py`): results of one test across visits out
of time order, results at the same time, more results than an answer
lists, values that are not numbers or were removed, a test without a
name, medications without a standard concept (a written name, a salt, a
name inside another, none at all), a brand name, drug classes,
microbiology with and without growth and every interpretation, an open
transfer, two ICU stays, an admission without times, one whose discharge
precedes its admission, a death in hospital and an age that passes 89.
Every one of 142 questions, including noise, markup, accents,
abbreviations, negations, misspellings, words of time, class words in
the singular and questions about what a medication was given for, is
asked in
every scope; none ends in a rejection or an error. The answers' checks
are tested by breaking correct answers one fact at a time
(`tests/validation/test_record_mutations.py`): all 1,481 changed answers
are withheld, and every answer and control is released.

The same questions, with the heavy ones of the first development check
(every result of every test, every culture, every dose), were asked of
every scope of every case of the development case sets through the
service, without the model and without runtime records: 31,652 requests
gave 13,425 answers, 9,048 "not recorded", 8,214 clarifications and 965
abstentions, with no rejection, error or exception (median 0.046 s, the
slowest 1.33 s). Seventeen condition questions, asked in every scope,
get the same response with and without the record. The answers' checks
were broken the same way on 300 of these answers (see
[validation.md](validation.md#mutants-of-answers-about-the-whole-record)).

### Consistency and junk input

Two more tests need no answer key
([ADR 0023](decisions/0023-consistency-and-junk-input.md)).
`tests/qa/test_consistency.py` asks each of 133 questions (the edge
questions and seven about conditions) in 13 rewordings: small letters,
capitals, no question mark, two, a full stop, more spaces, a tab, a
non-breaking space, a typographic apostrophe, "please" first and last,
"can you tell me" and "I'd like to know". Each must get the same
response. It also compares each question's answers in nested scopes:
what a visit or the history records, all visits record; a list of a
larger scope holds every item of a smaller one's; a count is never
smaller, a first time never later, a last time never earlier, a highest
value never lower and a lowest never higher; and a visit's or the
person's details are the same in every scope that holds them. On the
invented case set and record, 6,040 rewordings and 1,032 pairs of nested
scopes are compared. The rewordings found that the condition rules did
not read a polite opening: "Please, which conditions are recorded?" and
"Can you tell me which conditions are recorded?" were answered as
questions about the whole record (the same list, under another task and
never written by the model), and "I'd like to know which conditions are
recorded" was not understood. Rules version 5 removes a polite opening
before either reading, and reads a statement that something was given,
measured or done ("heparin was given?") as its question form.

`tests/qa/test_junk_input.py` asks 1,500 junk questions drawn with a
fixed seed: random characters of every kind (accents, symbols, emoji,
other scripts, combining marks, invisible and control characters,
unpaired surrogates), shuffled and misspelt words of real questions,
real questions with junk inserted, placeholders, format strings, markup
and query syntax, and long repetitions up to the length limit. It asks
them through the service, with every response recorded, and of the
invented record. Every one must end in an outcome a question may end in,
in text that can be sent and recorded, within two seconds, and the same
when asked again. It found that a question holding an unpaired surrogate
raised an error; such a character is now read, and recorded, as the
replacement character. Junk questions and malformed bodies, paths,
queries and methods also go over HTTP to the API and to the interface's
server (`tests/api/test_junk_requests.py`,
`tests/interface/test_junk_requests.py`): none is an internal error, and
every refusal now carries its code.

On the development case sets, with aggregate output only, through the
service without the model and without runtime records:

| Check | Requests | Result |
| --- | --- | --- |
| Nested scopes: every question of the test in every scope of every case | 25,669 | 20,475 pairs of nested scopes compared, none contradicting |
| Rewordings: the 133 questions in 13 rewordings, in the history and all visits of 3 cases of each set | 18,828 | none answered otherwise |
| Junk: 3,000 junk questions in the history and all visits of 2 cases of each set | 24,000 | no error or exception; 160 over the length limit, each `limit_exceeded`; median 0.055 s, the slowest 1.14 s |
| The edge questions again, in every scope of every case | 31,652 | the same outcomes as before the rules changed; no condition question answered otherwise with the record |

### What a medication was given for, and drug classes

A clinician trying the shared interface asked which medications were
given for a recorded condition
([ADR 0026](decisions/0026-what-a-medication-was-given-for.md)). Rules
`record-questions-v4`, `record-resolution-v3` and `record-answer-v3`
were checked on the development case sets, with aggregate output only,
through the service as the page asks (the condition rules first),
without the model and without runtime records. Each of 228 questions,
the edge questions, the first development check's, seventeen condition
questions, the new wordings and the help's examples, was asked in every
scope of every case with the rules before and after, and a digest of its
responses compared:

| Check | Requests | Result |
| --- | --- | --- |
| The 228 questions, before and after | 44,004 each | 207 give the same responses byte for byte; the 21 that change are the ones the rules meant to change |
| Twelve wordings asking what medications were given for something | 2,316 | ten now get the clarification; six of them were answered before as another question: a condition's presence ("what is he taking for his seizures"), a vital sign ("what was given for pain", "for blood pressure") or a test ("for the potassium"); "what medications treat his seizures" keeps the condition rules' refusal of treatment questions, and "for diabetes" a clarification, since the word alone names no condition |
| Eleven questions naming a class in the singular | 2,123 | each gives the outcomes of its plural, where each was a clarification before |
| The medication questions again, and every class word, after a class word kept its classes | 13,124 | only the three antibiotic questions changed: "Any antibiotics?" is answered in 103 of the 193 scopes instead of 62 |
| Mutants of the class answers | 60 answers, 1,862 mutants | all withheld; every original and its 24 controls released |
| Mutants of the record answers (as in [validation.md](validation.md#mutants-of-answers-about-the-whole-record)) | 300 answers, 30,719 mutants | all withheld; every original and the 762 controls released |
| The condition suite | 1,892 and 784 | 100%, with the summary digests of rules version 5 byte for byte |

"Any nsaids?" was then still read by the condition rules as a condition,
"Non-steroidal anti-inflammatory overdose", which the condition
vocabulary calls NSAIDs, and was not recorded in every scope. With rules
`qa-answer-v6` and `record-questions-v5`, the drug-class questions and
six NSAID wordings were asked again before and after (14,089 requests
each): "Any nsaids?", "Any nsaid?" and "Any NSAIDs?" are answered in 139
of the 193 scopes, as "Were any NSAIDs given?" always was, and the other
70 questions, "Is NSAID overdose recorded?" and "Any chemotherapy?"
among them, give the same responses byte for byte. Mutants of the NSAID
answers (36 answers, 1,365 mutants) are all withheld and every original
and its 15 controls released, and the condition suite, run under the
name of `qa-answer-v5`, gives its summary digests byte for byte.

### Names read whole

Asking about records by their own names found that the reading cut names
apart. A question's phrase is what is left once its frame, the kinds'
words, a selector and harmless words are set aside, and names hold such
words too. "When was chronic kidney disease first recorded?" looked for
"chronic kidney", which names nothing, and was asked back; "anxiety
disorder" was read as anxiety; "type 2 diabetes mellitus without
complication" lost "without"; and a condition named with a word that
points to another kind ("dependence on respirator", "complication of
surgical procedure") was read as a medication or a procedure, and could
be answered "not recorded" though the scope records it. Presence
questions and lists of conditions, which the condition rules read, were
not affected.

Rules `record-questions-v6` (rule 11) try the question's words from the
first to the last word of its phrase, with the words set aside between
them and up to three set aside beside them, as an item's whole name,
longest first, by a name or a fixed synonym only. A name so found is the
phrase; one that holds every kind's word of the question is looked for
in every kind. A question about what medications were given for
something is read by rule 9 first, and one whose longest name two kinds
hold, or in which no span is a name, is read as before. Version 6 also
reads "all ... values" around a name of up to eight words with digits
("all the hemoglobin a1c values"); four words of letters alone were read
before, and a name such as pO2 got its latest value.

Before and after, on the development case sets, with aggregate output
only, without the model and without runtime records:

| Check | Requests | Result |
| --- | --- | --- |
| "When was {name} first recorded?" for every condition record's name in every scope | 2,638 each | read whole 2,614 times instead of 1,399; answered "not recorded" while the scope records the condition never, instead of 46 times |
| The 228 questions of the last comparison, and 22 naming conditions and cultures with such words, in every scope; and, from each scope's own records, up to three conditions named with such a word, asked when and how often (324 each), and its cultures (141) | 49,039 each | 225 of the 228 give the same responses byte for byte. The blood culture questions ask about blood cultures rather than every specimen of blood (answered in 42 scopes instead of 49), and "What was the platelet count?" names the platelet count rather than every test whose name holds "platelet". Of the 22, the condition questions that were asked back are answered or not recorded, and "anxiety disorder" is answered in 16 scopes rather than anxiety's 23. The generated condition questions are answered 321 times of 324 instead of 16; each answer cites exactly the independent reference's records (`assessment.record_references`), but for 3 times that rest on records without a standard concept, which are unjudged, and none is "not recorded" while recorded, where 3 were |
| Mutants of the answers to questions naming conditions and cultures with such words | 72 answers, 1,899 mutants | all withheld; every original released |
| Rewordings and nested scopes, as in [Consistency and junk input](#consistency-and-junk-input): every question of the test in every scope, and its 13 rewordings in the history and all visits of 3 cases of each set | 27,020 and 19,836 | 21,150 pairs of nested scopes compared, none contradicting; no rewording answered otherwise; median 0.067 s, the slowest 2.43 s with other checks running beside it |
| The condition suite | 1,892 and 784 | the summary digests byte for byte |

### Questions from a user test

Trying the page as a user would found five kinds of message the reading
answered otherwise than the help says. A greeting, thanks or a request
for help ("hi", "thanks", "help", "help me") was looked up as a
condition's name and asked back ("Which condition do you mean? "help"
was not found as a condition name"), and so was a misspelt question
("wich condtions are recordd?"). A ranking that names conditions ("What
were the main conditions?", "What is the most important condition?")
was read as a question about the record and asked back, where the table
in [Interpretation](#interpretation) declines it: the condition suite
asks the condition rules alone, which decline it, so the broken test
between them and the record rules went unseen. "When was the first
visit?" was declined as a question about dates and times, though "When
was the patient admitted?" asked in that visit's scope is answered. And
"not recorded" began with the question's words in lower case ("chest x
ray is not recorded").

Rules `question-interpretation-v6`, `qa-answer-v7`,
`record-questions-v7` (rule 12) and `record-answer-v5` change these as
described above, and `tests/qa/test_user_questions.py` asks each on the
invented record. The question's words keep their normalised form ("x
ray" for "x-ray").

Before and after, on the development case sets, with aggregate output
only, through the service as the page asks, without the model and
without runtime records:

| Check | Requests | Result |
| --- | --- | --- |
| The 228 questions of the earlier comparisons and 45 of the user test's own, in every scope of every case | 52,689 each | 214 give the same responses byte for byte, and 31 differ only in the capital that begins "not recorded". The 28 that change are the ones the rules meant to change: eleven greetings, thanks and requests for help and two misspelt questions are not understood, where each was asked back as a condition's name; nine rankings of conditions are declined in every scope, where eight were asked back and "What was the first diagnosis?" was answered in 190 scopes and not recorded in 3; and six questions about when a named visit was are answered in that visit's scope, which each of the 36 cases has, and ask for the scope to be changed in the other 157, where they were declined. No rejection, error or exception |
| The condition suite, under the rules' names before | 1,892 and 784 | 100%, with the summary digests byte for byte |
| The written wording tests, versions 1 to 4, under the rules' names before | 198, 198, 228 and 226 | the summary digests byte for byte |
| The end-to-end test of the written set version 4, without the model | 226 | the same counts at every stage |
| The record suite | 7,865 and 3,188 | the same counts for every question |
| The mutation suite ([validation.md](validation.md#mutation-suite)) | 17,414 and 7,356 mutants | as recorded there: every original released unchanged and every mutant detected |
| Mutants of the record answers (as in [validation.md](validation.md#mutants-of-answers-about-the-whole-record)) | 300 answers, 30,921 mutants | all withheld; every original and the 768 controls released, the same counts as before |

## Limits

- One condition per question. Rankings and lists of a number of
  conditions are not answered; dates, times and counts are answered by
  the questions about the whole record.
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
- Questions about the whole record: vital signs and ICU procedures exist
  for ICU stays only; notes cannot be asked about; a medication whose
  drug record cannot be tied to one prescription has no name of its own;
  a medication without a standard concept is found by a class only when
  its written name holds an ingredient's whole name (abbreviated names,
  such as "sulfameth", are not); everyday names of procedures depend on a
  fixed synonym table; a value is never compared with a threshold; a
  question naming a misspelt item without a word that points to its kind
  is left to the condition rules. A name that is only another kind's word
  is read as that word ("Medicine", a care unit, as medications; "PT",
  the prothrombin time, as the patient); a name two kinds hold (the blood
  gases' oxygen saturation and temperature, which are vital signs too)
  asks which is meant; and a medication without a standard concept is
  asked about by an ingredient's name, not by its written drug name as a
  whole.

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
& $AppPython -m ehr_evidence_app.qa.record_vocabulary
& $AppPython -m ehr_evidence_app.qa.record_vocabulary --operator-real
```

The vocabulary build and the partition record each write one new
protected directory and refuse an existing one; the template and the
overview write one new protected file each. The suite, the sweep, the
held-out test and the score write nothing.
