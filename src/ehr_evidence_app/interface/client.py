"""The interface's client of the application's API (Phase 8, decision D1).

The interface reaches the API only over HTTP, on 127.0.0.1, with the
launch token it is given in its process environment (EHR_API_URL and
EHR_API_TOKEN, both set by the launcher). Proxy settings of the
environment are ignored, so that no request leaves the machine. A
refusal comes back as a ClientError holding the API's bare code.
"""

from __future__ import annotations

import os
import urllib.parse

import requests

from ehr_evidence_app.api import boundary

URL_VARIABLE = "EHR_API_URL"
LOCAL_PREFIX = "http://127.0.0.1:"
# Seconds to wait: for a lookup, and for a response the model may write
# (up to the time limit, and while the worker restarts).
LOOKUP_SECONDS = 30
WRITING_SECONDS = 600


class ClientError(Exception):
    """A request the API refused or could not answer: a bare code, and
    the HTTP status when there was a reply."""

    def __init__(self, code, status=None):
        super().__init__(code)
        self.code = code
        self.status = status


class Client:
    """The API's operations, over HTTP on 127.0.0.1."""

    def __init__(self, url, token, session=None):
        """Hold the API's address and the launch token."""
        port = url[len(LOCAL_PREFIX):].rstrip("/")
        if not url.startswith(LOCAL_PREFIX) or not port.isdigit():
            raise ClientError("api_not_local")
        self.url = LOCAL_PREFIX + port
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers["Authorization"] = "Bearer " + token

    @classmethod
    def from_environment(cls, environ=None):
        """Return the client named by the process environment."""
        environ = os.environ if environ is None else environ
        url = environ.get(URL_VARIABLE, "")
        token = environ.get(boundary.TOKEN_VARIABLE, "")
        if not url or not token:
            raise ClientError("api_not_configured")
        return cls(url, token)

    def call(self, method, path, seconds=LOOKUP_SECONDS, **options):
        """Make one request; return its JSON, or raise ClientError."""
        try:
            reply = self.session.request(method, self.url + path,
                                         timeout=seconds, **options)
        except requests.RequestException:
            raise ClientError("api_unreachable") from None
        try:
            body = reply.json()
        except ValueError:
            raise ClientError("api_reply_invalid", reply.status_code) from None
        if reply.status_code != 200:
            code = body.get("error") if isinstance(body, dict) else None
            raise ClientError(code if isinstance(code, str) else "api_refused",
                              reply.status_code)
        return body

    @staticmethod
    def case_path(case_set, case_number):
        """Return the path of a case."""
        return "/case-sets/%s/cases/%d" % (
            urllib.parse.quote(case_set, safe=""), case_number)

    def health(self):
        """Return the model's state, the served case sets and the
        versions."""
        return self.call("GET", "/health")

    def case_sets(self):
        """Return the served case sets."""
        return self.call("GET", "/case-sets")["case_sets"]

    def cases(self, case_set):
        """Return a case set's admitted cases."""
        return self.call("GET", "/case-sets/%s/cases" % urllib.parse.quote(
            case_set, safe=""))["cases"]

    def case(self, case_set, case_number):
        """Return a case's scopes and its prepared prediction."""
        return self.call("GET", self.case_path(case_set, case_number))

    def evidence(self, case_set, case_number, kind, visit_number=None):
        """Return the evidence of a scope, or of the prediction."""
        params = {"kind": kind}
        if visit_number is not None:
            params["visit"] = visit_number
        return self.call("GET", self.case_path(case_set, case_number)
                         + "/evidence", params=params)

    def ask(self, case_set, case_number, scope, question):
        """Return the response to a question about a case in a scope."""
        return self.call("POST", "/questions", seconds=WRITING_SECONDS,
                         json={"case_set": case_set,
                               "case_number": case_number, "scope": scope,
                               "question": question})

    def explain(self, case_set, case_number):
        """Return the explanation of a case's prepared prediction."""
        return self.call("POST", "/explanations", seconds=WRITING_SECONDS,
                         json={"case_set": case_set,
                               "case_number": case_number})
