"""What the interface calls things (Phase 8, decisions D4 to D6).

Plain functions from the API's values to the words the page shows: the
outcome of a response, the scope it answered, who wrote it, the model's
state, a score and the API's refusals. A score is shown as the number it
is, never as a percentage; an attribution score is always labelled
unsigned and not causal.
"""

from __future__ import annotations

OUTCOMES = {"answer": "Answer", "not_recorded": "Not recorded",
            "explanation": "Explanation",
            "clarification": "Clarification needed",
            "abstention": "Abstained", "case_rejection": "Case rejected",
            "validation_rejection": "Withheld by validation",
            "operational_error": "Operational error"}
# The badge colour of each outcome: released, asked back or refused, and
# withheld or failed.
TONES = {"answer": "green", "not_recorded": "green", "explanation": "green",
         "clarification": "blue", "abstention": "gray",
         "case_rejection": "orange", "validation_rejection": "red",
         "operational_error": "red"}
FALLBACKS = {"no_model": "the model had not loaded",
             "busy": "the model was writing another response",
             "prompt_over_budget": "the evidence was over the prompt limit",
             "output_over_budget": "the response was over the output limit",
             "truncated": "the model's reply reached the output limit",
             "timeout": "the model ran out of time",
             "resource_exhausted": "the model ran out of memory",
             "backend_failed": "the model failed",
             "malformed": "the model's reply was not a valid document",
             "schema_violated": "the model's reply broke its schema",
             "gate_rejected": "validation withheld the model's reply"}
MODEL_STATES = {"ready": "Ready",
                "loading": "Loading; the templates answer until it is ready",
                "unavailable": "Unavailable; the templates answer",
                "off": "Off; the templates answer"}
ERRORS = {"api_not_configured": "The interface was started without the "
                                "API's address; start the application with "
                                "the launcher.",
          "api_not_local": "The API must be on 127.0.0.1.",
          "api_unreachable": "The API is not answering. It may still be "
                             "starting, or it has stopped.",
          "case_set_not_served": "This case set is not served.",
          "case_unknown": "This case is not among the admitted cases.",
          "scope_unknown": "This case has no such scope.",
          "prediction_missing": "This case has no prepared prediction."}
UNSIGNED = ("Attribution scores are unsigned: they measure how sensitive "
            "the model's output was to each input, not whether a driver "
            "raised or lowered the prediction, and not that it caused it.")
NOT_A_RISK = "uncalibrated sigmoid of the logit; not a clinical risk"


def outcome(value):
    """Return the name of a response's outcome."""
    return OUTCOMES.get(value, value.replace("_", " "))


def tone(value):
    """Return the badge colour of an outcome."""
    return TONES.get(value, "gray")


def scope(value):
    """Return the name of a scope, as a sentence names it."""
    kind = value.get("kind")
    if kind == "pre_target_history":
        return "the pre-target history"
    if kind == "selected_visit":
        return "visit %d" % value["visit_number"]
    if kind == "case_visits":
        return "all case visits"
    return kind.replace("_", " ")


def scope_option(value):
    """Return the name of a scope as a menu lists it."""
    text = scope(value)
    return text[4:].capitalize() if text.startswith("the ") else (
        text.capitalize())


def author(result):
    """Return who wrote a response: the model, or the template and why."""
    if result.get("author") == "model":
        return "written by the model"
    fallback = result.get("fallback")
    if fallback:
        return "written by the template: %s" % FALLBACKS.get(
            fallback, fallback.replace("_", " "))
    return "written by the template"


def model(state):
    """Return what the model is doing."""
    return MODEL_STATES.get(state, state)


def reasons(codes):
    """Return the reason codes of a response as words."""
    return ", ".join(code.replace("_", " ") for code in codes)


def error(code):
    """Return what an API refusal means."""
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
    """Return a number with its noun, singular or plural."""
    return "%d %s%s" % (number, noun, "" if number == 1 else "s")


def references(response):
    """Return the references a response cites, in their first order."""
    found = []
    for claim in response.get("claims") or []:
        for ref in claim.get("refs") or []:
            if ref not in found:
                found.append(ref)
    return found


def escaped(text):
    """Return text with Markdown's special characters escaped, so that a
    question or a record's name shows as written."""
    special = "\\`*_{}[]()#+-.!|<>~$:"
    return "".join("\\" + char if char in special else char
                   for char in text)
