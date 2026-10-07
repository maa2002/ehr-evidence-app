# 0023. Answers Checked Against Each Other, Compound Mistakes and Junk Input

Status: accepted (2026-09-30). Amends [ADR 0022](0022-record-edge-cases.md)
(decision 7, readings: `record-questions-v3`) and
[ADR 0021](0021-the-whole-record.md) (decision 7, checks: stays in time
order); the condition rules become `question-interpretation-v5`.

## Context

The project owner asked for three more kinds of test: tests that need no
answer key, answers wrong in more than one fact at once, and junk input.
They were run on invented data, then with aggregate output only on the
development case sets, and found:

- **Polite openings read as other questions.** "Please, which conditions
  are recorded?", "Can you tell me which conditions are recorded?", "I'd
  like to know ..." and "Do you know if ... is recorded?" were not read by
  the condition rules, so a condition question went to the reading of
  questions about the record: the same list or presence answer, written
  under another task and never by the model, or a clarification that the
  question was not understood. A trailing "please" and "can you tell me
  if" were read.
- **Statement forms read as condition names.** "Heparin was given?" was
  taken for the condition phrase "heparin was given"; for an item the
  record does not name, the clarification asked which condition was
  meant, where "was heparin given?" asks which medication.
- **Stays out of time order.** The check of a list of ICU stays or
  transfers checked every stay and that none was left out, but not their
  order. The templates list them in time order, and the order of
  transfers is the patient's path through the hospital; a writer that
  listed them otherwise would have been released.
- **An unpaired surrogate** (half of a character outside the basic
  plane, which a JSON escape can carry) in a question raised an error in
  the service, since no encoding holds it.
- **Refusals in the framework's words.** A body that is not UTF-8 or
  nests too deeply, an unknown route and a method a route does not take
  were answered by the API in the web framework's words (`{"detail":
  ...}`) rather than with a bare code, and by the interface's server,
  which the tunnel exposes, as plain text rather than a code and its
  meaning. Nothing was an internal error, and nothing leaked.

## Decision

1. **Polite openings** (`qa.interpret`, rules
   `question-interpretation-v5`; `qa.record_interpret`, rules
   `record-questions-v3`). A question's polite opening is removed before
   either reading: "please" or "kindly", then "can you tell me", "tell
   me", "let me know", "I'd like to know (about)", "I want to know", "I
   wonder", "do you know" and the like, with the "if" or "whether" after
   it; not before "about" or "more", so "tell me about the patient" stays
   as asked.
2. **Statement forms** (`question-interpretation-v5`). A short phrase
   that states that something was given, measured or done ("heparin was
   given") is not a condition phrase; it is read as its question form
   is.
3. **Stays in time order** (`validation.record_support`). A list of ICU
   stays or transfers is in time order, as the templates write it.
4. **Well-formed text** (`qa.answer`). An unpaired surrogate in a
   question is read, and recorded, as the replacement character.
5. **Codes for every refusal.** A request the web framework refuses is
   answered by the API (`api.app`) with a bare code, `request_invalid`
   for a body that cannot be read, `not_found` or `method_not_allowed`,
   and by the interface's server (`interface.app`) with a code and what
   it means, `not_found` for an unknown route or file and
   `method_not_allowed`, as every other refusal.
6. **Tests.**
   - `tests/qa/test_consistency.py`: the same question in 13 rewordings
     (small letters, capitals, no question mark, two, a full stop, more
     spaces, a tab, a non-breaking space, a typographic apostrophe,
     "please" first and last, "can you tell me", "I'd like to know") gets
     the same response; a larger scope never finds less (presence, the
     items of a list, counts, first and last times, highest and lowest
     values, flagged results, and a visit's or the person's details), on
     the invented case set through the service and on the invented edge
     record.
   - `tests/validation/test_consistent_mistakes.py`: two mistakes at
     once; answers the templates write from a copy of the record with one
     record misread (left out, moved after or before every record, given
     a higher or lower value, its flag turned, taken for each of up to
     three other items, moved to another visit), checked against the real
     record; and the right answer to another question, scope or condition
     given as this one's; for answers about the record and for condition
     lists, presence checks and explanations. Each is withheld unless it
     states what the correct answer states: a list or presence item may be
     named by any of its own records, and the groups of different items
     and the notices may come in any order.
   - `tests/qa/test_junk_input.py`: 1,500 junk questions drawn with a
     fixed seed in six families, through the service with protected
     records written and of the invented record; each ends in an outcome
     a question may end in, in text that can be sent and recorded,
     within two seconds, and the same when asked again.
   - `tests/api/test_junk_requests.py` and
     `tests/interface/test_junk_requests.py`: 300 and 150 junk questions,
     and 51 and 49 malformed requests (bodies, paths, queries and
     methods), over HTTP to the API and to the interface's server.

## Development Results

On invented data: 6,040 rewordings and 1,032 pairs of nested scopes
compared; of the answers about the invented record, 1,761 with two
mistakes, 688 written from a misread record and 320 others' answers, and
of the invented condition answers 651, 29 and 62, all withheld, every
answer itself released; 1,500 junk questions, and junk and malformed
requests over HTTP, none an error.

On the development case sets (`pilot-10-v1`, `dev-30-v1`), with
aggregate output only and the evaluation set untouched:

- **Without an answer key.** Every question of the consistency test in
  every scope of every case (25,669 requests): 20,475 pairs of nested
  scopes compared, none contradicting. The 133 questions in 13
  rewordings, in the history and all visits of 3 cases of each set
  (18,828 requests): none answered otherwise.
- **Compound mistakes.** 300 answers about the record (the history and
  all visits of 3 cases of each set, 25 questions): 13,965 with two
  mistakes, 4,012 written from a misread record and 2,230 others'
  answers, all withheld. 576 condition answers (every scope of every
  case: lists and presence checks both ways): 13,643, 7,191 and 6,846 of
  the same kinds, all withheld. Every answer itself was released. In
  the first run 6 of 43,017 were released: answers to "were any labs
  abnormal?" written from a record with a flagged result taken for
  another test, which state exactly the correct groups of flagged
  results in another order. They are not wrong, and are now compared as
  such; the order of different items is left to the writer, since no
  reader relies on it, while stays and transfers keep their time order.
- **Junk.** 3,000 junk questions in the history and all visits of 2
  cases of each set (24,000 requests): no error or exception, every
  response sendable; 160 over the length limit, each `limit_exceeded`;
  median 0.055 s, the slowest 1.14 s.
- **Nothing else changed.** The development suite gives 1,892 of 1,892
  and 784 of 784 and, run under version 4's name, the summary digests
  recorded before byte for byte. The validation mutation suite detects
  17,414 of 17,414 and 7,356 of 7,356 mutants in 65 classes, every
  original and control released. The edge questions' 31,652 requests
  give the outcomes they gave, and no condition question is answered
  otherwise with the record. The one-fact mutants of 300 real answers
  about the record, the swapped stays included, are all withheld (30,719
  of 30,719), every original and the 762 controls released.

## Consequences

- A question with a polite opening, and a statement for its question,
  get the plain question's response; condition questions so worded are
  condition questions again, which the model writes.
- The rule versions change. The development suite gives the counts it
  gave; the written wording sets and the held-out wording test are not
  scored again, since each is scored once; Phase 9 measures these
  versions.
- A writer that lists stays or transfers out of time order is withheld.
- The tests show that the gate withholds the kinds of mistakes they
  make; a mistake of another kind is outside what they can show, which is
  why the facts stay in slots that no writer types, and why the final
  evaluation scores every answer against independent references.
