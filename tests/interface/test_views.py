"""Tests of what the page shows (interface.views), from the invented case
set's values; no real record is used."""

import json
import unittest

from ehr_evidence_app.api import app as api
from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.interface import labels, names, views


class InventedTestCase(unittest.TestCase):
    """The invented service, its case set and its one case."""

    @classmethod
    def setUpClass(cls):
        """Serve the invented case set with the scripted model."""
        cls.service = api.invented_service()
        cls.label = cls.service.case_sets()[0]["case_set"]
        cls.case = cls.service.case(cls.label, 1)

    def ask(self, question, scope=None):
        """Answer a question about the invented case."""
        scope = scope or self.case["scopes"][0]
        return self.service.ask(self.label, 1, contracts.ScopeRef(**scope),
                                question)


class SidebarTests(InventedTestCase):
    """The model's state, the case sets, the cases and a case."""

    def test_state(self):
        """The model's state has a short name and a meaning; each case set
        is a cohort with its number of patients."""
        found = views.state_view({"model": "loading"}, [
            {"case_set": "dev-30-v1", "cases": 30},
            {"case_set": "pilot-10-v1", "cases": 1}], "9.9.9")
        self.assertEqual(found["model"], {
            "state": "loading", "label": "Model loading",
            "detail": "The templates answer until the model is ready."})
        self.assertEqual(found["case_sets"], [
            {"id": "dev-30-v1", "label": "Development cohort",
             "detail": "30 patients"},
            {"id": "pilot-10-v1", "label": "Pilot cohort",
             "detail": "1 patient"}])
        self.assertEqual(found["version"], "9.9.9")

    def test_cases(self):
        """A patient is listed by pseudonym, with the case's number and
        visits."""
        found = views.cases_view(self.label, self.service.cases(self.label))
        self.assertEqual(found, [{"number": 1,
                                  "name": names.pseudonym(self.label, 1),
                                  "detail": "Case 1 · 3 visits",
                                  "prediction": True}])

    def test_case(self):
        """A case has its patient's pseudonym and introduction, its scopes,
        the prediction's evidence last, and its prediction with the
        score's fixed meaning, never a percentage."""
        found = views.case_view(self.case)
        name = names.pseudonym(self.label, 1)
        self.assertEqual(found["name"], name)
        self.assertEqual(found["subtitle"],
                         "Case 1 · Demonstration cohort · 3 visits")
        self.assertEqual(found["greeting"],
                         "What would you like to know about %s?"
                         % names.given_name(name))
        self.assertEqual(found["introduction"], (
            "Case 1 of the demonstration cohort (%s), an invented patient "
            "with 3 visits. %s is a pseudonym: the records hold no names."
            % (self.label.replace("-", "\u2011"), name)))
        self.assertEqual([scope["key"] for scope in found["scopes"]], [
            "pre_target_history", "selected_visit:1", "selected_visit:2",
            "selected_visit:3", "case_visits"])
        self.assertEqual(found["scopes"][1]["label"], "Visit 1")
        self.assertEqual(found["scopes"][0]["name"], "the pre-target history")
        self.assertEqual(found["evidence"][-1], {"key": "prediction",
                                                 "label": "The prediction"})
        prediction = found["prediction"]
        self.assertEqual([fact["value"] for fact in prediction["facts"]],
                         ["0.818", "1.5"])
        self.assertIn(labels.NOT_A_RISK, prediction["meaning"])
        self.assertNotIn("%", json.dumps(prediction))
        self.assertIsNone(views.case_view(dict(self.case, prediction=None))[
            "prediction"])


class ResponseTests(InventedTestCase):
    """Responses, line by line with their references."""

    def test_lines_cite_their_claims(self):
        """A line that states a claim carries its references; a list item
        keeps its depth; a paragraph has none."""
        found = views.text_lines(
            "Two things:\n- First thing\n  - Its detail.\nThe end.",
            [{"text": "First thing.", "refs": ["E1", "E2"]},
             {"text": "Its  detail", "refs": ["E2"]},
             {"text": "Not in the text.", "refs": ["E9"]}])
        self.assertEqual(found, [
            {"text": "Two things:", "depth": None, "refs": []},
            {"text": "First thing", "depth": 0, "refs": ["E1", "E2"]},
            {"text": "Its detail.", "depth": 1, "refs": ["E2"]},
            {"text": "The end.", "depth": None, "refs": []}])

    def test_answer(self):
        """An answer shows its outcome, its lines with their references,
        the scope it answered, who wrote it and its seconds."""
        found = views.response_view(self.ask("Which conditions are "
                                             "recorded?"))
        self.assertEqual(found["outcome"], {"code": "answer",
                                            "label": "Answer",
                                            "tone": "good"})
        self.assertEqual(found["references"], ["E1"])
        self.assertTrue(any(line["refs"] == ["E1"]
                            for line in found["lines"]))
        self.assertEqual(found["scope"], {
            "key": "pre_target_history", "name": "the pre-target history",
            "label": "Pre-target history"})
        self.assertEqual(found["evidence"], "pre_target_history")
        self.assertEqual(found["author"], "written by the model")
        self.assertRegex(found["seconds"], r"^\d+\.\d s$")
        self.assertIsNone(found["withheld"])

    def test_withheld(self):
        """A response without text says so, with its reasons."""
        result = self.ask("Which conditions are recorded?")
        response = dict(result["response"], outcome="validation_rejection",
                        text=None, claims=[],
                        reason_codes=["unsupported_claim"])
        found = views.response_view(dict(result, response=response,
                                         author="template",
                                         fallback="gate_rejected"))
        self.assertEqual(found["withheld"],
                         "No text was released: unsupported claim.")
        self.assertEqual(found["lines"], [])
        self.assertEqual(found["outcome"]["tone"], "bad")
        self.assertIn("validation withheld", found["author"])

    def test_explanation_keeps_its_parts_apart(self):
        """An explanation carries the prediction, the note that attribution
        is unsigned, and the conditions recorded in the pre-target history
        in a block of their own."""
        history = self.service.evidence(self.label, 1, "pre_target_history")
        found = views.explanation_view(self.service.explain(self.label, 1),
                                       self.case, history)
        self.assertEqual(found["outcome"]["code"], "explanation")
        self.assertEqual(found["evidence"], "prediction")
        self.assertEqual(found["prediction"]["label"], "SYNTHETIC LABEL")
        self.assertEqual(found["unsigned"], labels.UNSIGNED)
        table = found["conditions"]["table"]
        self.assertEqual([column["label"] for column in table["columns"]],
                         ["Condition", "Records", "Visits"])
        self.assertEqual(table["rows"], [{"ref": None, "cells": [
            "SYNTHETIC CONCEPT 1001", "1", "1"]}])
        self.assertIn("P1", found["references"])
        unread = views.explanation_view(self.service.explain(self.label, 1),
                                        self.case, None)
        self.assertIsNone(unread["conditions"]["table"])
        self.assertIn("could not be read", unread["conditions"]["note"])


class EvidenceTests(InventedTestCase):
    """The evidence panel's sections."""

    @staticmethod
    def refs(view):
        """Return every reference a view's sections and rows carry."""
        found = {section["ref"] for section in view["sections"]}
        for section in view["sections"]:
            for block in section["blocks"]:
                if block["type"] == "table":
                    found |= {row["ref"] for row in block["rows"]}
        return found - {None}

    def test_scope(self):
        """A scope's evidence: its coverage, visits, records and folded
        provenance, each row with its reference and no native
        identifier."""
        found = views.evidence_view(self.service.evidence(
            self.label, 1, "pre_target_history"))
        self.assertEqual(found["title"], "Pre-target history")
        self.assertEqual([section["title"] for section in found["sections"]],
                         ["Coverage", "Visits", "Records", "Provenance"])
        self.assertEqual(found["sections"][0]["ref"], "C1")
        self.assertTrue(found["sections"][-1]["collapsed"])
        self.assertEqual(self.refs(found), {"C1", "V1", "V2", "E1"})
        text = json.dumps(found)
        self.assertNotIn("native", text)
        self.assertNotIn("visit_occurrence_id", text)

    def test_prediction(self):
        """The prediction's evidence shows every attribution with its
        unsigned score and link status, and warns of those without a
        verified link."""
        view = self.service.evidence(self.label, 1, "prediction")
        found = views.evidence_view(view)
        attributions = [section for section in found["sections"]
                        if section["title"] == "Attributions"][0]
        table = [block for block in attributions["blocks"]
                 if block["type"] == "table"][0]
        self.assertIn("Score (unsigned)", [column["label"]
                                           for column in table["columns"]])
        self.assertIn("Link status", [column["label"]
                                      for column in table["columns"]])
        self.assertEqual(len(table["rows"]), len(view["attributions"]))
        self.assertEqual(attributions["blocks"][0]["text"], labels.UNSIGNED)
        self.assertTrue({"P1", "C1", "A1", "E1", "E3"} <= self.refs(found))
        explained = self.service.explain(self.label, 1)
        self.assertTrue(set(labels.references(explained["response"]))
                        <= self.refs(found))
        view["attributions"][0] = dict(view["attributions"][0],
                                       link_status="unlinked")
        warned = [block["text"] for section in views.evidence_view(view)[
            "sections"] for block in section["blocks"]
            if block["type"] == "note" and block["tone"] == "warn"]
        self.assertEqual(len(warned), 1)
        self.assertTrue(warned[0].startswith("1 of 3 attributions have no "
                                             "verified link"))

    def test_error(self):
        """A refusal is its code and what it means."""
        self.assertEqual(views.error_view("case_unknown"), {
            "error": "case_unknown",
            "message": "This case is not among the admitted cases."})


if __name__ == "__main__":
    unittest.main()
