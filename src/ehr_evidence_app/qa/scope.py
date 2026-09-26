"""Scope mentions and scope binding (work item 33).

A question is always answered within the active case and scope chosen in
the case selector. This module finds the scope phrases a question contains
and binds the request:

* no scope phrase: the active scope applies;
* a phrase naming the active scope: the active scope applies;
* a phrase naming another scope, several different scopes, or a visit the
  case does not have: clarification. The scope never changes silently.

Recognised phrases (after interpretation's normalisation):

* pre-target history: "before (or prior to) the final / last / latest /
  most recent / current / this admission", "before admission",
  "previous (prior, earlier, past) admissions" or "a prior admission",
  "pre-target history";
* one visit: "the first / second ... admission", "admission 2",
  "admission two", "visit number 3", "the final (last, latest, most
  recent, current, present) admission"; "this admission" names the
  selected visit when a visit is selected, and the final admission
  otherwise;
* all case visits: "in any (all, every) of the admissions", "any
  admission", "across (throughout, over) her admissions", "the entire
  (whole, complete) record".

An admission may also be called a visit, stay, encounter or
hospitalisation, with "hospital", "inpatient" or "ICU" before it, and
a phrase may start with "at the time of" or "during the course of".

Words such as "ever" or "at any time" are not scope phrases: the answer
states its scope, so the reader sees which records it covers. Other
mentions of admissions, stays or times are not guessed at: the
interpretation asks which part of the record is meant (qa.interpret).
"""

from __future__ import annotations

import dataclasses
import re

from ehr_evidence_app.evidence import contracts

KIND = r"(?:(?:hospital|inpatient|icu) )?"
VISIT = (r"(?:%s(?:admission|visit|stay|encounter)|hospitali[sz]ation)"
         % KIND)
VISITS = (r"(?:%s(?:admissions|visits|stays|encounters)|"
          r"hospitali[sz]ations)" % KIND)
LAST = (r"(?:final|last|latest|most recent|current|present|ongoing|"
        r"target|index|this)")
OWNER = (r"(?:the |this |his |her |their |that |the patient |"
         r"this patient )?")
IN = (r"(?:(?:in|during|at|for|on|within|from|of|throughout|across|over|"
      r"at the time of|during the course of|over the course of|"
      r"in the course of) )?")
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
            "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
            "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6,
            "7th": 7, "8th": 8, "9th": 9, "10th": 10}
ORDINAL = "(?P<ordinal>%s)" % "|".join(ORDINALS)
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
           "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

# Patterns in priority order; a matched span is blanked before the next.
PATTERNS = (
    ("pre_target_history", re.compile(
        r"\b(?:before|prior to|preceding|earlier than|previous to) %s%s %s\b"
        % (OWNER, LAST, VISIT))),
    ("pre_target_history", re.compile(
        r"\b(?:before|prior to|preceding|earlier than|previous to) %s%s\b"
        % (OWNER, VISIT))),
    ("pre_target_history", re.compile(
        r"\b(?:previous|prior|earlier|past|preceding|former|older) "
        r"(?:%s|%s)\b" % (VISITS, VISIT))),
    ("pre_target_history", re.compile(r"\bpre ?target history\b")),
    ("case_visits", re.compile(
        r"\b(?:in |across |during |over |throughout |at )?(?:any|all|every|"
        r"each) (?:of %s)?%s\b" % (OWNER, VISITS))),
    ("case_visits", re.compile(r"\b%s(?:any|every) %s\b" % (IN, VISIT))),
    ("case_visits", re.compile(
        r"\b(?:across|throughout|over|in|during) %s%s\b" % (OWNER, VISITS))),
    ("case_visits", re.compile(
        r"\b%s%s(?:entire|whole|complete|full) (?:record|chart)\b"
        % (IN, OWNER))),
    ("selected_visit", re.compile(r"\b%s%s%s %s\b" % (IN, OWNER, ORDINAL,
                                                    VISIT))),
    ("selected_visit", re.compile(
        r"\b%s%s%s (?:number |no |# ?)?(?P<number>[0-9]{1,3}|%s)\b"
        % (IN, OWNER, VISIT, "|".join(NUMBERS)))),
    ("selected_visit_last", re.compile(r"\b%s%s(?P<last>%s) %s\b" % (
        IN, OWNER, LAST, VISIT))),
)


@dataclasses.dataclass(frozen=True)
class Mention:
    """One scope phrase found in a question; this marks "this
    admission"."""

    kind: str
    visit_number: int | None
    last: bool
    span: tuple
    this: bool = False


@dataclasses.dataclass(frozen=True)
class Binding:
    """The scope a request is bound to, or why it cannot be bound."""

    status: str
    scope: contracts.ScopeRef | None = None
    requested: contracts.ScopeRef | None = None
    reason: str | None = None


def mentions(text):
    """Return the scope phrases of normalised text, in order of position."""
    working = text
    found = []
    for kind, pattern in PATTERNS:
        for match in pattern.finditer(working):
            groups = match.groupdict()
            number = None
            if groups.get("ordinal"):
                number = ORDINALS[groups["ordinal"]]
            elif groups.get("number"):
                number = NUMBERS.get(groups["number"]) or int(
                    groups["number"])
            found.append(Mention(
                kind="selected_visit" if kind.startswith("selected")
                else kind,
                visit_number=number, last=kind == "selected_visit_last",
                span=match.span(), this=groups.get("last") == "this"))
        working = pattern.sub(lambda match: " " * len(match.group(0)),
                              working)
    return tuple(sorted(found, key=lambda item: item.span))


def remove(text, found):
    """Return text with the spans of the given mentions removed."""
    for mention in sorted(found, key=lambda item: item.span, reverse=True):
        start, end = mention.span
        text = text[:start] + " " + text[end:]
    return " ".join(text.split())


def requested_scope(mention, visit_count, active=None):
    """Return the ScopeRef a mention names for a case, or None.

    "This admission" names the active visit when a visit is selected.
    """
    if mention.kind != "selected_visit":
        return contracts.ScopeRef(kind=mention.kind)
    if mention.this and active is not None and (
            active.kind == "selected_visit"):
        return active
    number = visit_count if mention.last else mention.visit_number
    if number is None or not 1 <= number <= visit_count:
        return None
    return contracts.ScopeRef(kind="selected_visit", visit_number=number)


def bind(found, active, visit_count):
    """Bind a request to the active scope, or say why it cannot be."""
    if not found:
        return Binding("bound", scope=active)
    scopes = []
    for mention in found:
        scope = requested_scope(mention, visit_count, active)
        if scope is None:
            return Binding("clarify", reason="visit_ambiguous")
        if scope not in scopes:
            scopes.append(scope)
    if len(scopes) > 1:
        return Binding("clarify", reason="scope_ambiguous")
    if scopes[0] != active:
        return Binding("clarify", requested=scopes[0],
                       reason="scope_ambiguous")
    return Binding("bound", scope=active)
