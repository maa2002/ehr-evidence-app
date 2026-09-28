import contextlib
import io
import math
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
from ehr_evidence_app.tracking import controlled as tracking


def valid_lineage():
    return {
        "code_revision": "rev-2026-09",
        "case_set_version": "cases-v3",
        "llm_configuration_fingerprint": "a" * 64,
        "prompt_version": "schema-v2",
        "evidence_schema_version": "schema-v4",
        "validation_configuration_fingerprint": "b" * 64,
        "evaluation_outputs": {"accuracy": 0.91, "macro_f1": 0.8},
        "protected_reference": tracking.WORKSPACE_ROOT + r"\outputs\run-42",
        "protected_checksum": "c" * 64,
    }


class PayloadTests(unittest.TestCase):
    def test_valid_allowlisted_payload_is_deterministic_and_bounded(self):
        payload = tracking.build_task_payload(
            "project_123", valid_lineage(), {"batch_size": 8, "learning_rate": 0.01})
        self.assertEqual(set(payload), {"name", "type", "project", "comment", "tags"})
        self.assertEqual(payload["project"], "project_123")
        self.assertEqual(payload["name"], tracking.TASK_NAME)
        self.assertEqual(payload["tags"], tracking.build_task_payload(
            "project_123", valid_lineage(), {"batch_size": 8, "learning_rate": 0.01})["tags"])
        self.assertTrue(all(tag.startswith(("lineage:", "hyperparameter:"))
                            for tag in payload["tags"]))

    def test_lineage_schema_is_exact(self):
        for change in ("missing", "unknown"):
            data = valid_lineage()
            if change == "missing":
                data.pop("prompt_version")
            else:
                data["arbitrary_comment"] = "hello"
            with self.subTest(change=change), self.assertRaises(tracking.UnsafeMetadata):
                tracking.build_metadata_tags(data, {})

    def test_hyperparameters_are_allowlisted_scalar_and_finite(self):
        for params in ({"dropout": 0.2}, {"epochs": True},
                       {"batch_size": 0}, {"learning_rate": math.nan},
                       {"token": "secret"}):
            with self.subTest(params=params), self.assertRaises(tracking.UnsafeMetadata):
                tracking.build_metadata_tags(valid_lineage(), params)

    def test_forbidden_values_are_rejected(self):
        cases = (
            ("code_revision", "Patient ID: ABC123"),
            ("code_revision", "sk-abcdefghijk"),
            ("code_revision", "C:" + r"\Users\someone\run"),
            ("code_revision", "synthetic-marker-7"),
            ("prompt_version", "prompt: copy this text"),
            ("prompt_version", "response-v2"),
            ("prompt_version", "attribution-text-v2"),
            ("prompt_version", "case-prediction-v2"),
            ("prompt_version", "patient_id_ABC123"),
            ("prompt_version", "response_payload"),
            ("prompt_version", "attribution_text"),
            ("prompt_version", "case_prediction_positive"),
            ("prompt_version", "api_key_ABCDEF123456"),
            ("prompt_version", "prompts-v2"),
            ("prompt_version", "responses-v2"),
            ("prompt_version", "attributions-v2"),
            ("prompt_version", "because the patient presents"),
        )
        for field, value in cases:
            data = valid_lineage()
            data[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(tracking.UnsafeMetadata):
                tracking.build_metadata_tags(data, {})

    def test_secret_name_value_shapes_are_rejected_in_versions_and_paths(self):
        secret_shapes = (
            "token_ABCDEF123456",
            "password_ABCDEF123456",
            "secret_ABCDEF123456",
            "api_key=ABCDEF123456",
            "token=ABCDEF123456",
            "password=ABCDEF123456",
        )
        for shape in secret_shapes:
            with self.subTest(context="version", value=shape):
                lineage = valid_lineage()
                lineage["code_revision"] = shape
                with self.assertRaises(tracking.UnsafeMetadata):
                    tracking.build_metadata_tags(lineage, {})
            with self.subTest(context="protected_reference", value=shape):
                lineage = valid_lineage()
                lineage["protected_reference"] = (
                    tracking.WORKSPACE_ROOT + "\\outputs\\" + shape)
                with self.assertRaises(tracking.UnsafeMetadata):
                    tracking.build_metadata_tags(lineage, {})

    def test_vendor_secret_shapes_are_rejected_in_versions_and_paths(self):
        secret_shapes = (
            "AKIA" + "A" * 16,
            "ASIA" + "B" * 16,
            "hf_" + "C" * 24,
            "xoxb-" + "D" * 16,
            "eyJ" + "E" * 20 + ".payload.signature",
            "-" * 5 + "BEGIN " + "PRIVATE KEY" + "-" * 5,
        )
        for shape in secret_shapes:
            with self.subTest(context="version", value=shape):
                lineage = valid_lineage()
                lineage["code_revision"] = shape
                with self.assertRaises(tracking.UnsafeMetadata):
                    tracking.build_metadata_tags(lineage, {})
            with self.subTest(context="protected_reference", value=shape):
                lineage = valid_lineage()
                lineage["protected_reference"] = (
                    tracking.WORKSPACE_ROOT + "\\outputs\\" + shape)
                with self.assertRaises(tracking.UnsafeMetadata):
                    tracking.build_metadata_tags(lineage, {})

    def test_explanation_like_stdout_is_neither_accepted_nor_captured(self):
        captured = io.StringIO()
        lineage = valid_lineage()
        lineage["prompt_version"] = "because the patient presents"
        with contextlib.redirect_stdout(captured):
            with self.assertRaises(tracking.UnsafeMetadata):
                tracking.build_task_payload("project_1", lineage, {})
        self.assertEqual(captured.getvalue(), "")
        with self.assertRaises(TypeError):
            tracking.build_task_payload(
                "project_1", valid_lineage(), {},
                stdout="because the patient presents")

    def test_protected_reference_is_canonical_bounded_and_content_checked(self):
        forward = valid_lineage()
        forward["protected_reference"] = "/".join(
            tracking.WORKSPACE_ROOT.split("\\") + ["outputs", "run-42"])
        tags = tracking.build_metadata_tags(forward, {})
        reference_tag = next(tag for tag in tags if tag.startswith("lineage:protected_reference="))
        self.assertIn(r"G:\Industry Dataset\ehr-evidence-workspace\outputs\run-42", reference_tag)
        invalid = (
            tracking.WORKSPACE_ROOT + r"\outputs/..\secret",
            tracking.WORKSPACE_ROOT + r"\outputs\..\secret",
            tracking.WORKSPACE_ROOT + r"\outputs\stream:secret",
            tracking.WORKSPACE_ROOT + r"\outputs\prompt-v2",
            tracking.WORKSPACE_ROOT + r"\outputs\patient_id_ABC123",
            tracking.WORKSPACE_ROOT + r"\outputs\response_payload",
            tracking.WORKSPACE_ROOT + r"\outputs\attribution_text",
            tracking.WORKSPACE_ROOT + r"\outputs\case_prediction_positive",
            tracking.WORKSPACE_ROOT + r"\outputs\api_key_ABCDEF123456",
            tracking.WORKSPACE_ROOT + r"\outputs\prompts",
            tracking.WORKSPACE_ROOT + r"\outputs\responses",
            tracking.WORKSPACE_ROOT + r"\outputs\attributions",
            tracking.WORKSPACE_ROOT + "\\" + ("x" * (tracking.MAX_COMPONENT_LENGTH + 1)),
            tracking.WORKSPACE_ROOT + "\\outputs\\" + ("x" * tracking.MAX_REFERENCE_LENGTH),
        )
        for reference in invalid:
            data = valid_lineage()
            data["protected_reference"] = reference
            with self.subTest(reference=reference), self.assertRaises(tracking.UnsafeMetadata):
                tracking.build_metadata_tags(data, {})

    def test_metrics_accept_only_approved_aggregate_names_and_numbers(self):
        for metric in ({"case_prediction": 1}, {"accuracy": float("inf")},
                       {"accuracy": True}, {}, {"accuracy": -0.01},
                       {"macro_f1": 1.01}, {"micro_f1": 2},
                       {"coverage": -1}, {"latency_ms": -0.1},
                       {"latency_ms": tracking.MAX_LATENCY_MS + 1},
                       {"latency_ms": 10 ** 10000}):
            data = valid_lineage()
            data["evaluation_outputs"] = metric
            with self.subTest(metric=metric), self.assertRaises(tracking.UnsafeMetadata):
                tracking.build_metadata_tags(data, {})

    def test_task_name_and_project_are_validated(self):
        for kwargs in ({"project_id": "bad id"},):
            params = dict(project_id="project_1", lineage=valid_lineage(), hyperparameters={})
            params.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(tracking.UnsafeMetadata):
                tracking.build_task_payload(**params)

    def test_readback_rejects_capture_or_payload_mismatch(self):
        expected = tracking.build_task_payload("project_1", valid_lineage(), {})
        task = dict(expected, id="task_1", script={})
        self.assertTrue(tracking.validate_readback(task, expected))
        for field, value in (("script", {"repository": "repo"}),
                             ("runtime", {"python": "3.x"}),
                             ("artifacts", ["file"]),
                             ("tags", ["unexpected content"])):
            altered = dict(task, **{field: value})
            with self.subTest(field=field), self.assertRaises(tracking.UnsafeMetadata):
                tracking.validate_readback(altered, expected)


def valid_configuration():
    """Return configuration fields in their permitted forms."""
    return {"case_set": "dev-30-v1", "commit": "93762df",
            "precision": "bf16-mixed", "prompt_version": "v9",
            "projection_version": "projection-v5",
            "shape_version": "candidate-shape-v4",
            "explanation_template": "explanation-template-v3",
            "drivers": 5, "max_output_tokens": 768,
            "model_repository": "google/medgemma-1.5-4b-it",
            "model_revision": "9" * 40}


class KindConfigurationScalarTests(unittest.TestCase):
    """Task kinds, configuration fields and scalars (ADR 0018)."""

    def test_kinds_have_fixed_names_and_only_probes_archive(self):
        """Every kind has its fixed name and type; lineage kinds stay
        visible."""
        for kind, fixed in tracking.TASK_KINDS.items():
            payload = tracking.build_task_payload(
                "project_123", valid_lineage(), {}, kind=kind)
            self.assertEqual((payload["name"], payload["type"]),
                             (fixed["name"], fixed["type"]))
            self.assertEqual(fixed["archive"], kind == "probe")
        with self.assertRaises(tracking.UnsafeMetadata):
            tracking.build_task_payload("project_123", valid_lineage(), {},
                                        kind="free text")

    def test_names_hold_checked_configuration_only(self):
        """A kind with a label is named after its configuration fields,
        each in its permitted form; a field outside the allowlist never
        reaches a name."""
        self.assertEqual(
            tracking.task_name("measurement", valid_configuration()),
            "Generation measurement: dev-30-v1, bf16-mixed, prompt v9, "
            "93762df")
        self.assertEqual(tracking.task_name("runtime_aggregates", {
            "case_set": "pilot-10-v1"}), "Runtime aggregates: pilot-10-v1")
        self.assertEqual(tracking.task_name("registration",
                                            valid_configuration()),
                         "Inherited input registration")
        for change in ({"commit": "HEAD"}, {"case_set": "Dev 30"}):
            with self.subTest(change=change):
                with self.assertRaises(tracking.UnsafeMetadata):
                    tracking.task_name("measurement", dict(
                        valid_configuration(), **change))

    def test_configuration_fields_take_their_forms_only(self):
        """Configuration becomes one hyper-parameter section; an unknown
        field, free text or an out-of-range number is refused."""
        payload = tracking.build_task_payload(
            "project_123", valid_lineage(), {}, kind="measurement",
            configuration=valid_configuration())
        section = payload["hyperparams"][tracking.CONFIGURATION_SECTION]
        self.assertEqual(section["drivers"],
                         {"section": "Configuration", "name": "drivers",
                          "value": "5", "type": "int"})
        self.assertEqual(section["prompt_version"]["value"], "v9")
        for change in ({"prompt_version": "prompt-v9"},
                       {"case_set": "the patient's cases"},
                       {"drivers": 0}, {"drivers": True},
                       {"max_output_tokens": 5000},
                       {"model_repository": "another/model"},
                       {"notes": "free text"}):
            with self.subTest(change=change):
                with self.assertRaises(tracking.UnsafeMetadata):
                    tracking.build_configuration(dict(
                        valid_configuration(), **change))

    def test_scalars_are_named_and_bounded(self):
        """Scalars carry allowlisted names and bounded values, once
        each."""
        events = tracking.build_scalar_events("task_1", [
            ("release rate", "lists", 0.73), ("released", "all", 385),
            ("F1", "explanations: drivers", 1.0),
            ("seconds", "lists: median", 32.9)])
        self.assertEqual(events[0], {
            "task": "task_1", "type": "training_stats_scalar",
            "metric": "release rate", "variant": "lists", "value": 0.73,
            "iter": 0})
        for scalars in ([("release rate", "lists", 1.2)],
                        [("released", "all", 2.5)],
                        [("loss", "all", 0.1)],
                        [("F1", "a patient's lists", 0.5)],
                        [("F1", "all", float("nan"))],
                        [("F1", "all", 0.5), ("F1", "all", 0.6)]):
            with self.subTest(scalars=scalars):
                with self.assertRaises(tracking.UnsafeMetadata):
                    tracking.build_scalar_events("task_1", scalars)

    def test_readback_accepts_exactly_the_configuration_sent(self):
        """The configuration read back must be the one sent; any other
        hyper-parameter is captured content."""
        expected = tracking.build_task_payload(
            "project_123", valid_lineage(), {}, kind="measurement",
            configuration=valid_configuration())
        task = dict(expected, id="task_1")
        self.assertTrue(tracking.validate_readback(task, expected))
        extra = dict(task, hyperparams={
            **expected["hyperparams"], "Args": {"x": {
                "section": "Args", "name": "x", "value": "1"}}})
        with self.assertRaises(tracking.UnsafeMetadata):
            tracking.validate_readback(extra, expected)
        bare = tracking.build_task_payload("project_123", valid_lineage(),
                                           {}, kind="measurement")
        with self.assertRaises(tracking.UnsafeMetadata):
            tracking.validate_readback(task, bare)


if __name__ == "__main__":
    unittest.main()
