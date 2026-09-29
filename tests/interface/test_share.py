"""Tests of the secret link and the tunnel's parts (interface.share), in a
temporary directory; no real link, token or connector is used."""

import hashlib
import json
import os
import tempfile
import unittest

from ehr_evidence_app.interface import share as sharing

HOST = "ehr.example.org"


class ShareTests(unittest.TestCase):
    """The secret link's record and the checks of the sharing's parts."""

    def setUp(self):
        """Use a temporary sharing directory."""
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.directory = scratch.name

    def write(self, name, size):
        """Write an invented file of a size."""
        path = os.path.join(self.directory, name)
        with open(path, "wb") as handle:
            handle.write(b"x" * size)
        return path

    def test_new_link_keeps_the_digest_only(self):
        """A new link is shown once; the record holds the host and the
        key's SHA-256, never the key."""
        link = sharing.new_link(HOST, self.directory)
        prefix = "https://%s/?k=" % HOST
        self.assertTrue(link.startswith(prefix))
        key = link[len(prefix):]
        self.assertGreaterEqual(len(key), 43)
        with open(sharing.link_path(self.directory), "r",
                  encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn(key, text)
        record = json.loads(text)
        self.assertEqual(record["key_sha256"],
                         hashlib.sha256(key.encode()).hexdigest())
        found = sharing.read_link(self.directory)
        self.assertEqual((found.host, found.key_sha256),
                         (HOST, record["key_sha256"]))

    def test_new_link_retires_the_old_one(self):
        """A second link replaces the first's digest."""
        first = sharing.new_link(HOST, self.directory)
        second = sharing.new_link(HOST, self.directory)
        self.assertNotEqual(first, second)
        digest = sharing.read_link(self.directory).key_sha256
        self.assertEqual(digest, hashlib.sha256(
            second.split("=", 1)[1].encode()).hexdigest())

    def test_hosts_and_records_are_checked(self):
        """A host that is not a public name is refused, and a missing or
        tampered record says so."""
        for host in ("localhost", "127.0.0.1", "https://ehr.example.org",
                     "EHR.example.org", "ehr.example.org/path"):
            with self.assertRaises(sharing.ShareError):
                sharing.new_link(host, self.directory)
        with self.assertRaises(sharing.ShareError) as raised:
            sharing.read_link(self.directory)
        self.assertEqual(str(raised.exception), "share_link_missing")
        sharing.new_link(HOST, self.directory)
        path = sharing.link_path(self.directory)
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(dict(record, key_sha256="not a digest"), handle)
        with self.assertRaises(sharing.ShareError) as raised:
            sharing.read_link(self.directory)
        self.assertEqual(str(raised.exception), "share_link_invalid")

    def test_parts_are_checked_without_reading_the_token(self):
        """The link's record, the token file and cloudflared are each
        checked; the token is examined by its size only."""
        cloudflared = self.write("cloudflared.exe", 4096)
        self.assertEqual(sharing.problems(self.directory, cloudflared), [
            "share_link_missing", "tunnel_token_missing"])
        sharing.new_link(HOST, self.directory)
        self.write(sharing.TOKEN_NAME, 10)
        self.assertEqual(sharing.problems(self.directory, cloudflared),
                         ["tunnel_token_implausible"])
        self.write(sharing.TOKEN_NAME, 300)
        self.assertEqual(sharing.problems(self.directory, cloudflared), [])
        self.assertEqual(sharing.problems(self.directory, os.path.join(
            self.directory, "absent.exe")), ["cloudflared_missing"])

    def test_tunnel_command_names_the_token_file(self):
        """cloudflared reads the token from its file; the command holds
        only the file's path."""
        command = sharing.tunnel_command("cloudflared.exe", self.directory)
        self.assertEqual(command[:2], ["cloudflared.exe", "tunnel"])
        self.assertEqual(command[-2:], ["--token-file",
                                        sharing.token_path(self.directory)])
        self.assertIn("--no-autoupdate", command)

    def test_command_needs_one_mode(self):
        """--check or --new-link, not both, not neither."""
        with self.assertRaises(SystemExit):
            sharing.main(["--new-link"])
        self.assertEqual(sharing.main([]), 2)
        self.assertEqual(sharing.main(["--check", "--new-link", HOST]), 2)


if __name__ == "__main__":
    unittest.main()
