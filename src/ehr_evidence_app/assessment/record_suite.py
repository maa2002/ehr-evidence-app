"""A generated development suite of questions about the whole record
(ADR 0016, decision 11; work item 60).

Every scope of every admitted development case is asked questions in
fixed wordings about each kind of record: lists and counts of a whole
kind, and for up to ITEMS of the scope's own items of each kind, their
presence, time and count, and for results their values and flags. The
visits' and the person's details are asked about too.

An item is named in a question by its records' name, as the references
read the records (assessment.record_references): a laboratory item's
label, a vital sign's group, a condition's concept name, a procedure's
concept name or ICU item's label, a medication's one ingredient (or the
drug its prescription writes, for a record without a standard concept),
a microbiology test's name and a care unit. The item meant is every item
of the kind with that name: a label two laboratory items share names
both; a drug name, every drug concept the vocabulary names so by a name
or a synonym (an ingredient and the ATC substance of the same name), and
the records without a standard concept whose written drug holds it; a
procedure's name, the records whose names hold its words.

Each question is assessed end to end (assessment.end_to_end) and counted
by its wording and stage. It is a development check of how the
application reads, resolves, retrieves and answers the records' own
names in fixed wordings, not a measurement. The default mode asks the
invented case; the operator mode refuses the evaluation set and prints
counts only, never a name, a value or a record.

Usage:
    python -m ehr_evidence_app.assessment.record_suite
    python -m ehr_evidence_app.assessment.record_suite --operator-real \\
        --case-set dev-30-v1
"""

from __future__ import annotations

import argparse
import collections
import sys
import typing

from ehr_evidence_app.assessment import end_to_end
from ehr_evidence_app.assessment import record_references as refs
from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.qa import resolve as vocabulary

ITEMS = 2


class Form(typing.NamedTuple):
    """A fixed wording of a question, and what it asks; {name} stands for
    an item's name."""

    task: str
    kind: str
    wording: str
    selector: str | None = None
    field: str | None = None

    @property
    def label(self):
        """Return how the wording is counted."""
        return "%s %s: %s" % (self.task, self.kind, self.wording)


FORMS = (
    Form("record_list", "lab", "What labs were done?"),
    Form("record_list", "medication", "What medications were given?"),
    Form("record_list", "procedure", "What procedures were done?"),
    Form("record_list", "microbiology", "What microbiology was done?"),
    Form("record_list", "vital", "What vital signs were charted?"),
    Form("record_list", "transfer", "Which units was the patient in?"),
    Form("record_count", "lab", "How many labs were done?"),
    Form("record_count", "medication", "How many medications were given?"),
    Form("record_count", "icu_stay", "How many ICU stays were there?"),
    Form("record_abnormal", "lab", "Were any labs abnormal?"),
    Form("record_values", "vital", "What were all the vital sign values?",
         "all"),
    Form("record_presence", "lab", "Was {name} measured?"),
    Form("record_timing", "lab", "When was {name} first measured?"),
    Form("record_count", "lab", "How many {name} results are there?"),
    Form("record_values", "lab", "What was the last {name}?", "latest"),
    Form("record_values", "lab", "What was the first {name}?", "first"),
    Form("record_values", "lab", "What was the highest {name}?",
         "highest"),
    Form("record_values", "lab", "What was the lowest {name}?", "lowest"),
    Form("record_values", "lab", "What were all the {name} values?", "all"),
    Form("record_abnormal", "lab", "Was the {name} abnormal?"),
    Form("record_values", "vital", "What was the last {name}?", "latest"),
    Form("record_values", "vital", "What was the highest {name}?",
         "highest"),
    Form("record_values", "vital", "What were all the {name} values?",
         "all"),
    Form("record_presence", "medication", "Was {name} given?"),
    Form("record_timing", "medication", "When was {name} first given?"),
    Form("record_count", "medication", "How many times was {name} given?"),
    Form("record_timing", "condition", "When was {name} first recorded?"),
    Form("record_count", "condition",
         "How many times was {name} recorded?"),
    Form("record_presence", "procedure", "Was {name} done?"),
    Form("record_timing", "procedure", "When was {name} done?"),
    Form("record_presence", "microbiology", "Was a {name} taken?"),
    Form("record_timing", "microbiology", "When was the {name} taken?"),
    Form("record_presence", "transfer", "Was the patient in {name}?"),
    Form("visit_details", "visit", "How many visits were there?",
         field="count"),
    Form("visit_details", "visit", "When was the patient admitted?",
         field="admitted"),
    Form("visit_details", "visit", "When was the patient discharged?",
         field="discharged"),
    Form("visit_details", "visit", "How long was each stay?",
         field="length"),
    Form("visit_details", "visit", "What type of admission was it?",
         field="type"),
    Form("visit_details", "visit", "Where was the patient admitted from?",
         field="admitted_from"),
    Form("visit_details", "visit", "Where was the patient discharged to?",
         field="discharged_to"),
    Form("person_details", "person", "What is the patient's sex?",
         field="sex"),
    Form("person_details", "person", "How old is the patient?",
         field="age"),
    Form("person_details", "person", "What is the patient's race?",
         field="race"),
    Form("person_details", "person", "What language does the patient "
         "speak?", field="language"),
    Form("person_details", "person", "What insurance does the patient "
         "have?", field="insurance"),
    Form("person_details", "person", "Is the patient married?",
         field="marital_status"),
    Form("person_details", "person", "Did the patient die?",
         field="death"),
)


class RecordSuiteError(Exception):
    """A fail-closed failure carrying a finding code only."""


class Namer:
    """Names a record's item as a question would, with the identifiers of
    every item of its kind named so."""

    def __init__(self, extract, snapshot, drugs):
        """Hold the dictionaries: the extract's laboratory and ICU items,
        the snapshot's concepts and the medication vocabulary."""
        self.labs = extract.labs
        self.items = extract.items
        self.concepts = snapshot.concepts
        self.drugs = drugs
        self.lab_codes = collections.defaultdict(set)
        for itemid, row in self.labs.items():
            self.lab_codes[refs.normalize(row.get("label"))].add(itemid)
        # Every drug concept by its name; a brand name names its
        # ingredients.
        self.drug_codes = collections.defaultdict(set)
        for concept in (drugs.drugs.values() if drugs is not None else ()):
            self.drug_codes[refs.normalize(concept.name)] |= (
                set(drugs.brands.get(concept.concept_id, ()))
                if concept.kind == "brand" else {concept.concept_id})

    def concept_name(self, concept):
        """Return a concept's name in the snapshot, or None."""
        row = self.concepts.get(concept) if concept else None
        return row.name if row is not None and row.name else None

    def __call__(self, row):
        """Return (name, identifiers) of a record's item, or None when
        the suite does not name it."""
        if row.kind == "lab":
            name = (self.labs.get(row.item[2]) or {}).get("label")
            key = refs.normalize(name)
            return ((name, {"codes": frozenset(self.lab_codes[key])})
                    if key else None)
        if row.kind == "vital":
            return ((row.group.replace("_", " "),
                     {"groups": frozenset((row.group,))})
                    if row.group else None)
        if row.kind == "condition":
            name = self.concept_name(row.concept)
            return (name, {"concepts": frozenset((row.concept,))}) \
                if name else None
        if row.kind == "medication":
            return self.medication(row)
        if row.kind == "procedure":
            name = self.concept_name(row.concept)
        elif row.kind == "icu_procedure":
            name = (self.items.get(row.item[2]) or {}).get("label")
        else:
            name = row.item[2]
        words = tuple(refs.normalize(name).split())
        return (name, {"words": (words,)}) if words else None

    def medication(self, row):
        """Return a drug record's one ingredient, or for a record without a
        standard concept the drug its prescription writes. A drug's name
        means the concepts the vocabulary names so, by a name or a
        synonym, and the records without a standard concept whose written
        drug holds it, as the references match them."""
        if not row.concept:
            name, found = row.written, set()
        else:
            ingredients = [
                concept for concept in sorted(row.ancestors)
                if self.drugs is not None and concept in self.drugs.drugs
                and self.drugs.drugs[concept].kind == "ingredient"]
            if len(ingredients) != 1:
                return None
            name, found = (self.drugs.drugs[ingredients[0]].name,
                           set(ingredients))
        words = tuple(refs.normalize(name).split())
        if not words:
            return None
        if self.drugs is not None:
            found |= self.drug_codes[refs.normalize(name)] | set(
                self.drugs.drug_names.get(vocabulary.normalize(name), ()))
        return name, {"concepts": frozenset(found), "written": (words,)}


def items_of(case, scope, kind, namer):
    """Return up to ITEMS (name, identifiers) of a kind's items in a scope,
    in the order of their first record, each name once."""
    found = {}
    for row in sorted(case.scoped(scope, kind), key=lambda row: row.order):
        named = namer(row)
        if named is None:
            continue
        key = refs.normalize(named[0])
        if key and key not in found:
            found[key] = named
            if len(found) == ITEMS:
                break
    return list(found.values())


def questions_of(case_set, case, scope, namer):
    """Return (form, row) of every generated question about a scope."""
    found = []
    for form in FORMS:
        named = ([(None, {})] if "{name}" not in form.wording
                 else items_of(case, scope, form.kind, namer))
        for name, identifiers in named:
            question = form.wording.format(name=(name or "").lower())
            asked = refs.Asked(form.task, form.kind, selector=form.selector,
                               field=form.field, **identifiers)
            found.append((form, end_to_end.RecordRow(
                "g", case_set, case.case.case_number, scope, question,
                "answer", asked)))
    return found


def assess(case_set, inputs, scopes_of, namer, limits):
    """Return the Assessed stages of every generated question, each
    counted under its wording."""
    found = []
    number = 0
    for case_number in sorted(inputs.cases):
        case = inputs.cases[case_number]
        for scope in scopes_of(case_number):
            for form, row in questions_of(case_set, case, scope, namer):
                number += 1
                item = end_to_end.assess_record_row(
                    row, inputs, limits, "g-%06d" % number)
                found.append(item._replace(intent=form.label)
                             if item is not None else None)
    return found


def summary(found):
    """Return the questions, and those right at every stage they reach,
    by stage: counts only."""
    counts = collections.Counter()
    for item in found:
        if item is None:
            continue
        counts["questions"] += 1
        counts["read right"] += item.interpretation == "right"
        counts["resolved exactly"] += item.resolution == "exact"
        counts["delivered correctly"] += item.delivery == "correct"
        counts["delivered apart"] += item.delivery == "apart"
    return dict(counts)


# Default And Operator Modes

# The invented case's suite: every question read right is delivered
# correctly. The invented procedure dictionary holds neither invented
# procedure's name, so the questions naming them are declined; the drug
# record without a standard concept writes no drug and is not named.
SYNTHETIC_EXPECTED = {"questions": 201, "read right": 191,
                      "resolved exactly": 66, "delivered correctly": 191,
                      "delivered apart": 0}
SYNTHETIC_DECLINED = {
    "record_presence procedure: Was {name} done?": 5,
    "record_timing procedure: When was {name} done?": 5}


def synthetic_inputs():
    """Return (case set, inputs, scopes, namer, limits) of the invented
    case."""
    from ehr_evidence_app.evidence import record_bundles
    from ehr_evidence_app.validation import record_support
    rows, inputs, limits = end_to_end.synthetic_record_inputs()
    label = end_to_end.SYNTHETIC_LABEL
    snapshot, _, extract, _ = record_bundles.synthetic_sources()
    namer = Namer(extract, snapshot, record_support.synthetic_drugs())
    case_inputs = inputs[label]

    def scopes_of(case_number):
        """Return the invented case's scopes."""
        count = case_inputs.cases[case_number].count
        return ([contracts.ScopeRef(kind="pre_target_history")]
                + [contracts.ScopeRef(kind="selected_visit",
                                      visit_number=number)
                   for number in range(1, count + 1)]
                + [contracts.ScopeRef(kind="case_visits")])
    return label, case_inputs, scopes_of, namer, limits


def run_synthetic():
    """Return (tally, summary, problems) of the invented case's suite."""
    label, inputs, scopes_of, namer, limits = synthetic_inputs()
    found = assess(label, inputs, scopes_of, namer, limits)
    counts, _, _ = end_to_end.tally(found)
    problems = []
    if summary(found) != SYNTHETIC_EXPECTED:
        problems.append("the invented case's suite ends unexpectedly")
    declined = {label: stages["interpretation"]["declined"]
                for label, stages in counts.items()
                if stages["interpretation"].get("declined")}
    if declined != SYNTHETIC_DECLINED or any(
            set(stages["interpretation"]) - {"right", "declined"}
            for stages in counts.values()):
        problems.append("the invented case's suite reads a wording "
                        "otherwise than expected")
    return counts, summary(found), problems


def run_real(case_set):
    """Assess the generated suite on a development case set; return the
    tally and the summary."""
    from ehr_evidence_app.api import service as services
    from ehr_evidence_app.casesets import registry
    from ehr_evidence_app.evidence import readers, record_bundles
    from ehr_evidence_app.qa import record_vocabulary
    if case_set != readers.PILOT_LABEL and registry.spec(case_set)[
            "purpose"] != "development":
        raise RecordSuiteError("case_set_held_out")
    service = services.Service.from_workspace((case_set,))
    service.recorder = None
    source = service.sources[case_set]
    drugs = record_vocabulary.load()
    ancestors = {concept: frozenset(found)
                 for concept, found in drugs.ancestors.items()}
    plan = readers.real_plan(None if case_set == readers.PILOT_LABEL
                             else case_set)
    snapshot = readers.read_snapshot(plan)
    cases = {case.case_number: case
             for case in readers.read_cases(plan).cases}
    tables, _ = record_bundles.read_extract(
        record_bundles.real_plan(case_set), [])
    extract = record_bundles.Extract(tables)
    inputs = end_to_end.RecordInputs(
        source.bundle_set, source.records,
        {number: refs.Case(cases[number], snapshot, extract, ancestors)
         for number in sorted(source.cases)}, service.vocabulary)
    found = assess(case_set, inputs,
                   lambda number: service.scopes(source, number),
                   Namer(extract, snapshot, drugs), service.limits)
    counts, left_out, _ = end_to_end.tally(found)
    return counts, left_out, summary(found)


def main(argv=None):
    """Run the synthetic default mode or the operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="assess the suite on a development case set; "
                             "prints counts only")
    parser.add_argument("--case-set", default="dev-30-v1")
    args = parser.parse_args(argv)
    try:
        if args.operator_real:
            counts, left_out, totals = run_real(args.case_set)
            end_to_end.report(counts, left_out)
            print("summary %s" % ", ".join("%s %d" % item
                                           for item in totals.items()))
        else:
            counts, totals, problems = run_synthetic()
            end_to_end.report(counts, 0)
            print("summary %s" % ", ".join("%s %d" % item
                                           for item in totals.items()))
            for problem in problems:
                print("FAILED: %s" % problem)
            if problems:
                return 1
    except (RecordSuiteError, refs.RecordReferenceError) as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
