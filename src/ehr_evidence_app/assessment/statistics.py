"""Paired comparison of the two conditions (ADR 0016, decision 10).

The final evaluation writes every request in both conditions and reports
each metric's paired difference, controlled minus baseline, with a 95%
interval:

* a proportion over the same requests, each request a success or not in
  each condition: Newcombe's method 10, which combines the score interval
  of each condition's proportion (Wilson's) with the correlation of the
  pairs, the correlation's numerator moved n/2 towards zero when it is
  positive (Newcombe 1998, Statistics in Medicine 17, 2635-2650); with
  the exact McNemar test of the discordant pairs;
* a ratio of sums, such as F1, whose terms are summed over requests
  before the ratio is taken: the paired bootstrap over cases. Each
  replicate draws the cases with replacement, the same draw for both
  conditions and a case's requests together; there are 10,000 replicates
  from a fixed seed, and the interval runs from the 2.5th to the 97.5th
  percentile of the replicates' differences, each the order statistic of
  nearest rank. A replicate in which either condition's ratio is not
  applicable (its denominator is zero) is left out and counted.

Nothing here decides anything: there is no threshold, required
improvement or significance level (ADR 0016, decision 10).

The default mode checks the functions on a published worked example
(StatsDirect's paired proportions: 20, 12, 2 and 16 pairs; difference
0.2, 95% interval 0.056156 to 0.329207, exact P 0.0129) and on values
worked by hand.

Usage:
    python -m ehr_evidence_app.assessment.statistics
"""

from __future__ import annotations

import argparse
import math
import random
import sys
import typing

Z95 = 1.959963984540054
REPLICATES = 10000
SEED = 20260928
# The percentiles of the bootstrap interval, as fractions, so that their
# ranks are computed exactly.
LOWER = (1, 40)
UPPER = (39, 40)


class Paired(typing.NamedTuple):
    """A paired difference of proportions, controlled minus baseline, with
    its 95% interval, the exact McNemar p-value and the four counts:
    both successes, the baseline's only, the controlled condition's only
    and neither."""

    difference: float
    lower: float
    upper: float
    p_value: float
    counts: tuple


class Bootstrapped(typing.NamedTuple):
    """A paired difference of ratios of sums, controlled minus baseline,
    with its bootstrap interval and the replicates left out. A difference
    that is not applicable is None, and so are its limits."""

    difference: float | None
    lower: float | None
    upper: float | None
    left_out: int


def wilson(successes, total, z=Z95):
    """Return the Wilson score interval of a proportion."""
    if not total:
        return 0.0, 1.0
    rate = successes / total
    centre = (rate + z * z / (2 * total)) / (1 + z * z / total)
    half = z * math.sqrt(rate * (1 - rate) / total
                         + z * z / (4 * total * total)) / (1 + z * z / total)
    return max(0.0, centre - half), min(1.0, centre + half)


def correlation(both, first, second, neither):
    """Return the correlation of paired outcomes as method 10 uses it: its
    numerator moved n/2 towards zero when it is positive, and 0 when a
    margin is empty."""
    margins = ((both + first) * (second + neither) * (both + second)
               * (first + neither))
    if not margins:
        return 0.0
    numerator = both * neither - first * second
    if numerator > 0:
        numerator = max(numerator - (both + first + second + neither) / 2, 0)
    return numerator / math.sqrt(margins)


def paired_difference(both, first, second, neither, z=Z95):
    """Return (difference, lower, upper) of the second condition's rate
    minus the first's, for paired outcomes counted as both, first only,
    second only and neither (Newcombe 1998, method 10)."""
    total = both + first + second + neither
    if not total:
        raise ValueError("no pairs")
    rate1, rate2 = (both + first) / total, (both + second) / total
    low1, high1 = wilson(both + first, total, z)
    low2, high2 = wilson(both + second, total, z)
    phi = correlation(both, first, second, neither)
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


def paired_counts(pairs):
    """Return (both, baseline only, controlled only, neither) of
    (baseline success, controlled success) pairs."""
    counts = [0, 0, 0, 0]
    for baseline, controlled in pairs:
        counts[{(True, True): 0, (True, False): 1, (False, True): 2,
                (False, False): 3}[(bool(baseline), bool(controlled))]] += 1
    return tuple(counts)


def compare_proportions(pairs):
    """Return the Paired comparison of (baseline success, controlled
    success) pairs over the same requests, or None when there are none
    (not applicable)."""
    counts = paired_counts(pairs)
    if not sum(counts):
        return None
    both, baseline, controlled, neither = counts
    difference, lower, upper = paired_difference(both, baseline, controlled,
                                                 neither)
    return Paired(difference, lower, upper, mcnemar(baseline, controlled),
                  counts)


def rank(fraction, count):
    """Return the 0-based index of the nearest-rank order statistic of a
    fraction (numerator, denominator) of count sorted values."""
    numerator, denominator = fraction
    return min(count, max(1, -(-numerator * count // denominator))) - 1


def distribution(values):
    """Return {count, median, p90, largest} of values (ADR 0016, decision
    8): the median as the middle value or the mean of the two middle ones,
    the 90th percentile as the order statistic of nearest rank; None for
    each when there are none."""
    ordered = sorted(values)
    if not ordered:
        return {"count": 0, "median": None, "p90": None, "largest": None}
    middle = len(ordered) // 2
    median = (ordered[middle] if len(ordered) % 2
              else (ordered[middle - 1] + ordered[middle]) / 2)
    return {"count": len(ordered), "median": median,
            "p90": ordered[rank((9, 10), len(ordered))],
            "largest": ordered[-1]}


def bootstrap_difference(cases, measure, replicates=REPLICATES, seed=SEED):
    """Return the Bootstrapped difference of a ratio of sums, controlled
    minus baseline, by the paired bootstrap over cases.

    cases is a sequence of (baseline terms, controlled terms) pairs, one
    per case, each a tuple of numbers summed over the case's requests;
    measure(terms) returns the ratio of summed terms, or None when it is
    not applicable. The difference is that of the cases as they are; it
    is None, and so are its limits, when either condition's measure is not
    applicable on them.
    """
    cases = [(tuple(baseline), tuple(controlled))
             for baseline, controlled in cases]
    if not cases:
        return Bootstrapped(None, None, None, 0)
    width = len(cases[0][0])

    def measured(weights):
        """Return both conditions' measures of the cases, each counted as
        often as its weight."""
        sums = ([0] * width, [0] * width)
        for (baseline, controlled), weight in zip(cases, weights):
            if weight:
                for side, terms in ((0, baseline), (1, controlled)):
                    for index, value in enumerate(terms):
                        sums[side][index] += weight * value
        return measure(tuple(sums[0])), measure(tuple(sums[1]))

    baseline, controlled = measured([1] * len(cases))
    if baseline is None or controlled is None:
        return Bootstrapped(None, None, None, 0)
    generator = random.Random(seed)
    differences, left_out = [], 0
    for _ in range(replicates):
        weights = [0] * len(cases)
        for _ in cases:
            weights[generator.randrange(len(cases))] += 1
        one, two = measured(weights)
        if one is None or two is None:
            left_out += 1
            continue
        differences.append(two - one)
    if not differences:
        return Bootstrapped(controlled - baseline, None, None, left_out)
    differences.sort()
    return Bootstrapped(controlled - baseline,
                        differences[rank(LOWER, len(differences))],
                        differences[rank(UPPER, len(differences))],
                        left_out)


# Default Mode: A Published Worked Example and Values Worked by Hand

def accuracy(terms):
    """Return correct over requests, or None for no requests."""
    correct, requests = terms
    return correct / requests if requests else None


def run_synthetic():
    """Return the problems found on the worked examples."""
    problems = []
    # StatsDirect, paired proportions: medium A grew 32 of 50 cultures and
    # medium B 22; A minus B is 0.2 with 95% interval 0.056156 to
    # 0.329207, and the exact two-sided P is 0.0129.
    difference, lower, upper = paired_difference(20, 2, 12, 16)
    if (abs(difference - 0.2) > 1e-12 or abs(lower - 0.056156) > 5e-7
            or abs(upper - 0.329207) > 5e-7):
        problems.append("the paired interval differs from the worked "
                        "example")
    if round(mcnemar(12, 2), 4) != 0.0129:
        problems.append("the McNemar test differs from the worked example")
    low, high = wilson(0, 10)
    if low != 0.0 or abs(high - Z95 ** 2 / (10 + Z95 ** 2)) > 1e-12:
        problems.append("the Wilson interval of no successes is wrong")
    pairs = [(True, True)] * 20 + [(False, True)] * 12 \
        + [(True, False)] * 2 + [(False, False)] * 16
    found = compare_proportions(pairs)
    if found is None or found.counts != (20, 2, 12, 16) or abs(
            found.difference - 0.2) > 1e-12:
        problems.append("the paired comparison counts the pairs wrongly")
    if compare_proportions([]) is not None:
        problems.append("an empty comparison is not 'not applicable'")
    # Each case's controlled accuracy is its baseline's plus one in ten, so
    # every paired replicate differs by exactly 0.1.
    shifted = [((case, 10), (case + 1, 10)) for case in range(10)]
    if any(abs(value - 0.1) > 1e-12 for value in bootstrap_difference(
            shifted, accuracy, replicates=200)[:3]):
        problems.append("the bootstrap does not pair the conditions")
    if bootstrap_difference([((0, 0), (0, 0))], accuracy) != Bootstrapped(
            None, None, None, 0):
        problems.append("a measure that is not applicable is bootstrapped")
    return problems


def main(argv=None):
    """Run the default mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    problems = run_synthetic()
    for problem in problems:
        print("FAILED: %s" % problem)
    if problems:
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
