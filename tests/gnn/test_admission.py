"""Synthetic checks for ehr_evidence_app.gnn.admission (Phase 3 closure).

Every fixture is invented: invented identity mapping, parity summary,
bridge outputs, provenance sidecar, split sidecar, wrapper recovery record,
run-binding record, vocabularies and graph bytes. No real record, graph or
patient data is opened, and torch is never imported; the graph runtime is
replaced by an in-memory fake. Scratch files are created only under the
configured workspace test temp directory.
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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.gnn import admission  # noqa: E402
from ehr_evidence_app.gnn import binding  # noqa: E402
from ehr_evidence_app.gnn import mapping  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402

PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def config_ref():
    """Return the parsed reference of the repository config."""
    with open(admission.CONFIG_PATH, "rb") as handle:
        return admission.load_config(handle.read())


def bundle():
    """Return a fresh invented closure bundle."""
    return admission.synthetic_bundle()


def rebuild(plan, raw, documents, attachments):
    """Build the closure from (possibly modified) invented documents."""
    return admission.build_closure(plan, raw, documents, attachments,
                                   config_ref())


def refresh(plan, raw, documents, role):
    """Re-encode one modified document and re-pin it."""
    raw[role] = binding.encode(documents[role])
    plan["inputs"][role]["sha256"] = hashlib.sha256(raw[role]).hexdigest()


class ConfigTests(unittest.TestCase):
    """The repository config mirrors the implemented values exactly."""

    def test_repository_config_matches(self):
        """The committed config loads and carries its own digest."""
        reference = config_ref()
        with open(admission.CONFIG_PATH, "rb") as handle:
            data = handle.read()
        self.assertEqual(reference["sha256"],
                         hashlib.sha256(data).hexdigest())
        self.assertEqual(reference["path"],
                         os.path.join("config", "phase3_closure.json"))

    def test_changed_config_is_refused(self):
        """Any policy difference, or non-JSON bytes, fails closed."""
        with open(admission.CONFIG_PATH, "rb") as handle:
            config = json.loads(handle.read())
        variants = []
        widened = copy.deepcopy(config)
        widened["claim_policy"]["allowed"].append("causal_attribution")
        variants.append(widened)
        dropped = copy.deepcopy(config)
        dropped["claim_policy"]["prohibited"].pop()
        variants.append(dropped)
        moved = copy.deepcopy(config)
        moved["supersedes"][0]["current"] = "unknown"
        variants.append(moved)
        missing = copy.deepcopy(config)
        del missing["held_out_definition"]
        variants.append(missing)
        for variant in variants:
            with self.subTest(variant=len(variants)):
                with self.assertRaisesRegex(admission.AdmissionError,
                                            "config_mismatch"):
                    admission.load_config(json.dumps(variant).encode())
        with self.assertRaisesRegex(admission.AdmissionError,
                                    "config_not_json"):
            admission.load_config(b"\xff not json")

    def test_real_plan_is_fully_pinned(self):
        """Every real input and identity is pinned; output is protected."""
        plan = admission.real_plan()
        self.assertIsNone(admission.plan_problem(plan))
        self.assertTrue(guard.is_within(plan["output_dir"],
                                        plan["protected_root"], True))
        self.assertTrue(plan["output_dir"].endswith(
            "\\model-provenance\\phase3-closure-v1"))
        self.assertEqual(plan["inputs"]["binding_summary"]["sha256"],
                         admission.BINDING_SUMMARY_SHA256)
        broken = copy.deepcopy(plan)
        broken["inputs"]["binding_cases"]["sha256"] = None
        self.assertEqual(admission.plan_problem(broken),
                         "plan_pin_missing:binding_cases")
        broken = copy.deepcopy(plan)
        del broken["identities"]["train"]
        self.assertEqual(admission.plan_problem(broken),
                         "plan_identities_unexpected")


class TagTests(unittest.TestCase):
    """Attachment, score and narration rules."""

    def test_attachment_classes(self):
        """Visits reached by a concept node decide its class."""
        self.assertEqual(admission.attachment(None, 3), admission.NOT_A_NODE)
        self.assertEqual(admission.attachment(set(), 3),
                         admission.NOT_ATTACHED)
        self.assertEqual(admission.attachment({3}, 3), admission.TARGET_ONLY)
        self.assertEqual(admission.attachment({1, 3}, 3),
                         admission.TARGET_AND_EARLIER)
        self.assertEqual(admission.attachment({0, 1}, 3),
                         admission.EARLIER_ONLY)

    def test_score_classes(self):
        """Positive and zero scores are classified; others fail."""
        self.assertEqual(admission.score_class(0.25), admission.POSITIVE)
        self.assertEqual(admission.score_class(0.0), admission.ZERO)
        for bad in (-0.1, float("nan"), float("inf"), 1, None):
            with self.subTest(bad=bad):
                with self.assertRaises(admission.AdmissionError):
                    admission.score_class(bad)

    def test_driver_tags(self):
        """Target-only conditions are excluded; no driver value is copied."""
        record = {"drivers": {
            "condition": [
                {"vocab_index": 1, "concept_id": 11, "name": "SYNTHETIC A",
                 "score": 0.0},
                {"vocab_index": 2, "concept_id": 12, "name": "SYNTHETIC B",
                 "score": 0.5}],
            "procedure": [
                {"vocab_index": 3, "concept_id": 13, "name": "SYNTHETIC C",
                 "score": 0.2}],
            "drug": [
                {"vocab_index": 4, "concept_id": None, "name": "",
                 "score": 0.1}]}}
        attachments = {"condition": [admission.TARGET_ONLY,
                                     admission.EARLIER_ONLY],
                       "procedure": [admission.TARGET_ONLY],
                       "drug": [admission.NOT_A_NODE]}
        inverse = {"condition": {1: 11, 2: 12}, "procedure": {3: 13},
                   "drug": {}}
        tags, mismatches = admission.tag_drivers(record, attachments,
                                                 inverse)
        self.assertEqual(mismatches, 0)
        outcome, earlier = tags["condition"]
        self.assertTrue(outcome["target_outcome_concept"])
        self.assertFalse(outcome["narration_eligible"])
        self.assertEqual(outcome["score_class"], admission.ZERO)
        self.assertTrue(earlier["narration_eligible"])
        procedure = tags["procedure"][0]
        self.assertFalse(procedure["target_outcome_concept"])
        self.assertTrue(procedure["narration_eligible"])
        self.assertFalse(tags["drug"][0]["narration_eligible"])
        text = json.dumps(tags)
        for value in ("SYNTHETIC", "vocab_index", "concept_id", "0.5"):
            self.assertNotIn(value, text)
        inverse["condition"][2] = 99
        _, mismatches = admission.tag_drivers(record, attachments, inverse)
        self.assertEqual(mismatches, 1)
        with self.assertRaisesRegex(admission.AdmissionError, "count"):
            admission.tag_drivers(record, dict(attachments, drug=[]),
                                  inverse)
        with self.assertRaisesRegex(admission.AdmissionError, "invalid"):
            admission.tag_drivers(record, dict(attachments, drug=["other"]),
                                  inverse)


class ClosureTests(unittest.TestCase):
    """Cross-checks, typed admission and aggregate output."""

    def test_invented_closure(self):
        """Every invented case is admitted; the summary is aggregate only."""
        plan, raw, documents, attachments, _ = bundle()
        closure, summary = rebuild(plan, raw, documents, attachments)
        counts = summary["counts"]
        self.assertEqual((counts["cases"], counts["admitted"],
                          counts["label_layer_b_matches"],
                          counts["explanation_eligible"]), (10, 10, 10, 10))
        self.assertEqual(counts["batch_sensitive"], 1)
        self.assertEqual(sum(counts["upstream_partition"].values()), 10)
        self.assertEqual(summary["closure_file"]["sha256"],
                         hashlib.sha256(binding.encode(closure)).hexdigest())
        body = {key: value for key, value in closure.items()
                if key != "preparation"}
        self.assertEqual(closure["preparation"]["fingerprint_sha256"],
                         admission.amp.digest(body))
        text = json.dumps(summary)
        for value in ("patient_index", "graph_position", "person_id"):
            self.assertNotIn(value, text)
        roles = {item["role"] for entry in closure["supersedes"]
                 for item in entry["evidence"]}
        self.assertLessEqual(roles - {"label_layers"},
                             set(admission.INPUT_ROLES))
        for entry in closure["supersedes"]:
            for item in entry["evidence"]:
                if "sha256" in item:
                    self.assertEqual(item["sha256"],
                                     plan["inputs"][item["role"]]["sha256"])
        batch_case = closure["cases"][4]
        self.assertIn("batch_sensitive_prediction",
                      batch_case["admission"]["restrictions"])
        again, _ = rebuild(plan, raw, documents, attachments)
        self.assertEqual(again, closure)

    def test_label_decode_failure_quarantines(self):
        """A case whose concept no longer decodes is quarantined."""
        plan, raw, documents, attachments, _ = bundle()
        index = documents["outputs"][0]["predicted_diagnosis"]["label_index"]
        documents["label_vocab"][index] = 1
        refresh(plan, raw, documents, "label_vocab")
        closure, summary = rebuild(plan, raw, documents, attachments)
        quarantined = [case for case in closure["cases"]
                       if case["admission"]["status"] == admission.QUARANTINED]
        self.assertTrue(quarantined)
        for case in quarantined:
            self.assertEqual(case["label_evidence"]["correspondence"],
                             "not_established")
            self.assertFalse(case["explanation_eligible"])
        self.assertLess(summary["counts"]["admitted"], 10)

    def test_unverified_binding_blocks_closure(self):
        """A failed, tampered or unbound run-binding record fails closed."""
        cases = []
        plan, raw, documents, attachments, _ = bundle()
        documents["binding_summary"]["status"] = binding.FAILED
        cases.append(((plan, raw, documents, attachments),
                      "binding_not_verified"))
        plan, raw, documents, attachments, _ = bundle()
        documents["binding_summary"]["checks"][
            "pos_weight_bitwise_equal"] = False
        cases.append(((plan, raw, documents, attachments),
                      "binding_not_verified"))
        plan, raw, documents, attachments, _ = bundle()
        documents["binding_summary"]["cases_file"]["sha256"] = "0" * 64
        cases.append(((plan, raw, documents, attachments),
                      "binding_fingerprint_mismatch"))
        plan, raw, documents, attachments, _ = bundle()
        plan["inputs"]["graph"]["sha256"] = "a" * 64
        cases.append(((plan, raw, documents, attachments),
                      "mapping_pinned_identity_mismatch"))
        plan, raw, documents, attachments, _ = bundle()
        documents["binding_cases"]["cases"][2]["patient_index"] += 100
        cases.append(((plan, raw, documents, attachments),
                      "binding_case_mismatch"))
        plan, raw, documents, attachments, _ = bundle()
        case = documents["binding_cases"]["cases"][2]
        case["upstream_partition"] = ("train" if case["upstream_partition"]
                                      != "train" else "test")
        refresh(plan, raw, documents, "binding_cases")
        cases.append(((plan, raw, documents, attachments),
                      "binding_cases_mismatch"))
        for (arguments, code) in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(admission.AdmissionError, code):
                    rebuild(*arguments)

    def test_record_chain_is_cross_checked(self):
        """Outputs, provenance, wrapper and split records must agree."""
        cases = []
        plan, raw, documents, attachments, _ = bundle()
        documents["outputs"][0]["drivers"]["condition"][0]["score"] = 9.0
        cases.append(((plan, raw, documents, attachments),
                      "provenance_record_mismatch"))
        plan, raw, documents, attachments, _ = bundle()
        documents["provenance"]["status"] = "draft"
        cases.append(((plan, raw, documents, attachments),
                      "provenance_unexpected"))
        plan, raw, documents, attachments, _ = bundle()
        documents["provenance"]["counts"]["records"] = 99
        cases.append(((plan, raw, documents, attachments),
                      "provenance_fingerprint_mismatch"))
        plan, raw, documents, attachments, _ = bundle()
        documents["wrapper_recovery"]["diff"][
            "applies_to_executed_and_reproduces_current"] = False
        cases.append(((plan, raw, documents, attachments),
                      "wrapper_recovery_unexpected"))
        plan, raw, documents, attachments, _ = bundle()
        documents["split_v2"]["bindings"]["step21"]["sha256"] = "b" * 64
        cases.append(((plan, raw, documents, attachments),
                      "split_sidecar_unexpected"))
        plan, raw, documents, attachments, _ = bundle()
        documents["concept_vocab"]["condition"] = {}
        cases.append(((plan, raw, documents, attachments),
                      "driver_decode_mismatch"))
        plan, raw, documents, attachments, _ = bundle()
        cases.append(((plan, raw, documents, attachments[:-1]),
                      "driver_attachment_count"))
        for (arguments, code) in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(admission.AdmissionError, code):
                    rebuild(*arguments)

    def test_synthetic_mode(self):
        """The default mode builds an invented closure and prints counts."""
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(admission.main([]), 0)
        text = stream.getvalue()
        self.assertIn("Status             : closed", text)
        self.assertIn("cases admitted     : 10/10", text)
        self.assertNotIn("patient_index", text)


class FakeGraphRuntime:
    """In-memory stand-in for the raw-graph reader."""

    calls = 0

    def attachments(self, streams, selection, outputs, step19):
        """Return invented attachments after re-verifying the stream."""
        FakeGraphRuntime.calls += 1
        streams["graph"].verify()
        return admission.synthetic_attachments(outputs)


class ScratchRunTests(unittest.TestCase):
    """End-to-end real-mode orchestration over invented scratch inputs."""

    def setUp(self):
        """Write the invented inputs into a workspace scratch tree."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT, True),
                        "test scratch space must stay in the workspace root")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="phase3-closure-", dir=root)
        self.addCleanup(self.remove_scratch)
        inputs = os.path.join(self.work, "protected", "inputs")
        os.makedirs(inputs)
        self.plan, _, _, _, files = bundle()
        for role, data in files.items():
            path = os.path.join(inputs, role + ".bin")
            with open(path, "wb") as handle:
                handle.write(data)
            self.plan["inputs"][role]["path"] = path
        self.plan["protected_root"] = os.path.join(self.work, "protected")
        self.plan["output_dir"] = os.path.join(self.work, "protected",
                                               "model-provenance", "closure")
        FakeGraphRuntime.calls = 0

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def hashes(self):
        """Return the current SHA-256 of every invented input."""
        return {role: mapping.sha256_file(entry["path"])
                for role, entry in self.plan["inputs"].items()}

    def test_publishes_closure_and_summary_once(self):
        """Two files are published atomically; a rerun refuses."""
        before = self.hashes()
        summary, digests = admission.run_real(
            self.plan, runtime_factory=FakeGraphRuntime)
        out = self.plan["output_dir"]
        self.assertEqual(sorted(os.listdir(out)),
                         [admission.CLOSURE_NAME, admission.SUMMARY_NAME])
        for name, value in digests.items():
            self.assertEqual(mapping.sha256_file(os.path.join(out, name)),
                             value)
        self.assertEqual(summary["closure_file"]["sha256"],
                         digests[admission.CLOSURE_NAME])
        self.assertEqual(summary["counts"]["admitted"], 10)
        self.assertEqual(FakeGraphRuntime.calls, 1)
        self.assertEqual(before, self.hashes())
        with self.assertRaisesRegex(admission.AdmissionError,
                                    "output_exists"):
            admission.run_real(self.plan, runtime_factory=FakeGraphRuntime)

    def test_changed_input_publishes_nothing(self):
        """A pin mismatch fails before the graph is read."""
        with open(self.plan["inputs"]["split_v1"]["path"], "ab") as handle:
            handle.write(b" ")
        with self.assertRaisesRegex(admission.AdmissionError,
                                    "input_hash_mismatch:split_v1"):
            admission.run_real(self.plan, runtime_factory=FakeGraphRuntime)
        self.assertEqual(FakeGraphRuntime.calls, 0)
        for path in (self.plan["output_dir"],
                     self.plan["output_dir"] + admission.PARTIAL):
            self.assertFalse(os.path.lexists(path))

    def test_output_boundaries(self):
        """Existing, unprotected and input-overlapping outputs are refused."""
        os.makedirs(self.plan["output_dir"] + admission.PARTIAL)
        with self.assertRaisesRegex(admission.AdmissionError,
                                    "output_exists"):
            admission.run_real(self.plan, runtime_factory=FakeGraphRuntime)
        os.rmdir(self.plan["output_dir"] + admission.PARTIAL)
        inside = copy.deepcopy(self.plan)
        inside["output_dir"] = os.path.join(self.work, "protected", "inputs",
                                            "closure")
        with self.assertRaisesRegex(admission.AdmissionError,
                                    "overlaps_input"):
            admission.run_real(inside, runtime_factory=FakeGraphRuntime)
        outside = copy.deepcopy(self.plan)
        outside["output_dir"] = os.path.join(self.work, "elsewhere")
        with self.assertRaisesRegex(admission.AdmissionError,
                                    "not_protected"):
            admission.run_real(outside, runtime_factory=FakeGraphRuntime)


if __name__ == "__main__":
    unittest.main()
