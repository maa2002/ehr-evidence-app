"""Tests of how the interface is served (interface.serve)."""

import contextlib
import io
import unittest

from ehr_evidence_app.api import boundary
from ehr_evidence_app.interface import client as clients
from ehr_evidence_app.interface import gate, serve


class ServeTests(unittest.TestCase):
    """The server's settings, sharing and the refusals before serving."""

    def test_server_settings(self):
        """Loopback only, no access log, no server or date header and no
        websocket."""
        self.assertEqual(serve.SERVER["host"], "127.0.0.1")
        self.assertFalse(serve.SERVER["access_log"])
        self.assertFalse(serve.SERVER["server_header"])
        self.assertFalse(serve.SERVER["date_header"])
        self.assertEqual(serve.SERVER["ws"], "none")

    def test_build(self):
        """The application stands behind its gate, which answers to this
        machine's host at the port and to the tunnel's public host."""
        share = gate.Share("ehr.example.org", "a" * 64)
        built = serve.build(8501, clients.Client("http://127.0.0.1:8601",
                                                 "t" * 43), share)
        self.assertIsInstance(built, gate.Gate)
        self.assertEqual(built.hosts, {"127.0.0.1:8501", "localhost:8501",
                                       "ehr.example.org"})

    def test_share(self):
        """Sharing needs a host name and a digest, both well formed."""
        self.assertIsNone(serve.share_of({}))
        found = serve.share_of({serve.SHARE_HOST_VARIABLE: "ehr.example.org",
                                serve.SHARE_DIGEST_VARIABLE: "a" * 64})
        self.assertEqual(found, gate.Share("ehr.example.org", "a" * 64))
        for host, digest in (("ehr.example.org", ""), ("", "a" * 64),
                             ("http://ehr.example.org", "a" * 64),
                             ("ehr.example.org", "A" * 64),
                             ("127.0.0.1", "a" * 64)):
            with self.assertRaises(serve.ServeError):
                serve.share_of({serve.SHARE_HOST_VARIABLE: host,
                                serve.SHARE_DIGEST_VARIABLE: digest})

    def test_refused_before_serving(self):
        """Without the API's address and token, or with a bad port, the
        interface is not served."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(serve.main(["--port", "8501"], environ={}), 2)
            self.assertEqual(serve.main(["--port", "80"], environ={}), 2)
            self.assertEqual(serve.main(["--port", "8501"], environ={
                clients.URL_VARIABLE: "http://127.0.0.1:8601",
                boundary.TOKEN_VARIABLE: "t" * 43,
                serve.SHARE_HOST_VARIABLE: "ehr.example.org"}), 2)
        self.assertIn("api_not_configured", output.getvalue())
        self.assertIn("share_invalid", output.getvalue())


if __name__ == "__main__":
    unittest.main()
