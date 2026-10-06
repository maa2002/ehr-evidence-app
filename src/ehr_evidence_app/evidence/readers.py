"""Source snapshot and case readers (work item 25).

Reads the bounded source snapshot (visits, conditions, procedures, drugs,
concepts) and the admitted cases of the Phase 3 closure record, into plain
immutable records. Every file is pinned: the snapshot manifest and the
closure and identity-mapping records by SHA-256 in code, and each snapshot
file by the size and SHA-256 its manifest records. Each file is read once
as bounded bytes and parsed only from those bytes. Native identifiers stay
in memory; nothing is printed or written.

A case is bound to its person and visits through the identity mapping, and
the binding is checked against the closure record's digest of the mapping
record, so a case can never be read with another case's visits.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import json
import re

from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.gnn import admission
from ehr_evidence_app.gnn import mapping as idmap
from ehr_evidence_app.gnn import outputs as pmo
from ehr_evidence_app.gnn import provenance as amp

PILOT_LABEL = idmap.PILOT_LABEL
PILOT_DIR = idmap.PILOT_DIR
CLOSURE_SHA256 = ("169a9a613a685ca306bebbecdaa91d9e"
                  "eed4e330dea9c9380ad957f247abbccb")
CLOSURE_SIZE = 67263

DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
DATETIME = re.compile(
    r"^([0-9]{4}-[0-9]{2}-[0-9]{2})[ T]([0-9]{2}:[0-9]{2}(?::[0-9]{2})?)"
    r"(?:\.[0-9]+)?$")

# domain: (file, id column, concept column, start date, start datetime,
#          end date, end datetime, source value, source concept)
DOMAIN_COLUMNS = {
    "condition": ("conditions.csv", "condition_occurrence_id",
                  "condition_concept_id", "condition_start_date",
                  "condition_start_datetime", "condition_end_date",
                  "condition_end_datetime", "condition_source_value",
                  "condition_source_concept_id"),
    "procedure": ("procedures.csv", "procedure_occurrence_id",
                  "procedure_concept_id", "procedure_date",
                  "procedure_datetime", "procedure_end_date",
                  "procedure_end_datetime", "procedure_source_value",
                  "procedure_source_concept_id"),
    "drug": ("drugs.csv", "drug_exposure_id", "drug_concept_id",
             "drug_exposure_start_date", "drug_exposure_start_datetime",
             "drug_exposure_end_date", "drug_exposure_end_datetime",
             "drug_source_value", "drug_source_concept_id"),
}
VISIT_COLUMNS = ("visit_occurrence_id", "person_id", "visit_concept_id",
                 "visit_start_date", "visit_end_date")
CONCEPT_COLUMNS = ("concept_id", "concept_name", "domain_id",
                   "vocabulary_id", "concept_class_id", "standard_concept",
                   "concept_code")


class ReaderError(Exception):
    """A fail-closed read problem carrying a finding code only."""


@dataclasses.dataclass(frozen=True)
class SourceVisit:
    """One visit_occurrence row; source_value is its visit_source_value
    ("subject_id|hadm_id" for a MIMIC-IV admission) when the snapshot
    holds it."""

    visit_occurrence_id: int
    person_id: int
    visit_concept_id: int
    start_date: str | None
    end_date: str | None
    source_value: str | None = None


@dataclasses.dataclass(frozen=True)
class SourceEvent:
    """One condition, procedure or drug row, kept as recorded."""

    domain: str
    native_id: int
    person_id: int
    visit_occurrence_id: int | None
    concept_id: int
    start_date: str | None
    start_datetime: str | None
    end_date: str | None
    end_datetime: str | None
    source_value: str | None
    source_concept_id: int | None


@dataclasses.dataclass(frozen=True)
class ConceptRow:
    """One concept row of the snapshot's concept table."""

    concept_id: int
    name: str | None
    domain_id: str | None
    vocabulary_id: str | None
    code: str | None = None


@dataclasses.dataclass(frozen=True)
class Snapshot:
    """The verified source snapshot, parsed."""

    label: str
    manifest_sha256: str
    concepts_sha256: str
    complete: bool
    visits: dict
    events: dict
    concepts: dict


@dataclasses.dataclass(frozen=True)
class DriverTag:
    """The closure record's tag for one published driver."""

    position: int
    score_class: str
    attachment: str
    target_outcome_concept: bool
    narration_eligible: bool


@dataclasses.dataclass(frozen=True)
class Case:
    """One case of the closure record, bound to its person and visits."""

    case_number: int
    patient_index: int
    graph_position: int
    person_id: int
    subject_id: int
    visit_ids: tuple
    admitted: bool
    explanation_eligible: bool
    restrictions: tuple
    upstream_partition: str
    output_record_sha256: str
    batch_sensitive: bool
    single_graph_reproduces_published: bool
    label_correspondence: str
    drivers: dict


@dataclasses.dataclass(frozen=True)
class CaseSet:
    """The admitted cases of one closure record."""

    label: str
    closure_sha256: str
    mapping_sha256: str
    cases: tuple


def real_plan(case_set=None):
    """Return the pinned snapshot, closure and identity-mapping inputs.

    Without a case set this is the development pilot; with one, every
    input is the case set's pinned stage record.
    """
    if case_set is not None:
        return {
            "label": case_set,
            "snapshot_dir": registry.snapshot_dir(case_set),
            "manifest": registry.pinned(case_set, "snapshot_manifest"),
            "legacy_concepts": dict(pmo.real_plan()["assets"]["kg_concepts"]),
            "closure": registry.pinned(case_set, "prepared_closure"),
            "mapping": registry.pinned(case_set, "prepared_mapping"),
        }
    return {
        "label": PILOT_LABEL,
        "snapshot_dir": PILOT_DIR,
        "manifest": dict(idmap.real_plan()["pilot_manifest"]),
        "legacy_concepts": dict(pmo.real_plan()["assets"]["kg_concepts"]),
        "closure": {"path": admission.OUTPUT_DIR + "\\"
                            + admission.CLOSURE_NAME,
                    "size_bytes": CLOSURE_SIZE, "sha256": CLOSURE_SHA256},
        "mapping": dict(pmo.real_plan()["assets"]["step17_mapping"]),
    }


# Parsing Helpers

def read_pinned(entry, name, held):
    """Read one pinned file once and verify size and SHA-256."""
    try:
        data, signature = idmap.read_verified(
            entry["path"], name, "input_hash_mismatch:%s" % name,
            sha256=entry["sha256"], size=entry.get("size_bytes"))
    except idmap.MappingError as error:
        raise ReaderError("input_identity:%s" % error) from None
    held.append((entry["path"], signature, name))
    return data


def parse_json(data, name):
    """Parse JSON from verified bytes."""
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ReaderError("input_not_json:%s" % name) from None


def csv_rows(data, name, columns):
    """Parse CSV rows from verified bytes; the columns must be present."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ReaderError("input_not_utf8:%s" % name) from None
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames is None or not set(columns) <= set(
            reader.fieldnames):
        raise ReaderError("input_columns_missing:%s" % name)
    return list(reader)


def required_int(value, name):
    """Return a strict decimal integer or fail."""
    number = idmap.canonical_int(value)
    if number is None:
        raise ReaderError("value_not_integer:%s" % name)
    return number


def optional_int(value, name):
    """Return None for an empty field, else a strict decimal integer."""
    return None if value in ("", None) else required_int(value, name)


def optional_text(value):
    """Return None for an empty field, else the stripped text."""
    text = (value or "").strip()
    return text or None


def optional_date(value, name):
    """Return an ISO date, None when empty; fail on other forms."""
    text = optional_text(value)
    if text is not None and not DATE.match(text):
        raise ReaderError("value_not_date:%s" % name)
    return text


def optional_datetime(value, name):
    """Return 'YYYY-MM-DD HH:MM[:SS]', None when empty; fail otherwise."""
    text = optional_text(value)
    if text is None:
        return None
    match = DATETIME.match(text)
    if not match:
        raise ReaderError("value_not_datetime:%s" % name)
    return "%s %s" % match.groups()


# Snapshot

def manifest_entries(manifest, plan):
    """Return the manifest's file records after checking the run state."""
    if (not isinstance(manifest, dict) or manifest.get("status") != "complete"
            or manifest.get("run_state") != "finished"
            or manifest.get("promoted") is not True
            or manifest.get("version_label") != plan["label"]):
        raise ReaderError("snapshot_not_complete")
    entries = {}
    for entry in manifest.get("files") or []:
        if isinstance(entry, dict) and isinstance(entry.get("file"), str):
            entries[entry["file"]] = entry
    return entries


def file_entry(entries, plan, name):
    """Return the pinned entry of one snapshot file from its manifest."""
    entry = entries.get(name)
    if (not isinstance(entry, dict) or entry.get("present") is not True
            or not isinstance(entry.get("sha256"), str)
            or not pmo.strict_int(entry.get("size_bytes"))):
        raise ReaderError("snapshot_file_unrecorded:%s" % name)
    return {"path": plan["snapshot_dir"] + "\\" + name,
            "size_bytes": entry["size_bytes"],
            "sha256": entry["sha256"].lower()}


def parse_visits(rows):
    """Return visits by visit_occurrence_id."""
    visits = {}
    for row in rows:
        visit = SourceVisit(
            visit_occurrence_id=required_int(row["visit_occurrence_id"],
                                             "visit_occurrence_id"),
            person_id=required_int(row["person_id"], "person_id"),
            visit_concept_id=required_int(row["visit_concept_id"],
                                          "visit_concept_id"),
            start_date=optional_date(row["visit_start_date"],
                                     "visit_start_date"),
            end_date=optional_date(row["visit_end_date"], "visit_end_date"),
            source_value=optional_text(row.get("visit_source_value")))
        if visit.visit_occurrence_id in visits:
            raise ReaderError("duplicate_native_key:visit")
        visits[visit.visit_occurrence_id] = visit
    return visits


def parse_events(domain, rows):
    """Return one domain's events in file order; native keys are unique."""
    (_, key, concept, start, start_time, end, end_time, source,
     source_concept) = DOMAIN_COLUMNS[domain]
    events, seen = [], set()
    for row in rows:
        event = SourceEvent(
            domain=domain,
            native_id=required_int(row[key], key),
            person_id=required_int(row["person_id"], "person_id"),
            visit_occurrence_id=optional_int(row["visit_occurrence_id"],
                                             "visit_occurrence_id"),
            concept_id=required_int(row[concept], concept),
            start_date=optional_date(row[start], start),
            start_datetime=optional_datetime(row[start_time], start_time),
            end_date=optional_date(row[end], end),
            end_datetime=optional_datetime(row[end_time], end_time),
            source_value=optional_text(row[source]),
            source_concept_id=optional_int(row[source_concept],
                                           source_concept))
        if event.native_id in seen:
            raise ReaderError("duplicate_native_key:%s" % domain)
        seen.add(event.native_id)
        events.append(event)
    return tuple(events)


def parse_concepts(rows):
    """Return concept rows by concept_id."""
    concepts = {}
    for row in rows:
        concept_id = required_int(row["concept_id"], "concept_id")
        if concept_id in concepts:
            raise ReaderError("duplicate_native_key:concept")
        concepts[concept_id] = ConceptRow(
            concept_id=concept_id, name=optional_text(row["concept_name"]),
            domain_id=optional_text(row["domain_id"]),
            vocabulary_id=optional_text(row["vocabulary_id"]),
            code=optional_text(row.get("concept_code")))
    return concepts


def read_snapshot(plan=None):
    """Verify and parse the source snapshot; return a Snapshot."""
    plan = plan or real_plan()
    held = []
    manifest_bytes = read_pinned(plan["manifest"], "snapshot_manifest", held)
    entries = manifest_entries(parse_json(manifest_bytes,
                                          "snapshot_manifest"), plan)
    raw = {}
    for name in ["visits.csv", "concepts.csv"] + [
            columns[0] for columns in DOMAIN_COLUMNS.values()]:
        raw[name] = read_pinned(file_entry(entries, plan, name), name, held)
    try:
        idmap.confirm_unchanged(held)
    except idmap.MappingError as error:
        raise ReaderError("input_identity:%s" % error) from None
    visits = parse_visits(csv_rows(raw["visits.csv"], "visits.csv",
                                   VISIT_COLUMNS))
    events = {}
    for domain, columns in DOMAIN_COLUMNS.items():
        events[domain] = parse_events(domain, csv_rows(
            raw[columns[0]], columns[0], columns[1:] + ("person_id",
                                                        "visit_occurrence_id")))
    concepts = parse_concepts(csv_rows(raw["concepts.csv"], "concepts.csv",
                                       CONCEPT_COLUMNS))
    return Snapshot(
        label=plan["label"],
        manifest_sha256=plan["manifest"]["sha256"].lower(),
        concepts_sha256=file_entry(entries, plan, "concepts.csv")["sha256"],
        complete=True, visits=visits, events=events, concepts=concepts)


def read_legacy_concepts(plan=None):
    """Verify and parse the legacy concept map; return (rows, SHA-256)."""
    plan = plan or real_plan()
    held = []
    data = read_pinned(plan["legacy_concepts"], "legacy_concepts", held)
    rows = parse_concepts(csv_rows(data, "legacy_concepts",
                                   ("concept_id", "concept_name",
                                    "domain_id", "vocabulary_id")))
    return rows, plan["legacy_concepts"]["sha256"].lower()


# Cases

def driver_tags(domains):
    """Return the closure's driver tags by domain."""
    tags = {}
    for domain in vocab.DOMAINS:
        entries = []
        for item in (domains or {}).get(domain) or []:
            if (not isinstance(item, dict)
                    or item.get("score_class") not in vocab.SCORE_CLASSES
                    or item.get("attachment") not in vocab.ATTACHMENTS):
                raise ReaderError("closure_driver_tag_invalid")
            entries.append(DriverTag(
                position=item["position"], score_class=item["score_class"],
                attachment=item["attachment"],
                target_outcome_concept=item["target_outcome_concept"],
                narration_eligible=item["narration_eligible"]))
        if [entry.position for entry in entries] != list(range(len(entries))):
            raise ReaderError("closure_driver_tag_invalid")
        tags[domain] = tuple(entries)
    return tags


def visit_ids(record):
    """Return a mapping record's visit ids in graph order (0..N-1)."""
    nodes = record.get("visit_nodes") or []
    positions = [node.get("node_position") for node in nodes]
    if positions != list(range(len(nodes))) or not nodes:
        raise ReaderError("mapping_visit_nodes_invalid")
    ids = tuple(node.get("visit_occurrence_id") for node in nodes)
    if not all(pmo.strict_int(value, 1) for value in ids):
        raise ReaderError("mapping_visit_nodes_invalid")
    if len(set(ids)) != len(ids):
        raise ReaderError("mapping_visit_repeated")
    return ids


def index_records(records):
    """Return mapping records by the digest the closure binds them with."""
    return {amp.digest(record): record for record in records or []
            if isinstance(record, dict)}


def closure_case(item, records):
    """Return one Case after checking it against the identity mapping.

    Every case is bound to its mapping record by digest. Only an admitted
    case must also resolve to its graph position and verified visits; a
    quarantined case keeps no visits and is never retrieved.
    """
    admission_block = item.get("admission") or {}
    admitted = admission_block.get("status") == admission.ADMITTED
    record = records.get(item.get("mapping_record_sha256"))
    if record is None:
        raise ReaderError("closure_mapping_mismatch")
    if admitted and (record.get("patient_index") != item.get("patient_index")
                     or record.get("graph_position")
                     != item.get("graph_position")):
        raise ReaderError("closure_mapping_mismatch")
    prediction = item.get("prediction") or {}
    label = item.get("label_evidence") or {}
    restrictions = tuple(admission_block.get("restrictions") or ())
    if any(code not in vocab.RESTRICTIONS for code in restrictions):
        raise ReaderError("closure_restriction_unknown")
    if item.get("upstream_partition") not in vocab.PARTITIONS:
        raise ReaderError("closure_partition_invalid")
    return Case(
        case_number=item["case_position"] + 1,
        patient_index=item["patient_index"],
        graph_position=item["graph_position"],
        person_id=record["person_id"],
        subject_id=record["subject_id"],
        visit_ids=visit_ids(record) if admitted else (),
        admitted=admitted,
        explanation_eligible=item.get("explanation_eligible") is True,
        restrictions=restrictions,
        upstream_partition=item["upstream_partition"],
        output_record_sha256=item["output_record_sha256"],
        batch_sensitive=prediction.get("batch_sensitive") is True,
        single_graph_reproduces_published=prediction.get(
            "single_graph_reproduces_published") is True,
        label_correspondence=label.get("correspondence"),
        drivers=driver_tags(item.get("drivers")))


def read_cases(plan=None):
    """Verify and parse the closure record's cases; return a CaseSet."""
    plan = plan or real_plan()
    held = []
    closure = parse_json(read_pinned(plan["closure"], "closure", held),
                         "closure")
    mapping_document = parse_json(read_pinned(plan["mapping"], "mapping",
                                              held), "mapping")
    try:
        idmap.confirm_unchanged(held)
    except idmap.MappingError as error:
        raise ReaderError("input_identity:%s" % error) from None
    if (not isinstance(closure, dict)
            or closure.get("schema") not in (admission.SCHEMA,
                                             registry.CLOSURE_SCHEMA)
            or closure.get("status") != "closed"
            or (closure["schema"] == registry.CLOSURE_SCHEMA
                and closure.get("case_set") != plan["label"])):
        raise ReaderError("closure_unexpected")
    body = {key: value for key, value in closure.items()
            if key != "preparation"}
    if (closure.get("preparation") or {}).get(
            "fingerprint_sha256") != amp.digest(body):
        raise ReaderError("closure_fingerprint_mismatch")
    records = index_records(mapping_document.get("records"))
    items = closure.get("cases") or []
    if [item.get("case_position") for item in items] != list(
            range(len(items))):
        raise ReaderError("closure_cases_unordered")
    cases = tuple(closure_case(item, records) for item in items)
    return CaseSet(label=plan["label"],
                   closure_sha256=plan["closure"]["sha256"].lower(),
                   mapping_sha256=plan["mapping"]["sha256"].lower(),
                   cases=cases)
