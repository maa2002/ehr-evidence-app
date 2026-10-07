"""Edge cases of how the facts of answers about the record are shown
(validation.record_text). Every record, visit and person is invented."""

import unittest

from ehr_evidence_app.evidence import record
from ehr_evidence_app.validation import record_text as words


def entry(**fields):
    """Return an invented laboratory record with changes."""
    values = dict(ref="R1", kind="lab", source="labevents", native_id=1,
                  visit_ref="V1", name_status="available", name="Potassium",
                  code="50971", detail="Blood · Chemistry",
                  measure=record.Measure(text="5", number=5.0, unit="mEq/L"))
    values.update(fields)
    return record.Entry(**values)


def visit(start="2150-01-10", end="2150-01-12", **admission):
    """Return an invented visit, with an admission when fields are
    given."""
    return record.RecordVisit(
        ref="V1", position=0, visit_number=1, role="pre_target",
        visit_concept_id=9201, visit_type="Inpatient Visit",
        start_date=start, end_date=end,
        admission=record.Admission(**dict({"native_id": 1,
                                           "hospital_death": False},
                                          **admission))
        if admission else None)


class NumberAndDateTests(unittest.TestCase):
    """Numbers and dates as recorded."""

    def test_numbers(self):
        """Every recorded digit, no exponent, no needless ".0"."""
        for number, shown in ((5.0, "5"), (0.5, "0.5"), (123456.7,
                                                        "123456.7"),
                              (1e-05, "0.00001"), (2.5e-07, "0.00000025"),
                              (-0.0, "0"), (-3.25, "-3.25"),
                              (1e16, "10000000000000000"), (None, "")):
            self.assertEqual(words.number_text(number), shown, number)

    def test_dates(self):
        """Dates with or without their time; what is not a date as it
        is."""
        for value, shown in (("2150-03-02", "2 Mar 2150"),
                             ("2150-03-02 08:05:00", "2 Mar 2150 08:05"),
                             ("2150-03-02 08:05", "2 Mar 2150 08:05"),
                             ("2150-13-02", "2150-13-02"),
                             ("2150-02-30", "2150-02-30"),
                             ("abcd-ef-gh", "abcd-ef-gh"),
                             ("", "no time recorded"),
                             (None, "no time recorded")):
            self.assertEqual(words.date_text(value), shown, value)


class NameTests(unittest.TestCase):
    """How a record is named, with and without a name."""

    def test_names_and_why_a_name_is_missing(self):
        """A name; a medication without a named concept by its drug as
        written, alone; else what the record holds and why it has no
        name."""
        self.assertEqual(words.record_name(entry()),
                         "Potassium (Blood, Chemistry)")
        self.assertEqual(words.record_name(entry(
            name_status="unavailable", name=None)),
            "Blood, Chemistry (name unavailable)")
        self.assertEqual(words.record_name(entry(
            name_status="unavailable", name=None,
            vocabulary="MIMIC-IV laboratory item")),
            "MIMIC-IV laboratory item 50971, Blood, Chemistry (name "
            "unavailable)")
        drug = dict(kind="medication", source="drug_exposure",
                    measure=None, detail=None, code=None)
        self.assertEqual(words.record_name(entry(
            **drug, concept_id=0, name_status="unmapped", name=None,
            dose=record.Dose(drug="Vancomycin"))), "Vancomycin")
        self.assertEqual(words.record_name(entry(
            **drug, concept_id=5, name_status="unavailable", name=None,
            dose=record.Dose(drug="Vancomycin"))), "Vancomycin")
        self.assertEqual(words.record_name(entry(
            **drug, concept_id=0, name_status="unmapped", name=None)),
            "medication whose name is not linked to its prescription")
        self.assertEqual(words.record_name(entry(
            **dict(drug, code="12345"), concept_id=5,
            name_status="unavailable", name=None)),
            "code 12345 (name unavailable)")
        self.assertEqual(words.record_name(entry(
            kind="condition", source="condition_occurrence", measure=None,
            concept_id=0, name_status="unmapped", name=None, code="I10",
            vocabulary="ICD10CM", detail="Essential hypertension")),
            "ICD10CM I10, Essential hypertension (no standard concept)")
        culture = entry(kind="microbiology", source="microbiologyevents",
                        measure=None, detail=None, name="URINE CULTURE",
                        microbe=record.Microbe(specimen="URINE"))
        self.assertEqual(words.record_name(culture), "URINE CULTURE (URINE)")
        same = culture.model_copy(update={"microbe": record.Microbe(
            specimen="urine culture")})
        self.assertEqual(words.record_name(same), "URINE CULTURE")


class ValueTests(unittest.TestCase):
    """Values of every kind."""

    def test_measures(self):
        """A value with its unit, its range when both bounds are known and
        its flag; a removed value without a unit."""
        measure = record.Measure
        for value, shown in (
                (None, "no value recorded"),
                (measure(text="5", unit="None"), "5"),
                (measure(text="___", unit="mg"),
                 "value removed by de-identification"),
                (measure(text="___", number=4.0, unit="mg"), "4 mg"),
                (measure(number=0.00001, unit="mg"), "0.00001 mg"),
                (measure(text="5", low=1.0), "5"),
                (measure(text="5", low=1.0, high=9.5, flag="abnormal"),
                 "5 (reference range 1 to 9.5; flagged abnormal)")):
            self.assertEqual(words.measure_text(value), shown)

    def test_microbiology(self):
        """The organism and an antibiotic's reading, or no organism."""
        microbe = record.Microbe
        self.assertEqual(words.microbe_text(microbe()),
                         "specimen; no organism recorded")
        self.assertEqual(words.microbe_text(microbe(
            specimen="URINE", organism="E. COLI", antibiotic="AMPICILLIN")),
            "URINE: E. COLI, AMPICILLIN without an interpretation")
        self.assertEqual(words.microbe_text(microbe(
            specimen="URINE", organism="E. COLI", antibiotic="AMPICILLIN",
            interpretation="P")), "URINE: E. COLI, AMPICILLIN pending (P)")

    def test_an_organism_and_its_results(self):
        """An organism's results by interpretation, resistant first, each
        antibiotic once and in name order; none, said so."""
        def row(antibiotic=None, reading=None):
            return entry(kind="microbiology", source="microbiologyevents",
                         name="URINE CULTURE", detail=None, measure=None,
                         microbe=record.Microbe(
                             specimen="URINE", organism="E. COLI",
                             antibiotic=antibiotic, interpretation=reading))
        rows = [row(), row("gentamicin", "S"), row("AMPICILLIN", "R"),
                row("CEFAZOLIN", "S"), row("AMPICILLIN", "R"),
                row("NITROFURANTOIN", "I"), row("MEROPENEM", "P"),
                row("TOBRAMYCIN")]
        self.assertEqual(words.organism_text(rows[0]), "E. COLI")
        self.assertEqual(words.results_text(rows), (
            "resistant to AMPICILLIN; intermediate to NITROFURANTOIN; "
            "sensitive to CEFAZOLIN and gentamicin; pending for MEROPENEM; "
            "tested without an interpretation against TOBRAMYCIN"))
        self.assertEqual(words.results_text(rows[:1]),
                         "no antibiotic results recorded")
        self.assertEqual(words.joined(["A", "B", "C"]), "A, B and C")

    def test_stays(self):
        """Times, an open end, a length rounded to a tenth of a day and
        the transfer's event."""
        stay = entry(kind="icu_stay", source="icustays", measure=None,
                     detail=None, start="2150-01-10 10:00:00", end=None,
                     stay=record.Stay(unit="MICU", days=0.96))
        self.assertEqual(words.stay_text(stay),
                         "10 Jan 2150 10:00 to an open end (1 day)")
        stay = stay.model_copy(update={"start": None, "stay": record.Stay(
            event="transfer", days=0.0)})
        self.assertEqual(words.stay_text(stay),
                         "no time recorded to an open end (0 days), "
                         "transfer")

    def test_doses(self):
        """A dose and route when linked; what is missing when not."""
        drug = dict(kind="medication", source="drug_exposure", measure=None,
                    detail=None, concept_id=5, name="Heparin")
        self.assertEqual(words.dose_text(entry(**drug)), "dose not linked")
        for dose, shown in ((record.Dose(), "dose not recorded"),
                            (record.Dose(amount="5000"), "5000"),
                            (record.Dose(unit="UNIT", route="SC"),
                             "route SC"),
                            (record.Dose(amount="1", unit="g", route="IV"),
                             "1 g, route IV")):
            self.assertEqual(words.dose_text(entry(**drug, dose=dose)),
                             shown)


class VisitAndPersonTests(unittest.TestCase):
    """Visits' and the person's fields."""

    def test_lengths(self):
        """From the admission's times, else the visit's dates; never a
        negative length."""
        timed = visit(admit_time="2150-01-10 08:00:00",
                      discharge_time="2150-01-11 08:00:00")
        self.assertEqual(words.visit_field("length", timed),
                         "length of stay 1 day")
        self.assertEqual(words.visit_field("length", visit()),
                         "length of stay 2 days")
        self.assertEqual(words.visit_field("length", visit(None, None)),
                         "length of stay not recorded")
        backwards = visit(admit_time="2150-01-10 08:00:00",
                          discharge_time="2150-01-09 08:00:00")
        self.assertEqual(words.visit_field("length", backwards),
                         "length of stay not given: the discharge is "
                         "recorded before the admission")
        self.assertNotIn("-", words.visit_field("summary", backwards))

    def test_fields_with_and_without_an_admission(self):
        """A field an admission lacks, or a visit without one, is not
        recorded."""
        bare = visit()
        for field, shown in (("admitted", "admitted 10 Jan 2150"),
                             ("discharged", "discharged 12 Jan 2150"),
                             ("type", "admission type not recorded "
                                      "(Inpatient Visit)"),
                             ("admitted_from", "admitted from not recorded"),
                             ("race", "race not recorded"),
                             ("hospital_death",
                              "death in hospital not recorded"),
                             ("summary", "Inpatient Visit, 10 Jan 2150 to "
                                         "12 Jan 2150, 2 days")):
            self.assertEqual(words.visit_field(field, bare), shown, field)
        died = visit(hospital_death=True)
        self.assertEqual(words.visit_field("hospital_death", died),
                         "died in hospital")

    def test_ages(self):
        """The anchor age plus the years to the visit; above 89 a lower
        bound; without a date, not known."""
        person = record.Person(native_id=1, anchor_age=80, anchor_year=2141)
        self.assertEqual(words.age_at(person, visit()), "89")
        self.assertEqual(words.age_at(person, visit("2151-01-01")),
                         "over 89")
        self.assertEqual(words.age_at(person.model_copy(update={
            "anchor_age": 91}), visit("2141-01-01")), "over 89")
        self.assertEqual(words.age_at(person, visit(None)),
                         "not known (the visit has no date)")
        self.assertEqual(words.person_field("sex", person),
                         "sex not recorded")
        self.assertEqual(words.person_field("death", person),
                         "no date of death recorded")
        with self.assertRaises(ValueError):
            words.person_field("race", person)


if __name__ == "__main__":
    unittest.main()
