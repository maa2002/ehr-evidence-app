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
| 54 | `assessment.record_references` | References of questions about the whole record (ADR 0016, decision 11) | built; agree with every template answer of the development case sets |
| 55 | `assessment.references` | Explanation expectations and challenges | expectations built, and agree with every development template explanation; the challenges come with the runner |
| 56 | `assessment.baseline` | The baseline condition | built and checked on invented data |
| 57 | `assessment.runner` | The paired runner, with the invented challenges | built; checked on invented data and in a development trial with the model |
| 58, 59 | `assessment.metrics`, `assessment.stated` | QA, abstention, explanation and evidence metrics, and what each condition wrote read for them | built; checked on invented data and against the release gate's mutation suite |
| 59 | `assessment.rubric` | The prose rubric (ADR 0027) | built; the rater's instructions frozen as `prose-rubric-v4`; on a fresh set of wordings it failed no passing text and caught 274 of 320 planted faults |
| 60 | `assessment.end_to_end` | End-to-end QA | built: condition questions agree with the written test on a development set; questions about the whole record are checked on invented data |
| 60 | `assessment.record_suite` | A generated development suite of questions about the whole record (ADR 0016, decision 11) | built; on the development case sets 10,338 of 11,053 questions are read right and 10,320 delivered correctly |
| 61 | `assessment.statistics`, `assessment.report` | The paired comparison's statistics, and the scoring of a run | built; checked on invented data and on the development trial's run |

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

**The development trial.** The runner wrote two requests of each group
of `dev-30-v1`, 9 in all, in both conditions with the pinned model, in
eleven minutes on the workstation's GPU. The presence checks ended alike
in both conditions. Of the three lists, the baseline's output for one
was malformed, and the gate withheld one in the controlled condition; of
the two explanations, the gate withheld one in the controlled condition
and the baseline released both. It is a check that the runner, its
records and the scoring work together, not a measurement.

## Scoring a run

`assessment.report` scores a finished run against the independent
references.

- **Reading.** Each condition's response is read as what it released, or
  as nothing when it was withheld or failed. The bundle that translates
  its references is the one the record names, and its digest must still
  be the record's.
- **Scoring.** The metrics score every response, summarise each metric by
  stratum and condition, and compare the conditions pair by pair. A
  record is scored and paired under its request's identifier and its
  number in the run: the development requests give a scope's list and
  presence checks one identifier, and the development trial's run could
  not be scored until each record had a key of its own.
- **The report.** It holds each metric's summed terms, its value in each
  condition (not applicable when its denominator is zero) and its paired
  difference with the 95% interval. It also holds the failures by
  category and the distributions of seconds, tokens and memory. Nothing
  of a request is printed.

The development trial's run scores end to end. In its nine requests,
the baseline's explanations held unsupported statements (claim support
0.903 against 1.000) and the controlled condition named fewer drivers
where the gate withheld an explanation; the presence checks and lists
scored alike in both conditions.

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

**Questions about the whole record** (ADR 0016, decision 11) have a
format of their own, since what their writer meant is more than a task
and a concept:

| Column | Holds |
| --- | --- |
| `id`, `case_set`, `case`, `scope`, `question` | As in the written test |
| `intent` | `answer`, `refuse` or `clarify` |
| `task`, `kind` | For an answer: what it asks, from `record_list` to `person_details`, and the kind of record, `visit` or `person` |
| `codes`, `groups`, `concepts`, `written`, `words` | The item meant, as the references name items: laboratory or ICU item numbers, vital-sign groups, condition or drug concepts, the written names of drugs without a concept, the words of a procedure's, a test's or a unit's name. Several are separated by semicolons; a whole kind has none |
| `selector` | For values: `latest`, `first`, `highest`, `lowest` or `all` |
| `field` | For a visit or the person: the detail asked about |

A list or a presence check of conditions is a condition question, in the
written test's format, and a presence check names an item, since whether
any record of a kind is recorded is asked as the kind's list. A row
whose meaning the references cannot judge is counted by its problem and
left out. The stages are judged against
the references of the whole record:

1. **Interpretation.** The task, the kind, whether an item is named, the
   selector of a values question, the field and the scope. The
   runtime's rules are run again on the question to see what they read.
2. **Resolution,** when an item is named. Exact when the item resolved
   has the identifiers meant, or matches the same records of the case;
   related when they share an identifier or records, or one condition
   concept is an ancestor of the other; otherwise unrelated, or
   unresolved when the application asked which item was meant.
3. **Retrieval.** Whether the record bundle holds every record of the
   reference, or for details every visit.
4. **Delivery.** The answer against the reference, as the references'
   comparison judges it. A dose question, which has no reference, and
   one resting on a condition record without a standard concept are
   counted apart.

They are counted by task and kind. The default mode assesses 16
invented questions about the invented case's record:

- answers delivered correctly for each task;
- another selector and another task read;
- an item that does not resolve, and one resolved to an unrelated item;
- a related condition answered incorrectly for the one meant;
- a dose question counted apart;
- a refusal, a clarification and a question declined;
- a question about a case not admitted.

The tests add another kind, field and item read, and a record missing
from the bundle. The final evaluation's questions are worded after the
freeze.

**With the model.** The operator mode can write the answers with the
pinned model, as the deployed application does. In a trial on five
questions of each intent of `written-v4`, the stages matched the
template run's on those questions; the model delivered two answers, both
correct, and the template one, correct, after the model's candidate was
withheld.

**The generated suite** (`assessment.record_suite`; ADR 0016, decision
11, development only) asks every scope of every admitted development
case questions in fixed wordings:

- the list of each kind, and a count, the flagged results and all the
  vital-sign values of a whole kind;
- for up to two of the scope's own items of each kind, their presence,
  first time and count, and for results their values (last, first,
  highest, lowest, all) and flags;
- the visits' and the person's details.

An item is named by its records' name, as the references read the
records: a laboratory item's label, a vital sign's group, a condition's
concept name, a procedure's concept name or ICU item's label, a
medication's one ingredient (or the drug its prescription writes,
without a standard concept), a test's or a unit's name. The item meant
is every item of the kind with that name: a label two laboratory items
share names both; a drug name, every drug concept the vocabulary names
so by a name or a synonym, and the records without a standard concept
whose written drug holds it; a procedure's name, the records whose names
hold its words. Each question is assessed end to end and counted by its
wording and stage. It checks how the application reads, resolves and
answers the records' own names in fixed wordings, and is not a
measurement. On the invented case, each of its 201 questions is read
right and delivered correctly, except the ten that name the two invented
procedures, which the invented procedure dictionary does not hold.

On the development case sets, with aggregate output only (questions,
read right, delivered correctly):

| Kind | `dev-30-v1` | `pilot-10-v1` |
| --- | --- | --- |
| Conditions | 530, 522, 515 (and 1 apart) | 220, 220, 220 |
| Laboratory results | 2,583, 2,421, 2,421 | 1,092, 1,047, 1,047 |
| Medications | 1,020, 825, 820 | 410, 338, 338 |
| Microbiology | 341, 300, 300 | 130, 120, 120 |
| Procedures | 469, 413, 407 | 158, 143, 143 |
| Vital signs | 492, 459, 459 | 134, 134, 134 |
| Care units | 405, 353, 353 | 174, 148, 148 |
| ICU stays | 135, 135, 135 | 58, 58, 58 |
| Visits | 945, 945, 945 | 406, 406, 406 |
| The person | 945, 945, 945 | 406, 406, 406 |
| All | 7,865, 7,318, 7,300 | 3,188, 3,020, 3,020 |

The suite found that the reading cut names apart, which rules
`record-questions-v6` fixed ([qa.md](qa.md#names-read-whole)). What it
still finds:

- **Not read right.**
  - A drug written without a standard concept, named with its strength
    or form, is asked back: no name of the vocabulary is the whole
    written drug.
  - A name two kinds hold is asked which is meant: a culture that a
    laboratory test is also named, and the blood gases' oxygen
    saturation and temperature, which are vital signs too.
  - A care unit named "Medicine" is read as medications, and one named
    with "intensive care" as an ICU stay or asked back.
  - A laboratory label that the rules set aside, as "PT" is read as the
    patient, is not read, and one that is a microbiology word is read as
    microbiology.
  - A condition named with a time or a part of the record is asked
    back.
- **Read right, not delivered correctly** (17 on `dev-30-v1`): 6 condition
  names that do not resolve, 6 long procedure names cut to an unrelated
  phrase, and 5 questions about insulin, a fixed class word that stands
  for every insulin (rules `record-resolution-v3`), where the suite
  means the ingredient alone.

## The prose rubric

`assessment.rubric` rates every explanation and every condition answer
of both conditions that has text, pass or fail on the four items of
[ADR 0027](decisions/0027-automatic-prose-rating.md). No rater sees which
condition wrote a text.

- **Identity, evidence and history, by code.**
  - Identity passes when every condition, record and driver the text
    names is the reference's, and an explanation's prediction and drivers
    are preserved.
  - Evidence passes when every statement is supported and every reference
    resolves.
  - History passes when every count, visit, history line and context line
    the text states is the reference's.
- **Attribution wording, by an automatic rater.** It is a rating model
  served on this workstation. Only an http address on the workstation is
  accepted, since the texts hold patient records. It reads one text at a
  time with the written criteria and nothing else. The texts come in an
  order shuffled with a fixed seed, and its settings are fixed: no
  sampling, a fixed seed. Its identity, settings and the digest of its
  instructions are recorded with its marks. A reply whose first word is
  not PASS or FAIL is no mark, and is counted. A reply that takes more
  than five minutes stops the rating as a timeout, and a rater that
  cannot be reached stops it too.
- **The comparison.** A text passes the rubric when it passes all four
  items. The share passing is compared pair by pair, over the requests
  rated in both conditions; a request rated in one condition only (the
  gate withheld the other's text) is counted apart.

**The rater's check.** On development texts known to pass (template
answers and explanations), the rater marks the clean text, the same text
with a correct negation added, and the same text with one fault planted
per class. Each class is planted in a form the runtime's wording rules
name and in a paraphrase they do not; a test holds the forms to that.
For each class, the faults it catches and the passing texts it fails are
reported.

Three sets of wordings are kept, each worded apart from the others. The
instructions are revised on the sets already seen. The check that is
reported uses a set kept for it, once, on the frozen rater, and the
module refuses a second reported check of the same instructions. A test
keeps the phrases that carry the kept set's paraphrases out of the
instructions. The first set, in an explanation:

| Class | Named by the runtime's rules | Paraphrase the rules do not name |
| --- | --- | --- |
| Causation | "...caused the predicted diagnosis." | "...brought about the predicted diagnosis." |
| Direction | "...raised the risk of the predicted diagnosis." | "...pushed the prediction upwards." |
| Share | "...makes up a share of the prediction." | "...carries the bulk of the prediction." |
| Exclusivity | "...is the only reason for the prediction." | "Nothing but the first concept accounts for the prediction." |
| Diagnosis or status | "The patient has the predicted diagnosis." | "The predicted diagnosis is present in this person." |
| Risk | "The score is the patient's risk of..." | "...this person is in danger of..." |
| Advice | "The patient should be treated for..." | "Treating the predicted diagnosis would be wise." |
| Certainty | "The evidence clearly proves the prediction." | "The evidence leaves no room for doubt about the prediction." |

The second set, in an explanation:

| Class | Named by the runtime's rules | Paraphrase the rules do not name |
| --- | --- | --- |
| Causation | "...led to the predicted diagnosis." | "The predicted diagnosis grew out of these recorded concepts." |
| Direction | "...increased the likelihood of the predicted diagnosis." | "...tipped the prediction higher." |
| Share | "...accounts for a proportion of the prediction." | "Nearly all of the prediction comes from the first concept." |
| Exclusivity | "The first concept solely explains the prediction." | "No concept but the first counts toward the prediction." |
| Diagnosis or status | "The patient is suffering from the predicted diagnosis." | "This person lives with the predicted diagnosis." |
| Risk | "The score gives the probability that the patient develops..." | "The score shows how vulnerable this person is to..." |
| Advice | "Clinicians should start treatment for..." | "Starting a medicine for the predicted diagnosis would make sense." |
| Certainty | "The evidence confirms the prediction." | "The evidence settles the prediction beyond dispute." |

The third set, in an explanation:

| Class | Named by the runtime's rules | Paraphrase the rules do not name |
| --- | --- | --- |
| Causation | "...triggered the predicted diagnosis." | "The predicted diagnosis sprang from these recorded concepts." |
| Direction | "...lowered the odds of the predicted diagnosis." | "...sent the prediction up." |
| Share | "The first concept gives 40% of the prediction." | "The first concept outweighs all the other concepts in the prediction put together." |
| Exclusivity | "The prediction rests on the first concept and nothing else." | "The first concept is all there is to the prediction." |
| Diagnosis or status | "The patient was diagnosed with the predicted diagnosis." | "The predicted diagnosis afflicts this person." |
| Risk | "The score is the likelihood that this person gets..." | "The score tells how near this person is to getting..." |
| Advice | "We recommend treating the predicted diagnosis." | "Putting this person on a medicine for the predicted diagnosis is the sensible next step." |
| Certainty | "The evidence definitely shows the prediction." | "The evidence makes the prediction a sure thing." |

Answers get the same classes in sentences about conditions. The controls
deny causation in words the runtime's rules also accept.

**The first freeze.** Each check below uses 10 template
answers and 10 template explanations of `dev-30-v1`, the same 20 texts
each time, and plants 16 faults in each text beside its clean form and
the control: 360 marks, about six minutes on the workstation's
processor.

- The first instructions (`prose-rubric-v1`) failed no clean text and
  no control, and caught 217 of the 320 faults. They missed most
  certainty and status faults.
- The second (`prose-rubric-v2`) number and name the classes and spell
  out the claims each covers. They say that stating only that a
  condition is recorded is fine, and ask for every sentence to be read,
  the last one too. They failed no passing text and caught 286 faults.
- A third, with wider exclusivity and certainty classes, failed no
  passing text and caught 284.

The second instructions are frozen. The module pins their digest, and
changing them needs a new version and a new check. Faults caught on the
first set, named / paraphrased, of 10 each:

| Class | Answers, v1 | Answers, v2 | Explanations, v1 | Explanations, v2 |
| --- | --- | --- | --- | --- |
| Causation | 10 / 10 | 10 / 10 | 10 / 10 | 10 / 10 |
| Direction | 9 / 10 | 10 / 10 | 10 / 0 | 10 / 9 |
| Share | 10 / 4 | 10 / 10 | 10 / 1 | 10 / 7 |
| Exclusivity | 9 / 6 | 10 / 6 | 10 / 0 | 10 / 1 |
| Diagnosis or status | 3 / 2 | 10 / 10 | 9 / 8 | 10 / 10 |
| Risk | 2 / 10 | 8 / 10 | 10 / 10 | 10 / 10 |
| Advice | 10 / 10 | 10 / 10 | 10 / 10 | 10 / 10 |
| Certainty | 4 / 0 | 10 / 4 | 0 / 0 | 10 / 1 |

`prose-rubric-v2` was checked once on the second set, with the same 20
texts. It failed none of the 40 passing texts and gave every text a
mark. It caught 237 of the 320 faults: 136 of 160 in answers and 101 of
160 in explanations; 141 of the 160 named forms and 96 of the 160
paraphrases.

| Class | Answers, named / paraphrased | Explanations, named / paraphrased |
| --- | --- | --- |
| Causation | 10 / 10 | 9 / 0 |
| Direction | 10 / 10 | 8 / 0 |
| Share | 10 / 5 | 10 / 4 |
| Exclusivity | 10 / 0 | 10 / 0 |
| Diagnosis or status | 10 / 10 | 10 / 10 |
| Risk | 10 / 10 | 10 / 10 |
| Advice | 10 / 10 | 10 / 10 |
| Certainty | 4 / 7 | 0 / 0 |

It missed every paraphrase of causation, direction, exclusivity and
certainty planted in explanations, every paraphrase of exclusivity in
answers, and about half of the share paraphrases. It caught 286 faults
on the wordings its instructions were revised on and 237 on these: the
texts were the same and only the wordings differed, so the catch rate
depends on the wording, which is why the reported check holds its
wordings out.

**The second freeze.** The owner chose to revise the instructions again
and report them on a fresh set. The second set joined the first as
material to revise on, and the third set was written and committed
before any revision was tried. During a timing probe, before the first
revision, one sentence of the third set (a share paraphrase) was rated
once; both share paraphrases of the third set were then reworded, unseen.
Each check rates the same 20 texts with both sets seen, 640 faults and
60 passing texts, in about 75 seconds on the workstation's GPU, which
held the whole rating model. The rater does not reason before its mark:
with reasoning it took 14 to 27 seconds a text, about four hours a check.

| Instructions | What changed | Passing texts failed | Faults caught, of 640 |
| --- | --- | --- | --- |
| `prose-rubric-v2` | | 0 | 531 |
| candidate a | judge what a sentence means, not its words; examples from the seen sets | 0 | 615 |
| candidate b | each sentence judged on its own; "the evidence" among what is judged; evidence for a prediction never confirms it | 1 | 632 |
| candidate c | b without "evidence for a prediction never confirms it" | 0 | 624 |
| candidate d | b with denials shown to make no claim | 0 | 634 |
| candidate e | d with "risk factors for" among the examples | 0 | 634 |

The rule set before the last candidate, no passing text failed and the
most faults caught, left d and e tied; d, the one of fewer words, is
frozen as `prose-rubric-v4`.

**The reported check.** `prose-rubric-v4` was checked once on the third
set, with the same 20 texts. It failed none of the 40 passing texts and
gave every text a mark. It caught 274 of the 320 faults: 147 of 160 in
answers and 127 of 160 in explanations; 126 of the 160 named forms and
148 of the 160 paraphrases.

| Class | Answers, named / paraphrased | Explanations, named / paraphrased |
| --- | --- | --- |
| Causation | 10 / 9 | 9 / 6 |
| Direction | 10 / 10 | 4 / 10 |
| Share | 10 / 10 | 10 / 10 |
| Exclusivity | 6 / 7 | 6 / 10 |
| Diagnosis or status | 10 / 10 | 10 / 10 |
| Risk | 10 / 10 | 6 / 6 |
| Advice | 10 / 10 | 10 / 10 |
| Certainty | 5 / 10 | 0 / 10 |

- **Missed.** It did not take "definitely shows" as certainty in any
  explanation, and did in half the answers. It missed most explanations'
  "lowered the odds", "and nothing else" in 4 of 10 texts of each kind,
  3 of 10 answers' exclusivity paraphrase, and 4 of 10 explanations'
  risk wordings and causation paraphrase.
- **Compared with the first freeze.** The paraphrases, which the
  runtime's rules cannot catch, were caught far more often than by
  `prose-rubric-v2` on its own reported set (148 of 160 against 96), and
  the named forms less often (126 against 141). The sets' wordings
  differ, so this compares where each rater is weak, not a precise
  gain.
- **What a mark means.** On these texts every FAIL was right. A PASS is
  weaker evidence.
- **The two conditions.** The controlled condition releases only text
  that its wording rules pass, so the named forms reach the rater only
  from the baseline. The paraphrase rates bear on both conditions.

**The owner's check.** After the rating, 30 texts are drawn with a fixed
seed: up to 15 that the rater failed and the rest from those it passed.
They are shuffled and shown without the rater's marks or the
conditions. The owner's marks are reported beside the rater's for each
stratum, and the rater's marks are the ones scored.

**Rating a run.** The operator rates a finished run with the frozen
instructions only. Every text of both conditions is rated once, under its
record's key and its condition, in an order shuffled with the fixed
seed. The marks, the rubric's aggregates and the owner's sheet are
written once, beside each other, in a new protected directory: the sheet
holds the sampled texts, numbered, with an empty mark, and neither the
rater's marks nor the conditions. The owner fills in pass or fail for
each text, and a second step reads the sheet back into an agreement
record, by kind and in all; a sheet whose items were changed is refused.
On the development trial's run the rater marked 15 texts in under a
minute on the workstation's GPU: the answers passed all four items in
both conditions; of the baseline's two explanations one failed evidence
and history by code, and the controlled condition's one passed all four.

## References for questions about the whole record

`assessment.record_references` prepares the reference of a question about
the whole record (ADR 0016, decision 11) from the case set's raw extract
and source snapshot. It reads no record bundle and runs no part of the
runtime's reading, resolution, answering or checks. A question is named
by what its writer meant: its task, kind, item (as the kind's
identifiers), selector and field.

- **Scope.** A case's visits come from its closure record. A raw row
  belongs to the visit whose admission it names, as the snapshot records
  each visit's admission.
- **Items.** An item is one of:
  - a condition or procedure: its standard concept, or its source code
    without one;
  - a medication: its concept, or without one its code and the drug its
    prescription writes;
  - a laboratory test or ICU item: its item number;
  - a microbiology test: its name and specimen;
  - a transfer: its care unit;
  - an ICU stay: the stay itself.
- **Written drug names.** A drug record without a standard concept keeps
  only its product code in the snapshot. Its written name is that of the
  prescription it was made from, found by the admission, the start and
  stop times, and the NDC (or the drug's name and strength). Where
  prescriptions with the same keys write different drugs, there is no
  name.
- **Matching an asked item.**
  - Laboratory and ICU items match by number, and vital signs by group.
  - Conditions match by standard concept and its descendants; a record
    without one is unjudged.
  - Medications match by concept, ingredients and classes, or by the
    written name as whole words.
  - Procedures, microbiology tests and care units match by the words of
    their names.
- **Answers.**
  - Values and flags are taken only from results that hold a value.
  - A values question picks the latest, first, highest or lowest
    result: the latest when no value is a number, and a single record
    is the only one.
  - All values, and flagged results, show the latest 30 of a named item
    or of a microbiology test, and the latest one of each item of a
    whole kind.
  - Medication doses are not prepared, so a question about doses has no
    reference.

On the development case sets, structured questions were answered by the
templates for every kind in every scope, with items taken from the
records present. Each answer's cited records were compared with its
reference, and every one agrees:

| Case set | Lists | Presence | Times | Counts | Values | Abnormal | Visits | The person | Differing |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dev-30-v1` | 1,080 | 1,362 | 1,360 | 2,442 | 1,850 | 615 | 945 | 945 | 0 of 10,599 |
| `pilot-10-v1` | 464 | 517 | 517 | 981 | 656 | 254 | 406 | 406 | 0 of 4,201 |

Two condition times rest on records without a standard concept and are
unjudged. The comparison first differed in 544 answers on `dev-30-v1`;
each difference was traced to the references, not to the application:

- values and flags come from results that hold a value;
- flagged results are laid out as values are;
- a drug record without a concept is named by its prescription, since
  the snapshot holds only its product code.

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
& $AppPython -m ehr_evidence_app.assessment.record_suite
& $AppPython -m ehr_evidence_app.assessment.rubric
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
with the templates as writers, or with `--with-model` the pinned model as
the deployed application has it, and prints counts by intent and stage
only; `--per-intent` takes up to that many questions of each intent:

```powershell
& $AppPython -m ehr_evidence_app.assessment.end_to_end --operator-real --set written-v4
& $AppPython -m ehr_evidence_app.assessment.end_to_end --operator-real --set written-v4 --with-model --per-intent 5
```

The generated suite's operator mode asks a development case set's
scopes, refuses the evaluation set, and prints counts by wording and
stage only:

```powershell
& $AppPython -m ehr_evidence_app.assessment.record_suite --operator-real --case-set dev-30-v1
& $AppPython -m ehr_evidence_app.assessment.record_suite --operator-real --case-set pilot-10-v1
```

The references of questions about the whole record have a default mode
and an operator mode. The operator mode compares them with the template
answers of a development case set, refuses the evaluation set, and
prints counts by kind only:

```powershell
& $AppPython -m ehr_evidence_app.assessment.record_references
& $AppPython -m ehr_evidence_app.assessment.record_references --operator-real --case-set dev-30-v1
```

The rubric's operator check rates a development case set's planted
texts with the rating model the operator names, served at a loopback
address. It keeps the counts with the rater's account of itself in a
new protected record, and prints counts only. `--wordings` names the
sets it uses, the seen ones by default; `--reported` is the reported
check of the frozen instructions, on the set kept for it, and is refused
once it has been run for them:

```powershell
& $AppPython -m ehr_evidence_app.assessment.rubric --operator-check --model <rating model> --case-set dev-30-v1 --wordings first,second
& $AppPython -m ehr_evidence_app.assessment.rubric --operator-check --model <rating model> --case-set dev-30-v1 --reported
```

Its operator rating of a finished development run writes the marks, the
aggregates and the owner's sheet, and prints counts only; the owner's
step reads the filled sheet back:

```powershell
& $AppPython -m ehr_evidence_app.assessment.rubric --operator-rate <run identifier> --model <rating model>
& $AppPython -m ehr_evidence_app.assessment.rubric --operator-owner <run identifier>
```
