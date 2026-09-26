# Record evidence

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

| Work item | Module | Role |
| --- | --- | --- |
| 23 | `evidence.contracts` | Request, response and execution-limit contracts; committed JSON Schemas |
| 24 | `evidence.schema` | Evidence records and the QA and explanation bundle types |
| 25 | `evidence.readers` | Pinned readers for the source snapshot and the closure record's cases |
| 26 | `evidence.concepts` | Readable concept names with a stated source |
| 27 | `evidence.retrieval` | Scoped retrieval with an explicit coverage record |
| 28 | `gnn.linkage`, `evidence.policy` | Driver-to-event linkage record; evidence claim policy |
| 29, 30 | `evidence.bundles` | QA and explanation bundle sets |

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

| Outcome | Text released | Reason codes |
| --- | --- | --- |
| `answer`, `not_recorded` | yes, with claims citing its bundle | optional disclosure (`unmapped_records_in_scope`) |
| `explanation` | yes, with claims citing its bundle | optional disclosure (restrictions) |
| `clarification`, `abstention` | yes | required, from the outcome's own list |
| `case_rejection`, `validation_rejection`, `operational_error` | no text, no claims | required, from the outcome's own list |

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

## Evidence records

Records are referenced inside a bundle by bundle-local identifiers, so a
response cites evidence without exposing native record numbers. The native
identifiers stay in the records.

| Reference | Record | Content |
| --- | --- | --- |
| `V1`.. | Visit | Graph position, visit number (position + 1), role (`pre_target`, `final_admission`), visit concept and dates |
| `E1`.. | Event | One recorded condition, procedure or drug row: native identifier, source table, visit, concept, dates |
| `P1` | Prediction | Selected label, raw logit, uncalibrated score, output-record digest, partition, batch sensitivity |
| `A1`.. | Attribution | One published driver: concept, unsigned score, closure tags, link status, linked events |
| `C1` | Coverage | Scope, status, counts and the coverage statement |
| - | Provenance | Name and SHA-256 of every input, schema version and builder |

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

## Scopes and coverage

| Scope | Visits | Used by |
| --- | --- | --- |
| `pre_target_history` (default) | positions 0 to *N*-2 | QA and explanations |
| `selected_visit` | one visit, numbered from 1 in graph order | QA only |
| `case_visits` | all case visits, including the final admission | QA only |

Coverage is complete only for an admitted case read from a complete
snapshot. It counts unmapped records in scope, final-admission records a
history scope excludes, and records of the person that no case visit holds
(never delivered). Its statement fixes what "complete" means: complete
within the OMOP person, visit and event tables for the case's person at the
recorded source snapshot; not a statement of clinical absence.

Records without a standard concept (concept 0) are kept, with their source
code, and disclosed through the coverage counts and the
`unmapped_records_in_scope` reason code.

## Event linkage

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

| Domain | Edges verified | Drivers verified | In-vocabulary events without an edge |
| --- | --- | --- | --- |
| Condition | 324 of 324 | 82 | 0 |
| Procedure | 30 of 30 | 26 | 0 |
| Drug | 301 of 301 | 85 | 0 |

Events outside the model's vocabulary are exactly the unmapped (concept 0)
records.

## QA bundles

For every admitted case, one bundle per scope: the pre-target history, each
case visit, and all case visits (`qa-c01-history`, `qa-c01-visit-1`,
`qa-c01-all`, ...). A QA bundle holds the scope's visits, every condition
event in them, and the coverage record. Listing and presence checking use
the same bundle.

## Explanation bundles

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

## Claim policy

`evidence.policy` extends the closure record's claim policy as a new,
versioned policy (`evidence-policy-v1`, mirrored in
`config/evidence_policy.json`); the closure record itself is unchanged.

| Change | Statement |
| --- | --- |
| Added | When, and in how many pre-target visits, a driver's concept was recorded, citing the linked events |
| Added | Repeated recordings of a driver's concept, citing each event |
| Narrowed | "Dated, frequency or progression claims about events" becomes "event claims without cited, linked events" |

Every other prohibition stands, including tying a score to a date, a visit
or a recurrence. Conditions attached to the policy: history statements cite
linked events in pre-target visits only; final-admission drug and procedure
records are described only as final-admission context; final-admission
diagnoses never enter an explanation. The template explanation and its
checks enforce the policy (see [validation.md](validation.md#explanations)).

## Published records

Paths are under `G:\Industry Dataset\ehr-evidence-workspace\protected-data`.

| Record | Directory | Files |
| --- | --- | --- |
| Event linkage | `evidence\event-linkage-pilot10-v1` | `linkage.json` (patient-bearing; pinned in `evidence.bundles`), `summary.json` (aggregate) |
| Evidence bundles | `evidence-bundles\pilot10-v1` | `qa\*.json`, `explanation\*.json` (patient-bearing); `manifest.json` (identifiers, digests, sizes); `summary.json` (aggregate) |

Bundle-set counts for the development pilot:

| Measure | Result |
| --- | --- |
| QA bundles (history / selected visit / all visits) | 58 (10 / 38 / 10) |
| QA coverage complete / complete and empty | 56 / 2 |
| Condition records in the pre-target history | 251 |
| Final-admission condition records excluded from the history | 105 |
| Condition records in all case visits | 356 |
| Unmapped condition records kept (in the history) | 29 (19) |
| Explanation bundles, eligible | 10, 10 |
| Attributions (all narratable) | 188 |
| Target-outcome entries excluded | 5 |
| Linked history events | 289 |
| Final-admission context events | 136 |
| Final-admission diagnoses withheld | 31 |

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

## Case sets

The same linkage and bundle builders run for the development and
evaluation case sets of work item 30.1, from each set's own pinned records;
see [case-sets.md](case-sets.md) for their preparation and results.
