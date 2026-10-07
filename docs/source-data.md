# Source Data

The clinical source is MIMIC-IV 3.1, transformed to the OMOP Common Data
Model by the upstream OHDSI MIMIC ETL and loaded into a local PostgreSQL
database. This project reads that database and the upstream's CSV exports;
it never modifies them. Command examples assume the session set-up in
[development.md](development.md#session-set-up).

| Source | Location | Access |
| :--- | :---: | :--- |
| OMOP CDM Database | Local PostgreSQL 18, Database `mimiciv_omop`, Schema `omopcdm` | Read-Only Transactions on Loopback |
| Legacy CSV Exports | `C:\omop_export` | Read-Only Copy Source |
| Raw MIMIC-IV 3.1 Hospital and ICU Modules | External Archive Disk | Eleven Pinned Files Read Once, Read-Only, for Exactly the Case Sets' Persons ([Raw MIMIC-IV Records](#raw-mimic-iv-records)) |
| Athena Vocabularies (Release of 31 August 2026) | External Archive Disk | Four Pinned Files Read Once, Read-Only, to Build the Condition Vocabulary ([ADR 0006](decisions/0006-condition-vocabulary.md)) and the Medication and Procedure Vocabulary ([ADR 0021](decisions/0021-the-whole-record.md); Version 2 in [ADR 0022](decisions/0022-record-edge-cases.md)) |

**Source identity.** The upstream ETL code is in the inherited tree
(`OHDSI_MIMIC\`) and its loader reads the MIMIC-IV 3.1 files. This is
documented provenance: nothing cryptographically binds the database contents
to a specific release or ETL run. Records therefore store the observed
database name, schema, server version and snapshot, not a claimed release.

## Staged Legacy Exports

`ehr_evidence_app.sources.exports` copies five files from `C:\omop_export`
into a versioned protected directory and profiles the copies:

```text
G:\Industry Dataset\ehr-evidence-workspace\protected-data\source-exports\<version-label>
```

The published version is `legacy-export-20260901-v1` (about 3.44 GB):
`clinical_visits.csv`, `clinical_conditions.csv`,
`clinical_procedures.csv`, `clinical_drugs.csv` and `kg_concepts.csv`.
Only these named files are opened; the source directory is never listed.

Integrity, per file:

1. Stat the source (size and modification time in nanoseconds).
2. If the destination exists, verify instead of copying: re-read it and
   compare size and SHA-256 with the source. A mismatch is reported and the
   destination is left untouched, never overwritten.
3. Otherwise stream the source in 8 MiB chunks into `<name>.csv.partial`,
   hashing as it reads, and `fsync`.
4. Stat the source again; a change during the copy fails the table.
5. Rename the partial file only when byte count, size and SHA-256 all
   match.

`manifest.json` records per table the source path, size, modification time,
source and destination SHA-256, headers, parsed row count, counts of rows
missing required identifiers, the action and a `complete` or `incomplete`
status. An in-progress manifest replaces the previous one before work
starts, so an interrupted run can never leave an old `complete` manifest
looking current. Profiling reads the staged copy and records aggregate
counts only.

**Export semantics** (from the export SQL in the inherited tree,
`OHDSI_MIMIC\export\01_export_clinical_events.sql`, not observed from the run
that produced the files):

- The cohort is people with at least two visits of `visit_concept_id` 9201,
  9203 or 262; only those visits and their linked events are exported.
- `clinical_visits.hadm_id` is the OMOP `visit_occurrence_id`, not a MIMIC
  `hadm_id`; `hadm_source_id` carries `visit_source_value`.
- The next-visit join used for readmission fields can repeat visit rows;
  repeated rows are staged unchanged.
- Native event keys (`condition_occurrence_id`, `procedure_occurrence_id`,
  `drug_exposure_id`) are absent, so events cannot be traced to source rows
  from these files alone.
- The execution provenance of the CSV files is not established.

These files must not support complete-negative claims ("the patient has no
record of X"): absence may reflect the cohort filter, the visit filter or an
unknown export run.

```powershell
& $AppPython -m ehr_evidence_app.sources.exports --version-label <new-label>
```

Options: `--version-label` (required), `--source-dir`, `--dest-root`,
`--config`. Exit code 0 only when every table is `complete`. Running against
an existing version re-verifies it and rewrites its manifest, so the label
must name a new or unpublished version, never `legacy-export-20260901-v1`.

## Bounded Read-Only Pilot

`ehr_evidence_app.sources.pilot` extracts, in one read-only transaction,
every row of the core OMOP tables for a small, deterministic set of persons.
It supplies what the legacy exports cannot: native event identifiers and
verified per-person coverage.

**Selection.** The first *N* `person_id` values in numeric order among
persons with at least two distinct `visit_occurrence` rows whose
`visit_concept_id` is 9201, 9203 or 262. *N* defaults to 10 (`--cases`,
1 to 100). The pilot is a development sample: it is not an evaluation cohort
and was not chosen for clinical representativeness.

**Extracted tables**, for exactly the selected persons, with no visit-type,
date or concept filter:

| Table | File | Native Key |
| :--- | :---: | :--- |
| `person` | `persons.csv` | `person_id` (`person_source_value` as `subject_id`) |
| `visit_occurrence` | `visits.csv` | `visit_occurrence_id` |
| `condition_occurrence` | `conditions.csv` | `condition_occurrence_id` |
| `procedure_occurrence` | `procedures.csv` | `procedure_occurrence_id` |
| `drug_exposure` | `drugs.csv` | `drug_exposure_id` |
| `concept` (Referenced IDs Only) | `concepts.csv` | `concept_id` |

The cohort is applied as a semi-join, so extraction can neither multiply nor
drop source rows. Events with a null visit link, repeated events and concept
id 0 are kept and counted.

**Read-only enforcement.** `PGOPTIONS` forces
`default_transaction_read_only=on` and statement, lock and
idle-in-transaction timeouts. The SQL opens
`BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY`, and its first statement
reads back the database, server version, isolation level, both read-only
settings and `pg_current_snapshot()`, which are validated. Metadata, counts
and all six extracts run in that one transaction from one generated SQL file
(`psql -X -w -v ON_ERROR_STOP=1 -f ...`), so counts and extracts see the same
snapshot. Extracts use client-side `\copy`; there is no DDL, DML,
server-side `COPY` or snapshot export.

**Validation**, on the written files: required columns, unique non-empty
native keys, a one-to-one `person_id`/`subject_id` mapping, every row
belonging to a selected person, every non-null visit link resolving to a
visit of the same person, and each table's row count equal to an independent
count from the same transaction. Extracts are written as `.partial` files and
renamed only after validation. Extraction never overwrites an existing
version directory.

```text
G:\Industry Dataset\ehr-evidence-workspace\protected-data\source-pilot\<version-label>
```

The published version is `pilot-10-v1`. `--verify-only` re-checks an existing
snapshot without any database access: manifest state, configuration hash,
every file's size and SHA-256, no unexpected files, and the same local
validation. It never rewrites the manifest, but it does write (or replace)
`verification-report.json` in the snapshot directory.

`pilot-10-v1` was extracted before the storage move, under a configuration
(SHA-256 `781dc6d0…c8e008b7f3`, preserved in the private workspace archive)
that differs from the current `config/source_pilot.json` only in its two
destination-root fields. It already holds its verification report and is
immutable, so do not run `--verify-only` against it. Consumers of the
snapshot (such as `gnn.mapping`) verify its files against the manifest and
pin the archived configuration.

```powershell
$env:PGHOST = "127.0.0.1"
$env:PGPORT = "5432"
$env:PGDATABASE = "mimiciv_omop"
$env:PGUSER = "<read-only user>"
$env:PGPASSWORD = "<password>"   # process environment only
& $AppPython -m ehr_evidence_app.sources.pilot --version-label <new-label>
& $AppPython -m ehr_evidence_app.sources.pilot --version-label <new-label> --verify-only
```

Options: `--version-label`, `--cases`, `--dest-root`, `--config`, `--psql`,
`--subprocess-timeout`, `--verify-only`, `--case-set`.

Before it creates the version directory, the extraction opens one
read-only connection and runs `SELECT 1` with the same flags and
environment. A wrong user or password, or a stopped server, therefore ends
the run with nothing written, and the same label can be used again.

With `--case-set <label>` the same extraction selects exactly the subjects
of that case set's pinned selection record instead of the first *N*
qualifying persons, and writes to `protected-data\source-casesets\<label>`;
see [case-sets.md](case-sets.md).

**Coverage.** Within the five queried tables, for the selected persons, at
the recorded snapshot, absence is observable because no filter beyond the
person selection is applied. This is not a statement of clinical absence or
of complete EHR coverage: measurements, observations, notes, devices, deaths
and other sources are not queried.

## Connection Policy

Fixed in code; configuration may narrow it but not widen it:

- `PGHOST` must be `127.0.0.1`, `::1` or `localhost`; host lists are refused.
- `PGDATABASE` must be exactly `mimiciv_omop`; conninfo strings and URIs are
  refused.
- `PGPORT` must be a plain integer from 1 to 65535.
- `PGHOSTADDR`, `PGSERVICE` and `PGSERVICEFILE` are refused when set, because
  they can redirect the connection.
- `PGUSER` and `PGPASSWORD` must be present; their values are never printed,
  logged, passed as arguments or stored.
- Timeouts must be positive integers.

psql runs with an allow-listed environment: operating-system basics, the
validated connection variables and the forced options. An inherited
`PGOPTIONS`, `PGPASSFILE` or other `PG*` variable is never passed on.

## Raw MIMIC-IV Records

The OMOP snapshots hold visits, conditions, procedures and drug
exposures. The rest of a patient's record is read from the raw MIMIC-IV
3.1 files ([ADR 0021](decisions/0021-the-whole-record.md)): the local
database's laboratory results carry no names, its ICU chart tables were
loaded empty, and it holds no microbiology, transfers or admission
details. `ehr_evidence_app.sources.mimic` reads, from
`W:\Industry Dataset\mimic-iv-3.1`:

| Table | Kept | Key |
| :--- | :---: | :--- |
| `hosp\patients` | Sex, Anchor Age and Year, Anchor Year Group, Date of Death | `subject_id` |
| `hosp\admissions` | Every Column but the Admitting Provider | `hadm_id` |
| `hosp\transfers` | Every Column | `transfer_id` |
| `icu\icustays` | Every Column | `stay_id` |
| `hosp\labevents`, `hosp\d_labitems` | Results With Value, Unit, Reference Range, Flag and Priority; Not the Comments or the Ordering Provider | `labevent_id` |
| `hosp\microbiologyevents` | Specimens, Tests, Organisms, Antibiotics, Dilutions and Interpretations; Not the Comments or the Ordering Provider | `microevent_id` |
| `hosp\prescriptions` | Drug, Strength, Form, Dose, Unit and Route; Not the Order or Pharmacy Identifiers | Row Number |
| `icu\chartevents`, `icu\d_items` | The Vital-Sign Items of a Fixed List (`VITAL_GROUPS`); Not the Caregiver | Row Number |
| `icu\procedureevents` | ICU Procedure Events; Not the Caregiver or the Order Identifiers | Row Number |

Every input is pinned by the size and SHA-256 of the file in the archive
(`INPUTS`; the archive's own checksums are of the compressed files), read
once and hashed over exactly the bytes read, and must be unchanged
during the read. The rows kept are those of exactly the persons of each
pinned source snapshot (`pilot-10-v1`, `dev-30-v1`, `eval-100-v1`),
matched on `subject_id`; nothing else is filtered but the vital-sign
list, so rows that no case visit holds are kept and counted downstream.
Each row keeps its number in its file. The item dictionaries are kept
whole, and the distinct specimen, test, organism, antibiotic and
care-unit names over every row form `d_names.csv`, which links no row to
them. A quoted value may span lines; a record that is not UTF-8 is read
as Latin-1 and counted.

One protected directory per case set,
`protected-data\source-records\<case set>`, is written through a partial
directory and renamed only when every input matched its pin; an existing
one is never overwritten. Its manifest records every input's size and
digest and every output's size, digest and row count, and is pinned in
code (`EXTRACT_PINS`). Console output carries counts only.

```powershell
& $AppPython -m ehr_evidence_app.sources.mimic
& $AppPython -m ehr_evidence_app.sources.mimic --operator-real --measure-inputs
& $AppPython -m ehr_evidence_app.sources.mimic --operator-real
& $AppPython -m ehr_evidence_app.sources.mimic --operator-real --verify-only
```

| Case Set | Admissions | Transfers | ICU Stays | Laboratory Results (in Snapshot Visits) | Microbiology Rows | Prescriptions | Vital-Sign Rows | ICU Procedure Events |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| `pilot-10-v1` | 38 | 135 | 1 | 6,698 (3,531) | 208 | 886 | 294 | 6 |
| `dev-30-v1` | 116 | 461 | 27 | 46,887 (27,971) | 792 | 5,290 | 18,289 | 167 |
| `eval-100-v1` | 407 | 1,629 | 53 | 104,254 (54,612) | 3,335 | 14,402 | 24,263 | 316 |

Every admission of a snapshot visit was found. Laboratory results and
microbiology outside the snapshot visits are mostly outpatient.

## Limitations

- The pilot is 10 persons; evaluation uses the larger, separately versioned
  case sets of [case-sets.md](case-sets.md).
- Referenced concepts missing from `concept` keep empty names; labels are
  never fabricated.
- Row-count agreement shows internal consistency of a snapshot, not ETL
  correctness.
- The raw files are identified by their own digests; nothing
  cryptographically binds them to the archive's compressed files or to
  the rows the database was loaded from, beyond the shared persons and
  admissions.
