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
| 58, 59 | `assessment.metrics`, `assessment.stated` | QA, abstention, explanation and evidence metrics, and what each condition wrote read for them | built and checked on invented data; scored against references once those exist |
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

## The metrics

`assessment.metrics` computes every metric of ADR 0016 from pairs of a
reference and a response, one pair per request and condition. Both are
plain data. The references come from the source records (work items 54
and 55), and the responses are read from what each condition wrote by
`assessment.stated`. Nothing in the metrics reads a bundle, a candidate
or a runtime check: support is judged against the reference alone.

- **Names.** A record is named by its domain and native identifier. A
  condition is named by its standard concept or, for a record without
  one, by its source code.
- **References read, not trusted.** `assessment.stated` reads each cited
  reference as what it names in the request's bundle. A reference the
  bundle does not hold names nothing: the baseline's documents are not
  checked, so they may cite records that do not exist.
- **Terms per request.** Each request contributes terms (true and false
  positives, numerators and denominators), and each metric sums its
  terms over a stratum. A metric with a zero denominator is not
  applicable, never 0 or 1. F1 is 0 when precision and recall are both
  0, and not applicable when either is.
- **Lists and presence checks.** A condition stated twice is one true
  positive and one false positive. A request that gets no list states no
  condition, so its reference's conditions and records are false
  negatives. A presence answer is correct when its outcome is the
  reference's and it cites exactly the reference's records.
- **Answerability.** An operational error or a validation rejection is
  neither an answer nor an abstention. It is never a successful
  abstention, and never over-abstention.
- **Explanations.** Preservation reads the prediction's label, score and
  logit, and each selected driver's concept, rank and score, as a
  response shows them: to three significant figures. A history or
  context line may be left out, which completeness counts apart; one
  stated twice counts as a false positive the second time.
- **Statements and disclosures.** Claim support reads every statement
  that states a fact of the record; a fixed notice states none. A
  disclosure the reference requires counts as a statement, and leaving
  it out is an unsupported one, since by its silence the response states
  that there is none. The disclosures are: records without a standard
  concept in an answer's scope (the reason code, and the line that
  counts them in a list or in a presence check's "not recorded"); each of
  an explanation's case restrictions (notice and reason code); and the
  drivers not described.
- **Unjudged records.** A record without a standard concept that a
  presence check may match by its ICD description alone has no
  independent reference, so it is left out of every count. An answer
  resting on such records alone, where nothing else matches, is counted
  apart.
- **Pairing.** A proportion whose denominator is the same requests in
  both conditions is compared by Newcombe's method 10 with the exact
  McNemar test, over the requests in its denominator in both. Every other
  ratio, F1 included, is compared by the paired bootstrap over cases.

Every metric is checked on examples worked by hand. The metrics are also
checked against the release gate's mutation suite on the invented
samples, using references built from the samples' bundles:

- every template answer and explanation, and every control the gate
  releases, scores as correct;
- every mutant the gate withholds for what a response states scores as
  faulty. These are an unsupported claim, an altered prediction or
  attribution, and an unresolved reference.

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
& $AppPython -m ehr_evidence_app.assessment.metrics
& $AppPython -m ehr_evidence_app.assessment.stated
```

The partition's operator mode publishes a new record. It refuses an
existing one, and prints aggregate counts only:

```powershell
& $AppPython -m ehr_evidence_app.assessment.partition --operator-real
```
