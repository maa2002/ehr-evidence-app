"""Checks of ehr_evidence_app.assessment.metrics on examples worked by hand.

Every reference and response here is invented; each expected figure is
worked out in the test's docstring or comments.
"""

import unittest

from ehr_evidence_app.assessment import metrics, statistics
from ehr_evidence_app.assessment.metrics import (A, B, X, Driver, Reference,
                                                 Response, Statement, record)

C = ("concept", 103)
CODES = ("batch_sensitive_prediction",)
P1 = ("prediction",)
COVERAGE = ("coverage",)


def nonzero(scored):
    """Return the terms of a scored request that are not zero."""
    return {key: value for key, value in scored.terms.items() if value}


def presence_reference(outcome="answer", request_id="p1"):
    """Return an invented presence reference asking about A (concept 101):
    record 1 is A itself (exact, visit 1), record 5 a more specific
    concept C (visit 3), and record 4 has no standard concept (visit 1),
    so cannot be judged. With outcome "not_recorded" nothing matches."""
    names = {record(1): A, record(5): C, record(4): X, record(3): B}
    groups = ({A: frozenset((record(1),)), C: frozenset((record(5),))}
              if outcome == "answer" else {})
    matches = ({record(1): "exact", record(5): "more_specific"}
               if outcome == "answer" else {})
    return Reference(request_id, ("s", 1), "condition_presence", "presence",
                     outcome, groups=groups, matches=matches, names=names,
                     visits={record(1): 1, record(5): 3, record(4): 1,
                             record(3): 2},
                     unmapped=1, unjudged=frozenset((record(4),)),
                     citable=frozenset(names) | {COVERAGE})


def presence_item(name, numbers, visits, match):
    """Return an invented presence item."""
    return Statement("presence_item", cited=tuple(record(number)
                                                   for number in numbers),
                     name=name, count=len(numbers), visits=visits,
                     match=match)


def explanation_reference(outcome="explanation", request_id="x1"):
    """Return an invented explanation reference: label 500 with score
    0.8734 and logit 1.932; driver 201 (rank 1, score 0.4521) with history
    records 10 and 11 (visits 1 and 2) and context record 12; driver 202
    (rank 2, score 0.2) with history record 13 (visit 1) and no context;
    one eligible driver not described; the prediction is batch-sensitive.
    """
    drivers = (Driver(201, 1, 0.4521, frozenset((record(10), record(11))),
                      frozenset((record(12),))),
               Driver(202, 2, 0.2, frozenset((record(13),))))
    citable = {P1, ("attribution", 201), ("attribution", 202),
               ("attribution", 203), record(10), record(11), record(12),
               record(13)}
    return Reference(request_id, ("s", 2), metrics.EXPLANATION,
                     "explanation", outcome,
                     visits={record(10): 1, record(11): 2, record(13): 1},
                     citable=frozenset(citable),
                     prediction=(500, 0.8734, 1.932)
                     if outcome == "explanation" else None,
                     drivers=drivers if outcome == "explanation" else (),
                     omitted=1 if outcome == "explanation" else 0,
                     restrictions=frozenset(("batch_sensitive_prediction",)))


def explanation_statements(score=0.873):
    """Return the statements of the explanation the reference expects; the
    scores as a response shows them."""
    return (
        Statement("prediction", cited=(P1,), label=500, score=score,
                  logit=1.93),
        Statement("driver", cited=(("attribution", 201),), driver=201,
                  rank=1, score=0.452),
        Statement("driver_history", cited=(record(10), record(11)),
                  driver=201, count=2, visits=(1, 2)),
        Statement("driver_context", cited=(record(12),), driver=201,
                  count=1),
        Statement("driver", cited=(("attribution", 202),), driver=202,
                  rank=2, score=0.2),
        Statement("driver_history", cited=(record(13),), driver=202,
                  count=1, visits=(1,)),
        Statement("notice", notice="drivers_omitted", count=1),
        Statement("notice", notice="restriction:batch_sensitive_prediction"),
        Statement("notice", notice="attribution_meaning"))


class ListTests(unittest.TestCase):
    """Decision 3: exact match and condition and record precision, recall
    and F1."""

    def test_perfect_list(self):
        """Three conditions with their four records, the summary, and
        the disclosure of one record without a standard concept by line and
        by reason code: 6 factual statements, 5 cited references, all
        right."""
        found = nonzero(metrics.score(metrics.list_reference(),
                                      metrics.perfect_list()))
        self.assertEqual(found, {
            "exact": 1, "list_answerable": 1, "condition_tp": 3,
            "record_tp": 4, "claims": 6, "supported": 6, "cited": 5,
            "resolved": 5, "correct": 1, "in_a": 1, "answered_a": 1,
            "answered": 1, "with_text": 1})

    def test_no_list(self):
        """A withheld list states nothing: its conditions and records are
        false negatives, and it is neither an answer nor a decline."""
        found = nonzero(metrics.score(
            metrics.list_reference(),
            Response("q1", "validation_rejection")))
        self.assertEqual(found, {"list_answerable": 1, "condition_fn": 3,
                                 "record_fn": 4, "in_a": 1})

    def test_empty_scope(self):
        """A scope without records: "not recorded" is exact and correct,
        and its one statement is supported; a list there is wrong."""
        reference = Reference("q2", ("s", 1), "condition_list", "list",
                              "not_recorded", citable=frozenset((COVERAGE,)))
        empty = Response("q2", "not_recorded", (
            Statement("list_empty", cited=(COVERAGE,)),
            Statement("notice", notice="absence")), has_text=True)
        found = nonzero(metrics.score(reference, empty))
        self.assertEqual((found.get("exact"), found.get("correct"),
                          found.get("claims"), found.get("supported")),
                         (1, 1, 1, 1))
        invented = Response("q2", "answer", (
            metrics.list_item(A, (1,), (1,)),), has_text=True)
        found = nonzero(metrics.score(reference, invented))
        self.assertEqual((found.get("exact", 0), found.get("condition_fp"),
                          found.get("record_fp"), found.get("resolved", 0),
                          found.get("unsupported_answer")),
                         (0, 1, 1, 0, 1))

    def test_record_cited_twice(self):
        """An item citing one record twice does not cite its records once
        each: it is unsupported, though its records are right. The record
        without a standard concept goes undisclosed by reason code and by
        line: 2 more unsupported statements."""
        twice = Response("q1", "answer", (
            metrics.list_item(A, (1, 2), (1, 2),
                              cited=(record(1), record(1), record(2))),),
            has_text=True)
        found = metrics.score(metrics.list_reference(), twice).terms
        self.assertEqual((found["claims"], found["supported"],
                          found["record_tp"]), (3, 0, 2))


class PresenceTests(unittest.TestCase):
    """Decision 4 and the unjudged records."""

    def test_correct_answer(self):
        """A recorded as itself in visit 1 and as a more specific concept
        in visit 3: the outcome is right and the records exact."""
        response = Response("p1", "answer", (
            presence_item(A, (1,), (1,), "exact"),
            presence_item(C, (5,), (3,), "more_specific"),
            Statement("presence_summary", totals=(2,))), has_text=True,
            reason_codes=(metrics.UNMAPPED_CODE,))
        found = nonzero(metrics.score(presence_reference(), response))
        self.assertEqual(found, {
            "presence_judged": 1, "presence_right": 1, "correct": 1,
            "in_a": 1, "answered_a": 1, "answered": 1, "with_text": 1,
            "condition_tp": 2, "record_tp": 2, "claims": 4, "supported": 4,
            "cited": 2, "resolved": 2})

    def test_wrong_match_kind(self):
        """Calling the more specific record an exact match leaves the
        outcome and records right but the item unsupported."""
        response = Response("p1", "answer", (
            presence_item(A, (1,), (1,), "exact"),
            presence_item(C, (5,), (3,), "exact")), has_text=True,
            reason_codes=(metrics.UNMAPPED_CODE,))
        found = metrics.score(presence_reference(), response).terms
        self.assertEqual((found["presence_right"], found["correct"],
                          found["claims"], found["supported"],
                          found["unsupported_answer"]), (1, 1, 3, 2, 1))

    def test_unjudged_records(self):
        """A record without a standard concept, matched by its ICD
        description, is left out of every count; an answer resting on it
        alone, where nothing else matches, is counted apart."""
        alone = Response("p2", "answer", (
            presence_item(X, (4,), (1,), "icd_description"),),
            has_text=True)
        reference = presence_reference("not_recorded", "p2")
        self.assertEqual(nonzero(metrics.score(reference, alone)),
                         {"apart": 1})
        # The summary counts the unjudged record too, so it cannot be
        # judged either.
        beside = Response("p1", "answer", (
            presence_item(A, (1,), (1,), "exact"),
            presence_item(C, (5,), (3,), "more_specific"),
            presence_item(X, (4,), (1,), "icd_description"),
            Statement("presence_summary", totals=(3,))), has_text=True,
            reason_codes=(metrics.UNMAPPED_CODE,))
        found = metrics.score(presence_reference(), beside).terms
        self.assertEqual((found["condition_tp"], found["condition_fp"],
                          found["record_fp"], found["claims"],
                          found["supported"], found["correct"]),
                         (2, 0, 0, 3, 3, 1))

    def test_not_recorded(self):
        """Nothing matches: "not recorded" with its disclosure of one
        record without a standard concept, by line and by reason code, is
        right, and its 3 statements are supported."""
        response = Response("p2", "not_recorded", (
            Statement("presence_absent", cited=(COVERAGE,), name=A),
            Statement("presence_unmapped", cited=(COVERAGE,), unmapped=1)),
            has_text=True, reason_codes=(metrics.UNMAPPED_CODE,))
        found = metrics.score(presence_reference("not_recorded", "p2"),
                              response).terms
        self.assertEqual((found["presence_right"], found["correct"],
                          found["claims"], found["supported"]),
                         (1, 1, 3, 3))
        wrong = Response("p1", "not_recorded", (
            Statement("presence_absent", cited=(COVERAGE,), name=A),),
            has_text=True)
        found = metrics.score(presence_reference(), wrong).terms
        self.assertEqual((found["presence_right"], found["correct"],
                          found["record_fn"], found["supported"]),
                         (0, 0, 2, 0))


class AnswerabilityTests(unittest.TestCase):
    """Decision 5: answers, declines and failures."""

    def reference(self, outcome):
        """Return a list reference with another expected outcome."""
        return Reference("q3", ("s", 1), "condition_list", "list", outcome)

    def test_expected_clarification(self):
        """A clarification where one is expected is appropriate; an
        answer there is neither appropriate nor correct, and every
        statement it makes is unsupported."""
        reference = self.reference("clarification")
        found = nonzero(metrics.score(reference, Response(
            "q3", "clarification", (Statement("notice", notice="ask"),),
            has_text=True)))
        self.assertEqual(found, {"in_n": 1, "appropriate": 1,
                                 "with_text": 1})
        found = nonzero(metrics.score(reference, metrics.perfect_list("q3")))
        self.assertEqual((found["in_n"], found.get("appropriate", 0),
                          found["answered"], found["unsupported_answer"]),
                         (1, 0, 1, 1))
        abstained = metrics.score(reference, Response(
            "q3", "abstention", has_text=True)).terms
        self.assertEqual(abstained["appropriate"], 0)

    def test_failures_are_not_declines(self):
        """An operational error or a validation rejection is neither an
        answer nor an abstention: never a successful abstention, never
        over-abstention."""
        for outcome in ("operational_error", "validation_rejection"):
            found = metrics.score(metrics.list_reference(),
                                  Response("q1", outcome)).terms
            self.assertEqual((found["in_a"], found["answered_a"],
                              found["declined_a"]), (1, 0, 0))
            found = metrics.score(self.reference("abstention"),
                                  Response("q3", outcome)).terms
            self.assertEqual((found["in_n"], found["appropriate"]), (1, 0))

    def test_over_abstention(self):
        """An abstention where an answer is expected is over-abstention."""
        found = metrics.score(metrics.list_reference(), Response(
            "q1", "abstention", has_text=True)).terms
        self.assertEqual((found["in_a"], found["declined_a"],
                          found["answered_a"]), (1, 1, 0))


class ExplanationTests(unittest.TestCase):
    """Decision 6, and the evidence of explanations."""

    def test_perfect_explanation(self):
        """Everything as the reference has it, scores shown to three
        figures: preserved and consistent; 9 factual statements (8
        statements and the restriction's reason code; the fixed notice
        states none); 3 lines of 3 stated right; 4 records."""
        response = Response("x1", "explanation", explanation_statements(),
                            has_text=True, wording=True, reason_codes=CODES)
        found = nonzero(metrics.score(explanation_reference(), response))
        self.assertEqual(found, {
            "driver_tp": 2, "record_tp": 4, "explanation_expected": 1,
            "consistent": 1, "explanations": 1, "preserved": 1,
            "lines_expected": 3, "lines_right": 3, "rated_explanations": 1,
            "claims": 9, "supported": 9, "cited": 7, "resolved": 7,
            "with_text": 1})

    def test_altered_score(self):
        """A score read as 0.874 instead of 0.873 alters the prediction:
        not preserved, not consistent, one unsupported statement."""
        response = Response("x1", "explanation",
                            explanation_statements(score=0.874),
                            has_text=True, reason_codes=CODES)
        found = metrics.score(explanation_reference(), response).terms
        self.assertEqual((found["preserved"], found["consistent"],
                          found["claims"], found["supported"]),
                         (0, 0, 9, 8))

    def test_no_explanation(self):
        """A withheld explanation states no driver and no record."""
        found = nonzero(metrics.score(explanation_reference(), Response(
            "x1", "validation_rejection")))
        self.assertEqual(found, {"driver_fn": 2, "record_fn": 4,
                                 "explanation_expected": 1})

    def test_repeated_line_and_wording(self):
        """A history line stated twice: its records are false positives
        the second time. A failed wording mark is a violation."""
        statements = explanation_statements() + (Statement(
            "driver_history", cited=(record(10), record(11)), driver=201,
            count=2, visits=(1, 2)),)
        found = metrics.score(explanation_reference(), Response(
            "x1", "explanation", statements, has_text=True,
            wording=False)).terms
        self.assertEqual((found["record_tp"], found["record_fp"],
                          found["violations"], found["rated_explanations"],
                          found["lines_right"]), (4, 2, 1, 1, 3))

    def test_wrong_driver_and_line(self):
        """Driver 203, out of the selection, stated in place of 202, and
        201's context line citing its history record 10: one driver
        false positive and one missed; of the 3 lines, 201's history and
        202's (still stated) are right; record 10 under context is a
        false positive and record 12 is missed."""
        statements = list(explanation_statements())
        statements[4] = Statement("driver", cited=(("attribution", 203),),
                                  driver=203, rank=2, score=0.1)
        statements[3] = Statement("driver_context", cited=(record(10),),
                                  driver=201, count=1)
        found = metrics.score(explanation_reference(), Response(
            "x1", "explanation", tuple(statements), has_text=True)).terms
        self.assertEqual((found["driver_tp"], found["driver_fp"],
                          found["driver_fn"], found["lines_right"],
                          found["record_fp"], found["record_fn"],
                          found["preserved"]), (1, 1, 1, 2, 1, 1, 0))

    def test_explanation_not_expected(self):
        """An explanation of an ineligible case states drivers the
        reference does not select."""
        found = metrics.score(explanation_reference("abstention"), Response(
            "x1", "explanation", explanation_statements(),
            has_text=True)).terms
        self.assertEqual((found["driver_fp"], found["explanation_expected"],
                          found["explanations"], found["preserved"]),
                         (2, 0, 1, 0))


class DisclosureTests(unittest.TestCase):
    """A disclosure the reference requires is a statement; leaving it out
    is an unsupported one."""

    def test_unmapped_disclosure(self):
        """A list answer whose scope has a record without a standard
        concept: without its line and its reason code, 2 unsupported
        statements; a reason code where there is no such record, 1."""
        statements = metrics.perfect_list().statements
        silent = Response("q1", "answer", tuple(
            statement for statement in statements
            if statement.kind != "list_unmapped"), has_text=True)
        found = metrics.score(metrics.list_reference(), silent).terms
        self.assertEqual((found["claims"], found["supported"]), (6, 4))
        reference = Reference("p3", ("s", 1), "condition_presence",
                              "presence", "not_recorded")
        coded = Response("p3", "not_recorded", (
            Statement("presence_absent", cited=(COVERAGE,), name=A),),
            has_text=True, reason_codes=(metrics.UNMAPPED_CODE,))
        found = metrics.score(reference, coded).terms
        self.assertEqual((found["claims"], found["supported"]), (2, 1))

    def test_explanation_disclosures(self):
        """An explanation without the notice of its case's restriction, or
        of the driver not described, or without the restriction's reason
        code, states that there is none: each is an unsupported statement,
        and the explanation is not consistent. A reason code of a
        restriction the case does not have is one too."""
        for left_out in ("restriction:batch_sensitive_prediction",
                         "drivers_omitted"):
            with self.subTest(left_out=left_out):
                response = Response("x1", "explanation", tuple(
                    statement for statement in explanation_statements()
                    if statement.notice != left_out), has_text=True,
                    reason_codes=CODES)
                found = metrics.score(explanation_reference(),
                                      response).terms
                self.assertEqual((found["claims"], found["supported"],
                                  found["consistent"]), (9, 8, 0))
        uncoded = Response("x1", "explanation", explanation_statements(),
                           has_text=True)
        found = metrics.score(explanation_reference(), uncoded).terms
        self.assertEqual((found["claims"], found["supported"],
                          found["consistent"]), (9, 8, 0))
        coded = Response("x1", "explanation", explanation_statements(),
                         has_text=True, reason_codes=(
                             "batch_sensitive_prediction",
                             "target_attached_drivers_present"))
        found = metrics.score(explanation_reference(), coded).terms
        self.assertEqual((found["claims"], found["supported"],
                          found["consistent"]), (10, 9, 0))


class SummaryTests(unittest.TestCase):
    """Empty sets, summaries and the paired comparison."""

    def test_empty_set_conventions(self):
        """A zero denominator is not applicable, never 0 or 1; F1 is 0
        when precision and recall are both 0 and not applicable when
        either is."""
        self.assertIsNone(metrics.measure("ratio", (0, 0)))
        self.assertIsNone(metrics.measure("precision", (0, 0)))
        self.assertIsNone(metrics.measure("f1", (0, 0, 3)))
        self.assertIsNone(metrics.measure("f1", (0, 3, 0)))
        self.assertEqual(metrics.measure("f1", (0, 2, 3)), 0.0)
        self.assertAlmostEqual(metrics.measure("f1", (3, 1, 2)),
                               2 * 0.75 * 0.6 / 1.35, places=12)

    def test_summary(self):
        """Over the perfect and the withheld list: exact match 1 of 2;
        condition recall 3 of 6; no clarification expected, so
        appropriate abstention is not applicable."""
        rows = [metrics.score(metrics.list_reference(),
                              metrics.perfect_list()),
                metrics.score(metrics.list_reference("q9"),
                              Response("q9", "validation_rejection"))]
        found, apart = metrics.summarise(rows)
        self.assertEqual(apart, 0)
        self.assertEqual(found["exact_match"], ((1, 2), 0.5))
        self.assertEqual(found["condition_recall"], ((3, 3), 0.5))
        self.assertEqual(found["appropriate_abstention"], ((0, 0), None))

    def test_paired_proportion(self):
        """Four lists of four cases: the baseline is exact for one, the
        controlled condition for three, including that one."""
        def rows(exact_ones):
            return [metrics.score(
                metrics.list_reference("q%d" % number, ("s", number)),
                metrics.perfect_list("q%d" % number) if number in exact_ones
                else Response("q%d" % number, "validation_rejection"))
                for number in range(4)]
        found = metrics.compare(rows({0}), rows({0, 1, 2}),
                                metrics.BY_NAME["exact_match"])
        self.assertEqual(found.counts, (1, 0, 2, 1))
        self.assertAlmostEqual(found.difference, 0.5, places=12)
        self.assertEqual(found.p_value, statistics.mcnemar(0, 2))

    def test_paired_ratio(self):
        """Condition recall by the bootstrap over cases: each case's
        controlled list finds all three conditions and its baseline
        none, so every replicate differs by exactly 1."""
        base = [metrics.score(
            metrics.list_reference("q%d" % number, ("s", number)),
            Response("q%d" % number, "validation_rejection"))
            for number in range(3)]
        ours = [metrics.score(
            metrics.list_reference("q%d" % number, ("s", number)),
            metrics.perfect_list("q%d" % number)) for number in range(3)]
        found = metrics.compare(base, ours,
                                metrics.BY_NAME["condition_recall"])
        self.assertEqual(found[:3], (1.0, 1.0, 1.0))
        self.assertIsNone(metrics.compare(
            base, ours, metrics.BY_NAME["appropriate_abstention"]))

    def test_conditions_must_be_paired(self):
        """Conditions over different requests cannot be compared, and a
        response must be scored against its own request's reference."""
        one = [metrics.score(metrics.list_reference(),
                             metrics.perfect_list())]
        two = [metrics.score(metrics.list_reference("q2"),
                             metrics.perfect_list("q2"))]
        with self.assertRaises(ValueError):
            metrics.compare(one, two, metrics.BY_NAME["exact_match"])
        with self.assertRaises(ValueError):
            metrics.score(metrics.list_reference(),
                          metrics.perfect_list("q2"))

    def test_default_mode_passes(self):
        """The module's own check finds nothing."""
        self.assertEqual(metrics.run_synthetic(), [])


if __name__ == "__main__":
    unittest.main()
