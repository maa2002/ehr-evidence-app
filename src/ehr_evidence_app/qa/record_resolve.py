"""Terms of questions about the record, and the records they match.

A question about the record names what it asks about in words: a
laboratory test, a vital sign, a medication or a class of medications, a
procedure, a microbiology specimen or organism, a care unit, or a
condition. The phrase is resolved within one kind of record by fixed
rules (rules record-resolution-v2), in order:

1. the phrase is a name the kind's dictionary holds (the laboratory and
   ICU item dictionaries, the medication vocabulary's ingredients,
   classes and brand names, the microbiology and care-unit names), after
   the question's own normalisation (qa.resolve.normalize);
2. the phrase is one of the fixed synonyms of the kind (TERMS): an
   abbreviation or everyday word ("hgb", "blood thinners", "cabg",
   "e coli", "micu"), read as the dictionary words it stands for; a
   medication class word that is also a name of the vocabulary stands
   for both;
3. otherwise the dictionary names holding every word of the phrase: a
   word of four letters or more may be part of a longer word ("dialysis"
   in "hemodialysis"), a shorter one must be a whole word. For a
   procedure, the names hold the words between them, and a phrase that
   is itself a procedure name resolves by rule 1. A resolution by words
   is weak: a question that names no kind takes it only in a kind its
   verbs or its task point to (qa.record_interpret).

A phrase that resolves to nothing is unknown, and comes with suggestions,
never a guess: names that begin with its first word, else names whose
first word is its closest spelling (one edit, two from eight letters),
and for a procedure the phrase with each unknown word spelled as the
closest word of the procedure names. One that resolves to too many names
is too broad. Conditions resolve as condition questions do (qa.resolve).

A record matches a resolved phrase, and the answer states how (MATCHES):

* item: its laboratory or ICU item is a resolved item, or its vital sign
  is of a resolved group;
* ingredient, class, brand name: its drug concept has a resolved
  ingredient or ATC class among its ancestors (a brand name resolves to
  its ingredients);
* name: a medication without a standard concept, whose drug name as
  written holds, as whole words, the phrase, the name of a resolved
  ingredient, or the name of an ingredient of a resolved class (the ATC
  hierarchy of qa.record_vocabulary); a counter-ion that is an
  ingredient's whole name (sodium, potassium, calcium, magnesium) counts
  only as the written name's first word, so "heparin sodium" is not
  sodium;
* words: its name, or the specimen, test, organism, antibiotic or unit
  written in it, holds the resolved words (procedures, microbiology and
  units);
* a condition's own match kinds (qa.resolve.match).

Version 2 matches medications without a standard concept against the
ingredients of a class and by whole words, names word matches apart from
matches of a written drug name, and suggests spellings of medications
and procedures. Version 3 resolves a medication phrase that resolves to
nothing as written by the plural of its last word, so that a class word
or a class name in the singular ("antiepileptic") resolves as its plural
("antiepileptics") does; and a fixed class word that the vocabulary also
names stands for both, so that "antibiotics" is the systemic
antibacterials as well as the classes ATC names Antibiotics (those of the
eye, the skin, the gut and the like).
"""

from __future__ import annotations

import dataclasses
import re

from ehr_evidence_app.qa import resolve as conditions
from ehr_evidence_app.sources import mimic

RULES_VERSION = "record-resolution-v3"
# The kinds a question may ask about; a procedure question covers the
# ICD procedures and the ICU procedure events.
QUESTION_KINDS = ("condition", "procedure", "medication", "lab",
                  "microbiology", "vital", "transfer", "icu_stay")
RECORD_KINDS = {"condition": ("condition",),
                "procedure": ("procedure", "icu_procedure"),
                "medication": ("medication",), "lab": ("lab",),
                "microbiology": ("microbiology",), "vital": ("vital",),
                "transfer": ("transfer",), "icu_stay": ("icu_stay",)}
MATCHES = {"item": "test or item", "group": "vital sign",
           "ingredient": "ingredient", "class": "drug class",
           "brand": "brand name", "name": "name as written",
           "words": "words of the name"}
# Salts written after a drug's name ("heparin sodium"): as an
# ingredient's whole name, each matches a written name only as its first
# word.
COUNTER_IONS = frozenset(("sodium", "potassium", "calcium", "magnesium"))
# More names than this make a phrase too broad to answer.
MAX_NAMES = 40
SUGGESTIONS = 6
SHORT_WORD = 4

# Fixed synonyms, by kind: an everyday word or an abbreviation, and the
# dictionary words it stands for. Words, not codes: each is resolved by
# the rules above, so a synonym never names what the dictionary lacks.
TERMS = {
    "lab": {
        "hb": ("hemoglobin",), "hgb": ("hemoglobin",),
        "haemoglobin": ("hemoglobin",),
        "wbc": ("white blood cells",), "white cell count": (
            "white blood cells",), "white count": ("white blood cells",),
        "white blood cell count": ("white blood cells",),
        "leukocytes": ("white blood cells",),
        "plt": ("platelet count",), "platelets": ("platelet count",),
        "k": ("potassium",), "na": ("sodium",), "cl": ("chloride",),
        "hco3": ("bicarbonate",), "bicarb": ("bicarbonate",),
        "cr": ("creatinine",), "creat": ("creatinine",),
        "bun": ("urea nitrogen",), "urea": ("urea nitrogen",),
        "inr": ("inr pt",), "ptt": ("ptt",), "aptt": ("ptt",),
        "alt": ("alanine aminotransferase alt",),
        "ast": ("aminotransferase ast",),
        "alk phos": ("alkaline phosphatase",),
        "alp": ("alkaline phosphatase",),
        "bili": ("bilirubin total",), "tbili": ("bilirubin total",),
        "total bilirubin": ("bilirubin total",),
        "trop": ("troponin",), "troponin": ("troponin",),
        "hct": ("hematocrit",), "lactic acid": ("lactate",),
        "mg": ("magnesium",), "phos": ("phosphate",),
        "calcium": ("calcium total",), "ca": ("calcium total",),
        "a1c": ("hemoglobin a1c",), "hba1c": ("hemoglobin a1c",),
        "crp": ("c reactive protein",), "esr": ("sedimentation rate",),
        "tsh": ("thyroid stimulating hormone",),
        "bnp": ("ntprobnp",), "ck": ("creatine kinase ck",),
        "ldh": ("lactate dehydrogenase ld",),
        "blood sugar": ("glucose",), "sugar": ("glucose",),
    },
    "vital": {
        "heart rate": ("heart_rate",), "hr": ("heart_rate",),
        "pulse": ("heart_rate",), "pulse rate": ("heart_rate",),
        "heart rhythm": ("heart_rhythm",), "rhythm": ("heart_rhythm",),
        "blood pressure": ("systolic_blood_pressure",
                           "diastolic_blood_pressure",
                           "mean_blood_pressure"),
        "bp": ("systolic_blood_pressure", "diastolic_blood_pressure",
               "mean_blood_pressure"),
        "systolic": ("systolic_blood_pressure",),
        "systolic blood pressure": ("systolic_blood_pressure",),
        "sbp": ("systolic_blood_pressure",),
        "diastolic": ("diastolic_blood_pressure",),
        "diastolic blood pressure": ("diastolic_blood_pressure",),
        "dbp": ("diastolic_blood_pressure",),
        "mean arterial pressure": ("mean_blood_pressure",),
        "map": ("mean_blood_pressure",),
        "mean blood pressure": ("mean_blood_pressure",),
        "respiratory rate": ("respiratory_rate",),
        "resp rate": ("respiratory_rate",), "rr": ("respiratory_rate",),
        "breathing rate": ("respiratory_rate",),
        "respirations": ("respiratory_rate",),
        "oxygen saturation": ("oxygen_saturation",),
        "o2 saturation": ("oxygen_saturation",),
        "o2 sat": ("oxygen_saturation",), "sats": ("oxygen_saturation",),
        "sat": ("oxygen_saturation",), "spo2": ("oxygen_saturation",),
        "saturation": ("oxygen_saturation",),
        "pulse ox": ("oxygen_saturation",),
        "pulse oximetry": ("oxygen_saturation",),
        "temperature": ("temperature",), "temp": ("temperature",),
        "weight": ("weight",), "body weight": ("weight",),
        "height": ("height",),
        "gcs": ("glasgow_coma_scale",),
        "glasgow coma scale": ("glasgow_coma_scale",),
        "glasgow": ("glasgow_coma_scale",),
        "pain": ("pain_level",), "pain score": ("pain_level",),
        "pain level": ("pain_level",),
        "oxygen flow": ("oxygen_flow",), "o2 flow": ("oxygen_flow",),
        "fio2": ("inspired_oxygen_fraction",),
        "inspired oxygen": ("inspired_oxygen_fraction",),
    },
    "medication": {
        "antibiotics": ("J01",), "antibiotic": ("J01",),
        "antibacterials": ("J01",),
        "anticoagulants": ("B01AA", "B01AB", "B01AE", "B01AF"),
        "anticoagulant": ("B01AA", "B01AB", "B01AE", "B01AF"),
        "blood thinners": ("B01AA", "B01AB", "B01AE", "B01AF"),
        "antiplatelets": ("B01AC",), "antiplatelet": ("B01AC",),
        "statins": ("C10AA",), "statin": ("C10AA",),
        "beta blockers": ("C07",), "beta blocker": ("C07",),
        "ace inhibitors": ("C09A", "C09B"),
        "ace inhibitor": ("C09A", "C09B"),
        "diuretics": ("C03",), "diuretic": ("C03",),
        "insulin": ("A10A",), "insulins": ("A10A",),
        "opioids": ("N02A",), "opioid": ("N02A",),
        "narcotics": ("N02A",),
        "steroids": ("H02",), "corticosteroids": ("H02",),
        "proton pump inhibitors": ("A02BC",), "ppis": ("A02BC",),
        "ppi": ("A02BC",),
        "vasopressors": ("C01CA",), "pressors": ("C01CA",),
        "antidepressants": ("N06A",), "antipsychotics": ("N05A",),
        "benzodiazepines": ("N05BA", "N05CD"),
        "antifungals": ("J02",), "antivirals": ("J05",),
        "bronchodilators": ("R03",),
        "anticonvulsants": ("N03",), "antiepileptics": ("N03",),
        "laxatives": ("A06",), "antiemetics": ("A04",),
        "analgesics": ("N02",), "painkillers": ("N02",),
        "chemotherapy": ("L01",), "immunosuppressants": ("L04",),
        "nsaids": ("M01A",), "sedatives": ("N05C",),
        "vitamins": ("A11",),
    },
    "procedure": {
        "cabg": ("coronary artery bypass", "bypass coronary"),
        "bypass": ("bypass",),
        "dialysis": ("dialysis", "urinary filtration"),
        "hemodialysis": ("dialysis", "urinary filtration"),
        "intubation": ("intubation", "endotracheal airway"),
        "intubated": ("intubation", "endotracheal airway"),
        "extubation": ("extubation",), "extubated": ("extubation",),
        "ventilated": ("ventilation",),
        "dialysed": ("dialysis", "urinary filtration"),
        "dialyzed": ("dialysis", "urinary filtration"),
        "transfused": ("transfusion",),
        "ventilation": ("ventilation",),
        "mechanical ventilation": ("ventilation",),
        "ventilator": ("ventilation",),
        "transfusion": ("transfusion",),
        "blood transfusion": ("transfusion",),
        "central line": ("central line", "central venous"),
        "arterial line": ("arterial line",),
        "chest x ray": ("chest x ray", "radiography of chest",
                        "plain radiography chest"),
        "chest xray": ("chest x ray", "radiography of chest"),
        "cxr": ("chest x ray", "radiography of chest"),
        "x ray": ("x ray", "radiography"),
        "xray": ("x ray", "radiography"),
        "ct": ("computerized tomography", "ct scan"),
        "ct scan": ("computerized tomography", "ct scan"),
        "cat scan": ("computerized tomography",),
        "mri": ("magnetic resonance imaging",),
        "echo": ("echocardiography", "ultrasonography heart"),
        "echocardiogram": ("echocardiography", "ultrasonography heart"),
        "ekg": ("electrocardiogram",), "ecg": ("electrocardiogram",),
        "endoscopy": ("endoscopy", "esophagogastroduodenoscopy",
                      "inspection upper intestinal tract"),
        "egd": ("esophagogastroduodenoscopy",
                "inspection upper intestinal tract"),
        "colonoscopy": ("colonoscopy", "inspection lower intestinal tract"),
        "cardiac catheterization": ("catheterization heart",
                                    "cardiac catheterization"),
        "cath": ("catheterization heart", "cardiac catheterization"),
        "pci": ("dilation coronary artery", "angioplasty"),
        "stent": ("stent", "intraluminal device"),
        "thoracentesis": ("thoracentesis", "drainage pleural"),
        "paracentesis": ("paracentesis", "drainage peritoneal"),
        "lumbar puncture": ("lumbar puncture", "spinal tap",
                            "drainage spinal canal"),
        "spinal tap": ("lumbar puncture", "spinal tap",
                       "drainage spinal canal"),
        "biopsy": ("biopsy", "diagnostic"),
    },
    "microbiology": {
        "e coli": ("escherichia coli",),
        "ecoli": ("escherichia coli",),
        "staph aureus": ("staph aureus",), "s aureus": ("staph aureus",),
        "staphylococcus aureus": ("staph aureus",),
        "staph": ("staph",), "strep": ("strep",),
        "c diff": ("difficile",), "c difficile": ("difficile",),
        "clostridium difficile": ("difficile",),
        "mrsa": ("mrsa",), "vre": ("vre",),
        "blood culture": ("blood culture",),
        "blood cultures": ("blood culture",),
        "urine culture": ("urine",), "urine cultures": ("urine",),
        "sputum culture": ("sputum",),
        "yeast": ("yeast",), "candida": ("candida",),
    },
    "transfer": {
        "micu": ("micu",), "sicu": ("sicu",), "ccu": ("ccu",),
        "cvicu": ("cvicu",), "tsicu": ("tsicu",), "nicu": ("neuro",),
        "ed": ("emergency department",), "er": ("emergency department",),
        "emergency room": ("emergency department",),
        "icu": ("intensive care",), "intensive care": ("intensive care",),
        "step down": ("step down",), "stepdown": ("step down",),
        "medicine": ("medicine",), "surgery": ("surgery",),
    },
}


class RecordResolutionError(Exception):
    """A dictionary that cannot be built; carries a short code."""


@dataclasses.dataclass(frozen=True)
class ItemResolution:
    """The resolution of one phrase within one kind.

    status is resolved, unknown or too_broad; names are what the phrase
    resolved to, as the dictionary writes them; keys are what records are
    matched by (item numbers, vital-sign groups, drug concepts, or word
    tuples); rule says how it resolved; suggestions are names offered for
    an unknown phrase. For a condition, condition holds its
    qa.resolve.Resolution; for a medication, written holds the names, as
    sorted word tuples, that a record without a standard concept is
    matched by.
    """

    kind: str
    phrase: str
    status: str
    rule: str | None = None
    names: tuple = ()
    keys: tuple = ()
    suggestions: tuple = ()
    condition: object = None
    written: tuple = ()


def normalize(text):
    """Return a phrase or name in normal form (as conditions are)."""
    return conditions.normalize(text)


def plurals(phrase):
    """Return the plural of a phrase's last word: "antiepileptic" as
    "antiepileptics". Tried only after the phrase as written, and its
    singulars, resolve to nothing, so a wrong plural can only fail to
    match."""
    words = phrase.split()
    if not words or len(words[-1]) <= 3 or words[-1].endswith("s"):
        return []
    return [" ".join(words[:-1] + [words[-1] + "s"])]


def class_word(phrase):
    """Return True when a phrase is a fixed word for a class of
    medications, or its singular or plural ("nsaids", "antibiotic")."""
    phrase = normalize(phrase or "")
    return any(form in TERMS["medication"] for form in
               [phrase] + conditions.singulars(phrase) + plurals(phrase))


def words_in(words, text):
    """Return True when text holds every word: whole words for short
    ones, and within longer words for words of four letters or more."""
    found = text.split()
    padded = " %s " % text
    return all((" %s " % word in padded) if len(word) < SHORT_WORD
               else any(word in token for token in found) for word in words)


def written_in(words, text):
    """Return True when a written drug name holds every word of a name as
    a whole word; a counter-ion that is the whole name only as the
    written name's first word."""
    found = text.split()
    if len(words) == 1 and words[0] in COUNTER_IONS:
        return bool(found) and found[0] == words[0]
    return set(words) <= set(found)


def closest(word, known, limit):
    """Return the words of known (grouped by their first letter) within
    limit edits of a word, closest first."""
    found = []
    for other in known.get(word[:1], ()):
        distance = conditions.edit_distance(word, other, limit)
        if distance <= limit:
            found.append((distance, len(other), other))
    return [other for _, _, other in sorted(found)]


def by_letter(words):
    """Return words grouped by their first letter, each group sorted."""
    groups = {}
    for word in sorted(words):
        groups.setdefault(word[:1], []).append(word)
    return groups


@dataclasses.dataclass(frozen=True)
class Dictionary:
    """The names of one kind: display name and matching key, by the
    normalised name."""

    kind: str
    names: dict

    def exact(self, phrase):
        """Return the (name, key) pairs whose name is the phrase."""
        return tuple(self.names.get(phrase, ()))

    def containing(self, phrase):
        """Return the (name, key) pairs whose name holds every word of
        the phrase."""
        words = phrase.split()
        return tuple(pair for name, pairs in sorted(self.names.items())
                     if words_in(words, name) for pair in pairs)

    def beginning(self, phrase, limit=SUGGESTIONS):
        """Return names to suggest for an unknown phrase: those that
        begin with its first word, else those whose first word is its
        closest spelling (one edit, two from eight letters), shorter
        names first."""
        first = phrase.split()[0] if phrase.split() else ""
        if not first:
            return ()
        ranked = sorted(self.names.items(), key=lambda item: (
            len(item[0]), item[0]))
        found = [pairs[0][0] for name, pairs in ranked
                 if name.startswith(first)]
        if not found and len(first) >= conditions.MIN_WORD:
            limit_edits = 2 if len(first) >= conditions.LONG_WORD else 1
            found = [pairs[0][0] for name, pairs in ranked
                     if name.split() and conditions.edit_distance(
                         first, name.split()[0], limit_edits)
                     <= limit_edits]
        return tuple(dict.fromkeys(found))[:limit]


def dictionary(kind, entries):
    """Return the Dictionary of (display name, key) pairs."""
    names = {}
    for display, key in entries:
        normal = normalize(display)
        if normal:
            names.setdefault(normal, [])
            if (display, key) not in names[normal]:
                names[normal].append((display, key))
    return Dictionary(kind, {name: tuple(pairs)
                             for name, pairs in names.items()})


class Terms:
    """Everything a question's phrase is resolved against: the item
    dictionaries of the raw extract, the medication and procedure
    vocabulary and the condition vocabulary."""

    def __init__(self, labs, items, micro, units, drugs, procedures,
                 condition_vocabulary=None):
        """Build the dictionaries.

        labs: (itemid, label, fluid, category) of the laboratory items;
        items: (itemid, label) of the ICU items; micro: the microbiology
        names (specimens, tests, organisms, antibiotics); units: the care
        units; drugs: a qa.record_vocabulary.RecordVocabulary, or None;
        procedures: known procedure names, normalised.
        """
        self.lab_items = {itemid: (label, fluid, category)
                          for itemid, label, fluid, category in labs}
        self.labs = dictionary("lab", ((label, itemid)
                                       for itemid, label, _, _ in labs))
        vital_items = {itemid: label for itemid, label in items
                       if mimic.VITAL_ITEMS.__contains__(int(itemid))}
        self.vital_labels = vital_items
        self.vitals = dictionary("vital", (
            (group.replace("_", " "), group)
            for group in mimic.VITAL_GROUPS))
        self.icu_items = {itemid: label for itemid, label in items}
        self.micro = dictionary("microbiology",
                                ((name, normalize(name)) for name in micro))
        self.units = dictionary("transfer",
                                ((name, normalize(name)) for name in units))
        self.drugs = drugs
        self.procedure_names = frozenset(procedures)
        self.procedure_words = frozenset(word for name in procedures
                                         for word in name.split())
        self.procedure_long_words = tuple(sorted(
            word for word in self.procedure_words if len(word) >= SHORT_WORD))
        # Built when first needed: the words of each ingredient's name,
        # and the words suggestions are spelled from.
        self.ingredient_words = {}
        self.spelling = {}
        self.condition_vocabulary = condition_vocabulary
        # ICD source concepts by vocabulary and code, for the description
        # matches of condition records without a standard concept.
        self.source_concepts = {}
        if condition_vocabulary is not None:
            for concept_id, row in condition_vocabulary.rows.items():
                if row.kind == "icd":
                    self.source_concepts[(row.vocabulary_id,
                                         row.concept_code)] = (
                        concept_id)

    # -- resolution ---------------------------------------------------------

    def resolve(self, kind, text):
        """Return the ItemResolution of a phrase within a kind."""
        phrase = normalize(text)
        if not phrase:
            return ItemResolution(kind, phrase, "unknown")
        if kind == "condition":
            return self.condition(phrase, text)
        if kind == "medication":
            return self.medication(phrase)
        if kind == "procedure":
            return self.procedure(phrase)
        if kind == "icu_stay":
            return ItemResolution(kind, phrase, "unknown")
        table = {"lab": self.labs, "vital": self.vitals,
                 "microbiology": self.micro, "transfer": self.units}[kind]
        return self.by_names(kind, phrase, table)

    def by_names(self, kind, phrase, table):
        """Resolve by the rules: a name, a synonym, then the names that
        hold every word."""
        found = table.exact(phrase)
        rule = "name"
        if not found and phrase in TERMS.get(kind, {}):
            rule = "synonym"
            targets = TERMS[kind][phrase]
            if kind == "vital":
                found = tuple((group.replace("_", " "), group)
                              for group in targets)
            else:
                found = tuple(pair for target in targets for pair in (
                    table.exact(target) or table.containing(target)))
        if not found and conditions.singulars(phrase):
            for singular in conditions.singulars(phrase):
                found = table.exact(singular)
                if found:
                    break
        if not found:
            rule = "words"
            found = table.containing(phrase)
        if not found:
            return ItemResolution(kind, phrase, "unknown",
                                  suggestions=table.beginning(phrase))
        names = tuple(dict.fromkeys(name for name, _ in found))
        keys = tuple(dict.fromkeys(key for _, key in found))
        if len(keys) > MAX_NAMES:
            return ItemResolution(kind, phrase, "too_broad", rule,
                                  names=names[:SUGGESTIONS])
        return ItemResolution(kind, phrase, "resolved", rule, names, keys)

    def condition(self, phrase, text):
        """Resolve a condition phrase as condition questions do."""
        if self.condition_vocabulary is None:
            return ItemResolution("condition", phrase, "unknown")
        found = conditions.resolve(text, self.condition_vocabulary)
        if found.status != "resolved":
            names = tuple(self.condition_vocabulary.rows[concept].name
                          for concept in found.concepts
                          if concept in self.condition_vocabulary.rows)
            return ItemResolution("condition", phrase, "unknown"
                                  if found.status == "unknown"
                                  else found.status,
                                  suggestions=names[:SUGGESTIONS],
                                  condition=found)
        names = ((found.codes[0],) if found.rule == "icd_code" else tuple(
            self.condition_vocabulary.rows[concept].name
            for concept in found.concepts))
        return ItemResolution("condition", phrase, "resolved", found.rule,
                              names, tuple(found.concepts or found.codes),
                              condition=found)

    def medication(self, phrase):
        """Resolve a medication phrase: an ingredient, a class or a brand
        name of the vocabulary, or a fixed class word; failing those, the
        same for the plural of its last word."""
        if self.drugs is None:
            return ItemResolution("medication", phrase, "unknown")
        ids, rule = self.drug_concepts(phrase)
        for form in plurals(phrase) if not ids else ():
            ids, rule = self.drug_concepts(form)
        if not ids:
            return ItemResolution("medication", phrase, "unknown",
                                  suggestions=self.drug_suggestions(phrase))
        keys, names = set(), []
        for concept_id in sorted(ids):
            concept = self.drugs.drugs.get(concept_id)
            if concept is None:
                continue
            names.append(concept.name)
            if concept.kind == "brand":
                keys |= {("ingredient", ingredient) for ingredient in
                         self.drugs.brands.get(concept_id, ())}
            else:
                keys.add((concept.kind, concept_id))
        if not keys:
            return ItemResolution("medication", phrase, "unknown")
        return ItemResolution("medication", phrase, "resolved", rule,
                              tuple(dict.fromkeys(names)),
                              tuple(sorted(keys)),
                              written=self.written_names(phrase, keys))

    def drug_concepts(self, phrase):
        """Return the drug concepts of a phrase, and the rule: those the
        vocabulary names by it, or by a singular of it; for a fixed class
        word, together with its fixed classes and those the vocabulary
        names by it in the plural, so that "antibiotics" is the systemic
        antibacterials as well as the classes named Antibiotics of the eye,
        the skin and the like. (empty, None) when it is neither."""
        forms = [phrase] + conditions.singulars(phrase)
        named = set()
        for candidate in forms:
            named = set(self.drugs.drug_names.get(candidate, ()))
            if named:
                break
        if phrase not in TERMS["medication"]:
            return named, "name" if named else None
        for candidate in forms + plurals(phrase):
            named |= set(self.drugs.drug_names.get(candidate, ()))
        codes = set(TERMS["medication"][phrase])
        fixed = {concept.concept_id for concept in self.drugs.drugs.values()
                 if concept.kind == "atc" and concept.code in codes}
        return named | fixed, "name" if named else "synonym"

    def written_names(self, phrase, keys):
        """Return the names, as sorted word tuples, that a medication
        without a standard concept is matched by: the phrase, each
        resolved ingredient and each ingredient of a resolved class."""
        ingredients = set()
        classes = getattr(self.drugs, "class_ingredients", {}) or {}
        for kind, concept in keys:
            if kind == "ingredient":
                ingredients.add(concept)
            elif kind == "atc":
                ingredients |= set(classes.get(concept, ()))
        found = {tuple(sorted(phrase.split()))}
        for concept in ingredients:
            words = self.ingredient_words.get(concept)
            if words is None:
                item = self.drugs.drugs.get(concept)
                words = tuple(sorted(normalize(item.name).split())) if (
                    item is not None) else ()
                self.ingredient_words[concept] = words
            if words:
                found.add(words)
        return tuple(sorted(found))

    def spelled(self, kind, words):
        """Return the words suggestions of a kind are spelled from,
        grouped by their first letter."""
        if kind not in self.spelling:
            self.spelling[kind] = by_letter(words())
        return self.spelling[kind]

    def drug_suggestions(self, phrase):
        """Return medication names that begin with the phrase's first
        word, else those whose first word is its closest spelling."""
        first = phrase.split()[0]
        found = sorted(name for name in self.drugs.drug_names
                       if name.startswith(first))
        if not found and len(first) >= conditions.MIN_WORD:
            limit = 2 if len(first) >= conditions.LONG_WORD else 1
            known = self.spelled("medication", lambda: {
                name.split()[0] for name in self.drugs.drug_names
                if name.split()})
            spellings = closest(first, known, limit)
            found = sorted(name for name in self.drugs.drug_names
                           if name.split() and name.split()[0] in spellings)
        found.sort(key=len)
        return tuple(found[:SUGGESTIONS])

    def procedure_suggestions(self, phrase):
        """Return the phrase with each word no procedure name holds spelled
        as the closest word of the procedure names, when every word then
        is one."""
        known = self.spelled("procedure", lambda: self.procedure_words)
        words, changed = [], False
        for word in phrase.split():
            if self.procedure_word(word):
                words.append(word)
                continue
            if len(word) < conditions.MIN_WORD or not word.isalpha():
                return ()
            limit = 2 if len(word) >= conditions.LONG_WORD else 1
            spellings = closest(word, known, limit)
            if not spellings:
                return ()
            words.append(spellings[0])
            changed = True
        return (" ".join(words),) if changed else ()

    def procedure(self, phrase):
        """Resolve a procedure phrase to the words records are matched
        by: a fixed synonym's words, or the phrase itself when it is a
        procedure name (rule name) or procedure names hold its words (rule
        words)."""
        targets = TERMS["procedure"].get(phrase)
        rule = "synonym"
        if targets is None:
            rule = "name" if phrase in self.procedure_names else "words"
            if rule == "words" and not all(self.procedure_word(word)
                                           for word in phrase.split()):
                return ItemResolution(
                    "procedure", phrase, "unknown",
                    suggestions=self.procedure_suggestions(phrase))
            targets = (phrase,)
        return ItemResolution("procedure", phrase, "resolved", rule,
                              (phrase,), tuple(tuple(target.split())
                                               for target in targets))

    def procedure_word(self, word):
        """Return True when procedure names hold a word: whole, or, for a
        word of four letters or more, within a longer word."""
        if word in self.procedure_words:
            return True
        return len(word) >= SHORT_WORD and any(
            word in known for known in self.procedure_long_words)

    # -- matching -----------------------------------------------------------

    def match(self, resolution, entry):
        """Return how a record matches a resolved phrase, or None."""
        if resolution.status != "resolved":
            return None
        kind = entry.kind
        if kind not in RECORD_KINDS.get(resolution.kind, ()):
            return None
        if kind == "condition":
            return conditions.match(resolution.condition,
                                    self.condition_vocabulary,
                                    entry.concept_id, entry.code,
                                    self.source_concepts.get(
                                        (entry.vocabulary, entry.code)))
        if kind in ("lab",):
            return "item" if entry.code in resolution.keys else None
        if kind == "vital":
            return "group" if entry.group in resolution.keys else None
        if kind == "medication":
            return self.medication_match(resolution, entry)
        text = normalize(" ".join(filter(None, record_texts(entry))))
        if kind in ("procedure", "icu_procedure"):
            keys = resolution.keys
        else:
            keys = tuple(tuple(key.split()) for key in resolution.keys)
        if any(words_in(words, text) for words in keys):
            return "words"
        return None

    def medication_match(self, resolution, entry):
        """Return how a drug record matches a resolved medication."""
        ancestors = set(entry.ancestors) | ({entry.concept_id}
                                            if entry.concept_id else set())
        ingredient = any(kind == "ingredient" and concept in ancestors
                         for kind, concept in resolution.keys)
        drug_class = any(kind == "atc" and concept in ancestors
                         for kind, concept in resolution.keys)
        if ingredient:
            return "brand" if resolution.rule == "name" and all(
                self.drugs.drugs.get(concept_id) is not None
                and self.drugs.drugs[concept_id].kind == "brand"
                for concept_id in self.drugs.drug_names.get(
                    resolution.phrase, ())) else "ingredient"
        if drug_class:
            return "class"
        if entry.concept_id == 0:
            text = normalize(" ".join(filter(None, record_texts(entry))))
            if any(written_in(words, text) for words in resolution.written
                   if words):
                return "name"
        return None


def record_texts(entry):
    """Return the texts a record is matched by name in: its name and
    detail, the drug written in its prescription, and the specimen, test,
    organism and antibiotic of a microbiology row."""
    texts = [entry.name, entry.detail]
    if entry.dose is not None:
        texts.append(entry.dose.drug)
    if entry.microbe is not None:
        texts += [entry.microbe.specimen, entry.microbe.test,
                  entry.microbe.organism, entry.microbe.antibiotic]
    if entry.stay is not None:
        texts += [entry.stay.unit, entry.stay.last_unit]
    return texts


# --------------------------------------------------------------------------
# Loading from the raw extract's dictionaries
# --------------------------------------------------------------------------

def extract_dictionaries(tables):
    """Return (labs, items, micro names, units) from a raw extract's
    dictionary tables (rows as dictionaries)."""
    labs = [(row["itemid"], row["label"], row["fluid"], row["category"])
            for row in tables["d_labitems"] if row["label"]]
    items = [(row["itemid"], row["label"]) for row in tables["d_items"]
             if row["label"]]
    micro, units = set(), set()
    for row in tables["d_names"]:
        if row["table"] == "microbiologyevents":
            micro.add(row["name"])
        elif row["table"] == "transfers":
            units.add(row["name"])
    return labs, items, sorted(micro), sorted(units)


WORD = re.compile(r"[a-z0-9]+")
