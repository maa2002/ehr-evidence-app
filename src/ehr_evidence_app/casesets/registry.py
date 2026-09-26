"""Case-set registry (work item 30.1).

A case set is a separately versioned group of cases prepared by the same
pipeline as the development pilot. Every case set is fixed here before
anything is drawn: its purpose, the upstream partition it is drawn from,
its size, the seed of its draw and its eligibility rule. The values are
mirrored exactly in config/case_sets.json; loading refuses a mismatch.

Each preparation stage publishes one new protected record per case set.
Once published, a record's size and SHA-256 are pinned here, and the next
stage reads it only through that pin.

Standard library only: the source extraction imports this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

from ehr_evidence_app.storage import policy as guard

REPO_ROOT = guard.REPO_ROOT
CONFIG_PATH = os.path.join(REPO_ROOT, "config", "case_sets.json")
PROTECTED_ROOT = guard.PRIVATE_ROOT + "\\protected-data"
SELECTION_ROOT = PROTECTED_ROOT + "\\case-sets"
SNAPSHOT_ROOT = PROTECTED_ROOT + "\\source-casesets"
PREPARED_ROOT = PROTECTED_ROOT + "\\case-preparation"
LINKAGE_ROOT = PROTECTED_ROOT + "\\evidence"
BUNDLES_ROOT = PROTECTED_ROOT + "\\evidence-bundles"
STAGES = ("selection", "snapshot_manifest", "prepared_mapping",
          "prepared_outputs", "prepared_closure", "linkage", "bundles")
# Version 1 bundle sets (schema 1.0) are kept under the bare case-set
# label; version 2 adds each record's source concept (schema 1.1).
BUNDLE_SET = "bundles-v2"
SELECTION_NAME = "selection.json"
SUMMARY_NAME = "summary.json"
SELECTION_SCHEMA = "case-set-selection"
CLOSURE_SCHEMA = "case-set-closure"
LABEL = re.compile(r"^[a-z][a-z0-9]*-[1-9][0-9]*-v[1-9][0-9]*$")
PARTITIONS = ("train", "validation", "test")

DRAW_RULE = ("Eligible cases are ranked by the SHA-256 of the text "
             "'<label>:<seed>:<patient_index>' and the first <size> are "
             "taken; case numbers follow ascending patient_index. Cases "
             "that later fail admission are reported, never replaced.")
ELIGIBILITY = (
    "the graph lies in the named upstream partition, by the split replay "
    "verified against the run-binding record",
    "the graph carries y_diagnosis (the bridge filter)",
    "the graph has a readable subject_id and at least min_visit_nodes "
    "visit nodes, each with a readable visit identifier",
    "the case does not belong to an excluded case set",
)

CASE_SETS = {
    "dev-30-v1": {
        "purpose": "development",
        "partition": "validation",
        "size": 30,
        "seed": 20260926,
        "min_visit_nodes": 2,
        "excluded_case_sets": ["pilot-10-v1"],
        "statement": ("Development case set: an engineering sample for "
                      "building and tuning the application. It is not an "
                      "evaluation cohort and not a clinical quality "
                      "target."),
    },
    "eval-100-v1": {
        "purpose": "evaluation",
        "partition": "test",
        "size": 100,
        "seed": 20260927,
        "min_visit_nodes": 2,
        "excluded_case_sets": ["pilot-10-v1"],
        "statement": ("Evaluation case set: drawn once from the upstream "
                      "test partition and held for the final evaluation; "
                      "never used for development or tuning. It is not a "
                      "clinical quality target."),
    },
}

CONFIG = {
    "schema_version": "1.0",
    "draw_rule": DRAW_RULE,
    "eligibility": list(ELIGIBILITY),
    "case_sets": CASE_SETS,
}

# (size in bytes, SHA-256) of each published stage record, pinned after
# its run; None until then.
PINS = {
    "dev-30-v1": {
        "selection": (6696, "593899ea2fe1a479aed7720ddcc5fa5a"
                            "87d2c61c677b015b985a72dac0b0f3fd"),
        "snapshot_manifest": (15148, "36a70b700ffc3911aec1f08dbdf18534"
                                     "c8330a9b43179327419cd3401e9411d8"),
        "prepared_mapping": (39266, "c0d6d45be4233a1ee73620171b088bfc"
                                    "47f5809f75f0232cdfcde3267e8a84e8"),
        "prepared_outputs": (140167, "4f10aab041ae2625af95755666217421"
                                     "921e7607b5784c69e8772308e99d7911"),
        "prepared_closure": (190439, "18069c8fceca664115e7420b0833604c"
                                     "ca45b72189ae8001b0baf66fa7376b2d"),
        "linkage": (374408, "f5134f7d5ce072702cd90236dfd5c1ae"
                            "9651ba1d976c03d30cec77a2e9a6ad40"),
        "bundles": (56044, "4adbd9c24cb3bbd053331e145fb6bd4b"
                           "8001c9b78f4944a38d90cf18a0dc696a"),
    },
    "eval-100-v1": {
        "selection": (17696, "f8d385dc3b2c69b1d024fc55e9f2e0ca"
                             "a2505aea00a53b3754f820b00632ad36"),
        "snapshot_manifest": (15245, "60b230af38ef98dd705e272ec180acc8"
                                     "4943f8027df50930bc8763f3b4c4d63d"),
        "prepared_mapping": (132128, "1d37eb18a09f564c583ceb94a8a74392"
                                     "884786c7d00a1c2a9a6c49f5551961be"),
        "prepared_outputs": (455945, "3dc38ab03aa9dd2ed0e64e398a972b15"
                                     "5c99391bf5a9019d2966d8ac29fd8a76"),
        "prepared_closure": (620928, "f3de5ac9b7ccd65ad8b2c49b7193d1e9"
                                     "0366ae6477c9e166856176d072fe41e9"),
        "linkage": (1359636, "a5923e0d656864701c127c7b12a814ea"
                             "147f33b35ad215b22d392bf692155185"),
        "bundles": (205608, "5c42897b003145d860c1ccd5ad61cd04"
                            "84f857b4c29be5e31e9e0e59d169b8f0"),
    },
}


class RegistryError(Exception):
    """A registry problem carrying a finding code only."""


def spec(label):
    """Return the fixed specification of one case set."""
    if label not in CASE_SETS:
        raise RegistryError("case_set_unknown")
    return CASE_SETS[label]


def selection_dir(label):
    """Return the protected directory of a case set's selection record."""
    spec(label)
    return SELECTION_ROOT + "\\" + label


def snapshot_dir(label):
    """Return the protected directory of a case set's source snapshot."""
    spec(label)
    return SNAPSHOT_ROOT + "\\" + label


def prepared_dir(label):
    """Return the protected directory of a case set's preparation record."""
    spec(label)
    return PREPARED_ROOT + "\\" + label


def linkage_dir(label):
    """Return the protected directory of a case set's linkage record."""
    spec(label)
    return LINKAGE_ROOT + "\\event-linkage-" + label


def bundle_set(label):
    """Return the version label of a case set's current bundle set."""
    spec(label)
    return label + "-" + BUNDLE_SET


def bundles_dir(label):
    """Return the protected directory of a case set's bundle set."""
    return BUNDLES_ROOT + "\\" + bundle_set(label)


def stage_path(label, stage):
    """Return the path of one stage record of a case set."""
    paths = {"selection": selection_dir(label) + "\\" + SELECTION_NAME,
             "snapshot_manifest": snapshot_dir(label) + "\\manifest.json",
             "prepared_mapping": prepared_dir(label) + "\\mapping.json",
             "prepared_outputs": prepared_dir(label) + "\\outputs.json",
             "prepared_closure": prepared_dir(label) + "\\closure.json",
             "linkage": linkage_dir(label) + "\\linkage.json",
             "bundles": bundles_dir(label) + "\\manifest.json"}
    if stage not in paths:
        raise RegistryError("stage_unknown")
    return paths[stage]


def pinned(label, stage):
    """Return {path, size_bytes, sha256} of a pinned stage record."""
    spec(label)
    pin = PINS.get(label, {}).get(stage)
    if pin is None:
        raise RegistryError("stage_not_pinned:%s" % stage)
    return {"path": stage_path(label, stage), "size_bytes": pin[0],
            "sha256": pin[1]}


def read_verified(entry, stage):
    """Read one pinned record once and verify its size and SHA-256.

    The path must be a plain drive path whose existing components are no
    links or reparse points, and the file must be unchanged across the read.
    """
    path = entry["path"]
    if (guard.path_problem(path) or guard.filesystem_problem(path, "file")
            or not os.path.isfile(path)):
        raise RegistryError("stage_path_rejected:%s" % stage)
    try:
        before = os.lstat(path)
        with open(path, "rb") as handle:
            data = handle.read(entry["size_bytes"] + 1)
        after = os.lstat(path)
    except OSError:
        raise RegistryError("stage_unreadable:%s" % stage) from None
    if ((before.st_size, before.st_mtime_ns)
            != (after.st_size, after.st_mtime_ns)
            or len(data) != entry["size_bytes"]
            or hashlib.sha256(data).hexdigest() != entry["sha256"].lower()):
        raise RegistryError("stage_hash_mismatch:%s" % stage)
    return data


def read_selection(label, entry=None):
    """Return (selection cases, record reference) through the pin."""
    entry = entry or pinned(label, "selection")
    data = read_verified(entry, "selection")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise RegistryError("selection_not_json") from None
    reference = {"path": entry["path"], "size_bytes": entry["size_bytes"],
                 "sha256": entry["sha256"].lower()}
    return selection_cases(document, label), reference


def config_problem(data):
    """Return a finding code when committed registry bytes differ."""
    try:
        config = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "case_set_config_not_json"
    if not isinstance(config, dict) or {
            key: config.get(key) for key in CONFIG} != CONFIG:
        return "case_set_config_mismatch"
    return None


def load_config(path=CONFIG_PATH):
    """Fail closed unless the committed registry mirrors the code."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        raise RegistryError("case_set_config_unreadable") from None
    problem = config_problem(data)
    if problem:
        raise RegistryError(problem)
    return CONFIG


def spec_problem(label, value):
    """Return a finding code when one specification is malformed."""
    if not LABEL.match(label):
        return "case_set_label_invalid"
    keys = {"purpose", "partition", "size", "seed", "min_visit_nodes",
            "excluded_case_sets", "statement"}
    if not isinstance(value, dict) or set(value) != keys:
        return "case_set_spec_keys"
    if value["purpose"] not in ("development", "evaluation"):
        return "case_set_purpose_invalid"
    if value["partition"] not in PARTITIONS:
        return "case_set_partition_invalid"
    for key, low in (("size", 1), ("seed", 0), ("min_visit_nodes", 2)):
        if type(value[key]) is not int or value[key] < low:
            return "case_set_%s_invalid" % key
    if value["size"] > 100:
        return "case_set_size_invalid"
    if str(value["size"]) != label.split("-")[1]:
        return "case_set_label_size_mismatch"
    if not isinstance(value["excluded_case_sets"], list):
        return "case_set_exclusions_invalid"
    return None


def selection_cases(document, label):
    """Return a selection record's cases after structural checks.

    Each case is (case_number, patient_index, graph_position, subject_id),
    numbered 1..size in ascending patient_index.
    """
    value = spec(label)
    if (not isinstance(document, dict)
            or document.get("schema") != SELECTION_SCHEMA
            or document.get("case_set") != label
            or document.get("spec") != value):
        raise RegistryError("selection_unexpected")
    cases = document.get("cases")
    if not isinstance(cases, list) or len(cases) != value["size"]:
        raise RegistryError("selection_size_mismatch")
    result = []
    for number, case in enumerate(cases, start=1):
        fields = (case.get("case_number"), case.get("patient_index"),
                  case.get("graph_position"), case.get("subject_id")) \
            if isinstance(case, dict) else (None,) * 4
        if (fields[0] != number
                or not all(type(item) is int and item >= 0
                           for item in fields[1:])
                or fields[3] < 1):
            raise RegistryError("selection_case_invalid")
        result.append(fields)
    indices = [item[1] for item in result]
    if indices != sorted(set(indices)):
        raise RegistryError("selection_case_order")
    if len({item[3] for item in result}) != len(result):
        raise RegistryError("selection_subject_repeated")
    return result
