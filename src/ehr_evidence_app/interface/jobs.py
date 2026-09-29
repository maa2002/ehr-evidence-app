"""Requests the page does not wait on (Phase 8, decision D1).

The model may take minutes over a response: longer than a page should
wait on one request, and longer than the tunnel holds one open. A
question or an explanation therefore runs in the background: the page is
given the job's identifier at once and asks after it until it is done.

A job belongs to the session that started it, the gate's or this
machine's, and only that session reads it; its identifier is 128 random
bits. A session runs a few jobs at most at once, and the interface a few
more; a finished job is kept for a quarter of an hour, then forgotten. A
job's result is what the page shows, or a refusal's code; a failure of
any other kind is logged by its type only. The jobs' threads do not keep
the process from stopping.
"""

from __future__ import annotations

import dataclasses
import secrets
import threading
import time

from ehr_evidence_app.interface import client as clients
from ehr_evidence_app.interface import views

PER_OWNER = 4
MAX_RUNNING = 16
KEEP_SECONDS = 15 * 60
MAX_KEPT = 256


class JobError(Exception):
    """A job that cannot be started; carries a short code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclasses.dataclass
class Job:
    """One request in the background: whose it is, when it started and
    ended, and its result or its refusal's code."""

    owner: str
    started: float
    ended: float | None = None
    result: object = None
    error: str | None = None


class Jobs:
    """The requests running in the background, and those recently done."""

    def __init__(self, per_owner=PER_OWNER, running=MAX_RUNNING,
                 keep_seconds=KEEP_SECONDS, kept=MAX_KEPT,
                 clock=time.monotonic, log=None):
        """Hold the limits, the clock and the log (a function of one
        line)."""
        self.per_owner = per_owner
        self.running = running
        self.keep_seconds = keep_seconds
        self.kept = kept
        self.clock = clock
        self.log = log or (lambda line: None)
        self.lock = threading.Lock()
        self.jobs = {}
        self.closed = False

    def forget(self, now):
        """Forget the finished jobs kept long enough, and the oldest
        finished ones beyond the number kept; the lock is held."""
        self.jobs = {key: job for key, job in self.jobs.items()
                     if job.ended is None
                     or now - job.ended < self.keep_seconds}
        finished = sorted((job.ended, key) for key, job in self.jobs.items()
                          if job.ended is not None)
        for _, key in finished[:max(0, len(self.jobs) - self.kept)]:
            del self.jobs[key]

    def submit(self, owner, work):
        """Start work (a function of no arguments returning the result)
        for an owner; return the job's identifier."""
        with self.lock:
            if self.closed:
                raise JobError("internal")
            now = self.clock()
            self.forget(now)
            running = [job for job in self.jobs.values() if job.ended is None]
            if (len(running) >= self.running
                    or sum(job.owner == owner for job in running)
                    >= self.per_owner):
                raise JobError("too_many_requests")
            key = secrets.token_urlsafe(16)
            job = self.jobs[key] = Job(owner, now)
        threading.Thread(target=self.run, args=(job, work),
                         name="interface-job", daemon=True).start()
        return key

    def run(self, job, work):
        """Do a job's work and keep its result, or its refusal's code."""
        result, error = None, None
        try:
            result = work()
        except clients.ClientError as refused:
            error = refused.code
        except Exception as failed:  # noqa: BLE001 - logged by type only
            self.log("interface job failed: %s" % type(failed).__name__)
            error = "internal"
        with self.lock:
            job.result, job.error, job.ended = result, error, self.clock()

    def read(self, owner, key):
        """Return a job's state as the page reads it, with its result or
        its refusal once done; None when the owner has no such job."""
        with self.lock:
            job = self.jobs.get(key)
            if job is None or job.owner != owner:
                return None
            if job.ended is None:
                return {"state": "running",
                        "seconds": round(self.clock() - job.started, 1)}
            found = {"state": "failed" if job.error else "done",
                     "seconds": round(job.ended - job.started, 1)}
            if job.error:
                found["error"] = views.error_view(job.error)
            else:
                found["result"] = job.result
            return found

    def close(self):
        """Take no more jobs; those running finish, unread."""
        with self.lock:
            self.closed = True
