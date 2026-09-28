"""Synthetic tests for keeping the pinned language model in the tracker.

The model's files are invented, written to the workspace test scratch
with their staging directory; the remote mode runs against an in-memory
tracker and file store. No model file, credential or service is used.
"""

import contextlib
import copy
import hashlib
import io
import json
import ntpath
import os
import tempfile
import unittest

from ehr_evidence_app.generation import runtime
from ehr_evidence_app.tracking import models
from tests.tracking.test_registration import (
    SCRATCH, FakeAdapter, regular_file, valid_environment)

MODEL_ID = "e4" * 16


class ModelTracker(FakeAdapter):
    """The in-memory tracker, answering the model registry too."""

    def __init__(self, registered=()):
        """Start with some registered models."""
        super(ModelTracker, self).__init__()
        self.models = [copy.deepcopy(item) for item in registered]

    def call(self, service, action, payload):
        """Answer the model registry's actions; the rest as before."""
        if service != "models":
            return super(ModelTracker, self).call(service, action, payload)
        self.calls.append((service + "." + action, copy.deepcopy(payload)))
        if action == "get_all" and "id" in payload:
            return 200, {"data": {"models": [
                item for item in self.models if item["id"] in payload["id"]]}}
        if action == "get_all":
            return 200, {"data": {"models": [
                item for item in self.models
                if item["project"] in payload["project"]]}}
        if action == "create":
            self.models.append(dict(copy.deepcopy(payload), id=MODEL_ID))
            return 200, {"data": {"id": MODEL_ID, "created": True}}
        if action == "add_or_update_metadata":
            return 200, {"data": {"updated": 1}}
        raise AssertionError("unexpected service action " + action)


class FileStore(object):
    """An in-memory files server keeping each upload's size."""

    def __init__(self, short=()):
        """Keep nothing yet; uploads of names in short arrive cut."""
        self.files, self.sent, self.short = {}, [], set(short)

    def size(self, url):
        """Return the size of an uploaded file, or None."""
        return self.files.get(url)

    def upload(self, local, url):
        """Keep the size of the uploaded file."""
        self.sent.append(url.rsplit("/", 1)[1])
        size = os.path.getsize(local)
        self.files[url] = size - 1 if self.sent[-1] in self.short else size


class StagingTests(unittest.TestCase):
    """Invented files and their staging, in the test scratch."""

    def setUp(self):
        """Write two invented model files and pin them."""
        if not os.path.isdir(SCRATCH):
            self.skipTest("configured test scratch is missing; run "
                          "scripts/setup-destinations.ps1 first")
        scratch = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.addCleanup(scratch.cleanup)
        self.model_dir = ntpath.join(scratch.name, "model")
        self.root = ntpath.join(scratch.name, "staging")
        os.makedirs(self.model_dir)
        files = []
        for name, data in (("config.json", b'{"invented": true}\n'),
                           ("model-00001-of-00001.safetensors", b"\0" * 64)):
            with open(ntpath.join(self.model_dir, name), "wb") as handle:
                handle.write(data)
            files.append({"path": name, "size_bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()})
        pinned = json.loads(models.invented_settings().model_dump_json())
        pinned["model"]["files"] = files
        self.settings = runtime.GenerationFile.model_validate_json(
            json.dumps(pinned))
        self.directory = models.staging_directory(self.settings, self.root)

    def agree(self, name=models.AGREEMENT_NAMES[0], size=4096):
        """Save an invented agreement copy in the staging directory."""
        os.makedirs(self.directory, exist_ok=True)
        with open(ntpath.join(self.directory, name), "wb") as handle:
            handle.write(b"x" * size)

    def stage(self):
        """Stage the invented model."""
        return models.stage(self.settings, self.model_dir, self.root)

    def remote(self, tracker, store):
        """Run the remote mode against the in-memory tracker and store."""
        return models.remote_result(
            valid_environment(), lambda: tracker, lambda: store,
            self.settings, self.model_dir, self.root, lstat=regular_file,
            say=lambda line: None)

    def test_agreement_copy_is_required(self):
        """Nothing is staged without exactly one agreement copy of a
        plausible size."""
        for prepare in (lambda: None, lambda: self.agree(size=10),
                        lambda: (self.agree(), self.agree(
                            models.AGREEMENT_NAMES[1]))):
            prepare()
            with self.assertRaises(models.ModelError):
                self.stage()

    def test_staging(self):
        """The notice and the manifest are written beside the agreement;
        the manifest names every file with its kind, size and SHA-256, and
        is uploaded last."""
        self.agree()
        directory, manifest, uploads = self.stage()
        self.assertEqual([name for _, name, _ in uploads], [
            "config.json", "model-00001-of-00001.safetensors",
            models.NOTICE_NAME, models.AGREEMENT_NAMES[0],
            models.MANIFEST_NAME])
        self.assertEqual([item["type"] for item in manifest["files"]], [
            "configuration", "weights", "licence_notice",
            "licence_agreement"])
        self.assertTrue(all(item["non_patient_non_secret_confirmation"]
                            for item in manifest["files"]))
        with open(ntpath.join(directory, models.NOTICE_NAME), "r",
                  encoding="utf-8") as handle:
            notice = handle.read()
        self.assertTrue(notice.startswith(
            "HAI-DEF is provided under and subject to the Health AI "
            "Developer Foundations Terms of Use found at "
            + models.TERMS_URL))
        self.assertIn("none was modified", notice)
        self.assertEqual(self.stage()[1], manifest)

    def test_changed_file_is_refused(self):
        """A model file that differs from its pin is never staged."""
        self.agree()
        with open(ntpath.join(self.model_dir, "config.json"), "ab") as handle:
            handle.write(b" ")
        with self.assertRaises(models.ModelError) as raised:
            self.stage()
        self.assertEqual(str(raised.exception), "model_files_differ")

    def test_remote_uploads_and_registers_once(self):
        """Every file is uploaded, the manifest last, and the revision
        registered with its tags and metadata; a second run finds it and
        uploads nothing."""
        self.agree()
        tracker, store = ModelTracker(), FileStore()
        result = self.remote(tracker, store)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual((result["model_id"], result["uploaded"]),
                         (MODEL_ID, 5))
        self.assertEqual(store.sent[-1], models.MANIFEST_NAME)
        created = [payload for action, payload in tracker.calls
                   if action == "models.create"][0]
        self.assertEqual(created["uri"], models.remote_base(self.settings)
                         + "/" + models.MANIFEST_NAME)
        self.assertIn("revision:" + "0" * 40, created["tags"])
        self.assertNotIn("tasks.create", tracker.actions())
        again = self.remote(ModelTracker(tracker.models), FileStore())
        self.assertEqual((again["status"], again["registered_before"],
                          again["uploaded"]), ("PASS", True, 0))

    def test_files_already_there_are_not_sent_again(self):
        """A file the files server holds at its size is kept."""
        self.agree()
        store = FileStore()
        base = models.remote_base(self.settings)
        store.files[base + "/model-00001-of-00001.safetensors"] = 64
        result = self.remote(ModelTracker(), store)
        self.assertEqual(result["uploaded"], 4)
        self.assertNotIn("model-00001-of-00001.safetensors", store.sent)

    def test_incomplete_upload_is_not_registered(self):
        """An upload of the wrong size stops the run before the model is
        registered."""
        self.agree()
        tracker = ModelTracker()
        result = self.remote(tracker, FileStore(short={"config.json"}))
        self.assertEqual((result["status"], result["finding"]),
                         ("FAIL", "upload_incomplete"))
        self.assertNotIn("models.create", tracker.actions())

    def test_unlisted_model_is_refused_first(self):
        """A model the policy does not list is refused before any file is
        read or any service reached."""
        other = self.settings.model_copy(update={
            "model": self.settings.model.model_copy(update={
                "repository": "another/model"})})
        tracker = ModelTracker()
        result = models.remote_result(
            valid_environment(), lambda: tracker, FileStore, other,
            self.model_dir, self.root, lstat=regular_file)
        self.assertEqual(result["finding"], "model_not_listed")
        self.assertEqual(tracker.calls, [])


class CommandTests(unittest.TestCase):
    """The command's modes."""

    def test_command(self):
        """The default mode passes; the two operator modes are one or the
        other."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(models.main([]), 0)
            self.assertEqual(models.main(["--operator-real",
                                          "--operator-remote"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())

    def test_pinned_model_is_listed(self):
        """The pinned model is the policy's listed model, and every one of
        its files has a kind."""
        settings = runtime.load_settings()
        models.listed(settings)
        self.assertEqual({models.kind_of(item.path)
                          for item in settings.model.files},
                         {"weights", "weights_index", "tokenizer",
                          "configuration", "model_card",
                          "repository_attributes"})


if __name__ == "__main__":
    unittest.main()
