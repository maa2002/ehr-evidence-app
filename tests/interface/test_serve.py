"""Tests of how the interface is served (interface.serve)."""

import contextlib
import io
import unittest

from ehr_evidence_app.api import boundary
from ehr_evidence_app.interface import client as clients
from ehr_evidence_app.interface import gate, serve


class ServeTests(unittest.TestCase):
    """The configuration, sharing and the refusals before serving."""

    def test_configuration(self):
        """Loopback only, no usage statistics, no error detail and no
        file watching."""
        self.assertEqual(serve.CONFIG["server.address"], "127.0.0.1")
        self.assertFalse(serve.CONFIG["browser.gatherUsageStats"])
        self.assertEqual(serve.CONFIG["client.showErrorDetails"], "none")
        self.assertEqual(serve.CONFIG["server.fileWatcherType"], "none")
        self.assertTrue(serve.CONFIG["server.enableXsrfProtection"])

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
