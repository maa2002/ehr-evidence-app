"""Synthetic checks for ehr_evidence_app.qa.suite.

The snapshot, bundles and vocabulary are the invented ones of the bundle
builder and the suite; the real modes are only checked for refusing a
held-out case set before anything is read.
"""

import contextlib
import dataclasses
import io
import unittest

from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import bundles, contracts
from ehr_evidence_app.qa import answer, suite

HISTORY = contracts.ScopeRef(kind="pre_target_history")
ALL = contracts.ScopeRef(kind="case_visits")
LIMITS = contracts.ExecutionLimits(**bundles.SYNTHETIC_LIMITS)
VOCAB = suite.synthetic_vocabulary()
SNAPSHOT, CASE_SET, _, _, _ = bundles.synthetic_inputs()
ORACLE = suite.Oracle(SNAPSHOT, CASE_SET, VOCAB)
BUNDLES = suite.synthetic_bundle_set()


def visit(number):
    """Return a selected-visit scope."""
    return contracts.ScopeRef(kind="selected_visit", visit_number=number)


def keys(*native_ids):
    """Return condition record keys."""
    return frozenset(("condition", native_id) for native_id in native_ids)


def ask(question, scope):
    """Answer one invented question about the synthetic case."""
    return answer.answer(question, CASE_SET.label, 1, scope, BUNDLES, VOCAB,
                         "q-1", LIMITS)


def probe(**fields):
    """Return a presence probe about the synthetic case in all visits."""
    values = dict(family="presence_name", question="unused", case_number=1,
                  scope=ALL, outcome="answer", task="condition_presence",
                  records=keys(1, 2, 3), coded_only=True, subject=1000)
    values.update(fields)
    return suite.Probe(**values)


class OracleTests(unittest.TestCase):
    """The oracle reads records by scope from the snapshot itself."""

    def test_scope_records(self):
        """History, each visit and all visits hold their own records."""
        case = ORACLE.cases[0]
        found = {suite.slug(scope): frozenset(
            record.key for record in ORACLE.scope_records(case, scope))
            for scope in ORACLE.scopes(case)}
        self.assertEqual(found, {"history": keys(1), "visit-1": keys(1),
                                 "visit-2": keys(), "visit-3": keys(2, 3),
                                 "all": keys(1, 2, 3)})

    def test_matches(self):
        """A concept matches itself and its descendants; codes by prefix."""
        records = ORACLE.scope_records(ORACLE.cases[0], ALL)
        self.assertEqual(ORACLE.matches(records, 1000), keys(1, 2, 3))
        self.assertEqual(ORACLE.matches(records, 1002), keys(3))
        coded = [suite.Record(("condition", 7), 0, "ICD10CM", "I50.9",
                              "I509", ("source", "ICD10CM", "I50.9")),
                 suite.Record(("condition", 8), 0, "ICD10CM", "I50.20",
                              "I5020", ("source", "ICD10CM", "I50.20"))]
        self.assertEqual(ORACLE.code_matches(coded, "I50"), keys(7, 8))
        self.assertEqual(ORACLE.code_matches(coded, "I509"), keys(7))

    def test_terms(self):
        """Pools hold invented, too broad and ambiguous terms only."""
        self.assertEqual(ORACLE.broad_terms(), ["clinical finding"])
        self.assertEqual(ORACLE.ambiguous_terms(), ["stw", "synthetic twin"])
        self.assertEqual(ORACLE.invented_terms(), list(suite.INVENTED))
        self.assertEqual(ORACLE.broader(1001), 1000)
        self.assertEqual(ORACLE.usable(900), "concept_too_broad")


class ScoringTests(unittest.TestCase):
    """The scorer finds every kind of wrong response."""

    RESULT = ask("Is synthetic parent disorder recorded?", ALL)

    def score(self, expected, result=None):
        """Return the failures of the result against a probe."""
        bundle = BUNDLES.qa_bundle(1, ALL)[0]
        return suite.score(expected, result or self.RESULT, bundle,
                           frozenset())[0]

    def test_correct_response(self):
        """The pipeline's own answer meets the oracle's expectation."""
        self.assertEqual(self.score(probe()), [])

    def test_wrong_responses(self):
        """Each departure from the expectation is its own category."""
        for expected, failures in (
                (probe(records=keys(1, 2)), ["records_extra"]),
                (probe(records=keys(1, 2, 3, 4)), ["records_missing"]),
                (probe(reasons=("unmapped_records_in_scope",)), ["reason"]),
                (probe(task="condition_list", groups=frozenset()), ["task"]),
                (probe(scope=HISTORY), ["scope"]),
                (probe(outcome="not_recorded"), ["outcome"])):
            self.assertEqual(self.score(expected), failures, expected)
        altered = dataclasses.replace(self.RESULT, matched=tuple(
            sorted(keys(1, 2))))
        self.assertEqual(self.score(probe(records=keys(1, 2)), altered),
                         ["citations"])

    def test_list_groups(self):
        """A list must name every expected group."""
        result = ask("List all recorded conditions.", ALL)
        listing = probe(family="list", task="condition_list", subject=None,
                        coded_only=False,
                        groups=frozenset({("concept", 1001)}))
        self.assertEqual(self.score(listing, result), ["groups"])

    def test_description_matches_are_unscored(self):
        """Matches of records without a standard concept are set apart."""
        bundle = BUNDLES.qa_bundle(1, ALL)[0]
        failures, unscored = suite.score(probe(records=keys(1, 2)),
                                         self.RESULT, bundle, keys(3))
        self.assertEqual((failures, unscored), ([], keys(3)))

    def test_bundles_from_other_inputs_are_refused(self):
        """A bundle built from other inputs fails the run."""
        cache = suite.BundleCache(BUNDLES, ("0" * 64,) * 3)
        with self.assertRaisesRegex(suite.SuiteError, "bundle_inputs_differ"):
            cache.qa_bundle(1, HISTORY)


class RunTests(unittest.TestCase):
    """Runs are complete, reproducible and aggregate only."""

    def test_synthetic_run(self):
        """Every probe of the synthetic case is answered correctly."""
        document, counts = suite.run_synthetic()
        self.assertEqual(document["correct"], document["questions"])
        self.assertEqual(set(document["families"]),
                         set(suite.FAMILIES) - {"presence_code"})
        self.assertEqual(document["failures"], {})
        self.assertEqual(counts["questions: returned the concept"],
                         counts["names asked"] * len(suite.PRESENCE))

    def test_probes_are_reproducible(self):
        """The same inputs always give the same questions."""
        first, _ = suite.generate(ORACLE)
        second, _ = suite.generate(suite.Oracle(SNAPSHOT, CASE_SET, VOCAB))
        self.assertEqual(first, second)

    def test_held_out_case_sets_are_refused(self):
        """The evaluation case set is never questioned here."""
        with self.assertRaisesRegex(suite.SuiteError, "case_set_held_out"):
            suite.run_real("eval-100-v1")
        with self.assertRaisesRegex(registry.RegistryError,
                                    "case_set_unknown"):
            suite.run_real("other-5-v1")

    def test_command(self):
        """The default mode passes; real options need --operator-real."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(suite.main([]), 0)
            self.assertEqual(suite.main(["--case-set", "dev-30-v1"]), 2)
            self.assertEqual(suite.main(["--operator-real"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
