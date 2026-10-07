# 0021. The Whole Record Answerable

Status: accepted (2026-09-29). Amends [ADR 0016](0016-evaluation-metrics.md)
(decision 11) and [ADR 0019](0019-api-and-interface.md) (the evidence the
interface shows). Points 5 to 7 amended by
[ADR 0022](0022-record-edge-cases.md): medications without a standard
concept found by drug class, notices tied to their tasks, and the rules'
second versions. Point 7 amended by
[ADR 0023](0023-consistency-and-junk-input.md): ICU stays and transfers
listed in time order.

## Context

Questions were answered about recorded conditions only; any other
question was refused. The project owner asked that everything relevant
in a patient's record be questionable and answerable, laboratory results
and vital signs included, and decided on 2026-09-29: the templates write
the new answers first, and the model takes them over only once its
answers have been measured; the final evaluation reports the new
questions as categories of their own, the condition results unchanged;
laboratory results and vital signs are included now; and dates are shown
as the records hold them.

The brief makes condition listing and presence checking the core and
leaves other data types optional, under the same evidence rules. The
OMOP source snapshots hold visits, conditions, procedures and drug
exposures. The local database holds nothing more that could be answered
from: its laboratory results carry no names (MIMIC-IV 3 removed the LOINC
codes from the laboratory item dictionary, so the ETL mapped none), its
ICU chart tables were loaded empty, and microbiology, transfers and the
details of each admission were never loaded. The raw MIMIC-IV 3.1
hospital and ICU modules in the cold archive hold all of them, for the
same persons and admissions. MIMIC-IV-Ext-Instr names the structured
parts of a MIMIC-IV record that questions ask about: laboratory results,
microbiology, prescriptions, procedures, transfers, demographics and
admission details.

## Decision

1. **The record.** A case's record is its OMOP conditions, procedures and
   medications, as before, and from the raw MIMIC-IV 3.1 files: the
   person (sex, anchor age and year, date of death); each admission's
   type, locations, times, death in hospital, insurance, language,
   marital status and race; transfers between care units; ICU stays;
   laboratory results with their reference ranges and the laboratory's
   abnormal flag; microbiology (specimens, tests, organisms,
   antibiotics and their interpretations); vital signs charted in the ICU
   (a fixed list of items: heart rate and rhythm, blood pressure,
   respiratory rate, oxygen saturation, temperature, weight, height,
   Glasgow coma scale, pain level, oxygen flow and inspired oxygen
   fraction); ICU procedure events; and each prescription's dose and
   route. Free-text comments and the identifiers of staff and orders are
   not read. Notes are not in the archive; medication administration,
   pharmacy and order tables, ICU input and output events, outpatient
   measurements (which belong to no admission), billing codes and
   services are not read.
2. **Extraction** (`sources.mimic`). Each raw file is pinned by the size
   and SHA-256 of the file in the archive, read once and hashed over the
   bytes read; the rows kept are those of exactly the persons of each
   pinned source snapshot, matched on subject_id, and nothing else is
   filtered but the vital-sign list. Every row keeps its number in its
   file, the key of rows without their own. The dictionaries of item,
   specimen, organism, antibiotic and care-unit names are kept whole.
   One protected directory per case set, published through a partial
   directory and pinned.
3. **Record bundles** (`evidence.record`, `evidence.record_bundles`). One
   per case and scope, as the QA bundles: the scope's visits with their
   admissions, the person, every record of the scope's visits and a
   coverage of every kind. A raw row belongs to a case visit when its
   admission is the one the snapshot records for that visit; rows of the
   person that no case visit holds are counted, never delivered. A
   medication carries its ingredients and ATC classes from the pinned
   Athena release (`qa.record_vocabulary`), and its prescription's dose
   and route only when every prescription that fits the drug record's
   own keys agrees. The condition QA bundles and the explanation bundles
   are unchanged.
4. **Reading** (`qa.record_interpret`, rules `record-questions-v1`). The
   condition rules read every question first, unchanged. A question they
   refuse as being about other data, times or counts, or holding words
   they cannot place, and one whose condition phrase names no condition,
   is read as a question about the record: its task (a list, a presence
   check, a time, a count, values, flagged results, visits' or the
   person's details), its kind and its phrase. A dose question, a past
   question about death and a first or last result are read too; a
   request for advice, a question about the future and a ranking of
   conditions keep their refusals. Filters by value, times and parts of
   the record that are not scope phrases, pronouns and two kinds at once
   ask again.
5. **Terms** (`qa.record_resolve`, rules `record-resolution-v1`). A phrase
   resolves within its kind as a name of the kind's dictionary, a fixed
   synonym, or the names holding its words; an unknown phrase gets
   suggestions, never a guess. A record matches by its item, its
   medication's ingredient or class, its name as written, or, for a
   condition, the condition rules' own matching; the answer says how.
6. **Answers** (`qa.record_answer`, rules `record-answer-v1`). The
   templates write every answer about the record: its facts are slots
   that name records, visits and the person by reference, and every
   name, value, time, date and age is rendered from what they name
   (`validation.record_text`). A "not recorded" is within the scope and
   never a statement of clinical absence. The model writes none of these
   answers until they have been measured; they are not fallbacks.
7. **Checks.** Every answer leaves through the release gate: structure
   and references (new statement kinds, their citations and order),
   support (`validation.record_support`: the chosen records are the
   latest, first, highest, lowest or flagged ones as stated, and none is
   left out) and the wording rules, unchanged. Responses cite records as
   R, visits as V and the person as D1; an answer about the record may
   hold up to 1,000 claims and 60,000 characters.
8. **The interface.** The evidence of a scope shows the person, each
   visit's admission and a table for every kind of record, each row with
   the reference an answer cites.
9. **Development measurement.** Before any evaluation, the record
   questions are measured on the development case sets only, through the
   service, with aggregate counts; the evaluation set's extract and
   bundles are published and pinned with the others and not read
   otherwise.
10. **Evaluation.** The record questions form strata of their own in the
    final evaluation, reported apart from condition lists, presence
    checks and explanations, by task and by kind (decision 11 of ADR
    0016).

## Consequences

- Every structured part of the record the brief's optional data types
  name can be asked about, under the evidence rules of the core: scoped,
  cited, checked, and never a statement of clinical absence.
- A question about conditions gets exactly the response it got before;
  a question the condition rules refused as being about other data may
  now be answered.
- Vital signs and ICU procedures exist for ICU stays only. Notes cannot
  be asked about. A medication whose drug record cannot be tied to one
  prescription (several drugs ordered at the same time without a code)
  has no name of its own and is listed as such.
- Ages are computed from the anchor age and year, so they may be one more
  than the age on the day, and ages over 89 are not given; dates are the
  shifted dates of MIMIC-IV.
- Reading is by rules, so unusual wording gets a clarifying question
  rather than a guess; everyday names of procedures depend on a fixed
  synonym table.
- For the model to write these answers, a compact projection of the
  record bundles and their measurement are needed, as in Phase 7.
