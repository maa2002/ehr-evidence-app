"""Record bundles: the whole record of one case in one scope.

A record bundle holds, for one admitted case and one scope (the
pre-target history, one visit, or all case visits), every record of the
case's visits in that scope: the person; each visit with the details of
its admission; its conditions, procedures and medications from the OMOP
source snapshot; and, from the raw MIMIC-IV extract (sources.mimic), its
laboratory results, microbiology, vital signs, transfers, ICU stays and
ICU procedure events. Questions about any part of the record are answered
from it (qa.record); the condition QA and explanation bundles are
unchanged.

References are bundle-local: V1.. visits, R1.. records, D1 the person and
C1 the coverage, so a response cites records without their native
identifiers; those, or the row number of a raw row that has no key of its
own, stay in the records.

Invariants enforced here:

* every record lies in a visit of the bundle, and repeated records stay
  separate (one record per source row);
* names are never invented: a record states whether its name is
  available, unavailable, or absent because it has no standard concept;
* a record holds the fields of its kind only: a result's value, unit,
  reference range and flag; microbiology's specimen, organism, antibiotic
  and interpretation; a medication's ingredients and the dose and route
  of its prescription; a stay's care unit and length;
* coverage counts agree with the delivered records, kind by kind; an
  empty kind is complete and empty;
* a history bundle holds no record of the final admission, and a visit's
  bundle only that visit.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, Optional

from pydantic import Field, StringConstraints, model_validator

from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.evidence.contracts import CaseRef, Frozen, \
    Identifier, ScopeRef
from ehr_evidence_app.evidence.schema import DateText, DateTimeText, \
    DigestRef, ShortText, VisitRef

SCHEMA_VERSION = "1.0"
KINDS = ("condition", "procedure", "medication", "lab", "microbiology",
         "vital", "transfer", "icu_stay", "icu_procedure")
SOURCES = {"condition": "condition_occurrence",
           "procedure": "procedure_occurrence",
           "medication": "drug_exposure", "lab": "labevents",
           "microbiology": "microbiologyevents", "vital": "chartevents",
           "transfer": "transfers", "icu_stay": "icustays",
           "icu_procedure": "procedureevents"}
OMOP_KINDS = ("condition", "procedure", "medication")
# The part a record of each kind carries beside its name and times.
PARTS = {"condition": None, "procedure": None, "medication": "dose",
         "lab": "measure", "microbiology": "microbe", "vital": "measure",
         "transfer": "stay", "icu_stay": "stay", "icu_procedure": "measure"}
INTERPRETATIONS = ("S", "R", "I", "P")
COVERAGE_STATEMENT = (
    "Complete within the OMOP visit, condition, procedure and drug tables "
    "of the source snapshot and the raw MIMIC-IV patients, admissions, "
    "transfers, ICU stays, laboratory, microbiology, prescriptions, ICU "
    "procedure and vital-sign chart tables, for the case's person at the "
    "pinned extract; not a statement of clinical absence. Vital signs and "
    "ICU procedures are charted during ICU stays only.")

Kind = Literal[KINDS]
Source = Literal[tuple(SOURCES.values())]
RecordRef = Annotated[str, StringConstraints(pattern=r"^R[1-9][0-9]{0,5}$")]
TimeText = Annotated[str, StringConstraints(
    pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}"
            r"(?: [0-9]{2}:[0-9]{2}(?::[0-9]{2})?)?$")]
Number = Annotated[float, Field(allow_inf_nan=False)]


class Measure(Frozen):
    """A result as recorded: its value as text and, when it is one, as a
    number; its unit; the reference range recorded with it; the flag the
    laboratory set; and the priority of the order. A test recorded
    without a value (its result was in a comment, which is not read) has
    no measure."""

    text: Optional[ShortText] = None
    number: Optional[Number] = None
    unit: Optional[ShortText] = None
    low: Optional[Number] = None
    high: Optional[Number] = None
    flag: Optional[Literal["abnormal"]] = None
    priority: Optional[ShortText] = None

    @model_validator(mode="after")
    def _value(self):
        """A measure holds a value, and a range is ordered."""
        if self.text is None and self.number is None:
            raise ValueError("a measure holds a value")
        if (self.low is not None and self.high is not None
                and self.low > self.high):
            raise ValueError("a reference range is ordered")
        return self


class Microbe(Frozen):
    """A microbiology row: the specimen, the test, the organism grown,
    the antibiotic tested and its interpretation (S, R, I or P) and
    dilution, as recorded."""

    specimen: Optional[ShortText] = None
    test: Optional[ShortText] = None
    organism: Optional[ShortText] = None
    antibiotic: Optional[ShortText] = None
    interpretation: Optional[Literal[INTERPRETATIONS]] = None
    dilution: Optional[ShortText] = None

    @model_validator(mode="after")
    def _susceptibility(self):
        """An antibiotic is tested against an organism."""
        if self.antibiotic is not None and self.organism is None:
            raise ValueError("an antibiotic result names its organism")
        if self.interpretation is not None and self.antibiotic is None:
            raise ValueError("an interpretation belongs to an antibiotic")
        return self


class Dose(Frozen):
    """A prescription's drug as written, its dose, form, strength and
    route, as ordered; linked to the drug record only when every raw
    prescription that could be its source agrees."""

    drug: Optional[ShortText] = None
    amount: Optional[ShortText] = None
    unit: Optional[ShortText] = None
    route: Optional[ShortText] = None
    form: Optional[ShortText] = None
    strength: Optional[ShortText] = None
    drug_type: Optional[ShortText] = None


class Stay(Frozen):
    """A stay in a care unit: the transfer's event, the unit and, for an
    ICU stay, its last unit and its length in days."""

    event: Optional[ShortText] = None
    unit: Optional[ShortText] = None
    last_unit: Optional[ShortText] = None
    days: Optional[Annotated[float, Field(ge=0.0, allow_inf_nan=False)]] = (
        None)


class Entry(Frozen):
    """One record of one source row, kept separate even when repeated."""

    ref: RecordRef
    kind: Kind
    source: Source
    native_id: int = Field(ge=1)
    visit_ref: VisitRef
    name_status: vocab.NameState
    name: Optional[ShortText] = None
    concept_id: Optional[int] = Field(default=None, ge=0)
    code: Optional[ShortText] = None
    vocabulary: Optional[ShortText] = None
    detail: Optional[ShortText] = None
    group: Optional[ShortText] = None
    ancestors: tuple[int, ...] = Field(default=(), max_length=200)
    start: Optional[TimeText] = None
    end: Optional[TimeText] = None
    measure: Optional[Measure] = None
    microbe: Optional[Microbe] = None
    dose: Optional[Dose] = None
    stay: Optional[Stay] = None

    @model_validator(mode="after")
    def _kind(self):
        """The source, concept, name and part follow the kind."""
        if SOURCES[self.kind] != self.source:
            raise ValueError("the source does not match the kind")
        if (self.kind in OMOP_KINDS) != (self.concept_id is not None):
            raise ValueError("only OMOP records carry a concept")
        if (self.name_status == "unmapped") != (self.concept_id == 0):
            raise ValueError("unmapped status is exactly concept 0")
        if (self.name_status == "available") != (self.name is not None):
            raise ValueError("only a record with an available name "
                             "carries one")
        if self.ancestors and self.kind != "medication":
            raise ValueError("only medications carry ingredients")
        parts = {"measure": self.measure, "microbe": self.microbe,
                 "dose": self.dose, "stay": self.stay}
        for part, value in parts.items():
            if value is not None and PARTS[self.kind] != part:
                raise ValueError("a record holds the part of its kind only")
        if self.kind in ("microbiology", "transfer", "icu_stay") and (
                parts[PARTS[self.kind]] is None):
            raise ValueError("a record of this kind holds its part")
        return self


class Admission(Frozen):
    """The raw admission of a case visit, as recorded: its type, where
    the patient came from and went to, its times, whether the patient
    died in hospital, and the details recorded with it."""

    native_id: int = Field(ge=1)
    admission_type: Optional[ShortText] = None
    admission_location: Optional[ShortText] = None
    discharge_location: Optional[ShortText] = None
    admit_time: Optional[DateTimeText] = None
    discharge_time: Optional[DateTimeText] = None
    death_time: Optional[DateTimeText] = None
    ed_registration: Optional[DateTimeText] = None
    ed_out: Optional[DateTimeText] = None
    hospital_death: bool
    insurance: Optional[ShortText] = None
    language: Optional[ShortText] = None
    marital_status: Optional[ShortText] = None
    race: Optional[ShortText] = None

    @model_validator(mode="after")
    def _death(self):
        """A death time belongs to an admission that ended in death."""
        if self.death_time is not None and not self.hospital_death:
            raise ValueError("a death time needs a death in hospital")
        return self


class RecordVisit(Frozen):
    """One case visit with its graph position, role, visit type and, when
    the raw admissions hold it, its admission."""

    ref: VisitRef
    position: int = Field(ge=0)
    visit_number: int = Field(ge=1)
    role: vocab.VisitRole
    visit_concept_id: int = Field(ge=0)
    visit_type: Optional[ShortText] = None
    start_date: Optional[DateText] = None
    end_date: Optional[DateText] = None
    admission: Optional[Admission] = None

    @model_validator(mode="after")
    def _numbering(self):
        """Visit numbers count graph positions from one."""
        if self.visit_number != self.position + 1:
            raise ValueError("visit_number must be position + 1")
        return self


class Person(Frozen):
    """The person, as the raw patients table records them: sex, the age
    at the anchor year, the anchor year's group and the date of death.
    MIMIC-IV gives ages over 89 as 91."""

    ref: Literal["D1"] = "D1"
    native_id: int = Field(ge=1)
    sex: Optional[Literal["F", "M"]] = None
    anchor_age: int = Field(ge=0, le=91)
    anchor_year: int = Field(ge=1900, le=2300)
    anchor_year_group: Optional[ShortText] = None
    date_of_death: Optional[DateText] = None


class KindCoverage(Frozen):
    """What the scope covered of one kind of record: the records
    delivered, those without a standard concept or a name, those of the
    final admission a history leaves out, and those of the person that no
    case visit holds (never delivered)."""

    kind: Kind
    status: Literal["complete", "complete_empty"]
    in_scope: int = Field(ge=0)
    unnamed_in_scope: int = Field(ge=0)
    excluded_final_admission: int = Field(ge=0)
    outside_case_visits: int = Field(ge=0)

    @model_validator(mode="after")
    def _status(self):
        """An empty kind is complete and empty."""
        if (self.status == "complete_empty") != (self.in_scope == 0):
            raise ValueError("an empty kind is complete_empty")
        if self.unnamed_in_scope > self.in_scope:
            raise ValueError("more unnamed records than records")
        return self


class RecordCoverage(Frozen):
    """The coverage of a record bundle: every kind, in order."""

    ref: Literal["C1"] = "C1"
    scope: ScopeRef
    kinds: tuple[KindCoverage, ...]
    source_snapshot: Identifier
    record_extract: Identifier
    statement: Literal[COVERAGE_STATEMENT] = COVERAGE_STATEMENT

    @model_validator(mode="after")
    def _kinds(self):
        """Every kind is covered once, in the fixed order."""
        if tuple(item.kind for item in self.kinds) != KINDS:
            raise ValueError("coverage lists every kind once, in order")
        return self

    def of(self, kind):
        """Return the coverage of one kind."""
        return self.kinds[KINDS.index(kind)]


class RecordProvenance(Frozen):
    """Versions of every input a record bundle was built from."""

    source_snapshot: DigestRef
    record_extract: DigestRef
    identity_mapping: DigestRef
    closure_record: DigestRef
    concept_table: DigestRef
    drug_ancestors: Optional[DigestRef] = None
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    builder: ShortText


class RecordBundle(Frozen):
    """The whole record of one case in one scope."""

    schema_version: Literal["1.0"] = SCHEMA_VERSION
    kind: Literal["record"] = "record"
    bundle_id: Identifier
    case: CaseRef
    scope: ScopeRef
    person: Person
    visits: tuple[RecordVisit, ...] = Field(min_length=1)
    records: tuple[Entry, ...] = ()
    coverage: RecordCoverage
    provenance: RecordProvenance

    @model_validator(mode="after")
    def _references(self):
        """Every record lies in a bundle visit; coverage matches the
        records; the scope holds only its own visits."""
        visit_refs = [visit.ref for visit in self.visits]
        record_refs = [entry.ref for entry in self.records]
        if (len(set(visit_refs)) != len(visit_refs)
                or len(set(record_refs)) != len(record_refs)):
            raise ValueError("references repeat")
        rows = [(entry.source, entry.native_id) for entry in self.records]
        if len(set(rows)) != len(rows):
            raise ValueError("a source row appears twice")
        if any(entry.visit_ref not in visit_refs for entry in self.records):
            raise ValueError("a record lies outside the bundle visits")
        coverage = self.coverage
        if coverage.scope != self.scope:
            raise ValueError("coverage does not describe this scope")
        for item in coverage.kinds:
            found = [entry for entry in self.records
                     if entry.kind == item.kind]
            if (item.in_scope != len(found) or item.unnamed_in_scope != sum(
                    entry.name_status != "available" for entry in found)):
                raise ValueError("coverage counts disagree with the records")
        if self.scope.kind == "pre_target_history" and any(
                visit.role != "pre_target" for visit in self.visits):
            raise ValueError("history scope includes the final admission")
        if self.scope.kind == "selected_visit" and (
                len(self.visits) != 1
                or self.visits[0].visit_number != self.scope.visit_number):
            raise ValueError("selected-visit scope holds another visit")
        if self.scope.kind != "pre_target_history" and (
                sum(visit.role == "final_admission"
                    for visit in self.visits) > 1):
            raise ValueError("a bundle has one final admission at most")
        return self


def item_key(entry):
    """Return the key of what a record is of: its standard concept, or its
    code and name without one; its laboratory or ICU item; its
    microbiology test and specimen; its care unit. Each ICU stay is its
    own. A list names each such item once."""
    if entry.kind in OMOP_KINDS:
        if entry.concept_id:
            return (entry.kind, "concept", str(entry.concept_id))
        written = entry.dose.drug if entry.dose is not None else None
        named = entry.detail or entry.name or written
        if not named:
            # Without a concept, a name or the prescription's drug, the
            # records cannot be told apart: they are one item.
            return (entry.kind, "unnamed")
        return (entry.kind, "code", entry.code or "", named)
    if entry.kind in ("lab", "vital", "icu_procedure"):
        return (entry.kind, "item", entry.code or "")
    if entry.kind == "microbiology":
        return (entry.kind, "test", entry.name or "",
                entry.microbe.specimen or "")
    if entry.kind == "transfer":
        return (entry.kind, "unit", entry.name or "")
    return (entry.kind, "record", entry.ref)


def refs_of(bundle):
    """Return every bundle-local reference a response may cite."""
    refs = {bundle.coverage.ref, bundle.person.ref}
    refs.update(visit.ref for visit in bundle.visits)
    refs.update(entry.ref for entry in bundle.records)
    return refs


def finite(value):
    """Return a float when value is a finite number, else None."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def record_schemas():
    """Return the JSON Schema of the record bundle, by file name."""
    return {"record-bundle.schema.json": RecordBundle.model_json_schema()}
