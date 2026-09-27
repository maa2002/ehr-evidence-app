"""The prompt of a model-written candidate (work item 41).

messages() returns the two chat messages of one request: the rules every
answer follows, then one worked example on invented data, and the
request's task, the shape of its reply and its evidence as the compact
projection of generation.projection (prompt-v7, ADR 0014). The canonical
bundle is not sent: the projection, structured for its task, leaves out
serialization redundancy and what the task does not use, and holds
everything a correct answer needs. A request whose prompt is over its
budget falls back to the template (generation.adapter).

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

PROMPT_VERSION = "prompt-v7"
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
GROUPS = """\
Each group names its condition, a standard concept (concept_id) or, for \
records without one, an ICD source code (source), lists its records as \
[ref, visit], the visit being the record's visit number, and gives their \
number (count) and their visit numbers (visits)."""
LIST_TASK = """\
Task: list every condition recorded within {scope}."""
LIST_VIEW = """\
The view gives the number of conditions (condition_count) and of \
records (record_count) within the scope, and groups the scope's records by \
condition ("conditions"), in the order of each condition's first record. \
""" + GROUPS
LIST_ANSWER = """\
Reply shape, if "conditions" holds groups: {{"outcome": "answer", \
"reason_codes": [...], \
"item_wording": ..., "items": [...], "summary": {{"wording": ..., \
"groups": ..., "records": ...}}, "unmapped": ..., "notices": \
["list_complete"]}}.
- items: one per group, in the view's order: [the ref of the group's \
first record, the refs of all its records, their visit numbers ascending \
and each once, their number].
- item_wording, used for every item: begins with {{condition}} and also \
has {{count}} and {{visits}}.
- summary: groups is the number of items and records the number of refs \
in all items; its wording begins with {{groups}} and also has {{scope}} \
and {{records}}.
- unmapped: null, or when records_without_standard_concept is above \
zero, {{"wording": ..., "unmapped": that number}}; its wording begins \
with {{unmapped}}."""
LIST_EMPTY = """\
Reply shape, if "conditions" is empty: {{"outcome": "not_recorded", \
"reason_codes": [...], "empty": {{"wording": ...}}, "notices": \
["absence"]}}. The wording has {{scope}} and says that no conditions are \
recorded there."""
PRESENCE_TASK = """\
Task: say whether {label} is recorded within {scope}.

The view gives the number of the scope's records that count as the \
condition asked about (matching_count) and lists them ("matching"), \
grouped by condition and match kind, in the order of each group's first \
record. """ + GROUPS + """ Each group also states its match kind (match). \
When "matching" is empty, no record within the scope counts as the \
condition."""
PRESENCE_ANSWER = """\
Reply shape, if records match: {{"outcome": "answer", "reason_codes": \
[...], "item_wording": ..., "items": [...], "summary": {{"wording": ..., \
"matches": ...}}}}.
- items: one per group of "matching", in the view's order: [the ref of \
the group's first record, its match kind, the refs of all its records, \
their visit numbers ascending and each once, their number].
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
restrictions and the notice of each (restriction_notices), and the drivers \
to describe, highest score first, each with \
its rank, ref and attribution score, its history (its records in visits \
before the final admission, as [ref, visit], the visit being the record's \
visit number), with their number (history_count) and visit numbers \
(history_visits), and its context (its records in the final admission, as \
refs), with their number (context_count); and how many other eligible \
drivers are not described (drivers_not_described).

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
- drivers: one per driver of the view, in its order: [its ref, its rank, \
its score, its history, its context]. History is null when the driver's \
history is empty, otherwise [the refs of its history, their visit numbers \
ascending and each once, their number]; context is null when the \
driver's context is empty, otherwise [the refs of its context, their \
number].
- notices: "drivers", then "drivers_omitted" when drivers_not_described \
is above zero, then "attribution_meaning" and "score_meaning", then the \
notices in restriction_notices.
- omitted: drivers_not_described when it is above zero, otherwise null."""
# One explanation example describes two drivers and leaves one out.
EXAMPLE_DRIVERS = 2


def scope_text(scope):
    """Return how a request's scope is named in a prompt."""
    return SCOPES.get(scope.kind) or candidates.scope_words(scope)


def worded_kinds(context):
    """Return the statement kinds whose wordings a reply of the request's
    task may hold, whatever its evidence."""
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
    if context.task == "condition_list":
        parts = [LIST_TASK, LIST_VIEW, LIST_ANSWER, LIST_EMPTY,
                 REASON_CODES]
    elif context.task == "condition_presence":
        values["label"] = context.asked.label
        parts = [PRESENCE_TASK, PRESENCE_ANSWER, PRESENCE_ABSENT,
                 REASON_CODES]
    else:
        parts = [EXPLANATION_TASK]
    return "\n\n".join([part.format(**values) for part in parts]
                       + [example_wordings(context)])


def without_standard_concept(context, unmatched=False):
    """Return an invented request whose last record has lost its standard
    concept, with its template: the list or presence example then shows
    how records without one are grouped by their ICD code and disclosed.
    With unmatched, the presence question asks about a condition none of
    the records match, so that "not recorded" is disclosed too."""
    from ehr_evidence_app.evidence import contracts, schema
    from ehr_evidence_app.qa import answer, resolve, suite, templates
    bundle = context.bundle
    last = bundle.events[-1]
    code = last.source_concept.code if last.source_concept else "SYN"
    events = bundle.events[:-1] + (last.model_copy(update={
        "concept": schema.Concept(concept_id=0, name_status="unmapped",
                                  source_code=code)}),)
    bundle = bundle.model_copy(update={
        "events": events, "coverage": bundle.coverage.model_copy(update={
            "unmapped_events_in_scope": sum(
                event.concept.concept_id == 0 for event in events)})})
    request = context.request
    ref = contracts.BundleRef(bundle_id=bundle.bundle_id,
                              sha256=context.bundle_sha256)
    if context.task == "condition_list":
        template, _ = templates.list_candidate(
            request.request_id, request.case, request.scope, bundle, ref)
        return shape.Context(request, bundle, context.bundle_sha256), template
    vocabulary = suite.synthetic_vocabulary()
    concepts = context.asked.name.concepts
    if unmatched:
        concepts = next(
            (concept,) for concept in sorted(vocabulary.rows)
            if not answer.record_matches(bundle, resolve.Resolution(
                "", "resolved", "concepts", concepts=(concept,)), vocabulary))
    resolution = resolve.Resolution("", "resolved", "concepts",
                                    concepts=concepts)
    template = templates.presence_candidate(
        request.request_id, request.case, request.scope, bundle, ref,
        resolution, answer.record_matches(bundle, resolution, vocabulary))
    return shape.Context(request, bundle, context.bundle_sha256, shape.asked(
        resolution, bundle, vocabulary)), template


@functools.lru_cache(maxsize=None)
def examples():
    """Return the worked examples, on invented data: for a list and a
    presence check, the invented answer that groups the most records into
    the most items (the smallest such), with its last record's standard
    concept removed so that the example discloses it, and for a presence
    check also its "not recorded" for a condition none of the records
    match (keyed ("condition_presence", False)); for an explanation, the
    invented one describing two of its drivers and leaving one out (keyed
    True), and describing all of them (keyed False)."""
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
    for task in ("condition_list", "condition_presence"):
        rank, context, _ = found[task]
        found[task] = (rank,) + without_standard_concept(context)
    found[("condition_presence", False)] = (0,) + without_standard_concept(
        found["condition_presence"][1], unmatched=True)
    return {key: (context, template)
            for key, (_, context, template) in found.items()}


def examples_of(context):
    """Return the worked examples of a request: its task's; for a presence
    check both its answer and its "not recorded", whatever the evidence;
    for an explanation the one that leaves drivers out exactly when the
    request does."""
    if context.task == "prediction_explanation":
        return [examples()[(context.task,
                            shape.omitted_drivers(context) > 0)]]
    if context.task == "condition_presence":
        return [examples()[context.task],
                examples()[(context.task, False)]]
    return [examples()[context.task]]


def request_text(context):
    """Return the task, reply shape and evidence view of one request."""
    return "\n\n".join((task_text(context), "Evidence view:",
                        projection.text(projection.view(context))))


def messages(context):
    """Return the chat messages of a request: the rules, then the worked
    example, the request and its evidence view."""
    parts = []
    for example, template in examples_of(context):
        parts += ["Worked example, on invented data.", request_text(example),
                  "Reply:", shape.text(shape.document_of(template))]
    body = "\n\n".join(parts + ["Now the request.", request_text(context),
                                 "Reply with the JSON document only."])
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
