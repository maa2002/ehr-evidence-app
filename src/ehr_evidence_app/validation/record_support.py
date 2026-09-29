"""Support of answers about the record by what they cite.

An answer about the record (qa.record_answer) names records, visits and
the person by reference, and its text is rendered from them, so no value,
time, date or age is typed. What these checks decide is whether the
right records were chosen and whether none was left out; any failure is
claim_unsupported:

* a list names every item of the kind within the scope once and cites
  each of its records once; an item's count and visits are its records';
  its summary counts the items and the records; an empty list needs no
  record of the kind in the scope;
* a presence answer cites every record that matches the question's
  phrase, each by the match kind the resolver finds
  (qa.record_resolve), and names the phrase's resolution; "not
  recorded" needs no matching record;
* a time answer's first and last records are the earliest and the latest
  matching records, and its count is theirs;
* a count is the number of matching records, or of the kind's items and
  records, and cites them all;
* a value is the latest, first, highest or lowest of its item's values
  as its selector says; every matching item is answered once; values in
  time order are the latest of their item, the others counted;
* flagged results are every result of the matching items that the
  laboratory flagged abnormal, or that microbiology reported resistant;
* visits and the person's details cover every visit of the scope, each
  with the field the question asks about;
* the disclosure unmapped_records_in_scope is stated exactly when the
  scope holds records of the kind without a standard concept or a name;
  a "not recorded" about a named condition, medication or procedure
  states the scope's records of the kind without a standard concept, and
  their number, and an empty list states nothing else;
* a single record is stated as the only one, and the latest stands in
  for the highest or lowest only when no value of the item is a number;
* the notices are exactly those the question needs (validation.candidate
  ties each to its tasks; structure requires those of every task).

Usage:
    python -m ehr_evidence_app.validation.record_support
"""

from __future__ import annotations

import collections
import dataclasses

from ehr_evidence_app.evidence import record
from ehr_evidence_app.qa import record_resolve as resolving
from ehr_evidence_app.validation import candidate as candidates

MAX_VALUES = 30
VISIT_FIELDS = {"list": "summary", "count": "summary"}


@dataclasses.dataclass(frozen=True)
class RecordCheck:
    """What a record answer is checked against: the terms, the
    resolution of the question's phrase (None when it names no item)
    and the question's reading (qa.record_interpret.RecordQuestion)."""

    terms: resolving.Terms
    resolution: object
    question: object


def of_kind(candidate, kind):
    """Return a candidate's statements of one kind."""
    return [statement for statement in candidate.statements
            if statement.kind == kind]


def records_of(bundle, kind):
    """Return the scope's records of a question kind."""
    kinds = resolving.RECORD_KINDS[kind]
    return [entry for entry in bundle.records if entry.kind in kinds]


def visits_of(bundle, entries):
    """Return the sorted visit numbers of some records."""
    numbers = {visit.ref: visit.visit_number for visit in bundle.visits}
    return tuple(sorted({numbers[entry.visit_ref] for entry in entries}))


def timed(entry):
    """Return the order of a record in time."""
    return (entry.start or "", entry.native_id)


def expected_terms(check):
    """Return the names an answer must give what it is about."""
    question = check.question
    if check.resolution is None:
        from ehr_evidence_app.validation import record_text
        return (record_text.kind_word(question.kind, plural=True),)
    return tuple(name[:255] for name in check.resolution.names[:50])


def matches_of(check, bundle):
    """Return {ref: match kind} of the scope's matching records."""
    question = check.question
    entries = records_of(bundle, question.kind)
    if check.resolution is None:
        return {entry.ref: None for entry in entries}
    found = {}
    for entry in entries:
        kind = check.terms.match(check.resolution, entry)
        if kind:
            found[entry.ref] = kind
    return found


def cited_once(statements, wanted):
    """Return True when the statements cite exactly the wanted
    references, each once."""
    cited = [ref for statement in statements for ref in statement.refs]
    return len(cited) == len(set(cited)) and set(cited) == set(wanted)


def group_supported(statement, bundle, records):
    """Return True when a statement's records are all of the item it
    names, and its count and visits are theirs."""
    slots = statement.slots
    if slots.item is None or slots.item.record not in statement.refs:
        return False
    named = records[slots.item.record]
    cited = [records[ref] for ref in statement.refs]
    return (all(record.item_key(entry) == record.item_key(named)
                for entry in cited)
            and slots.count == len(cited)
            and tuple(slots.visits or ()) == visits_of(bundle, cited))


def list_supported(candidate, bundle, check, records):
    """Return True when a list names every item of the kind once."""
    entries = records_of(bundle, check.question.kind)
    if candidate.outcome == "not_recorded":
        return not entries and absence_named(candidate, check, bundle)
    summary = of_kind(candidate, "record_list_summary")[0].slots
    items = of_kind(candidate, "record_list_item")
    stays = of_kind(candidate, "record_stay_item")
    if summary.items.kind != check.question.kind or not (items or stays):
        return False
    if stays:
        if items or check.question.kind not in ("icu_stay", "transfer"):
            return False
        groups = len(stays)
        if not all(statement.refs == (statement.slots.item.record,)
                   and statement.slots.value == statement.refs[0]
                   for statement in stays):
            return False
    else:
        if not all(group_supported(statement, bundle, records)
                   for statement in items):
            return False
        keys = [record.item_key(records[statement.slots.item.record])
                for statement in items]
        if len(keys) != len(set(keys)):
            return False
        groups = len(items)
    return (cited_once(items + stays, [entry.ref for entry in entries])
            and summary.items.count == groups
            and summary.records == len(entries))


def presence_supported(candidate, bundle, check, records):
    """Return True when a presence answer cites every matching record."""
    matches = matches_of(check, bundle)
    if candidate.outcome == "not_recorded":
        return not matches and absence_named(candidate, check, bundle)
    summary = of_kind(candidate, "record_presence_summary")[0].slots
    items = of_kind(candidate, "record_presence_item")
    if summary.item.terms != expected_terms(check) or (
            summary.matches != len(matches)):
        return False
    named = []
    for statement in items:
        if not group_supported(statement, bundle, records) or any(
                matches.get(ref) != statement.slots.how
                for ref in statement.refs):
            return False
        named.append((record.item_key(records[statement.slots.item.record]),
                      statement.slots.how))
    return len(named) == len(set(named)) and cited_once(items, matches)


def unmapped_of(bundle, check):
    """Return how many records of the question's kind within the scope
    have no standard concept, when a "not recorded" about a named item
    must say how they were checked; 0 otherwise."""
    if check.resolution is None or (
            check.question.kind not in candidates.UNMAPPED_KINDS):
        return 0
    return sum(1 for entry in records_of(bundle, check.question.kind)
               if entry.concept_id == 0)


def absence_named(candidate, check, bundle):
    """Return True when "not recorded" names the question's kind or its
    phrase's resolution, and states the scope's records without a
    standard concept that were checked, when there are any."""
    empty = of_kind(candidate, "record_list_empty")
    absent = of_kind(candidate, "record_absent")
    unmapped = of_kind(candidate, "record_unmapped")
    expected = unmapped_of(bundle, check)
    if len(unmapped) != bool(expected) or (expected and (
            unmapped[0].slots.unmapped != expected
            or unmapped[0].slots.kind != check.question.kind)):
        return False
    if check.resolution is None:
        return (len(empty) == 1 and not absent
                and empty[0].slots.kind == check.question.kind)
    return (len(absent) == 1 and not empty
            and absent[0].slots.item.terms == expected_terms(check))


def timing_supported(candidate, bundle, check, records):
    """Return True when a time answer gives the earliest and the latest
    matching records."""
    matches = [records[ref] for ref in matches_of(check, bundle)]
    if candidate.outcome == "not_recorded":
        return not matches and absence_named(candidate, check, bundle)
    summary = of_kind(candidate, "record_presence_summary")[0].slots
    times = of_kind(candidate, "record_time")
    if summary.item.terms != expected_terms(check) or (
            summary.matches != len(matches)):
        return False
    ordered = sorted(matches, key=timed)
    expected = ([("only", ordered[0])] if len(ordered) == 1
                else [("first", ordered[0]), ("latest", ordered[-1])])
    if len(times) != len(expected):
        return False
    for statement, (selector, entry) in zip(times, expected):
        slots = statement.slots
        if (slots.selector != selector or statement.refs != (entry.ref,)
                or slots.item.record != entry.ref or slots.when != entry.ref
                or slots.visit != entry.visit_ref):
            return False
    return True


def count_supported(candidate, bundle, check, records):
    """Return True when a count is the number of matching records, or of
    the kind's items and records."""
    matches = matches_of(check, bundle)
    if candidate.outcome == "not_recorded":
        return not matches and absence_named(candidate, check, bundle)
    counts = of_kind(candidate, "record_count")
    kinds = of_kind(candidate, "record_count_kind")
    if check.resolution is None:
        if counts or len(kinds) != 1:
            return False
        slots = kinds[0].slots
        keys = {record.item_key(records[ref]) for ref in matches}
        return (slots.items.kind == check.question.kind
                and slots.items.count == len(keys)
                and slots.records == len(matches)
                and cited_once(kinds, matches))
    if kinds or len(counts) != 1:
        return False
    slots = counts[0].slots
    return (slots.item.terms == expected_terms(check)
            and slots.count == len(matches)
            and tuple(slots.visits or ()) == visits_of(
                bundle, [records[ref] for ref in matches])
            and cited_once(counts, matches))


def has_value(entry):
    """Return True when a record holds a value to show."""
    if entry.kind in ("lab", "vital", "icu_procedure"):
        return entry.measure is not None
    return True


def number(entry):
    """Return a record's value as a number, or None."""
    return entry.measure.number if entry.measure is not None else None


def selected(entries, selector):
    """Return the record a stated selector picks among one item's
    records, or None when the selector cannot be stated for them."""
    if selector == "only":
        return entries[0] if len(entries) == 1 else None
    if len(entries) == 1:
        # A single record is stated as the only one.
        return None
    if selector in ("highest", "lowest"):
        numbers = [entry for entry in entries if number(entry) is not None]
        if not numbers:
            return None
        return (max if selector == "highest" else min)(
            numbers, key=lambda entry: (number(entry), timed(entry)))
    if selector == "first":
        return min(entries, key=timed)
    return max(entries, key=timed)


def groups_of(entries):
    """Return records by item."""
    found = collections.defaultdict(list)
    for entry in entries:
        found[record.item_key(entry)].append(entry)
    return found


def value_lines_supported(statements, groups, records, limit=MAX_VALUES):
    """Return the count of values left out when value groups list, for
    every item, its latest values (at most limit) in time order, or None
    when they do not."""
    left = 0
    seen = []
    index = 0
    while index < len(statements):
        header = statements[index]
        if header.kind != "record_value_group":
            return None
        key = record.item_key(records[header.slots.item.record])
        entries = sorted(groups.get(key, ()), key=timed)
        index += 1
        lines = []
        while index < len(statements) and (
                statements[index].kind == "record_value_item"):
            lines.append(statements[index])
            index += 1
        shown = entries[-limit:]
        if (not entries or key in seen or header.slots.count != len(entries)
                or header.slots.item.record != entries[0].ref
                or [line.refs for line in lines]
                != [(entry.ref,) for entry in shown]
                or any(line.slots.value != line.refs[0]
                       or line.slots.when != line.refs[0]
                       or line.slots.visit != records[line.refs[0]].visit_ref
                       for line in lines)):
            return None
        seen.append(key)
        left += len(entries) - len(shown)
    if set(seen) != set(groups):
        return None
    return left


def values_supported(candidate, bundle, check, records):
    """Return True when every matching item's value is the one its
    selector says, or its values are listed in time order."""
    matches = [records[ref] for ref in matches_of(check, bundle)
               if has_value(records[ref])]
    if candidate.outcome == "not_recorded":
        return not matches and absence_named(candidate, check, bundle)
    groups = groups_of(matches)
    body = [statement for statement in candidate.statements
            if statement.kind != "notice"]
    omitted = [statement for statement in candidate.statements
               if statement.notice == "values_omitted"]
    if check.question.selector == "all":
        left = value_lines_supported(body, groups, records,
                                     shown_of(check))
        if left is None:
            return False
        return (bool(omitted) == bool(left)) and (
            not omitted or omitted[0].slots.earlier == left)
    if omitted:
        return False
    seen = set()
    for statement in body:
        if statement.kind != "record_value":
            return False
        slots = statement.slots
        entry = records.get(slots.item.record)
        key = record.item_key(entry) if entry else None
        if (entry is None or key in seen or key not in groups
                or statement.refs != (entry.ref,)
                or slots.value != entry.ref or slots.when != entry.ref
                or slots.visit != entry.visit_ref):
            return False
        expected = selected(groups[key], slots.selector)
        wanted = check.question.selector
        # A single record is the only one; the latest stands in for the
        # highest or lowest only when no value of the item is a number.
        allowed = {wanted, "only"}
        if wanted in ("highest", "lowest") and not any(
                number(item) is not None for item in groups[key]):
            allowed.add("latest")
        if (expected is None or expected.ref != entry.ref
                or slots.selector not in allowed):
            return False
        seen.add(key)
    return seen == set(groups)


def shown_of(check):
    """Return how many values of each item a list shows: MAX_VALUES for a
    named item and for microbiology, the latest one of each item of
    another whole kind."""
    return MAX_VALUES if check.resolution is not None or (
        check.question.kind == "microbiology") else 1


def flagged(entry):
    """Return True when a result is flagged abnormal or resistant."""
    if entry.kind == "lab":
        return entry.measure is not None and entry.measure.flag == (
            "abnormal")
    if entry.kind == "microbiology":
        return entry.microbe.interpretation == "R"
    return False


def abnormal_supported(candidate, bundle, check, records):
    """Return True when flagged results are exactly the flagged results
    of the matching items."""
    matches = [records[ref] for ref in matches_of(check, bundle)
               if flagged(records[ref])]
    if candidate.outcome == "not_recorded":
        absent = of_kind(candidate, "record_abnormal_absent")
        return (not matches and len(absent) == 1
                and absent[0].slots.item.terms == expected_terms(check))
    summary = of_kind(candidate, "record_abnormal_summary")[0].slots
    body = [statement for statement in candidate.statements
            if statement.kind not in ("notice", "record_abnormal_summary")]
    omitted = [statement for statement in candidate.statements
               if statement.notice == "values_omitted"]
    left = value_lines_supported(body, groups_of(matches), records,
                                 shown_of(check))
    return (summary.item.terms == expected_terms(check)
            and summary.matches == len(matches) and left is not None
            and bool(omitted) == bool(left)
            and (not omitted or omitted[0].slots.earlier == left))


def visits_supported(candidate, bundle, check):
    """Return True when every visit of the scope is given, in order, with
    the field asked about."""
    summary = of_kind(candidate, "visit_summary")[0].slots
    facts = of_kind(candidate, "visit_fact")
    field = VISIT_FIELDS.get(check.question.field, check.question.field)
    return (summary.total == len(bundle.visits)
            and [statement.slots.visit for statement in facts]
            == [visit.ref for visit in bundle.visits]
            and all(statement.slots.field == field for statement in facts))


def person_supported(candidate, bundle, check):
    """Return True when the person's details give the field asked about,
    for the person or for every visit of the scope."""
    field = check.question.field
    facts = of_kind(candidate, "person_fact")
    ages = of_kind(candidate, "person_age")
    visits = of_kind(candidate, "visit_fact")
    every = [visit.ref for visit in bundle.visits]
    if field in ("sex", "death"):
        if len(facts) != 1 or facts[0].slots.field != field or ages:
            return False
        if field == "sex":
            return not visits
        return ([statement.slots.visit for statement in visits] == every
                and all(statement.slots.field == "hospital_death"
                        for statement in visits))
    if facts:
        return False
    if field == "age":
        return (not visits and [statement.slots.visit for statement in ages]
                == every and all(statement.slots.field == "age"
                                 for statement in ages))
    return (not ages and [statement.slots.visit for statement in visits]
            == every and all(statement.slots.field == field
                             for statement in visits))


def expected_notices(candidate, check):
    """Return the notices a record answer states, by its question: that
    "not recorded" is not clinical absence, that a list is complete, that
    vital signs are charted in ICU stays, that dates are shifted, what an
    age or a date of death means, that an unflagged result is not stated
    to be normal, and that vital signs carry no flag. Whether earlier
    values were left out (values_omitted) is checked with the values."""
    question = check.question
    task, outcome = candidate.task, candidate.outcome
    vital = {"vitals_icu_only"} if question.kind == "vital" else set()
    flag = {"not_flagged"} if question.kind == "lab" else set()
    if outcome == "not_recorded":
        return {"absence"} | (flag if task == "record_abnormal" else vital)
    if task == "record_list":
        return {"record_list_complete"} | vital
    if task == "record_timing":
        return {"dates_shifted"}
    if task == "record_values":
        return vital | ({"vital_no_flag"} if question.kind == "vital"
                        and question.category == "vital_flag"
                        and question.selector in ("highest", "lowest")
                        else set())
    if task == "record_abnormal":
        return flag
    if task == "visit_details":
        field = VISIT_FIELDS.get(question.field, question.field)
        return ({"dates_shifted"}
                if field in ("summary", "admitted", "discharged") else set())
    if task == "person_details":
        return {"death": {"death_meaning"}, "age": {"age_meaning"}}.get(
            question.field, set())
    return set()


def notices_supported(candidate, check):
    """Return True when a record answer states exactly the notices its
    question needs (values_omitted aside)."""
    stated = {statement.notice for statement in candidate.statements
              if statement.kind == "notice"} - {"values_omitted"}
    return stated == expected_notices(candidate, check)


def check(candidate, bundle=None, record_check=None):
    """Return the reason codes of the statements of a record answer that
    what they cite does not support; empty when it passes.

    The candidate must have passed the structure checks for this record
    bundle; record_check holds the terms, the resolution and the reading
    of the question it answers.
    """
    if candidate.outcome not in ("answer", "not_recorded") or (
            bundle is None):
        return ()
    if record_check is None or getattr(bundle, "kind", None) != "record":
        return ("claim_unsupported",)
    task = candidate.task
    question = record_check.question
    if question.task != task or not notices_supported(candidate,
                                                      record_check):
        return ("claim_unsupported",)
    records = {entry.ref: entry for entry in bundle.records}
    if task in ("visit_details", "person_details"):
        supported = (not candidate.reason_codes and (
            visits_supported(candidate, bundle, record_check)
            if task == "visit_details"
            else person_supported(candidate, bundle, record_check)))
        return () if supported else ("claim_unsupported",)
    unnamed = sum(bundle.coverage.of(kind).unnamed_in_scope
                  for kind in resolving.RECORD_KINDS[question.kind])
    if ("unmapped_records_in_scope" in candidate.reason_codes) != bool(
            unnamed):
        return ("claim_unsupported",)
    checks = {"record_list": list_supported,
              "record_presence": presence_supported,
              "record_timing": timing_supported,
              "record_count": count_supported,
              "record_values": values_supported,
              "record_abnormal": abnormal_supported}
    supported = checks[task](candidate, bundle, record_check, records)
    return () if supported else ("claim_unsupported",)


def run_synthetic():
    """Return (passed, faults detected, faults) on invented data."""
    from ehr_evidence_app.qa import record_answer, record_interpret
    from ehr_evidence_app.evidence import record_bundles, contracts
    bundles, _ = record_bundles.run_synthetic()
    bundle = next(item for item in bundles
                  if item.scope.kind == "case_visits")
    terms = synthetic_terms()
    question = record_interpret.RecordQuestion(
        "task", task="record_list", kind="lab")
    request = contracts.Request(
        schema_version="1.0", request_id="q-1", task="record_list",
        case=bundle.case, scope=bundle.scope, question="labs")
    bundle_ref = contracts.BundleRef(bundle_id=bundle.bundle_id,
                                     sha256="5" * 64)
    good = record_answer.list_candidate(request, bundle, bundle_ref,
                                        question)
    context = RecordCheck(terms, None, question)
    passed = not check(good, bundle, context)
    item = next(index for index, statement in enumerate(good.statements)
                if statement.kind == "record_list_item")
    faults = []
    statements = list(good.statements)
    statements[item] = statements[item].model_copy(update={
        "slots": statements[item].slots.model_copy(update={
            "count": statements[item].slots.count + 1})})
    faults.append(good.model_copy(update={"statements": tuple(statements)}))
    faults.append(good.model_copy(update={"statements": tuple(
        statement for index, statement in enumerate(good.statements)
        if index != item)}))
    detected = sum(1 for fault in faults
                   if check(fault, bundle, context) == ("claim_unsupported",))
    return passed, detected, len(faults)


def synthetic_drugs():
    """Return an invented medication vocabulary: one ingredient, named as
    the invented drug records' concept is, that the invented bundles give
    as their ingredient."""
    from ehr_evidence_app.qa import record_vocabulary
    concept = record_vocabulary.DrugConcept(
        7001, "ingredient", "SYNTHETIC", "Ingredient", "S7001",
        "SYNTHETIC CONCEPT 3001")
    return record_vocabulary.RecordVocabulary(
        drugs={7001: concept}, drug_names={
            resolving.normalize(concept.name): {7001}},
        brands={}, ancestors={3001: {7001}},
        procedure_names=("synthetic excision of organ",))


def synthetic_terms(conditions=None):
    """Return Terms of the invented dictionaries; conditions is the
    condition vocabulary, if any."""
    return resolving.Terms(
        labs=[("50971", "SYNTHETIC POTASSIUM", "Blood", "Chemistry"),
              ("50912", "SYNTHETIC CREATININE", "Blood", "Chemistry")],
        items=[("220045", "SYNTHETIC HEART RATE"),
               ("225459", "SYNTHETIC CHEST X-RAY")],
        micro=["SYNTHETIC ORGANISM", "BLOOD CULTURE", "SYNTHETIC CULTURE",
               "SYNTHETIC ANTIBIOTIC"],
        units=["SYNTHETIC UNIT", "SYNTHETIC ICU"], drugs=synthetic_drugs(),
        procedures=("synthetic excision of organ",),
        condition_vocabulary=conditions)


def main(argv=None):
    """Check an invented list answer and unsupported variants of it."""
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    passed, detected, total = run_synthetic()
    print("supported answer passes: %s" % ("yes" if passed else "no"))
    print("unsupported statements detected: %d of %d" % (detected, total))
    ok = passed and detected == total
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
