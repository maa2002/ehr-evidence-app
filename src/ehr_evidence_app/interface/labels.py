"""What the interface calls things (Phase 8, decisions D4 to D6).

Plain functions from the API's values to the words the page shows: the
outcome of a response and its tone, the scope it answered, who wrote it,
the model's state, a score, the values of the evidence and the refusals.
Labels, names and headings are in title case; sentences are in sentence
case. A score is shown as the number it is, never as a percentage; an
attribution score is always labelled unsigned and not causal. The page
shows every word as text, never as markup.
"""

from __future__ import annotations

import re

OUTCOMES = {"answer": "Answer", "not_recorded": "Not Recorded",
            "explanation": "Explanation",
            "clarification": "Clarification Needed",
            "abstention": "Abstained", "case_rejection": "Case Rejected",
            "validation_rejection": "Withheld by Validation",
            "operational_error": "Operational Error"}
# The tone of each outcome's badge: released (good), asked back (info),
# declined (neutral), refused (warn), and withheld or failed (bad).
TONES = {"answer": "good", "not_recorded": "good", "explanation": "good",
         "clarification": "info", "abstention": "neutral",
         "case_rejection": "warn", "validation_rejection": "bad",
         "operational_error": "bad"}
# Why the template wrote a response instead of the model.
FALLBACKS = {"no_model": "Model Not Loaded",
             "busy": "Model Busy",
             "prompt_over_budget": "Evidence Over the Prompt Limit",
             "output_over_budget": "Response Over the Output Limit",
             "truncated": "Model Reply Cut Off",
             "timeout": "Model Timed Out",
             "resource_exhausted": "Model Out of Memory",
             "backend_failed": "Model Failed",
             "malformed": "Model Reply Unreadable",
             "schema_violated": "Model Reply Off Schema",
             "gate_rejected": "Model Reply Withheld by Validation"}
# The model's state: a short name and what it means for the responses.
MODEL_STATES = {"ready": ("Model Ready", "The model writes each response."),
                "loading": ("Model Loading", "Templates answer until the "
                                             "model is ready."),
                "unavailable": ("Templates Only", "The model is "
                                                  "unavailable, so templates "
                                                  "answer."),
                "off": ("Templates Only", "The model is off, so templates "
                                          "answer.")}
# The scopes: how a menu names each, and what it holds.
SCOPES = {"pre_target_history": ("Prior History",
                                 "Visits before the final admission"),
          "case_visits": ("All Visits", "Every visit, the final admission "
                                        "included")}
COVERAGE = {"complete": ("Complete", "Every visit, condition, procedure and "
                                     "medication record of this patient in "
                                     "the source snapshot is included. A "
                                     "missing record means not recorded, "
                                     "not clinically absent."),
            "complete_empty": ("Complete, No Records", "The records were "
                                                       "read in full, and "
                                                       "this scope holds "
                                                       "none."),
            "unavailable": ("Unavailable", "The records of this scope could "
                                           "not be read."),
            "failed": ("Failed", "Reading the records of this scope "
                                 "failed.")}
# How the evidence's values are shown.
VALUES = {"drug": "Medication", "pre_target": "Prior",
          "final_admission": "Final Admission",
          "target_only": "Final Admission Only",
          "target_and_earlier": "Final Admission and Earlier",
          "earlier_only": "Earlier Only", "not_attached": "Not Attached",
          "not_a_patient_node": "Not a Patient Node",
          "no_edges": "No Edges", "partial": "Partial"}
ERRORS = {"api_not_configured": "The interface was started without the "
                                "API's address. Start the application with "
                                "the launcher.",
          "api_not_local": "The API must run on this machine.",
          "api_unreachable": "The API isn't answering. It may still be "
                             "starting, or it has stopped.",
          "case_set_not_served": "This cohort isn't available.",
          "case_unknown": "This patient isn't among the admitted cases.",
          "scope_unknown": "This patient has no such scope.",
          "scope_invalid": "That isn't a scope a question can be asked in.",
          "prediction_missing": "This patient has no predicted diagnosis.",
          "request_invalid": "The request wasn't understood.",
          "request_media": "The request wasn't understood.",
          "request_too_large": "The question is too long.",
          "too_many_requests": "Too many requests are running. Ask again "
                               "when one has finished.",
          "job_unknown": "This request has expired. Ask again.",
          "internal": "Something went wrong in the interface."}
UNSIGNED = ("Attribution scores are unsigned: they show how sensitive the "
            "prediction was to each input, not whether an input raised or "
            "lowered it, and not that it caused it.")
NOT_A_RISK = "uncalibrated sigmoid of the logit; not a clinical risk"
ONE_OUTPUT = ("One output of a multi-label prediction, made in advance by "
              "the diagnosis model.")
GENERATED = ("The explanation below is written from this patient's evidence "
             "when asked.")
# Short joining words a title keeps in lower case, inside a name.
SMALL_WORDS = frozenset(("a", "an", "and", "as", "at", "but", "by", "for",
                         "in", "nor", "of", "on", "or", "per", "the", "to",
                         "via", "vs"))
WORD = re.compile(r"^([^A-Za-z0-9]*)([a-z]+(?:'[a-z]+)?)([^A-Za-z0-9]*)$")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep",
          "Oct", "Nov", "Dec")


def title(text):
    """Return a name or a label in title case: each word written in lower
    case gains a capital, but for short joining words inside it; a word
    holding a capital or a digit is kept as written (ST, pH, T2)."""
    if not text:
        return text
    found, start = [], True
    for token in text.split(" "):
        parts = re.split(r"([-/])", token)
        for index, part in enumerate(parts):
            match = WORD.match(part)
            if match and part not in "-/":
                lead, word, tail = match.groups()
                if start or "(" in lead or word not in SMALL_WORDS:
                    word = word[0].upper() + word[1:]
                parts[index] = lead + word + tail
            if part and part not in "-/":
                start = False
        found.append("".join(parts))
        start = token.endswith((":", ";"))
    return " ".join(found)


def outcome(value):
    """Return the name of a response's outcome."""
    return OUTCOMES.get(value, title(value.replace("_", " ")))


def tone(value):
    """Return the tone of an outcome's badge."""
    return TONES.get(value, "neutral")


def scope(value):
    """Return the name of a scope, as a label names it."""
    kind = value.get("kind")
    if kind == "selected_visit":
        return "Visit %d" % value["visit_number"]
    if kind in SCOPES:
        return SCOPES[kind][0]
    return title(kind.replace("_", " "))


def author(result):
    """Return who wrote a response: the model, or the template and why."""
    if result.get("author") == "model":
        return "By the Model"
    fallback = result.get("fallback")
    if fallback:
        return "By the Template · %s" % FALLBACKS.get(
            fallback, title(fallback.replace("_", " ")))
    return "By the Template"


def model(state):
    """Return the model's state: its short name and what it means."""
    return MODEL_STATES.get(state, (title(state.replace("_", " ")), ""))


def coverage(status):
    """Return a coverage state's name and what it means."""
    return COVERAGE.get(status, (title(status.replace("_", " ")), ""))


def reasons(codes):
    """Return the reason codes of a response as words."""
    return ", ".join(code.replace("_", " ") for code in codes)


def error(code):
    """Return what a refusal means."""
    return ERRORS.get(code, "The API refused the request (%s)."
                      % code.replace("_", " "))


def score(value):
    """Return a score or a logit to three significant figures, as a
    response shows it; never as a percentage."""
    return "%.3g" % value


def seconds(value):
    """Return the seconds a response took."""
    return "%.1f s" % value


def counted(number, noun):
    """Return a number with its noun, singular or plural, in title
    case."""
    return "%d %s%s" % (number, title(noun), "" if number == 1 else "s")


def words(value):
    """Return a code as a title-case label; nothing for no code."""
    if value is None:
        return ""
    return VALUES.get(value, title(str(value).replace("_", " ")))


def date(value):
    """Return an ISO date as a short date ("2 Jan 2100"); a value that is
    not one, as it is; nothing for no date."""
    if not value:
        return ""
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value))
    if not match or not 1 <= int(match.group(2)) <= 12:
        return str(value)
    return "%d %s %s" % (int(match.group(3)), MONTHS[int(match.group(2)) - 1],
                         match.group(1))


def references(response):
    """Return the references a response cites, in their first order."""
    found = []
    for claim in response.get("claims") or []:
        for ref in claim.get("refs") or []:
            if ref not in found:
                found.append(ref)
    return found
