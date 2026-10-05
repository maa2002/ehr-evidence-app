# Figures of the final evaluation

The final evaluation ([assessment.md](assessment.md)) ran once on the
evaluation case set `eval-100-v1`, with the version frozen as release
0.11.2 ([ADR 0029](decisions/0029-the-freeze.md)). This page gives its
figures, aggregates only. The records they rest on are protected in the
workspace and are named here only by their place in it.

## The version and the run

| Item | Value |
| --- | --- |
| Frozen version | Release 0.11.2, commit `d1322c1`, freeze record `freeze-v3` (SHA-256 `ed84d362…d705`) |
| Model | MedGemma 1.5 4B IT in bfloat16 (`bf16-mixed` placement), the same in both conditions; one attempt a request |
| Limits | 16,384 prompt tokens, 768 output tokens and 180 seconds a request |
| Case set | `eval-100-v1`: 100 persons, none in a development case set, all of the GNN's upstream test partition and so unseen by it; 92 admitted, with 519 scopes |
| Run | `eval-100-v1-20261004T082135Z`, from 2026-10-04 19:21 to 2026-10-05 21:03 (92,552 seconds), every request in both conditions |
| Requests | 519 condition lists (4 of them with an empty reference), 1,034 presence checks (515 of a condition recorded in the scope, 519 of a condition recorded elsewhere in the case set only) and 91 explanations: 1,644 |
| Protected records | `protected-data\assessment\paired\<run>` (one record a request), the report `protected-data\assessment\reports\<run>.json`, the ratings and the owner's agreement in `protected-data\assessment\ratings\<run>` |

## The paired comparison

Each request was written once in each condition, by the same model from
the same evidence. The baseline lacks the prompt's grounding rules, the
sufficiency checks before generation and the release gate
([ADR 0028](decisions/0028-final-evaluation-design.md), decision 5). The
controlled condition's outcome is what it releases, or the failure its
fallback names, so a response it withholds counts against it. The
metrics are those of [ADR 0016](decisions/0016-evaluation-metrics.md);
the difference is controlled minus baseline, in percentage points, with
its 95% interval: Newcombe's method 10 for proportions over the same
requests, and a paired bootstrap over cases for ratios of sums
(decision 10). "n/a" is not applicable, as ADR 0016 defines it.

**All requests** (1,644)

| Metric | Baseline | Controlled | Difference | 95% interval |
| --- | --- | --- | --- | --- |
| Exact match | 64.5% | 71.5% | +6.9 | +4.4 to +9.5 |
| Condition precision | 96.8% | 100.0% | +3.2 | +2.1 to +4.6 |
| Condition recall | 57.2% | 53.4% | -3.8 | -7.8 to -0.1 |
| Condition F1 | 71.9% | 69.6% | -2.3 | -5.3 to +0.6 |
| Scoped accuracy | 100.0% | 98.7% | -1.3 | -2.1 to -0.6 |
| Coverage | 93.4% | 89.6% | -3.7 | -5.0 to -2.6 |
| Selective accuracy | 93.4% | 100.0% | +6.6 | +5.3 to +7.8 |
| Correct-answer yield | 87.3% | 89.6% | +2.4 | +1.5 to +3.3 |
| Over-abstention | 0.0% | 0.0% | +0.0 | -0.2 to +0.2 |
| Unsupported-answer rate | 6.3% | 0.0% | -6.3 | -7.5 to -5.2 |
| Citation resolution | 100.0% | 100.0% | +0.0 | +0.0 to +0.1 |
| Claim support | 98.0% | 100.0% | +2.0 | +1.5 to +2.6 |
| Unsupported-claim rate | 7.1% | 0.0% | -7.1 | -8.2 to -5.9 |
| Record precision | 90.1% | 100.0% | +9.9 | +7.8 to +12.2 |
| Record recall | 52.2% | 47.1% | -5.1 | -8.9 to -1.6 |
| Record F1 | 66.1% | 64.0% | -2.1 | -5.2 to +0.9 |

The rows on explanations alone are under Explanations. Claim support
counts statements; the unsupported-claim rate counts the responses with
text that hold at least one unsupported statement; the unsupported-answer
rate counts the answers that do.

**Condition lists** (515, the four with an empty reference apart)

| Metric | Baseline | Controlled | Difference | 95% interval |
| --- | --- | --- | --- | --- |
| Exact match | 64.5% | 71.3% | +6.8 | +4.2 to +9.4 |
| Condition precision | 96.9% | 100.0% | +3.1 | +1.8 to +4.6 |
| Condition recall | 54.3% | 50.4% | -3.9 | -8.1 to +0.1 |
| Condition F1 | 69.6% | 67.0% | -2.6 | -6.0 to +0.7 |
| Coverage | 80.0% | 71.3% | -8.7 | -12.0 to -5.5 |
| Selective accuracy | 80.6% | 100.0% | +19.4 | +15.3 to +23.7 |
| Correct-answer yield | 64.5% | 71.3% | +6.8 | +4.2 to +9.4 |
| Over-abstention | 0.0% | 0.0% | +0.0 | -0.7 to +0.7 |
| Unsupported-answer rate | 21.1% | 0.0% | -21.1 | -25.5 to -16.9 |
| Citation resolution | 100.0% | 100.0% | +0.0 | +0.0 to +0.0 |
| Claim support | 97.3% | 100.0% | +2.7 | +1.9 to +3.9 |
| Unsupported-claim rate | 21.1% | 0.0% | -21.1 | -25.5 to -16.9 |
| Record precision | 88.2% | 100.0% | +11.8 | +9.0 to +14.7 |
| Record recall | 46.1% | 42.0% | -4.1 | -8.3 to -0.2 |
| Record F1 | 60.6% | 59.2% | -1.4 | -5.1 to +2.3 |

The four lists with an empty reference are all of one case. The
controlled condition gave each as not recorded; the baseline gave three
so, and named a condition in the fourth, citing a reference that does
not resolve (exact match 3 of 4, citation resolution 50%). A bootstrap over
one case gives no interval; Newcombe's interval for exact match is -28.1
to +69.9.

**Presence checks of a condition recorded in the scope** (515)

| Metric | Baseline | Controlled | Difference | 95% interval |
| --- | --- | --- | --- | --- |
| Condition precision | 96.1% | 100.0% | +3.9 | +1.2 to +7.1 |
| Condition recall | 100.0% | 97.4% | -2.6 | -4.4 to -1.1 |
| Condition F1 | 98.0% | 98.7% | +0.7 | -0.1 to +1.9 |
| Scoped accuracy | 100.0% | 97.5% | -2.5 | -4.3 to -1.2 |
| Coverage | 100.0% | 97.5% | -2.5 | -4.3 to -1.2 |
| Selective accuracy | 97.3% | 100.0% | +2.7 | +1.1 to +4.7 |
| Correct-answer yield | 97.3% | 97.5% | +0.2 | -0.7 to +1.1 |
| Over-abstention | 0.0% | 0.0% | +0.0 | -0.7 to +0.7 |
| Unsupported-answer rate | 0.8% | 0.0% | -0.8 | -1.6 to -0.2 |
| Citation resolution | 100.0% | 100.0% | +0.0 | +0.0 to +0.0 |
| Claim support | 99.6% | 100.0% | +0.4 | +0.1 to +0.8 |
| Unsupported-claim rate | 0.8% | 0.0% | -0.8 | -1.6 to -0.2 |
| Record precision | 96.5% | 100.0% | +3.5 | +1.1 to +6.4 |
| Record recall | 99.0% | 94.7% | -4.3 | -7.7 to -1.6 |
| Record F1 | 97.7% | 97.3% | -0.4 | -1.5 to +0.8 |

**Presence checks of a condition recorded elsewhere only** (519): both
conditions answered every one as not recorded in the scope, with every
reference resolving and no unsupported statement (scoped accuracy,
coverage, selective accuracy and correct-answer yield 100.0% in both;
Newcombe's interval of the difference -0.7 to +0.7).

**Explanations** (91)

| Metric | Baseline | Controlled | Difference | 95% interval |
| --- | --- | --- | --- | --- |
| Preservation | 100.0% | 100.0% | +0.0 | +0.0 to +0.0 |
| Driver precision | 100.0% | 100.0% | +0.0 | +0.0 to +0.0 |
| Driver recall | 100.0% | 80.2% | -19.8 | -28.6 to -12.1 |
| Driver F1 | 100.0% | 89.0% | -11.0 | -16.7 to -6.4 |
| Evidence-consistent yield | 81.3% | 80.2% | -1.1 | -6.1 to +3.7 |
| Line completeness | 92.7% | 99.3% | +6.5 | +3.6 to +9.8 |
| Citation resolution | 100.0% | 100.0% | +0.0 | +0.0 to +0.0 |
| Claim support | 97.9% | 100.0% | +2.1 | +1.1 to +3.2 |
| Unsupported-claim rate | 18.7% | 0.0% | -18.7 | -27.5 to -11.0 |
| Record precision | 98.2% | 100.0% | +1.8 | +0.4 to +3.5 |
| Record recall | 93.5% | 74.7% | -18.8 | -29.6 to -9.0 |
| Record F1 | 95.8% | 85.5% | -10.3 | -17.9 to -4.3 |

The baseline gave all 91 explanations, 17 of them with an unsupported
statement; the controlled condition released 73, every one supported,
and withheld 18. Explanations consistent with the evidence: 74 against
73.

## Outcomes, failures and operations

| Task | Outcome | Baseline | Controlled |
| --- | --- | --- | --- |
| Condition list | answer | 413 | 367 |
| Condition list | not recorded | 3 | 4 |
| Condition list | operational error | 103 | 82 |
| Condition list | validation rejection | | 66 |
| Presence check | answer | 515 | 502 |
| Presence check | not recorded | 519 | 519 |
| Presence check | validation rejection | | 13 |
| Explanation | explanation | 91 | 73 |
| Explanation | validation rejection | | 18 |

The operational errors are lists whose document ran past a limit
(baseline 69, controlled 64) or could not be read as a document
(baseline 34, controlled 18). No request timed out, exhausted the GPU's
memory or met a backend failure.

| Task | Measure | Baseline: median, 90th percentile, largest | Controlled: median, 90th percentile, largest |
| --- | --- | --- | --- |
| Condition list | Seconds | 42.8, 88.3, 142.3 | 44.2, 93.8, 137.2 |
| Condition list | Prompt tokens | 1,957, 2,405, 2,872 | 2,027, 2,475, 2,942 |
| Condition list | Output tokens | 243, 497, 768 | 262, 534, 768 |
| Condition list | Peak GPU memory (MiB), largest | 6,739 | 6,752 |
| Presence check | Seconds | 15.8, 17.3, 37.5 | 15.8, 17.2, 29.5 |
| Presence check | Prompt tokens | 2,333, 2,361, 2,468 | 2,403, 2,431, 2,538 |
| Presence check | Output tokens | 87, 93, 192 | 87, 93, 163 |
| Presence check | Peak GPU memory (MiB), largest | 6,649 | 6,668 |
| Explanation | Seconds | 69.0, 79.4, 103.2 | 68.9, 78.7, 102.9 |
| Explanation | Prompt tokens | 2,162, 2,195, 2,337 | 2,232, 2,265, 2,407 |
| Explanation | Output tokens | 377, 423, 567 | 376, 412, 567 |
| Explanation | Peak GPU memory (MiB), largest | 6,621 | 6,640 |

The figures are of the model's attempts, on a laptop GPU of 8,188 MiB.

## The prose rubric

Every condition answer and explanation with text was rated on the four
items of [ADR 0027](decisions/0027-automatic-prose-rating.md): identity,
evidence and history by code against the reference, and attribution
wording by the frozen automatic rater (`prose-rubric-v4`).

| Texts | Condition | Rated | Identity | Evidence | History | Wording | All four |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Answers | Baseline | 1,450 | 1,356 | 1,358 | 1,395 | 1,450 | 1,329 (91.7%) |
| Answers | Controlled | 1,392 | 1,392 | 1,392 | 1,392 | 1,392 | 1,392 (100%) |
| Explanations | Baseline | 91 | 86 | 74 | 74 | 91 | 74 (81.3%) |
| Explanations | Controlled | 73 | 73 | 73 | 73 | 73 | 73 (100%) |

Over the requests rated in both conditions, the share passing all four
items differs by +3.6 points for answers (95% interval +2.7 to +4.8;
90 requests rated in one condition only) and by +1.4 points for
explanations (-3.8 to +7.4; 18 in one condition only). The rater failed
no text on wording in either condition. Before the run it caught 274 of
320 planted faults on wordings it was not revised on and failed none of
40 passing texts. The owner rated 30 texts drawn with a fixed seed,
blind: all 30 were answers the rater had passed, and the owner passed
all 30.

## Questions typed end to end

The final question set `final-v2` was worded after the freeze by a
writer who had not read the rules, and registered by digest before it
was read. Each question went through the application as deployed, with
the whole record, the model writing the condition answers.

| Condition questions | Questions | Read as meant | Then |
| --- | --- | --- | --- |
| Lists | 25 | 15 (declined 6, read as another task 4) | 15 delivered correctly |
| Presence checks | 119 | 52 (declined 56, another task 11) | resolved exactly 49, related 2, unrelated 1; 51 delivered correctly |
| To refuse | 44 | 44 refused | |
| To ask back | 39 | 32 asked back (answered 6, abstained 1) | |

Of the 66 condition answers delivered, all correct, the model wrote 60
and a template 6, after the model's candidate was withheld.

| Questions about the whole record | Questions | Read as meant | Delivered correctly |
| --- | --- | --- | --- |
| Laboratory results | 62 | 45 | 43 |
| Medications | 32 | 19 | 16 |
| Conditions (times and counts) | 11 | 8 | 7 |
| Procedures | 14 | 7 | 5 |
| Microbiology | 12 | 1 | 1 |
| Vital signs | 20 | 6 | 6 |
| Care units | 8 | 1 | 1 |
| ICU stays | 3 | 0 | 0 |
| Visits | 23 | 11 | 11 |
| The person | 23 | 12 | 12 |
| All to be answered | 208 | 110 | 102 |
| To refuse | 24 | 23 refused | |
| To ask back | 28 | 27 asked back | |

Every answer about the record that was delivered was correct; the
templates write them. The questions not read as meant were mostly
declined or asked back: vital-sign values (4 of 17 read as meant), care
units, microbiology and counts of ICU stays read worst.

## The held-out questions of MIMIC-IV-Ext-Instr

Read once, without a bundle, each family against the reading fixed
before the read ([ADR 0029](decisions/0029-the-freeze.md), decision 5;
record `instr-held-out-reading-v2`).

| Family | Questions | As expected | Most often instead |
| --- | --- | --- | --- |
| All diagnoses | 1,056 | 781 | asked back, the item named not found (179) |
| A ranking of diagnoses | 3,101 | 2,154 | read as a list of condition records (947) |
| Demographics | 4,199 | 1,595 | asked back, the task ambiguous (2,482) |
| Admission information | 4,215 | 1,350 | asked back, the task ambiguous (1,537), refused as a ranking (1,284) |
| Transfers | 4,220 | 0 | read as medication values (2,250), asked about the scope (1,325) |
| Procedures | 4,192 | 255 | asked about the scope (2,867) |
| Laboratory results | 4,118 | 23 | asked about the scope (3,327) |
| Prescriptions | 4,131 | 153 | asked about the scope (3,066) |
| Microbiology | 3,160 | 0 | asked about the scope (2,908) |
| All | 32,392 | 6,311 | |

Most questions about records name a time or a visit in words the rules
do not take as a scope, such as "in the first 24 hours", and are asked
about it. The questions
about transfers are mostly read as questions about medications.

## An added check: intervals by patient

Added after the figures, at the owner's request. The intervals of
proportions above treat the requests as independent pairs, but they come
from 92 cases whose scopes nest. Recomputed with the paired bootstrap
over cases that ADR 0016 fixes for ratios (10,000 replicates, the same
seed), beside, not in place of, the intervals above:

| Requests | Metric | Difference | Newcombe | By patient |
| --- | --- | --- | --- | --- |
| All | Exact match | +6.9 | +4.4 to +9.5 | +4.4 to +9.7 |
| All | Coverage | -3.7 | -5.0 to -2.6 | -5.2 to -2.4 |
| All | Correct-answer yield | +2.4 | +1.5 to +3.3 | +1.5 to +3.3 |
| All | Scoped accuracy | -1.3 | -2.1 to -0.6 | -2.2 to -0.5 |
| Lists | Exact match | +6.8 | +4.2 to +9.4 | +4.2 to +9.6 |
| Lists | Coverage | -8.7 | -12.0 to -5.5 | -12.5 to -5.1 |
| Presence, recorded | Scoped accuracy | -2.5 | -4.3 to -1.2 | -4.4 to -1.0 |
| Explanations | Evidence-consistent yield | -1.1 | -6.1 to +3.7 | -5.5 to +2.2 |

No conclusion changes. Where the two conditions never differ, or all
requests are of one case, the bootstrap gives no width, and the interval
above is the one to read.

## Reading the figures

- The controlled condition checks what it releases, so its unsupported
  rates of 0 and its selective accuracy of 100% follow from its gate.
  What the figures add is that the independent references found nothing
  unsupported in what it released, and what that costs: coverage, recall
  and the responses withheld, which correct-answer yield counts as
  failures. Its gain in exact match comes from what the model wrote
  under the grounding rules, since the gate only withholds.
- The three controls are measured together, not one at a time.
- Correct means as the structured record has it, not clinically true.
  The references are prepared by code of their own, from the same source
  records and vocabularies the application reads.
- The intervals of proportions treat the requests as independent pairs;
  the added check above finds no conclusion changed. Every metric is
  reported, none chosen after the figures, without a correction for the
  number of comparisons, and no threshold decides success (ADR 0016,
  decision 10).
- One run of one model with fixed decoding: the intervals describe the
  requests and cases, not variation between runs.
- The rubric compares the requests rated in both conditions. The owner's
  check covered 30 answers and no explanation; with none of the 30
  failed, the share of wording faults that the rater misses among the
  answers it passes may still be up to about 12% (95%).
- The typed questions stand in for the wording of the application's
  users, and their strata are small; they are reported as counts.
- The records are one hospital's, de-identified, with shifted dates;
  92 admitted cases, the requests built for each scope rather than asked
  by users. The figures support no clinical, prospective or calibrated
  risk claim; the prediction's score is the GNN's, not a probability of
  disease.

## The record of the protocol

- Three freeze records were written: `freeze-v1` (release 0.11.0) and
  `freeze-v2` (0.11.1) were superseded before any result of
  `eval-100-v1` was written, the first because its end-to-end final mode
  answered condition questions without the whole record, the second
  because the final run could not read the final case set's explanation
  bundles ([ADR 0029](decisions/0029-the-freeze.md), amendments). Both
  are kept as they were written.
- The question set worded under the second, `final-v1`, was superseded
  with it and is not read; `final-v2` was worded after the third record
  and registered under it.
- Before the run, a check under the freeze built every request, its
  reference and every input of the end-to-end assessment and the held-out
  read, and passed each request through the run's code with a stand-in
  writer in place of the model; it wrote nothing and printed counts only.
- The run, its scoring and rating, the end-to-end final mode and the
  held-out read were done under `freeze-v3`, each naming its digest.
