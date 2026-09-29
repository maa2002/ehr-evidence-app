"""The interface's web application (Phase 8, work items 49 to 52).

One page, with its script, style sheet and icon, and the operations the
page calls, each reaching the API only through interface.client:

    GET  /                  the page
    GET  /static/...        its script, style sheet and icon
    GET  /health            that the interface is serving
    GET  /api/state         the model's state, the served case sets, the
                            question limit and the version
    GET  /api/case-sets/{case_set}/cases
    GET  /api/case-sets/{case_set}/cases/{case_number}
    GET  /api/case-sets/{case_set}/cases/{case_number}/evidence?kind=K&visit=N
    POST /api/questions     a question about a case in a scope, as a job
    POST /api/explanations  the explanation of a case's prediction, as a job
    GET  /api/jobs/{job}    a job's state, and its result once done

What the operations return is what the page shows (interface.views). A
question or an explanation is a job (interface.jobs): the page is
answered at once and asks after the job until it is done, so that it
never waits on the model, and no request outlives the tunnel's limit. A
refusal is its code with what it means; any other failure is logged by
its type only.

The gate (interface.gate) stands in front: the hosts, the secret link
and its session, requests from other sites, and the headers of every
response, among them a content security policy that admits this
application's own script and style sheet only. The page builds
everything it shows as text.

The default mode serves the invented case set on free loopback ports,
the API with its scripted model behind its boundary and this application
behind its gate, and makes every operation as the page makes them, over a
real connection; nothing is written.

Usage:
    python -m ehr_evidence_app.interface.app
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sys
import time

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ehr_evidence_app import __version__
from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.interface import client as clients
from ehr_evidence_app.interface import gate
from ehr_evidence_app.interface import jobs as background
from ehr_evidence_app.interface import views

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "static")
PAGE = "index.html"
# The files the page names with a digest of their content, so that a
# browser never uses an old one.
ASSETS = ("app.css", "app.js", "mark.svg")
MAX_BODY_BYTES = 4096
MAX_QUESTION_CHARS = 4000
CASE_SET = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
SCOPE_KINDS = ("pre_target_history", "selected_visit", "case_visits")
EVIDENCE_KINDS = SCOPE_KINDS + (views.PREDICTION,)
# The status of each refusal; a refusal of the API not named here keeps
# the API's status when it is the request's fault, and is otherwise a bad
# gateway.
STATUS = {"case_set_not_served": 404, "case_unknown": 404,
          "scope_unknown": 404, "prediction_missing": 404,
          "job_unknown": 404, "scope_invalid": 422,
          "request_invalid": 422, "request_media": 415,
          "request_too_large": 413, "too_many_requests": 429,
          "internal": 500}


class Refusal(Exception):
    """A request this application refuses; carries a short code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def page_text(directory=STATIC_DIR):
    """Return the page, naming its files with a digest of their
    content."""
    digest = hashlib.sha256()
    for name in ASSETS:
        with open(os.path.join(directory, name), "rb") as handle:
            digest.update(handle.read())
    with open(os.path.join(directory, PAGE), "r", encoding="utf-8") as handle:
        return handle.read().replace("{{version}}", digest.hexdigest()[:16])


def status_of(code, status=None):
    """Return the HTTP status a refusal is answered with."""
    if code in STATUS:
        return STATUS[code]
    if status is not None and 400 <= status < 500:
        return status
    return 502


def owner_of(request):
    """Return whose a request is: its session's, or this machine's."""
    return request.scope.get(gate.SESSION_SCOPE_KEY) or "local"


# --------------------------------------------------------------------------
# What a request names
# --------------------------------------------------------------------------

def case_set_of(value):
    """Return a case set's label, or refuse one no case set could have."""
    if not isinstance(value, str) or not CASE_SET.fullmatch(value):
        raise Refusal("case_set_not_served")
    return value


def case_number_of(value):
    """Return a case's number, or refuse a value that is not one."""
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 1 <= value <= 10000):
        raise Refusal("request_invalid")
    return value


def scope_of(value):
    """Return a scope as the API takes it, or refuse one that is not a
    scope."""
    if not isinstance(value, dict) or value.get("kind") not in SCOPE_KINDS:
        raise Refusal("request_invalid")
    visit = value.get("visit_number")
    if value["kind"] == "selected_visit":
        if set(value) != {"kind", "visit_number"} or isinstance(
                visit, bool) or not isinstance(visit, int) or not (
                1 <= visit <= 1000):
            raise Refusal("request_invalid")
        return {"kind": "selected_visit", "visit_number": visit}
    if set(value) != {"kind"}:
        raise Refusal("request_invalid")
    return {"kind": value["kind"]}


def question_of(value):
    """Return a question, trimmed, or refuse an empty or overlong one.
    The API answers a question over the configured limit with its own
    outcome."""
    if not isinstance(value, str) or not value.strip() or (
            len(value) > MAX_QUESTION_CHARS):
        raise Refusal("request_invalid")
    return value.strip()


def evidence_of(query):
    """Return the kind and visit number a request for evidence names."""
    kind, visit = query.get("kind"), query.get("visit")
    if kind not in EVIDENCE_KINDS or set(query) - {"kind", "visit"}:
        raise Refusal("scope_invalid")
    if kind == "selected_visit":
        if not visit or not visit.isdigit() or not 1 <= int(visit) <= 1000:
            raise Refusal("scope_invalid")
        return kind, int(visit)
    if visit is not None:
        raise Refusal("scope_invalid")
    return kind, None


async def body_of(request, fields):
    """Return a request's JSON object, read whole up to its bound, with
    exactly the fields named."""
    media = request.headers.get("content-type", "").split(";")[0]
    if media.strip().lower() != "application/json":
        raise Refusal("request_media")
    length = request.headers.get("content-length")
    if length is not None and (not length.isdigit()
                               or int(length) > MAX_BODY_BYTES):
        raise Refusal("request_too_large")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise Refusal("request_too_large")
    try:
        value = json.loads(body.decode("utf-8"))
    except ValueError:
        raise Refusal("request_invalid") from None
    if not isinstance(value, dict) or set(value) != set(fields):
        raise Refusal("request_invalid")
    return value


# --------------------------------------------------------------------------
# The application
# --------------------------------------------------------------------------

def create_app(client, jobs=None, log=None, version=__version__,
               question_chars=None):
    """Return the interface's application over an API client: the page,
    its files and its operations; jobs runs its questions and
    explanations, and log (a function of one line) names its failures by
    type. Every job still running is left unread when the server shuts
    down."""
    log = log or (lambda line: None)
    jobs = jobs or background.Jobs(log=log)
    page = page_text()
    if question_chars is None:
        question_chars = contracts.load_limits().max_question_chars

    @contextlib.asynccontextmanager
    async def lifespan(app):
        """Take no more jobs when the server shuts down."""
        yield
        jobs.close()

    def guarded(endpoint):
        """Return an endpoint that answers a refusal with its code and
        any other failure as internal, logged by its type only."""
        async def respond(request):
            """Answer the request, or refuse it."""
            try:
                return await endpoint(request)
            except Refusal as refused:
                code, status = refused.code, None
            except clients.ClientError as refused:
                code, status = refused.code, refused.status
            except background.JobError as refused:
                code, status = refused.code, None
            except Exception as failed:  # noqa: BLE001 - logged by type
                log("interface operation failed: %s" % type(failed).__name__)
                code, status = "internal", None
            return JSONResponse(views.error_view(code),
                                status_code=status_of(code, status))
        return respond

    async def index(request):
        """The page."""
        return HTMLResponse(page)

    async def health(request):
        """That the interface is serving."""
        return JSONResponse({"status": "ok"})

    async def state(request):
        """The model's state, the served case sets, the question limit
        and the version."""
        found = await run_in_threadpool(client.health)
        sets = await run_in_threadpool(client.case_sets)
        return JSONResponse(dict(views.state_view(found, sets, version),
                                 question_chars=question_chars))

    async def cases(request):
        """A case set's admitted cases."""
        case_set = case_set_of(request.path_params["case_set"])
        found = await run_in_threadpool(client.cases, case_set)
        return JSONResponse({"case_set": case_set,
                             "cases": views.cases_view(case_set, found)})

    async def case(request):
        """A case's scopes, evidence and prepared prediction."""
        case_set = case_set_of(request.path_params["case_set"])
        number = case_number_of(request.path_params["case_number"])
        found = await run_in_threadpool(client.case, case_set, number)
        return JSONResponse(views.case_view(found))

    async def evidence(request):
        """The evidence of a scope, or of the prediction."""
        case_set = case_set_of(request.path_params["case_set"])
        number = case_number_of(request.path_params["case_number"])
        kind, visit = evidence_of(request.query_params)
        found = await run_in_threadpool(client.evidence, case_set, number,
                                        kind, visit)
        return JSONResponse(views.evidence_view(found))

    async def questions(request):
        """Start answering a question about a case in a scope."""
        body = await body_of(request, ("case_set", "case_number", "scope",
                                       "question"))
        case_set = case_set_of(body["case_set"])
        number = case_number_of(body["case_number"])
        scope, question = scope_of(body["scope"]), question_of(
            body["question"])

        def work():
            """Answer the question; return it as the page shows it."""
            return views.response_view(client.ask(case_set, number, scope,
                                                  question))

        return JSONResponse({"job": jobs.submit(owner_of(request), work)},
                            status_code=202)

    async def explanations(request):
        """Start explaining a case's prepared prediction."""
        body = await body_of(request, ("case_set", "case_number"))
        case_set = case_set_of(body["case_set"])
        number = case_number_of(body["case_number"])

        def work():
            """Explain the prediction; return it as the page shows it,
            with the prediction and the conditions recorded in the
            pre-target history."""
            result = client.explain(case_set, number)
            found = client.case(case_set, number)
            try:
                history = client.evidence(case_set, number,
                                          "pre_target_history")
            except clients.ClientError:
                history = None
            return views.explanation_view(result, found, history)

        return JSONResponse({"job": jobs.submit(owner_of(request), work)},
                            status_code=202)

    async def job(request):
        """A job's state, and its result once done."""
        found = jobs.read(owner_of(request), request.path_params["job"])
        if found is None:
            raise Refusal("job_unknown")
        return JSONResponse(found)

    base = "/api/case-sets/{case_set}/cases"
    return Starlette(routes=[
        Route("/", index),
        Route("/health", health),
        Route("/api/state", guarded(state)),
        Route(base, guarded(cases)),
        Route(base + "/{case_number:int}", guarded(case)),
        Route(base + "/{case_number:int}/evidence", guarded(evidence)),
        Route("/api/questions", guarded(questions), methods=["POST"]),
        Route("/api/explanations", guarded(explanations), methods=["POST"]),
        Route("/api/jobs/{job}", guarded(job)),
        Mount("/static", StaticFiles(directory=STATIC_DIR), name="static")],
        lifespan=lifespan)


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

def run_synthetic():
    """Serve the invented case set through the API and this application
    on loopback, and make every operation as the page makes them; return
    (requests made, problems). Nothing is written."""
    import requests

    from ehr_evidence_app.api import app as api
    from ehr_evidence_app.api import boundary
    service = api.invented_service()
    token, lines = boundary.new_token(), []
    problems, made = [], [0]
    with api.LoopbackServer(lambda port: api.create_app(
            service, token, boundary.loopback_hosts(port))) as backend:
        client = clients.Client(backend.url, token)
        with api.LoopbackServer(lambda port: gate.Gate(
                create_app(client, log=lines.append),
                gate.local_hosts(port), log=lines.append)) as server:
            http = requests.Session()
            http.trust_env = False
            origin = {"Origin": server.url}

            def call(method, path, expected, **options):
                """Make one request and check its status; return it."""
                made[0] += 1
                reply = http.request(method, server.url + path, timeout=30,
                                     allow_redirects=False, **options)
                if reply.status_code != expected:
                    problems.append("%s %s answered %d, not %d" % (
                        method, path.split("?")[0], reply.status_code,
                        expected))
                if "default-src 'none'" not in reply.headers.get(
                        "content-security-policy", ""):
                    problems.append("%s has no content security policy"
                                    % path.split("?")[0])
                return reply

            def finish(reply):
                """Ask after a job until it is done; return its state."""
                key = reply.json().get("job", "")
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    found = call("GET", "/api/jobs/" + key, 200).json()
                    if found["state"] != "running":
                        return found
                    time.sleep(0.1)
                problems.append("a job did not finish")
                return {"state": "running"}

            page = call("GET", "/", 200)
            if page.headers.get("cache-control") != "no-store" or (
                    "/static/app.js?v=" not in page.text):
                problems.append("the page is cached or names no script")
            for name in ASSETS:
                reply = call("GET", "/static/%s" % name, 200)
                if reply.headers.get("cache-control") != "no-cache":
                    problems.append("%s is not checked before use" % name)
            call("GET", "/health", 200)
            found = call("GET", "/api/state", 200).json()
            if found["model"]["state"] != "ready" or not found["case_sets"]:
                problems.append("the state lacks the model or a case set")
            label = found["case_sets"][0]["id"]
            base = "/api/case-sets/%s/cases" % label
            if not call("GET", base, 200).json()["cases"]:
                problems.append("the case set lists no case")
            case = call("GET", base + "/1", 200).json()
            if not case["scopes"] or case["prediction"] is None:
                problems.append("the case lacks its scopes or prediction")
            scope = case["scopes"][0]
            shown = call("GET", base + "/1/evidence?kind=prediction", 200)
            history = call("GET", base + "/1/evidence?kind=%s"
                           % scope["scope"]["kind"], 200)
            text = shown.text + history.text
            if "native_id" in text or "visit_occurrence_id" in text:
                problems.append("the evidence shows a native identifier")
            asked = finish(call("POST", "/api/questions", 202, headers=origin,
                                json={"case_set": label, "case_number": 1,
                                      "scope": scope["scope"],
                                      "question": "Which conditions are "
                                                  "recorded?"}))
            result = asked.get("result") or {}
            if asked["state"] != "done" or result.get("outcome", {}).get(
                    "code") != "answer" or not any(
                    line["refs"] for line in result.get("lines", [])):
                problems.append("the question was not answered with "
                                "references in its lines")
            explained = finish(call("POST", "/api/explanations", 202,
                                    headers=origin,
                                    json={"case_set": label,
                                          "case_number": 1}))
            result = explained.get("result") or {}
            refs = {row["ref"] for section in shown.json()["sections"]
                    for block in section["blocks"]
                    if block["type"] == "table" for row in block["rows"]} | {
                section["ref"] for section in shown.json()["sections"]}
            if (explained["state"] != "done"
                    or result.get("outcome", {}).get("code") != "explanation"
                    or not result.get("conditions", {}).get("table")
                    or not set(result.get("references", [])) <= refs):
                problems.append("the explanation lacks its prediction, "
                                "conditions or evidence")
            question = {"case_set": label, "case_number": 1,
                        "scope": scope["scope"], "question": "x"}
            call("POST", "/api/questions", 403, json=question)
            call("POST", "/api/questions", 403, json=question, headers={
                "Origin": "http://rebound.example"})
            call("POST", "/api/questions", 415, headers=origin,
                 data=json.dumps(question))
            call("POST", "/api/questions", 413, headers=dict(
                origin, **{"Content-Type": "application/json"}),
                 data="x" * (MAX_BODY_BYTES + 1))
            call("POST", "/api/questions", 422, headers=origin,
                 json=dict(question, scope={"kind": "selected_visit"}))
            call("GET", "/api/jobs/" + "x" * 22, 404)
            call("GET", base + "/1/evidence?kind=other", 422)
            call("GET", "/api/case-sets/eval-100-v1/cases", 404)
            call("GET", "/", 403, headers={"Host": "rebound.example"})
    logged = "\n".join(lines)
    for secret in (token, label, "Which conditions"):
        if secret in logged:
            problems.append("the interface's log holds more than its "
                            "decisions")
    return made[0], problems


def main(argv=None):
    """Run the default mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.parse_args(argv)
    made, problems = run_synthetic()
    for problem in problems:
        print("PROBLEM: %s" % problem)
    print("requests checked over loopback: %d" % made)
    print("RESULT: %s" % ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
