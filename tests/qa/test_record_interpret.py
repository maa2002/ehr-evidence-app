"""Synthetic checks for ehr_evidence_app.qa.record_interpret.

Every dictionary here is invented: generic test, vital-sign, drug,
organism and unit names, as a dictionary would hold them; no patient
record is read.
"""

import unittest

from ehr_evidence_app.qa import interpret as conditions
from ehr_evidence_app.qa import record_interpret as reader
from ehr_evidence_app.qa import record_resolve as resolving
from ehr_evidence_app.qa import record_vocabulary, resolve
from ehr_evidence_app.qa import vocabulary as build


def drug_vocabulary():
    """Return an invented medication vocabulary."""
    concepts = {
        1: ("ingredient", "Ingredient", "heparin"),
        2: ("ingredient", "Ingredient", "furosemide"),
        3: ("brand", "Brand Name", "Lasix"),
        4: ("atc", "ATC 2nd", "ANTIBACTERIALS FOR SYSTEMIC USE"),
        5: ("ingredient", "Ingredient", "vancomycin"),
        6: ("ingredient", "Ingredient", "potassium chloride"),
    }
    drugs = {concept_id: record_vocabulary.DrugConcept(
        concept_id, kind, "RxNorm", klass,
        "J01" if concept_id == 4 else "C%d" % concept_id, name)
        for concept_id, (kind, klass, name) in concepts.items()}
    names = {}
    for concept in drugs.values():
        names.setdefault(resolving.normalize(concept.name), set()).add(
            concept.concept_id)
    return record_vocabulary.RecordVocabulary(
        drugs=drugs, drug_names=names, brands={3: {2}},
        ancestors={101: {1}, 102: {2}, 105: {5, 4}},
        procedure_names=("insertion of endotracheal airway into trachea",
                         "radiography of chest", "appendectomy"))


def condition_vocabulary():
    """Return an invented condition vocabulary: a seizure, a thrombosis
    also known by its abbreviation, pain, which is a vital sign's name too,
    and a reaction its synonym calls by a drug class's word."""
    names = {2001: "Seizure", 2002: "Deep venous thrombosis", 2003: "Pain",
             2004: "Reaction to an antibacterial"}
    concepts = [(concept, "standard", "SNOMED", "Disorder", "S%d" % concept,
                 "", name) for concept, name in sorted(names.items())]
    return resolve.parse({
        build.FILES["concepts"]: build.encode_tsv("concepts", concepts),
        build.FILES["synonyms"]: build.encode_tsv(
            "synonyms", [(2002, "DVT"), (2004, "Antibiotics")]),
        build.FILES["ancestors"]: build.encode_tsv(
            "ancestors", [(concept, concept, 0) for concept in names]),
        build.FILES["icd_maps"]: build.encode_tsv("icd_maps", [])})


def terms(condition_vocabulary=None):
    """Return Terms over invented dictionaries."""
    drugs = drug_vocabulary()
    return resolving.Terms(
        labs=[("1", "Potassium", "Blood", "Chemistry"),
              ("2", "Creatinine", "Blood", "Chemistry"),
              ("3", "Hemoglobin", "Blood", "Hematology"),
              ("4", "White Blood Cells", "Blood", "Hematology"),
              ("5", "Glucose", "Blood", "Chemistry"),
              ("6", "Troponin T", "Blood", "Chemistry")],
        items=[("220045", "Heart Rate"),
               ("220179", "Non Invasive Blood Pressure systolic")],
        micro=["BLOOD CULTURE", "ESCHERICHIA COLI", "URINE",
               "VANCOMYCIN"],
        units=["Medical Intensive Care Unit (MICU)",
               "Emergency Department", "Medicine"],
        drugs=drugs, procedures=drugs.procedure_names,
        condition_vocabulary=condition_vocabulary)


class ReadingTests(unittest.TestCase):
    """Questions and the readings they get."""

    @classmethod
    def setUpClass(cls):
        """Build the invented terms once."""
        cls.terms = terms()

    def read(self, question):
        """Return the reading of a question."""
        return reader.interpret(question, self.terms)

    def assertReads(self, question, **expected):
        """Assert the fields of a question's reading."""
        found = self.read(question)
        for name, value in expected.items():
            self.assertEqual(getattr(found, name), value,
                             "%s: %s" % (question, name))

    def test_lists_of_a_kind(self):
        """A kind named without an item asks for its list."""
        for question, kind in (
                ("What medications were given?", "medication"),
                ("Show me the labs", "lab"),
                ("List the procedures", "procedure"),
                ("What vital signs were charted?", "vital"),
                ("Which units was she in?", "transfer")):
            self.assertReads(question, status="task", task="record_list",
                             kind=kind)

    def test_presence_of_an_item_by_its_kind_or_its_name(self):
        """A named item is resolved in the kind its words name, or in the
        one kind that holds its name."""
        self.assertReads("Was heparin given?", task="record_presence",
                         kind="medication", phrase="heparin")
        self.assertReads("Was she on Lasix?", task="record_presence",
                         kind="medication", phrase="lasix")
        self.assertReads("Any antibiotics?", task="record_presence",
                         kind="medication", phrase="antibiotics")
        self.assertReads("Was a chest x-ray done?", task="record_presence",
                         kind="procedure", phrase="chest x ray")
        self.assertReads("Was a blood culture taken?",
                         task="record_presence", kind="microbiology")
        self.assertReads("Was troponin checked?", task="record_presence",
                         kind="lab", phrase="troponin")

    def test_values_with_their_selectors(self):
        """Values questions take their selector; a bare result question
        asks for the latest."""
        for question, selector in (
                ("What was the last potassium?", "latest"),
                ("What was her first creatinine?", "first"),
                ("Highest glucose?", "highest"),
                ("What was the lowest hemoglobin value?", "lowest"),
                ("Show the potassium trend", "all"),
                ("What were all the potassium values?", "all"),
                ("What was the potassium?", "latest")):
            self.assertReads(question, task="record_values", kind="lab",
                             selector=selector)

    def test_counts_times_and_flags(self):
        """Frames give counts, times and flagged results."""
        self.assertReads("How many potassium results are there?",
                         task="record_count", kind="lab")
        self.assertReads("How many medications?", task="record_count",
                         kind="medication", phrase=None)
        self.assertReads("When was heparin first given?",
                         task="record_timing", kind="medication")
        self.assertReads("Were any labs abnormal?", task="record_abnormal",
                         kind="lab")
        self.assertReads("Was the potassium high?", task="record_abnormal",
                         kind="lab")

    def test_vital_signs_have_no_flag(self):
        """A vital sign asked about as high or low gives its highest or
        lowest value."""
        self.assertReads("Was the heart rate high?", task="record_values",
                         kind="vital", selector="highest",
                         category="vital_flag")
        self.assertReads("Was the blood pressure low?",
                         task="record_values", kind="vital",
                         selector="lowest")

    def test_microbiology_growth_lists_results(self):
        """What grew asks for the culture results, organisms included."""
        self.assertReads("What organisms grew?", task="record_values",
                         kind="microbiology", selector="all")
        self.assertReads("Did the blood culture grow anything?",
                         task="record_values", kind="microbiology")

    def test_doses(self):
        """A dose question asks for the values of a medication."""
        self.assertReads("What dose of heparin was given?",
                         task="record_values", kind="medication")

    def test_visits_and_the_person(self):
        """Visits' and the person's details are read as fields."""
        for question, task, field in (
                ("How many visits?", "visit_details", "count"),
                ("When was she admitted?", "visit_details", "admitted"),
                ("When was the patient discharged?", "visit_details",
                 "discharged"),
                ("How long was she in hospital?", "visit_details",
                 "length"),
                ("Where was she discharged to?", "visit_details",
                 "discharged_to"),
                ("What type of admission was it?", "visit_details",
                 "type"),
                ("What is the patient's sex?", "person_details", "sex"),
                ("How old is the patient?", "person_details", "age"),
                ("What is her race?", "person_details", "race"),
                ("What language does she speak?", "person_details",
                 "language"),
                ("Is the patient married?", "person_details",
                 "marital_status"),
                ("Did the patient die?", "person_details", "death")):
            self.assertReads(question, status="task", task=task,
                             field=field)

    def test_icu_stays(self):
        """ICU questions ask for the ICU stays."""
        self.assertReads("Was she in the ICU?", task="record_list",
                         kind="icu_stay")
        self.assertReads("How many ICU stays?", task="record_count",
                         kind="icu_stay")

    def test_fail_safes(self):
        """Filters, time words, pronouns and two kinds ask again; advice
        and the future are not read at all."""
        self.assertReads("Was potassium above 5?", status="clarify",
                         category="qualified")
        self.assertReads("What labs except potassium?", status="clarify",
                         category="qualified")
        self.assertReads("Was heparin given yesterday?", status="clarify",
                         category="scope_unrecognised")
        self.assertReads("Was it given?", status="clarify",
                         category="missing_item")
        self.assertReads("List the medications and the labs",
                         status="clarify", category="two_tasks")
        for question in ("Should she get heparin?",
                         "Will she survive?",
                         "Is she likely to need dialysis?"):
            self.assertReads(question, status="none")

    def test_an_unknown_item_of_a_named_kind_asks(self):
        """An item a named kind does not hold gets a clarification."""
        self.assertReads("Was florbnazine given?", status="clarify",
                         category="unknown_item")

    def test_a_name_no_kind_holds_is_not_read(self):
        """A phrase no kind resolves is left to the condition rules."""
        self.assertReads("florbnitis", status="none")

    def test_scope_phrases_are_set_aside(self):
        """A scope phrase is kept for binding, not read as the item."""
        found = self.read("Was heparin given in visit 2?")
        self.assertEqual((found.task, found.phrase), ("record_presence",
                                                      "heparin"))
        self.assertEqual(found.mentions[0].visit_number, 2)

    def test_condition_questions_are_left_to_the_condition_rules(self):
        """The condition rules read condition questions as before; the
        record reading only takes what they refuse."""
        for question in ("Is heart failure recorded?",
                         "Which conditions are recorded?"):
            self.assertEqual(conditions.interpret(question).status, "task")


class EdgeReadingTests(unittest.TestCase):
    """How questions at the edges of the rules are read (rules
    record-questions-v2), over the invented edge record's terms."""

    # (question, the reading's expected fields)
    TABLE = (
        # Noise, casing, accents and symbols.
        ("", {"status": "none"}),
        ("   ", {"status": "none"}),
        ("???", {"status": "none"}),
        ("WHAT MEDICATIONS WERE GIVEN???", {"task": "record_list",
                                            "kind": "medication"}),
        ("  labs.  ", {"task": "record_list", "kind": "lab"}),
        ("Was héparin given?", {"task": "record_presence",
                                "kind": "medication", "phrase": "heparin"}),
        # Markup is read as words; the phrase it leaves does not resolve,
        # so the answer asks which test is meant.
        ("<b>labs</b>", {"task": "record_presence", "kind": "lab",
                         "phrase": "b b"}),
        ("<script>alert(1)</script> labs", {
            "kind": "lab", "phrase": "script alert 1 script"}),
        # Abbreviations and everyday words.
        ("K?", {"task": "record_presence", "kind": "lab"}),
        ("Hgb", {"task": "record_presence", "kind": "lab"}),
        ("BP?", {"task": "record_presence", "kind": "vital"}),
        ("o2 sat", {"task": "record_presence", "kind": "vital"}),
        ("temp", {"task": "record_presence", "kind": "vital"}),
        ("cxr", {"task": "record_presence", "kind": "procedure"}),
        ("Was she intubated?", {"task": "record_presence",
                                "kind": "procedure"}),
        ("Any E. coli?", {"task": "record_presence",
                          "kind": "microbiology"}),
        ("Was she in the MICU?", {"task": "record_presence",
                                  "kind": "transfer"}),
        # Selectors.
        ("most recent potassium", {"task": "record_values",
                                   "selector": "latest"}),
        ("baseline creatinine", {"task": "record_values",
                                 "selector": "first"}),
        ("peak troponin", {"task": "record_values", "selector": "highest"}),
        ("nadir hemoglobin", {"task": "record_values",
                              "selector": "lowest"}),
        ("potassium over time", {"task": "record_values",
                                 "selector": "all"}),
        # Counts and times.
        ("How often was heparin given?", {"task": "record_count",
                                          "kind": "medication"}),
        ("number of potassium results", {"task": "record_count",
                                         "kind": "lab"}),
        ("What date was heparin given?", {"task": "record_timing"}),
        ("At what time was potassium checked?", {"task": "record_timing"}),
        # Doses.
        ("How much heparin was given?", {"task": "record_values",
                                         "kind": "medication",
                                         "selector": "all"}),
        ("What doses were given?", {"task": "record_values",
                                    "kind": "medication"}),
        # Flags.
        ("Were any labs high?", {"task": "record_abnormal", "kind": "lab"}),
        ("Was the hemoglobin low?", {"task": "record_abnormal"}),
        ("Was the heart rate abnormally high?", {
            "task": "record_values", "selector": "highest",
            "category": "vital_flag"}),
        ("Were any vitals low?", {"task": "record_values",
                                  "selector": "lowest",
                                  "category": "vital_flag"}),
        ("Were any vitals abnormal?", {"status": "clarify",
                                       "category": "flag_unrecorded"}),
        ("Was the chest x-ray abnormal?", {"category": "flag_unrecorded"}),
        ("Any abnormal medications?", {"category": "flag_unrecorded"}),
        # Microbiology results.
        ("Did the urine culture grow anything?", {
            "task": "record_values", "kind": "microbiology",
            "selector": "all"}),
        ("What were the microbiology results?", {
            "task": "record_values", "kind": "microbiology",
            "selector": "all"}),
        # ICU stays, visits and the person.
        ("Was she in intensive care?", {"task": "record_list",
                                        "kind": "icu_stay"}),
        ("Was she never in the ICU?", {"task": "record_list",
                                       "kind": "icu_stay"}),
        ("How many ICU admissions?", {"task": "record_count",
                                      "kind": "icu_stay"}),
        ("Did she get heparin in the ICU?", {
            "category": "scope_unrecognised"}),
        ("How many admissions?", {"task": "visit_details",
                                  "field": "count"}),
        ("Where was she admitted from?", {"field": "admitted_from"}),
        ("Was it an elective admission?", {"field": "type"}),
        ("How old?", {"task": "person_details", "field": "age"}),
        ("What gender?", {"field": "sex"}),
        ("Is she male or female?", {"field": "sex"}),
        ("Was the patient a man or a woman?", {"field": "sex"}),
        ("Does she need an interpreter?", {"field": "language"}),
        ("Did the patient not die?", {"field": "death"}),
        # Negations.
        ("Was heparin not given?", {"task": "record_presence",
                                    "phrase": "heparin"}),
        ("Was there no heparin?", {"task": "record_presence"}),
        ("Which labs were not done?", {"category": "qualified"}),
        ("Were no medications given?", {"category": "qualified"}),
        # Filters, times, pronouns, two items and two kinds.
        ("only potassium", {"category": "qualified"}),
        ("besides heparin, what was given?", {"category": "qualified"}),
        ("potassium last week", {"category": "scope_unrecognised"}),
        ("potassium this morning", {"category": "scope_unrecognised"}),
        ("What was the potassium on day 2?", {
            "category": "scope_unrecognised"}),
        ("Was potassium checked 3 days ago?", {
            "category": "scope_unrecognised"}),
        ("potassium in the first 24 hours", {
            "category": "scope_unrecognised"}),
        ("Was she ventilated for 96 hours?", {
            "category": "scope_unrecognised"}),
        ("Was it measured?", {"category": "missing_item"}),
        ("Was heparin or furosemide given?", {"category": "two_items"}),
        ("Were potassium and sodium checked?", {"category": "two_items"}),
        ("Show the labs and medications", {"category": "two_tasks"}),
        # Unknown and misspelt items, with suggestions of the kind the
        # question's verb points to.
        ("Was florbnazine given?", {"category": "unknown_item"}),
        ("Was heprin given?", {"category": "unknown_item",
                               "suggestions": ("heparin",
                                               "heparin group")}),
        ("Was an apendectomy done?", {"suggestions": ("appendectomy",)}),
        # Advice and the future are not questions about the record.
        ("Should heparin be stopped?", {"status": "none"}),
        ("What is the prognosis?", {"status": "none"}),
        ("Would she benefit from dialysis?", {"status": "none"}),
        # Conditions through the record's reading.
        ("When was synthetic concept 1001 first recorded?", {
            "task": "record_timing", "kind": "condition"}),
    )

    @classmethod
    def setUpClass(cls):
        """Build the invented edge terms once."""
        from tests.qa import record_cases
        cls.terms = record_cases.terms()

    def test_readings(self):
        """Each question gets its reading."""
        for question, expected in self.TABLE:
            found = reader.interpret(question, self.terms)
            for name, value in expected.items():
                with self.subTest(question=question, field=name):
                    self.assertEqual(getattr(found, name), value)

    def test_words_alone_never_take_a_question_that_names_no_kind(self):
        """A phrase that only the words of procedure names hold ("is
        radiography recorded", as "is diabetes recorded" once was) is left
        to the condition rules; a verb or a kind word, or a phrase that is
        a procedure's own name, takes it."""
        self.assertEqual(reader.interpret("Is radiography recorded?",
                                          self.terms).status, "none")
        self.assertEqual(reader.interpret(
            "Is the patient's chest radiography severe?",
            self.terms).status, "none")
        for question in ("Was radiography done?",
                         "Which procedures show radiography?",
                         "Is appendectomy recorded?"):
            found = reader.interpret(question, self.terms)
            self.assertEqual((found.status, found.kind),
                             ("task", "procedure"), question)
        found = self.terms.resolve("procedure", "appendectomy")
        self.assertEqual(found.rule, "name")
        self.assertEqual(self.terms.resolve("procedure", "radiography").rule,
                         "words")

    def test_numbered_times_are_times_and_numbered_names_are_not(self):
        """A day or an hour named by its number as a time is one; a number
        in a name ("24 hour urine") is not."""
        for text in ("what was the potassium on day 2",
                     "was potassium checked 3 days ago",
                     "potassium in the first 24 hours",
                     "was she ventilated for 96 hours",
                     "day 2 of the admission"):
            self.assertTrue(reader.NUMBERED_TIMES.search(text), text)
        for text in ("24 hour urine protein", "hydromorphone 12 hour",
                     "was potassium 5 4", "type 2 diabetes"):
            self.assertFalse(reader.NUMBERED_TIMES.search(text), text)

    def test_long_and_repeated_questions_are_read_without_failing(self):
        """A long question, or one word many times, is read, whatever the
        reading."""
        for question in ("potassium " * 200, "a" * 2000,
                         "what " * 300 + "labs", "? " * 500):
            found = reader.interpret(question, self.terms)
            self.assertIn(found.status, ("task", "clarify", "none"))

    def test_left_words_set_negations_and_frames_aside(self):
        """Negations, frames, selectors and harmless words are no part of
        a phrase; conjunctions are kept only when asked for."""
        self.assertEqual(reader.left_words(
            "was the last potassium not measured"), ["potassium"])
        self.assertEqual(reader.left_words("heparin or warfarin"),
                         ["heparin", "warfarin"])
        self.assertEqual(reader.left_words(
            "heparin or warfarin", keep=reader.CONJUNCTIONS),
            ["heparin", "or", "warfarin"])


class IndicationTests(unittest.TestCase):
    """Rule 9: the record does not say what a medication was given for."""

    @classmethod
    def setUpClass(cls):
        """Build the invented terms, with conditions, once."""
        cls.terms = terms(condition_vocabulary())

    def read(self, question):
        """Return the reading of a question."""
        return reader.interpret(question, self.terms)

    def test_medications_asked_for_by_what_they_were_given_for(self):
        """Medications asked for by a condition, a test or a vital sign
        they were given for get the clarification, naming it."""
        for question, phrase in (
                ("Is he taking medications for his seizures?", "seizures"),
                ("Which medications are for seizures?", "seizures"),
                ("What is he taking for his seizures?", "seizures"),
                ("What did she take to treat the seizures?", "seizures"),
                ("Which seizure medications was he given?", "seizure"),
                ("Was heparin given for DVT?", "dvt"),
                ("What was given for pain?", "pain"),
                ("Is she on anything for her heart rate?", "heart rate"),
                ("Was anything given for the potassium?", "potassium")):
            found = self.read(question)
            self.assertEqual((found.status, found.reason, found.category,
                              found.phrase),
                             ("clarify", "task_ambiguous", "indication",
                              phrase), question)

    def test_other_questions_keep_their_readings(self):
        """Questions that name what they ask about, or ask about another
        kind, are read as before: "pain" alone is the vital sign."""
        for question, task, kind in (
                ("Was heparin given?", "record_presence", "medication"),
                ("Any antibiotics?", "record_presence", "medication"),
                ("What medications were given?", "record_list",
                 "medication"),
                ("What was the pain score?", "record_values", "vital"),
                ("Was potassium checked?", "record_presence", "lab")):
            found = self.read(question)
            self.assertEqual((found.status, found.task, found.kind),
                             ("task", task, kind), question)
        for question in ("Was he given a chest x-ray for his seizures?",
                         "Was a chest x-ray done for the seizures?",
                         "Was the blood culture sent for the fever?"):
            self.assertNotEqual(self.read(question).category, "indication",
                                question)

    def test_a_drug_class_word_names_medications(self):
        """Rule 10: a fixed word for a class of medications names them,
        though a condition's synonym is the same word, in the singular or
        the plural; a question that names the condition kind keeps it."""
        self.assertEqual(self.terms.resolve("condition", "antibiotics").status,
                         "resolved")
        for question in ("Any antibiotics?", "Any antibiotic?",
                         "Antibiotics?"):
            found = self.read(question)
            self.assertEqual((found.status, found.task, found.kind),
                             ("task", "record_presence", "medication"),
                             question)
        found = self.read("Is the diagnosis antibiotics recorded?")
        self.assertNotEqual(found.kind, "medication")
        self.assertTrue(resolving.class_word("NSAIDs"))
        self.assertTrue(resolving.class_word("blood thinner"))
        self.assertFalse(resolving.class_word("heparin"))
        self.assertFalse(resolving.class_word(""))

    def test_a_drug_class_word_another_kind_names_still_asks(self):
        """A class word that is also the name of another kind of record,
        as "chemotherapy" is a procedure's, asks which kind is meant."""
        drugs = drug_vocabulary()
        terms = resolving.Terms(labs=[], items=[], micro=[], units=[],
                                drugs=drugs, procedures=("antibiotics",),
                                condition_vocabulary=condition_vocabulary())
        found = reader.interpret("Any antibiotics?", terms)
        self.assertEqual((found.status, found.category),
                         ("clarify", "several_kinds"))

    def test_without_a_condition_vocabulary_nothing_is_a_condition(self):
        """Words name a condition only through the condition vocabulary."""
        found = reader.interpret("Which medications are for seizures?",
                                 terms())
        self.assertNotEqual(found.category, "indication")


if __name__ == "__main__":
    unittest.main()
