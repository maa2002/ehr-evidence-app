"""Junk requests to the API, served on a loopback port: every one is
answered with a response or refused by code, and none is an internal
error.

Junk questions (tests.qa.test_junk_input) are sent in well-formed
requests, and malformed requests are sent as they are: bodies that are not
JSON, are not UTF-8, nest deeply or hold the wrong types, numbers out of
range, unknown case sets and scopes, and unpaired surrogates escaped in
JSON, unknown routes and methods. Each must be answered with a response,
or refused (400, 404, 405, 413 or 422) with a bare code; the access log
must hold no internal error; and the API must still answer afterwards.
Every case is invented.
"""

import json
import random
import unittest

import requests

from ehr_evidence_app.api import app, boundary
from tests.qa import test_junk_input as junk_input

ALLOWED = (200, 400, 404, 405, 413, 422)


class JunkRequestTests(unittest.TestCase):
    """Junk over HTTP."""

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

    @classmethod
    def tearDownClass(cls):
        """Stop serving."""
        cls.server.__exit__(None, None, None)

    def post(self, data, content_type="application/json"):
        """Post raw bytes to the questions route; return the reply."""
        return requests.post(self.server.url + "/questions", data=data,
                             headers={"Authorization": "Bearer " + self.token,
                                      "Content-Type": content_type},
                             timeout=30)

    def body(self, **changes):
        """Return a well-formed request body with some fields changed."""
        found = {"case_set": self.label, "case_number": 1,
                 "scope": {"kind": "case_visits"},
                 "question": "What labs were done?"}
        found.update(changes)
        return found

    def check(self, reply, label, problems):
        """Record a reply that is an internal error, not JSON, or a
        refusal without a bare code."""
        if reply.status_code not in ALLOWED:
            problems.append("%s: %d" % (label, reply.status_code))
            return
        try:
            found = reply.json()
        except ValueError:
            problems.append("%s: not JSON" % label)
            return
        if reply.status_code != 200 and not (
                isinstance(found.get("error"), str)
                and set(found) <= {"error", "fields"}):
            problems.append("%s: no bare code" % label)

    def test_junk_questions(self):
        """Junk questions in well-formed requests are answered, or
        refused as invalid when they are not text the request allows."""
        chance, problems, answered = random.Random(junk_input.SEED), [], 0
        for number in range(300):
            question = junk_input.junk(chance)
            data = json.dumps(self.body(question=question)).encode("ascii")
            reply = self.post(data)
            self.check(reply, "junk question %d" % number, problems)
            if reply.status_code == 200:
                answered += 1
                outcome = reply.json()["response"]["outcome"]
                if outcome not in junk_input.OUTCOMES:
                    problems.append("junk question %d: %s" % (number,
                                                              outcome))
        self.assertEqual(problems, [])
        self.assertGreater(answered, 250)

    def test_malformed_requests(self):
        """Malformed requests are refused by code, never answered with an
        internal error."""
        problems = []
        raw = (b"", b"{", b"null", b"[]", b"\"x\"", b"1e999", b"\xff\xfe\x00",
               b"{\"question\": \"\xc3\x28\"}", b"[" * 5000 + b"]" * 5000,
               b"{\"a\":" * 3000 + b"1" + b"}" * 3000,
               json.dumps(self.body()).encode("utf-16"))
        for number, data in enumerate(raw):
            self.check(self.post(data), "raw body %d" % number, problems)
        self.check(self.post(json.dumps(self.body()).encode("ascii"),
                             "text/plain"), "text/plain", problems)
        changes = (
            {"case_number": "1"}, {"case_number": 1.5}, {"case_number": 0},
            {"case_number": -1}, {"case_number": 10 ** 30},
            {"case_number": True}, {"case_number": None},
            {"case_number": 9999}, {"case_set": "EVAL"},
            {"case_set": "x" * 65}, {"case_set": "../etc"},
            {"case_set": "é"}, {"case_set": "eval-100-v1"},
            {"case_set": ["a"]}, {"scope": {"kind": "everything"}},
            {"scope": {"kind": "selected_visit"}},
            {"scope": {"kind": "selected_visit", "visit_number": 99}},
            {"scope": {"kind": "selected_visit", "visit_number": -1}},
            {"scope": {"kind": "selected_visit", "visit_number": "2"}},
            {"scope": {"kind": "selected_visit",
                       "visit_number": 10 ** 12}},
            {"scope": "case_visits"}, {"scope": None}, {"scope": []},
            {"question": None}, {"question": ""}, {"question": 7},
            {"question": ["labs"]}, {"question": {"text": "labs"}},
            {"question": "x" * 4001}, {"question": "x" * 2001},
            {"question": "\ud800 labs"}, {"question": "labs\udfff"},
            {"extra": "field"})
        for number, change in enumerate(changes):
            data = json.dumps(self.body(**change)).encode("ascii")
            self.check(self.post(data), "change %d %s" % (
                number, sorted(change)), problems)
        missing = self.body()
        del missing["question"]
        self.check(self.post(json.dumps(missing).encode("ascii")),
                   "no question", problems)
        headers = {"Authorization": "Bearer " + self.token}
        for method, path in (("GET", "/nope"), ("GET", "/questions"),
                             ("DELETE", "/health"), ("PUT", "/questions"),
                             ("POST", "/case-sets")):
            self.check(requests.request(method, self.server.url + path,
                                        headers=headers, timeout=30),
                       "%s %s" % (method, path), problems)
        self.assertEqual(problems, [])
        self.assertFalse([line for line in self.lines if "api error" in line])
        health = requests.get(self.server.url + "/health", timeout=30)
        self.assertEqual(health.status_code, 200)


if __name__ == "__main__":
    unittest.main()
