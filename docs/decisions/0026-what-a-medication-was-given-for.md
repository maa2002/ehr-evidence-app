# 0026. What a medication was given for

Status: accepted (2026-10-03), by the project owner. Adds to
[ADR 0021](0021-the-whole-record.md) (questions about the whole record).

## Context

A clinician with MIMIC-IV credentialed access who tried the shared
interface found it hard to find information beyond the suggested
questions, and asked which of a patient's medications were given for one
of the conditions recorded, and for how long. The record holds no answer
to the first: MIMIC-IV records what was prescribed and when, never what
for. Linking medications to conditions would take medical knowledge from
outside the record. The pinned vocabulary has the WHO's drug classes
(ATC) but no relationship between a drug and a condition (no NDF-RT or
MED-RT), and a table of such links made for this project would need a
clinician's review and would still say what a class is used for, not
why this patient was given a drug.

The questions also found faults of the record rules: "Is he taking
medications for his seizures?" was read as a question about a medication
called "seizures"; "What is he taking for his seizures?" was answered as
whether seizures are recorded, and "What was given for pain?" with the
pain scores; and a drug class named in the singular ("antiepileptic
medications") was not found.

## Decision

1. **No link between medications and conditions.** The application
   states what the record holds. It never says what a medication was
   given for and holds no table linking drug classes to conditions. A
   question that asks for it gets a clarification saying that the record
   does not say, which asks about a medication by name or a drug class
   instead (rules `record-questions-v4`, notice `indication_unrecorded`).
2. **Class names in the singular** resolve as their plurals do (rules
   `record-resolution-v3`).
3. **How long a medication was given** is not a new question: the first
   and the latest time a medication was given, and every prescription's
   start and end in the evidence, answer it within the hospital stays the
   record covers.
4. **Help.** Typing `/help` shows why questions follow set patterns and
   what can be asked, kind by kind, with example questions that the rules
   read as their kind's question; every response that asks back or
   declines points to it.

## Consequences

- A question about what a medication was for is answered honestly and
  points to a question the record can answer; no answer suggests why a
  medication was given.
- Lists of medications stay by name. Grouping them by drug class, and
  linking them to conditions with a reviewed source, are further work.
- The rule versions change before Phase 9 freezes them; the record
  questions of the evaluation are drafted after the freeze, so they meet
  these rules.
