# Validation

Phase 6 makes sure nothing reaches a user unless it has been checked
against the evidence: checks before any text is written, checks on every
candidate response, and one release gate that withholds whatever fails. It
is built before the language model of Phase 7 exists, so that model can only
reword text that passes the same checks. Nothing here loads a model.

The design is recorded in
[ADR 0011](decisions/0011-validation-design.md). Command examples assume the
session set-up in [development.md](development.md#session-set-up).

## Modules

| Work item | Module | Role | State |
| --- | --- | --- | --- |
| 35 | `validation.evidence` | Checks before generation | done |
| 36 | `validation.candidate`, `validation.structure` | The candidate contract; structure and reference checks | done |
| 37 | `validation.support` | Support of every QA statement by its cited records | done |
| 38 | `validation.explanation` | Template explanation; prediction and attribution preservation | done |
| 39 | `validation.language` | Wording rules for every outcome; dates and recurrence | done |
| 40 | `validation.gate` | The release gate | done |
| 40.1 | `validation.mutations` | Mutation suite: every error class applied to correct responses | done |

## Candidates

A writer (the QA templates in `qa.templates` and the template explanation
in `validation.explanation` now, the language model of Phase 7) produces a
candidate, never released text. A candidate names its
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

| Kind | Slots | Cites | Appears in |
| --- | --- | --- | --- |
| `list_summary` | `groups`, `scope`, `records` | nothing; it restates the items | list answers |
| `list_item` | `condition`, `count`, `visits` | the condition's records | list answers |
| `list_unmapped` | `unmapped` | the coverage record | list answers |
| `list_empty` | `scope` | the coverage record | empty lists |
| `presence_summary` | `condition`, `scope`, `matches` | nothing; it restates the items | presence answers |
| `presence_item` | `condition`, `count`, `visits`, `match` | the matching records | presence answers |
| `presence_absent` | `condition`, `scope` | the coverage record | not recorded |
| `presence_unmapped` | `unmapped` | the coverage record | not recorded |
| `prediction` | `label`, `score`, `logit` | the prediction (P1) | explanations |
| `driver` | `rank`, `driver`, `score` | the driver's attribution | explanations |
| `driver_history` | `driver`, `count`, `visits` | the driver's linked records in pre-target visits | explanations |
| `driver_context` | `driver`, `count` | the driver's records in the final admission | explanations |
| `notice` | the notice's own | nothing | any released outcome it is approved for |

A condition is named by a recorded event (its label comes from the bundle),
by vocabulary concepts (their names come from the vocabulary) or by an ICD
code. A notice is one of a fixed list of approved texts
(`validation.candidate.NOTICES`): that "not recorded" is not a statement
of clinical absence, that a list is complete, what attribution scores
and the model's score mean, each restriction of an explanation, each
refusal and each clarifying question. A notice's wording must be its approved text
exactly; its slots (the user's term, suggested names, scopes) are filled in
like any other.

The released text and claims are rendered from the statements only: each
statement is one line, list items led by "- " and items under them by
"  - ", and each statement that cites evidence is also a claim with the
same text, split into claims of at most 50 references. Scores are shown
to three significant figures; their slots hold the exact values. So no sentence reaches the user that is not a
statement of the candidate. The contract's JSON Schema is committed as
[schemas/candidate.schema.json](schemas/candidate.schema.json).

## Checks before generation

`validation.evidence` checks a request's bundle before any text is written.
The bundle has already been verified against its pinned manifest.

| Finding | Outcome |
| --- | --- |
| The bundle is another case's | case rejection, `identity_inconsistent` |
| The bundle is another scope's | case rejection, `scope_invalid` |
| Its visits are not numbered from visit 1 without a gap, or are not the selected visit | case rejection, `history_inconsistent` |
| A record has neither a name nor a code | case rejection, `evidence_inconsistent` |
| Coverage is not complete | abstention, `coverage_incomplete` |

An explanation request is checked the same way against its explanation
bundle, and also:

| Finding | Outcome |
| --- | --- |
| The bundle's prediction is not the one the request selects, by its output-record digest | case rejection, `prediction_link_invalid` |
| Its visits do not end with the final admission | case rejection, `history_inconsistent` |
| The coverage of the linked history is not complete | abstention, `evidence_insufficient` |
| No attribution is positive | abstention, `no_positive_attribution` |
| No driver may be described | abstention, `explanation_ineligible` |

A case rejection releases no text.

## Structure and reference checks

`validation.structure` checks a candidate against the request it answers and
the bundle bound to that request:

| Finding | Reason code |
| --- | --- |
| A malformed document; an outcome, reason codes, task or bundle that do not fit together; a digit in a wording (counts, visits, dates and scores are slots); a statement of a kind the answer cannot hold, or an answer without the statements it needs (a list needs one summary, at least one item and the completeness notice); a wording whose placeholders are not exactly its kind's slots; a changed notice; a statement that cites what its kind does not cite; a response the renderer cannot build within the response limits | `structure_invalid` |
| Another request's case or scope; a statement about another scope; another bundle than the one bound to the request | `scope_violated` |
| A reference, event, driver, visit number or concept that the bundle or the vocabulary lacks | `reference_unresolved` |

The checks extend the citation check of `evidence.schema` to candidates.
They say nothing about whether the cited records support what a statement
says; that is the next check.

## Support

`validation.support` decides whether the cited records support what each
statement of an answer says. Any failure is `claim_unsupported`.

| Statement | Supported when |
| --- | --- |
| A list item | It names the condition of one of the records it cites; every record it cites is of that condition (the same standard concept, or the same source code for a record without one); its count and visits are those of the cited records |
| A list | It names every condition of the scope once and cites every record of the scope once; its summary counts the scope's conditions and records; its note on records without a standard concept counts them |
| An empty list | Coverage is complete and empty |
| A presence item | Every record it cites matches the resolved condition by the match kind it states, found with the resolver's own matching; its count and visits are those of the cited records |
| A presence answer | It cites every matching record of the scope, and its summary counts them |
| Not recorded | Coverage is complete and no record of the scope matches the condition; its note on records without a standard concept counts them |
| Any answer | It discloses `unmapped_records_in_scope` exactly when the scope holds records without a standard concept |

Refusals and clarifying questions state no recorded facts and have nothing
to support.

## Wording

`validation.language` reads the wording of every statement of every
released outcome: answers, refusals and clarifying questions. It reads
only the writer's words: each slot is replaced by a neutral token first,
so a condition named "Pneumonia caused by a virus" never trips a rule.
Rules version `wording-rules-v2`:

| Rule | Examples | Reason code |
| --- | --- | --- |
| Causal or directional attribution | "caused", "led to", "due to", "raised the risk", "risk factor" | `prohibited_wording` |
| Percentage contribution | "40%", "percent", "a share of" | `prohibited_wording` |
| Exclusivity | "the only diagnosis", "solely", "nothing else" | `prohibited_wording` |
| Clinical status, severity or course | "the patient has" as a fact, "severe", "resolved", "worsening" | `prohibited_wording` |
| Advice | "should", "recommend", "start therapy" | `prohibited_wording` |
| Certainty | "definitely", "proves", "confirms" | `prohibited_wording` |
| Diagnosis | "diagnosed with", "consistent with", "likely has" | `prohibited_wording` |
| Model performance | "AUROC", "accuracy" | `prohibited_wording` |
| In explanations, the score as a risk or probability | "a risk of", "the probability" | `prohibited_wording` |
| In explanations, the prediction as prospective | "in advance", "forecast" | `prohibited_wording` |
| Dates, recurrence and frequency typed into a wording | a date, a month, "again", "recurs", "often", "every visit" | `date_unsupported` |

A negation shortly before a causal, diagnostic or risk word, in the same
clause, lets it pass, so "attribution does not mean causation", "this
application does not diagnose" and "not a clinical risk" are allowed.
Version 1 also let a negation in an earlier clause pardon such a word
("X is not recorded within the scope, caused by the final admission");
the mutation suite found it, and version 2 reads negations within their
clause only. How often and in which
visits a driver's concept was recorded is stated only through the slots
of a statement that cites the driver's linked events, as the evidence
claim policy allows; QA answers state it only as the records they cite.
Every template wording and approved notice passes the rules in the
outcomes it is used in, and a test keeps it so.

The rules are patterns: a sentence that says something prohibited in
words no pattern knows is not caught. A gap found later becomes a new
rules version, measured again.

## Explanations

`validation.explanation` builds the template explanation of a selected
prediction from its explanation bundle alone (`explanation-template-v1`):

1. the selected label, as one output of the model's multi-label
   prediction, with its uncalibrated score and logit;
2. every driver that may be described, in the order the model lists
   them, with its attribution score;
3. under each driver, how many of its concept's records lie in which
   pre-target visits, citing its linked history events, and its
   final-admission records as context, citing those;
4. notices on what attribution scores and the model's score mean, and one
   for each restriction of the case.

Its checks apply to any explanation candidate, whoever wrote it:

| Finding | Reason code |
| --- | --- |
| The prediction is not stated once, with the bundle's label, score and logit | `prediction_altered` |
| The drivers described are not exactly the bundle's describable drivers, in its order, with its scores and ranks; a zero-score driver is described | `attribution_altered` |
| A history statement cites other records than the driver's linked events in pre-target visits, or a context statement other records than its final-admission ones, or either miscounts them or their visits; the restrictions disclosed, as reason codes or notices, are not the case's | `claim_unsupported` |
| A notice on what the scores mean is missing | `structure_invalid` |

With the structure checks and the wording rules this enforces the
evidence claim policy (`evidence-policy-v1`): no score is tied to a date,
a visit or a recurrence (history statements carry no score and driver
statements no visits); history cites linked events in pre-target visits
only; final-admission records are context only; zero-score drivers are
never described; the label is one output, never the only diagnosis; the
score is never a clinical risk.

Every case of the development case sets gets its template explanation,
and every one passes all checks:

| Case set | Explanations | Drivers | History statements | Context statements | Claims | Restrictions disclosed (batch-sensitive, final-admission drivers, excluded outcome entries) |
| --- | --- | --- | --- | --- | --- | --- |
| `dev-30-v1` | 26 of 26 | 578 | 404 | 250 | 1,258 | 6, 26, 10 |
| `pilot-10-v1` | 10 of 10 | 188 | 142 | 71 | 412 | 2, 9, 3 |

## Release gate

Every response with text leaves the application through
`validation.gate.release`, for QA answers (`qa.answer`, rules
`qa-answer-v5`) and explanations alike. The gate runs every check the
candidate's answer requires: structure and references always, then
support for lists and presence checks, preservation for explanations and
the wording rules for every outcome, and finally the citation check on
the rendered response. Then:

| Finding | Released |
| --- | --- |
| Every check passes | The response, rendered from the candidate's statements only |
| A check finds problems | A validation rejection with their reason codes; no text, no claims |
| A check fails to run | An operational error, `validator_failed`; no text |

Since the text is rendered from checked statements only, a candidate
cannot carry a sentence past the checks: an extra sentence in a notice is
a changed notice, and a fact typed into a wording is a digit outside the
slots. Responses without text (operational errors and case rejections)
have no candidate. The QA development suite gives the same results
through the gate (see [qa.md](qa.md#development-suite)).

## Mutation suite

`validation.mutations` tests the checks by breaking correct responses
(`validation-mutations-v1`). Correct candidates are built by the
templates: for every scope bundle of every case, the list and presence
checks of a recorded condition and of one recorded elsewhere; refusals
and clarifying questions; and every case's template explanation. Every
error class is applied to them, or to the bundles they come from:

| Item | Error classes | Expected |
| --- | --- | --- |
| 35 | bundle of another case, bundle of another scope, history gap, record without a name, prediction not selected | case rejection |
| 35 | incomplete coverage | abstention |
| 36 | malformed document, missing slot, fact typed into a wording, changed notice | `structure_invalid` |
| 36 | absent reference | `reference_unresolved` |
| 36 | another patient, another scope, another bundle | `scope_violated` |
| 37 | omitted diagnosis, added diagnosis, wrong count, wrong visits, record of another condition, unsupported positive, negative for a recorded condition, negative without complete coverage | `claim_unsupported` |
| 38 | altered label, altered score | `prediction_altered` |
| 38 | altered attribution score, reordered drivers, omitted driver, zero-score driver described | `attribution_altered` |
| 38 | final-admission record as history, restriction misdisclosed | `claim_unsupported` |
| 39 | causal, directional, percentage, exclusive, diagnostic, advice and status wording | `prohibited_wording` |
| 39 | a date or recurrence typed into a wording | `date_unsupported` |
| 40 | a check that fails to run | operational error, `validator_failed` |

A mutant is detected when it ends in its class's outcome with its class's
reason code. Every original must be released unchanged, and so must a
control: a correct statement that a record does not mean causation,
added to an answer. Results, with the evaluation case set untouched:

| Samples | Originals released unchanged | Controls passed | Mutants detected | Error classes |
| --- | --- | --- | --- | --- |
| Invented fixtures | 17 of 17 | 13 of 13 | 345 of 345 | 39 |
| `dev-30-v1` | 434 of 434 | 430 of 430 | 10,935 of 10,935 | 40 |
| `pilot-10-v1` | 186 of 186 | 182 of 182 | 4,623 of 4,623 | 40 |

The suite found one gap on its first run, in wording rules version 1
(see [Wording](#wording)), which version 2 closes. It measures the
defined error classes; a statement that says something prohibited in
words no rule knows is outside what it can show, which is why the facts
stay in slots that no writer types.

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
