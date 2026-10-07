# Validation

Phase 6 makes sure nothing reaches a user unless it has been checked
against the evidence: checks before any text is written, checks on every
candidate response, and one release gate that withholds whatever fails. It
was built before the language model of Phase 7, which writes the complete
candidate of every response from evidence ([generation.md](generation.md)),
so that everything the model writes passes the same checks. Nothing here
loads a model.

The design is recorded in
[ADR 0011](decisions/0011-validation-design.md). Command examples assume the
session set-up in [development.md](development.md#session-set-up).

## Modules

| Work Item | Module | Role | State |
| :--- | :---: | :---: | ---: |
| 35 | `validation.evidence` | Checks Before Generation | Done |
| 36 | `validation.candidate`, `validation.structure` | The Candidate Contract; Structure and Reference Checks | Done |
| 37 | `validation.support` | Support of Every QA Statement by Its Cited Records | Done |
| 38 | `validation.explanation` | Template Explanation; Prediction and Attribution Preservation | Done |
| 39 | `validation.language` | Wording Rules for Every Outcome; Dates and Recurrence | Done |
| 40 | `validation.gate` | The Release Gate | Done |
| 40.1 | `validation.mutations` | Mutation Suite: Every Error Class Applied to Correct Responses | Done |
| 52.6 | `validation.record_text`, `validation.record_support` | How the Facts of Answers About the Whole Record Are Shown; Their Support | Done |

## Candidates

A writer (the language model of Phase 7, with the QA templates in
`qa.templates` and the template explanation in `validation.explanation`
as its fallback) produces a candidate, never released text. A candidate
names its
request, case, scope, outcome, reason codes and, for an answer, its bundle,
and holds an ordered list of statements. Each statement has:

- a **kind** from a closed list, which fixes the facts it states, what it
  cites and the answers it may appear in;
- its facts as typed **slots**. Slot values are references and numbers; the
  text they show is rendered from the evidence bundle and the vocabulary, so
  a writer never types a condition name, a count, a visit or a score;
- a **wording**: the words around the facts, with one placeholder, such as
  `{condition}`, for each slot of its kind;
- the bundle **references** that support it.

| Kind | Slots | Cites | Appears In |
| :--- | :---: | :---: | ---: |
| `list_summary` | `groups`, `scope`, `records` | Nothing; It Restates the Items | List Answers |
| `list_item` | `condition`, `count`, `visits` | The Condition's Records | List Answers |
| `list_unmapped` | `unmapped` | The Coverage Record | List Answers |
| `list_empty` | `scope` | The Coverage Record | Empty Lists |
| `presence_summary` | `condition`, `scope`, `matches` | Nothing; It Restates the Items | Presence Answers |
| `presence_item` | `condition`, `count`, `visits`, `match` | The Matching Records | Presence Answers |
| `presence_absent` | `condition`, `scope` | The Coverage Record | Not Recorded |
| `presence_unmapped` | `unmapped` | The Coverage Record | Not Recorded |
| `prediction` | `label`, `score`, `logit` | The Prediction (P1) | Explanations |
| `driver` | `rank`, `driver`, `score` | The Driver's Attribution | Explanations |
| `driver_history` | `driver`, `count`, `visits` | The Driver's Linked Records in Pre-Target Visits | Explanations |
| `driver_context` | `driver`, `count` | The Driver's Records in the Final Admission | Explanations |
| `notice` | The Notice's Own | Nothing | Any Released Outcome It Is Approved For |

A condition is named by a recorded event (its label comes from the bundle),
by vocabulary concepts (their names come from the vocabulary) or by an ICD
code. A notice is one of a fixed list of approved texts
(`validation.candidate.NOTICES`): that "not recorded" is not a statement
of clinical absence, that a list is complete, which drivers an
explanation describes and how many it does not, what attribution scores
and the model's score mean, each restriction of an explanation, each
refusal and each clarifying question. A notice's wording must be its
approved text exactly; its slots (the user's term, suggested names,
scopes, the number of drivers not described) are filled in like any
other.

The released text and claims are rendered from the statements only: each
statement is one line, list items led by "- " and items under them by
"  - ", and each statement that cites evidence is also a claim with the
same text, split into claims of at most 50 references. Scores are shown
to three significant figures; their slots hold the exact values. So no
sentence reaches the user that is not a statement of the candidate. The
contract is version 1.1, which added the notice on the drivers not
described and its slot; its JSON Schema is committed as
[schemas/candidate.schema.json](schemas/candidate.schema.json).

## Checks Before Generation

`validation.evidence` checks a request's bundle before any text is written.
The bundle has already been verified against its pinned manifest.

| Finding | Outcome |
| :--- | ---: |
| The Bundle Is Another Case's | Case Rejection, `identity_inconsistent` |
| The Bundle Is Another Scope's | Case Rejection, `scope_invalid` |
| Its Visits Are Not Numbered From Visit 1 Without a Gap, or Are Not the Selected Visit | Case Rejection, `history_inconsistent` |
| A Record Has Neither a Name nor a Code | Case Rejection, `evidence_inconsistent` |
| Coverage Is Not Complete | Abstention, `coverage_incomplete` |

An explanation request is checked the same way against its explanation
bundle, and also:

| Finding | Outcome |
| :--- | ---: |
| The Bundle's Prediction Is Not the One the Request Selects, by Its Output-Record Digest | Case Rejection, `prediction_link_invalid` |
| Its Visits Do Not End With the Final Admission | Case Rejection, `history_inconsistent` |
| The Coverage of the Linked History Is Not Complete | Abstention, `evidence_insufficient` |
| No Attribution Is Positive | Abstention, `no_positive_attribution` |
| No Driver May Be Described | Abstention, `explanation_ineligible` |

A case rejection releases no text.

## Structure and Reference Checks

`validation.structure` checks a candidate against the request it answers and
the bundle bound to that request:

| Finding | Reason Code |
| :--- | ---: |
| A Malformed Document; an Outcome, Reason Codes, Task or Bundle That Do Not Fit Together; a Digit in a Wording (Counts, Visits, Dates and Scores Are Slots); a Statement of a Kind the Answer Cannot Hold, or an Answer Without the Statements It Needs (a List Needs One Summary, at Least One Item and the Completeness Notice); a Wording Whose Placeholders Are Not Exactly Its Kind's Slots; a Changed Notice, a Notice Stated Twice, a Notice of Another Task, or an Answer Without a Notice Its Task and Outcome Require (Every "not recorded" Says That It Is Not Clinical Absence, a List That It Is Complete, a Time That Dates Are Shifted); a Visit's or the Person's Field That Is Not of Its Statement's Kind; Statements Out of Their Answer's Order (a List's Items After Its Summary, the Notice at the End; an Explanation's Prediction First, Then the Notice Introducing the Drivers, Each Driver Followed by Its Own History and Context, Then the Other Notices); a Statement That Cites What Its Kind Does Not Cite; a Response the Renderer Cannot Build Within the Response Limits | `structure_invalid` |
| Another Request's Case or Scope; a Statement About Another Scope; Another Bundle Than the One Bound to the Request; a Request With a Bound Bundle Answered Without It, Such as a Refusal of an Answerable Request; a Presence Answer About Another Condition Than the One the Question Was Resolved To | `scope_violated` |
| A Reference, Event, Driver, Visit Number or Concept That the Bundle or the Vocabulary Lacks | `reference_unresolved` |

The checks extend the citation check of `evidence.schema` to candidates.
The order, the notices stated twice, the bound bundle and the condition
asked about were added in Phase 7 for the mistakes a language model can
make and a template cannot. The checks say nothing about whether the cited
records support what a statement says; that is the next check.

## Support

`validation.support` decides whether the cited records support what each
statement of an answer says. Any failure is `claim_unsupported`.

| Statement | Supported When |
| :--- | ---: |
| A List Item | It Names the Condition of One of the Records It Cites; Every Record It Cites Is of That Condition (the Same Standard Concept, or the Same Source Code for a Record Without One); Its Count and Visits Are Those of the Cited Records |
| A List | It Names Every Condition of the Scope Once and Cites Every Record of the Scope Once; Its Summary Counts the Scope's Conditions and Records; Its Note on Records Without a Standard Concept Counts Them |
| An Empty List | Coverage Is Complete and Empty |
| A Presence Item | Every Record It Cites Matches the Resolved Condition by the Match Kind It States, Found With the Resolver's Own Matching; Its Count and Visits Are Those of the Cited Records |
| A Presence Answer | It Cites Every Matching Record of the Scope, Names Each Condition Once for Each Match Kind, and Its Summary Counts the Matching Records |
| Not Recorded | Coverage Is Complete and No Record of the Scope Matches the Condition; Its Note on Records Without a Standard Concept Counts Them |
| Any Answer | It Discloses `unmapped_records_in_scope` Exactly When the Scope Holds Records Without a Standard Concept |

Refusals and clarifying questions state no recorded facts and have nothing
to support.

### Answers About the Whole Record

An answer about the record (`qa.record_answer`) names records, visits and
the person by reference (`R`, `V`, `D1`), and its text is rendered from
them (`validation.record_text`), so no value, time, date or age is typed.
Its statement kinds, their citations and their order are checked like
the others, and `validation.record_support` decides whether the right
records were chosen and none was left out: a list names every item of
the kind once and cites each record once, ICU stays and transfers in time
order; a presence answer cites every
record that matches, each by the match kind the resolver finds; a time
answer's first and last records are the earliest and latest; a count is
the number of matching records; a value is the one its selector picks
for its item, and every matching item is answered; flagged results are
every flagged result of the matching items; visits' and the person's
details cover every visit of the scope; and the disclosure of records
without a standard concept follows the scope. A "not recorded" about a
named condition, medication or procedure states the scope's records of
the kind without a standard concept, and their number. A single record
is stated as the only one; the latest stands in for the highest or
lowest only when no value of the item is a number. An answer's notices
are exactly those its question needs: that "not recorded" is not
clinical absence, that a list is complete, that vital signs are charted
in ICU stays, that dates are shifted, what an age or a date of death
means, that an unflagged result is not stated to be normal, and that
vital signs carry no flag. The words "latest", "highest" and the like
are slots of a statement, rendered from its selector, never typed.

Each notice names the tasks it belongs to. The notices of answers about
the whole record were first open to every task, so a condition answer
could have carried one in place of its own (a "not recorded" with the
note on vital signs instead of the one on clinical absence) and passed;
the edge-case review found it, and notices are now tied to their tasks,
and the notices an answer must hold are required by its task and
outcome. The same review found three faults of the support checks, now
refused: a "first" value stated as the latest, a "not recorded" list
with a second statement about an item, and a visit's or the person's
field of another statement kind, which failed the rendering instead of
being refused.

## Wording

`validation.language` reads the wording of every statement of every
released outcome: answers, refusals and clarifying questions. It reads
only the writer's words: each slot is replaced by a neutral token first,
so a condition named "Pneumonia caused by a virus" never trips a rule.
Rules version `wording-rules-v5`:

| Rule | Examples | Reason Code |
| :--- | :---: | ---: |
| Causal or Directional Attribution | "caused", "led to", "due to", "raised the risk", "risk factor", "associated with", "secondary to" | `prohibited_wording` |
| Percentage Contribution | "40%", "percent", "a share of" | `prohibited_wording` |
| Exclusivity | "the only diagnosis", "solely", "nothing else" | `prohibited_wording` |
| Clinical Status, Severity or Course | "the patient has" as a Fact, "severe", "resolved", "worsening", "chronic", "currently", "active" | `prohibited_wording` |
| Advice | "should", "recommend", "start therapy" | `prohibited_wording` |
| Certainty | "definitely", "proves", "confirms", "clearly" | `prohibited_wording` |
| Diagnosis | "diagnosed with", "consistent with", "likely has", "suggests", "indicates", "a sign of" | `prohibited_wording` |
| Model Performance | "AUROC", "accuracy" | `prohibited_wording` |
| In Explanations, the Score as a Risk or Probability | "a risk of", "the probability" | `prohibited_wording` |
| In Explanations, the Prediction as Prospective | "in advance", "forecast" | `prohibited_wording` |
| In Answers and Explanations, Hedged Facts | "possibly", "may", "seems to" | `prohibited_wording` |
| In Answers and Explanations, Rankings | "the main", "most important", "key" | `prohibited_wording` |
| In Answers and Explanations, Counts or Quantities in Words | "three", "several", "most" | `prohibited_wording` |
| A Statement That Something Is Recorded (a Summary or Item of an Answer, the Prediction, a Driver, Its History and Context; an Answer About the Whole Record's List, Presence, Time, Count, Value, Flagged Results, Visit and Person), Negated | "No conditions are recorded" Above a List | `prohibited_wording` |
| A Statement That Something Is Not Recorded (an Empty List, a Condition, Kind or Item Not Recorded) Without a Negated "recorded" Wherever It Says "recorded" | "{condition} is recorded within {scope}" for an Absence | `prohibited_wording` |
| Dates, Recurrence, Frequency and Recency Typed Into a Wording | A Date, a Month, "again", "recurs", "often", "every visit", "recently", "the last admission" | `date_unsupported` |

A negation shortly before a causal, diagnostic or risk word, in the same
clause, lets it pass, so "attribution does not mean causation", "this
application does not diagnose" and "not a clinical risk" are allowed.
Version 1 also let a negation in an earlier clause pardon such a word
("X is not recorded within the scope, caused by the final admission");
the mutation suite found it, and version 2 reads negations within their
clause only. Version 3 adds the phrasings a language model writes around
facts and a template never does; every fact of an answer or explanation
is certain, unranked and stated by a slot, so hedges, rankings and counts
in words are prohibited there, while refusals and clarifying questions
may use them. Version 4 reads a wording with its statement's kind, after
a language model worded a list's summary "No conditions are recorded
..." on invented data and the gate released it: a statement that
something is recorded may hold a negation only where it denies a
causal, diagnostic or risk reading, and a statement that something is
not recorded must negate every "recorded" it holds. Version 5 reads the
statements of answers about the whole record with their kinds too; the
templates write them, and a writer that is not the templates is held to
the same rules. How often and in
which visits a driver's concept was recorded is stated only through the
slots of a statement that cites the driver's linked events, as the
evidence claim policy allows; QA answers state it only as the records
they cite.
Every template wording and approved notice passes the rules in the
outcomes and statement kinds it is used in, and a test keeps it so.

The rules are patterns: a sentence that says something prohibited in
words no pattern knows is not caught. A gap found later becomes a new
rules version, measured again.

## Explanations

An explanation describes the selected drivers: the eligible drivers with
the highest attribution scores, highest first, ties in the bundle's
order, at most the number configured in `config/explanation.json`
(ADR 0013, ADR 0014). Every attribution stays in the evidence bundle for
inspection. `validation.explanation` builds the template explanation of a
selected prediction from its explanation bundle alone, and checks any
explanation (`explanation-template-v3`):

1. the selected label, as one output of the model's multi-label
   prediction, with its uncalibrated score and logit;
2. the selected drivers, highest score first, each with its attribution
   score;
3. under each driver, how many of its concept's records lie in which
   pre-target visits, citing its linked history events, and its
   final-admission records as context, citing those;
4. when more drivers are eligible, a notice that states how many are not
   described and are kept in the evidence;
5. notices on what attribution scores and the model's score mean, and one
   for each restriction of the case.

Its checks apply to any explanation candidate, whoever wrote it:

| Finding | Reason Code |
| :--- | ---: |
| The Prediction Is Not Stated Once, With the Bundle's Label, and Its Score and Logit as a Response Shows Them | `prediction_altered` |
| The Drivers Described Are Not Exactly the Selected Drivers, in Their Order, With Their Ranks, and Their Scores as a Response Shows Them; a Zero-Score Driver, or a Driver Not Selected, Is Described | `attribution_altered` |
| A History Statement Cites Other Records Than the Driver's Linked Events in Pre-Target Visits, or a Context Statement Other Records Than Its Final-Admission Ones, or Either Miscounts Them or Their Visits; the Restrictions Disclosed, as Reason Codes or Notices, Are Not the Case's; the Drivers Not Described Are Not Stated, or Are Miscounted | `claim_unsupported` |
| A Notice on What the Scores Mean Is Missing | `structure_invalid` |

With the structure checks and the wording rules this enforces the
evidence claim policy (`evidence-policy-v1`): no score is tied to a date,
a visit or a recurrence (history statements carry no score and driver
statements no visits); history cites linked events in pre-target visits
only; final-admission records are context only; zero-score drivers are
never described; the label is one output, never the only diagnosis; the
score is never a clinical risk.

A score or logit is checked as a response shows it, to three significant
figures: that is the claim the response makes, and no released text can
differ from the one the exact value renders. The template states the
exact values. A driver's history or context may be left out: a line that
is stated must cite all of its records, since it states their count, but
none is required. How many of the available lines a writer states is
measured apart from the release (`generation.measure`), so that saying
less is never mistaken for being right (ADR 0014).

Every case of the development case sets gets its template explanation
with the configured number of drivers, 5, and every one passes all
checks:

| Case Set | Explanations | Drivers Described | Drivers Not Described | History Statements | Context Statements | Claims | Restrictions Disclosed (Batch-Sensitive, Final-Admission Drivers, Excluded Outcome Entries) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | ---: |
| `dev-30-v1` | 26 of 26 | 130 | 448 | 82 | 59 | 297 | 6, 26, 10 |
| `pilot-10-v1` | 10 of 10 | 47 | 141 | 36 | 14 | 107 | 2, 9, 3 |

Version 1 described every eligible driver in the model's order: 578
drivers on `dev-30-v1` and 188 on `pilot-10-v1`; with 10 drivers, version
2 described 259 and 92.

## Release Gate

Every response with text leaves the application through
`validation.gate.release`, for QA answers (`qa.answer`, rules
`qa-answer-v5`) and explanations alike, whoever wrote them. The gate is
given the request, the bundle bound to it and, for a presence check, the
condition the question was resolved to. It runs every check the
candidate's answer requires: structure and references always, then
support for lists and presence checks, preservation for explanations and
the wording rules for every outcome, and finally the citation check on
the rendered response. Then:

| Finding | Released |
| :--- | ---: |
| Every Check Passes | The Response, Rendered From the Candidate's Statements Only |
| A Check Finds Problems | A Validation Rejection With Their Reason Codes; No Text, No Claims |
| A Check Fails to Run | An Operational Error, `validator_failed`; No Text |

Since the text is rendered from checked statements only, a candidate
cannot carry a sentence past the checks: an extra sentence in a notice is
a changed notice, and a fact typed into a wording is a digit outside the
slots. Responses without text (operational errors and case rejections)
have no candidate. The QA development suite gives the same results
through the gate (see [qa.md](qa.md#development-suite)).

## Mutation Suite

`validation.mutations` tests the checks by breaking correct responses
(`validation-mutations-v4`). Correct candidates are built by the
templates: for every scope bundle of every case, the list and presence
checks of a recorded condition and of one recorded elsewhere; refusals
and clarifying questions; and every case's template explanation. Every
error class is applied to them, or to the bundles they come from, and
checked for the request each answers, with its bundle bound. Version 2
added the mistakes a language model can make and a template cannot,
version 3 the mistakes of driver selection and of a statement's
polarity, and version 4 checks scores as a response shows them:

| Item | Error Classes | Expected |
| :--- | :---: | ---: |
| 35 | Bundle of Another Case, Bundle of Another Scope, History Gap, Record Without a Name, Prediction Not Selected | Case Rejection |
| 35 | Incomplete Coverage | Abstention |
| 36 | Malformed Document, Missing Slot, Fact Typed Into a Wording, Changed Notice | `structure_invalid` |
| 36 | Absent Reference | `reference_unresolved` |
| 36 | Another Patient, Another Scope, Another Bundle | `scope_violated` |
| 37 | Omitted Diagnosis, Added Diagnosis, Wrong Count, Wrong Visits, Record of Another Condition, Unsupported Positive, Negative for a Recorded Condition, Negative Without Complete Coverage | `claim_unsupported` |
| 38 | Altered Label, Altered Score | `prediction_altered` |
| 38 | Altered Attribution Score, Reordered Drivers, Omitted Driver, Zero-Score Driver Described | `attribution_altered` |
| 38 | Final-Admission Record as History, Restriction Misdisclosed | `claim_unsupported` |
| 39 | Causal, Directional, Percentage, Exclusive, Diagnostic, Advice and Status Wording | `prohibited_wording` |
| 39 | A Date or Recurrence Typed Into a Wording | `date_unsupported` |
| 40 | A Check That Fails to Run | Operational Error, `validator_failed` |
| Model (v2) | A Refusal of an Answerable Request, an Answer About Another Condition | `scope_violated` |
| Model (v2) | A Missing, Extra or Repeated Notice; Statements Out of Their Order; a Driver's History Under Another Driver | `structure_invalid` |
| Model (v2) | An Empty List for a Scope With Records; a Fact or the Name of Another Record | `claim_unsupported` |
| Model (v2) | Hedged, Ranking, Interpretive, Association, Course and Certainty Wording; a Count in Words | `prohibited_wording` |
| Model (v2) | Recency Typed Into a Wording | `date_unsupported` |
| Selection (v3) | A Driver Not Selected Described | `attribution_altered` |
| Selection (v3) | The Drivers Not Described Miscounted, or Not Stated | `claim_unsupported` |
| Polarity (v3) | A Statement That Something Is Recorded Negated; a Statement That Something Is Not Recorded Worded as a Record | `prohibited_wording` |
| Scores (v4) | A Score Wrong in Its Last Shown Figure | `prediction_altered`, `attribution_altered` |

A mutant is detected when it ends in its class's outcome with its class's
reason code. Every original must be released unchanged, and so must
every control: a correct statement that a record does not mean
causation, added to an answer; and in an explanation, a driver's history
or context left out, and scores stated to the figures a response shows.
Results, with the evaluation case set untouched:

| Samples | Originals Released Unchanged | Controls Passed | Mutants Detected | Error Classes |
| :--- | :---: | :---: | :---: | ---: |
| Invented Fixtures | 18 of 18 | 21 of 21 | 584 of 584 | 64 |
| `dev-30-v1` | 434 of 434 | 530 of 530 | 17,414 of 17,414 | 65 |
| `pilot-10-v1` | 186 of 186 | 220 of 220 | 7,356 of 7,356 | 65 |

Version 1 detected all 15,558 mutants of its 40 classes on the
development case sets, version 2 all 23,999 of its 58, and version 3 all
24,772 of its 65, when it still required a driver's history and context.
The suite
found one gap on its first run, in wording rules version 1 (see
[Wording](#wording)), which version 2 closes. It measures the defined
error classes; a statement that says something prohibited in words no
rule knows is outside what it can show, which is why the facts stay in
slots that no writer types.

### Mutants of Answers About the Whole Record

The answers about the whole record are broken the same way, one fact at
a time, by `tests/validation/test_record_mutations.py`: a statement left
out or repeated, a digit typed into a wording, a count, selector, match
kind, field, kind, visit, value, time or named item changed, a reference
dropped, added or swapped for a record of another item, a notice
replaced or added, the outcome, the disclosure, the scope, the case or
the bundle changed, a summary moved after its items, and two ICU stays
or transfers swapped. A mutant is
withheld when the gate rejects it or it does not parse; the originals
and the controls (a list or presence item named by another of its own
records) must be released.

| Samples | Answers | Mutants Withheld | Controls Released |
| :--- | :---: | :---: | ---: |
| Invented Edge Record (Every Task and Outcome) | 38 | 1,481 of 1,481 | All |
| Development Case Sets (3 Cases of Each, All Visits and the History, 25 Questions) | 300 | 30,921 of 30,921 | 768 of 768 |

On their first run the tests found two faults, now refused: a visit's or
the person's field of another statement kind failed the rendering (an
operational error instead of a rejection), and a single record stated
as the highest passed. The swapped stays were added with
[ADR 0023](decisions/0023-consistency-and-junk-input.md) (the counts
before it were 1,479 and 30,703). On the development case sets the
counts were then 30,719 mutants and 762 controls; the record rules
changed after them in release 0.11.0 (drug-class words, medications
named by their written drug, names read whole) answer some of the
sampled questions otherwise, and release 1.0.0 gives the counts above,
as do the rules after it (2026-10-06).

### Wrong Answers That Agree With Themselves

A writer can get more than one fact wrong at once, and at worst in a way
that agrees with itself: a record left out and every count lowered to
match, which no check of the answer against itself finds.
`tests/validation/test_consistent_mistakes.py`
([ADR 0023](decisions/0023-consistency-and-junk-input.md)) makes three
kinds of such answers from correct ones of every task, about the whole
record and about conditions (lists, presence checks and explanations):

- two mistakes at once: a one-fact mutant broken again by a second;
- an answer the templates write from a copy of the record with one
  record misread (left out, moved after or before every other record,
  given a higher or lower value, its flag turned, taken for each of up
  to three other items, or moved to another visit), so its counts,
  references and times agree with each other, checked against the real
  record;
- the right answer to another question, scope or condition, given as
  this one's.

Each must be withheld unless it states what the correct answer states:
a list or presence item may be named by any of its own records, and the
groups of different items and the notices may come in any order, since
no reader relies on it. A misreading that leaves the answer as it was is
not a mistake and is not counted.

| Samples | Two Mistakes at Once | From a Misread Record | Another's Answer |
| :--- | :---: | :---: | ---: |
| Invented Edge Record: 38 Answers About the Record | 1,761 of 1,761 Withheld | 688 of 688 | 320 of 320 |
| Invented Case Set: 14 Condition Answers | 651 of 651 | 29 of 29 | 62 of 62 |
| Development Case Sets: 300 Answers About the Record | 13,965 of 13,965 Withheld | 4,012 of 4,012 | 2,230 of 2,230 |
| Development Case Sets: 576 Condition Answers | 13,643 of 13,643 Withheld | 7,191 of 7,191 | 6,846 of 6,846 |

On their first run the tests found that a list of ICU stays or transfers
out of time order was released: the check counted every stay but not
their order, which for transfers is the patient's path through the
hospital. It is checked now, and a one-fact mutant swaps two stays. On
the development case sets 6 answers of the first run were released
that list the groups of flagged results in another order, each group
with exactly its item's results: the same answer, in another order.

## Commands

The default modes check invented candidates and bundles:

```powershell
& $AppPython -m ehr_evidence_app.validation.candidate
& $AppPython -m ehr_evidence_app.validation.structure
& $AppPython -m ehr_evidence_app.validation.evidence
& $AppPython -m ehr_evidence_app.validation.support
& $AppPython -m ehr_evidence_app.validation.language
& $AppPython -m ehr_evidence_app.validation.explanation
& $AppPython -m ehr_evidence_app.validation.gate
& $AppPython -m ehr_evidence_app.validation.mutations
```

Operator modes explain every admitted case of a development case set, or
run the mutation suite on it, and print aggregate counts only; they
refuse the evaluation case set:

```powershell
& $AppPython -m ehr_evidence_app.validation.explanation --operator-real --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.validation.explanation --operator-real --case-set pilot-10-v1
& $AppPython -m ehr_evidence_app.validation.mutations --operator-real --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.validation.mutations --operator-real --case-set pilot-10-v1
```
