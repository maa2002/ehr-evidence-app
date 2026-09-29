#!/usr/bin/env python3
"""Medication and procedure vocabulary for questions about the record.

Questions about medications and procedures resolve their terms against a
vocabulary built once from the same pinned Athena release as the
condition vocabulary (qa.vocabulary), read-only and once per file,
hashing exactly the bytes parsed. It publishes:

  drugs.tsv              the standard RxNorm and RxNorm Extension
                         ingredients, the ATC classes and the RxNorm
                         brand names;
  drug_synonyms.tsv      their English synonyms;
  brand_ingredients.tsv  each brand name's ingredients: the ingredients
                         of the branded drugs it names;
  drug_ancestors.tsv     for every drug concept the source snapshots
                         record, its ingredients and ATC classes;
  class_ingredients.tsv  for every ATC class, the ingredients the ATC
                         hierarchy places in it (version 2), which records
                         without a standard concept are matched by, by
                         their written drug names;
  procedures.tsv         the procedure concepts of ICD-9-CM volume 3,
                         ICD-10-PCS, SNOMED, CPT-4 and HCPCS, with their
                         English synonyms;
  manifest.json          input pins, counts and every file's digest.

Version 2 adds class_ingredients.tsv; every other file is built as in
version 1, from the same pinned inputs, byte for byte.

The drug ancestors are read from the source snapshots' drug records
(concept identifiers only), so the vocabulary is kept with the protected
data; like the condition vocabulary it is licensed content and never in
Git.

Usage:
    python -m ehr_evidence_app.qa.record_vocabulary
    python -m ehr_evidence_app.qa.record_vocabulary --operator-real
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

import ehr_evidence_app
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.qa import vocabulary as conditions
from ehr_evidence_app.storage import policy as guard

SCHEMA = "record-vocabulary"
SCHEMA_VERSION = "1.0"
OUTPUT_VERSION = "record-vocabulary-athena-20260831-v2"
PROTECTED_ROOT = conditions.PROTECTED_ROOT
OUTPUT_DIR = PROTECTED_ROOT + "\\vocabularies\\" + OUTPUT_VERSION
MANIFEST_NAME = "manifest.json"
PARTIAL = ".partial"
BUILDER = "ehr_evidence_app.qa.record_vocabulary %s" % (
    ehr_evidence_app.__version__)
FILES = {"drugs": "drugs.tsv", "synonyms": "drug_synonyms.tsv",
         "brands": "brand_ingredients.tsv",
         "ancestors": "drug_ancestors.tsv",
         "classes": "class_ingredients.tsv",
         "procedures": "procedures.tsv"}
COLUMNS = {
    "drugs": ("concept_id", "kind", "vocabulary_id", "concept_class_id",
              "concept_code", "concept_name"),
    "synonyms": ("concept_id", "synonym"),
    "brands": ("brand_id", "ingredient_id"),
    "ancestors": ("drug_concept_id", "ancestor_id"),
    "classes": ("class_id", "ingredient_id"),
    "procedures": ("concept_id", "vocabulary_id", "concept_code", "name"),
}
DRUG_VOCABULARIES = ("RxNorm", "RxNorm Extension")
PROCEDURE_VOCABULARIES = ("ICD9Proc", "ICD10PCS", "SNOMED", "CPT4", "HCPCS")
SNAPSHOT_LABELS = ("pilot-10-v1", "dev-30-v1", "eval-100-v1")
# (size in bytes, SHA-256) of each version's published manifest, pinned
# after its build; the current version's pin is PIN.
PINS = {
    "record-vocabulary-athena-20260831-v1": (
        2110, "1418474f04f0b6cd97acc1ab95cabcab"
              "833ea71f20803d53a38439cd11ac0f1e"),
    "record-vocabulary-athena-20260831-v2": (
        2335, "366bb8fb956f3ede7766b97e96abae60"
              "bd7644d9130ca7b132f5014f316c2d16"),
}
PIN = PINS[OUTPUT_VERSION]


class RecordVocabularyError(Exception):
    """A fail-closed build or load failure carrying a finding code only."""


def real_plan():
    """Return the pinned Athena inputs, the snapshots whose drug concepts
    are needed and the fixed protected output."""
    from ehr_evidence_app.evidence import readers
    plan = conditions.real_plan()
    plan["output_dir"] = OUTPUT_DIR
    plan["snapshots"] = [readers.real_plan(
        None if label == readers.PILOT_LABEL else label)
        for label in SNAPSHOT_LABELS]
    return plan


def pinned(version=OUTPUT_VERSION):
    """Return {path, size_bytes, sha256, version} of a version's published
    manifest, the current version's by default."""
    pin = PINS.get(version)
    if pin is None:
        raise RecordVocabularyError("record_vocabulary_not_pinned")
    return {"path": (PROTECTED_ROOT + "\\vocabularies\\" + version + "\\"
                     + MANIFEST_NAME),
            "size_bytes": pin[0], "sha256": pin[1], "version": version}


# --------------------------------------------------------------------------
# Row selection (pure)
# --------------------------------------------------------------------------

def concept_kind(row):
    """Return the kind of a concept row the vocabulary keeps, or None."""
    if row["invalid_reason"]:
        return None
    vocabulary = row["vocabulary_id"]
    if (vocabulary in DRUG_VOCABULARIES and row["domain_id"] == "Drug"
            and row["standard_concept"] == "S"
            and row["concept_class_id"] == "Ingredient"):
        return "ingredient"
    if vocabulary == "ATC":
        return "atc"
    if vocabulary == "RxNorm" and row["concept_class_id"] == "Brand Name":
        return "brand"
    if (vocabulary in PROCEDURE_VOCABULARIES
            and row["domain_id"] == "Procedure"):
        return "procedure"
    return None


def select_concepts(rows):
    """Return (drug lines, procedure lines, ids by kind)."""
    drugs, procedures = [], []
    ids = collections.defaultdict(set)
    clean = conditions.clean
    for row in rows:
        kind = concept_kind(row)
        if kind is None:
            continue
        concept_id = int(row["concept_id"])
        ids[kind].add(concept_id)
        if kind == "procedure":
            procedures.append((concept_id, clean(row["vocabulary_id"]),
                               clean(row["concept_code"]),
                               clean(row["concept_name"])))
        else:
            drugs.append((concept_id, kind, clean(row["vocabulary_id"]),
                          clean(row["concept_class_id"]),
                          clean(row["concept_code"]),
                          clean(row["concept_name"])))
    return drugs, procedures, ids


def select_synonyms(rows, drugs, procedures):
    """Return the English synonyms of the kept drug concepts, and the
    procedure lines extended by their English synonyms."""
    seen, lines, extra = set(), [], []
    known = {line[0]: line for line in procedures}
    for row in rows:
        if row["language_concept_id"] != conditions.ENGLISH:
            continue
        concept_id = int(row["concept_id"])
        synonym = conditions.clean(row["concept_synonym_name"])
        if not synonym or (concept_id, synonym) in seen:
            continue
        if concept_id in drugs:
            seen.add((concept_id, synonym))
            lines.append((concept_id, synonym))
        elif concept_id in known and synonym != known[concept_id][3]:
            seen.add((concept_id, synonym))
            line = known[concept_id]
            extra.append((concept_id, line[1], line[2], synonym))
    return lines, procedures + extra


def select_branded(rows, brands):
    """Return {brand id: branded concept ids} from "Brand name of"."""
    found = collections.defaultdict(set)
    for row in rows:
        if row["relationship_id"] != "Brand name of" or row["invalid_reason"]:
            continue
        brand = int(row["concept_id_1"])
        if brand in brands:
            found[brand].add(int(row["concept_id_2"]))
    return found


def select_ancestors(rows, wanted, ancestors, classes=frozenset(),
                     ingredients=frozenset()):
    """Return ({concept: ancestor ids} for the wanted concepts, keeping
    only ancestors in the given set; the (class, ingredient) pairs whose
    ancestor is one of the classes and whose descendant is one of the
    ingredients)."""
    found = collections.defaultdict(set)
    pairs = []
    for row in rows:
        descendant = int(row["descendant_concept_id"])
        ancestor = int(row["ancestor_concept_id"])
        if descendant in ingredients and ancestor in classes:
            pairs.append((ancestor, descendant))
        if descendant in wanted and ancestor in ancestors:
            found[descendant].add(ancestor)
    return found, pairs


def encode_tsv(name, lines):
    """Return UTF-8 tab-separated bytes with a header row."""
    out = io.StringIO()
    out.write("\t".join(COLUMNS[name]) + "\n")
    for line in sorted(set(lines)):
        out.write("\t".join(str(value) for value in line) + "\n")
    return out.getvalue().encode("utf-8")


def build_files(read, recorded):
    """Build every file; read(role, consume) streams an Athena input and
    recorded holds the drug concepts the snapshots record."""
    drugs, procedures, ids = read("concept", select_concepts)
    if not ids["ingredient"] or not ids["atc"] or not procedures:
        raise RecordVocabularyError("vocabulary_empty")
    drug_ids = ids["ingredient"] | ids["atc"] | ids["brand"]
    synonyms, procedures = read("synonym", lambda rows: select_synonyms(
        rows, drug_ids, procedures))
    branded = read("relationship", lambda rows: select_branded(
        rows, ids["brand"]))
    wanted = set(recorded) | {concept for concepts in branded.values()
                              for concept in concepts}
    found, classes = read("ancestor", lambda rows: select_ancestors(
        rows, wanted, ids["ingredient"] | ids["atc"], ids["atc"],
        ids["ingredient"]))
    ancestors = [(concept, ancestor) for concept in recorded
                 for ancestor in found.get(concept, ())]
    brands = [(brand, ingredient) for brand, concepts in branded.items()
              for concept in concepts for ingredient in found.get(concept, ())
              if ingredient in ids["ingredient"]]
    files = {FILES["drugs"]: encode_tsv("drugs", drugs),
             FILES["synonyms"]: encode_tsv("synonyms", synonyms),
             FILES["brands"]: encode_tsv("brands", brands),
             FILES["ancestors"]: encode_tsv("ancestors", ancestors),
             FILES["classes"]: encode_tsv("classes", classes),
             FILES["procedures"]: encode_tsv("procedures", procedures)}
    counts = {"ingredients": len(ids["ingredient"]), "atc_classes": len(
        ids["atc"]), "brand_names": len(ids["brand"]),
        "drug_synonyms": len(synonyms),
        "brands_with_ingredients": len({brand for brand, _ in brands}),
        "recorded_drug_concepts": len(recorded),
        "recorded_drug_concepts_with_ancestors": sum(
            1 for concept in recorded if found.get(concept)),
        "class_ingredient_pairs": len(set(classes)),
        "classes_with_ingredients": len({pair[0] for pair in classes}),
        "procedure_names": len(procedures)}
    return files, counts


def recorded_drugs(plan):
    """Return the drug concepts the pinned source snapshots record."""
    from ehr_evidence_app.evidence import readers
    found = set()
    for snapshot_plan in plan["snapshots"]:
        try:
            snapshot = readers.read_snapshot(snapshot_plan)
        except readers.ReaderError as error:
            raise RecordVocabularyError(str(error)) from None
        found.update(event.concept_id for event in snapshot.events["drug"]
                     if event.concept_id)
    return found


def manifest_document(plan, files, counts):
    """Return the manifest: inputs, counts and file digests."""
    return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
            "version": OUTPUT_VERSION, "release": conditions.RELEASE,
            "builder": BUILDER, "patient_bearing": False,
            "licence": "Athena vocabularies; private workspace only",
            "inputs": {role: {"name": os.path.basename(entry["path"]),
                              "size_bytes": entry["size_bytes"],
                              "sha256": entry["sha256"].lower()}
                       for role, entry in sorted(plan["inputs"].items())},
            "snapshots": [item["label"] for item in plan["snapshots"]],
            "counts": counts,
            "files": {name: {"size_bytes": len(data),
                             "sha256": hashlib.sha256(data).hexdigest()}
                      for name, data in sorted(files.items())}}


def run_real(plan=None):
    """Build the vocabulary from the pinned Athena release."""
    plan = plan or real_plan()
    if guard.fixed_policy_problem():
        raise RecordVocabularyError("fixed_policy_rejected")
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        if os.path.lexists(path):
            raise RecordVocabularyError("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise RecordVocabularyError(problem)
    recorded = recorded_drugs(plan)

    def read(role, consume):
        """Stream one pinned input through consume."""
        try:
            return conditions.read_pinned(plan["inputs"][role], role,
                                          consume, plan["source_dir"])
        except conditions.VocabularyError as error:
            raise RecordVocabularyError(str(error)) from None

    files, counts = build_files(read, recorded)
    manifest = manifest_document(plan, files, counts)
    files[MANIFEST_NAME] = (json.dumps(manifest, indent=2, sort_keys=True)
                            + "\n").encode("utf-8")
    try:
        digests = conditions.publish(plan, files)
    except conditions.VocabularyError as error:
        raise RecordVocabularyError(str(error)) from None
    return counts, digests


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class DrugConcept:
    """One ingredient, ATC class or brand name."""

    concept_id: int
    kind: str
    vocabulary_id: str
    concept_class_id: str
    code: str
    name: str


@dataclasses.dataclass(frozen=True)
class RecordVocabulary:
    """The loaded vocabulary: drug concepts by identifier, their names and
    synonyms, each brand's ingredients, each recorded drug concept's
    ingredients and classes, the procedure names and each ATC class's
    ingredients."""

    drugs: dict
    drug_names: dict
    brands: dict
    ancestors: dict
    procedure_names: tuple
    manifest_sha256: str = ""
    class_ingredients: dict = dataclasses.field(default_factory=dict)


def read_files(entry):
    """Return (the files of the published vocabulary, manifest SHA-256),
    every file verified against the pinned manifest."""
    held = []
    try:
        data, signature = mapping.read_verified(
            entry["path"], "record_vocabulary",
            "input_hash_mismatch:record_vocabulary", sha256=entry["sha256"],
            size=entry["size_bytes"])
        held.append((entry["path"], signature, "record_vocabulary"))
        manifest = json.loads(data.decode("utf-8"))
        directory = os.path.dirname(entry["path"])
        files = {}
        for name, item in manifest["files"].items():
            path = os.path.join(directory, name)
            with open(path, "rb") as handle:
                content = handle.read(item["size_bytes"] + 1)
            if (len(content) != item["size_bytes"]
                    or hashlib.sha256(content).hexdigest() != item["sha256"]):
                raise RecordVocabularyError("vocabulary_hash_mismatch")
            files[name] = content
        mapping.confirm_unchanged(held)
    except (mapping.MappingError, OSError, KeyError, ValueError):
        raise RecordVocabularyError("vocabulary_unreadable") from None
    return files, entry["sha256"]


def rows_of(files, name):
    """Return the rows of one published TSV file."""
    text = files[FILES[name]].decode("utf-8")
    return list(csv.DictReader(io.StringIO(text, newline=""),
                               delimiter="\t", quoting=csv.QUOTE_NONE))


def vocabulary_of(files, digest=""):
    """Return the RecordVocabulary of published files."""
    from ehr_evidence_app.qa import interpret
    drugs, names = {}, collections.defaultdict(set)
    for row in rows_of(files, "drugs"):
        concept = DrugConcept(int(row["concept_id"]), row["kind"],
                              row["vocabulary_id"], row["concept_class_id"],
                              row["concept_code"], row["concept_name"])
        drugs[concept.concept_id] = concept
        names[interpret.normalize(concept.name)].add(concept.concept_id)
    for row in rows_of(files, "synonyms"):
        names[interpret.normalize(row["synonym"])].add(int(row["concept_id"]))
    brands = collections.defaultdict(set)
    for row in rows_of(files, "brands"):
        brands[int(row["brand_id"])].add(int(row["ingredient_id"]))
    ancestors = collections.defaultdict(set)
    for row in rows_of(files, "ancestors"):
        ancestors[int(row["drug_concept_id"])].add(int(row["ancestor_id"]))
    procedures = tuple(sorted({interpret.normalize(row["name"])
                               for row in rows_of(files, "procedures")}))
    classes = collections.defaultdict(set)
    if FILES["classes"] in files:
        for row in rows_of(files, "classes"):
            classes[int(row["class_id"])].add(int(row["ingredient_id"]))
    names.pop("", None)
    return RecordVocabulary(drugs, dict(names), dict(brands),
                            dict(ancestors), procedures, digest,
                            {concept: frozenset(found)
                             for concept, found in classes.items()})


def load(entry=None):
    """Return the pinned RecordVocabulary."""
    files, digest = read_files(entry or pinned())
    return vocabulary_of(files, digest)


def drug_ancestors(entry=None):
    """Return ({recorded drug concept: ingredient and ATC ids}, manifest
    SHA-256) for the record bundle builder."""
    files, digest = read_files(entry or pinned())
    found = collections.defaultdict(set)
    for row in rows_of(files, "ancestors"):
        found[int(row["drug_concept_id"])].add(int(row["ancestor_id"]))
    return dict(found), digest


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

SYNTHETIC_TABLES = {
    "concept": [
        {"concept_id": "31", "concept_name": "Synthetic ingredient",
         "domain_id": "Drug", "vocabulary_id": "RxNorm",
         "concept_class_id": "Ingredient", "standard_concept": "S",
         "concept_code": "R31", "invalid_reason": ""},
        {"concept_id": "32", "concept_name": "SYNTHETIC CLASS",
         "domain_id": "Drug", "vocabulary_id": "ATC",
         "concept_class_id": "ATC 4th", "standard_concept": "C",
         "concept_code": "Z01AA", "invalid_reason": ""},
        {"concept_id": "33", "concept_name": "Synthobrand",
         "domain_id": "Drug", "vocabulary_id": "RxNorm",
         "concept_class_id": "Brand Name", "standard_concept": "",
         "concept_code": "R33", "invalid_reason": ""},
        {"concept_id": "34", "concept_name": "Synthetic ingredient 5 MG "
                                             "Oral Tablet [Synthobrand]",
         "domain_id": "Drug", "vocabulary_id": "RxNorm",
         "concept_class_id": "Branded Drug", "standard_concept": "S",
         "concept_code": "R34", "invalid_reason": ""},
        {"concept_id": "35", "concept_name": "Synthetic ingredient 5 MG "
                                             "Oral Tablet",
         "domain_id": "Drug", "vocabulary_id": "RxNorm",
         "concept_class_id": "Clinical Drug", "standard_concept": "S",
         "concept_code": "R35", "invalid_reason": ""},
        {"concept_id": "41", "concept_name": "Synthetic excision of organ",
         "domain_id": "Procedure", "vocabulary_id": "ICD10PCS",
         "concept_class_id": "ICD10PCS", "standard_concept": "S",
         "concept_code": "0Z0", "invalid_reason": ""},
        {"concept_id": "42", "concept_name": "Retired synthetic procedure",
         "domain_id": "Procedure", "vocabulary_id": "ICD10PCS",
         "concept_class_id": "ICD10PCS", "standard_concept": "",
         "concept_code": "0Z1", "invalid_reason": "D"},
    ],
    "synonym": [
        {"concept_id": "31", "concept_synonym_name": "Synthetic drug",
         "language_concept_id": conditions.ENGLISH},
        {"concept_id": "41", "concept_synonym_name": "Synthetic organ "
                                                     "removal",
         "language_concept_id": conditions.ENGLISH},
    ],
    "relationship": [
        {"concept_id_1": "33", "concept_id_2": "34",
         "relationship_id": "Brand name of", "valid_start_date": "",
         "valid_end_date": "", "invalid_reason": ""},
    ],
    "ancestor": [
        {"ancestor_concept_id": "31", "descendant_concept_id": "34",
         "min_levels_of_separation": "2", "max_levels_of_separation": "2"},
        {"ancestor_concept_id": "31", "descendant_concept_id": "35",
         "min_levels_of_separation": "1", "max_levels_of_separation": "1"},
        {"ancestor_concept_id": "32", "descendant_concept_id": "35",
         "min_levels_of_separation": "2", "max_levels_of_separation": "2"},
        {"ancestor_concept_id": "32", "descendant_concept_id": "31",
         "min_levels_of_separation": "1", "max_levels_of_separation": "1"},
    ],
}


def synthetic_bytes(role):
    """Return one invented Athena table as tab-separated bytes."""
    rows = SYNTHETIC_TABLES[role]
    header = list(rows[0])
    lines = ["\t".join(header)] + ["\t".join(row[key] for key in header)
                                   for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def run_synthetic():
    """Build an invented vocabulary in memory and load it; nothing is read
    or written. Return (files, counts, vocabulary)."""
    def read(role, consume):
        """Stream one invented table through consume."""
        return consume(conditions.tsv_rows(io.BytesIO(synthetic_bytes(role))))

    files, counts = build_files(read, {35})
    return files, counts, vocabulary_of(files)


def main(argv=None):
    """Run the synthetic default mode or the operator-only build."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="build the vocabulary from the pinned Athena "
                             "release; writes one new protected directory")
    args = parser.parse_args(argv)
    try:
        if args.operator_real:
            counts, digests = run_real()
        else:
            _, counts, loaded = run_synthetic()
            digests = {}
            expected = {35: {31, 32}}
            if (loaded.ancestors != expected or loaded.brands != {33: {31}}
                    or loaded.class_ingredients != {32: {31}}
                    or "synthetic drug" not in loaded.drug_names):
                print("PROBLEM: the invented vocabulary loads wrongly")
                print("RESULT: FAIL")
                return 1
        for key, value in sorted(counts.items()):
            print("%-40s %s" % (key, value))
        for name, value in sorted(digests.items()):
            print("%-40s %s" % (name, value))
    except RecordVocabularyError as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
