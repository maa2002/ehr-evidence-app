"""How the facts of an answer about the record are shown.

A statement about the record names records, visits and the person by
reference; the words that show them are chosen here, from the records
themselves, so that no writer types a name, a value, a time, a date or
an age. validation.candidate renders a statement's slots with these
functions; validation.record_support checks what the references stand
for.
"""

from __future__ import annotations

import datetime

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
          "Oct", "Nov", "Dec")
# How a kind of record is named: singular and plural.
KIND_NOUNS = {
    "condition": ("condition", "conditions"),
    "procedure": ("procedure", "procedures"),
    "medication": ("medication", "medications"),
    "lab": ("laboratory test", "laboratory tests"),
    "microbiology": ("microbiology test", "microbiology tests"),
    "vital": ("vital sign", "vital signs"),
    "transfer": ("care unit", "care units"),
    "icu_stay": ("ICU stay", "ICU stays"),
    "icu_procedure": ("ICU procedure", "ICU procedures"),
}
SELECTOR_WORDS = {"latest": "Latest", "first": "First",
                  "highest": "Highest", "lowest": "Lowest",
                  "only": "Only"}
MATCH_WORDS = {"item": "the test or item itself",
               "group": "the vital sign",
               "ingredient": "an ingredient",
               "class": "a drug class",
               "brand": "the brand name's ingredient",
               "name": "the name as written (no standard concept)",
               "exact": "exact concept",
               "more_specific": "a more specific concept",
               "icd_code": "ICD code",
               "more_specific_icd_code": "a more specific ICD code",
               "icd_description": "ICD description (no standard concept)"}
INTERPRETATION_WORDS = {"S": "sensitive (S)", "R": "resistant (R)",
                        "I": "intermediate (I)", "P": "pending (P)"}
FIELD_LABELS = {"sex": "sex", "age": "age", "race": "race",
                "language": "language", "insurance": "insurance",
                "marital_status": "marital status",
                "death": "date of death",
                "admitted": "admitted", "discharged": "discharged",
                "length": "length of stay",
                "type": "admission type",
                "admitted_from": "admitted from",
                "discharged_to": "discharged to",
                "hospital_death": "death in hospital",
                "summary": "visit"}
# Ages MIMIC-IV does not give exactly.
OLDEST_AGE = 89
WITHHELD = "___"


def kind_word(kind, plural=False):
    """Return how a kind of record is named."""
    singular, many = KIND_NOUNS[kind]
    return many if plural else singular


def items_text(number, kind):
    """Return a count of distinct items of a kind: "3 distinct
    medications", "1 ICU stay"."""
    singular, plural = KIND_NOUNS[kind]
    noun = singular if number == 1 else plural
    if kind in ("icu_stay",):
        return "%d %s" % (number, noun)
    return "%d distinct %s" % (number, noun)


def date_text(value):
    """Return a date or a time as shown: "2 Mar 2150" or "2 Mar 2150
    08:00"."""
    if not value:
        return "no time recorded"
    try:
        day = datetime.date(int(value[:4]), int(value[5:7]),
                            int(value[8:10]))
    except ValueError:
        return value
    text = "%d %s %d" % (day.day, MONTHS[day.month - 1], day.year)
    if len(value) >= 16:
        text += " " + value[11:16]
    return text


def number_text(number):
    """Return a number as recorded, without a needless ".0"."""
    if number is None:
        return ""
    if float(number).is_integer():
        return "%d" % number
    return ("%.6g" % number)


def record_name(entry):
    """Return how a record's item is named: its name, with a laboratory
    test's fluid and category; a record without a name by what it holds
    instead."""
    if entry.name_status == "available":
        if entry.kind == "lab" and entry.detail:
            return "%s (%s)" % (entry.name, entry.detail.replace(" · ", ", "))
        specimen = entry.microbe.specimen if entry.microbe else None
        if specimen and specimen.casefold() != entry.name.casefold():
            return "%s (%s)" % (entry.name, specimen)
        return entry.name
    if entry.dose is not None and entry.dose.drug:
        return "%s (as written; no standard concept)" % entry.dose.drug
    if entry.detail:
        code = "%s %s, " % (entry.vocabulary, entry.code) if (
            entry.vocabulary and entry.code) else ""
        return "%s%s (no standard concept)" % (code, entry.detail)
    if entry.kind == "medication":
        return "medication whose name is not linked to its prescription"
    if entry.code:
        return "code %s (name unavailable)" % entry.code
    return "name unavailable"


def measure_text(measure):
    """Return a result's value as recorded, with its unit, reference
    range and flag."""
    if measure is None:
        return "no value recorded"
    if measure.text is not None and measure.text != WITHHELD:
        value = measure.text
    elif measure.number is not None:
        value = number_text(measure.number)
    else:
        value = "value removed by de-identification"
    if measure.unit and measure.unit not in ("None",):
        value += " " + measure.unit
    extras = []
    if measure.low is not None and measure.high is not None:
        extras.append("reference range %s to %s" % (
            number_text(measure.low), number_text(measure.high)))
    if measure.flag == "abnormal":
        extras.append("flagged abnormal")
    if extras:
        value += " (%s)" % "; ".join(extras)
    return value


def microbe_text(microbe):
    """Return what a microbiology row holds: the organism and an
    antibiotic's interpretation, or that no organism is recorded."""
    if microbe.organism is None:
        return "%s; no organism recorded" % (microbe.specimen or "specimen")
    text = "%s: %s" % (microbe.specimen or "specimen", microbe.organism)
    if microbe.antibiotic is not None:
        text += ", %s %s" % (microbe.antibiotic, INTERPRETATION_WORDS.get(
            microbe.interpretation, "without an interpretation"))
    return text


def stay_text(entry):
    """Return a stay's times and length."""
    text = "%s to %s" % (date_text(entry.start), date_text(entry.end)
                         if entry.end else "an open end")
    if entry.stay is not None and entry.stay.days is not None:
        days = entry.stay.days
        text += " (%s %s)" % (number_text(round(days, 1)),
                              "day" if round(days, 1) == 1 else "days")
    if entry.stay is not None and entry.stay.event:
        text += ", %s" % entry.stay.event
    return text


def dose_text(entry):
    """Return a medication's dose and route as ordered, when linked."""
    dose = entry.dose
    if dose is None:
        return "dose not linked"
    parts = []
    if dose.amount:
        parts.append(" ".join(part for part in (dose.amount, dose.unit)
                              if part))
    if dose.route:
        parts.append("route %s" % dose.route)
    return ", ".join(parts) or "dose not recorded"


def value_text(entry):
    """Return the value a record holds, by its kind."""
    if entry.kind in ("lab", "vital", "icu_procedure"):
        return measure_text(entry.measure)
    if entry.kind == "microbiology":
        return microbe_text(entry.microbe)
    if entry.kind in ("transfer", "icu_stay"):
        return stay_text(entry)
    if entry.kind == "medication":
        return dose_text(entry)
    return record_name(entry)


def when_text(entry):
    """Return when a record was made: its start."""
    return date_text(entry.start)


def visit_text(visit):
    """Return how a visit is named."""
    return "visit %d" % visit.visit_number


def length_days(visit):
    """Return a visit's length in days, from its admission's times when
    they are recorded, else from its dates; None when neither is."""
    admission = visit.admission
    if admission is not None and admission.admit_time and (
            admission.discharge_time):
        start = datetime.datetime.fromisoformat(admission.admit_time)
        end = datetime.datetime.fromisoformat(admission.discharge_time)
        return round((end - start).total_seconds() / 86400.0, 1)
    if visit.start_date and visit.end_date:
        return float((datetime.date.fromisoformat(visit.end_date)
                      - datetime.date.fromisoformat(visit.start_date)).days)
    return None


def age_at(person, visit):
    """Return a person's age at a visit as shown: the anchor age plus the
    years from the anchor year to the visit's year, or "over 89"."""
    if not visit.start_date:
        return "not known (the visit has no date)"
    age = person.anchor_age + int(visit.start_date[:4]) - person.anchor_year
    if person.anchor_age > OLDEST_AGE or age > OLDEST_AGE:
        return "over %d" % OLDEST_AGE
    return "%d" % age


def recorded(value):
    """Return a recorded value, or "not recorded"."""
    return value if value else "not recorded"


def visit_field(field, visit):
    """Return one field of a visit as shown."""
    admission = visit.admission
    label = FIELD_LABELS[field]
    if field == "admitted":
        return "%s %s" % (label, date_text(admission.admit_time
                                           if admission and
                                           admission.admit_time
                                           else visit.start_date))
    if field == "discharged":
        return "%s %s" % (label, date_text(admission.discharge_time
                                           if admission and
                                           admission.discharge_time
                                           else visit.end_date))
    if field == "length":
        days = length_days(visit)
        if days is None:
            return "%s not recorded" % label
        return "%s %s %s" % (label, number_text(days),
                             "day" if days == 1 else "days")
    if field == "type":
        kind = admission.admission_type if admission else None
        return "%s %s%s" % (label, recorded(kind), " (%s)" % visit.visit_type
                            if visit.visit_type else "")
    if field == "summary":
        parts = [visit.visit_type or "visit type not recorded"]
        if admission is not None and admission.admission_type:
            parts.append(admission.admission_type)
        start = (admission.admit_time if admission and admission.admit_time
                 else visit.start_date)
        end = (admission.discharge_time
               if admission and admission.discharge_time else visit.end_date)
        parts.append("%s to %s" % (date_text(start), date_text(end)))
        days = length_days(visit)
        if days is not None:
            parts.append("%s %s" % (number_text(days),
                                    "day" if days == 1 else "days"))
        return ", ".join(parts)
    if field == "hospital_death":
        if admission is None:
            return "%s not recorded" % label
        if admission.hospital_death:
            return "died in hospital%s" % (
                ", " + date_text(admission.death_time)
                if admission.death_time else "")
        return "no death in hospital recorded"
    values = {"admitted_from": "admission_location",
              "discharged_to": "discharge_location",
              "race": "race", "language": "language",
              "insurance": "insurance", "marital_status": "marital_status"}
    value = getattr(admission, values[field]) if admission else None
    return "%s %s" % (label, recorded(value))


def person_field(field, person, visit=None):
    """Return one field of the person as shown."""
    if field == "sex":
        return "sex %s" % {"F": "female", "M": "male"}.get(person.sex,
                                                            "not recorded")
    if field == "death":
        if person.date_of_death:
            return "date of death %s" % date_text(person.date_of_death)
        return "no date of death recorded"
    if field == "age":
        return "age %s" % age_at(person, visit)
    raise ValueError("not a field of the person")
