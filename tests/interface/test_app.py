"""Synthetic tests of the interface's web application (interface.app).

The API serves the invented case set with its scripted model on one
loopback port, and the interface, behind its gate, on another; requests
are made as the page makes them, with the locked requests library. No
real record is used.
"""

import hashlib
import json
import secrets
import socket
import time
import unittest

import requests

from ehr_evidence_app.api import app as api
from ehr_evidence_app.api import boundary
from ehr_evidence_app.interface import app as interface
from ehr_evidence_app.interface import client as clients
from ehr_evidence_app.interface import gate, names, views

PUBLIC = "ehr.example.org"
LIST = "Which conditions are recorded?"


class InterfaceTestCase(unittest.TestCase):
    """The invented API, and the interface over it."""

    @classmethod
    def setUpClass(cls):
        """Serve the invented case set through the API."""
        cls.service = api.invented_service()
        cls.label = cls.service.case_sets()[0]["case_set"]
        cls.token = boundary.new_token()
        cls.backend = api.LoopbackServer(lambda port: api.create_app(
            cls.service, cls.token, boundary.loopback_hosts(port)))
        cls.backend.__enter__()

    @classmethod
    def tearDownClass(cls):
        """Stop the API."""
        cls.backend.__exit__(None, None, None)

    def serve(self, client=None, share=None):
        """Serve the interface behind its gate; return the server."""
        self.lines = []
        client = client or clients.Client(self.backend.url, self.token)
        server = api.LoopbackServer(lambda port: gate.Gate(
            interface.create_app(client, log=self.lines.append),
            gate.local_hosts(port), share, self.lines.append))
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        self.http = requests.Session()
        self.http.trust_env = False
        return server

    def call(self, server, method, path, **options):
        """Make one request of the interface."""
        return self.http.request(method, server.url + path, timeout=30,
                                 allow_redirects=False, **options)

    def post(self, server, path, body, **headers):
        """Post as the page does: JSON, from the page's own origin."""
        return self.call(server, "POST", path, json=body, headers=dict(
            {"Origin": server.url}, **headers))

    def finish(self, server, reply, **options):
        """Ask after a job until it is done; return its state."""
        self.assertEqual(reply.status_code, 202)
        key = reply.json()["job"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            found = self.call(server, "GET", "/api/jobs/" + key, **options)
            self.assertEqual(found.status_code, 200)
            if found.json()["state"] != "running":
                return found.json()
            time.sleep(0.05)
        self.fail("the job did not finish")

    def question(self, **changes):
        """Return a question's body about the invented case."""
        return dict({"case_set": self.label, "case_number": 1,
                     "scope": {"kind": "pre_target_history"},
                     "question": LIST}, **changes)


class PageTests(InterfaceTestCase):
    """The page and its files."""

    def test_page_and_files(self):
        """The page names its files by a digest of their content, carries
        the content security policy and is never cached; its files are
        checked again before each use."""
        server = self.serve()
        page = self.call(server, "GET", "/")
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.headers["content-type"].startswith(
            "text/html"))
        self.assertEqual(page.headers["cache-control"], "no-store")
        self.assertIn("script-src 'self'",
                      page.headers["content-security-policy"])
        self.assertNotIn("{{version}}", page.text)
        version = interface.page_text().split("app.js?v=")[1][:16]
        self.assertIn("/static/app.js?v=%s" % version, page.text)
        for name in interface.ASSETS:
            reply = self.call(server, "GET", "/static/" + name)
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(reply.headers["cache-control"], "no-cache")
        self.assertEqual(self.call(server, "GET",
                                   "/static/none.js").status_code, 404)
        self.assertEqual(self.call(server, "GET", "/health").json(),
                         {"status": "ok"})


class OperationTests(InterfaceTestCase):
    """What the page reads and asks."""

    def test_state_cases_and_case(self):
        """The state names the model's state and the cohorts; a cohort
        lists its patients by pseudonym; a case has its scopes and its
        prediction."""
        server = self.serve()
        state = self.call(server, "GET", "/api/state").json()
        self.assertEqual(state["model"]["state"], "ready")
        self.assertEqual(state["case_sets"][0]["label"],
                         "Demonstration Cohort")
        self.assertEqual(state["question_chars"], 500)
        base = "/api/case-sets/%s/cases" % self.label
        cases = self.call(server, "GET", base).json()["cases"]
        self.assertEqual(cases[0]["name"], names.pseudonym(self.label, 1))
        case = self.call(server, "GET", base + "/1").json()
        self.assertEqual(case["scopes"][0]["key"], "pre_target_history")
        self.assertEqual(case["scopes"][-1]["detail"],
                         "3 Jan 2100 · Final Admission")
        self.assertEqual(case["prediction"]["ref"], "P1")
        found = self.call(server, "GET", base + "/1/evidence",
                          params={"kind": "selected_visit", "visit": 1})
        self.assertEqual(found.json()["title"], "Visit 1")

    def test_the_help(self):
        """The help is what the page shows for /help: every kind of
        information with its examples, never cached."""
        server = self.serve()
        found = self.call(server, "GET", "/api/help")
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.headers["cache-control"], "no-store")
        shown = found.json()
        self.assertEqual(shown, json.loads(json.dumps(views.help_view())))
        self.assertTrue(all(row["examples"] for section in shown["sections"]
                            for row in section["rows"]))

    def test_question_is_a_job(self):
        """A question is answered in the background; its result shows the
        outcome and the references of its lines."""
        server = self.serve()
        found = self.finish(server, self.post(server, "/api/questions",
                                              self.question()))
        self.assertEqual(found["state"], "done")
        result = found["result"]
        self.assertEqual(result["outcome"]["code"], "answer")
        self.assertEqual(result["references"], ["E1"])
        self.assertTrue(any(line["refs"] for line in result["lines"]))
        self.assertEqual(result["scope"]["label"], "Prior History")

    def test_explanation_is_a_job(self):
        """An explanation carries the prediction, the unsigned note and
        the recorded conditions apart."""
        server = self.serve()
        found = self.finish(server, self.post(
            server, "/api/explanations",
            {"case_set": self.label, "case_number": 1}))
        result = found["result"]
        self.assertEqual(result["outcome"]["code"], "explanation")
        self.assertEqual(result["prediction"]["label"], "SYNTHETIC LABEL")
        self.assertTrue(any("unsigned" in note for note in result["notes"]))
        self.assertIn("GNN model", result["intro"])
        self.assertEqual(result["prediction"]["timeline"]["final"],
                         "Visit 3 · 3 Jan 2100")
        self.assertTrue(result["conditions"]["table"]["rows"])

    def test_refusals(self):
        """What the page could not have sent is refused with a code and
        its meaning; nothing reaches the API."""
        server = self.serve()
        base = "/api/case-sets/%s/cases" % self.label
        for method, path, options, status, code in (
                ("GET", base + "/1/evidence", {"params": {"kind": "x"}},
                 422, "scope_invalid"),
                ("GET", base + "/1/evidence",
                 {"params": {"kind": "selected_visit"}}, 422,
                 "scope_invalid"),
                ("GET", base + "/0", {}, 422, "request_invalid"),
                ("GET", "/api/case-sets/eval-100-v1/cases", {}, 404,
                 "case_set_not_served"),
                ("GET", "/api/case-sets/Bad_Set/cases", {}, 404,
                 "case_set_not_served"),
                ("GET", "/api/jobs/" + "x" * 22, {}, 404, "job_unknown")):
            reply = self.call(server, method, path, **options)
            self.assertEqual((reply.status_code, reply.json()["error"]),
                             (status, code), path)
            self.assertTrue(reply.json()["message"])
        for body, status in (
                (self.question(scope={"kind": "selected_visit"}), 422),
                (self.question(scope={"kind": "other"}), 422),
                (self.question(question="  "), 422),
                (self.question(case_number=True), 422),
                (dict(self.question(), extra=1), 422)):
            self.assertEqual(self.post(server, "/api/questions",
                                       body).status_code, status)
        self.assertEqual(self.call(
            server, "POST", "/api/questions", data="{}",
            headers={"Origin": server.url,
                     "Content-Type": "text/plain"}).status_code, 415)
        self.assertEqual(self.call(
            server, "POST", "/api/questions",
            data="x" * (interface.MAX_BODY_BYTES + 1),
            headers={"Origin": server.url,
                     "Content-Type": "application/json"}).status_code, 413)

    def test_api_failures(self):
        """An API that does not answer is said to; a failure of any other
        kind is internal, logged by its type only."""
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            url = "http://127.0.0.1:%d" % probe.getsockname()[1]
        server = self.serve(clients.Client(url, self.token))
        reply = self.call(server, "GET", "/api/state")
        self.assertEqual((reply.status_code, reply.json()["error"]),
                         (502, "api_unreachable"))

        class Failing(clients.Client):
            """A client that fails with words that must not be logged."""

            def health(self):
                """Fail."""
                raise RuntimeError("a patient's words")

        server = self.serve(Failing(self.backend.url, self.token))
        reply = self.call(server, "GET", "/api/state")
        self.assertEqual((reply.status_code, reply.json()["error"]),
                         (500, "internal"))
        self.assertEqual(self.lines,
                         ["interface operation failed: RuntimeError"])


class SessionTests(InterfaceTestCase):
    """What a session started is its own."""

    def test_jobs_belong_to_their_session(self):
        """A job started through the tunnel is read by its session only,
        neither by another session nor by this machine."""
        key = secrets.token_urlsafe(32)
        server = self.serve(share=gate.Share(PUBLIC, hashlib.sha256(
            key.encode()).hexdigest()))

        def session():
            """Open a session with the secret link; return its cookie."""
            reply = self.call(server, "GET", "/", params={"k": key},
                              headers={"Host": PUBLIC})
            self.assertEqual(reply.status_code, 303)
            return reply.headers["set-cookie"].split(";")[0]

        mine, other = session(), session()
        remote = {"Host": PUBLIC, "Cookie": mine}
        reply = self.post(server, "/api/questions", self.question(),
                          Origin="https://" + PUBLIC, **remote)
        self.assertEqual(reply.status_code, 202)
        job = "/api/jobs/" + reply.json()["job"]
        self.assertEqual(self.finish(server, reply, headers=remote)[
            "state"], "done")
        self.assertEqual(self.call(server, "GET", job, headers={
            "Host": PUBLIC, "Cookie": other}).status_code, 404)
        self.assertEqual(self.call(server, "GET", job).status_code, 404)
        self.assertEqual(self.post(server, "/api/questions",
                                   self.question(), **remote).status_code,
                         403)


if __name__ == "__main__":
    unittest.main()
