"""Answers about microbiology at the edges of real records
(record-answer-v6).

The invented record of tests.qa.record_cases, with its microbiology
replaced by cultures at their edges: two organisms and a record without
one taken at one time, an organism tested against no antibiotic, every
interpretation, an antibiotic tested twice, a test with more cultures than
an answer shows, the latest culture of a test differing from the first,
an organism asked about by its name, and organisms named by their Gram
stain. Every answer leaves through the release gate, and every mutant of
it is withheld. Every record and value here is invented.
"""

import unittest

from pydantic import ValidationError

from ehr_evidence_app.evidence import record, record_bundles
from ehr_evidence_app.qa import answer, record_answer, record_interpret, suite
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import gate, record_support
from tests.qa import record_cases as cases
from tests.validation import test_record_mutations as mutations

VOCAB = suite.synthetic_vocabulary()
BLOOD = ("Blood Culture, Routine", "BLOOD CULTURE", "90201")
URINE = ("URINE CULTURE", "URINE", "90039")
GRAM = ("GRAM POSITIVE COCCUS(COCCI)", "GRAM NEGATIVE DIPLOCOCCI",
        "GRAM NEGATIVE ROD(S)", "STAPHYLOCOCCUS, COAGULASE NEGATIVE")


def culture(native, time, test, organism=None, antibiotic=None,
            reading=None):
    """Return the fields of an invented microbiology record."""
    name, specimen, code = test
    return ("microbiology", native, dict(
        name_status="available", name=name, code=code,
        vocabulary=record_bundles.MICRO_TESTS, start=time,
        microbe=record.Microbe(specimen=specimen, test=name,
                               organism=organism, antibiotic=antibiotic,
                               interpretation=reading)))


def context_with(rows, micro=()):
    """Return the RecordContext of the invented record with its
    microbiology replaced by rows, each (visit position, record), and the
    microbiology names in micro added to its dictionary."""
    found = {position: [item for item in items if item[0] != "microbiology"]
             for position, items in cases.entries().items()}
    for position, item in rows:
        found[position].append(item)
    return cases.context(cases.bundles(found), micro)


def mixed_culture():
    """One blood culture taken at one time: Escherichia coli tested
    against antibiotics with every interpretation, one twice; another
    organism tested against none; a record that names no organism."""
    time = "2150-01-10 10:00:00"
    rows = [culture(9500, time, BLOOD, "ESCHERICHIA COLI")]
    for index, (antibiotic, reading) in enumerate((
            ("AMPICILLIN", "R"), ("AMPICILLIN", "R"), ("CEFTRIAXONE", "S"),
            ("GENTAMICIN", "I"), ("MEROPENEM", "P"),
            ("TOBRAMYCIN", None))):
        rows.append(culture(9501 + index, time, BLOOD, "ESCHERICHIA COLI",
                            antibiotic, reading))
    rows.append(culture(9510, time, BLOOD,
                        "STAPHYLOCOCCUS, COAGULASE NEGATIVE"))
    rows.append(culture(9511, time, BLOOD))
    return [(0, row) for row in rows]


def gram_cultures():
    """Blood cultures of organisms named by their Gram stain or their
    coagulase, each in a dictionary that holds them: gram positive cocci
    tested against an antibiotic, gram negative diplococci and rods, and
    coagulase negative staphylococci."""
    return [(0, culture(9800, "2150-01-10 10:00:00", BLOOD, GRAM[0])),
            (0, culture(9801, "2150-01-10 10:00:00", BLOOD, GRAM[0],
                        "VANCOMYCIN", "S")),
            (1, culture(9802, "2150-03-02 09:00:00", BLOOD, GRAM[1])),
            (1, culture(9803, "2150-03-02 15:00:00", BLOOD, GRAM[2])),
            (2, culture(9804, "2151-06-02 08:00:00", BLOOD, GRAM[3]))]


def many_cultures(grown, negative):
    """Blood cultures of the final admission: the first ones grew an
    organism, the later ones none, each one record."""
    rows = []
    for index in range(grown + negative):
        time = "2151-06-%02d %02d:%02d:00" % (1 + index // 24, index % 24,
                                               index % 60)
        rows.append((2, culture(9600 + index, time, BLOOD,
                                "ESCHERICHIA COLI" if index < grown
                                else None)))
    return rows


class Answer:
    """A question answered about an invented record, through the gate."""

    def __init__(self, question, context, scope=cases.ALL):
        """Answer a question and hold what the mutants need."""
        result = answer.record_result(question, cases.CASE, scope,
                                      cases.Visits(), VOCAB, "q-1", context)
        self.question = question
        self.response = result.response
        self.candidate = result.candidate
        self.scope = scope
        self.bundle, self.digest = context.records.bundle(1, scope)
        reading = record_interpret.interpret(question, context.terms)
        self.check = record_support.RecordCheck(
            context.terms, record_answer.resolution_of(context, reading),
            reading)

    def outcome(self, candidate):
        """Return the gate's outcome of a candidate as a writer's
        document."""
        try:
            candidate = candidates.parse(candidate.model_dump(mode="json"))
        except ValidationError:
            return "structure_invalid"
        return gate.release(candidate, "q-1", cases.CASE, self.scope,
                            self.bundle, self.digest, VOCAB,
                            record=self.check).outcome

    def lines(self):
        """Return the lines of the answer's text."""
        return self.response.text.splitlines()


class CultureTests(unittest.TestCase):
    """Cultures at their edges, answered and checked."""

    def assertReleased(self, found):
        """Assert an answer is released, and every mutant of it
        withheld."""
        self.assertEqual(found.response.outcome, "answer", found.question)
        self.assertEqual(found.outcome(found.candidate), "answer")
        missed = [label for label, mutant in mutations.mutants(found)
                  if found.outcome(mutant) not in ("validation_rejection",
                                                   "structure_invalid")]
        self.assertEqual(missed, [], found.question)

    def test_organisms_of_one_culture(self):
        """Each organism once, in the order of its first record, with its
        antibiotic results by interpretation, each antibiotic named once;
        an organism tested against none; the record without one."""
        found = Answer("Was the blood culture positive?",
                       context_with(mixed_culture()))
        self.assertEqual(found.lines(), [
            "An organism is recorded in the results of BLOOD CULTURE within "
            "all case visits, including the final admission.",
            "Blood Culture, Routine (BLOOD CULTURE):",
            "- ESCHERICHIA COLI, 10 Jan 2150 10:00, in visit 1: resistant "
            "to AMPICILLIN; intermediate to GENTAMICIN; sensitive to "
            "CEFTRIAXONE; pending for MEROPENEM; tested without an "
            "interpretation against TOBRAMYCIN",
            "- STAPHYLOCOCCUS, COAGULASE NEGATIVE, 10 Jan 2150 10:00, in "
            "visit 1: no antibiotic results recorded",
            "- No organism recorded, 10 Jan 2150 10:00, in visit 1"])
        cited = {ref for claim in found.response.claims for ref in claim.refs}
        micro = {entry.ref for entry in found.bundle.records
                 if entry.kind == "microbiology"}
        self.assertEqual(cited, micro)
        self.assertReleased(found)

    def test_every_culture_that_grew_is_shown(self):
        """Past the limit, the earliest cultures that grew nothing are
        counted, never one that grew."""
        found = Answer("What grew in the blood culture?",
                       context_with(many_cultures(1, 35)))
        kinds = [statement.kind for statement in found.candidate.statements]
        self.assertEqual(kinds.count("record_growth_item"), 1)
        self.assertEqual(kinds.count("record_growth_negative"),
                         record_answer.MAX_VALUES - 1)
        omitted = [statement for statement in found.candidate.statements
                   if statement.notice == "values_omitted"]
        self.assertEqual(omitted[0].slots.earlier, 6)
        self.assertIn("- ESCHERICHIA COLI, 1 Jun 2151 00:00, in visit 3: "
                      "no antibiotic results recorded", found.lines())
        self.assertNotIn("- No organism recorded, 1 Jun 2151 01:01, in "
                         "visit 3", found.lines())
        self.assertReleased(found)

    def test_more_cultures_grew_than_are_shown_at_most(self):
        """When more cultures grew than the limit, all of them are shown
        and every culture that grew nothing is counted."""
        found = Answer("Did the blood culture grow anything?",
                       context_with(many_cultures(31, 3)))
        kinds = [statement.kind for statement in found.candidate.statements]
        self.assertEqual(kinds.count("record_growth_item"), 31)
        self.assertNotIn("record_growth_negative", kinds)
        self.assertEqual([statement.slots.earlier
                          for statement in found.candidate.statements
                          if statement.notice == "values_omitted"], [3])
        self.assertReleased(found)

    def test_the_latest_culture_differs_from_the_first(self):
        """The latest culture grew nothing while the first grew."""
        context = context_with(many_cultures(1, 2))
        latest = Answer("Was the latest blood culture positive?", context)
        self.assertEqual(latest.lines(), [
            "No organism is recorded in the results of the latest BLOOD "
            "CULTURE within all case visits, including the final admission.",
            "Latest Blood Culture, Routine (BLOOD CULTURE):",
            "- No organism recorded, 1 Jun 2151 02:02, in visit 3"])
        self.assertReleased(latest)
        first = Answer("Was the first blood culture positive?", context)
        self.assertEqual(first.lines()[:2], [
            "An organism is recorded in the results of the first BLOOD "
            "CULTURE within all case visits, including the final admission.",
            "First Blood Culture, Routine (BLOOD CULTURE):"])
        self.assertReleased(first)
        every = Answer("Was the blood culture positive?", context)
        self.assertEqual(every.lines()[0],
                         "An organism is recorded in the results of BLOOD "
                         "CULTURE within all case visits, including the "
                         "final admission.")
        self.assertReleased(every)

    def test_tests_of_a_kind_each_once(self):
        """A question about every culture gives each test once, by its
        earliest record, whether or not it grew."""
        rows = mixed_culture() + [(2, culture(9700, "2151-06-02 08:00:00",
                                              URINE))]
        found = Answer("Were any cultures negative?", context_with(rows))
        headers = [line for line in found.lines() if line.endswith(":")]
        self.assertEqual(headers, ["Blood Culture, Routine (BLOOD CULTURE):",
                                   "URINE CULTURE (URINE):"])
        self.assertEqual(found.lines()[0],
                         "An organism is recorded in the results of "
                         "microbiology tests within all case visits, "
                         "including the final admission.")
        self.assertReleased(found)

    def test_no_culture_grew(self):
        """Without an organism in any culture, the answer says so."""
        found = Answer("Any positive cultures?",
                       context_with(many_cultures(0, 2)))
        self.assertEqual(found.lines()[0],
                         "No organism is recorded in the results of "
                         "microbiology tests within all case visits, "
                         "including the final admission.")
        self.assertReleased(found)

    def test_the_gram_stain_is_part_of_the_name(self):
        """"Positive" stays in a name, so gram positive cocci are not the
        gram negative diplococci "gram cocci" also names, and the Gram
        stain at the end of a question names organisms rather than asking
        how a result reads (record-questions-v9, rule 19)."""
        context = context_with(gram_cultures(), GRAM)
        cocci = Answer("Did any culture grow gram positive cocci?", context)
        self.assertEqual(cocci.lines(), [
            "An organism is recorded in the results of GRAM POSITIVE "
            "COCCUS(COCCI) within all case visits, including the final "
            "admission.",
            "Blood Culture, Routine (BLOOD CULTURE):",
            "- GRAM POSITIVE COCCUS(COCCI), 10 Jan 2150 10:00, in visit 1: "
            "sensitive to VANCOMYCIN"])
        self.assertReleased(cocci)
        negative = Answer("Was the culture gram negative?", context)
        self.assertEqual(negative.lines(), [
            "GRAM NEGATIVE DIPLOCOCCI; GRAM NEGATIVE ROD(S) is recorded "
            "within all case visits, including the final admission: 2 "
            "matching records.",
            "- Blood Culture, Routine (BLOOD CULTURE) (2 records in visit "
            "2; matched by the words of its name)"])
        self.assertReleased(negative)

    def test_positive_within_a_name_is_never_left_out(self):
        """A name holding "positive" that the dictionary lacks is asked
        back, never answered as the name without it (coagulase negative
        staphylococci), and two Gram stains at once are asked one at a
        time."""
        context = context_with(gram_cultures(), GRAM)
        for question, reason in (
                ("Did any culture grow coagulase positive staph?",
                 "concept_unknown"),
                ("Was the culture gram positive or negative?",
                 "task_ambiguous")):
            result = answer.record_result(question, cases.CASE, cases.ALL,
                                          cases.Visits(), VOCAB, "q-1",
                                          context)
            self.assertEqual((result.response.outcome,
                              tuple(result.response.reason_codes)),
                             ("clarification", (reason,)), question)

    def test_a_culture_asked_about_by_its_organism(self):
        """An organism named in the question: its own records, without
        the other organism or the record without one."""
        found = Answer("Did any culture grow e coli?",
                       context_with(mixed_culture()))
        self.assertEqual(found.lines(), [
            "An organism is recorded in the results of ESCHERICHIA COLI "
            "within all case visits, including the final admission.",
            "Blood Culture, Routine (BLOOD CULTURE):",
            "- ESCHERICHIA COLI, 10 Jan 2150 10:00, in visit 1: resistant "
            "to AMPICILLIN; intermediate to GENTAMICIN; sensitive to "
            "CEFTRIAXONE; pending for MEROPENEM; tested without an "
            "interpretation against TOBRAMYCIN"])
        self.assertReleased(found)


if __name__ == "__main__":
    unittest.main()
