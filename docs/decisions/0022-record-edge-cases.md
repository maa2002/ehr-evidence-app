# 0022. The Whole Record at Its Edges

Status: accepted (2026-09-30). Amends [ADR 0021](0021-the-whole-record.md)
(points 5 to 7: terms, answers and checks), before any request of
`eval-100-v1` is written. Decision 7 amended by
[ADR 0023](0023-consistency-and-junk-input.md): a polite opening is
removed before a question is read (`record-questions-v3`).

## Context

I wanted the answers about the whole record tested at their edges. The
review read every new module and tested each
at its edges, on invented records built for them and, with aggregate
output only, on the development case sets. It found:

- **Condition questions taken over by weak matches.** "Is diabetes
  recorded?" is a condition question the condition rules cannot resolve
  (bare "diabetes" names no one concept), so they ask which condition is
  meant. Since ADR 0021 the question then went to the record's reading,
  where "diabetes" resolved as a procedure because the words of the
  434,913 procedure names hold it between them, and the answer was that
  diabetes is not recorded within the scope: a reply a reader could take
  for the absence of diabetes. "Is the patient's heart failure severe?"
  went the same way. The development sweep found both by comparing every
  condition question's response with and without the record (386
  responses differed, in every scope of every case).
- **Medications without a standard concept.** 1,840 of the 4,096
  medication records of the development case sets (45%) have no standard
  concept, and every case has some. A drug-class question ("any
  antibiotics?") matched them by nothing, and its "not recorded" did not
  say so: the disclosure `unmapped_records_in_scope` is a reason code the
  page shows only when no text is released. Measured by the ingredients
  of the classes' recorded drugs, some cases where a class was "not
  recorded" held it under a written drug name: 7 of 17 for opioids, 8 of
  25 for antiemetics, 3 of 26 for antibiotics.
- **Notices open to every task.** The notices of answers about the record
  were approved for any task, so a condition answer could carry one in
  place of its own, a "not recorded" with the note on vital signs instead
  of the note that it is not clinical absence, and pass every check.
- **Checks that let mistakes through, or failed.** A "first" value stated
  as the latest passed; so did a "not recorded" list with a second
  statement about an item, and a single record stated as the highest. A
  visit's or the person's field of another statement kind failed the
  rendering (an operational error instead of a rejection).
- **An error.** "Were any vitals abnormal?" (a flag vital signs do not
  carry, asked of a whole kind) raised an error; "were any labs high?"
  gave each test's latest value instead of the flagged results.
- **Readings.** A negation ("was heparin not given") left a phrase that
  named nothing; two items ("heparin or warfarin") were read as one
  unknown name; "on day 2" and "in the ICU" were not read as times that
  are not scopes; "is she male or female" was not read; a misspelling got
  suggestions only when the question named its kind, and none for
  medications or procedures.
- **Answers.** A value list named its item by a record other than the one
  its check expected when the order of the visits and the order in time
  differ (2 items of the development records), which would have been
  withheld; "what organisms grew" listed one result of each culture; a
  removed value was followed by its unit; a discharge recorded before its
  admission gave a negative length; numbers were rounded to six figures;
  procedures matched by the words of their names were said to have "no
  standard concept".

## Decision

1. **Weak matches never take a condition question.** A question that
   names no kind of record takes one only by a name or a synonym of it
   (a phrase that is a procedure's own name is one), or by the words of
   its names in a kind the question's verbs ("done", "given") or task
   point to (`qa.record_interpret`, `qa.record_resolve`). Otherwise the
   question is left to the condition rules, and gets the response it
   gets without the record: "is diabetes recorded" asks which diabetes
   is meant, as before ADR 0021.
2. **Record vocabulary version 2** (`qa.record_vocabulary`,
   `record-vocabulary-athena-20260831-v2`) adds `class_ingredients.tsv`:
   for every ATC class, the ingredients the Athena hierarchy places in
   it (83,300 pairs over 6,806 classes), from the same pinned release in
   the same single pass. Every file of version 1 is unchanged byte for
   byte, so the published record bundles, built with version 1's drug
   ancestors, stay as they are; version 1 stays published and pinned.
3. **Medications without a standard concept** (`qa.record_resolve`,
   rules `record-resolution-v2`) match a medication phrase by the drug
   name written in their prescription, as whole words: the phrase
   itself, the name of a resolved ingredient, or the name of any
   ingredient of a resolved class. A counter-ion that is an ingredient's
   whole name (sodium, potassium, calcium, magnesium) counts only as the
   written name's first word, so "heparin sodium" is heparin and not
   sodium. Word matches of procedures, microbiology and care units are
   said to match "the words of its name".
4. **A "not recorded" about a named condition, medication or procedure**
   (`qa.record_answer`, rules `record-answer-v2`) states how many records
   of its kind in the scope have no standard concept and how they were
   checked: their ICD codes and descriptions, their written drug names,
   or the words of their names (statement `record_unmapped`).
5. **Notices belong to their tasks.** Each approved notice names the
   tasks it may answer; every answer must hold the notices its task and
   outcome require (a "not recorded" that it is not clinical absence, a
   list that it is complete, a time that dates are shifted), and an
   answer about the record exactly the notices its question needs.
6. **Stricter support.** A single record is stated as the only one; the
   latest stands in for the highest or lowest only when no value is a
   number; a field is one of its statement kind's; an empty list states
   nothing else. The wording rules (`wording-rules-v5`) read the
   statements of answers about the record with their kinds.
7. **Readings** (`qa.record_interpret`, rules `record-questions-v2`). A
   negation asks the same presence, except in a question about a whole
   kind, where it is a filter. Two items that each resolve, flags a kind
   does not carry, and a day, hour or the ICU named as a time get a
   clarification. Flags are read alike for an item and for a whole kind.
   A dose question without an item asks for the medications' doses.
   Misspellings get suggestions of the kind a question's words or verbs
   name, for medications and procedures too.
8. **Answers** (`record-answer-v2`). A value list names each item by its
   earliest record and lists up to 30 results of each microbiology test;
   a removed value has no unit; a discharge before the admission gives no
   length; numbers are shown with every recorded digit and no exponent;
   a record without a name says whether it has no standard concept or its
   name is unavailable.
9. **Fail-closed inputs.** The raw extraction refuses a blank line in any
   table; the bundle builder refuses two visits of one admission and
   identifiers that are not ASCII digits; a bundle file must be the case
   and scope its manifest entry names. Runtime records name the three
   record rule versions.
10. **Tests.** An invented record built for these edges; every question
   of a list of 135 in every scope; the answers' checks broken one fact
   at a time (1,479 mutants, every one withheld, every original and
   control released); a randomised test of the raw-file reader at every
   chunk size; the builder, vocabulary, rendering, bundle cache under
   threads and evidence panel at their edges.

## Development Results

On the development case sets (`pilot-10-v1`, `dev-30-v1`), with
aggregate output only and the evaluation set untouched:

- The published record bundles rebuild byte for byte with the changed
  builder (193 of 193 bundles; both manifests match their pins).
- The condition development suite gives the summary digests it gave
  before (1,892 of 1,892 and 784 of 784 correct), and the validation
  mutation suite still withholds all 17,414 and 7,356 mutants: the
  changes leave every condition answer as it was.
- Every one of the invented record's 135 questions, with the heavy ones
  of the first development check, asked of every scope of every case
  through the service: 31,652 requests, 13,425 answers, 9,048 "not
  recorded", 8,214 clarifications and 965 abstentions; no rejection,
  error or exception (median 0.046 s, the slowest 1.33 s). Seventeen
  condition questions in every scope get the same response with and
  without the record (before decision 1, 386 responses of two of them
  differed).
- Thirteen drug-class questions over all visits of the 36 cases were
  "not recorded" in 278 case questions before and are in 237 now; 745
  medication records without a standard concept are found by their
  written names.
- The answers' checks broken one fact at a time on 300 answers of 6
  cases (the history and all visits, 25 questions): 30,703 of 30,703
  mutants withheld, every original and 762 of 762 controls released.

## Consequences

- Every condition question gets the response it gets without the
  record. A question that names no kind of record, and whose phrase only
  the words of some kind's names hold, is left to the condition rules
  too, so it may ask again ("any klebsiella?"); naming the kind or a verb
  ("did klebsiella grow?", "any klebsiella in the cultures?") answers it.
- A drug-class question finds medications without a standard concept
  whose written name holds an ingredient's whole name; abbreviated
  written names ("sulfameth") are not found, and the answer says how
  many records without a standard concept were checked.
- Some questions that were answered now ask again: two items at once, a
  day or the ICU named as a time, abnormal vital signs without high or
  low, and abnormal medications or procedures.
- The rules have new versions; the evaluation's record strata (ADR 0016,
  decision 11) are measured with these versions.
