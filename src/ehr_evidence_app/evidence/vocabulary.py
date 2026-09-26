"""Closed vocabularies shared by the evidence contracts and records.

Every value that later phases branch on (tasks, scopes, outcomes, reason
codes, coverage states, visit roles, link states) is fixed here, so a new
value is a deliberate, versioned change rather than a free-text drift.
"""

from typing import Literal

TASKS = ("condition_list", "condition_presence", "prediction_explanation")
QA_TASKS = ("condition_list", "condition_presence")

SCOPES = ("pre_target_history", "selected_visit", "case_visits")
EXPLANATION_SCOPES = ("pre_target_history",)

OUTCOMES = ("answer", "not_recorded", "explanation", "clarification",
            "abstention", "case_rejection", "validation_rejection",
            "operational_error")
RELEASED_TEXT_OUTCOMES = ("answer", "not_recorded", "explanation",
                          "clarification", "abstention")

# Reason codes each outcome may carry. Released answers may carry only
# disclosure codes; every other outcome must state at least one reason.
REASONS_BY_OUTCOME = {
    "answer": ("unmapped_records_in_scope",),
    "not_recorded": ("unmapped_records_in_scope",),
    "explanation": ("batch_sensitive_prediction",
                    "target_attached_drivers_present",
                    "target_outcome_entries_excluded"),
    "clarification": ("concept_ambiguous", "concept_unknown",
                      "concept_too_broad", "scope_ambiguous",
                      "visit_ambiguous", "task_ambiguous", "case_ambiguous",
                      "prediction_selection_needed"),
    "abstention": ("task_unsupported", "evidence_insufficient",
                   "coverage_incomplete", "unmapped_records_in_scope",
                   "no_positive_attribution", "explanation_ineligible"),
    "case_rejection": ("case_not_admitted", "identity_inconsistent",
                       "history_inconsistent", "prediction_link_invalid",
                       "scope_invalid", "evidence_inconsistent"),
    "validation_rejection": ("structure_invalid", "reference_unresolved",
                             "claim_unsupported", "prediction_altered",
                             "attribution_altered", "prohibited_wording",
                             "date_unsupported", "scope_violated"),
    "operational_error": ("timeout", "resource_exhausted", "limit_exceeded",
                          "retrieval_failed", "backend_failed",
                          "validator_failed"),
}
REASON_REQUIRED_OUTCOMES = ("clarification", "abstention", "case_rejection",
                            "validation_rejection", "operational_error")

COVERAGE_STATES = ("complete", "complete_empty", "unavailable", "failed")
DOMAINS = ("condition", "procedure", "drug")
SOURCE_TABLES = {"condition": "condition_occurrence",
                 "procedure": "procedure_occurrence",
                 "drug": "drug_exposure"}
NAME_STATES = ("available", "unavailable", "unmapped")
NAME_SOURCES = ("source_snapshot", "legacy_concept_map")
VISIT_ROLES = ("pre_target", "final_admission")
ATTACHMENTS = ("target_only", "target_and_earlier", "earlier_only",
               "not_attached", "not_a_patient_node")
SCORE_CLASSES = ("positive", "zero")
LINK_STATES = ("verified", "partial", "unlinked", "no_edges",
               "not_a_patient_node")
PARTITIONS = ("train", "validation", "test")
RESTRICTIONS = ("batch_sensitive_prediction",
                "target_attached_drivers_present",
                "target_outcome_entries_excluded")

Task = Literal[TASKS]
Scope = Literal[SCOPES]
Outcome = Literal[OUTCOMES]
CoverageState = Literal[COVERAGE_STATES]
Domain = Literal[DOMAINS]
SourceTable = Literal[tuple(SOURCE_TABLES.values())]
NameState = Literal[NAME_STATES]
NameSource = Literal[NAME_SOURCES]
VisitRole = Literal[VISIT_ROLES]
Attachment = Literal[ATTACHMENTS]
ScoreClass = Literal[SCORE_CLASSES]
LinkState = Literal[LINK_STATES]
Partition = Literal[PARTITIONS]
Restriction = Literal[RESTRICTIONS]
ReasonCode = Literal[tuple(sorted({code for codes in REASONS_BY_OUTCOME.values()
                                   for code in codes}))]
