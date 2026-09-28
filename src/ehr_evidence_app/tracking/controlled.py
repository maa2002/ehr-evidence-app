"""Controlled metadata-only task payload validation for Step 11.

This module is deliberately pure: it never imports ClearML, reads the
process environment, captures output, or contacts a service.
"""
import math
import ntpath
import re

WORKSPACE_ROOT = r"G:\Industry Dataset\ehr-evidence-workspace"
TASK_NAME = "controlled metadata probe"
MAX_REFERENCE_LENGTH = 512
MAX_COMPONENT_LENGTH = 96
MAX_METRIC_LENGTH = 24
MAX_LATENCY_MS = 1_000_000_000
LINEAGE_FIELDS = {
    "code_revision": "version",
    "case_set_version": "version",
    "llm_configuration_fingerprint": "sha256",
    "prompt_version": "version",
    "evidence_schema_version": "version",
    "validation_configuration_fingerprint": "sha256",
    "evaluation_outputs": "metrics",
    "protected_reference": "workspace_reference",
    "protected_checksum": "sha256",
}
HYPERPARAMETERS = {
    "learning_rate": (int, float),
    "batch_size": (int,),
    "epochs": (int,),
    "weight_decay": (int, float),
}
METRIC_NAMES = frozenset(("accuracy", "macro_f1", "micro_f1", "coverage",
                          "latency_ms", "macro_auroc", "macro_auprc",
                          "best_epoch", "history_length", "requests",
                          "model_written_rate", "fallback_rate"))
SCORE_METRICS = frozenset(("accuracy", "macro_f1", "micro_f1", "coverage",
                           "macro_auroc", "macro_auprc",
                           "model_written_rate", "fallback_rate"))
COUNT_METRICS = frozenset(("best_epoch", "history_length", "requests"))
VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")
SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PATIENT_ID = re.compile(r"(?i)\b(?:patient|subject|mrn|record|visit|encounter)(?:[ _-]*(?:id|number|no))?\s*[:=#-]\s*[A-Za-z0-9-]{2,}|\bcase(?:[ _-]*(?:id|number|no))\s*[:=#-]\s*[A-Za-z0-9-]{2,}|\bcase-[A-F0-9]{8,}\b")
SECRET_BEARER = re.compile(r"(?i)\bbearer[_ -]+[A-Za-z0-9._~+/=-]{8,}")
SECRET_DIRECT_ASSIGNMENT = re.compile(
    r"(?i)\b(?:(?:api|access)[_ -]?key|secret|token|password|auth)\s*[:=]\s*[A-Za-z0-9._~+/=-]{8,}")
SECRET_NAMED_VALUE = re.compile(
    r"(?i)\b(?:(?:api|access)[_-]key|secret|token|password|auth|private)[_-]+[A-Za-z0-9]{8,}")
SECRET_TOKEN_SHAPE = re.compile(r"(?i)\b(?:sk|pk|ghp|github_pat)[-_][A-Za-z0-9_-]{8,}")
SECRET_VENDOR_SHAPE = re.compile(
    r"(?i)\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bhf_[A-Za-z0-9]{20,}|"
    r"\bxox[abposr]-[A-Za-z0-9-]{10,}|\beyJ[A-Za-z0-9_-]{10,}\.|"
    r"-{5}BEGIN[A-Z ]*PRIVATE KEY")


def _secret_present(value):
    """Reject credential names with direct assignments or name/value separators."""
    return any(pattern.search(value) for pattern in (
        SECRET_BEARER, SECRET_DIRECT_ASSIGNMENT, SECRET_NAMED_VALUE,
        SECRET_TOKEN_SHAPE, SECRET_VENDOR_SHAPE))
LOCAL_PATH = re.compile(r"(?i)(?:^[A-Za-z]:[\\/]|^\\\\|(?:^|\s)[\\/](?:users|home)[\\/])")
SYNTHETIC = re.compile(r"(?i)synthetic|test[_ -]?marker|excluded[_ -]?marker")
FORBIDDEN_CONTENT = re.compile(r"(?i)\bbecause\s+the\s+patient\b")
FORBIDDEN_NAME_TOKENS = frozenset((
    "prompt", "prompts", "response", "responses", "completion",
    "completions", "attribution", "attributions", "prediction",
    "predictions", "clinical", "explanation", "explanations",
    "patient", "patients", "subject", "subjects", "mrn", "record",
    "records", "visit", "visits", "encounter", "encounters",
    "diagnosis", "diagnoses",
))


def _has_forbidden_name_token(value):
    """Match sensitive words after splitting on underscores and punctuation."""
    tokens = re.findall(r"[A-Za-z0-9]+", value.lower())
    return bool(FORBIDDEN_NAME_TOKENS.intersection(tokens))

RESERVED_COMPONENT = re.compile(r"(?i)^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$")


class UnsafeMetadata(ValueError):
    """Input does not fit the approved metadata schema."""


def _clean_text(value, field):
    if not isinstance(value, str) or not value or value != value.strip():
        raise UnsafeMetadata("invalid text field: " + field)
    if (_secret_present(value) or PATIENT_ID.search(value) or
            SYNTHETIC.search(value) or FORBIDDEN_CONTENT.search(value) or
            _has_forbidden_name_token(value) or LOCAL_PATH.search(value)):
        raise UnsafeMetadata("forbidden content in field: " + field)
    return value


def _canonical_protected_reference(value):
    """Validate and canonicalize one bounded workspace reference without file access."""
    if (not isinstance(value, str) or not value or
            len(value) > MAX_REFERENCE_LENGTH or
            any(ord(char) < 32 for char in value)):
        raise UnsafeMetadata("invalid protected reference length or characters")
    # A single separator convention avoids mixed-separator traversal tricks.
    if "/" in value and "\\" in value:
        raise UnsafeMetadata("mixed path separators are not allowed")
    canonical_input = value.replace("/", "\\")
    drive, tail = ntpath.splitdrive(canonical_input)
    workspace_drive = ntpath.splitdrive(WORKSPACE_ROOT)[0].lower()
    if drive.lower() != workspace_drive or not tail.startswith("\\"):
        raise UnsafeMetadata(
            "protected reference must be an absolute workspace path")
    if canonical_input.startswith(("\\\\?\\", "\\\\.\\")):
        raise UnsafeMetadata("device paths are not allowed")
    if canonical_input.count(":") != 1:
        raise UnsafeMetadata("alternate streams and ambiguous colons are not allowed")
    raw_parts = tail[1:].split("\\")
    if not raw_parts or any(part in ("", ".", "..") for part in raw_parts):
        raise UnsafeMetadata("empty and traversal path components are not allowed")
    if len(raw_parts[0]) == 0:
        raise UnsafeMetadata("protected workspace root is missing")
    canonical = ntpath.normpath(canonical_input)
    root = ntpath.normpath(WORKSPACE_ROOT)
    if ntpath.normcase(ntpath.commonpath((canonical, root))) != ntpath.normcase(root):
        raise UnsafeMetadata("protected reference must remain beneath the workspace")
    relative = ntpath.relpath(canonical, root)
    parts = relative.split("\\")
    if relative == "." or any(not part or len(part) > MAX_COMPONENT_LENGTH for part in parts):
        raise UnsafeMetadata("protected reference component length is invalid")
    for component in parts:
        # Check secret shapes before syntax rejection so the same sanitizer
        # protects identifiers in both lineage versions and path components.
        if _secret_present(component):
            raise UnsafeMetadata("forbidden content in protected reference")
        if (component.endswith((".", " ")) or RESERVED_COMPONENT.fullmatch(component) or
                any(char in component for char in '<>"|?*:')):
            raise UnsafeMetadata("ambiguous protected reference component")
        if (PATIENT_ID.search(component) or
                SYNTHETIC.search(component) or _has_forbidden_name_token(component) or
                LOCAL_PATH.search(component)):
            raise UnsafeMetadata("forbidden content in protected reference")
    return canonical


def _validate_lineage(lineage):
    if not isinstance(lineage, dict) or set(lineage) != set(LINEAGE_FIELDS):
        raise UnsafeMetadata("lineage fields must exactly match the approved schema")
    result = {}
    for field, kind in LINEAGE_FIELDS.items():
        value = lineage[field]
        if kind == "version":
            if not isinstance(value, str):
                raise UnsafeMetadata("invalid version field: " + field)
            _clean_text(value, field)
            if not VERSION.fullmatch(value):
                raise UnsafeMetadata("invalid version field: " + field)
        elif kind == "sha256":
            if not isinstance(value, str) or not SHA256.fullmatch(value):
                raise UnsafeMetadata("invalid fingerprint field: " + field)
        elif kind == "workspace_reference":
            value = _canonical_protected_reference(value)
        elif kind == "metrics":
            if not isinstance(value, dict) or not value or set(value) - METRIC_NAMES:
                raise UnsafeMetadata("evaluation metrics must use approved aggregate names")
            canonical_metrics = {}
            for name, metric in value.items():
                if isinstance(metric, bool) or not isinstance(metric, (int, float)):
                    raise UnsafeMetadata("aggregate metrics must be numeric")
                try:
                    numeric = float(metric)
                except (OverflowError, ValueError):
                    raise UnsafeMetadata("aggregate metric is not serializable")
                if not math.isfinite(numeric):
                    raise UnsafeMetadata("aggregate metrics must be finite")
                if name == "latency_ms":
                    if numeric < 0 or numeric > MAX_LATENCY_MS:
                        raise UnsafeMetadata("latency metric is outside its bound")
                elif name in COUNT_METRICS:
                    if not numeric.is_integer() or numeric < 0 or numeric > 1_000_000:
                        raise UnsafeMetadata("count metric is outside its bound")
                elif name in SCORE_METRICS and (numeric < 0 or numeric > 1):
                    raise UnsafeMetadata("score metric must be between zero and one")
                rendered = format(numeric, ".12g")
                if len(rendered) > MAX_METRIC_LENGTH:
                    raise UnsafeMetadata("aggregate metric serialization is too long")
                canonical_metrics[name] = rendered
            result[field] = {name: canonical_metrics[name] for name in sorted(canonical_metrics)}
            continue
        result[field] = value
    return result


def _validate_hyperparameters(parameters):
    if not isinstance(parameters, dict) or set(parameters) - set(HYPERPARAMETERS):
        raise UnsafeMetadata("hyperparameters contain an unsupported field")
    cleaned = {}
    for name, value in parameters.items():
        types = HYPERPARAMETERS[name]
        if isinstance(value, bool) or not isinstance(value, types):
            raise UnsafeMetadata("hyperparameters must be scalar numbers")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise UnsafeMetadata("hyperparameter must be finite")
        if name in ("batch_size", "epochs") and (value <= 0 or value > 1_000_000):
            raise UnsafeMetadata("integer hyperparameter is outside its bound")
        if name in ("learning_rate", "weight_decay") and (value < 0 or value > 1_000_000):
            raise UnsafeMetadata("numeric hyperparameter is outside its bound")
        cleaned[name] = value
    return cleaned


def build_metadata_tags(lineage, hyperparameters):
    """Return deterministic ClearML tags from approved fields only."""
    lineage = _validate_lineage(lineage)
    hyperparameters = _validate_hyperparameters(hyperparameters)
    tags = []
    for name, value in sorted(lineage.items()):
        if isinstance(value, dict):
            tags.extend("lineage:%s:%s=%s" % (name, key, value[key])
                        for key in sorted(value))
        else:
            tags.append("lineage:%s=%s" % (name, value))
    tags.extend("hyperparameter:%s=%s" % (name, hyperparameters[name])
                for name in sorted(hyperparameters))
    if any(len(tag) > 160 for tag in tags):
        raise UnsafeMetadata("serialized metadata tag is too long")
    for tag in tags:
        if (_secret_present(tag) or PATIENT_ID.search(tag) or
                SYNTHETIC.search(tag) or FORBIDDEN_CONTENT.search(tag) or
                (LOCAL_PATH.search(tag) and not tag.startswith("lineage:protected_reference="))):
            raise UnsafeMetadata("forbidden serialized metadata")
    return tags


def build_task_payload(project_id, lineage, hyperparameters):
    """Build a fixed task envelope with an internal name and no free text."""
    if not isinstance(project_id, str) or not SAFE_ID.fullmatch(project_id):
        raise UnsafeMetadata("invalid project identifier")
    return {
        "name": TASK_NAME,
        "type": "training",
        "project": project_id,
        "comment": "Controlled metadata only; automatic capture disabled.",
        "tags": build_metadata_tags(lineage, hyperparameters),
    }


BASE_READBACK_FIELDS = (
    "id", "name", "type", "project", "tags", "comment", "script",
    "execution", "output", "configuration", "hyperparams",
)
OPTIONAL_READBACK_FIELDS = ("runtime", "models")
SENSITIVE_READBACK_FIELDS = (
    "script", "execution", "output", "configuration", "hyperparams",
    "runtime", "models",
)
READBACK_FIELDS = frozenset(BASE_READBACK_FIELDS + OPTIONAL_READBACK_FIELDS)


def effectively_empty(value):
    """Treat recursively empty JSON shells as empty; numeric zero remains data."""
    if value is None or value is False or value == "":
        return True
    if isinstance(value, dict):
        return all(effectively_empty(item) for item in value.values())
    if isinstance(value, list):
        return all(effectively_empty(item) for item in value)
    return False


def _safe_binary_token(value):
    """Accept only a short interpreter token, never a path or free text."""
    return (isinstance(value, str) and
            re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", value) is not None and
            not _secret_present(value) and not PATIENT_ID.search(value) and
            not SYNTHETIC.search(value) and
            not _has_forbidden_name_token(value) and
            not FORBIDDEN_CONTENT.search(value))


def _empty_script(value):
    if effectively_empty(value):
        return True
    if not isinstance(value, dict):
        return False
    for field, item in value.items():
        if field == "binary":
            if not effectively_empty(item) and not _safe_binary_token(item):
                return False
        elif not effectively_empty(item):
            return False
    return True


def _empty_execution(value):
    if effectively_empty(value):
        return True
    if not isinstance(value, dict):
        return False
    for field, item in value.items():
        if field == "test_split":
            if not (type(item) in (int, float) and item == 0):
                return False
        elif field in ("artifacts", "dataviews", "model_desc",
                       "model_labels", "parameters"):
            if not effectively_empty(item):
                return False
        elif not effectively_empty(item):
            return False
    return True


def _field_is_empty(field, value):
    if field == "script":
        return _empty_script(value)
    if field == "execution":
        return _empty_execution(value)
    return effectively_empty(value)


def validate_readback(task, expected):
    """Check exact payload fields and reject any captured/sensitive content."""
    if not isinstance(task, dict):
        raise UnsafeMetadata("task readback is malformed")
    if any(field not in READBACK_FIELDS and not effectively_empty(value)
           for field, value in task.items()):
        raise UnsafeMetadata("task readback has an unknown nonempty field")
    if not SAFE_ID.fullmatch(task.get("id", "")):
        raise UnsafeMetadata("task readback identifier is invalid")
    for field in ("name", "type", "project", "comment"):
        if task.get(field) != expected.get(field):
            raise UnsafeMetadata("task readback mismatch: " + field)
    actual_tags = task.get("tags")
    expected_tags = expected.get("tags")
    if (not isinstance(actual_tags, list) or
            not all(isinstance(tag, str) for tag in actual_tags) or
            not isinstance(expected_tags, list) or
            sorted(actual_tags) != sorted(expected_tags)):
        raise UnsafeMetadata("task readback mismatch: tags")
    for field in SENSITIVE_READBACK_FIELDS:
        if not _field_is_empty(field, task.get(field)):
            raise UnsafeMetadata("task readback contains captured field: " + field)
    return True
