"""The application's operations, for the API (Phase 8, work item 47).

A Service holds what the application answers from: for each case set it
serves, the pinned QA bundle set, the explanation bundles and the
admitted cases; the condition vocabulary; the execution limits; the
recorder of the protected runtime records; and the model's writer once
the model has loaded. A question goes through qa.answer.answer and an
explanation through validation.explanation, as in 0.9.0, and each writes
its protected runtime record. What a request returns is the gate's
response, exactly, with who wrote it and the seconds it took; nothing the
gate withheld is returned.

Only the development case sets are served (decision D3), so that the
evaluation set's cases are not seen before the final evaluation. The
evidence is arranged for display without the native database identifiers
of visits and records; references and visit numbers trace every claim
(decision D6).

The model loads in the background. Until it has loaded, or when it
cannot load, the templates write every response as a recorded fallback
("no_model"), and the model writes one request at a time: a second one
meanwhile gets the template's response at once ("busy") (decision D1).
"""

from __future__ import annotations

import dataclasses
import hashlib
import threading
import time
import uuid

from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.generation import adapter, records
from ehr_evidence_app.qa import answer
from ehr_evidence_app.validation import explanation

SERVED = ("dev-30-v1", "pilot-10-v1")
MODEL_STATES = ("off", "loading", "ready", "unavailable")
# The evidence a case can be inspected in: a QA scope, or the prediction.
EVIDENCE_KINDS = ("pre_target_history", "case_visits", "selected_visit",
                  "prediction")


class ServiceError(Exception):
    """A request the service cannot serve: a short code, and the HTTP
    status it is answered with."""

    def __init__(self, code, status=404):
        super().__init__(code)
        self.code = code
        self.status = status


@dataclasses.dataclass(frozen=True)
class Source:
    """What one case set is answered from: its QA bundle set, its
    explanation bundles with their SHA-256 by case number, and its
    admitted cases by case number."""

    label: str
    bundle_set: answer.BundleSet
    explanations: dict
    cases: dict


def workspace_source(label):
    """Return the source of a served case set, every input verified
    against its pin."""
    from ehr_evidence_app.evidence import readers
    from ehr_evidence_app.gnn import mapping
    if label not in SERVED:
        raise ServiceError("case_set_not_served")
    bundle_set = answer.load_bundle_set(answer.bundle_set_plan(label))
    found = {number: (bundle, digest) for number, bundle, digest
             in explanation.read_bundles(label)}
    plan = readers.real_plan(None if label == mapping.PILOT_LABEL
                             else label)
    admitted = {case.case_number: case
                for case in readers.read_cases(plan).cases if case.admitted}
    return Source(label, bundle_set, found, admitted)


def invented_source():
    """Return the source of the invented case set, held in memory."""
    from ehr_evidence_app.evidence import bundles
    from ehr_evidence_app.qa import suite
    _, case_set, _, _, _ = bundles.synthetic_inputs()
    _, found, _ = bundles.run_synthetic()
    return Source(case_set.label, suite.synthetic_bundle_set(),
                  {bundle.case.case_number: (bundle, hashlib.sha256(
                      bundles.bundle_bytes(bundle)).hexdigest())
                   for bundle in found},
                  {case.case_number: case for case in case_set.cases
                   if case.admitted})


# --------------------------------------------------------------------------
# The evidence as displayed (decision D6)
# --------------------------------------------------------------------------

def visit_view(visit):
    """Return a visit as displayed: its reference, number, role and
    dates."""
    return {"ref": visit.ref, "visit_number": visit.visit_number,
            "role": visit.role, "start_date": visit.start_date,
            "end_date": visit.end_date}


def record_view(event, numbers):
    """Return a record as displayed: its reference, visit number, domain,
    concept, source code and dates; numbers gives each visit reference's
    number."""
    concept, source = event.concept, event.source_concept
    return {"ref": event.ref, "visit_number": numbers.get(event.visit_ref),
            "domain": event.domain, "concept_id": concept.concept_id,
            "concept_name": concept.name, "name_status": concept.name_status,
            "source_code": source.code if source else concept.source_code,
            "source_vocabulary": source.vocabulary_id if source else None,
            "start_date": event.start_date, "end_date": event.end_date}


def coverage_view(coverage, events):
    """Return the coverage of a scope as displayed, with the references of
    its records without a standard concept."""
    return {"ref": coverage.ref, "status": coverage.status,
            "statement": coverage.statement,
            "visits_in_scope": coverage.visits_in_scope,
            "records_in_scope": coverage.events_in_scope,
            "records_without_a_standard_concept": [
                event.ref for event in events
                if event.concept.name_status == "unmapped"],
            "final_admission_records_left_out":
                coverage.excluded_final_admission_events,
            "records_outside_the_case_visits":
                coverage.events_outside_case_visits,
            "reason_codes": list(coverage.reason_codes)}


def provenance_view(provenance):
    """Return the provenance of a bundle: every input's name and digest,
    the schema version and the builder."""
    inputs = {}
    for field in ("source_snapshot", "identity_mapping", "closure_record",
                  "concept_table", "legacy_concept_map", "model_outputs",
                  "linkage_record", "evidence_policy"):
        item = getattr(provenance, field)
        if item is not None:
            inputs[field] = {"name": item.name, "sha256": item.sha256}
    return {"inputs": inputs, "schema_version": provenance.schema_version,
            "builder": provenance.builder}


def prediction_view(prediction):
    """Return the prepared prediction: its label, score and logit, with
    the score's fixed meaning; the score is never a percentage."""
    return {"ref": prediction.ref, "label": prediction.concept.name,
            "concept_id": prediction.concept.concept_id,
            "label_index": prediction.label_index,
            "score": prediction.score, "logit": prediction.logit,
            "score_meaning": prediction.score_meaning,
            "definition": prediction.definition,
            "upstream_partition": prediction.upstream_partition}


def attribution_view(item):
    """Return one original attribution as displayed: its score, unsigned,
    with its attachment and link status."""
    return {"ref": item.ref, "domain": item.domain,
            "concept_id": item.concept.concept_id,
            "concept_name": item.concept.name,
            "name_status": item.concept.name_status,
            "score": item.score, "score_class": item.score_class,
            "attachment": item.attachment,
            "narration_eligible": item.narration_eligible,
            "link_status": item.link_status,
            "linked_record_refs": list(item.linked_event_refs)}


def scope_view(scope):
    """Return a scope as a request names it."""
    found = {"kind": scope.kind}
    if scope.visit_number is not None:
        found["visit_number"] = scope.visit_number
    return found


def qa_evidence_view(bundle):
    """Return the evidence of a QA bundle as displayed."""
    numbers = {visit.ref: visit.visit_number for visit in bundle.visits}
    return {"kind": "scope", "scope": scope_view(bundle.scope),
            "bundle": {"bundle_id": bundle.bundle_id},
            "visits": [visit_view(visit) for visit in bundle.visits],
            "records": [record_view(event, numbers)
                        for event in bundle.events],
            "coverage": coverage_view(bundle.coverage, bundle.events),
            "provenance": provenance_view(bundle.provenance)}


def prediction_evidence_view(bundle):
    """Return the evidence of an explanation bundle as displayed: the
    prediction, every original attribution, the linked history records,
    the final admission's records as context, the coverage, the case's
    restrictions and the provenance."""
    numbers = {visit.ref: visit.visit_number for visit in bundle.visits}
    events = bundle.history_events + bundle.final_admission_context
    return {"kind": "prediction", "scope": scope_view(bundle.scope),
            "bundle": {"bundle_id": bundle.bundle_id},
            "prediction": prediction_view(bundle.prediction),
            "attributions": [attribution_view(item)
                             for item in bundle.attributions],
            "visits": [visit_view(visit) for visit in bundle.visits],
            "history_records": [record_view(event, numbers)
                                for event in bundle.history_events],
            "final_admission_context": [
                record_view(event, numbers)
                for event in bundle.final_admission_context],
            "coverage": coverage_view(bundle.coverage, events),
            "attributions_left_out": bundle.excluded_driver_entries,
            "restrictions": list(bundle.restrictions),
            "explanation_eligible": bundle.explanation_eligible,
            "provenance": provenance_view(bundle.provenance)}


def written_view(response, written, seconds):
    """Return what a request returns: the gate's response, who wrote it
    (the model, or the template with the fallback and its category) and
    the seconds it took."""
    return {"response": response.model_dump(mode="json"),
            "author": written.author if written else "template",
            "fallback": written.fallback if written else None,
            "category": written.category if written else None,
            "gate_reasons": list(written.gate_reasons) if written else [],
            "seconds": round(seconds, 3)}


# --------------------------------------------------------------------------
# The service
# --------------------------------------------------------------------------

class Service:
    """The application's operations over the case sets it serves."""

    def __init__(self, sources, vocabulary, limits, recorder=None,
                 clock=time.monotonic):
        """Hold the sources, the vocabulary, the limits and the recorder;
        no model writes until one is given (use_backend)."""
        self.sources = {source.label: source for source in sources}
        self.vocabulary = vocabulary
        self.limits = limits
        self.recorder = recorder
        self.clock = clock
        self.backend = None
        self.writer = None
        self.model = "off"
        self.versions = records.versions()

    @classmethod
    def from_workspace(cls, labels=SERVED):
        """Return the service of the served development case sets, with
        the recorder of the protected runtime records."""
        from ehr_evidence_app.qa import resolve
        return cls([workspace_source(label) for label in labels],
                   resolve.load(), contracts.load_limits(),
                   records.Recorder())

    @classmethod
    def invented(cls, recorder=None):
        """Return the service of the invented case set, held in memory."""
        from ehr_evidence_app.evidence import bundles
        from ehr_evidence_app.qa import suite
        return cls([invented_source()], suite.synthetic_vocabulary(),
                   contracts.ExecutionLimits(**bundles.SYNTHETIC_LIMITS),
                   recorder)

    # -- the model -----------------------------------------------------------

    def use_backend(self, backend, limits=None):
        """Load a backend in the background; the model writes once it has
        loaded. Return the loading thread."""
        self.backend = backend
        self.model = "loading"
        thread = threading.Thread(target=self.load, args=(backend, limits),
                                  name="model-loader", daemon=True)
        thread.start()
        return thread

    def load(self, backend, limits=None):
        """Start a backend, when it has to be started, and let it write."""
        try:
            start = getattr(backend, "start", None)
            if start is not None:
                start()
        except Exception:  # noqa: BLE001 - any failure leaves the templates
            self.model = "unavailable"
            return
        writer = adapter.Writer(backend, limits or self.limits)
        if self.recorder is not None:
            self.recorder.writer = writer
        self.writer = writer
        self.model = "ready"

    def stop(self):
        """Stop the model; the templates write from then on."""
        backend, self.backend, self.writer = self.backend, None, None
        self.model = "off"
        if self.recorder is not None:
            self.recorder.writer = None
        stop = getattr(backend, "stop", None)
        if stop is not None:
            stop()

    # -- lookups -------------------------------------------------------------

    def source(self, case_set):
        """Return the source of a served case set."""
        found = self.sources.get(case_set)
        if found is None:
            raise ServiceError("case_set_not_served")
        return found

    def admitted(self, case_set, case_number):
        """Return the source of an admitted case of a served case set."""
        source = self.source(case_set)
        if case_number not in source.cases or not (
                source.bundle_set.visit_count(case_number)):
            raise ServiceError("case_unknown")
        return source

    def scopes(self, source, case_number):
        """Return the scopes of a case that have a QA bundle, history
        first, then the visits in order, then all of them."""
        order = {"pre_target_history": 0, "selected_visit": 1,
                 "case_visits": 2}
        return sorted((scope for number, scope in source.bundle_set.entries
                       if number == case_number),
                      key=lambda scope: (order[scope.kind],
                                         scope.visit_number or 0))

    # -- operations ----------------------------------------------------------

    def health(self):
        """Return whether the evidence is loaded and what the model is
        doing, and the versions; no patient data."""
        return {"status": "ok", "model": self.model,
                "case_sets": sorted(self.sources),
                "versions": dict(self.versions)}

    def case_sets(self):
        """Return the served case sets and their numbers of cases."""
        return [{"case_set": label, "cases": len(source.cases)}
                for label, source in sorted(self.sources.items())]

    def cases(self, case_set):
        """Return a case set's admitted cases: their numbers, their
        numbers of visits and whether they have a prepared prediction."""
        source = self.source(case_set)
        return [{"case_number": number,
                 "visits": source.bundle_set.visit_count(number),
                 "prediction": number in source.explanations}
                for number in sorted(source.cases)
                if source.bundle_set.visit_count(number)]

    def case(self, case_set, case_number):
        """Return a case: its scopes and its prepared prediction."""
        source = self.admitted(case_set, case_number)
        found = source.explanations.get(case_number)
        return {"case_set": case_set, "case_number": case_number,
                "visits": source.bundle_set.visit_count(case_number),
                "scopes": [scope_view(scope)
                           for scope in self.scopes(source, case_number)],
                "prediction": prediction_view(found[0].prediction)
                if found else None,
                "explanation_eligible": bool(
                    found and found[0].explanation_eligible)}

    def evidence(self, case_set, case_number, kind, visit_number=None):
        """Return the evidence of a case's scope, or of its prediction, as
        displayed."""
        source = self.admitted(case_set, case_number)
        if kind == "prediction":
            found = source.explanations.get(case_number)
            if found is None:
                raise ServiceError("prediction_missing")
            return prediction_evidence_view(found[0])
        try:
            scope = contracts.ScopeRef(kind=kind, visit_number=visit_number)
        except ValueError:
            raise ServiceError("scope_invalid", 422) from None
        try:
            bundle, _ = source.bundle_set.qa_bundle(case_number, scope)
        except answer.AnswerError as error:
            if str(error) == "bundle_missing":
                raise ServiceError("scope_unknown") from None
            raise
        return qa_evidence_view(bundle)

    def ask(self, case_set, case_number, scope, question):
        """Answer one question about a case in a scope, record it and
        return the gate's response with who wrote it."""
        source = self.admitted(case_set, case_number)
        started = self.clock()
        result = answer.answer(question, case_set, case_number, scope,
                               source.bundle_set, self.vocabulary,
                               "q-" + uuid.uuid4().hex[:16], self.limits,
                               self.writer, self.recorder)
        return written_view(result.response, result.written,
                            self.clock() - started)

    def explain(self, case_set, case_number):
        """Explain a case's prepared prediction, record it and return the
        gate's response with who wrote it."""
        source = self.admitted(case_set, case_number)
        found = source.explanations.get(case_number)
        if found is None:
            raise ServiceError("prediction_missing")
        bundle, digest = found
        request = contracts.Request(
            schema_version="1.0", request_id="x-" + uuid.uuid4().hex[:16],
            task="prediction_explanation", case=bundle.case,
            scope=bundle.scope, prediction=contracts.PredictionRef(
                output_record_sha256=source.cases[
                    case_number].output_record_sha256))
        started = self.clock()
        written = explanation.explain_written(request, bundle, digest,
                                              self.writer)
        if self.recorder is not None:
            self.recorder.record(written.response, request,
                                 bundle_sha256=digest, written=written)
        return written_view(written.response, written,
                            self.clock() - started)
