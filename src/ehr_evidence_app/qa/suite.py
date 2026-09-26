"""Development question suite with an independent oracle (work item 34.1).

The suite asks generated questions about every admitted case of a
development case set, in every scope, through the whole pipeline
(qa.answer), and scores each response against an oracle. The oracle reads
no bundle and calls nothing of the pipeline: it computes the expected
response from the verified source snapshot, the case set's visits and the
condition vocabulary's tables:

* scope: pre-target history is every case visit before the final one, a
  selected visit is one case visit, and all visits is every case visit. A
  condition record belongs to a scope when its visit does.
* list: every record of the scope, one group per standard concept (per
  source code for a record without one); an empty scope is not recorded.
* presence of a concept, asked by its name, a synonym or the name of a
  broader concept: a record with a standard concept matches when that
  concept is the asked one or descends from it. A record without a
  standard concept may match by its ICD description; that rule has no
  independent reference, so such matches are counted apart, not scored.
* presence of an ICD code: a record matches when its source code equals
  the code or starts with it.
* the fixed outcome of every other family: another scope or a missing
  visit named in the question, unsupported, explanation and other-patient
  requests, and invented, too broad or ambiguous terms.

Evidential responses must also disclose records without a standard
concept and cite exactly the records they report. The questions come from
fixed paraphrase banks written with the rules, so the suite measures the
rules on their own wording; other people's wording is the held-out test.
Probes are drawn in a fixed order from the suite version, so every run
asks the same questions. Only aggregate counts are printed: questions,
concept names and patient identifiers never leave the process.

The vocabulary sweep asks about every standard condition by name and
counts how often interpretation and resolution return that concept; it
reads no patient data.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import sys
import time

from ehr_evidence_app.casesets import registry
from ehr_evidence_app.evidence import bundles, contracts, readers
from ehr_evidence_app.evidence.concepts import NAME_LIMIT
from ehr_evidence_app.qa import answer, interpret, resolve
from ehr_evidence_app.qa import vocabulary as build

SUITE_VERSION = "qa-development-suite-v2"
FAMILIES = ("list", "presence_name", "presence_synonym", "presence_ancestor",
            "presence_code", "presence_absent", "scope_same", "scope_other",
            "visit_missing", "unsupported", "explanation", "other_patient",
            "unknown_term", "too_broad", "ambiguous_term", "ranking",
            "two_requests", "misspelling")
PRESENCE = ("Is {c} recorded?", "Does the patient have {c}?",
            "Any history of {c}?", "Was {c} documented?",
            "Has the patient been diagnosed with {c}?",
            "Is there any record of {c}?", "Check whether {c} is recorded.")
CODES = ("Is {c} recorded?", "Is ICD code {c} recorded?", "Was {c} coded?",
         "Any record of {c}?")
LISTS = ("List all recorded conditions.", "What diagnoses are recorded?",
         "Which conditions are documented?",
         "What has the patient been diagnosed with?",
         "Show me the past medical history.", "Give me all the diagnoses.")
SCOPED_PRESENCE = ("Is {c} recorded {s}?", "Was {c} documented {s}?")
SCOPED_LISTS = ("List the diagnoses {s}.", "What conditions are recorded {s}?")
UNSUPPORTED = ("What medications is the patient taking?",
               "Show the laboratory results.",
               "Which procedures were performed?",
               "How should {c} be treated?",
               "What is the risk of {c} getting worse?",
               "Will the patient survive this admission?",
               "What could the patient have?")
EXPLANATION = ("Why did the model predict {c}?", "Explain this prediction.")
OTHER_PATIENT = ("Is {c} recorded for patient {n}?",
                 "What about the next patient?")
RANKING = ("What were the top three diagnoses?",
           "What is the patient's main diagnosis?",
           "List the five most important conditions.",
           "Which diagnosis was billed first?")
TWO_REQUESTS = ("List all recorded conditions. Is {c} recorded?",
                "Show all diagnoses and tell me whether {c} is recorded.",
                "What conditions are documented; does the patient have "
                "{c}?")
MISSPELT = ("Is {c} recorded?", "Does the patient have {c}?")
NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten")
INVENTED = ("florbnitis", "quazzle syndrome", "vextral fever",
            "plimbic disorder", "zorvanian palsy")
BROAD = ("disease", "disorder", "clinical finding", "finding")
ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth",
            "seventh", "eighth", "ninth", "tenth")
LIMITS = {"presence_name": 2, "presence_synonym": 1, "presence_code": 1,
          "presence_absent": 2, "unsupported": 2}
AMBIGUOUS_POOL = 50
EVIDENTIAL = ("answer", "not_recorded")


class SuiteError(Exception):
    """A suite failure carrying a finding code only."""


# --------------------------------------------------------------------------
# Oracle
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Record:
    """One condition record as the oracle reads it from the snapshot."""

    key: tuple
    concept_id: int
    vocabulary: str
    code_text: str
    code: str
    group: tuple


def oracle_record(event, concepts):
    """Return the oracle's view of one condition row of the snapshot."""
    row = (concepts.get(event.source_concept_id)
           if event.source_concept_id else None)
    if row is not None and row.vocabulary_id and row.code:
        vocabulary = row.vocabulary_id[:NAME_LIMIT]
        code_text = row.code[:NAME_LIMIT]
    else:
        vocabulary = code_text = ""
    if event.concept_id:
        group = ("concept", event.concept_id)
    elif code_text:
        group = ("source", vocabulary, code_text)
    else:
        group = ("value", (event.source_value or "")[:NAME_LIMIT])
    return Record(key=(event.domain, event.native_id),
                  concept_id=event.concept_id, vocabulary=vocabulary,
                  code_text=code_text, code=resolve.code_key(code_text),
                  group=group)


class Oracle:
    """Expected responses computed from the snapshot and the vocabulary."""

    def __init__(self, snapshot, case_set, vocab):
        """Read each admitted case's condition records by visit."""
        self.vocab = vocab
        self.label = case_set.label
        self.cases = tuple(case for case in case_set.cases if case.admitted)
        by_person = collections.defaultdict(list)
        for event in snapshot.events["condition"]:
            by_person[event.person_id].append(event)
        self.records = {}
        for case in self.cases:
            visits = {visit_id: [] for visit_id in case.visit_ids}
            for event in by_person.get(case.person_id, ()):
                if event.visit_occurrence_id in visits:
                    visits[event.visit_occurrence_id].append(
                        oracle_record(event, snapshot.concepts))
            self.records[case.case_number] = visits
        self.recorded = sorted({
            record.concept_id for visits in self.records.values()
            for rows in visits.values() for record in rows
            if record.concept_id})
        self.broad = resolve.BROAD_FRACTION * vocab.standard_count
        self.parents = self.proper_ancestors(self.recorded)
        self._members = {}

    def proper_ancestors(self, concepts):
        """Return the proper ancestors of each of the given concepts."""
        wanted = set(concepts)
        found = collections.defaultdict(list)
        for ancestor, descendant in zip(self.vocab.ancestors,
                                        self.vocab.descendants):
            if descendant in wanted and ancestor != descendant:
                found[descendant].append(ancestor)
        return found

    def scopes(self, case):
        """Return the scopes of a case: history, each visit, all visits."""
        count = len(case.visit_ids)
        found = ([contracts.ScopeRef(kind="pre_target_history")]
                 if count > 1 else [])
        found += [contracts.ScopeRef(kind="selected_visit",
                                     visit_number=number)
                  for number in range(1, count + 1)]
        found.append(contracts.ScopeRef(kind="case_visits"))
        return found

    def scope_records(self, case, scope):
        """Return the records of one case within one scope."""
        ids = case.visit_ids
        if scope.kind == "pre_target_history":
            ids = ids[:-1]
        elif scope.kind == "selected_visit":
            ids = ids[scope.visit_number - 1:scope.visit_number]
        visits = self.records[case.case_number]
        return [record for visit_id in ids for record in visits[visit_id]]

    def case_concepts(self, case):
        """Return the standard concepts recorded in any visit of a case."""
        return sorted({record.concept_id
                       for rows in self.records[case.case_number].values()
                       for record in rows if record.concept_id})

    def members(self, concept):
        """Return a concept and its descendants."""
        if concept not in self._members:
            self._members[concept] = frozenset(
                self.vocab.descendants_of(concept))
        return self._members[concept]

    def matches(self, records, concept):
        """Return the records whose standard concept is the concept or
        descends from it."""
        members = self.members(concept)
        return frozenset(record.key for record in records
                         if record.concept_id in members)

    @staticmethod
    def code_matches(records, code):
        """Return the records whose source code equals or starts with the
        code (both without dots)."""
        return frozenset(record.key for record in records
                         if record.code and record.code.startswith(code))

    def usable(self, concept):
        """Return why a concept cannot be asked about by name, or None."""
        row = self.vocab.rows.get(concept)
        if row is None or row.kind != "standard":
            return "concept_not_in_vocabulary"
        if self.vocab.names.get(resolve.normalize(row.name)) != frozenset(
                (concept,)):
            return "name_shared"
        if self.vocab.descendant_count(concept) > self.broad:
            return "concept_too_broad"
        return None

    def names_nothing_else(self, phrase):
        """Return True when neither the phrase nor its singular form is a
        concept name, and the phrase is not an ICD code."""
        return not resolve.is_code(phrase) and not any(
            form in self.vocab.names
            for form in [phrase] + resolve.singulars(phrase))

    def synonym(self, concept, *key):
        """Return a synonym that names only this concept, or None."""
        name = resolve.normalize(self.vocab.rows[concept].name)
        options = [" ".join(words)
                   for words in self.vocab.labels.get(concept, ())]
        options = [phrase for phrase in options
                   if phrase != name
                   and self.vocab.synonyms.get(phrase) == frozenset(
                       (concept,))
                   and self.names_nothing_else(phrase)]
        return ordered(options, *key)[0] if options else None

    def broader(self, concept):
        """Return the most specific proper ancestor that can be asked about
        by name, or None."""
        options = [ancestor for ancestor in self.parents.get(concept, ())
                   if self.usable(ancestor) is None]
        if not options:
            return None
        return min(options, key=lambda ancestor: (
            self.vocab.descendant_count(ancestor), ancestor))

    def invented_terms(self):
        """Return the invented terms that name nothing in the vocabulary."""
        return [phrase for phrase in INVENTED
                if self.names_nothing_else(phrase)
                and phrase not in self.vocab.synonyms]

    def broad_terms(self):
        """Return the fixed broad terms that name one too broad concept."""
        found = []
        for phrase in BROAD:
            ids = self.vocab.names.get(phrase) or ()
            if len(ids) == 1 and self.vocab.descendant_count(
                    next(iter(ids))) > self.broad:
                found.append(phrase)
        return found

    def ambiguous_terms(self):
        """Return short synonyms that name several concepts, none of them
        an ancestor of all the others."""
        pool = []
        for phrase, ids in self.vocab.synonyms.items():
            if len(ids) < 2 or len(phrase.split()) > 3 \
                    or not self.names_nothing_else(phrase):
                continue
            if any(all(self.vocab.is_ancestor(root, other) for other in ids)
                   for root in ids):
                continue
            pool.append(phrase)
        return sorted(pool, key=lambda phrase: (len(phrase.split()),
                                                phrase))[:AMBIGUOUS_POOL]


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Probe:
    """One generated question and the response the oracle expects."""

    family: str
    question: str
    case_number: int
    scope: contracts.ScopeRef
    outcome: str
    reasons: tuple = ()
    task: str | None = None
    records: frozenset | None = None
    groups: frozenset | None = None
    coded_only: bool = False
    subject: int | str | None = None
    suggests: bool = False


def slug(scope):
    """Return the short name of a scope used in probe keys."""
    if scope.kind == "pre_target_history":
        return "history"
    if scope.kind == "selected_visit":
        return "visit-%d" % scope.visit_number
    return "all"


def ordered(items, *key):
    """Return items in the suite's fixed pseudo-random order for a key."""
    def rank(item):
        """Return the item's place for this key."""
        text = "|".join(str(part) for part in (SUITE_VERSION,) + key
                        + (item,))
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    return sorted(items, key=rank)


def choose(options, *key):
    """Return the option placed first for a key."""
    return ordered(options, *key)[0]


def scope_phrases(scope, visit_count):
    """Return phrases that name a scope for a case."""
    if scope.kind == "pre_target_history":
        return ("before the final admission", "in previous admissions",
                "prior to the last admission",
                "in her prior hospitalizations", "before this admission")
    if scope.kind == "case_visits":
        return ("in any admission", "across all admissions",
                "in the entire record", "across her admissions",
                "throughout his hospital stays")
    number = scope.visit_number
    phrases = ["in visit %d" % number, "during admission %d" % number,
               "during hospital stay %d" % number]
    if number <= len(ORDINALS):
        phrases += ["in the %s admission" % ORDINALS[number - 1],
                    "in admission %s" % NUMBER_WORDS[number - 1]]
    if number == visit_count:
        phrases += ["in the final admission", "during the last admission",
                    "during this admission", "in the present admission"]
    return tuple(phrases)


class ScopeProbes:
    """The probes of one case and one scope."""

    def __init__(self, oracle, case, scope, scopes, skipped):
        """Read the scope's records and the concepts that can be named."""
        self.oracle = oracle
        self.case = case
        self.scope = scope
        self.scopes = scopes
        self.skipped = skipped
        self.records = oracle.scope_records(case, scope)
        self.key = (case.case_number, slug(scope))
        self.disclosed = (("unmapped_records_in_scope",)
                          if any(not record.concept_id
                                 for record in self.records) else ())
        self.present = sorted({record.concept_id for record in self.records
                               if record.concept_id})
        self.named = []
        for concept in ordered(self.present, *self.key, "named"):
            problem = oracle.usable(concept)
            if problem:
                skipped["presence_name:" + problem] += 1
            else:
                self.named.append(concept)

    def probe(self, family, question, **fields):
        """Return a probe of this case and scope."""
        return Probe(family=family, question=question,
                     case_number=self.case.case_number, scope=self.scope,
                     **fields)

    def name(self, concept):
        """Return a concept's vocabulary name."""
        return self.oracle.vocab.rows[concept].name

    def listing(self, family, question):
        """Return a list probe: every record, grouped."""
        return self.probe(
            family, question,
            outcome="answer" if self.records else "not_recorded",
            reasons=self.disclosed, task="condition_list",
            records=frozenset(record.key for record in self.records),
            groups=frozenset(record.group for record in self.records))

    def presence(self, family, question, concept):
        """Return a presence probe for a concept."""
        expected = self.oracle.matches(self.records, concept)
        return self.probe(
            family, question,
            outcome="answer" if expected else "not_recorded",
            reasons=self.disclosed, task="condition_presence",
            records=expected, coded_only=True, subject=concept)

    def asked(self, family, concept, phrase):
        """Return a presence probe asking about a concept by a phrase."""
        template = choose(PRESENCE, *self.key, family, concept)
        return self.presence(family, template.format(c=phrase), concept)

    def all(self):
        """Return every probe of the scope."""
        found = [self.listing("list", choose(LISTS, *self.key, "list"))]
        found += [self.asked("presence_name", concept, self.name(concept))
                  for concept in self.named[:LIMITS["presence_name"]]]
        return (found + self.synonyms() + self.broader() + self.codes()
                + self.absent() + self.scoped())

    def synonyms(self):
        """Return presence probes that name a concept by a synonym."""
        found = []
        for concept in self.named:
            phrase = self.oracle.synonym(concept, *self.key)
            if phrase:
                found.append(self.asked("presence_synonym", concept, phrase))
            if len(found) == LIMITS["presence_synonym"]:
                return found
        self.skipped["presence_synonym:none"] += 1
        return found

    def broader(self):
        """Return a presence probe that names a broader concept."""
        for concept in ordered(self.present, *self.key, "broader"):
            ancestor = self.oracle.broader(concept)
            if ancestor is not None:
                return [self.asked("presence_ancestor", ancestor,
                                   self.name(ancestor))]
        self.skipped["presence_ancestor:none"] += 1
        return []

    def codes(self):
        """Return presence probes that name an ICD code of the scope."""
        options = sorted({(record.code_text, record.code)
                          for record in self.records
                          if record.vocabulary in build.ICD_VOCABULARIES
                          and record.code in self.oracle.vocab.codes})
        if not options:
            self.skipped["presence_code:none"] += 1
            return []
        found = []
        for text, code in ordered(options, *self.key, "code")[
                :LIMITS["presence_code"]]:
            template = choose(CODES, *self.key, "code", code)
            found.append(self.probe(
                "presence_code", template.format(c=text), outcome="answer",
                reasons=self.disclosed, task="condition_presence",
                records=self.oracle.code_matches(self.records, code),
                subject=code))
        return found

    def absent(self):
        """Return presence probes for concepts recorded elsewhere in the
        case set that nothing in this scope matches."""
        found = []
        for concept in ordered(self.oracle.recorded, *self.key, "absent"):
            if len(found) == LIMITS["presence_absent"]:
                break
            if (self.oracle.usable(concept) is None
                    and not self.oracle.matches(self.records, concept)):
                found.append(self.asked("presence_absent", concept,
                                        self.name(concept)))
        return found

    def scoped(self):
        """Return one question naming this scope and one naming another."""
        count = len(self.case.visit_ids)
        phrase = choose(scope_phrases(self.scope, count), *self.key, "same")
        if self.named and choose(("presence", "list"), *self.key,
                                 "same-task") == "presence":
            concept = self.named[0]
            template = choose(SCOPED_PRESENCE, *self.key, "same", concept)
            same = self.presence("scope_same", template.format(
                c=self.name(concept), s=phrase), concept)
        else:
            template = choose(SCOPED_LISTS, *self.key, "same")
            same = self.listing("scope_same", template.format(s=phrase))
        others = [(slug(scope), phrase) for scope in self.scopes
                  if scope != self.scope
                  for phrase in scope_phrases(scope, count)]
        _, phrase = choose(others, *self.key, "other")
        if self.named and choose(("presence", "list"), *self.key,
                                 "other-task") == "presence":
            template = choose(SCOPED_PRESENCE, *self.key, "other")
            question = template.format(c=self.name(self.named[0]), s=phrase)
            task = "condition_presence"
        else:
            question = choose(SCOPED_LISTS, *self.key, "other").format(
                s=phrase)
            task = "condition_list"
        return [same, self.probe("scope_other", question,
                                 outcome="clarification",
                                 reasons=("scope_ambiguous",), task=task)]


def case_probes(oracle, case, pools, skipped):
    """Return the probes of one admitted case."""
    scopes = oracle.scopes(case)
    probes = []
    for scope in scopes:
        probes += ScopeProbes(oracle, case, scope, scopes, skipped).all()
    key = (case.case_number,)
    names = [concept for concept in ordered(oracle.case_concepts(case), *key,
                                            "fixed")
             if oracle.usable(concept) is None]
    if not names:
        skipped["fixed:no_named_concept"] += 1
        return probes
    name = oracle.vocab.rows[names[0]].name
    scope = choose(scopes, *key, "fixed-scope")

    def fixed(family, question, outcome, reason, task=None):
        """Return a probe whose outcome does not depend on records."""
        return Probe(family=family, question=question,
                     case_number=case.case_number, scope=scope,
                     outcome=outcome, reasons=(reason,), task=task)

    missing = "in visit %d" % (len(case.visit_ids) + 1 + case.case_number % 3)
    probes.append(fixed(
        "visit_missing", choose(SCOPED_PRESENCE, *key, "missing").format(
            c=name, s=missing), "clarification", "visit_ambiguous",
        "condition_presence"))
    probes += [fixed("unsupported", template.format(c=name), "abstention",
                     "task_unsupported")
               for template in ordered(UNSUPPORTED, *key, "unsupported")[
                   :LIMITS["unsupported"]]]
    probes.append(fixed(
        "explanation", choose(EXPLANATION, *key, "explanation").format(
            c=name), "clarification", "prediction_selection_needed"))
    probes.append(fixed(
        "other_patient", choose(OTHER_PATIENT, *key, "other").format(
            c=name, n=case.case_number + 40), "clarification",
        "case_ambiguous"))
    probes.append(fixed("ranking", choose(RANKING, *key, "ranking"),
                        "abstention", "task_unsupported"))
    probes.append(fixed(
        "two_requests", choose(TWO_REQUESTS, *key, "two").format(c=name),
        "clarification", "task_ambiguous"))
    misspelt = misspelling(oracle, names)
    if misspelt:
        concept, phrase = misspelt
        probes.append(Probe(
            family="misspelling",
            question=choose(MISSPELT, *key, "misspelt").format(c=phrase),
            case_number=case.case_number, scope=scope,
            outcome="clarification", reasons=("concept_unknown",),
            task="condition_presence", subject=concept, suggests=True))
    else:
        skipped["misspelling:none"] += 1
    for family, reason in (("unknown_term", "concept_unknown"),
                           ("too_broad", "concept_too_broad"),
                           ("ambiguous_term", "concept_ambiguous")):
        if not pools[family]:
            skipped[family + ":none"] += 1
            continue
        term = choose(pools[family], *key, family)
        probes.append(fixed(family, choose(PRESENCE, *key, family).format(
            c=term), "clarification", reason, "condition_presence"))
    return probes


def misspelling(oracle, concepts):
    """Return (concept, name with one letter dropped) for the first
    concept whose misspelt name names nothing and holds no out-of-scope
    word, or None."""
    vocab = oracle.vocab
    for concept in concepts:
        words = resolve.normalize(vocab.rows[concept].name).split()
        if interpret.guard_of(" ".join(words)):
            continue
        longest = max(range(len(words)), key=lambda at: len(words[at]))
        word = words[longest]
        if len(word) < 6 or not word.isalpha():
            continue
        wrong = word[:len(word) // 2] + word[len(word) // 2 + 1:]
        phrase = " ".join(words[:longest] + [wrong] + words[longest + 1:])
        if (wrong in vocab.words().get(wrong[0], ())
                or not oracle.names_nothing_else(phrase)
                or phrase in vocab.synonyms):
            continue
        return concept, phrase
    return None


def generate(oracle):
    """Return the probes of every admitted case and the skipped counts."""
    skipped = collections.Counter()
    pools = {"unknown_term": oracle.invented_terms(),
             "too_broad": oracle.broad_terms(),
             "ambiguous_term": oracle.ambiguous_terms()}
    probes = []
    for case in oracle.cases:
        probes += case_probes(oracle, case, pools, skipped)
    return probes, skipped


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def cited(response, bundle):
    """Return the records the response's claims cite."""
    events = {event.ref: (event.domain, event.native_id)
              for event in bundle.events}
    return frozenset(events[ref] for claim in response.claims
                     for ref in claim.refs if ref in events)


def score(probe, result, bundle, unmapped):
    """Return (failure categories, unscored matches) of one response.

    unmapped holds the records without a standard concept; for a concept
    probe their matches are unscored and never a failure.
    """
    response = result.response
    matched = frozenset(result.matched)
    unscored = (matched & unmapped) if probe.coded_only else frozenset()
    outcome = probe.outcome
    if outcome == "not_recorded" and unscored:
        outcome = "answer"
    if response.outcome != outcome:
        return ["outcome"], unscored
    failures = []
    if response.reason_codes != probe.reasons:
        failures.append("reason")
    if response.task != probe.task:
        failures.append("task")
    if response.scope != probe.scope:
        failures.append("scope")
    if probe.suggests and (result.resolution is None or probe.subject
                           not in result.resolution.concepts):
        failures.append("suggestion_missing")
    if response.outcome not in EVIDENTIAL:
        return failures, unscored
    references = cited(response, bundle)
    if probe.task == "condition_list":
        returned = references
        if response.outcome == "answer" and frozenset(
                result.groups) != probe.groups:
            failures.append("groups")
    else:
        returned = matched - unscored
        if references != matched:
            failures.append("citations")
    if response.outcome == "not_recorded" and [
            claim.refs for claim in response.claims] != [("C1",)]:
        failures.append("citations")
    if probe.records - returned:
        failures.append("records_missing")
    if returned - probe.records:
        failures.append("records_extra")
    return sorted(set(failures)), unscored


def stage(question, task, subject, vocab):
    """Return where a question departs from the expected reading."""
    interpretation = interpret.interpret(question)
    if interpretation.status != "task":
        return "interpretation_%s_%s" % (interpretation.status,
                                         interpretation.reason)
    if interpretation.task != task:
        return "interpretation_%s" % interpretation.task
    if subject is None:
        return "answer"
    resolution = answer.resolve_phrase(interpretation, vocab,
                                       suggestions=False)
    if interpretation.guard and resolution.status == "unknown":
        return "interpretation_%s_%s" % interpretation.guard[:2]
    if resolution.status != "resolved":
        return "resolution_%s" % resolution.status
    found = (resolution.codes if isinstance(subject, str)
             else resolution.concepts)
    return "answer" if subject in found else "resolution_other"


class BundleCache:
    """A bundle set whose bundles are verified and checked once each."""

    def __init__(self, bundle_set, inputs):
        """Keep the bundle set and the input digests bundles must name."""
        self.bundle_set = bundle_set
        self.inputs = inputs
        self.read = {}

    def visit_count(self, case_number):
        """Return how many visits a case has."""
        return self.bundle_set.visit_count(case_number)

    def qa_bundle(self, case_number, scope):
        """Return (bundle, SHA-256); a bundle built from other inputs than
        the oracle's fails the run."""
        key = (case_number, scope)
        if key not in self.read:
            bundle, digest = self.bundle_set.qa_bundle(case_number, scope)
            provenance = bundle.provenance
            if (provenance.source_snapshot.sha256,
                    provenance.closure_record.sha256,
                    provenance.identity_mapping.sha256) != self.inputs:
                raise SuiteError("bundle_inputs_differ")
            self.read[key] = (bundle, digest)
        return self.read[key]


class Tally:
    """Aggregate counts of a suite run."""

    def __init__(self):
        """Start every count at zero."""
        self.families = {family: [0, 0] for family in FAMILIES}
        self.outcomes = collections.Counter()
        self.failures = collections.Counter()
        self.diagnostics = collections.Counter()
        self.records = collections.Counter()
        self.unscored = collections.Counter()

    def add(self, probe, response, failures, unscored, returned, diagnosis):
        """Count one scored response."""
        entry = self.families[probe.family]
        entry[0] += 1
        entry[1] += not failures
        self.outcomes["%s -> %s" % (probe.outcome, response.outcome)] += 1
        self.failures.update(failures)
        if diagnosis:
            self.diagnostics["%s: %s" % (probe.family, diagnosis)] += 1
        if unscored:
            self.unscored["questions"] += 1
            self.unscored["records"] += len(unscored)
        if returned is not None:
            self.records["expected"] += len(probe.records)
            self.records["returned"] += len(returned)
            self.records["true_positive"] += len(probe.records & returned)

    def summary(self):
        """Return the counts as a plain document."""
        questions = sum(entry[0] for entry in self.families.values())
        correct = sum(entry[1] for entry in self.families.values())
        records = dict(self.records)
        return {"questions": questions, "correct": correct,
                "families": {family: {"questions": entry[0],
                                      "correct": entry[1]}
                             for family, entry in self.families.items()
                             if entry[0]},
                "outcomes": dict(sorted(self.outcomes.items())),
                "failures": dict(sorted(self.failures.items())),
                "diagnostics": dict(sorted(self.diagnostics.items())),
                "presence_records": {key: records.get(key, 0) for key in (
                    "expected", "returned", "true_positive")},
                "unscored_description_matches": {
                    key: self.unscored.get(key, 0)
                    for key in ("questions", "records")}}


def run(oracle, bundle_set, vocab, limits, inputs, digests):
    """Ask every probe through the pipeline and score the responses.

    inputs are the (snapshot, closure, mapping) digests every bundle must
    name; digests are the input digests the summary records.
    """
    started = time.monotonic()
    probes, skipped = generate(oracle)
    cache = BundleCache(bundle_set, inputs)
    unmapped = frozenset(
        record.key for visits in oracle.records.values()
        for rows in visits.values() for record in rows
        if not record.concept_id)
    tally = Tally()
    for index, probe in enumerate(probes, 1):
        result = answer.answer(probe.question, oracle.label,
                               probe.case_number, probe.scope, cache, vocab,
                               "q-%05d" % index, limits)
        response = result.response
        bundle = (cache.qa_bundle(probe.case_number, probe.scope)[0]
                  if response.bundle is not None else None)
        failures, unscored = score(probe, result, bundle, unmapped)
        returned = None
        if (probe.task == "condition_presence" and probe.records is not None
                and response.outcome in EVIDENTIAL):
            returned = frozenset(result.matched) - unscored
        diagnosis = (stage(probe.question, probe.task, probe.subject, vocab)
                     if failures else None)
        tally.add(probe, response, failures, unscored, returned, diagnosis)
    document = {"suite": SUITE_VERSION, "case_set": oracle.label,
                "admitted_cases": len(oracle.cases),
                "rules": {"interpretation": interpret.RULES_VERSION,
                          "resolution": resolve.RULES_VERSION,
                          "answer": answer.RULES_VERSION},
                "inputs": digests}
    document.update(tally.summary())
    document["skipped_probes"] = dict(sorted(skipped.items()))
    document["summary_sha256"] = hashlib.sha256(json.dumps(
        document, sort_keys=True, separators=(",", ":")).encode(
            "utf-8")).hexdigest()
    document["elapsed_seconds"] = round(time.monotonic() - started, 1)
    return document


# --------------------------------------------------------------------------
# Vocabulary sweep
# --------------------------------------------------------------------------

def sweep(vocab):
    """Ask about every standard condition by name, in every presence
    wording of the suite; return the counts."""
    counts = collections.Counter()
    broad = resolve.BROAD_FRACTION * vocab.standard_count
    for concept, row in vocab.rows.items():
        if row.kind != "standard":
            continue
        if vocab.names.get(resolve.normalize(row.name)) != frozenset(
                (concept,)):
            counts["names shared with another concept"] += 1
            continue
        if vocab.descendant_count(concept) > broad:
            counts["names of too broad concepts"] += 1
            continue
        counts["names asked"] += 1
        for template in PRESENCE:
            found = stage(template.format(c=row.name), "condition_presence",
                          concept, vocab)
            counts["questions: " + ("returned the concept" if found ==
                                    "answer" else found)] += 1
    return dict(sorted(counts.items()))


# --------------------------------------------------------------------------
# Modes
# --------------------------------------------------------------------------

SYNTHETIC_FILLERS = range(1100, 1120)
SYNTHETIC_NAMES = {900: "Clinical finding", 1000: "Synthetic parent disorder",
                   1001: "Synthetic concept 1001",
                   1002: "Synthetic concept 1002",
                   1003: "Synthetic twin one", 1004: "Synthetic twin two",
                   1005: "Synthetic X-ray finding",
                   1006: "Synthetic injury of other person",
                   1007: "Synthetic reflex present",
                   1008: "Synthetic reflex",
                   **{concept: "Synthetic filler %d" % concept
                      for concept in SYNTHETIC_FILLERS}}
SYNTHETIC_PARENTS = {1000: 900, 1001: 1000, 1002: 1000,
                     **{concept: 900 for concept in (1003, 1004, 1005, 1006,
                                                     1007, 1008)},
                     **{concept: 900 for concept in SYNTHETIC_FILLERS}}
SYNTHETIC_SYNONYMS = ((1001, "Synthetic alpha disorder"),
                      (1002, "Synthetic beta disorder"),
                      (1003, "STW - Synthetic twin"),
                      (1004, "STW - Synthetic twin"))


def synthetic_vocabulary():
    """Return an invented vocabulary aligned with the synthetic bundles."""
    concepts = [(concept, "standard", "SNOMED", "Disorder", "S%d" % concept,
                 "", name) for concept, name in sorted(
                     SYNTHETIC_NAMES.items())]
    pairs = []
    for concept in sorted(SYNTHETIC_NAMES):
        chain = [concept]
        while chain[-1] in SYNTHETIC_PARENTS:
            chain.append(SYNTHETIC_PARENTS[chain[-1]])
        pairs += [(ancestor, concept, level)
                  for level, ancestor in enumerate(chain)]
    return resolve.parse({
        build.FILES["concepts"]: build.encode_tsv("concepts", concepts),
        build.FILES["synonyms"]: build.encode_tsv("synonyms",
                                                  SYNTHETIC_SYNONYMS),
        build.FILES["ancestors"]: build.encode_tsv("ancestors", pairs),
        build.FILES["icd_maps"]: build.encode_tsv("icd_maps", [])})


def synthetic_bundle_set():
    """Return the synthetic bundle set, held in memory."""
    qa, explanations, counts = bundles.run_synthetic()
    manifest = bundles.manifest_document(qa, explanations,
                                         bundles.synthetic_provenance(),
                                         counts)
    data = {"qa/%s.json" % bundle.bundle_id: bundles.bundle_bytes(bundle)
            for bundle in qa}
    return answer.BundleSet(manifest["case_set"], None, manifest,
                            reader=lambda entry: data[entry["path"]])


def run_synthetic():
    """Run the suite and the sweep on invented data; nothing is read."""
    snapshot, case_set, _, _, _ = bundles.synthetic_inputs()
    vocab = synthetic_vocabulary()
    provenance = bundles.synthetic_provenance()
    inputs = (provenance.source_snapshot.sha256,
              provenance.closure_record.sha256,
              provenance.identity_mapping.sha256)
    document = run(Oracle(snapshot, case_set, vocab), synthetic_bundle_set(),
                   vocab, contracts.ExecutionLimits(**bundles.SYNTHETIC_LIMITS),
                   inputs, {"mode": "synthetic"})
    return document, sweep(vocab)


def run_real(case_set):
    """Run the suite on a development case set; return the summary."""
    if case_set != answer.PILOT and registry.spec(case_set)[
            "purpose"] != "development":
        raise SuiteError("case_set_held_out")
    plan = readers.real_plan(None if case_set == answer.PILOT else case_set)
    snapshot = readers.read_snapshot(plan)
    cases = readers.read_cases(plan)
    bundle_plan = answer.bundle_set_plan(case_set)
    bundle_set = answer.load_bundle_set(bundle_plan)
    vocab = resolve.load()
    digests = {"bundle_manifest": bundle_plan["manifest"]["sha256"],
               "vocabulary_manifest": vocab.digest,
               "snapshot_manifest": snapshot.manifest_sha256,
               "closure": cases.closure_sha256}
    return run(Oracle(snapshot, cases, vocab), bundle_set, vocab,
               contracts.load_limits(),
               (snapshot.manifest_sha256, cases.closure_sha256,
                cases.mapping_sha256), digests)


def report(document):
    """Print a suite summary: aggregate counts and digests only."""
    line = "%-44s %s"
    questions, correct = document["questions"], document["correct"]
    for key in ("suite", "case_set", "admitted_cases"):
        print(line % (key, document[key]))
    print(line % ("questions", questions))
    print(line % ("correct", "%d (%.1f%%)" % (
        correct, 100.0 * correct / questions if questions else 0.0)))
    print("families (questions, correct)")
    for family, entry in document["families"].items():
        print(line % ("  " + family, "%d %d" % (entry["questions"],
                                                entry["correct"])))
    for section in ("outcomes", "failures", "diagnostics",
                    "skipped_probes"):
        print(section.replace("_", " "))
        for key, value in document[section].items():
            print(line % ("  " + key, value))
    records = document["presence_records"]
    print(line % ("presence records (expected, returned, correct)",
                  "%d %d %d" % (records["expected"], records["returned"],
                                records["true_positive"])))
    unscored = document["unscored_description_matches"]
    print(line % ("unscored description matches (questions, records)",
                  "%d %d" % (unscored["questions"], unscored["records"])))
    for key, value in document["rules"].items():
        print(line % ("rules " + key, value))
    for key, value in document["inputs"].items():
        print(line % ("input " + key, value))
    print(line % ("summary_sha256", document["summary_sha256"]))
    print(line % ("elapsed_seconds", document["elapsed_seconds"]))


def report_sweep(counts):
    """Print the vocabulary sweep counts."""
    print("vocabulary sweep")
    for key, value in counts.items():
        print("%-60s %d" % ("  " + key, value))


def main(argv=None):
    """Run the synthetic default mode or an operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="run on real inputs; prints aggregate counts "
                             "only and writes nothing")
    parser.add_argument("--case-set",
                        help="the development case set to question "
                             "(dev-30-v1 or pilot-10-v1)")
    parser.add_argument("--vocabulary-sweep", action="store_true",
                        help="ask about every standard condition of the "
                             "condition vocabulary instead (reads no "
                             "patient data)")
    args = parser.parse_args(argv)
    if not args.operator_real and (args.case_set or args.vocabulary_sweep):
        print("FAILED: --case-set and --vocabulary-sweep need "
              "--operator-real")
        return 2
    if args.operator_real and bool(args.case_set) == args.vocabulary_sweep:
        print("FAILED: give exactly one of --case-set and "
              "--vocabulary-sweep")
        return 2
    try:
        if args.operator_real and args.vocabulary_sweep:
            report_sweep(sweep(resolve.load()))
        elif args.operator_real:
            report(run_real(args.case_set))
        else:
            document, counts = run_synthetic()
            report(document)
            report_sweep(counts)
            if document["correct"] != document["questions"] or any(
                    key.startswith("questions: ") and "returned" not in key
                    for key in counts):
                print("FAILED: synthetic_suite_incorrect")
                return 1
    except (SuiteError, answer.AnswerError, resolve.ResolutionError,
            readers.ReaderError, registry.RegistryError,
            contracts.ContractError) as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
