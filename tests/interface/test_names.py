"""Tests of what the page calls the case sets and the patients
(interface.names)."""

import re
import unittest

from ehr_evidence_app.interface import names


class NameTests(unittest.TestCase):
    """Cohorts and pseudonyms."""

    def test_cohorts(self):
        """The served case sets and the invented ones have plain names;
        any other keeps its label."""
        self.assertEqual(names.cohort("dev-30-v1"), "Development cohort")
        self.assertEqual(names.cohort("pilot-10-v1"), "Pilot cohort")
        self.assertEqual(names.cohort("synthetic-1-v1"),
                         "Demonstration cohort")
        self.assertEqual(names.cohort("other-5-v1"), "other-5-v1")
        self.assertTrue(names.invented("synthetic-1-v1"))
        self.assertFalse(names.invented("dev-30-v1"))

    def test_pseudonyms_are_fixed_and_distinct(self):
        """A pseudonym is a given name and an initial, the same each time,
        different for each case of a case set, with no given name twice
        among the first sixty cases."""
        pattern = re.compile(r"^[A-Z][a-z]+ [A-Z]\.$")
        count = len(names.GIVEN_NAMES) * len(names.INITIALS)
        for label in ("dev-30-v1", "pilot-10-v1", "synthetic-1-v1"):
            found = [names.pseudonym(label, number)
                     for number in range(1, count + 1)]
            self.assertTrue(all(pattern.fullmatch(name) for name in found))
            self.assertEqual(len(set(found)), count)
            self.assertEqual(len({names.given_name(name)
                                  for name in found[:60]}), 60)
            self.assertEqual(found[0], names.pseudonym(label, 1))
        self.assertNotEqual([names.pseudonym("dev-30-v1", number)
                             for number in range(1, 11)],
                            [names.pseudonym("pilot-10-v1", number)
                             for number in range(1, 11)])
        self.assertEqual(names.pseudonym("dev-30-v1", count + 1),
                         names.pseudonym("dev-30-v1", 1) + " 2")
        self.assertEqual(names.given_name("Jordan K."), "Jordan")
        self.assertEqual(names.unbroken("dev-30-v1"),
                         "dev\u201130\u2011v1")


if __name__ == "__main__":
    unittest.main()
