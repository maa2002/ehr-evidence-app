#!/usr/bin/env python3
"""Condition resolution vocabulary (work item 32.1).

Question answering resolves condition terms against a vocabulary built
once from the Athena release that was loaded into the OMOP database. The
build reads four pinned Athena files from the cold archive, read-only and
once each, hashing exactly the bytes it parses, and publishes:

  concepts.tsv   every standard Condition concept, and every ICD-9-CM and
                 ICD-10-CM concept (for code lookup and descriptions);
  synonyms.tsv   the English synonyms of the standard Condition concepts;
  ancestors.tsv  every ancestor-descendant pair of standard Condition
                 concepts, self pairs included, with the minimum distance;
  icd_maps.tsv   valid "Maps to" links from ICD concepts to standard
                 Condition concepts;
  manifest.json  input pins, counts and the digest of every file.

The vocabulary holds no patient data. It is licensed content (SNOMED CT
through Athena), so it lives in the private workspace and never in Git.

Usage:
    python -m ehr_evidence_app.qa.vocabulary
    python -m ehr_evidence_app.qa.vocabulary --operator-real
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys

import ehr_evidence_app
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.storage import policy as guard

SCHEMA = "condition-vocabulary"
SCHEMA_VERSION = "1.0"
RELEASE = "athena-20260831"
OUTPUT_VERSION = "condition-vocabulary-athena-20260831-v1"
ATHENA_ROOT = guard.VOCABULARY_SOURCE_DIR
PROTECTED_ROOT = guard.PRIVATE_ROOT + "\\protected-data"
OUTPUT_DIR = PROTECTED_ROOT + "\\vocabularies\\" + OUTPUT_VERSION
MANIFEST_NAME = "manifest.json"
FILES = {"concepts": "concepts.tsv", "synonyms": "synonyms.tsv",
         "ancestors": "ancestors.tsv", "icd_maps": "icd_maps.tsv"}
COLUMNS = {
    "concepts": ("concept_id", "kind", "vocabulary_id", "concept_class_id",
                 "concept_code", "invalid_reason", "concept_name"),
    "synonyms": ("concept_id", "synonym"),
    "ancestors": ("ancestor_id", "descendant_id", "min_levels"),
    "icd_maps": ("icd_id", "standard_id"),
}
ENGLISH = "4180186"
ICD_VOCABULARIES = ("ICD9CM", "ICD10CM")
PARTIAL = ".partial"
CHUNK_BYTES = 8 * 1024 * 1024
BUILDER = "ehr_evidence_app.qa.vocabulary %s" % ehr_evidence_app.__version__
INPUTS = {
    "concept": ("CONCEPT.csv", 773851657,
                "07b70224d31257506e2135eac44bc6f3"
                "82599159385dbd94c2306fa172382534"),
    "synonym": ("CONCEPT_SYNONYM.csv", 223037685,
                "a645ef48fa10209c37a18df6b4e4a7ac"
                "adb6b3f0ebdef196b307625dab610801"),
    "ancestor": ("CONCEPT_ANCESTOR.csv", 902957914,
                 "ddc34a5ff81596d877e0803faff565f2"
                 "3489807dd547d2fc3249119e4d6da9ce"),
    "relationship": ("CONCEPT_RELATIONSHIP.csv", 1981228195,
                     "949325ad09670807db27515fdc3d2fbe"
                     "f119de4a38d79cd7f5771c066960eeef"),
}


class VocabularyError(Exception):
    """A fail-closed build or load failure carrying a finding code only."""


def real_plan():
    """Return the pinned Athena inputs and the fixed protected output."""
    return {
        "source_dir": ATHENA_ROOT,
        "inputs": {role: {"path": ATHENA_ROOT + "\\" + name,
                          "size_bytes": size, "sha256": digest}
                   for role, (name, size, digest) in INPUTS.items()},
        "output_dir": OUTPUT_DIR,
        "protected_root": PROTECTED_ROOT,
    }


# Verified Single-Pass Reading

class DigestingReader(io.RawIOBase):
    """A raw stream that hashes and counts every byte it hands on."""

    def __init__(self, handle):
        """Wrap an open binary handle."""
        super().__init__()
        self.handle = handle
        self.digest = hashlib.sha256()
        self.size = 0

    def readable(self):
        """Report the stream as readable."""
        return True

    def readinto(self, buffer):
        """Read into buffer, hashing the bytes read."""
        data = self.handle.read(len(buffer))
        self.digest.update(data)
        self.size += len(data)
        buffer[:len(data)] = data
        return len(data)


def tsv_rows(stream):
    """Yield the rows of an Athena tab-separated file as dictionaries."""
    text = io.TextIOWrapper(io.BufferedReader(stream, CHUNK_BYTES),
                            encoding="utf-8", newline="")
    yield from csv.DictReader(text, delimiter="\t", quoting=csv.QUOTE_NONE)


def read_pinned(entry, role, consume, directory):
    """Stream one pinned input once through consume; verify its identity.

    The file must pass the vocabulary source check for directory. consume
    receives the row iterator; the digest and size are checked over
    exactly the bytes it read, which must be the whole file.
    """
    if guard.vocabulary_source_problem(entry["path"], directory):
        raise VocabularyError("input_path_rejected:%s" % role)
    try:
        before = os.lstat(entry["path"])
        with open(entry["path"], "rb") as handle:
            stream = DigestingReader(handle)
            result = consume(tsv_rows(stream))
            while stream.readinto(bytearray(CHUNK_BYTES)):
                pass
        after = os.lstat(entry["path"])
    except (OSError, UnicodeDecodeError, csv.Error, KeyError, ValueError):
        raise VocabularyError("input_unreadable:%s" % role) from None
    if ((before.st_size, before.st_mtime_ns)
            != (after.st_size, after.st_mtime_ns)):
        raise VocabularyError("input_changed_during_read:%s" % role)
    if (stream.size != entry["size_bytes"]
            or stream.digest.hexdigest() != entry["sha256"].lower()):
        raise VocabularyError("input_hash_mismatch:%s" % role)
    return result


def clean(value):
    """Return a field value safe for one tab-separated cell."""
    return " ".join((value or "").split())


# Row Selection (Pure)

def select_concepts(rows):
    """Return (standard condition ids, ICD ids, concept lines)."""
    standard, icd, lines = set(), set(), []
    for row in rows:
        vocabulary = row["vocabulary_id"]
        if row["domain_id"] == "Condition" and row["standard_concept"] == "S":
            kind = "standard"
        elif vocabulary in ICD_VOCABULARIES:
            kind = "icd"
        else:
            continue
        concept_id = int(row["concept_id"])
        (standard if kind == "standard" else icd).add(concept_id)
        lines.append((concept_id, kind, vocabulary,
                      clean(row["concept_class_id"]),
                      clean(row["concept_code"]),
                      clean(row["invalid_reason"]),
                      clean(row["concept_name"])))
    return standard, icd, lines


def select_synonyms(rows, standard):
    """Return the unique English synonyms of standard condition concepts."""
    seen, lines = set(), []
    for row in rows:
        concept_id = int(row["concept_id"])
        if concept_id in standard and row["language_concept_id"] == ENGLISH:
            key = (concept_id, clean(row["concept_synonym_name"]))
            if key[1] and key not in seen:
                seen.add(key)
                lines.append(key)
    return lines


def select_ancestors(rows, standard):
    """Return ancestor pairs whose ends are both standard conditions."""
    lines = []
    for row in rows:
        ancestor = int(row["ancestor_concept_id"])
        descendant = int(row["descendant_concept_id"])
        if ancestor in standard and descendant in standard:
            lines.append((ancestor, descendant,
                          int(row["min_levels_of_separation"])))
    return lines


def select_icd_maps(rows, icd, standard):
    """Return valid "Maps to" links from ICD to standard conditions."""
    seen, lines = set(), []
    for row in rows:
        if row["relationship_id"] != "Maps to" or row["invalid_reason"]:
            continue
        source = int(row["concept_id_1"])
        target = int(row["concept_id_2"])
        if source in icd and target in standard and (source, target) \
                not in seen:
            seen.add((source, target))
            lines.append((source, target))
    return lines


def encode_tsv(name, lines):
    """Return UTF-8 tab-separated bytes with a header row."""
    out = io.StringIO()
    out.write("\t".join(COLUMNS[name]) + "\n")
    for line in sorted(lines):
        out.write("\t".join(str(value) for value in line) + "\n")
    return out.getvalue().encode("utf-8")


def build_files(read):
    """Build every vocabulary file; read(role, consume) streams an input."""
    standard, icd, concepts = read("concept", select_concepts)
    if not standard or not icd:
        raise VocabularyError("vocabulary_empty")
    synonyms = read("synonym", lambda rows: select_synonyms(rows, standard))
    ancestors = read("ancestor",
                     lambda rows: select_ancestors(rows, standard))
    icd_maps = read("relationship",
                    lambda rows: select_icd_maps(rows, icd, standard))
    files = {FILES["concepts"]: encode_tsv("concepts", concepts),
             FILES["synonyms"]: encode_tsv("synonyms", synonyms),
             FILES["ancestors"]: encode_tsv("ancestors", ancestors),
             FILES["icd_maps"]: encode_tsv("icd_maps", icd_maps)}
    counts = {"standard_conditions": len(standard), "icd_concepts": len(icd),
              "synonyms": len(synonyms), "ancestor_pairs": len(ancestors),
              "icd_maps": len(icd_maps)}
    return files, counts


def manifest_document(plan, files, counts):
    """Return the manifest: inputs, counts and file digests."""
    return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
            "version": OUTPUT_VERSION, "release": RELEASE,
            "builder": BUILDER, "patient_bearing": False,
            "licence": "Athena vocabularies, including SNOMED CT; private "
                       "workspace only",
            "inputs": {role: {"name": os.path.basename(entry["path"]),
                              "size_bytes": entry["size_bytes"],
                              "sha256": entry["sha256"].lower()}
                       for role, entry in sorted(plan["inputs"].items())},
            "counts": counts,
            "files": {name: {"size_bytes": len(data),
                             "sha256": hashlib.sha256(data).hexdigest()}
                      for name, data in sorted(files.items())}}


# Publication

def output_preconditions(plan):
    """Refuse an existing or unprotected output."""
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        if os.path.lexists(path):
            raise VocabularyError("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise VocabularyError(problem)


def publish(plan, files):
    """Write every file through a new .partial directory and rename it."""
    partial = plan["output_dir"] + PARTIAL
    os.makedirs(os.path.dirname(plan["output_dir"]), exist_ok=True)
    os.mkdir(partial)
    try:
        for name, data in sorted(files.items()):
            with open(os.path.join(partial, name), "xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            os.rename(partial, plan["output_dir"])
        except OSError:
            raise VocabularyError("output_rename_failed") from None
    except BaseException:
        for name in files:
            target = os.path.join(partial, name)
            if os.path.lexists(target):
                os.remove(target)
        if os.path.isdir(partial):
            os.rmdir(partial)
        raise
    return {name: hashlib.sha256(data).hexdigest()
            for name, data in sorted(files.items())}


def run_real(plan=None):
    """Build the vocabulary from the pinned Athena release."""
    plan = plan or real_plan()
    if guard.fixed_policy_problem():
        raise VocabularyError("fixed_policy_rejected")
    output_preconditions(plan)

    def read(role, consume):
        """Stream one pinned input through consume."""
        return read_pinned(plan["inputs"][role], role, consume,
                           plan["source_dir"])

    files, counts = build_files(read)
    manifest = manifest_document(plan, files, counts)
    files[MANIFEST_NAME] = (json.dumps(manifest, indent=2, sort_keys=True)
                            + "\n").encode("utf-8")
    return counts, publish(plan, files)


# Synthetic Default Mode (Invented Fixtures Only)

SYNTHETIC_TABLES = {
    "concept": [
        {"concept_id": "10", "concept_name": "Synthetic disorder",
         "domain_id": "Condition", "vocabulary_id": "SNOMED",
         "concept_class_id": "Disorder", "standard_concept": "S",
         "concept_code": "S10", "invalid_reason": ""},
        {"concept_id": "11", "concept_name": "Synthetic heart failure",
         "domain_id": "Condition", "vocabulary_id": "SNOMED",
         "concept_class_id": "Disorder", "standard_concept": "S",
         "concept_code": "S11", "invalid_reason": ""},
        {"concept_id": "12", "concept_name": "Synthetic drug",
         "domain_id": "Drug", "vocabulary_id": "RxNorm",
         "concept_class_id": "Ingredient", "standard_concept": "S",
         "concept_code": "R12", "invalid_reason": ""},
        {"concept_id": "20", "concept_name": "Synthetic ICD heart failure",
         "domain_id": "Condition", "vocabulary_id": "ICD10CM",
         "concept_class_id": "4-char billing code", "standard_concept": "",
         "concept_code": "Q50.9", "invalid_reason": ""},
    ],
    "synonym": [
        {"concept_id": "11", "concept_synonym_name": "Synthetic cardiac failure",
         "language_concept_id": ENGLISH},
        {"concept_id": "11", "concept_synonym_name": "Synthetic HF",
         "language_concept_id": "4182511"},
        {"concept_id": "12", "concept_synonym_name": "Synthetic tablet",
         "language_concept_id": ENGLISH},
    ],
    "ancestor": [
        {"ancestor_concept_id": "10", "descendant_concept_id": "10",
         "min_levels_of_separation": "0", "max_levels_of_separation": "0"},
        {"ancestor_concept_id": "10", "descendant_concept_id": "11",
         "min_levels_of_separation": "1", "max_levels_of_separation": "1"},
        {"ancestor_concept_id": "11", "descendant_concept_id": "11",
         "min_levels_of_separation": "0", "max_levels_of_separation": "0"},
        {"ancestor_concept_id": "10", "descendant_concept_id": "12",
         "min_levels_of_separation": "1", "max_levels_of_separation": "1"},
    ],
    "relationship": [
        {"concept_id_1": "20", "concept_id_2": "11",
         "relationship_id": "Maps to", "valid_start_date": "",
         "valid_end_date": "", "invalid_reason": ""},
        {"concept_id_1": "20", "concept_id_2": "10",
         "relationship_id": "Maps to", "valid_start_date": "",
         "valid_end_date": "", "invalid_reason": "D"},
        {"concept_id_1": "20", "concept_id_2": "10",
         "relationship_id": "Is a", "valid_start_date": "",
         "valid_end_date": "", "invalid_reason": ""},
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
    """Build an invented vocabulary in memory; nothing is read or written."""
    def read(role, consume):
        """Stream one invented table through consume."""
        return consume(tsv_rows(io.BytesIO(synthetic_bytes(role))))

    return build_files(read)


def report(counts, digests=None):
    """Print aggregate counts and file digests only."""
    for key, value in sorted(counts.items()):
        print("%-24s %s" % (key, value))
    for name, value in sorted((digests or {}).items()):
        print("%-24s %s" % (name, value))


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
            report(counts, digests)
        else:
            _, counts = run_synthetic()
            report(counts)
    except VocabularyError as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
