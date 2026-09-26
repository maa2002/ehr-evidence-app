"""Synthetic checks for ehr_evidence_app.qa.answer.

The bundle set is the bundle builder's invented one, held in memory; the
vocabulary is invented and aligned with it. No real record is read.
"""

import dataclasses
import unittest

from ehr_evidence_app.evidence import bundles, contracts, schema
from ehr_evidence_app.qa import answer, resolve
from ehr_evidence_app.qa import vocabulary as build

CASE_SET = "synthetic-1-v1"
HISTORY = contracts.ScopeRef(kind="pre_target_history")
ALL = contracts.ScopeRef(kind="case_visits")
LIMITS = contracts.ExecutionLimits(**bundles.SYNTHETIC_LIMITS)
FILLERS = {concept: "Synthetic filler %d" % concept
           for concept in range(1100, 1120)}
STANDARD = {900: "Clinical finding", 1000: "Synthetic parent disorder",
            1001: "Synthetic concept 1001", 1002: "Synthetic concept 1002",
            1003: "Synthetic twin one", 1004: "Synthetic twin two",
            **FILLERS}
PARENTS = {1000: 900, 1001: 1000, 1002: 1000, 1003: 900, 1004: 900,
           **{concept: 900 for concept in FILLERS}}


def visit(number):
    """Return a selected-visit scope."""
    return contracts.ScopeRef(kind="selected_visit", visit_number=number)


def vocabulary():
    """Return an invented vocabulary aligned with the synthetic bundles."""
    concepts = [(concept, "standard", "SNOMED", "Disorder", "S%d" % concept,
                 "", name) for concept, name in STANDARD.items()]
    pairs = []
    for concept in STANDARD:
        chain, level = [concept], 0
        while chain[-1] in PARENTS:
            chain.append(PARENTS[chain[-1]])
        for level, ancestor in enumerate(chain):
            pairs.append((ancestor, concept, level))
    synonyms = [(1003, "STW - Synthetic twin"), (1004, "STW - Synthetic twin")]
    return resolve.parse({
        build.FILES["concepts"]: build.encode_tsv("concepts", concepts),
        build.FILES["synonyms"]: build.encode_tsv("synonyms", synonyms),
        build.FILES["ancestors"]: build.encode_tsv("ancestors", pairs),
        build.FILES["icd_maps"]: build.encode_tsv("icd_maps", [])})


def bundle_set(tamper=False):
    """Return the synthetic bundle set, read from memory."""
    qa, explanations, counts = bundles.run_synthetic()
    manifest = bundles.manifest_document(qa, explanations,
                                         bundles.synthetic_provenance(),
                                         counts)
    data = {"qa/%s.json" % bundle.bundle_id: bundles.bundle_bytes(bundle)
            for bundle in qa}

    def reader(entry):
        """Return a bundle's bytes, optionally changed."""
        content = data[entry["path"]]
        return content + b" " if tamper else content

    return answer.BundleSet(CASE_SET, None, manifest, reader=reader)


VOCAB = vocabulary()
BUNDLES = bundle_set()


def ask(question, scope=HISTORY, case_number=1, bundle_set=BUNDLES):
    """Answer one invented question."""
    return answer.answer(question, CASE_SET, case_number, scope, bundle_set,
                         VOCAB, "q-1", LIMITS)


class ListTests(unittest.TestCase):
    """Lists need complete coverage and cite every record."""

    def test_list(self):
        """History and all visits list each condition once."""
        result = ask("List all recorded conditions.")
        response = result.response
        self.assertEqual((response.outcome, response.task),
                         ("answer", "condition_list"))
        self.assertEqual(result.groups, (("concept", 1001),))
        self.assertIn("1 distinct condition is recorded", response.text)
        self.assertEqual(response.claims[0].refs, ("E1",))
        everything = ask("What diagnoses are recorded?", ALL)
        self.assertEqual(everything.groups, (("concept", 1001),
                                             ("concept", 1002)))
        self.assertIn("2 distinct conditions are recorded",
                      everything.response.text)

    def test_empty_scope_is_not_recorded(self):
        """A complete empty scope is not recorded, citing coverage."""
        response = ask("List the diagnoses.", visit(2)).response
        self.assertEqual(response.outcome, "not_recorded")
        self.assertEqual(response.claims[0].refs, ("C1",))
        self.assertIn(answer.ABSENCE, response.text)


class PresenceTests(unittest.TestCase):
    """Presence needs a matching record; absence needs complete coverage."""

    def test_recorded(self):
        """Exact and more specific matches cite every record."""
        result = ask("Is synthetic concept 1001 recorded?")
        self.assertEqual((result.response.outcome, result.matched),
                         ("answer", (("condition", 1),)))
        parent = ask("Does the patient have synthetic parent disorder?", ALL)
        self.assertEqual(parent.matched, (("condition", 1), ("condition", 2),
                                          ("condition", 3)))
        self.assertIn("a more specific concept", parent.response.text)

    def test_not_recorded(self):
        """No match with complete coverage cites the coverage record."""
        response = ask("Any history of synthetic concept 1002?").response
        self.assertEqual(response.outcome, "not_recorded")
        self.assertEqual(response.claims[0].refs, ("C1",))
        self.assertIn(answer.ABSENCE, response.text)

    def test_terms_that_need_clarification(self):
        """Unknown, ambiguous and too broad terms are not guessed."""
        for question, reason in (
                ("Is zzqx recorded?", "concept_unknown"),
                ("Is STW recorded?", "concept_ambiguous"),
                ("Is clinical finding recorded?", "concept_too_broad")):
            response = ask(question).response
            self.assertEqual((response.outcome, response.reason_codes),
                             ("clarification", (reason,)), question)
        response = ask("Is synthetic concept recorded?").response
        self.assertIn("Did you mean", response.text)


class BoundaryTests(unittest.TestCase):
    """Scope, case, limits and unsupported requests."""

    def test_scope_is_never_changed_silently(self):
        """A different scope in the question gets a clarification."""
        response = ask("Is synthetic concept 1002 recorded in the final "
                       "admission?").response
        self.assertEqual((response.outcome, response.reason_codes,
                          response.scope, response.task),
                         ("clarification", ("scope_ambiguous",), HISTORY,
                          "condition_presence"))
        self.assertIn("visit 3", response.text)
        same = ask("Is synthetic concept 1002 recorded in the final "
                   "admission?", visit(3)).response
        self.assertEqual(same.outcome, "answer")

    def test_unsupported_and_other_cases(self):
        """Abstentions and clarifications carry no claims or bundle."""
        for question, outcome, reason in (
                ("What medications is she on?", "abstention",
                 "task_unsupported"),
                ("Why did the model predict this?", "clarification",
                 "prediction_selection_needed"),
                ("Is it recorded for patient 7?", "clarification",
                 "case_ambiguous")):
            response = ask(question).response
            self.assertEqual((response.outcome, response.reason_codes,
                              response.claims, response.bundle),
                             (outcome, (reason,), (), None), question)

    def test_limits_and_unknown_cases(self):
        """Over-long questions and cases without bundles end early."""
        response = ask("x" * (LIMITS.max_question_chars + 1)).response
        self.assertEqual((response.outcome, response.text),
                         ("operational_error", None))
        response = ask("Is synthetic concept 1001 recorded?",
                       case_number=2).response
        self.assertEqual((response.outcome, response.reason_codes),
                         ("case_rejection", ("case_not_admitted",)))

    def test_changed_bundle_is_refused(self):
        """A bundle that differs from its manifest entry fails closed."""
        with self.assertRaisesRegex(answer.AnswerError,
                                    "bundle_hash_mismatch"):
            ask("List all recorded conditions.",
                bundle_set=bundle_set(tamper=True))

    def test_missing_scope_bundle_is_rejected(self):
        """A scope without a bundle is rejected, not answered."""
        manifest = {"files": [{"kind": "qa", "case_number": 1,
                               "scope": {"kind": "selected_visit",
                                         "visit_number": 1}}]}
        sparse = answer.BundleSet(CASE_SET, None, manifest,
                                  reader=lambda entry: b"")
        response = ask("List all recorded conditions.",
                       bundle_set=sparse).response
        self.assertEqual((response.outcome, response.reason_codes),
                         ("case_rejection", ("scope_invalid",)))

    def test_incomplete_coverage_abstains(self):
        """A scope whose coverage is not complete gets no answer."""
        good, digest = BUNDLES.qa_bundle(1, HISTORY)
        coverage = dataclasses.make_dataclass(
            "Coverage", [("status", str)])("unavailable")
        stub = dataclasses.make_dataclass(
            "Stub", [("coverage", object)])(coverage)

        class Partial(answer.BundleSet):
            """A bundle set whose history bundle is incomplete."""

            def qa_bundle(self, case_number, scope):
                """Return the incomplete stand-in."""
                return stub, digest

        partial = Partial(CASE_SET, None, {"files": [
            {"kind": "qa", "case_number": 1,
             "scope": {"kind": "selected_visit", "visit_number": 1}}]})
        response = ask("Is synthetic concept 1001 recorded?",
                       scope=HISTORY, bundle_set=partial).response
        self.assertEqual((response.outcome, response.reason_codes),
                         ("abstention", ("coverage_incomplete",)))
        self.assertIsInstance(good, schema.QABundle)

    def test_plans(self):
        """The pilot and registered case sets have pinned bundle sets."""
        plan = answer.bundle_set_plan(answer.PILOT)
        self.assertEqual(plan["manifest"]["sha256"],
                         bundles.PILOT_MANIFEST_SHA256)
        self.assertEqual(answer.bundle_set_plan("dev-30-v1")["label"],
                         "dev-30-v1")
        with self.assertRaisesRegex(answer.AnswerError, "case_set_unknown"):
            answer.bundle_set_plan("other-2-v1")


if __name__ == "__main__":
    unittest.main()
