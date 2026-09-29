"""Synthetic tests of the interface's page (interface.app) with Streamlit's
test harness.

A stand-in client answers from the invented service in memory, showing
its one invented case as two, so that a case change can be seen; one test
runs the page against the real API on a loopback port. No real record is
used.
"""

import copy
import os
import unittest

from streamlit.testing.v1 import AppTest

from ehr_evidence_app.api import app as api
from ehr_evidence_app.api import boundary
from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.interface import client as clients

PAGE = os.path.join(os.path.dirname(clients.__file__), "app.py")
LIST = "Which conditions are recorded?"


class StandIn:
    """A client answering from the invented service: its one case shown
    as cases 1 and 2."""

    def __init__(self):
        """Serve the invented case set with the scripted model."""
        self.service = api.invented_service()
        self.label = self.service.case_sets()[0]["case_set"]
        self.asked = []

    def health(self):
        """The model's state and the versions."""
        return self.service.health()

    def case_sets(self):
        """The one invented case set, with two cases."""
        return [{"case_set": self.label, "cases": 2}]

    def cases(self, case_set):
        """Cases 1 and 2."""
        found = self.service.cases(case_set)[0]
        return [dict(found, case_number=number) for number in (1, 2)]

    def case(self, case_set, case_number):
        """The invented case, numbered as asked."""
        return dict(self.service.case(case_set, 1), case_number=case_number)

    def evidence(self, case_set, case_number, kind, visit_number=None):
        """The invented case's evidence."""
        return self.service.evidence(case_set, 1, kind, visit_number)

    def ask(self, case_set, case_number, scope, question):
        """Answer about the invented case."""
        self.asked.append((case_number, scope, question))
        found = copy.deepcopy(self.service.ask(
            case_set, 1, contracts.ScopeRef(**scope), question))
        found["response"]["case"]["case_number"] = case_number
        return found

    def explain(self, case_set, case_number):
        """Explain the invented case's prediction."""
        return self.service.explain(case_set, 1)


def page():
    """Return the page run against a stand-in client."""
    tester = AppTest.from_file(PAGE, default_timeout=60)
    tester.session_state["api"] = StandIn()
    return tester.run()


def texts(tester, kind):
    """Return the values of one kind of element."""
    return [item.value for item in getattr(tester, kind)]


def ask(tester, question=LIST):
    """Ask a question through the form."""
    tester.text_input(key="question").input(question)
    tester.button[0].click()
    return tester.run()


class PageTests(unittest.TestCase):
    """The selector, the questions, the explanation and the evidence."""

    def test_selector_and_status(self):
        """The case set, the case and the scope are chosen in the sidebar,
        and the page names the active case, scope and model state."""
        tester = page()
        self.assertEqual(texts(tester, "exception"), [])
        self.assertEqual([box.label for box in tester.sidebar.selectbox],
                         ["Case set", "Case", "Scope"])
        self.assertIn("Case 1 of", texts(tester, "caption")[0])
        self.assertIn("scope: the pre-target history",
                      texts(tester, "caption")[0])
        self.assertIn("Model: Ready", texts(tester, "caption"))

    def test_answer_shows_outcome_scope_author_and_references(self):
        """A response shows its outcome, the scope it answered, who wrote
        it, its text and its references."""
        tester = ask(page())
        self.assertIn(":green-badge[Answer]", texts(tester, "markdown"))
        meta = [text for text in texts(tester, "caption")
                if text.startswith("Asked about")]
        self.assertEqual(len(meta), 1)
        self.assertIn("the pre-target history", meta[0])
        self.assertIn("written by the model", meta[0])
        self.assertEqual([pill.label for pill in tester.pills],
                         ["References"])
        self.assertTrue(tester.pills[0].options)

    def test_scope_change_never_shows_old_answers_as_current(self):
        """After a scope change the earlier response stays, greyed and
        labelled with the scope it answered."""
        tester = ask(page())
        tester.sidebar.selectbox[2].select("selected_visit:1")
        tester.run()
        earlier = [text for text in texts(tester, "caption")
                   if text.startswith("Earlier scope")]
        self.assertEqual(len(earlier), 1)
        self.assertIn("the pre-target history", earlier[0])
        self.assertNotIn(":green-badge[Answer]", texts(tester, "markdown"))
        self.assertIn(":gray-badge[Answer]", texts(tester, "markdown"))

    def test_case_change_clears_the_context(self):
        """A case change clears every earlier response."""
        tester = ask(page())
        self.assertEqual(len(tester.session_state["responses"]), 1)
        tester.sidebar.selectbox[1].select(2)
        tester.run()
        self.assertEqual(tester.session_state["responses"], [])
        self.assertFalse([text for text in texts(tester, "caption")
                          if text.startswith(("Asked about",
                                              "Earlier scope"))])

    def test_reference_opens_its_record(self):
        """A reference opens the evidence view on its record, in the scope
        the response answered."""
        tester = ask(page())
        ref = tester.pills[0].options[0]
        tester.pills[0].set_value(ref)
        tester.run()
        self.assertEqual(tester.segmented_control[0].value, "Evidence")
        self.assertTrue(any(text.startswith("Reference %s:" % ref)
                            for text in texts(tester, "info")))

    def test_explanation_keeps_prediction_drivers_and_records_apart(self):
        """The explanation view shows the prediction with its score's
        meaning, never as a percentage, the explanation with attribution
        labelled unsigned, and the recorded conditions apart."""
        tester = page()
        tester.segmented_control[0].set_value("Explanation")
        tester.run()
        captions = texts(tester, "caption")
        self.assertTrue(any("not a clinical risk" in text
                            for text in captions))
        self.assertFalse(any("%" in item.value for item in tester.metric))
        tester.button(key="explain").click()
        tester.run()
        self.assertIn(":green-badge[Explanation]", texts(tester, "markdown"))
        self.assertTrue(any(text.startswith("Attribution scores are "
                                            "unsigned") for text in captions
                            + texts(tester, "caption")))
        self.assertIn("**Conditions recorded in the pre-target history**",
                      texts(tester, "markdown"))
        self.assertEqual(texts(tester, "exception"), [])

    def test_evidence_shows_attribution_and_link_status(self):
        """The prediction's evidence lists every original attribution with
        its link status and never a native identifier."""
        tester = page()
        tester.segmented_control[0].set_value("Evidence")
        tester.run()
        key = [box for box in tester.selectbox
               if box.label == "Show the evidence of"][0]
        key.select("prediction")
        tester.run()
        frames = [frame.value for frame in tester.dataframe]
        self.assertIn("Link status", frames[0].columns)
        self.assertIn("Score (unsigned)", frames[0].columns)
        for frame in frames:
            for column in frame.columns:
                self.assertNotIn("native", column.lower())
        self.assertEqual(texts(tester, "exception"), [])

    def test_refusal_is_shown(self):
        """A refusal of the API is shown in words, not as a failure."""
        tester = AppTest.from_file(PAGE, default_timeout=60)
        stand_in = StandIn()

        def refuse(*args):
            """Refuse the question."""
            raise clients.ClientError("api_unreachable")

        stand_in.ask = refuse
        tester.session_state["api"] = stand_in
        tester.run()
        ask(tester)
        self.assertTrue(any("not answering" in text
                            for text in texts(tester, "error")))
        self.assertEqual(texts(tester, "exception"), [])


class LoopbackPageTests(unittest.TestCase):
    """The page against the real API on a loopback port."""

    def test_page_over_the_api(self):
        """The page reaches the API with the launch token and shows its
        answer."""
        service = api.invented_service()
        token = boundary.new_token()
        saved = {name: os.environ.get(name) for name in (
            clients.URL_VARIABLE, boundary.TOKEN_VARIABLE)}
        with api.LoopbackServer(lambda port: api.create_app(
                service, token, boundary.loopback_hosts(port))) as server:
            os.environ[clients.URL_VARIABLE] = server.url
            os.environ[boundary.TOKEN_VARIABLE] = token
            try:
                tester = AppTest.from_file(PAGE, default_timeout=60).run()
                tester = ask(tester)
            finally:
                for name, value in saved.items():
                    if value is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = value
        self.assertEqual(texts(tester, "exception"), [])
        self.assertIn(":green-badge[Answer]", texts(tester, "markdown"))


if __name__ == "__main__":
    unittest.main()
