"""Keep the pinned language model in the tracker (ADR 0018).

MedGemma 1.5 4B IT, pinned by config/generation.json, is the one model the
project keeps in the tracker (config/clearml_policy.json, model_artefacts).
Its developer publishes it pretrained, and it was never trained on the
project's data; its licence, the Health AI Developer Foundations terms,
lets it be passed on with a copy of the agreement and a notice. Nothing
patient-derived is uploaded: no graph checkpoint, output, record or data
file.

--operator-real checks every file of the model against its size and
SHA-256 in config/generation.json, then writes a staging directory in the
workspace, outputs\\model-staging\\<repository>\\<revision>, holding:

* NOTICE: the notice the licence requires, and that no file was modified;
* the copy of the licence agreement (one of AGREEMENT_NAMES), which the
  project owner saves there first; without it nothing is staged or
  uploaded;
* MANIFEST.json: the model's repository, revision and licence, and for
  each file its name, kind, size, SHA-256 and provenance, and the
  confirmation that it holds nothing patient-derived or secret.

--operator-remote then uploads the model's files, the notice, the
agreement and, last, the manifest to the tracker's files server under
ehr-evidence-app/models/medgemma-1.5-4b-it/<revision>/, checking the size
of each upload (a file already there at its size is not sent again). It
registers the model in the project's model registry, with the manifest's
address, its tags and metadata, and reads it back; a revision is
registered once. The upload is about 8.6 GB. The mode needs the tracker's
configuration in the process environment, which this module never reads
or prints.

The default mode checks the manifest, the notice and the registration
of invented files, in memory.

Usage:
    python -m ehr_evidence_app.tracking.models
    python -m ehr_evidence_app.tracking.models --operator-real
    python -m ehr_evidence_app.tracking.models --operator-remote
"""

import argparse
import contextlib
import hashlib
import json
import ntpath
import os
import re
import sys
import time

from ehr_evidence_app.tracking import connection as checker
from ehr_evidence_app.tracking import controlled as tracking

SCHEMA_VERSION = "1.0"
STAGING_ROOT = tracking.WORKSPACE_ROOT + "\\outputs\\model-staging"
MODEL_NAME = "MedGemma 1.5 4B IT"
LICENCE = "health-ai-developer-foundations"
TERMS_URL = ("https://developers.google.com/health-ai-developer-foundations/"
             "terms")
NOTICE_NAME = "NOTICE"
MANIFEST_NAME = "MANIFEST.json"
# The names the project owner may save the licence agreement under.
AGREEMENT_NAMES = ("HAI-DEF-terms-of-use.pdf", "HAI-DEF-terms-of-use.html",
                   "HAI-DEF-terms-of-use.txt")
MAX_AGREEMENT_BYTES = 8 * 1024 * 1024
# The largest upload whose size is counted from its decoded bytes, when the
# files server sends it compressed although asked not to.
MAX_COUNTED_BYTES = 64 * 1024 * 1024
REMOTE_FOLDER = "ehr-evidence-app/models"
FRAMEWORK = "PyTorch"
COMMENT = ("As published by its developer at the pinned revision, not "
           "trained on this project's data; distributed with the licence's "
           "notice and a copy of its agreement (see MANIFEST.json).")
# The kind of each file of the model, by name or ending.
KINDS = ((re.compile(r"\.safetensors$"), "weights"),
         (re.compile(r"^model\.safetensors\.index\.json$"), "weights_index"),
         (re.compile(r"^(?:tokenizer\.json|tokenizer\.model|tokenizer_config"
                     r"\.json|special_tokens_map\.json|added_tokens\.json|"
                     r"chat_template\.jinja)$"), "tokenizer"),
         (re.compile(r"^(?:config|generation_config|preprocessor_config|"
                     r"processor_config)\.json$"), "configuration"),
         (re.compile(r"^README\.md$"), "model_card"),
         (re.compile(r"^\.gitattributes$"), "repository_attributes"))


class ModelError(Exception):
    """A model that cannot be staged or kept; carries a short code."""


def notice_text(repository, revision):
    """Return the NOTICE of the model's files: the licence's notice, and
    that no file was modified."""
    return ("HAI-DEF is provided under and subject to the Health AI "
            "Developer Foundations Terms of Use found at %s\n\n"
            "The files of %s at revision %s are as published; none was "
            "modified.\n" % (TERMS_URL, repository, revision))


def kind_of(name):
    """Return the kind of one of the model's files."""
    for pattern, kind in KINDS:
        if pattern.search(name):
            return kind
    raise ModelError("file_kind_unknown")


def staging_directory(settings, root=STAGING_ROOT):
    """Return the staging directory of the pinned revision."""
    return ntpath.join(root, settings.model.repository.replace("/", "--"),
                       settings.model.revision)


def remote_base(settings, files_server=checker.ENDPOINTS["files_server"]):
    """Return the address of the pinned revision on the files server."""
    return "/".join((files_server, REMOTE_FOLDER,
                     settings.model.repository.split("/", 1)[1],
                     settings.model.revision))


def listed(settings):
    """Refuse any model the policy does not list, before any file is
    read."""
    if settings.model.repository not in checker.MODEL_ARTEFACTS[
            "listed_models"]:
        raise ModelError("model_not_listed")


def agreement_of(directory):
    """Return the path of the agreement copy in a staging directory; there
    must be exactly one, of a plausible size."""
    found = [ntpath.join(directory, name) for name in AGREEMENT_NAMES
             if os.path.isfile(ntpath.join(directory, name))]
    if len(found) != 1:
        raise ModelError("agreement_copy_missing" if not found
                         else "agreement_copy_ambiguous")
    size = os.path.getsize(found[0])
    if not 1024 <= size <= MAX_AGREEMENT_BYTES:
        raise ModelError("agreement_copy_implausible")
    return found[0]


def digest(path):
    """Return (size, SHA-256) of a small file."""
    with open(path, "rb") as handle:
        data = handle.read()
    return len(data), hashlib.sha256(data).hexdigest()


def manifest_of(settings, notice, agreement):
    """Return the manifest of the model's files, its notice and its
    agreement copy, each given as (name, size, SHA-256), with the fields of
    connection.MODEL_ARTEFACTS."""
    model = settings.model
    source = "%s at revision %s, unmodified" % (model.repository,
                                               model.revision)
    files = [{"logical_name": pinned.path, "type": kind_of(pinned.path),
              "size_bytes": pinned.size_bytes, "sha256": pinned.sha256,
              "provenance": source,
              "non_patient_non_secret_confirmation": True}
             for pinned in model.files]
    for (name, size, sha256), kind, provenance in (
            (notice, "licence_notice", "the licence's required notice"),
            (agreement, "licence_agreement",
             "a copy of the licence agreement, saved by the project "
             "owner")):
        files.append({"logical_name": name, "type": kind,
                      "size_bytes": size, "sha256": sha256,
                      "provenance": provenance,
                      "non_patient_non_secret_confirmation": True})
    if [set(item) for item in files] != [set(checker.MODEL_ARTEFACTS[
            "manifest_fields"])] * len(files):
        raise ModelError("manifest_fields")
    return {"schema_version": SCHEMA_VERSION, "name": model.name,
            "repository": model.repository, "revision": model.revision,
            "licence": LICENCE, "terms": TERMS_URL,
            "trained_on_project_data": False, "modified": False,
            "files": files}


def manifest_bytes(manifest):
    """Return a manifest as deterministic JSON bytes."""
    return (json.dumps(manifest, sort_keys=True, indent=1)
            + "\n").encode("utf-8")


def write_exactly(path, data):
    """Write a small file, replacing it only when its bytes differ."""
    if os.path.isfile(path):
        with open(path, "rb") as handle:
            if handle.read() == data:
                return path
    partial = path + ".partial"
    with open(partial, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(partial, path)
    return path


def stage(settings, model_dir=None, root=STAGING_ROOT, verify=True):
    """Check the model's files and write its staging directory; return
    (directory, manifest, the files to upload in order as (local path,
    name, size)). The agreement copy must be there first. verify=False
    skips the digests of the model's files, for tests only."""
    from ehr_evidence_app.generation import runtime
    listed(settings)
    model_dir = model_dir or runtime.model_directory(settings)
    if verify and runtime.model_problems(settings, model_dir):
        raise ModelError("model_files_differ")
    directory = staging_directory(settings, root)
    if not os.path.isdir(directory):
        raise ModelError("agreement_copy_missing")
    agreement = agreement_of(directory)
    notice = write_exactly(ntpath.join(directory, NOTICE_NAME), notice_text(
        settings.model.repository, settings.model.revision).encode("utf-8"))
    manifest = manifest_of(settings, (NOTICE_NAME,) + digest(notice),
                           (ntpath.basename(agreement),) + digest(agreement))
    manifest_path = write_exactly(ntpath.join(directory, MANIFEST_NAME),
                                  manifest_bytes(manifest))
    uploads = [(ntpath.join(model_dir, pinned.path), pinned.path,
                pinned.size_bytes) for pinned in settings.model.files]
    uploads += [(path, ntpath.basename(path), os.path.getsize(path))
                for path in (notice, agreement, manifest_path)]
    return directory, manifest, uploads


# --------------------------------------------------------------------------
# The model registry
# --------------------------------------------------------------------------

def model_tags(settings):
    """Return the registered model's tags."""
    return ["pretrained", "licence:" + LICENCE,
            "revision:" + settings.model.revision]


def registration_payload(settings, project_id, manifest_url):
    """Return the models.create request of the pinned revision."""
    return {"name": MODEL_NAME, "uri": manifest_url, "framework": FRAMEWORK,
            "comment": COMMENT, "project": project_id, "ready": True,
            "tags": model_tags(settings)}


def model_metadata(settings, manifest):
    """Return the registered model's metadata items."""
    items = (("repository", "str", settings.model.repository),
             ("revision", "str", settings.model.revision),
             ("licence", "str", LICENCE),
             ("files", "int", str(len(manifest["files"]))),
             ("size_bytes", "int", str(sum(item["size_bytes"]
                                           for item in manifest["files"]))),
             ("manifest_sha256", "str", hashlib.sha256(
                 manifest_bytes(manifest)).hexdigest()),
             ("trained_on_project_data", "bool", "false"))
    return [{"key": key, "type": kind, "value": value}
            for key, kind, value in items]


def find_model(adapter, project_id, settings):
    """Return the ID of the pinned revision's registered model, or None."""
    data = checker.call_service(adapter, "models", "get_all", {
        "project": [project_id], "name": "^%s$" % re.escape(MODEL_NAME),
        "only_fields": ["id", "name", "tags"]})
    found = [item for item in data.get("models") or []
             if isinstance(item, dict) and item.get("name") == MODEL_NAME
             and "revision:" + settings.model.revision in (
                 item.get("tags") or [])]
    if len(found) > 1:
        raise checker.ProbeFailure("model", "model_ambiguous")
    return checker.safe_id(found[0].get("id")) if found else None


def register(adapter, project_id, settings, manifest, manifest_url):
    """Register the pinned revision, add its metadata and read it back;
    return its ID."""
    payload = registration_payload(settings, project_id, manifest_url)
    created = checker.call_service(adapter, "models", "create", payload)
    model_id = checker.safe_id(created.get("id"))
    if model_id is None:
        raise checker.ProbeFailure("model", "unexpected_model_id")
    updated = checker.call_service(adapter, "models",
                                   "add_or_update_metadata", {
                                       "model": model_id,
                                       "metadata": model_metadata(
                                           settings, manifest)})
    if updated.get("updated") != 1:
        raise checker.ProbeFailure("model", "metadata_failed")
    data = checker.call_service(adapter, "models", "get_all", {
        "id": [model_id], "only_fields": ["id", "name", "uri", "project",
                                          "tags", "ready", "framework",
                                          "comment"]})
    models = data.get("models")
    if not (isinstance(models, list) and len(models) == 1
            and isinstance(models[0], dict)):
        raise checker.ProbeFailure("model", "readback_mismatch")
    model = models[0]
    if (model.get("id") != model_id
            or any(model.get(field) != payload[field]
                   for field in ("name", "uri", "project", "comment",
                                 "ready"))
            or str(model.get("framework", "")).lower() != FRAMEWORK.lower()
            or sorted(model.get("tags") or []) != sorted(payload["tags"])):
        raise checker.ProbeFailure("model", "readback_mismatch")
    return model_id


class SdkStorage(object):
    """Bridge to the SDK's storage manager, used by the live mode only;
    the SDK sends its credentials to the declared files server only."""

    def __init__(self, manager=None):
        """Import the SDK's storage manager; manager is injectable for
        tests."""
        if manager is None:
            from clearml.storage.manager import StorageManager
            manager = StorageManager
        self._manager = manager

    def size(self, url):
        """Return the size of an uploaded file as the files server holds
        it, or None when it holds none. The file is asked for through the
        SDK's session, with the headers the SDK attaches for the files
        server, and without content coding: the server compresses some
        types, such as JSON, on the way out, and a compressed reply's
        length is not the file's. A reply compressed anyway, or without a
        length, is counted from its decoded bytes, for a small file
        only."""
        container = self._manager.storage_helper.get(url)._container
        headers = dict(container.get_headers(url) or {})
        headers["Accept-Encoding"] = "identity"
        try:
            response = container.session.get(url, headers=headers,
                                              stream=True, timeout=(30, 120))
            with contextlib.closing(response):
                if response.status_code != 200:
                    return None
                coding = response.headers.get("Content-Encoding",
                                              "identity").strip().lower()
                length = response.headers.get("Content-Length", "")
                if coding == "identity" and length.isdigit():
                    return int(length)
                counted = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    counted += len(chunk)
                    if counted > MAX_COUNTED_BYTES:
                        return None
                return counted
        except (OSError, ValueError):
            return None

    def upload(self, local, url):
        """Upload one file and wait for it."""
        self._manager.upload_file(local, url, wait_for_upload=True,
                                  retries=3)


def offline_result(settings=None, model_dir=None, root=STAGING_ROOT):
    """Check the model's files and write the staging directory."""
    from ehr_evidence_app.generation import runtime
    try:
        settings = settings or runtime.load_settings()
        _, manifest, uploads = stage(settings, model_dir, root)
    except ModelError as error:
        return {"status": "FAIL", "mode": "offline", "finding": str(error)}
    except runtime.RuntimeProblem as problem:
        return {"status": "FAIL", "mode": "offline", "finding": str(problem)}
    return {"status": "PASS", "mode": "offline",
            "model": settings.model.repository,
            "files": len(uploads),
            "size_bytes": sum(size for _, _, size in uploads),
            "licence": manifest["licence"]}


def remote_result(environ=None, adapter_factory=checker.SdkAdapter,
                  storage_factory=SdkStorage, settings=None, model_dir=None,
                  root=STAGING_ROOT, lstat=os.lstat, say=print,
                  verify=True):
    """Stage the model, upload what the files server does not hold yet,
    and register the revision once. environ, adapter_factory,
    storage_factory, settings, model_dir, root, lstat, say and verify are
    injectable for tests."""
    from ehr_evidence_app.generation import runtime
    env = os.environ if environ is None else environ
    started = time.monotonic()
    try:
        if checker.environment_problems(env, lstat=lstat):
            return {"status": "FAIL", "mode": "operator_remote",
                    "finding": "environment_invalid"}
        settings = settings or runtime.load_settings()
        _, manifest, uploads = stage(settings, model_dir, root, verify)
        base = remote_base(settings)
        with checker.quiet_sdk():
            adapter = adapter_factory()
            project_id = checker.connect_project(adapter)
            existing = find_model(adapter, project_id, settings)
        if existing:
            return {"status": "PASS", "mode": "operator_remote",
                    "model_id": existing, "registered_before": True,
                    "uploaded": 0}
        with checker.quiet_sdk():
            storage = storage_factory()
        sent = 0
        for number, (local, name, size) in enumerate(uploads, 1):
            url = base + "/" + name
            with checker.quiet_sdk():
                if storage.size(url) != size:
                    storage.upload(local, url)
                    sent += 1
                found = storage.size(url)
            if found != size:
                return {"status": "FAIL", "mode": "operator_remote",
                        "finding": "upload_incomplete", "file": name,
                        "size_bytes": size, "found_bytes": found}
            say("uploaded %d of %d: %s (%d bytes)" % (number, len(uploads),
                                                      name, size))
        with checker.quiet_sdk():
            model_id = register(adapter, project_id, settings, manifest,
                                base + "/" + MANIFEST_NAME)
        return {"status": "PASS", "mode": "operator_remote",
                "model_id": model_id, "registered_before": False,
                "files": len(uploads), "uploaded": sent,
                "size_bytes": sum(size for _, _, size in uploads),
                "elapsed_seconds": round(time.monotonic() - started, 3)}
    except checker.ProbeFailure as failure:
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": failure.finding[1]}
    except ModelError as error:
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": str(error)}
    except runtime.RuntimeProblem as problem:
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": str(problem)}
    except Exception as error:  # noqa: BLE001 - reported by code only
        return {"status": "FAIL", "mode": "operator_remote",
                "finding": checker.SDK_ERRORS.get(type(error).__name__,
                                                  "model_upload_failed")}


# --------------------------------------------------------------------------
# Synthetic default mode (invented files only)
# --------------------------------------------------------------------------

def invented_settings():
    """Return settings pinning two invented files, in memory."""
    from ehr_evidence_app.generation import runtime
    files = [{"path": name, "size_bytes": len(data),
              "sha256": hashlib.sha256(data).hexdigest()}
             for name, data in (("config.json", b'{"invented": true}\n'),
                                ("model-00001-of-00001.safetensors",
                                 b"\0" * 64))]
    return runtime.GenerationFile.model_validate_json(json.dumps({
        "schema_version": "1.0", "purpose": "Invented files.",
        "model": {"name": MODEL_NAME,
                  "repository": "google/medgemma-1.5-4b-it",
                  "revision": "0" * 40, "directory": "invented",
                  "files": files},
        "worker": {"precision": "bf16-mixed", "decoder": "xgrammar",
                   "seed": 1, "minimum_free_gpu_mib": 0,
                   "start_seconds": 1}}))


def run_synthetic():
    """Return (files described, problems) of invented files and of the
    pinned model's file list, in memory: the manifest, the notice, the
    registration and what is refused."""
    from ehr_evidence_app.generation import runtime
    problems = []
    settings = invented_settings()
    notice = notice_text(settings.model.repository,
                         settings.model.revision).encode("utf-8")
    manifest = manifest_of(
        settings, (NOTICE_NAME, len(notice), hashlib.sha256(
            notice).hexdigest()),
        (AGREEMENT_NAMES[0], 2048, "a" * 64))
    kinds = [item["type"] for item in manifest["files"]]
    if kinds != ["configuration", "weights", "licence_notice",
                 "licence_agreement"]:
        problems.append("a file's kind is wrong")
    if manifest["trained_on_project_data"] or manifest["modified"]:
        problems.append("the manifest misdescribes the model")
    if TERMS_URL not in notice.decode("utf-8"):
        problems.append("the notice does not name the terms")
    pinned = runtime.load_settings()
    try:
        listed(pinned)
        for item in pinned.model.files:
            kind_of(item.path)
    except ModelError:
        problems.append("the pinned model cannot be described")
    payload = registration_payload(settings, "invented-project",
                                   remote_base(settings) + "/"
                                   + MANIFEST_NAME)
    if payload["uri"] != ("https://files.clear.ml/ehr-evidence-app/models/"
                          "medgemma-1.5-4b-it/%s/MANIFEST.json" % ("0" * 40)):
        problems.append("the model's address is wrong")
    if "revision:" + "0" * 40 not in payload["tags"]:
        problems.append("the model's tags do not name its revision")
    other = settings.model_copy(update={"model": settings.model.model_copy(
        update={"repository": "another/model"})})
    try:
        listed(other)
        problems.append("a model the policy does not list was accepted")
    except ModelError:
        pass
    try:
        kind_of("optimizer.pt")
        problems.append("a file of no known kind was described")
    except ModelError:
        pass
    return len(manifest["files"]), problems


def main(argv=None):
    """Run the default mode on invented files, or an operator mode on the
    pinned model; prints counts, codes and IDs only."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="check the model's files and write the staging "
                             "directory")
    parser.add_argument("--operator-remote", action="store_true",
                        help="also upload the model and register it")
    args = parser.parse_args(argv)
    if args.operator_real and args.operator_remote:
        print("FAILED: give one of --operator-real and --operator-remote")
        return 2
    if args.operator_remote:
        result = remote_result()
    elif args.operator_real:
        result = offline_result()
    else:
        count, problems = run_synthetic()
        for problem in problems:
            print("PROBLEM: %s" % problem)
        print("invented files described: %d" % count)
        print("RESULT: %s" % ("FAIL" if problems else "PASS"))
        return 1 if problems else 0
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
