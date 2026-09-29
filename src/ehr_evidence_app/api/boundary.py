"""The API's access boundary (Phase 8, work item 48; decision D2).

The API listens on 127.0.0.1 only, and a request must name the host and
port it listens on, so that a web page cannot reach it through a domain
name rebound to this machine. A request carrying an Origin header, as
every cross-origin request from a browser does, is refused, and no
cross-origin header is ever sent. Every operation but the health check
needs the launch token: a random value made at each launch and handed to
the interface in its process environment, compared in constant time and
never logged. A body is bounded, and every response is marked not to be
cached or sniffed and to send no referrer. An exception inside the API is
answered with a bare code and logged by its type name only, so that
nothing it holds reaches a console or a log.

The access log holds each request's method, route (its template, such as
/case-sets/{case_set}/cases/{case_number}; never the values in the path,
nor the query), status and milliseconds, and nothing else. The log audit
(generation.records) reads it.
"""

from __future__ import annotations

import datetime
import hmac
import json
import logging
import os
import secrets
import time

from starlette.routing import Match

from ehr_evidence_app.generation import records

TOKEN_VARIABLE = "EHR_API_TOKEN"
MIN_TOKEN_CHARS = 32
MAX_BODY_BYTES = 16 * 1024
OPEN_PATHS = frozenset(("/health",))
SECURITY_HEADERS = ((b"cache-control", b"no-store"),
                    (b"pragma", b"no-cache"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-frame-options", b"DENY"))
LOG_DIR = os.path.join(records.LOGS_ROOT, "api")


class BodyTooLarge(Exception):
    """A request body over its bound."""


def new_token():
    """Return a fresh launch token."""
    return secrets.token_urlsafe(32)


def loopback_hosts(port):
    """Return the Host values a request to this machine's port may
    name."""
    return ("127.0.0.1:%d" % port, "localhost:%d" % port)


def file_log(directory=LOG_DIR, name="api"):
    """Return a function that appends one line to the day's log file in
    directory, with its time."""
    os.makedirs(directory, exist_ok=True)
    logger = logging.getLogger("ehr_evidence.%s.access" % name)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    path = os.path.join(directory, "%s-%s.log" % (
        name, datetime.date.today().isoformat()))
    if not any(getattr(handler, "baseFilename", None) == os.path.abspath(path)
               for handler in logger.handlers):
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
    return logger.info


class Boundary:
    """ASGI middleware around the API application."""

    def __init__(self, app, token, hosts, log=None, routes=(),
                 max_body=MAX_BODY_BYTES):
        """Hold the application, the launch token, the Host values it
        answers to, the access log (a function of one line), the routes
        whose templates the log names and the bound of a body."""
        if not isinstance(token, str) or len(token) < MIN_TOKEN_CHARS:
            raise ValueError("the launch token is too short")
        self.app = app
        self.token = token.encode("utf-8")
        self.hosts = frozenset(hosts)
        self.log = log or (lambda line: None)
        self.routes = list(routes)
        self.max_body = max_body

    def route_of(self, scope):
        """Return the template of the route a request names, never its
        values."""
        for route in self.routes:
            match, _ = route.matches(scope)
            if match != Match.NONE:
                return route.path
        return "unmatched"

    def refusal(self, scope, headers):
        """Return (status, code) of a request the boundary refuses, or
        None."""
        host = headers.get(b"host", b"").decode("latin-1")
        if host not in self.hosts:
            return 403, "host_refused"
        if b"origin" in headers:
            return 403, "origin_refused"
        if scope["path"] not in OPEN_PATHS:
            given = headers.get(b"authorization", b"")
            if not hmac.compare_digest(given, b"Bearer " + self.token):
                return 401, "token_required"
        length = headers.get(b"content-length", b"0")
        if not length.isdigit() or int(length) > self.max_body:
            return 413, "body_too_large"
        return None

    async def bounded(self, receive):
        """Read the whole body, raising BodyTooLarge past the bound, and
        return a receive that gives it to the application in one
        message."""
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            if len(body) > self.max_body:
                raise BodyTooLarge()
            if not message.get("more_body", False):
                break
        given = [False]

        async def replay():
            """Give the body once, then wait for the client to leave."""
            if not given[0]:
                given[0] = True
                return {"type": "http.request", "body": bytes(body),
                        "more_body": False}
            return await receive()
        return replay

    @staticmethod
    async def respond(send, status, code):
        """Send a JSON response holding a bare code."""
        body = json.dumps({"error": code}).encode("utf-8")
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length",
                                 str(len(body)).encode("ascii"))]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        """Answer one connection through the boundary."""
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        started = time.monotonic()
        status = [0]

        async def marked(message):
            """Send a message, marking a response's start with the
            security headers."""
            if message["type"] == "http.response.start":
                status[0] = message["status"]
                message = dict(message, headers=[
                    (name, value) for name, value in message.get(
                        "headers", []) if name.lower() not in dict(
                            SECURITY_HEADERS)] + list(SECURITY_HEADERS))
            await send(message)

        headers = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.lower(), value)
        route = self.route_of(scope)
        try:
            refused = self.refusal(scope, headers)
            if refused:
                await self.respond(marked, *refused)
            else:
                await self.app(scope, await self.bounded(receive), marked)
        except BodyTooLarge:
            if not status[0]:
                await self.respond(marked, 413, "body_too_large")
        except Exception as error:  # noqa: BLE001 - logged by type only
            self.log("api error %s" % type(error).__name__)
            if not status[0]:
                await self.respond(marked, 500, "internal")
        finally:
            self.log("api %s %s %d %.0f ms" % (
                scope.get("method", "?"), route, status[0],
                (time.monotonic() - started) * 1000))
