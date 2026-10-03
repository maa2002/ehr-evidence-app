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
| 54 | `assessment.references` | Independent QA references | built; agree with every template answer of the development case sets |
| 55 | `assessment.references` | Explanation expectations and challenges | expectations built, and agree with every development template explanation; the challenges come with the runner |
| 56 | `assessment.baseline` | The baseline condition | built and checked on invented data |
| 57 | `assessment.runner` | The paired runner, with the invented challenges | built and checked on invented data; a development run with the model is next |
| 58, 59 | `assessment.metrics`, `assessment.stated` | QA, abstention, explanation and evidence metrics, and what each condition wrote read for them | built and checked on invented data; scored against references once those exist |
| 60 | `assessment.end_to_end` | End-to-end QA | built for condition questions, and in agreement with the written test on a development set; questions about the whole record need their references |
| 61 | `assessment.statistics`, `assessment.report` | The paired comparison's statistics, and the scoring of a run | built and checked on invented data |

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

## The references

`assessment.references` prepares each compared request's reference from
the source records. It reads no bundle and runs no part of the runtime's
pipeline; it shares only the pinned readers of the source records and
the vocabulary's tables.

- **Lists and presence checks.** The development suite's oracle
  (`qa.suite.Oracle`) reads the case set's source snapshot, its cases'
  visits and the condition vocabulary.
  - A list expects every record of the scope, by condition, each with its
    visit number.
  - A presence check expects the records whose standard concept is the
    condition asked about, or descends from it.
  - The scope's records without a standard concept are unjudged in a
    presence check.
- **Explanations.** The case set's published records give the expected
  explanation. The output record gives the selected prediction and the
  drivers' scores; the closure record gives each driver's tags and the
  case's restrictions; the linkage record gives each driver's linked
  records. Each linked record is checked against the snapshot for its
  visit and concept.
  - The expected drivers are the eligible ones with the highest scores, at
    most the frozen limit of 5.
  - Each driver comes with its records in pre-target visits (history) and
    its drug and procedure records of the final admission (context).
  - The reference also holds the drivers not described and the expected
    outcome: an explanation, an abstention without an eligible driver, or
    a case rejection for a case not admitted.
  - A record that does not bind (an output record the closure does not
    name by its digest, a linkage entry for another record, a linked
    record of another visit or concept) stops the reference.

Every reference is compared with the template's document for the same
request on the development case sets. The templates pass the release
gate, so each should score as a correct response. All of them do:

| Case set | Lists | Empty lists | Presence, recorded | Presence, recorded elsewhere | Explanations | Templates differing from their references |
| --- | --- | --- | --- | --- | --- | --- |
| `dev-30-v1` | 134 | 1 | 134 | 135 | 26 | 0 of 430 |
| `pilot-10-v1` | 56 | 2 | 56 | 58 | 10 | 0 of 182 |

The comparison is not empty. On invented data, a condition record removed
from the snapshot makes the templates that state it differ, and the
explanation's reference refuses a linked record that is missing.

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

## The baseline

`assessment.baseline` writes the compared requests as the controlled
condition does, from the same bundles, projection, schema, model,
precision, decoding, number of drivers, limits and single attempt. It
keeps the checks that bind the evidence to the request: case, scope,
prediction link, visit numbering and readable records. It lacks three
controls:

- **The grounding rules.** Rules 1, 4 and 5 of `prompt-v9` are left out:
  state only what the evidence records; say what is recorded, never what
  the patient has; do not hedge, rank, judge or say when or how often.
  The role and the other three rules are kept, renumbered 1 to 3, and the
  request's message, worked examples included, is the controlled
  condition's.
- **The sufficiency checks.** A scope whose coverage is incomplete, or an
  explanation without an eligible driver, is written from the evidence
  there is, where the controlled condition abstains.
- **The release gate.** The document becomes a candidate whose text is
  rendered without checks. A fact the bundle cannot render is shown as
  `[not in the record]`.

A document that does not parse, breaks its schema or cannot become a
candidate is invalid output in both conditions. A prompt or expected
answer over its limit, a timeout, exhausted memory or a backend failure
is the same failure in both.

## The paired runner

`assessment.runner` writes every request once in each condition, in the
same order, with one attempt each.

- **Controlled.** The checks before generation run first, then the
  model's one attempt through the adapter and the release gate. When the
  model's candidate is not released, the outcome is the failure the
  fallback names: a validation rejection when the gate withheld it,
  otherwise an operational error of its category. The template the
  application would release in its place is never credited.
- **Baseline.** As above.

Each request writes one protected record. It holds the request and its
stratum, the bundle's identity, and for each condition:

- the outcome and its category, the reason, the reason codes and the
  gate's reasons;
- the prompt's digest, the backend's output, the candidate and the text;
- the tokens, seconds and peak GPU memory.

A record is written once. A run is one pass, and a run that stops is
started again in a new directory. The run's summary holds aggregates
only: outcomes and failure categories by task and condition, and the
median, 90th percentile and largest of the seconds, tokens and peak GPU
memory.

The invented challenges (work item 55) check how each condition ends
each invalid case and each failure; all 20 end as they should:

| Challenge | Controlled | Baseline |
| --- | --- | --- |
| A bundle of another case or scope, a history with a gap, a record without a name; a prediction that is not the one selected | case rejection | case rejection |
| Incomplete coverage of a list's scope | abstention | writes an answer |
| An explanation with incomplete history, no positive attribution or no eligible driver | abstention | writes an explanation |
| A cut-off, malformed or schema-breaking output; a timeout; exhausted memory; a backend failure or crash; a prompt or expected answer over its limit | that failure | that failure |

## Scoring a run

`assessment.report` scores a finished run against the independent
references.

- **Reading.** Each condition's response is read as what it released, or
  as nothing when it was withheld or failed. The bundle that translates
  its references is the one the record names, and its digest must still
  be the record's.
- **Scoring.** The metrics score every response, summarise each metric by
  stratum and condition, and compare the conditions pair by pair.
- **The report.** It holds each metric's summed terms, its value in each
  condition (not applicable when its denominator is zero) and its paired
  difference with the 95% interval. It also holds the failures by
  category and the distributions of seconds, tokens and memory. Nothing
  of a request is printed.

On an invented run where both conditions write correct documents, every
metric is perfect in both, with no difference between them. Where each
list cites a record the bundle does not hold, the gate withholds the lists
and the baseline gives them. The controlled condition's coverage then
falls, and the baseline's answers hold unsupported statements and
references to nothing.

## End-to-end assessment

`assessment.end_to_end` starts from a question's text, in the application
as deployed: the QA pipeline with the whole record and, when it is
given, the model as writer. It scores each stage, each only when the one
before it succeeded:

1. **Interpretation.** Whether the application read what the writer
   meant. For a list or a presence check that is the task and the scope;
   a request to refuse should get a clarification or an abstention; a
   question to be answered with a clarifying question should get a
   clarification.
2. **Resolution.** For a presence check: whether the condition resolved
   is the one meant (exact), an ancestor or a descendant of it (related),
   or another (unrelated).
3. **Retrieval.** Whether the complete sources answer the question, and
   whether the runtime's bundle holds exactly the reference's records. A
   source-available answer the bundle lacks stays a failure, even when
   the runtime's abstention is right for its bundle.
4. **Delivery.** Whether the released response, scored against the
   independent reference, is correct, and who wrote it: the model, or
   the template after the model's failure. The model's own results come
   from the paired comparison only.

The questions use the written test's format (`qa.written`). As a check of
the code, not a measurement, the written set `written-v4` was assessed
with the templates as writers. On every one of its 226 questions the
stages agree with the written test's own verdicts:

| Intent | End-to-end stages | Written test |
| --- | --- | --- |
| List (24) | read right 10, each delivered correctly; declined 14 | correct 10, not understood 14 |
| Presence (117) | read right 60 (resolved exactly 57, related 3), each delivered correctly; declined 57 | exact 60, not understood 57 |
| Refuse (46) | refused 44; answered 2 | correct 44, answered 2 |
| Clarify (39) | clarified 33; abstained 6 | correct 33, abstained 6 |

The final evaluation's set is worded after the freeze. Questions about
the whole record will be assessed the same way once their independent
references exist (ADR 0016, decision 11).

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
& $AppPython -m ehr_evidence_app.assessment.references
& $AppPython -m ehr_evidence_app.assessment.baseline
& $AppPython -m ehr_evidence_app.assessment.runner
& $AppPython -m ehr_evidence_app.assessment.report
& $AppPython -m ehr_evidence_app.assessment.end_to_end
```

The partition's operator mode publishes a new record. It refuses an
existing one, and prints aggregate counts only:

```powershell
& $AppPython -m ehr_evidence_app.assessment.partition --operator-real
```

The references' operator mode compares the references with the template
answers of a development case set, and refuses the evaluation set. It
prints counts by stratum and kind only:

```powershell
& $AppPython -m ehr_evidence_app.assessment.references --operator-real --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.assessment.references --operator-real --case-set pilot-10-v1
```

The runner's operator mode writes a development case set's sampled
requests in both conditions with the pinned model, into a new
protected run directory, and needs the GPU. The report's operator
mode scores a finished run. Both print aggregates only and refuse the
evaluation set:

```powershell
& $AppPython -m ehr_evidence_app.assessment.runner --operator-real --case-set dev-30-v1 --per-group 2
& $AppPython -m ehr_evidence_app.assessment.report --operator-real --run <run identifier>
```

The end-to-end assessment's operator mode reads a development written set
with the templates as writers, and prints counts by intent and stage only:

```powershell
& $AppPython -m ehr_evidence_app.assessment.end_to_end --operator-real --set written-v4
```
