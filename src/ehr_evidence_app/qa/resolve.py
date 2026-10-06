"""Condition term resolution and record matching (work item 32).

A condition phrase from a question is resolved against the pinned condition
vocabulary by fixed rules, applied in order:

1. ICD code: a phrase that is an ICD-9-CM or ICD-10-CM code (dots
   optional) and exists in the vocabulary resolves to that code.
2. Name: the normalised phrase equals the normalised name of standard
   condition concepts.
3. Synonym: otherwise, it equals a normalised English synonym. A synonym
   written "ABC - full name" is indexed both as the full name and, when
   ABC is 2 to 6 capitals or digits, as the acronym.

A phrase that matches nothing is retried with its last word in singular
form. A rule that yields one concept resolves the phrase. Several
concepts resolve to one of them only when it is an ancestor of all the
others (nested candidates); otherwise they are ambiguous. A phrase that
matches nothing is unknown, and comes with suggestions: concepts whose
name or synonym begins with the phrase, or contains it as whole words,
shorter names first. When there are none, words of four or more
letters that are in no name or synonym are compared with the words that
are (same first letter; one edit, or two for words of eight or more
letters), and the names and synonyms of the closest spellings (among
equally close ones, the words in most names first) are suggested. A
suggestion never resolves a phrase. A concept whose
descendants cover more than a quarter of all standard conditions is too
broad to answer.

Normalisation: Unicode compatibility decomposition with accents removed,
case folded, "&" read as "and", possessive "'s" removed, every other
non-alphanumeric character a space, spaces collapsed.

Matching a record (every rule is labelled in the answer):

* exact: the record's standard concept is a resolved concept;
* more specific: it is a descendant of a resolved concept;
* ICD code: for an ICD phrase, the record's ICD source code equals it, or
  (more specific ICD code) starts with it;
* ICD description: a record without a standard concept matches when its
  ICD description contains the name or a synonym of a resolved concept or
  of one of its descendants as a whole phrase that is not negated
  ("without", "w/o", "no", "not", "except", "excluding", "without
  mention of").
"""

from __future__ import annotations

import array
import bisect
import collections
import csv
import dataclasses
import io
import itertools
import json
import re
import unicodedata

from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.qa import vocabulary as build

RULES_VERSION = "condition-resolution-v2"
MANIFEST_SIZE = 1784
MANIFEST_SHA256 = ("0214772bc1b0e244f2d5a3e1904f0865"
                   "78a109f931bb016df9a0054df974a466")
BROAD_FRACTION = 0.25
ACRONYM = re.compile(r"^[A-Z0-9]{2,6}$")
ICD10 = re.compile(r"^[A-Z][0-9][0-9A-Z][0-9A-Z]{0,4}$")
ICD9 = re.compile(r"^(?:V[0-9]{2}|E[0-9]{3}|[0-9]{3})[0-9]{0,2}$")
NEGATIONS = ("without", "no", "not", "except", "excluding")
STATUSES = ("resolved", "ambiguous", "unknown", "too_broad")
MATCH_KINDS = ("exact", "more_specific", "icd_code", "more_specific_icd_code",
               "icd_description")
SUGGESTIONS = 5
# Spelling suggestions: words of at least this length are corrected, by
# at most one edit, or two from LONG_WORD letters; at most VARIANTS
# corrected phrases are looked up.
MIN_WORD = 4
LONG_WORD = 8
VARIANTS = 4


class ResolutionError(Exception):
    """A vocabulary load failure carrying a finding code only."""


@dataclasses.dataclass(frozen=True)
class ConceptRow:
    """One vocabulary concept: a standard condition or an ICD code."""

    concept_id: int
    kind: str
    vocabulary_id: str
    concept_class_id: str
    concept_code: str
    invalid_reason: str
    name: str


class Vocabulary:
    """The loaded condition vocabulary with its lookup indexes."""

    def __init__(self, rows, names, synonyms, codes, ancestors, descendants,
                 icd_maps, digest):
        """Keep the parsed tables; ancestors are sorted by ancestor."""
        self.rows = rows
        self.names = names
        self.synonyms = synonyms
        labels = collections.defaultdict(set)
        for table in (names, synonyms):
            for phrase, ids in table.items():
                for concept_id in ids:
                    labels[concept_id].add(tuple(phrase.split()))
        self.labels = {key: frozenset(value)
                       for key, value in labels.items()}
        self.codes = codes
        self.ancestors = ancestors
        self.descendants = descendants
        self.icd_maps = icd_maps
        self.digest = digest
        self.standard_count = sum(1 for row in rows.values()
                                  if row.kind == "standard")
        self._phrases = {}
        self._labelled = None
        self._words = None

    def span(self, concept_id):
        """Return the (start, end) of a concept's descendant pairs."""
        start = bisect.bisect_left(self.ancestors, concept_id)
        end = bisect.bisect_right(self.ancestors, concept_id, start)
        return start, end

    def descendant_count(self, concept_id):
        """Return how many standard conditions descend from a concept."""
        start, end = self.span(concept_id)
        return end - start

    def descendants_of(self, concept_id):
        """Return the descendant concept ids of a concept (self included)."""
        start, end = self.span(concept_id)
        return self.descendants[start:end]

    def is_ancestor(self, ancestor, descendant):
        """Return True when descendant descends from ancestor (or equals)."""
        start, end = self.span(ancestor)
        index = bisect.bisect_left(self.descendants, descendant, start, end)
        return index < end and self.descendants[index] == descendant

    def labelled(self):
        """Return every (name or synonym as words, concepts) pair."""
        if self._labelled is None:
            self._labelled = tuple(
                (tuple(key.split()), ids)
                for table in (self.names, self.synonyms)
                for key, ids in table.items())
        return self._labelled

    def words(self):
        """Return the alphabetic words of names and synonyms with how many
        names and synonyms hold them, by first letter."""
        if self._words is None:
            found = collections.defaultdict(collections.Counter)
            for words, _ in self.labelled():
                for word in set(words):
                    if word.isalpha():
                        found[word[0]][word] += 1
            self._words = {letter: dict(group)
                           for letter, group in found.items()}
        return self._words

    def phrases(self, concepts):
        """Return the normalised names and synonyms of concepts and their
        descendants, as word tuples."""
        key = tuple(sorted(concepts))
        if key not in self._phrases:
            found = set()
            for concept in key:
                for descendant in self.descendants_of(concept):
                    found.update(self.labels.get(descendant, ()))
            self._phrases[key] = frozenset(found)
        return self._phrases[key]


@dataclasses.dataclass(frozen=True)
class Resolution:
    """The resolution of one condition phrase."""

    phrase: str
    status: str
    rule: str | None = None
    concepts: tuple = ()
    codes: tuple = ()


# Normalisation

def normalize(text):
    """Return the normalised form of a phrase or vocabulary name."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold().replace("’", "'").replace("&", " and ")
    text = re.sub(r"'s\b", "", text)
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return " ".join(text.split())


def singulars(phrase):
    """Return singular forms of the phrase's last word, most likely first.

    They are only tried after the phrase itself fails, so a wrong guess
    such as "diabete" can only fail to match.
    """
    words = phrase.split()
    if not words or len(words[-1]) <= 3 or not words[-1].endswith("s") \
            or words[-1].endswith("ss"):
        return []
    last = words[-1]
    forms = []
    if last.endswith("ies"):
        forms.append(last[:-3] + "y")
    if last.endswith("es"):
        forms.append(last[:-2])
    forms.append(last[:-1])
    return [" ".join(words[:-1] + [form]) for form in forms]


def code_key(text):
    """Return an ICD code without dots, spaces or case differences."""
    return re.sub(r"[.\s]", "", (text or "").upper())


def is_code(text):
    """Return True when text has the form of an ICD-9 or ICD-10 code."""
    key = code_key(text)
    return bool(ICD10.match(key) or ICD9.match(key))


# Loading

def tsv(data, columns):
    """Return the rows of verified vocabulary TSV bytes."""
    reader = csv.DictReader(io.StringIO(data.decode("utf-8"), newline=""),
                            delimiter="\t", quoting=csv.QUOTE_NONE)
    if tuple(reader.fieldnames or ()) != columns:
        raise ResolutionError("vocabulary_columns_unexpected")
    return reader


def index_name(table, text, concept_id):
    """Add one name or synonym to an index."""
    key = normalize(text)
    if key:
        table.setdefault(key, set()).add(concept_id)


def parse(files, digest=""):
    """Return a Vocabulary from the bytes of the four vocabulary files."""
    rows, names, synonyms, codes = {}, {}, {}, {}
    for row in tsv(files[build.FILES["concepts"]], build.COLUMNS["concepts"]):
        concept = ConceptRow(int(row["concept_id"]), row["kind"],
                             row["vocabulary_id"], row["concept_class_id"],
                             row["concept_code"], row["invalid_reason"],
                             row["concept_name"])
        rows[concept.concept_id] = concept
        if concept.kind == "standard":
            index_name(names, concept.name, concept.concept_id)
        else:
            codes.setdefault(code_key(concept.concept_code), set()).add(
                concept.concept_id)
    for row in tsv(files[build.FILES["synonyms"]], build.COLUMNS["synonyms"]):
        concept_id = int(row["concept_id"])
        text = row["synonym"]
        head, dash, tail = text.partition(" - ")
        if dash and ACRONYM.match(head.strip()):
            index_name(synonyms, head, concept_id)
            index_name(synonyms, tail, concept_id)
        else:
            index_name(synonyms, text, concept_id)
    pairs = [(int(row["ancestor_id"]), int(row["descendant_id"]))
             for row in tsv(files[build.FILES["ancestors"]],
                            build.COLUMNS["ancestors"])]
    pairs.sort()
    ancestors = array.array("q", (pair[0] for pair in pairs))
    descendants = array.array("q", (pair[1] for pair in pairs))
    icd_maps = collections.defaultdict(set)
    for row in tsv(files[build.FILES["icd_maps"]], build.COLUMNS["icd_maps"]):
        icd_maps[int(row["icd_id"])].add(int(row["standard_id"]))
    freeze = lambda table: {key: frozenset(value)  # noqa: E731
                            for key, value in table.items()}
    return Vocabulary(rows, freeze(names), freeze(synonyms), freeze(codes),
                      ancestors, descendants, freeze(icd_maps), digest)


def real_plan():
    """Return the pinned vocabulary manifest."""
    return {"directory": build.OUTPUT_DIR,
            "manifest": {"path": build.OUTPUT_DIR + "\\"
                                 + build.MANIFEST_NAME,
                         "size_bytes": MANIFEST_SIZE,
                         "sha256": MANIFEST_SHA256}}


def load(plan=None):
    """Load the vocabulary through its pinned manifest; verify every file."""
    plan = plan or real_plan()
    held = []
    try:
        entry = plan["manifest"]
        data, signature = mapping.read_verified(
            entry["path"], "vocabulary_manifest",
            "input_hash_mismatch:vocabulary_manifest",
            sha256=entry["sha256"], size=entry["size_bytes"])
        held.append((entry["path"], signature, "vocabulary_manifest"))
        manifest = json.loads(data.decode("utf-8"))
        if manifest.get("schema") != build.SCHEMA:
            raise ResolutionError("vocabulary_manifest_unexpected")
        files = {}
        for name in build.FILES.values():
            record = manifest["files"][name]
            path = plan["directory"] + "\\" + name
            files[name], signature = mapping.read_verified(
                path, name, "input_hash_mismatch:%s" % name,
                sha256=record["sha256"], size=record["size_bytes"])
            held.append((path, signature, name))
        mapping.confirm_unchanged(held)
    except mapping.MappingError as error:
        raise ResolutionError("vocabulary_identity:%s" % error) from None
    except (KeyError, UnicodeDecodeError, ValueError):
        raise ResolutionError("vocabulary_manifest_unexpected") from None
    return parse(files, plan["manifest"]["sha256"].lower())


# Resolution

def decide(vocab, phrase, rule, ids):
    """Return the resolution for the concepts one rule produced."""
    concepts = tuple(sorted(ids))
    if len(concepts) > 1:
        roots = [concept for concept in concepts
                 if all(vocab.is_ancestor(concept, other)
                        for other in concepts)]
        if len(roots) != 1:
            return Resolution(phrase, "ambiguous", rule, concepts)
        concepts = (roots[0],)
    if vocab.descendant_count(concepts[0]) > BROAD_FRACTION * \
            vocab.standard_count:
        return Resolution(phrase, "too_broad", rule, concepts)
    return Resolution(phrase, "resolved", rule, concepts)


def resolve(text, vocab, suggestions=True):
    """Resolve one condition phrase by the fixed rules; an unknown phrase
    comes with suggestions unless they are not wanted."""
    phrase = normalize(text)
    if not phrase:
        return Resolution(phrase, "unknown")
    if is_code(text) and code_key(text) in vocab.codes:
        return Resolution(phrase, "resolved", "icd_code",
                          codes=(code_key(text),))
    candidates = [phrase] + singulars(phrase)
    for rule, table in (("name", vocab.names), ("synonym", vocab.synonyms)):
        for candidate in candidates:
            ids = table.get(candidate)
            if ids:
                return decide(vocab, candidate, rule, ids)
    return Resolution(phrase, "unknown", concepts=suggest(vocab, phrase)
                      if suggestions else ())


def edit_distance(first, second, limit):
    """Return the edit distance of two words, or limit + 1 when it is
    larger than limit."""
    if abs(len(first) - len(second)) > limit:
        return limit + 1
    previous = list(range(len(second) + 1))
    for row, char in enumerate(first, 1):
        current = [row] + [0] * len(second)
        for column, other in enumerate(second, 1):
            current[column] = min(previous[column] + 1,
                                  current[column - 1] + 1,
                                  previous[column - 1] + (char != other))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def spellings(vocab, phrase):
    """Return phrases with each unknown word replaced by its closest
    spellings in the vocabulary, closest first."""
    index = vocab.words()
    options, changed = [], False
    for word in phrase.split():
        group = index.get(word[:1], {})
        if len(word) < MIN_WORD or not word.isalpha() or word in group:
            options.append([word])
            continue
        limit = 2 if len(word) >= LONG_WORD else 1
        close = sorted((edit_distance(word, other, limit), -count, other)
                       for other, count in group.items()
                       if abs(len(other) - len(word)) <= limit)
        close = [other for distance, _, other in close
                 if distance <= limit][:2]
        changed = changed or bool(close)
        options.append(close or [word])
    if not changed:
        return []
    return [" ".join(words) for words in itertools.islice(
        itertools.product(*options), VARIANTS)]


def containing(vocab, words, rank, ranked):
    """Rank concepts whose name or synonym begins with the words (rank)
    or contains them (rank + 1)."""
    for candidate, ids in vocab.labelled():
        if words[0] not in candidate:
            continue
        if candidate[:len(words)] == words:
            found = rank
        elif any(candidate[start:start + len(words)] == words
                 for start in range(1, len(candidate))):
            found = rank + 1
        else:
            continue
        for concept in ids:
            score = (found, len(candidate),
                     -vocab.descendant_count(concept), concept)
            if concept not in ranked or score < ranked[concept]:
                ranked[concept] = score


def suggest(vocab, phrase, limit=SUGGESTIONS):
    """Return concepts whose name or synonym begins with the phrase or
    contains its words: names that begin with it first, then shorter
    names, then broader concepts. Without any, the names and synonyms
    of the closest spellings. Never a resolution."""
    words = tuple(phrase.split())
    if not words:
        return ()
    ranked = {}
    containing(vocab, words, 0, ranked)
    if not ranked:
        variants = spellings(vocab, phrase)
        for variant in variants:
            for table in (vocab.names, vocab.synonyms):
                for concept in table.get(variant, ()):
                    score = (0, len(variant.split()),
                             -vocab.descendant_count(concept), concept)
                    if concept not in ranked or score < ranked[concept]:
                        ranked[concept] = score
        if not ranked and variants:
            containing(vocab, tuple(variants[0].split()), 2, ranked)
    ordered = sorted(ranked, key=ranked.get)
    broad = BROAD_FRACTION * vocab.standard_count
    return tuple(concept for concept in ordered
                 if vocab.descendant_count(concept) <= broad)[:limit]


# Matching

def negated(words, start):
    """Return True when the phrase at start is preceded by a negation."""
    before = words[max(0, start - 3):start]
    if before[-1:] and before[-1] in NEGATIONS:
        return True
    if before[-2:] == ["w", "o"]:
        return True
    return before == ["without", "mention", "of"]


def contains_phrase(text, phrases):
    """Return True when text holds one of the phrases, not negated."""
    words = normalize(text).split()
    first = collections.defaultdict(list)
    for phrase in phrases:
        if phrase:
            first[phrase[0]].append(phrase)
    for start, word in enumerate(words):
        for phrase in first.get(word, ()):
            if (tuple(words[start:start + len(phrase)]) == phrase
                    and not negated(words, start)):
                return True
    return False


def match(resolution, vocab, concept_id, source_code=None,
          source_concept_id=None):
    """Return how a record matches a resolved phrase, or None.

    concept_id is the record's standard concept (0 when it has none);
    source_code and source_concept_id identify its ICD source concept.
    """
    if resolution.status != "resolved":
        return None
    if resolution.rule == "icd_code":
        key = code_key(source_code)
        for code in resolution.codes:
            if key == code:
                return "icd_code"
            if key.startswith(code):
                return "more_specific_icd_code"
        return None
    if concept_id:
        for concept in resolution.concepts:
            if concept_id == concept:
                return "exact"
            if vocab.is_ancestor(concept, concept_id):
                return "more_specific"
        return None
    row = vocab.rows.get(source_concept_id)
    if row is None or row.kind != "icd":
        return None
    if contains_phrase(row.name, vocab.phrases(resolution.concepts)):
        return "icd_description"
    return None
