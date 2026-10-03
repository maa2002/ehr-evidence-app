# The final evaluation

Phase 9 compares the application as built (the controlled condition) with
a baseline on the evaluation case set, request by request, and assesses
question answering end to end. What is measured is fixed in
[ADR 0016](decisions/0016-evaluation-metrics.md), the rating of the
prose in [ADR 0027](decisions/0027-automatic-prose-rating.md), and the
design in [ADR 0028](decisions/0028-final-evaluation-design.md). The
records of `eval-100-v1` are not read before the freeze of work item 64;
everything is built and checked on invented data and the development
case sets first. Command examples assume the session set-up in
[development.md](development.md#session-set-up).

## Work items

| Work item | Module | Role | Status |
| --- | --- | --- | --- |
| 53 | `assessment.partition` | The downstream partition of the case sets | done: `downstream-partition-v1` |
| 54 | | Independent QA references | planned |
| 55 | | Explanation expectations and challenges | planned |
| 56 | | The baseline condition | planned |
| 57 | | The paired runner | planned |
| 58, 59 | | QA, abstention, explanation and evidence metrics | planned |
| 60 | | End-to-end QA | planned |
| 61 | `assessment.statistics` | The paired comparison's statistics | done; the report is planned |

## The downstream partition

`assessment.partition` keeps the final evaluation's cases apart from
everything the application was built and tuned on:

- **Partitions by case set.** The development pilot and `dev-30-v1` are
  development; `eval-100-v1` is final.
- **One partition per person.** A person in both partitions stops the
  record.
- **Requests stay with their case.** Every request about a case,
  paraphrases included, goes with its case set's partition.
- **Upstream partitions kept apart.** Each case's upstream partition (the
  GNN's own split, from the case set's closure record) is recorded beside
  it. A case set drawn from one upstream partition may hold no case of
  another, and only a case of the upstream test partition may be called
  unseen by the GNN.

The record reads only the cases' identities and upstream partitions,
through the pinned closure and identity-mapping records. It is protected,
since it holds persons and subjects, and pinned by size and SHA-256 in
the module:
`protected-data\assessment\downstream-partition-v1\partition.json`,
21,322 bytes, SHA-256
`1718de367b822d2a03a82c8bfc82be28a032db14331a916e481dc756101f5b0c`.

| Case set | Partition | Drawn from | Cases | Admitted | Upstream partition of the cases | Unseen by the GNN (admitted) |
| --- | --- | --- | --- | --- | --- | --- |
| `pilot-10-v1` | development | no partition (the first ten qualifying persons) | 10 | 10 | train 8, test 2 | 2 (2) |
| `dev-30-v1` | development | validation | 30 | 26 | validation 30 | 0 |
| `eval-100-v1` | final | test | 100 | 92 | test 100 | 100 (92) |

The development partition holds 40 persons, the final one 100, and no
person is in both. Every case of the final evaluation is unseen by the
GNN. The development cases are not: `dev-30-v1` comes from the upstream
validation partition, on which the GNN's checkpoint was chosen, and 8 of
the pilot's 10 from the partition it was trained on. A claim about
patients the GNN never saw rests on `eval-100-v1` alone.

## The paired comparison's statistics

`assessment.statistics` holds the comparison of ADR 0016 (decision 10),
controlled minus baseline over the same requests:

- **Proportions.** Newcombe's method 10 (Newcombe 1998, *Statistics in
  Medicine* 17, 2635-2650): the score interval of each condition's
  proportion combined with the correlation of the pairs. The
  correlation's numerator is moved n/2 towards zero when it is positive.
  The exact McNemar test of the discordant pairs is reported beside it.
- **Ratios of sums** (F1, precision, recall and the like). The paired
  bootstrap over cases: a case's requests are drawn together, with the
  same draw for both conditions. It uses 10,000 replicates from the
  fixed seed 20260928, and the interval runs from the 2.5th to the 97.5th
  percentile of the replicates' differences, each the order statistic of
  nearest rank. A replicate in which either condition's ratio has a zero
  denominator is left out and counted.

The default mode checks the interval against StatsDirect's published
worked example of paired proportions: 20, 12, 2 and 16 pairs give a
difference of 0.2 with a 95% interval from 0.056156 to 0.329207, and an
exact P of 0.0129. It also checks values worked by hand.

The interval that `generation.measure` used for the precision comparison
lacked method 10's correction of the correlation, so it was Newcombe's
method 8. The benchmark now imports this module, and the development
intervals in [generation.md](generation.md#structured-evidence), ADR
0014 and ADR 0015 are recomputed from the recorded pairs. Each widened
slightly; for all 430 requests, NF4 minus BF16 is -33.0 points, from
-37.4 to -28.3 where it was -28.4. No conclusion changes.

## Commands

The default modes run invented data:

```powershell
& $AppPython -m ehr_evidence_app.assessment.statistics
& $AppPython -m ehr_evidence_app.assessment.partition
```

The partition's operator mode publishes a new record. It refuses an
existing one, and prints aggregate counts only:

```powershell
& $AppPython -m ehr_evidence_app.assessment.partition --operator-real
```
