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
  most recent / current admission", "previous (prior, earlier, past)
  admissions", "pre-target history";
* one visit: "the first / second ... admission", "admission 2", "visit
  number 3", "the final (last, latest, most recent, current) admission";
* all case visits: "in any (all, every) of the admissions", "any
  admission", "the entire (whole, complete) record".

Words such as "ever" or "at any time" are not scope phrases: the answer
states its scope, so the reader sees which records it covers.
"""

from __future__ import annotations

import dataclasses
import re

from ehr_evidence_app.evidence import contracts

VISIT = r"(?:admission|visit|stay|hospitali[sz]ation|encounter)"
VISITS = r"(?:admissions|visits|stays|hospitali[sz]ations|encounters)"
LAST = r"(?:final|last|latest|most recent|current|target|index)"
OWNER = r"(?:the |this |his |her |their |that )?"
IN = r"(?:(?:in|during|at|for|on|within|from|of|throughout) )?"
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
            "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
            "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5, "6th": 6,
            "7th": 7, "8th": 8, "9th": 9, "10th": 10}
ORDINAL = "(?P<ordinal>%s)" % "|".join(ORDINALS)

# Patterns in priority order; a matched span is blanked before the next.
PATTERNS = (
    ("pre_target_history", re.compile(
        r"\b(?:before|prior to|preceding|earlier than|previous to) %s%s %s\b"
        % (OWNER, LAST, VISIT))),
    ("pre_target_history", re.compile(
        r"\b(?:previous|prior|earlier|past|preceding|former) %s\b" % VISITS)),
    ("pre_target_history", re.compile(r"\bpre ?target history\b")),
    ("case_visits", re.compile(
        r"\b(?:in |across |during |over |throughout |at )?(?:any|all|every|"
        r"each) (?:of %s)?%s\b" % (OWNER, VISITS))),
    ("case_visits", re.compile(r"\b%s(?:any|every) %s\b" % (IN, VISIT))),
    ("case_visits", re.compile(
        r"\b%s%s(?:entire|whole|complete|full) (?:record|chart)\b"
        % (IN, OWNER))),
    ("selected_visit", re.compile(r"\b%s%s%s %s\b" % (IN, OWNER, ORDINAL,
                                                    VISIT))),
    ("selected_visit", re.compile(
        r"\b%s%s%s (?:number |no |# ?)?(?P<number>[0-9]{1,3})\b"
        % (IN, OWNER, VISIT))),
    ("selected_visit_last", re.compile(r"\b%s%s%s %s\b" % (IN, OWNER, LAST,
                                                         VISIT))),
)


@dataclasses.dataclass(frozen=True)
class Mention:
    """One scope phrase found in a question."""

    kind: str
    visit_number: int | None
    last: bool
    span: tuple


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
                number = int(groups["number"])
            found.append(Mention(
                kind="selected_visit" if kind.startswith("selected")
                else kind,
                visit_number=number, last=kind == "selected_visit_last",
                span=match.span()))
        working = pattern.sub(lambda match: " " * len(match.group(0)),
                              working)
    return tuple(sorted(found, key=lambda item: item.span))


def remove(text, found):
    """Return text with the spans of the given mentions removed."""
    for mention in sorted(found, key=lambda item: item.span, reverse=True):
        start, end = mention.span
        text = text[:start] + " " + text[end:]
    return " ".join(text.split())


def requested_scope(mention, visit_count):
    """Return the ScopeRef a mention names for a case, or None."""
    if mention.kind != "selected_visit":
        return contracts.ScopeRef(kind=mention.kind)
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
        scope = requested_scope(mention, visit_count)
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
