"""Synthetic checks for ehr_evidence_app.gnn.provenance (Step 21).

Every fixture is invented: invented Step 17 mapping, Step 19 summary and
Step 20 outputs, agreement and manifest (built by the Step 20 manifest
builder over an invented asset plan). No real mapping, output, manifest,
graph, checkpoint or patient record is opened. Scratch files are created
only under the configured workspace test temp directory.
"""

import copy
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.gnn import provenance as amp  # noqa: E402
from ehr_evidence_app.gnn import mapping as mapping  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402
from ehr_evidence_app.gnn import outputs as pmo  # noqa: E402

PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
PILOT = list(amp.SYNTHETIC_PILOT)
MARKER = "SYNTHETIC_STEP21_MARKER"


def configured_test_temp():
    """Return the configured workspace test scratch directory (workspace-only rules)."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def config_ref():
    """Return the parsed reference of the repository config."""
    return amp.load_config(amp.read_config_bytes(amp.CONFIG_PATH))


def documents():
    """Return fresh invented Step 17, 19 and 20 documents."""
    return amp.synthetic_documents(amp.synthetic_step20_plan())


def build(plan, raw):
    """Build a sidecar from invented bytes with the repository config."""
    return amp.build_sidecar(plan, raw, config_ref())


def repin(plan, raw, role, data):
    """Replace one role's bytes and pin the plan to them."""
    raw[role] = data
    plan["inputs"][role]["sha256"] = hashlib.sha256(data).hexdigest()
    plan["inputs"][role].pop("size_bytes", None)


def edit_manifest(plan, raw, change):
    """Apply change() to the invented manifest and re-pin its bytes."""
    manifest = json.loads(raw["step20_manifest"])
    change(manifest)
    repin(plan, raw, "step20_manifest", pmo.encode(manifest, sort_keys=True))


def record_by_index(docs, index):
    """Return the invented Step 17 record with a given patient_index."""
    for record in docs["step17_mapping"]["records"]:
        if record["patient_index"] == index:
            return record
    raise KeyError(index)


class BuildTestCase(unittest.TestCase):
    """A consistent invented preparation builds the expected sidecar."""

    def setUp(self):
        """Build one invented preparation."""
        self.docs = documents()
        self.plan, self.raw = amp.synthetic_bundle(documents=self.docs)
        self.sidecar = build(self.plan, self.raw)

    def test_ten_records_in_order(self):
        """Exactly the ten outputs, in ascending patient_index order."""
        records = self.sidecar["records"]
        self.assertEqual([item["patient_index"] for item in records], PILOT)
        self.assertEqual(self.sidecar["counts"], {
            "records": 10, "visit_nodes": 23, "empty_history_records": 3})
        outputs = self.docs["step20_outputs"]
        for item, output in zip(records, outputs):
            self.assertEqual(item["step20_record"]["sha256"],
                             amp.digest(output))
            mapped = record_by_index(self.docs, item["patient_index"])
            self.assertEqual(item["graph_position"],
                             mapped["graph_position"])
            self.assertEqual(item["patient_identity"], {
                "person_id": mapped["person_id"],
                "subject_id": mapped["subject_id"],
                "identity_status": "verified"})
            self.assertEqual(item["step17_mapping"]["sha256"],
                             hashlib.sha256(
                                 self.raw["step17_mapping"]).hexdigest())
            self.assertEqual(item["step17_mapping"]["schema"],
                             mapping.SCHEMA)

    def test_target_and_ordered_history(self):
        """Target is N-1; history is 0..N-2 in order; N=1 is empty."""
        seen_empty = False
        for item in self.sidecar["records"]:
            nodes = record_by_index(self.docs,
                                    item["patient_index"])["visit_nodes"]
            count = len(nodes)
            self.assertEqual(item["visit_node_count"], count)
            self.assertEqual(item["target"]["position"], count - 1)
            self.assertEqual(item["target"]["visit_occurrence_id"],
                             nodes[-1]["visit_occurrence_id"])
            history = item["pre_target_history"]
            self.assertEqual([visit["position"] for visit in
                              history["visits"]], list(range(count - 1)))
            self.assertEqual([visit["visit_occurrence_id"]
                              for visit in history["visits"]],
                             [node["visit_occurrence_id"]
                              for node in nodes[:-1]])
            self.assertEqual(history["count"], count - 1)
            self.assertIs(history["empty"], count == 1)
            if count == 1:
                seen_empty = True
                self.assertEqual(history["visits"], [])
                self.assertNotIn(
                    str(item["target"]["visit_occurrence_id"]),
                    json.dumps(history))
        self.assertTrue(seen_empty)

    def test_flags_and_limitations(self):
        """Every required flag is present with its exact value and type."""
        for key, value in amp.REQUIRED_FLAGS.items():
            self.assertIn(key, self.sidecar)
            self.assertIs(type(self.sidecar[key]), type(value), key)
            self.assertEqual(self.sidecar[key], value, key)
        self.assertIs(self.sidecar["phase4_eligible"], False)
        self.assertIs(self.sidecar["history_only_claim_allowed"], False)
        self.assertEqual(self.sidecar["source_event_linkage"], "absent")
        self.assertEqual(self.sidecar["upstream_split_membership"],
                         "unknown")
        self.assertEqual(self.sidecar["label_index_correspondence"],
                         "not_established")
        self.assertEqual(self.sidecar["target_visit_inputs_remaining"], [
            "final-visit visit.x features", "final-visit procedure edges",
            "final-visit drug edges"])
        for item in self.sidecar["records"]:
            self.assertIs(item["phase4_eligible"], False)
            self.assertIs(item["history_only_claim_allowed"], False)
            self.assertIs(item["history_attribution_assigned"], False)
        text = json.dumps(self.sidecar["limitations"])
        self.assertIn("not archived", text)
        self.assertIn("split membership", text)

    def test_no_inherited_values_copied(self):
        """No prediction, driver or score value reaches the sidecar."""
        text = amp.encode(self.sidecar).decode("utf-8")
        for word in ("predicted_diagnosis", "drivers", "logit",
                     "probability", "vocab_index", "SYNTHETIC LABEL",
                     "SYNTHETIC CONDITION", "\"score\""):
            self.assertNotIn(word, text)
        for output in self.docs["step20_outputs"]:
            prediction = output["predicted_diagnosis"]
            self.assertNotIn(repr(prediction["logit"]), text)
            self.assertNotIn(str(prediction["concept_id"]), text)

    def test_wrapper_identities_distinct(self):
        """Executed and current wrapper identities are separate and honest."""
        wrapper = self.sidecar["step20"]["wrapper"]
        self.assertEqual(wrapper["executed"], {
            "sha256": self.plan["executed_wrapper_sha256"],
            "bytes_available": False, "retrievable": False})
        self.assertEqual(wrapper["current_source"]["sha256"],
                         hashlib.sha256(
                             self.raw["step20_wrapper_current"]).hexdigest())
        self.assertNotEqual(wrapper["current_source"]["sha256"],
                            wrapper["executed"]["sha256"])
        self.assertIs(wrapper["current_source_resolves_to_executed_hash"],
                      False)
        self.assertIn("not retrievable", wrapper["limitation"])

    def test_upstream_identities_carried_forward(self):
        """Step 13/17/18/19/20 identities and assets are carried exactly."""
        manifest = json.loads(self.raw["step20_manifest"])
        step20 = self.sidecar["step20"]
        self.assertEqual(step20["asset_inventory"], manifest["assets"])
        self.assertEqual(step20["asset_inventory"],
                         self.plan["step20_assets"])
        self.assertEqual(step20["settings"], manifest["settings"])
        self.assertEqual(step20["runtime"], manifest["runtime"])
        self.assertEqual(step20["selection"], manifest["selection"])
        for role, key in (("step20_outputs", "outputs_sha256"),
                          ("step20_agreement", "agreement_sha256"),
                          ("step20_manifest", "manifest_sha256")):
            self.assertEqual(step20[key], hashlib.sha256(
                self.raw[role]).hexdigest())
        self.assertEqual(set(self.sidecar["model_versions"]),
                         set(amp.MODEL_VERSION_ROLES))
        for name, entry in self.sidecar["model_versions"].items():
            self.assertEqual(entry, manifest["assets"][name])
        step19 = self.sidecar["step19_preprocessing"]
        summary = self.docs["step19_summary"]
        self.assertEqual(step19["model_input_digest"],
                         summary["model_input_digest"])
        self.assertEqual((step19["graphs_total"], step19["graphs_filtered"]),
                         (24, 20))
        self.assertEqual(self.sidecar["step13_registration"]["sha256"],
                         hashlib.sha256(
                             self.raw["step13_registration"]).hexdigest())
        policy = self.sidecar["step18_policy"]
        self.assertEqual(policy["document"]["sha256"], hashlib.sha256(
            self.raw["step18_policy"]).hexdigest())
        self.assertEqual(policy["pinned_sources"],
                         self.plan["policy_sources"])
        self.assertEqual(
            self.sidecar["step17_mapping"]["rules_doc"]["sha256"],
            hashlib.sha256(self.raw["step17_rules_doc"]).hexdigest())
        self.assertEqual(set(self.sidecar["inputs"]), set(amp.INPUT_ROLES))


class BijectionTestCase(unittest.TestCase):
    """Missing, duplicate, ambiguous and cross-patient links all fail."""

    def setUp(self):
        """Start from fresh invented documents."""
        self.docs = documents()
        self.records = self.docs["step17_mapping"]["records"]
        self.outputs = self.docs["step20_outputs"]

    def expect(self, code):
        """Assert that building from the edited documents fails with code."""
        plan, raw = amp.synthetic_bundle(documents=self.docs)
        with self.assertRaisesRegex(amp.Step21Error, "^%s$" % code):
            build(plan, raw)

    def test_output_count(self):
        """Nine or eleven outputs fail."""
        saved = self.outputs.pop()
        self.expect("output_record_count")
        self.outputs.extend([saved, amp.synthetic_output(14)])
        self.expect("output_record_count")

    def test_duplicate_output_index(self):
        """Two outputs for one patient_index fail."""
        self.outputs[1] = amp.synthetic_output(self.outputs[0][
            "patient_index"])
        self.expect("output_patient_index_repeated")

    def test_output_without_mapping(self):
        """An output with no Step 17 record fails (no renumbering)."""
        self.outputs[-1] = amp.synthetic_output(99)
        self.expect("output_without_unique_mapping")

    def test_output_order_and_schema(self):
        """Out-of-order or schema-breaking outputs fail."""
        self.outputs.reverse()
        self.expect("output_order_unexpected")
        self.outputs.reverse()
        self.outputs[0]["extra"] = 1
        self.expect("output_schema_invalid")

    def test_mapping_count_and_duplicate(self):
        """A missing or duplicated Step 17 record fails."""
        record = record_by_index(self.docs, 12)
        record["patient_index"] = 13
        self.expect("mapping_patient_index_repeated")
        record["patient_index"] = 12
        self.records.remove(record)
        self.expect("mapping_record_count")

    def test_unverified_or_ambiguous_mapping(self):
        """Quarantined, flagged or finding-bearing records fail."""
        record = record_by_index(self.docs, 6)
        record["identity_status"] = mapping.STATUS_QUARANTINED
        self.expect("mapping_record_not_verified")
        record["identity_status"] = mapping.STATUS_REPEATED
        self.expect("mapping_record_not_verified")
        record["identity_status"] = mapping.STATUS_VERIFIED
        record["findings"] = ["graph_match_multiple"]
        self.expect("mapping_record_has_findings")
        record["findings"] = []
        record["visit_nodes"][0]["repeated_visit_node"] = True
        self.expect("mapping_record_repeated_visits")
        record["visit_nodes"][0]["repeated_visit_node"] = False
        record["phase4_eligible"] = True
        self.expect("mapping_phase4_flag")

    def test_cross_person_identity(self):
        """Shared person, subject or visit identities across records fail."""
        first, second = record_by_index(self.docs, 3), record_by_index(
            self.docs, 4)
        saved = second["person_id"]
        second["person_id"] = first["person_id"]
        self.expect("cross_person_identity")
        second["person_id"] = saved
        second["subject_id"] = first["subject_id"]
        self.expect("cross_person_identity")

    def test_cross_person_visit(self):
        """One source visit mapped for two persons fails."""
        first, second = record_by_index(self.docs, 3), record_by_index(
            self.docs, 4)
        visit = first["visit_nodes"][0]["visit_occurrence_id"]
        second["visit_nodes"][0].update(visit_occurrence_id=visit,
                                        hadm_id=visit)
        self.expect("cross_person_visit_link")

    def test_invalid_visit_nodes(self):
        """Gaps, identity mismatches, bad links and empty nodes fail."""
        record = record_by_index(self.docs, 6)
        node = record["visit_nodes"][1]
        node["node_position"] = 5
        self.expect("visit_node_position_invalid")
        node["node_position"] = 1
        node["hadm_id"] = node["visit_occurrence_id"] + 1
        self.expect("visit_node_identity_invalid")
        node["hadm_id"] = str(node["visit_occurrence_id"])
        self.expect("visit_node_identity_invalid")
        node["hadm_id"] = node["visit_occurrence_id"]
        node["link_status"] = "visit_link_missing"
        self.expect("mapping_visit_nodes_invalid")
        node["link_status"] = mapping.STATUS_VERIFIED
        record["visit_nodes"] = []
        self.expect("mapping_visit_nodes_invalid")

    def test_repeated_visit_nodes(self):
        """An unflagged repeated visit fails; a repeated target conflicts."""
        record = record_by_index(self.docs, 6)
        nodes = record["visit_nodes"]
        first = nodes[0]["visit_occurrence_id"]
        nodes[1].update(visit_occurrence_id=first, hadm_id=first)
        self.expect("visit_node_repeated")
        self.docs = documents()
        nodes = record_by_index(self.docs, 6)["visit_nodes"]
        first = nodes[0]["visit_occurrence_id"]
        nodes[-1].update(visit_occurrence_id=first, hadm_id=first)
        self.expect("target_history_conflict")

    def test_graph_links(self):
        """Inconsistent or out-of-range graph positions fail."""
        record_by_index(self.docs, 10)["graph_position"] = 10
        self.expect("graph_link_inconsistent")
        self.docs = documents()
        first, second = record_by_index(self.docs, 12), record_by_index(
            self.docs, 13)
        first["graph_position"], second["graph_position"] = (
            second["graph_position"], first["graph_position"])
        self.expect("graph_link_inconsistent")
        self.docs = documents()
        self.docs["step19_summary"]["counts"]["graphs_total"] = 16
        self.expect("graph_link_out_of_range")


class TamperTestCase(unittest.TestCase):
    """Tampered or mismatched inputs fail closed."""

    def setUp(self):
        """Build one consistent invented preparation."""
        self.plan, self.raw = amp.synthetic_bundle()

    def expect(self, code):
        """Assert that building fails with code."""
        with self.assertRaisesRegex(amp.Step21Error, "^%s$" % code):
            build(self.plan, self.raw)

    def test_pinned_hash_and_size(self):
        """Every role's bytes must match its pinned SHA-256 and size."""
        for role in amp.INPUT_ROLES:
            plan = copy.deepcopy(self.plan)
            plan["inputs"][role]["sha256"] = "0" * 64
            with self.assertRaisesRegex(
                    amp.Step21Error,
                    "^input_identity:input_hash_mismatch:%s$" % role):
                build(plan, self.raw)
        self.plan["inputs"]["step17_mapping"]["size_bytes"] += 1
        self.expect("input_identity:input_size_mismatch:step17_mapping")

    def test_missing_role_and_bad_json(self):
        """A missing role or unparsable JSON fails."""
        raw = dict(self.raw)
        del raw["step13_registration"]
        with self.assertRaisesRegex(amp.Step21Error,
                                    "^input_roles_unexpected$"):
            build(self.plan, raw)
        repin(self.plan, self.raw, "step17_mapping", b"not json")
        self.expect("input_not_json:step17_mapping")

    def test_outputs_differ_from_manifest(self):
        """Outputs or agreement bytes not recorded by the manifest fail."""
        outputs = json.loads(self.raw["step20_outputs"])
        outputs[0]["predicted_diagnosis"]["logit"] += 1.0
        repin(self.plan, self.raw, "step20_outputs",
              pmo.encode(outputs, sort_keys=False))
        self.expect("step20_manifest_file_mismatch:outputs.json")
        self.plan, self.raw = amp.synthetic_bundle()
        repin(self.plan, self.raw, "step20_agreement",
              self.raw["step20_agreement"] + b"\n")
        self.expect("step20_manifest_file_mismatch:canonical-agreement.json")

    def test_asset_inventory_mismatch(self):
        """A manifest asset differing from the pinned inventory fails."""
        edit_manifest(self.plan, self.raw, lambda manifest: manifest[
            "assets"]["checkpoint"].update(sha256="1" * 64))
        self.expect("step20_asset_inventory_mismatch")
        self.plan, self.raw = amp.synthetic_bundle()
        self.plan["step20_assets"]["label_vocab"]["size_bytes"] += 1
        self.expect("step20_asset_inventory_mismatch")

    def test_upstream_reference_mismatch(self):
        """The Step 17/19 inputs must be the ones Step 20 recorded."""
        for role in ("step17_mapping", "step19_summary"):
            plan, raw = amp.synthetic_bundle()
            plan["inputs"][role]["path"] = "W:\\synthetic\\other.json"
            with self.assertRaisesRegex(
                    amp.Step21Error,
                    "^step20_upstream_reference_mismatch:%s$" % role):
                build(plan, raw)

    def test_manifest_flags_and_settings(self):
        """Changed flags, settings or counts in the manifest fail."""
        cases = ((("phase4_eligible",), True, "step20_manifest_field:"
                  "phase4_eligible"),
                 (("phase4_eligible",), 0, "step20_manifest_field:"
                  "phase4_eligible"),
                 (("upstream_split_membership",), "test",
                  "step20_manifest_field:upstream_split_membership"),
                 (("history_only_claim_allowed",), True,
                  "step20_manifest_field:history_only_claim_allowed"),
                 (("canonical_agreement",), "failed",
                  "step20_manifest_field:canonical_agreement"),
                 (("output_version",), "step-20-other",
                  "step20_manifest_field:output_version"),
                 (("settings", "top_k"), 5,
                  "step20_manifest_settings_unexpected"),
                 (("counts", "published_records"), 9,
                  "step20_manifest_counts_unexpected"))
        for path, value, code in cases:
            plan, raw = amp.synthetic_bundle()

            def change(manifest, path=path, value=value):
                """Set one invented manifest value."""
                target = manifest
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
            edit_manifest(plan, raw, change)
            with self.assertRaisesRegex(amp.Step21Error, "^%s$" % code):
                build(plan, raw)

    def test_agreement_and_step19(self):
        """A failed agreement or mismatched Step 19 summary fails."""
        docs = documents()
        docs["step20_agreement"]["mismatch_counts"]["float_mismatch"] = 1
        self.plan, self.raw = amp.synthetic_bundle(documents=docs)
        self.expect("step20_agreement_unexpected")
        for key, value in (("graph_sha256", "0" * 64),
                           ("status", "failed"),
                           ("phase4_eligible", True)):
            docs = documents()
            docs["step19_summary"][key] = value
            self.plan, self.raw = amp.synthetic_bundle(documents=docs)
            self.expect("step19_summary_unexpected")

    def test_mapping_and_policy_sources(self):
        """Mapping graph/bridge and policy source hashes must agree."""
        docs = documents()
        docs["step17_mapping"]["inputs"]["graph"]["sha256"] = "0" * 64
        self.plan, self.raw = amp.synthetic_bundle(documents=docs)
        self.expect("mapping_pinned_identity_mismatch")
        docs = documents()
        docs["step17_mapping"]["inputs"]["builder"]["sha256"] = "0" * 64
        self.plan, self.raw = amp.synthetic_bundle(documents=docs)
        self.expect("policy_source_mismatch")
        self.plan, self.raw = amp.synthetic_bundle()
        self.plan["policy_sources"]["model"] = "0" * 64
        self.expect("policy_source_mismatch")
        docs = documents()
        docs["step17_mapping"]["step"] = "18"
        self.plan, self.raw = amp.synthetic_bundle(documents=docs)
        self.expect("mapping_schema_unexpected")

    def test_config_must_match(self):
        """A changed or unparsable config is refused."""
        with open(amp.CONFIG_PATH, "rb") as handle:
            data = handle.read()
        config = json.loads(data)
        config["required_flags"]["phase4_eligible"] = True
        with self.assertRaisesRegex(amp.Step21Error, "^config_mismatch$"):
            amp.load_config(json.dumps(config).encode())
        with self.assertRaisesRegex(amp.Step21Error, "^config_not_json$"):
            amp.load_config(b"{")
        self.assertEqual(amp.load_config(data)["sha256"],
                         hashlib.sha256(data).hexdigest())


class FingerprintTestCase(unittest.TestCase):
    """The preparation fingerprint is deterministic and version-sensitive."""

    def version(self, plan, raw, ref=None):
        """Return the preparation block of one build."""
        return amp.build_sidecar(plan, raw, ref or config_ref())[
            "preparation"]

    def test_deterministic(self):
        """Equal inputs give equal bytes and the documented fingerprint."""
        plan, raw = amp.synthetic_bundle()
        first = build(plan, raw)
        second = build(copy.deepcopy(plan), dict(raw))
        self.assertEqual(amp.encode(first), amp.encode(second))
        body = {key: value for key, value in first.items()
                if key != "preparation"}
        preparation = first["preparation"]
        self.assertEqual(preparation["fingerprint_sha256"],
                         hashlib.sha256(amp.canonical_json(body)).hexdigest())
        self.assertRegex(preparation["version"],
                         r"^step21-prep-[0-9a-f]{16}$")
        self.assertTrue(preparation["fingerprint_sha256"].startswith(
            preparation["version"][len("step21-prep-"):]))
        text = amp.encode(first).decode("utf-8").lower()
        for word in ("timestamp", "created", "generated_at", "hostname"):
            self.assertNotIn(word, text)

    def test_changes_across_preparations(self):
        """Any changed input, selection, policy or setting is visible."""
        base_plan, base_raw = amp.synthetic_bundle()
        versions = {"base": self.version(base_plan, base_raw)["version"]}

        plan, raw = amp.synthetic_bundle()
        edit_manifest(plan, raw, lambda manifest: manifest["settings"].update(
            graph_indexing="synthetic alternative"))
        versions["step20_setting"] = self.version(plan, raw)["version"]

        plan, raw = amp.synthetic_bundle()
        edit_manifest(plan, raw, lambda manifest: manifest["runtime"].update(
            torch="synthetic-2"))
        versions["runtime"] = self.version(plan, raw)["version"]

        plan, raw = amp.synthetic_bundle(
            extra={"step18_policy": b"# synthetic policy v2\n"})
        versions["policy"] = self.version(plan, raw)["version"]

        plan, raw = amp.synthetic_bundle(
            extra={"step20_wrapper_current": b"# synthetic wrapper v2\n"})
        versions["wrapper"] = self.version(plan, raw)["version"]

        plan, raw = amp.synthetic_bundle(
            extra={"step13_registration": b"{\"synthetic\": 2}\n"})
        versions["step13"] = self.version(plan, raw)["version"]

        docs = documents()
        for node in record_by_index(docs, 7)["visit_nodes"]:
            node["visit_occurrence_id"] += 5
            node["hadm_id"] += 5
        plan, raw = amp.synthetic_bundle(documents=docs)
        versions["selection"] = self.version(plan, raw)["version"]

        docs = documents()
        docs["step19_summary"]["model_input_digest"] = "e" * 64
        plan, raw = amp.synthetic_bundle(documents=docs)
        versions["step19"] = self.version(plan, raw)["version"]

        plan, raw = amp.synthetic_bundle()
        plan["executed_wrapper_sha256"] = "f" * 64
        versions["executed"] = self.version(plan, raw)["version"]

        ref = dict(config_ref(), sha256="a" * 64)
        versions["config"] = self.version(base_plan, base_raw, ref)[
            "version"]

        self.assertEqual(len(set(versions.values())), len(versions),
                         versions)


class ScratchTestCase(unittest.TestCase):
    """Invented inputs written to a workspace scratch directory."""

    def setUp(self):
        """Write invented pinned inputs into a workspace scratch directory."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT, True),
                        "test scratch space must stay in the workspace root")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="step-21-", dir=root)
        self.addCleanup(self.remove_scratch)
        inputs = os.path.join(self.work, "protected", "inputs")
        os.makedirs(inputs)
        self.plan, self.raw = amp.synthetic_bundle(input_dir=inputs)
        for role, data in self.raw.items():
            with open(self.plan["inputs"][role]["path"], "wb") as handle:
                handle.write(data)
        self.plan["protected_root"] = os.path.join(self.work, "protected")
        self.plan["output_dir"] = os.path.join(
            self.work, "protected", "model-provenance", "step-21-test")

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def input_hashes(self):
        """Return the current SHA-256 of every invented input file."""
        return {role: mapping.sha256_file(entry["path"])
                for role, entry in self.plan["inputs"].items()}

    def assert_nothing_published(self):
        """Assert no final or partial output directory exists."""
        for path in (self.plan["output_dir"],
                     self.plan["output_dir"] + amp.PARTIAL):
            self.assertFalse(os.path.lexists(path))


class PublicationTestCase(ScratchTestCase):
    """Fixed boundaries, no overwrite, atomic publication, immutable inputs."""

    def test_publishes_one_file_atomically(self):
        """A passing run publishes only provenance.json; inputs unchanged."""
        before = self.input_hashes()
        result = amp.attach(self.plan)
        out = self.plan["output_dir"]
        self.assertEqual(os.listdir(out), [amp.OUTPUT_NAME])
        self.assertFalse(os.path.lexists(out + amp.PARTIAL))
        with open(os.path.join(out, amp.OUTPUT_NAME), "rb") as handle:
            data = handle.read()
        self.assertEqual(result["sidecar_sha256"],
                         hashlib.sha256(data).hexdigest())
        sidecar = json.loads(data)
        expected = build(self.plan, self.raw)
        self.assertEqual(sidecar, expected)
        self.assertEqual(result["preparation_version"],
                         expected["preparation"]["version"])
        self.assertEqual(self.input_hashes(), before)
        self.assertEqual(result["records"], 10)
        self.assertIs(result["phase4_eligible"], False)

    def test_no_overwrite(self):
        """An existing final or partial directory is refused."""
        amp.attach(self.plan)
        with self.assertRaisesRegex(amp.Step21Error, "^output_exists$"):
            amp.attach(self.plan)
        self.plan["output_dir"] += "-second"
        os.makedirs(self.plan["output_dir"] + amp.PARTIAL)
        with self.assertRaisesRegex(amp.Step21Error, "^output_exists$"):
            amp.attach(self.plan)
        self.assertFalse(os.path.lexists(self.plan["output_dir"]))

    def test_output_boundaries(self):
        """Unprotected or input-overlapping outputs are refused."""
        self.plan["output_dir"] = os.path.join(self.work, "elsewhere", "out")
        with self.assertRaisesRegex(amp.Step21Error,
                                    "^output_not_protected$"):
            amp.attach(self.plan)
        self.plan["output_dir"] = os.path.join(
            os.path.dirname(self.plan["inputs"]["step20_outputs"]["path"]),
            "provenance")
        with self.assertRaisesRegex(amp.Step21Error,
                                    "^output_overlaps_input$"):
            amp.attach(self.plan)
        self.plan["output_dir"] = "C:\\synthetic\\out"
        with self.assertRaises(amp.Step21Error):
            amp.attach(self.plan)

    def test_identity_mismatch_publishes_nothing(self):
        """A hash or size mismatch in any role fails before publication."""
        for role in amp.INPUT_ROLES:
            saved = self.plan["inputs"][role]["sha256"]
            self.plan["inputs"][role]["sha256"] = "0" * 64
            with self.assertRaisesRegex(
                    amp.Step21Error,
                    "^input_identity:input_hash_mismatch:%s$" % role):
                amp.attach(self.plan)
            self.plan["inputs"][role]["sha256"] = saved
        self.plan["inputs"]["step17_mapping"]["size_bytes"] += 1
        with self.assertRaisesRegex(
                amp.Step21Error,
                "^input_identity:input_hash_mismatch:step17_mapping$"):
            amp.attach(self.plan)
        self.assert_nothing_published()

    def test_malformed_pin_refused_before_any_read(self):
        """A malformed pin refuses to start without reading anything."""
        self.plan["inputs"]["step20_manifest"]["sha256"] = "g" * 64
        with mock.patch.object(mapping, "read_verified",
                               side_effect=AssertionError("read")):
            with self.assertRaisesRegex(
                    amp.Step21Error,
                    "^pinned_hash_malformed:step20_manifest$"):
                amp.attach(self.plan)
        self.assert_nothing_published()

    def test_path_outside_anchor(self):
        """An input outside the storage anchor is refused."""
        self.plan["inputs"]["step13_registration"]["path"] = (
            "C:\\synthetic\\registration.json")
        with self.assertRaisesRegex(
                amp.Step21Error, "^input_identity:input_path_rejected$"):
            amp.attach(self.plan)
        self.assert_nothing_published()

    def test_step20_rewrite_before_publication_fails(self):
        """A same-size Step 20 rewrite with restored mtime publishes nothing."""
        path = self.plan["inputs"]["step20_outputs"]["path"]
        original = amp.build_sidecar

        def build_then_rewrite(plan, raw, ref):
            """Build, then rewrite the invented outputs file in place."""
            sidecar = original(plan, raw, ref)
            info = os.stat(path)
            with open(path, "r+b") as handle:
                handle.write(b"X" * info.st_size)
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
            return sidecar
        with mock.patch.object(amp, "build_sidecar", build_then_rewrite):
            with self.assertRaisesRegex(
                    amp.Step21Error,
                    "^input_identity:input_hash_mismatch:step20_outputs$"):
                amp.attach(self.plan)
        self.assert_nothing_published()

    def test_change_during_write_removes_partial(self):
        """An input changed while the partial is written publishes nothing."""
        path = self.plan["inputs"]["step20_manifest"]["path"]
        original = amp.encode

        def encode_then_touch(sidecar):
            """Encode, then move the invented manifest's mtime."""
            data = original(sidecar)
            info = os.stat(path)
            os.utime(path, ns=(info.st_atime_ns,
                               info.st_mtime_ns + 10 ** 9))
            return data
        with mock.patch.object(amp, "encode", encode_then_touch):
            with self.assertRaisesRegex(
                    amp.Step21Error,
                    "^input_identity:input_changed_during_read:"
                    "step20_manifest$"):
                amp.attach(self.plan)
        self.assert_nothing_published()


    def test_rename_failure_removes_partial(self):
        """A failed final rename leaves no final or partial path; retry works."""
        before = self.input_hashes()
        with mock.patch.object(amp.os, "rename",
                               side_effect=OSError(MARKER)):
            with self.assertRaisesRegex(amp.Step21Error,
                                        "^output_rename_failed$"):
                amp.attach(self.plan)
        self.assert_nothing_published()
        self.assertEqual(self.input_hashes(), before)
        amp.attach(self.plan)
        self.assertEqual(os.listdir(self.plan["output_dir"]),
                         [amp.OUTPUT_NAME])


class ModesTestCase(unittest.TestCase):
    """Default synthetic mode, the closed real gate and the pinned plan."""

    LINE = re.compile(r"^(status: passed|records: \d+|visit_nodes: \d+|"
                      r"empty_history_records: \d+|phase4_eligible: false|"
                      r"(alternative_)?preparation_version: "
                      r"step21-prep-[0-9a-f]{16}|"
                      r"fingerprint_sha256: [0-9a-f]{64})$")

    def forbid_protected_reads(self):
        """Make any guarded file access fail the test."""
        def refuse(*args, **kwargs):
            """Fail if a protected or pinned file is touched."""
            raise AssertionError("protected access")
        for name in ("regular_file_info", "sha256_file", "read_verified"):
            patcher = mock.patch.object(mapping, name, refuse)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_default_mode_is_synthetic_and_sanitised(self):
        """The default mode passes on invented data with sanitised lines."""
        self.forbid_protected_reads()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(amp.main([]), 0)
        lines = out.getvalue().splitlines()
        self.assertIn("status: passed", lines)
        self.assertIn("records: 10", lines)
        self.assertIn("phase4_eligible: false", lines)
        for line in lines:
            self.assertRegex(line, self.LINE)
        versions = [line.split(": ")[1] for line in lines
                    if "preparation_version" in line]
        self.assertEqual(len(versions), 2)
        self.assertNotEqual(versions[0], versions[1])

    def test_real_mode_gated_closed(self):
        """The real mode refuses before touching any file."""
        self.assertFalse(amp.REAL_MODE_ENABLED)
        self.forbid_protected_reads()
        with mock.patch.object(amp, "attach",
                               side_effect=AssertionError("attach")):
            with self.assertRaisesRegex(amp.Step21Error,
                                        "^real_mode_gated_closed$"):
                amp.run_real()
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(amp.main(["--operator-real"]), 1)
        self.assertEqual(out.getvalue().strip(),
                         "status: failed (real_mode_gated_closed)")

    def test_errors_are_sanitised(self):
        """Failures print a finding code; other messages are withheld."""
        def coded():
            """Raise an invented coded failure."""
            raise amp.Step21Error("cross_person_identity")

        def unexpected():
            """Raise an error whose message must not be printed."""
            raise ValueError(MARKER)
        for failing, expected in (
                (coded, "status: failed (cross_person_identity)"),
                (unexpected, "status: failed (ValueError, message "
                             "withheld)")):
            out = io.StringIO()
            with mock.patch.object(amp, "run_synthetic", failing):
                with redirect_stdout(out):
                    self.assertEqual(amp.main([]), 1)
            self.assertEqual(out.getvalue().strip(), expected)
            self.assertNotIn(MARKER, out.getvalue())

    def test_real_plan_pins(self):
        """The real plan pins the approved identities and fixed output."""
        plan = amp.real_plan()
        expected = {
            "step17_mapping": "D12DDB031DC6F8F5E8C271AF70A90D7161073D2EF48D"
                              "FC1DE2CC3AC31DADA18D",
            "step17_rules_doc": "C4835B72D744E007C7B56F59BA595528BD990CFA"
                                "BEFFC2FB175E0FA0D43A9487",
            "step18_policy": "795482BE9FE415BA5DAF5F0132194FB801C7BB5953"
                             "0D0224F39473432166DB9C",
            "step19_summary": "4472843C61B5196CB3D999D71EEC2EF15FBE75AEE7"
                              "F9A27B03712BA42F470B47",
            "step20_outputs": "3BB25C85E1A2E1D3B289D7AA54FB6A015A131195608F"
                              "7E6D09CB1B15F2FECC8D",
            "step20_agreement": "5C8D0CB7DC71C21C0B5313CA5F03C5057130E31A"
                                "8B27489FC4135D07785C2C8C",
            "step20_manifest": "BB774842654C9407A622B74FD969564444E71FEE325A"
                               "54E17ADB0F479A4962EA",
            "step20_wrapper_current": "D0A3218C5F0E8CA08EB60684518D9A9D656A"
                                      "01F0B781BB0D68BE693CAAE1F077",
            "step13_registration": "A9BB00B44A7416B6E412EA7EC82280E7EFFF8831"
                                   "7EA63FADB0AFBF7BE2D5AD1F",
        }
        self.assertEqual({name: entry["sha256"].upper()
                          for name, entry in plan["inputs"].items()},
                         expected)
        # Exact single-literal checks of the Step 20 aggregate pins.
        self.assertEqual(
            plan["inputs"]["step20_manifest"]["sha256"],
            "bb774842654c9407a622b74fd969564444e71fee325a54e17adb0f479a4962ea")
        self.assertEqual(
            plan["inputs"]["step20_agreement"]["sha256"],
            "5c8d0cb7dc71c21c0b5313ca5f03c5057130e31a8b27489fc4135d07785c2c8c")
        self.assertEqual(
            plan["inputs"]["step20_outputs"]["sha256"],
            "3bb25c85e1a2e1d3b289d7aa54fb6a015a131195608f7e6d09cb1b15f2fecc8d")
        self.assertEqual(plan["executed_wrapper_sha256"].upper(),
                         "FE37AC0A3FA81E4ABEFF4E3542E05DE5109085F75EFEDE0F"
                         "ECDD53C44323C9AB")
        self.assertNotEqual(plan["executed_wrapper_sha256"],
                            plan["inputs"]["step20_wrapper_current"][
                                "sha256"])
        self.assertIsNone(amp.pinned_hash_problem(plan))
        self.assertEqual(plan["inputs"]["step17_mapping"]["size_bytes"],
                         17782)
        self.assertEqual(
            plan["output_dir"],
            "G:\\Industry Dataset\\ehr-evidence-workspace\\protected-data"
            "\\model-provenance\\step-21-pilot10-v1")
        self.assertEqual(amp.OUTPUT_NAME, "provenance.json")
        self.assertEqual(
            plan["inputs"]["step20_outputs"]["path"],
            "G:\\Industry Dataset\\ehr-evidence-workspace\\protected-data"
            "\\model-outputs\\step-20-pilot10-v1\\outputs.json")
        self.assertEqual(
            plan["inputs"]["step13_registration"]["path"],
            "G:\\Industry Dataset\\ehr-evidence-workspace\\outputs\\lineage"
            "\\inherited-input-registration-v1.json")
        for entry in plan["inputs"].values():
            self.assertTrue(guard.is_within(entry["path"],
                                            guard.POLICY_ANCHOR, True))
        self.assertEqual(plan["step20_assets"],
                         amp.step20_inventory(pmo.real_plan()["assets"]))
        for role in ("step17_mapping", "step19_summary"):
            self.assertEqual(plan["step20_assets"][role]["path"],
                             plan["inputs"][role]["path"])
            self.assertEqual(plan["step20_assets"][role]["sha256"],
                             plan["inputs"][role]["sha256"])
        self.assertEqual(plan["policy_sources"]["explain_patient"],
                         plan["step20_assets"]["explain_patient"]["sha256"])
        self.assertEqual(plan["policy_sources"]["model"],
                         plan["step20_assets"]["model"]["sha256"])
        self.assertTrue(guard.is_within(plan["output_dir"],
                                        plan["protected_root"], True))
        self.assertFalse(guard.is_within(plan["output_dir"],
                                         pmo.OUTPUT_DIR))


if __name__ == "__main__":
    unittest.main()
