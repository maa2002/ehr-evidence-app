import copy
import json
import os
import sys
import unittest
import tempfile
import io
import contextlib

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
from ehr_evidence_app.tracking import lineage as validator  # noqa: E402


class LineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(validator.SCHEMA_PATH, encoding="utf-8") as handle:
            cls.schema = json.load(handle)

    def test_synthetic_chain_passes(self):
        self.assertEqual(validator.validate_record(validator.synthetic_chain(), self.schema), [])

    def test_missing_predecessor_fails(self):
        record = validator.synthetic_chain()
        record["nodes"][1]["predecessors"] = ["missing"]
        self.assertIn("predecessor_edge_invalid",
                      {item["code"] for item in validator.validate_record(record, self.schema)})

    def test_duplicate_id_and_wrong_type_fail(self):
        record = validator.synthetic_chain()
        record["nodes"][1]["record_id"] = record["nodes"][0]["record_id"]
        record["nodes"][2]["node_type"] = "unknown"
        codes = {item["code"] for item in validator.validate_record(record, self.schema)}
        self.assertIn("node_id_invalid", codes)
        self.assertIn("node_type_invalid", codes)

    def test_path_checksum_and_status_fail(self):
        record = validator.synthetic_chain()
        record["nodes"][0]["protected_reference"] = r"G:\bad"
        record["nodes"][0]["protected_checksum"] = "bad"
        record["nodes"][0]["status"] = "unknown"
        codes = {item["code"] for item in validator.validate_record(record, self.schema)}
        self.assertIn("protected_reference_invalid", codes)
        self.assertIn("status_invalid", codes)

    def test_cycle_and_missing_type_fail(self):
        record = validator.synthetic_chain()
        record["nodes"][-1]["predecessors"] = [record["nodes"][-1]["record_id"]]
        record["nodes"][-1]["node_type"] = "evaluation_output"
        codes = {item["code"] for item in validator.validate_record(record, self.schema)}
        self.assertIn("required_node_type_missing", codes)
        self.assertIn("predecessor_edge_invalid", codes)

    def test_disconnected_chain_fails(self):
        """Every non-root node must name its immediate predecessor."""
        record = validator.synthetic_chain()
        for node in record["nodes"][1:]:
            node["predecessors"] = []
        codes = {item["code"] for item in validator.validate_record(record, self.schema)}
        self.assertIn("predecessor_edge_invalid", codes)

    def test_malformed_record_is_operationally_sanitized(self):
        """CLI emits an operational envelope for malformed JSON/record input."""
        self.assertEqual(validator.validate_record([], self.schema)[0]["code"],
                         "record_shape")

    def test_required_references_and_serialized_order_fail(self):
        record = validator.synthetic_chain()
        record["nodes"][0].pop("protected_reference")
        record["nodes"][0].pop("protected_checksum")
        record["nodes"] = list(reversed(record["nodes"]))
        codes = {item["code"] for item in validator.validate_record(record, self.schema)}
        self.assertIn("protected_reference_missing", codes)
        self.assertIn("serialized_order_invalid", codes)

    def test_unhashable_fields_and_stage_are_safe(self):
        record = validator.synthetic_chain()
        record["nodes"][0]["record_id"] = []
        record["nodes"][1]["node_type"] = {"secret": "fixture"}
        findings = validator.validate_record(record, self.schema)
        self.assertTrue(findings)
        self.assertTrue(all(item["stage"] == "record" or
                            item["stage"] in self.schema["node_types"]
                            for item in findings))

    def test_malformed_file_returns_operational_envelope(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{malformed")
            path = handle.name
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = validator.main(["--record", path])
            self.assertEqual(status, 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "OPERATIONAL")
        finally:
            os.unlink(path)

    def test_chain_identity_and_content_tokens_fail_closed(self):
        record = validator.synthetic_chain()
        record["chain_id"] = []
        findings = validator.validate_record(record, self.schema)
        self.assertIn("record_identity", {item["code"] for item in findings})
        record = validator.synthetic_chain()
        record["chain_id"] = "patient_id_ABC123"
        codes = {item["code"] for item in validator.validate_record(record, self.schema)}
        self.assertIn("record_identity", codes)
        record = validator.synthetic_chain()
        record["nodes"][0]["record_id"] = "token_ABCDEF123456"
        record["nodes"][0]["version"] = "secret_ABCDEF123456"
        codes = {item["code"] for item in validator.validate_record(record, self.schema)}
        self.assertIn("node_id_invalid", codes)
        self.assertIn("version_invalid", codes)
        findings = validator.validate_record(record, self.schema)
        self.assertTrue(all(item["node_id"] is None or
                            item["node_id"] not in ("token_ABCDEF123456",)
                            for item in findings))

    def test_protected_path_content_tokens_fail(self):
        record = validator.synthetic_chain()
        record["nodes"][0]["protected_reference"] = (
            validator.PROTECTED_ROOT + r"\outputs\patient_id_ABC123.json")
        codes = {item["code"] for item in validator.validate_record(record, self.schema)}
        self.assertIn("protected_reference_invalid", codes)


if __name__ == "__main__":
    unittest.main()
