"""Tests of the launcher (api.launch): its commands, its environment,
stopping a process, and a real start of both processes serving the
invented case set, which reads and writes nothing."""

import contextlib
import io
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import requests

from ehr_evidence_app.api import boundary, launch
from ehr_evidence_app.interface import client as clients
from ehr_evidence_app.interface import serve


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
        """The processes receive the token, the API's address and the
        instruction to stop with the launcher."""
        found = launch.environment("t" * 43, 8601, base={"A": "1"})
        self.assertEqual(found, {"A": "1", boundary.TOKEN_VARIABLE: "t" * 43,
                                 clients.URL_VARIABLE:
                                     "http://127.0.0.1:8601",
                                 launch.WATCH_VARIABLE: "1"})
        self.assertIsNone(launch.exit_with_launcher(environ={}))

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
        """Closing a process's standard input asks it to stop; one that
        does not stop in time is ended."""
        listening = launch.start([sys.executable, "-c",
                                  "import sys; sys.stdin.read()"], None)
        launch.stop(listening, seconds=30)
        self.assertEqual(listening.returncode, 0)
        stubborn = launch.start([sys.executable, "-c",
                                 "import time; time.sleep(120)"], None)
        launch.stop(stubborn, seconds=1)
        self.assertIsNotNone(stubborn.poll())

    def test_process_stops_with_the_launcher(self):
        """A process watching for the launcher stops when the launcher's
        end of its standard input closes."""
        env = launch.environment(boundary.new_token(), 8601)
        watcher = launch.start([sys.executable, "-c", (
            "import time\n"
            "from ehr_evidence_app.api import launch\n"
            "launch.exit_with_launcher()\n"
            "time.sleep(120)\n")], env)
        time.sleep(1)
        started = time.monotonic()
        launch.stop(watcher, seconds=30)
        self.assertLess(time.monotonic() - started, 30)
        self.assertIsNotNone(watcher.poll())

    def test_wait_until_notices_a_stopped_process(self):
        """Waiting ends when the process has stopped."""
        process = subprocess.Popen([sys.executable, "-c", "pass"])
        process.wait()
        with self.assertRaises(launch.LaunchError) as raised:
            launch.wait_until("http://127.0.0.1:%d/" % free_port(), process,
                              seconds=5)
        self.assertEqual(str(raised.exception), "process_stopped")


class ShareLaunchTests(unittest.TestCase):
    """Sharing is refused before anything starts when a part is missing;
    cloudflared is given nothing of the application's."""

    def test_missing_parts_refuse_sharing(self):
        """Without the link's record, the token file or cloudflared, the
        launcher starts nothing."""
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(launch.LaunchError) as raised:
                launch.launch(free_port(), free_port(), share=True,
                              cloudflared=os.path.join(directory, "none"),
                              directory=directory)
        self.assertEqual(str(raised.exception), "share_link_missing")

    def test_tunnel_environment(self):
        """cloudflared's environment holds no launch token, address or
        share setting."""
        base = launch.environment("t" * 43, 8601, base={"PATH": "p"})
        base.update({serve.SHARE_HOST_VARIABLE: "h",
                     serve.SHARE_DIGEST_VARIABLE: "d"})
        self.assertEqual(launch.tunnel_environment(base), {"PATH": "p"})


@unittest.skipUnless(os.name == "nt", "Windows jobs only")
class JobTests(unittest.TestCase):
    """The job ends its processes when it closes."""

    def test_closing_the_job_ends_its_processes(self):
        """A process in the job ends when the job closes."""
        job = launch.Job()
        self.assertIsNotNone(job.handle)
        process = subprocess.Popen([sys.executable, "-c",
                                    "import time; time.sleep(120)"])
        try:
            self.assertTrue(job.add(process))
            job.close()
            process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
        self.assertIsNotNone(process.returncode)


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
