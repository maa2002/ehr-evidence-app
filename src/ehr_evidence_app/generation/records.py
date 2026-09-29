"""Protected runtime records (Phase 7, work item 46; ADR 0012, decision 5).

Every request the application answers can be traced from one record,
written under the workspace's protected data on G:
(protected-data\\runtime-records). A record holds the request (its task,
case, scope and question), the evidence it was answered from (the
bundle's identity and digest), the versions and configuration digests of
everything that wrote and checked the response, and the released
response. When the response was written from evidence it also holds who
wrote it, the model or the template, and for a fallback why, with the
reason's category, the model's own output and candidate, and its tokens,
seconds and peak GPU memory.

Records hold patient records: they are written only under the protected
root, never logged, printed or committed. A record is written once and
never overwritten: one JSON file per request, named by the record's
identifier, in a directory per UTC day. Routine logs hold statuses and
counts only, and aggregate() sums records into counts that name nothing
from a record.

Usage:
    python -m ehr_evidence_app.generation.records
    python -m ehr_evidence_app.generation.records --operator-real \\
        --aggregate
"""

from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import statistics
import sys
import tempfile
import uuid
from typing import Literal, Optional

from pydantic import Field, ValidationError

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.generation import adapter, prompt
from ehr_evidence_app.storage import policy as guard
from ehr_evidence_app.validation import candidate as candidates

SCHEMA_VERSION = "1.0"
RECORDS_ROOT = guard.PRIVATE_ROOT + "\\protected-data\\runtime-records"
PROTECTED_ROOT = guard.PRIVATE_ROOT + "\\protected-data"
CONFIG_FILES = ("execution_limits.json", "explanation.json",
                "generation.json")
MAX_RECORD_BYTES = 4 * 1024 * 1024


class RecordError(Exception):
    """A record that cannot be written or read; carries a short code."""


class Generation(contracts.Frozen):
    """Who wrote a response from evidence and how: the model or the
    template, the fallback and its category when the model's candidate
    was not released, the gate's reasons for a candidate it withheld, the
    backend, and the model's output with its measures."""

    author: Literal["model", "template"]
    fallback: Optional[Literal[adapter.FALLBACKS]] = None
    category: Optional[str] = None
    gate_reasons: tuple[str, ...] = ()
    backend: dict
    prompt_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    finished: Optional[bool] = None
    seconds: Optional[float] = None
    peak_memory_bytes: Optional[int] = None
    output: Optional[str] = None
    model_candidate: Optional[dict] = None


class Record(contracts.Frozen):
    """One request, its evidence, the versions that answered it and its
    response; the Generation when the response was written from
    evidence."""

    schema_version: Literal["1.0"]
    record_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    created: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
                                 r"(\.\d{1,6})?\+00:00$")
    request_id: str
    task: Optional[str] = None
    case: contracts.CaseRef
    scope: contracts.ScopeRef
    question: Optional[str] = None
    prediction: Optional[dict] = None
    bundle: Optional[contracts.BundleRef] = None
    bundle_sha256: Optional[str] = Field(default=None,
                                         pattern=r"^[0-9a-f]{64}$")
    versions: dict
    response: contracts.Response
    response_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    generation: Optional[Generation] = None


def config_digest(name):
    """Return the SHA-256 of a configuration file of the repository."""
    with open(os.path.join(guard.REPO_ROOT, "config", name), "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def versions():
    """Return the versions of everything that writes and checks a
    response, and the SHA-256 of the configuration it reads."""
    from ehr_evidence_app import __version__
    from ehr_evidence_app.qa import answer, interpret, record_answer, \
        record_interpret, record_resolve, resolve
    from ehr_evidence_app.validation import explanation, language
    found = {"application": __version__}
    found.update(prompt.describe())
    found.update({"candidate_contract": candidates.CANDIDATE_VERSION,
                  "question_interpretation": interpret.RULES_VERSION,
                  "condition_resolution": resolve.RULES_VERSION,
                  "qa_answer": answer.RULES_VERSION,
                  "record_questions": record_interpret.RULES_VERSION,
                  "record_resolution": record_resolve.RULES_VERSION,
                  "record_answer": record_answer.RULES_VERSION,
                  "explanation_template": explanation.TEMPLATE_VERSION,
                  "wording_rules": language.RULES_VERSION})
    for name in CONFIG_FILES:
        found[name.replace(".json", "_sha256")] = config_digest(name)
    return found


def generation_of(written, backend):
    """Return the Generation of an adapter.Written, or None when the
    response was not written from evidence: a notice, or a request the
    checks before generation stopped, has no author but its template and
    no fallback."""
    if written is None or (written.author == "template"
                           and written.fallback is None):
        return None
    output = written.output
    return Generation(
        author=written.author, fallback=written.fallback,
        category=written.category, gate_reasons=tuple(written.gate_reasons),
        backend=dict(backend),
        prompt_tokens=output.prompt_tokens if output else None,
        output_tokens=output.output_tokens if output else None,
        finished=output.finished if output else None,
        seconds=float(output.seconds) if output else None,
        peak_memory_bytes=output.peak_memory_bytes if output else None,
        output=output.text if output else None,
        model_candidate=written.model_candidate.model_dump(mode="json")
        if written.model_candidate is not None else None)


def build(response, created, record_id, found_versions, request=None,
          question=None, bundle_sha256=None, written=None,
          backend=None):
    """Return the Record of one response. request is the bound Request
    when there is one; question the text asked when no request was
    bound; written the adapter.Written of a response from evidence."""
    if request is not None:
        question = request.question
    return Record(
        schema_version=SCHEMA_VERSION, record_id=record_id,
        created=created.isoformat(timespec="microseconds"),
        request_id=response.request_id, task=response.task,
        case=response.case, scope=response.scope, question=question,
        prediction=request.prediction.model_dump(mode="json")
        if request is not None and request.prediction is not None
        else None,
        bundle=response.bundle, bundle_sha256=bundle_sha256,
        versions=dict(found_versions), response=response,
        response_sha256=hashlib.sha256(
            response.model_dump_json().encode("utf-8")).hexdigest(),
        generation=generation_of(written, backend or {"backend": "none"}))


def containment_problem(root, protected=PROTECTED_ROOT):
    """Return why a records root is not a directory under the protected
    data of the workspace, or None."""
    resolved = os.path.normcase(os.path.abspath(root))
    if not resolved.startswith(os.path.normcase(
            os.path.abspath(protected)) + os.sep):
        return "records_root_not_protected"
    return None


def store(root, record):
    """Write a record atomically under root, in its UTC day's directory;
    never over an existing one. Return its path."""
    directory = os.path.join(root, record.created[:10])
    path = os.path.join(directory, record.record_id + ".json")
    os.makedirs(directory, exist_ok=True)
    data = record.model_dump_json().encode("utf-8")
    if len(data) > MAX_RECORD_BYTES:
        raise RecordError("record_too_large")
    handle, temporary = tempfile.mkstemp(prefix=".record-", suffix=".tmp",
                                         dir=directory)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        try:
            os.link(temporary, path)
        except FileExistsError:
            raise RecordError("record_exists") from None
    finally:
        os.remove(temporary)
    return path


def read(path):
    """Return the Record in a file."""
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_RECORD_BYTES + 1)
    except OSError:
        raise RecordError("record_unreadable") from None
    if len(data) > MAX_RECORD_BYTES:
        raise RecordError("record_too_large")
    try:
        return Record.model_validate_json(data)
    except ValidationError:
        raise RecordError("record_invalid") from None


def paths(root=RECORDS_ROOT):
    """Return the record files under a records root, in order."""
    found = []
    for directory, names, files in os.walk(root):
        names.sort()
        found += [os.path.join(directory, name) for name in sorted(files)
                  if name.endswith(".json") and not name.startswith(".")]
    return found


class Recorder:
    """Writes one protected record for each response it is given.

    root is the records directory, which must lie under protected, the
    workspace's protected data; writer, when the application has one, the
    generation.adapter.Writer whose backend a record names; clock returns
    the current UTC time.
    """

    def __init__(self, root=RECORDS_ROOT, writer=None, clock=None,
                 protected=PROTECTED_ROOT):
        problem = containment_problem(root, protected)
        if problem:
            raise RecordError(problem)
        self.root = root
        self.writer = writer
        self.clock = clock or (
            lambda: datetime.datetime.now(datetime.timezone.utc))
        self.versions = versions()

    def backend(self):
        """Return the description of the backend that writes, if any."""
        if self.writer is None:
            return {"backend": "none"}
        return self.writer.backend.describe()

    def record(self, response, request=None, question=None,
               bundle_sha256=None, written=None):
        """Write the record of one response; return its path."""
        return store(self.root, build(
            response, self.clock(), uuid.uuid4().hex, self.versions,
            request, question, bundle_sha256, written, self.backend()))


# --------------------------------------------------------------------------
# Aggregates
# --------------------------------------------------------------------------

def rounded(values):
    """Return the median, 90th percentile and largest of some numbers."""
    values = sorted(values)
    if not values:
        return None
    return {"median": round(statistics.median(values), 3),
            "p90": round(values[min(len(values) - 1, int(round(
                0.9 * (len(values) - 1))))], 3),
            "largest": round(values[-1], 3)}


def aggregate(records):
    """Return what some records add up to, as counts and measures only:
    requests by task and outcome; for responses written from evidence,
    how many the model wrote, the fallbacks by reason and category, and
    the tokens, seconds and peak GPU memory of the model's attempts.
    Nothing from a record's content is returned."""
    tasks, outcomes = collections.Counter(), collections.Counter()
    authors, fallbacks = collections.Counter(), collections.Counter()
    categories = collections.Counter()
    seconds, prompts, outputs, peaks = [], [], [], []
    for record in records:
        tasks[record.task or "none"] += 1
        outcomes[record.response.outcome] += 1
        generation = record.generation
        if generation is None:
            continue
        authors[generation.author] += 1
        if generation.fallback:
            fallbacks[generation.fallback] += 1
            categories[generation.category] += 1
        if generation.seconds is not None:
            seconds.append(generation.seconds)
        if generation.prompt_tokens is not None:
            prompts.append(generation.prompt_tokens)
        if generation.output_tokens is not None:
            outputs.append(generation.output_tokens)
        if generation.peak_memory_bytes:
            peaks.append(generation.peak_memory_bytes / 2 ** 20)
    written = sum(authors.values())
    return {"requests": sum(tasks.values()), "tasks": dict(sorted(
        tasks.items())), "outcomes": dict(sorted(outcomes.items())),
            "written_from_evidence": written,
            "written_by_the_model": authors["model"],
            "fallbacks": dict(sorted(fallbacks.items())),
            "fallback_categories": dict(sorted(categories.items())),
            "seconds": rounded(seconds), "prompt_tokens": rounded(prompts),
            "output_tokens": rounded(outputs),
            "peak_gpu_mib": rounded(peaks)}


# --------------------------------------------------------------------------
# The log audit
# --------------------------------------------------------------------------

LOGS_ROOT = guard.PRIVATE_ROOT + "\\outputs\\logs"
MAX_LOG_BYTES = 64 * 1024 * 1024
# What only a prompt, an evidence view, a candidate document, a bundle or a
# record holds; routine logs hold statuses and counts, never these.
CONTENT_MARKERS = {
    "prompt_text": re.compile(r"Worked example, on invented data|Evidence "
                              r"view:|Reply with the JSON document only"),
    "evidence_view": re.compile(
        r'"(?:records_without_standard_concept|restriction_notices|'
        r'drivers_not_described|record_columns|matching_count)"\s*:'),
    "candidate_document": re.compile(
        r'"(?:item_wording|driver_wording|history_wording|context_wording|'
        r'statements|slots)"\s*:'),
    "bundle_content": re.compile(
        r'"(?:bundle_id|linked_event_refs|history_events|'
        r'final_admission_context|source_concept)"\s*:'),
    "record_content": re.compile(
        r'"(?:model_candidate|response_sha256|question)"\s*:'),
}


def chunks(data, size):
    """Yield a log's bytes in pieces of at most size, cut after a line
    where one ends within the piece."""
    while data:
        if len(data) <= size:
            yield data
            return
        cut = data.rfind(b"\n", 0, size) + 1 or size
        yield data[:cut]
        data = data[cut:]


def log_findings(data, rules, relative="log.txt"):
    """Return the rule of each finding in one log's bytes, never what it
    matched: the publication policy's secrets, user paths and patient
    identifiers, and the markers of content only a prompt, a document, a
    bundle or a record holds."""
    from ehr_evidence_app.quality import publication
    found = []
    for piece in chunks(data, rules.max_bytes):
        found += publication.content_findings(relative, piece, rules)
    text = data.decode("utf-8", errors="replace")
    for rule, pattern in CONTENT_MARKERS.items():
        found += [rule] * len(pattern.findall(text))
    return found


def audit_logs(roots, rules=None):
    """Return, for each log file under some roots that holds a finding,
    (its path, a Counter of findings by rule), and the number of files
    read. A file too large to read is a finding of its own."""
    from ehr_evidence_app.quality import publication
    if rules is None:
        rules = publication.load_rules(os.path.join(
            guard.REPO_ROOT, "config", publication.POLICY_NAME))
    flagged, read_files = [], 0
    for root in roots:
        for directory, names, files in os.walk(root):
            names.sort()
            for name in sorted(files):
                path = os.path.join(directory, name)
                read_files += 1
                if os.path.getsize(path) > MAX_LOG_BYTES:
                    flagged.append((path, collections.Counter(
                        {"log_too_large": 1})))
                    continue
                with open(path, "rb") as handle:
                    found = collections.Counter(log_findings(
                        handle.read(), rules, name))
                if found:
                    flagged.append((path, found))
    return flagged, read_files


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

def scripted_writer(pairs):
    """Return a writer whose scripted backend writes each invented
    request's template document."""
    from ehr_evidence_app.generation import projection, shape
    documents = {projection.text(projection.view(context)): shape.text(
        shape.document_of(template)) for context, template in pairs}
    end = "\n\nReply with the JSON document only."

    def script(messages, schema):
        """Write the template document of the request's view."""
        body = messages[1]["content"]
        return adapter.output(documents[body[:-len(end)].rsplit(
            "\n\n", 1)[1]])

    return adapter.Writer(adapter.ScriptedBackend(script),
                          adapter.scripted_limits())


def run_synthetic():
    """Record the invented requests, written by a scripted backend, under
    a temporary directory standing for the protected data; return
    (records written, aggregates, problems)."""
    from ehr_evidence_app.generation import shape
    from ehr_evidence_app.qa import suite
    vocabulary = suite.synthetic_vocabulary()
    problems = []
    pairs = shape.synthetic_contexts()
    writer = scripted_writer(pairs)
    with tempfile.TemporaryDirectory() as scratch:
        recorder = Recorder(os.path.join(scratch, "runtime-records"),
                            writer, protected=scratch)
        written_paths = []
        for context, template in pairs:
            release = adapter.gate_release(
                context, vocabulary
                if context.task != "prediction_explanation" else None)
            written = adapter.write(writer, context, template, release)
            written_paths.append(recorder.record(
                written.response, context.request,
                bundle_sha256=context.bundle_sha256, written=written))
        records = [read(path) for path in written_paths]
        if sorted(written_paths) != paths(recorder.root):
            problems.append("the records found are not those written")
        try:
            store(recorder.root, records[0])
            problems.append("a record was overwritten")
        except RecordError:
            pass
    if containment_problem(os.path.join(guard.PRIVATE_ROOT,
                                        "outputs")) is None:
        problems.append("a records root outside the protected data passed")
    totals = aggregate(records)
    if totals["written_by_the_model"] != len(pairs):
        problems.append("not every invented request was written by the "
                        "scripted model")
    problems += audit_problems()
    return len(records), totals, problems


def invented_logs():
    """Return invented log lines, by the rule each should be found by (None
    for a routine line); the secret and identifier are built here, not
    written out, so that no source file holds one."""
    token = "hf" + "_" + "Q7x" * 12
    return {None: "2026-09-28T08:30:00Z request q-1: answer, released as "
                  "written; 91 output tokens in 11.3 s\n",
            "prompt_text": "Now the request.\n\nEvidence view:\n\n",
            "candidate_document": '{"item_wording": "{condition}"}\n',
            "huggingface_token": "token loaded: %s\n" % token,
            "patient_identifier_assignment": "subject_id=%d\n" % (
                10000000 + 32)}


def audit_problems():
    """Return what the log audit gets wrong on invented logs."""
    problems = []
    with tempfile.TemporaryDirectory() as scratch:
        for number, (rule, line) in enumerate(invented_logs().items()):
            with open(os.path.join(scratch, "run-%d.log" % number), "w",
                      encoding="utf-8") as handle:
                handle.write(line)
        flagged, count = audit_logs([scratch])
        found = {os.path.basename(path): set(rules)
                 for path, rules in flagged}
    for number, rule in enumerate(invented_logs()):
        name = "run-%d.log" % number
        if rule is None and name in found:
            problems.append("a routine log line was flagged")
        if rule is not None and rule not in found.get(name, ()):
            problems.append("the log audit missed %s" % rule)
    if count != len(invented_logs()):
        problems.append("the log audit did not read every log")
    return problems


def main(argv=None):
    """Run the default mode on invented data, or aggregate the protected
    records; prints counts only."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="read the protected runtime records or the "
                             "routine logs")
    parser.add_argument("--aggregate", action="store_true",
                        help="print what the records add up to")
    parser.add_argument("--audit-logs", action="store_true",
                        help="check that the routine logs hold no secret "
                             "or patient text")
    args = parser.parse_args(argv)
    modes = args.aggregate + args.audit_logs
    if args.operator_real != bool(modes) or modes > 1:
        print("FAILED: --operator-real goes with one of --aggregate and "
              "--audit-logs")
        return 2
    if args.audit_logs:
        flagged, count = audit_logs([LOGS_ROOT])
        for path, found in flagged:
            print("%s: %s" % (os.path.relpath(path, LOGS_ROOT), ", ".join(
                "%s %d" % pair for pair in sorted(found.items()))))
        print("log files read: %d; with findings: %d" % (count,
                                                         len(flagged)))
        print("RESULT: %s" % ("FAIL" if flagged else "PASS"))
        return 1 if flagged else 0
    if args.operator_real:
        try:
            records = [read(path) for path in paths()]
        except RecordError as error:
            print("FAILED: %s" % error)
            return 1
        print(json.dumps(aggregate(records), indent=1, sort_keys=True))
        print("RESULT: PASS")
        return 0
    count, totals, problems = run_synthetic()
    for problem in problems:
        print("PROBLEM: %s" % problem)
    print("invented records written and read back: %d; written by the "
          "model: %d" % (count, totals["written_by_the_model"]))
    print("RESULT: %s" % ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
