"""Synthetic checks for ehr_evidence_app.validation.evidence.

The bundles are the bundle builder's invented ones; no real record is read.
"""

import contextlib
import io
import unittest

from ehr_evidence_app.evidence import bundles
from ehr_evidence_app.validation import evidence

QA, _, _ = bundles.run_synthetic()


class EvidenceTests(unittest.TestCase):
    """A bundle must fit its request and suffice before any text."""

    def test_bundles_that_fit_pass(self):
        """Every invented bundle passes for its own request."""
        for bundle in QA:
            self.assertIsNone(evidence.qa_stop(evidence.request_for(bundle),
                                               bundle), bundle.bundle_id)

    def test_faults(self):
        """Case, scope, visits, names and coverage each stop the request."""
        bundle = next(item for item in QA if item.events
                      and item.scope.kind == "case_visits")
        for expected, faulty in evidence.synthetic_faults(bundle):
            self.assertEqual(evidence.qa_stop(evidence.request_for(bundle),
                                              faulty), expected)

    def test_readable(self):
        """A record is readable by its name or by a source code."""
        event = next(item for item in QA if item.events).events[0]
        self.assertTrue(evidence.readable(event))

    def test_command(self):
        """The default mode passes on invented data."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(evidence.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
