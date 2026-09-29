"""Synthetic tests of the interface's access checks (interface.gate).

A small invented application stands behind the gate on a loopback port;
requests are made with the locked requests library. The secret link's key
is made here and never written out.
"""

import asyncio
import hashlib
import secrets
import unittest

import requests
from starlette.applications import Starlette
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from ehr_evidence_app.api import app as api
from ehr_evidence_app.interface import gate

PUBLIC = "ehr.example.org"


async def page(request):
    """The invented page."""
    return PlainTextResponse("page")


async def script(request):
    """An invented static file."""
    return PlainTextResponse("static")


async def echo(request):
    """An invented operation: whether the gate named a session."""
    return JSONResponse({"session": gate.SESSION_SCOPE_KEY in request.scope})


def invented_app():
    """Return the invented application."""
    return Starlette(routes=[Route("/", page),
                             Route("/static/app.js", script),
                             Route("/echo", echo, methods=["GET", "POST"])])


class GateTests(unittest.TestCase):
    """Local access, sharing through the secret link, and requests from
    other sites."""

    def serve(self, share=None, clock=None):
        """Serve the invented application behind a gate; return it."""
        self.lines = []
        server = api.LoopbackServer(lambda port: gate.Gate(
            invented_app(), gate.local_hosts(port), share, self.lines.append,
            **({"clock": clock} if clock else {})))
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        return server

    def shared(self, **options):
        """Serve the invented application shared with a new key; return
        the server and the key."""
        key = secrets.token_urlsafe(32)
        return self.serve(gate.Share(PUBLIC, hashlib.sha256(
            key.encode()).hexdigest()), **options), key

    def call(self, server, path="/", host=None, headers=None,
             method="GET"):
        """Make a request naming a host; do not follow redirects."""
        found = dict(headers or {})
        if host:
            found["Host"] = host
        session = requests.Session()
        session.trust_env = False
        return session.request(method, server.url + path, headers=found,
                               timeout=30, allow_redirects=False)

    def open_session(self, server, key):
        """Open a session with the secret link; return its cookie."""
        return self.call(server, "/?k=" + key, host=PUBLIC).headers[
            "set-cookie"].split(";")[0]

    def test_local_access(self):
        """This machine's host is let in; every response carries the
        content security policy and is marked not to be cached, the
        static files being checked again before use; any other host is
        refused."""
        server = self.serve()
        reply = self.call(server)
        self.assertEqual((reply.status_code, reply.text), (200, "page"))
        for name, value in (("cache-control", "no-store"),
                            ("referrer-policy", "no-referrer"),
                            ("x-frame-options", "DENY"),
                            ("x-content-type-options", "nosniff"),
                            ("x-robots-tag", "noindex, nofollow"),
                            ("cross-origin-opener-policy", "same-origin")):
            self.assertEqual(reply.headers[name], value)
        policy = reply.headers["content-security-policy"]
        for part in ("default-src 'none'", "script-src 'self'",
                     "frame-ancestors 'none'",
                     "require-trusted-types-for 'script'"):
            self.assertIn(part, policy)
        self.assertEqual(self.call(server, "/static/app.js").headers[
            "cache-control"], "no-cache")
        refused = self.call(server, host="rebound.example")
        self.assertEqual(refused.status_code, 403)
        self.assertIn("content-security-policy", refused.headers)

    def test_secret_link(self):
        """Through the tunnel only the key opens a session: a cookie that
        is HttpOnly, Secure and Lax, and a redirect without the key; then
        the cookie lets the browser in, and nothing else does."""
        server, key = self.shared()
        self.assertEqual(self.call(server, host=PUBLIC).status_code, 404)
        self.assertEqual(self.call(server, "/?k=" + "x" * 43,
                                   host=PUBLIC).status_code, 404)
        self.assertEqual(self.call(server, "/static/app.js?k=" + key,
                                   host=PUBLIC).status_code, 404)
        self.assertEqual(self.call(server, "/?k=" + key, host=PUBLIC,
                                   method="POST").status_code, 404)
        opened = self.call(server, "/?k=" + key, host=PUBLIC)
        self.assertEqual((opened.status_code, opened.headers["location"]),
                         (303, "/"))
        cookie = opened.headers["set-cookie"]
        for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
            self.assertIn(flag, cookie)
        session = cookie.split(";")[0]
        reply = self.call(server, host=PUBLIC, headers={"Cookie": session})
        self.assertEqual((reply.status_code, reply.text), (200, "page"))
        self.assertEqual(self.call(server, host=PUBLIC, headers={
            "Cookie": gate.COOKIE + "=other"}).status_code, 404)
        self.assertEqual(self.call(server, headers={
            "cf-ray": "invented"}).status_code, 404)
        self.assertEqual(self.call(server).status_code, 200)
        for line in self.lines:
            self.assertNotIn(key, line)
            self.assertNotIn(session.split("=", 1)[1], line)

    def test_sessions_end(self):
        """A session ends after a day."""
        now = [1000.0]
        server, key = self.shared(clock=lambda: now[0])
        session = self.open_session(server, key)
        now[0] += gate.SESSION_SECONDS + 1
        self.assertEqual(self.call(server, host=PUBLIC, headers={
            "Cookie": session}).status_code, 404)

    def test_the_application_knows_the_session(self):
        """The application is told a request's session when it came
        through the tunnel, and none on this machine."""
        server, key = self.shared()
        session = self.open_session(server, key)
        self.assertEqual(self.call(server, "/echo", host=PUBLIC, headers={
            "Cookie": session}).json(), {"session": True})
        self.assertEqual(self.call(server, "/echo").json(),
                         {"session": False})

    def test_changes_come_from_the_page(self):
        """A request that changes something is let in only from the page's
        own origin, never from another site or without an origin."""
        server, key = self.shared()
        local = "http://127.0.0.1:%d" % server.port
        for headers, status in (({"Origin": local}, 200),
                                ({}, 403),
                                ({"Origin": "http://rebound.example"}, 403),
                                ({"Origin": "null"}, 403),
                                ({"Origin": local,
                                  "Sec-Fetch-Site": "cross-site"}, 403)):
            self.assertEqual(self.call(server, "/echo", method="POST",
                                       headers=headers).status_code,
                             status, headers)
        session = self.open_session(server, key)
        for origin, status in (("https://" + PUBLIC, 200),
                               ("http://" + PUBLIC, 403), (local, 403)):
            self.assertEqual(self.call(server, "/echo", host=PUBLIC,
                                       method="POST", headers={
                                           "Cookie": session,
                                           "Origin": origin}).status_code,
                             status, origin)
        self.assertIn("interface refused a cross-site request", self.lines)

    def test_websockets_are_refused(self):
        """No websocket opens, from anywhere."""
        sent = []

        async def receive():
            """Nothing arrives."""
            return {"type": "websocket.connect"}

        async def send(message):
            """Keep what the gate sends."""
            sent.append(message)

        checks = gate.Gate(invented_app(), gate.local_hosts(8501))
        asyncio.run(checks({"type": "websocket", "path": "/",
                            "headers": [(b"host", b"127.0.0.1:8501")]},
                           receive, send))
        self.assertEqual(sent, [{"type": "websocket.close", "code": 1008}])


if __name__ == "__main__":
    unittest.main()
