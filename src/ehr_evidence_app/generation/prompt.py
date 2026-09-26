"""The prompt of a model-written candidate (work item 41).

messages() returns the two chat messages of one request: the rules every
answer follows, then the task, the shape of the reply, one worked example
on invented data, the request and its complete evidence bundle as JSON.
The bundle is never trimmed; a request whose prompt is over its budget
falls back to the template (generation.adapter).

For a presence question the prompt also states which of the bundle's
concepts count as the condition asked about, and how: vocabulary facts
(more specific concepts, ICD descriptions) the bundle does not carry and
the model could not know. The model still finds, groups, counts and cites
the records.

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

from ehr_evidence_app.generation import shape
from ehr_evidence_app.validation import candidate as candidates

PROMPT_VERSION = "prompt-v1"
RULES = """\
You write the responses of a research application that answers questions \
about one patient's recorded conditions, and explains a model's \
prediction, from an evidence bundle. You receive one request, its \
complete evidence bundle as JSON and the shape of your reply. Reply with \
one JSON document in that shape and nothing else.

Rules for every reply:
1. State only what the bundle records. Add no knowledge, interpretation, \
advice or opinion.
2. Every fact goes in its own field: record references (E1, E2, ...), \
visit numbers, counts, match kinds, labels, scores and ranks. A \
"wording" holds no fact: no digits, no dates and no numbers in words.
3. A wording is one short line of plain words around its placeholders: \
each placeholder listed for it, written as {name}, exactly once, and no \
other. When the answer is shown, each placeholder is replaced by the text \
of its fact (a condition's name, a count such as "3 records", visits such \
as "visits 1 and 2", the scope's name), so the words around the \
placeholders never state those facts again.
4. Say what is recorded, never what the patient has or does not have. \
"Not recorded" never means clinically absent.
5. Do not hedge, rank, judge severity or course, or say when or how \
often something happened.
6. Notices are named by their identifiers; their text is fixed."""
SCOPES = {"pre_target_history": "the pre-target history (the visits "
                                "before the final admission)",
          "case_visits": "all case visits, including the final admission"}
LIST_TASK = """\
Task: list every condition recorded within {scope}.

A condition is one standard concept (the concept_id of a record's \
"concept"), or, for a record whose concept_id is 0, one ICD source code \
(its "source_concept" code). Records with the same concept_id are one \
condition, even when their source codes differ. The records are the \
bundle's "events"; a record's visit is the entry of "visits" whose ref is \
its visit_ref.

Reply shape:
- If the bundle has no events: {{"outcome": "not_recorded", \
"reason_codes": [], "empty": {{"wording": ...}}, "notices": \
["absence"]}}. The wording has the placeholder {{scope}}.
- Otherwise: {{"outcome": "answer", "reason_codes": [...], "summary": \
{{"wording": ..., "groups": the number of conditions, "records": the \
number of records}}, "items": [...], "unmapped": ..., "notices": \
["list_complete"]}}. The summary wording has {{groups}}, {{scope}} and \
{{records}}.
- items: one per condition, in the order of its first record: \
{{"wording": ..., "condition": the reference of its first record, \
"count": its number of records, "visits": the visit numbers of its \
records' visits, ascending, each once, "refs": the references of all its \
records}}. The wording has {{condition}}, {{count}} and {{visits}}.
- unmapped: null, or when coverage.unmapped_events_in_scope is above \
zero, {{"wording": ..., "unmapped": that number}}; the wording has \
{{unmapped}}.
- reason_codes: ["unmapped_records_in_scope"] when \
coverage.unmapped_events_in_scope is above zero, otherwise []."""
PRESENCE_TASK = """\
Task: say whether {label} is recorded within {scope}.

{counts_as}

Reply shape:
- If no record matches: {{"outcome": "not_recorded", "reason_codes": \
[...], "absent": {{"wording": ...}}, "unmapped": ..., "notices": \
["absence"]}}. The wording has {{condition}} and {{scope}}.
- Otherwise: {{"outcome": "answer", "reason_codes": [...], "summary": \
{{"wording": ..., "matches": the number of matching records}}, "items": \
[...]}}. The summary wording has {{condition}}, {{scope}} and \
{{matches}}.
- items: one per condition and match kind of the matching records, in \
the order of its first record: {{"wording": ..., "condition": the \
reference of its first record, "count": its number of records, \
"visits": the visit numbers of its records' visits, ascending, each \
once, "match": its match kind, "refs": the references of all its \
records}}. A condition is one standard concept, or one ICD source code \
for a record whose concept_id is 0; records with the same concept_id are \
one condition, even when their source codes differ. The wording has \
{{condition}}, {{count}}, {{visits}} and {{match}}.
- unmapped: null, or when coverage.unmapped_events_in_scope is above \
zero, {{"wording": ..., "unmapped": that number}}; the wording has \
{{unmapped}}.
- reason_codes: ["unmapped_records_in_scope"] when \
coverage.unmapped_events_in_scope is above zero, otherwise []."""
EXPLANATION_TASK = """\
Task: explain the selected prediction through the recorded concepts the \
model's output was sensitive to.

The bundle's "prediction" (P1) is the selected label, one output of a \
multi-label model. Its "attributions" are the recorded concepts in the \
order the model lists them; only those whose narration_eligible is true \
may be described. An attribution's linked_event_refs are its records: \
those among "history_events" are its history (pre-target visits), those \
among "final_admission_context" its final-admission context. A record's \
visit is the entry of "visits" whose ref is its visit_ref.

Reply shape: {{"outcome": "explanation", "reason_codes": the bundle's \
"restrictions", "prediction": {{...}}, "drivers": [...], "notices": \
[...]}}.
- prediction: {{"wording": ..., "label": prediction.concept.concept_id, \
"score": prediction.score, "logit": prediction.logit}}. The wording has \
{{label}}, {{score}} and {{logit}}.
- drivers: every attribution that may be described, in the bundle's \
order: {{"wording": ..., "driver": its ref, "rank": 1 for the first, \
then 2, 3, ..., "score": its score, "history": ..., "context": ...}}. \
The wording has {{rank}}, {{driver}} and {{score}}.
- history: null when the driver has no history records, otherwise \
{{"wording": ..., "count": their number, "visits": their visit numbers, \
ascending, each once, "refs": their references}}. The wording has \
{{driver}}, {{count}} and {{visits}}.
- context: null when the driver has no final-admission records, \
otherwise {{"wording": ..., "count": their number, "refs": their \
references}}. The wording has {{driver}} and {{count}}.
- notices: "drivers", "attribution_meaning" and "score_meaning", then \
for each restriction its notice: batch_sensitive_prediction: \
"batch_sensitive"; target_attached_drivers_present: "target_attached"; \
target_outcome_entries_excluded: "target_excluded"."""
TASKS = {"condition_list": LIST_TASK, "condition_presence": PRESENCE_TASK,
         "prediction_explanation": EXPLANATION_TASK}


def scope_text(scope):
    """Return how a request's scope is named in a prompt."""
    return SCOPES.get(scope.kind) or candidates.scope_words(scope)


def counts_as_text(question):
    """Return which concepts of the bundle count as the condition a
    presence question asks about."""
    if not question.counts_as:
        return ("No concept of this bundle counts as it, so no record "
                "matches.")
    lines = ["These concepts of this bundle count as it, with their match "
             "kinds; no other concept does:"]
    for entry in question.counts_as:
        if entry.by == "concept":
            lines.append("- a record whose concept_id is %d: %s"
                         % (entry.concept_id, entry.match))
        else:
            lines.append("- a record whose source_concept code is %s (%s): "
                         "%s" % (entry.code, entry.vocabulary_id,
                                 entry.match))
    return "\n".join(lines)


def bundle_text(bundle):
    """Return a bundle as compact JSON; absent values are left out,
    which loses nothing."""
    return bundle.model_dump_json(exclude_none=True)


def example_wordings(task):
    """Return the templates' wordings of a task's statements, as examples
    the model may follow or vary."""
    # Imported here: qa and validation write through this package.
    from ehr_evidence_app.qa import templates
    from ehr_evidence_app.validation import explanation
    found = dict(explanation.WORDINGS, **{
        kind: templates.wording(kind, 2) for kind in templates.WORDINGS})
    lines = ["Example wordings (others that follow the rules may be used):"]
    lines += ['- %s: "%s"' % (kind, found[kind])
              for kind in shape.WORDED[task]]
    return "\n".join(lines)


def task_text(context):
    """Return the task, reply shape and example wordings of a request."""
    values = {"scope": scope_text(context.request.scope)}
    if context.task == "condition_presence":
        values.update(label=context.asked.label,
                      counts_as=counts_as_text(context.asked))
    return "\n\n".join((TASKS[context.task].format(**values),
                        example_wordings(context.task)))


@functools.lru_cache(maxsize=None)
def examples():
    """Return the worked example of each task, on invented data: the
    smallest invented bundle with records for a list and a presence check
    (a condition recorded there), and the invented explanation."""
    found = {}
    for context, template in shape.synthetic_contexts():
        records = getattr(context.bundle, "events", None)
        if records is not None and (not records or (
                context.task == "condition_presence"
                and template.outcome != "answer")):
            continue
        size = len(bundle_text(context.bundle))
        if context.task not in found or size < found[context.task][0]:
            found[context.task] = (size, context, template)
    return {task: (context, template)
            for task, (_, context, template) in found.items()}


def request_text(context, bundle_label):
    """Return the task, shape and bundle of one request."""
    return "\n\n".join((task_text(context), bundle_label + ":",
                        bundle_text(context.bundle)))


def messages(context):
    """Return the chat messages of a request: the rules, then the worked
    example, the request and its complete bundle."""
    example, template = examples()[context.task]
    body = "\n\n".join((
        "Worked example, on invented data.",
        request_text(example, "Evidence bundle"),
        "Reply:", shape.text(shape.document_of(template)),
        "Now the request.",
        request_text(context, "Evidence bundle"),
        "Reply with the JSON document only."))
    return [{"role": "system", "content": RULES},
            {"role": "user", "content": body}]


def describe():
    """Return the versions a model-written candidate was produced with."""
    return {"prompt_version": PROMPT_VERSION,
            "shape_version": shape.SHAPE_VERSION}


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
        if not text.endswith(bundle_text(context.bundle)
                             + "\n\nReply with the JSON document only."):
            problems.append("a prompt does not end with its bundle")
        if type(context.bundle).model_validate_json(
                bundle_text(context.bundle)) != context.bundle:
            problems.append("a bundle lost a value in the prompt")
    return built, longest, problems


def main(argv=None):
    """Build the prompts of the invented fixtures; print sizes only."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    built, longest, problems = run_synthetic()
    for problem in problems:
        print("PROBLEM: %s" % problem)
    print("prompts built: %d; longest %d characters" % (built, longest))
    print("prompt %s, shape %s" % (PROMPT_VERSION, shape.SHAPE_VERSION))
    print("RESULT: %s" % ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
