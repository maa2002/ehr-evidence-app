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


def vocabulary_of(names, synonyms=()):
    """Return an invented condition vocabulary of standard concepts, each
    its own only ancestor."""
    concepts = [(concept, "standard", "SNOMED", "Disorder", "S%d" % concept,
                 "", name) for concept, name in sorted(names.items())]
    return resolve.parse({
        build.FILES["concepts"]: build.encode_tsv("concepts", concepts),
        build.FILES["synonyms"]: build.encode_tsv("synonyms",
                                                  list(synonyms)),
        build.FILES["ancestors"]: build.encode_tsv(
            "ancestors", [(concept, concept, 0) for concept in names]),
        build.FILES["icd_maps"]: build.encode_tsv("icd_maps", [])})


def condition_vocabulary():
    """Return an invented condition vocabulary: a seizure, a thrombosis
    also known by its abbreviation, pain, which is a vital sign's name too,
    and a reaction its synonym calls by a drug class's word."""
    return vocabulary_of({2001: "Seizure", 2002: "Deep venous thrombosis",
                          2003: "Pain", 2004: "Reaction to an antibacterial"},
                         [(2002, "DVT"), (2004, "Antibiotics")])


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
                ("What were all the troponin t values?", "all"),
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

    def test_positive_and_negative_cultures(self):
        """Rule 13: positive and negative ask whether anything grew, about
        the culture the question names."""
        for question in ("Was the blood culture positive?",
                         "Was the blood culture negative?"):
            self.assertReads(question, task="record_values",
                             kind="microbiology", phrase="blood culture",
                             selector="all")
        self.assertReads("Were the blood cultures negative?",
                         task="record_values", kind="microbiology",
                         selector="all")
        self.assertReads("Were any cultures negative?",
                         task="record_values", kind="microbiology",
                         selector="all")

    def test_the_latest_or_first_culture(self):
        """Rule 13: the latest or the first asks about one culture; the
        highest or lowest about all of them."""
        self.assertReads("Was the latest blood culture positive?",
                         task="record_values", kind="microbiology",
                         selector="latest")
        self.assertReads("Did the first urine culture grow anything?",
                         task="record_values", kind="microbiology",
                         selector="first")
        self.assertReads("What was the highest culture growth?",
                         task="record_values", kind="microbiology",
                         selector="all")

    def test_how_a_result_reads(self):
        """Rule 14: normal, positive or negative at the end of a question
        asks for the results the laboratory flagged; a kind without a flag
        is asked back, as for abnormal."""
        for question in ("Was the potassium normal?",
                         "Was the troponin positive?",
                         "Was the troponin T negative?",
                         "Is the glucose within normal limits?",
                         "Was the creatinine normal or abnormal?"):
            self.assertReads(question, task="record_abnormal", kind="lab")
        self.assertReads("Were any labs normal?", task="record_abnormal",
                         kind="lab")
        for question in ("Was the heart rate normal?", "Was heparin normal?"):
            self.assertReads(question, status="clarify",
                             category="flag_unrecorded")

    def test_quality_words_within_a_name(self):
        """Rule 14: within a question the words stay part of the phrase."""
        reading = self.read("Was normal heparin given?")
        self.assertNotEqual(reading.task, "record_abnormal")
        self.assertEqual(reader.left_words("normal heparin", ()),
                         ["normal", "heparin"])
        self.assertEqual(reader.QUALITY.search("gram negative rods"), None)

    def test_the_gram_stain_and_positive_within_a_name(self):
        """Rule 19: "positive" stays in a name after "gram" or between two
        of its words, and the Gram stain at the end of a question is a
        name; elsewhere "positive" is a word of the question, as before."""
        drugs = drug_vocabulary()
        terms = resolving.Terms(
            labs=[("6", "Troponin T", "Blood", "Chemistry")], items=[],
            micro=["BLOOD CULTURE", "MRSA SCREEN",
                   "GRAM POSITIVE COCCUS(COCCI)", "GRAM POSITIVE RODS",
                   "GRAM NEGATIVE DIPLOCOCCI", "GRAM NEGATIVE ROD(S)",
                   "STAPHYLOCOCCUS, COAGULASE NEGATIVE"],
            units=[], drugs=drugs, procedures=drugs.procedure_names)
        positive = ["GRAM POSITIVE COCCUS(COCCI)", "GRAM POSITIVE RODS"]
        for question, task, phrase, names in (
                ("Did any culture grow gram positive cocci?",
                 "record_values", "gram positive cocci", positive[:1]),
                ("Any gram positive organisms?", "record_values",
                 "gram positive", positive),
                ("Was the culture gram positive?", "record_presence",
                 "gram positive", positive),
                ("Was the culture gram negative?", "record_presence",
                 "gram negative", ["GRAM NEGATIVE DIPLOCOCCI",
                                   "GRAM NEGATIVE ROD(S)"]),
                ("Did any culture grow coagulase positive staph?",
                 "record_values", "coagulase positive staph", [])):
            found = reader.interpret(question, terms)
            self.assertEqual((found.task, found.kind, found.phrase),
                             (task, "microbiology", phrase), question)
            resolution = terms.resolve("microbiology", phrase)
            self.assertEqual(sorted(resolution.names), names, question)
        for question, expected in (
                ("Any positive cultures?", dict(task="record_values",
                                                phrase=None)),
                ("Was the blood culture positive?",
                 dict(task="record_values", phrase="blood culture")),
                ("Was the MRSA screen positive?", dict(phrase="mrsa screen")),
                ("Was the troponin positive?", dict(task="record_abnormal",
                                                    phrase="troponin")),
                ("Was the culture gram positive or negative?",
                 dict(status="clarify", category="two_items"))):
            found = reader.interpret(question, terms)
            for name, value in expected.items():
                self.assertEqual(getattr(found, name), value, question)
        self.assertNotIn("positive", reader.left_words(
            "did she test positive for mrsa"))
        self.assertEqual(reader.left_words("any gram positive organisms",
                                           ("microbiology",)),
                         ["gram", "positive"])

    def test_a_name_asked_about_as_a_test(self):
        """Rule 20: "test" alone names a laboratory or a microbiology test,
        and the name is read where the dictionary holds it as a name or a
        synonym, the question asking which when both do; the laboratory's
        named tests, and questions that name none, are read as before."""
        drugs = drug_vocabulary()
        terms = resolving.Terms(
            labs=[("1", "Potassium", "Blood", "Chemistry"),
                  ("7", "MRSA PCR", "Other Body Fluid", "Microbiology"),
                  ("8", "Legionella Antigen", "Urine", "Microbiology")],
            items=[], micro=["BLOOD CULTURE", "ESCHERICHIA COLI", "URINE",
                             "MRSA SCREEN", "LEGIONELLA ANTIGEN"],
            units=[], drugs=drugs, procedures=drugs.procedure_names)
        for question, expected in (
                ("Did she test positive for MRSA?",
                 dict(task="record_values", kind="microbiology",
                      phrase="mrsa")),
                ("Was the MRSA test positive?",
                 dict(task="record_values", kind="microbiology")),
                ("Was an MRSA test done?",
                 dict(task="record_presence", kind="microbiology")),
                ("When was the MRSA test?",
                 dict(task="record_timing", kind="microbiology")),
                ("Was the e coli test positive?",
                 dict(kind="microbiology", phrase="e coli")),
                ("Was the potassium test done?",
                 dict(task="record_presence", kind="lab",
                      phrase="potassium")),
                ("Was the MRSA PCR test positive?",
                 dict(task="record_abnormal", kind="lab",
                      phrase="mrsa pcr")),
                ("Was a lab test for MRSA done?",
                 dict(kind="lab", phrase="mrsa")),
                ("Was a legionella antigen test done?",
                 dict(status="clarify", category="several_kinds")),
                ("What tests were done?",
                 dict(task="record_list", kind="lab", phrase=None)),
                ("Were any tests abnormal?",
                 dict(task="record_abnormal", kind="lab"))):
            found = reader.interpret(question, terms)
            for name, value in expected.items():
                self.assertEqual(getattr(found, name), value, question)
        self.assertTrue(reader.tested_only("did she test positive for mrsa"))
        self.assertFalse(reader.tested_only("which blood tests were done"))
        self.assertFalse(reader.tested_only("was a lab test for mrsa done"))

    def test_a_selected_result_and_its_flag(self):
        """Rule 15: a result a selector picks, asked about by its flag,
        keeps its selector; a vital sign and a plain selector do not ask
        for a flag."""
        for question, selector in (
                ("Was the latest potassium high?", "latest"),
                ("Is the latest creatinine abnormal?", "latest"),
                ("Was the first glucose normal?", "first"),
                ("Was the highest potassium abnormal?", "highest"),
                ("Was the lowest hemoglobin low?", "lowest")):
            self.assertReads(question, task="record_values", kind="lab",
                             selector=selector, category="lab_flag")
        self.assertReads("What was the highest potassium?",
                         task="record_values", selector="highest",
                         category=None)
        self.assertReads("Was the latest heart rate high?",
                         task="record_values", kind="vital",
                         selector="latest", category=None)
        self.assertReads("Was the latest culture resistant?",
                         task="record_values", kind="microbiology",
                         selector="latest")
        self.assertReads("Were any cultures resistant?",
                         task="record_abnormal", kind="microbiology")

    def test_surgery_and_the_prediction(self):
        """Rules 16 and 17: procedures asked about as surgery, and the
        prediction."""
        self.assertReads("Did the patient have surgery?", task="record_list",
                         kind="procedure", category="surgery")
        self.assertReads("How many operations?", task="record_count",
                         kind="procedure", category="surgery")
        self.assertReads("Which procedures were done?", task="record_list",
                         category=None)
        for question in ("Is the predicted diagnosis recorded?",
                         "Does the patient have the predicted diagnosis?"):
            self.assertReads(question, status="clarify",
                             reason="prediction_selection_needed",
                             category="prediction")

    def test_visits_and_the_person_in_other_words(self):
        """Rule 18: the visits' and the person's fields in other words;
        an age is answered, never compared."""
        for question, field in (
                ("Was the patient admitted through the ED?",
                 "admitted_from"),
                ("Did she come in via the emergency department?",
                 "admitted_from"),
                ("Was it an emergency?", "type"),
                ("Was the admission urgent?", "type"),
                ("Was the patient readmitted?", "list")):
            self.assertReads(question, task="visit_details", field=field)
        for question, field in (("Is the patient over 65?", "age"),
                                ("Is she older than 80 years?", "age"),
                                ("Was the patient alive at discharge?",
                                 "death"),
                                ("Did the patient die in hospital?",
                                 "death")):
            self.assertReads(question, task="person_details", field=field)
        reading = self.read("When was the patient discharged from the "
                            "emergency department?")
        self.assertNotEqual((reading.task, reading.field),
                            ("visit_details", "discharged"))
        self.assertReads("Was potassium above 5?", status="clarify",
                         category="qualified")

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
        ("Any positive cultures?", {"task": "record_values",
                                    "kind": "microbiology",
                                    "selector": "all"}),
        ("Was the latest culture negative?", {"task": "record_values",
                                              "kind": "microbiology",
                                              "selector": "latest"}),
        ("What was the last blood culture result?", {
            "task": "record_values", "kind": "microbiology",
            "selector": "latest"}),
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

    def test_a_names_words_stay_together(self):
        """Rule 11: a span of the question that is a whole name by a name
        or a synonym is the phrase, its words set aside within it and
        beside it included; otherwise the question is read as before."""
        terms = globals()["terms"](vocabulary_of({
            2001: "Seizure", 2002: "Deep venous thrombosis", 2003: "Pain",
            2101: "Chronic kidney disease", 2102: "Anxiety disorder",
            2103: "Anxiety", 2104: "Type 2 diabetes mellitus without "
            "complication", 2105: "Disorder of kidney", 2106: "Dependence "
            "on respirator", 2107: "Personal history of tobacco use",
            2108: "Kidney stone", 2109: "Complication of surgical "
            "procedure"}))
        for question, expected in (
                ("When was chronic kidney disease first recorded?",
                 ("record_timing", "condition", "chronic kidney disease")),
                ("How many times was anxiety disorder recorded?",
                 ("record_count", "condition", "anxiety disorder")),
                ("When was anxiety first recorded?",
                 ("record_timing", "condition", "anxiety")),
                ("When was type 2 diabetes mellitus without complication "
                 "first recorded?", ("record_timing", "condition",
                                     "type 2 diabetes mellitus without "
                                     "complication")),
                ("When was disorder of kidney first recorded?",
                 ("record_timing", "condition", "disorder of kidney")),
                ("When was dependence on respirator first recorded?",
                 ("record_timing", "condition", "dependence on respirator")),
                ("How many times was personal history of tobacco use "
                 "recorded?", ("record_count", "condition",
                               "personal history of tobacco use")),
                ("When was the blood culture taken?",
                 ("record_timing", "microbiology", "blood culture")),
                ("When was complication of surgical procedure first "
                 "recorded?", ("record_timing", "condition",
                               "complication of surgical procedure")),
                ("When was the disease first recorded?",
                 ("record_timing", "condition", None))):
            found = reader.interpret(question, terms)
            self.assertEqual((found.status, found.task, found.kind,
                              found.phrase), ("task",) + expected, question)
            if found.phrase:
                self.assertEqual(terms.resolve(found.kind,
                                               found.phrase).status,
                                 "resolved", question)
        found = reader.interpret("When was kidney stone disease first "
                                 "recorded?", terms)
        self.assertEqual((found.kind, found.phrase),
                         ("condition", "kidney stone"))
        found = reader.interpret("Which procedure was done for the kidney "
                                 "stone?", terms)
        self.assertNotEqual(found.kind, "condition")
        for question, category in (
                ("Was heparin or furosemide given?", "two_items"),
                ("What medications were given for chronic kidney disease?",
                 None),
                ("What is he taking for his seizures?", "indication"),
                ("Was heparin given yesterday?", "scope_unrecognised"),
                ("Was the potassium above 5?", "qualified")):
            found = reader.interpret(question, terms)
            self.assertEqual(found.status, "clarify", question)
            if category is not None:
                self.assertEqual(found.category, category, question)
        spans = reader.name_spans("when was chronic kidney disease first "
                                  "recorded", ["condition"])
        self.assertIn("chronic kidney disease", spans)
        self.assertNotIn("chronic kidney", spans)
        self.assertEqual([len(span.split()) for span in spans], sorted(
            (len(span.split()) for span in spans), reverse=True))
        self.assertEqual(reader.name_spans("which diseases are recorded",
                                           ["condition"]), [])
        self.assertIsNone(terms.exact("condition", "chronic kidney"))
        self.assertIsNone(terms.exact("lab", "potass"))
        self.assertEqual(terms.exact("lab", "potassium").keys, ("1",))

    def test_without_a_condition_vocabulary_nothing_is_a_condition(self):
        """Words name a condition only through the condition vocabulary."""
        found = reader.interpret("Which medications are for seizures?",
                                 terms())
        self.assertNotEqual(found.category, "indication")


if __name__ == "__main__":
    unittest.main()
