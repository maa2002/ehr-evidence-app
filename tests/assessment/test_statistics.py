"""Checks of ehr_evidence_app.assessment.statistics on a published worked
example and values worked by hand; no record is read."""

import math
import unittest

from ehr_evidence_app.assessment import statistics

Z2 = statistics.Z95 ** 2


def accuracy(terms):
    """Return correct over requests, or None for no requests."""
    correct, requests = terms
    return correct / requests if requests else None


class WilsonTests(unittest.TestCase):
    """The score interval of one proportion."""

    def test_ends(self):
        """No successes: from 0 to z^2/(n+z^2); all: from n/(n+z^2) to 1."""
        self.assertEqual(statistics.wilson(0, 20),
                         (0.0, Z2 / (20 + Z2)))
        low, high = statistics.wilson(20, 20)
        self.assertAlmostEqual(low, 20 / (20 + Z2), places=12)
        self.assertEqual(high, 1.0)

    def test_values_worked_by_hand(self):
        """81 of 263, 15 of 148 and 1 of 29, to four places."""
        for successes, total, expected in ((81, 263, (0.2553, 0.3662)),
                                           (15, 148, (0.0624, 0.1605)),
                                           (1, 29, (0.0061, 0.1718))):
            low, high = statistics.wilson(successes, total)
            self.assertEqual((round(low, 4), round(high, 4)), expected)

    def test_no_trials(self):
        """With no trials the interval is the whole range."""
        self.assertEqual(statistics.wilson(0, 0), (0.0, 1.0))


class NewcombeTests(unittest.TestCase):
    """Newcombe's method 10 for a paired difference of proportions."""

    def test_published_example(self):
        """StatsDirect's paired proportions: 20 pairs both, 12 the first
        medium only, 2 the second only, 16 neither; the difference 0.2,
        95% interval 0.056156 to 0.329207, exact P 0.0129."""
        difference, lower, upper = statistics.paired_difference(20, 2, 12,
                                                                16)
        self.assertAlmostEqual(difference, 0.2, places=12)
        self.assertEqual((round(lower, 6), round(upper, 6)),
                         (0.056156, 0.329207))
        self.assertEqual(statistics.mcnemar(12, 2), 212 / 16384)
        self.assertEqual(round(statistics.mcnemar(12, 2), 4), 0.0129)

    def test_correlation_is_corrected(self):
        """The numerator ad - bc, when positive, is moved n/2 towards zero:
        320 - 24 = 296 becomes 271 over the root of 22 * 28 * 32 * 18.
        Without the correction (method 8) the interval would be narrower,
        0.061805 to 0.324162."""
        self.assertAlmostEqual(statistics.correlation(20, 2, 12, 16),
                               271 / math.sqrt(22 * 28 * 32 * 18),
                               places=12)
        _, lower, upper = statistics.paired_difference(20, 2, 12, 16)
        self.assertLess(lower, 0.061805)
        self.assertGreater(upper, 0.324162)

    def test_correlation_edges(self):
        """A negative numerator is not corrected; a positive one smaller
        than n/2 becomes 0; an empty margin gives 0."""
        self.assertAlmostEqual(statistics.correlation(1, 5, 5, 1),
                               -24 / 36, places=12)
        self.assertEqual(statistics.correlation(2, 1, 1, 2), 0.0)
        self.assertEqual(statistics.correlation(5, 0, 0, 0), 0.0)

    def test_turns_over_with_the_conditions(self):
        """Swapping the conditions negates the difference and its
        interval."""
        difference, lower, upper = statistics.paired_difference(10, 3, 1, 6)
        self.assertAlmostEqual(difference, -2 / 20, places=12)
        self.assertTrue(lower < difference < upper)
        self.assertEqual(statistics.paired_difference(10, 1, 3, 6),
                         (-difference, -upper, -lower))

    def test_narrows_with_more_pairs(self):
        """Ten times the pairs give a narrower interval."""
        wide = statistics.paired_difference(10, 3, 1, 6)
        narrow = statistics.paired_difference(100, 30, 10, 60)
        self.assertLess(narrow[2] - narrow[1], wide[2] - wide[1])

    def test_no_pairs(self):
        """No pairs cannot be compared."""
        with self.assertRaises(ValueError):
            statistics.paired_difference(0, 0, 0, 0)


class McNemarTests(unittest.TestCase):
    """The exact two-sided test of the discordant pairs."""

    def test_values_worked_by_hand(self):
        """0 and 6: twice 1/64; 3 and 3, or none: 1; 9 and 1: twice
        11/1024."""
        self.assertEqual(statistics.mcnemar(0, 6), 2 / 64)
        self.assertEqual(statistics.mcnemar(3, 3), 1.0)
        self.assertEqual(statistics.mcnemar(0, 0), 1.0)
        self.assertEqual(statistics.mcnemar(9, 1), 22 / 1024)


class CompareTests(unittest.TestCase):
    """Pairs of (baseline, controlled) successes."""

    def test_controlled_minus_baseline(self):
        """The counts are both, the baseline's only, the controlled
        condition's only and neither, and the difference is controlled
        minus baseline."""
        pairs = ([(True, True)] * 20 + [(False, True)] * 12
                 + [(True, False)] * 2 + [(False, False)] * 16)
        found = statistics.compare_proportions(pairs)
        self.assertEqual(found.counts, (20, 2, 12, 16))
        self.assertAlmostEqual(found.difference, 0.2, places=12)
        self.assertEqual(found.p_value, 212 / 16384)
        flipped = statistics.compare_proportions(
            [(controlled, baseline) for baseline, controlled in pairs])
        self.assertAlmostEqual(flipped.difference, -0.2, places=12)

    def test_no_pairs_is_not_applicable(self):
        """No requests: not applicable, never 0."""
        self.assertIsNone(statistics.compare_proportions([]))


class BootstrapTests(unittest.TestCase):
    """The paired bootstrap over cases for ratios of sums."""

    def test_nearest_rank(self):
        """The 2.5th and 97.5th percentiles of 10,000 values are the 250th
        and 9,750th; of 39 values the 1st and 39th."""
        self.assertEqual(statistics.rank(statistics.LOWER, 10000), 249)
        self.assertEqual(statistics.rank(statistics.UPPER, 10000), 9749)
        self.assertEqual(statistics.rank(statistics.LOWER, 39), 0)
        self.assertEqual(statistics.rank(statistics.UPPER, 39), 38)
        self.assertEqual(statistics.rank(statistics.UPPER, 1), 0)

    def test_two_cases_worked_by_hand(self):
        """Case A: baseline 0 of 1, controlled 1 of 1; case B: 1 of 1 in
        both. Drawn twice, the cases give differences of 1 (A, A), 0.5 (A
        and B) and 0 (B, B), each pair at least a quarter of the time, so
        the interval runs from 0 to 1 around the difference 0.5."""
        found = statistics.bootstrap_difference(
            [((0, 1), (1, 1)), ((1, 1), (1, 1))], accuracy)
        self.assertEqual(found, statistics.Bootstrapped(0.5, 0.0, 1.0, 0))

    def test_conditions_are_paired(self):
        """Every case gains one in ten, so every replicate does: an
        unpaired bootstrap would give a wide interval instead."""
        cases = [((case, 10), (case + 1, 10)) for case in range(10)]
        found = statistics.bootstrap_difference(cases, accuracy,
                                                replicates=500)
        for value in found[:3]:
            self.assertAlmostEqual(value, 0.1, places=12)

    def test_not_applicable(self):
        """No cases, or a measure without a denominator, are not
        applicable; replicates without one are left out and counted."""
        self.assertEqual(statistics.bootstrap_difference([], accuracy),
                         statistics.Bootstrapped(None, None, None, 0))
        self.assertEqual(statistics.bootstrap_difference(
            [((0, 0), (0, 0))], accuracy),
            statistics.Bootstrapped(None, None, None, 0))
        found = statistics.bootstrap_difference(
            [((1, 1), (1, 1)), ((0, 0), (0, 0))], accuracy)
        self.assertEqual(found.difference, 0.0)
        self.assertTrue(1500 < found.left_out < 3500)

    def test_fixed_seed(self):
        """The same seed gives the same interval."""
        cases = [((case % 3, 4), (case % 4, 4)) for case in range(30)]
        self.assertEqual(statistics.bootstrap_difference(cases, accuracy),
                         statistics.bootstrap_difference(cases, accuracy))

    def test_default_mode_passes(self):
        """The module's own check finds nothing."""
        self.assertEqual(statistics.run_synthetic(), [])


if __name__ == "__main__":
    unittest.main()
