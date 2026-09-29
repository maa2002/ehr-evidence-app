"""Synthetic checks for the record vocabulary, term resolution and the
rendering of record facts.

The Athena rows, dictionaries and records here are invented. No
vocabulary file or patient record is read.
"""

import unittest

from ehr_evidence_app.evidence import record
from ehr_evidence_app.qa import record_resolve as resolving
from ehr_evidence_app.qa import record_vocabulary
from ehr_evidence_app.validation import record_text as words
from tests.qa.test_record_interpret import terms


class VocabularyTests(unittest.TestCase):
    """The invented build and its loading."""

    def test_synthetic_build_loads(self):
        """Ingredients, classes, brands and recorded drugs' ancestors are
        built and loaded; retired procedures are left out."""
        files, counts, loaded = record_vocabulary.run_synthetic()
        self.assertEqual(loaded.ancestors, {35: {31, 32}})
        self.assertEqual(loaded.brands, {33: {31}})
        self.assertIn("synthetic drug", loaded.drug_names)
        self.assertEqual(counts["procedure_names"], 2)
        self.assertIn("synthetic organ removal", loaded.procedure_names)
        self.assertNotIn("retired synthetic procedure",
                         loaded.procedure_names)
        self.assertEqual(set(files), set(record_vocabulary.FILES.values()))

    def test_the_vocabulary_is_pinned(self):
        """The published vocabulary is read through its pin."""
        entry = record_vocabulary.pinned()
        self.assertTrue(entry["path"].endswith("manifest.json"))
        self.assertEqual(len(entry["sha256"]), 64)


class ResolutionTests(unittest.TestCase):
    """The rules: a name, a synonym, then the names holding the words."""

    @classmethod
    def setUpClass(cls):
        """Build the invented terms once."""
        cls.terms = terms()

    def test_names_synonyms_and_words(self):
        """Each rule resolves its phrases."""
        found = self.terms.resolve("lab", "potassium")
        self.assertEqual((found.status, found.rule, found.keys),
                         ("resolved", "name", ("1",)))
        found = self.terms.resolve("lab", "wbc")
        self.assertEqual((found.rule, found.keys), ("synonym", ("4",)))
        found = self.terms.resolve("lab", "troponin")
        self.assertEqual((found.rule, found.keys), ("synonym", ("6",)))
        found = self.terms.resolve("vital", "bp")
        self.assertEqual(found.keys, ("systolic_blood_pressure",
                                      "diastolic_blood_pressure",
                                      "mean_blood_pressure"))
        found = self.terms.resolve("transfer", "micu")
        self.assertEqual(found.status, "resolved")

    def test_medications_by_ingredient_brand_and_class(self):
        """A brand resolves to its ingredient; a class word to the
        class."""
        self.assertEqual(self.terms.resolve("medication", "lasix").keys,
                         (("ingredient", 2),))
        self.assertEqual(self.terms.resolve("medication",
                                            "antibiotics").keys,
                         (("atc", 4),))
        self.assertEqual(self.terms.resolve("medication", "heparin").keys,
                         (("ingredient", 1),))

    def test_unknown_phrases_come_with_suggestions(self):
        """An unknown phrase is never guessed."""
        found = self.terms.resolve("lab", "potasium")
        self.assertEqual(found.status, "unknown")
        self.assertIn("Potassium", found.suggestions)
        self.assertEqual(self.terms.resolve("procedure",
                                            "zzzectomy").status, "unknown")
        self.assertEqual(self.terms.resolve("procedure",
                                            "appendectomy").status,
                         "resolved")

    def test_matching_records(self):
        """Records match by item, ingredient, class or name."""
        lab = record.Entry(ref="R1", kind="lab", source="labevents",
                           native_id=1, visit_ref="V1",
                           name_status="available", name="Potassium",
                           code="1")
        self.assertEqual(self.terms.match(
            self.terms.resolve("lab", "potassium"), lab), "item")
        self.assertIsNone(self.terms.match(
            self.terms.resolve("lab", "creatinine"), lab))
        drug = record.Entry(ref="R2", kind="medication",
                            source="drug_exposure", native_id=2,
                            visit_ref="V1", name_status="available",
                            name="vancomycin 1 G Injection",
                            concept_id=105, ancestors=(4, 5))
        self.assertEqual(self.terms.match(
            self.terms.resolve("medication", "vancomycin"), drug),
            "ingredient")
        self.assertEqual(self.terms.match(
            self.terms.resolve("medication", "antibiotics"), drug), "class")
        written = record.Entry(ref="R3", kind="medication",
                               source="drug_exposure", native_id=3,
                               visit_ref="V1", name_status="unmapped",
                               concept_id=0,
                               dose=record.Dose(drug="Heparin Sodium"))
        self.assertEqual(self.terms.match(
            self.terms.resolve("medication", "heparin"), written), "name")
        culture = record.Entry(ref="R4", kind="microbiology",
                               source="microbiologyevents", native_id=4,
                               visit_ref="V1", name_status="available",
                               name="BLOOD CULTURE",
                               microbe=record.Microbe(
                                   specimen="BLOOD CULTURE",
                                   organism="ESCHERICHIA COLI"))
        self.assertEqual(self.terms.match(
            self.terms.resolve("microbiology", "e coli"), culture), "name")

    def test_words_within_longer_words(self):
        """Words of four letters or more may be part of longer words."""
        self.assertTrue(resolving.words_in(["dialysis"], "hemodialysis"))
        self.assertFalse(resolving.words_in(["ct"], "section"))
        self.assertTrue(resolving.words_in(["ct"], "head ct scan"))


class TextTests(unittest.TestCase):
    """How record facts are shown."""

    def test_values_units_ranges_and_flags(self):
        """A result shows its value, unit, range and flag."""
        measure = record.Measure(text="5.6", number=5.6, unit="mEq/L",
                                 low=3.3, high=5.1, flag="abnormal")
        self.assertEqual(words.measure_text(measure),
                         "5.6 mEq/L (reference range 3.3 to 5.1; flagged "
                         "abnormal)")
        self.assertEqual(words.measure_text(record.Measure(text="___")),
                         "value removed by de-identification")
        self.assertEqual(words.measure_text(None), "no value recorded")

    def test_dates_and_times(self):
        """Dates are shown as dates, times with their hour."""
        self.assertEqual(words.date_text("2150-03-02"), "2 Mar 2150")
        self.assertEqual(words.date_text("2150-03-02 08:05:00"),
                         "2 Mar 2150 08:05")
        self.assertEqual(words.date_text(None), "no time recorded")

    def test_ages(self):
        """An age is the anchor age plus the years to the visit; ages
        over 89 are not given."""
        person = record.Person(native_id=1, anchor_age=60, anchor_year=2100)
        visit = record.RecordVisit(ref="V1", position=0, visit_number=1,
                                   role="pre_target", visit_concept_id=0,
                                   start_date="2105-01-01")
        self.assertEqual(words.age_at(person, visit), "65")
        old = person.model_copy(update={"anchor_age": 91})
        self.assertEqual(words.age_at(old, visit), "over 89")
        older = person.model_copy(update={"anchor_age": 88})
        self.assertEqual(words.age_at(older, visit), "over 89")

    def test_lengths_of_stay(self):
        """A stay's length comes from its admission's times."""
        admission = record.Admission(
            native_id=1, admit_time="2100-01-01 08:00:00",
            discharge_time="2100-01-03 20:00:00", hospital_death=False)
        visit = record.RecordVisit(ref="V1", position=0, visit_number=1,
                                   role="pre_target", visit_concept_id=0,
                                   start_date="2100-01-01",
                                   end_date="2100-01-03",
                                   admission=admission)
        self.assertEqual(words.length_days(visit), 2.5)
        self.assertEqual(words.visit_field("length", visit),
                         "length of stay 2.5 days")


if __name__ == "__main__":
    unittest.main()
