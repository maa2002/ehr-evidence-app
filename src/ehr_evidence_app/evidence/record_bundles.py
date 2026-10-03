#!/usr/bin/env python3
"""Record bundles of a case set: the whole record, scope by scope.

For every admitted case, one record bundle per scope (the pre-target
history, each case visit, all case visits) holding every record of the
scope's visits (evidence.record): the conditions, procedures and
medications of the pinned OMOP source snapshot, and the laboratory
results, microbiology, vital signs, transfers, ICU stays and ICU
procedure events of the pinned raw extract (sources.mimic), with the
person and each visit's admission.

A raw row belongs to a case visit when its admission is the visit's own:
the snapshot records each visit's MIMIC-IV admission in its source value
("subject_id|hadm_id"); two visits of one admission stop the build. Rows
of the person that no case visit holds (outpatient results, other
admissions) are counted in the coverage and never delivered.

A medication's ingredients and ATC classes come from the pinned drug
ancestors (qa.record_vocabulary). Its dose and route come from the raw
prescription it was made from, found by the source's own keys (the
admission, the start and stop times and the drug's code or name and
strength); they are attached only when every prescription that fits the
keys agrees, so no dose is ever chosen between two.

The bundle set is published once, as a new versioned directory:

  record/<bundle id>.json  patient-bearing;
  manifest.json            bundle identifiers, digests and sizes;
  summary.json             aggregate counts only.

Usage:
    python -m ehr_evidence_app.evidence.record_bundles
    python -m ehr_evidence_app.evidence.record_bundles --operator-real \\
        --case-set LABEL
"""

from __future__ import annotations

import argparse
import collections
import csv
import dataclasses
import hashlib
import io
import json
import os
import sys

from pydantic import ValidationError

import ehr_evidence_app
from ehr_evidence_app.evidence import concepts as names
from ehr_evidence_app.evidence import contracts, record, retrieval
from ehr_evidence_app.evidence.schema import DigestRef
from ehr_evidence_app.sources import mimic

SCHEMA = "record-bundle-set"
SUMMARY_SCHEMA = "record-bundle-set-summary"
SCHEMA_VERSION = "1.0"
SET_VERSION = "records-v1"
MANIFEST_NAME = "manifest.json"
SUMMARY_NAME = "summary.json"
FOLDER = "record"
PARTIAL = ".partial"
BUILDER = "ehr_evidence_app.evidence.record_bundles %s" % (
    ehr_evidence_app.__version__)
# A record bundle is read by the templates, never by the model; its own
# ceilings bound what one bundle may hold.
MAX_RECORDS = 60000
MAX_BUNDLE_BYTES = 48 * 1024 * 1024
DOMAIN_KINDS = {"condition": "condition", "procedure": "procedure",
                "drug": "medication"}
LAB_ITEMS = "MIMIC-IV laboratory item"
ICU_ITEMS = "MIMIC-IV ICU item"
MICRO_TESTS = "MIMIC-IV microbiology test"
DOSE_FIELDS = (("drug", "drug"), ("amount", "dose_val_rx"),
               ("unit", "dose_unit_rx"), ("route", "route"),
               ("form", "form_rx"), ("strength", "prod_strength"),
               ("drug_type", "drug_type"))


# (size in bytes, SHA-256) of each case set's published record bundle
# manifest, pinned after its build.
PINS = {
    "pilot-10-v1": (22038, "cb45941da049f2379d3335bff6cdae27"
                           "faa4ce93a3fcebab0d70d05abc1fe2dd"),
    "dev-30-v1": (48361, "0b9a276d1b4b153b90d4f10c13b713be"
                         "f8ae430c570bc52e727b74fc00be9c47"),
    "eval-100-v1": (179488, "ca0ca65500bd6417d600eb09897bba96"
                            "36c2e70e66b4e7c51834e9b77b47fe2c"),
}


class RecordBundleError(Exception):
    """A fail-closed build problem carrying a finding code only."""


def pinned(case_set):
    """Return the plan of a case set's pinned record bundle set: its
    label, directory and manifest pin."""
    pin = PINS.get(case_set)
    if pin is None:
        raise RecordBundleError("record_bundles_not_pinned:%s" % case_set)
    directory = output_dir(case_set)
    return {"label": case_set, "directory": directory,
            "manifest": {"path": directory + "\\" + MANIFEST_NAME,
                         "size_bytes": pin[0], "sha256": pin[1]}}


# --------------------------------------------------------------------------
# The raw extract
# --------------------------------------------------------------------------

def text(value):
    """Return a field's stripped text, or None when it is empty."""
    value = (value or "").strip()
    return value or None


def short(value):
    """Return text within a record's length limit, or None."""
    return names.clip(text(value))


def integer(value):
    """Return a strict decimal integer of ASCII digits, or None."""
    value = text(value)
    return int(value) if value is not None and value.isascii() and (
        value.isdigit()) else None


def time_of(value):
    """Return a raw time as 'YYYY-MM-DD HH:MM:SS' or a date; None when
    empty."""
    value = text(value)
    if value is None:
        return None
    if len(value) >= 19:
        return value[:19]
    return value


def extract_rows(files):
    """Return the raw extract's tables as lists of rows by table name;
    files maps each extract file's name to its verified bytes."""
    tables = {}
    for name, data in files.items():
        if not name.endswith(".csv"):
            continue
        try:
            reader = csv.DictReader(io.StringIO(data.decode("utf-8"),
                                                newline=""))
            tables[name[:-4]] = list(reader)
        except (UnicodeDecodeError, csv.Error):
            raise RecordBundleError("extract_unreadable:%s" % name) from None
    return tables


class Extract:
    """The raw rows of one case set, indexed by person and admission."""

    def __init__(self, tables):
        """Index the tables of a raw extract."""
        self.labs = {row["itemid"]: row for row in tables["d_labitems"]}
        self.items = {row["itemid"]: row for row in tables["d_items"]}
        self.patients = {row["subject_id"]: row
                         for row in tables["patients"]}
        self.admissions = {row["hadm_id"]: row
                           for row in tables["admissions"]}
        self.by_person = collections.defaultdict(
            lambda: collections.defaultdict(list))
        for name in ("transfers", "icustays", "labevents",
                     "microbiologyevents", "chartevents", "procedureevents",
                     "prescriptions"):
            for row in tables[name]:
                self.by_person[row["subject_id"]][name].append(row)

    def rows(self, subject, table):
        """Return a person's rows of a table."""
        return self.by_person.get(subject, {}).get(table, [])


# The vital-sign group of each ICU item of the list.
VITAL_OF = {str(item): group for group, items in mimic.VITAL_GROUPS.items()
            for item in items}


def measure(value, number, unit, low=None, high=None, flag=None,
            priority=None):
    """Return a Measure, or None when nothing was recorded as a value."""
    value, number = short(value), record.finite(number)
    if value is None and number is None:
        return None
    low, high = record.finite(low), record.finite(high)
    if low is not None and high is not None and low > high:
        low = high = None
    return record.Measure(
        text=value, number=number, unit=short(unit), low=low, high=high,
        flag="abnormal" if text(flag) == "abnormal" else None,
        priority=short(priority))


def named(name):
    """Return the name fields of a record whose name may be missing."""
    name = short(name)
    return {"name": name,
            "name_status": "available" if name else "unavailable"}


def raw_entries(extract, subject, visit_of):
    """Return (entries by visit id, rows outside case visits by kind) of a
    person's raw rows; visit_of maps an admission to a case visit id.
    Entries are (kind, native id, visit id, fields)."""
    found = collections.defaultdict(list)
    outside = collections.Counter()

    def keep(kind, native_id, admission, fields):
        """Keep one entry, or count it when no case visit holds it."""
        visit = visit_of.get(text(admission))
        if visit is None:
            outside[kind] += 1
            return
        found[visit].append((kind, native_id, fields))

    for row in extract.rows(subject, "labevents"):
        item = extract.labs.get(row["itemid"], {})
        detail = " · ".join(part for part in (text(item.get("fluid")),
                                              text(item.get("category")))
                            if part)
        keep("lab", integer(row["labevent_id"]), row["hadm_id"], dict(
            named(item.get("label")), code=short(row["itemid"]),
            vocabulary=LAB_ITEMS, detail=short(detail),
            start=time_of(row["charttime"]),
            measure=measure(row["value"], row["valuenum"], row["valueuom"],
                            row["ref_range_lower"], row["ref_range_upper"],
                            row["flag"], row["priority"])))
    for row in extract.rows(subject, "microbiologyevents"):
        keep("microbiology", integer(row["microevent_id"]), row["hadm_id"],
             dict(named(row["test_name"] or row["spec_type_desc"]),
                  code=short(row["test_itemid"]), vocabulary=MICRO_TESTS,
                  start=time_of(row["charttime"] or row["chartdate"]),
                  microbe=record.Microbe(
                      specimen=short(row["spec_type_desc"]),
                      test=short(row["test_name"]),
                      organism=short(row["org_name"]),
                      antibiotic=short(row["ab_name"])
                      if text(row["org_name"]) else None,
                      interpretation=text(row["interpretation"])
                      if text(row["interpretation"])
                      in record.INTERPRETATIONS
                      and text(row["ab_name"])
                      and text(row["org_name"]) else None,
                      dilution=short(row["dilution_text"]))))
    for row in extract.rows(subject, "chartevents"):
        item = extract.items.get(row["itemid"], {})
        keep("vital", integer(row[_ROW]), row["hadm_id"], dict(
            named(item.get("label")), code=short(row["itemid"]),
            vocabulary=ICU_ITEMS, group=VITAL_OF.get(row["itemid"]),
            start=time_of(row["charttime"]),
            measure=measure(row["value"], row["valuenum"],
                            row["valueuom"])))
    for row in extract.rows(subject, "procedureevents"):
        item = extract.items.get(row["itemid"], {})
        keep("icu_procedure", integer(row[_ROW]), row["hadm_id"], dict(
            named(item.get("label")), code=short(row["itemid"]),
            vocabulary=ICU_ITEMS, detail=short(row["ordercategoryname"]),
            start=time_of(row["starttime"]), end=time_of(row["endtime"]),
            measure=measure(row["value"], row["value"], row["valueuom"])))
    for row in extract.rows(subject, "transfers"):
        keep("transfer", integer(row["transfer_id"]), row["hadm_id"], dict(
            named(row["careunit"]), start=time_of(row["intime"]),
            end=time_of(row["outtime"]),
            stay=record.Stay(event=short(row["eventtype"]),
                             unit=short(row["careunit"]))))
    for row in extract.rows(subject, "icustays"):
        keep("icu_stay", integer(row["stay_id"]), row["hadm_id"], dict(
            named(row["first_careunit"]), start=time_of(row["intime"]),
            end=time_of(row["outtime"]),
            stay=record.Stay(unit=short(row["first_careunit"]),
                             last_unit=short(row["last_careunit"]),
                             days=record.finite(row["los"]))))
    return found, outside


_ROW = "row_number"


# --------------------------------------------------------------------------
# Medications: ingredients and the prescription's dose
# --------------------------------------------------------------------------

def full_time(value):
    """Return a time as 'YYYY-MM-DD HH:MM:SS', or None."""
    if value is None:
        return None
    return value + ":00" if len(value) == 16 else value


def prescription_key(row):
    """Return the keys the OMOP drug record of a raw prescription carries:
    its start and end time and its code (the NDC, else the drug's name
    and strength), as the source ETL builds them."""
    start = time_of(row["starttime"])
    if start is None or not row["drug"]:
        return None
    end = time_of(row["stoptime"]) or start
    code = row["ndc"] if row["ndc"] else (
        row["drug"] + " " + (row["prod_strength"] or "")).strip(" ")
    return full_time(start), full_time(end), code.strip()


def doses_of(extract, subject, admissions):
    """Return {(admission, keys): Dose or None} for a person's
    prescriptions: None when prescriptions that share the keys disagree."""
    found = {}
    for row in extract.rows(subject, "prescriptions"):
        admission = text(row["hadm_id"])
        keys = prescription_key(row)
        if admission not in admissions or keys is None:
            continue
        dose = record.Dose(**{field: short(row[column])
                              for field, column in DOSE_FIELDS})
        key = (admission, keys)
        if key in found and found[key] != dose:
            found[key] = None
        else:
            found.setdefault(key, dose)
    return found


def drug_key(event):
    """Return the keys an OMOP drug record's prescription is found by."""
    if event.start_datetime is None:
        return None
    return (full_time(event.start_datetime),
            full_time(event.end_datetime or event.start_datetime),
            event.source_value or "")


# --------------------------------------------------------------------------
# Bundles
# --------------------------------------------------------------------------

def scope_slug(scope):
    """Return the bundle-identifier suffix for a scope."""
    if scope.kind == "pre_target_history":
        return "history"
    if scope.kind == "selected_visit":
        return "visit-%d" % scope.visit_number
    return "all"


def admission_of(extract, admission):
    """Return the Admission of a raw admission, or None."""
    row = extract.admissions.get(admission)
    if row is None:
        return None
    death = text(row["hospital_expire_flag"]) == "1"
    return record.Admission(
        native_id=integer(row[_ROW]),
        admission_type=short(row["admission_type"]),
        admission_location=short(row["admission_location"]),
        discharge_location=short(row["discharge_location"]),
        admit_time=time_of(row["admittime"]),
        discharge_time=time_of(row["dischtime"]),
        death_time=time_of(row["deathtime"]) if death else None,
        ed_registration=time_of(row["edregtime"]),
        ed_out=time_of(row["edouttime"]), hospital_death=death,
        insurance=short(row["insurance"]), language=short(row["language"]),
        marital_status=short(row["marital_status"]),
        race=short(row["race"]))


def person_of(extract, subject):
    """Return the Person of a case's subject."""
    row = extract.patients.get(subject)
    if row is None:
        raise RecordBundleError("person_missing")
    sex = text(row["gender"])
    return record.Person(
        native_id=integer(row[_ROW]), sex=sex if sex in ("F", "M") else None,
        anchor_age=integer(row["anchor_age"]),
        anchor_year=integer(row["anchor_year"]),
        anchor_year_group=short(row["anchor_year_group"]),
        date_of_death=text(row["dod"])[:10] if text(row["dod"]) else None)


def omop_entry(event, concepts, ancestors, dose):
    """Return the fields of an OMOP record."""
    concept = names.concept(event.concept_id, concepts, event.source_value)
    source = names.source_concept(event.source_concept_id, concepts)
    fields = {"concept_id": event.concept_id,
              "name_status": concept.name_status, "name": concept.name,
              "code": source.code if source else names.clip(
                  event.source_value),
              "vocabulary": source.vocabulary_id if source else None,
              "detail": source.name if source and source.name else None,
              "start": event.start_datetime or event.start_date,
              "end": event.end_datetime or event.end_date}
    if event.domain == "drug":
        fields["ancestors"] = tuple(sorted(ancestors.get(event.concept_id,
                                                         ())))
        fields["dose"] = dose
    return fields


def case_records(case, snapshot, extract, ancestors, counts):
    """Return (visits by position, entries by position, outside counts by
    kind, person) for one admitted case."""
    positions = {visit_id: position
                 for position, visit_id in enumerate(case.visit_ids)}
    subject = str(case.subject_id)
    visit_of, admission_of_visit = {}, {}
    for visit_id in case.visit_ids:
        source = snapshot.visits.get(visit_id)
        if source is None or source.person_id != case.person_id:
            raise RecordBundleError("case_visit_missing")
        person, _, admission = (source.source_value or "").partition("|")
        if person == subject and admission:
            if admission in visit_of:
                # Two visits of one admission: its rows would belong to
                # either.
                raise RecordBundleError("visit_admission_repeated")
            visit_of[admission] = visit_id
            admission_of_visit[visit_id] = admission
    raw, outside = raw_entries(extract, subject, visit_of)
    doses = doses_of(extract, subject, set(visit_of))
    entries = collections.defaultdict(list)
    for visit_id, items in raw.items():
        entries[positions[visit_id]].extend(items)
    for domain, kind in DOMAIN_KINDS.items():
        for event in snapshot.events[domain]:
            if event.person_id != case.person_id:
                continue
            if event.visit_occurrence_id not in positions:
                outside[kind] += 1
                continue
            dose = None
            if domain == "drug":
                keys = drug_key(event)
                admission = admission_of_visit.get(event.visit_occurrence_id)
                found = doses.get((admission, keys)) if keys else None
                dose = found
                counts["dose_linked" if found is not None
                       else "dose_not_linked"] += 1
            entries[positions[event.visit_occurrence_id]].append(
                (kind, event.native_id,
                 omop_entry(event, snapshot.concepts, ancestors, dose)))
    visits = {}
    for visit_id, position in positions.items():
        source = snapshot.visits[visit_id]
        concept = names.concept(source.visit_concept_id, snapshot.concepts)
        visits[position] = record.RecordVisit(
            ref="V%d" % (position + 1), position=position,
            visit_number=position + 1,
            role=("final_admission" if position == len(case.visit_ids) - 1
                  else "pre_target"),
            visit_concept_id=source.visit_concept_id,
            visit_type=concept.name, start_date=source.start_date,
            end_date=source.end_date,
            admission=admission_of(extract,
                                   admission_of_visit.get(visit_id)))
        counts["visits_with_admission"] += visits[position].admission \
            is not None
    return visits, entries, outside, person_of(extract, subject)


def sort_key(item):
    """Order a scope's entries by visit, kind, start and native id."""
    position, (kind, native_id, fields) = item
    return (position, record.KINDS.index(kind), fields.get("start") or "",
            native_id)


def record_bundle(case_set, case, scope, visits, entries, outside, person,
                  provenance, labels):
    """Return one scope's RecordBundle."""
    chosen = retrieval.scope_positions(case, scope)
    final = len(case.visit_ids) - 1
    items = sorted(((position, item) for position in chosen
                    for item in entries.get(position, ())), key=sort_key)
    records = tuple(
        record.Entry(ref="R%d" % (index + 1), kind=kind,
                     source=record.SOURCES[kind], native_id=native_id,
                     visit_ref="V%d" % (position + 1), **fields)
        for index, (position, (kind, native_id, fields))
        in enumerate(items))
    kinds = []
    for kind in record.KINDS:
        found = [entry for entry in records if entry.kind == kind]
        excluded = (sum(1 for item in entries.get(final, ())
                        if item[0] == kind)
                    if scope.kind == "pre_target_history" else 0)
        kinds.append(record.KindCoverage(
            kind=kind, status="complete" if found else "complete_empty",
            in_scope=len(found),
            unnamed_in_scope=sum(entry.name_status != "available"
                                 for entry in found),
            excluded_final_admission=excluded,
            outside_case_visits=outside.get(kind, 0)))
    return record.RecordBundle(
        bundle_id="rec-c%02d-%s" % (case.case_number, scope_slug(scope)),
        case=contracts.CaseRef(case_set=case_set,
                               case_number=case.case_number),
        scope=scope, person=person,
        visits=tuple(visits[position] for position in chosen),
        records=records,
        coverage=record.RecordCoverage(
            scope=scope, kinds=tuple(kinds),
            source_snapshot=labels["snapshot"],
            record_extract=labels["extract"]),
        provenance=provenance)


def scopes_of(case):
    """Return a case's scopes: history, each visit, all visits."""
    return ([retrieval.scope("pre_target_history")]
            + [retrieval.scope("selected_visit", number)
               for number in range(1, len(case.visit_ids) + 1)]
            + [retrieval.scope("case_visits")])


def encode(document):
    """Return deterministic JSON bytes of a bundle or record."""
    return (json.dumps(document, indent=2, sort_keys=True, allow_nan=False)
            + "\n").encode("utf-8")


def bundle_bytes(bundle):
    """Return the deterministic bytes of one validated bundle."""
    return encode(bundle.model_dump(mode="json"))


def build_set(case_set, snapshot, extract, ancestors, provenance, labels):
    """Return (bundles, counts) for the admitted cases of a case set."""
    cases = [case for case in case_set.cases if case.admitted]
    if not cases:
        raise RecordBundleError("no_admitted_cases")
    bundles, counts = [], collections.Counter()
    try:
        for case in cases:
            visits, entries, outside, person = case_records(
                case, snapshot, extract, ancestors, counts)
            for scope in scopes_of(case):
                bundles.append(record_bundle(case_set.label, case, scope,
                                             visits, entries, outside,
                                             person, provenance, labels))
    except ValidationError as error:
        raise RecordBundleError("record_invalid:%s" % error.title) from None
    except retrieval.ScopeError:
        raise RecordBundleError("case_visit_missing") from None
    for bundle in bundles:
        if (len(bundle.records) > MAX_RECORDS
                or len(bundle_bytes(bundle)) > MAX_BUNDLE_BYTES):
            raise RecordBundleError("bundle_limit_exceeded")
    return bundles, summarise(bundles, counts)


def summarise(bundles, counts):
    """Return aggregate counts over a record bundle set."""
    every = [bundle for bundle in bundles
             if bundle.scope.kind == "case_visits"]
    kinds = {kind: sum(bundle.coverage.of(kind).in_scope for bundle in every)
             for kind in record.KINDS}
    return {
        "record_bundles": len(bundles),
        "cases": len(every),
        "records_in_case_visits": kinds,
        "records_outside_case_visits": {
            kind: sum(bundle.coverage.of(kind).outside_case_visits
                      for bundle in every) for kind in record.KINDS},
        "unnamed_records_in_case_visits": {
            kind: sum(bundle.coverage.of(kind).unnamed_in_scope
                      for bundle in every) for kind in record.KINDS},
        "visits": sum(len(bundle.visits) for bundle in every),
        "visits_with_admission": counts["visits_with_admission"],
        "medications_with_dose": counts["dose_linked"],
        "medications_without_dose": counts["dose_not_linked"],
        "medications_with_ingredients": sum(
            1 for bundle in every for entry in bundle.records
            if entry.kind == "medication" and entry.ancestors),
        "largest_bundle_records": max(len(bundle.records)
                                      for bundle in bundles),
        "largest_bundle_bytes": max(len(bundle_bytes(bundle))
                                    for bundle in bundles),
    }


def manifest_document(bundles, provenance, counts, version):
    """Return the manifest of a record bundle set (no native
    identifiers)."""
    files = []
    for bundle in bundles:
        data = bundle_bytes(bundle)
        files.append({"kind": "record", "bundle_id": bundle.bundle_id,
                      "path": "%s/%s.json" % (FOLDER, bundle.bundle_id),
                      "case_number": bundle.case.case_number,
                      "scope": bundle.scope.model_dump(mode="json"),
                      "size_bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest()})
    return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
            "version": version, "builder": BUILDER,
            "patient_bearing": False,
            "case_set": bundles[0].case.case_set if bundles else None,
            "provenance": provenance.model_dump(mode="json"),
            "counts": counts, "files": files}


def summary_document(manifest):
    """Return the aggregate summary of a manifest."""
    return {"schema": SUMMARY_SCHEMA, "schema_version": SCHEMA_VERSION,
            "version": manifest["version"], "counts": manifest["counts"],
            "manifest_sha256": hashlib.sha256(
                encode(manifest)).hexdigest()}


# --------------------------------------------------------------------------
# The real run
# --------------------------------------------------------------------------

def real_plan(case_set):
    """Return the pinned inputs and the fixed protected output of a case
    set's record bundles."""
    from ehr_evidence_app.evidence import readers
    from ehr_evidence_app.qa import record_vocabulary
    from ehr_evidence_app.sources import mimic
    return {
        "label": case_set,
        "readers": readers.real_plan(None if case_set == readers.PILOT_LABEL
                                     else case_set),
        "extract": mimic.pinned_manifest(case_set),
        "extract_dir": mimic.OUTPUT_ROOT + "\\" + case_set,
        "vocabulary": record_vocabulary.pinned(),
        "output_dir": output_dir(case_set),
        "protected_root": mimic.PROTECTED_ROOT,
    }


def output_dir(case_set):
    """Return the protected directory of a case set's record bundles."""
    from ehr_evidence_app.sources import mimic
    return (mimic.PROTECTED_ROOT + "\\evidence-bundles\\" + case_set + "-"
            + SET_VERSION)


def read_extract(plan, held):
    """Return (tables, manifest SHA-256) of the pinned raw extract, every
    file verified against its manifest."""
    from ehr_evidence_app.gnn import mapping
    try:
        data, signature = mapping.read_verified(
            plan["extract"]["path"], "record_extract",
            "input_hash_mismatch:record_extract",
            sha256=plan["extract"]["sha256"],
            size=plan["extract"]["size_bytes"])
    except mapping.MappingError as error:
        raise RecordBundleError(str(error)) from None
    held.append((plan["extract"]["path"], signature, "record_extract"))
    manifest = json.loads(data.decode("utf-8"))
    if manifest.get("status") != "complete" or (
            manifest.get("version_label") != plan["label"]):
        raise RecordBundleError("extract_not_complete")
    files = {}
    for entry in manifest["files"]:
        path = plan["extract_dir"] + "\\" + entry["file"]
        try:
            content = read_large(path, entry["size_bytes"], entry["sha256"])
        except OSError:
            raise RecordBundleError("extract_unreadable") from None
        files[entry["file"]] = content
    return extract_rows(files), plan["extract"]["sha256"]


def read_large(path, size, sha256):
    """Read one file and verify its size and SHA-256."""
    with open(path, "rb") as handle:
        data = handle.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        raise RecordBundleError("extract_hash_mismatch")
    return data


def run_real(plan):
    """Operator-only real run: build and publish one case set's record
    bundles; return (summary, digests)."""
    from ehr_evidence_app.evidence import readers
    from ehr_evidence_app.gnn import mapping
    from ehr_evidence_app.qa import record_vocabulary
    from ehr_evidence_app.storage import policy as guard
    if guard.fixed_policy_problem():
        raise RecordBundleError("fixed_policy_rejected")
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        if os.path.lexists(path):
            raise RecordBundleError("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise RecordBundleError(problem)
    try:
        snapshot = readers.read_snapshot(plan["readers"])
        case_set = readers.read_cases(plan["readers"])
    except readers.ReaderError as error:
        raise RecordBundleError(str(error)) from None
    held = []
    tables, extract_sha = read_extract(plan, held)
    ancestors, vocabulary_sha = record_vocabulary.drug_ancestors(
        plan["vocabulary"])
    provenance = record.RecordProvenance(
        source_snapshot=DigestRef(name=snapshot.label,
                                  sha256=snapshot.manifest_sha256),
        record_extract=DigestRef(name=plan["label"] + "-records",
                                 sha256=extract_sha),
        identity_mapping=DigestRef(name=plan["label"] + "-mapping",
                                   sha256=case_set.mapping_sha256),
        closure_record=DigestRef(name=plan["label"] + "-closure",
                                 sha256=case_set.closure_sha256),
        concept_table=DigestRef(name=snapshot.label + "-concepts",
                                sha256=snapshot.concepts_sha256),
        drug_ancestors=DigestRef(name=plan["vocabulary"].get(
            "version", record_vocabulary.OUTPUT_VERSION),
            sha256=vocabulary_sha),
        builder=BUILDER)
    bundles, counts = build_set(
        case_set, snapshot, Extract(tables), ancestors, provenance,
        {"snapshot": snapshot.label, "extract": plan["label"] + "-records"})
    manifest = manifest_document(bundles, provenance, counts,
                                 plan["label"] + "-" + SET_VERSION)
    summary = summary_document(manifest)
    files = {"%s/%s.json" % (FOLDER, bundle.bundle_id): bundle_bytes(bundle)
             for bundle in bundles}
    files[MANIFEST_NAME] = encode(manifest)
    files[SUMMARY_NAME] = encode(summary)
    mapping.confirm_unchanged(held)
    publish(plan, files)
    return summary, {name: hashlib.sha256(files[name]).hexdigest()
                     for name in (MANIFEST_NAME, SUMMARY_NAME)}


def publish(plan, files):
    """Write the files through a new .partial directory; rename it."""
    partial = plan["output_dir"] + PARTIAL
    os.makedirs(os.path.dirname(plan["output_dir"]), exist_ok=True)
    os.mkdir(partial)
    written = []
    try:
        os.mkdir(os.path.join(partial, FOLDER))
        for name, data in sorted(files.items()):
            target = os.path.join(partial, *name.split("/"))
            with open(target, "xb") as handle:
                written.append(target)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        os.rename(partial, plan["output_dir"])
    except BaseException:
        for target in written:
            if os.path.lexists(target):
                os.remove(target)
        if os.path.isdir(os.path.join(partial, FOLDER)):
            os.rmdir(os.path.join(partial, FOLDER))
        if os.path.isdir(partial):
            os.rmdir(partial)
        raise


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

SYNTHETIC_SUBJECT = "900001"


def synthetic_extract(case, snapshot):
    """Return an invented raw extract for the invented case: its
    admissions, transfers, an ICU stay, results, a culture, vital signs
    and prescriptions, in the case's visits and outside them."""
    visits = [snapshot.visits[visit_id] for visit_id in case.visit_ids]
    admissions = ["80000%d" % (index + 1) for index in range(len(visits))]
    last = admissions[-1]
    first = admissions[0]

    def rows(columns, values):
        """Return rows as dictionaries."""
        return [dict(zip(columns, value)) for value in values]

    tables = {
        "d_labitems": rows(("row_number", "itemid", "label", "fluid",
                            "category"),
                           [("1", "50971", "SYNTHETIC POTASSIUM", "Blood",
                             "Chemistry"),
                            ("2", "50912", "SYNTHETIC CREATININE", "Blood",
                             "Chemistry")]),
        "d_items": rows(("row_number", "itemid", "label", "category"),
                        [("1", "220045", "SYNTHETIC HEART RATE",
                          "Routine Vital Signs"),
                         ("2", "225459", "SYNTHETIC CHEST X-RAY",
                          "Imaging")]),
        "patients": rows(("row_number", "subject_id", "gender", "anchor_age",
                          "anchor_year", "anchor_year_group", "dod"),
                         [("1", SYNTHETIC_SUBJECT, "F", "60", "2100",
                           "2008 - 2010", "")]),
        "admissions": rows(
            ("row_number", "subject_id", "hadm_id", "admittime",
             "dischtime", "deathtime", "admission_type",
             "admission_location", "discharge_location", "insurance",
             "language", "marital_status", "race", "edregtime", "edouttime",
             "hospital_expire_flag"),
            [(str(index + 1), SYNTHETIC_SUBJECT, admission,
              (visit.start_date or "2100-01-01") + " 08:00:00",
              (visit.end_date or "2100-01-02") + " 12:00:00", "",
              "EW EMER.", "EMERGENCY ROOM", "HOME", "Other", "English",
              "SINGLE", "WHITE", "", "", "0")
             for index, (visit, admission) in enumerate(zip(visits,
                                                            admissions))]),
        "transfers": rows(
            ("row_number", "subject_id", "hadm_id", "transfer_id",
             "eventtype", "careunit", "intime", "outtime"),
            [("1", SYNTHETIC_SUBJECT, last, "70001", "admit",
              "SYNTHETIC UNIT", "2100-01-03 08:00:00",
              "2100-01-04 08:00:00")]),
        "icustays": rows(
            ("row_number", "subject_id", "hadm_id", "stay_id",
             "first_careunit", "last_careunit", "intime", "outtime", "los"),
            [("1", SYNTHETIC_SUBJECT, last, "60001",
              "SYNTHETIC ICU", "SYNTHETIC ICU", "2100-01-03 09:00:00",
              "2100-01-04 09:00:00", "1.0")]),
        "labevents": rows(
            ("row_number", "labevent_id", "subject_id", "hadm_id",
             "specimen_id", "itemid", "charttime", "storetime", "value",
             "valuenum", "valueuom", "ref_range_lower", "ref_range_upper",
             "flag", "priority"),
            [("1", "11", SYNTHETIC_SUBJECT, first, "1", "50971",
              "2100-01-01 09:00:00", "", "5.6", "5.6", "mEq/L", "3.3", "5.1",
              "abnormal", "STAT"),
             ("2", "12", SYNTHETIC_SUBJECT, last, "2", "50971",
              "2100-01-03 09:00:00", "", "4.1", "4.1", "mEq/L", "3.3", "5.1",
              "", "ROUTINE"),
             ("3", "13", SYNTHETIC_SUBJECT, "", "3", "50912",
              "2099-12-01 09:00:00", "", "1.0", "1.0", "mg/dL", "0.5", "1.2",
              "", "ROUTINE")]),
        "microbiologyevents": rows(
            ("row_number", "microevent_id", "subject_id", "hadm_id",
             "micro_specimen_id", "chartdate", "charttime", "spec_itemid",
             "spec_type_desc", "test_seq", "storedate", "storetime",
             "test_itemid", "test_name", "org_itemid", "org_name",
             "isolate_num", "quantity", "ab_itemid", "ab_name",
             "dilution_text", "dilution_comparison", "dilution_value",
             "interpretation"),
            [("1", "21", SYNTHETIC_SUBJECT, last, "1",
              "2100-01-03 00:00:00", "2100-01-03 10:00:00", "70012",
              "BLOOD CULTURE", "1", "", "", "90201", "SYNTHETIC CULTURE",
              "80002", "SYNTHETIC ORGANISM", "1", "", "90015",
              "SYNTHETIC ANTIBIOTIC", "<=1", "<=", "1", "S")]),
        "chartevents": rows(
            ("row_number", "subject_id", "hadm_id", "stay_id", "charttime",
             "storetime", "itemid", "value", "valuenum", "valueuom",
             "warning"),
            [("7", SYNTHETIC_SUBJECT, last, "60001", "2100-01-03 10:00:00",
              "", "220045", "88", "88", "bpm", "0"),
             ("9", SYNTHETIC_SUBJECT, last, "60001", "2100-01-03 11:00:00",
              "", "220045", "96", "96", "bpm", "0")]),
        "procedureevents": rows(
            ("row_number", "subject_id", "hadm_id", "stay_id", "starttime",
             "endtime", "storetime", "itemid", "value", "valueuom",
             "location", "locationcategory", "ordercategoryname",
             "ordercategorydescription", "patientweight",
             "statusdescription"),
            [("4", SYNTHETIC_SUBJECT, last, "60001", "2100-01-03 12:00:00",
              "2100-01-03 12:01:00", "", "225459", "1", "None", "", "",
              "Imaging", "Task", "70", "FinishedRunning")]),
        "prescriptions": rows(
            ("row_number", "subject_id", "hadm_id", "starttime", "stoptime",
             "drug_type", "drug", "formulary_drug_cd", "gsn", "ndc",
             "prod_strength", "form_rx", "dose_val_rx", "dose_unit_rx",
             "form_val_disp", "form_unit_disp", "doses_per_24_hrs",
             "route"),
            []),
    }
    return tables


def synthetic_sources():
    """Return the invented sources of the invented record bundles: the
    snapshot, the case set of its one case, the raw extract and the drug
    concepts' ingredients."""
    from ehr_evidence_app.evidence import bundles as qa_bundles
    snapshot, case_set, _, _, _ = qa_bundles.synthetic_inputs()
    case = case_set.cases[0]
    snapshot = dataclasses.replace(snapshot, visits={
        visit_id: dataclasses.replace(
            snapshot.visits[visit_id],
            source_value="%s|80000%d" % (SYNTHETIC_SUBJECT, index + 1))
        for index, visit_id in enumerate(case.visit_ids)})
    case = dataclasses.replace(case, subject_id=int(SYNTHETIC_SUBJECT))
    case_set = dataclasses.replace(case_set, cases=(case,))
    extract = Extract(synthetic_extract(case, snapshot))
    return snapshot, case_set, extract, {3001: (7001,)}


def run_synthetic():
    """Build invented record bundles in memory; nothing is read or
    written."""
    snapshot, case_set, extract, ancestors = synthetic_sources()
    ref = DigestRef
    provenance = record.RecordProvenance(
        source_snapshot=ref(name="synthetic-1-v1", sha256="1" * 64),
        record_extract=ref(name="synthetic-1-v1-records", sha256="6" * 64),
        identity_mapping=ref(name="synthetic-mapping", sha256="4" * 64),
        closure_record=ref(name="synthetic-closure", sha256="3" * 64),
        concept_table=ref(name="synthetic-concepts", sha256="2" * 64),
        builder=BUILDER)
    return build_set(case_set, snapshot, extract, ancestors, provenance,
                     {"snapshot": "synthetic-1-v1",
                      "extract": "synthetic-1-v1-records"})


def report(counts, digests=None):
    """Print the aggregate counts."""
    for key in sorted(counts):
        value = counts[key]
        if isinstance(value, dict):
            value = ", ".join("%s %d" % item for item in value.items())
        print("%-34s %s" % (key, value))
    for name, value in sorted((digests or {}).items()):
        print("%-34s %s" % (name, value))


def main(argv=None):
    """Run the synthetic default mode or the operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="build a case set's record bundles; writes one "
                             "new protected directory")
    parser.add_argument("--case-set", help="the case set (with "
                                           "--operator-real)")
    args = parser.parse_args(argv)
    if args.operator_real != (args.case_set is not None):
        print("FAILED: --operator-real and --case-set go together")
        return 2
    try:
        if args.operator_real:
            summary, digests = run_real(real_plan(args.case_set))
            report(summary["counts"], digests)
        else:
            _, counts = run_synthetic()
            report(counts)
    except RecordBundleError as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
