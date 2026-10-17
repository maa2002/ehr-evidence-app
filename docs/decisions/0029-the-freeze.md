# 0029. The Freeze and the Final Evaluation's Execution

Status: accepted (2026-10-04), within the design of
[ADR 0028](0028-final-evaluation-design.md). Amended twice the same day:
the second and the third freeze (below). Work item 64.

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
   commit, and is written after the release, before anything else. (The
   amendments below freeze 0.11.1 and then 0.11.2, the version evaluated.)
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
6. **Order.** Release 0.11.0 (0.11.2 after the amendments); the freeze
   record; the question sets worded and registered; the final run of
   every request of the final case set in both conditions; then its
   scoring, its rating and my sheet, the end-to-end assessment and the
   held-out read; and the report.

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

## Amendment (2026-10-04): The Second Freeze

The first freeze record (`freeze-v1`), of release 0.11.0 (commit
`f5820ea`), was superseded before any question set was worded or
registered and before anything of `eval-100-v1` was read. Its end-to-end
final mode answered the condition questions without the case set's whole
record, which the deployed application answers with (ADR 0028,
decision 7: in the application as deployed). On the development set
`written-v4`, 45 of 226 questions end at other stages with the whole
record: 17 of its 46 refusals and 4 of its clarifications are answered
as questions about the record. The tests of the final modes also read
the workspace's freeze record, so on a frozen checkout they could have
passed the gate.

- Release 0.11.1 answers the condition questions with the whole record
  in every mode of the end-to-end assessment, and no test reads the
  workspace's freeze record or final question sets.
- Release 0.11.1 is frozen as `freeze-v2`, under decisions 1 to 6. The
  first record is kept as it was written, and the gate reads only the
  second.
- Nothing was measured under the first record.

## Amendment (2026-10-04): The Third Freeze

The second freeze record (`freeze-v2`), of release 0.11.1 (commit
`cafe210`), was superseded before any result of `eval-100-v1` was
written. Under it the final question set `final-v1` was worded and
registered, and the final run was started. The run stopped while it
built its requests, before it wrote anything: the reader of a case
set's explanation bundles refused every case set but a development one,
so the runner, the scoring of a run and its rating could not read the
final case set's.

- Release 0.11.2 reads them when the caller has checked the freeze, as
  the other final paths read the final case set, and is frozen as
  `freeze-v3`, under decisions 1 to 6.
- The final question set is worded again after the third record, as
  `final-v2`, by the same procedure with seeds of its own. `final-v1` is
  kept as it was registered, under the second record, and is not read.
- Before the final run, a check under the freeze builds every request
  of the run and its references, and every input of the end-to-end
  assessment and of the held-out read, and passes every request through
  the run's code with a stand-in writer in place of the model. It
  writes nothing and prints counts only.
- Nothing was measured under the first two records.
