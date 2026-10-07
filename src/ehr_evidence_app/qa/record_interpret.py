"""Interpretation of questions about the whole record.

Questions about conditions are read by the condition rules
(qa.interpret) exactly as before. A question those rules refuse, or
whose condition phrase names no condition, is read here (rules
record-questions-v3), by fixed rules and without a model:

1. The question is normalised as condition questions are, its polite
   opening is removed as theirs is, "ICU" and "intensive care" are
   marked, and scope phrases are set aside (qa.scope).
2. A question about the person (sex, age, race, language, insurance,
   marital status, death) asks for the person's details; one about the
   visits (how many, when admitted or discharged, how long, the type of
   admission, where the patient came from or went) asks for the visits'
   details; one about ICU stays or transfers between units asks for
   those records.
3. Otherwise the question's frame gives its task: a count ("how many"),
   a time ("when", "what date"), results flagged abnormal ("abnormal",
   "out of range", "high"), values ("what was the last potassium", "the
   highest heart rate", "values", "trend"), a list ("what medications
   were given", "list the labs"), or a presence check ("was heparin
   given", "did she have a CT").
4. Its kind comes from its words (medications, procedures, laboratory
   results, vital signs, microbiology, conditions), or, when it names
   none, from the one kind its phrase resolves in (qa.record_resolve);
   a phrase that resolves in several kinds asks which one is meant.
5. The words left, once the frame, the kind's words, a selector (last,
   first, highest, lowest, all) and harmless words are set aside, are
   the phrase. It must resolve, or the question gets a clarification;
   words of time or of a part of the record that are not a scope
   phrase, exclusions, filters by value and pronouns do too.
6. A negation ("was heparin not given", "did she never have a CT") asks
   the same presence as the question without it, as condition questions
   do; a negated question about a whole kind ("which labs were not
   done") is a filter, and gets a clarification. Two items joined by
   "and" or "or", each of which resolves while the whole does not, get
   a clarification that asks about one at a time.
7. A phrase that names no kind takes a kind only by a name or a synonym
   of it, or by its words in a kind the question's verbs or task point
   to; otherwise the question is left to the condition rules, so that
   "is diabetes recorded" keeps the condition rules' response.
8. Flags are read the same way for an item and for a whole kind: a
   laboratory result asked about as high or low asks for the results
   the laboratory flagged; a vital sign asked about as high or low for
   its highest or lowest value, since vital signs carry no flag; and a
   question about abnormal results of a kind that carries no flag (vital
   signs without high or low, medications, procedures) gets a
   clarification. A question about the doses of the medications asks
   for their values.
9. The record does not say what a medication was given for. A question
   about medications that asks what they were given for ("medications
   for his seizures", "what was given for pain", "seizure medications")
   gets a clarification that says so: one whose words after "for" or
   "treat" name a condition, a test or a vital sign ("for blood
   pressure"), the words before them naming no item of another kind, or
   one that names medications by a condition instead of by a
   medication's name.
10. A fixed word for a class of medications ("nsaids", "an antibiotic")
    names medications rather than a condition of the same name, in a
    question that names no kind: the condition vocabulary also calls an
    overdose NSAIDs, and "any nsaids" asks about medications. A word that
    is also a name of another kind of record ("chemotherapy", a
    procedure's too) still asks which is meant.
11. A name's words stay together. Before the rest of rules 4 to 7, the
    question's words from the first to the last word of its phrase,
    with the words set aside between them, and with up to three words
    set aside beside them (harmless words and kinds' words), are tried
    as the whole name of an item, longest first: by a name or a fixed
    synonym of the kind the question names, or of the one kind of
    ORDER that holds it (record_resolve.Terms.exact), never by the words
    of names. A span that holds every word of the kinds the question
    names, as "complication of surgical procedure" holds "surgical" and
    "procedure", names no kind outside the name, and is tried in every
    kind. A name so found is the phrase, and the words within it are
    not read as filters, times, kinds or two items: "when was chronic
    kidney disease first recorded" asks about chronic kidney disease,
    where the phrase alone, "chronic kidney", names nothing; "anxiety
    disorder" is not read as anxiety; "type 2 diabetes mellitus without
    complication", "personal history of tobacco use" and "disorder of
    kidney" are names, not a negation, a part of the record or a kind's
    word; and "blood culture" is not every specimen of blood. A question
    about medications that asks what they were given for ("for" or
    "treat") is read by rule 9 first, and one whose longest name is held
    by two kinds is read as before, as is one in which no span is a
    name.
12. A question that asks only when one visit named by a scope phrase
    was ("when was the first visit", "when was visit 2", "what date was
    the final admission") asks for that visit's admission, as "when was
    the patient admitted" asks within it; the visit must be the selected
    scope, as for every scope phrase, or the question asks for the scope
    to be changed.
13. A question about microbiology that asks whether anything grew
    ("grew", "growth", "organisms", "bacteria", "isolated", "positive",
    or "normal", "positive" or "negative" at its end) asks for the values
    of its tests, whose answer says first whether an organism is recorded
    (qa.record_answer); so does one that asks about the latest or first
    culture's resistance. The latest or the first ("was the latest blood
    culture positive") asks about the latest or the first culture of each
    test; any other selector, or none, about all of them.
14. "Normal", "positive" or "negative" at the end of a question, or
    "within normal limits", asks how a result reads: set aside from the
    phrase, it asks for the results the laboratory flagged, as "abnormal"
    does, and for a kind that carries no flag it gets the clarification
    of rule 8. Within a question the words stay part of a name ("normal
    saline", "gram negative rods").
15. A laboratory result that a selector picks (the latest, first,
    highest or lowest) asked about as high, low, abnormal, normal,
    positive or negative asks for that result with whether the
    laboratory flagged it, where the selector was left out before.
16. A question about procedures as surgery ("did the patient have
    surgery", "how many operations") lists or counts every procedure,
    with a notice that the record does not mark which were surgery.
17. A question about the prediction ("is the predicted diagnosis
    recorded") gets the clarification the condition rules give one: the
    prediction and its explanation are shown in their own workflow.
18. Where the patient was admitted from is also asked as "admitted
    through the ED" or "came via the emergency department"; the type of
    admission as "was it an emergency" or "urgent"; the visits as
    "readmitted"; the age as "over 65" or "older than 80", answered with
    the ages and never compared; and death as "alive at discharge" or
    "died in hospital", with each visit's death in hospital.

Version 2 adds rules 6 to 8; version 3 removes the polite opening
(rule 1); version 4 adds rule 9, version 5 rule 10, version 6 rule 11,
version 7 rule 12 and version 8 rules 13 to 18. Before version 8 a
question about what grew asked about all of a test's results whatever
its selector; "negative" or "normal" at the end of a question, a
prediction, arriving through the ED, an emergency, readmission, an age
over a number and death at discharge were asked back or not understood;
and a selected laboratory result asked about as abnormal lost its
selector. Version 6 also reads "all ... values" around a name of up to
eight words, digits included ("all the hemoglobin a1c values"), where
four words of letters alone were read before.
"""

from __future__ import annotations

import dataclasses
import re

from ehr_evidence_app.qa import interpret as conditions
from ehr_evidence_app.qa import record_resolve as resolving
from ehr_evidence_app.qa import scope as scopes

RULES_VERSION = "record-questions-v8"
RECORD_TASKS = ("record_list", "record_presence", "record_timing",
                "record_count", "record_values", "record_abnormal",
                "visit_details", "person_details")
PERSON_FIELDS = ("sex", "age", "race", "language", "insurance",
                 "marital_status", "death")
VISIT_FIELDS = ("list", "count", "admitted", "discharged", "length",
                "type", "admitted_from", "discharged_to")
SELECTORS = ("latest", "first", "highest", "lowest", "all")
# Kinds in the order a phrase that names none is tried.
ORDER = ("lab", "vital", "medication", "procedure", "microbiology",
         "transfer", "condition")

ICU = re.compile(r"\b(?:intensive care unit|intensive care|icu)"
                 r"(?: (?:stay|stays|admission|admissions|unit|units))?\b")
KIND_WORDS = {
    "medication": r"medications?|medicines?|meds|drugs?|prescriptions?|"
                  r"prescribed|pills?|tablets?",
    "procedure": r"procedures?|surgery|surgeries|surgical|operations?|"
                 r"interventions?|underwent|undergo|undergone|operated",
    "lab": r"labs?|laboratory|laboratories|blood tests?|lab tests?|bloods|"
           r"blood work|bloodwork|tests?|lab results?|lab values?",
    "vital": r"vitals?|vital signs?|observations|obs",
    "microbiology": r"cultures?|microbiology|micro|organisms?|bacteria|"
                    r"bacterium|bugs?|pathogens?|sensitivit(?:y|ies)|"
                    r"susceptibilit(?:y|ies)|specimens?|swabs?",
    "transfer": r"transfers?|transferred|wards?|care units?|units?",
    "condition": r"conditions?|diagnos(?:is|es|ed)|problems?|diseases?|"
                 r"illness(?:es)?|disorders?",
}
KIND_PATTERNS = {kind: re.compile(r"\b(?:%s)\b" % words)
                 for kind, words in KIND_WORDS.items()}
# How many words set aside beside a phrase rule 11 tries as part of a
# name, on each side.
SPAN_REACH = 3
# Verbs that point to a kind without naming it: they break a tie between
# kinds that both hold a name, and let a name no kind holds be asked
# about.
HINTS = {
    "medication": re.compile(r"\b(?:given|administered|taking|took|take|"
                             r"taken|received|receiving|started|on)\b"),
    "procedure": re.compile(r"\b(?:done|performed|underwent|undergo|"
                            r"undergone|had)\b"),
    "lab": re.compile(r"\b(?:measured|checked|drawn|tested|sent)\b"),
    "microbiology": re.compile(r"\b(?:grew|grown|isolated|cultured)\b"),
}
PERSON = (
    ("sex", re.compile(r"\b(?:sex|gender)\b|\b(?:is|was) (?:the patient|he|"
                       r"she|they|patient|this patient) (?:a )?(?:male|"
                       r"female|man|woman)(?: or (?:a )?(?:male|female|man|"
                       r"woman))?\b|\bmale or female\b|"
                       r"\bfemale or male\b")),
    ("age", re.compile(r"\bhow old\b|\bages?\b|\baged\b|\byears? old\b|"
                       r"\bborn\b|\bbirth\b|\bbirthday\b|\b(?:over|under|"
                       r"older than|younger than|above|below|at least|"
                       r"more than|less than) [0-9]{1,3}(?: years?)?"
                       r"(?: old)?(?: (?:please|thanks|thank you))*$")),
    ("race", re.compile(r"\b(?:race|ethnicity|ethnic|ethnic background)\b")),
    ("language", re.compile(r"\b(?:language|languages|speak|speaks|spoke|"
                            r"interpreter)\b")),
    ("insurance", re.compile(r"\b(?:insurance|insured|insurer|payer|"
                             r"medicare|medicaid)\b")),
    ("marital_status", re.compile(r"\b(?:marital|married|marriage|"
                                  r"divorced|widowed|spouse)\b")),
    ("death", re.compile(r"\b(?:die|died|dies|dying|death|dead|deceased|"
                         r"alive|living|expired|pass away|passed away|"
                         r"survive|survived|survival|mortality)\b")),
)
VISIT = (
    ("count", re.compile(r"\bhow many (?:times )?(?:visits|admissions|"
                         r"hospitali[sz]ations|stays|encounters|"
                         r"hospital stays)\b|\bhow many times (?:was|has|"
                         r"had|did) (?:the patient|he|she|they|patient) "
                         r"(?:been )?(?:admitted|hospitali[sz]ed|come in)\b|"
                         r"\bnumber of (?:visits|admissions|stays|"
                         r"hospitali[sz]ations|encounters)\b")),
    ("length", re.compile(r"\bhow long\b|\blength of (?:the )?(?:stay|"
                          r"admission|hospitali[sz]ation|visit)\b|\blos\b|"
                          r"\bhow many days\b")),
    ("admitted_from", re.compile(r"\badmitted from\b|\bcome from\b|\bcame "
                                 r"from\b|\badmission (?:location|source)\b|"
                                 r"\bsource of admission\b|\bwhere (?:was|"
                                 r"did) (?:the patient|he|she|they) "
                                 r"(?:admitted|come)\b|\badmitted (?:through|"
                                 r"via)\b|\b(?:come|came|arrive|arrived) "
                                 r"(?:in )?(?:through|via)\b|\b(?:through|"
                                 r"via) (?:the )?(?:ed|er|emergency "
                                 r"(?:department|room))\b")),
    ("discharged_to", re.compile(r"\bdischarged? (?:to|home)\b|\bdischarge "
                                 r"(?:location|disposition|destination)\b|"
                                 r"\bdisposition\b|\bwhere (?:was|did) "
                                 r"(?:the patient|he|she|they) (?:discharged"
                                 r"|go)\b|\bsent home\b")),
    ("type", re.compile(r"\b(?:type|kind) of (?:admission|visit|stay|"
                        r"encounter)\b|\badmission type\b|\bvisit type\b|"
                        r"\belective\b|\bemergency admission\b|"
                        r"\burgent admission\b|\b(?:an? )?(?:emergency|"
                        r"urgent)\b(?! (?:department|room|ward|unit))")),
    ("discharged", re.compile(r"\bdischarged?\b|\bdischarge date\b|"
                              r"\bdate of discharge\b|\bwent home\b")),
    ("admitted", re.compile(r"\badmitted\b|\badmission date\b|\bdate of "
                            r"admission\b|\badmission dates\b|"
                            r"\bhospitali[sz]ed\b")),
    ("list", re.compile(r"\b(?:visits|admissions|hospitali[sz]ations|"
                        r"encounters|hospital stays)\b|\breadmi(?:t|ts|"
                        r"tted|tting|ssion|ssions)\b")),
)
# Words a question about the visits may hold besides its field's, and
# those only a question about where the patient came from may hold
# ("admitted through the emergency department").
VISIT_REST = frozenset(("admission", "admissions", "visit", "visits", "stay",
                        "stays", "hospital", "discharge", "discharged",
                        "admitted", "long", "length", "type", "kind", "home",
                        "location", "source", "go", "come", "came", "went",
                        "sent", "dates", "date"))
ARRIVAL_REST = frozenset(("ed", "er", "emergency", "department", "room"))
# Words a question about the person may hold besides its field's.
PERSON_REST = frozenset(("admission", "visit", "admitted", "time", "old",
                         "year", "years", "discharge", "discharged",
                         "hospital", "stay"))
# Rule 12: all that is left of a question about when one visit, named by a
# scope phrase, was.
VISIT_WHEN = re.compile(
    r"(?:when|what date|what day|which date|which day|on what date|"
    r"on which date)(?: (?:was|were|is|did))?(?: (?:it|that|this))?"
    r"(?: (?:happen|start|begin|take place|occur))?")
COUNT = re.compile(r"\bhow many\b|\bnumber of\b|\bhow often\b|\bcount of\b|"
                   r"\btotal number\b|\bhow frequently\b")
TIMING = re.compile(r"\bwhen\b|\bwhat (?:date|day|time|dates|days)\b|"
                    r"\bwhich (?:date|day|dates|days)\b|\bdates? of\b|"
                    r"\bon what (?:date|day)\b|\bat what time\b|"
                    r"\btimes? of\b")
ABNORMAL = re.compile(r"\babnormal(?:ly)?\b|\bout of range\b|\bflagged\b|"
                      r"\bderanged\b|\bcritical\b|\bresistant\b|"
                      r"\bresistance\b")
HIGH = re.compile(r"\b(?:high|elevated|raised|increased)\b")
LOW = re.compile(r"\b(?:low|decreased|reduced)\b")
VALUES = re.compile(r"\bvalues?\b|\blevels?\b|\bresults?\b|\breadings?\b|"
                    r"\bmeasurements?\b|\btrends?\b|\bseries\b|"
                    r"\bover time\b|\bwhat (?:is|was|were|are) (?:the |his |"
                    r"her |their |its )?")
LIST = re.compile(r"\b(?:list|show|display|enumerate|summari[sz]e|give|tell|"
                  r"what|which)\b")
SELECTOR_PATTERNS = (
    ("latest", re.compile(r"\b(?:last|latest|most recent|recent|newest|"
                          r"final)\b")),
    ("first", re.compile(r"\b(?:first|initial|earliest|baseline)\b")),
    ("highest", re.compile(r"\b(?:highest|max|maximum|peak)\b")),
    ("lowest", re.compile(r"\b(?:lowest|min|minimum|nadir)\b")),
    ("all", re.compile(r"\btrends?\b|\bseries\b|\bover time\b|\ball\b"
                       r"(?: [a-z0-9]+){0,8} (?:values|results|readings|"
                       r"levels|measurements)\b|\bevery (?:value|result|"
                       r"reading|level|measurement)\b")),
)
# The words of the selectors, set aside before the phrase is read.
SELECTOR_WORDS = re.compile(r"\b(?:last|latest|most recent|recent|newest|"
                            r"final|first|initial|earliest|baseline|highest|"
                            r"max|maximum|peak|lowest|min|minimum|nadir|"
                            r"trends?|series|over time)\b")
# Microbiology asked about what grew: its results, organisms included.
GROWTH = re.compile(r"\b(?:grew|grow|grown|growth|organisms?|bacteria|"
                    r"bacterium|isolated|positive|sensitivit(?:y|ies)|"
                    r"susceptibilit(?:y|ies))\b")
# The end of a question, after any closing courtesy ("..., please").
COURTESY_END = r"(?: (?:please|thanks|thank you))*$"
# How a result reads, asked at the end of a question ("was the creatinine
# normal", "was the blood culture negative"): for a laboratory test its
# flag, for microbiology whether anything grew (rules 13 and 14). Within a
# question, the words stay part of a name ("normal saline", "gram negative
# rods").
QUALITY = re.compile(
    r"(?:\b(?:normal|positive|negatives?)(?: or (?:not|normal|abnormal|"
    r"positive|negatives?))?|\b(?:within|in) (?:the )?(?:normal|reference) "
    r"(?:limits|range))" + COURTESY_END)
# Selectors that pick one result of an item.
SINGLE = ("latest", "first", "highest", "lowest")
# Procedures asked about as surgery, which the record does not mark (rule
# 16).
SURGERY = re.compile(r"\b(?:surgery|surgeries|surgical|operations?|"
                     r"operated)\b")
# The predicted diagnosis, which its own workflow shows (rule 17).
PREDICTION = re.compile(r"\b(?:predicted|predict|predicts|prediction|"
                        r"predictions)\b")
# Words of results, which point to tests and vital signs.
RESULTS = re.compile(r"\b(?:results?|values?|levels?|readings?|"
                     r"measurements?)\b")
# Filters and time words a question may not hold (they are not applied).
FILTERS = re.compile(r"\b(?:above|below|over(?! time)|under|greater than|"
                     r"less than|more than|higher than|lower than|between|"
                     r"except|excluding|exclude|other than|besides|"
                     r"apart from|only|unless|instead|rather than)\b")
TIMES = re.compile(r"\b(?:yesterday|today|tomorrow|tonight|ago|since|after|"
                   r"until|before|prior|lately|morning|evening|night|"
                   r"overnight|week|weeks|month|months|year|years|hours?|"
                   r"january|february|march|april|may|june|july|august|"
                   r"september|october|november|december)\b")
# A day, hour or week named by its number as a time ("on day 2", "in the
# first 24 hours", "3 days ago"), not a scope; a number in a name ("24
# hour urine") is not one.
NUMBERED_TIMES = re.compile(
    r"\b(?:on|in|at|by|during|from|until|till) (?:the )?(?:days?|hours?|"
    r"weeks?|months?) [0-9]+\b|"
    r"\b(?:days?|hours?|weeks?|months?) [0-9]+ (?:of|after|before|into)\b|"
    r"\b(?:within|after|before|for|over|in|during|first|last|past|next) "
    r"(?:the )?(?:first |last |past |next )?[0-9]+ (?:days?|hours?|weeks?|"
    r"months?)\b|"
    r"\b[0-9]+ (?:days?|hours?|weeks?|months?) (?:ago|after|before|later|"
    r"earlier|into)\b")
PARTS = re.compile(r"\b(?:admission|admissions|visit|visits|stay|stays|"
                   r"encounter|encounters|hospital|hospitali[sz]ation|"
                   r"discharge|currently|current|now|presently|then)\b")
# Words a record question may hold besides its frame, kind and phrase.
HARMLESS = frozenset(conditions.EXPLAINED) | frozenset("""
given administered received receive receiving get got gets getting take
took taking taken started start starting stopped stop performed done
underwent undergo undergone had measured measure checked check tested
test drawn sent ordered order charted obtained collected grew grow grown
growth isolated found positive result results value values level levels
reading readings measurement measurements trend trends series over time
times date dates day days when what which how many much number often
frequently count total recent last latest most first initial earliest
baseline highest lowest max maximum min minimum peak nadir newest final
abnormal abnormally out range flagged deranged critical high low elevated
raised increased decreased reduced resistant resistance sensitive
susceptible put placed used use using on off from to at in of patient
patients pt her his their any some every all were was is are be been
being did does do has have having also still ever again was anything
something any none
""".split())
# Advice and the future are not what the record holds.
ADVICE = re.compile(r"\b(?:should|ought to|recommend\w*|advis\w*|need to|"
                    r"needs to|must|will|would|going to|likely|likelihood|"
                    r"chance|chances|risk|prognosis|outlook|expected to|"
                    r"could (?:he|she|they|the patient))\b")
PRONOUNS = frozenset(("it", "this", "that", "these", "those", "them",
                      "they", "one", "ones", "anything", "something"))
# Negations: harmless in a question about an item or a field, a filter in
# one about a whole kind.
NEGATIONS = frozenset(("not", "no", "never", "none", "without", "neither",
                       "nor"))
# Words that join two items.
CONJUNCTIONS = frozenset(("and", "or", "plus", "versus", "vs"))
# Words that ask what a medication was given for (rule 9), and the kinds
# whose item named before them makes the question one about that item.
TREATING = re.compile(r"\b(?:for|to treat|treat|treats|treating|treated|"
                      r"against)\b")
OTHER_ITEMS = ("lab", "vital", "procedure", "microbiology", "transfer")


@dataclasses.dataclass(frozen=True)
class RecordQuestion:
    """The reading of one question about the record.

    status is "task" (task, kind and the rest set), "clarify" (reason and
    category set) or "none" (not a question about the record). kind is a
    question kind of qa.record_resolve, "visit" or "person"; phrase is
    the normalised phrase of what it asks about; selector is one of
    SELECTORS; field one of PERSON_FIELDS or VISIT_FIELDS.
    """

    status: str
    task: str | None = None
    kind: str | None = None
    phrase: str | None = None
    selector: str | None = None
    field: str | None = None
    mentions: tuple = ()
    reason: str | None = None
    category: str | None = None
    names: tuple = ()
    suggestions: tuple = ()


def clarify(reason, category, mentions=(), names=(), phrase=None,
            suggestions=()):
    """Return a clarification reading."""
    return RecordQuestion("clarify", reason=reason, category=category,
                          mentions=mentions, names=names, phrase=phrase,
                          suggestions=suggestions)


def suggestions_of(terms, phrase, kinds):
    """Return the names the first of some kinds suggests for a phrase that
    none of them resolves."""
    for kind in kinds:
        found = terms.resolve(kind, phrase).suggestions
        if found:
            return found
    return ()


def marked(text):
    """Return normalised text with ICU phrases read as one word, so that
    an ICU stay is not taken for a visit."""
    return ICU.sub(" icustay ", text)


def selector_of(text):
    """Return the selector a question names, or None."""
    for selector, pattern in SELECTOR_PATTERNS:
        if pattern.search(text):
            return selector
    return None


def blank(pattern, text):
    """Return text with a pattern's matches removed."""
    return " ".join(pattern.sub(" ", text).split())


def left_words(text, kinds=(), keep=frozenset()):
    """Return the words of text that are not harmless, a negation, a
    kind's words, a selector or a frame, in order; harmless words in keep
    are kept."""
    for kind in kinds:
        text = blank(KIND_PATTERNS[kind], text)
    text = blank(SELECTOR_WORDS, text)
    for pattern in (COUNT, TIMING, ABNORMAL, HIGH, LOW, DOSE, re.compile(
            r"\bout of range\b|\bhow many\b|\bnumber of\b|\bover time\b")):
        text = blank(pattern, text)
    return [word for word in text.split() if word in keep or (
        word not in HARMLESS and word not in NEGATIONS)]


def set_aside(word):
    """Return True when a word is one the phrase leaves out as harmless or
    as a kind's word."""
    return word in HARMLESS or any(pattern.fullmatch(word)
                                   for pattern in KIND_PATTERNS.values())


def name_spans(body, kinds):
    """Return the spans of a question that rule 11 tries as whole names,
    longest first: its words from the first to the last word of its
    phrase, and with up to SPAN_REACH words set aside before or after
    them; the phrase itself is not one."""
    tokens = body.split()
    words = left_words(body, kinds)
    positions, index = [], 0
    for word in words:
        while index < len(tokens) and tokens[index] != word:
            index += 1
        if index == len(tokens):
            return []
        positions.append(index)
        index += 1
    if not positions:
        return []
    first, last = positions[0], positions[-1]
    spans = {(first, last)}
    for reach in range(1, SPAN_REACH + 1):
        if last + reach < len(tokens) and all(
                set_aside(tokens[position])
                for position in range(last + 1, last + reach + 1)):
            spans.add((first, last + reach))
        if first - reach >= 0 and all(
                set_aside(tokens[position])
                for position in range(first - reach, first)):
            spans.add((first - reach, last))
    phrase = " ".join(words)
    found = [" ".join(tokens[start:end + 1]) for start, end in sorted(
        spans, key=lambda span: (span[0] - span[1], span))]
    return [span for span in found if span != phrase]


def whole_name(terms, body, kinds, hints, prefer):
    """Return (kind, resolution, phrase) of the longest span of a question
    that is an item's whole name, in the kind the question names or the
    one kind of ORDER that holds it, the hints and then the kinds the
    task prefers breaking a tie (rule 11); None when no span is, when
    the longest is held by two kinds, and for a question about what
    medications were given for (rule 9)."""
    if ("medication" in kinds or (not kinds and "medication" in hints)) and (
            TREATING.search(body)):
        return None
    for span in name_spans(body, kinds):
        found = {}
        within = kinds and not any(
            KIND_PATTERNS[kind].search(body.replace(span, " "))
            for kind in kinds)
        for kind in ORDER if within or not kinds else kinds:
            resolution = terms.exact(kind, span)
            if resolution is not None:
                found[kind] = resolution
        for narrower in (hints, prefer):
            kept = {kind: item for kind, item in found.items()
                    if kind in narrower}
            if len(found) > 1 and kept:
                found = kept
        if len(found) == 1:
            kind = next(iter(found))
            return kind, found[kind], span
        if found:
            return None
    return None


def negated(body):
    """Return True when a question holds a negation."""
    return any(word in NEGATIONS for word in body.split())


def two_items(terms, body, kinds, hints, prefer):
    """Return True when a question names two items joined by "and" or
    "or", each of which resolves."""
    words = left_words(body, kinds, keep=CONJUNCTIONS)
    parts, current = [], []
    for word in words:
        if word in CONJUNCTIONS:
            parts.append(current)
            current = []
        else:
            current.append(word)
    parts.append(current)
    parts = [part for part in parts if part]
    if len(parts) < 2:
        return False
    for part in parts:
        kind, resolution = resolve_phrase(terms, " ".join(part), kinds,
                                          hints, prefer)
        if isinstance(resolution, dict):
            continue
        if kind is None or resolution is None or (
                resolution.status != "resolved"):
            return False
    return True


def condition_named(terms, words):
    """Return words as a phrase when they name a condition (one that
    resolves, or is ambiguous or too broad), else None."""
    phrase = " ".join(words)
    if phrase and terms.resolve("condition", phrase).status != "unknown":
        return phrase
    return None


def indication_of(terms, body, phrase, kinds, hints):
    """Return what a question about medications asks them to have been
    given for, or None (rule 9): the question names medications, or names
    no kind and its verbs point to medications; and either its words
    after "for" or "treat" name a condition, a test or a vital sign, while
    the words before them name no item of another kind, or it names
    medications and its phrase is a condition, not a medication."""
    if kinds != ["medication"] and (kinds or "medication" not in hints):
        return None
    parts = TREATING.split(body)
    if len(parts) > 1:
        before = " ".join(left_words(" ".join(parts[:-1]), kinds))
        if before and terms.resolve("medication", before).status != (
                "resolved") and any(terms.resolve(kind, before).status
                                    == "resolved" for kind in OTHER_ITEMS):
            return None
        after = left_words(parts[-1], kinds)
        named = condition_named(terms, after)
        if named is None and after and any(
                terms.resolve(kind, " ".join(after)).status == "resolved"
                for kind in ("lab", "vital")):
            named = " ".join(after)
        return named
    if kinds and terms.resolve("medication", phrase).status != "resolved":
        return condition_named(terms, phrase.split())
    return None


def person_field(body):
    """Return the person field a question asks about, or None."""
    found = [field for field, pattern in PERSON if pattern.search(body)]
    return found[0] if len(found) == 1 else (found[0] if found else None)


def visit_field(body):
    """Return the visit field a question asks about, or None."""
    for field, pattern in VISIT:
        if pattern.search(body):
            return field
    return None


DOSE = re.compile(r"\b(?:dose|doses|dosage|dosing|how much|strength|"
                  r"route|routes)\b")


def task_of(body, selector):
    """Return the task a question's frame gives."""
    if DOSE.search(body):
        return "record_values"
    if COUNT.search(body):
        return "record_count"
    if TIMING.search(body):
        return "record_timing"
    if ABNORMAL.search(body):
        return "record_abnormal"
    if selector is not None or VALUES.search(body) or HIGH.search(body) or (
            LOW.search(body)):
        return "record_values"
    return None


def kinds_named(body):
    """Return the kinds whose words a question holds, in ORDER."""
    return [kind for kind in ORDER if kind in KIND_PATTERNS
            and KIND_PATTERNS[kind].search(body)]


def hints_of(body):
    """Return the kinds a question's verbs point to."""
    return [kind for kind, pattern in HINTS.items() if pattern.search(body)]


def resolve_phrase(terms, phrase, kinds, hints=(), prefer=()):
    """Return (kind, resolution) of a phrase: in the kind the question
    names, or the one kind of ORDER it resolves in by a name or a
    synonym, else by its words in a kind its verbs (hints) or its task
    (prefer) point to; the hints, then the kinds the task prefers, break
    a tie. Return (None, resolutions) when several remain, and (None,
    None) when none resolves it.

    Words alone never take a question that names no kind: "is diabetes
    recorded" is a condition question the condition rules could not
    resolve, and the words of the procedure names hold "diabetes", which
    would answer it as a procedure."""
    if kinds:
        kind = kinds[0]
        return kind, terms.resolve(kind, phrase)
    if resolving.class_word(phrase):
        # Rule 10: a fixed word for a class of medications names them
        # rather than a condition, unless another kind names it too.
        resolution = terms.resolve("medication", phrase)
        others = [terms.resolve(kind, phrase) for kind in OTHER_ITEMS]
        if resolution.status == "resolved" and not any(
                other.status == "resolved"
                and other.rule in ("name", "synonym") for other in others):
            return "medication", resolution
    found = {}
    for kind in ORDER:
        resolution = terms.resolve(kind, phrase)
        if resolution.status == "resolved":
            found[kind] = resolution
    named = {kind: item for kind, item in found.items()
             if item.rule in ("name", "synonym", "icd_code")}
    guided = {kind: item for kind, item in found.items()
              if kind in hints or kind in prefer}
    chosen = named or guided
    for narrower in (hints, prefer):
        kept = {kind: item for kind, item in chosen.items()
                if kind in narrower}
        if len(chosen) > 1 and kept:
            chosen = kept
    if len(chosen) == 1:
        kind = next(iter(chosen))
        return kind, chosen[kind]
    if len(chosen) > 1:
        return None, chosen
    return None, None


def interpret(question, terms):
    """Read one question about the record; terms resolves phrases. A
    request for advice, or a question about the future, is not one about
    the record."""
    text = marked(conditions.courteous(conditions.normalize(question)))
    if not text or ADVICE.search(text):
        return RecordQuestion("none")
    if PREDICTION.search(text):
        # Rule 17: the prediction and its explanation have their own
        # workflow, as the condition rules say.
        return clarify("prediction_selection_needed", "prediction")
    found = scopes.mentions(text)
    body = scopes.remove(text, found)
    if not body:
        return RecordQuestion("none")
    if (len(found) == 1 and found[0].kind == "selected_visit"
            and VISIT_WHEN.fullmatch(body)):
        return RecordQuestion("task", task="visit_details", kind="visit",
                              field="admitted", mentions=found)
    words = body.split()
    field = person_field(body)
    if field is not None and not kinds_named(body) and (
            "icustay" not in words):
        rest = [word for word in left_words(blank(PERSON[
            PERSON_FIELDS.index(field)][1], body))
            if word not in PERSON_REST]
        if not rest:
            return RecordQuestion("task", task="person_details",
                                  kind="person", field=field,
                                  mentions=found)
    if "icustay" in words:
        return icu_question(body, found)
    field = visit_field(body)
    if field is not None and not (set(kinds_named(body)) - {"transfer"}):
        pattern = dict(VISIT)[field]
        allowed = VISIT_REST | (ARRIVAL_REST if field == "admitted_from"
                                else frozenset())
        rest = [word for word in left_words(blank(pattern, body))
                if word not in allowed]
        if not rest:
            return RecordQuestion("task", task="visit_details",
                                  kind="visit", field=field,
                                  mentions=found)
    return item_question(body, found, terms)


def icu_question(body, found):
    """Read a question about ICU stays: a count, or the stays with their
    units, times and lengths; a negation asks the same. Any other words
    ask about records within the stays, which is not a scope."""
    rest = left_words(blank(re.compile(r"\bicustay\b"), body))
    if rest:
        return clarify("scope_ambiguous", "scope_unrecognised", found)
    task = "record_count" if COUNT.search(body) else "record_list"
    return RecordQuestion("task", task=task, kind="icu_stay", mentions=found)


def flag_rules(kind, task, selector, body):
    """Return (task, selector, category) of a question once its flags are
    read for its kind, or None when it asks about a flag that its kind
    does not carry."""
    high, low = HIGH.search(body), LOW.search(body)
    if kind == "lab" and task == "record_values" and selector is None and (
            high or low):
        return "record_abnormal", selector, None
    if kind == "vital" and (task == "record_abnormal" or (
            task == "record_values" and selector is None
            and (high or low))):
        if not (high or low):
            return None
        # Vital signs carry no flag: the highest or lowest value instead.
        return ("record_values", "lowest" if low and not high else
                "highest", "vital_flag")
    if task == "record_abnormal" and kind not in ("lab", "microbiology"):
        return None
    return task, selector, None


def default_selector(task, kind, selector):
    """Return the selector of a values question that names none: the
    latest result of a test or vital sign, every other value."""
    if task == "record_values" and selector is None:
        return "latest" if kind in ("lab", "vital") else "all"
    return selector


def item_question(body, found, terms):
    """Read a question about records of one kind."""
    quality = QUALITY.search(body)
    if quality:
        # Rules 13 and 14: how a result reads, asked at the end, is set
        # aside from the phrase.
        body = body[:quality.start()].rstrip()
    selector = selector_of(body)
    task = task_of(body, selector)
    kinds = kinds_named(body)
    hints = hints_of(body)
    prefer = ("lab", "vital") if task in (
        "record_values", "record_abnormal") or RESULTS.search(body) or (
        quality) else ()
    whole = whole_name(terms, body, kinds, hints, prefer)
    if whole is not None:
        kind, _, phrase = whole
        return item_reading(body, found, task, selector, kind, phrase,
                            bool(quality))
    if FILTERS.search(body):
        return clarify("task_ambiguous", "qualified", found)
    if len(kinds) > 1 and not (set(kinds) <= {"lab", "microbiology"}):
        return clarify("task_ambiguous", "two_tasks", found)
    phrase_words = left_words(body, kinds)
    remaining = " ".join(phrase_words)
    if TIMES.search(remaining) or PARTS.search(remaining) or (
            NUMBERED_TIMES.search(body)):
        return clarify("scope_ambiguous", "scope_unrecognised", found)
    if not phrase_words:
        if not kinds and hints and any(word in PRONOUNS
                                       for word in body.split()):
            return clarify("concept_unknown", "missing_item", found)
        if not kinds and "medication" in hints and DOSE.search(body):
            # "What doses were given?": the medications' doses.
            kinds = ["medication"]
        if not kinds:
            return RecordQuestion("none")
        if negated(body):
            # "Which labs were not done?": a filter of the kind.
            return clarify("task_ambiguous", "qualified", found)
        kind = kinds[0]
        read = read_flags(kind, task, selector, body, quality)
        if read is not None:
            task, selector, category = read
            if category == "lab_flag" or kind == "microbiology":
                return RecordQuestion("task", task=task, kind=kind,
                                      selector=selector, mentions=found,
                                      category=category)
        flags = flag_rules(kind, task, selector, body)
        if flags is None:
            return clarify("task_ambiguous", "flag_unrecorded", found)
        task, selector, category = flags
        if task is None:
            task = "record_list"
        if task == "record_values" and kind not in (
                "lab", "vital", "microbiology") and not (
                kind == "medication" and DOSE.search(body)):
            task = "record_list"
        if kind == "procedure" and task in ("record_list", "record_count") \
                and SURGERY.search(body):
            # Rule 16: the record does not mark which procedures were
            # surgery, and the answer says so.
            category = "surgery"
        return RecordQuestion("task", task=task, kind=kind,
                              selector=default_selector(task, kind,
                                                        selector),
                              mentions=found, category=category)
    phrase = " ".join(phrase_words)
    if phrase in PRONOUNS or all(word in PRONOUNS for word in phrase_words):
        return clarify("concept_unknown", "missing_item", found)
    condition = indication_of(terms, body, phrase, kinds, hints)
    if condition is not None:
        return clarify("task_ambiguous", "indication", found,
                       phrase=condition)
    kind, resolution = resolve_phrase(terms, phrase, kinds, hints, prefer)
    unresolved = (kind is None and resolution is None) or (
        kind is not None and resolution.status != "resolved")
    if unresolved and two_items(terms, body, kinds, hints, prefer):
        return clarify("task_ambiguous", "two_items", found)
    if kind is None and resolution is None and (kinds or hints):
        return clarify("concept_unknown", "unknown_item", found,
                       phrase=phrase, suggestions=suggestions_of(
                           terms, phrase, kinds or hints))
    if kind is None and isinstance(resolution, dict):
        return clarify("concept_ambiguous", "several_kinds", found,
                       names=tuple(resolution), phrase=phrase)
    if kind is None:
        return RecordQuestion("none", phrase=phrase)
    return item_reading(body, found, task, selector, kind, phrase,
                        bool(quality))


def growth_selector(selector):
    """Return the selector of a question about what grew (rule 13): the
    latest or the first culture, or all of them."""
    return selector if selector in ("latest", "first") else "all"


def read_flags(kind, task, selector, body, quality):
    """Return (task, selector, category) of a question about what grew or
    how a result reads (rules 13 to 15), or None when it is neither:
    microbiology's values, by culture; one laboratory result a selector
    picks, with whether it is flagged; or the results the laboratory
    flagged."""
    if kind == "microbiology" and (
            (task in (None, "record_values")
             and (quality or GROWTH.search(body)))
            or (task == "record_abnormal" and selector in ("latest",
                                                           "first"))):
        return "record_values", growth_selector(selector), None
    if kind == "lab" and selector in SINGLE and (
            task == "record_abnormal" or quality or HIGH.search(body)
            or LOW.search(body)):
        return "record_values", selector, "lab_flag"
    if quality and task in (None, "record_values"):
        return "record_abnormal", selector, None
    return None


def item_reading(body, found, task, selector, kind, phrase, quality=False):
    """Return the reading of a question about an item of a kind: its task,
    by default a presence check, with the flags and selector its kind
    takes."""
    read = read_flags(kind, task, selector, body, quality)
    if read is not None:
        task, selector, category = read
        if category == "lab_flag" or kind == "microbiology":
            return RecordQuestion("task", task=task, kind=kind,
                                  phrase=phrase, selector=selector,
                                  mentions=found, category=category)
    if task is None:
        task = "record_presence"
    flags = flag_rules(kind, task, selector, body)
    if flags is None:
        return clarify("task_ambiguous", "flag_unrecorded", found)
    task, selector, category = flags
    if task == "record_values" and kind not in ("lab", "vital",
                                                "microbiology",
                                                "medication"):
        task = "record_presence" if selector not in ("latest", "first") \
            else "record_timing"
    return RecordQuestion("task", task=task, kind=kind, phrase=phrase,
                          selector=default_selector(task, kind, selector),
                          mentions=found, category=category)
