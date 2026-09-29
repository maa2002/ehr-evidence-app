"""An invented whole record for the edge-case tests of answers about the
record.

Every person, visit, record, name and value here is invented. The record
is built to hold what real records hold at their edges: results of one
test across visits out of time order, results at the same time, more
results than an answer lists, values that are not numbers or were removed,
a test without a name, medications without a standard concept (written
names, a salt, a name that holds another's, none at all), a brand name,
drug classes, microbiology with and without growth and with every
interpretation, an open transfer, two ICU stays, an admission without
times, one whose discharge precedes its admission, a death in hospital
and a person whose age passes 89.
"""

from ehr_evidence_app.evidence import contracts, record, record_bundles
from ehr_evidence_app.evidence.schema import DigestRef
from ehr_evidence_app.qa import record_answer, record_resolve, \
    record_vocabulary, suite

CASE_SET = "synthetic-edge-v1"
CASE = contracts.CaseRef(case_set=CASE_SET, case_number=1)
HISTORY = contracts.ScopeRef(kind="pre_target_history")
ALL = contracts.ScopeRef(kind="case_visits")
SCOPES = (HISTORY, contracts.ScopeRef(kind="selected_visit", visit_number=1),
          contracts.ScopeRef(kind="selected_visit", visit_number=2),
          contracts.ScopeRef(kind="selected_visit", visit_number=3), ALL)
VISITS = 3
# Invented drug concepts: ingredients, ATC classes and a brand name.
INGREDIENTS = {1: "heparin", 2: "furosemide", 5: "vancomycin",
               6: "potassium chloride", 7: "morphine", 8: "insulin glargine",
               9: "sodium", 10: "urea"}
CLASSES = {11: ("B01AB", "HEPARIN GROUP"), 12: ("C03", "DIURETICS"),
           13: ("N02A", "OPIOIDS"), 14: ("A10A", "INSULINS AND ANALOGUES"),
           15: ("J01", "ANTIBACTERIALS FOR SYSTEMIC USE")}
CLASS_INGREDIENTS = {11: {1}, 12: {2}, 13: {7}, 14: {8}, 15: {5}}


def drug_vocabulary():
    """Return the invented medication vocabulary."""
    drugs = {concept: record_vocabulary.DrugConcept(
        concept, "ingredient", "RxNorm", "Ingredient", "I%d" % concept, name)
        for concept, name in INGREDIENTS.items()}
    drugs.update({concept: record_vocabulary.DrugConcept(
        concept, "atc", "ATC", "ATC 3rd", code, name)
        for concept, (code, name) in CLASSES.items()})
    drugs[3] = record_vocabulary.DrugConcept(3, "brand", "RxNorm",
                                             "Brand Name", "B3", "Lasix")
    names = {}
    for concept in drugs.values():
        names.setdefault(record_resolve.normalize(concept.name),
                         set()).add(concept.concept_id)
    return record_vocabulary.RecordVocabulary(
        drugs=drugs, drug_names=names, brands={3: {2}},
        ancestors={101: {1, 11}, 102: {2, 12}},
        procedure_names=("insertion of endotracheal airway into trachea",
                         "radiography of chest", "appendectomy",
                         "chest x ray"),
        class_ingredients={concept: frozenset(found)
                           for concept, found in CLASS_INGREDIENTS.items()})


def terms():
    """Return Terms over the invented dictionaries and vocabularies."""
    drugs = drug_vocabulary()
    return record_resolve.Terms(
        labs=[("50971", "Potassium", "Blood", "Chemistry"),
              ("50912", "Creatinine", "Blood", "Chemistry"),
              ("50931", "Glucose", "Blood", "Chemistry"),
              ("51003", "Troponin T", "Blood", "Chemistry"),
              ("51222", "Hemoglobin", "Blood", "Hematology"),
              ("50983", "Sodium", "Blood", "Chemistry")],
        items=[("220045", "Heart Rate"),
               ("220179", "Non Invasive Blood Pressure systolic"),
               ("223761", "Temperature Fahrenheit"),
               ("225459", "Chest X-Ray")],
        micro=["BLOOD CULTURE", "Blood Culture, Routine", "URINE",
               "URINE CULTURE", "ESCHERICHIA COLI", "AMPICILLIN",
               "CEFAZOLIN", "NITROFURANTOIN"],
        units=["Emergency Department", "Medical Intensive Care Unit (MICU)",
               "Surgical Intensive Care Unit (SICU)", "Medicine"],
        drugs=drugs, procedures=drugs.procedure_names,
        condition_vocabulary=suite.synthetic_vocabulary())


def person():
    """Return the invented person: 88 at the anchor year, so 89 in the
    first visits and over 89 in the last one."""
    return record.Person(native_id=1, sex="F", anchor_age=88,
                         anchor_year=2149, anchor_year_group="2008 - 2010",
                         date_of_death="2151-06-04")


def visits():
    """Return the invented visits by position: an admission with times,
    one without, and one whose discharge precedes its admission and that
    ended in death."""
    first = record.Admission(
        native_id=1, admission_type="EW EMER.",
        admission_location="EMERGENCY ROOM", discharge_location="HOME",
        admit_time="2150-01-10 08:00:00", discharge_time="2150-01-15 12:00:00",
        hospital_death=False, insurance="Medicare", language="English",
        marital_status="MARRIED", race="WHITE")
    second = record.Admission(native_id=2, admission_type="ELECTIVE",
                              hospital_death=False)
    third = record.Admission(
        native_id=3, admission_type="URGENT",
        admission_location="TRANSFER FROM HOSPITAL",
        admit_time="2151-06-01 10:00:00", discharge_time="2151-06-01 08:00:00",
        death_time="2151-06-04 09:00:00", hospital_death=True)
    found = {}
    for position, (start, end, admission) in enumerate((
            ("2150-01-10", "2150-01-15", first),
            ("2150-03-01", "2150-03-03", second),
            ("2151-06-01", "2151-06-05", third))):
        found[position] = record.RecordVisit(
            ref="V%d" % (position + 1), position=position,
            visit_number=position + 1,
            role="final_admission" if position == VISITS - 1 else "pre_target",
            visit_concept_id=9201, visit_type="Inpatient Visit",
            start_date=start, end_date=end, admission=admission)
    return found


def lab(native, time, code, name, text=None, number=None, flag=None,
        detail="Blood · Chemistry", status="available"):
    """Return the fields of an invented laboratory result."""
    return ("lab", native, dict(
        name_status=status, name=name if status == "available" else None,
        code=code, vocabulary=record_bundles.LAB_ITEMS, detail=detail,
        start=time, measure=record.Measure(
            text=text, number=number, unit="mEq/L", low=3.3, high=5.1,
            flag=flag)))


def entries():
    """Return the invented records by visit position, as the record
    bundle builder holds them: (kind, native id, fields)."""
    found = {0: [], 1: [], 2: []}
    # Potassium: 20 results in the first visit (two at the same time, the
    # second flagged), 2 in the second visit that come before them in
    # time, and 15 in the last, one whose value was removed.
    for index in range(20):
        time = "2150-01-10 %02d:00:00" % min(index, 18)
        found[0].append(lab(1000 + index, time, "50971", "Potassium",
                            text="%.1f" % (3.5 + index / 10),
                            number=3.5 + index / 10,
                            flag="abnormal" if index in (15, 19) else None))
    for index in range(2):
        found[1].append(lab(1100 + index, "2150-01-05 0%d:00:00" % index,
                            "50971", "Potassium", text="4.0", number=4.0))
    for index in range(15):
        text = "___" if index == 3 else "%.1f" % (4.0 + index / 10)
        found[2].append(lab(1200 + index, "2151-06-02 %02d:00:00" % index,
                            "50971", "Potassium", text=text,
                            number=None if index == 3 else 4.0 + index / 10,
                            flag="abnormal" if index == 14 else None))
    found[0].append(lab(2000, "2150-01-11 08:00:00", "50912", "Creatinine",
                        text="1.1", number=1.1))
    for index, text in enumerate(("NEG", "TR", "HOLD")):
        found[0].append(lab(3000 + index, "2150-01-12 0%d:00:00" % index,
                            "50931", "Glucose", text=text))
    for index in range(2):
        found[2].append(lab(4000 + index, "2151-06-03 0%d:00:00" % index,
                            "51003", "Troponin T", text="0.05",
                            number=0.05))
    found[2].append(lab(5000, "2151-06-03 05:00:00", "51222", "Hemoglobin",
                        text="7.1", number=7.1, flag="abnormal",
                        detail="Blood · Hematology"))
    found[1].append(lab(6000, "2150-03-02 08:00:00", "99999", None,
                        text="12", number=12.0, status="unavailable"))
    # Vital signs, charted in the ICU of the last visit.
    for index, value in enumerate((88.0, 132.0, 96.0)):
        found[2].append(("vital", 7000 + index, dict(
            name_status="available", name="Heart Rate", code="220045",
            vocabulary=record_bundles.ICU_ITEMS, group="heart_rate",
            start="2151-06-02 1%d:00:00" % index,
            measure=record.Measure(text="%d" % value, number=value,
                                   unit="bpm"))))
    for index, value in enumerate((90.0, 145.0)):
        found[2].append(("vital", 7100 + index, dict(
            name_status="available",
            name="Non Invasive Blood Pressure systolic", code="220179",
            vocabulary=record_bundles.ICU_ITEMS,
            group="systolic_blood_pressure",
            start="2151-06-02 1%d:30:00" % index,
            measure=record.Measure(text="%d" % value, number=value,
                                   unit="mmHg"))))
    found[2].append(("vital", 7200, dict(
        name_status="available", name="Temperature Fahrenheit",
        code="223761", vocabulary=record_bundles.ICU_ITEMS,
        group="temperature", start="2151-06-02 12:00:00",
        measure=record.Measure(text="98.6", number=98.6, unit="°F"))))
    # Medications: heparin and furosemide with standard concepts; and
    # without one: vancomycin, insulin, a salt, a name holding another's,
    # morphine, and a record whose name is not linked.

    def drug(native, time, concept, name=None, ancestors=(), dose=None):
        """Return the fields of an invented drug record."""
        return ("medication", native, dict(
            concept_id=concept,
            name_status="available" if concept else "unmapped",
            name=name if concept else None, code=None,
            start=time, end=time, ancestors=tuple(sorted(ancestors)),
            dose=dose))

    heparin = record.Dose(drug="Heparin", amount="5000", unit="UNIT",
                          route="SC")
    found[0] += [drug(8000, "2150-01-10 09:00:00", 101,
                      "heparin 5000 UNT/ML Injectable Solution", (1, 11),
                      heparin),
                 drug(8001, "2150-01-11 09:00:00", 101,
                      "heparin 5000 UNT/ML Injectable Solution", (1, 11),
                      None)]
    found[1] += [drug(8100, "2150-03-01 09:00:00", 0),
                 drug(8101, "2150-03-01 10:00:00", 0, dose=record.Dose(
                     drug="Heparin Sodium", route="IV"))]
    found[2] += [drug(8200, "2151-06-01 11:00:00", 101,
                      "heparin 5000 UNT/ML Injectable Solution", (1, 11),
                      heparin),
                 drug(8201, "2151-06-01 12:00:00", 102,
                      "furosemide 40 MG Oral Tablet [Lasix]", (2, 12),
                      record.Dose(drug="Furosemide", amount="40",
                                  unit="mg", route="PO")),
                 drug(8202, "2151-06-01 13:00:00", 0, dose=record.Dose(
                     drug="Vancomycin", amount="1", unit="g", route="IV")),
                 drug(8203, "2151-06-01 14:00:00", 0, dose=record.Dose(
                     drug="Insulin", route="SC")),
                 drug(8204, "2151-06-01 15:00:00", 0, dose=record.Dose(
                     drug="Hydroxyurea", route="PO")),
                 drug(8205, "2151-06-01 16:00:00", 0, dose=record.Dose(
                     drug="Morphine Sulfate", amount="2", unit="mg",
                     route="IV"))]
    # Procedures: one with a standard concept, one without.
    found[2].append(("procedure", 9000, dict(
        concept_id=201, name_status="available",
        name="Insertion of endotracheal airway into trachea",
        code="0BH17EZ", vocabulary="ICD10PCS", start="2151-06-01")))
    found[0].append(("procedure", 9001, dict(
        concept_id=0, name_status="unmapped", code="BW03ZZZ",
        vocabulary="ICD10PCS", detail="Plain radiography of chest",
        start="2150-01-11")))
    found[2].append(("icu_procedure", 9100, dict(
        name_status="available", name="Chest X-Ray", code="225459",
        vocabulary=record_bundles.ICU_ITEMS, detail="Imaging",
        start="2151-06-02 09:00:00", end="2151-06-02 09:05:00",
        measure=record.Measure(text="1", number=1.0))))
    # Microbiology: a blood culture without growth; a urine culture that
    # grew an organism, tested against three antibiotics.
    found[0].append(("microbiology", 9200, dict(
        name_status="available", name="Blood Culture, Routine",
        code="90201", vocabulary=record_bundles.MICRO_TESTS,
        start="2150-01-10 10:00:00", microbe=record.Microbe(
            specimen="BLOOD CULTURE", test="Blood Culture, Routine"))))
    for index, (antibiotic, reading) in enumerate((
            (None, None), ("AMPICILLIN", "R"), ("CEFAZOLIN", "S"),
            ("NITROFURANTOIN", "I"))):
        found[2].append(("microbiology", 9300 + index, dict(
            name_status="available", name="URINE CULTURE", code="90039",
            vocabulary=record_bundles.MICRO_TESTS,
            start="2151-06-02 08:00:00", microbe=record.Microbe(
                specimen="URINE", test="URINE CULTURE",
                organism="ESCHERICHIA COLI", antibiotic=antibiotic,
                interpretation=reading))))
    # Transfers: the emergency department, and an open ICU transfer.
    found[0].append(("transfer", 9400, dict(
        name_status="available", name="Emergency Department",
        start="2150-01-10 06:00:00", end="2150-01-10 08:00:00",
        stay=record.Stay(event="ED", unit="Emergency Department"))))
    found[2].append(("transfer", 9401, dict(
        name_status="available", name="Medical Intensive Care Unit (MICU)",
        start="2151-06-01 10:00:00", end=None,
        stay=record.Stay(event="admit",
                         unit="Medical Intensive Care Unit (MICU)"))))
    for index, (unit, days) in enumerate((
            ("Medical Intensive Care Unit (MICU)", 1.54),
            ("Surgical Intensive Care Unit (SICU)", 1.04))):
        found[2].append(("icu_stay", 9500 + index, dict(
            name_status="available", name=unit,
            start="2151-06-0%d 10:00:00" % (1 + 2 * index),
            end="2151-06-0%d 11:00:00" % (2 + 2 * index),
            stay=record.Stay(unit=unit, last_unit=unit, days=days))))
    # Conditions: one with a standard concept in two visits, and one
    # without, named by its ICD code and description.
    for position, native in ((0, 9600), (2, 9601)):
        found[position].append(("condition", native, dict(
            concept_id=1001, name_status="available",
            name="Synthetic concept 1001", code="X01",
            vocabulary="ICD10CM", start="2150-01-10"
            if position == 0 else "2151-06-01")))
    found[1].append(("condition", 9602, dict(
        concept_id=0, name_status="unmapped", code="I10",
        vocabulary="ICD10CM", detail="Essential (primary) hypertension",
        start="2150-03-01")))
    return found


class Case:
    """The invented case, as record_bundles.record_bundle reads it."""

    case_number = 1
    visit_ids = (1, 2, 3)


OUTSIDE = {"lab": 3, "medication": 1}
PROVENANCE = record.RecordProvenance(
    source_snapshot=DigestRef(name="synthetic-edge-v1", sha256="1" * 64),
    record_extract=DigestRef(name="synthetic-edge-v1-records",
                             sha256="6" * 64),
    identity_mapping=DigestRef(name="synthetic-mapping", sha256="4" * 64),
    closure_record=DigestRef(name="synthetic-closure", sha256="3" * 64),
    concept_table=DigestRef(name="synthetic-concepts", sha256="2" * 64),
    builder="tests.qa.record_cases")


def bundles(found=None):
    """Return the invented record bundles of every scope, built by the
    record bundle builder from the invented records."""
    found = entries() if found is None else found
    return [record_bundles.record_bundle(
        CASE_SET, Case, scope, visits(), found, OUTSIDE, person(),
        PROVENANCE, {"snapshot": "synthetic-edge-v1",
                     "extract": "synthetic-edge-v1-records"})
        for scope in SCOPES]


def record_set(built=None):
    """Return a RecordSet holding the invented bundles."""
    built = bundles() if built is None else built
    manifest = record_bundles.manifest_document(built, PROVENANCE, {},
                                                "synthetic-edge-records")
    return record_answer.RecordSet(
        CASE_SET, None, manifest,
        bundles={bundle.bundle_id: bundle for bundle in built})


def context(built=None):
    """Return the RecordContext of the invented record."""
    return record_answer.RecordContext(record_set(built), terms())


class Visits:
    """The invented case set's visit counts, as a bundle set gives them."""

    def visit_count(self, case_number):
        """Return the number of visits of the invented case."""
        return VISITS if case_number == CASE.case_number else 0
