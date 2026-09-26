"""Wording rules for every outcome (work item 39).

The rules read a statement's wording, the words a writer chose around the
facts; the facts themselves are slots rendered from the evidence, so a
condition named "Pneumonia caused by a virus" never trips a rule. Every
statement of every released outcome is read: answers, refusals and
clarifying questions (and, from work item 38, explanations).

Rules version wording-rules-v3 prohibits (prohibited_wording):

* causal or directional attribution ("caused", "led to", "due to",
  "raised the risk", "risk factor", "associated with", "secondary to");
* percentage contribution ("40%", "percent", "a share of");
* exclusivity ("the only diagnosis", "solely", "nothing else");
* clinical status, severity or course ("the patient has" as a fact,
  "severe", "resolved", "worsening", "chronic", "currently", "active");
* advice ("should be treated", "recommend", "start therapy");
* certainty ("definitely", "proves", "confirms", "clearly");
* diagnosis ("diagnosed with", "consistent with", "likely has",
  "suggests", "indicates", "a sign of");
* model performance ("AUROC", "accuracy"), which single-case
  predictions never state;
* in explanations, the model's score as a risk or probability, and a
  prediction read as prospective ("in advance", "forecast");
* in answers and explanations, hedged facts ("possibly", "may", "seems
  to"), rankings ("the main", "most important", "key") and counts or
  quantities in words ("three", "several", "most"): every fact is
  certain, unranked and stated by a slot.

Causal, diagnostic and risk words pass when a negation comes shortly
before them in the same clause ("attribution does not mean causation",
"this application does not diagnose", "not a clinical risk"). Version
1 also let a negation in an earlier clause pardon them ("X is not
recorded, caused by ..."); the mutation suite (validation.mutations)
found it. Version 3 adds the phrasings a language model writes around
facts and a template never does. Dates, recurrence, frequency and
recency are never typed into a wording (date_unsupported); an
explanation states how often and in which visits a driver's concept was
recorded only through the slots of a statement that cites its linked
events, as the evidence claim policy allows (validation.explanation).

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

RULES_VERSION = "wording-rules-v3"
# Stands in for a slot, so that no fact is read as wording.
SLOT = "X"
NEGATIONS = frozenset(("not", "no", "never", "nor", "without", "cannot",
                       "neither", "none"))
# How many words before a match a negation may stand, within its clause.
NEGATION_WINDOW = 4
CLAUSE_END = re.compile(r"[.,;:!?()]")
# (rule, pardoned by a negation, pattern, outcomes it applies to; None for
# every outcome)
# The outcomes that state recorded facts.
FACT_OUTCOMES = ("answer", "not_recorded", "explanation")
PROHIBITED = (
    ("causal", True, re.compile(
        r"\b(?:caus(?:e|es|ed|ing|al|ally|ation|ative)|led to|leads? to|"
        r"leading to|results? in|resulted in|resulting in|due to|because|"
        r"owing to|responsible for|contribut(?:e|es|ed|ing) to|"
        r"trigger(?:s|ed|ing)?|(?:increas|rais|lower|reduc|decreas)(?:e|es|"
        r"ed|ing) the (?:risk|likelihood|chance|odds|probability)|risk "
        r"factors?|predispos\w*|explains? why|associated with|secondary "
        r"to|complications? of|attributable to|driven by|as a "
        r"(?:result|consequence) of|consequen\w*)\b"), None),
    ("percentage", False, re.compile(
        r"(?:\b[0-9]+(?:\.[0-9]+)? ?%|%|\bper ?cent\w*|\bpercentage\w*|"
        r"\b(?:a|the) (?:share|proportion|fraction) of)"), None),
    ("exclusive", False, re.compile(
        r"\b(?:the only|sole|solely|exclusively|nothing else|no other "
        r"(?:diagnosis|diagnoses|condition|conditions|cause|causes|reason|"
        r"explanation|driver|drivers)|entirely|wholly|fully explains?)\b"),
     None),
    ("status", False, re.compile(
        r"\b(?:(?:the patient|this patient|he|she|they) (?:has|have|had|is "
        r"suffering|suffers|suffered|is ill)|severe|severity|mild|moderate|"
        r"worsen\w*|improv(?:e|es|ed|ing)|resolved|resolving|stable|unstable|"
        r"critical|fatal|terminal|curable|incurable|in remission|"
        r"life ?threatening|current(?:ly)?|ongoing|active|inactive|chronic|"
        r"acute|known|pre-?existing|underlying|newly|new-onset|"
        r"long-?standing|(?:un|well-|poorly )?controlled)\b"), None),
    ("advice", False, re.compile(
        r"\b(?:should|must|ought to|recommend\w*|advis(?:e|es|ed|able)|"
        r"prescrib\w*|(?:start|stop|begin|continue|change) (?:the )?"
        r"(?:treatment|therapy|medication|drug)|(?:needs?|requires?) "
        r"(?:treatment|therapy|medication|care))\b"), None),
    ("certainty", False, re.compile(
        r"\b(?:definitely|certainly|undoubtedly|conclusively|definitive\w*|"
        r"proves?|proven|confirms?|confirmed|guarantee\w*|without (?:a )?"
        r"doubt|for sure|clear(?:ly)?|obvious(?:ly)?|evident(?:ly)?|surely|"
        r"unquestionabl\w*|undeniabl\w*|established)\b"), None),
    ("performance", False, re.compile(
        r"\b(?:auroc|auc|roc|accuracy|accurate|f1 score|precision|recall|"
        r"sensitivity and specificity)\b"), None),
    ("diagnosis", True, re.compile(
        r"\b(?:diagnosed with|(?:is|was|been) diagnosed|diagnose[sd]? "
        r"(?:the patient|him|her|them)|we diagnose|diagnose\b|consistent "
        r"with|suggestive of|indicative of|likely has|probably has|may have|"
        r"might have|could have|suggest\w*|indicat(?:e|es|ed|ing|ion|ions|"
        r"ive)|points? to|in keeping with|compatible with|typical of|"
        r"characteristic of|signs? of|symptoms? of|manifestations? of)\b"),
     None),
    ("score_as_risk", True, re.compile(
        r"\b(?:risks?|likelihood|probabilit(?:y|ies)|chances?|odds)\b"),
     ("explanation",)),
    ("prospective", False, re.compile(
        r"\b(?:prospective\w*|in advance|ahead of time|beforehand|"
        r"forecast\w*|early warning|would have predicted|will develop|"
        r"going to develop)\b"), ("explanation",)),
    ("hedging", False, re.compile(
        r"\b(?:possibl[ey]|probabl[ey]|perhaps|maybe|likely|unlikely|"
        r"presumabl[ey]|apparent(?:ly)?|appears? to|seems? to|may|might|"
        r"could)\b"), FACT_OUTCOMES),
    ("ranking", False, re.compile(
        r"\b(?:main|mainly|primary|primarily|principal|chief|chiefly|most "
        r"(?:important|significant|influential|relevant|notable)|leading|"
        r"dominant|predominant\w*|major|key|strongest|biggest|largest|top|"
        r"mostly|largely)\b"), FACT_OUTCOMES),
    ("count_words", False, re.compile(
        r"\b(?:two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
        r"twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundreds?|"
        r"thousands?|dozens?|double|triple|half|quarter|several|many|few|"
        r"multiple|numerous|majority|minority|countless|most)\b"),
     FACT_OUTCOMES),
)
DATES = re.compile(
    r"\b(?:[0-9]{4}-[0-9]{2}-[0-9]{2}|[0-9]{1,2}/[0-9]{1,2}(?:/[0-9]{2,4})?|"
    r"(?:19|20|21)[0-9]{2}|january|february|march|april|june|july|august|"
    r"september|october|november|december|monday|tuesday|wednesday|"
    r"thursday|friday|saturday|sunday|yesterday|today|tomorrow|ago|"
    r"recur\w*|repeated\w*|again|twice|thrice|[0-9]+ times|frequent\w*|"
    r"often|regularly|every (?:day|week|month|year|visit|admission)|"
    r"(?:daily|weekly|monthly|yearly|annually)|persistent\w*|"
    r"progress\w*|recent\w*|lately|latest|earliest|oldest|newest|"
    r"(?:first|last|previous|prior|next|earlier|later) (?:visits?|"
    r"admissions?|stays?|times?|years?|months?|weeks?))\b")


def neutral(wording):
    """Return a wording in lower case with every slot replaced by SLOT."""
    return candidates.PLACEHOLDER.sub(SLOT, wording).casefold()


def negated(text, start):
    """Return True when a negation stands shortly before a position, in
    the same clause."""
    clause = CLAUSE_END.split(text[:start])[-1]
    words = re.findall(r"[a-z']+", clause)
    return any(word.split("'")[0] in NEGATIONS or word.endswith("n't")
               for word in words[-NEGATION_WINDOW:])


def findings(wording, outcome=None):
    """Return (reason code, rule) pairs a wording breaks, in rule order,
    in a statement of an outcome (every rule when it is None)."""
    text = neutral(wording)
    found = []
    for rule, pardonable, pattern, outcomes in PROHIBITED:
        if outcomes is not None and outcome is not None and (
                outcome not in outcomes):
            continue
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
                         for code, _ in findings(statement.wording,
                                                 candidate.outcome)}))


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
    ("The score is not a clinical risk.", ()),
    ("{condition} is not recorded, caused by {scope}.",
     ("prohibited_wording",)),
    ("{label} has a risk of {score}.", ("prohibited_wording",)),
    ("The model forecasts {label}.", ("prohibited_wording",)),
    ("The model has an AUROC of 0.9.", ("prohibited_wording",)),
    # Version 3: what a language model writes around facts.
    ("{condition} is possibly recorded within {scope}.",
     ("prohibited_wording",)),
    ("{condition} is the main condition within {scope}.",
     ("prohibited_wording",)),
    ("{condition} is recorded in three visits: {visits}.",
     ("prohibited_wording",)),
    ("{condition} suggests heart disease.", ("prohibited_wording",)),
    ("{condition} is chronic and currently active.",
     ("prohibited_wording",)),
    ("{condition} is clearly recorded.", ("prohibited_wording",)),
    ("{driver} is associated with {label}.", ("prohibited_wording",)),
    ("{condition} was recorded recently, in {visits}.",
     ("date_unsupported",)),
    ("{condition}: {count} in {visits}, in the last admission.",
     ("date_unsupported",)),
    ("{condition} does not suggest a diagnosis.", ()),
    ("{driver}: {count} in {visits}, before the final admission", ()),
    ("{label} is the label the model selected, one output of its "
     "multi-label prediction.", ()),
)


def run_synthetic():
    """Return (correct, total) over the invented wordings and every
    approved notice, which must all pass."""
    cases = [(wording, expected, "explanation")
             for wording, expected in SYNTHETIC_WORDINGS] + [
        (notice.text, (), notice.outcomes[0])
        for notice in candidates.NOTICES.values()]
    correct = sum(1 for wording, expected, outcome in cases
                  if tuple(sorted({code for code, _ in findings(
                      wording, outcome)})) == expected)
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
