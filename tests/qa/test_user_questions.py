"""Questions a user tried on the page, end to end through the service of
the invented case set, as the page asks them (api.service.Service.invented:
the condition rules first, then the record rules): greetings and requests
for help, sentences the frames do not read, rankings of conditions, when
a visit was, and a "not recorded" that begins with the question's own
words. Every person, record and value is invented."""

import unittest

from ehr_evidence_app.api import service as services
from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.qa import answer, interpret, record_answer
from ehr_evidence_app.validation import candidate as candidates

SERVICE = services.Service.invented()
LABEL = SERVICE.case_sets()[0]["case_set"]
HISTORY = contracts.ScopeRef(kind="pre_target_history")
VISIT = {number: contracts.ScopeRef(kind="selected_visit",
                                    visit_number=number)
         for number in (1, 2, 3)}
NOT_UNDERSTOOD = "The question was not understood."


def ask(question, scope=HISTORY):
    """Return the released response to a question about the invented
    case, as a mapping."""
    return SERVICE.ask(LABEL, 1, scope, question)["response"]


class ConversationTests(unittest.TestCase):
    """A greeting, thanks or a request for help is not a question about
    the record, and never a condition's name (rules v6)."""

    MESSAGES = ("help", "Help me", "please help", "Can you help me?", "hi",
                "Hi there", "hello", "Hey!", "Good morning", "thanks",
                "Thank you so much", "ok", "no", "what can I ask?",
                "What can you do?", "Hello, what can you do?",
                "How does this work?", "who are you?")

    def test_read_as_conversation(self):
        """The condition rules give no task and no condition phrase."""
        for message in self.MESSAGES:
            found = interpret.interpret(message)
            self.assertEqual((found.status, found.category, found.term),
                             ("clarify", "conversation", None), message)

    def test_answered_as_not_understood(self):
        """The page shows "not understood", never a condition's name."""
        for message in self.MESSAGES:
            response = ask(message)
            self.assertEqual(response["outcome"], "clarification", message)
            self.assertTrue(response["text"].startswith(NOT_UNDERSTOOD),
                            message)
            self.assertNotIn("condition name", response["text"], message)

    def test_a_greeting_before_a_question_keeps_the_question(self):
        """Only a whole message of such words is conversation."""
        for question in ("Is synthetic concept 1001 recorded?",
                         "help with synthetic concept 1001"):
            self.assertNotEqual(interpret.interpret(question).category,
                                "conversation", question)


class SentenceTests(unittest.TestCase):
    """A short phrase is a condition's name only when it holds no verb of
    a sentence (rules v6)."""

    def test_sentences_the_frames_do_not_read_are_not_names(self):
        """Misspelt questions are not understood, not looked up."""
        for question in ("wich condtions are recordd?",
                         "wat conditons were recordd",
                         "conditons have been recordd"):
            found = interpret.interpret(question)
            self.assertEqual((found.status, found.category),
                             ("clarify", "unrecognised"), question)
            response = ask(question)
            self.assertEqual(response["outcome"], "clarification", question)
            self.assertNotIn("condition name", response["text"], question)

    def test_names_alone_are_still_checked(self):
        """A name, misspelt or not, is still a presence check."""
        for phrase, term in (("synthetic concept 1001",
                              "synthetic concept 1001"),
                             ("type 2 diabetes", "type 2 diabetes"),
                             ("May-Thurner syndrome", "may thurner syndrome"),
                             ("asdfgh qwerty", "asdfgh qwerty")):
            found = interpret.interpret(phrase)
            self.assertEqual((found.status, found.task, found.term),
                             ("task", "condition_presence", term), phrase)
        self.assertEqual(ask("synthetic concept 1001")["outcome"], "answer")


class RankingTests(unittest.TestCase):
    """A ranking that names conditions, in the singular or the plural, is
    refused; one that names a test is a question about the record (answer
    rules v7)."""

    RANKINGS = ("What were the main conditions?",
                "What is the most important condition?",
                "Which condition came first?", "What is the main diagnosis?",
                "What is the primary problem?",
                "Which disease is the most serious?",
                "What was the first diagnosis?",
                "What were the top three diagnoses?",
                "What is her main diagnosis?")

    def test_rankings_of_conditions_stay_refused(self):
        """Not read as a question about the record; refused."""
        for question in self.RANKINGS:
            found = interpret.interpret(question)
            self.assertEqual(found.category, "ranking", question)
            self.assertFalse(answer.record_allowed(found), question)
            response = ask(question)
            self.assertEqual((response["outcome"], response["reason_codes"]),
                             ("abstention", ["task_unsupported"]), question)
            self.assertIn("not ranked", response["text"], question)

    def test_the_first_of_a_test_is_a_question_about_the_record(self):
        """"The first potassium" is a time, read by the record rules."""
        found = interpret.interpret("What was the first potassium?")
        self.assertTrue(answer.record_allowed(found))
        response = ask("What was the first potassium?")
        self.assertIn(response["outcome"], ("answer", "not_recorded"))
        self.assertNotIn("not ranked", response["text"])

    def test_no_stray_control_character_in_the_source(self):
        """The test of versions 5 and 6 held literal backspaces for its
        word boundaries; the module now holds no control character."""
        with open(answer.__file__, "rb") as handle:
            source = handle.read()
        self.assertFalse([byte for byte in source
                          if byte < 32 and byte not in (9, 10, 13)])


class VisitWhenTests(unittest.TestCase):
    """When a visit named by a scope phrase was (record rules v7, rule
    12)."""

    def test_asked_in_its_own_scope(self):
        """The visit's admission, in the visit's scope."""
        for question, number in (("When was the first visit?", 1),
                                 ("When was visit 2?", 2),
                                 ("What date was the final admission?", 3)):
            response = ask(question, VISIT[number])
            self.assertEqual(response["outcome"], "answer", question)
            self.assertIn("visit %d: admitted" % number, response["text"],
                          question)

    def test_asked_in_another_scope(self):
        """The scope must be changed to ask about it, as for every scope
        phrase; not the refusal of dates and times."""
        response = ask("When was the first visit?")
        self.assertEqual(response["outcome"], "clarification")
        self.assertIn("Change the scope", response["text"])
        self.assertNotIn("could not be interpreted", response["text"])


class NotRecordedTests(unittest.TestCase):
    """"Not recorded" begins with a capital when it begins with the
    question's own words in lower case (record answer rules v5)."""

    def test_begins_with_a_capital(self):
        """The question's words, their first letter a capital."""
        response = ask("Was a chest x-ray done?")
        self.assertEqual(response["outcome"], "not_recorded")
        self.assertTrue(response["text"].startswith(
            "Chest x ray is not recorded"), response["text"])

    def test_a_name_keeps_its_own_case(self):
        """A first word that is not in lower case alone keeps its case;
        only "not recorded" is changed."""
        lookup = candidates.Lookup(None, None)
        for terms, first in ((("pH",), "pH is not recorded"),
                             (("e. coli",), "E. coli is not recorded"),
                             (("Potassium",), "Potassium is not recorded"),
                             (("5-fluorouracil",),
                              "5-fluorouracil is not recorded")):
            statement = record_answer.statement(
                "record_absent", refs=("C1",),
                item=candidates.ItemName(terms=terms), scope=HISTORY)
            self.assertTrue(candidates.sentence(statement, lookup).startswith(
                first), terms)


if __name__ == "__main__":
    unittest.main()
