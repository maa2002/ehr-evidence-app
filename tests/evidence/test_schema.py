"""Synthetic checks for ehr_evidence_app.evidence.schema.

Records are invented inline, and valid bundles come from the bundle
builder's synthetic mode; each invariant is shown by one changed copy of a
valid record being refused. No real record is read.
"""

import copy
import json
import math
import unittest

from pydantic import ValidationError

from ehr_evidence_app.evidence import bundles, contracts, schema

HISTORY = contracts.ScopeRef(kind="pre_target_history")


def concept(concept_id=1001, **changes):
    """Return an invented available concept (or a changed one)."""
    values = dict(concept_id=concept_id, name_status="available",
                  name="SYNTHETIC %d" % concept_id,
                  name_source="source_snapshot")
    values.update(changes)
    return schema.Concept(**values)


def attribution(**changes):
    """Return an invented attribution (or a changed one)."""
    values = dict(ref="A1", domain="procedure", position=0,
                  concept=concept(2001), score=0.5, score_class="positive",
                  attachment="earlier_only", narration_eligible=True,
                  link_status="verified", linked_event_refs=("E1",))
    values.update(changes)
    return schema.Attribution(**values)


def coverage(**changes):
    """Return an invented coverage record (or a changed one)."""
    values = dict(scope=HISTORY, status="complete", domains=("condition",),
                  source_snapshot="synthetic-1-v1", visits_in_scope=2,
                  events_in_scope=3, unmapped_events_in_scope=1,
                  excluded_final_admission_events=0,
                  events_outside_case_visits=0)
    values.update(changes)
    return schema.Coverage(**values)


class SyntheticBundles:
    """Valid synthetic bundles as mutable JSON documents."""

    qa, explanations, _ = bundles.run_synthetic()

    @classmethod
    def qa_document(cls, slug):
        """Return one QA bundle's JSON document by its id suffix."""
        for bundle in cls.qa:
            if bundle.bundle_id.endswith(slug):
                return json.loads(bundle.model_dump_json())
        raise KeyError(slug)

    @classmethod
    def explanation_document(cls):
        """Return the explanation bundle's JSON document."""
        return json.loads(cls.explanations[0].model_dump_json())


class ConceptTests(unittest.TestCase):
    """Names are never invented and always state their source."""

    def test_valid_states(self):
        """Available, unavailable and unmapped concepts validate."""
        self.assertEqual(concept().name_source, "source_snapshot")
        self.assertIsNone(schema.Concept(concept_id=7,
                                         name_status="unavailable").name)
        unmapped = schema.Concept(concept_id=0, name_status="unmapped",
                                  source_code="SRC-1")
        self.assertEqual(unmapped.source_code, "SRC-1")

    def test_invalid_states(self):
        """Each name-status rule refuses its violation."""
        for values in (dict(name_status="unmapped"),
                       dict(concept_id=0),
                       dict(name=None),
                       dict(name_source=None),
                       dict(name_status="unavailable"),
                       dict(name_source="guessed"),
                       dict(concept_id=-1),
                       dict(name="x" * 256)):
            with self.assertRaises(ValidationError):
                concept(**values)
        with self.assertRaises(ValidationError):
            schema.Concept(concept_id=7, name_status="unavailable",
                           name_source="legacy_concept_map")


class RecordTests(unittest.TestCase):
    """Visits, events, prediction and coverage records."""

    def test_visit_numbers_count_positions_from_one(self):
        """visit_number must equal position + 1."""
        values = dict(ref="V1", visit_occurrence_id=801, position=0,
                      visit_number=1, role="pre_target",
                      visit_concept_id=9201, start_date="2100-01-01")
        self.assertEqual(schema.Visit(**values).visit_number, 1)
        for changes in (dict(visit_number=2), dict(ref="V0"),
                        dict(start_date="01/01/2100")):
            with self.assertRaises(ValidationError):
                schema.Visit(**dict(values, **changes))

    def test_event_table_matches_domain(self):
        """The source table follows from the domain."""
        values = dict(ref="E1", domain="drug", source_table="drug_exposure",
                      native_id=5, visit_ref="V1", concept=concept(3001),
                      start_datetime="2100-01-01 10:00:00")
        self.assertEqual(schema.Event(**values).native_id, 5)
        for changes in (dict(source_table="condition_occurrence"),
                        dict(native_id=0), dict(ref="V1"),
                        dict(start_datetime="2100-01-01")):
            with self.assertRaises(ValidationError):
                schema.Event(**dict(values, **changes))

    def test_coverage_states(self):
        """Empty results are complete_empty; failures deliver nothing."""
        self.assertEqual(coverage().status, "complete")
        self.assertEqual(coverage(status="complete_empty", events_in_scope=0,
                                  unmapped_events_in_scope=0).status,
                         "complete_empty")
        self.assertEqual(coverage(status="unavailable", events_in_scope=0,
                                  unmapped_events_in_scope=0,
                                  reason_codes=("case_not_admitted",)
                                  ).status, "unavailable")
        for changes in (dict(events_in_scope=0, unmapped_events_in_scope=0),
                        dict(status="complete_empty"),
                        dict(status="unavailable", events_in_scope=0,
                             unmapped_events_in_scope=0),
                        dict(status="failed", reason_codes=("timeout",)),
                        dict(unmapped_events_in_scope=4),
                        dict(domains=("condition", "condition")),
                        dict(domains=()),
                        dict(statement="Complete.")):
            with self.assertRaises(ValidationError):
                coverage(**changes)

    def test_prediction_is_finite_and_bounded(self):
        """The logit is finite; the score lies in [0, 1]."""
        values = json.loads(
            SyntheticBundles.explanations[0].prediction.model_dump_json())
        schema.Prediction.model_validate_json(json.dumps(values))
        for changes in (dict(score=1.5), dict(score=-0.1),
                        dict(label_correspondence="not_established"),
                        dict(score_meaning="risk"),
                        dict(upstream_partition="holdout")):
            with self.assertRaises(ValidationError):
                schema.Prediction.model_validate_json(
                    json.dumps(dict(values, **changes)))
        prediction = SyntheticBundles.explanations[0].prediction
        with self.assertRaises(ValidationError):
            schema.Prediction(**dict(prediction.__dict__, logit=math.inf))


class AttributionTests(unittest.TestCase):
    """Attribution stays concept-level, unsigned and consistent."""

    def test_valid_attribution(self):
        """A positive, attached, linked driver validates."""
        self.assertTrue(attribution().narration_eligible)
        self.assertEqual(attribution(score=0.0, score_class="zero",
                                     narration_eligible=False).score, 0.0)

    def test_invalid_attributions(self):
        """Each attribution rule refuses its violation."""
        for changes in (
                dict(score_class="zero"),
                dict(score=0.0, score_class="zero"),
                dict(score=-0.5),
                dict(score=math.inf),
                dict(attachment="not_attached", link_status="no_edges",
                     linked_event_refs=()),
                dict(link_status="unlinked"),
                dict(linked_event_refs=("E1", "E1")),
                dict(attachment="not_attached", narration_eligible=False),
                dict(link_status="no_edges", linked_event_refs=()),
                dict(attachment="not_a_patient_node",
                     narration_eligible=False, link_status="verified"),
                dict(domain="condition", attachment="target_only"),
                dict(prediction_ref="P2")):
            with self.assertRaises(ValidationError):
                attribution(**changes)

    def test_unattached_states_agree(self):
        """Not-a-node and no-edge drivers carry matching link states."""
        self.assertEqual(attribution(
            attachment="not_attached", link_status="no_edges",
            narration_eligible=False, linked_event_refs=()).link_status,
            "no_edges")
        self.assertEqual(attribution(
            attachment="not_a_patient_node",
            link_status="not_a_patient_node", narration_eligible=False,
            linked_event_refs=()).link_status, "not_a_patient_node")


class QABundleTests(unittest.TestCase):
    """QA bundles: condition events of the scope, with matching coverage."""

    def refused(self, document, message):
        """Assert that a changed QA document is refused for message."""
        with self.assertRaises(ValidationError) as caught:
            schema.QABundle.model_validate_json(json.dumps(document))
        self.assertIn(message, str(caught.exception))

    def test_synthetic_bundles_round_trip(self):
        """Every synthetic QA bundle revalidates to an equal record."""
        for bundle in SyntheticBundles.qa:
            self.assertEqual(schema.QABundle.model_validate_json(
                bundle.model_dump_json()), bundle)

    def test_event_outside_bundle_visits(self):
        """Events must lie in a bundle visit."""
        document = SyntheticBundles.qa_document("all")
        document["events"][0]["visit_ref"] = "V9"
        self.refused(document, "outside the bundle visits")

    def test_condition_events_only(self):
        """QA bundles hold condition events only."""
        document = SyntheticBundles.qa_document("all")
        document["events"][0].update(domain="drug",
                                     source_table="drug_exposure")
        self.refused(document, "condition events only")

    def test_coverage_counts_agree(self):
        """Coverage counts must equal the delivered events and visits."""
        document = SyntheticBundles.qa_document("all")
        document["coverage"]["events_in_scope"] += 1
        self.refused(document, "coverage counts disagree")
        document = SyntheticBundles.qa_document("all")
        document["coverage"]["scope"] = {"kind": "pre_target_history",
                                         "visit_number": None}
        self.refused(document, "coverage does not describe this scope")

    def test_history_excludes_the_final_admission(self):
        """A history bundle holds pre-target visits only."""
        document = SyntheticBundles.qa_document("history")
        document["visits"][-1]["role"] = "final_admission"
        self.refused(document, "includes the final admission")

    def test_selected_visit_holds_that_visit(self):
        """A selected-visit bundle holds exactly the named visit."""
        document = SyntheticBundles.qa_document("visit-1")
        scope = {"kind": "selected_visit", "visit_number": 2}
        document["scope"] = document["coverage"]["scope"] = scope
        self.refused(document, "holds another visit")

    def test_repeated_records_are_refused(self):
        """One native record cannot appear twice."""
        document = SyntheticBundles.qa_document("all")
        twin = copy.deepcopy(document["events"][0])
        twin["ref"] = "E9"
        document["events"].append(twin)
        document["coverage"]["events_in_scope"] += 1
        self.refused(document, "appears twice")


class ExplanationBundleTests(unittest.TestCase):
    """Explanation bundles keep history, context and attribution apart."""

    def refused(self, document, message):
        """Assert that a changed explanation document is refused."""
        with self.assertRaises(ValidationError) as caught:
            schema.ExplanationBundle.model_validate_json(json.dumps(document))
        self.assertIn(message, str(caught.exception))

    def test_synthetic_bundle_round_trips(self):
        """The synthetic explanation bundle revalidates to an equal record."""
        bundle = SyntheticBundles.explanations[0]
        self.assertEqual(schema.ExplanationBundle.model_validate_json(
            bundle.model_dump_json()), bundle)

    def test_final_admission_diagnoses_never_enter(self):
        """A diagnosis in final-admission context is refused."""
        document = SyntheticBundles.explanation_document()
        document["final_admission_context"][0].update(
            domain="condition", source_table="condition_occurrence")
        self.refused(document, "final-admission diagnoses never enter")

    def test_history_is_pre_target(self):
        """History events lie in pre-target visits; context in the final."""
        document = SyntheticBundles.explanation_document()
        document["history_events"][0]["visit_ref"] = "V3"
        self.refused(document, "not pre-target")
        document = SyntheticBundles.explanation_document()
        document["final_admission_context"][0]["visit_ref"] = "V1"
        self.refused(document, "not in the final admission")

    def test_every_event_supports_an_attribution(self):
        """Uncited events and links to other concepts are refused."""
        document = SyntheticBundles.explanation_document()
        document["attributions"][-1]["linked_event_refs"] = ["E3"]
        self.refused(document, "every bundled event supports")
        document = SyntheticBundles.explanation_document()
        document["attributions"][0]["linked_event_refs"] = ["E1", "E2"]
        document["attributions"][1]["linked_event_refs"] = []
        self.refused(document, "another concept")

    def test_eligibility_and_restrictions_follow_content(self):
        """Eligibility needs a narratable driver; restrictions are implied."""
        document = SyntheticBundles.explanation_document()
        document["explanation_eligible"] = False
        self.refused(document, "narratable attribution")
        document = SyntheticBundles.explanation_document()
        document["restrictions"] = ["target_attached_drivers_present"]
        self.refused(document, "restrictions do not follow")
        document = SyntheticBundles.explanation_document()
        document["excluded_driver_entries"] = 0
        self.refused(document, "restrictions do not follow")
        document = SyntheticBundles.explanation_document()
        document["prediction"]["batch_sensitive"] = True
        self.refused(document, "restrictions do not follow")

    def test_one_final_admission_and_history_scope(self):
        """One final admission; the scope is the pre-target history."""
        document = SyntheticBundles.explanation_document()
        document["visits"][0]["role"] = "final_admission"
        self.refused(document, "exactly one final admission")
        document = SyntheticBundles.explanation_document()
        document["scope"] = {"kind": "case_visits", "visit_number": None}
        self.refused(document, "pre-target history scope")

    def test_coverage_describes_the_history(self):
        """Coverage counts equal the history events and pre-target visits."""
        document = SyntheticBundles.explanation_document()
        document["coverage"]["visits_in_scope"] = 3
        self.refused(document, "coverage does not describe the history")

    def test_expected_restrictions_order(self):
        """Restrictions come in the closure record's fixed order."""
        bundle = SyntheticBundles.explanations[0]
        self.assertEqual(schema.expected_restrictions(
            bundle.prediction, bundle.attributions, 1),
            ("target_attached_drivers_present",
             "target_outcome_entries_excluded"))
        batch = bundle.prediction.model_copy(update={"batch_sensitive": True})
        self.assertEqual(schema.expected_restrictions(batch, (), 0),
                         ("batch_sensitive_prediction",))


class CitationTests(unittest.TestCase):
    """A response is bound to one bundle; its references must resolve."""

    def setUp(self):
        """Pick the synthetic history bundle and a matching response."""
        self.bundle = SyntheticBundles.qa[0]
        self.digest = "9" * 64
        self.response = contracts.Response(
            schema_version="1.0", request_id="q-1",
            task="condition_presence", case=self.bundle.case,
            scope=self.bundle.scope, outcome="answer",
            text="Synthetic answer.",
            claims=(contracts.Claim(text="Recorded.", refs=("E1", "V1")),),
            bundle=contracts.BundleRef(bundle_id=self.bundle.bundle_id,
                                       sha256=self.digest))

    def test_bundle_refs(self):
        """QA bundles expose C, V and E; explanations add P and A."""
        self.assertEqual(schema.bundle_refs(self.bundle),
                         {"C1", "V1", "V2", "E1"})
        refs = schema.bundle_refs(SyntheticBundles.explanations[0])
        self.assertTrue({"P1", "A1", "A2", "A3", "E4", "V3"} <= refs)

    def test_matching_response_has_no_problem(self):
        """A response bound to this bundle and citing its refs passes."""
        self.assertEqual(schema.citation_problems(
            self.response, self.bundle, self.digest), [])

    def test_other_bundle_case_or_scope_is_refused(self):
        """Another digest, bundle, case or scope is a scope violation."""
        other_case = contracts.CaseRef(case_set="synthetic-1-v1",
                                       case_number=2)
        visit = contracts.ScopeRef(kind="selected_visit", visit_number=1)
        for response, digest in (
                (self.response, "8" * 64),
                (self.response.model_copy(update={"case": other_case}),
                 self.digest),
                (self.response.model_copy(update={"scope": visit}),
                 self.digest),
                (self.response.model_copy(update={"bundle": None}),
                 self.digest)):
            self.assertEqual(schema.citation_problems(
                response, self.bundle, digest), ["scope_violated"])

    def test_unresolved_reference_is_refused(self):
        """A reference the bundle lacks is reported."""
        response = self.response.model_copy(update={"claims": (
            contracts.Claim(text="Recorded.", refs=("E7",)),)})
        self.assertEqual(schema.citation_problems(
            response, self.bundle, self.digest), ["reference_unresolved"])


if __name__ == "__main__":
    unittest.main()
