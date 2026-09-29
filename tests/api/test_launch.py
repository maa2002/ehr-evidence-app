"""Tests of the launcher (api.launch): its commands, its environment,
stopping a process, and a real start of both processes serving the
invented case set, which reads and writes nothing."""

import contextlib
import io
import socket
import subprocess
import sys
import unittest

import requests

from ehr_evidence_app.api import boundary, launch
from ehr_evidence_app.interface import client as clients


def free_port():
    """Return a free port of 127.0.0.1."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class LaunchTests(unittest.TestCase):
    """The pieces of a launch."""

    def test_commands(self):
        """The invented case set by default, with no log; the development
        case sets only with --operator-real."""
        api, interface = launch.commands(8601, 8501, python="py")
        self.assertEqual(api, ["py", "-m", "ehr_evidence_app.api.app",
                               "--serve", "--port", "8601"])
        self.assertIn("--no-log", interface)
        api, interface = launch.commands(8601, 8501, real=True, model=False,
                                         python="py")
        self.assertEqual(api[-2:], ["--operator-real", "--no-model"])
        self.assertNotIn("--no-log", interface)

    def test_environment(self):
        """The processes receive the token and the API's address."""
        found = launch.environment("t" * 43, 8601, base={"A": "1"})
        self.assertEqual(found, {"A": "1", boundary.TOKEN_VARIABLE: "t" * 43,
                                 clients.URL_VARIABLE:
                                     "http://127.0.0.1:8601"})

    def test_ports(self):
        """A bound port is not free; the command refuses equal or low
        ports and --no-model without --operator-real."""
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            self.assertFalse(launch.port_free(taken.getsockname()[1]))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(launch.main(["--api-port", "8501", "--port",
                                          "8501"]), 2)
            self.assertEqual(launch.main(["--port", "80"]), 2)
            self.assertEqual(launch.main(["--no-model"]), 2)

    def test_stop_ends_a_process(self):
        """A process asked to stop ends."""
        process = launch.start([sys.executable, "-c",
                                "import time; time.sleep(120)"], None)
        launch.stop(process)
        self.assertIsNotNone(process.poll())

    def test_wait_until_notices_a_stopped_process(self):
        """Waiting ends when the process has stopped."""
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        process.wait()
        with self.assertRaises(launch.LaunchError) as raised:
            launch.wait_until("http://127.0.0.1:%d/" % free_port(), process,
                              seconds=5)
        self.assertEqual(str(raised.exception), "process_stopped")


class StartTests(unittest.TestCase):
    """Both processes, started and stopped as the launcher does."""

    def test_both_processes(self):
        """The API and the interface answer on 127.0.0.1, the interface's
        page is marked not to be cached, another host is refused, and both
        stop when asked."""
        api_port, port = free_port(), free_port()
        env = launch.environment(boundary.new_token(), api_port)
        api_command, interface_command = launch.commands(api_port, port)
        processes = []
        try:
            processes.append(launch.start(api_command, env))
            launch.wait_until("http://127.0.0.1:%d/health" % api_port,
                              processes[0], seconds=120)
            processes.append(launch.start(interface_command, env))
            launch.wait_until("http://127.0.0.1:%d/_stcore/health" % port,
                              processes[1], seconds=120)
            session = requests.Session()
            session.trust_env = False
            page = session.get("http://127.0.0.1:%d/" % port, timeout=30)
            self.assertEqual(page.status_code, 200)
            self.assertEqual(page.headers["cache-control"], "no-store")
            self.assertEqual(session.get(
                "http://127.0.0.1:%d/" % port, timeout=30,
                headers={"Host": "rebound.example"}).status_code, 403)
            self.assertEqual(session.get(
                "http://127.0.0.1:%d/case-sets" % api_port,
                timeout=30).status_code, 401)
        finally:
            for process in reversed(processes):
                launch.stop(process)
        self.assertTrue(all(process.poll() is not None
                            for process in processes))


if __name__ == "__main__":
    unittest.main()
