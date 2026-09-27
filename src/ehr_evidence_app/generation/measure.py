"""Development measurement of the model's generation (work item 46.1).

Two operator modes read a development case set (never the evaluation set)
and print aggregate counts only:

* --tokens counts, with the model's own tokenizer (through the worker),
  the prompt tokens of every request's compact prompt and the output
  tokens of its template document in the compact shape; for explanations,
  for each candidate number of drivers. This is the token distribution the
  limits are set from (ADR 0013).
* --benchmark writes a representative sample of the requests with the
  model at one precision, and reports by task how many candidates the gate
  released as the model wrote them, the fallbacks by reason, and the
  prompt and output tokens, seconds, tokens a second and peak GPU memory.
  For each document it names, without any value, what went wrong: where
  output was cut off, how it broke its schema or shape, or which fields
  differ from the template's document (the correct answer; wordings are
  the model's own and are not compared).
  The sample is drawn deterministically: for each task and outcome, the
  requests are ordered by a digest of their identifiers and the first are
  taken. --task keeps one task's requests, and --drivers has explanations
  describe another number of drivers than the configured one, so that
  candidate numbers are compared on the same requests.
* --paired writes the same requests in BF16 with the mixed placement and
  in NF4, reports each as --benchmark does, and compares them pair by
  pair: how many requests either or both released, the difference in
  release rate (NF4 minus BF16) with its 95% confidence interval for
  paired proportions (Newcombe's method 10), the exact McNemar test, and
  whether NF4 is non-inferior at the margin of ADR 0014 (the interval's
  lower bound above minus 5 percentage points). A difference that is not
  significant is not shown equivalence; only the margin decides.

A request over a limit is a fallback, counted as a failure of the model,
never as its success. The default mode checks the sampling and summary
functions on invented values.

Usage:
    python -m ehr_evidence_app.generation.measure
    python -m ehr_evidence_app.generation.measure --tokens \\
        --case-set dev-30-v1
    python -m ehr_evidence_app.generation.measure --benchmark \\
        --case-set dev-30-v1 --precision bf16-mixed --per-group 8
    python -m ehr_evidence_app.generation.measure --benchmark \\
        --case-set dev-30-v1 --task prediction_explanation --drivers 5
    python -m ehr_evidence_app.generation.measure --paired \\
        --case-set dev-30-v1 --per-group 1000
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import re
import statistics
import sys
import time

from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.generation import adapter, prompt, shape
from ehr_evidence_app.validation import candidate as candidates

CANDIDATE_LIMITS = (3, 5, 8, 10, 15)
# The non-inferiority margin of NF4's release rate against BF16's, and the
# normal quantile of a two-sided 95% interval (ADR 0014).
MARGIN = 0.05
Z95 = 1.959963984540054
PRECISIONS = ("bf16-mixed", "nf4")
PRECISION_LABELS = {"bf16-mixed": "BF16, mixed GPU/CPU placement (output "
                                  "layer and token embeddings on the "
                                  "processor)",
                    "nf4": "4-bit NF4, GPU-resident"}
# The rows of each task's document, and their columns.
ROWS = {"condition_list": ("items", shape.LIST_ROW),
        "condition_presence": ("items", shape.PRESENCE_ROW),
        "prediction_explanation": ("drivers", shape.DRIVER_ROW)}
# The parts of a driver's history and context rows.
NESTED = {"history": shape.HISTORY_ROW, "context": shape.CONTEXT_ROW}
# Findings that are counts or sets of visits only: what the view would
# state if it carried them. A request withheld for these alone counts
# towards the test of ADR 0014 for adding them to the view.
BOOKKEEPING = frozenset(("items[count]", "items[visits]", "summary.groups",
                         "summary.records", "summary.matches",
                         "drivers[history.count]", "drivers[history.visits]",
                         "drivers[context.count]"))
# Values a response shows to three significant figures.
SHOWN = frozenset(("score", "logit"))
KEY = re.compile(r'"([a-z_]+)": ')
PATH_TOKEN = re.compile(r"\$|\.[a-z_]+|\[\d+\]")


def summary(values):
    """Return n, min, median, 90th percentile and max of some numbers."""
    values = sorted(values)
    if not values:
        return "n=0"
    p90 = values[min(len(values) - 1, int(round(0.9 * (len(values) - 1))))]
    return "n=%d min=%s median=%s p90=%s max=%s" % (
        len(values), round(values[0], 1), round(statistics.median(values), 1),
        round(p90, 1), round(values[-1], 1))


def group_of(context, template):
    """Return the stratum of a request: its task and template outcome."""
    return "%s %s" % (context.task, template.outcome)


def order_key(context):
    """Return a request's place in the deterministic sample order."""
    request = context.request
    text = "|".join((request.case.case_set, str(request.case.case_number),
                     request.scope.kind, str(request.scope.visit_number),
                     request.task, request.request_id))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sample(pairs, per_group):
    """Return up to per_group requests of each stratum, deterministically."""
    groups = collections.defaultdict(list)
    for context, template in pairs:
        groups[group_of(context, template)].append((context, template))
    found = []
    for name in sorted(groups):
        members = sorted(groups[name], key=lambda pair: order_key(pair[0]))
        found += members[:per_group]
    return found


def with_limit(context, template, limit):
    """Return an explanation request and its template for another number
    of drivers."""
    from ehr_evidence_app.validation import explanation
    return (shape.Context(context.request, context.bundle,
                          context.bundle_sha256, limit=limit),
            explanation.template(context.request, context.bundle,
                                 context.bundle_sha256, limit))


def same_value(name, value, expected):
    """Return True when a stated value is the correct one: a score or
    logit as a response shows it (ADR 0014), anything else as a set where
    it is a list of plain values."""
    if name in SHOWN and isinstance(value, (int, float)) and isinstance(
            expected, (int, float)) and not isinstance(value, bool):
        return candidates.shown(value) == candidates.shown(expected)
    return normal(value) == normal(expected)


def normal(value):
    """Return a JSON value with each list of plain values as a sorted set,
    since visits, references, notices and reason codes are read as sets."""
    if isinstance(value, list):
        if all(not isinstance(item, (list, dict)) for item in value):
            return sorted({json.dumps(item) for item in value})
        return [normal(item) for item in value]
    if isinstance(value, dict):
        return {key: normal(item) for key, item in value.items()}
    return value


def row_differences(task, mine, theirs):
    """Return where a document's rows differ from the template's: rows
    added, repeated or missing, in another order, or a column's value. A
    condition may be named by any of its records; a driver by its own
    reference."""
    name, columns = ROWS[task]
    if task == "prediction_explanation":
        index = {row[0]: at for at, row in enumerate(theirs)}
    else:
        cited = columns.index("refs")
        index = {ref: at for at, row in enumerate(theirs)
                 for ref in row[cited]}
    found, places = set(), []
    for row in mine:
        at = index.get(row[0])
        if at is None:
            found.add(name + ": added")
            continue
        places.append(at)
        for column, value, expected in zip(columns, row, theirs[at]):
            if same_value(column, value, expected):
                continue
            if value is None or expected is None:
                found.add("%s[%s] %s" % (name, column, "left out"
                                         if value is None else "added"))
            elif column in NESTED and isinstance(value, list) and len(
                    value) == len(NESTED[column]):
                found |= {"%s[%s.%s]" % (name, column, part)
                          for part, stated, correct in zip(
                              NESTED[column], value, expected)
                          if normal(stated) != normal(correct)}
            else:
                found.add("%s[%s]" % (name, column))
    if len(set(places)) != len(places):
        found.add(name + ": repeated")
    if set(range(len(theirs))) - set(places):
        found.add(name + ": missing")
    elif places != sorted(places):
        found.add(name + ": order")
    return found


def differences(task, document, expected):
    """Return the fields in which a model's document differs from the
    template's, by name only; wordings, which are the model's own, are not
    compared."""
    if document.get("outcome") != expected.get("outcome"):
        return {"outcome %s instead of %s" % (document.get("outcome"),
                                              expected.get("outcome"))}
    rows = ROWS[task][0]
    found = set()
    for key in sorted(set(document) | set(expected)):
        mine, theirs = document.get(key), expected.get(key)
        if key.endswith("wording"):
            continue
        if key == rows and isinstance(mine, list) and isinstance(theirs,
                                                                 list):
            found |= row_differences(task, mine, theirs)
        elif key == "notices" and isinstance(mine, list) and isinstance(
                theirs, list):
            # Notices are named by their approved identifiers.
            found |= {"notices: %s missing" % notice
                      for notice in set(theirs) - set(mine)}
            found |= {"notices: %s added" % notice
                      for notice in set(mine) - set(theirs)
                      if isinstance(notice, str)}
        elif isinstance(mine, dict) and isinstance(theirs, dict):
            found |= {"%s.%s" % (key, name)
                      for name in set(mine) | set(theirs)
                      if name != "wording" and not same_value(
                          name, mine.get(name), theirs.get(name))}
        elif normal(mine) != normal(theirs):
            found.add(key)
    return found


def cut_place(text):
    """Return where output cut off at a limit stopped: after which key,
    and whether inside a string. A wording holds no double quote, so the
    quotes before the cut tell."""
    keys = KEY.findall(text)
    return "after %s, %s" % (keys[-1] if keys else "the start",
                             "inside a string" if text.count('"') % 2
                             else "outside a string")


def place(problem):
    """Return a schema problem with its row and element numbers removed and
    a row's column kept, such as "$.items[][2]: repeated items"."""
    path, _, message = problem.partition(": ")
    found, depth = [], None
    for token in PATH_TOKEN.findall(path):
        if not token.startswith("["):
            found.append(token)
            depth = 0 if token in (".items", ".drivers") else None
        elif depth == 1:
            found.append(token)
            depth = 2
        else:
            found.append("[]")
            depth = 1 if depth == 0 else None
    return "".join(found) + ": " + message


def schema_findings(document, rules):
    """Return where a document breaks its schema; when the schema has
    alternatives (an answer or "not recorded"), where it breaks the
    closest one."""
    branches = rules.get("anyOf", [rules])
    return {place(problem) for problem in min(
        (shape.violations(document, branch, "$", rules)
         for branch in branches), key=len)}


def findings(context, template, written):
    """Return what went wrong with a model's document, by name only: where
    it was cut, how it breaks its schema or its shape, or where it differs
    from the template's document."""
    output = written.output
    if output is None:
        return set()
    if not output.finished:
        return {"cut " + cut_place(output.text)}
    try:
        document = json.loads(output.text)
    except ValueError:
        return {"not JSON"}
    problems = schema_findings(document, shape.schema(context))
    if problems:
        return problems
    try:
        shape.candidate(context, document)
    except shape.ShapeError as error:
        return {"shape: %s" % error}
    return differences(context.task, document, shape.document_of(template))


def tokens(case_set, backend):
    """Print the prompt and output token distributions of a case set."""
    pairs, _ = adapter.development_requests(case_set)
    counts = collections.defaultdict(lambda: ([], []))
    for context, template in pairs:
        variants = [(context.task, context, template)]
        if context.task == "prediction_explanation":
            variants = [("%s K=%d" % (context.task, limit),)
                        + with_limit(context, template, limit)
                        for limit in CANDIDATE_LIMITS]
        for label, variant, drafted in variants:
            prompts, outputs = counts[label]
            prompts.append(backend.prompt_tokens(prompt.messages(variant)))
            outputs.append(backend.text_tokens(
                shape.text(shape.document_of(drafted))))
    for label, (prompts, outputs) in sorted(counts.items()):
        print("%s %-34s prompt tokens %s" % (case_set, label,
                                              summary(prompts)))
        print("%s %-34s output tokens %s" % (case_set, label,
                                              summary(outputs)))


def chosen_requests(pairs, per_group, task=None, drivers=None):
    """Return the benchmark's sample: of one task's requests when task is
    given, with explanations describing drivers drivers when given (the
    same requests as with the configured number)."""
    if task is not None:
        pairs = [pair for pair in pairs if pair[0].task == task]
    return [with_limit(context, template, drivers)
            if drivers is not None
            and context.task == "prediction_explanation"
            else (context, template)
            for context, template in sample(pairs, per_group)]


def completeness(context, template, written):
    """Return (history lines stated, history lines the evidence has,
    context lines stated, context lines the evidence has) of a model's
    explanation that stands for a candidate, or None. A driver's history
    or context may be left out (ADR 0014); how often is measured here."""
    if context.task != "prediction_explanation" or (
            written.model_candidate is None):
        return None
    found = []
    for kind in ("driver_history", "driver_context"):
        available = {item.slots.driver for item in template.statements
                     if item.kind == kind}
        stated = {item.slots.driver
                  for item in written.model_candidate.statements
                  if item.kind == kind}
        found += [len(stated & available), len(available)]
    return tuple(found)


def bookkeeping_only(written, names):
    """Return True when a withheld document differs from the correct one
    in counts or visits alone."""
    return written.fallback == "gate_rejected" and bool(names) and (
        set(names) <= BOOKKEEPING)


def write_sample(pairs, release_of, writer, per_group, task=None,
                 drivers=None):
    """Write the sample of some requests through the adapter; return, by
    stratum, each request's (Written, what went wrong with it, how
    complete an explanation is)."""
    results = collections.defaultdict(list)
    for context, template in chosen_requests(pairs, per_group, task,
                                             drivers):
        written = adapter.write(writer, context, template,
                                release_of(context))
        results[group_of(context, template)].append(
            (written, findings(context, template, written),
             completeness(context, template, written)))
    return results


def completeness_text(entries):
    """Return the history and context lines stated of the lines the
    evidence has, summed over some explanations."""
    sums = [sum(entry[index] for entry in entries) for index in range(4)]
    return "history lines %d of %d, context lines %d of %d" % tuple(sums)


def report(case_set, results, seconds):
    """Print, by stratum, the outcomes, what went wrong by name, and the
    tokens, seconds and memory; aggregates only."""
    for name, pairs in sorted(results.items()):
        items = [item for item, _, _ in pairs]
        outcomes = collections.Counter(
            "released as written" if item.author == "model"
            else "fallback " + item.fallback + (
                " " + ",".join(item.gate_reasons)
                if item.gate_reasons else "") for item in items)
        outputs = [item.output for item in items if item.output]
        print("%s %s: %d requests; %s" % (
            case_set, name, len(items),
            "; ".join("%s %d" % pair for pair in sorted(outcomes.items()))))
        found = collections.Counter(
            ("released" if item.fallback is None else item.fallback, finding)
            for item, names, _ in pairs for finding in names)
        for (reason, finding), count in sorted(found.items()):
            print("  %s: %s %d" % (reason, finding, count))
        print("  withheld for counts or visits only: %d" % sum(
            bookkeeping_only(item, names) for item, names, _ in pairs))
        stated = [entry for _, _, entry in pairs if entry is not None]
        if stated:
            print("  stated by the model: %s; released: %s" % (
                completeness_text(stated), completeness_text(
                    [entry for item, _, entry in pairs
                     if entry is not None and item.fallback is None])))
        if outputs:
            print("  prompt tokens %s" % summary(
                [output.prompt_tokens for output in outputs]))
            print("  output tokens %s" % summary(
                [output.output_tokens for output in outputs]))
            print("  seconds %s" % summary(
                [output.seconds for output in outputs]))
            print("  tokens a second %s" % summary(
                [output.output_tokens / output.seconds
                 for output in outputs if output.seconds]))
            peaks = [output.peak_memory_bytes / 2 ** 20 for output in outputs
                     if output.peak_memory_bytes]
            if peaks:
                print("  peak GPU MiB %s" % summary(peaks))
    released = sum(item.author == "model" for pairs in results.values()
                   for item, _, _ in pairs)
    bookkeeping = sum(bookkeeping_only(item, names)
                      for pairs in results.values()
                      for item, names, _ in pairs)
    total = sum(len(pairs) for pairs in results.values())
    print("%s: released as the model wrote them %d of %d; withheld for "
          "counts or visits only %d; %.0f s" % (
              case_set, released, total, bookkeeping, seconds))


def benchmark(case_set, backend, per_group, limits, task=None,
              drivers=None):
    """Write a sample of a case set's requests with the model; print, by
    stratum, the outcomes and the timings."""
    pairs, release_of = adapter.development_requests(case_set)
    started = time.monotonic()
    results = write_sample(pairs, release_of, adapter.Writer(backend, limits),
                           per_group, task, drivers)
    report(case_set, results, time.monotonic() - started)


def wilson(successes, total, z=Z95):
    """Return the Wilson score interval of a proportion."""
    if not total:
        return 0.0, 1.0
    rate = successes / total
    centre = (rate + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(rate * (1 - rate) / total
                         + z * z / (4 * total * total)) / (1 + z * z / total)
    return centre - half, centre + half


def paired_difference(both, first, second, neither, z=Z95):
    """Return (difference, lower, upper) of the second condition's rate
    minus the first's, for paired outcomes counted as both, first only,
    second only and neither (Newcombe 1998, method 10)."""
    total = both + first + second + neither
    rate1, rate2 = (both + first) / total, (both + second) / total
    low1, high1 = wilson(both + first, total, z)
    low2, high2 = wilson(both + second, total, z)
    margins = ((both + first) * (second + neither) * (both + second)
               * (first + neither))
    phi = (both * neither - first * second) / math.sqrt(margins) \
        if margins else 0.0
    difference = rate2 - rate1
    lower = difference - math.sqrt(max(0.0, (rate2 - low2) ** 2 - 2 * phi * (
        rate2 - low2) * (high1 - rate1) + (high1 - rate1) ** 2))
    upper = difference + math.sqrt(max(0.0, (high2 - rate2) ** 2 - 2 * phi * (
        high2 - rate2) * (rate1 - low1) + (rate1 - low1) ** 2))
    return difference, lower, upper


def mcnemar(first, second):
    """Return the two-sided exact McNemar p-value of the discordant
    pairs."""
    discordant = first + second
    if not discordant:
        return 1.0
    tail = sum(math.comb(discordant, k)
               for k in range(min(first, second) + 1)) / 2 ** discordant
    return min(1.0, 2 * tail)


def paired_counts(first, second):
    """Return (both, first only, second only, neither) released of two
    runs of the same requests, stratum by stratum."""
    counts = [0, 0, 0, 0]
    for name in first:
        for (one, _, _), (two, _, _) in zip(first[name], second[name]):
            released = (one.author == "model", two.author == "model")
            counts[{(True, True): 0, (True, False): 1, (False, True): 2,
                    (False, False): 3}[released]] += 1
    return tuple(counts)


def paired_report(case_set, first, second):
    """Print the pair-by-pair comparison of BF16 (first) and NF4 (second)
    on the same requests, overall and by stratum; aggregates only."""
    print("%s paired: BF16 with the mixed placement against NF4" % case_set)
    rows = [(name, {name: first[name]}, {name: second[name]})
            for name in sorted(first)] + [("all", first, second)]
    for name, one, two in rows:
        both, only_first, only_second, neither = paired_counts(one, two)
        total = both + only_first + only_second + neither
        difference, lower, upper = paired_difference(
            both, only_first, only_second, neither)
        print("  %s: %d requests; both released %d, BF16 only %d, NF4 only "
              "%d, neither %d; release rate BF16 %.3f, NF4 %.3f; NF4 minus "
              "BF16 %+.3f (95%% CI %+.3f to %+.3f); exact McNemar p %.3f" % (
                  name, total, both, only_first, only_second, neither,
                  (both + only_first) / total, (both + only_second) / total,
                  difference, lower, upper,
                  mcnemar(only_first, only_second)))
    _, lower, _ = paired_difference(*paired_counts(first, second))
    print("NF4 non-inferior at a margin of %d points: %s" % (
        round(MARGIN * 100), "yes" if lower > -MARGIN else "no"))


def paired(case_set, per_group, limits, task=None, drivers=None):
    """Write the same sample in BF16 and in NF4, report each, and compare
    them pair by pair."""
    from ehr_evidence_app.generation import runtime
    pairs, release_of = adapter.development_requests(case_set)
    results = []
    for precision in PRECISIONS:
        print("precision: %s" % PRECISION_LABELS[precision])
        print("limits: %d prompt tokens, %d output tokens, %d s" % (
            limits.max_prompt_tokens, limits.max_output_tokens,
            limits.max_request_seconds))
        backend = runtime.WorkerBackend(verify=False, precision=precision)
        started = time.monotonic()
        try:
            found = write_sample(pairs, release_of,
                                 adapter.Writer(backend, limits), per_group,
                                 task, drivers)
        finally:
            backend.stop()
        report(case_set, found, time.monotonic() - started)
        results.append(found)
    paired_report(case_set, *results)


def run_synthetic():
    """Return problems of the sampling and summaries on invented data."""
    problems = []
    pairs = shape.synthetic_contexts()
    once = sample(pairs, 1)
    if sample(pairs, 1) != once:
        problems.append("the sample is not deterministic")
    if len({group_of(context, template) for context, template in once}) \
            != len(once):
        problems.append("a stratum is sampled twice")
    if summary([3, 1, 2]) != "n=3 min=1 median=2 p90=3 max=3":
        problems.append("the summary is wrong")
    difference, lower, upper = paired_difference(10, 3, 1, 6)
    if not lower < difference < upper or paired_difference(
            10, 1, 3, 6) != (-difference, -upper, -lower):
        problems.append("the paired interval is wrong")
    if mcnemar(0, 6) != 2 / 64 or mcnemar(3, 3) != 1.0:
        problems.append("the McNemar test is wrong")
    fewer = chosen_requests(pairs, 1, "prediction_explanation", 1)
    if not fewer or any(context.task != "prediction_explanation"
                        or context.limit != 1 for context, _ in fewer):
        problems.append("the task or the number of drivers is not applied")
    for context, template in pairs:
        document = shape.document_of(template)
        if differences(context.task, document, document):
            problems.append("a document differs from itself")
        rows, columns = ROWS[context.task]
        if document.get(rows):
            column = "rank" if rows == "drivers" else "count"
            changed = json.loads(json.dumps(document))
            changed[rows][0][columns.index(column)] += 1
            if differences(context.task, changed, document) != {
                    "%s[%s]" % (rows, column)}:
                problems.append("a changed row is not found")
    if cut_place('{"outcome": "answer", "summary": {"wording": "abc') != (
            "after wording, inside a string"):
        problems.append("the place of a cut is wrong")
    return problems


def main(argv=None):
    """Run the default mode, or an operator mode on a development case
    set; prints aggregate counts only."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tokens", action="store_true",
                        help="count prompt and output tokens of a case set")
    parser.add_argument("--benchmark", action="store_true",
                        help="write a sample of a case set with the model")
    parser.add_argument("--paired", action="store_true",
                        help="write a sample in BF16 and in NF4 and compare "
                             "them pair by pair")
    parser.add_argument("--case-set",
                        help="the development case set (dev-30-v1 or "
                             "pilot-10-v1)")
    parser.add_argument("--precision", choices=PRECISIONS,
                        default="bf16-mixed")
    parser.add_argument("--per-group", type=int, default=8)
    parser.add_argument("--seconds", type=int, default=None,
                        help="time limit of a request for the benchmark "
                             "(default: the configured limit)")
    parser.add_argument("--output-tokens", type=int, default=None,
                        help="output limit for the benchmark (default: the "
                             "configured limit)")
    parser.add_argument("--task", choices=vocab.TASKS, default=None,
                        help="benchmark one task's requests only")
    parser.add_argument("--drivers", type=int, default=None,
                        help="drivers an explanation describes in the "
                             "benchmark (default: the configured number)")
    args = parser.parse_args(argv)
    modes = args.tokens + args.benchmark + args.paired
    if bool(modes) != bool(args.case_set) or modes > 1:
        print("FAILED: give one of --tokens, --benchmark and --paired with "
              "--case-set")
        return 2
    if args.drivers is not None and not 1 <= args.drivers <= 100:
        print("FAILED: --drivers is a number from 1 to 100")
        return 2
    if not args.case_set:
        problems = run_synthetic()
        for problem in problems:
            print("PROBLEM: %s" % problem)
        print("RESULT: %s" % ("FAIL" if problems else "PASS"))
        return 1 if problems else 0
    from ehr_evidence_app.evidence import contracts
    from ehr_evidence_app.generation import runtime
    if args.paired:
        try:
            paired(args.case_set, args.per_group, contracts.load_limits(),
                   args.task, args.drivers)
        except adapter.DevelopmentError as error:
            print("FAILED: %s" % error)
            return 1
        print("RESULT: PASS")
        return 0
    backend = runtime.WorkerBackend(verify=False, precision=args.precision)
    try:
        if args.tokens:
            tokens(args.case_set, backend)
        else:
            limits = contracts.load_limits()
            changes = {}
            if args.seconds:
                changes["max_request_seconds"] = args.seconds
            if args.output_tokens:
                changes["max_output_tokens"] = args.output_tokens
            limits = limits.model_copy(update=changes)
            print("precision: %s" % PRECISION_LABELS[args.precision])
            print("limits: %d prompt tokens, %d output tokens, %d s" % (
                limits.max_prompt_tokens, limits.max_output_tokens,
                limits.max_request_seconds))
            if args.drivers is not None:
                print("drivers an explanation describes: %d"
                      % args.drivers)
            benchmark(args.case_set, backend, args.per_group, limits,
                      args.task, args.drivers)
    except adapter.DevelopmentError as error:
        print("FAILED: %s" % error)
        return 1
    finally:
        backend.stop()
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
