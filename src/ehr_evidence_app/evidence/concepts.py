"""Readable concept names (work item 26).

Names come from two pinned tables, and every name states which one:

* the source snapshot's concept table (``source_snapshot``), filled by the
  extraction for every standard and source concept its events reference;
* the legacy concept map (``legacy_concept_map``), the readable map the
  inherited bridge used, consulted only for concepts the snapshot lacks
  (for example a predicted label that is not among the patient's records).

A concept is available (a non-empty name from one of them), unavailable
(neither has a name; nothing is guessed) or unmapped (concept 0, a record
with no standard concept, which keeps its recorded source code).
"""

from __future__ import annotations

from ehr_evidence_app.evidence import schema

NAME_LIMIT = 255


def clip(text):
    """Return text within the record's length limit, or None."""
    if not text:
        return None
    return text[:NAME_LIMIT]


def concept(concept_id, concepts, source_value=None, legacy=None):
    """Return the evidence Concept for one recorded concept id."""
    if concept_id == 0:
        return schema.Concept(concept_id=0, name_status="unmapped",
                              source_code=clip(source_value))
    for table, source in ((concepts, "source_snapshot"),
                          (legacy or {}, "legacy_concept_map")):
        row = table.get(concept_id)
        if row is not None and row.name:
            return schema.Concept(
                concept_id=concept_id, name_status="available",
                name=clip(row.name), name_source=source,
                vocabulary_id=clip(row.vocabulary_id),
                domain_id=clip(row.domain_id))
    row = concepts.get(concept_id) or (legacy or {}).get(concept_id)
    return schema.Concept(
        concept_id=concept_id, name_status="unavailable",
        vocabulary_id=clip(row.vocabulary_id) if row else None,
        domain_id=clip(row.domain_id) if row else None)
