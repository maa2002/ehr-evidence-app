"""Synthetic tests for the application's operations (api.service).

Every case is invented and held in memory; the model is a scripted
backend, and records are written to a temporary directory standing for
the protected data. No real record, bundle or model is used.
"""

import json
import os
import tempfile
import threading
import unittest

from ehr_evidence_app.api import app, service as services
from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.generation import adapter, records

ALL = contracts.ScopeRef(kind="case_visits")
HISTORY = contracts.ScopeRef(kind="pre_target_history")
LIST = "Which conditions are recorded?"
PRESENT = "Is synthetic concept 1001 recorded?"


class ServiceTests(unittest.TestCase):
    """The operations over the invented case set."""

    def setUp(self):
        """Serve the invented case set with the scripted model."""
        self.service = app.invented_service()
        self.label = self.service.case_sets()[0]["case_set"]

    def test_case_sets_cases_and_case(self):
        """The served set lists its admitted cases; a case lists its
        scopes, history first, and its prepared prediction."""
        self.assertEqual(self.service.case_sets(),
                         [{"case_set": self.label, "cases": 1}])
        self.assertEqual(self.service.cases(self.label), [
            {"case_number": 1, "visits": 3, "prediction": True}])
        case = self.service.case(self.label, 1)
        self.assertEqual(case["scopes"][0], {"kind": "pre_target_history"})
        self.assertEqual(case["scopes"][-1], {"kind": "case_visits"})
        self.assertEqual(case["prediction"]["score_meaning"],
                         "uncalibrated sigmoid of the logit; not a "
                         "clinical risk")
        self.assertTrue(case["explanation_eligible"])

    def test_the_prediction_against_the_record(self):
        """The evidence of a prediction says whether its diagnosis is
        recorded for the final admission, from that visit's QA bundle by
        the condition rules' matching."""
        view = self.service.evidence(self.label, 1, "prediction")
        found = view["in_record"]
        self.assertEqual(found["visit_number"], 3)
        self.assertIn(found["coverage"], ("complete", "complete_empty"))
        source = self.service.sources[self.label]
        bundle, _ = source.explanations[1]
        visit, _ = source.bundle_set.qa_bundle(1, contracts.ScopeRef(
            kind="selected_visit", visit_number=3))
        self.assertEqual({item["ref"] for item in found["matches"]}, {
            event.ref for event in visit.events
            if event.concept.concept_id
            == bundle.prediction.concept.concept_id})
        # A prediction of a concept the final admission records matches
        # that visit's records of it exactly.
        event = next(event for event in visit.events
                     if event.concept.concept_id)
        recorded = bundle.model_copy(update={
            "prediction": bundle.prediction.model_copy(update={
                "concept": event.concept})})
        found = services.prediction_record(source.bundle_set,
                                           self.service.vocabulary, 1,
                                           recorded)
        self.assertIn({"ref": event.ref, "match": "exact",
                       "concept_name": event.concept.name,
                       "source_code": event.source_concept.code
                       if event.source_concept else None},
                      found["matches"])

    def test_refusals(self):
        """Another case set, an unknown case or scope and a case without a
        prediction are refused by code."""
        for call, code in (
                (lambda: self.service.cases("eval-100-v1"),
                 "case_set_not_served"),
                (lambda: self.service.case(self.label, 2), "case_unknown"),
                (lambda: self.service.evidence(self.label, 1,
                                               "selected_visit", 9),
                 "scope_unknown"),
                (lambda: self.service.evidence(self.label, 1,
                                               "selected_visit"),
                 "scope_invalid")):
            with self.assertRaises(services.ServiceError) as raised:
                call()
            self.assertEqual(raised.exception.code, code)
        with self.assertRaises(services.ServiceError):
            services.workspace_source("eval-100-v1")

    def test_answers_keep_their_identity(self):
        """An answer is the gate's response for the asked case and scope,
        written by the model when its document passes the gate."""
        found = self.service.ask(self.label, 1, HISTORY, LIST)
        response = found["response"]
        self.assertEqual((response["case"], response["scope"]), (
            {"case_set": self.label, "case_number": 1},
            {"kind": "pre_target_history", "visit_number": None}))
        self.assertEqual((response["outcome"], found["author"]),
                         ("answer", "model"))
        self.assertTrue(response["claims"])
        refused = self.service.ask(self.label, 1, HISTORY,
                                   "How should heart failure be treated?")
        self.assertEqual((refused["response"]["outcome"], refused["author"],
                          refused["fallback"]),
                         ("abstention", "template", None))
        listed = self.service.ask(self.label, 1, ALL,
                                  "What medications were given?")
        self.assertEqual((listed["response"]["outcome"],
                          listed["response"]["task"], listed["author"],
                          listed["fallback"]),
                         ("answer", "record_list", "template", None))

    def test_withheld_candidates_never_return(self):
        """A model's candidate the gate withholds never reaches the
        response: the template's is returned, with the reason."""
        wrong = adapter.ScriptedBackend(lambda messages, schema: (
            adapter.output('{"outcome": "answer", "items": []}')))
        self.service.stop()
        self.service.use_backend(wrong).join()
        found = self.service.ask(self.label, 1, HISTORY, LIST)
        self.assertEqual(found["author"], "template")
        self.assertIn(found["fallback"], ("schema_violated", "malformed",
                                          "gate_rejected"))
        self.assertEqual(found["response"]["outcome"], "answer")

    def test_model_states(self):
        """No model means the templates write, as a recorded fallback; a
        model that cannot load leaves them writing."""
        self.service.stop()
        self.assertEqual(self.service.health()["model"], "off")
        found = self.service.ask(self.label, 1, HISTORY, LIST)
        self.assertEqual((found["author"], found["fallback"],
                          found["category"]),
                         ("template", "no_model", "unavailable"))

        class Failing:
            """A backend that cannot start."""

            def start(self):
                """Fail to load."""
                raise adapter.BackendError("resource_exhausted")

        self.service.use_backend(Failing()).join()
        self.assertEqual(self.service.health()["model"], "unavailable")
        self.assertIsNone(self.service.writer)

    def test_loading_then_ready(self):
        """While the model loads the templates write; then it writes."""
        release = threading.Event()
        inner = app.invented_service().writer.backend

        class Slow:
            """A backend that loads when told."""

            def start(self):
                """Wait to be released."""
                release.wait(10)

            def __getattr__(self, name):
                """Write as the scripted backend."""
                return getattr(inner, name)

        self.service.stop()
        thread = self.service.use_backend(Slow())
        self.assertEqual(self.service.health()["model"], "loading")
        self.assertEqual(self.service.ask(self.label, 1, HISTORY,
                                          LIST)["fallback"], "no_model")
        release.set()
        thread.join()
        self.assertEqual(self.service.health()["model"], "ready")
        self.assertEqual(self.service.ask(self.label, 1, HISTORY,
                                          LIST)["author"], "model")

    def test_one_request_at_a_time(self):
        """While the model writes one request, another gets the template's
        response at once, marked busy."""
        self.assertTrue(self.service.writer.slots.acquire(blocking=False))
        try:
            found = self.service.ask(self.label, 1, HISTORY, LIST)
        finally:
            self.service.writer.slots.release()
        self.assertEqual((found["fallback"], found["category"]),
                         ("busy", "unavailable"))

    def test_explanation_and_its_evidence(self):
        """An explanation cites only what the prediction's evidence shows,
        and no evidence view holds a native identifier."""
        found = self.service.explain(self.label, 1)
        self.assertEqual((found["response"]["outcome"], found["author"]),
                         ("explanation", "model"))
        view = self.service.evidence(self.label, 1, "prediction")
        shown = {item["ref"] for item in view["attributions"]
                 + view["history_records"] + view["final_admission_context"]
                 } | {"P1", "C1"}
        cited = {ref for claim in found["response"]["claims"]
                 for ref in claim["refs"]}
        self.assertTrue(cited)
        self.assertLessEqual(cited, shown)
        self.assertTrue(all("link_status" in item
                            for item in view["attributions"]))
        scope = self.service.evidence(self.label, 1, "pre_target_history")
        for text in (json.dumps(view), json.dumps(scope)):
            for key in ("native_id", "visit_occurrence_id", "person_id",
                        "subject_id"):
                self.assertNotIn(key, text)
        refs = {record["ref"] for record in scope["records"]}
        answer = self.service.ask(self.label, 1, HISTORY, LIST)
        cited = {ref for claim in answer["response"]["claims"]
                 for ref in claim["refs"] if ref.startswith("E")}
        self.assertLessEqual(cited, refs)

    def test_every_response_is_recorded(self):
        """Every question and explanation writes its protected record,
        with who wrote it."""
        with tempfile.TemporaryDirectory() as scratch:
            recorder = records.Recorder(os.path.join(scratch, "records"),
                                        protected=scratch)
            self.service.recorder = recorder
            recorder.writer = self.service.writer
            self.service.ask(self.label, 1, HISTORY, LIST)
            self.service.ask(self.label, 1, HISTORY,
                             "How should heart failure be treated?")
            self.service.ask(self.label, 1, ALL,
                             "What medications were given?")
            self.service.explain(self.label, 1)
            found = [records.read(path)
                     for path in records.paths(recorder.root)]
        self.assertEqual(sorted(item.response.outcome for item in found),
                         ["abstention", "answer", "answer", "explanation"])
        self.assertEqual(sorted(item.generation.author for item in found
                                if item.generation), ["model", "model"])
        self.assertEqual([item.bundle.bundle_id for item in found
                          if item.task == "record_list"], ["rec-c01-all"])


if __name__ == "__main__":
    unittest.main()
