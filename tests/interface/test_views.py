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
        cls.visits = cls.service.evidence(cls.label, 1, "case_visits")[
            "visits"]

    def ask(self, question, scope=None):
        """Answer a question about the invented case."""
        scope = scope or self.case["scopes"][0]
        return self.service.ask(self.label, 1, contracts.ScopeRef(**scope),
                                question)


class SidebarTests(InventedTestCase):
    """The model's state, the cohorts, the patients and a case."""

    def test_state(self):
        """The model's state has a short name and a meaning; each case set
        is a cohort with its number of patients."""
        found = views.state_view({"model": "loading"}, [
            {"case_set": "dev-30-v1", "cases": 30},
            {"case_set": "pilot-10-v1", "cases": 1}], "9.9.9")
        self.assertEqual(found["model"], {
            "state": "loading", "label": "Model Loading",
            "detail": "Templates answer until the model is ready."})
        self.assertEqual(found["case_sets"], [
            {"id": "dev-30-v1", "label": "Development Cohort",
             "detail": "30 Patients"},
            {"id": "pilot-10-v1", "label": "Pilot Cohort",
             "detail": "1 Patient"}])
        self.assertEqual(found["version"], "9.9.9")

    def test_cases(self):
        """A patient is listed by pseudonym, with the case's number and
        visits."""
        found = views.cases_view(self.label, self.service.cases(self.label))
        self.assertEqual(found, [{"number": 1,
                                  "name": names.pseudonym(self.label, 1),
                                  "detail": "Case 1 · 3 Visits",
                                  "prediction": True}])

    def test_case(self):
        """A case has its patient's pseudonym and greeting, its scopes
        grouped for the menus with what each holds, the prediction's
        evidence last, and its prediction with the score's fixed meaning,
        never a percentage; no case set's label is shown."""
        found = views.case_view(self.case, self.visits)
        name = names.pseudonym(self.label, 1)
        given = names.given_name(name)
        self.assertEqual(found["name"], name)
        self.assertEqual(found["tags"],
                         ["Case 1", "Demonstration Cohort", "3 Visits"])
        self.assertEqual(found["subtitle"], " · ".join(found["tags"]))
        self.assertEqual(found["greeting"],
                         "What Would You Like to Know About %s?" % given)
        self.assertEqual(found["placeholder"], "Ask About %s's Record" % given)
        self.assertEqual([(scope["key"], scope["group"], scope["label"])
                          for scope in found["scopes"]], [
            ("pre_target_history", "Across Visits", "Prior History"),
            ("case_visits", "Across Visits", "All Visits"),
            ("selected_visit:1", "Single Visit", "Visit 1"),
            ("selected_visit:2", "Single Visit", "Visit 2"),
            ("selected_visit:3", "Single Visit", "Visit 3")])
        self.assertEqual(found["scopes"][2]["detail"], "1 Jan 2100")
        self.assertEqual(found["scopes"][4]["detail"],
                         "3 Jan 2100 · Final Admission")
        self.assertEqual(found["scopes"][0]["detail"],
                         "Visits before the final admission")
        self.assertEqual(found["evidence"][-1]["key"], "prediction")
        self.assertEqual(found["evidence"][-1]["label"], "Predicted Diagnosis")
        prediction = found["prediction"]
        self.assertEqual([fact["value"] for fact in prediction["facts"]],
                         ["0.818", "1.5"])
        self.assertIn(labels.NOT_A_RISK, prediction["meaning"])
        text = json.dumps(found)
        self.assertNotIn("%", text)
        self.assertNotIn(self.label, text.replace(
            '"case_set": "%s"' % self.label, ""))
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

    def test_names_show_in_title_case(self):
        """The names of concepts in a line show in title case, the longest
        first and each only where it stands whole; the rest of the line is
        kept as released."""
        shown = views.namer({"heart failure", "congestive heart failure",
                             "Aortocoronary bypass graft present"})
        self.assertEqual(shown(
            "- Congestive heart failure: 2 records in visit 1"),
            "- Congestive Heart Failure: 2 records in visit 1")
        self.assertEqual(shown("aortocoronary bypass graft present; "
                               "heart failures"),
                         "Aortocoronary Bypass Graft Present; heart failures")
        self.assertEqual(views.namer(())("kept as written"),
                         "kept as written")

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
        self.assertEqual(found["scope"], {"key": "pre_target_history",
                                          "label": "Prior History"})
        self.assertEqual(found["evidence"], "pre_target_history")
        self.assertEqual(found["author"], "By the Model")
        self.assertRegex(found["seconds"], r"^\d+\.\d s$")
        self.assertIsNone(found["withheld"])

    def test_presence_names_its_condition(self):
        """A presence answer names the condition asked about in title case,
        whether or not it is recorded."""
        visit = {"kind": "selected_visit", "visit_number": 2}
        for scope in (None, visit):
            found = views.response_view(self.ask(
                "Is synthetic concept 1001 recorded?", scope))
            self.assertTrue(found["lines"][0]["text"].startswith(
                "Synthetic Concept 1001 is "), found["lines"][0]["text"])
            self.assertTrue(found["text"].startswith("Synthetic Concept"))

    def test_withheld(self):
        """A response without text says so, with its reasons."""
        result = self.ask("Which conditions are recorded?")
        response = dict(result["response"], outcome="validation_rejection",
                        text=None, claims=[],
                        reason_codes=["claim_unsupported"])
        found = views.response_view(dict(result, response=response,
                                         author="template",
                                         fallback="gate_rejected"))
        self.assertEqual(found["withheld"],
                         "No text was released: claim unsupported.")
        self.assertEqual(found["lines"], [])
        self.assertEqual(found["outcome"]["tone"], "bad")
        self.assertIn("Withheld by Validation", found["author"])

    def test_explanation_keeps_its_parts_apart(self):
        """An explanation carries the prediction, the note that attribution
        is unsigned, and the conditions recorded in the prior history in a
        block of their own."""
        history = self.service.evidence(self.label, 1, "pre_target_history")
        known = views.names_of(self.service.evidence(self.label, 1,
                                                     "prediction"))
        found = views.explanation_view(self.service.explain(self.label, 1),
                                       self.case, history, known)
        self.assertEqual(found["outcome"]["code"], "explanation")
        self.assertEqual(found["evidence"], "prediction")
        self.assertEqual(found["prediction"]["label"], "SYNTHETIC LABEL")
        self.assertEqual(found["unsigned"], labels.UNSIGNED)
        self.assertEqual(found["generated"], labels.GENERATED)
        table = found["conditions"]["table"]
        self.assertEqual([column["label"] for column in table["columns"]],
                         ["Condition", "Records", "Visits"])
        self.assertEqual(table["rows"], [{"ref": None, "cells": [
            "SYNTHETIC CONCEPT 1001", "1", "1"]}])
        self.assertIn("P1", found["references"])
        self.assertIn("SYNTHETIC CONCEPT 3001", known)
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
        """A scope's evidence: its coverage in words, its visits, its
        records with their names and dates as the page shows them, and
        its folded provenance, each row with its reference and no native
        identifier."""
        view = dict(self.service.evidence(self.label, 1,
                                          "pre_target_history"),
                    record=None)
        found = views.evidence_view(view)
        self.assertEqual(found["title"], "Prior History")
        self.assertEqual([section["title"] for section in found["sections"]],
                         ["Coverage", "Visits", "Records", "Provenance"])
        coverage = found["sections"][0]
        self.assertEqual(coverage["ref"], "C1")
        self.assertEqual(coverage["blocks"][0]["items"][0],
                         {"label": "Status", "value": "Complete"})
        self.assertTrue(found["sections"][-1]["collapsed"])
        self.assertEqual(self.refs(found), {"C1", "V1", "V2", "E1"})
        visits = found["sections"][1]["blocks"][0]["rows"]
        self.assertEqual(visits[0]["cells"], ["V1", "1", "Prior",
                                              "1 Jan 2100", "2 Jan 2100"])
        record = found["sections"][2]["blocks"][0]["rows"][0]["cells"]
        self.assertEqual(record[2], "Condition")
        text = json.dumps(found)
        self.assertNotIn("native", text)
        self.assertNotIn("visit_occurrence_id", text)

    def test_scope_with_the_whole_record(self):
        """With the whole record, a scope's evidence adds the person, each
        visit's admission, a table for each kind of record and the record
        coverage; every row keeps its reference and no native
        identifier."""
        found = views.evidence_view(self.service.evidence(
            self.label, 1, "case_visits"))
        titles = [section["title"] for section in found["sections"]]
        self.assertEqual(titles[:4], ["Coverage", "Person", "Visits",
                                      "Conditions"])
        self.assertEqual(titles[-3:], ["Record Coverage", "Provenance",
                                       "Record Provenance"])
        self.assertIn("Laboratory Results · 2", titles)
        self.assertIn("Vital Signs · 2", titles)
        person = found["sections"][1]
        self.assertEqual((person["ref"], person["blocks"][0]["items"][0]),
                         ("D1", {"label": "Sex", "value": "Female"}))
        refs = self.refs(found)
        self.assertTrue({"V1", "V2", "V3", "D1", "C1"} <= refs)
        self.assertTrue(any(ref.startswith("R") for ref in refs))
        labs = next(section for section in found["sections"]
                    if section["title"].startswith("Laboratory"))
        cells = labs["blocks"][0]["rows"][0]["cells"]
        self.assertEqual(cells[4:7], ["5.6 mEq/L", "3.3 to 5.1",
                                      "Abnormal"])
        text = json.dumps(found)
        self.assertNotIn("native", text)
        self.assertNotIn("hadm", text)

    def test_prediction(self):
        """The prediction's evidence shows every attribution with its
        unsigned score and link status, warns of those without a verified
        link, and says what the case's restrictions mean."""
        view = self.service.evidence(self.label, 1, "prediction")
        found = views.evidence_view(view)
        self.assertEqual(found["title"], "Predicted Diagnosis")
        attributions = [section for section in found["sections"]
                        if section["title"] == "Attributions"][0]
        table = [block for block in attributions["blocks"]
                 if block["type"] == "table"][0]
        self.assertIn("Score (Unsigned)", [column["label"]
                                           for column in table["columns"]])
        self.assertIn("Link Status", [column["label"]
                                      for column in table["columns"]])
        self.assertEqual(len(table["rows"]), len(view["attributions"]))
        self.assertEqual(attributions["blocks"][0]["text"], labels.UNSIGNED)
        self.assertTrue({"P1", "C1", "A1", "E1", "E3"} <= self.refs(found))
        explained = self.service.explain(self.label, 1)
        self.assertTrue(set(labels.references(explained["response"]))
                        <= self.refs(found))
        restrictions = [section for section in found["sections"]
                        if section["title"] == "Restrictions"][0]
        self.assertTrue(all(block["text"].endswith(".")
                            and "_" not in block["text"]
                            for block in restrictions["blocks"]))
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
            "message": "This patient isn't among the admitted cases."})


if __name__ == "__main__":
    unittest.main()
