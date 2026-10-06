"""The paired runner of the final evaluation (work item 57; ADR 0028,
decision 6).

Every request is written once in each condition, in the same order for
both, with one attempt each:

* controlled: the checks before generation (validation.evidence), then
  the model's one attempt through the adapter and the release gate
  (generation.adapter). Its outcome is the response it releases. When the
  model's candidate is not released, the outcome is the failure the
  fallback names (ADR 0012, decision 4): a validation rejection when the
  gate withheld it, otherwise an operational error of its category. The
  template the application would release in its place is never
  credited;
* baseline: the same request without the grounding rules, the
  sufficiency checks and the gate (assessment.baseline).

With a run directory, each request writes one protected record holding
the request, its bundle's identity, and for each condition the outcome
and its category, the reason, the reason codes and the gate's reasons,
the digest of the prompt, the backend's output, the candidate, the text,
the tokens, the seconds and the peak GPU memory. Records hold patient
records and are written only beneath the protected root, never logged or
printed. A record is written once; a run is one pass, and a run that
stops is started again as a new run in a new directory. The run's
summary holds aggregates only: outcomes and failure categories by task
and condition, and the distribution of seconds, tokens and peak GPU
memory (ADR 0016, decision 8).

Invented challenges (work item 55) check that each condition ends each
invalid case and each operational failure as it should. These are a
bundle of another case, another scope or another prediction, a history
with a gap, a record without a name, incomplete coverage, no positive
attribution and an ineligible explanation, and each failure of a
scripted backend. A case rejection is shared; the controlled condition
abstains where the evidence does not suffice, and the baseline writes
there; a failure is the same failure in both.

The operator mode runs a development case set's requests, as the
development benchmark builds them, with the pinned model. It refuses the
evaluation set until the freeze (work item 64) provides for it.

Usage:
    python -m ehr_evidence_app.assessment.runner
    python -m ehr_evidence_app.assessment.runner --operator-real \\
        --case-set dev-30-v1 --per-group 2
"""

from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import os
import sys

import ehr_evidence_app
from ehr_evidence_app.assessment import baseline, statistics
from ehr_evidence_app.generation import adapter, prompt, shape
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.storage import policy as guard
from ehr_evidence_app.validation import evidence

RECORD_SCHEMA = "paired-request"
RUN_SCHEMA = "paired-run"
SCHEMA_VERSION = "1.0"
CONDITIONS = ("controlled", "baseline")
PROTECTED_ROOT = guard.PRIVATE_ROOT + "\\protected-data"
OUTPUT_ROOT = PROTECTED_ROOT + "\\assessment\\paired"
RECORDS = "requests"
BUILDER = "ehr_evidence_app.assessment.runner %s" % (
    ehr_evidence_app.__version__)


class RunnerError(Exception):
    """A fail-closed failure carrying a finding code only."""


def stop_of(context):
    """Return the controlled condition's stop before generation, or
    None."""
    if context.task == "prediction_explanation":
        return evidence.explanation_stop(context.request, context.bundle)
    return evidence.qa_stop(context.request, context.bundle)


def controlled(writer, context, template, release):
    """Write one request in the controlled condition."""
    stop = stop_of(context)
    if stop is not None:
        return baseline.Written(stop.outcome, None, stop.reasons[0],
                                reason_codes=stop.reasons)
    written = adapter.attempt(writer, context, template, release)
    if written.author == "model":
        response = written.response
        return baseline.Written(response.outcome, None, None,
                                written.candidate, response.text,
                                tuple(response.reason_codes),
                                output=written.output, released=True)
    category = adapter.FALLBACK_CATEGORIES[written.fallback]
    return baseline.Written(
        "validation_rejection" if category == "validation_rejection"
        else "operational_error", category, written.fallback,
        written.model_candidate, None, (), tuple(written.gate_reasons),
        written.output)


def prompt_digest(messages):
    """Return the SHA-256 of a prompt's messages."""
    return hashlib.sha256(json.dumps(messages, sort_keys=True).encode(
        "utf-8")).hexdigest()


def condition_document(written, messages):
    """Return one condition's part of a request's record."""
    output = written.output
    return {
        "outcome": written.outcome, "category": written.category,
        "reason": written.reason, "released": written.released,
        "reason_codes": list(written.reason_codes),
        "gate_reasons": list(written.gate_reasons),
        "prompt_sha256": prompt_digest(messages) if messages else None,
        "output": output.text if output is not None else None,
        "finished": output.finished if output is not None else None,
        "candidate": (written.candidate.model_dump(mode="json")
                      if written.candidate is not None else None),
        "text": written.text,
        "prompt_tokens": output.prompt_tokens if output else None,
        "output_tokens": output.output_tokens if output else None,
        "seconds": output.seconds if output else None,
        "peak_memory_bytes": output.peak_memory_bytes if output else None}


def request_document(context, template):
    """Return the request part of a record, with its stratum."""
    from ehr_evidence_app.assessment import references
    request = context.request
    return {"request_id": request.request_id, "task": request.task,
            "stratum": references.stratum_of(context, template),
            "case_set": request.case.case_set,
            "case_number": request.case.case_number,
            "scope": request.scope.model_dump(mode="json"),
            "asked": (list(context.asked.name.concepts)
                      if context.asked is not None else None),
            "limit": context.limit,
            "bundle": {"bundle_id": context.bundle.bundle_id,
                       "sha256": context.bundle_sha256}}


def record_of(run_id, number, context, template, written):
    """Return the protected record of one request written in both
    conditions; written maps each condition to (Written, messages)."""
    return {"schema": RECORD_SCHEMA, "schema_version": SCHEMA_VERSION,
            "run": run_id, "number": number,
            "request": request_document(context, template),
            "conditions": {name: condition_document(*written[name])
                           for name in CONDITIONS}}


def record_bytes(document):
    """Return the canonical bytes of a record."""
    return (json.dumps(document, sort_keys=True, separators=(",", ":"))
            + "\n").encode("utf-8")


def write_once(path, document):
    """Write a record that must not exist yet."""
    with open(path, "xb") as handle:
        handle.write(record_bytes(document))
        handle.flush()
        os.fsync(handle.fileno())


class Tally:
    """Aggregate counts and measures of a run: nothing of a request."""

    def __init__(self):
        """Start empty."""
        self.outcomes = collections.Counter()
        self.categories = collections.Counter()
        self.measures = collections.defaultdict(list)

    def add(self, task, name, written):
        """Count one condition's result for one request."""
        self.outcomes[(name, task, written.outcome)] += 1
        if written.category:
            self.categories[(name, task, written.category)] += 1
        output = written.output
        if output is None:
            return
        for measure, value in (("seconds", output.seconds),
                               ("prompt_tokens", output.prompt_tokens),
                               ("output_tokens", output.output_tokens),
                               ("peak_memory_bytes",
                                output.peak_memory_bytes)):
            if value is not None:
                self.measures[(name, task, measure)].append(value)

    def document(self, requests):
        """Return the run's summary."""
        return {
            "requests": requests,
            "outcomes": [[*key, count] for key, count
                         in sorted(self.outcomes.items())],
            "categories": [[*key, count] for key, count
                           in sorted(self.categories.items())],
            "measures": [[*key, statistics.distribution(values)]
                         for key, values in sorted(self.measures.items())]}


def run(pairs, writer, release_of, vocabulary=None, directory=None,
        run_id="unrecorded", kept=None):
    """Write every (context, template) pair in both conditions; return the
    run's summary. With a directory, each request's record is written
    there; with a list, each record is kept in it instead (for invented
    data only)."""
    tally = Tally()
    for number, (context, template) in enumerate(pairs, 1):
        written = {
            "controlled": (controlled(writer, context, template,
                                      release_of(context)),
                           prompt.messages(context)),
            "baseline": (baseline.attempt(writer, context, template,
                                          vocabulary),
                         baseline.messages(context))}
        for name in CONDITIONS:
            tally.add(context.task, name, written[name][0])
        record = None
        if directory is not None:
            record = record_of(run_id, number, context, template, written)
            write_once(os.path.join(directory, RECORDS, "%05d.json" % number),
                       record)
        if kept is not None:
            kept.append(record or record_of(run_id, number, context,
                                            template, written))
    return tally.document(len(pairs))


# A Run's Directory

def run_identifier(case_set, now=None):
    """Return a run's identifier: its case set and UTC start time."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return "%s-%s" % (case_set, now.strftime("%Y%m%dT%H%M%SZ"))


def open_run(run_id, configuration, root=OUTPUT_ROOT,
             protected_root=PROTECTED_ROOT):
    """Create a run's directory beneath the protected root, refusing one
    that exists, and write its configuration; return the directory."""
    directory = os.path.join(root, run_id)
    problem = mapping.output_problem(directory, protected_root)
    if problem:
        raise RunnerError(problem)
    if os.path.lexists(directory):
        raise RunnerError("run_exists")
    os.makedirs(os.path.join(directory, RECORDS))
    write_once(os.path.join(directory, "run.json"), dict(
        configuration, schema=RUN_SCHEMA, schema_version=SCHEMA_VERSION,
        run=run_id, builder=BUILDER))
    return directory


def close_run(directory, summary):
    """Write a finished run's summary."""
    write_once(os.path.join(directory, "summary.json"), summary)


def configuration_of(case_set, writer, requests, commit,
                     freeze_sha256=None):
    """Return what a run is written with; a run of the final case set
    names the freeze record it was written under."""
    return {"case_set": case_set, "requests": requests, "commit": commit,
            "freeze_sha256": freeze_sha256,
            "writer": writer.describe(),
            "baseline": {"version": baseline.BASELINE_VERSION,
                         "grounding_rules_left_out": list(
                             baseline.GROUNDING)},
            "limits": writer.limits.model_dump(mode="json")}


# Challenges And The Default Mode

def challenges():
    """Return (name, context, template, expected outcome per condition)
    of the invented invalid cases. None as a baseline expectation means
    "writes from the evidence": anything but the controlled condition's
    abstention."""
    from ehr_evidence_app.evidence import bundles
    found = []
    pairs = shape.synthetic_contexts()
    listed = next((context, template) for context, template in pairs
                  if context.task == "condition_list"
                  and context.bundle.events
                  and context.bundle.scope.kind == "case_visits")
    context, template = listed
    for stop, faulty in evidence.synthetic_faults(context.bundle):
        changed = shape.Context(context.request, faulty,
                                context.bundle_sha256)
        found.append(("list: " + stop.reasons[0], changed, template, {
            "controlled": stop.outcome,
            "baseline": stop.outcome if stop.outcome == "case_rejection"
            else None}))
    _, explanations, _ = bundles.run_synthetic()
    explained = next((context, template) for context, template in pairs
                     if context.task == "prediction_explanation"
                     and context.limit is None)
    for stop, request, faulty in evidence.explanation_faults(
            explanations[0]):
        changed = shape.Context(request, faulty, explained[0].bundle_sha256)
        found.append(("explanation: " + stop.reasons[0], changed,
                      explained[1], {
            "controlled": stop.outcome,
            "baseline": stop.outcome if stop.outcome == "case_rejection"
            else None}))
    return found


def run_challenges(limits, vocabulary):
    """Return {challenge: (expected, found) per condition} of the invented
    invalid cases and of every scripted failure, both conditions written
    with a backend that writes the template's own document."""
    found = {}
    for name, context, template, expected in challenges():
        script = adapter.scripts(template)[0][1]
        writer = adapter.Writer(adapter.ScriptedBackend(script), limits)
        release = adapter.gate_release(
            context, vocabulary if context.task != "prediction_explanation"
            else None)
        got = {"controlled": controlled(writer, context, template, release),
               "baseline": baseline.attempt(writer, context, template,
                                            vocabulary)}
        found[name] = {condition: (expected[condition],
                                   got[condition].outcome)
                       for condition in CONDITIONS}
    context, template = next(
        (context, template) for context, template
        in shape.synthetic_contexts() if context.task == "condition_list"
        and template.outcome == "answer")
    release = adapter.gate_release(context, vocabulary)
    for number, (expected, script) in enumerate(adapter.scripts(template)):
        if expected in (None, "gate_rejected"):
            continue
        writer = adapter.Writer(adapter.ScriptedBackend(script), limits)
        category = adapter.FALLBACK_CATEGORIES[expected]
        got = {"controlled": controlled(writer, context, template, release),
               "baseline": baseline.attempt(writer, context, template,
                                            vocabulary)}
        found["failure %d: %s" % (number, expected)] = {
            condition: (category, got[condition].category)
            for condition in CONDITIONS}
    for expected, limit in (("prompt_over_budget", {"max_prompt_tokens": 1}),
                            ("output_over_budget",
                             {"max_output_tokens": 1})):
        writer = adapter.Writer(adapter.ScriptedBackend(
            adapter.scripts(template)[0][1]), limits.model_copy(update=limit))
        category = adapter.FALLBACK_CATEGORIES[expected]
        got = {"controlled": controlled(writer, context, template, release),
               "baseline": baseline.attempt(writer, context, template,
                                            vocabulary)}
        found["failure: %s" % expected] = {
            condition: (category, got[condition].category)
            for condition in CONDITIONS}
    return found


def challenge_problems(found):
    """Return the challenges that did not end as expected."""
    problems = []
    for name, conditions in sorted(found.items()):
        for condition, (expected, got) in conditions.items():
            if expected is None:
                if got == found[name]["controlled"][0]:
                    problems.append("%s: the %s abstained" % (name,
                                                              condition))
            elif got != expected:
                problems.append("%s: the %s gave %s" % (name, condition,
                                                        got))
    return problems


def run_synthetic():
    """Return (summary, challenge results, problems) of the invented
    requests written in both conditions by a backend that writes each
    template's own document, and of the challenges."""
    from ehr_evidence_app.qa import suite
    vocabulary = suite.synthetic_vocabulary()
    limits = adapter.scripted_limits()
    pairs = shape.synthetic_contexts()
    problems = []
    tally = Tally()
    for context, template in pairs:
        writer = adapter.Writer(adapter.ScriptedBackend(
            adapter.scripts(template)[0][1]), limits)
        release = adapter.gate_release(
            context, vocabulary if context.task != "prediction_explanation"
            else None)
        written = {"controlled": controlled(writer, context, template,
                                            release),
                   "baseline": baseline.attempt(writer, context, template,
                                                vocabulary)}
        for name in CONDITIONS:
            tally.add(context.task, name, written[name])
        one, two = written["controlled"], written["baseline"]
        if not one.released or one.outcome != template.outcome:
            problems.append("a correct document is not released")
        if (two.candidate != one.candidate or two.outcome != one.outcome
                or two.text != one.text):
            problems.append("the baseline reads a correct document "
                            "otherwise than the controlled condition")
    found = run_challenges(limits, vocabulary)
    problems += challenge_problems(found)
    return tally.document(len(pairs)), found, sorted(set(problems))


def report(summary):
    """Print a run's summary: counts and distributions only."""
    print("requests: %d" % summary["requests"])
    for name, task, outcome, count in summary["outcomes"]:
        print("%-10s %-24s %-22s %d" % (name, task, outcome, count))
    for name, task, category, count in summary["categories"]:
        print("%-10s %-24s failure %-14s %d" % (name, task, category, count))


# The Operator Mode: A Development Case Set With The Pinned Model

def run_real(case_set, per_group, task=None):
    """Write a case set's sampled requests in both conditions with the
    pinned model, into a new protected run directory; return the summary.
    A development case set is sampled; the final case set needs the
    freeze, and every request of it is written (per_group is not used)."""
    from ehr_evidence_app.assessment import freeze
    from ehr_evidence_app.evidence import contracts
    from ehr_evidence_app.generation import measure, runtime
    from ehr_evidence_app.qa import resolve
    from ehr_evidence_app.tracking import measurements
    try:
        frozen = freeze.allowed(case_set)
    except freeze.FreezeError as error:
        raise RunnerError(str(error)) from None
    if guard.fixed_policy_problem():
        raise RunnerError("fixed_policy_rejected")
    commit = measurements.code_commit()
    pairs, release_of = adapter.development_requests(
        case_set, final=frozen is not None)
    chosen = (pairs if frozen is not None
              else measure.chosen_requests(pairs, per_group, task))
    writer = adapter.Writer(runtime.WorkerBackend(), contracts.load_limits())
    run_id = run_identifier(case_set)
    directory = open_run(run_id, configuration_of(
        case_set, writer, len(chosen), commit,
        None if frozen is None else frozen[1]))
    summary = run(chosen, writer, release_of, resolve.load(), directory,
                  run_id)
    if measurements.code_commit() != commit:
        raise RunnerError("code_changed_during_run")
    close_run(directory, summary)
    return summary


def main(argv=None):
    """Run the synthetic default mode or the operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="write a development case set's requests in "
                             "both conditions with the pinned model; "
                             "prints counts only")
    parser.add_argument("--case-set", default="dev-30-v1")
    parser.add_argument("--per-group", type=int, default=2)
    parser.add_argument("--task", default=None)
    args = parser.parse_args(argv)
    try:
        if args.operator_real:
            report(run_real(args.case_set, args.per_group, args.task))
        else:
            summary, found, problems = run_synthetic()
            report(summary)
            print("challenges: %d" % len(found))
            for problem in problems:
                print("FAILED: %s" % problem)
            if problems:
                return 1
    except RunnerError as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
