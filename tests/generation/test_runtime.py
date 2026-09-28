"""Synthetic checks for ehr_evidence_app.generation.runtime.

The worker runs its stand-in engine under the test interpreter, with its
caches and home in a temporary directory; no model is loaded and no real
record is read. The model files are invented.
"""

import contextlib
import hashlib
import io
import os
import shutil
import sys
import tempfile
import unittest

from ehr_evidence_app.generation import adapter, runtime, shape

SETTINGS = runtime.load_settings()


def fake(behaviour, root):
    """Return a backend on the worker's stand-in engine."""
    return runtime.WorkerBackend(SETTINGS, python=sys.executable,
                                 fake=behaviour, runtime_root=root)


class RuntimeTests(unittest.TestCase):
    """Pinned files, the worker's environment and its protocol."""

    def setUp(self):
        """Make a temporary runtime root."""
        self.root = tempfile.mkdtemp(prefix="runtime-test-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_configuration(self):
        """The committed configuration pins the model's revision and every
        file, and the default mode passes."""
        self.assertEqual(SETTINGS.model.repository,
                         "google/medgemma-1.5-4b-it")
        self.assertEqual(len(SETTINGS.model.revision), 40)
        self.assertEqual(SETTINGS.worker.precision, "bf16-mixed")
        backend = runtime.WorkerBackend(SETTINGS, precision="nf4")
        command = backend.command()
        self.assertEqual(command[command.index("--precision") + 1], "nf4")
        self.assertEqual(backend.describe()["precision"], "nf4")
        self.assertTrue(any(item.path.endswith(".safetensors")
                            for item in SETTINGS.model.files))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(runtime.main([]), 0)
            self.assertEqual(runtime.main(["--case-set", "dev-30-v1"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())

    def test_invalid_configuration(self):
        """A configuration that breaks its contract is refused."""
        path = os.path.join(self.root, "generation.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"schema_version": "1.0"}')
        with self.assertRaisesRegex(runtime.RuntimeProblem,
                                    "generation_config_invalid"):
            runtime.load_settings(path)

    def test_model_files_are_checked(self):
        """A file that is missing, of another size or another digest is
        found; matching files pass."""
        contents = {"config.json": b"{}", "model.safetensors": b"weights"}
        for name, data in contents.items():
            with open(os.path.join(self.root, name), "wb") as handle:
                handle.write(data)
        files = tuple(runtime.ModelFile(path=name, size_bytes=len(data),
                                        sha256=hashlib.sha256(data)
                                        .hexdigest())
                      for name, data in contents.items())
        settings = SETTINGS.model_copy(update={
            "model": SETTINGS.model.model_copy(update={"files": files})})
        self.assertEqual(runtime.model_problems(settings, self.root), [])
        with open(os.path.join(self.root, "model.safetensors"), "wb") as out:
            out.write(b"WEIGHTS")
        self.assertEqual(runtime.model_problems(settings, self.root),
                         ["digest differs: model.safetensors"])
        with open(os.path.join(self.root, "config.json"), "wb") as out:
            out.write(b"{ }")
        os.remove(os.path.join(self.root, "model.safetensors"))
        self.assertEqual(runtime.model_problems(settings, self.root),
                         ["size differs: config.json",
                          "missing: model.safetensors"])

    def test_worker_environment(self):
        """The worker runs offline, with every cache, home and temporary
        directory under the runtime root, and inherits no credentials."""
        found = runtime.worker_environment(self.root)
        for name in ("TEMP", "TMP", "USERPROFILE", "HOME", "APPDATA",
                     "LOCALAPPDATA", "HF_HOME", "HF_HUB_CACHE", "TORCH_HOME",
                     "CUDA_CACHE_PATH", "XDG_CACHE_HOME",
                     "PYTHONPYCACHEPREFIX"):
            self.assertTrue(found[name].startswith(self.root), name)
        for name in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
                     "HF_HUB_DISABLE_TELEMETRY"):
            self.assertEqual(found[name], "1")
        for name in ("HF_TOKEN", "PGPASSWORD", "CLEARML_API_ACCESS_KEY",
                     "CLEARML_API_SECRET_KEY"):
            self.assertNotIn(name, found)

    def test_protocol(self):
        """Counts and a finished reply; output cut off at its limit."""
        backend = fake("ok", self.root)
        self.addCleanup(backend.stop)
        messages = [{"role": "user", "content": "x" * 40}]
        output = backend.generate(messages, {}, 10, 30)
        self.assertEqual((output.text, output.finished), ("{}", True))
        self.assertEqual(backend.prompt_tokens(messages), 11)
        self.assertEqual(backend.text_tokens("abcd" * 5), 6)
        self.assertEqual(backend.describe()["model"],
                         SETTINGS.model.repository)
        cut = fake("truncate", self.root)
        self.addCleanup(cut.stop)
        self.assertFalse(cut.generate(messages, {}, 10, 30).finished)

    def test_stopping_closes_the_pipes(self):
        """A stopped worker has ended, its reading threads have finished
        and its pipes are closed, so nothing is left to the collector."""
        backend = fake("ok", self.root)
        self.addCleanup(backend.stop)
        backend.text_tokens("abcd")
        process, readers = backend.process, list(backend.readers)
        backend.stop()
        self.assertIsNotNone(process.poll())
        self.assertFalse(any(reader.is_alive() for reader in readers))
        self.assertTrue(all(stream.closed for stream in (
            process.stdin, process.stdout, process.stderr)))
        self.assertEqual((backend.process, backend.readers), (None, []))

    def test_failures(self):
        """Exhausted memory and failures are backend errors; a worker that
        does not reply in time is stopped, and the next call starts a new
        one."""
        for behaviour, reason in (("oom", "resource_exhausted"),
                                  ("fail", "backend_failed")):
            backend = fake(behaviour, self.root)
            self.addCleanup(backend.stop)
            with self.assertRaises(adapter.BackendError) as caught:
                backend.generate([{"role": "user", "content": "x"}], {}, 5,
                                 30)
            self.assertEqual(caught.exception.reason, reason)
        slow = fake("slow", self.root)
        self.addCleanup(slow.stop)
        with self.assertRaises(adapter.BackendError) as caught:
            slow.generate([{"role": "user", "content": "x"}], {}, 5, 1)
        self.assertEqual(caught.exception.reason, "timeout")
        self.assertIsNone(slow.process)
        self.assertEqual(slow.text_tokens("abcd"), 2)

    def test_adapter_with_the_worker(self):
        """Through the adapter, the stand-in's "{}" breaks its schema and
        the template answers."""
        context, template = shape.synthetic_contexts()[0]
        backend = fake("ok", self.root)
        self.addCleanup(backend.stop)
        written = adapter.write(
            adapter.Writer(backend, adapter.scripted_limits()), context,
            template, adapter.gate_release(context))
        self.assertEqual((written.author, written.fallback),
                         ("template", "schema_violated"))


if __name__ == "__main__":
    unittest.main()
