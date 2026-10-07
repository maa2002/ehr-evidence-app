# Record Evidence

Phase 4 turns the admitted cases of the Phase 3 closure into versioned
evidence bundles. A bundle is the only thing later layers (question
interpretation, generation, validation) receive about a case: records they
may cite, what the retrieval covered, and the versions of every input it was
built from. Nothing in a bundle is recomputed from the model; predictions
and attributions are copied from the published outputs unchanged.

Decisions are recorded in
[ADR 0004](decisions/0004-evidence-contracts.md) (contracts, scopes and
references) and [ADR 0005](decisions/0005-event-linkage-and-claim-policy.md)
(event linkage and the evidence claim policy). Command examples assume the
session set-up in [development.md](development.md#session-set-up).

## Modules

| Work Item | Module | Role |
| :--- | :---: | ---: |
| 23 | `evidence.contracts` | Request, Response and Execution-Limit Contracts; Committed JSON Schemas |
| 24 | `evidence.schema` | Evidence Records and the QA and Explanation Bundle Types |
| 25 | `evidence.readers` | Pinned Readers for the Source Snapshot and the Closure Record's Cases |
| 26 | `evidence.concepts` | Readable Concept Names With a Stated Source |
| 27 | `evidence.retrieval` | Scoped Retrieval With an Explicit Coverage Record |
| 28 | `gnn.linkage`, `evidence.policy` | Driver-to-Event Linkage Record; Evidence Claim Policy |
| 29, 30 | `evidence.bundles` | QA and Explanation Bundle Sets |
| 52.3 | `evidence.record`, `evidence.record_bundles` | Record Bundles: The Whole Record of a Case, Scope by Scope |

Closed vocabularies (tasks, scopes, outcomes, reason codes, coverage states,
visit roles, attachment classes, link states) are fixed in
`evidence.vocabulary`, so a new value is a deliberate, versioned change.

## Contracts

A request binds one task to one case and one explicit scope. Tasks are
`condition_list` and `condition_presence` (QA) and `prediction_explanation`.
QA requests carry a question and no prediction reference; an explanation
request carries the selected prediction's output-record digest, no question,
and the pre-target history scope.

A response carries exactly one outcome:

| Outcome | Text Released | Reason Codes |
| :--- | :---: | ---: |
| `answer`, `not_recorded` | Yes, With Claims Citing Its Bundle | Optional Disclosure (`unmapped_records_in_scope`) |
| `explanation` | Yes, With Claims Citing Its Bundle | Optional Disclosure (Restrictions) |
| `clarification`, `abstention` | Yes | Required, From the Outcome's Own List |
| `case_rejection`, `validation_rejection`, `operational_error` | No Text, No Claims | Required, From the Outcome's Own List |

`not_recorded` means *not recorded within the selected scope*; it never
states clinical absence. Contracts are strict, immutable and refuse unknown
fields. Their JSON Schemas are committed in [schemas/](schemas/) and a test
fails when they drift from the models; regenerate them with
`--write-schemas`.

Execution limits (question length, prompt and output tokens, concurrency,
duration, attempts, evidence items and bundle bytes) have fixed ceilings
in code. `config/execution_limits.json` may lower a limit, never raise it;
a request outside a limit ends as an explicit outcome. On the generation
path the model's attempt ends instead and the template answers, a
recorded fallback ([generation.md](generation.md#limits)).

## Evidence Records

Records are referenced inside a bundle by bundle-local identifiers, so a
response cites evidence without exposing native record numbers. The native
identifiers stay in the records.

| Reference | Record | Content |
| :--- | :---: | ---: |
| `V1`.. | Visit | Graph Position, Visit Number (Position + 1), Role (`pre_target`, `final_admission`), Visit Concept and Dates |
| `E1`.. | Event | One Recorded Condition, Procedure or Drug Row: Native Identifier, Source Table, Visit, Concept, Dates |
| `P1` | Prediction | Selected Label, Raw Logit, Uncalibrated Score, Output-Record Digest, Partition, Batch Sensitivity |
| `A1`.. | Attribution | One Published Driver: Concept, Unsigned Score, Closure Tags, Link Status, Linked Events |
| `C1` | Coverage | Scope, Status, Counts and the Coverage Statement |
| - | Provenance | Name and SHA-256 of Every Input, Schema Version and Builder |

Invariants enforced by the record types:

- Repeated occurrences stay separate events: one event per native record.
- Names are never invented. A concept is `available` (with the table that
  named it), `unavailable`, or `unmapped` (concept 0, which keeps its
  recorded source code).
- Attribution is concept-level. An attribution lists the events of its
  concept that the linkage record ties to its graph edges; no score is ever
  attached to, divided across or tied to an event, a date or a visit.
- Coverage counts agree with the delivered events. An empty result is
  `complete_empty`, never `complete`; `unavailable` and `failed` coverage
  delivers nothing and states a reason.
- An attribution's attachment (from the closure record) and its link status
  (from the linkage record) must agree, and a bundle's restrictions must be
  exactly those its content implies.

A response is bound to one bundle by identifier and digest, and that bundle
to one case and scope; `schema.citation_problems` reports a response that
names another bundle, case or scope (`scope_violated`) or cites a reference
the bundle lacks (`reference_unresolved`). Phase 6 extends the check to
the candidates responses are rendered from, and checks whether a cited
record supports its claim (see [validation.md](validation.md)).

## Names

Concept names come from two pinned tables, in order: the source snapshot's
concept table, then the legacy concept map the inherited bridge used (for
concepts the snapshot lacks, such as a predicted label that is not among the
patient's records). Every name states its source. For the pilot, all 198
names in the explanation bundles (10 predictions, 188 attributions) equal
the names in the published outputs.

## Scopes and Coverage

| Scope | Visits | Used By |
| :--- | :---: | ---: |
| `pre_target_history` (Default) | Positions 0 to *N*-2 | QA and Explanations |
| `selected_visit` | One Visit, Numbered From 1 in Graph Order | QA Only |
| `case_visits` | All Case Visits, Including the Final Admission | QA Only |

Coverage is complete only for an admitted case read from a complete
snapshot. It counts unmapped records in scope, final-admission records a
history scope excludes, and records of the person that no case visit holds
(never delivered). Its statement fixes what "complete" means: complete
within the OMOP person, visit and event tables for the case's person at the
recorded source snapshot; not a statement of clinical absence.

Records without a standard concept (concept 0) are kept, with their source
code, and disclosed through the coverage counts and the
`unmapped_records_in_scope` reason code.

## Event Linkage

`gnn.linkage` links each published driver's visit-to-concept graph edges to
the source events of the same person, visit and concept in the bounded
source snapshot. Per driver it records the attached visits with the native
identifiers of the events behind each edge, and a link status: `verified`
(every edge has events), `partial`, `unlinked`, `no_edges` or
`not_a_patient_node`. It also checks that the graph is complete for the
snapshot: every event in a case visit whose concept is in the model's
vocabulary must appear as an edge. Scores are not read or divided.

The record states its overall status; the bundle builder accepts it only
when every edge and driver verifies. It reads the raw graphs through their
verified handle on the CPU in the GNN environment; no model is loaded.

| Domain | Edges Verified | Drivers Verified | In-Vocabulary Events Without an Edge |
| :--- | :---: | :---: | ---: |
| Condition | 324 of 324 | 82 | 0 |
| Procedure | 30 of 30 | 26 | 0 |
| Drug | 301 of 301 | 85 | 0 |

Events outside the model's vocabulary are exactly the unmapped (concept 0)
records.

## QA Bundles

For every admitted case, one bundle per scope: the pre-target history, each
case visit, and all case visits (`qa-c01-history`, `qa-c01-visit-1`,
`qa-c01-all`, ...). A QA bundle holds the scope's visits, every condition
event in them, and the coverage record. Listing and presence checking use
the same bundle.

## Explanation Bundles

For every admitted case, one bundle (`exp-c01`, ...) holding:

- the selected prediction, copied unchanged and checked against the
  output-record digest in the closure record;
- the published drivers as attributions, with their scores unchanged, their
  closure tags and their link status. Target-outcome entries (diagnoses
  attached only to the final admission) are left out and counted;
- the events the linkage record ties to each attribution. Events in
  pre-target visits are history. Drug and procedure events of the final
  admission are kept apart as final-admission context. Diagnoses recorded in
  the final admission are withheld and only counted;
- every case visit, the coverage of the linked history, the case's
  restrictions and its eligibility.

The builder checks each driver's closure attachment against the visits its
linkage entry lists, and the bundle's eligibility against the closure
record.

## Record Bundles

Questions about the whole record ([ADR 0021](decisions/0021-the-whole-record.md))
are answered from record bundles, one per case and scope like the QA
bundles (`rec-c01-history`, `rec-c01-visit-1`, `rec-c01-all`). A record
bundle holds the person (`D1`: sex, anchor age and year, date of death),
the scope's visits (`V` and the visit's number) with their admissions
(type, locations, times, death in hospital, insurance, language, marital
status, race), every record of those visits (`R1`..) and a coverage
(`C1`) of every kind:

| Kind | Source | Holds |
| :--- | :---: | ---: |
| Condition, Procedure, Medication | OMOP Snapshot | Concept, Source Code and Dates; a Medication's Ingredients and ATC Classes, and Its Prescription's Dose and Route |
| Lab | Raw Laboratory Results | Test, Fluid and Category, Value, Unit, Reference Range, Flag, Time |
| Microbiology | Raw Microbiology | Specimen, Test, Organism, Antibiotic, Interpretation, Time |
| Vital | Raw ICU Chart Events | The Vital Sign, Its Value and Unit, Time |
| Transfer, icu_stay | Raw Transfers and ICU Stays | Care Unit, Event, Times, Length |
| icu_procedure | Raw ICU Procedure Events | The Procedure, Its Value, Category and Times |

A raw row belongs to a case visit when its admission is the one the
snapshot records for the visit (`visit_source_value`,
"subject_id|hadm_id"); the person's other rows are counted as outside
the case visits and never delivered, and a history bundle counts the
final admission's records it leaves out. A medication's ingredients and
classes come from the pinned record vocabulary (`qa.record_vocabulary`,
version 1; version 2 adds each class's ingredients for the matching of
records without a standard concept, every version 1 file unchanged,
[ADR 0022](decisions/0022-record-edge-cases.md));
its dose and route from the raw prescription it was made from, found by
the source ETL's own keys (admission, start and stop times, and the NDC,
else the drug's name and strength), and attached only when every
prescription that fits them agrees. Names are never invented: a record
without a standard concept keeps its source description, or its written
drug name. The record types enforce that a record holds the fields of its
kind only and that the coverage counts agree with the records.

| Measure | `pilot-10-v1` | `dev-30-v1` | `eval-100-v1` |
| :--- | :---: | :---: | ---: |
| Record Bundles | 58 | 135 | 519 |
| Records in Case Visits (All-Visits Scope) | 5,270 | 29,514 | 81,297 |
| Laboratory Results | 3,531 | 13,390 | 43,002 |
| Vital-Sign Records | 294 | 11,001 | 21,092 |
| Medications (With Dose, Without a Standard Concept) | 887 (871, 385) | 3,209 (2,984, 1,455) | 11,223 (10,709, 4,777) |
| Microbiology, Transfers, ICU Stays, ICU Procedures | 41, 121, 1, 6 | 321, 318, 20, 146 | 1,199, 1,187, 46, 282 |
| Largest Bundle (Records, Bytes) | 1,479, 1,048,833 | 3,999, 2,825,542 | 6,975, 4,870,133 |

```powershell
& $AppPython -m ehr_evidence_app.evidence.record_bundles
& $AppPython -m ehr_evidence_app.evidence.record_bundles --operator-real --case-set dev-30-v1
```

## Claim Policy

`evidence.policy` extends the closure record's claim policy as a new,
versioned policy (`evidence-policy-v1`, mirrored in
`config/evidence_policy.json`); the closure record itself is unchanged.

| Change | Statement |
| :--- | ---: |
| Added | When, and in How Many Pre-Target Visits, a Driver's Concept Was Recorded, Citing the Linked Events |
| Added | Repeated Recordings of a Driver's Concept, Citing Each Event |
| Narrowed | "Dated, frequency or progression claims about events" Becomes "event claims without cited, linked events" |

Every other prohibition stands, including tying a score to a date, a visit
or a recurrence. Conditions attached to the policy: history statements cite
linked events in pre-target visits only; final-admission drug and procedure
records are described only as final-admission context; final-admission
diagnoses never enter an explanation. The template explanation and its
checks enforce the policy (see [validation.md](validation.md#explanations)).

## Published Records

Paths are under `G:\Industry Dataset\ehr-evidence-workspace\protected-data`.

| Record | Directory | Files |
| :--- | :---: | ---: |
| Event Linkage | `evidence\event-linkage-pilot10-v1` | `linkage.json` (Patient-Bearing; Pinned in `evidence.bundles`), `summary.json` (Aggregate) |
| Evidence Bundles | `evidence-bundles\pilot10-v1` | `qa\*.json`, `explanation\*.json` (Patient-Bearing); `manifest.json` (Identifiers, Digests, Sizes); `summary.json` (Aggregate) |

Bundle-set counts for the development pilot:

| Measure | Result |
| :--- | ---: |
| QA Bundles (History / Selected Visit / All Visits) | 58 (10 / 38 / 10) |
| QA Coverage Complete / Complete and Empty | 56 / 2 |
| Condition Records in the Pre-Target History | 251 |
| Final-Admission Condition Records Excluded From the History | 105 |
| Condition Records in All Case Visits | 356 |
| Unmapped Condition Records Kept (in the History) | 29 (19) |
| Explanation Bundles, Eligible | 10, 10 |
| Attributions (All Narratable) | 188 |
| Target-Outcome Entries Excluded | 5 |
| Linked History Events | 289 |
| Final-Admission Context Events | 136 |
| Final-Admission Diagnoses Withheld | 31 |

The manifest's digest is recorded in `summary.json`; every bundle file is
listed in the manifest with its size and SHA-256.

## Commands

Default modes build invented fixtures in memory:

```powershell
& $AppPython -m ehr_evidence_app.evidence.contracts
& $AppPython -m ehr_evidence_app.evidence.bundles
& $AppPython -m ehr_evidence_app.gnn.linkage
```

Real runs are explicit and write one new protected directory each; both
records exist, so a re-run refuses:

```powershell
& $GnnPython -m ehr_evidence_app.gnn.linkage --operator-real
& $AppPython -m ehr_evidence_app.evidence.bundles --operator-real
```

## Case Sets

The same linkage and bundle builders run for the development and
evaluation case sets of work item 30.1, from each set's own pinned records;
see [case-sets.md](case-sets.md) for their preparation and results.
