"""Synthetic checks for ehr_evidence_app.casesets.prepare.

Graph metadata, graph structures, bridge records, batch rows, vocabularies,
snapshots and run-binding summaries are invented; the scratch run replaces
the torch runtime with an in-memory fake, so torch is never imported.
Scratch files are created only under the configured workspace test temp
directory. No real record is read.
"""

import copy
import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from ehr_evidence_app.casesets import prepare, registry, selection
from ehr_evidence_app.evidence import readers
from ehr_evidence_app.gnn import admission, binding, mapping
from ehr_evidence_app.gnn import provenance as amp
from ehr_evidence_app.storage import policy as guard

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
LABEL = "synthetic-3-v1"
SPEC = prepare.SYNTHETIC_SPEC


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def build(inputs, partition="test", inverse=None, label_vocab=None):
    """Build invented case-set documents from pure-core inputs."""
    ordered, summary = prepare.mapping_records(
        inputs["persons"], inputs["visits"], inputs["metadata"],
        inputs["cases"])
    return prepare.build_records(
        LABEL, SPEC, inputs["cases"], ordered, summary, inputs["records"],
        inputs["rows"], inputs["plains"],
        {"status": "passed", "compared_records": 3},
        label_vocab or inputs["label_vocab"], inverse or inputs["inverse"],
        True, {case[1]: partition for case in inputs["cases"]},
        {"graph": {}, "explain_patient": {}, "snapshot_manifest": {},
         "snapshot_files": {}})


class MappingTests(unittest.TestCase):
    """Each snapshot person maps to exactly the drawn case."""

    def setUp(self):
        """Take fresh invented inputs."""
        self.inputs = prepare.synthetic_inputs()

    def test_records_follow_the_draw(self):
        """Records come back in case order, verified."""
        ordered, summary = prepare.mapping_records(
            self.inputs["persons"], self.inputs["visits"],
            self.inputs["metadata"], self.inputs["cases"])
        self.assertEqual([record["patient_index"] for record in ordered],
                         [case[1] for case in self.inputs["cases"]])
        self.assertEqual((summary["verified"], summary["quarantined"]),
                         (3, 0))

    def test_mismatches_fail_closed(self):
        """Missing persons, other subjects or other graphs are refused."""
        inputs = self.inputs
        with self.assertRaisesRegex(prepare.PreparationError,
                                    "snapshot_person_count_mismatch"):
            prepare.mapping_records(inputs["persons"][:2], inputs["visits"],
                                    inputs["metadata"], inputs["cases"])
        persons = copy.deepcopy(inputs["persons"])
        persons[0]["subject_id"] = "123"
        with self.assertRaisesRegex(prepare.PreparationError,
                                    "snapshot_subject_missing"):
            prepare.mapping_records(persons, inputs["visits"],
                                    inputs["metadata"], inputs["cases"])
        cases = list(inputs["cases"])
        number, index, position, subject = cases[0]
        cases[0] = (number, index + 1, position, subject)
        with self.assertRaisesRegex(prepare.PreparationError,
                                    "mapping_case_mismatch"):
            prepare.mapping_records(inputs["persons"], inputs["visits"],
                                    inputs["metadata"], cases)

    def test_quarantined_identity_is_kept(self):
        """A person whose visit links fail stays in, quarantined."""
        visits = self.inputs["visits"][1:]
        ordered, summary = prepare.mapping_records(
            self.inputs["persons"], visits, self.inputs["metadata"],
            self.inputs["cases"])
        self.assertEqual(ordered[0]["identity_status"],
                         mapping.STATUS_QUARANTINED)
        self.assertEqual(summary["quarantined"], 1)


class AttachmentTests(unittest.TestCase):
    """Driver attachment follows the Phase 3 closure rule."""

    def test_classes(self):
        """Target, earlier, both, no edges and outside the graph."""
        inputs = prepare.synthetic_inputs(count=1)
        plain = inputs["plains"][inputs["cases"][0][1]]
        record = copy.deepcopy(inputs["records"][0])
        self.assertEqual(prepare.attachment_classes(plain, record), {
            "condition": ["target_and_earlier", "target_only"],
            "procedure": ["earlier_only"], "drug": ["target_only"]})
        plain = copy.deepcopy(plain)
        plain["drug"]["node_vocab"].append(4)
        record["drivers"]["drug"] += [
            {"vocab_index": 4, "concept_id": 3004, "name": "", "score": 0.1},
            {"vocab_index": 9, "concept_id": 3009, "name": "", "score": 0.1}]
        self.assertEqual(prepare.attachment_classes(plain, record)["drug"],
                         ["target_only", "not_attached",
                          "not_a_patient_node"])
        plain["drug"]["node_vocab"].append(0)
        with self.assertRaisesRegex(prepare.PreparationError,
                                    "graph_concept_node_repeated"):
            prepare.attachment_classes(plain, record)


class AdmissionTests(unittest.TestCase):
    """Case entries follow the closure's admission and restriction rules."""

    def setUp(self):
        """Build one invented case entry's parts."""
        inputs = prepare.synthetic_inputs(count=1)
        ordered, _ = prepare.mapping_records(
            inputs["persons"], inputs["visits"], inputs["metadata"],
            inputs["cases"])
        self.case = inputs["cases"][0]
        self.record = ordered[0]
        self.output = inputs["records"][0]
        self.fact = prepare.case_fact(inputs["rows"][0], "test")
        classes = prepare.attachment_classes(
            inputs["plains"][self.case[1]], self.output)
        self.tags, _ = admission.tag_drivers(self.output, classes,
                                             inputs["inverse"])

    def entry(self, record=None, fact=None, label_ok=True, layer_c=True):
        """Return a case entry for changed parts."""
        return prepare.case_entry(1, self.case, record or self.record,
                                  self.output, fact or self.fact, self.tags,
                                  label_ok, layer_c)

    def test_admitted_case(self):
        """A verified, corresponding, reproduced case is admitted."""
        entry = self.entry()
        self.assertEqual(entry["admission"]["status"], admission.ADMITTED)
        self.assertEqual(entry["admission"]["restrictions"],
                         ["target_attached_drivers_present",
                          "target_outcome_entries_excluded"])
        self.assertEqual((entry["excluded_driver_entries"],
                          entry["narration_eligible_drivers"],
                          entry["explanation_eligible"]), (1, 3, True))
        self.assertEqual(entry["mapping_record_sha256"],
                         amp.digest(self.record))
        self.assertEqual(entry["output_record_sha256"],
                         amp.digest(self.output))

    def test_quarantine_reasons(self):
        """Each failed condition is recorded; nothing is admitted."""
        fact = copy.deepcopy(self.fact)
        fact["single_graph_reproduces_published"] = False
        for entry, reason in (
                (self.entry(record=dict(self.record,
                                        identity_status="quarantined")),
                 "identity_not_verified"),
                (self.entry(label_ok=False),
                 "label_correspondence_not_established"),
                (self.entry(layer_c=False),
                 "label_correspondence_not_established"),
                (self.entry(fact=fact), "single_graph_not_reproduced")):
            self.assertEqual(entry["admission"]["status"],
                             admission.QUARANTINED)
            self.assertEqual(entry["admission"]["quarantine_reasons"],
                             [reason])
            self.assertFalse(entry["explanation_eligible"])

    def test_batch_sensitivity(self):
        """Any batch argmax change marks the prediction batch-sensitive."""
        fact = copy.deepcopy(self.fact)
        fact["batch"]["128"]["argmax_unchanged"] = False
        entry = self.entry(fact=fact)
        self.assertTrue(entry["prediction"]["batch_sensitive"])
        self.assertEqual(entry["admission"]["restrictions"][0],
                         "batch_sensitive_prediction")


class RecordTests(unittest.TestCase):
    """The published documents and their summary."""

    def test_documents(self):
        """The closure is fingerprinted; the summary pins every file."""
        documents, summary, files = prepare.run_synthetic()
        closure = documents["closure"]
        body = {key: value for key, value in closure.items()
                if key != "preparation"}
        self.assertEqual(closure["preparation"]["fingerprint_sha256"],
                         amp.digest(body))
        self.assertEqual(closure["schema"], registry.CLOSURE_SCHEMA)
        self.assertEqual(len(closure["cases"]), 3)
        self.assertEqual(sorted(files), sorted(prepare.NAMES.values()))
        for name, entry in summary["files"].items():
            self.assertEqual(entry["sha256"],
                             hashlib.sha256(files[name]).hexdigest())
        self.assertEqual(summary["counts"]["restrictions"][
            "batch_sensitive_prediction"], 1)
        text = files[prepare.NAMES["summary"]].decode("utf-8")
        for key in ("subject_id", "patient_index", "person_id"):
            self.assertNotIn(key, text)

    def test_partition_and_decode_are_checked(self):
        """A case outside the set's partition or a decode mismatch fails."""
        inputs = prepare.synthetic_inputs()
        with self.assertRaisesRegex(prepare.PreparationError,
                                    "case_partition_mismatch"):
            build(inputs, partition="train")
        inverse = copy.deepcopy(inputs["inverse"])
        inverse["drug"][0] = 3999
        with self.assertRaisesRegex(prepare.PreparationError,
                                    "driver_decode_mismatch"):
            build(inputs, inverse=inverse)

    def test_commands(self):
        """The default mode passes; the real flags go together."""
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(prepare.main([]), 0)
            self.assertEqual(prepare.main(["--operator-real"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())


class FakeRuntime:
    """In-memory stand-in for the torch runtime."""

    def __init__(self, inputs):
        """Keep the invented results."""
        self.inputs = inputs
        self.calls = 0

    def run(self, plan, bound, held, selection_items, step19, canonical,
            vocabularies, trips):
        """Return invented results after re-verifying the graph stream."""
        self.calls += 1
        bound["streams"]["graph"].verify()
        return {"metadata": self.inputs["metadata"],
                "plains": self.inputs["plains"],
                "records": self.inputs["records"],
                "report": {"status": "passed", "compared_records": 3},
                "rows": self.inputs["rows"],
                "versions": {"python": "synthetic"}}


class ScratchRunTests(unittest.TestCase):
    """Real-mode orchestration over invented scratch inputs."""

    def setUp(self):
        """Write every invented input into a workspace scratch tree."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="case-prepare-", dir=root)
        self.addCleanup(self.remove_scratch)
        self.inputs = prepare.synthetic_inputs()
        patcher = mock.patch.dict(registry.CASE_SETS, {LABEL: SPEC})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.plan = self.write_plan()

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def write(self, path, data):
        """Write bytes and return their pinned entry."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        return {"path": path, "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def write_csv(self, path, columns, rows):
        """Write invented CSV rows and return the pinned entry."""
        text = io.StringIO()
        writer = csv.DictWriter(text, fieldnames=columns,
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return self.write(path, text.getvalue().encode("utf-8"))

    def write_plan(self):
        """Write the invented inputs and return the run plan."""
        inputs = self.inputs
        upstream = os.path.join(self.work, "upstream")
        protected = os.path.join(self.work, "protected")
        assets = {
            "explain_patient": self.write(os.path.join(upstream, "bridge.py"),
                                          b"# invented bridge\n"),
            "model": self.write(os.path.join(upstream, "model.py"),
                                b"# invented model\n"),
            "graph": self.write(os.path.join(upstream, "graphs.pt"),
                                b"invented graphs"),
            "checkpoint": self.write(os.path.join(upstream, "model.pt"),
                                     b"invented checkpoint"),
            "label_vocab": self.write(os.path.join(upstream, "labels.json"),
                                      json.dumps(inputs["label_vocab"])
                                      .encode("utf-8")),
            "concept_vocab": self.write(
                os.path.join(upstream, "concepts.json"),
                json.dumps({domain: {str(concept): index
                                     for index, concept in table.items()}
                            for domain, table in inputs["inverse"].items()})
                .encode("utf-8")),
            "kg_concepts": self.write(os.path.join(upstream, "kg.csv"),
                                      b"concept_id,concept_name\n"),
        }
        canonical = []
        for index in range(3):
            record = copy.deepcopy(inputs["records"][0])
            record["patient_index"] = index
            canonical.append(record)
        assets["canonical_explanations"] = self.write(
            os.path.join(upstream, "explanations.json"),
            json.dumps(canonical).encode("utf-8"))
        count = len(inputs["metadata"])
        assets["step19_summary"] = self.write(
            os.path.join(protected, "verification", "step19.json"),
            json.dumps({"status": "passed", "phase4_eligible": False,
                        "graph_sha256": assets["graph"]["sha256"],
                        "source_sha256": {
                            "explain_patient":
                                assets["explain_patient"]["sha256"],
                            "model": assets["model"]["sha256"]},
                        "model_input_digest": "0" * 64,
                        "counts": {"graphs_total": count,
                                   "graphs_filtered": count}})
            .encode("utf-8"))
        facts = selection.synthetic_facts(inputs["metadata"])
        body = {"schema": binding.SCHEMA, "status": binding.VERIFIED,
                "checks": {"split_replay_matches_upstream": True,
                           "pos_weight_bitwise_equal": True,
                           "reversed_label_control_failed": True},
                "run_arguments": {key: facts[key] for key in (
                    "seed", "val_frac", "test_frac")},
                "split": {key: facts[key] for key in (
                    "graphs_filtered", "partition_sha256", "counts")}}
        summary = dict(body, preparation={
            "fingerprint_sha256": binding.digest(body)})
        binding_entry = self.write(
            os.path.join(protected, "verification", "binding.json"),
            binding.encode(summary))
        chosen = inputs["cases"]
        selection_document = {
            "schema": registry.SELECTION_SCHEMA, "case_set": LABEL,
            "spec": SPEC,
            "cases": [{"case_number": number, "patient_index": index,
                       "graph_position": position, "subject_id": subject}
                      for number, index, position, subject in chosen]}
        selection_entry = self.write(
            os.path.join(protected, "case-sets", LABEL, "selection.json"),
            json.dumps(selection_document).encode("utf-8"))
        snapshot = os.path.join(protected, "source-casesets", LABEL)
        persons = self.write_csv(os.path.join(snapshot, "persons.csv"),
                                 ["person_id", "subject_id"],
                                 inputs["persons"])
        visits = self.write_csv(os.path.join(snapshot, "visits.csv"),
                                ["visit_occurrence_id", "person_id"],
                                inputs["visits"])
        manifest = {"status": "complete", "run_state": "finished",
                    "promoted": True, "version_label": LABEL,
                    "selection": {"cases_requested": 3, "case_set": {
                        "label": LABEL,
                        "selection_record": {
                            "sha256": selection_entry["sha256"]}}},
                    "files": [dict(file=name, present=True,
                                   size_bytes=entry["size_bytes"],
                                   sha256=entry["sha256"])
                              for name, entry in (("persons.csv", persons),
                                                  ("visits.csv", visits))]}
        manifest_entry = self.write(os.path.join(snapshot, "manifest.json"),
                                    json.dumps(manifest).encode("utf-8"))
        return {"label": LABEL, "upstream_root": upstream, "assets": assets,
                "selection": selection_entry,
                "snapshot_manifest": manifest_entry,
                "snapshot_dir": snapshot, "binding_summary": binding_entry,
                "output_dir": os.path.join(protected, "case-preparation",
                                           LABEL),
                "protected_root": protected}

    def test_publishes_records_readers_accept(self):
        """Six files are published and read back as a case set."""
        runtime = FakeRuntime(self.inputs)
        summary, digests = prepare.prepare(self.plan, runtime)
        self.assertEqual(runtime.calls, 1)
        out = self.plan["output_dir"]
        self.assertEqual(sorted(os.listdir(out)),
                         sorted(prepare.NAMES.values()))
        for name, value in digests.items():
            self.assertEqual(mapping.sha256_file(os.path.join(out, name)),
                             value)
        self.assertEqual(summary["counts"]["admitted"], 3)

        def pin(name):
            """Return the pinned entry of one published file."""
            path = os.path.join(out, name)
            with open(path, "rb") as handle:
                data = handle.read()
            return {"path": path, "size_bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest()}

        cases = readers.read_cases({
            "label": LABEL, "closure": pin(prepare.NAMES["closure"]),
            "mapping": pin(prepare.NAMES["mapping"])})
        self.assertEqual([case.admitted for case in cases.cases],
                         [True, True, True])
        first = self.inputs["cases"][0]
        self.assertEqual(cases.cases[0].visit_ids, tuple(
            self.inputs["metadata"][first[2]]["hadm_ids"]))
        with self.assertRaisesRegex(prepare.PreparationError,
                                    "output_exists"):
            prepare.prepare(self.plan, runtime)

    def test_changed_inputs_publish_nothing(self):
        """A changed manifest or selection fails before the runtime runs."""
        runtime = FakeRuntime(self.inputs)
        with open(self.plan["snapshot_manifest"]["path"], "ab") as handle:
            handle.write(b" ")
        with self.assertRaisesRegex(prepare.PreparationError,
                                    "input_hash_mismatch:snapshot_manifest"):
            prepare.prepare(self.plan, runtime)
        with open(self.plan["selection"]["path"], "ab") as handle:
            handle.write(b" ")
        with self.assertRaisesRegex(prepare.PreparationError,
                                    "stage_hash_mismatch:selection"):
            prepare.prepare(self.plan, runtime)
        self.assertEqual(runtime.calls, 0)
        for path in (self.plan["output_dir"],
                     self.plan["output_dir"] + prepare.PARTIAL):
            self.assertFalse(os.path.lexists(path))


if __name__ == "__main__":
    unittest.main()
