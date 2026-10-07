# 0028. Design of the Final Evaluation (Phase 9)

Status: accepted (2026-10-03). Carries out
[ADR 0016](0016-evaluation-metrics.md) and
[ADR 0027](0027-automatic-prose-rating.md) in work items 53 to 61, with
the parity of [ADR 0014](0014-structured-evidence.md) (decision 7) and
the counting of fallbacks of [ADR 0012](0012-generation-design.md)
(decision 4).

## Context

ADR 0016 fixes what the final evaluation measures and ADR 0027 how its
prose is rated. ADR 0014 fixes what the baseline shares with the
controlled condition and what it lacks, and ADR 0012 counts every
fallback as a failure of the model. Still open are the requests the two
conditions are compared on, how the references are kept independent of
the runtime, which parts of the prompt are its grounding rules, what a
run records, and the order of the work. All of these must be fixed before
anything of `eval-100-v1` is read.

## Decision

1. **A package of its own.** `ehr_evidence_app.assessment` holds the work
   of Phase 9. The repository's publication rules refuse "evaluation" and
   "results" as path names. Each module checks itself on invented data by
   default and on a development case set in an operator mode. No module
   reads the records of `eval-100-v1` (its source snapshot, raw extract,
   model outputs or bundles) until the freeze of work item 64. The freeze
   writes a protected record naming the commit, versions and
   configuration digests that every result is recorded under, and a run
   whose code differs from that record is refused. The partition
   (decision 2) reads only the identities and upstream partitions of the
   case set's cases.
2. **The partition (work item 53).** A protected record lists the persons
   of the development case sets (`pilot-10-v1`, `dev-30-v1`) and of the
   final one (`eval-100-v1`), and refuses a person found in both. A
   case's requests, paraphrases included, go with its case set. Each
   case's upstream partition is recorded beside it, from the case-set
   records: only a case drawn from the upstream test partition may be
   called unseen by the GNN.
3. **The compared requests.** They are built for the final case set as
   the development benchmark builds them
   (`generation.adapter.development_requests`). In every scope of every
   admitted case there is a list, a presence check of a condition
   recorded in the scope, and one of a condition recorded elsewhere in
   the case set and not in the scope; every admitted case also gets the
   explanation of its prediction. A request names its condition by
   concept, so the comparison measures writing; reading is measured by
   the end-to-end assessment (decision 7). Invented challenges (work item
   55), reported apart, are run with the scripted backend:
   - an explanation request for each kind of invalid case (no positive
     attribution, unlinked drivers, a history from the wrong visits);
   - each operational failure (timeout, exhausted memory, backend
     failure, cut-off and malformed output);
   - each with its expected outcome.
4. **References (work items 54 and 55).** They are prepared from the
   source records by code that reads no bundle and runs no part of the
   runtime's pipeline (reading, resolution, retrieval, answering or
   checks). It shares only the pinned readers of the source records and
   the vocabulary's tables:
   - conditions by the development suite's oracle (`qa.suite.Oracle`),
     from the source snapshot, the case set's visits and the vocabulary;
   - the whole record by a new oracle over the raw extract and the
     snapshot (ADR 0016, decision 11);
   - explanations from the case set's published outputs, closure and
     linkage records: the prediction, the drivers to describe, and each
     driver's history and context.

   Each QA reference records whether the complete sources answer the
   request, apart from whether the runtime's bundle does. A request the
   sources answer is never a successful abstention because the bundle
   could not answer it. On the development case sets, every reference is
   compared with the template's document for the same request; each
   difference is explained or fixed before the freeze.
5. **The baseline (work item 56).** It shares the controlled condition's
   requests, bundles, projection, schema, model, precision, decoding,
   number of drivers, limits and single attempt, and the checks that
   bind the evidence to the request (case, scope, prediction link,
   visit numbering, readable records). It lacks three controls:
   - the prompt's grounding rules, rules 1, 4 and 5 of `prompt-v9` (state
     only what the evidence records; say what is recorded, never what the
     patient has; do not hedge, rank, judge or say when or how often).
     The other rules are renumbered and nothing else in the messages
     changes;
   - the sufficiency checks before generation: a scope with incomplete
     coverage, or an explanation without an eligible driver, is written
     from the evidence there is, where the controlled condition abstains;
   - the release gate: the document becomes a candidate whose text is
     rendered without checks, a reference the bundle lacks being shown as
     unresolved.

   A document that does not parse, breaks its schema or cannot become a
   candidate is invalid output in both conditions. A test holds the two
   conditions' messages to differ by exactly those rules.
6. **The paired runner (work item 57).** It makes one pass over the
   requests, writing each request once per condition, in the same order
   for both, with one attempt each. The controlled condition's outcome is
   its released response, or the failure its fallback names. Every
   request writes one protected record holding:
   - the request and both conditions' prompt digests;
   - the documents, candidates, gate reasons, outcomes and texts;
   - the tokens, seconds and peak GPU memory;
   - the frozen version's digests.

   Records are never logged or printed; the tracker receives aggregates
   (ADR 0018). A run that stops is started again as a new run.
7. **End-to-end assessment (work item 60).** It starts from question text,
   in the application as deployed. It uses the questions worded after the
   freeze by a writer who has not read the rules (condition and record
   questions) and the held-out questions of MIMIC-IV-Ext-Instr, read once
   without a bundle. Each question is scored by stage:
   - interpretation: the task and scope read;
   - resolution: the concept or item resolved;
   - retrieval: whether the bundle holds the reference's records;
   - delivery: the released response against the reference, naming
     whether the model or the template wrote it.

   The model's own results come from the paired comparison only.
8. **Scoring and comparison (work items 58, 59 and 61).**
   - `assessment.metrics` implements decisions 3 to 8 and 11 of ADR 0016
     over (request, reference, response) triples. Each metric has
     hand-worked examples and its empty-set convention in the tests. A
     disclosure the reference requires is a statement, and leaving it out
     is an unsupported one: records without a standard concept in an
     answer's scope, and an explanation's restrictions and drivers not
     described. Records a presence check may match only by ICD
     description have no independent reference and are left out of every
     count.
   - `assessment.statistics` holds the paired comparison of decision 10:
     Newcombe's method 10 and the exact McNemar test, and the paired
     bootstrap over cases. They are checked against a published worked
     example. The interval moved from `generation.measure` lacked
     method 10's correction of the pairs' correlation, so it was
     Newcombe's method 8; the development intervals computed with it
     are recomputed, and no conclusion changes.
   - `assessment.rubric` assembles ADR 0027's rubric: the items judged by
     code, the marks of the rater (reached at a local address the
     operator names, its identity, version and settings recorded with its
     marks), and the owner's sample.
   - The report gives every metric by stratum and condition with its
     paired difference; the failures by category; latency, tokens and
     peak GPU memory; and the frozen version. Its totals reconcile, so
     every request is counted once per condition, and it holds aggregate
     figures only, linked to the protected records by digest.
9. **Order of the work.**
   1. The package, the partition record and the statistics.
   2. The metrics with their hand-worked examples.
   3. The references and their comparison on the development case sets.
   4. The baseline and the runner, run on a development sample with the
      model.
   5. The end-to-end assessment, the rubric and the report.

   Then Phase 10's freeze (work item 64): release 0.11.0, the question
   set worded after it, and the one final run.

## Consequences

- `eval-100-v1` stays unread until the freeze; every check before it runs
  on invented data and the development case sets.
- The development benchmark (`generation.measure`) imports the paired
  statistics from `assessment.statistics`, so its intervals are now
  method 10's.
- A change after the freeze to anything a result depends on (code,
  rules, prompts, references, scoring) makes a new evaluated version,
  measured again on a newly worded question set.
