"""Synthetic checks for ehr_evidence_app.evidence.contracts.

Requests, responses and limits are invented inline; the real-repository
cases read only the committed limits configuration and JSON Schemas.
"""

import io
import json
import os
import unittest
from contextlib import redirect_stdout

from pydantic import ValidationError

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.evidence import vocabulary as vocab

CASE = {"case_set": "synthetic-3-v1", "case_number": 2}
HISTORY = {"kind": "pre_target_history"}
DIGEST = "5" * 64
LIMITS = {"max_question_chars": 500, "max_prompt_tokens": 16384,
          "max_output_tokens": 768, "max_concurrent_requests": 1,
          "max_request_seconds": 180, "max_generation_attempts": 1,
          "max_evidence_items": 2000, "max_bundle_bytes": 1048576}


def document(base, changes):
    """Return base updated with changes; a None value removes the key."""
    result = dict(base, **changes)
    return {key: value for key, value in result.items() if value is not None}


def request(**changes):
    """Return an invented QA request document."""
    return document({"schema_version": "1.0", "request_id": "q-001",
                     "task": "condition_presence", "case": CASE,
                     "scope": HISTORY,
                     "question": "Is synthetic condition A recorded?"},
                    changes)


def explanation_request(**changes):
    """Return an invented explanation request document."""
    return request(**dict({"request_id": "x-001",
                           "task": "prediction_explanation",
                           "question": None,
                           "prediction": {"output_record_sha256": DIGEST}},
                          **changes))


def response(**changes):
    """Return an invented evidential answer document."""
    return document({"schema_version": "1.0", "request_id": "q-001",
                     "task": "condition_presence", "case": CASE,
                     "scope": HISTORY, "outcome": "answer",
                     "text": "Synthetic condition A is recorded.",
                     "claims": [{"text": "Recorded in the first visit.",
                                 "refs": ["E1", "V1"]}],
                     "bundle": {"bundle_id": "qa-c02-history",
                                "sha256": DIGEST}}, changes)


def parse(model, value):
    """Validate one invented document through its JSON form."""
    return model.model_validate_json(json.dumps(value))


class RequestTests(unittest.TestCase):
    """A request binds one task to one case and one explicit scope."""

    def test_qa_and_explanation_requests_are_accepted(self):
        """Both request shapes validate."""
        self.assertEqual(parse(contracts.Request, request()).task,
                         "condition_presence")
        explained = parse(contracts.Request, explanation_request())
        self.assertEqual(explained.prediction.output_record_sha256, DIGEST)

    def test_qa_request_shape(self):
        """QA needs a question and carries no prediction reference."""
        for value in (request(question=None),
                      request(prediction={"output_record_sha256": DIGEST}),
                      request(question="")):
            with self.assertRaises(ValidationError):
                parse(contracts.Request, value)

    def test_explanation_request_shape(self):
        """An explanation needs a prediction, no question, history scope."""
        for value in (explanation_request(prediction=None),
                      explanation_request(question="Why?"),
                      explanation_request(scope={"kind": "case_visits"}),
                      explanation_request(scope={"kind": "selected_visit",
                                                 "visit_number": 1})):
            with self.assertRaises(ValidationError):
                parse(contracts.Request, value)

    def test_only_a_selected_visit_names_its_visit(self):
        """visit_number is required for selected_visit, else forbidden."""
        accepted = parse(contracts.Request, request(
            scope={"kind": "selected_visit", "visit_number": 2}))
        self.assertEqual(accepted.scope.visit_number, 2)
        for scope in ({"kind": "selected_visit"},
                      {"kind": "case_visits", "visit_number": 1},
                      {"kind": "selected_visit", "visit_number": 0}):
            with self.assertRaises(ValidationError):
                parse(contracts.Request, request(scope=scope))

    def test_closed_values_and_unknown_fields_are_refused(self):
        """Unknown fields, tasks, identifiers and case numbers fail."""
        for value in (request(extra="x"), request(task="free_text"),
                      request(request_id="has space"),
                      request(case={"case_set": "Bad Set",
                                    "case_number": 1}),
                      request(case={"case_set": "synthetic-3-v1",
                                    "case_number": 0}),
                      request(schema_version="2.0")):
            with self.assertRaises(ValidationError):
                parse(contracts.Request, value)

    def test_contracts_are_immutable(self):
        """Validated contracts cannot be changed in place."""
        accepted = parse(contracts.Request, request())
        with self.assertRaises(ValidationError):
            accepted.task = "condition_list"


class ResponseTests(unittest.TestCase):
    """Exactly one outcome per response, with structurally distinct rules."""

    def test_evidential_answer_is_accepted(self):
        """An answer with text, claims and its bundle validates."""
        accepted = parse(contracts.Response, response())
        self.assertEqual(accepted.claims[0].refs, ("E1", "V1"))

    def test_reason_codes_belong_to_their_outcome(self):
        """A code from another outcome's list is refused; codes are unique."""
        self.assertEqual(parse(contracts.Response, response(
            reason_codes=["unmapped_records_in_scope"])).reason_codes,
            ("unmapped_records_in_scope",))
        for value in (response(reason_codes=["timeout"]),
                      response(reason_codes=["unmapped_records_in_scope",
                                             "unmapped_records_in_scope"])):
            with self.assertRaises(ValidationError):
                parse(contracts.Response, value)

    def test_every_non_answer_outcome_states_a_reason(self):
        """Outcomes that need a reason are refused without one."""
        for outcome in vocab.REASON_REQUIRED_OUTCOMES:
            released = outcome in vocab.RELEASED_TEXT_OUTCOMES
            value = response(outcome=outcome, claims=None, bundle=None,
                             text="Synthetic text." if released else None)
            with self.assertRaises(ValidationError):
                parse(contracts.Response, value)
            reason = vocab.REASONS_BY_OUTCOME[outcome][0]
            self.assertEqual(parse(contracts.Response, dict(
                value, reason_codes=[reason])).outcome, outcome)

    def test_text_is_released_only_for_released_outcomes(self):
        """Rejections and errors carry no text or claims; others need text."""
        rejection = response(outcome="case_rejection", claims=None,
                             bundle=None, reason_codes=["case_not_admitted"])
        error_with_claims = response(outcome="operational_error", text=None,
                                     bundle=None, reason_codes=["timeout"])
        silent_abstention = response(outcome="abstention", claims=None,
                                     bundle=None, text=None,
                                     reason_codes=["task_unsupported"])
        for value in (rejection, error_with_claims, silent_abstention):
            with self.assertRaises(ValidationError):
                parse(contracts.Response, value)
        self.assertIsNone(parse(contracts.Response,
                                document(rejection, {"text": None})).text)

    def test_evidential_outcomes_cite_claims_from_their_bundle(self):
        """Answers need claims and a bundle reference."""
        for value in (response(claims=[]), response(bundle=None),
                      response(outcome="not_recorded", claims=[])):
            with self.assertRaises(ValidationError):
                parse(contracts.Response, value)

    def test_outcome_matches_task(self):
        """Explanations answer explanation requests; answers answer QA."""
        for value in (response(outcome="explanation"),
                      response(task="prediction_explanation")):
            with self.assertRaises(ValidationError):
                parse(contracts.Response, value)

    def test_task_is_absent_only_without_an_interpretation(self):
        """A clarification may name no task; answers always name theirs."""
        unclear = response(task=None, outcome="clarification", claims=None,
                           bundle=None, text="Which condition do you mean?",
                           reason_codes=["task_ambiguous"])
        self.assertIsNone(parse(contracts.Response, unclear).task)
        for outcome in ("answer", "not_recorded", "explanation"):
            with self.assertRaises(ValidationError):
                parse(contracts.Response, response(task=None,
                                                   outcome=outcome))

    def test_claim_references_are_bundle_local(self):
        """References follow the V/E/P/A/C pattern and are not empty."""
        for refs in ([], ["X1"], ["E0"], ["E1234567"]):
            with self.assertRaises(ValidationError):
                parse(contracts.Response, response(
                    claims=[{"text": "Synthetic.", "refs": refs}]))


class LimitsTests(unittest.TestCase):
    """Execution limits are bounded by fixed ceilings in code."""

    def test_committed_limits_load(self):
        """The repository configuration loads with the documented values."""
        self.assertEqual(contracts.load_limits().model_dump(), LIMITS)

    def test_configuration_cannot_raise_a_ceiling(self):
        """A value above its ceiling, or an unknown limit, is refused."""
        for name, ceiling in contracts.LIMIT_CEILINGS.items():
            with self.assertRaises(ValidationError):
                contracts.ExecutionLimits(**dict(LIMITS, **{name:
                                                            ceiling + 1}))
        with self.assertRaises(ValidationError):
            contracts.ExecutionLimits(**dict(LIMITS, max_tools=1))
        with self.assertRaises(ValidationError):
            contracts.LimitsFile.model_validate_json(json.dumps(
                {"schema_version": "1.0", "purpose": "x",
                 "limits": dict(LIMITS, max_bundle_bytes=1023)}))

    def test_missing_configuration_fails_closed(self):
        """An unreadable limits file raises a contract error."""
        with self.assertRaises(contracts.ContractError):
            contracts.load_limits(os.path.join(contracts.REPO_ROOT,
                                               "config", "missing.json"))

    def test_question_length_limit(self):
        """A question over the limit ends as limit_exceeded."""
        limits = contracts.ExecutionLimits(**LIMITS)
        short = parse(contracts.Request, request())
        long = parse(contracts.Request, request(question="x" * 501))
        self.assertIsNone(contracts.request_limit_problem(short, limits))
        self.assertEqual(contracts.request_limit_problem(long, limits),
                         "limit_exceeded")


class SchemaTests(unittest.TestCase):
    """Committed JSON Schemas match the models."""

    def test_committed_schemas_match_the_models(self):
        """No committed schema drifts from its model."""
        self.assertEqual(contracts.schema_drift(), [])
        self.assertEqual(sorted(contracts.json_schemas()), [
            "candidate.schema.json", "execution-limits.schema.json",
            "explanation-bundle.schema.json", "qa-bundle.schema.json",
            "request.schema.json", "response.schema.json"])

    def test_drift_is_detected(self):
        """A changed or missing schema is reported by name."""
        self.assertEqual(contracts.schema_drift(schemas={
            "request.schema.json": {"changed": True},
            "absent.schema.json": {}}),
            ["absent.schema.json", "request.schema.json"])

    def test_schema_text_is_canonical(self):
        """Keys are sorted, indented by two, with a final newline."""
        self.assertEqual(contracts.schema_text({"b": 1, "a": 2}),
                         '{\n  "a": 2,\n  "b": 1\n}\n')


class CommandTests(unittest.TestCase):
    """The default command checks limits, examples and schemas."""

    def test_checks_pass(self):
        """Valid examples pass, invalid ones fail, schemas match."""
        self.assertEqual(contracts.run_checks(), [])
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(contracts.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
