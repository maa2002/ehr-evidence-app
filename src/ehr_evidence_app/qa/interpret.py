"""Question interpretation (work item 31).

A closed, rule-based interpreter for one question about the active case.
The same question always gets the same result, and no model is involved.
The question is normalised and its scope phrases are set aside (qa.scope);
then:

1. a presence frame ("is X recorded", "does the patient have X", "any
   history of X", "check whether ...") gives a presence check and the
   condition phrase X; a list frame ("list the conditions", "what diagnoses
   are recorded", "what has the patient been diagnosed with") gives a list;
   a short phrase on its own is a presence check;
2. the words outside the condition phrase are checked for requests outside
   the supported tasks: an explanation of a prediction (clarification:
   select a prediction in the explanation workflow), another patient
   (clarification: select that case), treatment, prognosis or risk, a
   diagnosis to be made, other data types such as medications, laboratory
   results or procedures, dates, times, order or counts, and a ranking
   of conditions (abstention; the scope selector chooses the visits).
   Inside the condition
   phrase such words belong to condition names ("drug-induced", "cardiac
   procedure", "other person"), so the rule found there is kept with the
   interpretation and applied by the answer step (qa.answer) only when the
   phrase names no condition;
3. a question with both tasks, in one sentence or in two, or with
   neither, gets a clarification.

The condition phrase comes with its candidates, longest first: the phrase
as asked, then with one question word at a time removed from its start
("a", "the", "history of", "ICD code") or its end ("recorded", "in the
chart", "present"). The answer step resolves the longest candidate that
names something, so a name such as "Cough reflex present" stays whole.
Wording such as "does the patient have" is read as "is it recorded"; an
answer never states whether the patient has a condition.
"""

from __future__ import annotations

import dataclasses
import re
import unicodedata

from ehr_evidence_app.qa import scope as scopes

RULES_VERSION = "question-interpretation-v2"
CONTRACTIONS = {"what's": "what is", "isn't": "is not", "wasn't": "was not",
                "doesn't": "does not", "didn't": "did not",
                "hasn't": "has not", "haven't": "have not",
                "aren't": "are not", "weren't": "were not",
                "there's": "there is", "who's": "who is"}
CONTRACTION = re.compile(r"\b(?:%s)\b" % "|".join(
    re.escape(short) for short in CONTRACTIONS))
SUBJECT = (r"(?:the patient|this patient|that patient|patient|the person|"
           r"this person|he|she|they|him|her|them|pt)")
CONDITIONS = (r"(?:conditions|diagnoses|diagnosis|problems|diseases|"
              r"illnesses|comorbidities|disorders|medical problems|"
              r"medical conditions|health problems|health conditions)")
RECORDED = (r"(?:recorded|documented|coded|noted|listed|present|mentioned|"
            r"captured|reported|on record|on file|in the record|in the "
            r"records|in the chart|in (?:his|her|their|the patient) "
            r"(?:record|records|chart|history)|diagnosed|included|"
            r"among (?:them|those)|on (?:the|that|this|his|her|their) "
            r"(?:problem )?list|in (?:the|that|this) list)")

UNSUPPORTED = (
    ("clarify", "prediction_selection_needed", "explanation", re.compile(
        r"\b(?:why (?:did|does|was|is|would) the model|the model(?: s)? "
        r"(?:predict\w*|output|score)|explain (?:the|this|that|a) "
        r"prediction|predicted diagnos\w*|prediction)\b")),
    ("clarify", "case_ambiguous", "other_patient", re.compile(
        r"\b(?:(?:patient|case|subject|person) (?:number |no )?[0-9]+|"
        r"(?:another|other|different|next|previous|last) "
        r"(?:patient|case|person|subject)s?|someone else|somebody else|"
        r"(?:bed|room) (?:number |no )?[0-9]+|mrn|medical record "
        r"number)\b")),
    ("abstain", "task_unsupported", "treatment", re.compile(
        r"\b(?:treat|treated|treating|treatment|treatments|prescribe|"
        r"therapy|therapies|dose|"
        r"dosage|dosing|manage|management|recommend\w*|should (?:we|i|they|"
        r"you|he|she) (?:give|start|stop|use|do|order|try))\b")),
    ("abstain", "task_unsupported", "prognosis", re.compile(
        r"\b(?:will (?:he|she|they|the patient|this patient|his|her|"
        r"their)|get worse|getting worse|worsen\w*|progress\w*|"
        r"improve\w*|recover\w*|outlook|risk of|"
        r"at risk|chance|chances|likely|likelihood|prognosis|survive|"
        r"survival|die|dying|mortality|expected to|going to)\b")),
    ("abstain", "task_unsupported", "diagnosis_request", re.compile(
        r"\b(?:diagnose|differential|what (?:could|might|may) "
        r"(?:he|she|they|the patient|this patient) have|what is wrong with|"
        r"what s wrong with)\b")),
    ("abstain", "task_unsupported", "other_data", re.compile(
        r"\b(?:medications?|medicines?|drugs?|prescriptions?|labs?|"
        r"laboratory|test results?|lab results?|procedures?|surgery|"
        r"surgeries|undergo|undergoes|underwent|undergone|undergoing|"
        r"operations?|vitals?|vital signs?|blood pressure (?:readings?|"
        r"values?|measurements?)|heart rate|imaging|x rays?|scans?|"
        r"vaccin\w*|immuni[sz]ations?|notes?|discharge summar\w*)\b")),
    # Dates, times, order and counts: the scope selector chooses visits,
    # and answers make no dated or frequency claims.
    ("abstain", "task_unsupported", "time_or_count", re.compile(
        r"\b(?:(?:1[89]|2[0-2])[0-9]{2}|january|february|march|april|june|"
        r"july|august|september|october|november|december|yesterday|today|"
        r"tomorrow|tonight|recently|recent|lately|ago|since|after|until|"
        r"between|when|date|dates|dated|how often|how many times|frequency|"
        r"(?:last|past|previous|next|this|[0-9]+|one|two|three|four|five|"
        r"six|seven|eight|nine|ten|few|several) (?:hours?|days?|weeks?|"
        r"months?|years?|decades?)|twice|thrice|(?<!at least )once|"
        r"(?:[0-9]+|two|three|four|five|multiple|several|many) times|"
        r"more than|less than|fewer than|at least(?! once)|at most|"
        r"first time|last time)\b")),
    # A ranking of conditions (first, top three, main): recorded
    # conditions are not ranked in the evidence.
    ("abstain", "task_unsupported", "ranking", re.compile(
        r"\b(?:first|1st|top|primary|principal|main|leading|predominant|"
        r"most (?:important|significant|serious|severe|relevant)|"
        r"biggest)\b")),
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
# Words that make "which X conditions are recorded" a plain list.
LIST_WORDS = frozenset(("", "medical", "health", "all", "other", "existing",
                        "current", "active"))
PRESENCE = (
    re.compile(
        r"^%s (?:has|had|have|got|gets) (?:ever |previously |already |"
        r"also )?(?:had |been diagnosed with |a history of |any history of |"
        r"a diagnosis of )?(?P<term>.+)$" % SUBJECT),
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
        r"actually )?(?:(?:have|had|has|got|get)(?: (?:a |any )?(?:history|"
        r"diagnosis|record) of)?|suffer from|suffered from|suffers from|"
        r"been diagnosed with|diagnosed with|carry a diagnosis of) "
        r"(?P<term>.+)$" % SUBJECT),
    re.compile(
        r"^(?:is|are) %s (?:known to have |diagnosed with |a known )"
        r"(?P<term>.+)$" % SUBJECT),
    # The phrase runs to the last reporting word, so a name that contains
    # one ("Abnormal heart beat noted before labor") stays whole.
    re.compile(
        r"^(?:is|was|are|were|has|have) (?P<term>.+) %s\b.*$" % RECORDED),
    re.compile(
        r"^(?:any|some) (?:history of |record of |mention of |evidence of |"
        r"diagnosis of |documentation of )?(?P<term>.+)$"),
    re.compile(
        r"^(?:history|hx|pmh|record|evidence|diagnosis|mention|"
        r"documentation) of "
        r"(?P<term>.+)$"),
)
LIST = (
    # "list" as a request, not the noun ("her problem list", "on the list").
    re.compile(r"(?<!problem )(?<!diagnosis )(?<!the )(?<!a )(?<!on )"
               r"(?<!this )(?<!that )\b(?:list|enumerate|summari[sz]e|"
               r"itemi[sz]e)\b"),
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
    re.compile(r"\b(?:what|which|show|give|list|pull up|display|tell)\b"
               r".*\b(?:problem list|diagnosis list|pmh|list of "
               r"(?:(?:his|her|their|the|all|the patient) )?"
               r"(?:conditions|diagnoses|problems|illnesses|disorders|"
               r"diseases))\b"),
)
# A condition phrase that is only a word for conditions asks for the list.
LIST_PHRASE = re.compile(r"(?:%s|medical history|past medical history)"
                         % CONDITIONS)
LEADING = re.compile(
    r"^(?:a|an|the|any|some|there|known|previous|prior|past|recorded|"
    r"hx of|pmh of|past history of|past medical history of|"
    r"medical history of|"
    r"patient|patients|this patient|his|her|their|its|"
    r"documented|diagnosed|confirmed|history of|diagnosis of|diagnoses of|"
    r"evidence of|mention of|record of|records of|documentation of|"
    r"condition of|disease of|of|with|for|"
    r"(?:icd(?: ?(?:9|10))?(?: cm)? |diagnosis |diagnostic )?codes?|"
    r"icd(?: ?(?:9|10))?(?: cm)?) ")
TRAILING = re.compile(
    r" (?:recorded|documented|coded|noted|listed|present|mentioned|"
    r"diagnosed|reported|captured|before|previously|ever|been|at any point|"
    r"at any time|in the past|at all|also|too|either|on record|on file|"
    r"in the record|in the records|in the chart|in (?:his|her|their|the "
    r"patient) (?:record|records|chart|history)|in the history|history|"
    r"then|now|here|please|in|during|at|for|on|within|from|of|by|to|and|"
    r"or|nor)$")
QUESTION_START = re.compile(
    r"^(?:what|which|who|whom|whose|when|where|why|how|tell|show|give|"
    r"explain|describe|is|are|was|were|does|did|do|can|could|would|"
    r"should|will|please|list|find|check|diagnose|treat|prescribe|"
    r"recommend|manage|predict|assess|evaluate)\b")
SENTENCES = re.compile(r"[.?!;]+(?:\s+|$)")
CLAUSES = re.compile(
    r"\b(?:and|also|then|plus|as well as|along with|in addition|"
    r"additionally|including whether|including if)\b")
MAX_BARE_WORDS = 6
# A condition named only by a pronoun refers to earlier conversation, which
# questions never rely on.
PRONOUNS = frozenset(("it", "this", "that", "these", "those", "them", "one",
                      "they", "the same", "the condition", "the diagnosis",
                      "this condition", "that condition", "this diagnosis",
                      "that diagnosis", "the disease"))


@dataclasses.dataclass(frozen=True)
class Interpretation:
    """The interpretation of one question.

    terms are the condition phrase's candidates, longest first; term is the
    last of them. guard is the (status, reason, category) of an
    out-of-scope rule matched only inside the condition phrase.
    """

    status: str
    task: str | None = None
    term: str | None = None
    terms: tuple = ()
    mentions: tuple = ()
    reason: str | None = None
    category: str | None = None
    guard: tuple | None = None
    text: str = ""


def normalize(question):
    """Return the question in lower case with plain spacing and words.

    As in the vocabulary (qa.resolve), accents are removed and "&" is read
    as "and". Dots inside codes (I50.9) are kept; every other symbol
    becomes a space.
    """
    text = unicodedata.normalize("NFKD", question or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.replace("’", "'").replace("‘", "'").replace("&", " and ")
    text = CONTRACTION.sub(lambda match: CONTRACTIONS[match.group(0)],
                           text.casefold())
    text = re.sub(r"'s\b", "", text)
    text = re.sub(r"(?<=[0-9a-z])\.(?=[0-9a-z])", "\x00", text)
    text = re.sub(r"[^0-9a-z\x00]+", " ", text).replace("\x00", ".")
    return " ".join(text.split())


def candidates(phrase):
    """Return a condition phrase's candidates, longest first.

    Question words are removed one at a time, from the start while any is
    there, then from the end.
    """
    found = []
    phrase = " ".join(phrase.split())
    while phrase and phrase not in found:
        found.append(phrase)
        shorter = LEADING.sub("", phrase, count=1)
        if shorter == phrase:
            shorter = TRAILING.sub("", phrase, count=1)
        phrase = shorter.strip()
    return tuple(found)


def clean_term(phrase):
    """Return a condition phrase with every question word removed."""
    found = candidates(phrase)
    return found[-1] if found else ""


def presence_span(text):
    """Return the span of the condition phrase of a presence frame."""
    wrapped = WRAPPER.match(text)
    if wrapped:
        inner = presence_span(wrapped.group("rest"))
        if inner is None:
            return None
        start = wrapped.start("rest")
        return inner[0] + start, inner[1] + start
    for pattern in PRESENCE:
        match = pattern.match(text)
        if match:
            return match.span("term")
    return None


def is_list(text):
    """Return True when the text asks for the list of conditions."""
    return any(pattern.search(text) for pattern in LIST)


def guard_of(text):
    """Return the (status, reason, category) of the first out-of-scope rule
    the text matches, or None."""
    for status, reason, category, pattern in UNSUPPORTED:
        checked = (DATA_CONDITIONS.sub(" ", text) if category == "other_data"
                   else text)
        if pattern.search(checked):
            return status, reason, category
    return None


def is_bare(text):
    """Return True when the text is a short phrase without a question."""
    return bool(text) and len(text.split()) <= MAX_BARE_WORDS and not (
        QUESTION_START.match(text) or is_list(text))


def two_tasks(question):
    """Return True when a question asks for a list and a presence check:
    one sentence each, or two clauses of one sentence.

    Whole sentences are compared first, so a condition name holding "and"
    still reads as one presence check.
    """
    sentences = []
    for sentence in SENTENCES.split(question or ""):
        text = normalize(sentence)
        text = scopes.remove(text, scopes.mentions(text))
        if text:
            sentences.append(text)
    if any(is_list(first) and presence_span(second)
           for first in sentences for second in sentences
           if first is not second):
        return True
    for text in sentences:
        clauses = [re.sub(r"^(?:whether|if) ", "", part.strip())
                   for part in CLAUSES.split(text) if part.strip()]
        if (len(clauses) > 1 and any(is_list(part) for part in clauses)
                and any(presence_span(part) for part in clauses)):
            return True
    return False


def interpret(question):
    """Interpret one question about the active case."""
    text = normalize(question)
    found = scopes.mentions(text)
    if not text:
        return Interpretation("clarify", reason="task_ambiguous",
                              category="empty", text=text)
    body = scopes.remove(text, found)
    filtered = FILTERED_LIST.match(body)
    if filtered and clean_term(filtered.group("term")) in LIST_WORDS:
        filtered = None
    span = filtered.span("term") if filtered else presence_span(body)
    if span is None and is_bare(body):
        span = (0, len(body))
    rest = body if span is None else body[:span[0]] + " " + body[span[1]:]
    # Two requests at once come first: a condition name in one of them may
    # hold words of the out-of-scope rules.
    if two_tasks(question):
        return Interpretation("clarify", reason="task_ambiguous",
                              category="two_tasks", mentions=found,
                              text=text)
    guard = guard_of(rest)
    if guard is not None:
        return Interpretation(guard[0], reason=guard[1], category=guard[2],
                              mentions=found, text=text)
    terms = candidates(body[span[0]:span[1]]) if span else ()
    term = terms[-1] if terms else None
    listing = is_list(rest) and not filtered
    if term is not None and LIST_PHRASE.fullmatch(term):
        listing, term = True, None
    if term is not None and listing:
        return Interpretation("clarify", reason="task_ambiguous",
                              category="two_tasks", mentions=found,
                              text=text)
    if listing:
        return Interpretation("task", task="condition_list", mentions=found,
                              text=text)
    if span is None:
        return Interpretation("clarify", reason="task_ambiguous",
                              category="unrecognised", mentions=found,
                              text=text)
    if not term or term in PRONOUNS:
        return Interpretation("clarify", reason="concept_unknown",
                              category="missing_condition", mentions=found,
                              text=text)
    return Interpretation("task", task="condition_presence", term=term,
                          terms=terms, mentions=found,
                          guard=guard_of(body[span[0]:span[1]]), text=text)
