"""Synthetic checks for ehr_evidence_app.gnn.mapping (Step 17).

Every fixture is invented: fake graph objects stand in for the pinned graph
list and inline CSVs stand in for the source pilot. The loader is a stub, so
torch is never imported and no real input, graph, source-pilot file or
patient record is opened. Scratch directories are created only under the
configured workspace test temp directory.
"""

import csv
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.gnn import mapping as mapping  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402

PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
TOKENS = ("700001", "700002", "8100001", "8100002", "900001", "900002",
          "900003")
PRODUCER_SHA = "ab" * 32

PERSONS = [{"person_id": "700001", "subject_id": "8100001"},
           {"person_id": "700002", "subject_id": "8100002"}]
VISITS = [{"visit_occurrence_id": "900001", "person_id": "700001"},
          {"visit_occurrence_id": "900002", "person_id": "700001"},
          {"visit_occurrence_id": "900003", "person_id": "700002"}]


class FakeGraph:
    """Minimal stand-in for a stored patient graph."""

    def __init__(self, subject_id, hadm_ids, diagnosis=True, nodes=None):
        """Store a subject, optional y_diagnosis and a visit node store."""
        self.subject_id = subject_id
        if diagnosis:
            self.y_diagnosis = [1]
        count = len(hadm_ids) if nodes is None else nodes
        self.visit = types.SimpleNamespace(num_nodes=count,
                                           hadm_ids=list(hadm_ids))

    def __getitem__(self, key):
        """Return the node store for a node type."""
        return getattr(self, key)


def base_graphs():
    """Return a graph list whose first graph is dropped by the bridge."""
    return [FakeGraph(8100099, [999999], diagnosis=False),
            FakeGraph(8100002, [900003]),
            FakeGraph(8100001, [900001, 900002])]


def configured_test_temp():
    """Return the configured workspace test scratch directory (workspace-only rules)."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def sha(path):
    """Return the SHA-256 of a scratch file."""
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def write_csv(path, columns, rows):
    """Write synthetic rows as a CSV file."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


class MappingTestCase(unittest.TestCase):
    """Crosswalk rules, input identity checks and protected output."""

    def setUp(self):
        """Create synthetic pinned inputs and a scratch protected root."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT, True),
                        "test scratch space must stay in the workspace root")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="step-17-", dir=root)
        self.addCleanup(self.remove_scratch)
        self.loads = 0
        self.graphs = base_graphs()

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def loader(self, path):
        """Count deserialisations and return the fake graph list."""
        self.loads += 1
        return self.graphs

    def build_plan(self, persons=PERSONS, visits=VISITS, output="out"):
        """Write the synthetic inputs and return a plan pinned to them."""
        files = {}
        for name, data in (("graph", b"synthetic graph bytes"),
                           ("builder", b"# synthetic builder\n"),
                           ("bridge", b"# synthetic bridge\n"),
                           ("pilot_reader", b"# synthetic reader\n")):
            path = os.path.join(self.work, name + ".bin")
            with open(path, "wb") as handle:
                handle.write(data)
            files[name] = path
        config = {"tables": {"person": {"file": "persons.csv"},
                             "visit_occurrence": {"file": "visits.csv"}}}
        files["pilot_config"] = os.path.join(self.work, "pilot_config.json")
        with open(files["pilot_config"], "w", encoding="utf-8") as handle:
            json.dump(config, handle)
        pilot_dir = os.path.join(self.work, "pilot")
        os.makedirs(pilot_dir, exist_ok=True)
        write_csv(os.path.join(pilot_dir, "persons.csv"),
                  ["person_id", "subject_id"], persons)
        write_csv(os.path.join(pilot_dir, "visits.csv"),
                  ["visit_occurrence_id", "person_id", "visit_concept_id"],
                  [dict(row, visit_concept_id="9201") for row in visits])
        manifest = {
            "status": "complete", "run_state": "finished", "promoted": True,
            "version_label": "synthetic-v1",
            "configuration": {"sha256": sha(files["pilot_config"])},
            "extraction_script": {"version": "0.3.2",
                                  "sha256": PRODUCER_SHA},
            "selection": {"cases_requested": len(persons)},
            "coverage": {"statement": "synthetic"}, "limitations": [],
            "files": [{"file": name, "present": True,
                       "size_bytes": os.path.getsize(
                           os.path.join(pilot_dir, name)),
                       "sha256": sha(os.path.join(pilot_dir, name))}
                      for name in ("persons.csv", "visits.csv")]}
        files["pilot_manifest"] = os.path.join(pilot_dir, "manifest.json")
        with open(files["pilot_manifest"], "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        plan = {name: {"path": path, "sha256": sha(path).upper()}
                for name, path in files.items()}
        plan["graph"]["size_bytes"] = os.path.getsize(files["graph"])
        plan["pilot_producer"] = {"version": "0.3.2",
                                  "sha256": PRODUCER_SHA.upper()}
        plan.update({"pilot_dir": pilot_dir, "pilot_label": "synthetic-v1",
                     "protected_root": os.path.join(self.work, "protected"),
                     "output_dir": os.path.join(self.work, "protected",
                                                "identity", output)})
        return plan

    def run_plan(self, plan):
        """Build the mapping and return (payload, raw bytes, summary)."""
        summary, digest = mapping.build_mapping(plan, self.loader)
        path = os.path.join(plan["output_dir"], mapping.OUTPUT_NAME)
        with open(path, "rb") as handle:
            data = handle.read()
        self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        return json.loads(data), data, summary

    def records(self, **kwargs):
        """Return mapping records keyed by synthetic person_id."""
        payload = self.run_plan(self.build_plan(**kwargs))[0]
        return {record["person_id"]: record for record in payload["records"]}

    def assert_fails(self, plan, code):
        """Assert a fail-closed error with no output written."""
        with self.assertRaises(mapping.MappingError) as caught:
            mapping.build_mapping(plan, self.loader)
        self.assertEqual(str(caught.exception), code)
        self.assertFalse(os.path.lexists(plan["output_dir"]))
        self.assertFalse(os.path.lexists(plan["output_dir"]
                                         + mapping.PARTIAL))

    # ---------------------------------------------------------- identifiers

    def test_canonical_int_is_strict(self):
        """Only exact decimal round-trips and true integers are accepted."""
        self.assertEqual(mapping.canonical_int("900001"), 900001)
        self.assertEqual(mapping.canonical_int(-3), -3)
        for value in ("09", " 9", "9 ", "+9", "-0", "9.0", "", "\u0669",
                      9.0, True, None, "abc"):
            self.assertIsNone(mapping.canonical_int(value), repr(value))

    # ------------------------------------------------------------- mapping

    def test_verified_mapping_uses_filtered_patient_index(self):
        """patient_index is the position after the y_diagnosis filter."""
        payload, _, summary = self.run_plan(self.build_plan())
        records = {r["person_id"]: r for r in payload["records"]}
        self.assertEqual(records[700001]["graph_position"], 2)
        self.assertEqual(records[700001]["patient_index"], 1)
        self.assertEqual(records[700002]["patient_index"], 0)
        self.assertEqual(records[700001]["subject_id"], 8100001)
        self.assertEqual(
            [n["visit_occurrence_id"] for n in records[700001]["visit_nodes"]],
            [900001, 900002])
        for record in records.values():
            self.assertEqual(record["identity_status"],
                             mapping.STATUS_VERIFIED)
            self.assertFalse(record["phase4_eligible"])
        self.assertFalse(payload["phase4_eligible"])
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["bridge_filtered_size"], 2)
        self.assertEqual(summary["phase4_eligible_records"], 0)
        self.assertEqual(self.loads, 1)

    def test_y_diagnosis_presence_alone_passes_bridge_filter(self):
        """A y_diagnosis attribute set to None keeps its patient_index."""
        self.graphs[0].y_diagnosis = None
        records = self.records()
        self.assertEqual(records[700002]["graph_position"], 1)
        self.assertEqual(records[700002]["patient_index"], 1)
        self.assertEqual(records[700001]["patient_index"], 2)
        self.graphs = base_graphs()
        self.graphs[1].y_diagnosis = None
        records = self.records(output="second")
        self.assertEqual(records[700002]["patient_index"], 0)
        self.assertEqual(records[700002]["identity_status"],
                         mapping.STATUS_VERIFIED)
        self.assertEqual(records[700001]["patient_index"], 1)

    def test_repeated_visit_nodes_are_preserved_and_flagged(self):
        """Repeated same-person node IDs keep every position and a flag."""
        self.graphs[2] = FakeGraph(8100001, [900001, 900002, 900001])
        record = self.records()[700001]
        self.assertEqual(record["identity_status"], mapping.STATUS_REPEATED)
        self.assertEqual(record["repeated_visit_occurrence_ids"], [900001])
        self.assertEqual([n["node_position"] for n in record["visit_nodes"]],
                         [0, 1, 2])
        self.assertEqual([n["repeated_visit_node"]
                          for n in record["visit_nodes"]],
                         [True, False, True])
        self.assertFalse(record["phase4_eligible"])

    def test_missing_graph_keeps_quarantined_person(self):
        """A person without a graph is written with a null patient_index."""
        del self.graphs[1]
        records = self.records()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[700002]["identity_status"],
                         mapping.STATUS_QUARANTINED)
        self.assertIsNone(records[700002]["patient_index"])
        self.assertEqual(records[700002]["findings"], ["graph_match_missing"])

    def test_multiple_and_filtered_graph_matches_quarantine(self):
        """Two graphs, or a graph dropped by the bridge, never map."""
        self.graphs.append(FakeGraph(8100002, [900003]))
        self.assertEqual(self.records()[700002]["findings"],
                         ["graph_match_multiple"])
        self.graphs = base_graphs()
        self.graphs[1] = FakeGraph(8100002, [900003], diagnosis=False)
        self.assertEqual(self.records(output="second")[700002]["findings"],
                         ["graph_excluded_by_bridge_filter"])

    def test_missing_and_cross_person_visit_links_quarantine(self):
        """Unknown visits and visits owned by another person never map."""
        self.graphs[2] = FakeGraph(8100001, [900001, 900009])
        self.graphs[1] = FakeGraph(8100002, [900001])
        records = self.records()
        self.assertEqual(records[700001]["findings"], ["visit_link_missing"])
        self.assertEqual(records[700002]["findings"], ["visit_owner_conflict"])
        for record in records.values():
            self.assertEqual(record["identity_status"],
                             mapping.STATUS_QUARANTINED)

    def test_duplicate_source_keys_quarantine(self):
        """Duplicate visit keys and a subject on two persons never map."""
        visits = VISITS + [{"visit_occurrence_id": "900003",
                            "person_id": "700002"}]
        persons = PERSONS + [{"person_id": "700003",
                              "subject_id": "8100001"}]
        payload, _, summary = self.run_plan(
            self.build_plan(persons=persons, visits=visits))
        records = payload["records"]
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["findings"],
                         ["subject_maps_to_multiple_persons"])
        self.assertEqual(records[1]["findings"],
                         ["duplicate_source_visit_key"])
        self.assertEqual(summary["global_finding_counts"]
                         ["duplicate_source_visit_keys"], 1)
        self.assertEqual(summary["status"], "complete_with_quarantine")

    def test_non_round_trip_ids_quarantine(self):
        """Padded subjects and float visit IDs are never coerced."""
        persons = [{"person_id": "700001", "subject_id": "08100001"},
                   PERSONS[1]]
        self.graphs[1] = FakeGraph(8100002, [900003.0])
        records = self.records(persons=persons)
        self.assertEqual(records[700001]["findings"],
                         ["subject_id_not_round_trip"])
        self.assertEqual(records[700002]["findings"],
                         ["visit_id_not_round_trip"])

    def test_visit_node_length_mismatch_quarantines(self):
        """hadm_ids must have exactly one entry per visit node."""
        self.graphs[2] = FakeGraph(8100001, [900001, 900002], nodes=3)
        record = self.records()[700001]
        self.assertEqual(record["findings"], ["visit_node_count_mismatch"])
        self.assertEqual(record["visit_nodes"], [])

    # ------------------------------------------------------ input identity

    def test_tampered_small_inputs_fail_before_loading(self):
        """A changed builder, reader or pilot CSV stops before any load."""
        plan = self.build_plan()
        with open(plan["builder"]["path"], "ab") as handle:
            handle.write(b"# changed\n")
        self.assert_fails(plan, "input_hash_mismatch:builder")
        plan = self.build_plan(output="second")
        with open(os.path.join(plan["pilot_dir"], "visits.csv"), "a",
                  encoding="utf-8") as handle:
            handle.write("900004,700002,9201\n")
        self.assert_fails(plan, "pilot_file_hash_mismatch")
        plan = self.build_plan(output="third")
        plan["pilot_config"]["sha256"] = "A" * 64
        self.assert_fails(plan, "input_hash_mismatch:pilot_config")
        self.assertEqual(self.loads, 0)

    def test_incomplete_pilot_manifest_fails(self):
        """A manifest that is not a completed promoted run is refused."""
        plan = self.build_plan()
        path = plan["pilot_manifest"]["path"]
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["status"] = "incomplete"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        plan["pilot_manifest"]["sha256"] = sha(path)
        self.assert_fails(plan, "pilot_manifest_not_complete")
        self.assertEqual(self.loads, 0)

    def test_manifest_producer_mismatch_fails_before_loading(self):
        """The manifest must name the pinned historical producer."""
        plan = self.build_plan()
        plan["pilot_producer"]["sha256"] = "CD" * 32
        self.assert_fails(plan, "pilot_manifest_producer_mismatch")
        plan = self.build_plan(output="second")
        plan["pilot_producer"]["version"] = "0.4"
        self.assert_fails(plan, "pilot_manifest_producer_mismatch")
        self.assertEqual(self.loads, 0)

    def test_historical_producer_and_current_verifier_are_distinct(self):
        """Both source-pilot code identities are recorded with their roles."""
        plan = self.build_plan()
        inputs = self.run_plan(plan)[0]["inputs"]
        self.assertEqual(inputs["pilot_historical_producer"]["sha256"],
                         PRODUCER_SHA)
        self.assertEqual(inputs["pilot_historical_producer"]["version"],
                         "0.3.2")
        self.assertEqual(inputs["pilot_reader"]["sha256"],
                         plan["pilot_reader"]["sha256"].lower())
        self.assertNotEqual(inputs["pilot_reader"]["sha256"], PRODUCER_SHA)
        self.assertIn("current", inputs["pilot_reader"]["role"])
        self.assertIn("historical",
                      inputs["pilot_historical_producer"]["role"])

    def test_mutation_between_verification_and_parse_fails(self):
        """A CSV changed after its verified read stops before any load."""
        plan = self.build_plan()
        original = mapping.parse_rows
        self.addCleanup(setattr, mapping, "parse_rows", original)
        visits = os.path.join(plan["pilot_dir"], "visits.csv")

        def mutating_parse(data, columns):
            """Change visits.csv on disk after its bytes were verified."""
            if "visit_occurrence_id" in columns:
                with open(visits, "a", encoding="utf-8") as handle:
                    handle.write("900004,700002,9201\n")
            return original(data, columns)

        mapping.parse_rows = mutating_parse
        self.assert_fails(plan, "input_changed_during_read:visits")
        self.assertEqual(self.loads, 0)
        self.assertFalse(os.path.lexists(os.path.dirname(
            plan["output_dir"])))

    def test_graph_identity_checked_before_deserialisation(self):
        """A wrong graph size or hash never reaches the loader."""
        plan = self.build_plan()
        plan["graph"]["sha256"] = "0" * 64
        self.assert_fails(plan, "input_hash_mismatch:graph")
        plan["graph"]["size_bytes"] += 1
        self.assert_fails(plan, "input_size_mismatch:graph")
        self.assertEqual(self.loads, 0)

    def test_graph_change_during_load_fails_closed(self):
        """A graph modified while loading is refused and nothing is written."""
        plan = self.build_plan()

        def changing_loader(path):
            """Append to the graph file to simulate a concurrent change."""
            with open(path, "ab") as handle:
                handle.write(b"x")
            return self.graphs

        with self.assertRaises(mapping.MappingError) as caught:
            mapping.build_mapping(plan, changing_loader)
        self.assertEqual(str(caught.exception), "graph_changed_during_load")
        self.assertFalse(os.path.lexists(plan["output_dir"]))

    # -------------------------------------------------------------- output

    def test_output_is_deterministic_and_never_overwritten(self):
        """Identical inputs give identical bytes; reruns are refused."""
        plan = self.build_plan()
        first = self.run_plan(plan)[1]
        with self.assertRaises(mapping.MappingError) as caught:
            mapping.build_mapping(plan, self.loader)
        self.assertEqual(str(caught.exception), "output_exists")
        plan["output_dir"] = os.path.join(plan["protected_root"], "identity",
                                          "again")
        self.assertEqual(self.run_plan(plan)[1], first)
        self.assertFalse(os.path.lexists(plan["output_dir"]
                                         + mapping.PARTIAL))

    def test_protected_output_path_checks(self):
        """Outputs must be private, outside the repo and protected."""
        protected = os.path.join(self.work, "protected")
        inside = os.path.join(protected, "out")
        self.assertIsNone(mapping.output_problem(inside, protected))
        self.assertEqual(mapping.output_problem(
            os.path.join(self.work, "elsewhere"), protected),
            "output_not_protected")
        self.assertEqual(mapping.output_problem(
            os.path.join(REPO_ROOT, "out"), protected),
            "output_path_rejected")
        self.assertEqual(mapping.output_problem(
            "C:\\temp\\out", "C:\\temp"), "output_not_protected")
        plan = mapping.real_plan()
        self.assertTrue(guard.is_within(plan["output_dir"],
                                        plan["protected_root"], True))
        self.assertTrue(plan["output_dir"].endswith(
            "\\identity-mappings\\step-17-pilot10-v1"))

    def test_status_output_is_sanitised(self):
        """Console output carries counts and codes, never identifiers."""
        self.graphs[1] = FakeGraph(8100002, [900001])
        plan = self.build_plan()
        original = (mapping.real_plan, mapping.torch_loader)
        self.addCleanup(setattr, mapping, "real_plan", original[0])
        self.addCleanup(setattr, mapping, "torch_loader", original[1])
        mapping.real_plan = lambda: plan
        mapping.torch_loader = self.loader
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(mapping.main([]), 0)
            self.assertEqual(mapping.main([]), 1)
        text = stream.getvalue()
        self.assertIn("RESULT: COMPLETE_WITH_QUARANTINE", text)
        self.assertIn("visit_owner_conflict", text)
        self.assertIn("FAILED: output_exists", text)
        for token in TOKENS:
            self.assertNotIn(token, text)
        self.assertNotIn(self.work, text)


if __name__ == "__main__":
    unittest.main()
