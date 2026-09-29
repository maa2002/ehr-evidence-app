"""Mutation tests of the checks of answers about the whole record.

Correct answers about the invented record (tests.qa.record_cases), of
every task and outcome, are broken one fact at a time: a statement left
out or repeated, a count, selector, match kind, field, kind, visit, value,
time or named item changed, a reference dropped, added or swapped for a
record of another item, a notice replaced or added, the outcome, the
reason codes, the scope, the case or the bundle changed, a summary moved.
A writer that is not the templates can make each of these mistakes; the
gate must withhold every one (a validation rejection, or a document that
does not parse), and must release every original and every control (a
list or presence item named by another of its own records).
"""

import unittest

from pydantic import ValidationError

from ehr_evidence_app.evidence import contracts, record
from ehr_evidence_app.qa import answer, record_answer, record_interpret, suite
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import gate, record_support
from tests.qa import record_cases as cases

VISIT = {number: contracts.ScopeRef(kind="selected_visit",
                                    visit_number=number)
         for number in (1, 2, 3)}
# (question, scope): every task and outcome of an answer about the record.
SAMPLES = (
    ("What labs were done?", cases.ALL),
    ("What vital signs were charted?", cases.ALL),
    ("What vital signs were charted?", cases.HISTORY),
    ("Was she in the ICU?", cases.ALL),
    ("Which units was the patient in?", cases.ALL),
    ("What medications were given?", VISIT[2]),
    ("Was heparin given?", cases.ALL),
    ("Any antibiotics?", cases.ALL),
    ("Any antibiotics?", cases.HISTORY),
    ("Was a chest x-ray done?", cases.ALL),
    ("Did she have a CT?", VISIT[3]),
    ("Was the heart rate measured?", cases.HISTORY),
    ("When was potassium first measured?", cases.ALL),
    ("When was creatinine measured?", cases.ALL),
    ("When was troponin measured?", cases.HISTORY),
    ("How many times was heparin given?", cases.ALL),
    ("How many medications?", cases.ALL),
    ("How many ICU stays?", cases.HISTORY),
    ("What was the last potassium?", cases.ALL),
    ("What was the highest troponin?", cases.ALL),
    ("What was the highest glucose?", cases.ALL),
    ("What was the last creatinine?", cases.ALL),
    ("What were all the potassium values?", cases.ALL),
    ("Were any vitals high?", cases.ALL),
    ("What organisms grew?", cases.ALL),
    ("What doses were given?", cases.ALL),
    ("What was the last troponin?", cases.HISTORY),
    ("Were any labs abnormal?", cases.ALL),
    ("Were any cultures resistant?", cases.ALL),
    ("Was the creatinine high?", cases.ALL),
    ("Were any cultures resistant?", cases.HISTORY),
    ("How many visits?", cases.ALL),
    ("How long was she in hospital?", cases.ALL),
    ("When was she admitted?", cases.HISTORY),
    ("How old was the patient?", cases.ALL),
    ("Did the patient die?", cases.ALL),
    ("What is the patient's sex?", VISIT[1]),
    ("What is her race?", cases.ALL),
)
COUNTS = ("count", "records", "matches", "total", "earlier", "unmapped")
SELECTORS = ("latest", "first", "highest", "lowest", "only")
MATCHES = ("item", "group", "ingredient", "class", "brand", "name", "words",
           "exact", "more_specific", "icd_code")
FIELDS = ("sex", "age", "race", "death", "admitted", "length", "summary",
          "hospital_death", "insurance")
KINDS = ("condition", "procedure", "medication", "lab", "microbiology",
         "vital", "transfer", "icu_stay")
VOCAB = suite.synthetic_vocabulary()
CONTEXT = cases.context()


class Sample:
    """A released answer and what it is checked against."""

    def __init__(self, question, scope):
        """Answer a question and hold its candidate, bundle and check."""
        result = answer.record_result(question, cases.CASE, scope,
                                      cases.Visits(), VOCAB, "q-1", CONTEXT)
        self.question = question
        self.response = result.response
        self.candidate = result.candidate
        self.scope = result.candidate.scope
        self.bundle, self.digest = CONTEXT.records.bundle(1, self.scope)
        reading = record_interpret.interpret(question, CONTEXT.terms)
        self.check = record_support.RecordCheck(
            CONTEXT.terms, record_answer.resolution_of(CONTEXT, reading),
            reading)

    def outcome(self, candidate):
        """Return the outcome the gate gives a candidate as a writer's
        document: structure_invalid when it does not parse."""
        try:
            candidate = candidates.parse(candidate.model_dump(mode="json"))
        except ValidationError:
            return "structure_invalid"
        return gate.release(candidate, "q-1", cases.CASE, self.scope,
                            self.bundle, self.digest, VOCAB,
                            record=self.check).outcome


def replaced(candidate, index, statement):
    """Return a candidate with one statement replaced."""
    statements = list(candidate.statements)
    statements[index] = statement
    return candidate.model_copy(update={"statements": tuple(statements)})


def with_slots(candidate, index, **changes):
    """Return a candidate with one statement's slots changed."""
    statement = candidate.statements[index]
    return replaced(candidate, index, statement.model_copy(update={
        "slots": statement.slots.model_copy(update=changes)}))


def with_refs(candidate, index, refs):
    """Return a candidate with one statement's references changed."""
    statement = candidate.statements[index]
    return replaced(candidate, index,
                    statement.model_copy(update={"refs": tuple(refs)}))


def other_item(bundle, ref, exclude=()):
    """Return a record of another item than a record's, of its kind when
    the scope has one, not among exclude."""
    entries = {entry.ref: entry for entry in bundle.records}
    key = record.item_key(entries[ref])
    others = [entry for entry in bundle.records
              if record.item_key(entry) != key and entry.ref not in exclude]
    same = [entry for entry in others if entry.kind == entries[ref].kind]
    return (same or others)[0].ref


def mutants(sample):
    """Return (label, mutant) pairs of a sample's candidate."""
    good = sample.candidate
    bundle = sample.bundle
    statements = good.statements
    visits = [visit.ref for visit in bundle.visits]
    cited = {ref for statement in statements for ref in statement.refs}
    notices = [statement.notice for statement in statements
               if statement.kind == "notice"]
    found = []
    for index, statement in enumerate(statements):
        label = "%s[%d]" % (statement.notice or statement.kind, index)
        found.append(("drop " + label, good.model_copy(update={
            "statements": statements[:index] + statements[index + 1:]})))
        found.append(("repeat " + label, good.model_copy(update={
            "statements": statements[:index + 1] + statements[index:]})))
        found.append(("digit in " + label, replaced(
            good, index, statement.model_copy(update={
                "wording": statement.wording + " 2"}))))
        slots = statement.slots
        for name in COUNTS:
            value = getattr(slots, name)
            if value is not None:
                found.append(("%s %s + 1" % (label, name),
                              with_slots(good, index, **{name: value + 1})))
        if slots.items is not None:
            found.append((label + " items + 1", with_slots(
                good, index, items=slots.items.model_copy(
                    update={"count": slots.items.count + 1}))))
            found.append((label + " items of another kind", with_slots(
                good, index, items=slots.items.model_copy(update={
                    "kind": next(kind for kind in KINDS
                                 if kind != slots.items.kind)}))))
        if slots.kind is not None:
            found.append((label + " another kind", with_slots(
                good, index, kind=next(kind for kind in KINDS
                                       if kind != slots.kind))))
        for name, values in (("selector", SELECTORS), ("how", MATCHES),
                             ("field", FIELDS)):
            value = getattr(slots, name)
            if value is None:
                continue
            for other in values:
                if other != value:
                    found.append(("%s %s %s" % (label, name, other),
                                  with_slots(good, index, **{name: other})))
        if slots.visit is not None and len(visits) > 1:
            other = next(ref for ref in visits if ref != slots.visit)
            found.append((label + " another visit",
                          with_slots(good, index, visit=other)))
            if statement.kind == "visit_fact":
                found.append((label + " another visit, cited", replaced(
                    good, index, statement.model_copy(update={
                        "refs": (other,), "slots": slots.model_copy(
                            update={"visit": other})}))))
        for name in ("value", "when"):
            value = getattr(slots, name)
            if value is not None:
                found.append(("%s %s of another record" % (label, name),
                              with_slots(good, index, **{
                                  name: other_item(bundle, value)})))
        if slots.item is not None and slots.item.record is not None:
            found.append((label + " named by another item", with_slots(
                good, index, item=candidates.ItemName(
                    record=other_item(bundle, slots.item.record)))))
        if slots.item is not None and slots.item.terms:
            found.append((label + " another term", with_slots(
                good, index, item=candidates.ItemName(
                    terms=slots.item.terms + ("another item",)))))
        if slots.scope is not None:
            other = next(scope for scope in cases.SCOPES
                         if scope != slots.scope)
            found.append((label + " another scope",
                          with_slots(good, index, scope=other)))
        refs = statement.refs
        if refs and refs[0].startswith("R"):
            if len(refs) > 1:
                found.append((label + " a reference left out",
                              with_refs(good, index, refs[:-1])))
            found.append((label + " a record of another item added",
                          with_refs(good, index, refs + (other_item(
                              bundle, refs[0], exclude=cited),))))
            found.append((label + " a record of another item instead",
                          with_refs(good, index, (other_item(
                              bundle, refs[0]),) + refs[1:])))
        if statement.kind == "notice":
            for notice_id, notice in candidates.NOTICES.items():
                if (notice_id not in notices
                        and good.outcome in notice.outcomes
                        and notice.answers(good.task)
                        and not candidates.placeholders(notice.text)):
                    found.append(("%s replaced by %s" % (label, notice_id),
                                  replaced(good, index,
                                           candidates.notice(notice_id))))
    for notice_id, notice in candidates.NOTICES.items():
        if (notice_id not in notices and good.outcome in notice.outcomes
                and notice.answers(good.task)
                and not candidates.placeholders(notice.text)):
            found.append(("notice %s added" % notice_id, good.model_copy(
                update={"statements": statements + (
                    candidates.notice(notice_id),)})))
    flipped = {"answer": "not_recorded", "not_recorded": "answer"}
    found.append(("outcome " + flipped[good.outcome], good.model_copy(
        update={"outcome": flipped[good.outcome]})))
    disclosed = "unmapped_records_in_scope" in good.reason_codes
    found.append(("disclosure toggled", good.model_copy(update={
        "reason_codes": () if disclosed
        else ("unmapped_records_in_scope",)})))
    found.append(("another case", good.model_copy(update={
        "case": good.case.model_copy(update={
            "case_number": good.case.case_number % 999 + 1})})))
    found.append(("another bundle digest", good.model_copy(update={
        "bundle": good.bundle.model_copy(update={"sha256": "0" * 64})})))
    first = statements[0]
    body = [index for index, statement in enumerate(statements)
            if statement.kind != "notice"]
    if first.kind in ("record_list_summary", "record_presence_summary",
                      "record_abnormal_summary", "visit_summary",
                      "record_value_group") and len(body) > 1:
        moved = statements[1:body[-1] + 1] + (first,) + statements[
            body[-1] + 1:]
        found.append(("summary moved after its items", good.model_copy(
            update={"statements": moved})))
    return found


def controls(sample):
    """Return (label, control) pairs that must be released: a list or
    presence item named by another of its own records."""
    found = []
    for index, statement in enumerate(sample.candidate.statements):
        if statement.kind in ("record_list_item", "record_presence_item") \
                and len(statement.refs) > 1:
            found.append(("%s[%d] named by its own other record" % (
                statement.kind, index), with_slots(
                sample.candidate, index,
                item=candidates.ItemName(record=statement.refs[1]))))
    return found


class RecordMutationTests(unittest.TestCase):
    """Every mutant is withheld; every original and control released."""

    @classmethod
    def setUpClass(cls):
        """Answer every sample question once."""
        cls.samples = [Sample(question, scope) for question, scope in SAMPLES]

    def test_the_samples_cover_every_task_and_outcome(self):
        """The samples are released answers of every task and outcome."""
        found = {(sample.response.task, sample.response.outcome)
                 for sample in self.samples}
        for task in ("record_list", "record_presence", "record_timing",
                     "record_count", "record_values", "record_abnormal"):
            for outcome in ("answer", "not_recorded"):
                self.assertIn((task, outcome), found)
        self.assertIn(("visit_details", "answer"), found)
        self.assertIn(("person_details", "answer"), found)
        kinds = {statement.kind for sample in self.samples
                 for statement in sample.candidate.statements}
        self.assertEqual(kinds - {"notice"}, set(candidates.KINDS) - {
            "notice", "list_summary", "list_item", "list_unmapped",
            "list_empty", "presence_summary", "presence_item",
            "presence_absent", "presence_unmapped", "prediction", "driver",
            "driver_history", "driver_context"})

    def test_every_original_is_released(self):
        """The templates' answers pass every check."""
        for sample in self.samples:
            self.assertEqual(sample.outcome(sample.candidate),
                             sample.response.outcome, sample.question)

    def test_every_mutant_is_withheld(self):
        """Each broken answer is a validation rejection, or does not
        parse."""
        missed, total = [], 0
        for sample in self.samples:
            for label, mutant in mutants(sample):
                total += 1
                if sample.outcome(mutant) not in ("validation_rejection",
                                                  "structure_invalid"):
                    missed.append("%s [%s]: %s" % (
                        sample.question, sample.scope.kind, label))
        self.assertEqual(missed, [])
        self.assertGreater(total, 1400)

    def test_every_control_is_released(self):
        """An item named by another of its own records is the same item."""
        count = 0
        for sample in self.samples:
            for label, control in controls(sample):
                count += 1
                self.assertEqual(sample.outcome(control),
                                 sample.response.outcome,
                                 "%s: %s" % (sample.question, label))
        self.assertGreater(count, 5)


if __name__ == "__main__":
    unittest.main()
