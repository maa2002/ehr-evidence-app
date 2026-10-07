<div align="center">

# EHR Evidence App

Ask questions about one patient's hospital record and get answers that cite
the records they rest on, with the diagnosis a graph neural network predicts
for the final admission and the recorded concepts behind it.

[![CI](https://github.com/maa2002/ehr-evidence-app/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/maa2002/ehr-evidence-app/actions/workflows/ci.yml)
[![Release workflow](https://github.com/maa2002/ehr-evidence-app/actions/workflows/release.yml/badge.svg)](https://github.com/maa2002/ehr-evidence-app/actions/workflows/release.yml)
[![Version](https://img.shields.io/badge/version-1.0.0-2e7d5b)](CHANGELOG.md)
[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-3776ab?logo=python&logoColor=white)](docs/environments.md)
[![Windows 11](https://img.shields.io/badge/platform-Windows%2011-0078d4)](docs/environments.md)

[![Data: MIMIC-IV 3.1](https://img.shields.io/badge/data-MIMIC--IV%203.1-6f42c1)](https://physionet.org/content/mimiciv/3.1/)
[![OMOP CDM](https://img.shields.io/badge/standard-OMOP%20CDM-e8710a)](https://github.com/OHDSI/MIMIC)
[![Model: MedGemma 1.5 4B](https://img.shields.io/badge/model-MedGemma%201.5%204B-4285f4)](https://huggingface.co/google/medgemma-1.5-4b-it)
[![GNN: PyTorch Geometric](https://img.shields.io/badge/GNN-PyTorch%20Geometric-ee4c2c)](docs/inherited-gnn.md)
[![API: FastAPI](https://img.shields.io/badge/API-FastAPI-009688)](docs/interface.md)

</div>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/conversation-dark.png">
  <img alt="A question about a patient's recorded conditions answered as a list with numbered references, the evidence panel open beside it with the cited record highlighted" src="docs/images/conversation-light.png">
</picture>

> [!NOTE]
> A research prototype for retrospective analysis, not for clinical use. The
> application runs on MIMIC-IV, whose records may not be shared, so every
> screenshot here shows an invented patient: the names, values and dates are
> made up, while the diagnoses, medicines and tests are named from the same
> vocabularies the real records use.

## What it does

For one patient of [MIMIC-IV 3.1](https://physionet.org/content/mimiciv/3.1/)
mapped to the OMOP Common Data Model, the application:

- answers questions about the whole record (conditions, medications,
  procedures, laboratory results, microbiology, vital signs, transfers, ICU
  stays, the visits and the person) within a scope the user picks: the visits
  before the final admission, all visits, or one visit;
- explains a predicted diagnosis: a patient-journey graph neural network
  predicts a diagnosis for the final admission, and the application shows the
  prediction, whether that admission records it, and the recorded concepts its
  score was most sensitive to;
- cites every fact, with references that open the evidence panel at the record
  each statement rests on;
- checks every answer before showing it: a release gate checks the answer's
  structure, its references, that the cited records support every statement,
  and its wording, and withholds an answer that fails;
- refuses rankings and advice, asks back when it cannot read a question, and
  reports "not recorded" as a fact about the scope searched, never as clinical
  absence.

Fixed rules read the questions. MedGemma 1.5 4B, running locally, writes the
answers about conditions and the explanations from a structured view of the
evidence; templates write the other answers, and stand in whenever the gate
withholds the model's text.

## See it work

<p align="center">
  <img alt="A question typed into the box, the model's answer listing two matching records with references, and the first reference opening the evidence panel at that record" src="docs/images/demo.gif" width="100%">
</p>

"Is heart failure recorded?": the answer the model wrote, with one record
matched exactly and one as a more specific concept, and the first reference
opening the evidence at the record it cites. The 12.6 seconds of writing are
cut short here.

## A tour

### The whole record

![Two answers: every creatinine value across three visits, flagged against its reference range, and a blood culture's organism with its antibiotic susceptibilities, each line citing its record](docs/images/whole-record.png)

Laboratory results keep their reference ranges and flags, and a culture lists
its organism with every susceptibility. Each line cites its record.

### The predicted diagnosis

![The predicted diagnosis card: Acute Kidney Injury, score 0.741 and logit 1.05, the earlier visits and the final admission where it is predicted, and a note that the final admission records it](docs/images/prediction.png)

The card shows the model's prediction for the final admission with its
uncalibrated score and the visits it read, and checks whether that admission
records the diagnosis.

![The explanation: five recorded concepts in order of attribution score, each with the visits that hold it, followed by notes on how to read attribution scores](docs/images/explanation.png)

Below it, the explanation lists the recorded concepts the score was most
sensitive to, each with the visits that hold it, and notes what attribution
scores do and do not show.

### Questions it will not answer

![Three replies: a word that names no condition answered with suggestions, a ranking of conditions declined, and a condition reported as not recorded within the scope](docs/images/safety.png)

A word that names no condition gets suggestions, a ranking of conditions is
declined because the record does not rank them, and a condition that is not
recorded is reported with the scope it was looked for in.

### Help and scopes

<table>
  <tr>
    <td width="50%"><img alt="The help: the kinds of question the rules read, each with example questions" src="docs/images/help.png"></td>
    <td width="50%"><img alt="The scope menu: prior history, all visits, and each visit with its date" src="docs/images/scopes.png"></td>
  </tr>
</table>

`/help` lists the kinds of question the rules read, with examples to try, and
every question is answered within the scope chosen in the composer.

### On a phone, in light and dark

<table>
  <tr>
    <td width="50%"><img alt="On a phone: every potassium value of the final admission with its references" src="docs/images/phone-answer.png"></td>
    <td width="50%"><img alt="On a phone: the evidence sheet open at the cited laboratory result" src="docs/images/phone-evidence.png"></td>
  </tr>
  <tr>
    <td width="50%"><img alt="The welcome screen in the light theme" src="docs/images/welcome-light.png"></td>
    <td width="50%"><img alt="The welcome screen in the dark theme" src="docs/images/welcome-dark.png"></td>
  </tr>
</table>

## How it works

```mermaid
flowchart LR
    mimic[(MIMIC-IV 3.1)] -->|OHDSI ETL| omop[(OMOP CDM)]
    omop --> gnn[Patient-journey GNN]
    gnn -->|prediction and attributions| evidence[Evidence bundles]
    omop --> evidence
    mimic -->|labs, microbiology, vitals, stays| records[Record bundles]
    question([Question]) --> rules[Reading rules]
    rules --> writer[MedGemma or template]
    evidence --> writer
    records --> writer
    writer --> gate{Release gate}
    gate -->|passes| answer([Answer with references])
    gate -->|fails| withheld([Withheld])
```

1. Offline builders turn each patient's OMOP records and raw MIMIC-IV tables
   into versioned evidence bundles, one for each scope, with stable references
   (`E1`, `R31`), coverage statements and provenance. The GNN's published
   predictions and attribution scores are linked to the recorded events behind
   them.
2. Fixed rules read a question's task, the item it names and its scope, and
   ask back when they cannot read it.
3. A template, or MedGemma under a schema limited to the request's own
   references, writes the answer as statements whose facts are slots filled
   from the evidence.
4. The release gate checks the structure, the references, the support of every
   statement by its cited records, the preservation of the prediction and the
   wording before anything is shown.
5. A FastAPI service and a single page of plain HTML, CSS and JavaScript run
   on 127.0.0.1, with an optional Cloudflare tunnel behind a secret link for
   sharing.

Patient records never leave the machine: logs, console output and the
experiment tracker carry counts, hashes and finding codes only
([data handling](docs/data-handling.md)).

## Results

The final evaluation ran once, with the version frozen, on 100 persons the GNN
never saw (92 admitted, 1,644 requests). The same model wrote every request
twice: once with no grounding rules, no checks before generation and no
release gate (the baseline), and once as the application does.

| Measure | Baseline | Application | Difference (95% interval) |
| --- | --- | --- | --- |
| Unsupported-claim rate | 7.1% | 0.0% | -7.1 (-8.2 to -5.9) |
| Claim support | 98.0% | 100.0% | +2.0 (+1.5 to +2.6) |
| Selective accuracy | 93.4% | 100.0% | +6.6 (+5.3 to +7.8) |
| Exact match | 64.5% | 71.5% | +6.9 (+4.4 to +9.5) |
| Correct-answer yield | 87.3% | 89.6% | +2.4 (+1.5 to +3.3) |
| Coverage | 93.4% | 89.6% | -3.7 (-5.0 to -2.6) |
| Condition recall | 57.2% | 53.4% | -3.8 (-7.8 to -0.1) |

Selective accuracy counts only the answers given; correct-answer yield counts
the correct answers among all requests; coverage is the share of requests
answered. The application released no unsupported statement. It pays for that by
withholding some answers the baseline gave, which lowers coverage and recall.
Of 91 explanations, the baseline gave all of them, 17 with an unsupported
statement; the application released 73, every one supported. The full figures,
the prose rubric, the questions typed end to end and the held-out read of
MIMIC-IV-Ext-Instr are in [docs/final-figures.md](docs/final-figures.md).

## Quality

More than 1,600 tests run on invented fixtures. CI runs them on Windows on
every push, together with every module's synthetic mode and the publication
audit, which checks every file and every commit for secrets, patient
identifiers and machine paths.

Mutation tests break correct answers one fact at a time, and every broken
answer must be withheld. On the development case sets, all 24,770 mutants of
condition answers and explanations (65 kinds of error) and all 30,921 mutants
of answers about the whole record are caught. On the invented fixtures, as CI
runs it:

```text
> python -m ehr_evidence_app.validation.mutations
originals released unchanged                 18 of 18
absence_worded_as_record                     detected 4 of 4
absent_reference                             detected 10 of 10
...
mutants detected                             584 of 584 in 64 classes
RESULT: PASS
```

Checks on real data print aggregate counts only, as the condition suite does
on the development case set:

```text
> python -m ehr_evidence_app.qa.suite --operator-real --case-set dev-30-v1
questions                                    1892
correct                                      1892 (100.0%)
```

## Run the demo

The demo serves invented patients with a scripted model, so it needs no data,
GPU or credentials, only Windows and Python 3.12.

```powershell
git clone https://github.com/maa2002/ehr-evidence-app.git
cd ehr-evidence-app
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --require-hashes --no-deps -r requirements\app.lock.txt
$env:PYTHONPATH = "$PWD\src"
.venv\Scripts\python -m ehr_evidence_app.api.launch
```

Then open <http://127.0.0.1:8501/>; Ctrl+C stops it. The tests run with
`.venv\Scripts\python -m pytest -q`.

## Documentation

| Document | Contents |
| --- | --- |
| [Architecture](docs/architecture.md) | Purpose, data flow, packages, configuration, design rules |
| [Data handling](docs/data-handling.md) | Storage layout, destinations, path checks, publication audit, secrets |
| [Source data](docs/source-data.md) | OMOP source, staged exports, bounded pilot extraction |
| [Inherited GNN](docs/inherited-gnn.md) | Model, bridge contract, identity mapping, target and history, parity, limitations |
| [Model outputs](docs/model-outputs.md) | Published records, superseded flags, admission, claim policy |
| [Record evidence](docs/evidence.md) | Contracts, evidence records, scopes and coverage, event linkage, bundles |
| [Case sets](docs/case-sets.md) | Development and evaluation case sets: registry, draw rule, preparation |
| [Natural-language QA](docs/qa.md) | Interpretation, scope, concept resolution and matching, answers, suites |
| [Validation](docs/validation.md) | Candidate contract, checks, explanations, release gate, mutation suite |
| [Generation](docs/generation.md) | Model-written candidates: schema, the model's view of the evidence, prompt, limits, fallbacks |
| [API and interface](docs/interface.md) | The local API, its routes, the page, access checks and the launcher |
| [Experiment tracking](docs/experiment-tracking.md) | ClearML policy and connection, controlled tracking, the final figures, lineage |
| [The final evaluation](docs/assessment.md) | Partition, references, baseline, paired runner, metrics, prose rubric, the freeze |
| [Final figures](docs/final-figures.md) | The final run's figures and how to read them |
| [Acceptance matrix](docs/acceptance.md) | Each acceptance test and requirement, with what was run and observed |
| [Environments](docs/environments.md) and [development](docs/development.md) | Environments, set-up, tests, CI, reconstruction, releases |
| [Decisions](docs/decisions/) and [changelog](CHANGELOG.md) | Architecture decision records and release history |

<details>
<summary>Packages in <code>src/ehr_evidence_app</code></summary>

| Package | Responsibility |
| --- | --- |
| `storage` | Fixed storage roots, path checks, destination set-up, volume preflight |
| `sources` | OMOP export staging; bounded read-only extraction of MIMIC-IV tables |
| `gnn` | The inherited model's identity, validation, identity mapping, parity, outputs, provenance, event linkage |
| `evidence` | Request and response contracts, evidence records, scoped retrieval, QA, explanation and record bundles |
| `casesets` | Versioned development and evaluation case sets: registry and seeded selection |
| `qa` | Vocabularies, question interpretation, concept and scope resolution, answers, suites |
| `validation` | Candidate contract, checks, template explanation, release gate, mutation suite |
| `generation` | Model-written candidates: document schema, the model's view of the evidence, prompt, adapter, worker, measurement |
| `assessment` | The final evaluation: partition, references, baseline, paired runner, metrics, rubric, end-to-end assessment, freeze |
| `api` and `interface` | The local API, its access boundary, the launcher and the page |
| `tracking` | Tracker policy, ClearML connection checks, controlled tracking, measurements, lineage |
| `environment` and `quality` | Environment verification; publication audit and release checks |

</details>

## Reproducing the full system

The full system needs credentialed data and a few external pieces. With them,
[docs/environments.md](docs/environments.md) and
[docs/development.md](docs/development.md) describe the set-up, and the
[reconstruction](docs/development.md#reconstruction-and-restart) gives the
order in which a fresh clone was rebuilt and restarted.

| What | Where |
| --- | --- |
| PhysioNet credentialed access (training and a signed data use agreement) | [physionet.org](https://physionet.org/) |
| MIMIC-IV 3.1, hospital and ICU modules | [physionet.org/content/mimiciv/3.1](https://physionet.org/content/mimiciv/3.1/) |
| MIMIC-IV-Ext-Instr 1.0.0, for the held-out reading test | [physionet.org/content/mimic-iv-ext-instr/1.0.0](https://physionet.org/content/mimic-iv-ext-instr/1.0.0/) |
| MIMIC-IV in the OMOP CDM, in PostgreSQL, built with the OHDSI MIMIC ETL | [github.com/OHDSI/MIMIC](https://github.com/OHDSI/MIMIC) |
| The OMOP standardized vocabularies (CONCEPT, CONCEPT_ANCESTOR and the rest) | [athena.ohdsi.org](https://athena.ohdsi.org/) |
| The patient-journey GNN (`standardized-omop-gnn`), trained on the same OMOP data | [anonymous.4open.science/r/standardized-omop-gnn-7B82](https://anonymous.4open.science/r/standardized-omop-gnn-7B82/) |
| MedGemma 1.5 4B IT, after accepting its terms on Hugging Face | [huggingface.co/google/medgemma-1.5-4b-it](https://huggingface.co/google/medgemma-1.5-4b-it) |
| Windows 11 with PowerShell 5.1, Git, uv and Python 3.12.8 | |
| An NVIDIA GPU with 8 GB of memory, for the model in bfloat16 | |
| Optional: a ClearML account for experiment tracking, and a Cloudflare tunnel with a domain for sharing | |

The GNN run this project uses is the knowledge-graph variant of the diagnosis
task, replicated with a test macro AUROC of 0.906 over 275 diagnosis labels
([inherited GNN](docs/inherited-gnn.md)).

## Licences

- The code is released under the [MIT licence](LICENSE).
- MIMIC-IV and MIMIC-IV-Ext-Instr are available under the PhysioNet
  Credentialed Health Data License 1.5.0. No data is distributed with this
  repository, and running the full system needs your own credentialed access.
- The OMOP standardized vocabularies come from Athena, each vocabulary under
  its own terms.
- The OHDSI MIMIC ETL is licensed under the Apache License 2.0.
- MedGemma is used under the Health AI Developer Foundations terms of use.
- The patient-journey GNN is used under the terms of its repository.

## Citation

If you use MIMIC-IV or MIMIC-IV-Ext-Instr, PhysioNet asks you to cite them:

- Johnson, A., Bulgarelli, L., Pollard, T., Gow, B., Moody, B., Horng, S.,
  Celi, L. A., & Mark, R. (2024). MIMIC-IV (version 3.1). PhysioNet.
  <https://doi.org/10.13026/kpb9-mt58>
- Johnson, A. E. W., Bulgarelli, L., Shen, L., et al. (2023). MIMIC-IV, a
  freely accessible electronic health record dataset. *Scientific Data*, 10, 1.
  <https://doi.org/10.1038/s41597-022-01899-x>
- Pollard, T., Moody, B. E., Lehman, L., Gow, B., Fernandes, C., Xie, C.,
  Johnson, A., Mark, R. G., & Heldt, T. (2026). PhysioNet as a global platform
  for biomedical research. *Nature Health*.
  <https://doi.org/10.1038/s44360-026-00096-z>
- Wu, Z., Dadu, A., Nalls, M., Faghri, F., & Sun, J. (2025).
  MIMIC-IV-Ext-Instr: A Dataset of 450K+ EHR-Grounded Instruction-Following
  Examples (version 1.0.0). PhysioNet. <https://doi.org/10.13026/e5bq-pr14>
- Wu, Z., Dadu, A., Nalls, M., Faghri, F., & Sun, J. (2024). Instruction tuning
  large language models to understand electronic health records. *Advances in
  Neural Information Processing Systems 37, Datasets and Benchmarks Track*.

The other components:

- Hripcsak, G., Duke, J. D., Shah, N. H., et al. (2015). Observational Health
  Data Sciences and Informatics (OHDSI): opportunities for observational
  researchers. *Studies in Health Technology and Informatics*, 216, 574-578.
- Sellergren, A., Kazemzadeh, S., Jaroensri, T., et al. (2025). MedGemma
  technical report. arXiv:2507.05201. <https://arxiv.org/abs/2507.05201>
- The OHDSI MIMIC ETL: <https://github.com/OHDSI/MIMIC>
- The patient-journey GNN: `standardized-omop-gnn`,
  <https://anonymous.4open.science/r/standardized-omop-gnn-7B82/>
