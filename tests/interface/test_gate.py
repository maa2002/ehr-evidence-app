"""Synthetic tests of the interface's access checks (interface.gate).

A small invented application stands behind the gate on a loopback port;
requests are made with the locked requests and websockets libraries. The
secret link's key is made here and never written out.
"""

import hashlib
import secrets
import socket
import unittest

import requests
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from websockets.exceptions import InvalidStatus
from websockets.sync.client import connect

from ehr_evidence_app.api import app as api
from ehr_evidence_app.interface import gate

PUBLIC = "ehr.example.org"


async def page(request):
    """The invented page."""
    return PlainTextResponse("page")


async def script(request):
    """An invented static file."""
    return PlainTextResponse("static")


async def stream(websocket):
    """An invented websocket: accept, say hello, close."""
    await websocket.accept()
    await websocket.send_text("hello")
    await websocket.close()


def invented_app():
    """Return the invented application."""
    return Starlette(routes=[Route("/", page),
                             Route("/static/app.js", script),
                             WebSocketRoute("/_stcore/stream", stream)])


class GateTests(unittest.TestCase):
    """Local access, and sharing through the secret link."""

    def serve(self, share=None, clock=None):
        """Serve the invented application behind a gate; return it."""
        self.lines = []
        server = api.LoopbackServer(lambda port: gate.Gate(
            invented_app(), gate.local_hosts(port), share, self.lines.append,
            **({"clock": clock} if clock else {})))
        server.__enter__()
        self.addCleanup(server.__exit__, None, None, None)
        return server

    def get(self, server, path="/", host=None, headers=None):
        """Make a request naming a host; do not follow redirects."""
        found = dict(headers or {})
        if host:
            found["Host"] = host
        session = requests.Session()
        session.trust_env = False
        return session.get(server.url + path, headers=found, timeout=30,
                           allow_redirects=False)

    def test_local_access(self):
        """This machine's host is let in, marked not to be cached (static
        files aside); any other host is refused."""
        server = self.serve()
        reply = self.get(server)
        self.assertEqual((reply.status_code, reply.text), (200, "page"))
        self.assertEqual(reply.headers["cache-control"], "no-store")
        self.assertEqual(reply.headers["referrer-policy"], "no-referrer")
        self.assertEqual(reply.headers["x-frame-options"], "DENY")
        self.assertNotIn("cache-control", self.get(
            server, "/static/app.js").headers)
        self.assertEqual(self.get(server, host="rebound.example").status_code,
                         403)

    def test_secret_link(self):
        """Through the tunnel only the key opens a session: a cookie that
        is HttpOnly, Secure and Lax, and a redirect without the key; then
        the cookie lets the browser in, and nothing else does."""
        key = secrets.token_urlsafe(32)
        server = self.serve(gate.Share(PUBLIC, hashlib.sha256(
            key.encode()).hexdigest()))
        self.assertEqual(self.get(server, host=PUBLIC).status_code, 404)
        self.assertEqual(self.get(server, "/?k=" + "x" * 43,
                                  host=PUBLIC).status_code, 404)
        self.assertEqual(self.get(server, "/static/app.js?k=" + key,
                                  host=PUBLIC).status_code, 404)
        opened = self.get(server, "/?k=" + key, host=PUBLIC)
        self.assertEqual((opened.status_code, opened.headers["location"]),
                         (303, "/"))
        cookie = opened.headers["set-cookie"]
        for flag in ("HttpOnly", "Secure", "SameSite=Lax", "Path=/"):
            self.assertIn(flag, cookie)
        session = cookie.split(";")[0]
        reply = self.get(server, host=PUBLIC, headers={"Cookie": session})
        self.assertEqual((reply.status_code, reply.text), (200, "page"))
        self.assertEqual(self.get(server, host=PUBLIC, headers={
            "Cookie": gate.COOKIE + "=other"}).status_code, 404)
        self.assertEqual(self.get(server, headers={
            "cf-ray": "invented"}).status_code, 404)
        self.assertEqual(self.get(server).status_code, 200)
        for line in self.lines:
            self.assertNotIn(key, line)
            self.assertNotIn(session.split("=", 1)[1], line)

    def test_sessions_end(self):
        """A session ends after a day."""
        now = [1000.0]
        key = secrets.token_urlsafe(32)
        server = self.serve(gate.Share(PUBLIC, hashlib.sha256(
            key.encode()).hexdigest()), clock=lambda: now[0])
        session = self.get(server, "/?k=" + key, host=PUBLIC).headers[
            "set-cookie"].split(";")[0]
        now[0] += gate.SESSION_SECONDS + 1
        self.assertEqual(self.get(server, host=PUBLIC, headers={
            "Cookie": session}).status_code, 404)

    def test_websockets_need_a_session(self):
        """A websocket through the tunnel opens only with a session; one
        naming another host, or two hosts, never opens."""
        key = secrets.token_urlsafe(32)
        server = self.serve(gate.Share(PUBLIC, hashlib.sha256(
            key.encode()).hexdigest()))

        def open_socket(host, **options):
            """Connect to the gate's port, naming a host."""
            return connect("ws://%s/_stcore/stream" % host,
                           sock=socket.create_connection(
                               ("127.0.0.1", server.port)), **options)

        with open_socket("127.0.0.1:%d" % server.port) as channel:
            self.assertEqual(channel.recv(timeout=10), "hello")
        for host, headers in ((PUBLIC, {}), ("rebound.example", {}),
                              ("127.0.0.1:%d" % server.port,
                               {"Host": PUBLIC})):
            with self.assertRaises(InvalidStatus):
                with open_socket(host, additional_headers=headers):
                    pass
        session = self.get(server, "/?k=" + key, host=PUBLIC).headers[
            "set-cookie"].split(";")[0]
        with open_socket(PUBLIC, additional_headers={
                "Cookie": session}) as channel:
            self.assertEqual(channel.recv(timeout=10), "hello")


if __name__ == "__main__":
    unittest.main()
