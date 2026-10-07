# 0005. Driver-to-Event Linkage and the Evidence Claim Policy

Status: accepted (2026-09-26).

## Context

The Phase 3 closure ([ADR 0002](0002-phase-3-closure.md)) prohibited dated,
per-visit, frequency and progression claims about events, because the
identity mapping linked persons and visits but not events. The closure
record and its claim policy are immutable.

A published driver is a concept node of the patient's graph; its
visit-to-concept edges say which visits the model saw the concept in. Each
edge comes from recorded events of that concept in that visit, so the edges
can be tied back to the source records without touching the scores.

## Decision

1. `gnn.linkage` publishes a versioned linkage record: for every admitted
   case and driver, the attached visits with the native identifiers of the
   events behind each edge, and a link status. The record is published only
   if every edge is backed by events and every in-vocabulary event in a case
   visit appears as an edge.
2. Attribution stays concept-level. Linked events are context for an
   attribution; a score is never assigned to, divided across or tied to an
   event, a date or a visit.
3. A new, versioned claim policy (`evidence-policy-v1`) extends the closure
   policy: statements about when, and in how many pre-target visits, a
   driver's concept was recorded, and about its repeated recordings, are
   allowed when they cite the linked events. The ban on event claims becomes
   a ban on event claims without cited, linked events. Everything else is
   unchanged.
4. Explanation bundles keep linked pre-target events as history, keep the
   final admission's linked drug and procedure events apart as
   final-admission context, and withhold final-admission diagnoses, which
   are only counted.

## Consequences

- For the pilot, all 655 edges (324 condition, 30 procedure, 301 drug) and
  all 193 drivers verify, and no in-vocabulary event lacks an edge.
- Explanations can cite the recorded history of a driver's concept, while
  the model's sensitivity stays a single unsigned value per concept.
- The closure record remains the authority for admission and driver tags;
  the bundle builder checks that the closure's attachment classes agree with
  the linkage record.
- A new case set needs its own linkage record before its bundles are built.
