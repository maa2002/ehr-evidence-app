"""Tests of the interface's client of the API (interface.client), against
the API on a loopback port serving the invented case set."""

import socket
import unittest

from ehr_evidence_app.api import app as api
from ehr_evidence_app.api import boundary
from ehr_evidence_app.interface import client as clients


class ClientTests(unittest.TestCase):
    """Every operation over loopback, and the refusals."""

    @classmethod
    def setUpClass(cls):
        """Serve the invented case set."""
        cls.token = boundary.new_token()
        cls.server = api.LoopbackServer(lambda port: api.create_app(
            api.invented_service(), cls.token,
            boundary.loopback_hosts(port)))
        cls.server.__enter__()
        cls.client = clients.Client(cls.server.url, cls.token)
        cls.label = cls.client.case_sets()[0]["case_set"]

    @classmethod
    def tearDownClass(cls):
        """Stop serving."""
        cls.server.__exit__(None, None, None)

    def test_operations(self):
        """Every operation returns the API's data."""
        self.assertEqual(self.client.health()["model"], "ready")
        self.assertEqual(self.client.cases(self.label)[0]["case_number"], 1)
        case = self.client.case(self.label, 1)
        scope = case["scopes"][1]
        self.assertEqual(self.client.evidence(
            self.label, 1, scope["kind"], scope.get("visit_number"))[
                "scope"], scope)
        answered = self.client.ask(self.label, 1, scope,
                                   "Which conditions are recorded?")
        self.assertEqual(answered["response"]["scope"]["kind"],
                         scope["kind"])
        self.assertEqual(self.client.explain(self.label, 1)["response"][
            "outcome"], "explanation")

    def test_refusals_carry_the_api_code(self):
        """A refusal raises ClientError with the API's code; a wrong token
        is refused."""
        with self.assertRaises(clients.ClientError) as raised:
            self.client.cases("eval-100-v1")
        self.assertEqual((raised.exception.code, raised.exception.status),
                         ("case_set_not_served", 404))
        with self.assertRaises(clients.ClientError) as raised:
            clients.Client(self.server.url, "z" * 43).case_sets()
        self.assertEqual(raised.exception.code, "token_required")

    def test_local_only(self):
        """The client reaches 127.0.0.1 only, ignores proxy settings, and
        says when the API does not answer."""
        for url in ("http://example.org:80", "https://127.0.0.1:1",
                    "http://127.0.0.1:x", "http://localhost:8601"):
            with self.assertRaises(clients.ClientError):
                clients.Client(url, self.token)
        self.assertFalse(self.client.session.trust_env)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            silent = probe.getsockname()[1]
        with self.assertRaises(clients.ClientError) as raised:
            clients.Client("http://127.0.0.1:%d" % silent,
                           self.token).health()
        self.assertEqual(raised.exception.code, "api_unreachable")
        with self.assertRaises(clients.ClientError) as raised:
            clients.Client.from_environment({})
        self.assertEqual(raised.exception.code, "api_not_configured")


if __name__ == "__main__":
    unittest.main()
