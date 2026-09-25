"""Evidence claim policy (work item 28, decision D1).

Extends the Phase 3 closure record's claim policy for evidence that the
driver-to-event linkage record ties to the model's graph edges. The closure
record itself is not edited; this is a new, versioned policy that names it
as its base.

Changes relative to the closure policy:

* allowed: statements about when, and in how many pre-target visits, a
  driver's concept was recorded, citing the linked events, and descriptions
  of repeated recordings of that concept, citing each event;
* the blanket ban on dated, frequency and progression claims about events
  becomes a ban on such claims without cited, linked events.

Everything else stays prohibited, including tying a score to a date, a visit
or a recurrence. The values are fixed here and mirrored exactly in
config/evidence_policy.json; loading refuses a mismatch.
"""

from __future__ import annotations

import hashlib
import json
import os

from ehr_evidence_app.gnn import admission

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
CONFIG_PATH = os.path.join(REPO_ROOT, "config", "evidence_policy.json")
POLICY_VERSION = "evidence-policy-v1"
LINKAGE_VERSION = "event-linkage-pilot10-v1"

ADDED = ["linked_event_history_for_driver_concepts",
         "repeated_recordings_of_driver_concepts"]
REPLACED = "event_level_dated_frequency_or_progression_claims"
REPLACEMENT = "event_claims_without_cited_linked_events"
BASE = admission.CONFIG["claim_policy"]

CONFIG = {
    "schema_version": "1.0",
    "policy_version": POLICY_VERSION,
    "base": {"record": admission.OUTPUT_VERSION,
             "claim_policy": "claim_policy of the closure record"},
    "linkage_record": LINKAGE_VERSION,
    "claim_policy": {
        "allowed": list(BASE["allowed"]) + ADDED,
        "prohibited": [item if item != REPLACED else REPLACEMENT
                       for item in BASE["prohibited"]]},
    "conditions": [
        "History statements about a driver's concept cite linked events in "
        "pre-target visits only.",
        "A score is never assigned to, divided across or tied to an event, "
        "a date or a visit.",
        "Final-admission drug and procedure records are described only as "
        "final-admission context, never as history.",
        "Final-admission diagnoses never enter an explanation.",
    ],
}


class PolicyError(Exception):
    """The committed policy does not match the implemented values."""


def parse(data):
    """Return (policy dict, SHA-256) for committed policy bytes."""
    try:
        config = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PolicyError("evidence policy is not JSON") from None
    if not isinstance(config, dict) or {
            key: config.get(key) for key in CONFIG} != CONFIG:
        raise PolicyError("evidence policy differs from the implemented "
                          "values")
    return config, hashlib.sha256(data).hexdigest()


def load(path=CONFIG_PATH):
    """Return (policy dict, SHA-256 of the committed bytes)."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        raise PolicyError("evidence policy is unreadable") from None
    return parse(data)
