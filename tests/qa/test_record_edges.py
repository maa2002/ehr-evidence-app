"""Edge cases of answers about the whole record, end to end.

Questions are read, resolved, answered by the templates and released
through the gate (qa.answer.record_result) against the invented record of
tests.qa.record_cases, whose records sit at the edges real records reach.
Every person, record and value is invented.
"""

import unittest

from ehr_evidence_app.evidence import contracts, record
from ehr_evidence_app.qa import answer, record_answer, suite
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import language
from tests.qa import record_cases as cases

VISIT = {number: contracts.ScopeRef(kind="selected_visit",
                                    visit_number=number)
         for number in (1, 2, 3)}
# Questions of every task, kind and selector, and the phrasings at the
# edges of the rules: abbreviations, negations, two items, flags,
# misspellings, times and places that are not scopes, and noise.
QUESTIONS = (
    "What labs were done?", "Show me the labs", "labs", "LABS???",
    "What medications were given?", "meds", "Which procedures were done?",
    "What vital signs were charted?", "What microbiology was done?",
    "Which units was the patient in?", "Was she in the ICU?",
    "How many ICU stays?", "Which conditions are recorded?",
    "Was potassium measured?", "K?", "What is the K?", "HR?", "BP?",
    "SpO2", "What was the last Hgb?", "Was troponin checked?",
    "When was potassium first measured?", "When was the first heparin?",
    "How many potassium results are there?", "How many medications?",
    "How many labs?", "How many times was heparin given?",
    "What was the last potassium?", "What was the first potassium?",
    "What was the highest potassium?", "What was the lowest potassium?",
    "What were all the potassium values?", "Show the potassium trend",
    "What was the highest troponin?", "What was the highest glucose?",
    "What was the creatinine?", "What were all the lab values?",
    "What were the vital signs?", "What was the lowest blood pressure?",
    "What was the highest heart rate?", "Was the heart rate high?",
    "Was the heart rate low?", "Was the heart rate abnormal?",
    "Were any vitals high?", "Were any vitals abnormal?",
    "Was the temperature high?", "Were any labs abnormal?",
    "Were any labs high?", "Was the potassium high?",
    "Was the hemoglobin low?", "Any resistant organisms?",
    "Were any cultures resistant?", "What organisms grew?",
    "Was the blood culture positive?", "What were the microbiology results?",
    "Was the blood culture negative?", "Were any cultures negative?",
    "Did the urine culture grow anything?",
    "Was the latest blood culture positive?",
    "What was the first urine culture result?",
    "What was the latest culture result?",
    "Was the latest potassium high?", "Was the first potassium normal?",
    "Is the latest creatinine abnormal?", "Was the troponin positive?",
    "Was the troponin T negative?", "Were any labs normal?",
    "Was the heart rate normal?", "Was the highest glucose abnormal?",
    "Was the latest culture resistant?", "Did she have surgery?",
    "How many operations?", "Was she admitted through the ED?",
    "Was it an emergency?", "Was she readmitted?", "Is she over 65?",
    "Was she alive at discharge?", "Did she die in hospital?",
    "Is the predicted diagnosis recorded?", "Was normal saline given?",
    "Did the culture grow gram negative rods?",
    "Did any culture grow gram positive cocci?",
    "Was the culture gram negative?", "Did she test positive for MRSA?",
    "Was the e coli test positive?",
    "Did any culture grow e coli?", "Was the creatinine normal, please?",
    "Was heparin given?", "Was heparin not given?", "Did she never get "
    "heparin?", "Was she on Lasix?", "Was furosemide given?",
    "Any antibiotics?", "Any opioids?", "Was insulin given?",
    "Any blood thinners?", "Any diuretics?", "Was sodium given?",
    "Was urea given?", "Was vancomycin given?", "Was morphine given?",
    "What dose of morphine was given?", "What route was vancomycin given?",
    "What doses were given?", "What medication doses were given?",
    "Was she intubated?", "Was a chest x-ray done?", "Did she have a CT?",
    "Was an appendectomy done?", "Was the chest x-ray abnormal?",
    "Any abnormal medications?", "How many visits?",
    "When was she admitted?", "When was she discharged?",
    "How long was she in hospital?", "What type of admission was it?",
    "Where was she discharged to?", "Where was she admitted from?",
    "What is the patient's sex?", "Is she male or female?",
    "How old was the patient?", "What is her race?",
    "What language does she speak?", "What insurance does she have?",
    "Is the patient married?", "Did the patient die?",
    "Did the patient not die?", "Is the patient alive?",
    "When was synthetic concept 1001 first recorded?",
    "How many times was synthetic concept 1001 recorded?",
    "When was hypertension first recorded?",
    "Was it given?", "Was anything given?", "What was given?",
    "Was heparin or furosemide given?", "Was she on insulin and heparin?",
    "Were the potassium and sodium checked?", "What labs were not done?",
    "Was potassium above 5?", "What labs except potassium?",
    "Was potassium 5.4?", "What was the potassium on day 2?",
    "Did she get vancomycin in the ICU?", "Was heparin given yesterday?",
    "Was heprin given?", "Was an apendectomy done?",
    "Was the potasium checked?", "Was florbnazine given?",
    "Was potassium measured in visit 2?",
    "Was potassium measured in visit 4?",
    "Was potassium measured in the last visit?",
    "Should she get heparin?", "Will she survive?",
    "Is she likely to need dialysis?", "<script>alert(1)</script> labs",
    "<b>labs</b>", "Was héparin given?", "Was there no heparin?",
    "'; DROP TABLE labs;--", "¿Potasio?", "potassium " * 40, "",
    "   ", "???", "Tell me about the patient", "WBC trend",
    "List the medications and the labs",
    "Any blood thinner?", "Was an antibacterial given?",
    "What is she taking for synthetic concept 1001?",
    "Which synthetic concept 1001 medications was she given?",
    "Was heparin given for synthetic concept 1001?",
    "What was given for pain?", "Was a chest x-ray done for pain?",
)


def ask(question, scope):
    """Return the Result of a question about the invented case, or None
    when it is not a question about the record."""
    return answer.record_result(question, cases.CASE, scope, cases.Visits(),
                                VOCAB, "q-1", CONTEXT)


def refs_cited(response):
    """Return every reference a response's claims cite."""
    return {ref for claim in response.claims for ref in claim.refs}


def notices_of(result):
    """Return the notices of a result's candidate, in order."""
    return [statement.notice for statement in result.candidate.statements
            if statement.kind == "notice"]


def kinds_of(result):
    """Return the statement kinds of a result's candidate, in order."""
    return [statement.kind for statement in result.candidate.statements]


VOCAB = suite.synthetic_vocabulary()
CONTEXT = cases.context()


class SweepTests(unittest.TestCase):
    """Every question, in every scope, ends in an outcome of its own."""

    def test_no_question_is_withheld_or_fails_in_any_scope(self):
        """No validation rejection, operational error or exception; every
        answer cites only its scope's records, visits and the person."""
        outcomes = set()
        for scope in cases.SCOPES:
            bundle, _ = CONTEXT.records.bundle(1, scope)
            refs = record.refs_of(bundle)
            for question in QUESTIONS:
                with self.subTest(question=question, scope=scope):
                    found = ask(question, scope)
                    if found is None:
                        outcomes.add("none")
                        continue
                    response = found.response
                    outcomes.add(response.outcome)
                    self.assertNotIn(response.outcome, (
                        "validation_rejection", "operational_error"))
                    if response.outcome in ("answer", "not_recorded"):
                        self.assertTrue(response.text)
                        self.assertTrue(refs_cited(response) <= refs)
                        self.assertEqual(response.bundle.bundle_id,
                                         bundle.bundle_id)
        self.assertTrue({"answer", "not_recorded", "clarification",
                         "none"} <= outcomes)

    def test_answers_are_deterministic(self):
        """The same question gets the same response every time."""
        for question in ("What were all the potassium values?",
                         "Any antibiotics?", "What labs were done?"):
            first = ask(question, cases.ALL).response
            again = ask(question, cases.ALL).response
            self.assertEqual((first.text, first.claims),
                             (again.text, again.claims), question)


class ValueTests(unittest.TestCase):
    """Values: selectors, ties, limits, text values and removed values."""

    @classmethod
    def setUpClass(cls):
        """Hold the invented bundles by scope."""
        cls.bundles = {scope: CONTEXT.records.bundle(1, scope)[0]
                       for scope in cases.SCOPES}

    def native(self, scope, ref):
        """Return the native id of a record of a scope."""
        return next(entry.native_id for entry in self.bundles[scope].records
                    if entry.ref == ref)

    def value_statements(self, result):
        """Return the value statements of a result."""
        return [statement for statement in result.candidate.statements
                if statement.kind in ("record_value", "record_value_item")]

    def test_a_long_series_lists_the_latest_and_counts_the_rest(self):
        """37 potassium results: the latest 30 in time order, 7 counted."""
        found = ask("What were all the potassium values?", cases.ALL)
        lines = self.value_statements(found)
        self.assertEqual(len(lines), 30)
        omitted = [statement for statement in found.candidate.statements
                   if statement.notice == "values_omitted"]
        self.assertEqual(omitted[0].slots.earlier, 7)
        starts = [next(entry.start for entry in self.bundles[cases.ALL]
                       .records if entry.ref == line.refs[0])
                  for line in lines]
        self.assertEqual(starts, sorted(starts))
        self.assertIn("Earlier records are not listed here",
                      found.response.text)
        history = ask("What were all the potassium values?", cases.HISTORY)
        self.assertEqual(len(self.value_statements(history)), 22)
        self.assertNotIn("values_omitted", notices_of(history))

    def test_an_item_is_named_by_its_earliest_record(self):
        """The second visit's results come before the first visit's in
        time: the group is named by the earliest, whatever the order of
        the visits."""
        found = ask("What were all the potassium values?", cases.HISTORY)
        header = next(statement for statement in found.candidate.statements
                      if statement.kind == "record_value_group")
        self.assertEqual(self.native(cases.HISTORY, header.slots.item.record),
                         1100)

    def test_ties_are_broken_by_time_then_row(self):
        """Two results at the same time: the later row is the latest; two
        equal highest values: the later one."""
        latest = ask("What was the last potassium?", cases.HISTORY)
        value = self.value_statements(latest)[0]
        self.assertEqual(self.native(cases.HISTORY, value.refs[0]), 1019)
        highest = ask("What was the highest potassium?", cases.ALL)
        value = self.value_statements(highest)[0]
        self.assertEqual(self.native(cases.ALL, value.refs[0]), 1214)
        troponin = ask("What was the highest troponin?", cases.ALL)
        value = self.value_statements(troponin)[0]
        self.assertEqual(self.native(cases.ALL, value.refs[0]), 4001)

    def test_highest_of_text_values_is_the_latest(self):
        """No glucose value is a number: the latest is given, as such."""
        found = ask("What was the highest glucose?", cases.ALL)
        value = self.value_statements(found)[0]
        self.assertEqual(value.slots.selector, "latest")
        self.assertIn("Latest Glucose", found.response.text)
        self.assertIn("HOLD", found.response.text)

    def test_a_single_record_is_the_only_one(self):
        """One creatinine: its value is the only one."""
        found = ask("What was the last creatinine?", cases.ALL)
        self.assertEqual(self.value_statements(found)[0].slots.selector,
                         "only")

    def test_a_removed_value_is_shown_without_a_unit(self):
        """A value MIMIC-IV removed is said so, with no unit after it."""
        found = ask("What were all the potassium values?", cases.ALL)
        self.assertIn("value removed by de-identification (reference range",
                      found.response.text)
        self.assertNotIn("de-identification mEq/L", found.response.text)

    def test_microbiology_says_whether_an_organism_is_recorded(self):
        """What grew says first that an organism is recorded, then each
        test's cultures: each organism once with its antibiotic results,
        and a culture that grew nothing."""
        found = ask("What organisms grew?", cases.ALL)
        self.assertEqual(found.response.text, "\n".join((
            "An organism is recorded in the results of microbiology tests "
            "within all case visits, including the final admission.",
            "Blood Culture, Routine (BLOOD CULTURE):",
            "- No organism recorded, 10 Jan 2150 10:00, in visit 1",
            "URINE CULTURE (URINE):",
            "- ESCHERICHIA COLI, 2 Jun 2151 08:00, in visit 3: resistant to "
            "AMPICILLIN; intermediate to NITROFURANTOIN; sensitive to "
            "CEFAZOLIN")))
        self.assertNotIn("values_omitted", notices_of(found))
        bundle, _ = CONTEXT.records.bundle(1, cases.ALL)
        micro = {entry.ref for entry in bundle.records
                 if entry.kind == "microbiology"}
        self.assertEqual(refs_cited(found.response), micro)

    def test_a_culture_that_grew_nothing(self):
        """Positive and negative ask the same: no organism is recorded."""
        for question in ("Was the blood culture positive?",
                         "Was the blood culture negative?"):
            found = ask(question, VISIT[1])
            self.assertEqual(found.response.outcome, "answer")
            self.assertEqual(found.response.text.splitlines()[0],
                             "No organism is recorded in the results of "
                             "BLOOD CULTURE within visit 1.")

    def test_the_latest_or_first_culture(self):
        """The latest or the first asks about one culture of each test,
        the only one when the test has one."""
        found = ask("Was the latest urine culture positive?", cases.ALL)
        self.assertEqual(found.response.text.splitlines()[:2], [
            "An organism is recorded in the results of the latest URINE "
            "CULTURE within all case visits, including the final admission.",
            "Only URINE CULTURE (URINE):"])
        found = ask("Was the first blood culture positive?", cases.ALL)
        self.assertEqual(kinds_of(found), [
            "record_growth_none_selected", "record_growth_selected_group",
            "record_growth_negative"])

    def test_a_culture_asked_about_by_its_organism(self):
        """An organism named where a test would be asks whether an
        organism is recorded in its own results, by culture."""
        found = ask("Did any culture grow e coli?", cases.ALL)
        self.assertEqual(found.response.text, "\n".join((
            "An organism is recorded in the results of ESCHERICHIA COLI "
            "within all case visits, including the final admission.",
            "URINE CULTURE (URINE):",
            "- ESCHERICHIA COLI, 2 Jun 2151 08:00, in visit 3: resistant to "
            "AMPICILLIN; intermediate to NITROFURANTOIN; sensitive to "
            "CEFAZOLIN")))

    def test_a_culture_outside_the_scope_is_not_recorded(self):
        """A named test the scope lacks is not recorded, as before."""
        found = ask("Was the urine culture positive?", cases.HISTORY)
        self.assertEqual(found.response.outcome, "not_recorded")
        self.assertEqual(kinds_of(found), ["record_absent", "notice"])

    def test_doses_of_the_medications(self):
        """A dose question gives each medication's latest dose."""
        found = ask("What doses were given?", cases.ALL)
        self.assertEqual(found.response.task, "record_values")
        self.assertIn("40 mg, route PO", found.response.text)
        self.assertIn("dose not linked", ask(
            "What doses were given?", cases.HISTORY).response.text)


class DirectAnswerTests(unittest.TestCase):
    """Questions that ask yes or no, answered directly
    (record-questions-v8, record-answer-v6)."""

    def test_a_selected_result_says_whether_it_is_flagged(self):
        """The latest or first result asked about by its flag says first
        whether the laboratory flagged it."""
        found = ask("Was the latest potassium high?", cases.ALL)
        self.assertEqual(found.response.text.splitlines()[0],
                         "An abnormal flag is recorded for the latest result "
                         "of Potassium (Blood, Chemistry) within all case "
                         "visits, including the final admission:")
        found = ask("Was the first potassium normal?", cases.ALL)
        self.assertEqual(found.response.text.splitlines()[:2], [
            "No abnormal flag is recorded for the first result of "
            "Potassium (Blood, Chemistry) within all case visits, including "
            "the final admission:",
            "- 4.0 mEq/L (reference range 3.3 to 5.1), 5 Jan 2150 00:00, in "
            "visit 2"])
        self.assertIn("not_flagged", notices_of(found))

    def test_how_a_result_reads_asks_for_its_flag(self):
        """Positive, negative or normal asks for the flagged results."""
        found = ask("Was the troponin positive?", cases.ALL)
        self.assertEqual((found.response.task, found.response.outcome),
                         ("record_abnormal", "not_recorded"))
        self.assertTrue(found.response.text.startswith(
            "No result of Troponin T within all case visits"))

    def test_surgery_is_not_marked(self):
        """Procedures asked about as surgery say the record does not mark
        it; procedures asked about as such do not."""
        for question in ("Did she have surgery?", "How many operations?"):
            self.assertIn("surgery_unmarked",
                          notices_of(ask(question, cases.ALL)))
        self.assertNotIn("surgery_unmarked", notices_of(
            ask("Which procedures were done?", cases.ALL)))

    def test_visits_and_the_person_in_other_words(self):
        """Where she came from, her age and her death, asked otherwise."""
        lines = ask("Was she admitted through the ED?",
                    cases.ALL).response.text.splitlines()
        self.assertIn("- visit 1: admitted from EMERGENCY ROOM", lines)
        lines = ask("Is she over 65?", cases.ALL).response.text.splitlines()
        self.assertEqual(lines[:3], ["- At visit 1: age 89",
                                     "- At visit 2: age 89",
                                     "- At visit 3: age over 89"])
        lines = ask("Was she alive at discharge?",
                    cases.ALL).response.text.splitlines()
        self.assertIn("- visit 3: died in hospital, 4 Jun 2151 09:00", lines)

    def test_the_prediction_has_its_own_workflow(self):
        """A question about the prediction is pointed to its workflow."""
        found = ask("Is the predicted diagnosis recorded?", cases.ALL)
        self.assertEqual(found.response.reason_codes,
                         ("prediction_selection_needed",))


class MedicationTests(unittest.TestCase):
    """Medications without a standard concept, classes, brands and salts."""

    def matches(self, question, scope=cases.ALL):
        """Return {native id: how} of the records a presence answer
        cites."""
        found = ask(question, scope)
        if found.response.outcome != "answer":
            return {}
        bundle, _ = CONTEXT.records.bundle(1, scope)
        natives = {entry.ref: entry.native_id for entry in bundle.records}
        return {natives[ref]: statement.slots.how
                for statement in found.candidate.statements
                if statement.kind == "record_presence_item"
                for ref in statement.refs}

    def test_classes_find_written_names_by_their_ingredients(self):
        """A class question finds medications without a standard concept
        whose written name holds an ingredient of the class."""
        self.assertEqual(self.matches("Any antibiotics?"), {8202: "name"})
        self.assertEqual(self.matches("Any opioids?"), {8205: "name"})
        self.assertEqual(self.matches("Any diuretics?"), {8201: "class"})

    def test_a_class_word_in_the_singular_finds_what_its_plural_finds(self):
        """"An antibacterial" finds what "antibiotics" finds, and "a blood
        thinner" what "blood thinners" finds."""
        self.assertEqual(self.matches("Was an antibacterial given?"),
                         self.matches("Any antibiotics?"))
        self.assertEqual(self.matches("Any blood thinner?"),
                         self.matches("Any blood thinners?"))
        self.assertTrue(self.matches("Any blood thinner?"))

    def test_the_phrase_itself_is_matched_in_written_names(self):
        """"Insulin" names the class, and is written as such."""
        self.assertEqual(self.matches("Was insulin given?"), {8203: "name"})

    def test_whole_words_and_salts(self):
        """Hydroxyurea is not urea; heparin sodium is heparin, not
        sodium."""
        self.assertEqual(ask("Was urea given?", cases.ALL).response.outcome,
                         "not_recorded")
        self.assertEqual(ask("Was sodium given?",
                             cases.ALL).response.outcome, "not_recorded")
        self.assertEqual(self.matches("Was heparin given?"),
                         {8000: "ingredient", 8001: "ingredient",
                          8101: "name", 8200: "ingredient"})

    def test_brand_names_and_ingredients(self):
        """A brand name is matched by its ingredient and says so."""
        self.assertEqual(self.matches("Was she on Lasix?"), {8201: "brand"})
        self.assertEqual(self.matches("Was furosemide given?"),
                         {8201: "ingredient"})

    def test_not_recorded_states_the_records_without_a_concept(self):
        """A "not recorded" about a named medication says how many records
        without a standard concept were checked, and how."""
        found = ask("Any antibiotics?", cases.HISTORY)
        self.assertEqual(found.response.outcome, "not_recorded")
        unmapped = [statement for statement in found.candidate.statements
                    if statement.kind == "record_unmapped"]
        self.assertEqual((unmapped[0].slots.unmapped,
                          unmapped[0].slots.kind), (2, "medication"))
        self.assertIn("2 medication records in this scope have no standard "
                      "concept; the drug names written in them were checked",
                      found.response.text)
        self.assertEqual(notices_of(found), ["absence"])
        found = ask("Did she have a CT?", cases.ALL)
        self.assertIn("1 procedure record in this scope has no standard "
                      "concept", found.response.text)
        # Without a record lacking a concept, nothing is stated.
        found = ask("Was a CT done?", VISIT[3])
        self.assertNotIn("record_unmapped", kinds_of(found))

    def test_a_kind_named_without_an_item_states_no_unmapped_count(self):
        """A list names records without a concept by their written names."""
        found = ask("What medications were given?", cases.ALL)
        self.assertNotIn("record_unmapped", kinds_of(found))
        self.assertIn("medication whose name is not linked to its "
                      "prescription", found.response.text)


class ReadingTests(unittest.TestCase):
    """Negations, two items, flags, misspellings and words of time."""

    def outcome(self, question, scope=cases.ALL):
        """Return (outcome, notice) of a question's response."""
        found = ask(question, scope)
        notices = notices_of(found)
        return found.response.outcome, notices[0] if notices else None

    def test_a_negation_asks_the_same(self):
        """A negated presence question gets the same answer."""
        plain = ask("Was heparin given?", cases.ALL).response
        for question in ("Was heparin not given?",
                         "Did she never get heparin?"):
            negated = ask(question, cases.ALL).response
            self.assertEqual((negated.outcome, negated.claims),
                             (plain.outcome, plain.claims), question)
        self.assertEqual(self.outcome("What labs were not done?"),
                         ("clarification", "qualified"))

    def test_two_items_ask_one_at_a_time(self):
        """Two items that each resolve are asked about one at a time."""
        for question in ("Was heparin or furosemide given?",
                         "Was she on insulin and heparin?",
                         "Were the potassium and sodium checked?"):
            self.assertEqual(self.outcome(question),
                             ("clarification", "two_items"), question)

    def test_flags_follow_the_kind(self):
        """Laboratory results carry a flag, vital signs do not; other
        kinds carry none."""
        self.assertEqual(ask("Were any labs high?",
                             cases.ALL).response.task, "record_abnormal")
        found = ask("Were any vitals high?", cases.ALL)
        self.assertEqual({statement.slots.selector
                          for statement in found.candidate.statements
                          if statement.kind == "record_value"},
                         {"highest", "only"})
        self.assertIn("vital_no_flag", notices_of(found))
        found = ask("Was the heart rate low?", cases.ALL)
        self.assertIn("Lowest Heart Rate: 88 bpm", found.response.text)
        for question in ("Were any vitals abnormal?",
                         "Was the heart rate abnormal?",
                         "Was the chest x-ray abnormal?",
                         "Any abnormal medications?"):
            self.assertEqual(self.outcome(question),
                             ("clarification", "flag_unrecorded"), question)

    def test_misspellings_are_suggested(self):
        """A misspelt item is never guessed: its spelling is suggested."""
        for question, name in (("Was heprin given?", "heparin"),
                               ("Was an apendectomy done?", "appendectomy"),
                               ("Was the potasium checked?", "Potassium")):
            found = ask(question, cases.ALL)
            self.assertEqual(notices_of(found), ["item_suggested"], question)
            self.assertIn(name, found.candidate.statements[0].slots.labels)

    def test_times_and_places_that_are_not_scopes(self):
        """Days, times and the ICU are not scopes; other visits are asked
        about in their scope."""
        for question in ("What was the potassium on day 2?",
                         "Did she get vancomycin in the ICU?",
                         "Was heparin given yesterday?"):
            self.assertEqual(self.outcome(question),
                             ("clarification", "scope_unrecognised"),
                             question)
        self.assertEqual(self.outcome("Was potassium measured in visit 4?"),
                         ("clarification", "visit_missing"))
        self.assertEqual(self.outcome("Was potassium measured in visit 2?"),
                         ("clarification", "scope_other"))
        found = ask("Was potassium measured in visit 2?", VISIT[2])
        self.assertEqual(found.response.outcome, "answer")

    def test_what_a_medication_was_given_for_is_not_recorded(self):
        """Medications asked for by what they were given for get the
        clarification that the record does not say, naming what was
        asked; a question about another kind's item is not one."""
        for question, term in (
                ("What is she taking for synthetic concept 1001?",
                 "synthetic concept 1001"),
                ("Which synthetic concept 1001 medications was she given?",
                 "synthetic concept 1001"),
                ("Was heparin given for synthetic concept 1001?",
                 "synthetic concept 1001"),
                ("What was given for pain?", "pain")):
            for scope in cases.SCOPES:
                found = ask(question, scope)
                self.assertEqual(
                    (found.response.outcome, found.response.reason_codes,
                     notices_of(found)),
                    ("clarification", ("task_ambiguous",),
                     ["indication_unrecorded"]), question)
                self.assertIn('which were given for "%s"' % term,
                              found.response.text)
        self.assertNotEqual(self.outcome("Was a chest x-ray done for pain?")[1],
                            "indication_unrecorded")

    def test_markup_asks_which_item_is_meant(self):
        """Markup in a question is read as words, which name nothing: the
        answer asks, and echoes the words as plain text."""
        found = ask("<script>alert(1)</script> labs", cases.ALL)
        self.assertEqual(found.response.outcome, "clarification")
        self.assertNotIn("<", found.response.text)
        self.assertIn('"script alert 1 script"', found.response.text)

    def test_advice_and_the_future_are_not_read(self):
        """A request for advice, or about the future, is not a question
        about the record."""
        for question in ("Should she get heparin?", "Will she survive?",
                         "Is she likely to need dialysis?"):
            self.assertIsNone(ask(question, cases.ALL), question)


class VisitAndPersonTests(unittest.TestCase):
    """Visits and the person at their edges."""

    def text(self, question, scope=cases.ALL):
        """Return a question's released text."""
        return ask(question, scope).response.text

    def test_lengths_of_stay(self):
        """A discharge recorded before its admission gives no length; an
        admission without times is measured by its visit's dates."""
        text = self.text("How long was she in hospital?")
        self.assertIn("visit 1: length of stay 5.2 days", text)
        self.assertIn("visit 2: length of stay 2 days", text)
        self.assertIn("visit 3: length of stay not given: the discharge is "
                      "recorded before the admission", text)
        summary = self.text("How many visits?")
        self.assertNotIn("-0.1", summary)

    def test_ages_beyond_89_are_a_lower_bound(self):
        """89 is given; 90 and above are "over 89"."""
        text = self.text("How old was the patient?")
        self.assertIn("At visit 1: age 89", text)
        self.assertIn("At visit 3: age over 89", text)

    def test_death(self):
        """The date of death and each visit's death in hospital."""
        text = self.text("Did the patient die?")
        self.assertIn("date of death 4 Jun 2151", text)
        self.assertIn("visit 3: died in hospital, 4 Jun 2151 09:00", text)
        self.assertIn("visit 1: no death in hospital recorded", text)

    def test_stays_and_transfers(self):
        """An open transfer, and ICU stays with their lengths."""
        self.assertIn("1 Jun 2151 10:00 to an open end, admit",
                      self.text("Which units was the patient in?"))
        text = self.text("Was she in the ICU?")
        self.assertIn("(1.5 days)", text)
        self.assertIn("(1 day)", text)

    def test_details_recorded_or_not(self):
        """A detail an admission lacks is not recorded."""
        text = self.text("What insurance does she have?")
        self.assertIn("visit 1: insurance Medicare", text)
        self.assertIn("visit 2: insurance not recorded", text)
        self.assertIn("sex female", self.text("Is she male or female?"))


class WordingTests(unittest.TestCase):
    """The templates' own wordings keep the wording rules."""

    def test_every_template_wording_passes(self):
        """Each wording of each statement kind, in each outcome it may
        end, breaks no wording rule."""
        for name, entry in record_answer.WORDINGS.items():
            wordings = []
            for item in (entry.values() if isinstance(entry, dict)
                         else (entry,)):
                wordings += [item] if isinstance(item, str) else list(item)
            for wording in wordings:
                for outcome in candidates.KINDS[name].outcomes:
                    self.assertEqual(language.findings(wording, outcome,
                                                       name), [],
                                     (name, wording))

    def test_every_notice_passes(self):
        """Every approved notice passes the wording rules."""
        for notice_id, notice in candidates.NOTICES.items():
            for outcome in notice.outcomes:
                self.assertEqual(language.findings(notice.text, outcome,
                                                   "notice"), [], notice_id)


if __name__ == "__main__":
    unittest.main()
