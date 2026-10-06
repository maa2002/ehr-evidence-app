"""The baseline condition of the final evaluation (work item 56; ADR 0028,
decision 5; ADR 0014, decision 7).

The baseline writes the same requests as the controlled condition, from
the same bundles, projection, schema, model, precision, decoding, number
of drivers, limits and single attempt. It keeps the checks that bind the
evidence to the request (case, scope, prediction link, visit numbering,
readable records), which decide whether there is evidence to write from.
It lacks three controls:

* the prompt's grounding rules, rules 1, 4 and 5 of prompt-v9: state only
  what the evidence records; say what is recorded, never what the patient
  has; do not hedge, rank, judge or say when or how often. The system
  message keeps the role and the other rules, renumbered, and the
  request's message is the controlled condition's, worked examples
  included;
* the sufficiency checks before generation: a scope whose coverage is
  incomplete, or an explanation without an eligible driver, is written
  from the evidence there is, where the controlled condition abstains;
* the release gate: the document becomes a candidate, and its text is
  rendered without checks. A fact the bundle cannot render is shown as
  not in the record, and nothing else is changed.

A document that does not parse, breaks its schema or cannot become a
candidate is invalid output, as it is in the controlled condition; a
prompt or an expected answer over its limit, a timeout, exhausted memory
and a backend failure are the same failures as there.

The default mode checks the baseline's messages against the controlled
condition's, and writes the invented requests with scripted backends.

Usage:
    python -m ehr_evidence_app.assessment.baseline
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
import time

from ehr_evidence_app.generation import adapter, prompt, shape
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import evidence

BASELINE_VERSION = "baseline-v1"
# The grounding rules of the controlled prompt, by their numbers there.
GROUNDING = (1, 4, 5)
RULE = re.compile(r"^(\d+)\. ")
UNRESOLVED = "[not in the record]"
# What the controlled condition's checks before generation end in, and
# which of them the baseline keeps: the binding checks end in a case
# rejection, the sufficiency checks in an abstention.
BINDING = "case_rejection"


@dataclasses.dataclass(frozen=True)
class Written:
    """What one condition gave for a request.

    outcome is the response's outcome, or for a failure
    "validation_rejection" (the gate withheld it) or "operational_error";
    category is the failure's category (ADR 0016, decision 8), or None;
    reason is the adapter's fallback reason, or the reason codes' first
    for a stop before generation; candidate is the candidate the condition
    gave, or the model's candidate it withheld; text the text it gave;
    output the backend's output, when it wrote.
    """

    outcome: str
    category: str | None = None
    reason: str | None = None
    candidate: candidates.Candidate | None = None
    text: str | None = None
    reason_codes: tuple = ()
    gate_reasons: tuple = ()
    output: adapter.Output | None = None
    released: bool = False


def rules(text=prompt.RULES):
    """Return the controlled prompt's rules without its grounding rules,
    the others renumbered."""
    kept, number = [], 0
    for line in text.split("\n"):
        match = RULE.match(line)
        if match:
            if int(match.group(1)) in GROUNDING:
                continue
            number += 1
            line = "%d. %s" % (number, line[match.end():])
        kept.append(line)
    return "\n".join(kept)


def messages(context):
    """Return the baseline's chat messages of a request: the controlled
    condition's, with the rules in the system message less the grounding
    rules."""
    found = prompt.messages(context)
    return [{"role": "system", "content": rules(found[0]["content"])}] + (
        found[1:])


def binding_stop(context):
    """Return the stop of a request whose evidence is not bound to it (a
    case rejection), or None: the sufficiency checks do not apply."""
    if context.task == "prediction_explanation":
        stop = evidence.explanation_stop(context.request, context.bundle)
    else:
        stop = evidence.qa_stop(context.request, context.bundle)
    return stop if stop is not None and stop.outcome == BINDING else None


def slot_or_unresolved(statement, name, lookup):
    """Return a slot's text, or the unresolved mark when the bundle cannot
    render it."""
    try:
        return candidates.slot_text(statement, name, lookup)
    except candidates.CandidateError:
        return UNRESOLVED


def render_unchecked(candidate, bundle, concept_name=None):
    """Return a candidate's text rendered without checks, line by line as
    the released text is laid out."""
    lookup = candidates.Lookup(bundle, concept_name)
    lines = []
    for statement in candidate.statements:
        text = candidates.PLACEHOLDER.sub(
            lambda match, item=statement: slot_or_unresolved(
                item, match.group(1), lookup), statement.wording)
        depth = candidates.KINDS[statement.kind].depth
        lines.append(("  " * (depth - 1) + "- " + text) if depth else text)
    return "\n".join(lines)


def failure(reason, output=None, candidate=None):
    """Return the Written of a failure with its adapter reason."""
    return Written("operational_error", adapter.FALLBACK_CATEGORIES[reason],
                   reason, candidate, output=output)


def attempt(writer, context, template, vocabulary=None):
    """Write one request in the baseline condition: the same steps and
    limits as the controlled condition's attempt (generation.adapter),
    with the baseline's messages and no gate."""
    stop = binding_stop(context)
    if stop is not None:
        return Written(stop.outcome, None, stop.reasons[0],
                       reason_codes=stop.reasons)
    backend, limits = writer.backend, writer.limits
    written = messages(context)
    if backend.prompt_tokens(written) > limits.max_prompt_tokens:
        return failure("prompt_over_budget")
    if backend.text_tokens(shape.text(shape.document_of(template))) > (
            limits.max_output_tokens):
        return failure("output_over_budget")
    schema = shape.schema(context)
    started = time.monotonic()
    try:
        output = adapter.generate(writer, written, schema)
    except adapter.BackendError as error:
        return failure(error.reason)
    except Exception:  # noqa: BLE001 - a backend crash fails closed
        return failure("backend_failed")
    if time.monotonic() - started > limits.max_request_seconds:
        return failure("timeout", output)
    if not output.finished:
        return failure("truncated", output)
    try:
        document = json.loads(output.text)
    except ValueError:
        return failure("malformed", output)
    if shape.violations(document, schema):
        return failure("schema_violated", output)
    try:
        candidate = shape.candidate(context, document)
    except shape.ShapeError:
        return failure("malformed", output)
    from ehr_evidence_app.validation import gate
    text = render_unchecked(candidate, context.bundle,
                            gate.concept_namer(vocabulary))
    return Written(candidate.outcome, None, None, candidate, text,
                   tuple(candidate.reason_codes), output=output,
                   released=True)


# Default Mode: Invented Requests And Scripted Backends

def run_synthetic():
    """Return the problems of the baseline on the invented requests."""
    from ehr_evidence_app.qa import suite
    problems = []
    controlled = prompt.RULES.split("\n")
    kept = rules().split("\n")
    if len(controlled) - len(kept) != len(GROUNDING):
        problems.append("the baseline does not leave out exactly the "
                        "grounding rules")
    vocabulary = suite.synthetic_vocabulary()
    limits = adapter.scripted_limits()
    for context, template in shape.synthetic_contexts():
        if messages(context)[1:] != prompt.messages(context)[1:]:
            problems.append("the request's message differs")
        for expected, script in adapter.scripts(template):
            writer = adapter.Writer(adapter.ScriptedBackend(script), limits)
            found = attempt(writer, context, template, vocabulary)
            if expected is None or expected == "gate_rejected":
                if not found.released or found.candidate is None:
                    problems.append("a document the gate would see is not "
                                    "written")
            elif found.reason != expected:
                problems.append("a failure is not the controlled "
                                "condition's: %s" % expected)
    return sorted(set(problems))


def main(argv=None):
    """Run the default mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    problems = run_synthetic()
    for problem in problems:
        print("FAILED: %s" % problem)
    if problems:
        return 1
    print("baseline %s: prompt %s without rules %s" % (
        BASELINE_VERSION, prompt.PROMPT_VERSION,
        ", ".join(str(number) for number in GROUNDING)))
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
