"""Synthetic checks for ehr_evidence_app.casesets.selection.

Graph metadata, run-binding summaries and case-set specifications are
invented; the scratch run replaces the graph loader with invented in-memory
graphs, so torch is never imported. Scratch files are created only under
the configured workspace test temp directory. No real record is read.
"""

import copy
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from ehr_evidence_app.casesets import registry, selection
from ehr_evidence_app.gnn import binding, mapping
from ehr_evidence_app.gnn import provenance as amp
from ehr_evidence_app.storage import policy as guard

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
LABEL = "synthetic-20-v1"
SPEC = dict(selection.SYNTHETIC_SPEC, excluded_case_sets=[])


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def binding_summary(metadata, **changes):
    """Return an invented verified run-binding summary for metadata."""
    facts = selection.synthetic_facts(metadata)
    body = {"schema": binding.SCHEMA, "status": binding.VERIFIED,
            "checks": {"split_replay_matches_upstream": True,
                       "pos_weight_bitwise_equal": True},
            "run_arguments": {"seed": facts["seed"],
                              "val_frac": facts["val_frac"],
                              "test_frac": facts["test_frac"]},
            "split": {"graphs_filtered": facts["graphs_filtered"],
                      "partition_sha256": facts["partition_sha256"],
                      "counts": facts["counts"]}}
    body.update(changes)
    return dict(body, preparation={"fingerprint_sha256":
                                   binding.digest(body)})


class FakeVisit:
    """Invented visit node store."""

    def __init__(self, hadm_ids):
        """Keep the invented visit identifiers."""
        self.hadm_ids = list(hadm_ids)
        self.num_nodes = len(self.hadm_ids)


class FakeGraph:
    """Invented patient graph exposing only the metadata the rule reads."""

    def __init__(self, item):
        """Build the graph from one invented metadata item."""
        self.subject_id = item["subject_id"]
        self.visit = FakeVisit(item["hadm_ids"])
        if item["has_y_diagnosis"]:
            self.y_diagnosis = [1]

    def __getitem__(self, key):
        """Return the visit store, as a heterogeneous graph would."""
        if key != "visit":
            raise KeyError(key)
        return self.visit


class CoreTests(unittest.TestCase):
    """Split facts, partitions, eligibility and the seeded draw."""

    def setUp(self):
        """Take invented metadata and matching split facts."""
        self.metadata = selection.synthetic_metadata()
        self.facts = selection.synthetic_facts(self.metadata)
        self.partitions = selection.verified_partitions(
            self.facts["graphs_filtered"], self.facts)

    def test_split_facts(self):
        """A verified summary yields its split; anything else fails."""
        facts = selection.split_facts(binding_summary(self.metadata))
        self.assertEqual(facts, self.facts)
        summary = binding_summary(self.metadata)
        summary["status"] = "failed"
        for document, code in (
                (summary, "binding_not_verified"),
                (binding_summary(self.metadata, checks={
                    "split_replay_matches_upstream": False}),
                 "binding_not_verified"),
                (dict(binding_summary(self.metadata), split={}),
                 "binding_fingerprint_mismatch"),
                (binding_summary(self.metadata, split={}),
                 "binding_split_unexpected"),
                ([], "binding_not_verified")):
            with self.assertRaisesRegex(selection.SelectionError, code):
                selection.split_facts(document)

    def test_partitions_are_verified(self):
        """The replay must reproduce every recorded digest and count."""
        sizes = {name: len(items) for name, items in self.partitions.items()}
        self.assertEqual(sizes, self.facts["counts"])
        tampered = copy.deepcopy(self.facts)
        tampered["partition_sha256"]["test"] = "0" * 64
        with self.assertRaisesRegex(selection.SelectionError,
                                    "partition_digest_mismatch"):
            selection.verified_partitions(self.facts["graphs_filtered"],
                                          tampered)
        with self.assertRaisesRegex(selection.SelectionError,
                                    "graph_count_mismatch"):
            selection.verified_partitions(self.facts["graphs_filtered"] - 1,
                                          self.facts)

    def test_skip_reasons(self):
        """Exclusion, unreadable subjects or visits and short histories."""
        item = {"subject_id": 5, "hadm_ids": [1, 2], "visit_node_count": 2}
        reason = selection.skip_reason
        self.assertIsNone(reason(item, 1, set(), 2))
        self.assertEqual(reason(item, 1, {1}, 2), "excluded_case_set")
        for changes, code in (
                ({"subject_id": None}, "subject_unreadable"),
                ({"hadm_ids": None}, "visit_identifiers_unreadable"),
                ({"visit_node_count": 3}, "visit_identifiers_unreadable"),
                ({"hadm_ids": [1, "x"]}, "visit_identifiers_unreadable"),
                ({"hadm_ids": [1], "visit_node_count": 1},
                 "too_few_visit_nodes")):
            self.assertEqual(reason(dict(item, **changes), 1, set(), 2),
                             code)

    def test_rank_depends_on_label_seed_and_index(self):
        """The rank key is a fixed digest of its three parts."""
        self.assertEqual(selection.rank("a-1-v1", 7, 3), hashlib.sha256(
            b"a-1-v1:7:3").hexdigest())
        self.assertEqual(len({selection.rank("a-1-v1", 7, 3),
                              selection.rank("a-1-v2", 7, 3),
                              selection.rank("a-1-v1", 8, 3),
                              selection.rank("a-1-v1", 7, 4)}), 4)

    def test_draw(self):
        """The draw is deterministic, eligible, ordered and excluding."""
        excluded = set(self.partitions["test"][:3])
        cases, counts = selection.draw(LABEL, SPEC, self.metadata,
                                       self.partitions, excluded)
        again, _ = selection.draw(LABEL, SPEC, self.metadata,
                                  self.partitions, excluded)
        self.assertEqual(cases, again)
        self.assertEqual(counts["selected"], 20)
        self.assertEqual(counts["partition_size"],
                         len(self.partitions["test"]))
        self.assertEqual(counts["eligible"] + sum(counts["skipped"].values()),
                         counts["partition_size"])
        indices = [case["patient_index"] for case in cases]
        self.assertEqual(indices, sorted(indices))
        self.assertTrue(set(indices) <= set(self.partitions["test"]))
        self.assertFalse(set(indices) & excluded)
        positions = mapping.bridge_positions(self.metadata)
        for number, case in enumerate(cases, start=1):
            self.assertEqual(case["case_number"], number)
            self.assertEqual(positions[case["graph_position"]],
                             case["patient_index"])
            item = self.metadata[case["graph_position"]]
            self.assertEqual(case["subject_id"], item["subject_id"])
            self.assertGreaterEqual(case["visit_node_count"], 2)
        other, _ = selection.draw(LABEL, dict(SPEC, seed=8), self.metadata,
                                  self.partitions, excluded)
        self.assertNotEqual(other, cases)
        with self.assertRaisesRegex(selection.SelectionError,
                                    "too_few_eligible_cases"):
            selection.draw(LABEL, dict(SPEC, size=100), self.metadata,
                           self.partitions, set())

    def test_records(self):
        """The record is fingerprinted; the summary is aggregate only."""
        document, summary = selection.run_synthetic()
        body = {key: value for key, value in document.items()
                if key != "preparation"}
        self.assertEqual(document["preparation"]["fingerprint_sha256"],
                         amp.digest(body))
        self.assertEqual(summary["selection_file"]["sha256"],
                         hashlib.sha256(binding.encode(document)).hexdigest())
        text = binding.encode(summary).decode("utf-8")
        for key in ("subject_id", "patient_index", "graph_position"):
            self.assertNotIn(key, text)
        self.assertEqual(summary["visit_nodes"]["min"], 2)

    def test_commands(self):
        """The default mode passes; the real flags go together."""
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(selection.main([]), 0)
            self.assertEqual(selection.main(["--operator-real"]), 2)
            self.assertEqual(selection.main(["--case-set", "dev-30-v1"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())


class ScratchRunTests(unittest.TestCase):
    """Real-mode orchestration over invented scratch inputs."""

    def setUp(self):
        """Write the invented inputs into a workspace scratch tree."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        if guard.fixed_policy_problem():
            self.skipTest("the fixed layout policy does not hold here")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="case-selection-", dir=root)
        self.addCleanup(self.remove_scratch)
        protected = os.path.join(self.work, "protected")
        inputs = os.path.join(protected, "inputs")
        os.makedirs(inputs)
        self.metadata = selection.synthetic_metadata()
        self.graphs = [FakeGraph(item) for item in self.metadata]
        self.plan = {
            "label": LABEL,
            "graph": self.write(os.path.join(inputs, "graphs.pt"),
                                b"invented graph bytes"),
            "binding_summary": self.write(
                os.path.join(inputs, "binding-summary.json"),
                binding.encode(binding_summary(self.metadata))),
            "readers": {"closure": {"path": os.path.join(
                inputs, "closure.json")}},
            "output_dir": os.path.join(protected, "case-sets", LABEL),
            "protected_root": protected}
        for patcher in (mock.patch.dict(registry.CASE_SETS, {LABEL: SPEC}),
                        mock.patch.object(registry, "load_config",
                                          lambda: None)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def write(self, path, data):
        """Write bytes and return their pinned entry."""
        with open(path, "wb") as handle:
            handle.write(data)
        return {"path": path, "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def run_real(self):
        """Run the real mode with the invented graph loader."""
        return selection.run_real(LABEL, self.plan,
                                  loader=lambda path: self.graphs)

    def test_publishes_selection_once(self):
        """Both files are published; the record verifies; a rerun refuses."""
        summary, digests = self.run_real()
        out = self.plan["output_dir"]
        self.assertEqual(sorted(os.listdir(out)),
                         [registry.SELECTION_NAME, registry.SUMMARY_NAME])
        for name, value in digests.items():
            self.assertEqual(mapping.sha256_file(os.path.join(out, name)),
                             value)
        with open(os.path.join(out, registry.SELECTION_NAME), "rb") as handle:
            data = handle.read()
        self.assertEqual(summary["selection_file"]["sha256"],
                         hashlib.sha256(data).hexdigest())
        entry = {"path": os.path.join(out, registry.SELECTION_NAME),
                 "size_bytes": len(data),
                 "sha256": hashlib.sha256(data).hexdigest()}
        cases, _ = registry.read_selection(LABEL, entry)
        self.assertEqual(len(cases), SPEC["size"])
        with self.assertRaisesRegex(selection.SelectionError,
                                    "output_exists"):
            self.run_real()

    def test_changed_or_inconsistent_inputs_publish_nothing(self):
        """A changed summary or a mismatched graph count fails closed."""
        with open(self.plan["binding_summary"]["path"], "ab") as handle:
            handle.write(b" ")
        with self.assertRaisesRegex(selection.SelectionError,
                                    "input_hash_mismatch:binding_summary"):
            self.run_real()
        self.plan["binding_summary"] = self.write(
            self.plan["binding_summary"]["path"],
            binding.encode(binding_summary(self.metadata)))
        self.graphs = self.graphs[:-1]
        with self.assertRaisesRegex(selection.SelectionError,
                                    "graph_count_mismatch"):
            self.run_real()
        for path in (self.plan["output_dir"],
                     self.plan["output_dir"] + selection.PARTIAL):
            self.assertFalse(os.path.lexists(path))


if __name__ == "__main__":
    unittest.main()
