"""Junk requests to the interface's own server, which is what the tunnel
exposes: every one is answered, or refused with a code and its meaning,
and none is an internal error.

Junk questions (tests.qa.test_junk_input) are asked as jobs, as the page
asks them, and read back; malformed bodies, paths and queries are sent
as they are. Every case is invented.
"""

import json
import random

from tests.interface.test_app import InterfaceTestCase
from tests.qa import test_junk_input as junk_input

ALLOWED = (200, 202, 404, 405, 413, 415, 422)


class JunkTests(InterfaceTestCase):
    """Junk through the interface."""

    def send(self, server, method, path, data=None, **headers):
        """Send raw bytes as the page's own origin."""
        return self.call(server, method, path, data=data, headers=dict(
            {"Origin": server.url, "Content-Type": "application/json"},
            **headers))

    def check(self, reply, label, problems):
        """Record a reply that is an internal error, or a refusal
        without a code and its meaning."""
        if reply.status_code not in ALLOWED:
            problems.append("%s: %d" % (label, reply.status_code))
            return
        if reply.status_code >= 400:
            try:
                found = reply.json()
            except ValueError:
                problems.append("%s: not JSON" % label)
                return
            if not (isinstance(found.get("error"), str)
                    and found.get("message")):
                problems.append("%s: no code and meaning" % label)

    def test_junk_questions(self):
        """Junk questions asked as jobs end in a response or a refusal,
        never an internal error."""
        server = self.serve()
        chance, problems, done = random.Random(junk_input.SEED), [], 0
        for number in range(150):
            question = junk_input.junk(chance)
            reply = self.send(server, "POST", "/api/questions", json.dumps(
                self.question(question=question)).encode("ascii"))
            label = "junk question %d" % number
            if reply.status_code != 202:
                self.check(reply, label, problems)
                continue
            found = self.finish(server, reply)
            if found["state"] == "failed":
                if found["error"]["error"] == "internal":
                    problems.append(label + ": internal")
            elif found["result"]["outcome"]["code"] not in (
                    junk_input.OUTCOMES):
                problems.append(label + ": " + found["result"]["outcome"][
                    "code"])
            else:
                done += 1
        self.assertEqual(problems, [])
        self.assertEqual(self.lines, [])
        self.assertGreater(done, 100)

    def test_malformed_requests(self):
        """Malformed bodies, paths and queries are refused with a code
        and its meaning."""
        server = self.serve()
        problems = []
        for number, data in enumerate((
                b"", b"{", b"null", b"[]", b"\"x\"", b"1e999",
                b"\xff\xfe\x00", b"{\"question\": \"\xc3\x28\"}",
                b"[" * 2000 + b"]" * 2000,
                b"{\"a\":" * 800 + b"1" + b"}" * 800,
                json.dumps(self.question()).encode("utf-16"),
                json.dumps(self.question(question=7)).encode("ascii"),
                json.dumps(self.question(question="\ud800 labs")).encode(
                    "ascii"),
                json.dumps(self.question(case_number=10 ** 30)).encode(
                    "ascii"),
                json.dumps(self.question(scope=[])).encode("ascii"))):
            for path in ("/api/questions", "/api/explanations"):
                self.check(self.send(server, "POST", path, data),
                           "body %d to %s" % (number, path), problems)
        base = "/api/case-sets/%s/cases" % self.label
        for path, params in (
                ("/api/case-sets/%00/cases", None),
                ("/api/case-sets/..%2F..%2Fetc/cases", None),
                ("/api/case-sets/" + "a" * 100 + "/cases", None),
                ("/api/case-sets/%C3%A9/cases", None),
                (base + "/abc", None), (base + "/-1", None),
                (base + "/99999999999999999999", None),
                (base + "/1.5", None), (base + "/1/evidence", None),
                (base + "/1/evidence", {"kind": "selected_visit",
                                        "visit": "99999999999"}),
                (base + "/1/evidence", {"kind": "selected_visit",
                                        "visit": "-1"}),
                (base + "/1/evidence", {"kind": "case_visits",
                                        "visit": "1"}),
                (base + "/1/evidence", {"kind": "\u0000"}),
                ("/api/jobs/" + "%00" * 5, None),
                ("/api/jobs/" + "a" * 500, None), ("/api/nope", None)):
            self.check(self.call(server, "GET", path, params=params),
                       "GET " + path[:40], problems)
        for method, path in (("PUT", "/api/questions"),
                             ("DELETE", "/api/state"),
                             ("GET", "/api/questions")):
            self.check(self.send(server, method, path, b"{}"),
                       "%s %s" % (method, path), problems)
        self.assertEqual(problems, [])
        self.assertEqual(self.call(server, "GET", "/health").json(),
                         {"status": "ok"})
