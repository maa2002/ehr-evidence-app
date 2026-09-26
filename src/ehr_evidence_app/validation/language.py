"""Wording rules for every outcome (work item 39).

The rules read a statement's wording, the words a writer chose around the
facts; the facts themselves are slots rendered from the evidence, so a
condition named "Pneumonia caused by a virus" never trips a rule. Every
statement of every released outcome is read: answers, refusals and
clarifying questions (and, from work item 38, explanations).

Rules version wording-rules-v1 prohibits (prohibited_wording):

* causal or directional attribution ("caused", "led to", "due to",
  "raised the risk", "risk factor");
* percentage contribution ("40%", "percent", "a share of");
* exclusivity ("the only diagnosis", "solely", "nothing else");
* clinical status, severity or course ("the patient has" as a fact,
  "severe", "resolved", "worsening");
* advice ("should be treated", "recommend", "start therapy");
* certainty ("definitely", "proves", "confirms");
* diagnosis ("diagnosed with", "consistent with", "likely has").

Causal and diagnostic words pass when a negation comes shortly before
them ("attribution does not mean causation", "this application does not
diagnose"). Dates, recurrence and frequency are not stated by QA answers
at all (date_unsupported); explanations may state them only with the
linked events the evidence claim policy allows (work item 38).

The rules are patterns: a sentence that says something prohibited in
words no pattern knows is not caught. Gaps found later become a new rules
version, measured again.

Usage:
    python -m ehr_evidence_app.validation.language
"""

from __future__ import annotations

import argparse
import re
import sys

from ehr_evidence_app.validation import candidate as candidates

RULES_VERSION = "wording-rules-v1"
# Stands in for a slot, so that no fact is read as wording.
SLOT = "X"
NEGATIONS = frozenset(("not", "no", "never", "nor", "without", "cannot",
                       "neither", "none"))
# How many words before a match a negation may stand.
NEGATION_WINDOW = 4
PROHIBITED = (
    ("causal", True, re.compile(
        r"\b(?:caus(?:e|es|ed|ing|al|ally|ation|ative)|led to|leads? to|"
        r"leading to|results? in|resulted in|resulting in|due to|because "
        r"of|owing to|responsible for|contribut(?:e|es|ed|ing) to|"
        r"trigger(?:s|ed|ing)?|(?:increas|rais|lower|reduc|decreas)(?:e|es|"
        r"ed|ing) the (?:risk|likelihood|chance|odds|probability)|risk "
        r"factors?|predispos\w*|explains? why)\b")),
    ("percentage", False, re.compile(
        r"(?:\b[0-9]+(?:\.[0-9]+)? ?%|%|\bper ?cent\w*|\bpercentage\w*|"
        r"\b(?:a|the) (?:share|proportion|fraction) of)")),
    ("exclusive", False, re.compile(
        r"\b(?:the only|sole|solely|exclusively|nothing else|no other "
        r"(?:diagnosis|diagnoses|condition|conditions|cause|causes|reason|"
        r"explanation|driver|drivers)|entirely|wholly|fully explains?)\b")),
    ("status", False, re.compile(
        r"\b(?:(?:the patient|this patient|he|she|they) (?:has|have|had|is "
        r"suffering|suffers|suffered|is ill)|severe|severity|mild|moderate|"
        r"worsen\w*|improv(?:e|es|ed|ing)|resolved|resolving|stable|unstable|"
        r"critical|fatal|terminal|curable|incurable|in remission|"
        r"life ?threatening)\b")),
    ("advice", False, re.compile(
        r"\b(?:should|must|ought to|recommend\w*|advis(?:e|es|ed|able)|"
        r"prescrib\w*|(?:start|stop|begin|continue|change) (?:the )?"
        r"(?:treatment|therapy|medication|drug)|(?:needs?|requires?) "
        r"(?:treatment|therapy|medication|care))\b")),
    ("certainty", False, re.compile(
        r"\b(?:definitely|certainly|undoubtedly|conclusively|definitive\w*|"
        r"proves?|proven|confirms?|confirmed|guarantee\w*|without (?:a )?"
        r"doubt|for sure)\b")),
    ("diagnosis", True, re.compile(
        r"\b(?:diagnosed with|(?:is|was|been) diagnosed|diagnose[sd]? "
        r"(?:the patient|him|her|them)|we diagnose|diagnose\b|consistent "
        r"with|suggestive of|indicative of|likely has|probably has|may have|"
        r"might have|could have)\b")),
)
DATES = re.compile(
    r"\b(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}(?:/[0-9]{2,4})?|"
    r"(?:19|20|21)[0-9]{2}|january|february|march|april|june|july|august|"
    r"september|october|november|december|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday|yesterday|today|tomorrow|ago|"
    r"recur\w*|repeated\w*|again|twice|thrice|[0-9]+ times|frequent\w*|"
    r"often|regularly|every (?:day|week|month|year|visit|admission)|"
    r"(?:daily|weekly|monthly|yearly|annually)|persistent\w*|"
    r"progress\w*)\b")


def neutral(wording):
    """Return a wording in lower case with every slot replaced by SLOT."""
    return candidates.PLACEHOLDER.sub(SLOT, wording).casefold()


def negated(text, start):
    """Return True when a negation stands shortly before a position."""
    words = re.findall(r"[a-z']+", text[:start])
    return any(word.split("'")[0] in NEGATIONS or word.endswith("n't")
               for word in words[-NEGATION_WINDOW:])


def findings(wording):
    """Return (reason code, rule) pairs a wording breaks, in rule order."""
    text = neutral(wording)
    found = []
    for rule, pardonable, pattern in PROHIBITED:
        for match in pattern.finditer(text):
            if pardonable and negated(text, match.start()):
                continue
            found.append(("prohibited_wording", rule))
            break
    if DATES.search(text):
        found.append(("date_unsupported", "dates"))
    return found


def check(candidate):
    """Return the reason codes of the wording rules a candidate's
    statements break; empty when it passes."""
    return tuple(sorted({code for statement in candidate.statements
                         for code, _ in findings(statement.wording)}))


# --------------------------------------------------------------------------
# Synthetic default mode (invented wordings only)
# --------------------------------------------------------------------------

# (wording, expected reason codes)
SYNTHETIC_WORDINGS = (
    ("{condition} is recorded within {scope}: {matches}.", ()),
    ("{condition} ({count} in {visits}; matched as {match})", ()),
    ("Attribution does not mean causation.", ()),
    ("This application does not diagnose. It reports what is recorded.", ()),
    ("{condition} caused the admission.", ("prohibited_wording",)),
    ("{condition} accounts for 40% of the prediction.",
     ("prohibited_wording",)),
    ("{condition} is the only diagnosis within {scope}.",
     ("prohibited_wording",)),
    ("The patient has {condition}.", ("prohibited_wording",)),
    ("{condition} should be treated.", ("prohibited_wording",)),
    ("{condition} is definitely recorded.", ("prohibited_wording",)),
    ("{condition} is consistent with sepsis.", ("prohibited_wording",)),
    ("{condition} was recorded again in 2150.", ("date_unsupported",)),
    ("{condition} recurs within {scope}.", ("date_unsupported",)),
)


def run_synthetic():
    """Return (correct, total) over the invented wordings and every
    approved notice, which must all pass."""
    cases = list(SYNTHETIC_WORDINGS) + [
        (notice.text, ()) for notice in candidates.NOTICES.values()]
    correct = sum(1 for wording, expected in cases
                  if tuple(sorted({code for code, _ in findings(wording)}))
                  == expected)
    return correct, len(cases)


def main(argv=None):
    """Check invented wordings and the approved notices."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    correct, total = run_synthetic()
    print("rules %s: wordings read as expected: %d of %d" % (
        RULES_VERSION, correct, total))
    print("RESULT: %s" % ("PASS" if correct == total else "FAIL"))
    return 0 if correct == total else 1


if __name__ == "__main__":
    sys.exit(main())
