"""Synthetic checks for ehr_evidence_app.qa.record_interpret.

Every dictionary here is invented: generic test, vital-sign, drug,
organism and unit names, as a dictionary would hold them; no patient
record is read.
"""

import unittest

from ehr_evidence_app.qa import interpret as conditions
from ehr_evidence_app.qa import record_interpret as reader
from ehr_evidence_app.qa import record_resolve as resolving
from ehr_evidence_app.qa import record_vocabulary


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


def terms():
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
        drugs=drugs, procedures=drugs.procedure_names)


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


if __name__ == "__main__":
    unittest.main()
