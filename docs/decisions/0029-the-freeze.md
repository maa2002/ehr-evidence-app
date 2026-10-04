# 0029. The freeze and the final evaluation's execution

Status: accepted (2026-10-04), within the design of
[ADR 0028](0028-final-evaluation-design.md); the project owner asked for
the recommended plan to be carried out. Work item 64.

## Context

ADR 0028 fixes what the final evaluation runs and in what order: the
freeze writes a protected record that every result is recorded under, a
run whose code differs from it is refused, the questions are worded
after it, and `eval-100-v1` is not read before it. This record says how,
before the freeze is made.

## Decision

1. **The freeze record** (`assessment.freeze`, `freeze-v1`). Written
   once, to the protected workspace, before the final case set is read.
   It holds:
   - the commit of the code checked out, which must be unmodified;
   - the versions of everything that writes, checks and scores a
     response: the rules of reading, resolution and answering, of
     questions about the whole record and of wording, the prompt, the
     projection, the candidate's shape, the explanation template, the
     baseline, the prose rubric and its instructions' digest;
   - the digests of the configuration the runtime reads, of the
     vocabularies' and the partition's pins, and of the final case set's
     pins (case-set records, record bundles, raw extract);
   - the rating model's name, settings and instructions' digest, as the
     operator names them. The rating model is named in the protected
     record only.
2. **The gate.** Every operator mode that reads the final case set (the
   runner, the scoring of a run, the rating of a run, the end-to-end
   assessment's final mode, the held-out read of MIMIC-IV-Ext-Instr)
   requires the record. It refuses unless the code and configuration
   now running (`src/` and `config/`) are the frozen commit's, with
   nothing changed or added, and every version and digest is the
   record's. A commit that changes neither, such as documentation or
   results, keeps the frozen version. A run names the freeze record it
   was written under; it is scored and rated only under that record, and
   rated only by the rater the record names. A development case set
   needs no freeze; any other set is refused.
3. **Release 0.11.0 is the frozen version.** The freeze record names its
   commit, and is written after the release, before anything else.
4. **The final question sets.** Worded after the freeze, as the written
   sets were, by a writer who has not read the rules, into the set
   `final-v1`: `questions.csv` (condition questions, in the written
   test's format) and `record-questions.csv` (questions about the whole
   record, in the end-to-end assessment's format). Both files are
   registered by size and digest under the freeze, once, before they are
   read, and the final mode reads only registered files.
5. **The held-out questions of MIMIC-IV-Ext-Instr** (ADR 0016,
   decision 11; `assessment.instr`, `instr-held-out-reading-v2`). Every
   question of the held-out partition is read once, without a bundle, as
   the application routes it: the condition rules first and, where they
   refuse a question about other data or name no condition, the rules
   for questions about the whole record, with the final case set's
   dictionaries. Each family's expected reading is fixed here: the
   all-diagnoses list a condition list; a ranking of diagnoses not
   answered; demographics the person's details; admission information a
   visit's or the person's details; transfers transfers or ICU stays;
   procedures, laboratory results, prescriptions and microbiology a
   question about that kind of record. The result is written once to a
   protected record named by the freeze.
6. **Order.** Release 0.11.0; the freeze record; the question sets
   worded and registered; the final run of every request of the final
   case set in both conditions; then its scoring, its rating and the
   owner's sheet, the end-to-end assessment and the held-out read; and
   the report.

## Consequences

- A change after the freeze to code or configuration is refused by the
  gate, and makes a new evaluated version, measured again on a newly
  worded question set (ADR 0028).
- The final run writes about 1,650 requests (519 QA scopes with a list
  and two presence checks each, and 91 explanations) in both
  conditions. At the speed measured in Phase 7, that is about 40 hours
  of the workstation's GPU, during which the shared application cannot
  run.
- The held-out read's reading is held, in the tests, to what the
  application does with the same questions: the edge and help questions
  and the junk test's questions, without a difference.
