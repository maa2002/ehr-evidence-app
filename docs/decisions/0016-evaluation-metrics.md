# 0016. Evaluation Metrics, Fixed Before the Final Evaluation (Phase 9)

Status: accepted (2026-09-28), agreed by the project owner before any
request of `eval-100-v1` is written. These definitions are frozen with
the settings, prompts, evidence selection and validators; changing any of
them afterwards makes a new evaluated version. Decision 11 was added by
[ADR 0021](0021-the-whole-record.md) on 2026-09-29, and decision 9
amended by [ADR 0027](0027-automatic-prose-rating.md) on 2026-10-03, each
with the owner's agreement, before any request of `eval-100-v1` was
written.

## Context

The project brief names the metrics of the final evaluation by area: for
lists, exact match and canonical set precision, recall and F1; for
presence checks, scoped accuracy; for answerability, coverage, selective
accuracy, correct-answer yield, appropriate abstention, over-abstention
and the unsupported-answer rate; for explanations, prediction and
attribution preservation, evidence-consistent yield and the
attribution-language violation rate; for evidence, citation resolution,
claim support and the unsupported-claim rate; for operations, failure
rate, latency, tokens and peak GPU memory. It asks for a structured prose
rubric, for denominators and empty-set conventions agreed in advance, and
for scoring frozen before final testing. It supplies no numerical
threshold and no required improvement over the baseline, and the
inherited GNN's AUROC is not a measure of this application.

Development measurement has counted a response as released only when
every fact is right, which says nothing about how close a withheld one
came. The development benchmark now also scores conditions, records,
drivers and lines with precision, recall and F1 against the correct
document (`generation.measure`); the final evaluation scores against
independent references instead.

## Decision

1. **Requests and references.** The unit is a request: a QA question, or
   the explanation of a case's selected prediction. Every request of the
   final evaluation has a reference prepared from the complete permitted
   source records, independently of the runtime's evidence and validators
   (work items 54 and 55): its expected outcome and, for an answer, the
   expected conditions and each one's records; for an explanation, the
   prediction, the selected drivers and each driver's history and
   context. Both conditions are scored on the same requests against the
   same references.
2. **What is scored.** For each request and condition, the final outcome
   (answer, "not recorded", explanation, clarification, abstention, case
   rejection, validation rejection or operational error) and the
   response's statements. The controlled condition's response is what it
   releases; the baseline's is its model document rendered without the
   gate. A request with no response of a kind states nothing of that
   kind. An operational error, a timeout or a validator failure is never
   a successful abstention.
3. **Lists.** A condition is a standard concept, or the ICD source code of
   records without one. A list is an exact match when its conditions are
   the reference's and each cites exactly the reference's records for it.
   Set precision, recall and F1 of the conditions are micro-averaged:
   true positives, false positives and false negatives are summed over
   requests before the ratios are taken. A condition stated twice is one
   true positive and one false positive; a request that gets no list
   states no condition, so its reference conditions are false negatives.
4. **Presence checks.** Scoped accuracy is the share of presence requests
   answered with the reference's outcome ("recorded" or "not recorded")
   for the scope asked about.
5. **Answerability.** With A the requests whose reference is an answer
   (a list, a presence answer or "not recorded") and N those whose
   reference is a clarification or an abstention: coverage is the answers
   given to A over A; selective accuracy the correct answers over the
   answers given to A; correct-answer yield the correct answers over A;
   appropriate abstention the requests of N given the reference's kind of
   response over N; over-abstention the requests of A given a
   clarification or abstention over A; and the unsupported-answer rate
   the answers holding a statement the reference does not support over
   all answers given. A list answer is correct when it is an exact match;
   a presence answer when its outcome is the reference's and it cites
   exactly the reference's records.
6. **Explanations.** Preservation is the share of explanations that state
   the prediction's label, score and logit as the evidence has them (to
   the figures a response shows) and every selected driver with its
   concept, rank and score; drivers are also scored with precision,
   recall and F1 against the reference's selection. Evidence-consistent
   yield is the explanation requests whose explanation holds only
   supported statements, over the explanation requests with an
   explanation reference. The attribution-language violation rate is the
   explanations holding wording that states or implies causation,
   diagnosis, risk or treatment from an attribution, over the
   explanations with text. Completeness, the history and context lines
   stated right over those the evidence has, is reported beside them
   (ADR 0014, decision 5).
7. **Evidence.** Citation resolution is the cited references that resolve
   to a record of the request's case and scope, over all cited
   references. Claim support is the factual statements whose cited
   records support them, over all factual statements; the
   unsupported-claim rate is the responses holding at least one
   unsupported statement, over the responses with text. The records
   cited for each condition, driver history and context are also scored
   with micro-averaged precision, recall and F1. Support is judged
   against the reference, never by the runtime's own validators.
8. **Operations.** Failures by category, per request (limit exceeded,
   timeout, resources exhausted, backend failure, invalid output,
   validation rejection); latency, prompt and output tokens and peak GPU
   memory as median, 90th percentile and largest, by task and condition.
9. **Prose rubric** (as amended by
   [ADR 0027](0027-automatic-prose-rating.md)). Every explanation, and
   every answer to a condition question, of both conditions is rated
   pass or fail on four items with written criteria: prediction and
   concept identity, evidence and history by code against the reference,
   and attribution wording by an automatic rater that is not the
   evaluated model, checked on planted faults in development texts and
   by the owner on 30 texts. No rater sees which condition wrote a
   response.
10. **Comparison.** For each metric the paired difference, controlled
    minus baseline, is reported with a 95% interval: Newcombe's method 10
    for proportions over the same requests, with the exact McNemar test;
    a paired bootstrap over cases (a case's requests resampled together;
    10,000 replicates, fixed seed; percentile interval) for F1 and other
    ratios of sums. No threshold, required improvement or significance
    level decides success; every metric defined here is reported, and
    none is chosen after the results. Development results are reported
    apart from final ones.
11. **Questions about the whole record.** They form strata of their own,
    reported apart from condition lists, presence checks and
    explanations: lists, presence checks, times, counts, values, flagged
    results, visits' details and the person's details, each also by kind
    of record. Their references are prepared from the evaluation set's
    raw extract and OMOP snapshot, independently of the runtime's record
    bundles and checks. A list is scored as in decision 3, an item being
    a condition (a standard concept, or the source code of records
    without one), a laboratory test or ICU item, a medication's concept
    (or its written name without one), a microbiology test and specimen,
    a care unit or an ICU stay; a presence check by scoped accuracy
    (decision 4); a time, a count or a value is correct when it names
    exactly the reference's records (the first and the last, every
    counted one, the one each item's selector picks) with their values;
    visits' and the person's details when every visit of the scope is
    given with the reference's value. Answerability, evidence and
    operations are measured as in decisions 5, 7 and 8 over these
    strata. The templates write these answers in both conditions, so no
    difference between them is reported for these strata until the model
    writes them. The questions: a generated suite over the development
    set (development only); a set worded, after the record rules are
    fixed, by a writer who has not read them; and the structured
    questions of MIMIC-IV-Ext-Instr's held-out partition about its seven
    event types, read once with the fixed rules, each event type with
    its expected reading. Questions of the condition question sets that
    asked about other data, and were to be refused, are scored in these
    strata instead; the condition strata keep the condition questions.

| Metric | Numerator | Denominator | When the Denominator Is Zero |
| :--- | :---: | :---: | ---: |
| Exact Match (Lists) | Exact Lists | List Requests With an Answer Reference | Not Applicable |
| Condition, Record, Driver and Line Precision | True Positives | True and False Positives | Not Applicable |
| Condition, Record, Driver and Line Recall | True Positives | True Positives and False Negatives | Not Applicable |
| F1 | Twice Precision Times Recall | Precision Plus Recall | 0 When Both Are 0; Not Applicable When Either Is |
| Scoped Accuracy | Presence Requests With the Reference's Outcome | Presence Requests | Not Applicable |
| Coverage, Over-Abstention, Correct-Answer Yield | As in Decision 5 | A | Not Applicable |
| Selective Accuracy | Correct Answers | Answers Given to A | Not Applicable |
| Appropriate Abstention | Requests of N Given the Reference's Kind | N | Not Applicable |
| Unsupported-Answer Rate | Answers With an Unsupported Statement | Answers Given | 0 Answers: Not Applicable |
| Citation Resolution, Claim Support, Unsupported-Claim Rate | As in Decision 7 | As in Decision 7 | Not Applicable |

"Not applicable" is reported as such, never as 0 or 1.

## Consequences

- Work items 58 and 59 implement these definitions, each with
  hand-worked examples and its empty-set convention, before the final
  evaluation runs; work items 54 and 55 prepare the references they need.
- On development data the benchmark scores the same quantities against
  the correct documents, so development and final results read on one
  scale, reported apart.
- Nothing here sets a pass mark.
