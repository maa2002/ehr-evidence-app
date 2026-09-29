"""Serve the interface on 127.0.0.1 (Phase 8, work items 48 to 52).

The interface's web application (interface.app) is served with its
access checks (interface.gate) in front of it, on 127.0.0.1 only. The
server keeps no access log, sends no server or date header and takes no
websocket; a failure of an operation is logged by its type only.

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
from ehr_evidence_app.interface import app as application
from ehr_evidence_app.interface import client as clients
from ehr_evidence_app.interface import gate

SHARE_HOST_VARIABLE = "EHR_SHARE_HOST"
SHARE_DIGEST_VARIABLE = "EHR_SHARE_KEY_SHA256"
LOG_DIR = os.path.join(records.LOGS_ROOT, "interface")
HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+"
                  r"[a-z]{2,63}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
# How uvicorn serves the interface: no access log, no server or date
# header, no websocket.
SERVER = {"host": "127.0.0.1", "log_config": None, "access_log": False,
          "log_level": "warning", "server_header": False,
          "date_header": False, "ws": "none", "lifespan": "on"}


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


def build(port, client, share=None, log=None):
    """Return the interface's application over an API client, behind its
    gate."""
    return gate.Gate(application.create_app(client, log=log),
                     gate.local_hosts(port), share, log)


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
        client = clients.Client.from_environment(environ)
        share = share_of(environ)
    except (clients.ClientError, ServeError) as error:
        print("FAILED: %s" % error)
        return 2
    import uvicorn

    from ehr_evidence_app.api import launch
    served = build(args.port, client, share, None if args.no_log
                   else boundary.file_log(LOG_DIR, "interface"))
    boundary.quiet_access_log()
    launch.exit_with_launcher(environ=environ)
    uvicorn.run(served, port=args.port, **SERVER)
    return 0


if __name__ == "__main__":
    sys.exit(main())
