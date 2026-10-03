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

import decimal
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
          "not_found": "There is no such page or operation.",
          "method_not_allowed": "That operation isn't available here.",
          "internal": "Something went wrong in the interface."}
UNSIGNED = ("Attribution scores are unsigned: they show how sensitive the "
            "prediction was to each input, not whether an input raised or "
            "lowered it, and not that it caused it.")
NOT_A_RISK = "uncalibrated sigmoid of the logit; not a clinical risk"
ONE_OUTPUT = ("One output of a multi-label prediction, made in advance by "
              "the GNN model.")
# The explanation in plain words: what it is, what its score means, and
# how to read its parts. Each keeps the wording rules the responses keep
# (tests/interface/test_views.py).
EXPLANATION_INTRO = (
    "A GNN model that predicts diagnoses read this patient's record and "
    "predicted a diagnosis for the final admission. Shown here are that "
    "prediction and the recorded concepts its score was sensitive to, in "
    "order of their attribution scores. It is a model's output to inspect, "
    "not a diagnosis of the patient and not advice.")
# What the predicted diagnosis is, and where it applies; visit is the
# final admission's number and date, in brackets, when they are known.
TARGET = ("A prediction of a diagnosis recorded for the final admission"
          "{visit}: of the 275 diagnoses the GNN model scores, the one it "
          "scored highest. The model saw the visits before the final "
          "admission and that admission's other records, such as its "
          "procedures and medications, but not the diagnoses recorded for "
          "it.")
# Whether the predicted diagnosis is recorded for the final admission,
# from that visit's records (the page's record check).
RECORD_EXACT = ("Recorded for the final admission: {records} of this "
                "diagnosis in visit {visit}.")
RECORD_ICD = ("Recorded for the final admission under an ICD code whose "
              "description names it: {records} in visit {visit}.")
RECORD_MORE_SPECIFIC = ("Not recorded as such for the final admission, but "
                        "a more specific diagnosis is: {names}.")
RECORD_ABSENT = ("Not recorded for the final admission: no record of visit "
                 "{visit} is this diagnosis or a more specific one. This "
                 "means not recorded in that visit's records; it is not a "
                 "statement of clinical absence.")
RECORD_UNKNOWN = ("Whether it is recorded for the final admission cannot be "
                  "told: the records of visit {visit} are not complete.")
SCORE_PLAIN = ("Scores run from 0 to 1: the higher the score, the more the "
               "model favoured this diagnosis. It is not a probability.")
GLOSSARY = (
    ("Predicted Diagnosis",
     "The model's prediction of a diagnosis recorded for the final "
     "admission: of the 275 diagnoses it scores together, the one it "
     "scored highest. It is a prediction, not a diagnosis of the "
     "patient, and not necessarily the reason for the admission."),
    ("Score",
     "The model's output for this diagnosis, from 0 to 1 (the uncalibrated "
     "sigmoid of the logit). The higher it is, the more the model favoured "
     "the diagnosis; it is not a probability and not a clinical risk."),
    ("Logit", "The raw number the score is computed from."),
    ("Attribution Score",
     "How sensitive the model's score was to a recorded concept: the larger "
     "it is, the more a small change to that concept would have moved the "
     "score. It does not show whether the concept raised or lowered the "
     "score, and it does not show a cause."),
    ("History and Final-Admission Context",
     "Records from the visits before the final admission are history. "
     "Records of a concept that is also recorded in the final admission are "
     "shown as that admission's context, since the model saw the final "
     "admission apart from its diagnoses."),
    ("Left Out",
     "Diagnoses recorded only in the final admission are what the model "
     "predicts, so they are never shown as its evidence."),
    ("Sources",
     "P1 is the prediction, A1 and the like its attributions and E1 and the "
     "like the records: select one to see it in the evidence panel."))
# The help, shown when /help is typed: why questions follow set patterns,
# then what can be asked, kind by kind, with example questions that the
# rules read as that kind's question (tests/interface/test_views.py).
HELP_COMMAND = "/help"
HELP_TITLE = "What You Can Ask"
HELP_WHY_TITLE = "Why Questions Follow Set Patterns"
HELP_WHY = (
    "Each question is read by fixed rules, not by the language model, so "
    "that it is read the same way every time and every answer can be "
    "checked against the record before it is shown.",
    "The questions are not a fixed list: the rules read many wordings, "
    "abbreviations and names. But a question has to ask for one of the "
    "kinds of information below. Anything else gets a reply that says what "
    "was not understood, never a guess.")
HELP_SECTIONS = (
    ("Conditions", (
        ("Conditions Recorded", ("Which conditions are recorded?",)),
        ("Whether a Condition Is Recorded",
         ("Is essential hypertension recorded?",
          "Is type 2 diabetes mellitus recorded?")))),
    ("Medications", (
        ("Medications Given", ("What medications were given?",)),
        ("A Medication or a Drug Class",
         ("Was heparin given?", "Were any antibiotics given?")),
        ("When, How Often and What Dose",
         ("When was heparin given?", "How many times was heparin given?",
          "What doses of heparin were given?")))),
    ("Laboratory Results", (
        ("Tests Done", ("What labs were done?",)),
        ("One Result", ("What was the last potassium?",
                        "What was the highest creatinine?")),
        ("Every Result of a Test", ("What were all the potassium values?",)),
        ("Results Flagged Abnormal", ("Were any labs abnormal?",
                                      "Was the potassium high?")))),
    ("Vital Signs, Charted in the ICU", (
        ("One Value", ("What was the latest heart rate?",
                       "What was the highest temperature?")),
        ("Every Value", ("What were all the blood pressure values?",)))),
    ("Procedures", (
        ("Procedures Done", ("What procedures were done?",)),
        ("Whether One Was Done, and When",
         ("Was a chest x-ray done?", "When was the chest x-ray done?")))),
    ("Microbiology", (
        ("Cultures and What Grew", ("What microbiology was done?",
                                    "What organisms grew?",
                                    "Was E. coli found?")),)),
    ("Stays, Units and the ICU", (
        ("Hospital Stays", ("How many visits were there?",
                            "When was the patient admitted?",
                            "How long was each stay?",
                            "Where was the patient discharged to?")),
        ("Units and ICU Stays", ("Which units was the patient in?",
                                 "How many ICU stays were there?")))),
    ("The Patient", (
        ("Details Recorded", ("How old is the patient?",
                              "What is the patient's sex?",
                              "What language does the patient speak?",
                              "Did the patient die?")),)),
)
HELP_PREDICTION = (
    "The Predicted Diagnosis",
    "Press Generate the Predicted Diagnosis, below the box, for the GNN "
    "model's prediction of a diagnosis for the final admission and the "
    "recorded concepts its score was sensitive to.")
HELP_NOTES = (
    "Answers cover the part of the record chosen in the menu below the "
    "box: Prior History, All Visits or one visit.",
    "Other wordings work too, with abbreviations such as K, Hb or BP and "
    "everyday names of drug classes such as blood thinners or painkillers.",
    "Not answered: advice on what to do, the future, or what a medication "
    "was given for, which the record does not say.",
    "Select an example to put it in the box, change it as you like, and "
    "send it.")
# Under a response that asks back or declines; and the reply to a command
# that is not one, shown as a response is, with a badge.
HELP_HINT = "Type /help to see what you can ask."
HELP_UNKNOWN_LABEL = "Not a Command"
HELP_UNKNOWN = "The one command here is /help, which shows what you can ask."
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


def moment(value):
    """Return an ISO time as a short date and its hour ("2 Jan 2100
    08:00"), or a date alone; nothing for no time."""
    if not value:
        return ""
    shown = date(value)
    match = re.match(r"^\d{4}-\d{2}-\d{2}[ T](\d{2}:\d{2})", str(value))
    return "%s %s" % (shown, match.group(1)) if match else shown


def number(value):
    """Return a number as recorded: its shortest exact decimal form,
    without a needless ".0", rounding or an exponent."""
    if value is None:
        return ""
    value = float(value)
    if value.is_integer():
        return "%d" % value
    return format(decimal.Decimal(repr(value)), "f")


def references(response):
    """Return the references a response cites, in their first order."""
    found = []
    for claim in response.get("claims") or []:
        for ref in claim.get("refs") or []:
            if ref not in found:
                found.append(ref)
    return found
