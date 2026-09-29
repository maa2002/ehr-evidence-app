"""Tests of the requests the page does not wait on (interface.jobs)."""

import threading
import time
import unittest

from ehr_evidence_app.interface import client as clients
from ehr_evidence_app.interface import jobs as background


def wait_for(jobs, owner, key, state, seconds=10):
    """Return a job's state once it is the one expected."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = jobs.read(owner, key)
        if found and found["state"] == state:
            return found
        time.sleep(0.01)
    raise AssertionError("the job did not become %s" % state)


class JobTests(unittest.TestCase):
    """A job's life, whose it is, and the limits."""

    def test_running_then_done(self):
        """A job runs while its work does and then holds its result, read
        by its owner only."""
        release = threading.Event()
        jobs = background.Jobs()
        key = jobs.submit("a", lambda: release.wait(10) and {"done": 1})
        self.assertEqual(jobs.read("a", key)["state"], "running")
        self.assertIsNone(jobs.read("b", key))
        self.assertIsNone(jobs.read("a", "unknown"))
        release.set()
        found = wait_for(jobs, "a", key, "done")
        self.assertEqual(found["result"], {"done": 1})
        self.assertGreaterEqual(found["seconds"], 0)
        self.assertGreaterEqual(len(key), 22)

    def test_refusal_and_failure(self):
        """A refusal keeps its code and meaning; any other failure is
        internal, logged by its type only."""
        lines = []
        jobs = background.Jobs(log=lines.append)

        def refused():
            """Refuse, as the API does."""
            raise clients.ClientError("case_unknown", 404)

        def failed():
            """Fail with words that must not be logged."""
            raise RuntimeError("a patient's words")

        found = wait_for(jobs, "a", jobs.submit("a", refused), "failed")
        self.assertEqual(found["error"]["error"], "case_unknown")
        self.assertIn("admitted cases", found["error"]["message"])
        found = wait_for(jobs, "a", jobs.submit("a", failed), "failed")
        self.assertEqual(found["error"]["error"], "internal")
        self.assertEqual(lines, ["interface job failed: RuntimeError"])

    def test_limits(self):
        """An owner runs a few jobs at once, and the interface a few
        more; a closed set of jobs takes none."""
        release = threading.Event()
        self.addCleanup(release.set)
        jobs = background.Jobs(per_owner=2, running=3)
        for owner in ("a", "a", "b"):
            jobs.submit(owner, lambda: release.wait(10))
        with self.assertRaises(background.JobError) as raised:
            jobs.submit("a", dict)
        self.assertEqual(raised.exception.code, "too_many_requests")
        with self.assertRaises(background.JobError):
            jobs.submit("c", dict)
        jobs.close()
        release.set()
        with self.assertRaises(background.JobError):
            jobs.submit("c", dict)

    def test_finished_jobs_are_forgotten(self):
        """A finished job is kept for a while, and only so many are
        kept."""
        now = [100.0]
        jobs = background.Jobs(keep_seconds=60, kept=2,
                               clock=lambda: now[0])
        first = jobs.submit("a", dict)
        wait_for(jobs, "a", first, "done")
        now[0] += 61
        second = jobs.submit("a", dict)
        self.assertIsNone(jobs.read("a", first))
        wait_for(jobs, "a", second, "done")
        keys = []
        for _ in range(2):
            now[0] += 1
            keys.append(jobs.submit("a", dict))
            wait_for(jobs, "a", keys[-1], "done")
        now[0] += 1
        jobs.submit("a", dict)
        self.assertIsNone(jobs.read("a", second))
        for key in keys:
            self.assertEqual(jobs.read("a", key)["state"], "done")


if __name__ == "__main__":
    unittest.main()
