"""Synthetic checks for ehr_evidence_app.validation.support.

Candidates come from the QA templates on the bundle builder's invented
bundles and the suite's invented vocabulary; no real record is read.
"""

import contextlib
import io
import unittest

from ehr_evidence_app.evidence import bundles, contracts
from ehr_evidence_app.qa import answer, suite
from ehr_evidence_app.validation import candidate as candidates
from ehr_evidence_app.validation import gate, structure, support

ALL = contracts.ScopeRef(kind="case_visits")
HISTORY = contracts.ScopeRef(kind="pre_target_history")
LIMITS = contracts.ExecutionLimits(**bundles.SYNTHETIC_LIMITS)
VOCAB = suite.synthetic_vocabulary()
BUNDLES = suite.synthetic_bundle_set()


def ask(question, scope):
    """Return the Result of one invented question."""
    return answer.answer(question, "synthetic-1-v1", 1, scope, BUNDLES,
                         VOCAB, "q-1", LIMITS)


def statements(candidate, **changes):
    """Return a candidate with statements replaced by index."""
    items = list(candidate.statements)
    for index, statement in changes.items():
        items[int(index[1:])] = statement
    return candidate.model_copy(update={"statements": tuple(items)})


class SupportTests(unittest.TestCase):
    """Every statement must be supported by its cited records."""

    def test_template_answers_pass(self):
        """Lists and presence checks, recorded and not, are supported."""
        for question, scope, outcome in (
                ("List all recorded conditions.", ALL, "answer"),
                ("List all recorded conditions.",
                 contracts.ScopeRef(kind="selected_visit", visit_number=2),
                 "not_recorded"),
                ("Is synthetic parent disorder recorded?", ALL, "answer"),
                ("Is synthetic concept 1002 recorded?", HISTORY,
                 "not_recorded")):
            result = ask(question, scope)
            self.assertEqual(result.response.outcome, outcome, question)
            bundle = BUNDLES.qa_bundle(1, scope)[0]
            self.assertEqual(support.check(result.candidate, bundle, VOCAB),
                             (), question)

    def test_list_faults(self):
        """Omitted diagnoses, wrong counts or visits, another condition's
        records, miscounted summaries and wrong disclosures."""
        bundle, digest = structure.synthetic_bundle()
        good = structure.synthetic_list(bundle, digest)
        for fault in support.synthetic_faults(good):
            self.assertEqual(support.check(fault, bundle),
                             ("claim_unsupported",))

    def test_presence_faults(self):
        """An omitted match, a wrong match kind or count, and a negative
        for a recorded condition are unsupported."""
        result = ask("Is synthetic parent disorder recorded?", ALL)
        bundle = BUNDLES.qa_bundle(1, ALL)[0]
        good = result.candidate
        items = [index for index, statement in enumerate(good.statements)
                 if statement.kind == "presence_item"]
        item = good.statements[items[0]]
        omitted = good.model_copy(update={"statements": tuple(
            statement for statement in good.statements
            if statement is not item)})
        rekinded = statements(good, **{"s%d" % items[0]: item.model_copy(
            update={"slots": item.slots.model_copy(update={
                "match": "icd_description"})})})
        summary = good.statements[0]
        recount = statements(good, s0=summary.model_copy(update={
            "slots": summary.slots.model_copy(update={"matches": 99})}))
        for fault in (omitted, rekinded, recount):
            self.assertEqual(support.check(fault, bundle, VOCAB),
                             ("claim_unsupported",))
        negative = ask("Is synthetic concept 1002 recorded?", HISTORY)
        absent = negative.candidate.model_copy(update={
            "scope": ALL, "bundle": contracts.BundleRef(
                bundle_id=bundle.bundle_id, sha256="5" * 64)})
        self.assertEqual(support.check(absent, bundle, VOCAB),
                         ("claim_unsupported",))
        incomplete = bundle.model_copy(update={
            "coverage": bundle.coverage.model_copy(
                update={"status": "failed"})})
        self.assertEqual(support.check(good, incomplete, VOCAB),
                         ("claim_unsupported",))

    def test_presence_conditions_are_named_once(self):
        """One condition split into two items of the same match kind is
        unsupported, although each item alone is true."""
        result = ask("Is synthetic parent disorder recorded?", ALL)
        bundle = BUNDLES.qa_bundle(1, ALL)[0]
        good = result.candidate
        index = next(number for number, statement in enumerate(
            good.statements) if statement.kind == "presence_item"
            and len(statement.refs) > 1)
        item = good.statements[index]
        events = candidates.bundle_events(bundle)
        halves = tuple(item.model_copy(update={
            "refs": refs, "slots": item.slots.model_copy(update={
                "condition": candidates.ConditionName(event=refs[0]),
                "count": len(refs),
                "visits": support.visits_of(bundle, [events[ref]
                                                     for ref in refs])})})
            for refs in (item.refs[:1], item.refs[1:]))
        split = good.model_copy(update={
            "statements": good.statements[:index] + halves
            + good.statements[index + 1:]})
        self.assertEqual(support.check(good, bundle, VOCAB), ())
        self.assertEqual(support.check(split, bundle, VOCAB),
                         ("claim_unsupported",))

    def test_refusals_state_no_records(self):
        """Clarifications and abstentions have nothing to support."""
        result = ask("What medications is she on?", HISTORY)
        self.assertEqual(result.response.outcome, "abstention")
        self.assertEqual(support.check(result.candidate), ())

    def test_released_answers_must_be_supported(self):
        """An unsupported candidate is withheld as a validation
        rejection."""
        result = ask("List all recorded conditions.", ALL)
        bundle, digest = BUNDLES.qa_bundle(1, ALL)
        item = result.candidate.statements[1]
        wrong = statements(result.candidate, s1=item.model_copy(update={
            "slots": item.slots.model_copy(update={
                "count": item.slots.count + 1})}))
        response = gate.release(wrong, "q-1", result.response.case, ALL,
                                bundle, digest, VOCAB)
        self.assertEqual((response.outcome, response.reason_codes,
                          response.text),
                         ("validation_rejection", ("claim_unsupported",),
                          None))
        self.assertIsInstance(result.candidate, candidates.Candidate)

    def test_command(self):
        """The default mode passes on invented data."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(support.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
