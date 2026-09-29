"""The interface's access checks (Phase 8, work item 48; decision D2).

ASGI middleware in front of Streamlit. A request must name a host the
interface answers to: 127.0.0.1 or localhost at its port, and, when the
interface is shared, the tunnel's public host. That stops a web page from
reaching it through a domain name rebound to this machine.

When the interface is shared, a request from the tunnel (it names the
public host, or carries Cloudflare's headers) is let in only with a
session: the first visit opens the secret link, whose key (the query
parameter k) is checked against its SHA-256 in constant time; the gate
then sets a session cookie (HttpOnly, Secure, SameSite=Lax; Lax, because
the link is opened from another site) and redirects to the page without
the key. Anything else is answered as not found, and a websocket without
a session is closed before it opens. Sessions live in this process's
memory and end after a day, or when it stops.

Every response is marked not to be sniffed, framed or sent a referrer
from, and every one but Streamlit's static files not to be cached. The
server's access log, which would hold every path and query, the secret
link's included, is switched off; the gate's own log names only what it
decided, never a path, a query or a key.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import secrets
import time
import urllib.parse

from ehr_evidence_app.api.boundary import quiet_access_log

COOKIE = "ehr_session"
KEY_PARAMETER = "k"
SESSION_SECONDS = 24 * 3600
MAX_SESSIONS = 64
CLOUDFLARE_HEADERS = (b"cf-ray", b"cf-connecting-ip", b"cf-visitor")
STATIC_PREFIX = "/static/"
HEADERS = ((b"x-content-type-options", b"nosniff"),
           (b"referrer-policy", b"no-referrer"),
           (b"x-frame-options", b"DENY"))
NO_STORE = ((b"cache-control", b"no-store"), (b"pragma", b"no-cache"))


def local_hosts(port):
    """Return the Host values a request on this machine may name."""
    return ("127.0.0.1:%d" % port, "localhost:%d" % port)


@dataclasses.dataclass(frozen=True)
class Share:
    """How the interface is shared: the tunnel's public host and the
    SHA-256 of the secret link's key."""

    host: str
    key_sha256: str


class Gate:
    """ASGI middleware around the Streamlit application."""

    def __init__(self, app, hosts, share=None, log=None, clock=time.time):
        """Hold the application, the local Host values, how the interface
        is shared (a Share, or None) and the log (a function of one
        line)."""
        self.app = app
        self.share = share
        self.hosts = frozenset(hosts) | (
            frozenset((share.host,)) if share else frozenset())
        self.log = log or (lambda line: None)
        self.clock = clock
        self.sessions = {}

    # -- sessions ------------------------------------------------------------

    def key_matches(self, key):
        """Return True when a key is the secret link's."""
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, self.share.key_sha256)

    def open_session(self):
        """Open a session and return its identifier."""
        now = self.clock()
        self.sessions = {session: ends for session, ends
                         in self.sessions.items() if ends > now}
        while len(self.sessions) >= MAX_SESSIONS:
            self.sessions.pop(min(self.sessions, key=self.sessions.get))
        session = secrets.token_urlsafe(32)
        self.sessions[session] = now + SESSION_SECONDS
        return session

    def has_session(self, headers):
        """Return True when a request carries a live session cookie. A
        session identifier is 256 random bits, found by lookup."""
        cookies = headers.get(b"cookie", b"").decode("latin-1")
        for part in cookies.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                ends = self.sessions.get(value)
                if ends is not None and ends > self.clock():
                    return True
        return False

    # -- the checks ----------------------------------------------------------

    def remote(self, headers):
        """Return True when a request came through the tunnel."""
        host = headers.get(b"host", b"").decode("latin-1")
        return bool(self.share) and (host == self.share.host or any(
            name in headers for name in CLOUDFLARE_HEADERS))

    @staticmethod
    def marked(send, cache):
        """Return send, marking a response's start with the headers."""
        extra = HEADERS + (() if cache else NO_STORE)
        names = {name for name, _ in extra}

        async def marking(message):
            """Send a message, with the headers on a response's start."""
            if message["type"] == "http.response.start":
                message = dict(message, headers=[
                    (name, value) for name, value in message.get(
                        "headers", []) if name.lower() not in names]
                    + list(extra))
            await send(message)
        return marking

    @staticmethod
    async def plain(send, status, text, headers=()):
        """Send a short plain-text response."""
        body = text.encode("utf-8")
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type",
                                 b"text/plain; charset=utf-8"),
                                (b"content-length",
                                 str(len(body)).encode("ascii"))]
                    + list(headers)})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        """Let a request through, or refuse it."""
        quiet_access_log()
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.lower(), value)
        host = headers.get(b"host", b"").decode("latin-1")
        if sum(name.lower() == b"host"
               for name, _ in scope.get("headers", [])) != 1:
            host = ""
        path = scope.get("path", "")
        if scope["type"] == "websocket":
            if host not in self.hosts or (self.remote(headers)
                                          and not self.has_session(headers)):
                self.log("interface refused a websocket")
                await send({"type": "websocket.close", "code": 1008})
                return
            await self.app(scope, receive, send)
            return
        send = self.marked(send, cache=path.startswith(STATIC_PREFIX))
        if host not in self.hosts:
            self.log("interface refused a host")
            await self.plain(send, 403, "Forbidden")
            return
        if self.remote(headers) and not self.has_session(headers):
            query = urllib.parse.parse_qs(
                scope.get("query_string", b"").decode("latin-1"))
            keys = query.get(KEY_PARAMETER, [])
            if path == "/" and len(keys) == 1 and self.key_matches(keys[0]):
                session = self.open_session()
                self.log("interface opened a session")
                await self.plain(send, 303, "See Other", headers=(
                    (b"location", b"/"),
                    (b"set-cookie", (
                        "%s=%s; Path=/; Max-Age=%d; HttpOnly; Secure; "
                        "SameSite=Lax" % (COOKIE, session, SESSION_SECONDS)
                    ).encode("ascii"))))
                return
            self.log("interface refused a request without a session")
            await self.plain(send, 404, "Not Found")
            return
        await self.app(scope, receive, send)
