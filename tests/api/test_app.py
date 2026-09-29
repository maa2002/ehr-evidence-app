"""Synthetic tests of the API and its boundary, over a real loopback
connection with the locked requests library.

The service serves the invented case set with a scripted model; logs are
written to a temporary directory. No real record, bundle or model is used.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest

import requests

from ehr_evidence_app.api import app, boundary
from ehr_evidence_app.generation import records

LIST = "Which conditions are recorded?"


class ApiTests(unittest.TestCase):
    """The routes and the boundary, served on a loopback port."""

    @classmethod
    def setUpClass(cls):
        """Serve the invented case set on a free loopback port."""
        cls.service = app.invented_service()
        cls.label = cls.service.case_sets()[0]["case_set"]
        cls.token = boundary.new_token()
        cls.lines = []
        cls.server = app.LoopbackServer(lambda port: app.create_app(
            cls.service, cls.token, boundary.loopback_hosts(port),
            cls.lines.append))
        cls.server.__enter__()
        cls.base = "/case-sets/%s/cases/1" % cls.label

    @classmethod
    def tearDownClass(cls):
        """Stop serving."""
        cls.server.__exit__(None, None, None)

    def call(self, method, path, token=True, **options):
        """Make one request over loopback; return the reply."""
        headers = dict(options.pop("headers", {}))
        if token:
            headers.setdefault("Authorization", "Bearer " + self.token)
        return requests.request(method, self.server.url + path,
                                headers=headers, timeout=30, **options)

    def test_routes(self):
        """Every route answers with its data, marked not to be cached."""
        for path in ("/health", "/case-sets", "/case-sets/%s/cases"
                     % self.label, self.base, self.base
                     + "/evidence?kind=pre_target_history", self.base
                     + "/evidence?kind=selected_visit&visit=1", self.base
                     + "/evidence?kind=prediction"):
            reply = self.call("GET", path)
            self.assertEqual(reply.status_code, 200, path)
            self.assertEqual(reply.headers["cache-control"], "no-store")
            self.assertEqual(reply.headers["referrer-policy"], "no-referrer")
            self.assertNotIn("access-control-allow-origin", reply.headers)
        question = self.call("POST", "/questions", json={
            "case_set": self.label, "case_number": 1,
            "scope": {"kind": "selected_visit", "visit_number": 1},
            "question": LIST}).json()
        self.assertEqual(question["response"]["scope"],
                         {"kind": "selected_visit", "visit_number": 1})
        self.assertEqual(question["author"], "model")
        explained = self.call("POST", "/explanations", json={
            "case_set": self.label, "case_number": 1}).json()
        self.assertEqual(explained["response"]["outcome"], "explanation")

    def test_boundary_refusals(self):
        """No token, a wrong one, another host, a cross-origin request and
        a large body are refused by code; the health check is open."""
        cases = (
            ({"token": False}, 401, "token_required"),
            ({"headers": {"Authorization": "Bearer " + "y" * 43}}, 401,
             "token_required"),
            ({"headers": {"Authorization": self.token}}, 401,
             "token_required"),
            ({"headers": {"Host": "rebound.example:80"}}, 403,
             "host_refused"),
            ({"headers": {"Origin": "https://example.org"}}, 403,
             "origin_refused"))
        for options, status, code in cases:
            with self.subTest(code=code, options=sorted(options)):
                reply = self.call("GET", "/case-sets", **options)
                self.assertEqual((reply.status_code, reply.json()),
                                 (status, {"error": code}))
                self.assertEqual(reply.headers["cache-control"], "no-store")
        self.assertEqual(self.call("GET", "/health",
                                   token=False).status_code, 200)
        big = "x" * (boundary.MAX_BODY_BYTES + 1)
        self.assertEqual(self.call("POST", "/questions",
                                   data=big).status_code, 413)
        self.assertEqual(self.call("POST", "/questions", data=iter(
            [big.encode("ascii")])).status_code, 413)

    def test_refused_case_sets_and_invalid_requests(self):
        """The evaluation set is not served; a malformed request names its
        fields, never their values."""
        reply = self.call("GET", "/case-sets/eval-100-v1/cases")
        self.assertEqual((reply.status_code, reply.json()),
                         (404, {"error": "case_set_not_served"}))
        reply = self.call("POST", "/questions", json={
            "case_set": self.label, "case_number": 1,
            "scope": {"kind": "pre_target_history"},
            "question": "Which conditions are recorded?", "extra": "a-b-c"})
        self.assertEqual(reply.status_code, 422)
        self.assertEqual(reply.json()["error"], "request_invalid")
        self.assertNotIn("a-b-c", reply.text)
        reply = self.call("GET", self.base + "/evidence?kind=everything")
        self.assertEqual(reply.json(), {"error": "scope_invalid"})

    def test_failure_is_a_bare_code(self):
        """An exception inside the service is answered with a bare code,
        and logged by its type only."""
        original = self.service.health

        def broken():
            """Fail with a message that must not leave the process."""
            raise RuntimeError("fixture detail 4471")

        self.service.health = broken
        try:
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                reply = self.call("GET", "/health")
        finally:
            self.service.health = original
        self.assertEqual((reply.status_code, reply.json()),
                         (500, {"error": "internal"}))
        self.assertIn("api error RuntimeError", self.lines)
        self.assertNotIn("4471", "\n".join(self.lines) + output.getvalue())

    def test_access_log_holds_routes_only(self):
        """The log names each request's route template and status, never
        the case set, the case, the question or the token."""
        before = len(self.lines)
        self.call("POST", "/questions", json={
            "case_set": self.label, "case_number": 1,
            "scope": {"kind": "pre_target_history"}, "question": LIST})
        self.call("GET", self.base + "/evidence?kind=case_visits")
        added = self.lines[before:]
        self.assertEqual(len(added), 2)
        self.assertRegex(added[0], r"^api POST /questions 200 \d+ ms$")
        self.assertRegex(added[1], r"^api GET /case-sets/\{case_set\}/"
                                   r"cases/\{case_number\}/evidence 200 ")
        text = "\n".join(self.lines)
        for value in (self.label, LIST, self.token, "case_visits"):
            self.assertNotIn(value, text)


class LogTests(unittest.TestCase):
    """The API's log file passes the log audit."""

    def test_log_file_passes_the_audit(self):
        """Lines written to the day's log file hold nothing the audit
        finds."""
        with tempfile.TemporaryDirectory() as directory:
            log = boundary.file_log(directory, name="api-test")
            log("api GET /case-sets/{case_set}/cases 200 4 ms")
            log("api error RuntimeError")
            flagged, read = records.audit_logs([directory])
            logger = boundary.logging.getLogger(
                "ehr_evidence.api-test.access")
            for handler in list(logger.handlers):
                handler.close()
                logger.removeHandler(handler)
        self.assertEqual((flagged, read), ([], 1))

    def test_token_is_required_long(self):
        """A short launch token is refused before anything is served."""
        with self.assertRaises(ValueError):
            boundary.Boundary(None, "short", ("127.0.0.1:1",))


class CommandTests(unittest.TestCase):
    """The command's modes."""

    def test_default_mode(self):
        """The default mode checks the invented case set over loopback."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(app.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())

    def test_serve_needs_a_token(self):
        """Serving without a launch token in the environment fails before
        anything is read."""
        saved = os.environ.pop(boundary.TOKEN_VARIABLE, None)
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(app.main(["--serve", "--port", "8601"]), 2)
        finally:
            if saved is not None:
                os.environ[boundary.TOKEN_VARIABLE] = saved
        self.assertIn("no launch token", output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(app.main(["--serve", "--port", "80"]), 2)


if __name__ == "__main__":
    unittest.main()
