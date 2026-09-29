"""Shared evidence records and bundles (work item 24).

Six record types keep the semantic classes apart: visits, events,
prediction, attribution, coverage and provenance. A bundle holds them for
one case and one scope and references them by bundle-local identifiers
(V1, E1, P1, A1, C1), so responses can cite evidence without exposing
native record numbers. The native identifiers stay in the records.

Invariants enforced here:

* repeated occurrences stay separate events (one event per native record);
* names are never invented: a concept is available, unavailable or
  unmapped (concept 0, with its recorded source code);
* attribution is concept-level: an attribution lists the events of its
  concept that the linkage record ties to its graph edges, and no score is
  ever attached to an event;
* coverage counts agree with the delivered events, and an empty result is
  reported as complete and empty only when coverage is complete;
* an explanation bundle never contains final-admission diagnoses, keeps the
  final admission's drug and procedure context separate from history, and
  is eligible only with at least one narratable attribution;
* an attribution's attachment (from the closure record) and link status
  (from the linkage record) agree, and a bundle's restrictions are exactly
  those its content implies.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, Optional

from pydantic import Field, StringConstraints, model_validator

from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.evidence.contracts import CaseRef, Frozen, \
    Identifier, ScopeRef, Sha256

SCHEMA_VERSION = "1.1"
SCHEMA_VERSIONS = ("1.0", "1.1")
SchemaVersion = Literal[SCHEMA_VERSIONS]
VisitRef = Annotated[str, StringConstraints(pattern=r"^V[1-9][0-9]{0,5}$")]
EventRef = Annotated[str, StringConstraints(pattern=r"^E[1-9][0-9]{0,5}$")]
AttributionRef = Annotated[str, StringConstraints(
    pattern=r"^A[1-9][0-9]{0,5}$")]
DateText = Annotated[str, StringConstraints(
    pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")]
DateTimeText = Annotated[str, StringConstraints(
    pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}(:[0-9]{2})?$")]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=255)]
COVERAGE_STATEMENT = (
    "Complete within the OMOP person, visit_occurrence, "
    "condition_occurrence, procedure_occurrence and drug_exposure tables "
    "for the case's person at the recorded source snapshot; not a "
    "statement of clinical absence.")
PREDICTION_DEFINITION = (
    "argmax of the inherited bridge's single-graph diagnosis logits (one "
    "graph per forward pass)")


class DigestRef(Frozen):
    """A versioned input identified by name and SHA-256."""

    name: Identifier
    sha256: Sha256


class Concept(Frozen):
    """An OMOP concept as recorded, with an explicit name status."""

    concept_id: int = Field(ge=0)
    name_status: vocab.NameState
    name: Optional[ShortText] = None
    name_source: Optional[vocab.NameSource] = None
    vocabulary_id: Optional[ShortText] = None
    domain_id: Optional[ShortText] = None
    source_code: Optional[ShortText] = None

    @model_validator(mode="after")
    def _name_status(self):
        """Available names exist; unmapped means concept 0; none invented."""
        if (self.name_status == "unmapped") != (self.concept_id == 0):
            raise ValueError("unmapped status is exactly concept 0")
        if (self.name_status == "available") != (self.name is not None):
            raise ValueError("only an available concept carries a name")
        if (self.name is None) != (self.name_source is None):
            raise ValueError("a name always states its source")
        return self


class SourceConcept(Frozen):
    """The source vocabulary concept a record was coded with, such as an
    ICD-10-CM code, as the snapshot's concept table names it."""

    concept_id: int = Field(ge=1)
    vocabulary_id: ShortText
    code: ShortText
    name_status: Literal["available", "unavailable"]
    name: Optional[ShortText] = None

    @model_validator(mode="after")
    def _name(self):
        """Only an available source concept carries a name."""
        if (self.name_status == "available") != (self.name is not None):
            raise ValueError("only an available source concept carries a "
                             "name")
        return self


class Visit(Frozen):
    """One case visit with its graph position and role."""

    ref: VisitRef
    visit_occurrence_id: int = Field(ge=1)
    position: int = Field(ge=0)
    visit_number: int = Field(ge=1)
    role: vocab.VisitRole
    visit_concept_id: int = Field(ge=0)
    start_date: Optional[DateText] = None
    end_date: Optional[DateText] = None

    @model_validator(mode="after")
    def _numbering(self):
        """Visit numbers count graph positions from one."""
        if self.visit_number != self.position + 1:
            raise ValueError("visit_number must be position + 1")
        return self


class Event(Frozen):
    """One recorded clinical event, kept separate even when repeated."""

    ref: EventRef
    domain: vocab.Domain
    source_table: vocab.SourceTable
    native_id: int = Field(ge=1)
    visit_ref: VisitRef
    concept: Concept
    source_concept: Optional[SourceConcept] = None
    start_date: Optional[DateText] = None
    start_datetime: Optional[DateTimeText] = None
    end_date: Optional[DateText] = None
    end_datetime: Optional[DateTimeText] = None

    @model_validator(mode="after")
    def _table(self):
        """The source table matches the domain."""
        if vocab.SOURCE_TABLES[self.domain] != self.source_table:
            raise ValueError("source_table does not match the domain")
        return self


class Coverage(Frozen):
    """What the retrieval covered for the requested scope."""

    ref: Literal["C1"] = "C1"
    scope: ScopeRef
    status: vocab.CoverageState
    domains: tuple[vocab.Domain, ...] = Field(min_length=1, max_length=3)
    source_snapshot: Identifier
    statement: Literal[COVERAGE_STATEMENT] = COVERAGE_STATEMENT
    visits_in_scope: int = Field(ge=0)
    events_in_scope: int = Field(ge=0)
    unmapped_events_in_scope: int = Field(ge=0)
    excluded_final_admission_events: int = Field(ge=0)
    events_outside_case_visits: int = Field(ge=0)
    reason_codes: tuple[vocab.ReasonCode, ...] = ()

    @model_validator(mode="after")
    def _status(self):
        """Empty results are complete-and-empty; failures deliver nothing."""
        if len(set(self.domains)) != len(self.domains):
            raise ValueError("domains repeat")
        if self.unmapped_events_in_scope > self.events_in_scope:
            raise ValueError("more unmapped events than events")
        if self.status == "complete" and self.events_in_scope == 0:
            raise ValueError("an empty complete result is complete_empty")
        if self.status == "complete_empty" and self.events_in_scope != 0:
            raise ValueError("complete_empty delivers no events")
        if self.status in ("unavailable", "failed") and (
                self.events_in_scope or not self.reason_codes):
            raise ValueError("unavailable and failed coverage deliver no "
                             "events and state a reason")
        return self


class Prediction(Frozen):
    """The selected inherited prediction, copied unchanged."""

    ref: Literal["P1"] = "P1"
    label_index: int = Field(ge=0)
    concept: Concept
    logit: float
    score: float = Field(ge=0.0, le=1.0)
    score_meaning: Literal["uncalibrated sigmoid of the logit; not a "
                           "clinical risk"] = (
        "uncalibrated sigmoid of the logit; not a clinical risk")
    output_record_sha256: Sha256
    definition: Literal[PREDICTION_DEFINITION] = PREDICTION_DEFINITION
    label_correspondence: Literal["established"]
    single_graph_reproduces_published: bool
    batch_sensitive: bool
    upstream_partition: vocab.Partition

    @model_validator(mode="after")
    def _finite(self):
        """The logit is finite."""
        if not math.isfinite(self.logit):
            raise ValueError("the logit is not finite")
        return self


class Attribution(Frozen):
    """One published driver: concept-level, unsigned, never split."""

    ref: AttributionRef
    prediction_ref: Literal["P1"] = "P1"
    domain: vocab.Domain
    position: int = Field(ge=0)
    concept: Concept
    score: float = Field(ge=0.0)
    score_class: vocab.ScoreClass
    attachment: vocab.Attachment
    narration_eligible: bool
    link_status: vocab.LinkState
    linked_event_refs: tuple[EventRef, ...] = ()

    @model_validator(mode="after")
    def _semantics(self):
        """Score class, narration and linkage agree with the score."""
        if not math.isfinite(self.score):
            raise ValueError("the score is not finite")
        if (self.score_class == "positive") != (self.score > 0.0):
            raise ValueError("score_class does not match the score")
        if self.narration_eligible and (
                self.score_class != "positive"
                or self.attachment in ("not_attached",
                                       "not_a_patient_node")):
            raise ValueError("only positive, attached drivers are "
                             "narratable")
        if len(set(self.linked_event_refs)) != len(self.linked_event_refs):
            raise ValueError("linked events repeat")
        if self.link_status in ("unlinked", "no_edges",
                                "not_a_patient_node") and (
                self.linked_event_refs):
            raise ValueError("an unlinked attribution lists no events")
        if ((self.attachment == "not_a_patient_node")
                != (self.link_status == "not_a_patient_node")
                or (self.attachment == "not_attached")
                != (self.link_status == "no_edges")):
            raise ValueError("attachment and link status disagree")
        if self.domain == "condition" and self.attachment == "target_only":
            raise ValueError("a diagnosis attached only to the final "
                             "admission is a target-outcome entry")
        return self


class Provenance(Frozen):
    """Versions of every input a bundle was built from."""

    source_snapshot: DigestRef
    identity_mapping: DigestRef
    closure_record: DigestRef
    concept_table: DigestRef
    legacy_concept_map: Optional[DigestRef] = None
    model_outputs: Optional[DigestRef] = None
    linkage_record: Optional[DigestRef] = None
    evidence_policy: Optional[DigestRef] = None
    schema_version: SchemaVersion = SCHEMA_VERSION
    builder: ShortText


def _unique(refs, label):
    """Raise when bundle-local references repeat."""
    if len(set(refs)) != len(refs):
        raise ValueError("%s references repeat" % label)


def _check_version(bundle, events):
    """Source concepts need schema 1.1; provenance shares the version."""
    if bundle.provenance.schema_version != bundle.schema_version:
        raise ValueError("provenance and bundle schema versions differ")
    if bundle.schema_version == "1.0" and any(
            event.source_concept is not None for event in events):
        raise ValueError("source concepts need schema version 1.1")


def _unique_events(events):
    """Raise when one native record appears twice in a domain."""
    keys = [(event.domain, event.native_id) for event in events]
    if len(set(keys)) != len(keys):
        raise ValueError("an event record appears twice")


class QABundle(Frozen):
    """Evidence for one QA request scope: condition events and coverage."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    kind: Literal["qa"] = "qa"
    bundle_id: Identifier
    case: CaseRef
    scope: ScopeRef
    visits: tuple[Visit, ...] = Field(min_length=1)
    events: tuple[Event, ...] = ()
    coverage: Coverage
    provenance: Provenance

    @model_validator(mode="after")
    def _references(self):
        """Every event lies in a bundle visit; coverage matches the events."""
        _check_version(self, self.events)
        _unique([visit.ref for visit in self.visits], "visit")
        _unique([event.ref for event in self.events], "event")
        _unique_events(self.events)
        visit_refs = {visit.ref for visit in self.visits}
        if any(event.visit_ref not in visit_refs for event in self.events):
            raise ValueError("an event lies outside the bundle visits")
        if any(event.domain != "condition" for event in self.events):
            raise ValueError("QA bundles hold condition events only")
        coverage = self.coverage
        if coverage.scope != self.scope or coverage.domains != (
                "condition",):
            raise ValueError("coverage does not describe this scope")
        if (coverage.events_in_scope != len(self.events)
                or coverage.visits_in_scope != len(self.visits)
                or coverage.unmapped_events_in_scope != sum(
                    event.concept.name_status == "unmapped"
                    for event in self.events)):
            raise ValueError("coverage counts disagree with the events")
        if self.scope.kind == "pre_target_history" and any(
                visit.role != "pre_target" for visit in self.visits):
            raise ValueError("history scope includes the final admission")
        if self.scope.kind == "selected_visit" and (
                len(self.visits) != 1
                or self.visits[0].visit_number != self.scope.visit_number):
            raise ValueError("selected-visit scope holds another visit")
        return self


class ExplanationBundle(Frozen):
    """Evidence for explaining one selected prediction."""

    schema_version: SchemaVersion = SCHEMA_VERSION
    kind: Literal["explanation"] = "explanation"
    bundle_id: Identifier
    case: CaseRef
    scope: ScopeRef
    prediction: Prediction
    attributions: tuple[Attribution, ...]
    visits: tuple[Visit, ...] = Field(min_length=1)
    history_events: tuple[Event, ...] = ()
    final_admission_context: tuple[Event, ...] = ()
    coverage: Coverage
    excluded_driver_entries: int = Field(ge=0)
    restrictions: tuple[vocab.Restriction, ...] = ()
    explanation_eligible: bool
    provenance: Provenance

    @model_validator(mode="after")
    def _references(self):
        """History, final-admission context and attributions stay apart."""
        if self.scope.kind != "pre_target_history":
            raise ValueError("explanations use the pre-target history scope")
        events = self.history_events + self.final_admission_context
        _check_version(self, events)
        _unique([visit.ref for visit in self.visits], "visit")
        _unique([event.ref for event in events], "event")
        _unique([item.ref for item in self.attributions], "attribution")
        _unique_events(events)
        roles = {visit.ref: visit.role for visit in self.visits}
        if sum(role == "final_admission" for role in roles.values()) != 1:
            raise ValueError("the bundle needs exactly one final admission")
        for event in self.history_events:
            if roles.get(event.visit_ref) != "pre_target":
                raise ValueError("a history event is not pre-target")
        for event in self.final_admission_context:
            if roles.get(event.visit_ref) != "final_admission":
                raise ValueError("a context event is not in the final "
                                 "admission")
            if event.domain == "condition":
                raise ValueError("final-admission diagnoses never enter an "
                                 "explanation")
        by_ref = {event.ref: event for event in events}
        cited = set()
        for item in self.attributions:
            for ref in item.linked_event_refs:
                event = by_ref.get(ref)
                if (event is None or event.domain != item.domain
                        or event.concept.concept_id
                        != item.concept.concept_id):
                    raise ValueError("an attribution links an event of "
                                     "another concept")
                cited.add(ref)
        if cited != set(by_ref):
            raise ValueError("every bundled event supports an attribution")
        if self.explanation_eligible != any(
                item.narration_eligible for item in self.attributions):
            raise ValueError("eligibility needs a narratable attribution")
        coverage = self.coverage
        if (coverage.scope != self.scope
                or coverage.events_in_scope != len(self.history_events)
                or coverage.visits_in_scope != sum(
                    role == "pre_target" for role in roles.values())):
            raise ValueError("coverage does not describe the history")
        if self.restrictions != expected_restrictions(
                self.prediction, self.attributions,
                self.excluded_driver_entries):
            raise ValueError("restrictions do not follow from the bundle")
        return self


def expected_restrictions(prediction, attributions, excluded_driver_entries):
    """Return the restrictions a bundle's content implies, in fixed order.

    The rule is the Phase 3 closure record's: a batch-sensitive prediction,
    a narratable driver attached to the final admission, and excluded
    target-outcome entries each carry their restriction.
    """
    restrictions = []
    if prediction.batch_sensitive:
        restrictions.append("batch_sensitive_prediction")
    if any(item.narration_eligible
           and item.attachment in ("target_only", "target_and_earlier")
           for item in attributions):
        restrictions.append("target_attached_drivers_present")
    if excluded_driver_entries:
        restrictions.append("target_outcome_entries_excluded")
    return tuple(restrictions)


def bundle_refs(bundle):
    """Return every bundle-local reference a response may cite."""
    if getattr(bundle, "kind", None) == "record":
        from ehr_evidence_app.evidence import record
        return record.refs_of(bundle)
    refs = {bundle.coverage.ref}
    refs.update(visit.ref for visit in bundle.visits)
    if isinstance(bundle, QABundle):
        refs.update(event.ref for event in bundle.events)
    else:
        refs.add(bundle.prediction.ref)
        refs.update(item.ref for item in bundle.attributions)
        refs.update(event.ref for event in bundle.history_events
                    + bundle.final_admission_context)
    return refs


def citation_problems(response, bundle, bundle_sha256):
    """Return reason codes for citations that do not belong to the bundle.

    References are bundle-local (every bundle has an E1), so a response is
    bound to one bundle by identifier and digest, and that bundle to one
    case and scope. A response naming another bundle, case or scope, or
    citing a reference the bundle lacks, is refused. Resolution alone does
    not show that a record supports its claim; that check is later work.
    """
    problems = []
    if (response.bundle is None
            or response.bundle.bundle_id != bundle.bundle_id
            or response.bundle.sha256 != bundle_sha256
            or response.case != bundle.case
            or response.scope != bundle.scope):
        problems.append("scope_violated")
    known = bundle_refs(bundle)
    if any(ref not in known for claim in response.claims
           for ref in claim.refs):
        problems.append("reference_unresolved")
    return problems


def bundle_schemas():
    """Return the JSON Schema of each bundle type, by file name."""
    return {"qa-bundle.schema.json": QABundle.model_json_schema(),
            "explanation-bundle.schema.json":
                ExplanationBundle.model_json_schema()}
