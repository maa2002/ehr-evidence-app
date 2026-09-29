"""The application's interface (Phase 8, work items 49 to 52).

A Streamlit page over the API (interface.client). The sidebar selects the
case set, the case and the scope, and shows the model's state; the page
shows one of three views: questions, the explanation and the evidence.

A question is answered for the selected case and scope only (decision
D4). Changing the case clears every earlier response; changing the scope
keeps them, greyed and labelled with the scope they answered, never shown
as current. Each response shows its outcome, its text, the scope it
answered, who wrote it and the seconds it took, and its references, each
opening its record in the evidence view.

The explanation view shows the prepared prediction with its score's fixed
meaning, the explanation of its drivers with their scores labelled
unsigned and not causal, and the conditions recorded in the pre-target
history in a block of their own (decision D5). The evidence view shows a
scope's visits, records, coverage and provenance, or the prediction with
every original attribution and its link status, so that missing linkage
is visible (decision D6).

interface.serve runs this page; it reaches the API only through
interface.client.
"""

from __future__ import annotations

import collections
import uuid

import streamlit as st

from ehr_evidence_app.interface import client as clients
from ehr_evidence_app.interface import labels

VIEWS = ("Questions", "Explanation", "Evidence")


def api():
    """Return the API client of this session."""
    if "api" not in st.session_state:
        st.session_state.api = clients.Client.from_environment()
    return st.session_state.api


def scope_key(scope):
    """Return the menu key of a scope, or of the prediction."""
    if scope.get("visit_number"):
        return "%s:%d" % (scope["kind"], scope["visit_number"])
    return scope["kind"]


def key_scope(key):
    """Return the scope of a menu key."""
    kind, _, visit = key.partition(":")
    return {"kind": kind, "visit_number": int(visit)} if visit else {
        "kind": kind}


def lines(text):
    """Return text as Markdown that shows it as written, line by line."""
    return "  \n".join(labels.escaped(line) for line in text.splitlines())


# --------------------------------------------------------------------------
# The case, the scope and their context (work item 49)
# --------------------------------------------------------------------------

def select(client, health):
    """Show the case set, case and scope menus and the model's state;
    return (case set, case, scope)."""
    bar = st.sidebar
    bar.title("EHR evidence")
    sets = {item["case_set"]: item["cases"] for item in client.case_sets()}
    case_set = bar.selectbox("Case set", list(sets), key="case_set",
                             format_func=lambda label: "%s · %d cases" % (
                                 label, sets[label]))
    visits = {item["case_number"]: item["visits"]
              for item in client.cases(case_set)}
    number = bar.selectbox("Case", list(visits), key="case-" + case_set,
                           format_func=lambda value: "Case %d · %d visits"
                           % (value, visits[value]))
    case = client.case(case_set, number)
    scopes = {scope_key(scope): scope for scope in case["scopes"]}
    chosen = bar.selectbox("Scope", list(scopes),
                           key="scope-%s-%d" % (case_set, number),
                           format_func=lambda key: labels.scope_option(
                               scopes[key]))
    bar.caption("Model: " + labels.model(health["model"]))
    bar.caption("Questions are answered from the case's records within the "
                "selected scope only.")
    return case_set, case, scopes[chosen]


def keep_context(case_set, case):
    """Clear every earlier response when the case changes."""
    context = "%s/%d" % (case_set, case["case_number"])
    if st.session_state.get("context") != context:
        st.session_state.context = context
        st.session_state.responses = []
        st.session_state.explained = None
        st.session_state.focus = None


def open_reference(key, case_set, case_number, scope):
    """Open the reference chosen under a response in the evidence view,
    in the scope the response answered."""
    ref = st.session_state.get(key)
    if ref:
        st.session_state.focus = {"scope": scope, "ref": ref}
        st.session_state["evidence-%s-%d" % (case_set, case_number)] = (
            scope_key(scope))
        st.session_state.view = "Evidence"


def show_references(response, key, case_set, case_number, scope):
    """Show a response's references as choices that open the evidence."""
    refs = labels.references(response)
    if refs:
        st.pills("References", refs, key=key, selection_mode="single",
                 on_change=open_reference,
                 args=(key, case_set, case_number, scope))


# --------------------------------------------------------------------------
# Questions (work item 50)
# --------------------------------------------------------------------------

def show_response(item, current, case_set, case_number):
    """Show one response: current, or greyed with the scope it
    answered."""
    result, response = item["result"], item["result"]["response"]
    with st.container(border=True):
        st.badge(labels.outcome(response["outcome"]),
                 color=labels.tone(response["outcome"]) if current
                 else "gray")
        meta = "Asked about %s · %s · %s" % (
            labels.scope(item["scope"]), labels.author(result),
            labels.seconds(result["seconds"]))
        if not current:
            meta = "Earlier scope, not the current one. " + meta
        st.caption(meta)
        asked = "Question: " + labels.escaped(item["question"])
        text = response.get("text")
        said = lines(text) if text else labels.escaped(
            "No text was released: " + labels.reasons(
                response["reason_codes"]))
        if current:
            st.markdown("**%s**" % asked)
            st.markdown(said)
        else:
            st.caption(asked)
            st.caption(said)
        show_references(response, "refs-" + item["id"], case_set,
                        case_number, item["scope"])


def questions(client, case_set, case, scope):
    """The question view."""
    st.subheader("Questions")
    st.caption("Ask which conditions are recorded, or whether a condition "
               "is recorded, within %s. Each question is answered on its "
               "own, for the case and scope selected now."
               % labels.scope(scope))
    with st.form("ask", clear_on_submit=True, border=False):
        question = st.text_input("Question", key="question", max_chars=500,
                                 placeholder="Which conditions are recorded?")
        asked = st.form_submit_button("Ask", type="primary")
    if asked and question.strip():
        with st.spinner("Writing the response..."):
            try:
                result = client.ask(case_set, case["case_number"], scope,
                                    question.strip())
            except clients.ClientError as error:
                st.error(labels.error(error.code))
                result = None
        if result is not None:
            st.session_state.responses.insert(0, {
                "id": uuid.uuid4().hex[:12], "question": question.strip(),
                "scope": scope, "result": result})
    for item in st.session_state.responses:
        show_response(item, item["scope"] == scope, case_set,
                      case["case_number"])


# --------------------------------------------------------------------------
# The explanation (work item 51)
# --------------------------------------------------------------------------

def show_prediction(prediction):
    """Show the prepared prediction with its score's fixed meaning."""
    with st.container(border=True):
        st.markdown("**Prepared prediction:** " + labels.escaped(
            prediction["label"] or "concept %d" % prediction["concept_id"]))
        score, logit = st.columns(2)
        score.metric("Score", labels.score(prediction["score"]))
        logit.metric("Logit", labels.score(prediction["logit"]))
        st.caption("The score is the %s. The label is one output of the "
                   "model's multi-label prediction, not the only diagnosis "
                   "it predicts." % labels.NOT_A_RISK)


def recorded_conditions(client, case_set, case):
    """Show the conditions recorded in the pre-target history, apart from
    the prediction and its drivers."""
    st.markdown("**Conditions recorded in the pre-target history**")
    st.caption("What the case's records hold before the predicted "
               "admission. These are records, not the model's prediction "
               "or its drivers.")
    view = client.evidence(case_set, case["case_number"],
                           "pre_target_history")
    counts, visits = collections.Counter(), collections.defaultdict(set)
    for record in view["records"]:
        name = record["concept_name"] or (
            "No standard concept (source code %s)" % record["source_code"])
        counts[name] += 1
        visits[name].add(record["visit_number"])
    if not counts:
        st.caption("No condition is recorded in the pre-target history.")
        return
    st.dataframe([{"Condition": name, "Records": counts[name],
                   "Visits": ", ".join(str(number) for number in sorted(
                       visits[name]))} for name in sorted(counts)],
                 hide_index=True)


def explanation(client, case_set, case):
    """The explanation view."""
    st.subheader("Explanation")
    prediction = case["prediction"]
    if prediction is None:
        st.info(labels.error("prediction_missing"))
        return
    show_prediction(prediction)
    if st.button("Explain this prediction", type="primary", key="explain"):
        with st.spinner("Writing the explanation..."):
            try:
                st.session_state.explained = {
                    "id": uuid.uuid4().hex[:12],
                    "result": client.explain(case_set,
                                             case["case_number"])}
            except clients.ClientError as error:
                st.error(labels.error(error.code))
    found = st.session_state.explained
    if found:
        result, response = found["result"], found["result"]["response"]
        with st.container(border=True):
            st.badge(labels.outcome(response["outcome"]),
                     color=labels.tone(response["outcome"]))
            st.caption("%s · %s" % (labels.author(result).capitalize(),
                                    labels.seconds(result["seconds"])))
            text = response.get("text")
            st.markdown(lines(text) if text else labels.escaped(
                "No text was released: " + labels.reasons(
                    response["reason_codes"])))
            st.caption(labels.UNSIGNED)
            show_references(response, "refs-" + found["id"], case_set,
                            case["case_number"], {"kind": "prediction"})
    recorded_conditions(client, case_set, case)


# --------------------------------------------------------------------------
# The evidence (work item 52)
# --------------------------------------------------------------------------

def record_rows(records):
    """Return records as table rows."""
    return [{"Ref": record["ref"], "Visit": record["visit_number"],
             "Domain": record["domain"],
             "Concept": record["concept_name"] or (
                 "No standard concept" if record["name_status"] == "unmapped"
                 else "Name unavailable"),
             "Concept ID": record["concept_id"],
             "Source code": record["source_code"],
             "Start": record["start_date"], "End": record["end_date"]}
            for record in records]


def show_provenance(provenance):
    """Show the digest of every input of the evidence."""
    with st.expander("Provenance"):
        st.dataframe([{"Input": name.replace("_", " "),
                       "Name": item["name"], "SHA-256": item["sha256"]}
                      for name, item in provenance["inputs"].items()],
                     hide_index=True)
        st.caption("Schema %s · %s" % (provenance["schema_version"],
                                       provenance["builder"]))


def show_coverage(coverage):
    """Show a scope's coverage."""
    st.markdown("**Coverage:** %s" % coverage["status"].replace("_", " "))
    st.caption(coverage["statement"])
    columns = st.columns(4)
    columns[0].metric("Visits in scope", coverage["visits_in_scope"])
    columns[1].metric("Records in scope", coverage["records_in_scope"])
    columns[2].metric("Without a standard concept",
                      len(coverage["records_without_a_standard_concept"]))
    columns[3].metric("Final-admission records left out",
                      coverage["final_admission_records_left_out"])


def show_focus(ref, view):
    """Show the record, visit, attribution or prediction a reference
    names, or say that the evidence does not hold it."""
    found = [item for name in ("records", "history_records",
                               "final_admission_context", "attributions",
                               "visits")
             for item in view.get(name, []) if item["ref"] == ref]
    if ref == "P1" and "prediction" in view:
        found = [view["prediction"]]
    if ref == "C1":
        found = [view["coverage"]]
    if not found:
        st.warning("Reference %s is not in this evidence." % ref)
        return
    item = found[0]
    st.info("Reference %s: %s" % (ref, labels.escaped(", ".join(
        "%s %s" % (key.replace("_", " "), value)
        for key, value in item.items()
        if key != "ref" and value not in (None, "", [])))))


def show_scope_evidence(view, ref):
    """Show a scope's evidence."""
    show_coverage(view["coverage"])
    if ref:
        show_focus(ref, view)
    st.markdown("**Visits**")
    st.dataframe([{"Ref": visit["ref"], "Visit": visit["visit_number"],
                   "Role": visit["role"].replace("_", " "),
                   "Start": visit["start_date"], "End": visit["end_date"]}
                  for visit in view["visits"]], hide_index=True)
    st.markdown("**Records**")
    if view["records"]:
        st.dataframe(record_rows(view["records"]), hide_index=True)
    else:
        st.caption("No record in this scope.")
    show_provenance(view["provenance"])


def show_prediction_evidence(view, ref):
    """Show the prediction's evidence: every original attribution with
    its link status, the linked records and the context."""
    show_prediction(view["prediction"])
    if ref:
        show_focus(ref, view)
    st.markdown("**Attributions**")
    st.caption(labels.UNSIGNED)
    st.dataframe([{"Ref": item["ref"], "Domain": item["domain"],
                   "Concept": item["concept_name"] or "Name unavailable",
                   "Score (unsigned)": item["score"],
                   "Class": item["score_class"],
                   "Attachment": item["attachment"].replace("_", " "),
                   "Described": "yes" if item["narration_eligible"] else "no",
                   "Link status": item["link_status"].replace("_", " "),
                   "Linked records": ", ".join(item["linked_record_refs"])}
                  for item in view["attributions"]], hide_index=True)
    unlinked = sum(item["link_status"] != "verified"
                   for item in view["attributions"])
    if unlinked:
        st.warning("%d of %d attributions have no verified link to a "
                   "record: their concepts are inputs of the model, not "
                   "dated events." % (unlinked, len(view["attributions"])))
    st.markdown("**Linked history records**")
    st.dataframe(record_rows(view["history_records"]), hide_index=True)
    st.markdown("**Final admission, as context**")
    st.dataframe(record_rows(view["final_admission_context"]),
                 hide_index=True)
    if view["restrictions"]:
        st.caption("Restrictions of this case: " + labels.reasons(
            view["restrictions"]))
    show_coverage(view["coverage"])
    show_provenance(view["provenance"])


def evidence(client, case_set, case):
    """The evidence view."""
    st.subheader("Evidence")
    options = [scope_key(scope) for scope in case["scopes"]]
    if case["prediction"] is not None:
        options.append("prediction")
    chosen = st.selectbox(
        "Show the evidence of", options,
        key="evidence-%s-%d" % (case_set, case["case_number"]),
        format_func=lambda key: "The prediction" if key == "prediction"
        else labels.scope_option(key_scope(key)))
    focus = st.session_state.focus
    ref = focus["ref"] if focus and scope_key(focus["scope"]) == chosen \
        else None
    scope = key_scope(chosen)
    view = client.evidence(case_set, case["case_number"], scope["kind"],
                           scope.get("visit_number"))
    if view["kind"] == "prediction":
        show_prediction_evidence(view, ref)
    else:
        show_scope_evidence(view, ref)


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

def main():
    """Show the page."""
    st.set_page_config(page_title="EHR evidence", layout="wide")
    try:
        client = api()
        health = client.health()
        case_set, case, scope = select(client, health)
    except clients.ClientError as error:
        st.error(labels.error(error.code))
        st.stop()
    keep_context(case_set, case)
    st.caption("Case %d of %s · %d visits · scope: %s · model: %s" % (
        case["case_number"], case_set, case["visits"],
        labels.scope(scope), labels.model(health["model"]).lower()))
    if "view" not in st.session_state:
        st.session_state.view = VIEWS[0]
    view = st.segmented_control("View", VIEWS, key="view", required=True,
                                label_visibility="collapsed")
    try:
        if view == "Explanation":
            explanation(client, case_set, case)
        elif view == "Evidence":
            evidence(client, case_set, case)
        else:
            questions(client, case_set, case, scope)
    except clients.ClientError as error:
        st.error(labels.error(error.code))


if __name__ == "__main__":
    main()
