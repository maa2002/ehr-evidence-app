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
            self.terms.resolve("microbiology", "e coli"), culture), "words")

    def test_words_within_longer_words(self):
        """Words of four letters or more may be part of longer words."""
        self.assertTrue(resolving.words_in(["dialysis"], "hemodialysis"))
        self.assertFalse(resolving.words_in(["ct"], "section"))
        self.assertTrue(resolving.words_in(["ct"], "head ct scan"))


class EdgeResolutionTests(unittest.TestCase):
    """Resolution at its edges (rules record-resolution-v3), over the
    invented edge record's terms."""

    @classmethod
    def setUpClass(cls):
        """Build the invented edge terms once."""
        from tests.qa import record_cases
        cls.cases = record_cases
        cls.terms = record_cases.terms()

    def test_written_names_are_matched_by_whole_words(self):
        """Every word whole, in any order; a counter-ion that is a whole
        name only as the written name's first word."""
        self.assertTrue(resolving.written_in(("heparin",), "heparin sodium"))
        self.assertTrue(resolving.written_in(("chloride", "potassium"),
                                             "potassium chloride 20 meq"))
        self.assertFalse(resolving.written_in(("urea",), "hydroxyurea"))
        self.assertFalse(resolving.written_in(("sodium",), "heparin sodium"))
        self.assertTrue(resolving.written_in(("sodium",),
                                             "sodium bicarbonate"))
        self.assertFalse(resolving.written_in(("sodium",), ""))
        self.assertTrue(resolving.words_in(["heparin"], "heparin sodium"))
        self.assertTrue(resolving.words_in(["urea"], "hydroxyurea"))

    def test_class_questions_carry_the_names_of_their_ingredients(self):
        """A class resolves to its concept, and to the names its
        ingredients and the phrase are written as."""
        found = self.terms.resolve("medication", "opioids")
        self.assertEqual((found.status, found.rule, found.keys),
                         ("resolved", "name", (("atc", 13),)))
        self.assertEqual(found.written, (("morphine",), ("opioids",)))
        found = self.terms.resolve("medication", "painkillers")
        self.assertEqual((found.status, found.rule), ("unknown", None))
        found = self.terms.resolve("medication", "antibiotics")
        self.assertEqual((found.rule, found.keys, found.written), (
            "synonym", (("atc", 15),), (("antibiotics",), ("vancomycin",))))
        found = self.terms.resolve("medication", "insulin")
        self.assertIn(("insulin",), found.written)
        self.assertIn(("glargine", "insulin"), found.written)

    def test_singular_class_words_and_names_resolve_as_their_plurals(self):
        """A class word or a class name in the singular resolves as its
        plural does (version 3); a phrase that resolves as written keeps
        its resolution."""
        for singular, plural in (("opioid", "opioids"),
                                 ("diuretic", "diuretics"),
                                 ("antibacterial", "antibacterials"),
                                 ("blood thinner", "blood thinners")):
            one = self.terms.resolve("medication", singular)
            many = self.terms.resolve("medication", plural)
            self.assertEqual((one.status, one.keys), ("resolved", many.keys),
                             singular)
        self.assertEqual(self.terms.resolve("medication", "heparin").keys,
                         (("ingredient", 1),))
        drugs = {21: record_vocabulary.DrugConcept(
                     21, "atc", "ATC", "ATC 2nd", "N03", "ANTIEPILEPTICS"),
                 22: record_vocabulary.DrugConcept(
                     22, "ingredient", "RxNorm", "Ingredient", "I22",
                     "levetiracetam")}
        terms = resolving.Terms(
            labs=[], items=[], micro=[], units=[], procedures=(),
            drugs=record_vocabulary.RecordVocabulary(
                drugs=drugs, drug_names={
                    resolving.normalize(concept.name): {concept.concept_id}
                    for concept in drugs.values()},
                brands={}, ancestors={}, procedure_names=(),
                class_ingredients={21: frozenset({22})}))
        found = terms.resolve("medication", "antiepileptic")
        self.assertEqual((found.status, found.rule, found.names, found.keys),
                         ("resolved", "name", ("ANTIEPILEPTICS",),
                          (("atc", 21),)))

    def test_a_class_word_named_by_the_vocabulary_keeps_its_classes(self):
        """A fixed class word that the vocabulary also names, as ATC names
        the local antibiotics of the eye, is both: "antibiotics" and "an
        antibiotic" are the systemic antibacterials and the eye's."""
        drugs = {31: record_vocabulary.DrugConcept(
                     31, "atc", "ATC", "ATC 2nd", "J01",
                     "ANTIBACTERIALS FOR SYSTEMIC USE"),
                 32: record_vocabulary.DrugConcept(
                     32, "atc", "ATC", "ATC 4th", "S01AA", "Antibiotics"),
                 33: record_vocabulary.DrugConcept(
                     33, "atc", "ATC", "ATC 2nd", "N02A", "OPIOIDS")}
        terms = resolving.Terms(
            labs=[], items=[], micro=[], units=[], procedures=(),
            drugs=record_vocabulary.RecordVocabulary(
                drugs=drugs, drug_names={
                    resolving.normalize(concept.name): {concept.concept_id}
                    for concept in drugs.values()},
                brands={}, ancestors={}, procedure_names=()))
        for phrase in ("antibiotics", "antibiotic"):
            found = terms.resolve("medication", phrase)
            self.assertEqual((found.status, found.rule, found.keys),
                             ("resolved", "name", (("atc", 31), ("atc", 32))),
                             phrase)
        found = terms.resolve("medication", "opioids")
        self.assertEqual((found.rule, found.keys), ("name", (("atc", 33),)))
        found = terms.resolve("medication", "antibacterial")
        self.assertEqual((found.rule, found.keys), ("synonym",
                                                    (("atc", 31),)))

    def test_plurals_of_a_phrase(self):
        """The plural of the last word, unless it is short or ends in s."""
        self.assertEqual(resolving.plurals("antiepileptic"),
                         ["antiepileptics"])
        self.assertEqual(resolving.plurals("beta blocker"), ["beta blockers"])
        for phrase in ("opioids", "abc", "", "glass"):
            self.assertEqual(resolving.plurals(phrase), [], phrase)

    def test_misspelt_medications_and_procedures_are_suggested(self):
        """A misspelling within one edit (two from eight letters) is
        suggested; nothing is suggested for what is further away."""
        self.assertEqual(self.terms.resolve("medication",
                                            "heprin").suggestions,
                         ("heparin", "heparin group"))
        self.assertEqual(self.terms.resolve("medication",
                                            "zzzz").suggestions, ())
        self.assertEqual(self.terms.resolve("medication",
                                            "heprain").suggestions, ())
        self.assertEqual(self.terms.resolve("procedure",
                                            "apendectomy").suggestions,
                         ("appendectomy",))
        self.assertEqual(self.terms.resolve(
            "procedure", "radiografy of chest").suggestions,
            ("radiography of chest",))
        self.assertEqual(self.terms.resolve("procedure",
                                            "zzzzectomy").suggestions, ())
        self.assertEqual(self.terms.resolve("lab", "potasium").suggestions,
                         ("Potassium",))

    def test_names_before_synonyms_before_words(self):
        """A dictionary name is taken before a synonym, and a synonym
        before the names that hold the phrase's words; a singular form is
        tried before those."""
        terms = resolving.Terms(
            labs=[("1", "INR", "Blood", "Hematology"),
                  ("2", "INR(PT)", "Blood", "Hematology"),
                  ("3", "Potassium", "Blood", "Chemistry")],
            items=[], micro=[], units=[], drugs=None, procedures=())
        found = terms.resolve("lab", "inr")
        self.assertEqual((found.rule, found.keys), ("name", ("1",)))
        found = terms.resolve("lab", "potassiums")
        self.assertEqual((found.status, found.keys), ("resolved", ("3",)))
        found = terms.resolve("lab", "blood")
        self.assertEqual(found.status, "unknown")

    def test_too_many_names_are_too_broad(self):
        """A phrase whose words more than MAX_NAMES names hold is too
        broad, and names a few of them."""
        labs = [(str(number), "Synthetic panel %d" % number, "Blood",
                 "Chemistry") for number in range(resolving.MAX_NAMES + 1)]
        terms = resolving.Terms(labs=labs, items=[], micro=[], units=[],
                                drugs=None, procedures=())
        found = terms.resolve("lab", "panel")
        self.assertEqual(found.status, "too_broad")
        self.assertEqual(len(found.names), resolving.SUGGESTIONS)
        labs = labs[:resolving.MAX_NAMES]
        terms = resolving.Terms(labs=labs, items=[], micro=[], units=[],
                                drugs=None, procedures=())
        self.assertEqual(terms.resolve("lab", "panel").status, "resolved")

    def test_nothing_to_resolve_against(self):
        """Without a vocabulary, or a name, nothing resolves."""
        terms = resolving.Terms(labs=[], items=[], micro=[], units=[],
                                drugs=None, procedures=())
        for kind in ("medication", "condition", "icu_stay", "lab"):
            self.assertEqual(terms.resolve(kind, "heparin").status,
                             "unknown", kind)
        self.assertEqual(self.terms.resolve("lab", "   ").status, "unknown")

    def test_a_record_matches_only_its_own_kinds(self):
        """A resolution matches no record of another kind, and an
        unresolved one matches nothing."""
        bundle = self.cases.bundles()[-1]
        heparin = self.terms.resolve("medication", "heparin")
        potassium = self.terms.resolve("lab", "potassium")
        for entry in bundle.records:
            if entry.kind != "medication":
                self.assertIsNone(self.terms.match(heparin, entry))
            if entry.kind != "lab":
                self.assertIsNone(self.terms.match(potassium, entry))
        unknown = self.terms.resolve("lab", "florbnazine")
        self.assertTrue(all(self.terms.match(unknown, entry) is None
                            for entry in bundle.records))

    def test_extract_dictionaries(self):
        """Items without a label are left out; the names file gives the
        microbiology and care-unit names."""
        labs, items, micro, units = resolving.extract_dictionaries({
            "d_labitems": [{"itemid": "1", "label": "A", "fluid": "Blood",
                            "category": "C"},
                           {"itemid": "2", "label": "", "fluid": "",
                            "category": ""}],
            "d_items": [{"itemid": "220045", "label": "Heart Rate"},
                        {"itemid": "9", "label": ""}],
            "d_names": [{"table": "microbiologyevents", "column": "org_name",
                         "name": "B"},
                        {"table": "transfers", "column": "careunit",
                         "name": "Unit"},
                        {"table": "microbiologyevents",
                         "column": "spec_type_desc", "name": "A"}]})
        self.assertEqual(labs, [("1", "A", "Blood", "C")])
        self.assertEqual(items, [("220045", "Heart Rate")])
        self.assertEqual((micro, units), (["A", "B"], ["Unit"]))


class VocabularyVersionTests(unittest.TestCase):
    """Version 2 of the record vocabulary: each class's ingredients."""

    def test_class_ingredients_are_selected_in_the_same_pass(self):
        """One pass over the ancestors gives the recorded drugs'
        ancestors and every class's ingredients."""
        rows = [{"ancestor_concept_id": "32", "descendant_concept_id": "31"},
                {"ancestor_concept_id": "31", "descendant_concept_id": "35"},
                {"ancestor_concept_id": "32", "descendant_concept_id": "35"},
                {"ancestor_concept_id": "31", "descendant_concept_id": "31"}]
        found, pairs = record_vocabulary.select_ancestors(
            rows, {35}, {31, 32}, {32}, {31})
        self.assertEqual(dict(found), {35: {31, 32}})
        self.assertEqual(pairs, [(32, 31)])

    def test_a_vocabulary_without_classes_loads_without_them(self):
        """The first version's files load, with no class ingredients."""
        files, _, loaded = record_vocabulary.run_synthetic()
        self.assertEqual(loaded.class_ingredients, {32: {31}})
        older = dict(files)
        del older[record_vocabulary.FILES["classes"]]
        self.assertEqual(record_vocabulary.vocabulary_of(
            older).class_ingredients, {})

    def test_versions_are_pinned_by_name(self):
        """Each published version is read through its own pin."""
        first = record_vocabulary.pinned(
            "record-vocabulary-athena-20260831-v1")
        current = record_vocabulary.pinned()
        self.assertEqual(first["size_bytes"], 2110)
        self.assertEqual(current["version"],
                         record_vocabulary.OUTPUT_VERSION)
        self.assertNotEqual(first["path"], current["path"])
        with self.assertRaises(record_vocabulary.RecordVocabularyError):
            record_vocabulary.pinned("record-vocabulary-unknown")


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
