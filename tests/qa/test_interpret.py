"""Synthetic checks for ehr_evidence_app.qa.interpret and qa.scope.

Every question is invented. Paraphrase families are grouped by the task
they must yield; none of them is the independent held-out test.
"""

import unittest

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.qa import interpret, resolve, scope

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

    def test_normalize_like_the_vocabulary(self):
        """Accents and '&' are read as the vocabulary index reads them."""
        for text in ("Ménière's disease", "Crohn's disease & colitis",
                     "Sjögren syndrome"):
            self.assertEqual(interpret.normalize(text),
                             resolve.normalize(text), text)

    def test_candidates(self):
        """Question words leave the phrase one at a time, longest first."""
        for question, terms in (
                ("Is cough reflex present recorded?",
                 ("cough reflex present", "cough reflex")),
                ("Is newly diagnosed diabetes recorded?",
                 ("newly diagnosed diabetes",)),
                ("Has heart failure ever been diagnosed?",
                 ("heart failure ever been", "heart failure ever",
                  "heart failure")),
                ("Is a diagnosis of heart failure recorded in the chart?",
                 ("a diagnosis of heart failure recorded",
                  "diagnosis of heart failure recorded",
                  "heart failure recorded", "heart failure")),
                ("Does the patient have a history of heart failure?",
                 ("heart failure",)),
                ("Is muscle, ligament and fascia disorders recorded?",
                 ("muscle ligament and fascia disorders",))):
            result = interpret.interpret(question)
            self.assertEqual((result.task, result.terms),
                             ("condition_presence", terms), question)

    def test_words_for_conditions_ask_for_the_list(self):
        """'Are any diagnoses recorded' and similar questions are lists."""
        for question in ("Are any diagnoses recorded?",
                         "Is there any diagnosis recorded?",
                         "What medical conditions are recorded?"):
            self.assertEqual(interpret.interpret(question).task,
                             "condition_list", question)

    def test_code_prefixes(self):
        """'ICD-10 code', 'diagnosis code' and 'code' are not the term."""
        for question, term in (("Is ICD-10 code I50.9 recorded?", "i50.9"),
                               ("Was diagnosis code 428.0 coded?", "428.0"),
                               ("Is code E11.9 documented?", "e11.9"),
                               ("Any ICD-9-CM 250.00?", "250.00")):
            self.assertEqual(interpret.interpret(question).term, term,
                             question)

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
        ("Was heart failure recorded in 2150?", "abstain",
         "task_unsupported"),
        ("Has sepsis been coded since March?", "abstain",
         "task_unsupported"),
        ("Was AKI recorded after the surgery?", "abstain",
         "task_unsupported"),
        ("Is diabetes documented more than twice?", "abstain",
         "task_unsupported"),
        ("What conditions were recorded in the last 6 months?", "abstain",
         "task_unsupported"),
        ("When was heart failure first recorded?", "abstain",
         "task_unsupported"),
        ("What were the top three billed diagnoses?", "abstain",
         "task_unsupported"),
        ("List the top 5 diagnoses.", "abstain", "task_unsupported"),
        ("What is her main diagnosis?", "abstain", "task_unsupported"),
        ("What conditions do other patients have?", "clarify",
         "case_ambiguous"),
        ("Does the patient in bed 4 have COPD?", "clarify",
         "case_ambiguous"),
        ("Will her kidney disease get worse?", "abstain",
         "task_unsupported"),
        ("Did she undergo surgery?", "abstain", "task_unsupported"),
        ("List the diagnoses. Is heart failure recorded?", "clarify",
         "task_ambiguous"),
        ("Show all conditions; does she have COPD?", "clarify",
         "task_ambiguous"),
        ("List her conditions along with whether she has asthma", "clarify",
         "task_ambiguous"),
    )

    def test_ranking_words_inside_names_wait_for_the_vocabulary(self):
        """'Primary' in a condition name is a guard, not a refusal."""
        result = interpret.interpret("Is primary hypertension recorded?")
        self.assertEqual((result.task, result.term, result.guard),
                         ("condition_presence", "primary hypertension",
                          ("abstain", "task_unsupported", "ranking")))

    def test_list_and_history_wordings(self):
        """Problem lists, PMH, 'hx of' and inclusion wordings."""
        for question, task, term in (
                ("What's on her problem list?", "condition_list", None),
                ("Show me the PMH", "condition_list", None),
                ("hx of MI?", "condition_presence", "mi"),
                ("Is COPD among them?", "condition_presence", "copd"),
                ("Is diabetes on her problem list?", "condition_presence",
                 "diabetes"),
                ("Any past medical history of stroke?", "condition_presence",
                 "stroke")):
            result = interpret.interpret(question)
            self.assertEqual((result.task, result.term), (task, term),
                             question)

    def test_at_least_once_is_a_presence_check(self):
        """'At least once' asks whether it is recorded at all."""
        result = interpret.interpret(
            "Was heart failure recorded at least once?")
        self.assertEqual((result.task, result.term),
                         ("condition_presence", "heart failure"))

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
        self.assertEqual(interpret.interpret(
            "Which drugs was he given?").reason, "task_unsupported")

    def test_rules_inside_the_phrase_wait_for_the_vocabulary(self):
        """Out-of-scope words inside the phrase are kept as a guard."""
        for question, term, guard in (
                ("Is a medication list recorded?", "medication list",
                 ("abstain", "task_unsupported", "other_data")),
                ("Is plain X-ray of hand normal recorded?",
                 "plain x ray of hand normal",
                 ("abstain", "task_unsupported", "other_data")),
                ("Is hitting other person recorded?", "hitting other person",
                 ("clarify", "case_ambiguous", "other_patient")),
                ("Is heart failure recorded?", "heart failure", None)):
            result = interpret.interpret(question)
            self.assertEqual((result.task, result.term, result.guard),
                             ("condition_presence", term, guard), question)
        result = interpret.interpret("Is drug abuse recorded for patient 12?")
        self.assertEqual((result.status, result.reason),
                         ("clarify", "case_ambiguous"))


class FailSafeTests(unittest.TestCase):
    """Every word of an answered question is accounted for (rules v3)."""

    def check(self, cases, status, reason, category):
        """Assert that each question ends with the same fail-safe."""
        for question in cases:
            result = interpret.interpret(question)
            self.assertEqual(
                (result.status, result.reason, result.category),
                (status, reason, category), question)

    def test_unrecognised_visits_and_times(self):
        """Admissions, stays and times outside a scope phrase."""
        self.check((
            "Was heart failure coded in the admission before the last one?",
            "Is heart failure documented for the inpatient period?",
            "Was heart failure recorded during the hospital course?",
            "Is heart failure recorded in the ICU?",
            "Which diagnoses were coded at discharge?",
            "What are his current diagnoses?",
            "Was heart failure recorded then?",
            "Does the patient have heart failure now?",
            "Was sepsis recorded at that time?"),
            "clarify", "scope_ambiguous", "scope_unrecognised")

    def test_exclusions_and_filters(self):
        """Exclusions in any question; negations in a list."""
        self.check((
            "List the conditions except diabetes.",
            "What diagnoses other than diabetes are recorded?",
            "Which conditions are not recorded?",
            "List only the chronic conditions.",
            "Which diagnoses does she not have?",
            "Is heart failure recorded, excluding the final admission?"),
            "clarify", "task_ambiguous", "qualified")

    def test_unexplained_words(self):
        """Words outside the frames and the condition phrase."""
        self.check((
            "List the patient's cardiac conditions.",
            "Which diagnoses are chronic?",
            "Is heart failure recorded in his family history?",
            "Is diabetes recorded as a complication of pregnancy?",
            "Diabetes is recorded and hypertension is recorded."),
            "clarify", "task_ambiguous", "unexplained")

    def test_two_conditions(self):
        """Two presence checks in one message."""
        self.check((
            "Is heart failure recorded? Is diabetes recorded?",
            "Does the patient have asthma? Does the patient have COPD?"),
            "clarify", "task_ambiguous", "two_conditions")

    def test_counted_lists_are_refused(self):
        """A list limited to a number of conditions is a ranking."""
        self.check((
            "List three conditions.", "Give me a few of the diagnoses.",
            "What are 5 of the patient's conditions?",
            "Show two of her diagnoses."),
            "abstain", "task_unsupported", "ranking")

    def test_negative_and_harmless_wordings_stay_tasks(self):
        """Negative forms check the condition; known words are kept."""
        for question in (
                "Is there no record of heart failure?",
                "Is heart failure not recorded?",
                "Does the patient not have heart failure?",
                "Has the patient never had heart failure?",
                "Was heart failure never documented?",
                "Does the patient have heart failure or not?",
                "Check whether or not heart failure is recorded.",
                "Was the patient ever diagnosed with heart failure?",
                "Is heart failure recorded in this patient's chart?",
                "Was heart failure recorded at any time?"):
            result = interpret.interpret(question)
            self.assertEqual((result.task, result.term),
                             ("condition_presence", "heart failure"),
                             question)
        for question in ("Which diagnoses were coded for this case so far?",
                         "Could you please list her diagnoses?"):
            self.assertEqual(interpret.interpret(question).task,
                             "condition_list", question)

    def test_peel(self):
        """The words removed from the phrase's ends are returned."""
        self.assertEqual(
            interpret.peel("a history of heart failure recorded now"),
            (("a history of heart failure recorded now",
              "history of heart failure recorded now",
              "heart failure recorded now", "heart failure recorded",
              "heart failure"), "a history of now recorded"))


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

    def test_more_phrases(self):
        """Hospital stays, this admission, plural spans, number words."""
        for question, kind, number, last in (
                ("Was sepsis coded during this hospital stay?",
                 "selected_visit", None, True),
                ("Is sepsis recorded in the present admission?",
                 "selected_visit", None, True),
                ("Is sepsis recorded before this admission?",
                 "pre_target_history", None, False),
                ("Was sepsis recorded before admission?",
                 "pre_target_history", None, False),
                ("Any sepsis in her prior hospitalization?",
                 "pre_target_history", None, False),
                ("Was sepsis coded across her admissions?", "case_visits",
                 None, False),
                ("Was sepsis noted throughout his hospital stays?",
                 "case_visits", None, False),
                ("Is sepsis recorded in admission two?", "selected_visit", 2,
                 False),
                ("Was sepsis coded in ICU stay 3?", "selected_visit", 3,
                 False),
                ("Was sepsis coded during the patient's stays?",
                 "case_visits", None, False),
                ("Was sepsis coded across every admission?", "case_visits",
                 None, False),
                ("Which diagnoses were recorded at the time of the final "
                 "admission?", "selected_visit", None, True),
                ("Is sepsis recorded during the course of her most recent "
                 "admission?", "selected_visit", None, True)):
            result = self.check(question, kind, number, last)
            self.assertEqual(result.status, "task", question)

    def test_this_admission(self):
        """'This admission' is marked; 'the current admission' is not."""
        for question, this in (
                ("Is sepsis recorded during this admission?", True),
                ("Was sepsis coded in this hospital stay?", True),
                ("Is sepsis recorded in the current admission?", False)):
            mention = interpret.interpret(question).mentions[0]
            self.assertEqual((mention.last, mention.this), (True, this),
                             question)

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

    def test_this_admission_is_the_selected_visit(self):
        """With a visit selected, 'this admission' is that visit;
        otherwise it is the final admission."""
        question = "Is sepsis recorded during this admission?"
        for active in (visit(1), visit(3)):
            binding = self.bind(question, active)
            self.assertEqual((binding.status, binding.scope),
                             ("bound", active))
        for active in (HISTORY, ALL):
            binding = self.bind(question, active)
            self.assertEqual((binding.status, binding.requested),
                             ("clarify", visit(3)))
        binding = self.bind("Is sepsis recorded in the current "
                            "admission?", visit(1))
        self.assertEqual((binding.status, binding.requested),
                         ("clarify", visit(3)))

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
