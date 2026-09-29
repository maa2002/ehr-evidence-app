"""The secret link and the tunnel (Phase 8, work item 48; decision D2).

The interface is shared through a Cloudflare tunnel that the project
owner creates in Cloudflare's dashboard: its public hostname, on the
owner's domain, forwards to http://127.0.0.1:8501 and leaves the request's
Host header as it is. cloudflared, the tunnel's connector, runs on this
laptop with the tunnel's token, which the owner saves in the workspace
(sharing\\tunnel-token.txt): cloudflared reads it (--token-file); this
project only checks, with lstat, that it is there, and never reads,
prints or copies it.

--new-link HOST makes a new secret link: a long random key, of which only
the SHA-256 is kept, with the host and the date (sharing\\share-link.json
in the workspace). The link, https://HOST/?k=KEY, is printed once, in the
owner's console, to be put in an issue of the private repository for the
supervisor; nothing else prints or stores it. A new link retires the one
before it. --check checks the sharing's parts and prints the host and
findings only.

The launcher shares the interface with --share (api.launch): it starts
cloudflared with the token file and gives the interface the host and the
key's digest, and the gate (interface.gate) then admits the tunnel's
requests only with the key or its session cookie.

Usage:
    python -m ehr_evidence_app.interface.share --check
    python -m ehr_evidence_app.interface.share --new-link HOST
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import secrets
import stat
import sys

from ehr_evidence_app.interface import gate, serve
from ehr_evidence_app.storage import policy as guard

SHARING_DIR = guard.PRIVATE_ROOT + "\\sharing"
LINK_NAME = "share-link.json"
TOKEN_NAME = "tunnel-token.txt"
CLOUDFLARED = guard.RUNTIME_ROOT + "\\tools\\cloudflared.exe"
SCHEMA_VERSION = "1.0"
# A tunnel token is a few hundred characters; anything far outside that is
# not one.
TOKEN_BYTES = (40, 8192)


class ShareError(Exception):
    """Sharing that cannot be set up or used; carries a short code."""


def link_path(directory=SHARING_DIR):
    """Return where the secret link's record is kept."""
    return os.path.join(directory, LINK_NAME)


def token_path(directory=SHARING_DIR):
    """Return where the owner saves the tunnel's token."""
    return os.path.join(directory, TOKEN_NAME)


def new_link(host, directory=SHARING_DIR, now=None):
    """Make a new secret link for a host: keep the key's SHA-256, the
    host and the date, replacing the link before it, and return the link.
    The key itself is returned in the link only."""
    if not serve.HOST.fullmatch(host):
        raise ShareError("host_invalid")
    key = secrets.token_urlsafe(32)
    record = {"schema_version": SCHEMA_VERSION, "host": host,
              "key_sha256": hashlib.sha256(key.encode("utf-8")).hexdigest(),
              "created": (now or datetime.datetime.now(
                  datetime.timezone.utc)).isoformat(timespec="seconds")}
    os.makedirs(directory, exist_ok=True)
    path = link_path(directory)
    partial = path + ".partial"
    with open(partial, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, indent=1, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)
    return "https://%s/?%s=%s" % (host, gate.KEY_PARAMETER, key)


def read_link(directory=SHARING_DIR):
    """Return how the interface is shared: the host and the key's
    digest."""
    try:
        with open(link_path(directory), "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except FileNotFoundError:
        raise ShareError("share_link_missing") from None
    except (OSError, ValueError):
        raise ShareError("share_link_invalid") from None
    if not isinstance(record, dict) or record.get(
            "schema_version") != SCHEMA_VERSION:
        raise ShareError("share_link_invalid")
    host, digest = record.get("host"), record.get("key_sha256")
    if not isinstance(host, str) or not serve.HOST.fullmatch(host) or (
            not isinstance(digest, str) or not serve.SHA256.fullmatch(
                digest)):
        raise ShareError("share_link_invalid")
    return gate.Share(host, digest)


def file_problems(path, low, high, missing, lstat=os.lstat):
    """Return the findings of a file that must exist, be a regular file
    and not a link, and have a size between low and high; it is examined
    with lstat only, never opened."""
    try:
        info = lstat(path)
    except OSError:
        return [missing]
    if not stat.S_ISREG(info.st_mode) or getattr(
            info, "st_file_attributes", 0) & 0x400:
        return [missing.replace("missing", "not_regular")]
    if not low <= info.st_size <= high:
        return [missing.replace("missing", "implausible")]
    return []


def problems(directory=SHARING_DIR, cloudflared=CLOUDFLARED,
             lstat=os.lstat):
    """Return the findings of the sharing's parts: the secret link's
    record, the tunnel's token file and cloudflared."""
    found = []
    try:
        read_link(directory)
    except ShareError as error:
        found.append(str(error))
    found += file_problems(token_path(directory), *TOKEN_BYTES,
                           "tunnel_token_missing", lstat)
    found += file_problems(cloudflared, 1024, 1024 ** 3,
                           "cloudflared_missing", lstat)
    return found


def tunnel_command(cloudflared=CLOUDFLARED, directory=SHARING_DIR):
    """Return the command line that runs the tunnel's connector; the
    token is read by cloudflared from its file."""
    return [cloudflared, "tunnel", "--no-autoupdate", "--loglevel", "warn",
            "run", "--token-file", token_path(directory)]


def main(argv=None):
    """Check the sharing, or make a new secret link."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="check the sharing's parts")
    parser.add_argument("--new-link", metavar="HOST",
                        help="make a new secret link for the tunnel's "
                             "public host, retiring the one before it")
    parser.add_argument("--cloudflared", default=CLOUDFLARED,
                        help="the path of cloudflared.exe")
    args = parser.parse_args(argv)
    if bool(args.check) == bool(args.new_link):
        print("FAILED: give one of --check and --new-link")
        return 2
    if args.new_link:
        try:
            link = new_link(args.new_link)
        except ShareError as error:
            print("FAILED: %s" % error)
            return 1
        print("The secret link, shown this once; put it in an issue of the "
              "private repository for your supervisor:")
        print(link)
        print("Only its digest is kept. A new link retires this one.")
        return 0
    found = problems(cloudflared=args.cloudflared)
    try:
        print("host: %s" % read_link().host)
    except ShareError:
        pass
    for finding in found:
        print("FINDING: %s" % finding)
    print("RESULT: %s" % ("FAIL" if found else "PASS"))
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
