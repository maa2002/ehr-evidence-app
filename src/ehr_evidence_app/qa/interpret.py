"""Question interpretation (work item 31).

A closed, rule-based interpreter for one question about the active case.
The same question always gets the same result, and no model is involved.
The question is normalised, then checked in this order:

1. requests outside the supported tasks end the question: an explanation of
   a prediction (clarification: select a prediction in the explanation
   workflow), a request about another patient (clarification: select that
   case), treatment, prognosis or risk, a diagnosis to be made, and other
   data types such as medications, laboratory results or procedures
   (abstention);
2. a presence frame ("is X recorded", "does the patient have X", "any
   history of X", "check whether ...") gives a presence check and the
   condition phrase X;
3. a list frame ("list the conditions", "what diagnoses are recorded",
   "what has the patient been diagnosed with") gives a list;
4. a question with both, or neither, gets a clarification.

Scope phrases are found by qa.scope and removed from the condition phrase.
Wording such as "does the patient have" is read as "is it recorded"; an
answer never states whether the patient has a condition.
"""

from __future__ import annotations

import dataclasses
import re

from ehr_evidence_app.qa import scope as scopes

RULES_VERSION = "question-interpretation-v1"
CONTRACTIONS = {"what's": "what is", "isn't": "is not", "wasn't": "was not",
                "doesn't": "does not", "didn't": "did not",
                "hasn't": "has not", "haven't": "have not",
                "aren't": "are not", "weren't": "were not",
                "there's": "there is", "who's": "who is"}
SUBJECT = (r"(?:the patient|this patient|that patient|patient|the person|"
           r"this person|he|she|they|him|her|them|pt)")
CONDITIONS = (r"(?:conditions|diagnoses|diagnosis|problems|diseases|"
              r"illnesses|comorbidities|disorders|medical problems|"
              r"medical conditions|health problems|health conditions)")
RECORDED = (r"(?:recorded|documented|coded|noted|listed|present|mentioned|"
            r"captured|reported|on record|on file|in the record|in the "
            r"records|in the chart|in (?:his|her|their|the patient) "
            r"(?:record|records|chart|history)|diagnosed)")

UNSUPPORTED = (
    ("clarify", "prediction_selection_needed", "explanation", re.compile(
        r"\b(?:why (?:did|does|was|is|would) the model|the model(?: s)? "
        r"(?:predict\w*|output|score)|explain (?:the|this|that|a) "
        r"prediction|predicted diagnos\w*|prediction)\b")),
    ("clarify", "case_ambiguous", "other_patient", re.compile(
        r"\b(?:(?:patient|case|subject|person) (?:number |no )?[0-9]+|"
        r"(?:another|other|different|next|previous|last) "
        r"(?:patient|case|person|subject))\b")),
    ("abstain", "task_unsupported", "treatment", re.compile(
        r"\b(?:treat|treated|treating|treatment|treatments|prescribe|"
        r"therapy|therapies|dose|"
        r"dosage|dosing|manage|management|recommend\w*|should (?:we|i|they|"
        r"you|he|she) (?:give|start|stop|use|do|order|try))\b")),
    ("abstain", "task_unsupported", "prognosis", re.compile(
        r"\b(?:will (?:he|she|they|the patient|this patient)|risk of|"
        r"at risk|chance|chances|likely|likelihood|prognosis|survive|"
        r"survival|die|dying|mortality|expected to|going to)\b")),
    ("abstain", "task_unsupported", "diagnosis_request", re.compile(
        r"\b(?:diagnose|differential|what (?:could|might|may) "
        r"(?:he|she|they|the patient|this patient) have|what is wrong with|"
        r"what s wrong with)\b")),
    ("abstain", "task_unsupported", "other_data", re.compile(
        r"\b(?:medications?|medicines?|drugs?|prescriptions?|labs?|"
        r"laboratory|test results?|lab results?|procedures?|surgeries|"
        r"operations?|vitals?|vital signs?|blood pressure (?:readings?|"
        r"values?|measurements?)|heart rate|imaging|x rays?|scans?|"
        r"vaccin\w*|immuni[sz]ations?|notes?|discharge summar\w*)\b")),
)
# Data words that are part of a condition name ("drug abuse", "adverse drug
# reaction", "allergy to medication") do not make a question about other
# data; they are blanked before the other-data check.
DATA_CONDITIONS = re.compile(
    r"\b(?:(?:adverse|allergy to|allergic to|reaction to|intolerance to|"
    r"sensitivity to) (?:drugs?|medications?|medicines?|vaccin\w*)|"
    r"(?:drugs?|medications?|medicines?|vaccin\w*|procedures?|surgery|"
    r"surgical|operations?) (?:abuse|dependence|use|induced|allergy|"
    r"allergies|reactions?|overdose|toxicity|poisoning|intoxication|"
    r"withdrawal|addiction|misuse|overuse|eruption|rash|fever|"
    r"complications?|resistant|resistance|seeking|seeker))\b")

WRAPPER = re.compile(
    r"^(?:can you |could you |please |would you )?(?:check|find out|tell me|"
    r"confirm|verify|see|determine|show me|let me know) (?:if|whether) "
    r"(?P<rest>.+)$")
FILTERED_LIST = re.compile(
    r"^(?:which|what) (?:types of |kinds of |forms of )?(?P<term>.+?) "
    r"%s (?:are|were|have been|has been|is|was) (?:recorded|documented|"
    r"coded|noted|listed)\b" % CONDITIONS)
PRESENCE = (
    re.compile(
        r"^%s (?:has|had|have|got|gets) (?:ever |previously |already |"
        r"also )?(?:had |been diagnosed with |a "
        r"history of |any history of |a diagnosis of )?(?P<term>.+)$"
        % SUBJECT),
    re.compile(
        r"^(?!(?:what|which|how|who|when|where|why|list|show|give|tell)\b)"
        r"(?P<term>.+?) (?:is|was|are|were|has been|have been|had been) "
        r"(?:ever |previously |also |already )?%s\b.*$" % RECORDED),
    re.compile(
        r"^(?:is|was|are|were) there (?:any |a |an )?(?:record|records|"
        r"history|diagnosis|diagnoses|mention|evidence|documentation|sign|"
        r"signs|indication|code|codes) (?:of|for) (?P<term>.+)$"),
    re.compile(
        r"^(?:does|did|has|have|do) %s (?:ever |previously |already |also |"
        r"actually )?(?:have|had|has|got|get|suffer from|suffered from|"
        r"suffers from|been diagnosed with|diagnosed with|"
        r"a history of|any history of|history of|a diagnosis of|a record "
        r"of|any record of|carry a diagnosis of) (?P<term>.+)$" % SUBJECT),
    re.compile(
        r"^(?:is|are) %s (?:known to have |diagnosed with |a known )"
        r"(?P<term>.+)$" % SUBJECT),
    re.compile(
        r"^(?:is|was|are|were|has|have) (?:a |an |the |any )?(?:diagnosis "
        r"of |history of |record of )?(?P<term>.+?) (?:been |ever been |"
        r"ever )?%s\b(?P<tail>.*)$" % RECORDED),
    re.compile(
        r"^(?:any|some) (?:history of |record of |mention of |evidence of |"
        r"diagnosis of |documentation of )?(?P<term>.+)$"),
    re.compile(
        r"^(?:history|record|evidence|diagnosis|mention|documentation) of "
        r"(?P<term>.+)$"),
)
LIST = (
    re.compile(r"\b(?:list|enumerate|summari[sz]e|itemi[sz]e)\b"),
    re.compile(
        r"\b(?:what|which) (?:are |were |is |was )?(?:all )?(?:the |his |"
        r"her |their )?(?:recorded |documented |coded |known |past |"
        r"previous |prior |existing |current )?%s\b" % CONDITIONS),
    re.compile(
        r"\b%s (?:are |were |have been |has been )?(?:recorded|documented|"
        r"coded|listed|noted|on file|in the record)\b" % CONDITIONS),
    re.compile(
        r"\b(?:show|give|tell) (?:me )?(?:the |all |all the |a list of |"
        r"his |her |their )?(?:recorded |documented )?(?:%s|medical history|"
        r"past medical history)\b" % CONDITIONS),
    re.compile(
        r"^what (?:has|have|did|does|do) %s (?:been diagnosed with|had|"
        r"have|suffer from|suffered from)$" % SUBJECT),
    re.compile(r"^(?:what is |what s )?(?:the |his |her |their )?"
               r"(?:past )?medical history$"),
    re.compile(r"\bhow many %s\b" % CONDITIONS),
)
LEADING = re.compile(
    r"^(?:(?:a|an|the|any|some|known|previous|prior|past|recorded|"
    r"patient|patients|this patient|his|her|their|its|"
    r"documented|diagnosed|confirmed|history of|diagnosis of|diagnoses of|"
    r"evidence of|mention of|record of|records of|documentation of|"
    r"condition of|disease of|of|with|for) )+")
TRAILING = re.compile(
    r"(?: (?:recorded|documented|coded|noted|listed|present|mentioned|"
    r"diagnosed|reported|captured|before|previously|ever|at any point|"
    r"at any time|in the past|at all|also|too|either|on record|on file|"
    r"in the record|in the records|in the chart|in (?:his|her|their|the "
    r"patient) (?:record|records|chart|history)|in the history|history|"
    r"then|now|here|please|in|during|at|for|on|within|from|of|by|to))+$")
QUESTION_START = re.compile(
    r"^(?:what|which|who|whom|whose|when|where|why|how|tell|show|give|"
    r"explain|describe|is|are|was|were|does|did|do|can|could|would|"
    r"should|will|please|list|find|check)\b")
CLAUSES = re.compile(r"\b(?:and|also|then|plus|as well as)\b")
MAX_BARE_WORDS = 6
# A condition named only by a pronoun refers to earlier conversation, which
# questions never rely on.
PRONOUNS = frozenset(("it", "this", "that", "these", "those", "them", "one",
                      "they", "the same", "the condition", "the diagnosis",
                      "this condition", "that condition", "this diagnosis",
                      "that diagnosis", "the disease"))


@dataclasses.dataclass(frozen=True)
class Interpretation:
    """The interpretation of one question."""

    status: str
    task: str | None = None
    term: str | None = None
    mentions: tuple = ()
    reason: str | None = None
    category: str | None = None
    text: str = ""


def normalize(question):
    """Return the question in lower case with plain spacing and words.

    Dots inside codes (I50.9) are kept; every other symbol becomes a space.
    """
    text = (question or "").replace("’", "'").replace("‘", "'")
    text = text.casefold()
    for short, full in CONTRACTIONS.items():
        text = re.sub(r"\b%s\b" % re.escape(short), full, text)
    text = re.sub(r"'s\b", "", text)
    text = re.sub(r"(?<=[0-9a-z])\.(?=[0-9a-z])", "\x00", text)
    text = re.sub(r"[^0-9a-z\x00]+", " ", text).replace("\x00", ".")
    return " ".join(text.split())


def clean_term(term):
    """Strip question words that are not part of the condition phrase."""
    term = " ".join(term.split())
    previous = None
    while previous != term:
        previous = term
        term = LEADING.sub("", term).strip()
        term = TRAILING.sub("", term).strip()
    return term


def presence_term(text):
    """Return the condition phrase of a presence frame, or None."""
    wrapped = WRAPPER.match(text)
    if wrapped:
        return presence_term(wrapped.group("rest")) or None
    for pattern in PRESENCE:
        match = pattern.match(text)
        if match:
            return clean_term(match.group("term"))
    return None


def is_list(text):
    """Return True when the question asks for the list of conditions."""
    return any(pattern.search(text) for pattern in LIST)


def interpret(question):
    """Interpret one question about the active case."""
    text = normalize(question)
    found = scopes.mentions(text)
    if not text:
        return Interpretation("clarify", reason="task_ambiguous",
                              category="empty", text=text)
    for status, reason, category, pattern in UNSUPPORTED:
        checked = (DATA_CONDITIONS.sub(" ", text) if category == "other_data"
                   else text)
        if pattern.search(checked):
            return Interpretation(status, reason=reason, category=category,
                                  mentions=found, text=text)
    body = scopes.remove(text, found)
    filtered = FILTERED_LIST.match(body)
    term = (clean_term(filtered.group("term")) if filtered
            else presence_term(body))
    listing = is_list(body) and not filtered
    clauses = [part.strip() for part in CLAUSES.split(body)
               if part.strip()]
    if len(clauses) > 1 and any(is_list(part) for part in clauses) and \
            any(presence_term(part) for part in clauses):
        listing, term = True, term or ""
    if term is not None and listing:
        return Interpretation("clarify", reason="task_ambiguous",
                              category="two_tasks", mentions=found,
                              text=text)
    if listing:
        return Interpretation("task", task="condition_list", mentions=found,
                              text=text)
    if (term is None and body and len(body.split()) <= MAX_BARE_WORDS
            and not QUESTION_START.match(body)):
        term = clean_term(body)
    if term is None:
        return Interpretation("clarify", reason="task_ambiguous",
                              category="unrecognised", mentions=found,
                              text=text)
    if not term or term in PRONOUNS:
        return Interpretation("clarify", reason="concept_unknown",
                              category="missing_condition", mentions=found,
                              text=text)
    return Interpretation("task", task="condition_presence", term=term,
                          mentions=found, text=text)
