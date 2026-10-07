# 0009. Rules Version 2 and Spelling Suggestions

Status: accepted (2026-09-26).

## Context

The rules of release 0.6.0 were measured on wording written by others:
the held-out MIMIC-IV-Ext-Instr questions and the first written question
set. No answer was about the wrong condition, but four gaps gave wrong
answers: ranking questions ("list the top three diagnoses") got the whole
unranked list; some ways of naming another part of the record were not
recognised, so the selected scope answered them; two requests in one
message were answered for one of them; and a few requests that should be
refused read as condition questions. Condition wordings outside the
vocabulary (abbreviations, everyday terms, misspellings) got a
clarification without help.

## Decision

1. Rules version 2 (`question-interpretation-v2`,
   `condition-resolution-v2`, `qa-answer-v2`):
   - a ranking of conditions (first, top three, main) is refused, as
     dates, times and counts are;
   - more scope wordings are read: hospital, inpatient and ICU stays,
     "this" and "present" admission, "before admission", a prior
     hospitalisation, plural spans ("across her admissions"), visit
     numbers in words;
   - two requests are found in two sentences as well as in two clauses,
     before any out-of-scope rule;
   - the other-patient, prognosis and procedure rules are wider, and
     "hx of", problem lists and inclusion ("among them") are read.
2. Spelling suggestions: an unknown condition wording is compared, word
   by word, with the vocabulary's spellings, and the closest are offered
   in the clarification. A suggestion never resolves a phrase, and there
   is still no project synonym or abbreviation list
   ([ADR 0006](0006-condition-vocabulary.md)).
3. Results stay tied to their rule version. The held-out and first
   written results remain the figures for 0.6.0. Version 2 was fixed
   before it was measured on a new written set, worded afterwards; the
   held-out set informed version 2, so its result for version 2 is
   reported as a follow-up only.

## Consequences

- On new written wording, wrong answers fell from 9 to 3 of 198, all
  from scope wordings still not recognised.
- "This admission" is read as the current, final admission; a question
  meaning an earlier selected visit gets a clarification.
- A list limited to a number of conditions without a ranking word ("list
  the three diagnoses") is still answered with the whole list.
- A further rule version needs another new question set to be measured
  blind.
