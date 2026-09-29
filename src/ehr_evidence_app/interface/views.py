"""What the page shows (Phase 8, work items 49 to 52; decisions D4 to D6).

Plain functions from the API's values to the page's: the model's state
and the cohorts, the patients of a cohort, a case with its scopes and its
predicted diagnosis, a response as the conversation shows it, and the
evidence as the evidence panel shows it. Every word the page shows about
a value is chosen here, with interface.labels; the page lays the values
out, as text only.

Names of concepts are shown in title case, in the evidence and in a
response's text alike; the rest of a response's text is shown as it was
released, and the runtime record keeps it exactly. A response's text is
kept line by line, each line with the references of the claim it states,
so that the page cites them where they are made. The evidence comes in
sections of facts, tables and notes; every row and section a reference
names carries it, so that a reference opened from a response finds what
it names. The native database identifiers of visits and records never
reach this module: the API leaves them out.
"""

from __future__ import annotations

import collections
import re

from ehr_evidence_app.interface import labels, names
from ehr_evidence_app.validation import candidate

# The key of the prediction's evidence, beside the keys of the scopes.
PREDICTION = "prediction"
MAX_DEPTH = 3
ACROSS, SINGLE, PREDICTED = "Across Visits", "Single Visit", "Prediction"

RECORD_COLUMNS = (("Ref", "ref"), ("Visit", "number"), ("Type", "text"),
                  ("Concept", "text"), ("Concept ID", "code"),
                  ("Source Code", "code"), ("Vocabulary", "text"),
                  ("Start", "date"), ("End", "date"))
VISIT_COLUMNS = (("Ref", "ref"), ("Visit", "number"), ("Role", "text"),
                 ("Start", "date"), ("End", "date"))
ATTRIBUTION_COLUMNS = (("Ref", "ref"), ("Type", "text"),
                       ("Concept", "text"), ("Score (Unsigned)", "number"),
                       ("Class", "text"), ("Attachment", "text"),
                       ("Described", "text"), ("Link Status", "text"),
                       ("Linked Records", "code"))
PROVENANCE_COLUMNS = (("Input", "text"), ("Name", "text"),
                      ("SHA-256", "code"))
CONDITION_COLUMNS = (("Condition", "text"), ("Records", "number"),
                     ("Visits", "text"))
# The first line of a presence answer names the condition asked about.
PRESENCE_SUBJECT = re.compile(r"^(?P<name>.+?)(?= is (?:not )?recorded "
                              r"within )")


# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------

def section(title, blocks, ref=None, collapsed=False):
    """Return a section of the evidence: a title, its blocks, the
    reference it stands for, and whether it starts folded."""
    return {"title": title, "ref": ref, "collapsed": collapsed,
            "blocks": blocks}


def facts(items):
    """Return a block of named values."""
    return {"type": "facts", "items": [{"label": label, "value": value}
                                       for label, value in items]}


def note(tone, text):
    """Return a block of text in a tone: info, muted or warn."""
    return {"type": "note", "tone": tone, "text": text}


def table(columns, rows, empty):
    """Return a table: its columns (a label and the kind of value, which
    sets its alignment), its rows and what it says when it has none."""
    return {"type": "table",
            "columns": [{"label": label, "kind": kind}
                        for label, kind in columns],
            "rows": rows, "empty": empty}


def row(cells, ref=None):
    """Return a row of a table, with the reference it stands for; a cell
    without a value is empty."""
    return {"ref": ref, "cells": ["" if cell is None else str(cell)
                                  for cell in cells]}


# --------------------------------------------------------------------------
# The model, the cohorts and the patients (work item 49)
# --------------------------------------------------------------------------

def scope_key(scope):
    """Return the key of a scope: its kind, and its visit's number for a
    selected visit."""
    if scope.get("visit_number"):
        return "%s:%d" % (scope["kind"], scope["visit_number"])
    return scope["kind"]


def state_view(health, case_sets, version):
    """Return the model's state, the served case sets as cohorts and the
    application's version."""
    name, meaning = labels.model(health["model"])
    return {"model": {"state": health["model"], "label": name,
                      "detail": meaning},
            "case_sets": [{"id": item["case_set"],
                           "label": names.cohort(item["case_set"]),
                           "detail": labels.counted(item["cases"],
                                                    "patient")}
                          for item in case_sets],
            "version": version}


def cases_view(case_set, cases):
    """Return a case set's admitted cases as the sidebar lists them: each
    patient's pseudonym, with the case's number and visits."""
    return [{"number": item["case_number"],
             "name": names.pseudonym(case_set, item["case_number"]),
             "detail": "Case %d · %s" % (item["case_number"], labels.counted(
                 item["visits"], "visit")),
             "prediction": bool(item.get("prediction"))}
            for item in cases]


def prediction_view(prediction):
    """Return the predicted diagnosis: its label, its score and logit,
    never as a percentage, and the score's fixed meaning."""
    return {"ref": prediction["ref"],
            "label": labels.title(prediction["label"]) or (
                "Concept %d" % prediction["concept_id"]),
            "facts": [{"label": "Score",
                       "value": labels.score(prediction["score"])},
                      {"label": "Logit",
                       "value": labels.score(prediction["logit"])}],
            "meaning": "Score: %s." % labels.NOT_A_RISK,
            "note": labels.ONE_OUTPUT}


def visit_details(visits):
    """Return what a menu says of each visit, by its number: its date,
    and whether it is the final admission."""
    found = {}
    for visit in visits or []:
        parts = [labels.date(visit.get("start_date"))]
        if visit.get("role") == "final_admission":
            parts.append("Final Admission")
        found[visit["visit_number"]] = " · ".join(part for part in parts
                                                  if part)
    return found


def scope_entry(scope, details):
    """Return a scope as the menus list it: its key, group, label and what
    it holds."""
    kind = scope["kind"]
    if kind == "selected_visit":
        return {"key": scope_key(scope), "group": SINGLE,
                "label": labels.scope(scope),
                "detail": details.get(scope["visit_number"], ""),
                "scope": scope}
    return {"key": scope_key(scope), "group": ACROSS,
            "label": labels.scope(scope),
            "detail": labels.SCOPES.get(kind, ("", ""))[1], "scope": scope}


def case_view(case, visits=None):
    """Return a case: the patient's pseudonym and how the page greets it,
    the case's scopes grouped for the menus, the evidence it can be
    inspected in and its predicted diagnosis; visits are the case's
    visits, with their dates and roles, when known."""
    details = visit_details(visits)
    entries = [scope_entry(scope, details) for scope in case["scopes"]]
    scopes = ([entry for entry in entries if entry["group"] == ACROSS]
              + [entry for entry in entries if entry["group"] == SINGLE])
    evidence = [{key: entry[key] for key in ("key", "group", "label",
                                             "detail")} for entry in scopes]
    prediction = case.get("prediction")
    if prediction is not None:
        evidence.append({"key": PREDICTION, "group": PREDICTED,
                         "label": "Predicted Diagnosis",
                         "detail": "Its drivers, linked records and "
                                   "context"})
    case_set, number = case["case_set"], case["case_number"]
    name = names.pseudonym(case_set, number)
    given = names.given_name(name)
    tags = ["Case %d" % number, names.cohort(case_set),
            labels.counted(case["visits"], "visit")]
    return {"case_set": case_set, "number": number, "name": name,
            "subtitle": " · ".join(tags), "tags": tags,
            "greeting": "What Would You Like to Know About %s?" % given,
            "placeholder": "Ask About %s's Record" % given,
            "scopes": scopes, "evidence": evidence,
            "prediction": prediction_view(prediction) if prediction
            else None}


# --------------------------------------------------------------------------
# Responses (work items 50 and 51)
# --------------------------------------------------------------------------

def normal(text):
    """Return text as a claim is compared with a line: its spaces folded
    and no full stop at its end."""
    return " ".join(text.split()).rstrip(".")


def names_of(view):
    """Return the names of the concepts an evidence view holds: its
    records', its attributions' and its prediction's."""
    found = set()
    for key in ("records", "history_records", "final_admission_context",
                "attributions"):
        found |= {item["concept_name"] for item in view.get(key, [])
                  if item.get("concept_name")}
    if view.get("prediction") and view["prediction"].get("label"):
        found.add(view["prediction"]["label"])
    return found


def namer(known):
    """Return a function that shows every name of a concept in a text in
    title case, the longest name first, each where it stands whole."""
    ordered = sorted({name for name in known if name.strip()}, key=len,
                     reverse=True)
    if not ordered:
        return lambda text: text
    pattern = re.compile(r"(?<![\w])(?:%s)(?![\w])" % "|".join(
        re.escape(name) for name in ordered), re.IGNORECASE)
    return lambda text: pattern.sub(lambda match: labels.title(
        match.group(0)), text)


def text_lines(text, claims, shown=lambda text: text):
    """Return a response's text line by line: each line's text as shown,
    its depth in a list (None for a paragraph) and the references of the
    claim it states. A line states a claim when it says the claim's text,
    its list marker and full stop aside."""
    cited = {}
    for claim in claims:
        refs = cited.setdefault(normal(claim["text"]), [])
        refs.extend(ref for ref in claim["refs"] if ref not in refs)
    found = []
    for line in text.splitlines():
        body = line.strip()
        if not body:
            continue
        depth = None
        if body.startswith("- "):
            indent = len(line) - len(line.lstrip(" "))
            depth = min(indent // 2, MAX_DEPTH)
            body = body[2:].strip()
        found.append({"text": shown(body), "depth": depth,
                      "refs": list(cited.get(normal(body), []))})
    return found


def response_view(result, known=()):
    """Return a response as the conversation shows it: its outcome, its
    text line by line with the references each line cites and the names
    of concepts (known, and the condition a presence answer names) in
    title case, what was withheld, the scope it answered, who wrote it
    and the seconds it took."""
    response = result["response"]
    text = response.get("text")
    shown = namer(known)
    if response.get("task") == "condition_presence":
        named = shown

        def shown(line):
            """Show a line's names, and the condition it answers about."""
            return PRESENCE_SUBJECT.sub(lambda match: labels.title(
                match.group("name")), named(line), count=1)
    withheld = None
    if not text:
        codes = response.get("reason_codes") or []
        withheld = "No text was released%s." % (
            ": " + labels.reasons(codes) if codes else "")
    lines = text_lines(text, response.get("claims") or [], shown) \
        if text else []
    return {"outcome": {"code": response["outcome"],
                        "label": labels.outcome(response["outcome"]),
                        "tone": labels.tone(response["outcome"])},
            "lines": lines,
            "text": "\n".join(("- " if line["depth"] is not None else "")
                              + line["text"] for line in lines),
            "withheld": withheld,
            "references": labels.references(response),
            "evidence": scope_key(response["scope"]),
            "scope": {"key": scope_key(response["scope"]),
                      "label": labels.scope(response["scope"])},
            "author": labels.author(result),
            "seconds": labels.seconds(result["seconds"])}


def conditions_view(history):
    """Return the conditions recorded in the prior history, apart from
    the prediction and its drivers: each with its number of records and
    the visits they are in; history is that scope's evidence, or None
    when it could not be read."""
    found = {"title": "Recorded Conditions · Prior History",
             "note": "What the records hold before the final admission. "
                     "These are records, not the model's prediction or "
                     "its drivers.",
             "table": None}
    if history is None:
        found["note"] = "The prior history could not be read."
        return found
    counts, visits = collections.Counter(), collections.defaultdict(set)
    for record in history["records"]:
        name = labels.title(record["concept_name"]) or (
            "No Standard Concept (Source Code %s)" % record["source_code"])
        counts[name] += 1
        if record["visit_number"] is not None:
            visits[name].add(record["visit_number"])
    found["table"] = table(CONDITION_COLUMNS, [
        row((name, counts[name], ", ".join(
            str(number) for number in sorted(visits[name]))))
        for name in sorted(counts)],
        "No condition is recorded in the prior history.")
    return found


def explanation_view(result, case, history, known=()):
    """Return an explanation as the conversation shows it: the response,
    with the predicted diagnosis and its score's meaning before it, the
    note that attribution scores are unsigned, and the conditions recorded
    in the prior history in a block of their own (decision D5)."""
    found = response_view(result, known)
    prediction = case.get("prediction")
    found.update(evidence=PREDICTION,
                 prediction=prediction_view(prediction) if prediction
                 else None,
                 generated=labels.GENERATED,
                 unsigned=labels.UNSIGNED,
                 conditions=conditions_view(history))
    return found


# --------------------------------------------------------------------------
# The evidence (work item 52)
# --------------------------------------------------------------------------

def concept_name(record):
    """Return a record's concept as the evidence names it."""
    if record["concept_name"]:
        return labels.title(record["concept_name"])
    return ("No Standard Concept" if record["name_status"] == "unmapped"
            else "Name Unavailable")


def record_rows(records):
    """Return records as rows of a table."""
    return [row((record["ref"], record["visit_number"],
                 labels.words(record["domain"]), concept_name(record),
                 record["concept_id"], record["source_code"],
                 record.get("source_vocabulary"),
                 labels.date(record["start_date"]),
                 labels.date(record["end_date"])), record["ref"])
            for record in records]


def visit_rows(visits):
    """Return visits as rows of a table."""
    return [row((visit["ref"], visit["visit_number"],
                 labels.words(visit["role"]), labels.date(visit["start_date"]),
                 labels.date(visit["end_date"])), visit["ref"])
            for visit in visits]


def attribution_rows(attributions):
    """Return attributions as rows of a table, each score unsigned."""
    return [row((item["ref"], labels.words(item["domain"]),
                 labels.title(item["concept_name"]) or "Name Unavailable",
                 labels.score(item["score"]),
                 labels.words(item["score_class"]),
                 labels.words(item["attachment"]),
                 "Yes" if item["narration_eligible"] else "No",
                 labels.words(item["link_status"]),
                 ", ".join(item["linked_record_refs"])), item["ref"])
            for item in attributions]


def coverage_section(coverage):
    """Return a scope's coverage: its state, its counts and what it
    means."""
    status, meaning = labels.coverage(coverage["status"])
    items = [("Status", status),
             ("Visits in Scope", str(coverage["visits_in_scope"])),
             ("Records in Scope", str(coverage["records_in_scope"])),
             ("Without a Standard Concept",
              str(len(coverage["records_without_a_standard_concept"]))),
             ("Final Admission Records Excluded",
              str(coverage["final_admission_records_left_out"]))]
    if coverage["records_outside_the_case_visits"]:
        items.append(("Records Outside the Case Visits",
                      str(coverage["records_outside_the_case_visits"])))
    blocks = [facts(items)]
    if meaning:
        blocks.append(note("muted", meaning))
    if coverage["records_without_a_standard_concept"]:
        blocks.append(note("muted", "Records without a standard concept: "
                           "%s." % ", ".join(coverage[
                               "records_without_a_standard_concept"])))
    if coverage["reason_codes"]:
        blocks.append(note("info", "Coverage reasons: %s." % labels.reasons(
            coverage["reason_codes"])))
    return section("Coverage", blocks, ref=coverage["ref"])


def provenance_section(provenance, coverage):
    """Return the digest of every input of the evidence and the coverage's
    own statement, folded."""
    blocks = [table(PROVENANCE_COLUMNS, [
        row((labels.title(name.replace("_", " ")), item["name"],
             item["sha256"]))
        for name, item in provenance["inputs"].items()], "No input.")]
    if coverage.get("statement"):
        blocks.append(note("muted", "Coverage statement: %s"
                           % coverage["statement"]))
    blocks.append(note("muted", "Schema %s · %s" % (
        provenance["schema_version"], provenance["builder"])))
    return section("Provenance", blocks, collapsed=True)


def scope_evidence_view(view):
    """Return a scope's evidence: its coverage, visits, records and
    provenance."""
    return {"key": scope_key(view["scope"]),
            "title": labels.scope(view["scope"]),
            "sections": [
                coverage_section(view["coverage"]),
                section("Visits", [table(VISIT_COLUMNS,
                                         visit_rows(view["visits"]),
                                         "No visit in this scope.")]),
                section("Records", [table(RECORD_COLUMNS,
                                          record_rows(view["records"]),
                                          "No record in this scope.")]),
                provenance_section(view["provenance"], view["coverage"])]}


def restriction_text(code):
    """Return what a case's restriction means, in the approved words of
    its notice."""
    notice = candidate.NOTICES.get(candidate.RESTRICTION_NOTICES.get(code))
    return notice.text if notice else labels.words(code) + "."


def prediction_evidence_view(view):
    """Return the prediction's evidence: the predicted diagnosis, every
    original attribution with its link status, so that missing linkage is
    seen, the linked history records, the final admission's records as
    context, the restrictions, the coverage, the visits and the
    provenance (decision D6)."""
    prediction = prediction_view(view["prediction"])
    attributions = view["attributions"]
    unlinked = sum(item["link_status"] != "verified"
                   for item in attributions)
    blocks = [note("info", labels.UNSIGNED),
              table(ATTRIBUTION_COLUMNS, attribution_rows(attributions),
                    "No attribution.")]
    if unlinked:
        blocks.append(note("warn", "%d of %d attributions have no verified "
                                   "link to a record: their concepts are "
                                   "inputs of the model, not dated events."
                           % (unlinked, len(attributions))))
    left_out = view.get("attributions_left_out") or 0
    if left_out:
        blocks.append(note("muted", "%d attribution %s left out of this "
                                    "evidence." % (left_out, "entry is"
                                                   if left_out == 1
                                                   else "entries are")))
    sections = [
        section("Predicted Diagnosis", [
            facts([("Label", prediction["label"])] + [
                (item["label"], item["value"])
                for item in prediction["facts"]]),
            note("muted", prediction["meaning"]),
            note("muted", prediction["note"])], ref=prediction["ref"]),
        section("Attributions", blocks),
        section("Linked History Records", [
            table(RECORD_COLUMNS, record_rows(view["history_records"]),
                  "No linked history record.")]),
        section("Final Admission Context", [
            table(RECORD_COLUMNS,
                  record_rows(view["final_admission_context"]),
                  "No final admission record.")])]
    if view["restrictions"]:
        sections.append(section("Restrictions", [
            note("muted", restriction_text(code))
            for code in view["restrictions"]]))
    sections += [coverage_section(view["coverage"]),
                 section("Visits", [table(VISIT_COLUMNS,
                                          visit_rows(view["visits"]),
                                          "No visit.")]),
                 provenance_section(view["provenance"], view["coverage"])]
    return {"key": PREDICTION, "title": "Predicted Diagnosis",
            "sections": sections}


def evidence_view(view):
    """Return the evidence of a scope, or of the prediction, as the panel
    shows it."""
    if view["kind"] == PREDICTION:
        return prediction_evidence_view(view)
    return scope_evidence_view(view)


def error_view(code):
    """Return a refusal: its code and what it means."""
    return {"error": code, "message": labels.error(code)}
