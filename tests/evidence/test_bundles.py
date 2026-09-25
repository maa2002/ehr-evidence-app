"""Synthetic checks for ehr_evidence_app.evidence.bundles.

Every fixture is invented: the bundle builder's synthetic snapshot, case,
published record and linkage record, changed inline. The scratch pipeline
writes those invented inputs as files under the configured workspace test
temp directory and runs the real-mode linkage (with an in-memory graph
runtime; torch is never imported) and bundle orchestration over them. No
real record is read.
"""

import csv
import dataclasses
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

from ehr_evidence_app.evidence import bundles, contracts, policy, readers
from ehr_evidence_app.gnn import admission, binding, linkage, mapping
from ehr_evidence_app.gnn import outputs as pmo
from ehr_evidence_app.gnn import provenance as amp
from ehr_evidence_app.storage import policy as guard

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
EXPECTED_COUNTS = {
    "qa_bundles": 5,
    "qa_by_scope": {"pre_target_history": 1, "selected_visit": 3,
                    "case_visits": 1},
    "qa_coverage": {"complete": 4, "complete_empty": 1, "unavailable": 0,
                    "failed": 0},
    "history_condition_events": 1,
    "history_unmapped_condition_events": 0,
    "history_excluded_final_admission_events": 2,
    "case_visit_condition_events": 3,
    "case_visit_unmapped_condition_events": 0,
    "explanation_bundles": 1,
    "explanation_eligible": 1,
    "attributions": 3,
    "narratable_attributions": 3,
    "attribution_names": {"available": 3, "unavailable": 0, "unmapped": 0},
    "excluded_driver_entries": 1,
    "linked_history_events": 2,
    "final_admission_context_events": 2,
    "withheld_final_admission_diagnoses": 1,
    "published_name_agreement": {"same": 3, "different": 0,
                                 "published_empty": 1},
}


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def refingerprint(document):
    """Re-seal a changed invented linkage record."""
    body = {key: value for key, value in document.items()
            if key != "preparation"}
    document["preparation"]["fingerprint_sha256"] = amp.digest(body)
    return document


def replace_case(case_set, **changes):
    """Return the invented case set with its one case changed."""
    return dataclasses.replace(case_set, cases=(
        dataclasses.replace(case_set.cases[0], **changes),))


def build(snapshot, case_set, outputs, linked, legacy, limits=None):
    """Build an invented bundle set."""
    return bundles.build_set(
        snapshot, case_set, outputs, linked, legacy,
        bundles.synthetic_provenance(),
        limits or contracts.ExecutionLimits(**bundles.SYNTHETIC_LIMITS))


class SyntheticSetTests(unittest.TestCase):
    """The invented bundle set is complete, consistent and deterministic."""

    def test_counts(self):
        """Aggregate counts match the invented case."""
        _, _, counts = bundles.run_synthetic()
        self.assertEqual(counts, EXPECTED_COUNTS)

    def test_identifiers_and_round_trip(self):
        """Bundle ids name case and scope; bundles revalidate unchanged."""
        qa, explanations, _ = bundles.run_synthetic()
        self.assertEqual([bundle.bundle_id for bundle in qa],
                         ["qa-c01-history", "qa-c01-visit-1",
                          "qa-c01-visit-2", "qa-c01-visit-3", "qa-c01-all"])
        self.assertEqual(explanations[0].bundle_id, "exp-c01")
        for bundle in qa + explanations:
            self.assertEqual(type(bundle).model_validate_json(
                bundles.bundle_bytes(bundle)), bundle)

    def test_explanation_content(self):
        """Prediction unchanged; history, context and exclusions apart."""
        snapshot, case_set, outputs, linked, legacy = (
            bundles.synthetic_inputs())
        _, explanations, _ = build(snapshot, case_set, outputs, linked,
                                   legacy)
        bundle = explanations[0]
        record = outputs[case_set.cases[0].patient_index]
        selected = record["predicted_diagnosis"]
        self.assertEqual((bundle.prediction.logit, bundle.prediction.score,
                          bundle.prediction.label_index),
                         (selected["logit"], selected["probability"],
                          selected["label_index"]))
        self.assertEqual(bundle.prediction.concept.name_source,
                         "legacy_concept_map")
        self.assertEqual([(item.domain, item.position, item.score,
                           item.linked_event_refs)
                          for item in bundle.attributions],
                         [("condition", 0, 0.25, ("E1",)),
                          ("procedure", 0, 0.125, ("E2",)),
                          ("drug", 0, 0.5, ("E3", "E4"))])
        self.assertEqual([event.native_id for event in bundle.history_events],
                         [1, 4])
        self.assertEqual([event.native_id
                          for event in bundle.final_admission_context],
                         [5, 6])
        self.assertEqual((bundle.excluded_driver_entries,
                          bundle.coverage.excluded_final_admission_events,
                          bundle.restrictions),
                         (1, 1, ("target_attached_drivers_present",
                                 "target_outcome_entries_excluded")))

    def test_deterministic_bytes(self):
        """Two builds give byte-identical files and manifest."""
        first = bundles.run_synthetic()
        second = bundles.run_synthetic()
        provenance = bundles.synthetic_provenance()
        files = []
        for qa, explanations, counts in (first, second):
            manifest = bundles.manifest_document(qa, explanations,
                                                 provenance, counts)
            files.append(bundles.set_files(
                qa, explanations, manifest,
                bundles.summary_document(manifest)))
        self.assertEqual(files[0], files[1])
        self.assertEqual(len(files[0]), 8)

    def test_manifest_and_summary(self):
        """The manifest pins every bundle and holds no native identifiers."""
        qa, explanations, counts = bundles.run_synthetic()
        manifest = bundles.manifest_document(
            qa, explanations, bundles.synthetic_provenance(), counts)
        by_id = {bundle.bundle_id: bundle for bundle in qa + explanations}
        self.assertEqual(len(manifest["files"]), 6)
        for entry in manifest["files"]:
            data = bundles.bundle_bytes(by_id[entry["bundle_id"]])
            self.assertEqual((entry["size_bytes"], entry["sha256"]),
                             (len(data), hashlib.sha256(data).hexdigest()))
        text = bundles.encode(manifest).decode("utf-8")
        for key in ("native_id", "visit_occurrence_id", "person_id",
                    "patient_index"):
            self.assertNotIn(key, text)
        summary = bundles.summary_document(manifest)
        self.assertEqual(summary["manifest_sha256"], hashlib.sha256(
            bundles.encode(manifest)).hexdigest())
        self.assertEqual(summary["counts"], counts)

    def test_synthetic_mode(self):
        """The default command builds the invented set and passes."""
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(bundles.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())


class FailureTests(unittest.TestCase):
    """Inconsistent inputs fail closed with a finding code."""

    def setUp(self):
        """Take fresh invented inputs."""
        (self.snapshot, self.case_set, self.outputs, self.linked,
         self.legacy) = bundles.synthetic_inputs()
        self.case = self.case_set.cases[0]

    def refused(self, code, snapshot=None, case_set=None, outputs=None,
                linked=None, limits=None):
        """Assert that a build over changed inputs is refused for code."""
        with self.assertRaisesRegex(bundles.BundleError, code):
            build(snapshot or self.snapshot, case_set or self.case_set,
                  self.outputs if outputs is None else outputs,
                  linked or self.linked, self.legacy, limits)

    def test_linkage_record_binding(self):
        """Unverified, altered or foreign linkage records are refused."""
        self.refused("linkage_not_verified",
                     linked=dict(self.linked, status="partial"))
        altered = json.loads(json.dumps(self.linked))
        altered["counts"]["cases"] = 2
        self.refused("linkage_fingerprint_mismatch", linked=altered)
        foreign = json.loads(json.dumps(self.linked))
        foreign["cases"][0]["patient_index"] = 5
        self.refused("linkage_case_mismatch", linked=refingerprint(foreign))
        moved = json.loads(json.dumps(self.linked))
        moved["cases"][0]["domains"]["drug"]["drivers"][0]["visits"][0][
            "visit_occurrence_id"] = 802
        self.refused("linkage_visit_mismatch", linked=refingerprint(moved))
        short = json.loads(json.dumps(self.linked))
        short["cases"][0]["domains"]["drug"]["drivers"].pop()
        self.refused("driver_count_mismatch", linked=refingerprint(short))

    def test_published_record_binding(self):
        """A changed or missing published record is refused."""
        changed = json.loads(json.dumps(self.outputs))
        record = changed[str(self.case.patient_index)]
        record["drivers"]["drug"][0]["score"] = 0.75
        self.refused("output_record_mismatch", outputs={
            self.case.patient_index: record})
        self.refused("output_record_missing", outputs={})
        self.refused("label_correspondence_not_established",
                     case_set=replace_case(
                         self.case_set,
                         label_correspondence="not_established"))

    def test_closure_tags_must_agree(self):
        """Score class, attachment and eligibility must match the record."""
        tags = dict(self.case.drivers)
        tag = tags["procedure"][0]
        tags["procedure"] = (dataclasses.replace(
            tag, attachment="target_and_earlier"),)
        self.refused("driver_attachment_mismatch",
                     case_set=replace_case(self.case_set, drivers=tags))
        tags = dict(self.case.drivers)
        tags["condition"] = (dataclasses.replace(
            tags["condition"][0], score_class="zero"),
            tags["condition"][1])
        self.refused("driver_tag_mismatch",
                     case_set=replace_case(self.case_set, drivers=tags))
        self.refused("explanation_eligibility_mismatch",
                     case_set=replace_case(self.case_set,
                                           explanation_eligible=False))
        self.refused("bundle_record_invalid:ExplanationBundle",
                     case_set=replace_case(self.case_set, restrictions=()))

    def test_linked_events_must_exist(self):
        """A linked event missing from the snapshot is refused."""
        events = dict(self.snapshot.events)
        events["drug"] = events["drug"][:1]
        self.refused("linked_event_mismatch", snapshot=dataclasses.replace(
            self.snapshot, events=events))
        visits = dict(self.snapshot.visits)
        visits.pop(802)
        self.refused("qa_scope_unavailable", snapshot=dataclasses.replace(
            self.snapshot, visits=visits))

    def test_no_admitted_case(self):
        """A set without admitted cases builds nothing."""
        self.refused("no_admitted_cases",
                     case_set=replace_case(self.case_set, admitted=False))

    def test_limits(self):
        """Evidence-count and byte limits are enforced per bundle."""
        for changes, code in (({"max_evidence_items": 1},
                               "bundle_evidence_limit_exceeded"),
                              ({"max_bundle_bytes": 1024},
                               "bundle_size_limit_exceeded")):
            limits = contracts.ExecutionLimits(
                **dict(bundles.SYNTHETIC_LIMITS, **changes))
            self.refused(code, limits=limits)

    def test_published_concepts_are_checked(self):
        """A missing, negative or boolean concept id is refused."""
        for value in (None, -1, True, "1001"):
            with self.assertRaisesRegex(bundles.BundleError,
                                        "published_concept_invalid"):
                bundles.concept_of(value, self.snapshot, self.legacy)

    def test_name_agreement(self):
        """Published names are compared with the bundle's names."""
        _, explanations, _ = build(self.snapshot, self.case_set,
                                   self.outputs, self.linked, self.legacy)
        record = json.loads(json.dumps(
            self.outputs[self.case.patient_index]))
        record["drivers"]["drug"][0]["name"] = "SYNTHETIC OTHER NAME"
        self.assertEqual(bundles.name_agreement(explanations[0], record),
                         {"same": 2, "different": 1, "published_empty": 1})

    def test_attachment_classes(self):
        """Linkage visits imply the closure's attachment classes."""
        visits = [{"position": 0}, {"position": 2}]
        self.assertEqual(bundles.attachment_of(
            {"node_found": False, "visits": []}, 2), "not_a_patient_node")
        self.assertEqual(bundles.attachment_of(
            {"node_found": True, "visits": []}, 2), "not_attached")
        self.assertEqual(bundles.attachment_of(
            {"node_found": True, "visits": visits[1:]}, 2), "target_only")
        self.assertEqual(bundles.attachment_of(
            {"node_found": True, "visits": visits}, 2), "target_and_earlier")
        self.assertEqual(bundles.attachment_of(
            {"node_found": True, "visits": visits[:1]}, 2), "earlier_only")


class FakeGraphRuntime:
    """In-memory stand-in for the raw-graph reader."""

    calls = 0

    def case_graphs(self, stream, cases):
        """Return the invented graph after re-verifying the stream."""
        FakeGraphRuntime.calls += 1
        stream.verify()
        _, case, graph, _, _, _ = linkage.synthetic_fixture()
        return {case.patient_index: graph}


def write_csv(path, columns, rows):
    """Write invented rows as UTF-8 CSV and return the pinned entry."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(columns)
        for row in rows:
            writer.writerow(["" if value is None else value
                             for value in row])
    return pin(path)


def write_bytes(path, data):
    """Write invented bytes and return the pinned entry."""
    with open(path, "wb") as handle:
        handle.write(data)
    return pin(path)


def pin(path):
    """Return {path, size_bytes, sha256} of a written file."""
    with open(path, "rb") as handle:
        data = handle.read()
    return {"path": path, "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


class ScratchPipelineTests(unittest.TestCase):
    """Real-mode linkage then bundles over invented scratch files."""

    def setUp(self):
        """Write the invented inputs into a workspace scratch tree."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT, True),
                        "test scratch space must stay in the workspace root")
        if guard.fixed_policy_problem():
            self.skipTest("the fixed layout policy does not hold here")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="evidence-bundles-", dir=root)
        self.addCleanup(self.remove_scratch)
        FakeGraphRuntime.calls = 0
        self.write_inputs()

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def write_inputs(self):
        """Write snapshot, closure, mapping, outputs, vocabulary, graph."""
        snapshot, case_set, outputs, _, legacy = bundles.synthetic_inputs()
        case = case_set.cases[0]
        protected = os.path.join(self.work, "protected")
        source = os.path.join(protected, "source", snapshot.label)
        inputs = os.path.join(protected, "inputs")
        os.makedirs(source)
        os.makedirs(inputs)
        entries = [dict(write_csv(
            os.path.join(source, "visits.csv"), readers.VISIT_COLUMNS,
            [(visit.visit_occurrence_id, visit.person_id,
              visit.visit_concept_id, visit.start_date, visit.end_date)
             for visit in snapshot.visits.values()]), file="visits.csv")]
        entries.append(dict(write_csv(
            os.path.join(source, "concepts.csv"), readers.CONCEPT_COLUMNS,
            [(row.concept_id, row.name, row.domain_id, row.vocabulary_id,
              "Clinical Finding", "S", "C%d" % row.concept_id)
             for row in snapshot.concepts.values()]), file="concepts.csv"))
        for domain, columns in readers.DOMAIN_COLUMNS.items():
            header = columns[1:] + ("person_id", "visit_occurrence_id")
            rows = [(event.native_id, event.concept_id, event.start_date,
                     event.start_datetime, event.end_date,
                     event.end_datetime, event.source_value,
                     event.source_concept_id, event.person_id,
                     event.visit_occurrence_id)
                    for event in snapshot.events[domain]]
            entries.append(dict(write_csv(os.path.join(source, columns[0]),
                                          header, rows), file=columns[0]))
        manifest = {"status": "complete", "run_state": "finished",
                    "promoted": True, "version_label": snapshot.label,
                    "files": [{"file": entry["file"], "present": True,
                               "size_bytes": entry["size_bytes"],
                               "sha256": entry["sha256"]}
                              for entry in entries]}
        record = outputs[case.patient_index]
        mapping_record = {
            "patient_index": case.patient_index,
            "graph_position": case.graph_position,
            "person_id": case.person_id, "subject_id": case.subject_id,
            "visit_nodes": [{"node_position": position,
                             "visit_occurrence_id": visit_id}
                            for position, visit_id in
                            enumerate(case.visit_ids)]}
        item = {"case_position": 0, "patient_index": case.patient_index,
                "graph_position": case.graph_position,
                "mapping_record_sha256": amp.digest(mapping_record),
                "output_record_sha256": amp.digest(record),
                "admission": {"status": admission.ADMITTED,
                              "restrictions": list(case.restrictions)},
                "prediction": {"batch_sensitive": case.batch_sensitive,
                               "single_graph_reproduces_published":
                                   case.single_graph_reproduces_published},
                "label_evidence": {"correspondence": "established"},
                "upstream_partition": case.upstream_partition,
                "explanation_eligible": True,
                "drivers": {domain: [dataclasses.asdict(tag)
                                     for tag in tags]
                            for domain, tags in case.drivers.items()}}
        body = {"schema": admission.SCHEMA, "status": "closed",
                "cases": [item]}
        closure = dict(body, preparation={
            "fingerprint_sha256": amp.digest(body)})
        _, _, _, _, inverse, _ = linkage.synthetic_fixture()
        vocabulary = {domain: {str(concept): index
                               for index, concept in table.items()}
                      for domain, table in inverse.items()}
        reader_plan = {
            "label": snapshot.label, "snapshot_dir": source,
            "manifest": write_bytes(os.path.join(source, "manifest.json"),
                                    binding.encode(manifest)),
            "legacy_concepts": write_csv(
                os.path.join(inputs, "kg_concepts.csv"),
                ("concept_id", "concept_name", "domain_id",
                 "vocabulary_id"),
                [(row.concept_id, row.name, row.domain_id,
                  row.vocabulary_id) for row in legacy.values()]),
            "closure": write_bytes(os.path.join(inputs, "closure.json"),
                                   binding.encode(closure)),
            "mapping": write_bytes(os.path.join(inputs, "mapping.json"),
                                   binding.encode(
                                       {"records": [mapping_record]}))}
        outputs_entry = write_bytes(os.path.join(inputs, "outputs.json"),
                                    binding.encode([record]))
        self.linkage_plan = {
            "graph": write_bytes(os.path.join(inputs, "graphs.pt"),
                                 b"invented graph bytes"),
            "concept_vocab": write_bytes(
                os.path.join(inputs, "concept_vocab.json"),
                binding.encode(vocabulary)),
            "outputs": outputs_entry, "readers": reader_plan,
            "output_dir": os.path.join(protected, "evidence", "linkage"),
            "protected_root": protected}
        self.bundle_plan = {
            "readers": reader_plan, "outputs": outputs_entry,
            "linkage": None, "policy_path": policy.CONFIG_PATH,
            "version": "synthetic-set-v1",
            "names": {"identity_mapping": "synthetic-mapping",
                      "closure_record": "synthetic-closure",
                      "model_outputs": "synthetic-outputs",
                      "linkage_record": "synthetic-linkage"},
            "output_dir": os.path.join(protected, "evidence-bundles", "set"),
            "protected_root": protected}

    def run_linkage(self):
        """Run the real-mode linkage and pin its record for the bundles."""
        with redirect_stdout(io.StringIO()):
            summary, digests = linkage.run_real(
                self.linkage_plan, runtime_factory=FakeGraphRuntime)
        self.bundle_plan["linkage"] = pin(os.path.join(
            self.linkage_plan["output_dir"], linkage.LINKAGE_NAME))
        return summary, digests

    def test_pipeline_publishes_once(self):
        """Linkage then bundles publish complete, pinned directories."""
        summary, digests = self.run_linkage()
        self.assertEqual(summary["status"], "verified")
        self.assertEqual(FakeGraphRuntime.calls, 1)
        self.assertEqual(digests[linkage.LINKAGE_NAME],
                         self.bundle_plan["linkage"]["sha256"])
        summary, digests = bundles.run_real(self.bundle_plan)
        self.assertEqual(summary["counts"], EXPECTED_COUNTS)
        out = self.bundle_plan["output_dir"]
        self.assertEqual(sorted(os.listdir(out)),
                         ["explanation", "manifest.json", "qa",
                          "summary.json"])
        self.assertEqual(len(os.listdir(os.path.join(out, "qa"))), 5)
        for name, value in digests.items():
            self.assertEqual(mapping.sha256_file(os.path.join(out, name)),
                             value)
        with open(os.path.join(out, bundles.MANIFEST_NAME), "rb") as handle:
            manifest = json.loads(handle.read())
        for entry in manifest["files"]:
            path = os.path.join(out, *entry["path"].split("/"))
            self.assertEqual(mapping.sha256_file(path), entry["sha256"])
        provenance = manifest["provenance"]
        self.assertEqual(provenance["linkage_record"]["sha256"],
                         self.bundle_plan["linkage"]["sha256"])
        self.assertEqual(provenance["evidence_policy"]["sha256"],
                         policy.load()[1])
        self.assertEqual(provenance["legacy_concept_map"]["name"],
                         "inputs-kg-concepts")
        self.assertEqual((manifest["version"],
                          provenance["closure_record"]["name"]),
                         ("synthetic-set-v1", "synthetic-closure"))
        with self.assertRaisesRegex(bundles.BundleError, "output_exists"):
            bundles.run_real(self.bundle_plan)
        with self.assertRaisesRegex(linkage.LinkageError, "output_exists"):
            linkage.run_real(self.linkage_plan,
                             runtime_factory=FakeGraphRuntime)

    def test_unpinned_or_changed_linkage_publishes_nothing(self):
        """Bundles refuse an unpinned or altered linkage record."""
        self.bundle_plan["linkage"] = {"path": "unused", "sha256": None}
        with self.assertRaisesRegex(bundles.BundleError,
                                    "linkage_record_not_pinned"):
            bundles.run_real(self.bundle_plan)
        self.run_linkage()
        with open(self.bundle_plan["linkage"]["path"], "ab") as handle:
            handle.write(b" ")
        with self.assertRaisesRegex(bundles.BundleError,
                                    "input_hash_mismatch:linkage"):
            bundles.run_real(self.bundle_plan)
        for path in (self.bundle_plan["output_dir"],
                     self.bundle_plan["output_dir"] + bundles.PARTIAL):
            self.assertFalse(os.path.lexists(path))

    def test_changed_snapshot_is_refused(self):
        """A snapshot file that no longer matches its manifest fails."""
        self.run_linkage()
        path = os.path.join(self.bundle_plan["readers"]["snapshot_dir"],
                            "drugs.csv")
        with open(path, "ab") as handle:
            handle.write(b"99,77,801,3001,,,,,,\n")
        with self.assertRaisesRegex(bundles.BundleError,
                                    "input_hash_mismatch:drugs.csv"):
            bundles.run_real(self.bundle_plan)

    def test_output_boundaries(self):
        """Existing, overlapping and unprotected outputs are refused."""
        self.run_linkage()
        os.makedirs(self.bundle_plan["output_dir"] + bundles.PARTIAL)
        with self.assertRaisesRegex(bundles.BundleError, "output_exists"):
            bundles.run_real(self.bundle_plan)
        os.rmdir(self.bundle_plan["output_dir"] + bundles.PARTIAL)
        inside = dict(self.bundle_plan, output_dir=os.path.join(
            self.work, "protected", "inputs", "set"))
        with self.assertRaisesRegex(bundles.BundleError, "overlaps_input"):
            bundles.run_real(inside)
        outside = dict(self.bundle_plan, output_dir=os.path.join(
            self.work, "elsewhere", "set"))
        with self.assertRaisesRegex(bundles.BundleError, "output_not_"):
            bundles.run_real(outside)

    def test_failed_publish_leaves_nothing(self):
        """An input changed before the rename removes every written file."""
        probe = write_bytes(os.path.join(self.work, "protected", "inputs",
                                         "probe.bin"), b"probe")
        held = []
        pmo.verify_read(probe, "probe", held)
        with open(probe["path"], "ab") as handle:
            handle.write(b"changed")
        qa, explanations, counts = bundles.run_synthetic()
        manifest = bundles.manifest_document(
            qa, explanations, bundles.synthetic_provenance(), counts)
        files = bundles.set_files(qa, explanations, manifest,
                                  bundles.summary_document(manifest))
        with self.assertRaisesRegex(bundles.BundleError, "probe"):
            bundles.publish(self.bundle_plan, files, held)
        for path in (self.bundle_plan["output_dir"],
                     self.bundle_plan["output_dir"] + bundles.PARTIAL):
            self.assertFalse(os.path.lexists(path))


if __name__ == "__main__":
    unittest.main()
