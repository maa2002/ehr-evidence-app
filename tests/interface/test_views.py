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
        self.assertEqual(found["greeting_lines"], [
            "What Would You Like", "to Know About %s?"
            % found["greeting"].rsplit(" ", 1)[1][:-1]])
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
        self.assertIn(views.sentence(labels.NOT_A_RISK), prediction["meaning"])
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
        the scope it answered and its seconds, never who wrote it."""
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
        self.assertNotIn("author", found)
        self.assertRegex(found["seconds"], r"^\d+\.\d s$")
        self.assertIsNone(found["withheld"])

    def test_a_response_that_asks_back_points_to_the_help(self):
        """A clarification or an abstention points to /help; an answer
        does not."""
        for question in ("Was florbnazine given?",
                         "Should she get heparin?"):
            found = views.response_view(self.ask(question))
            self.assertIn(found["outcome"]["code"],
                          ("clarification", "abstention"), question)
            self.assertEqual(found["hint"], labels.HELP_HINT, question)
        self.assertIsNone(views.response_view(self.ask(
            "Which conditions are recorded?"))["hint"])

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
        self.assertNotIn("author", found)

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
        self.assertEqual(found["prediction"]["plain"], labels.SCORE_PLAIN)
        self.assertTrue(found["prediction"]["meaning"].startswith(
            "Score: Uncalibrated sigmoid"))
        # Without the visits the prediction still says what it is.
        self.assertIn("final admission: of the 275",
                      found["prediction"]["target"])
        self.assertIsNone(found["prediction"]["timeline"])
        visits = self.service.evidence(self.label, 1, "prediction")["visits"]
        placed = views.explanation_view(self.service.explain(self.label, 1),
                                        self.case, history, known, visits)
        self.assertEqual(placed["prediction"]["timeline"], {
            "earlier": "Visits 1 to 2 · 1 Jan 2100 to 2 Jan 2100",
            "final": "Visit 3 · 3 Jan 2100",
            "visit": " (visit 3, 3 Jan 2100)"})
        self.assertIn("recorded for the final admission (visit 3, 3 Jan "
                      "2100)", placed["prediction"]["target"])
        self.assertEqual(found["intro"], labels.EXPLANATION_INTRO)
        self.assertEqual([item["term"] for item in found["glossary"]],
                         [term for term, _ in labels.GLOSSARY])
        # The response's fixed notes stand apart from what it states; the
        # note on attribution is not repeated.
        self.assertTrue(found["notes"])
        self.assertTrue(any("unsigned" in note for note in found["notes"]))
        self.assertIsNone(found["unsigned"])
        lines = found["lines"]
        for index, line in enumerate(lines):
            following = lines[index + 1] if index + 1 < len(lines) else None
            self.assertTrue(line["refs"] or line["depth"] is not None or (
                following is not None and following["depth"] is not None),
                line["text"])
        self.assertEqual(len(lines) + len(found["notes"]), len(
            views.response_view(self.service.explain(self.label, 1),
                                known)["lines"]))
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

    def test_the_prediction_against_the_record(self):
        """Whether the predicted diagnosis is recorded for the final
        admission: the diagnosis itself first, then under an ICD code,
        then a more specific diagnosis; "not recorded" only with complete
        records."""
        def check(matches, coverage="complete"):
            """Return the page's record check of some matches."""
            return views.record_check({"visit_number": 3,
                                       "coverage": coverage,
                                       "matches": matches})
        exact = {"ref": "E1", "match": "exact", "concept_name": "A",
                 "source_code": None}
        icd = dict(exact, match="icd_description", concept_name=None)
        specific = dict(exact, match="more_specific",
                        concept_name="hypertensive heart disease")
        self.assertEqual(check([exact, exact, specific]), {
            "tone": "good", "text": labels.RECORD_EXACT.format(
                records="2 records", visit=3)})
        self.assertEqual(check([icd, specific])["text"],
                         labels.RECORD_ICD.format(records="1 record",
                                                  visit=3))
        self.assertEqual(check([specific]), {
            "tone": "info", "text": labels.RECORD_MORE_SPECIFIC.format(
                names="Hypertensive Heart Disease")})
        self.assertEqual(check([]), {
            "tone": "neutral", "text": labels.RECORD_ABSENT.format(
                visit=3)})
        self.assertEqual(check([], "complete_empty")["tone"], "neutral")
        self.assertEqual(check([], "failed"), {
            "tone": "muted", "text": labels.RECORD_UNKNOWN.format(
                visit=3)})
        self.assertIsNone(views.record_check(None))
        # The evidence of the invented prediction carries the check, and
        # the explanation and the evidence panel show it.
        view = self.service.evidence(self.label, 1, "prediction")
        found = views.explanation_view(
            self.service.explain(self.label, 1), self.case, None, (),
            view["visits"], view["in_record"])
        self.assertEqual(found["prediction"]["record"],
                         views.record_check(view["in_record"]))
        section = views.evidence_view(view)["sections"][0]
        self.assertIn(found["prediction"]["record"]["text"],
                      [block.get("text") for block in section["blocks"]])


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

    def test_the_whole_record_at_its_edges(self):
        """The invented edge record: every kind gets its table, records
        without a name say why, a removed value has no unit, an open
        transfer has no end, and no native identifier is shown."""
        from ehr_evidence_app.api import service as services
        from tests.qa import record_cases
        for bundle in record_cases.bundles():
            whole = services.whole_record_view(bundle)
            text = json.dumps(whole)
            self.assertNotIn("native_id", text)
            self.assertNotIn("\"source\"", text)
        whole = services.whole_record_view(record_cases.bundles()[-1])
        sections = {section["title"].split(" · ")[0]: section
                    for section in views.record_sections(whole)}
        self.assertEqual(set(sections) - {"Record Coverage"},
                         {title for _, title in views.KIND_TITLES})
        self.assertTrue(sections["Condition Records"]["collapsed"])
        self.assertFalse(sections["Laboratory Results"]["collapsed"])

        def cells(title):
            """Return the cells of a section's rows."""
            return [row["cells"]
                    for row in sections[title]["blocks"][-1]["rows"]]

        labs = cells("Laboratory Results")
        self.assertIn("Removed by de-identification",
                      [row[4] for row in labs])
        self.assertIn("Blood, Chemistry (Name Unavailable)",
                      [row[2] for row in labs])
        medications = [row[2] for row in cells("Medications")]
        self.assertIn("Vancomycin (No Standard Concept)", medications)
        self.assertIn("No Standard Concept", medications)
        transfers = cells("Transfers")
        self.assertEqual(transfers[-1][-1], "")
        self.assertEqual([row[-1] for row in cells("ICU Stays")],
                         ["1.5", "1.0"])
        visits = views.admissions_section(whole["visits"])
        rows = visits["blocks"][0]["rows"]
        self.assertEqual(rows[1]["cells"][6:8], ["1 Mar 2150", "3 Mar 2150"])
        self.assertEqual(rows[2]["cells"][-1], "Yes")
        person = views.person_section(whole["person"])
        self.assertEqual(person["blocks"][0]["items"][1]["value"],
                         "88 in 2149")
        older = views.person_section(dict(whole["person"], anchor_age=91))
        self.assertEqual(older["blocks"][0]["items"][1]["value"],
                         "Over 89 in 2149")

    def test_values_as_recorded(self):
        """Numbers keep every recorded digit and never an exponent; a
        result without a value says so."""
        for value, shown in ((5.0, "5"), (0.1, "0.1"), (123456.7,
                                                        "123456.7"),
                             (0.00001, "0.00001"), (-2.5, "-2.5"),
                             (1e20, "100000000000000000000"), (None, "")):
            self.assertEqual(labels.number(value), shown, value)
        self.assertEqual(views.measure_cells(None),
                         ("No value recorded", "", ""))
        self.assertEqual(views.measure_cells({"text": None, "number": 0.00001,
                                              "unit": "mg"}),
                         ("0.00001 mg", "", ""))
        self.assertEqual(views.measure_cells({"text": "___", "number": None,
                                              "unit": "mg", "flag":
                                              "abnormal"}),
                         ("Removed by de-identification", "", "Abnormal"))

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


class HelpTests(unittest.TestCase):
    """The help shown for /help: that questions are not a fixed list, and
    examples that the rules read as their kind's questions."""

    # The kinds of record each section's examples ask about.
    KINDS = {"Medications": {"medication"}, "Laboratory Results": {"lab"},
             "Vital Signs, Charted in the ICU": {"vital"},
             "Procedures": {"procedure"}, "Microbiology": {"microbiology"},
             "Stays, Units and the ICU": {"visit", "transfer", "icu_stay"},
             "The Patient": {"person"}}

    def test_the_view(self):
        """The help says that questions are not a fixed list before it
        lists each kind of information with its examples; a command that
        is not one gets a line of its own."""
        found = views.help_view()
        self.assertEqual((found["command"], found["title"]),
                         ("/help", "What You Can Ask"))
        self.assertTrue(found["intro"].startswith(
            "The questions are not a fixed list"))
        self.assertTrue(found["intro"].endswith(
            "a reply that says what was not understood."))
        self.assertNotIn("language model", json.dumps(found))
        self.assertEqual([section["title"] for section in found["sections"]],
                         ["Conditions"] + list(self.KINDS))
        for section in found["sections"]:
            for row in section["rows"]:
                self.assertEqual(row["what"], labels.title(row["what"]))
                self.assertTrue(row["examples"], row["what"])
        self.assertTrue(found["prediction"]["text"].startswith(
            "Press Generate the Predicted Diagnosis"))
        self.assertEqual(found["unknown"], {
            "label": "Not a Command", "tone": "neutral",
            "text": labels.HELP_UNKNOWN})
        self.assertIn("/help", found["unknown"]["text"])
        json.dumps(found)

    def test_every_example_is_answered_as_its_kind(self):
        """Each example is read as its section's question, never as a
        clarification: a condition question by the condition rules, any
        other by the record rules, which answer it on the invented record."""
        from ehr_evidence_app.qa import answer, interpret, record_interpret
        from ehr_evidence_app.qa import suite
        from tests.qa import record_cases as cases
        context = cases.context()
        vocab = suite.synthetic_vocabulary()
        for title, rows in labels.HELP_SECTIONS:
            for _, examples in rows:
                for example in examples:
                    with self.subTest(example=example):
                        first = interpret.interpret(example)
                        if title == "Conditions":
                            self.assertEqual(first.status, "task")
                            continue
                        self.assertIn(first.status, ("abstain", "clarify"))
                        self.assertTrue(answer.record_allowed(first))
                        reading = record_interpret.interpret(example,
                                                             context.terms)
                        self.assertEqual(reading.status, "task")
                        self.assertIn(reading.kind, self.KINDS[title])
                        found = answer.record_result(
                            example, cases.CASE, cases.ALL, cases.Visits(),
                            vocab, "q-1", context)
                        self.assertIn(found.response.outcome,
                                      ("answer", "not_recorded"))

    def test_the_texts_are_plain(self):
        """The help's words are plain text, in their own case: titles in
        title case, sentences ending in a full stop."""
        for text in (labels.HELP_TITLE, labels.HELP_PREDICTION[0]):
            self.assertEqual(text, labels.title(text))
        for text in labels.HELP_NOTES + (
                labels.HELP_INTRO, labels.HELP_PREDICTION[1],
                labels.HELP_HINT, labels.HELP_UNKNOWN):
            self.assertTrue(text.endswith("."), text)
            self.assertNotRegex(text, r"[<>{}]")


if __name__ == "__main__":
    unittest.main()
