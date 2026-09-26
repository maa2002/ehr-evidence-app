#!/usr/bin/env python3
"""Versioned QA and explanation evidence bundles (work items 29 and 30).

QA bundles: for every admitted case, one bundle per scope (pre-target
history, each case visit, all case visits) holding the scope's condition
events, its visits and its coverage record. Both QA tasks, listing and
presence checking, are answered from the same scope evidence.

Explanation bundles: for every admitted case, one bundle holding the
selected prediction copied unchanged from the published output, the
published drivers as attributions (scores unchanged, with their closure
tags and link status) and the recorded events the linkage record ties to
each driver. Linked events in pre-target visits are history; linked drug
and procedure events of the final admission are kept apart as
final-admission context; linked final-admission diagnoses are withheld and
only counted. Target-outcome driver entries (diagnoses attached only to the
final admission) are left out and counted. Each driver's closure attachment
is checked against the visits its linkage entry lists.

Every bundle records the digests of the inputs it was built from, and the
bundle set is published once, as a new versioned directory:

  qa/<bundle id>.json, explanation/<bundle id>.json  patient-bearing;
  manifest.json  bundle identifiers, digests and sizes (case numbers only);
  summary.json   aggregate counts only.

A registered case set is built the same way from its own pinned records
(--case-set), into its own versioned directory.

Usage:
    python -m ehr_evidence_app.evidence.bundles
    python -m ehr_evidence_app.evidence.bundles --operator-real
    python -m ehr_evidence_app.evidence.bundles --operator-real --case-set L
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import ntpath
import os
import sys

from pydantic import ValidationError

import ehr_evidence_app
from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import concepts as names
from ehr_evidence_app.evidence import contracts, policy, readers, retrieval
from ehr_evidence_app.evidence import schema
from ehr_evidence_app.evidence import vocabulary as vocab
from ehr_evidence_app.gnn import admission, linkage
from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.gnn import outputs as pmo
from ehr_evidence_app.gnn import provenance as amp
from ehr_evidence_app.storage import policy as guard

SCHEMA = "evidence-bundle-set"
SUMMARY_SCHEMA = "evidence-bundle-set-summary"
SCHEMA_VERSION = "1.0"
OUTPUT_VERSION = "pilot10-v2"
PROTECTED_ROOT = pmo.PROTECTED_ROOT
OUTPUT_DIR = PROTECTED_ROOT + "\\evidence-bundles\\" + OUTPUT_VERSION
MANIFEST_NAME = "manifest.json"
SUMMARY_NAME = "summary.json"
KINDS = ("qa", "explanation")
PARTIAL = ".partial"
BUILDER = "ehr_evidence_app.evidence.bundles %s" % ehr_evidence_app.__version__
LINKAGE_SHA256 = ("762ae7b57255c6d29cf681ff5f070c64"
                  "86a9914f18705a6ae49f9f313b0c3ede")
LINKAGE_SIZE = 131641
# The published version 2 bundle set of the pilot (schema 1.1).
PILOT_MANIFEST_SIZE = 25104
PILOT_MANIFEST_SHA256 = ("636b3476196e6f155ef04ed96875f125"
                         "954f6e2b8cb14522a3ea9b9efa3662d6")
NAME_AGREEMENT = ("same", "different", "published_empty")


class BundleError(Exception):
    """A fail-closed precondition failure carrying a finding code only."""


def real_plan(case_set=None):
    """Return the pinned inputs and the fixed protected output.

    Without a case set this is the development pilot; with one, every
    input is the case set's pinned stage record.
    """
    if case_set is not None:
        return {
            "readers": readers.real_plan(case_set),
            "outputs": registry.pinned(case_set, "prepared_outputs"),
            "linkage": registry.pinned(case_set, "linkage"),
            "policy_path": policy.CONFIG_PATH,
            "version": registry.bundle_set(case_set),
            "names": {"identity_mapping": case_set + "-mapping",
                      "closure_record": case_set + "-closure",
                      "model_outputs": case_set + "-outputs",
                      "linkage_record": "event-linkage-" + case_set},
            "output_dir": registry.bundles_dir(case_set),
            "protected_root": PROTECTED_ROOT,
        }
    return {
        "readers": readers.real_plan(),
        "outputs": dict(amp.real_plan()["inputs"]["step20_outputs"]),
        "linkage": {"path": linkage.OUTPUT_DIR + "\\" + linkage.LINKAGE_NAME,
                    "size_bytes": LINKAGE_SIZE, "sha256": LINKAGE_SHA256},
        "policy_path": policy.CONFIG_PATH,
        "version": OUTPUT_VERSION,
        "names": {"identity_mapping": ntpath.basename(mapping.OUTPUT_DIR),
                  "closure_record": admission.OUTPUT_VERSION,
                  "model_outputs": pmo.OUTPUT_VERSION,
                  "linkage_record": linkage.OUTPUT_VERSION},
        "output_dir": OUTPUT_DIR,
        "protected_root": PROTECTED_ROOT,
    }


def encode(document):
    """Return deterministic JSON bytes of a bundle or record."""
    return (json.dumps(document, indent=2, sort_keys=True, allow_nan=False)
            + "\n").encode("utf-8")


def bundle_bytes(bundle):
    """Return the deterministic bytes of one validated bundle."""
    return encode(bundle.model_dump(mode="json"))


def concept_of(concept_id, snapshot, legacy):
    """Return the evidence Concept of a published concept identifier."""
    if type(concept_id) is not int or concept_id < 0:
        raise BundleError("published_concept_invalid")
    return names.concept(concept_id, snapshot.concepts, legacy=legacy)


# --------------------------------------------------------------------------
# QA bundles (work item 29)
# --------------------------------------------------------------------------

def scope_slug(scope):
    """Return the bundle-identifier suffix for a scope."""
    if scope.kind == "pre_target_history":
        return "history"
    if scope.kind == "selected_visit":
        return "visit-%d" % scope.visit_number
    return "all"


def qa_scopes(case):
    """Return the QA scopes of a case: history, each visit, all visits."""
    scopes = [retrieval.scope("pre_target_history")]
    scopes += [retrieval.scope("selected_visit", number)
               for number in range(1, len(case.visit_ids) + 1)]
    scopes.append(retrieval.scope("case_visits"))
    return scopes


def qa_bundles(snapshot, case_set, case, provenance):
    """Return the QA bundles of one admitted case."""
    bundles = []
    for scope in qa_scopes(case):
        found = retrieval.retrieve(snapshot, case, scope, ("condition",))
        if found.coverage.status not in ("complete", "complete_empty"):
            raise BundleError("qa_scope_unavailable")
        bundles.append(schema.QABundle(
            bundle_id="qa-c%02d-%s" % (case.case_number, scope_slug(scope)),
            case=contracts.CaseRef(case_set=case_set.label,
                                   case_number=case.case_number),
            scope=scope, visits=found.visits, events=found.events,
            coverage=found.coverage, provenance=provenance))
    return bundles


# --------------------------------------------------------------------------
# Explanation bundles (work item 30)
# --------------------------------------------------------------------------

def prediction_record(case, record, snapshot, legacy):
    """Return the Prediction copied unchanged from the published output."""
    if amp.digest(record) != case.output_record_sha256:
        raise BundleError("output_record_mismatch")
    if case.label_correspondence != "established":
        raise BundleError("label_correspondence_not_established")
    selected = record["predicted_diagnosis"]
    return schema.Prediction(
        label_index=selected["label_index"],
        concept=concept_of(selected["concept_id"], snapshot, legacy),
        logit=selected["logit"], score=selected["probability"],
        output_record_sha256=case.output_record_sha256,
        label_correspondence="established",
        single_graph_reproduces_published=(
            case.single_graph_reproduces_published),
        batch_sensitive=case.batch_sensitive,
        upstream_partition=case.upstream_partition)


def linkage_entries(document, cases):
    """Return the linkage record's case entries after binding checks."""
    if (not isinstance(document, dict)
            or document.get("schema") != linkage.SCHEMA
            or document.get("status") != "verified"):
        raise BundleError("linkage_not_verified")
    body = {key: value for key, value in document.items()
            if key != "preparation"}
    if (document.get("preparation") or {}).get(
            "fingerprint_sha256") != amp.digest(body):
        raise BundleError("linkage_fingerprint_mismatch")
    entries = {entry.get("case_number"): entry
               for entry in document.get("cases") or []
               if isinstance(entry, dict)}
    for case in cases:
        entry = entries.get(case.case_number)
        if (entry is None or entry.get("patient_index") != case.patient_index
                or entry.get("output_record_sha256")
                != case.output_record_sha256):
            raise BundleError("linkage_case_mismatch")
    return entries


def attachment_of(link, final):
    """Return the attachment class a driver's linkage entry implies."""
    if not link["node_found"]:
        return "not_a_patient_node"
    positions = {visit["position"] for visit in link["visits"]}
    if not positions:
        return "not_attached"
    if positions == {final}:
        return "target_only"
    return "target_and_earlier" if final in positions else "earlier_only"


def linked_drivers(case, record, entry, source, positions):
    """Return (kept drivers, excluded entries, withheld event count).

    Each kept driver is (domain, position, driver, tag, link, event keys).
    Linked final-admission diagnoses are withheld; target-outcome entries
    are excluded with their events.
    """
    final = len(case.visit_ids) - 1
    kept, excluded, withheld = [], 0, 0
    for domain in vocab.DOMAINS:
        drivers = record["drivers"][domain]
        tags = case.drivers[domain]
        links = entry["domains"][domain]["drivers"]
        if len(tags) != len(drivers) or len(links) != len(drivers):
            raise BundleError("driver_count_mismatch")
        for position, (driver, tag, link) in enumerate(zip(drivers, tags,
                                                           links)):
            if (link["position"] != position
                    or (driver["score"] > 0.0)
                    != (tag.score_class == "positive")):
                raise BundleError("driver_tag_mismatch")
            if attachment_of(link, final) != tag.attachment:
                raise BundleError("driver_attachment_mismatch")
            keys = []
            for visit in link["visits"]:
                if positions.get(visit["visit_occurrence_id"]) != visit[
                        "position"]:
                    raise BundleError("linkage_visit_mismatch")
                for native_id in visit["event_ids"]:
                    event = source.get((domain, native_id))
                    if (event is None or event.visit_occurrence_id
                            != visit["visit_occurrence_id"]
                            or event.concept_id != driver["concept_id"]):
                        raise BundleError("linked_event_mismatch")
                    keys.append((domain, native_id))
            if tag.target_outcome_concept:
                excluded += 1
                continue
            final_diagnoses = [key for key in keys if domain == "condition"
                               and positions[source[key].visit_occurrence_id]
                               == final]
            withheld += len(final_diagnoses)
            kept.append((domain, position, driver, tag, link,
                         [key for key in keys if key not in final_diagnoses]))
    return kept, excluded, withheld


def explanation_bundle(snapshot, case_set, case, record, entry, legacy,
                       provenance):
    """Return the explanation bundle of one admitted case."""
    prediction = prediction_record(case, record, snapshot, legacy)
    positions = {visit_id: position
                 for position, visit_id in enumerate(case.visit_ids)}
    final = len(case.visit_ids) - 1
    source = {(event.domain, event.native_id): event
              for domain in vocab.DOMAINS
              for event in snapshot.events[domain]
              if event.person_id == case.person_id}
    kept, excluded, withheld = linked_drivers(case, record, entry, source,
                                              positions)
    ordered = sorted({key for *_, keys in kept for key in keys},
                     key=lambda key: retrieval.event_sort_key(source[key],
                                                              positions))
    visits = tuple(retrieval.visit_record(case, snapshot, position,
                                          "V%d" % (position + 1))
                   for position in range(len(case.visit_ids)))
    visit_ref = {visit.visit_occurrence_id: visit.ref for visit in visits}
    event_ref = {key: "E%d" % (index + 1) for index, key in enumerate(ordered)}
    history, context = [], []
    for key in ordered:
        event = source[key]
        item = retrieval.event_record(event, event_ref[key],
                                      visit_ref[event.visit_occurrence_id],
                                      snapshot.concepts)
        (context if positions[event.visit_occurrence_id] == final
         else history).append(item)
    attributions = tuple(
        schema.Attribution(
            ref="A%d" % (index + 1), domain=domain, position=position,
            concept=concept_of(driver["concept_id"], snapshot, legacy),
            score=driver["score"], score_class=tag.score_class,
            attachment=tag.attachment,
            narration_eligible=tag.narration_eligible,
            link_status=link["link_status"],
            linked_event_refs=tuple(event_ref[key] for key in keys))
        for index, (domain, position, driver, tag, link, keys)
        in enumerate(kept))
    eligible = any(item.narration_eligible for item in attributions)
    if eligible != case.explanation_eligible:
        raise BundleError("explanation_eligibility_mismatch")
    unmapped = sum(item.concept.name_status == "unmapped" for item in history)
    scope = retrieval.scope("pre_target_history")
    coverage = schema.Coverage(
        scope=scope, status="complete" if history else "complete_empty",
        domains=vocab.DOMAINS, source_snapshot=snapshot.label,
        visits_in_scope=final, events_in_scope=len(history),
        unmapped_events_in_scope=unmapped,
        excluded_final_admission_events=withheld,
        events_outside_case_visits=sum(
            1 for event in source.values()
            if event.visit_occurrence_id not in positions),
        reason_codes=("unmapped_records_in_scope",) if unmapped else ())
    return schema.ExplanationBundle(
        bundle_id="exp-c%02d" % case.case_number,
        case=contracts.CaseRef(case_set=case_set.label,
                               case_number=case.case_number),
        scope=scope, prediction=prediction, attributions=attributions,
        visits=visits, history_events=tuple(history),
        final_admission_context=tuple(context), coverage=coverage,
        excluded_driver_entries=excluded, restrictions=case.restrictions,
        explanation_eligible=eligible, provenance=provenance)


def name_agreement(bundle, record):
    """Count published names equal to, differing from or absent for the
    bundle's names (prediction and attributions)."""
    pairs = [(record["predicted_diagnosis"]["name"],
              bundle.prediction.concept)]
    pairs += [(record["drivers"][item.domain][item.position]["name"],
               item.concept) for item in bundle.attributions]
    counts = collections.Counter()
    for published, concept in pairs:
        if not published:
            counts["published_empty"] += 1
        elif concept.name == names.clip(published):
            counts["same"] += 1
        else:
            counts["different"] += 1
    return counts


# --------------------------------------------------------------------------
# Bundle set
# --------------------------------------------------------------------------

def check_limits(bundles, limits):
    """Fail closed when a bundle exceeds the evidence or size limits."""
    for bundle in bundles:
        if isinstance(bundle, schema.QABundle):
            count = len(bundle.events)
        else:
            count = (len(bundle.history_events)
                     + len(bundle.final_admission_context)
                     + len(bundle.attributions))
        if count > limits.max_evidence_items:
            raise BundleError("bundle_evidence_limit_exceeded")
        if len(bundle_bytes(bundle)) > limits.max_bundle_bytes:
            raise BundleError("bundle_size_limit_exceeded")


def summarise(qa, explanations, agreement):
    """Return aggregate counts over a bundle set."""
    coverage = collections.Counter(bundle.coverage.status for bundle in qa)
    scopes = collections.Counter(bundle.scope.kind for bundle in qa)
    history = [bundle for bundle in qa
               if bundle.scope.kind == "pre_target_history"]
    every = [bundle for bundle in qa if bundle.scope.kind == "case_visits"]
    attributions = [item for bundle in explanations
                    for item in bundle.attributions]
    name_status = collections.Counter(
        item.concept.name_status for item in attributions)
    return {
        "qa_bundles": len(qa),
        "qa_by_scope": {kind: scopes.get(kind, 0) for kind in vocab.SCOPES},
        "qa_coverage": {state: coverage.get(state, 0)
                        for state in vocab.COVERAGE_STATES},
        "history_condition_events": sum(len(bundle.events)
                                        for bundle in history),
        "history_unmapped_condition_events": sum(
            bundle.coverage.unmapped_events_in_scope for bundle in history),
        "history_excluded_final_admission_events": sum(
            bundle.coverage.excluded_final_admission_events
            for bundle in history),
        "case_visit_condition_events": sum(len(bundle.events)
                                           for bundle in every),
        "case_visit_unmapped_condition_events": sum(
            bundle.coverage.unmapped_events_in_scope for bundle in every),
        "explanation_bundles": len(explanations),
        "explanation_eligible": sum(bundle.explanation_eligible
                                    for bundle in explanations),
        "attributions": len(attributions),
        "narratable_attributions": sum(item.narration_eligible
                                       for item in attributions),
        "attribution_names": {state: name_status.get(state, 0)
                              for state in vocab.NAME_STATES},
        "excluded_driver_entries": sum(bundle.excluded_driver_entries
                                       for bundle in explanations),
        "linked_history_events": sum(len(bundle.history_events)
                                     for bundle in explanations),
        "final_admission_context_events": sum(
            len(bundle.final_admission_context) for bundle in explanations),
        "withheld_final_admission_diagnoses": sum(
            bundle.coverage.excluded_final_admission_events
            for bundle in explanations),
        "events_with_source_concept": sum(
            event.source_concept is not None
            for event in [event for bundle in qa for event in bundle.events]
            + [event for bundle in explanations
               for event in bundle.history_events
               + bundle.final_admission_context]),
        "published_name_agreement": {state: agreement.get(state, 0)
                                     for state in NAME_AGREEMENT},
    }


def build_set(snapshot, case_set, outputs, linkage_document, legacy,
              provenance, limits):
    """Return (qa bundles, explanation bundles, counts) for admitted cases."""
    cases = [case for case in case_set.cases if case.admitted]
    if not cases:
        raise BundleError("no_admitted_cases")
    entries = linkage_entries(linkage_document, cases)
    qa, explanations, agreement = [], [], collections.Counter()
    try:
        for case in cases:
            qa.extend(qa_bundles(snapshot, case_set, case, provenance))
            record = outputs.get(case.patient_index)
            if record is None:
                raise BundleError("output_record_missing")
            bundle = explanation_bundle(snapshot, case_set, case, record,
                                        entries[case.case_number], legacy,
                                        provenance)
            agreement.update(name_agreement(bundle, record))
            explanations.append(bundle)
    except ValidationError as error:
        raise BundleError("bundle_record_invalid:%s" % error.title) from None
    except retrieval.ScopeError:
        raise BundleError("case_visit_missing") from None
    check_limits(qa + explanations, limits)
    return qa, explanations, summarise(qa, explanations, agreement)


def manifest_document(qa, explanations, provenance, counts,
                      version=OUTPUT_VERSION):
    """Return the manifest of a bundle set (no native identifiers)."""
    files = []
    for kind, bundles in zip(KINDS, (qa, explanations)):
        for bundle in bundles:
            data = bundle_bytes(bundle)
            files.append({
                "kind": kind, "bundle_id": bundle.bundle_id,
                "path": "%s/%s.json" % (kind, bundle.bundle_id),
                "case_number": bundle.case.case_number,
                "scope": bundle.scope.model_dump(mode="json"),
                "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()})
    return {"schema": SCHEMA, "schema_version": SCHEMA_VERSION,
            "version": version, "builder": BUILDER,
            "patient_bearing": False,
            "case_set": qa[0].case.case_set if qa else None,
            "provenance": provenance.model_dump(mode="json"),
            "counts": counts, "files": files}


def summary_document(manifest):
    """Return the aggregate summary of a manifest."""
    return {"schema": SUMMARY_SCHEMA, "schema_version": SCHEMA_VERSION,
            "version": manifest["version"],
            "counts": manifest["counts"],
            "manifest_sha256": hashlib.sha256(encode(manifest)).hexdigest()}


def output_preconditions(plan, paths):
    """Refuse an existing, unprotected or input-overlapping output."""
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        for input_path in paths:
            if (guard.is_within(input_path, path)
                    or guard.is_within(path, os.path.dirname(input_path))):
                raise BundleError("output_overlaps_input")
        if os.path.lexists(path):
            raise BundleError("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise BundleError(problem)


def set_files(qa, explanations, manifest, summary):
    """Return {relative path: bytes} for every file of a bundle set."""
    files = {"%s/%s.json" % (kind, bundle.bundle_id): bundle_bytes(bundle)
             for kind, bundles in zip(KINDS, (qa, explanations))
             for bundle in bundles}
    files[MANIFEST_NAME] = encode(manifest)
    files[SUMMARY_NAME] = encode(summary)
    return files


def publish(plan, files, held):
    """Write the files through a new .partial directory; rename it."""
    partial = plan["output_dir"] + PARTIAL
    os.makedirs(os.path.dirname(plan["output_dir"]), exist_ok=True)
    os.mkdir(partial)
    written = []
    try:
        for kind in KINDS:
            os.mkdir(os.path.join(partial, kind))
        for name, data in sorted(files.items()):
            target = os.path.join(partial, *name.split("/"))
            with open(target, "xb") as handle:
                written.append(target)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            pmo.confirm_inputs(held, rehash=True)
        except pmo.Step20Error as error:
            raise BundleError(str(error)) from None
        try:
            os.rename(partial, plan["output_dir"])
        except OSError:
            raise BundleError("output_rename_failed") from None
    except BaseException:
        for target in written:
            if os.path.lexists(target):
                os.remove(target)
        for kind in KINDS:
            directory = os.path.join(partial, kind)
            if os.path.isdir(directory):
                os.rmdir(directory)
        if os.path.isdir(partial):
            os.rmdir(partial)
        raise
    return {name: hashlib.sha256(files[name]).hexdigest()
            for name in (MANIFEST_NAME, SUMMARY_NAME)}


def input_paths(plan):
    """Return every pinned input path."""
    reader_plan = plan["readers"]
    return [reader_plan["manifest"]["path"], reader_plan["closure"]["path"],
            reader_plan["mapping"]["path"],
            reader_plan["legacy_concepts"]["path"],
            reader_plan["snapshot_dir"] + "\\visits.csv",
            plan["outputs"]["path"], plan["linkage"]["path"]]


def real_provenance(plan, snapshot, case_set, legacy_sha, policy_config,
                    policy_sha):
    """Return the provenance record of the real bundle set."""
    ref = schema.DigestRef
    labels = plan["names"]
    legacy_path = plan["readers"]["legacy_concepts"]["path"]
    return schema.Provenance(
        source_snapshot=ref(name=snapshot.label,
                            sha256=snapshot.manifest_sha256),
        identity_mapping=ref(name=labels["identity_mapping"],
                             sha256=case_set.mapping_sha256),
        closure_record=ref(name=labels["closure_record"],
                           sha256=case_set.closure_sha256),
        concept_table=ref(name=snapshot.label + "-concepts",
                          sha256=snapshot.concepts_sha256),
        legacy_concept_map=ref(
            name=ntpath.basename(ntpath.dirname(legacy_path))
            + "-kg-concepts", sha256=legacy_sha),
        model_outputs=ref(name=labels["model_outputs"],
                          sha256=plan["outputs"]["sha256"].lower()),
        linkage_record=ref(name=labels["linkage_record"],
                           sha256=plan["linkage"]["sha256"].lower()),
        evidence_policy=ref(name=policy_config["policy_version"],
                            sha256=policy_sha),
        builder=BUILDER)


def run_real(plan=None):
    """Operator-only real run in the application environment."""
    plan = plan or real_plan()
    if guard.fixed_policy_problem():
        raise BundleError("fixed_policy_rejected")
    if not isinstance(plan["linkage"].get("sha256"), str):
        raise BundleError("linkage_record_not_pinned")
    output_preconditions(plan, input_paths(plan))
    limits = contracts.load_limits()
    policy_config, policy_sha = policy.load(plan["policy_path"])
    reader_plan = plan["readers"]
    try:
        snapshot = readers.read_snapshot(reader_plan)
        case_set = readers.read_cases(reader_plan)
        legacy, legacy_sha = readers.read_legacy_concepts(reader_plan)
    except readers.ReaderError as error:
        raise BundleError(str(error)) from None
    held = []
    try:
        outputs_bytes = pmo.verify_read(plan["outputs"], "outputs", held)
        linkage_bytes = pmo.verify_read(plan["linkage"], "linkage", held)
        pmo.confirm_inputs(held)
    except pmo.Step20Error as error:
        raise BundleError(str(error)) from None
    outputs = {record["patient_index"]: record
               for record in mapping.parse_json(outputs_bytes)}
    provenance = real_provenance(plan, snapshot, case_set, legacy_sha,
                                 policy_config, policy_sha)
    qa, explanations, counts = build_set(
        snapshot, case_set, outputs, mapping.parse_json(linkage_bytes),
        legacy, provenance, limits)
    manifest = manifest_document(qa, explanations, provenance, counts,
                                 plan["version"])
    summary = summary_document(manifest)
    digests = publish(plan, set_files(qa, explanations, manifest, summary),
                      held)
    return summary, digests


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

SYNTHETIC_LIMITS = dict(max_question_chars=500, max_prompt_tokens=16384,
                        max_output_tokens=768, max_concurrent_requests=1,
                        max_request_seconds=120, max_generation_attempts=1,
                        max_evidence_items=2000, max_bundle_bytes=1048576)


def synthetic_inputs():
    """Return invented (snapshot, case set, outputs, linkage, legacy)."""
    plan, case, graph, record, inverse, snapshot = linkage.synthetic_fixture()
    visits = {visit_id: readers.SourceVisit(
        visit_occurrence_id=visit_id, person_id=case.person_id,
        visit_concept_id=9201, start_date="2100-01-0%d" % (index + 1),
        end_date="2100-01-0%d" % (index + 2))
        for index, visit_id in enumerate(case.visit_ids)}
    concepts = {concept_id: readers.ConceptRow(
        concept_id=concept_id, name="SYNTHETIC CONCEPT %d" % concept_id,
        domain_id=domain_id, vocabulary_id="SYNTHETIC")
        for concept_id, domain_id in ((1001, "Condition"),
                                      (1002, "Condition"),
                                      (2001, "Procedure"), (3001, "Drug"))}
    events = {}
    for domain, rows in snapshot.events.items():
        events[domain] = tuple(dataclasses.replace(
            event, source_concept_id=5000 + event.native_id,
            source_value="SYN%d" % event.native_id) for event in rows)
        for event in rows:
            concepts[5000 + event.native_id] = readers.ConceptRow(
                concept_id=5000 + event.native_id,
                name="SYNTHETIC SOURCE %d" % event.native_id,
                domain_id="Condition", vocabulary_id="SYNTHETIC-ICD",
                code="SYN%d" % event.native_id)
    snapshot = readers.Snapshot(
        label=snapshot.label, manifest_sha256=snapshot.manifest_sha256,
        concepts_sha256=snapshot.concepts_sha256, complete=True,
        visits=visits, events=events, concepts=concepts)
    legacy = {9001: readers.ConceptRow(
        concept_id=9001, name="SYNTHETIC LABEL", domain_id="Condition",
        vocabulary_id="SYNTHETIC")}

    def driver(domain, position, score):
        """Return one invented published driver entry."""
        entry = record["drivers"][domain][position]
        return dict(entry, score=score,
                    name="" if domain == "procedure"
                    else "SYNTHETIC CONCEPT %d" % entry["concept_id"])

    record = {"patient_index": case.patient_index,
              "predicted_diagnosis": {"label_index": 3, "concept_id": 9001,
                                      "name": "SYNTHETIC LABEL",
                                      "logit": 1.5,
                                      "probability": 0.8175744761936437},
              "drivers": {"condition": [driver("condition", 0, 0.25),
                                        driver("condition", 1, 0.0)],
                          "procedure": [driver("procedure", 0, 0.125)],
                          "drug": [driver("drug", 0, 0.5)]}}
    tag = readers.DriverTag
    case = readers.Case(
        case_number=1, patient_index=case.patient_index,
        graph_position=case.graph_position, person_id=case.person_id,
        subject_id=case.subject_id, visit_ids=case.visit_ids,
        admitted=True, explanation_eligible=True,
        restrictions=("target_attached_drivers_present",
                      "target_outcome_entries_excluded"),
        upstream_partition="test", output_record_sha256=amp.digest(record),
        batch_sensitive=False, single_graph_reproduces_published=True,
        label_correspondence="established",
        drivers={"condition": (tag(0, "positive", "target_and_earlier",
                                   False, True),
                               tag(1, "zero", "target_only", True, False)),
                 "procedure": (tag(0, "positive", "earlier_only", False,
                                   True),),
                 "drug": (tag(0, "positive", "target_only", False, True),)})
    entry = linkage.link_case(case, graph, record, inverse, snapshot)
    linked, _ = linkage.build_record(plan, [entry], {"synthetic": True})
    case_set = readers.CaseSet(label="synthetic-1-v1",
                               closure_sha256="3" * 64,
                               mapping_sha256="4" * 64, cases=(case,))
    return snapshot, case_set, {case.patient_index: record}, linked, legacy


def synthetic_provenance():
    """Return an invented provenance record."""
    ref = schema.DigestRef
    return schema.Provenance(
        source_snapshot=ref(name="synthetic-1-v1", sha256="1" * 64),
        identity_mapping=ref(name="synthetic-mapping", sha256="4" * 64),
        closure_record=ref(name="synthetic-closure", sha256="3" * 64),
        concept_table=ref(name="synthetic-concepts", sha256="2" * 64),
        builder=BUILDER)


def run_synthetic():
    """Build an invented bundle set in memory; nothing is read or written."""
    snapshot, case_set, outputs, linked, legacy = synthetic_inputs()
    return build_set(snapshot, case_set, outputs, linked, legacy,
                     synthetic_provenance(),
                     contracts.ExecutionLimits(**SYNTHETIC_LIMITS))


def report(counts, digests=None):
    """Print the aggregate counts."""
    for key in sorted(counts):
        value = counts[key]
        if isinstance(value, dict):
            value = ", ".join("%s %d" % item for item in value.items())
        print("%-40s %s" % (key, value))
    for name, value in sorted((digests or {}).items()):
        print("%-40s %s" % (name, value))


def main(argv=None):
    """Run the synthetic default mode or the operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="build the real bundle set; writes one new "
                             "protected directory")
    parser.add_argument("--case-set",
                        help="build a registered case set instead of the "
                             "development pilot (with --operator-real)")
    args = parser.parse_args(argv)
    if args.case_set is not None and not args.operator_real:
        print("FAILED: --case-set needs --operator-real")
        return 2
    try:
        if args.operator_real:
            try:
                plan = real_plan(args.case_set)
            except registry.RegistryError as error:
                raise BundleError(str(error)) from None
            summary, digests = run_real(plan)
            report(summary["counts"], digests)
        else:
            _, _, counts = run_synthetic()
            report(counts)
    except (BundleError, contracts.ContractError, policy.PolicyError) as \
            error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
