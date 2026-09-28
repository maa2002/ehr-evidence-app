"""The model runtime: the pinned model, its worker and the backend that
reaches it (work item 42).

config/generation.json pins the model (repository, revision and every
file's size and SHA-256) and the worker's settings. Before the worker
starts, every model file is checked against its pin; a missing or changed
file stops it (model_file_invalid). The worker (generation.worker) runs in
the model environment on G:, in its own process, with an environment built
here: Hugging Face offline and without telemetry, every cache and temporary
directory on G: (CUDA's included), and nothing else inherited.

WorkerBackend is the backend generation.adapter uses. It starts the worker
when first needed, sends one request at a time and waits for its reply
within the time limit: a worker that times out is stopped, and the next
request starts a new one. A reply that reports exhausted memory or a
failure, or a worker that dies, is a BackendError, and so a fallback.

The operator check verifies the model files, loads the model, reports how
it was placed and how much GPU memory it took, writes a candidate for each
invented request and reports tokens, seconds and peak memory. With
--case-set it also counts the prompt tokens of a development case set's
requests, as aggregate counts only.

Usage:
    python -m ehr_evidence_app.generation.runtime
    python -m ehr_evidence_app.generation.runtime --operator-check
    python -m ehr_evidence_app.generation.runtime --operator-check \\
        --case-set dev-30-v1
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import queue
import statistics
import subprocess
import sys
import threading
import time
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, ValidationError

from ehr_evidence_app.evidence.contracts import Frozen, Sha256
from ehr_evidence_app.generation import adapter
from ehr_evidence_app.storage import policy

REPO_ROOT = policy.REPO_ROOT
CONFIG_PATH = os.path.join(REPO_ROOT, "config", "generation.json")
MODELS_DIR = policy.RUNTIME_ROOT + "\\models"
LLM_ENV = policy.RUNTIME_ROOT + "\\environments\\uv\\ehr-evidence-llm"
CHUNK = 16 * 1024 * 1024
# Variables the worker inherits from Windows; everything else is set here.
INHERITED = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT",
             "OS", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
             "PROCESSOR_IDENTIFIER")
COUNT_SECONDS = 120
STOP_SECONDS = 20

RelativePath = Annotated[str, StringConstraints(
    pattern=r"^[A-Za-z0-9_.-]+(\\[A-Za-z0-9_.-]+)*$", max_length=200)]
FileName = Annotated[str, StringConstraints(
    pattern=r"^[A-Za-z0-9_.][A-Za-z0-9_.-]*$", max_length=100)]


class RuntimeProblem(Exception):
    """A model runtime that cannot start, carrying a finding code only."""


class ModelFile(Frozen):
    """One pinned model file."""

    path: FileName
    size_bytes: int = Field(ge=0)
    sha256: Sha256


class ModelPin(Frozen):
    """The pinned model: repository, revision and files."""

    name: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    repository: Annotated[str, StringConstraints(
        pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")]
    revision: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    directory: RelativePath
    files: tuple[ModelFile, ...] = Field(min_length=1, max_length=100)


class WorkerSettings(Frozen):
    """How the worker runs."""

    precision: Literal["bf16-mixed", "nf4"]
    decoder: Literal["xgrammar", "llguidance"]
    seed: int = Field(ge=0, le=2 ** 31 - 1)
    minimum_free_gpu_mib: int = Field(ge=0, le=1_000_000)
    start_seconds: int = Field(ge=1, le=3600)


class GenerationFile(Frozen):
    """The committed configuration of the model runtime."""

    schema_version: Literal["1.0"]
    purpose: Annotated[str, StringConstraints(min_length=1, max_length=600)]
    model: ModelPin
    worker: WorkerSettings


def load_settings(path=CONFIG_PATH):
    """Return the validated runtime configuration."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return GenerationFile.model_validate_json(handle.read())
    except OSError:
        raise RuntimeProblem("generation_config_unreadable") from None
    except ValidationError:
        raise RuntimeProblem("generation_config_invalid") from None


def model_directory(settings, models_dir=MODELS_DIR):
    """Return the directory of the pinned model files."""
    return os.path.join(models_dir, settings.model.directory)


def file_digest(path):
    """Return (size, SHA-256) of a file, read in chunks."""
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def model_problems(settings, directory):
    """Return the pinned files that are missing or changed in a model
    directory, by name; empty when every file matches its pin."""
    problems = []
    for pinned in settings.model.files:
        path = os.path.join(directory, pinned.path)
        if not os.path.isfile(path) or os.path.islink(path):
            problems.append("missing: " + pinned.path)
            continue
        if os.path.getsize(path) != pinned.size_bytes:
            problems.append("size differs: " + pinned.path)
            continue
        if file_digest(path) != (pinned.size_bytes, pinned.sha256):
            problems.append("digest differs: " + pinned.path)
    return problems


def worker_environment(runtime_root=policy.RUNTIME_ROOT):
    """Return the worker's process environment: Windows' own variables,
    offline Hugging Face without telemetry, and every cache, home and
    temporary directory on G:."""
    temp = runtime_root + "\\temp"
    home = temp + "\\worker-home"
    cache = runtime_root + "\\cache"
    found = {name: os.environ[name] for name in INHERITED
             if name in os.environ}
    system = found.get("SYSTEMROOT", "C:\\Windows")
    found.update({
        "PATH": ";".join((LLM_ENV + "\\Scripts", system + "\\System32",
                          system)),
        "PYTHONPATH": os.path.join(REPO_ROOT, "src"),
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
        "PYTHONPYCACHEPREFIX": cache + "\\pycache",
        "TEMP": temp, "TMP": temp, "TMPDIR": temp,
        "USERPROFILE": home, "HOME": home, "APPDATA": home + "\\roaming",
        "LOCALAPPDATA": home + "\\local",
        "HF_HOME": cache + "\\huggingface",
        "HF_HUB_CACHE": cache + "\\huggingface\\hub",
        "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1", "HF_DATASETS_OFFLINE": "1",
        "TORCH_HOME": cache + "\\torch", "CUDA_CACHE_PATH": cache + "\\cuda",
        "XDG_CACHE_HOME": cache + "\\xdg", "TRANSFORMERS_VERBOSITY": "error",
        "TOKENIZERS_PARALLELISM": "false",
    })
    return found


class WorkerBackend:
    """The model worker as a generation.adapter backend."""

    def __init__(self, settings=None, python=None, directory=None,
                 fake=None, verify=True, runtime_root=policy.RUNTIME_ROOT,
                 precision=None):
        """Hold the worker's settings; nothing starts until it is used.

        python is the model environment's interpreter; fake starts the
        worker's stand-in engine (for tests), which loads no model;
        runtime_root holds the worker's caches, home and temporary files;
        precision overrides the configured one (for measurement).
        """
        self.settings = settings or load_settings()
        self.precision = precision or self.settings.worker.precision
        self.python = python or (LLM_ENV + "\\Scripts\\python.exe")
        self.directory = directory or model_directory(self.settings)
        self.fake = fake
        self.verify = verify and fake is None
        self.runtime_root = runtime_root
        self.process = None
        self.replies = None
        self.readers = []
        self.info = None
        self.verified = False
        self.lock = threading.Lock()
        self.number = 0
        # The worker's last status lines, in memory only, for a load that
        # fails before any request is sent.
        self.status = collections.deque(maxlen=30)

    def command(self):
        """Return the worker's command line."""
        worker = self.settings.worker
        found = [self.python, "-m", "ehr_evidence_app.generation.worker",
                 "--decoder", worker.decoder, "--seed", str(worker.seed),
                 "--minimum-free-gpu-mib", str(worker.minimum_free_gpu_mib),
                 "--precision", self.precision]
        if self.fake:
            return found + ["--fake", self.fake]
        return found + ["--model-dir", self.directory,
                        "--repository", self.settings.model.repository,
                        "--revision", self.settings.model.revision]

    def start(self):
        """Start the worker and wait until it has loaded; raises
        BackendError when it cannot."""
        if self.verify and not self.verified:
            if model_problems(self.settings, self.directory):
                raise adapter.BackendError("backend_failed")
            self.verified = True
        environment = worker_environment(self.runtime_root)
        for name in ("TEMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            os.makedirs(environment[name], exist_ok=True)
        try:
            self.process = subprocess.Popen(
                self.command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, env=environment, cwd=REPO_ROOT,
                encoding="utf-8", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            self.process = None
            raise adapter.BackendError("backend_failed") from None
        self.replies = queue.Queue()
        self.readers = [
            threading.Thread(target=self.read, args=(self.process,
                                                     self.replies),
                             daemon=True),
            threading.Thread(target=self.drain, args=(self.process,
                                                      self.status),
                             daemon=True)]
        for reader in self.readers:
            reader.start()
        ready = self.receive(self.settings.worker.start_seconds)
        if ready.get("op") != "ready":
            self.stop()
            raise adapter.BackendError(ready.get("error", "backend_failed")
                                       if ready.get("error") in
                                       adapter.BACKEND_FAILURES
                                       else "backend_failed")
        self.info = ready.get("describe", {})

    @staticmethod
    def read(process, replies):
        """Queue the worker's reply lines until its output ends."""
        for line in process.stdout:
            try:
                replies.put(json.loads(line))
            except ValueError:
                replies.put({"error": "backend_failed"})
        replies.put(None)

    @staticmethod
    def drain(process, status):
        """Read the worker's status lines so that it never blocks on them,
        keeping the last few in memory; they are never written out."""
        for line in process.stderr:
            status.append(line.rstrip())

    def receive(self, seconds):
        """Return the worker's next reply; a worker that ends or does not
        reply in time is stopped."""
        try:
            reply = self.replies.get(timeout=seconds)
        except queue.Empty:
            self.stop()
            raise adapter.BackendError("timeout") from None
        if reply is None:
            self.stop()
            raise adapter.BackendError("backend_failed")
        return reply

    def call(self, request, seconds):
        """Send one request and return its reply."""
        with self.lock:
            if self.process is None or self.process.poll() is not None:
                self.start()
            self.number += 1
            request = dict(request, id=self.number)
            try:
                self.process.stdin.write(json.dumps(request) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.stop()
                raise adapter.BackendError("backend_failed") from None
            reply = self.receive(seconds)
            if reply.get("id") != self.number:
                self.stop()
                raise adapter.BackendError("backend_failed")
            if "error" in reply:
                raise adapter.BackendError(
                    reply["error"] if reply["error"] in
                    adapter.BACKEND_FAILURES else "backend_failed")
            return reply

    def prompt_tokens(self, messages):
        """Return the tokens of a prompt, with its chat template."""
        return self.call({"op": "count", "messages": messages},
                         COUNT_SECONDS)["tokens"]

    def text_tokens(self, text):
        """Return the tokens of a text."""
        return self.call({"op": "count", "text": text},
                         COUNT_SECONDS)["tokens"]

    def generate(self, messages, schema, max_tokens, seconds):
        """Write one reply within the time limit."""
        reply = self.call({"op": "generate", "messages": messages,
                           "schema": schema, "max_tokens": max_tokens},
                          seconds)
        return adapter.Output(
            text=reply["text"], prompt_tokens=reply["prompt_tokens"],
            output_tokens=reply["output_tokens"],
            finished=reply["finished"], seconds=reply["seconds"],
            peak_memory_bytes=reply.get("peak_memory_bytes"))

    def describe(self):
        """Return what writes the candidates: the model, its revision and
        how the worker loaded it."""
        found = {"backend": "worker",
                 "model": self.settings.model.repository,
                 "revision": self.settings.model.revision,
                 "precision": self.precision,
                 "decoder": self.settings.worker.decoder,
                 "seed": self.settings.worker.seed}
        for key in ("torch", "cuda", "transformers", "decoder_version",
                    "dtype", "device"):
            if self.info and key in self.info:
                found[key] = self.info[key]
        return found

    def stop(self):
        """Stop the worker, asking first and then ending it, and close its
        pipes once the threads reading them have seen them end."""
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            try:
                process.stdin.write(json.dumps({"op": "stop"}) + "\n")
                process.stdin.flush()
                process.wait(timeout=STOP_SECONDS)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                process.kill()
                process.wait()
        readers, self.readers = self.readers, []
        for reader in readers:
            reader.join(timeout=STOP_SECONDS)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except (OSError, ValueError):
                pass


# --------------------------------------------------------------------------
# Default mode (the configuration) and the operator check
# --------------------------------------------------------------------------

def check_settings():
    """Return problems of the committed configuration."""
    try:
        settings = load_settings()
    except RuntimeProblem as error:
        return [str(error)]
    problems = []
    names = [item.path for item in settings.model.files]
    if len(names) != len(set(names)):
        problems.append("a model file is pinned twice")
    if not any(name.endswith(".safetensors") for name in names):
        problems.append("no weights are pinned")
    return problems


def summary(values):
    """Return min, median, 90th percentile and max of some numbers."""
    values = sorted(values)
    if not values:
        return "none"
    p90 = values[min(len(values) - 1, int(round(0.9 * (len(values) - 1))))]
    return "n=%d min=%d median=%d p90=%d max=%d" % (
        len(values), values[0], int(statistics.median(values)), p90,
        values[-1])


def operator_check(case_set=None):
    """Load the model and write every invented request; with a case set,
    count its prompts. Returns True when every step succeeded."""
    from ehr_evidence_app.generation import prompt, shape
    from ehr_evidence_app.qa import suite
    settings = load_settings()
    started = time.monotonic()
    problems = model_problems(settings, model_directory(settings))
    print("model files checked against their pins in %.0f s: %s" % (
        time.monotonic() - started,
        "all %d match" % len(settings.model.files) if not problems
        else "; ".join(problems)))
    if problems:
        return False
    backend = WorkerBackend(settings, verify=False)
    started = time.monotonic()
    try:
        backend.start()
    except adapter.BackendError as error:
        # No request was sent, so its status lines hold no record.
        print("worker failed to load: %s" % error.reason)
        for line in backend.status:
            print("  worker: %s" % line)
        return False
    info = backend.info
    print("worker loaded in %.0f s (model %.1f s); GPU weights %.0f MiB of "
          "%.0f MiB" % (time.monotonic() - started, info["load_seconds"],
                        info["gpu_weights_bytes"] / 2 ** 20,
                        info["gpu_total_bytes"] / 2 ** 20))
    print("on the GPU: %s" % ", ".join(info["gpu_modules"]))
    print("in system memory: %s" % ", ".join(info["cpu_modules"]))
    print("torch %s, CUDA %s, transformers %s, %s %s" % (
        info["torch"], info["cuda"], info["transformers"], info["decoder"],
        info["decoder_version"]))
    vocabulary = suite.synthetic_vocabulary()
    # Measured at the ceilings, so that a slow request shows its time.
    limits = adapter.scripted_limits().model_copy(update={
        "max_request_seconds": 600})
    released = 0
    pairs = shape.synthetic_contexts()
    for context, template in pairs:
        release = adapter.gate_release(
            context, vocabulary if context.task != "prediction_explanation"
            else None)
        writer = adapter.Writer(backend, limits)
        written = adapter.write(writer, context, template, release)
        output = written.output
        released += written.author == "model"
        print("%-24s %-13s prompt %5s  output %5s  %6s s  %6s tok/s  "
              "peak %5s MiB  %s" % (
                  context.task, template.outcome,
                  output.prompt_tokens if output else "-",
                  output.output_tokens if output else "-",
                  "%.1f" % output.seconds if output else "-",
                  "%.1f" % (output.output_tokens / output.seconds)
                  if output and output.seconds else "-",
                  "%.0f" % (output.peak_memory_bytes / 2 ** 20)
                  if output and output.peak_memory_bytes else "-",
                  "released as written" if written.author == "model"
                  else "fallback " + written.fallback + (
                      " " + ",".join(written.gate_reasons)
                      if written.gate_reasons else "")))
    print("invented requests released as the model wrote them: %d of %d"
          % (released, len(pairs)))
    if case_set:
        pairs, _ = adapter.development_requests(case_set)
        counts = {}
        for context, _ in pairs:
            counts.setdefault(context.task, []).append(
                backend.prompt_tokens(prompt.messages(context)))
        for task, values in sorted(counts.items()):
            print("%s prompt tokens, %s: %s" % (case_set, task,
                                                summary(values)))
    backend.stop()
    return True


def main(argv=None):
    """Check the configuration, or run the operator check."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-check", action="store_true",
                        help="load the model and write the invented "
                             "requests; prints counts and timings only")
    parser.add_argument("--case-set",
                        help="with --operator-check, also count the prompt "
                             "tokens of a development case set")
    args = parser.parse_args(argv)
    if args.case_set and not args.operator_check:
        print("FAILED: --case-set needs --operator-check")
        return 2
    if args.operator_check:
        ok = operator_check(args.case_set)
        print("RESULT: %s" % ("PASS" if ok else "FAIL"))
        return 0 if ok else 1
    problems = check_settings()
    for problem in problems:
        print("PROBLEM: %s" % problem)
    settings = None if problems else load_settings()
    if settings:
        print("model %s at %s; %d files pinned; decoder %s" % (
            settings.model.repository, settings.model.revision[:8],
            len(settings.model.files), settings.worker.decoder))
    print("RESULT: %s" % ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
