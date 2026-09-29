#!/usr/bin/env python3
"""Raw MIMIC-IV records of the case sets' persons (the whole record).

The OMOP source snapshots hold each person's visits, conditions,
procedures and drug exposures. The rest of the record is in the raw
MIMIC-IV 3.1 hospital and ICU modules in the cold archive: the person's
sex, anchor age and date of death; each admission's type, locations,
times and the details recorded with it; the transfers between care
units; the ICU stays; the laboratory results; the microbiology; the vital
signs charted in the ICU; the ICU procedure events; and the prescriptions
with their doses and routes. The local database never loaded most of
them (its ICU chart tables are empty, and its laboratory results carry no
names), so they are read here, from the raw files.

Each pinned raw file is read once, read-only, and hashed over exactly the
bytes read; a blank line, or a record whose quotes never close, stops the
extraction. The rows kept are those of exactly the persons of each pinned
source snapshot (pilot-10-v1, dev-30-v1 and eval-100-v1), matched on
subject_id; of the ICU chart events, only the items of the fixed
vital-sign list (VITAL_GROUPS). Nothing else is filtered: rows that no
case visit holds are kept, so that coverage can count them. Free-text
comments and the identifiers of staff and orders are dropped. Every row
keeps its number in its source file, the key of rows that have no key of
their own; the item dictionaries are kept whole.

Per case set one protected directory is published,
protected-data\\source-records\\<label>: one UTF-8 CSV per table and a
manifest with every input's size and SHA-256 and every output's size,
SHA-256 and row count. The directories are written as .partial and
renamed only when every input matched its pin and every check passed; an
existing directory is never overwritten.

Console output carries aggregate counts only: never an identifier, a
date or a value.

Usage:
    python -m ehr_evidence_app.sources.mimic
    python -m ehr_evidence_app.sources.mimic --operator-real
    python -m ehr_evidence_app.sources.mimic --operator-real --verify-only
    python -m ehr_evidence_app.sources.mimic --operator-real \\
        --measure-inputs
"""

from __future__ import annotations

import argparse
import collections
import csv
import dataclasses
import datetime
import hashlib
import io
import json
import os
import sys

import ehr_evidence_app
from ehr_evidence_app.storage import policy as guard

SCHEMA = "record-source-extract"
SCHEMA_VERSION = "1.0"
RAW_ROOT = guard.RAW_SOURCE_DIR
PROTECTED_ROOT = guard.PRIVATE_ROOT + "\\protected-data"
OUTPUT_ROOT = PROTECTED_ROOT + "\\source-records"
MANIFEST_NAME = "manifest.json"
PARTIAL = ".partial"
CHUNK_BYTES = 16 * 1024 * 1024
ROW_NUMBER = "row_number"
LABELS = ("pilot-10-v1", "dev-30-v1", "eval-100-v1")
BUILDER = "ehr_evidence_app.sources.mimic %s" % ehr_evidence_app.__version__
STATEMENT = (
    "Every row of the listed raw MIMIC-IV 3.1 tables for exactly the "
    "persons of the source snapshot, at the pinned files; of the ICU chart "
    "events, the items of the vital-sign list only. Not a statement of "
    "clinical absence: the ICU tables hold ICU stays only, and nothing "
    "outside these tables is read.")

# The vital signs read from the ICU chart events, by group: MIMIC-IV item
# numbers of the ICU item dictionary. The groups name what a question asks
# about; weights and heights are the kilogram and centimetre items only.
VITAL_GROUPS = {
    "heart_rate": (220045,),
    "heart_rhythm": (220048,),
    "systolic_blood_pressure": (220179, 220050, 225309, 224167, 227243),
    "diastolic_blood_pressure": (220180, 220051, 225310, 224643, 227242),
    "mean_blood_pressure": (220181, 220052, 225312),
    "respiratory_rate": (220210, 224690, 224689),
    "oxygen_saturation": (220277,),
    "temperature": (223761, 223762),
    "weight": (226512, 224639),
    "height": (226730,),
    "glasgow_coma_scale": (220739, 223900, 223901),
    "pain_level": (223791,),
    "oxygen_flow": (223834,),
    "inspired_oxygen_fraction": (223835,),
}
VITAL_ITEMS = frozenset(item for items in VITAL_GROUPS.values()
                        for item in items)


class ExtractionError(Exception):
    """A fail-closed extraction problem carrying a finding code only."""


@dataclasses.dataclass(frozen=True)
class Table:
    """One raw table: its file, its exact header, the column that names its
    person (None for an item dictionary, kept whole), the columns kept, in
    order, the columns that are its own key, its admission and the item
    the vital-sign list filters, and the columns whose distinct values,
    over every row of the table, form a dictionary of names that links
    no row to them."""

    name: str
    path: str
    header: tuple
    subject: str | None
    keep: tuple
    key: str | None = None
    visit: str | None = None
    items: str | None = None
    names: tuple = ()

    def index(self, column):
        """Return a column's position in the raw header."""
        return self.header.index(column)


def _table(name, module, header, subject, dropped=(), **fields):
    """Return a Table keeping every column but the dropped ones."""
    header = tuple(header.split(","))
    keep = tuple(column for column in header if column not in dropped)
    return Table(name, "%s\\%s.csv\\%s.csv" % (module, name, name), header,
                 subject, keep, **fields)


TABLES = (
    _table("d_labitems", "hosp", "itemid,label,fluid,category", None,
           key="itemid"),
    _table("d_items", "icu", "itemid,label,abbreviation,linksto,category,"
           "unitname,param_type,lownormalvalue,highnormalvalue", None,
           key="itemid"),
    _table("patients", "hosp", "subject_id,gender,anchor_age,anchor_year,"
           "anchor_year_group,dod", "subject_id", key="subject_id"),
    _table("admissions", "hosp", "subject_id,hadm_id,admittime,dischtime,"
           "deathtime,admission_type,admit_provider_id,admission_location,"
           "discharge_location,insurance,language,marital_status,race,"
           "edregtime,edouttime,hospital_expire_flag", "subject_id",
           ("admit_provider_id",), key="hadm_id", visit="hadm_id"),
    _table("transfers", "hosp", "subject_id,hadm_id,transfer_id,eventtype,"
           "careunit,intime,outtime", "subject_id", key="transfer_id",
           visit="hadm_id", names=("careunit",)),
    _table("icustays", "icu", "subject_id,hadm_id,stay_id,first_careunit,"
           "last_careunit,intime,outtime,los", "subject_id", key="stay_id",
           visit="hadm_id"),
    _table("microbiologyevents", "hosp", "microevent_id,subject_id,hadm_id,"
           "micro_specimen_id,order_provider_id,chartdate,charttime,"
           "spec_itemid,spec_type_desc,test_seq,storedate,storetime,"
           "test_itemid,test_name,org_itemid,org_name,isolate_num,quantity,"
           "ab_itemid,ab_name,dilution_text,dilution_comparison,"
           "dilution_value,interpretation,comments", "subject_id",
           ("order_provider_id", "comments"), key="microevent_id",
           visit="hadm_id", names=("spec_type_desc", "test_name",
                                   "org_name", "ab_name")),
    _table("procedureevents", "icu", "subject_id,hadm_id,stay_id,"
           "caregiver_id,starttime,endtime,storetime,itemid,value,valueuom,"
           "location,locationcategory,orderid,linkorderid,ordercategoryname,"
           "ordercategorydescription,patientweight,isopenbag,"
           "continueinnextdept,statusdescription,originalamount,"
           "originalrate", "subject_id",
           ("caregiver_id", "orderid", "linkorderid", "isopenbag",
            "continueinnextdept", "originalamount", "originalrate"),
           visit="hadm_id"),
    _table("prescriptions", "hosp", "subject_id,hadm_id,pharmacy_id,poe_id,"
           "poe_seq,order_provider_id,starttime,stoptime,drug_type,drug,"
           "formulary_drug_cd,gsn,ndc,prod_strength,form_rx,dose_val_rx,"
           "dose_unit_rx,form_val_disp,form_unit_disp,doses_per_24_hrs,"
           "route", "subject_id",
           ("pharmacy_id", "poe_id", "poe_seq", "order_provider_id"),
           visit="hadm_id"),
    _table("labevents", "hosp", "labevent_id,subject_id,hadm_id,specimen_id,"
           "itemid,order_provider_id,charttime,storetime,value,valuenum,"
           "valueuom,ref_range_lower,ref_range_upper,flag,priority,comments",
           "subject_id", ("order_provider_id", "comments"),
           key="labevent_id", visit="hadm_id"),
    _table("chartevents", "icu", "subject_id,hadm_id,stay_id,caregiver_id,"
           "charttime,storetime,itemid,value,valuenum,valueuom,warning",
           "subject_id", ("caregiver_id",), visit="hadm_id", items="itemid"),
)
TABLE_NAMES = tuple(table.name for table in TABLES)
NAMES_FILE = "d_names.csv"
NAMES_COLUMNS = ("table", "column", "name")

# (size in bytes, SHA-256) of each raw input, pinned before the first
# extraction; the digests are of the files as they are in the archive
# (the archive's own checksums are of the compressed files).
INPUTS = {
    "d_labitems": (64663, "f3182dbace857d8cd3439ebbd2095d31"
                          "28c1751cb42b12e3e4208bdc4b363567"),
    "d_items": (375225, "2e8987d8ac98e48358e582708f953710"
                        "f50bdea7fb4a8bb427acb364a7212894"),
    "patients": (12051137, "11df8b065f89410e894ab72857c1ce5c"
                           "431f6f5d675e1541f132d49122f7d756"),
    "admissions": (94156458, "9592fb5d355321801f3cb5789a20921d"
                             "7394fde2ade5325832ea527318056d63"),
    "transfers": (205259634, "99a5d03793a18c7497203ab79a94f6af"
                             "a90b2faccc1b58cd86d88945ca6be6e8"),
    "icustays": (14686092, "3b16bc6cc9b93c7f20a040ba5ec38d00"
                           "951f42475bf5157aea7ffde052676ef0"),
    "microbiologyevents": (909240343, "2ae3f75d0399476902c9a17f41aeb5a3"
                                      "de663c4012c18a4f4a5a017d6b24d4bd"),
    "procedureevents": (150454128, "4858144bf33be198a002658556fd0352"
                                   "143653802a93d965de4f891b0beb1450"),
    "prescriptions": (3488170135, "f7967bbf1a4aff8e37f466f675cb36b6"
                                  "bff0cea44faec139f572f5353e9738c0"),
    "labevents": (18402851720, "dc4deb75491acb09b0c589b88d36e72f"
                               "6e6db7676152510d83221aa8133dff54"),
    "chartevents": (41935806083, "0d1932df345c2d135ce715df1f6afa60"
                                 "3f7124894a5aa8c6ba56aa6ee02160d4"),
}

# (size in bytes, SHA-256) of each case set's published extract manifest,
# pinned after the extraction.
EXTRACT_PINS = {
    "pilot-10-v1": (10051, "77fdb59e2af0a947b9d62c5e0baa90bf"
                           "e6a0c3310b718c530cc7343bf5c5c7de"),
    "dev-30-v1": (10073, "c500482c6d3662dee657feabf6138091"
                         "7447700fd6bd0cdb23f13212ed5bff03"),
    "eval-100-v1": (10089, "1588878ea59acb483d687c4dac172d6f"
                           "65f4cbc0463534bcc022b8c6decfe4d1"),
}


def pinned_manifest(label):
    """Return {path, size_bytes, sha256} of a case set's pinned extract
    manifest."""
    pin = EXTRACT_PINS.get(label)
    if pin is None:
        raise ExtractionError("extract_not_pinned:%s" % label)
    return {"path": OUTPUT_ROOT + "\\" + label + "\\" + MANIFEST_NAME,
            "size_bytes": pin[0], "sha256": pin[1]}


def read_files(label, names):
    """Return {file name: rows} of some files of a case set's pinned
    extract, each verified against its manifest."""
    from ehr_evidence_app.gnn import mapping as idmap
    entry = pinned_manifest(label)
    try:
        data, _ = idmap.read_verified(
            entry["path"], "record_extract",
            "input_hash_mismatch:record_extract", sha256=entry["sha256"],
            size=entry["size_bytes"])
        manifest = json.loads(data.decode("utf-8"))
    except idmap.MappingError as error:
        raise ExtractionError(str(error)) from None
    listed = {item["file"]: item for item in manifest["files"]}
    found = {}
    for name in names:
        item = listed.get(name)
        if item is None:
            raise ExtractionError("extract_file_unlisted")
        path = os.path.join(os.path.dirname(entry["path"]), name)
        try:
            with open(path, "rb") as handle:
                content = handle.read(item["size_bytes"] + 1)
        except OSError:
            raise ExtractionError("extract_file_unreadable") from None
        if (len(content) != item["size_bytes"]
                or hashlib.sha256(content).hexdigest() != item["sha256"]):
            raise ExtractionError("extract_hash_mismatch")
        found[name] = list(csv.DictReader(io.StringIO(
            content.decode("utf-8"), newline="")))
    return found


# --------------------------------------------------------------------------
# Reading one raw table
# --------------------------------------------------------------------------

def records(handle, counter):
    """Yield the logical CSV records of a binary stream, one bytes value
    each, without their line ends; counter holds the size and SHA-256 of
    every byte read. A record whose quotes are open continues on the next
    line."""
    carry, pending, quotes = b"", None, 0
    while True:
        chunk = handle.read(CHUNK_BYTES)
        if not chunk:
            break
        counter["sha256"].update(chunk)
        counter["size"] += len(chunk)
        lines = (carry + chunk).split(b"\n")
        carry = lines.pop()
        for line in lines:
            if pending is None:
                count = line.count(b'"')
                if count & 1:
                    pending, quotes = line, count
                    continue
                yield line[:-1] if line.endswith(b"\r") else line
            else:
                pending += b"\n" + line
                quotes += line.count(b'"')
                if not quotes & 1:
                    record, pending = pending, None
                    yield record[:-1] if record.endswith(b"\r") else record
    if pending is not None:
        carry = pending + b"\n" + carry if carry else pending
    if carry.count(b'"') & 1:
        raise ExtractionError("record_unterminated")
    if carry:
        yield carry[:-1] if carry.endswith(b"\r") else carry


def decode(record, counts):
    """Return a record as text: UTF-8, or Latin-1 when it is not UTF-8
    (counted)."""
    try:
        return record.decode("utf-8")
    except UnicodeDecodeError:
        counts["decoded_latin1"] += 1
        return record.decode("latin-1")


def fields_of(text):
    """Return the fields of one logical CSV record."""
    rows = list(csv.reader(io.StringIO(text, newline="")))
    if len(rows) != 1:
        raise ExtractionError("record_malformed")
    return rows[0]


def scan(handle, table, routes, emit):
    """Read one raw table; emit(labels, number, fields) each kept row
    with its number. routes maps a subject_id to the labels whose persons
    it names. Return the table's counts: the records read, the rows kept
    by label, the kept rows decoded as Latin-1, the distinct values of its
    name columns, and the size and SHA-256 of the bytes read."""
    counter = {"sha256": hashlib.sha256(), "size": 0}
    counts = collections.Counter()
    kept = collections.Counter()
    names = {column: set() for column in table.names}
    named = [(column, table.index(column)) for column in table.names]
    everyone = tuple(sorted({label for labels in routes.values()
                             for label in labels}))
    subject = table.index(table.subject) if table.subject else None
    item = table.index(table.items) if table.items else None
    need = max(index for index in (subject, item, 0) if index is not None)
    vital = {str(number).encode() for number in VITAL_ITEMS}
    number = -1
    for record in records(handle, counter):
        number += 1
        if number == 0:
            if tuple(decode(record, counts).lstrip("﻿").split(",")) != (
                    table.header):
                raise ExtractionError("header_unexpected:%s" % table.name)
            continue
        if not record:
            # A blank line is no row, in every table alike.
            raise ExtractionError("record_malformed:%s" % table.name)
        if named:
            fields = fields_of(decode(record, collections.Counter()))
            if len(fields) != len(table.header):
                raise ExtractionError("record_malformed:%s" % table.name)
            for column, index in named:
                if fields[index].strip():
                    names[column].add(fields[index].strip())
        if subject is None:
            labels = everyone
        else:
            # The columns before the person's and the item's are numbers
            # and times; a quote among them means the record is read whole.
            head = record.split(b",", need + 1)
            if len(head) <= need:
                raise ExtractionError("record_malformed:%s" % table.name)
            prefix = (record if len(head) == need + 1
                      else record[:len(record) - len(head[-1])])
            if b'"' in prefix:
                head = [value.encode("utf-8") for value in fields_of(
                    decode(record, collections.Counter()))]
            labels = routes.get(head[subject])
            if labels is None or (item is not None
                                  and head[item] not in vital):
                continue
        fields = fields_of(decode(record, counts))
        if len(fields) != len(table.header):
            raise ExtractionError("record_malformed:%s" % table.name)
        if subject is not None and routes.get(
                fields[subject].encode("utf-8")) != labels:
            raise ExtractionError("record_malformed:%s" % table.name)
        emit(labels, number, fields)
        for label in labels:
            kept[label] += 1
    if number < 0:
        raise ExtractionError("header_unexpected:%s" % table.name)
    counts["records"] = number
    return {"records": counts["records"], "kept": dict(kept),
            "decoded_latin1": counts["decoded_latin1"],
            "names": {column: sorted(values)
                      for column, values in names.items()},
            "size_bytes": counter["size"],
            "sha256": counter["sha256"].hexdigest()}


# --------------------------------------------------------------------------
# The rows kept for one case set
# --------------------------------------------------------------------------

class Collector:
    """The rows of one case set's tables, checked as they arrive: each
    table's own key is unique, and the rows are counted by whether their
    admission is one of the snapshot's visits."""

    def __init__(self, label, admissions):
        """Start empty for a case set whose snapshot visits have these
        admission identifiers."""
        self.label = label
        self.admissions = admissions
        self.rows = {name: [] for name in TABLE_NAMES}
        self.keys = {name: set() for name in TABLE_NAMES}
        self.linked = collections.Counter()

    def add(self, table, number, fields):
        """Keep one row of a table."""
        if table.key is not None:
            key = fields[table.index(table.key)]
            if not key or key in self.keys[table.name]:
                raise ExtractionError("duplicate_native_key:%s" % table.name)
            self.keys[table.name].add(key)
        if table.visit is not None and (
                fields[table.index(table.visit)] in self.admissions):
            self.linked[table.name] += 1
        self.rows[table.name].append(
            [str(number)] + [fields[table.index(column)]
                             for column in table.keep])

    def table_bytes(self, table):
        """Return one table's CSV bytes: a header, then its rows in the
        order of the source file."""
        text = io.StringIO(newline="")
        writer = csv.writer(text, lineterminator="\n")
        writer.writerow((ROW_NUMBER,) + table.keep)
        writer.writerows(self.rows[table.name])
        return text.getvalue().encode("utf-8")


# --------------------------------------------------------------------------
# Plan, inputs and the source snapshots
# --------------------------------------------------------------------------

def real_plan():
    """Return the pinned raw inputs, the source snapshot of each case set
    and the fixed protected outputs."""
    from ehr_evidence_app.evidence import readers
    snapshots = {}
    for label in LABELS:
        plan = readers.real_plan(None if label == readers.PILOT_LABEL
                                 else label)
        snapshots[label] = {"snapshot_dir": plan["snapshot_dir"],
                            "manifest": plan["manifest"], "label": label}
    return {
        "raw_root": RAW_ROOT,
        "inputs": {table.name: {"path": RAW_ROOT + "\\" + table.path,
                                "pin": INPUTS[table.name]}
                   for table in TABLES},
        "snapshots": snapshots,
        "output_root": OUTPUT_ROOT,
        "protected_root": PROTECTED_ROOT,
    }


def output_dir(plan, label):
    """Return a case set's protected output directory."""
    return plan["output_root"] + "\\" + label


def snapshot_persons(snapshot):
    """Return (subject ids, admission ids of the snapshot's visits, the
    snapshot manifest's SHA-256) of one pinned source snapshot, every file
    verified against its pin."""
    from ehr_evidence_app.evidence import readers
    from ehr_evidence_app.gnn import mapping as idmap
    held = []
    try:
        manifest = readers.parse_json(readers.read_pinned(
            snapshot["manifest"], "snapshot_manifest", held),
            "snapshot_manifest")
        entries = readers.manifest_entries(manifest, snapshot)
        persons = readers.csv_rows(readers.read_pinned(
            readers.file_entry(entries, snapshot, "persons.csv"),
            "persons.csv", held), "persons.csv", ("person_id",
                                                  "subject_id"))
        visits = readers.csv_rows(readers.read_pinned(
            readers.file_entry(entries, snapshot, "visits.csv"),
            "visits.csv", held), "visits.csv", ("visit_occurrence_id",
                                                "visit_source_value"))
        idmap.confirm_unchanged(held)
    except (readers.ReaderError, idmap.MappingError) as error:
        raise ExtractionError("snapshot:%s" % error) from None
    subjects = {row["subject_id"] for row in persons}
    if not subjects or "" in subjects or len(subjects) != len(persons):
        raise ExtractionError("snapshot_persons_invalid")
    admissions = set()
    for row in visits:
        subject, _, admission = (row["visit_source_value"] or "").partition(
            "|")
        if subject in subjects and admission:
            admissions.add(admission)
    return subjects, admissions, snapshot["manifest"]["sha256"].lower()


def routes_of(persons):
    """Return {subject_id bytes: labels} for {label: subject ids}."""
    routes = collections.defaultdict(list)
    for label, subjects in sorted(persons.items()):
        for subject in subjects:
            routes[subject.encode("utf-8")].append(label)
    return {subject: tuple(labels) for subject, labels in routes.items()}


def input_problem(entry, raw_root):
    """Return a finding code when a raw input may not be read."""
    if guard.raw_source_problem(entry["path"], raw_root):
        return "input_path_rejected"
    return None


def read_table(entry, table, routes, emit, raw_root):
    """Read one raw input once, checking that it did not change while it
    was read; return its counts."""
    problem = input_problem(entry, raw_root)
    if problem:
        raise ExtractionError("%s:%s" % (problem, table.name))
    try:
        before = os.lstat(entry["path"])
        with open(entry["path"], "rb") as handle:
            counts = scan(handle, table, routes, emit)
        after = os.lstat(entry["path"])
    except OSError:
        raise ExtractionError("input_unreadable:%s" % table.name) from None
    if ((before.st_size, before.st_mtime_ns)
            != (after.st_size, after.st_mtime_ns)):
        raise ExtractionError("input_changed_during_read:%s" % table.name)
    return counts


def pin_problem(counts, pin, name):
    """Return a finding code when an input's bytes are not its pin's."""
    if pin is None:
        return "input_not_pinned:%s" % name
    if (counts["size_bytes"], counts["sha256"]) != (pin[0], pin[1]):
        return "input_hash_mismatch:%s" % name
    return None


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------

def now_utc():
    """Return the current UTC time in ISO form."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")


def encode(document):
    """Return deterministic JSON bytes of a manifest."""
    return (json.dumps(document, indent=2, sort_keys=True, allow_nan=False)
            + "\n").encode("utf-8")


def manifest_document(label, snapshot, persons, admissions, collector,
                      inputs, files):
    """Return the manifest of one case set's extract (aggregates only)."""
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "version_label": label, "status": "complete",
        "run_state": "finished", "promoted": True, "builder": BUILDER,
        "created": now_utc(), "patient_bearing": False,
        "statement": STATEMENT,
        "source_snapshot": {"label": label, "manifest_sha256": snapshot,
                            "persons": persons,
                            "visit_admissions": admissions},
        "vital_items": sorted(VITAL_ITEMS),
        "inputs": [{"table": table.name, "path": table.path,
                    "size_bytes": inputs[table.name]["size_bytes"],
                    "sha256": inputs[table.name]["sha256"],
                    "records": inputs[table.name]["records"],
                    "decoded_latin1": inputs[table.name]["decoded_latin1"]}
                   for table in TABLES],
        "files": [{"file": table.name + ".csv",
                   "columns": [ROW_NUMBER] + list(table.keep),
                   "rows": len(collector.rows[table.name]),
                   "rows_in_snapshot_visits": (
                       collector.linked[table.name]
                       if table.visit is not None else None),
                   "size_bytes": len(files[table.name + ".csv"]),
                   "sha256": hashlib.sha256(
                       files[table.name + ".csv"]).hexdigest()}
                  for table in TABLES] + [
            {"file": NAMES_FILE, "columns": list(NAMES_COLUMNS),
             "rows": sum(len(values) for table in TABLES
                         for values in inputs[table.name]["names"].values()),
             "rows_in_snapshot_visits": None,
             "size_bytes": len(files[NAMES_FILE]),
             "sha256": hashlib.sha256(files[NAMES_FILE]).hexdigest()}],
    }


def names_bytes(inputs):
    """Return the dictionary of names: every distinct value of the name
    columns over every row of their tables, sorted; no row is linked to
    them."""
    text = io.StringIO(newline="")
    writer = csv.writer(text, lineterminator="\n")
    writer.writerow(NAMES_COLUMNS)
    for table in TABLES:
        for column in table.names:
            writer.writerows((table.name, column, value)
                             for value in inputs[table.name]["names"][column])
    return text.getvalue().encode("utf-8")


def output_preconditions(plan):
    """Refuse an existing or unprotected output or partial directory."""
    from ehr_evidence_app.gnn import mapping as idmap
    for label in LABELS:
        for path in (output_dir(plan, label),
                     output_dir(plan, label) + PARTIAL):
            if os.path.lexists(path):
                raise ExtractionError("output_exists")
            problem = idmap.output_problem(path, plan["protected_root"])
            if problem:
                raise ExtractionError(problem)


def publish(plan, outputs):
    """Write each case set's files through a new .partial directory, then
    rename every directory; outputs maps a label to {file name: bytes}."""
    written = []
    try:
        for label, files in outputs.items():
            partial = output_dir(plan, label) + PARTIAL
            os.makedirs(plan["output_root"], exist_ok=True)
            os.mkdir(partial)
            written.append(partial)
            for name, data in sorted(files.items()):
                with open(os.path.join(partial, name), "xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
        for label in outputs:
            os.rename(output_dir(plan, label) + PARTIAL,
                      output_dir(plan, label))
    except BaseException:
        for partial in written:
            if os.path.isdir(partial):
                for name in os.listdir(partial):
                    os.remove(os.path.join(partial, name))
                os.rmdir(partial)
        raise


def extract(plan):
    """Read every raw input once, keep each case set's rows, check every
    input against its pin and publish; return the manifests by label."""
    if guard.fixed_policy_problem():
        raise ExtractionError("fixed_policy_rejected")
    missing = [name for name, pin in INPUTS.items() if pin is None]
    if missing:
        raise ExtractionError("input_not_pinned:%s" % missing[0])
    output_preconditions(plan)
    persons, admissions, snapshots = {}, {}, {}
    for label, snapshot in plan["snapshots"].items():
        persons[label], admissions[label], snapshots[label] = (
            snapshot_persons(snapshot))
    routes = routes_of(persons)
    collectors = {label: Collector(label, admissions[label])
                  for label in plan["snapshots"]}
    inputs = {}
    for table in TABLES:
        def emit(labels, number, fields, table=table):
            """Keep one row for every case set it belongs to."""
            for label in labels:
                collectors[label].add(table, number, fields)
        inputs[table.name] = read_table(plan["inputs"][table.name], table,
                                        routes, emit, plan["raw_root"])
        problem = pin_problem(inputs[table.name],
                              plan["inputs"][table.name]["pin"], table.name)
        if problem:
            raise ExtractionError(problem)
    outputs, manifests = {}, {}
    for label, collector in collectors.items():
        files = {table.name + ".csv": collector.table_bytes(table)
                 for table in TABLES}
        files[NAMES_FILE] = names_bytes(inputs)
        manifest = manifest_document(label, snapshots[label],
                                     len(persons[label]),
                                     len(admissions[label]), collector,
                                     inputs, files)
        files[MANIFEST_NAME] = encode(manifest)
        outputs[label], manifests[label] = files, manifest
    publish(plan, outputs)
    return manifests


def measure_inputs(plan):
    """Return {table: (size, SHA-256)} of every raw input, reading each
    once; nothing is kept or written."""
    found = {}
    for table in TABLES:
        counts = read_table(plan["inputs"][table.name], table, {},
                            lambda *_: None, plan["raw_root"])
        found[table.name] = (counts["size_bytes"], counts["sha256"])
    return found


def verify(plan, label):
    """Return the problems of one published extract: every file of its
    manifest re-read and checked, and no other file."""
    directory = output_dir(plan, label)
    try:
        with open(os.path.join(directory, MANIFEST_NAME), "rb") as handle:
            manifest = json.loads(handle.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return ["manifest_unreadable"]
    problems = []
    if (manifest.get("schema") != SCHEMA
            or manifest.get("version_label") != label
            or manifest.get("status") != "complete"):
        problems.append("manifest_unexpected")
    listed = {entry["file"]: entry for entry in manifest.get("files", [])}
    present = set(os.listdir(directory)) - {MANIFEST_NAME}
    if present != set(listed):
        problems.append("files_unexpected")
    for name, entry in sorted(listed.items()):
        try:
            with open(os.path.join(directory, name), "rb") as handle:
                data = handle.read()
        except OSError:
            problems.append("file_unreadable:%s" % name)
            continue
        if (len(data) != entry["size_bytes"]
                or hashlib.sha256(data).hexdigest() != entry["sha256"]):
            problems.append("file_hash_mismatch:%s" % name)
    return problems


# --------------------------------------------------------------------------
# Synthetic default mode (invented rows only)
# --------------------------------------------------------------------------

SYNTHETIC_SUBJECTS = {"synthetic-a-v1": {"900001"},
                      "synthetic-b-v1": {"900002"}}
SYNTHETIC_ADMISSIONS = {"synthetic-a-v1": {"800001"},
                        "synthetic-b-v1": {"800002"}}


def synthetic_files():
    """Return invented raw file bytes by table: rows of both invented
    persons and of one other, a quoted value holding a comma and a line
    break, a Latin-1 byte, and chart items inside and outside the
    vital-sign list."""
    rows = {
        "d_labitems": ["1,SYNTHETIC TEST,Blood,Chemistry"],
        "d_items": ["220045,Heart Rate,HR,chartevents,Routine Vital Signs,"
                    "bpm,Numeric,,",
                    "999999,SYNTHETIC ITEM,SI,chartevents,General,,Text,,"],
        "patients": ["900001,F,60,2100,2008 - 2010,",
                     "900002,M,70,2101,2011 - 2013,2102-05-01",
                     "900003,F,50,2100,2008 - 2010,"],
        "admissions": ["900001,800001,2100-01-01 08:00:00,2100-01-05 "
                       "12:00:00,,EW EMER.,P1,EMERGENCY ROOM,HOME,Other,"
                       "English,SINGLE,WHITE,,,0",
                       "900002,800002,2101-02-01 08:00:00,2101-02-03 "
                       "10:00:00,,URGENT,P2,TRANSFER FROM HOSPITAL,HOME,"
                       "Other,English,MARRIED,WHITE,,,0",
                       "900003,800003,2100-01-01 08:00:00,2100-01-02 "
                       "08:00:00,,URGENT,P3,PHYSICIAN REFERRAL,HOME,Other,"
                       "English,SINGLE,WHITE,,,0"],
        "transfers": ["900001,800001,700001,admit,Medicine,2100-01-01 "
                      "08:00:00,2100-01-05 12:00:00"],
        "icustays": ["900002,800002,600002,Medical Intensive Care Unit "
                     "(MICU),Medical Intensive Care Unit (MICU),2101-02-01 "
                     "09:00:00,2101-02-02 09:00:00,1.0"],
        "microbiologyevents": [
            "1,900001,800001,11,P1,2100-01-02 00:00:00,2100-01-02 08:00:00,"
            "70012,BLOOD CULTURE,1,,,90201,Blood Culture,,,,,,,,,,,"
            "\"No growth, final\""],
        "procedureevents": ["900002,800002,600002,5,2101-02-01 10:00:00,"
                            "2101-02-01 10:30:00,2101-02-01 10:30:00,"
                            "225459,1,None,,,1,1,Imaging,Imaging,70,0,0,"
                            "FinishedRunning,1,1"],
        "prescriptions": ["900001,800001,1,P-1,1,P1,2100-01-01 09:00:00,"
                          "2100-01-03 09:00:00,MAIN,Synthetic\xb5drug,SYN1,"
                          "1,0,5 mg Tab,Tab,5,mg,1,TAB,1,PO/NG"],
        "labevents": ["1,900001,800001,1,1,P1,2100-01-02 06:00:00,"
                      "2100-01-02 07:00:00,5.1,5.1,mEq/L,3.5,5.0,abnormal,"
                      "ROUTINE,\"line one\nline two\"",
                      "2,900003,800003,2,1,P1,2100-01-02 06:00:00,"
                      "2100-01-02 07:00:00,4.0,4.0,mEq/L,3.5,5.0,,ROUTINE,"],
        "chartevents": ["900002,800002,600002,5,2101-02-01 10:00:00,"
                        "2101-02-01 10:05:00,220045,88,88,bpm,0",
                        "900002,800002,600002,5,2101-02-01 10:00:00,"
                        "2101-02-01 10:05:00,999999,x,,,0"],
    }
    files = {}
    for table in TABLES:
        text = ",".join(table.header) + "\r\n" + "".join(
            row + "\r\n" for row in rows[table.name])
        files[table.name] = text.encode("latin-1" if table.name
                                        == "prescriptions" else "utf-8")
    return files


def run_synthetic():
    """Extract the invented rows in memory; return (kept rows by case set
    and table, Latin-1 rows, problems)."""
    routes = routes_of(SYNTHETIC_SUBJECTS)
    collectors = {label: Collector(label, SYNTHETIC_ADMISSIONS[label])
                  for label in SYNTHETIC_SUBJECTS}
    latin1, names = 0, {}
    for table, data in zip(TABLES, (synthetic_files()[table.name]
                                    for table in TABLES)):
        def emit(labels, number, fields, table=table):
            """Keep one invented row."""
            for label in labels:
                collectors[label].add(table, number, fields)
        counts = scan(io.BytesIO(data), table, routes, emit)
        latin1 += counts["decoded_latin1"]
        names[table.name] = counts["names"]
    kept = {label: {name: len(rows) for name, rows in collector.rows.items()
                    if rows}
            for label, collector in collectors.items()}
    problems = []
    expected = {"synthetic-a-v1": {"d_labitems": 1, "d_items": 2,
                                   "patients": 1, "admissions": 1,
                                   "transfers": 1, "microbiologyevents": 1,
                                   "prescriptions": 1, "labevents": 1},
                "synthetic-b-v1": {"d_labitems": 1, "d_items": 2,
                                   "patients": 1, "admissions": 1,
                                   "icustays": 1, "procedureevents": 1,
                                   "chartevents": 1}}
    if kept != expected:
        problems.append("kept rows differ from the invented expectation")
    if latin1 != 1:
        problems.append("the Latin-1 row was not decoded as such")
    lab = collectors["synthetic-a-v1"].rows["labevents"][0]
    if lab[0] != "1" or lab[-1] != "ROUTINE":
        problems.append("the quoted multi-line record was not read whole")
    if (names["transfers"] != {"careunit": ["Medicine"]}
            or names["microbiologyevents"]["spec_type_desc"]
            != ["BLOOD CULTURE"]):
        problems.append("the dictionary of names differs from the invented "
                        "rows")
    return kept, latin1, problems


def report_manifest(manifest):
    """Print one extract's aggregate counts."""
    print("%s: %d persons, %d snapshot admissions" % (
        manifest["version_label"], manifest["source_snapshot"]["persons"],
        manifest["source_snapshot"]["visit_admissions"]))
    for entry in manifest["files"]:
        linked = entry["rows_in_snapshot_visits"]
        print("  %-22s %9d rows%s" % (
            entry["file"], entry["rows"],
            "" if linked is None else ", %d in snapshot visits" % linked))


def main(argv=None):
    """Run the synthetic default mode or an operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="extract the case sets' raw records; writes "
                             "one new protected directory per case set")
    parser.add_argument("--verify-only", action="store_true",
                        help="re-check the published extracts (with "
                             "--operator-real)")
    parser.add_argument("--measure-inputs", action="store_true",
                        help="print each raw input's size and SHA-256 "
                             "(with --operator-real)")
    args = parser.parse_args(argv)
    if (args.verify_only or args.measure_inputs) and not args.operator_real:
        print("FAILED: --verify-only and --measure-inputs need "
              "--operator-real")
        return 2
    try:
        if not args.operator_real:
            kept, latin1, problems = run_synthetic()
            for label, tables in sorted(kept.items()):
                print("%s: %s" % (label, ", ".join(
                    "%s %d" % item for item in sorted(tables.items()))))
            print("rows decoded as Latin-1: %d" % latin1)
            for problem in problems:
                print("PROBLEM: %s" % problem)
            print("RESULT: %s" % ("FAIL" if problems else "PASS"))
            return 1 if problems else 0
        plan = real_plan()
        if args.measure_inputs:
            for name, (size, digest) in measure_inputs(plan).items():
                print("%-20s %14d %s" % (name, size, digest))
        elif args.verify_only:
            failed = False
            for label in LABELS:
                problems = verify(plan, label)
                failed = failed or bool(problems)
                print("%s: %s" % (label, ", ".join(problems) or "verified"))
            print("RESULT: %s" % ("FAIL" if failed else "PASS"))
            return 1 if failed else 0
        else:
            manifests = extract(plan)
            for label in LABELS:
                report_manifest(manifests[label])
                print("  manifest SHA-256 %s" % hashlib.sha256(encode(
                    manifests[label])).hexdigest())
    except ExtractionError as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may quote row content
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
