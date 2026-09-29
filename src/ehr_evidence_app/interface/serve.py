"""Serve the interface on 127.0.0.1 (Phase 8, work items 48 to 52).

Streamlit serves the page (interface.app) as an ASGI application with
the interface's access checks (interface.gate) in front of it. Its usage
statistics are off, no error detail reaches the browser, no file is
watched, and a failure of the page is logged by its type only.

The API's address and the launch token come from the process environment
(EHR_API_URL and EHR_API_TOKEN), as the launcher (api.launch) sets them;
so does sharing (EHR_SHARE_HOST, the tunnel's public host, and
EHR_SHARE_KEY_SHA256, the SHA-256 of the secret link's key). The gate
logs its decisions to the workspace's logs.

Usage:
    python -m ehr_evidence_app.interface.serve --port 8501
    python -m ehr_evidence_app.interface.serve --port 8501 --no-log
"""

from __future__ import annotations

import argparse
import os
import re
import sys

from ehr_evidence_app.api import boundary
from ehr_evidence_app.generation import records
from ehr_evidence_app.interface import client as clients
from ehr_evidence_app.interface import gate

PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
SHARE_HOST_VARIABLE = "EHR_SHARE_HOST"
SHARE_DIGEST_VARIABLE = "EHR_SHARE_KEY_SHA256"
LOG_DIR = os.path.join(records.LOGS_ROOT, "interface")
HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+"
                  r"[a-z]{2,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
CONFIG = {"server.address": "127.0.0.1", "server.headless": True,
          "browser.gatherUsageStats": False,
          "client.showErrorDetails": "none", "client.toolbarMode": "minimal",
          "server.fileWatcherType": "none", "server.runOnSave": False,
          "server.enableCORS": True, "server.enableXsrfProtection": True,
          "logger.level": "error", "theme.primaryColor": "#15847c"}


class ServeError(Exception):
    """An interface that cannot be served; carries a short code."""


def share_of(environ):
    """Return how the interface is shared, from the process environment,
    or None when it is not."""
    host = environ.get(SHARE_HOST_VARIABLE, "")
    digest = environ.get(SHARE_DIGEST_VARIABLE, "")
    if not host and not digest:
        return None
    if not HOST.fullmatch(host) or not SHA256.fullmatch(digest):
        raise ServeError("share_invalid")
    return gate.Share(host, digest)


def build(port, share=None, log=None):
    """Return the Streamlit application of the page behind its gate."""
    import streamlit as st
    from starlette.middleware import Middleware
    log = log or (lambda line: None)

    def failed(error):
        """Log a failure of the page by its type only."""
        log("interface page failed: %s" % type(error).__name__)
        return None

    return st.App(PAGE, middleware=[Middleware(
        gate.Gate, hosts=gate.local_hosts(port), share=share, log=log)],
        on_script_error=failed)


def main(argv=None, environ=None):
    """Serve the interface; blocks until stopped."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--no-log", action="store_true",
                        help="keep no log (for the invented case set)")
    args = parser.parse_args(argv)
    environ = os.environ if environ is None else environ
    if not 1024 <= args.port <= 65535:
        print("FAILED: --port is a number from 1024 to 65535")
        return 2
    try:
        clients.Client.from_environment(environ)
        share = share_of(environ)
    except (clients.ClientError, ServeError) as error:
        print("FAILED: %s" % error)
        return 2
    from ehr_evidence_app.api import launch
    app = build(args.port, share, None if args.no_log
                else boundary.file_log(LOG_DIR, "interface"))
    gate.quiet_access_log()
    launch.exit_with_launcher(environ=environ)
    app.run(config=dict(CONFIG, **{"server.port": args.port}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
