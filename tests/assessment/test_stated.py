"""Checks of ehr_evidence_app.assessment.stated with the metrics, on the
invented fixtures.

The template candidates of the validation mutation suite's invented
samples are read and scored against references built here from the same
bundles. Every template must score as a correct response, and every
mutant that breaks what a response states must score as a wrong one.
"""

import collections
import unittest

from ehr_evidence_app.assessment import metrics, stated
from ehr_evidence_app.validation import explanation, mutations

SAMPLES = mutations.synthetic_samples()
# The mutants whose error lies in what a response states, and that keep
# the sample's bundle and request.
STATED_REASONS = ("claim_unsupported", "prediction_altered",
                  "attribution_altered", "reference_unresolved")


def visit_numbers(bundle):
    """Return {visit reference: visit number} of a bundle."""
    return {visit.ref: visit.visit_number for visit in bundle.visits}


def qa_reference(sample):
    """Return the reference of a QA sample, built from its bundle."""
    bundle, request_id = sample.bundle, sample.candidate.request_id
    numbers = visit_numbers(bundle)
    names, visits = {}, {}
    for event in bundle.events:
        key = stated.record_key(event)
        names[key] = stated.condition_of(event)
        visits[key] = numbers[event.visit_ref]
    unmapped = frozenset(key for key, name in names.items()
                         if name[0] != "concept")
    task = sample.candidate.task
    groups, matches, unjudged = collections.defaultdict(set), {}, frozenset()
    if task == "condition_list":
        for key, name in names.items():
            groups[name].add(key)
    else:
        unjudged = unmapped
        asked = sample.condition
        for event in bundle.events:
            key, name = stated.record_key(event), names[
                stated.record_key(event)]
            kind = None
            if asked.concepts and name[0] == "concept":
                if name[1] in asked.concepts:
                    kind = "exact"
                elif any(name[1] in sample.vocab.descendants_of(concept)
                         for concept in asked.concepts):
                    kind = "more_specific"
            elif asked.code and event.source_concept is not None:
                code = event.source_concept.code.replace(".", "")
                wanted = asked.code.replace(".", "")
                kind = ("icd_code" if code == wanted else
                        "more_specific_icd_code" if code.startswith(wanted)
                        else None)
                unjudged = unjudged - {key} if kind else unjudged
            if kind:
                groups[name].add(key)
                matches[key] = kind
    outcome = "answer" if groups else "not_recorded"
    return metrics.Reference(
        request_id, (sample.case.case_set, sample.case.case_number), task,
        task, outcome, groups={name: frozenset(keys)
                               for name, keys in groups.items()},
        matches=matches, names=names, visits=visits,
        unmapped=len(unmapped), unjudged=unjudged,
        citable=frozenset(names) | {("coverage",)})


def explanation_reference(sample):
    """Return the reference of an explanation sample, built from its
    bundle."""
    bundle = sample.bundle
    numbers = visit_numbers(bundle)
    events = {event.ref: event for event in stated.events_of(bundle)}
    history = {event.ref for event in bundle.history_events}
    visits = {stated.record_key(event): numbers[event.visit_ref]
              for event in bundle.history_events}
    drivers = []
    for rank, attribution in enumerate(explanation.selected(
            bundle, sample.drivers), 1):
        linked = [events[ref] for ref in attribution.linked_event_refs
                  if ref in events]
        drivers.append(metrics.Driver(
            attribution.concept.concept_id, rank, attribution.score,
            frozenset(stated.record_key(event) for event in linked
                      if event.ref in history),
            frozenset(stated.record_key(event) for event in linked
                      if event.ref not in history)))
    names, _ = stated.lookup(bundle)
    return metrics.Reference(
        sample.candidate.request_id,
        (sample.case.case_set, sample.case.case_number),
        metrics.EXPLANATION, "explanation", "explanation", visits=visits,
        citable=frozenset(names.values()),
        prediction=(bundle.prediction.concept.concept_id,
                    bundle.prediction.score, bundle.prediction.logit),
        drivers=tuple(drivers),
        omitted=explanation.omitted(bundle, sample.drivers),
        restrictions=frozenset(bundle.restrictions))


def reference_of(sample):
    """Return the reference of a sample answered from evidence, or
    None."""
    if sample.bundle is None or sample.candidate.outcome not in (
            "answer", "not_recorded", "explanation"):
        return None
    if sample.candidate.task == metrics.EXPLANATION:
        return explanation_reference(sample)
    return qa_reference(sample)


def scored(sample, candidate):
    """Return the terms of a candidate read for the sample's request."""
    reference = reference_of(sample)
    response = stated.read(candidate.request_id, candidate.outcome,
                           candidate, sample.bundle, True)
    return metrics.score(reference, response).terms


def faultless(terms, task):
    """Return True when the terms show a response right in every way the
    metrics read. An explanation may leave a driver's history or context
    out (validation.explanation): how complete it is is measured apart."""
    common = (terms["claims"] == terms["supported"]
              and terms["cited"] == terms["resolved"])
    if task == metrics.EXPLANATION:
        return common and terms["preserved"] == 1 and terms[
            "consistent"] == 1
    return common and terms["correct"] == 1


class TemplateTests(unittest.TestCase):
    """The templates, which pass the release gate, score as correct."""

    def test_samples_are_answered(self):
        """The invented samples include lists, presence checks and
        explanations answered from evidence."""
        tasks = {sample.candidate.task for sample in SAMPLES
                 if reference_of(sample) is not None}
        self.assertEqual(tasks, {"condition_list", "condition_presence",
                                 metrics.EXPLANATION})

    def test_templates_are_faultless(self):
        """Every template answer and explanation is correct, with every
        statement supported and every reference resolved."""
        for sample in SAMPLES:
            if reference_of(sample) is None:
                continue
            with self.subTest(request=sample.candidate.request_id):
                terms = scored(sample, sample.candidate)
                self.assertTrue(faultless(terms, sample.candidate.task),
                                dict(terms))


def same_request(sample, mutant):
    """Return True for a mutant of the sample's candidate alone: its own
    bundle, document and checks."""
    return (mutant.candidate is not None
            and (mutant.bundle is None or mutant.bundle == sample.bundle)
            and mutant.document is None and mutant.checks is None)


class MutantTests(unittest.TestCase):
    """What the gate withholds for what a response states, the metrics
    count as wrong; what it releases, as right."""

    def test_stated_errors_are_found(self):
        """Every mutant of a stated error (an unsupported claim, an
        altered prediction or attribution, an unresolved reference) that
        keeps its bundle scores as a faulty response."""
        missed, checked = collections.Counter(), collections.Counter()
        for sample in SAMPLES:
            if reference_of(sample) is None:
                continue
            for mutant in mutations.mutants(sample):
                if mutant.reason not in STATED_REASONS or not same_request(
                        sample, mutant):
                    continue
                checked[sample.candidate.task] += 1
                terms = scored(sample, mutant.candidate)
                if faultless(terms, sample.candidate.task):
                    missed[mutant.error] += 1
        self.assertEqual(set(checked), {"condition_list",
                                        "condition_presence",
                                        metrics.EXPLANATION})
        self.assertGreater(sum(checked.values()), 50)
        self.assertEqual(dict(missed), {})

    def test_controls_are_faultless(self):
        """The suite's controls, correct responses the gate must release,
        score as correct."""
        checked = 0
        for sample in SAMPLES:
            if reference_of(sample) is None:
                continue
            for mutant in mutations.mutants(sample):
                if mutant.reason or not same_request(sample, mutant) or (
                        mutant.outcome != sample.candidate.outcome):
                    continue
                checked += 1
                with self.subTest(control=mutant.error):
                    self.assertTrue(faultless(scored(
                        sample, mutant.candidate), sample.candidate.task))
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
