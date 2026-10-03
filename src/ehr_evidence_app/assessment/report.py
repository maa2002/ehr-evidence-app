"""Scoring a paired run (work items 58, 59 and 61; ADR 0028).

A run's protected records (assessment.runner) are scored request by
request against the independent references (assessment.references):

* each condition's response is read with assessment.stated: what it
  released, or nothing when it was withheld or failed. The bundle that
  translates its references is the one the record names, and its digest
  must be the record's;
* each request's reference is prepared from the source records;
* assessment.metrics scores every response, summarises each metric by
  stratum and condition, and compares the conditions pair by pair.

The report holds aggregate figures only: each metric's summed terms, its
value in each condition (None when not applicable), and its paired
difference, controlled minus baseline, with its 95% interval (and the
exact McNemar p-value for a proportion over the same requests). It also
holds the run's failures by category and its distributions of seconds,
tokens and peak GPU memory. Nothing of a request is printed.

The operator mode scores a run of a development case set; the evaluation
set waits for the freeze (work item 64).

Usage:
    python -m ehr_evidence_app.assessment.report
    python -m ehr_evidence_app.assessment.report --operator-real \\
        --run dev-30-v1-20261003T120000Z
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from ehr_evidence_app.assessment import (metrics, references, runner,
                                         stated)
from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.validation import candidate as candidates

ALL = "all"


class ReportError(Exception):
    """A fail-closed failure carrying a finding code only."""


def read_run(directory):
    """Return (configuration, summary, records) of a run directory."""
    def load(path):
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    configuration = load(os.path.join(directory, "run.json"))
    summary_path = os.path.join(directory, "summary.json")
    if not os.path.isfile(summary_path):
        raise ReportError("run_not_finished")
    names = sorted(os.listdir(os.path.join(directory, runner.RECORDS)))
    records = [load(os.path.join(directory, runner.RECORDS, name))
               for name in names]
    if len(records) != configuration.get("requests"):
        raise ReportError("run_records_missing")
    return configuration, load(summary_path), records


def scope_of(request):
    """Return the scope of a record's request."""
    return contracts.ScopeRef.model_validate_json(json.dumps(
        request["scope"]))


class Bundles:
    """The bundles a run's records name, by request: QA bundles by (case,
    scope), explanation bundles by case."""

    def __init__(self, qa, explanations):
        """Hold qa(case_number, scope) -> (bundle, digest) and
        {case_number: (bundle, digest)}."""
        self.qa = qa
        self.explanations = explanations

    def of(self, request):
        """Return the bundle a record's request names, checked by its
        digest."""
        case_number = request["case_number"]
        if request["task"] == metrics.EXPLANATION:
            bundle, digest = self.explanations[case_number]
        else:
            bundle, digest = self.qa(case_number, scope_of(request))
        if (bundle.bundle_id, digest) != (request["bundle"]["bundle_id"],
                                          request["bundle"]["sha256"]):
            raise ReportError("bundle_changed")
        return bundle

    @classmethod
    def read(cls, case_set):
        """Read a development case set's pinned bundles."""
        from ehr_evidence_app.qa import answer
        from ehr_evidence_app.validation import explanation
        bundle_set = answer.load_bundle_set(answer.bundle_set_plan(case_set))
        return cls(bundle_set.qa_bundle, {
            case_number: (bundle, digest) for case_number, bundle, digest
            in explanation.read_bundles(case_set)})


def response_of(request_id, document, bundle):
    """Return one condition's response, as the metrics read it."""
    candidate = None
    if document["released"] and document["candidate"] is not None:
        candidate = candidates.Candidate.model_validate_json(json.dumps(
            document["candidate"]))
    return stated.read(request_id, document["outcome"], candidate, bundle,
                       document["text"] is not None)


def reference_of(request, sources, limit):
    """Return the reference of a record's request."""
    if request["task"] == metrics.EXPLANATION:
        return sources.explanation(
            request["request_id"], request["case_number"],
            limit if request["limit"] is None else request["limit"])
    return sources.condition(
        request["request_id"], request["task"], request["case_number"],
        scope_of(request), tuple(request["asked"] or ()),
        request["stratum"])


def score_records(records, sources, bundles, limit):
    """Return {condition: [Scored]} of a run's records."""
    scored = {name: [] for name in runner.CONDITIONS}
    for record in records:
        request = record["request"]
        reference = reference_of(request, sources, limit)
        bundle = bundles.of(request)
        for name in runner.CONDITIONS:
            scored[name].append(metrics.score(reference, response_of(
                request["request_id"], record["conditions"][name], bundle)))
    return scored


def comparison_document(found):
    """Return a comparison's figures, or None."""
    if found is None:
        return None
    figures = {"difference": found.difference, "lower": found.lower,
               "upper": found.upper}
    if hasattr(found, "p_value"):
        figures.update(p_value=found.p_value, counts=list(found.counts))
    else:
        figures["replicates_left_out"] = found.left_out
    return figures


def strata_of(scored):
    """Return the strata of scored rows, and the whole run."""
    return sorted({row.stratum for row in scored}) + [ALL]


def report_document(scored, summary=None):
    """Return the aggregate report of scored conditions: by stratum, each
    metric in each condition and its paired comparison."""
    found = {}
    for stratum in strata_of(scored["controlled"]):
        rows = {name: [row for row in scored[name]
                       if stratum == ALL or row.stratum == stratum]
                for name in runner.CONDITIONS}
        values = {name: metrics.summarise(rows[name])
                  for name in runner.CONDITIONS}
        entry = {"requests": len(rows["controlled"]),
                 "counted_apart": {name: values[name][1]
                                   for name in runner.CONDITIONS},
                 "metrics": {}}
        for metric in metrics.METRICS:
            terms = {name: values[name][0][metric.name]
                     for name in runner.CONDITIONS}
            if not any(sum(term for term in figures[0])
                       for figures in terms.values()):
                continue
            entry["metrics"][metric.name] = {
                "decision": metric.decision, "form": metric.form,
                "terms": {name: list(terms[name][0]) for name in terms},
                "values": {name: terms[name][1] for name in terms},
                "comparison": comparison_document(metrics.compare(
                    rows["baseline"], rows["controlled"], metric))}
        found[stratum] = entry
    document = {"strata": found}
    if summary is not None:
        document["operations"] = {key: summary[key] for key in (
            "outcomes", "categories", "measures")}
    return document


def shown(value):
    """Return a figure as the report prints it."""
    return "n/a" if value is None else "%.3f" % value


def report(document):
    """Print a report: aggregate figures only."""
    for stratum, entry in document["strata"].items():
        print("== %s (%d requests)" % (stratum, entry["requests"]))
        for name, figures in entry["metrics"].items():
            values = figures["values"]
            line = "%-38s baseline %s  controlled %s" % (
                name, shown(values["baseline"]), shown(values["controlled"]))
            comparison = figures["comparison"]
            if comparison is not None:
                line += "  difference %s [%s, %s]" % (
                    shown(comparison["difference"]),
                    shown(comparison["lower"]), shown(comparison["upper"]))
                if "p_value" in comparison:
                    line += " p %s" % shown(comparison["p_value"])
            print(line)


# --------------------------------------------------------------------------
# Default mode: an invented run
# --------------------------------------------------------------------------

def synthetic_bundles():
    """Return the Bundles of the invented requests."""
    from ehr_evidence_app.evidence import bundles
    from ehr_evidence_app.qa import suite
    bundle_set = suite.synthetic_bundle_set()
    _, explanations, _ = bundles.run_synthetic()
    return Bundles(bundle_set.qa_bundle, {
        bundle.case.case_number: (bundle, "5" * 64)
        for bundle in explanations})


def run_synthetic():
    """Return (report, problems) of the invented requests written in both
    conditions by a backend that writes each template's own document: both
    conditions must score every metric alike, and perfectly."""
    from ehr_evidence_app.generation import adapter, shape
    from ehr_evidence_app.qa import suite
    vocabulary = suite.synthetic_vocabulary()
    limits = adapter.scripted_limits()
    records = []
    pairs = [(context, template) for context, template
             in shape.synthetic_contexts()]
    for number, (context, template) in enumerate(pairs, 1):
        writer = adapter.Writer(adapter.ScriptedBackend(
            adapter.scripts(template)[0][1]), limits)
        kept = []
        runner.run([(context, template)], writer,
                   lambda item, vocab=vocabulary: adapter.gate_release(
                       item, vocab if item.task != metrics.EXPLANATION
                       else None), vocabulary, kept=kept)
        record = kept[0]
        record["request"]["request_id"] = "r-%03d" % number
        records.append(record)
    scored = score_records(records, references.synthetic_sources(),
                           synthetic_bundles(),
                           references.configured_limit())
    document = report_document(scored)
    problems = []
    whole = document["strata"][ALL]["metrics"]
    for name, figures in whole.items():
        values = figures["values"]
        if values["baseline"] != values["controlled"]:
            problems.append("%s differs between conditions" % name)
    for name in ("exact_match", "scoped_accuracy", "preservation",
                 "claim_support", "citation_resolution",
                 "evidence_consistent_yield", "correct_answer_yield"):
        if whole.get(name, {}).get("values", {}).get("controlled") != 1.0:
            problems.append("%s is not perfect" % name)
    return document, problems


def run_real(run_id):
    """Score a finished run of a development case set; return its
    report."""
    from ehr_evidence_app.casesets import registry
    from ehr_evidence_app.evidence import readers
    directory = os.path.join(runner.OUTPUT_ROOT, run_id)
    configuration, summary, records = read_run(directory)
    case_set = configuration["case_set"]
    if case_set != readers.PILOT_LABEL and registry.spec(case_set)[
            "purpose"] != "development":
        raise ReportError("case_set_held_out")
    scored = score_records(records, references.Sources.read(case_set),
                           Bundles.read(case_set),
                           references.configured_limit())
    return report_document(scored, summary)


def main(argv=None):
    """Run the synthetic default mode or score a run."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="score a finished run of a development case "
                             "set; prints aggregates only")
    parser.add_argument("--run", default=None)
    args = parser.parse_args(argv)
    try:
        if args.operator_real:
            if not args.run:
                print("FAILED: --operator-real needs --run")
                return 2
            report(run_real(args.run))
        else:
            document, problems = run_synthetic()
            report(document)
            for problem in problems:
                print("FAILED: %s" % problem)
            if problems:
                return 1
    except (ReportError, references.ReferenceError) as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
