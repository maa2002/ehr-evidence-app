"""Synthetic checks for ehr_evidence_app.casesets.registry.

The committed registry is read; selection documents are invented inline,
and verified reads use files under the configured workspace test temp
directory only.
"""

import copy
import hashlib
import json
import os
import shutil
import tempfile
import unittest

from ehr_evidence_app.casesets import registry
from ehr_evidence_app.storage import policy as guard

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
LABEL = "dev-30-v1"


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def selection(label=LABEL, count=None):
    """Return an invented selection document for a registered case set."""
    value = registry.spec(label)
    size = value["size"] if count is None else count
    return {"schema": registry.SELECTION_SCHEMA, "case_set": label,
            "spec": copy.deepcopy(value),
            "cases": [{"case_number": number, "patient_index": 10 * number,
                       "graph_position": 10 * number + 1,
                       "subject_id": 500 + number}
                      for number in range(1, size + 1)]}


class ConfigTests(unittest.TestCase):
    """The committed registry mirrors the code exactly."""

    def test_committed_config_loads(self):
        """The file mirrors the fixed specifications."""
        self.assertIs(registry.load_config(), registry.CONFIG)

    def test_changed_config_is_refused(self):
        """A changed size, seed or rule, or non-JSON bytes, fail closed."""
        with open(registry.CONFIG_PATH, "rb") as handle:
            committed = json.loads(handle.read())
        for change in (lambda c: c["case_sets"][LABEL].update(size=31),
                       lambda c: c["case_sets"]["eval-100-v1"].update(
                           seed=1),
                       lambda c: c.update(draw_rule="first come"),
                       lambda c: c["case_sets"].pop(LABEL)):
            changed = copy.deepcopy(committed)
            change(changed)
            self.assertEqual(registry.config_problem(
                json.dumps(changed).encode("utf-8")),
                "case_set_config_mismatch")
        self.assertEqual(registry.config_problem(b"{"),
                         "case_set_config_not_json")
        with self.assertRaisesRegex(registry.RegistryError,
                                    "case_set_config_unreadable"):
            registry.load_config(os.path.join(REPO_ROOT, "config",
                                              "missing.json"))


class SpecTests(unittest.TestCase):
    """Specifications are fixed, well formed and disjoint."""

    def test_registered_specifications(self):
        """Both sets are valid; they come from different partitions."""
        for label, value in registry.CASE_SETS.items():
            self.assertIsNone(registry.spec_problem(label, value), label)
        self.assertEqual(
            {label: (value["purpose"], value["partition"], value["size"])
             for label, value in registry.CASE_SETS.items()},
            {"dev-30-v1": ("development", "validation", 30),
             "eval-100-v1": ("evaluation", "test", 100)})
        for value in registry.CASE_SETS.values():
            self.assertEqual(value["excluded_case_sets"], ["pilot-10-v1"])
            self.assertEqual(value["min_visit_nodes"], 2)

    def test_invalid_specifications(self):
        """Each rule refuses its violation."""
        value = registry.spec(LABEL)
        for label, changes, code in (
                ("Dev-30-v1", {}, "case_set_label_invalid"),
                ("dev-31-v1", {}, "case_set_label_size_mismatch"),
                ("dev-101-v1", {"size": 101}, "case_set_size_invalid"),
                (LABEL, {"purpose": "tuning"}, "case_set_purpose_invalid"),
                (LABEL, {"partition": "all"}, "case_set_partition_invalid"),
                (LABEL, {"min_visit_nodes": 1},
                 "case_set_min_visit_nodes_invalid"),
                (LABEL, {"seed": True}, "case_set_seed_invalid"),
                (LABEL, {"excluded_case_sets": "pilot-10-v1"},
                 "case_set_exclusions_invalid")):
            self.assertEqual(registry.spec_problem(label, dict(value,
                                                               **changes)),
                             code, label)
        self.assertEqual(registry.spec_problem(LABEL, {"size": 30}),
                         "case_set_spec_keys")

    def test_paths_and_pins(self):
        """Paths are fixed under the protected root; pins are well formed."""
        self.assertEqual(registry.selection_dir(LABEL),
                         registry.SELECTION_ROOT + "\\" + LABEL)
        self.assertEqual(registry.snapshot_dir(LABEL),
                         registry.SNAPSHOT_ROOT + "\\" + LABEL)
        for root in (registry.SELECTION_ROOT, registry.SNAPSHOT_ROOT):
            self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT, True))
        with self.assertRaisesRegex(registry.RegistryError,
                                    "case_set_unknown"):
            registry.spec("dev-31-v1")
        with self.assertRaisesRegex(registry.RegistryError, "stage_unknown"):
            registry.stage_path(LABEL, "report")
        self.assertEqual(registry.bundle_set(LABEL), LABEL + "-bundles-v2")
        self.assertTrue(registry.stage_path(LABEL, "bundles").startswith(
            registry.bundles_dir(LABEL)))
        for label, stages in registry.PINS.items():
            self.assertIn(label, registry.CASE_SETS)
            for stage, pin in stages.items():
                if pin is None:
                    with self.assertRaisesRegex(registry.RegistryError,
                                                "stage_not_pinned"):
                        registry.pinned(label, stage)
                    continue
                size, digest = pin
                self.assertTrue(type(size) is int and size > 0)
                self.assertRegex(digest, "^[0-9a-f]{64}$")
                self.assertEqual(registry.pinned(label, stage)["path"],
                                 registry.stage_path(label, stage))


class SelectionCaseTests(unittest.TestCase):
    """A selection record's cases are checked before use."""

    def test_valid_selection(self):
        """Cases come back numbered, ordered and typed."""
        cases = registry.selection_cases(selection(), LABEL)
        self.assertEqual(len(cases), 30)
        self.assertEqual(cases[0], (1, 10, 11, 501))

    def test_invalid_selections(self):
        """Wrong schema, set, spec, size, numbering or values fail."""
        def changed(change):
            """Return an invented selection after one change."""
            document = selection()
            change(document)
            return document

        for document, code in (
                (changed(lambda d: d.update(schema="other")),
                 "selection_unexpected"),
                (changed(lambda d: d.update(case_set="eval-100-v1")),
                 "selection_unexpected"),
                (changed(lambda d: d["spec"].update(seed=1)),
                 "selection_unexpected"),
                (selection(count=29), "selection_size_mismatch"),
                (changed(lambda d: d["cases"][0].update(case_number=2)),
                 "selection_case_invalid"),
                (changed(lambda d: d["cases"][0].update(subject_id=0)),
                 "selection_case_invalid"),
                (changed(lambda d: d["cases"][0].update(patient_index=True)),
                 "selection_case_invalid"),
                (changed(lambda d: d["cases"][0].update(patient_index=900)),
                 "selection_case_order"),
                (changed(lambda d: d["cases"][1].update(subject_id=501)),
                 "selection_subject_repeated"),
                ([], "selection_unexpected")):
            with self.assertRaisesRegex(registry.RegistryError, code):
                registry.selection_cases(document, LABEL)


class VerifiedReadTests(unittest.TestCase):
    """Pinned records are read once and verified."""

    def setUp(self):
        """Create a scratch directory under the workspace test temp."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="case-registry-", dir=root)
        self.addCleanup(self.remove_scratch)

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def entry(self, data):
        """Write bytes and return their pinned entry."""
        path = os.path.join(self.work, "selection.json")
        with open(path, "wb") as handle:
            handle.write(data)
        return {"path": path, "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def test_verified_read_and_selection(self):
        """Matching bytes are returned; the selection parses from them."""
        data = json.dumps(selection()).encode("utf-8")
        entry = self.entry(data)
        self.assertEqual(registry.read_verified(entry, "selection"), data)
        cases, reference = registry.read_selection(LABEL, entry)
        self.assertEqual((len(cases), reference["sha256"]),
                         (30, entry["sha256"]))

    def test_changed_or_missing_record_is_refused(self):
        """Other bytes, sizes or a missing file fail closed."""
        entry = self.entry(json.dumps(selection()).encode("utf-8"))
        for changed, code in (
                (dict(entry, sha256="0" * 64), "stage_hash_mismatch"),
                (dict(entry, size_bytes=entry["size_bytes"] - 1),
                 "stage_hash_mismatch"),
                (dict(entry, path=entry["path"] + ".missing"),
                 "stage_path_rejected"),
                (dict(entry, path="relative\\selection.json"),
                 "stage_path_rejected")):
            with self.assertRaisesRegex(registry.RegistryError, code):
                registry.read_verified(changed, "selection")
        broken = self.entry(b"{")
        with self.assertRaisesRegex(registry.RegistryError,
                                    "selection_not_json"):
            registry.read_selection(LABEL, broken)


if __name__ == "__main__":
    unittest.main()
