"""The application's HTTP API (Phase 8, work item 47).

Every route but /health needs the launch token (api.boundary):

    GET  /health
        the model's state, the served case sets and the versions
    GET  /case-sets
        the served case sets and their numbers of cases
    GET  /case-sets/{case_set}/cases
        their admitted cases, with their numbers of visits
    GET  /case-sets/{case_set}/cases/{case_number}
        a case's scopes and its prepared prediction
    GET  /case-sets/{case_set}/cases/{case_number}/evidence?kind=K&visit=N
        the evidence of a scope, or of the prediction (kind=prediction)
    POST /questions
        a question about a case in a scope
    POST /explanations
        the explanation of a case's prepared prediction

A question or an explanation returns the gate's response exactly, with
who wrote it and the seconds it took, and writes its protected runtime
record (api.service). A refusal is a bare code.

The default mode serves the invented case set on a free loopback port
with a scripted model, and checks every route and the boundary over a real
connection; nothing is written. --serve serves on 127.0.0.1 only, taking
the launch token from the process environment (EHR_API_TOKEN), as the
launcher (api.launch) sets it: the invented case set and its scripted
model, reading and writing nothing, or with --operator-real the
development case sets with the model, logging routes only to the
workspace's logs.

Usage:
    python -m ehr_evidence_app.api.app
    python -m ehr_evidence_app.api.app --serve --port 8601
    python -m ehr_evidence_app.api.app --serve --port 8601 --operator-real
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sys
import threading
import time

from fastapi import FastAPI, Path, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ehr_evidence_app import __version__
from ehr_evidence_app.api import boundary
from ehr_evidence_app.api import service as services
from ehr_evidence_app.evidence import contracts
from ehr_evidence_app.qa import answer

CASE_SET = r"^[a-z0-9][a-z0-9.-]{0,63}$"


class QuestionBody(BaseModel):
    """A question about a case in a scope."""

    model_config = ConfigDict(extra="forbid")

    case_set: contracts.CaseSetId
    case_number: int = Field(ge=1, le=10000)
    scope: contracts.ScopeRef
    question: contracts.QuestionText


class ExplanationBody(BaseModel):
    """A request for the explanation of a case's prepared prediction."""

    model_config = ConfigDict(extra="forbid")

    case_set: contracts.CaseSetId
    case_number: int = Field(ge=1, le=10000)


def create_app(service, token, hosts, log=None):
    """Return the API application of a service behind its boundary: the
    launch token, the Host values it answers to and the access log (a
    function of one line). The service's model is stopped when the
    server shuts down."""

    @contextlib.asynccontextmanager
    async def lifespan(app):
        """Stop the model when the server shuts down."""
        yield
        service.stop()

    api = FastAPI(title="EHR evidence app API", version=__version__,
                  docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)

    @api.exception_handler(services.ServiceError)
    async def refused(request, error):
        """Answer a request the service cannot serve with its code."""
        return JSONResponse({"error": error.code},
                            status_code=error.status)

    @api.exception_handler(RequestValidationError)
    async def invalid(request, error):
        """Answer a malformed request with the fields at fault, never
        their values."""
        fields = sorted({".".join(str(part) for part in item["loc"])
                         for item in error.errors()})
        return JSONResponse({"error": "request_invalid", "fields": fields},
                            status_code=422)

    @api.exception_handler(answer.AnswerError)
    async def failed(request, error):
        """Answer a failed read of the pinned evidence with its code."""
        return JSONResponse({"error": str(error)}, status_code=500)

    @api.exception_handler(Exception)
    async def internal(request, error):
        """Answer any other failure with a bare code; the boundary logs
        its type."""
        return JSONResponse({"error": "internal"}, status_code=500)

    @api.get("/health")
    def health():
        """The model's state, the served case sets and the versions."""
        return service.health()

    @api.get("/case-sets")
    def case_sets():
        """The served case sets."""
        return {"case_sets": service.case_sets()}

    @api.get("/case-sets/{case_set}/cases")
    def cases(case_set: str = Path(pattern=CASE_SET)):
        """A case set's admitted cases."""
        return {"case_set": case_set, "cases": service.cases(case_set)}

    @api.get("/case-sets/{case_set}/cases/{case_number}")
    def case(case_set: str = Path(pattern=CASE_SET),
             case_number: int = Path(ge=1, le=10000)):
        """A case's scopes and its prepared prediction."""
        return service.case(case_set, case_number)

    @api.get("/case-sets/{case_set}/cases/{case_number}/evidence")
    def evidence(case_set: str = Path(pattern=CASE_SET),
                 case_number: int = Path(ge=1, le=10000),
                 kind: str = Query(),
                 visit: int | None = Query(default=None, ge=1, le=1000)):
        """The evidence of a scope, or of the prediction."""
        if kind not in services.EVIDENCE_KINDS:
            raise services.ServiceError("scope_invalid", 422)
        return service.evidence(case_set, case_number, kind, visit)

    @api.post("/questions")
    def questions(body: QuestionBody):
        """Answer a question about a case in a scope."""
        return service.ask(body.case_set, body.case_number, body.scope,
                           body.question)

    @api.post("/explanations")
    def explanations(body: ExplanationBody):
        """Explain a case's prepared prediction."""
        return service.explain(body.case_set, body.case_number)

    return boundary.Boundary(api, token, hosts, log, api.routes)


class LoopbackServer:
    """An ASGI application served on a free port of 127.0.0.1, in a
    thread; make_app(port) returns the application."""

    def __init__(self, make_app):
        """Bind the port and build the application for it."""
        import uvicorn
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("127.0.0.1", 0))
        self.port = self.socket.getsockname()[1]
        self.url = "http://127.0.0.1:%d" % self.port
        self.app = make_app(self.port)
        self.server = uvicorn.Server(uvicorn.Config(
            self.app, log_config=None, access_log=False,
            log_level="warning", server_header=False, date_header=False))
        self.thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.socket]},
            name="loopback-server", daemon=True)

    def __enter__(self):
        """Serve, and wait until the server has started."""
        self.thread.start()
        deadline = time.monotonic() + 30
        while not self.server.started:
            if time.monotonic() > deadline or not self.thread.is_alive():
                raise RuntimeError("the loopback server did not start")
            time.sleep(0.02)
        return self

    def __exit__(self, *failure):
        """Stop serving."""
        self.server.should_exit = True
        self.thread.join(timeout=30)
        self.socket.close()


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

def invented_service():
    """Return the service of the invented case set with a scripted model
    that writes each invented request's template document."""
    from ehr_evidence_app.generation import records, shape
    service = services.Service.invented()
    writer = records.scripted_writer(shape.synthetic_contexts())
    service.use_backend(writer.backend, writer.limits).join()
    return service


def run_synthetic():
    """Serve the invented case set on loopback and check every route and
    the boundary; return (requests made, problems). Nothing is written."""
    import requests
    service = invented_service()
    token, lines = boundary.new_token(), []
    problems, made = [], [0]
    with LoopbackServer(lambda port: create_app(
            service, token, boundary.loopback_hosts(port),
            lines.append)) as server:
        http = requests.Session()
        http.headers["Authorization"] = "Bearer " + token

        def call(method, path, expected, **options):
            """Make one request and check its status; return its JSON."""
            made[0] += 1
            reply = http.request(method, server.url + path, timeout=30,
                                 **options)
            if reply.status_code != expected:
                problems.append("%s %s answered %d, not %d" % (
                    method, path.split("?")[0], reply.status_code,
                    expected))
            if reply.headers.get("cache-control") != "no-store":
                problems.append("%s is not marked no-store" % path)
            return reply.json()

        label = call("GET", "/case-sets", 200)["case_sets"][0]["case_set"]
        base = "/case-sets/%s/cases/1" % label
        case = call("GET", base, 200)
        if not case["scopes"] or case["prediction"] is None:
            problems.append("the case lacks its scopes or its prediction")
        scope = case["scopes"][0]
        for question in ("Which conditions are recorded?",
                         "Is synthetic concept 1001 recorded?",
                         "What medications were given?"):
            found = call("POST", "/questions", 200, json={
                "case_set": label, "case_number": 1, "scope": scope,
                "question": question})
            response = found["response"]
            if (response["case"] != {"case_set": label, "case_number": 1}
                    or found["author"] not in ("model", "template")):
                problems.append("an answer changed its case or author")
        explained = call("POST", "/explanations", 200,
                         json={"case_set": label, "case_number": 1})
        if explained["response"]["outcome"] != "explanation":
            problems.append("the invented prediction was not explained")
        view = call("GET", base + "/evidence?kind=prediction", 200)
        shown = {item["ref"] for item in view["attributions"]} | {
            item["ref"] for item in view["history_records"]
            + view["final_admission_context"]} | {"P1", "C1"}
        cited = {ref for claim in explained["response"]["claims"]
                 for ref in claim["refs"]}
        if not cited <= shown:
            problems.append("an explanation cites what its evidence "
                            "does not show")
        text = json.dumps(view) + json.dumps(call(
            "GET", base + "/evidence?kind=pre_target_history", 200))
        if "native_id" in text or "visit_occurrence_id" in text:
            problems.append("the evidence shows a native identifier")
        call("GET", "/case-sets/eval-100-v1/cases", 404)
        call("GET", "/health", 200, headers={"Authorization": ""})
        call("GET", "/case-sets", 401, headers={"Authorization": ""})
        call("GET", "/case-sets", 401,
             headers={"Authorization": "Bearer " + "x" * 43})
        call("GET", "/case-sets", 403, headers={"Host": "rebound.example"})
        call("GET", "/case-sets", 403,
             headers={"Origin": "http://127.0.0.1:1"})
        call("POST", "/questions", 413, data="x" * (
            boundary.MAX_BODY_BYTES + 1))
    logged = "\n".join(lines)
    for secret in (token, label, "Which conditions"):
        if secret in logged:
            problems.append("the access log holds more than routes")
    if len(lines) < made[0]:
        problems.append("a request was not logged")
    return made[0], problems


def serve(port, real=False, model=True):
    """Serve the invented case set and its scripted model, or with real
    the development case sets and the model, on 127.0.0.1 with the launch
    token of the process environment; blocks until stopped."""
    import uvicorn
    token = os.environ.get(boundary.TOKEN_VARIABLE, "")
    if len(token) < boundary.MIN_TOKEN_CHARS:
        print("FAILED: %s holds no launch token" % boundary.TOKEN_VARIABLE)
        return 2
    if not real:
        service, log = invented_service(), None
    else:
        service, log = services.Service.from_workspace(), boundary.file_log()
        if model:
            from ehr_evidence_app.generation import runtime
            service.use_backend(runtime.WorkerBackend())
    app = create_app(service, token, boundary.loopback_hosts(port), log)
    boundary.quiet_access_log()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_config=None,
                    access_log=False, log_level="warning",
                    server_header=False, date_header=False)
    finally:
        # The shutdown already stopped the model; this covers a server
        # that never started.
        service.stop()
    return 0


def main(argv=None):
    """Run the default mode, or serve the development case sets."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serve", action="store_true",
                        help="serve on 127.0.0.1: the invented case set, "
                             "or with --operator-real the development ones")
    parser.add_argument("--operator-real", action="store_true",
                        help="serve the development case sets, with the "
                             "model")
    parser.add_argument("--port", type=int, default=8601)
    parser.add_argument("--no-model", action="store_true",
                        help="answer the development case sets with the "
                             "templates only")
    args = parser.parse_args(argv)
    if (args.operator_real or args.no_model) and not args.serve:
        print("FAILED: --operator-real and --no-model go with --serve")
        return 2
    if args.no_model and not args.operator_real:
        print("FAILED: --no-model goes with --operator-real")
        return 2
    if args.serve:
        if not 1024 <= args.port <= 65535:
            print("FAILED: --port is a number from 1024 to 65535")
            return 2
        return serve(args.port, real=args.operator_real,
                     model=not args.no_model)
    made, problems = run_synthetic()
    for problem in problems:
        print("PROBLEM: %s" % problem)
    print("requests checked over loopback: %d" % made)
    print("RESULT: %s" % ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
