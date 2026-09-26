"""The generation adapter: one request, one writer, one release (work
items 41 and 43).

For every response from evidence the language model writes the complete
candidate (ADR 0012, decision 1), and the adapter makes that the one
path from its output to a release:

1. the prompt (generation.prompt) and the request's schema
   (generation.shape) are built, and a prompt over its token budget, or a
   request whose template document already exceeds the output budget,
   ends the attempt before anything runs;
2. the backend writes once, as the attempt limit allows (only a failure
   of the backend itself is tried again), within the time limit, and one
   request at a time (the concurrency limit); output cut off at the token
   limit is never read;
3. the output must parse as JSON, meet its schema and stand for a
   candidate, which then leaves only through the release gate;
4. anything else, and no writer at all, is a fallback: the template
   candidate is released through the same gate instead, and the result
   records the reason (decision 4). Phase 9 counts every fallback as a
   failure of the model, never as its success.

The backends are the model worker (generation.runtime) and, for tests, a
scripted backend that writes any document or fails in any way. Nothing
here logs a prompt or a document; they hold patient records.

Usage:
    python -m ehr_evidence_app.generation.adapter
    python -m ehr_evidence_app.generation.adapter --operator-real \\
        --case-set dev-30-v1
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import json
import sys
import threading
import time

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.generation import prompt, shape
from ehr_evidence_app.validation import candidate as candidates

# Why a model's candidate was not released; closed, so that every fallback
# is counted under one reason.
FALLBACKS = ("no_model", "busy", "prompt_over_budget", "output_over_budget",
             "timeout", "resource_exhausted", "backend_failed", "truncated",
             "malformed", "schema_violated", "gate_rejected")
BACKEND_FAILURES = ("timeout", "resource_exhausted", "backend_failed")
WITHHELD = ("validation_rejection", "operational_error")


class BackendError(Exception):
    """A backend that could not write: timed out, ran out of memory or
    failed. Carries the reason only."""

    def __init__(self, reason):
        if reason not in BACKEND_FAILURES:
            raise ValueError("unknown backend failure: %s" % reason)
        super().__init__(reason)
        self.reason = reason


@dataclasses.dataclass(frozen=True)
class Output:
    """What a backend wrote: the text, token counts, whether it stopped by
    itself (False when cut off at the token limit), the seconds taken and,
    when known, the peak GPU memory."""

    text: str
    prompt_tokens: int
    output_tokens: int
    finished: bool
    seconds: float
    peak_memory_bytes: int | None = None


@dataclasses.dataclass(frozen=True)
class Written:
    """The response to a request from evidence, who wrote it and, for a
    fallback, why the model's candidate was not released.

    author is "model" or "template"; fallback is one of FALLBACKS, or None
    when the model's candidate was released; gate_reasons are the gate's
    reason codes for a model candidate it withheld; model_candidate is the
    model's candidate when its document stood for one.
    """

    response: contracts.Response
    candidate: candidates.Candidate | None
    author: str
    fallback: str | None = None
    gate_reasons: tuple = ()
    model_candidate: candidates.Candidate | None = None
    output: Output | None = None


class Writer:
    """A backend under the execution limits; one per application."""

    def __init__(self, backend, limits):
        """Hold a backend and the limits its requests run under."""
        self.backend = backend
        self.limits = limits
        self.slots = threading.BoundedSemaphore(
            limits.max_concurrent_requests)

    def describe(self):
        """Return what candidates are written with, for records."""
        return dict(prompt.describe(), **self.backend.describe())


class ScriptedBackend:
    """A test backend: script(messages, schema) returns an Output or raises
    BackendError. Tokens are counted as one per four characters."""

    def __init__(self, script):
        """Hold the script."""
        self.script = script
        self.calls = 0

    @staticmethod
    def count(text):
        """Return the tokens of a text, one per four characters."""
        return len(text) // 4 + 1

    def prompt_tokens(self, messages):
        """Return the tokens of a prompt."""
        return self.count("".join(message["content"]
                                  for message in messages))

    def text_tokens(self, text):
        """Return the tokens of a text."""
        return self.count(text)

    def generate(self, messages, schema, max_tokens, seconds):
        """Return the scripted output."""
        self.calls += 1
        return self.script(messages, schema)

    def describe(self):
        """Return what this backend is."""
        return {"backend": "scripted"}


def written_by_template(template, release, reason, **fields):
    """Return the Written of a template candidate released in place of the
    model's."""
    return Written(release(template), template, "template", reason,
                   **fields)


def generate(writer, messages, rules):
    """Return the backend's Output, trying a failed backend again within
    the attempt limit; raises BackendError when every attempt fails."""
    attempts = writer.limits.max_generation_attempts
    for attempt in range(attempts):
        try:
            return writer.backend.generate(
                messages, rules, writer.limits.max_output_tokens,
                writer.limits.max_request_seconds)
        except BackendError as error:
            if error.reason != "backend_failed" or attempt == attempts - 1:
                raise
    raise BackendError("backend_failed")


def write(writer, context, template, release):
    """Return the Written of a request from evidence: the model's
    candidate when it passes the gate, otherwise the template's.

    context is the request's shape.Context; template the template
    candidate, which passes the gate; release(candidate) returns the
    gate's Response for this request.
    """
    if writer is None:
        return written_by_template(template, release, "no_model")
    if not writer.slots.acquire(blocking=False):
        return written_by_template(template, release, "busy")
    try:
        return attempt(writer, context, template, release)
    finally:
        writer.slots.release()


def attempt(writer, context, template, release):
    """Run the model's one attempt under the limits."""
    backend, limits = writer.backend, writer.limits
    messages = prompt.messages(context)
    if backend.prompt_tokens(messages) > limits.max_prompt_tokens:
        return written_by_template(template, release, "prompt_over_budget")
    if backend.text_tokens(shape.text(shape.document_of(template))) > (
            limits.max_output_tokens):
        return written_by_template(template, release, "output_over_budget")
    rules = shape.schema(context)
    started = time.monotonic()
    try:
        output = generate(writer, messages, rules)
    except BackendError as error:
        return written_by_template(template, release, error.reason)
    except Exception:  # noqa: BLE001 - a backend crash fails closed
        return written_by_template(template, release, "backend_failed")
    if time.monotonic() - started > limits.max_request_seconds:
        return written_by_template(template, release, "timeout",
                                   output=output)
    if not output.finished:
        return written_by_template(template, release, "truncated",
                                   output=output)
    try:
        document = json.loads(output.text)
    except ValueError:
        return written_by_template(template, release, "malformed",
                                   output=output)
    if shape.violations(document, rules):
        return written_by_template(template, release, "schema_violated",
                                   output=output)
    try:
        candidate = shape.candidate(context, document)
    except shape.ShapeError:
        return written_by_template(template, release, "malformed",
                                   output=output)
    response = release(candidate)
    if response.outcome in WITHHELD:
        return written_by_template(template, release, "gate_rejected",
                                   gate_reasons=response.reason_codes,
                                   model_candidate=candidate, output=output)
    return Written(response, candidate, "model", model_candidate=candidate,
                   output=output)


# --------------------------------------------------------------------------
# Scripted runs: every way a writer can fail, on the template's answers
# --------------------------------------------------------------------------

def output(text, finished=True):
    """Return a scripted Output of a text."""
    return Output(text=text, prompt_tokens=0, output_tokens=len(text) // 4,
                  finished=finished, seconds=0.0)


def raising(reason):
    """Return a script that fails with a backend failure."""
    def script(messages, schema):
        """Fail."""
        raise BackendError(reason)
    return script


def crashing(messages, schema):
    """A script whose backend crashes."""
    raise RuntimeError("backend crashed")


def scripts(template):
    """Return (expected fallback or None, script) pairs for one request:
    the template's own document written by the model, then each way the
    writer can fail."""
    document = shape.document_of(template)
    correct = shape.text(document)
    wrong = dict(document, outcome="abstention")
    found = [(None, lambda messages, schema: output(correct)),
             ("truncated", lambda messages, schema: output(
                 correct[:len(correct) // 2], finished=False)),
             ("malformed", lambda messages, schema: output(
                 correct[:len(correct) // 2])),
             ("schema_violated", lambda messages, schema: output("[]")),
             ("schema_violated", lambda messages, schema: output(
                 shape.text(wrong))),
             ("timeout", raising("timeout")),
             ("resource_exhausted", raising("resource_exhausted")),
             ("backend_failed", raising("backend_failed")),
             ("backend_failed", crashing)]
    if document.get("reason_codes") is not None and template.outcome in (
            "answer", "not_recorded"):
        # Right shape, wrong fact: a disclosure the scope does not call
        # for, or one it calls for left out.
        codes = [] if document["reason_codes"] else [
            "unmapped_records_in_scope"]
        found.append(("gate_rejected", lambda messages, schema: output(
            shape.text(dict(document, reason_codes=codes)))))
    if template.task == "prediction_explanation":
        found.append(("gate_rejected", lambda messages, schema: output(
            shape.text(dict(document, notices=[
                notice for notice in document["notices"]
                if notice != "score_meaning"])))))
    return found


def released_as_template(written, template_response):
    """Return True when a fallback released exactly the template's
    response."""
    return (written.author == "template"
            and written.response == template_response)


def run_scripts(pairs, release_of, limits):
    """Run every script on (context, template) pairs; return counts by
    expected outcome: (runs, as expected)."""
    counts = collections.defaultdict(lambda: [0, 0])
    for context, template in pairs:
        release = release_of(context)
        template_response = release(template)
        for expected, script in scripts(template):
            writer = Writer(ScriptedBackend(script), limits)
            written = write(writer, context, template, release)
            key = expected or "model_released"
            counts[key][0] += 1
            if expected is None:
                counts[key][1] += (written.author == "model"
                                   and written.response == template_response
                                   and written.fallback is None)
            else:
                counts[key][1] += (written.fallback == expected
                                   and released_as_template(
                                       written, template_response))
        for expected, limit in (("prompt_over_budget",
                                 {"max_prompt_tokens": 1}),
                                ("output_over_budget",
                                 {"max_output_tokens": 1})):
            tight = limits.model_copy(update=limit)
            backend = ScriptedBackend(scripts(template)[0][1])
            written = write(Writer(backend, tight), context, template,
                            release)
            counts[expected][0] += 1
            counts[expected][1] += (written.fallback == expected
                                    and backend.calls == 0
                                    and released_as_template(
                                        written, template_response))
        written = write(None, context, template, release)
        counts["no_model"][0] += 1
        counts["no_model"][1] += (written.fallback == "no_model"
                                  and released_as_template(
                                      written, template_response))
    return {key: {"runs": value[0], "as_expected": value[1]}
            for key, value in sorted(counts.items())}


def gate_release(context, vocabulary=None):
    """Return the release function of a request: its gate call."""
    # Imported here: the gate's explanation check imports this package.
    from ehr_evidence_app.validation import gate
    request = context.request

    def release(candidate):
        """Release a candidate through the gate for this request."""
        return gate.release(candidate, request.request_id, request.case,
                            request.scope, context.bundle,
                            context.bundle_sha256, vocabulary,
                            condition=context.asked.name
                            if context.asked else None)
    return release


def scripted_limits():
    """Return the invented limits, with the output ceiling as room for
    the scripted documents."""
    from ehr_evidence_app.evidence import bundles
    return contracts.ExecutionLimits(**dict(
        bundles.SYNTHETIC_LIMITS,
        max_output_tokens=contracts.LIMIT_CEILINGS["max_output_tokens"],
        max_prompt_tokens=contracts.LIMIT_CEILINGS["max_prompt_tokens"]))


def run_synthetic():
    """Return the scripted-run counts on the invented fixtures."""
    from ehr_evidence_app.qa import suite
    vocabulary = suite.synthetic_vocabulary()
    return run_scripts(shape.synthetic_contexts(),
                       lambda context: gate_release(context, vocabulary),
                       scripted_limits())


class DevelopmentError(Exception):
    """A development case set that cannot be used, carrying a finding
    code only."""


def development_requests(case_set):
    """Return ((context, template) pairs, release function maker) for a
    development case set: a list and presence checks (a recorded
    condition and one recorded elsewhere) in every scope bundle, and the
    explanation of every eligible case. The evaluation set is refused."""
    from ehr_evidence_app.casesets import registry
    from ehr_evidence_app.evidence import readers
    from ehr_evidence_app.gnn import mapping
    from ehr_evidence_app.qa import answer, resolve, templates
    from ehr_evidence_app.validation import evidence, explanation
    if case_set != mapping.PILOT_LABEL and registry.spec(case_set)[
            "purpose"] != "development":
        raise DevelopmentError("case_set_held_out")
    vocabulary = resolve.load()
    bundle_set = answer.load_bundle_set(answer.bundle_set_plan(case_set))
    pairs = []
    loaded = [(case_number, scope) + bundle_set.qa_bundle(case_number, scope)
              for case_number, scope in sorted(
                  bundle_set.entries, key=lambda key: (
                      key[0], key[1].kind, key[1].visit_number or 0))]
    recorded = sorted({event.concept.concept_id
                       for *_, bundle, _ in loaded for event in bundle.events
                       if event.concept.concept_id in vocabulary.rows})
    for number, (case_number, scope, bundle, digest) in enumerate(loaded, 1):
        case = contracts.CaseRef(case_set=case_set, case_number=case_number)
        ref = contracts.BundleRef(bundle_id=bundle.bundle_id, sha256=digest)
        rid = "w-%05d" % number

        def request(task):
            """Return a request of a task in this scope."""
            return contracts.Request(schema_version="1.0", request_id=rid,
                                     task=task, case=case, scope=scope,
                                     question="Development request.")

        template, _ = templates.list_candidate(rid, case, scope, bundle, ref)
        pairs.append((shape.Context(request("condition_list"), bundle,
                                    digest), template))
        mine = sorted({event.concept.concept_id for event in bundle.events
                       if event.concept.concept_id in vocabulary.rows})
        other = next((concept for concept in recorded
                      if concept not in mine), None)
        for concept in mine[:1] + ([other] if other else []):
            resolution = resolve.Resolution("", "resolved", "concepts",
                                            concepts=(concept,))
            template = templates.presence_candidate(
                rid, case, scope, bundle, ref, resolution,
                answer.record_matches(bundle, resolution, vocabulary))
            pairs.append((shape.Context(
                request("condition_presence"), bundle, digest,
                shape.asked(resolution, bundle, vocabulary)), template))
    plan = readers.real_plan(None if case_set == mapping.PILOT_LABEL
                             else case_set)
    cases = {case.case_number: case
             for case in readers.read_cases(plan).cases if case.admitted}
    for case_number, bundle, digest in explanation.read_bundles(case_set):
        request = contracts.Request(
            schema_version="1.0", request_id="x-%03d" % case_number,
            task="prediction_explanation", case=bundle.case,
            scope=bundle.scope, prediction=contracts.PredictionRef(
                output_record_sha256=cases[
                    case_number].output_record_sha256))
        if evidence.explanation_stop(request, bundle) is None:
            pairs.append((shape.Context(request, bundle, digest),
                          explanation.template(request, bundle, digest)))
    return pairs, lambda context: gate_release(
        context, vocabulary if context.task != "prediction_explanation"
        else None)


def passed(counts):
    """Return True when every scripted run ended as expected."""
    return all(entry["runs"] == entry["as_expected"]
               for entry in counts.values())


def report(counts):
    """Print scripted-run counts: aggregates only."""
    for key, entry in counts.items():
        print("%-36s %d of %d" % (key, entry["as_expected"], entry["runs"]))


def main(argv=None):
    """Run every script on the invented fixtures or a development case
    set; prints aggregate counts only."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="use a development case set; prints aggregate "
                             "counts only")
    parser.add_argument("--case-set",
                        help="the development case set (dev-30-v1 or "
                             "pilot-10-v1)")
    args = parser.parse_args(argv)
    if args.operator_real != bool(args.case_set):
        print("FAILED: --operator-real and --case-set go together")
        return 2
    if args.operator_real:
        from pydantic import ValidationError

        from ehr_evidence_app.casesets import registry
        from ehr_evidence_app.evidence import readers
        from ehr_evidence_app.gnn import mapping
        from ehr_evidence_app.qa import answer, resolve
        from ehr_evidence_app.validation import explanation
        try:
            pairs, release_of = development_requests(args.case_set)
        except (DevelopmentError, registry.RegistryError,
                readers.ReaderError, mapping.MappingError,
                answer.AnswerError, resolve.ResolutionError,
                explanation.ExplanationError, ValidationError) as error:
            print("FAILED: %s" % type(error).__name__)
            return 1
        counts = run_scripts(pairs, release_of, scripted_limits())
    else:
        counts = run_synthetic()
    report(counts)
    print("fallback reasons: %s" % ", ".join(FALLBACKS))
    ok = passed(counts)
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
