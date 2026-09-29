"""What the page calls the case sets and the patients (Phase 8, work
item 49).

A case set is shown as a cohort with a plain name, and no case set's
label reaches the page. A patient is shown by
a pseudonym, as case reports name one: a given name and an initial, such
as "Jordan K.". The records hold no names, and a pseudonym is not drawn
from them: it follows from the case set's label and the case's number
alone, by a fixed ordering of every given name and initial, so that it is
the same on every machine, in every session, and different for each case
of a case set; no given name comes twice among a case set's first sixty
cases, so that the page can greet a patient by it. The given names are
ones used for any sex, so that a pseudonym suggests nothing about the
patient that the record does not hold. The page says that the names are
pseudonyms, and keeps each case's number beside its name.
"""

from __future__ import annotations

import functools
import hashlib

COHORTS = {"dev-30-v1": "Development Cohort",
           "pilot-10-v1": "Pilot Cohort"}
INVENTED_PREFIX = "synthetic"
INVENTED_COHORT = "Demonstration Cohort"
GIVEN_NAMES = (
    "Alex", "Arden", "Ari", "Avery", "Bailey", "Blair", "Brook", "Cameron",
    "Casey", "Charlie", "Dakota", "Dana", "Devon", "Drew", "Eden", "Ellis",
    "Elliot", "Emerson", "Emery", "Finley", "Frankie", "Gray", "Harper",
    "Hayden", "Hollis", "Jamie", "Jesse", "Jordan", "Jules", "Kai",
    "Kendall", "Kerry", "Lane", "Linden", "Logan", "Marley", "Micah",
    "Morgan", "Noel", "Oakley", "Parker", "Peyton", "Quinn", "Reese",
    "Remy", "Riley", "River", "Robin", "Rory", "Rowan", "Sage", "Sam",
    "Sasha", "Shay", "Sidney", "Skyler", "Tatum", "Taylor", "Terry", "Wren")
INITIALS = "ABCDEFGHJKLMNPRSTW"


def invented(case_set):
    """Return True for an invented case set."""
    return case_set.startswith(INVENTED_PREFIX)


def cohort(case_set):
    """Return the name of a case set's cohort."""
    if case_set in COHORTS:
        return COHORTS[case_set]
    return INVENTED_COHORT if invented(case_set) else case_set


@functools.lru_cache(maxsize=16)
def order(case_set):
    """Return every pseudonym in the case set's fixed order, in which no
    given name comes twice before every one has come once."""
    def rank(text):
        """Return the place of a name or an initial in the order."""
        return hashlib.sha256(("%s/%s" % (case_set, text)).encode(
            "utf-8")).hexdigest()

    given = sorted(GIVEN_NAMES, key=rank)
    initials = {name: sorted(INITIALS, key=lambda initial: rank(
        "%s %s" % (name, initial))) for name in given}
    return tuple("%s %s." % (name, initials[name][turn])
                 for turn in range(len(INITIALS)) for name in given)


def pseudonym(case_set, number):
    """Return a case's pseudonym, the same for the same case set and
    number and different for each case of a case set; past the number of
    pseudonyms, a count follows the name."""
    found = order(case_set)
    turn, index = divmod(number - 1, len(found))
    return found[index] + (" %d" % (turn + 1) if turn else "")


def given_name(name):
    """Return the given name of a pseudonym."""
    return name.split(" ", 1)[0]


def unbroken(label):
    """Return a label with hyphens that do not end a line, so that it
    shows whole in running text."""
    return label.replace("-", "\u2011")
