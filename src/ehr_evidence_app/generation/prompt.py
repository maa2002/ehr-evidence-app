"""The prompt of a model-written candidate (work item 41).

messages() returns the two chat messages of one request: the rules every
answer follows, then one worked example on invented data, and the
request's task, the shape of its reply and its evidence as the compact
projection of generation.projection (prompt-v3, ADR 0013). The canonical
bundle is not sent: the projection drops only serialization redundancy
and what the task does not use, and holds everything a correct answer
needs. A request whose prompt is over its budget falls back to the
template (generation.adapter).

The prompt is versioned; a change of wording is a new version, measured
again on the development cases. Prompts hold patient records and are
never logged.

Usage:
    python -m ehr_evidence_app.generation.prompt
"""

from __future__ import annotations

import argparse
import functools
import sys

from ehr_evidence_app.generation import projection, shape
from ehr_evidence_app.validation import candidate as candidates

PROMPT_VERSION = "prompt-v3"
RULES = """\
You write the responses of a research application that answers questions \
about one patient's recorded conditions, and explains a model's \
prediction, from the patient's evidence. You receive one request, its \
evidence as a compact JSON view and the shape of your reply. Reply with \
one JSON document in that shape and nothing else.

Rules for every reply:
1. State only what the evidence records. Add no knowledge, \
interpretation, advice or opinion.
2. Every fact goes in its own field: record references (E1, E2, ...), \
visit numbers, counts, match kinds, labels, scores and ranks. A \
"wording" holds no fact: no digits, no dates and no numbers in words.
3. A wording is one short line of plain words around its placeholders: \
each placeholder listed for it, written as {name}, exactly once, and no \
other, beginning with the placeholder it is said to begin with. When the \
answer is shown, each placeholder is replaced by the text of its fact (a \
condition's name, a count such as "3 records", visits such as "visits 1 \
and 2", the scope's name), so the words around the placeholders never \
state those facts again. One wording serves every item of its kind.
4. Say what is recorded, never what the patient has or does not have. \
"Not recorded" never means clinically absent.
5. Do not hedge, rank, judge severity or course, or say when or how \
often something happened.
6. Notices are named by their identifiers; their text is fixed."""
SCOPES = {"pre_target_history": "the pre-target history (the visits "
                                "before the final admission)",
          "case_visits": "all case visits, including the final admission"}
RECORDS = """\
The view lists the scope's records as rows of its "columns": ref, visit \
(the record's visit number), concept_id (its standard concept; 0 when it \
has none) and source (its ICD source code)."""
LIST_TASK = """\
Task: list every condition recorded within {scope}."""
LIST_ANSWER = RECORDS + """ A condition is one concept_id, or, for records \
whose concept_id is 0, one source. Records with the same concept_id are \
one condition, even when their sources differ.

Reply shape: {{"outcome": "answer", "reason_codes": [...], \
"item_wording": ..., "items": [...], "summary": {{"wording": ..., \
"groups": ..., "records": ...}}, "unmapped": ..., "notices": \
["list_complete"]}}.
- items: one per condition, in the order of its first record: [the ref of \
its first record, the refs of all its records, their visit numbers \
ascending and each once, their number].
- item_wording, used for every item: begins with {{condition}} and also \
has {{count}} and {{visits}}.
- summary: groups is the number of items and records the number of refs \
in all items; its wording begins with {{groups}} and also has {{scope}} \
and {{records}}.
- unmapped: null, or when records_without_standard_concept is above \
zero, {{"wording": ..., "unmapped": that number}}; its wording begins \
with {{unmapped}}."""
LIST_EMPTY = """\
The view has no records within the scope.

Reply shape: {{"outcome": "not_recorded", "reason_codes": [], "empty": \
{{"wording": ...}}, "notices": ["absence"]}}. The wording has {{scope}} \
and says that no conditions are recorded there."""
PRESENCE_TASK = """\
Task: say whether {label} is recorded within {scope}.

""" + RECORDS + """ Its "counts_as" rows say which concept_ids or sources \
count as the condition, each with its match kind. When "matched_by" is \
"concept", a record matches by its concept_id, or by its source when its \
concept_id is 0; when it is "source_code", every record matches by its \
source."""
PRESENCE_ANSWER = """\
Reply shape, if records match: {{"outcome": "answer", "reason_codes": \
[...], "item_wording": ..., "items": [...], "summary": {{"wording": ..., \
"matches": ...}}}}.
- items: one per condition and match kind of the matching records, in the \
order of its first record: [the ref of its first record, its match kind, \
the refs of all its matching records, their visit numbers ascending and \
each once, their number]. A condition is one concept_id, or one source \
for records whose concept_id is 0.
- item_wording, used for every item: begins with {{condition}} and also \
has {{count}}, {{visits}} and {{match}}.
- summary: matches is the number of refs in all items; its wording begins \
with {{condition}} and also has {{scope}} and {{matches}}."""
PRESENCE_ABSENT = """\
Reply shape, if no record matches: {{"outcome": "not_recorded", \
"reason_codes": [...], "absent": {{"wording": ...}}, "unmapped": ..., \
"notices": ["absence"]}}.
- absent: its wording begins with {{condition}}, also has {{scope}}, and \
says that it is not recorded there.
- unmapped: null, or when records_without_standard_concept is above \
zero, {{"wording": ..., "unmapped": that number}}; its wording begins \
with {{unmapped}}."""
REASON_CODES = """\
reason_codes: ["unmapped_records_in_scope"] when \
records_without_standard_concept is above zero, otherwise []."""
EXPLANATION_TASK = """\
Task: explain the selected prediction through the recorded concepts with \
the highest attribution scores.

The view gives the prediction (its label, score and logit), the case's \
restrictions, and the drivers to describe, highest score first, as rows of \
its "columns": ref, score, history (the driver's records in visits before \
the final admission, as [ref, visit number]) and context (its records in \
the final admission, as refs); and how many other eligible drivers are not \
described (drivers_not_described).

Reply shape: {{"outcome": "explanation", "reason_codes": the \
restrictions, "prediction": {{"wording": ..., "label": ..., "score": ..., \
"logit": ...}}, "driver_wording": ..., "history_wording": ..., \
"context_wording": ..., "drivers": [...], "notices": [...], "omitted": \
...}}.
- The prediction wording begins with {{label}} and also has {{score}} and \
{{logit}}; the driver wording, used for every driver, begins with \
{{rank}} and also has {{driver}} and {{score}}; the history wording begins \
with {{driver}} and also has {{count}} and {{visits}}; the context wording \
begins with {{driver}} and also has {{count}}.
- drivers: one per driver of the view, in its order: [its ref, its rank \
(1, 2, ...), its score, its history, its context]. History is null when \
the driver has no history records, otherwise [their refs, their visit \
numbers ascending and each once, their number]; context is null when it \
has none, otherwise [their refs, their number].
- notices: "drivers", then "drivers_omitted" when drivers_not_described \
is above zero, then "attribution_meaning" and "score_meaning", then for \
each restriction its notice: batch_sensitive_prediction: \
"batch_sensitive"; target_attached_drivers_present: "target_attached"; \
target_outcome_entries_excluded: "target_excluded".
- omitted: drivers_not_described when it is above zero, otherwise null."""
# One explanation example describes two drivers and leaves one out.
EXAMPLE_DRIVERS = 2


def scope_text(scope):
    """Return how a request's scope is named in a prompt."""
    return SCOPES.get(scope.kind) or candidates.scope_words(scope)


def worded_kinds(context):
    """Return the statement kinds whose wordings a request's reply may
    hold: a list of a scope with records is an answer, one without is
    empty, and a presence check of a scope without records is "not
    recorded"."""
    if context.task == "condition_list":
        return (("list_item", "list_summary", "list_unmapped")
                if context.bundle.events else ("list_empty",))
    if context.task == "condition_presence" and not context.bundle.events:
        return ("presence_absent", "presence_unmapped")
    return shape.WORDED[context.task]


def example_wordings(context):
    """Return the templates' wordings of the statements a request's reply
    may hold, as examples the model may follow or vary."""
    lines = ["Example wordings (others that follow the rules may be used):"]
    lines += ['- %s: "%s"' % (kind, shape.example_wording(kind))
              for kind in worded_kinds(context)]
    return "\n".join(lines)


def task_text(context):
    """Return the task, reply shape and example wordings of a request."""
    values = {"scope": scope_text(context.request.scope)}
    records = (context.task != "prediction_explanation"
               and bool(context.bundle.events))
    if context.task == "condition_list":
        parts = [LIST_TASK] + ([LIST_ANSWER, REASON_CODES] if records
                               else [LIST_EMPTY])
    elif context.task == "condition_presence":
        values["label"] = context.asked.label
        parts = [PRESENCE_TASK] + ([PRESENCE_ANSWER] if records else []) + [
            PRESENCE_ABSENT, REASON_CODES]
    else:
        parts = [EXPLANATION_TASK]
    return "\n\n".join([part.format(**values) for part in parts]
                       + [example_wordings(context)])


@functools.lru_cache(maxsize=None)
def examples():
    """Return the worked examples, on invented data: for a list and a
    presence check, the invented answer that groups the most records into
    the most items (the smallest such); for an explanation, the invented
    one describing two of its drivers and leaving one out (keyed True),
    and describing all of them (keyed False)."""
    from ehr_evidence_app.validation import explanation
    found = {}
    for context, template in shape.synthetic_contexts():
        if context.task == "prediction_explanation":
            every = len(explanation.describable(context.bundle))
            for limit in (EXAMPLE_DRIVERS, every):
                key = (context.task, limit < every)
                if key not in found:
                    found[key] = (0, shape.Context(
                        context.request, context.bundle,
                        context.bundle_sha256, limit=limit),
                        explanation.template(context.request, context.bundle,
                                             context.bundle_sha256, limit))
            continue
        if template.outcome != "answer":
            continue
        items = [item for item in template.statements
                 if item.kind in ("list_item", "presence_item")]
        rank = (-len(items), -sum(len(item.refs) for item in items),
                len(projection.text(projection.view(context))))
        if context.task not in found or rank < found[context.task][0]:
            found[context.task] = (rank, context, template)
    return {key: (context, template)
            for key, (_, context, template) in found.items()}


def example_of(context):
    """Return the worked example of a request: its task's, and for an
    explanation the one that leaves drivers out exactly when the request
    does."""
    if context.task == "prediction_explanation":
        return examples()[(context.task,
                           shape.omitted_drivers(context) > 0)]
    return examples()[context.task]


def request_text(context):
    """Return the task, reply shape and evidence view of one request."""
    return "\n\n".join((task_text(context), "Evidence view:",
                        projection.text(projection.view(context))))


def messages(context):
    """Return the chat messages of a request: the rules, then the worked
    example, the request and its evidence view."""
    example, template = example_of(context)
    body = "\n\n".join((
        "Worked example, on invented data.",
        request_text(example),
        "Reply:", shape.text(shape.document_of(template)),
        "Now the request.",
        request_text(context),
        "Reply with the JSON document only."))
    return [{"role": "system", "content": RULES},
            {"role": "user", "content": body}]


def describe():
    """Return the versions a model-written candidate was produced with."""
    return {"prompt_version": PROMPT_VERSION,
            "shape_version": shape.SHAPE_VERSION,
            "projection_version": projection.PROJECTION_VERSION}


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

def run_synthetic():
    """Return (prompts built, characters of the longest, problems) for the
    invented fixtures."""
    problems, longest, built = [], 0, 0
    for context, _ in shape.synthetic_contexts():
        found = messages(context)
        text = "".join(message["content"] for message in found)
        built += 1
        longest = max(longest, len(text))
        if not text.endswith(projection.text(projection.view(context))
                             + "\n\nReply with the JSON document only."):
            problems.append("a prompt does not end with its evidence view")
        if context.bundle.bundle_id in text:
            problems.append("a prompt carries the canonical bundle")
    return built, longest, problems


def main(argv=None):
    """Build the prompts of the invented fixtures; print sizes only."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    built, longest, problems = run_synthetic()
    for problem in problems:
        print("PROBLEM: %s" % problem)
    print("prompts built: %d; longest %d characters" % (built, longest))
    print("prompt %s, shape %s, projection %s" % (
        PROMPT_VERSION, shape.SHAPE_VERSION, projection.PROJECTION_VERSION))
    print("RESULT: %s" % ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
