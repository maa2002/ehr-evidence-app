"""What the page shows (Phase 8, work items 49 to 52; decisions D4 to D6).

Plain functions from the API's values to the page's: the model's state
and the case sets, the cases of a case set, a case with its scopes and
its prepared prediction, a response as the conversation shows it, and
the evidence as the evidence panel shows it. Every word the page shows
about a value is chosen here, with interface.labels; the page lays the
values out, as text only.

A response's text is kept line by line, each line with the references of
the claim it states, so that the page cites them where they are made.
The evidence comes in sections of facts, tables and notes; every row and
section a reference names carries it, so that a reference opened from a
response finds what it names. The native database identifiers of visits
and records never reach this module: the API leaves them out.
"""

from __future__ import annotations

import collections

from ehr_evidence_app.interface import labels, names

# The key of the prediction's evidence, beside the keys of the scopes.
PREDICTION = "prediction"
MAX_DEPTH = 3

RECORD_COLUMNS = (("Ref", "ref"), ("Visit", "number"), ("Domain", "text"),
                  ("Concept", "text"), ("Concept ID", "code"),
                  ("Source code", "code"), ("Vocabulary", "text"),
                  ("Start", "date"), ("End", "date"))
VISIT_COLUMNS = (("Ref", "ref"), ("Visit", "number"), ("Role", "text"),
                 ("Start", "date"), ("End", "date"))
ATTRIBUTION_COLUMNS = (("Ref", "ref"), ("Domain", "text"),
                       ("Concept", "text"), ("Score (unsigned)", "number"),
                       ("Class", "text"), ("Attachment", "text"),
                       ("Described", "text"), ("Link status", "text"),
                       ("Linked records", "code"))
PROVENANCE_COLUMNS = (("Input", "text"), ("Name", "text"),
                      ("SHA-256", "code"))
CONDITION_COLUMNS = (("Condition", "text"), ("Records", "number"),
                     ("Visits", "text"))


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
# The model, the case sets and the cases (work item 49)
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
    """Return the prepared prediction: its label, its score and logit,
    never as a percentage, and the score's fixed meaning."""
    return {"ref": prediction["ref"],
            "label": prediction["label"] or (
                "Concept %d" % prediction["concept_id"]),
            "facts": [{"label": "Score",
                       "value": labels.score(prediction["score"])},
                      {"label": "Logit",
                       "value": labels.score(prediction["logit"])}],
            "meaning": "The score is the %s." % labels.NOT_A_RISK,
            "note": labels.ONE_OUTPUT}


def case_view(case):
    """Return a case: the patient's pseudonym and how the page introduces
    it, the case's scopes, the evidence it can be inspected in and its
    prepared prediction."""
    scopes = [{"key": scope_key(scope), "label": labels.scope_option(scope),
               "name": labels.scope(scope), "scope": scope}
              for scope in case["scopes"]]
    evidence = [{"key": item["key"], "label": item["label"]}
                for item in scopes]
    prediction = case.get("prediction")
    if prediction is not None:
        evidence.append({"key": PREDICTION, "label": "The prediction"})
    case_set, number = case["case_set"], case["case_number"]
    name, cohort = names.pseudonym(case_set, number), names.cohort(case_set)
    visits = labels.counted(case["visits"], "visit")
    return {"case_set": case_set, "number": number, "name": name,
            "subtitle": "Case %d · %s · %s" % (number, cohort, visits),
            "greeting": "What would you like to know about %s?"
                        % names.given_name(name),
            "introduction": "Case %d of the %s (%s), %s patient with %s. "
                            "%s is a pseudonym: the records hold no names."
                            % (number, cohort.lower(),
                               names.unbroken(case_set),
                               "an invented" if names.invented(case_set)
                               else "a", visits, name),
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


def text_lines(text, claims):
    """Return a response's text line by line: each line's text, its depth
    in a list (None for a paragraph) and the references of the claim it
    states. A line states a claim when it says the claim's text, its list
    marker and full stop aside."""
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
        found.append({"text": body, "depth": depth,
                      "refs": list(cited.get(normal(body), []))})
    return found


def response_view(result):
    """Return a response as the conversation shows it: its outcome, its
    text line by line with the references each line cites, what was
    withheld, the scope it answered, who wrote it and the seconds it
    took."""
    response = result["response"]
    text = response.get("text")
    withheld = None
    if not text:
        codes = response.get("reason_codes") or []
        withheld = "No text was released%s." % (
            ": " + labels.reasons(codes) if codes else "")
    return {"outcome": {"code": response["outcome"],
                        "label": labels.outcome(response["outcome"]),
                        "tone": labels.tone(response["outcome"])},
            "lines": text_lines(text, response.get("claims") or [])
            if text else [],
            "text": text or "",
            "withheld": withheld,
            "references": labels.references(response),
            "evidence": scope_key(response["scope"]),
            "scope": {"key": scope_key(response["scope"]),
                      "name": labels.scope(response["scope"]),
                      "label": labels.scope_option(response["scope"])},
            "author": labels.author(result),
            "seconds": labels.seconds(result["seconds"])}


def conditions_view(history):
    """Return the conditions recorded in the pre-target history, apart
    from the prediction and its drivers: each with its number of records
    and the visits they are in; history is that scope's evidence, or None
    when it could not be read."""
    found = {"title": "Conditions recorded in the pre-target history",
             "note": "What the case's records hold before the predicted "
                     "admission. These are records, not the model's "
                     "prediction or its drivers.",
             "table": None}
    if history is None:
        found["note"] = "The pre-target history could not be read."
        return found
    counts, visits = collections.Counter(), collections.defaultdict(set)
    for record in history["records"]:
        name = record["concept_name"] or (
            "No standard concept (source code %s)" % record["source_code"])
        counts[name] += 1
        if record["visit_number"] is not None:
            visits[name].add(record["visit_number"])
    found["table"] = table(CONDITION_COLUMNS, [
        row((name, counts[name], ", ".join(
            str(number) for number in sorted(visits[name]))))
        for name in sorted(counts)],
        "No condition is recorded in the pre-target history.")
    return found


def explanation_view(result, case, history):
    """Return an explanation as the conversation shows it: the response,
    with the prepared prediction and its score's meaning before it, the
    note that attribution scores are unsigned, and the conditions recorded
    in the pre-target history in a block of their own (decision D5)."""
    found = response_view(result)
    prediction = case.get("prediction")
    found.update(evidence=PREDICTION,
                 prediction=prediction_view(prediction) if prediction
                 else None,
                 unsigned=labels.UNSIGNED,
                 conditions=conditions_view(history))
    return found


# --------------------------------------------------------------------------
# The evidence (work item 52)
# --------------------------------------------------------------------------

def concept_name(record):
    """Return a record's concept as the evidence names it."""
    if record["concept_name"]:
        return record["concept_name"]
    return ("No standard concept" if record["name_status"] == "unmapped"
            else "Name unavailable")


def record_rows(records):
    """Return records as rows of a table."""
    return [row((record["ref"], record["visit_number"],
                 labels.words(record["domain"]), concept_name(record),
                 record["concept_id"], record["source_code"],
                 record.get("source_vocabulary"), record["start_date"],
                 record["end_date"]), record["ref"])
            for record in records]


def visit_rows(visits):
    """Return visits as rows of a table."""
    return [row((visit["ref"], visit["visit_number"],
                 labels.words(visit["role"]), visit["start_date"],
                 visit["end_date"]), visit["ref"]) for visit in visits]


def attribution_rows(attributions):
    """Return attributions as rows of a table, each score unsigned."""
    return [row((item["ref"], labels.words(item["domain"]),
                 item["concept_name"] or "Name unavailable",
                 labels.score(item["score"]),
                 labels.words(item["score_class"]),
                 labels.words(item["attachment"]),
                 "yes" if item["narration_eligible"] else "no",
                 labels.words(item["link_status"]),
                 ", ".join(item["linked_record_refs"])), item["ref"])
            for item in attributions]


def coverage_section(coverage):
    """Return a scope's coverage: its status and counts, and what it
    covers."""
    items = [("Status", labels.words(coverage["status"]).capitalize()),
             ("Visits in scope", str(coverage["visits_in_scope"])),
             ("Records in scope", str(coverage["records_in_scope"])),
             ("Without a standard concept",
              str(len(coverage["records_without_a_standard_concept"]))),
             ("Final-admission records left out",
              str(coverage["final_admission_records_left_out"]))]
    if coverage["records_outside_the_case_visits"]:
        items.append(("Records outside the case visits",
                      str(coverage["records_outside_the_case_visits"])))
    blocks = [facts(items), note("muted", coverage["statement"])]
    if coverage["records_without_a_standard_concept"]:
        blocks.append(note("muted", "Records without a standard concept: "
                           "%s." % ", ".join(coverage[
                               "records_without_a_standard_concept"])))
    if coverage["reason_codes"]:
        blocks.append(note("info", "Coverage reasons: %s." % labels.reasons(
            coverage["reason_codes"])))
    return section("Coverage", blocks, ref=coverage["ref"])


def provenance_section(provenance):
    """Return the digest of every input of the evidence, folded."""
    return section("Provenance", [
        table(PROVENANCE_COLUMNS, [
            row((labels.words(name).capitalize(), item["name"],
                 item["sha256"]))
            for name, item in provenance["inputs"].items()], "No input."),
        note("muted", "Schema %s · %s" % (provenance["schema_version"],
                                          provenance["builder"]))],
        collapsed=True)


def scope_evidence_view(view):
    """Return a scope's evidence: its coverage, visits, records and
    provenance."""
    return {"key": scope_key(view["scope"]),
            "title": labels.scope_option(view["scope"]),
            "sections": [
                coverage_section(view["coverage"]),
                section("Visits", [table(VISIT_COLUMNS,
                                         visit_rows(view["visits"]),
                                         "No visit in this scope.")]),
                section("Records", [table(RECORD_COLUMNS,
                                          record_rows(view["records"]),
                                          "No record in this scope.")]),
                provenance_section(view["provenance"])]}


def prediction_evidence_view(view):
    """Return the prediction's evidence: the prediction, every original
    attribution with its link status, so that missing linkage is seen,
    the linked history records, the final admission's records as
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
        section("Prediction", [
            facts([("Label", prediction["label"])] + [
                (item["label"], item["value"])
                for item in prediction["facts"]]),
            note("muted", prediction["meaning"]),
            note("muted", prediction["note"])], ref=prediction["ref"]),
        section("Attributions", blocks),
        section("Linked history records", [
            table(RECORD_COLUMNS, record_rows(view["history_records"]),
                  "No linked history record.")]),
        section("Final admission, as context", [
            table(RECORD_COLUMNS,
                  record_rows(view["final_admission_context"]),
                  "No final-admission record.")])]
    if view["restrictions"]:
        sections.append(section("Restrictions", [note(
            "muted", "Restrictions of this case: %s." % labels.reasons(
                view["restrictions"]))]))
    sections += [coverage_section(view["coverage"]),
                 section("Visits", [table(VISIT_COLUMNS,
                                          visit_rows(view["visits"]),
                                          "No visit.")]),
                 provenance_section(view["provenance"])]
    return {"key": PREDICTION, "title": "The prediction",
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
