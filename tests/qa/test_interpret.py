"""Synthetic checks for ehr_evidence_app.qa.interpret and qa.scope.

Every question is invented. Paraphrase families are grouped by the task
they must yield; none of them is the independent held-out test.
"""

import unittest

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.qa import interpret, scope

HISTORY = contracts.ScopeRef(kind="pre_target_history")
ALL = contracts.ScopeRef(kind="case_visits")


def visit(number):
    """Return a selected-visit scope."""
    return contracts.ScopeRef(kind="selected_visit", visit_number=number)


class NormalisationTests(unittest.TestCase):
    """Questions normalise to plain lower-case words."""

    def test_normalize(self):
        """Contractions, possessives, symbols and codes."""
        self.assertEqual(interpret.normalize("What's the patient's CKD?"),
                         "what is the patient ckd")
        self.assertEqual(interpret.normalize("Is I50.9 recorded?"),
                         "is i50.9 recorded")
        self.assertEqual(interpret.normalize("  Heart-failure!!  "),
                         "heart failure")
        self.assertEqual(interpret.normalize(None), "")

    def test_clean_term(self):
        """Question words around the condition are removed."""
        for raw, clean in (("any history of copd", "copd"),
                           ("a diagnosis of heart failure recorded",
                            "heart failure"),
                           ("the patient hypertension", "hypertension"),
                           ("aki during", "aki"),
                           ("sepsis at any point", "sepsis")):
            self.assertEqual(interpret.clean_term(raw), clean)


class PresenceFamilyTests(unittest.TestCase):
    """Every paraphrase of a presence check yields the same term."""

    FAMILY = (
        "Is heart failure recorded?", "Was heart failure documented?",
        "Does the patient have heart failure?",
        "Did she ever have heart failure?",
        "Has the patient been diagnosed with heart failure?",
        "Has he had heart failure?", "Any history of heart failure?",
        "Is there any record of heart failure?",
        "Is there evidence of heart failure?",
        "Check whether heart failure is recorded.",
        "Can you check if the patient has heart failure?",
        "Tell me whether heart failure was coded.",
        "Heart failure?", "heart failure",
        "Is heart failure in the patient's history?",
        "Has heart failure ever been diagnosed?",
        "Heart failure is documented, right?",
        "History of heart failure?",
    )

    def test_family(self):
        """All paraphrases give a presence check for heart failure."""
        for question in self.FAMILY:
            result = interpret.interpret(question)
            self.assertEqual((result.status, result.task, result.term),
                             ("task", "condition_presence", "heart failure"),
                             question)

    def test_filtered_list_is_presence(self):
        """'Which X diagnoses are recorded' checks X."""
        result = interpret.interpret("Which diabetes diagnoses are recorded?")
        self.assertEqual((result.task, result.term),
                         ("condition_presence", "diabetes"))


class ListFamilyTests(unittest.TestCase):
    """Every paraphrase of a list request yields a list."""

    FAMILY = (
        "List all recorded conditions.", "List the diagnoses.",
        "What diagnoses are recorded?", "Which conditions are documented?",
        "What conditions does the patient have?",
        "What has the patient been diagnosed with?",
        "Show me the past medical history.",
        "Give me all the diagnoses.", "What are her medical problems?",
        "Summarise the conditions.", "How many diagnoses were coded?",
        "What is the past medical history?",
    )

    def test_family(self):
        """All paraphrases give a list without a term."""
        for question in self.FAMILY:
            result = interpret.interpret(question)
            self.assertEqual((result.status, result.task, result.term),
                             ("task", "condition_list", None), question)


class OutOfScopeTests(unittest.TestCase):
    """Unsupported, unclear and other-patient questions end early."""

    CASES = (
        ("Why did the model predict sepsis?", "clarify",
         "prediction_selection_needed"),
        ("Explain this prediction", "clarify", "prediction_selection_needed"),
        ("Is diabetes recorded for patient 12?", "clarify",
         "case_ambiguous"),
        ("What about the other patient?", "clarify", "case_ambiguous"),
        ("What should we give her for her diabetes?", "abstain",
         "task_unsupported"),
        ("How should heart failure be treated?", "abstain",
         "task_unsupported"),
        ("Will the patient die?", "abstain", "task_unsupported"),
        ("What is his risk of stroke?", "abstain", "task_unsupported"),
        ("What's wrong with him?", "abstain", "task_unsupported"),
        ("Diagnose this patient", "abstain", "task_unsupported"),
        ("What medications is he on?", "abstain", "task_unsupported"),
        ("Show the lab results", "abstain", "task_unsupported"),
        ("Which procedures were performed?", "abstain", "task_unsupported"),
        ("Tell me about the weather", "clarify", "task_ambiguous"),
        ("", "clarify", "task_ambiguous"),
        ("List the conditions and tell me if diabetes is recorded",
         "clarify", "task_ambiguous"),
        ("Is it recorded?", "clarify", "concept_unknown"),
    )

    def test_cases(self):
        """Each question ends with its expected status and reason."""
        for question, status, reason in self.CASES:
            result = interpret.interpret(question)
            self.assertEqual((result.status, result.reason),
                             (status, reason), question)

    def test_clinical_wording_stays_a_presence_check(self):
        """Condition names that contain a data word are still conditions."""
        for question, term in (
                ("Does she have high blood pressure?", "high blood pressure"),
                ("Is drug abuse recorded?", "drug abuse"),
                ("Any history of adverse drug reaction?",
                 "adverse drug reaction"),
                ("Was drug induced liver injury coded?",
                 "drug induced liver injury"),
                ("Is there a record of allergy to medication?",
                 "allergy to medication")):
            result = interpret.interpret(question)
            self.assertEqual((result.task, result.term),
                             ("condition_presence", term), question)
        for question in ("Which drugs was he given?",
                         "Is a medication list recorded?"):
            self.assertEqual(interpret.interpret(question).reason,
                             "task_unsupported", question)


class ScopeMentionTests(unittest.TestCase):
    """Scope phrases are found and removed from the condition phrase."""

    def check(self, question, kind, number=None, last=False):
        """Assert the single scope mention of a question."""
        result = interpret.interpret(question)
        self.assertEqual(len(result.mentions), 1, question)
        mention = result.mentions[0]
        self.assertEqual((mention.kind, mention.visit_number, mention.last),
                         (kind, number, last), question)
        return result

    def test_phrases(self):
        """History, one visit, the last visit and all visits."""
        self.check("Is sepsis recorded before the final admission?",
                   "pre_target_history")
        self.check("Was sepsis coded in previous admissions?",
                   "pre_target_history")
        self.check("Is sepsis recorded in the second admission?",
                   "selected_visit", 2)
        self.check("Is sepsis recorded in visit number 3?",
                   "selected_visit", 3)
        self.check("Is sepsis recorded during the last admission?",
                   "selected_visit", last=True)
        self.check("Was sepsis recorded in any admission?", "case_visits")
        self.check("Is sepsis in the entire record?", "case_visits")
        result = self.check("Is there evidence of AKI during the final "
                            "admission?", "selected_visit", last=True)
        self.assertEqual(result.term, "aki")

    def test_no_scope_words(self):
        """'Ever' and 'at any time' are not scope phrases."""
        for question in ("Has she ever had sepsis?",
                         "Was sepsis recorded at any time?"):
            self.assertEqual(interpret.interpret(question).mentions, ())


class BindingTests(unittest.TestCase):
    """The active scope is kept, or the user is asked."""

    def bind(self, question, active, visits=3):
        """Bind a question's mentions to an active scope."""
        return scope.bind(interpret.interpret(question).mentions, active,
                          visits)

    def test_active_scope_is_kept(self):
        """No phrase, or a phrase naming the active scope, binds."""
        for question, active in (
                ("Is sepsis recorded?", HISTORY),
                ("Is sepsis recorded before the last admission?", HISTORY),
                ("Is sepsis recorded in visit 2?", visit(2)),
                ("Is sepsis recorded in the final admission?", visit(3)),
                ("Is sepsis recorded in any admission?", ALL)):
            binding = self.bind(question, active)
            self.assertEqual((binding.status, binding.scope),
                             ("bound", active), question)

    def test_other_scopes_are_asked_about(self):
        """Another scope, two scopes or a missing visit clarify."""
        binding = self.bind("Is sepsis recorded in the final admission?",
                            HISTORY)
        self.assertEqual((binding.status, binding.reason, binding.requested),
                         ("clarify", "scope_ambiguous", visit(3)))
        binding = self.bind("Is sepsis recorded in visit 1 or in any "
                            "admission?", HISTORY)
        self.assertEqual((binding.reason, binding.requested),
                         ("scope_ambiguous", None))
        binding = self.bind("Is sepsis recorded in visit 7?", HISTORY)
        self.assertEqual(binding.reason, "visit_ambiguous")


if __name__ == "__main__":
    unittest.main()
