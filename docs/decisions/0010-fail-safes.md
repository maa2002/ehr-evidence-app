# 0010. Fail-Safes (Rules Versions 3 and 4)

Status: accepted (2026-09-26).

## Context

Rules version 2 was measured on new written wording: 3 of 198 questions
got a wrong answer, all naming a part of the record in words the scope
rules do not recognise, so the selected scope answered them. On the
held-out MIMIC-IV-Ext-Instr questions, 236 lists limited to a number of
conditions ("list the three diagnoses") were answered with the whole
list. Each version so far closed gaps one wording at a time, and wording
that no rule anticipated could still be answered as if it were plain.

## Decision

1. Rules version 3 (`question-interpretation-v3`, `qa-answer-v3`) adds
   layered fail-safes. A list or a presence check is released only when
   every word of the question is accounted for by a question frame, the
   condition phrase, a scope phrase or a fixed list of harmless words.
   Otherwise:
   - a visit, stay or time named in words that are not a scope phrase
     gets a clarification that names the selected scope
     (`scope_ambiguous`);
   - an exclusion or filter, and in a list request a negation, gets
     `task_ambiguous`;
   - any other word gets `task_ambiguous`;
   - two presence checks in one message get `task_ambiguous`;
   - a list limited to a number of conditions is refused.
   The condition phrase is not checked word by word: it must resolve to
   a condition, or the question gets a clarification anyway. The
   question words removed from its ends are checked for visits, times
   and exclusions. Negative wordings of a presence check ("is there no
   record of X") check X.
2. Version 3 was measured on a new written set: 1 wrong answer in 228
   questions, where version 2 gives 9 on the same questions, but 105
   answered as expected against 151. Its fail-safes also stopped many
   harmless questions, most of them naming the selected scope in words
   of their own ("the selected part of the record", "the current, final
   admission"). It is recorded and not released.
3. Rules version 4 (`question-interpretation-v4`, `qa-answer-v4`) keeps
   every fail-safe and reads those words as scope phrases: "the selected
   part of the record" names the active scope; "this admission", and an
   admission named without a number or order ("her hospital stay"),
   name the selected visit when a visit is selected and the final
   admission otherwise; "the current, final admission", "visit 2 of 4"
   and "all of her admissions, including the current one" are read. It
   accepts more harmless words, and refuses a list of only some of the
   conditions ("some of her diagnoses") with its own message.
4. Resolution stays `condition-resolution-v2`. Each version was fixed
   before it was measured on a written set worded afterwards; the
   held-out results for both are follow-ups only.

## Consequences

- On a new written set worded after version 4 was fixed, version 4
  handles 147 of 226 questions as expected with 2 wrong answers, both
  requests to refuse read as condition questions. On the same questions
  version 2 gives 154 and 9 wrong answers, and version 3 gives 102 and
  2. On the held-out questions no ranking, no list of a number of
  conditions and no question about other data is answered.
- The list of harmless words is closed: a harmless word missing from it
  makes a question ask again. The written and held-out results count
  these clarifications as well as the wrong answers.
- A question that names a visit in words the rules read differently
  from the selected scope gets a clarification rather than an answer.
- Development suite versions 3 and 4 have a family for each fail-safe
  and the new scope phrasings. A further rule version again needs a new
  question set to be measured blind.
