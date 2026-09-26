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
| 37 | `validation.support` | Support of every QA claim by its cited records | planned |
| 38 | `validation.explanation` | Template explanation; prediction and attribution preservation | planned |
| 39 | `validation.language` | Wording rules for every outcome; dates and recurrence | planned |
| 40 | `validation.gate` | The release gate | planned |
| 40.1 | `validation.mutations` | Mutation suite: every error class applied to correct responses | planned |

## Candidates

A writer (the QA templates in `qa.templates` now, the language model of
Phase 7) produces a candidate, never released text. A candidate names its
request, case, scope, outcome, reason codes and, for an answer, its bundle,
and holds an ordered list of statements. Each statement has:

- a **kind** from a closed list, which fixes the facts it states, what it
  cites and the answers it may appear in;
- its facts as typed **slots**. Slot values are references and numbers; the
  text they show is rendered from the evidence bundle and the vocabulary, so
  a writer never types a condition name, a count or a visit;
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
| `notice` | the notice's own | nothing | any released outcome it is approved for |

A condition is named by a recorded event (its label comes from the bundle),
by vocabulary concepts (their names come from the vocabulary) or by an ICD
code. A notice is one of a fixed list of approved texts
(`validation.candidate.NOTICES`): that "not recorded" is not a statement
of clinical absence, that a list is complete, each refusal and each
clarifying question. A notice's wording must be its approved text
exactly; its slots (the user's term, suggested names, scopes) are filled in
like any other.

The released text and claims are rendered from the statements only: each
statement is one line, list items led by "- ", and each statement that
cites records is also a claim with the same text, split into claims of at
most 50 references. So no sentence reaches the user that is not a
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

A case rejection releases no text. Explanation requests are checked with
their prediction and attributions in work item 38.

## Structure and reference checks

`validation.structure` checks a candidate against the request it answers and
the bundle bound to that request:

| Finding | Reason code |
| --- | --- |
| A malformed document; an outcome, reason codes, task or bundle that do not fit together; a statement of a kind the answer cannot hold, or an answer without the statements it needs (a list needs one summary, at least one item and the completeness notice); a wording whose placeholders are not exactly its kind's slots; a changed notice; a statement that cites what its kind does not cite; a response the renderer cannot build within the response limits | `structure_invalid` |
| Another request's case or scope; a statement about another scope; another bundle than the one bound to the request | `scope_violated` |
| A reference, event, visit number or concept that the bundle or the vocabulary lacks | `reference_unresolved` |

The checks extend the citation check of `evidence.schema` to candidates.
They say nothing about whether the cited records support what a statement
says; that is work item 37.

## QA responses

Every QA response with text is written as a candidate by `qa.templates`,
checked, and rendered from the candidate (`qa.answer`, rules
`qa-answer-v5`). A candidate that fails a check is withheld: the response is
a validation rejection with the check's reason codes and no text. Responses
without text (operational errors and case rejections) have no candidate.
The development suite gives the same results with the answers written this
way (see [qa.md](qa.md#development-suite)).

## Commands

The default modes check invented candidates and bundles:

```powershell
& $AppPython -m ehr_evidence_app.validation.candidate
& $AppPython -m ehr_evidence_app.validation.structure
& $AppPython -m ehr_evidence_app.validation.evidence
```
