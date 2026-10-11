# 0030. The Final Evaluation's Figures in the Tracker

Status: accepted (2026-10-05). Extends the allowlist of controlled
tracking ([ADR 0018](0018-tracker-as-experiment-record.md)).

## Context

The tracker is the project's experiment record (ADR 0018): metadata,
aggregates and the files of the one listed model, each through a fixed
allowlist. The final evaluation ([ADR 0028](0028-final-evaluation-design.md),
[ADR 0029](0029-the-freeze.md)) is done, and its figures are in protected
aggregate records ([final-figures.md](../final-figures.md)). The
allowlist holds the names of the development benchmark and of the
runtime aggregates; none of the final evaluation's figures, no value
that can be negative, and at most 400 scalars a task.

## Decision

1. **A task kind, "Final evaluation".** Of type testing, kept visible and
   marked completed as lineage, and named by its case set and commit. The
   final evaluation has one such task.
2. **Its figures**, aggregates only, each a scalar:
   - the metrics of [ADR 0016](0016-evaluation-metrics.md) by stratum:
     exact match, scoped accuracy, coverage, selective accuracy, correct
     answer yield, over-abstention, unsupported answer rate, unsupported
     claim rate, claim support, citation resolution, preservation,
     evidence-consistent yield, line completeness, and the precision,
     recall and F1 of conditions, records and drivers;
   - the prose rubric by kind of text (answers, explanations) and
     condition: its pass rate and its four items (identity, evidence,
     history, wording), its paired difference, and my agreement with
     the rater;
   - the questions typed end to end: questions, read right and delivered
     correctly, for condition questions by intent (lists, presence,
     refuse, clarify) and for questions about the record by intent
     (answer, refuse, clarify);
   - the held-out read: questions and read as expected, for each family
     (diagnosis lists, diagnosis rankings, demographics, admission
     information, transfers, procedures, lab results, prescriptions,
     microbiology) and in all.
3. **Series** name a stratum and the value: baseline, controlled,
   difference, lower, upper, patient-level lower or patient-level upper;
   a precision, recall or F1 also names its part, as in "lists:
   conditions: controlled". A difference and the limits of an interval
   may be negative, down to minus one; every other value keeps its
   bounds.
4. **Configuration fields**: those of a measurement, and the freeze, the
   question set, the rubric and the baseline, each in one form.
5. **Up to 1,000 scalars** for this kind; every other kind keeps 400.
6. **Where the figures come from.** `tracking.final_figures` reads the
   final evaluation's protected aggregate records: the report of the
   run, the intervals by patient, the ratings' aggregates and my
   agreement with the rater, the end-to-end figures and the held-out
   read. Each must name the same freeze and run. The ratings' marks and
   texts, and the rating model, which only the protected records name,
   are never read. The driver limit is taken from the configuration only
   when its digest is the one the freeze names. The figures are written
   once as a manifest under the workspace's `outputs\lineage`, which the
   task names with its checksum, and an index keeps the task, so it is
   sent once.
7. **Two records kept for it.** The end-to-end final mode and the check
   of the intervals by patient printed their figures. They are kept as
   structured records beside the report (2026-10-05): the end-to-end
   figures read from the final mode's printout, whose digest the record
   names, and the intervals recomputed by the same check, which gives the
   same figures.

## Consequences

- The frozen version is unchanged: this is code of the tracker only, and
  the figures sent are those of the protected records.
- Nothing of a request, a text, a mark or a patient is sent, and the
  rating model is not named.
- The task is created by the operator's remote mode, with the tracker's
  configuration in the process environment.
