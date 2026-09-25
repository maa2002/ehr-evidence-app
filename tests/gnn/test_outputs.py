"""Synthetic checks for ehr_evidence_app.gnn.outputs (Step 20).

Every fixture is invented: small numpy graphs, a stand-in bridge, a
torch-like namespace and invented mapping, canonical and Step 19 documents.
torch is never imported, no upstream module is imported and no real graph,
checkpoint, mapping, explanation or patient record is opened. Scratch files
are created only under the configured workspace test temp directory.
"""

import contextlib
import copy
import hashlib
import importlib
import importlib.util
import io
import json
import logging
import math
import os
import re
import shutil
import socket
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.gnn import mapping as mapping  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402
from ehr_evidence_app.gnn import outputs as pmo  # noqa: E402

PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
PILOT = [1, 3, 4, 6, 7, 8, 10, 11, 12, 13]
MARKER = "SYNTHETIC_STEP20_MARKER"


def configured_test_temp():
    """Return the configured workspace test scratch directory (workspace-only rules)."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def fixtures():
    """Return (plan, graphs, documents) built from invented data only."""
    plan = pmo.synthetic_plan()
    graphs = pmo.synthetic_collection()
    return plan, graphs, pmo.synthetic_documents(plan, graphs)


def run(plan, runtime, documents, held=()):
    """Run the in-memory generation phase on invented fixtures."""
    return pmo.run_isolated(plan, runtime, documents, list(held))


class RecordingBridge(pmo.SyntheticBridge):
    """Synthetic bridge with an optional hook run inside explain()."""

    def __init__(self, hook=None, model=None, **kwargs):
        """Keep the hook and an optional replacement model."""
        super().__init__(**kwargs)
        self.hook = hook
        self.model = model
        self.seen = []

    def load_model(self, device, vocabularies=None):
        """Return the replacement model when one is given."""
        self.seen.append(vocabularies)
        model = super().load_model(device, vocabularies)
        return self.model if self.model is not None else model

    def explain(self, model, graph, patient_index, top_k, device):
        """Run the hook, then return the invented record."""
        if self.hook:
            self.hook(self, model)
        return super().explain(model, graph, patient_index, top_k, device)


class SelectionTestCase(unittest.TestCase):
    """Step 17 pilot selection and every rejection case."""

    def setUp(self):
        """Build invented plan, graphs and mapping document."""
        self.plan, self.graphs, documents = fixtures()
        self.document = documents["step17_mapping"]
        self.records = self.document["records"]

    def expect(self, code):
        """Assert that selection fails with the given code."""
        with self.assertRaisesRegex(pmo.Step20Error, "^%s$" % code):
            pmo.select_pilot(self.document, self.plan)

    def test_selects_all_ten_sorted(self):
        """All and only the ten records are selected, by patient_index."""
        selection = pmo.select_pilot(self.document, self.plan)
        self.assertEqual([item["patient_index"] for item in selection],
                         PILOT)
        self.records.reverse()
        again = pmo.select_pilot(self.document, self.plan)
        self.assertEqual(again, selection)

    def test_record_count_rejected(self):
        """Nine or eleven records fail."""
        extra = copy.deepcopy(self.records[0])
        del self.records[0]
        self.expect("mapping_record_count")
        self.records.extend([extra, copy.deepcopy(extra)])
        self.expect("mapping_record_count")

    def test_invalid_patient_index_rejected(self):
        """Null, text, bool, negative and float indices fail."""
        for value in (None, "1", True, -1, 1.0):
            self.records[0]["patient_index"] = value
            self.expect("mapping_patient_index_invalid")

    def test_repeated_patient_index_rejected(self):
        """Two records with one patient_index fail."""
        self.records[1]["patient_index"] = self.records[0]["patient_index"]
        self.expect("mapping_patient_index_repeated")

    def test_not_verified_rejected(self):
        """Quarantined and repeated-visit statuses fail."""
        for status in ("quarantined", "verified_with_repeated_visit_nodes",
                       None):
            self.records[2]["identity_status"] = status
            self.expect("mapping_record_not_verified")

    def test_findings_rejected(self):
        """Any record finding fails."""
        self.records[3]["findings"] = ["visit_link_missing"]
        self.expect("mapping_record_has_findings")

    def test_repeated_visits_rejected(self):
        """Repeated visit IDs or a flagged repeated node fail."""
        self.records[4]["repeated_visit_occurrence_ids"] = [1]
        self.expect("mapping_record_repeated_visits")
        self.records[4]["repeated_visit_occurrence_ids"] = []
        self.records[4]["visit_nodes"][0]["repeated_visit_node"] = True
        self.expect("mapping_record_repeated_visits")

    def test_unverified_visit_node_rejected(self):
        """A visit node without a verified link fails."""
        self.records[5]["visit_nodes"][0]["link_status"] = (
            "visit_link_missing")
        self.expect("mapping_visit_nodes_invalid")
        self.records[5]["visit_nodes"] = []
        self.expect("mapping_visit_nodes_invalid")

    def test_phase4_flag_rejected(self):
        """A true or missing phase4_eligible flag fails, per record or top."""
        self.records[6]["phase4_eligible"] = True
        self.expect("mapping_phase4_flag")
        del self.records[6]["phase4_eligible"]
        self.expect("mapping_phase4_flag")
        self.records[6]["phase4_eligible"] = False
        self.document["phase4_eligible"] = True
        self.expect("mapping_phase4_flag")

    def test_schema_and_pilot_label_rejected(self):
        """Another schema, version or pilot label fails."""
        for key, value in (("schema", "other"), ("schema_version", "9"),
                           ("scope", {"source_pilot_version": "other"})):
            saved = self.document[key]
            self.document[key] = value
            self.expect("mapping_schema_unexpected")
            self.document[key] = saved

    def test_pinned_identity_rejected(self):
        """A mapping built on another graph or bridge fails."""
        for role in ("graph", "bridge"):
            saved = self.document["inputs"][role]["sha256"]
            self.document["inputs"][role]["sha256"] = "0" * 64
            self.expect("mapping_pinned_identity_mismatch")
            self.document["inputs"][role]["sha256"] = saved

    def test_graph_link_rejected(self):
        """A graph_position before patient_index or a repeat fails."""
        self.records[0]["graph_position"] = 0
        self.expect("mapping_graph_link_invalid")

    def test_raw_graph_cross_check(self):
        """patient_index must match graph_position and subject_id."""
        selection = pmo.select_pilot(self.document, self.plan)
        pmo.check_selection_graphs(self.graphs, selection)
        graphs = pmo.synthetic_collection()
        graphs[selection[0]["graph_position"]].subject_id = 1
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "mapping_subject_mismatch"):
            pmo.check_selection_graphs(graphs, selection)
        graphs = pmo.synthetic_collection()
        del graphs[0].y_diagnosis
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "mapping_graph_position_mismatch"):
            pmo.check_selection_graphs(graphs, selection)
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "mapping_graph_position_mismatch"):
            pmo.check_selection_graphs(self.graphs[:5], selection)


class ComparatorTestCase(unittest.TestCase):
    """Canonical comparison: exact structure, fixed float tolerance."""

    def setUp(self):
        """Build an invented canonical prefix and an identical candidate."""
        _, _, documents = fixtures()
        self.canonical = documents["canonical_explanations"]
        self.candidate = copy.deepcopy(self.canonical)

    def compare(self):
        """Return the agreement report of candidate vs canonical."""
        return pmo.compare_canonical(self.candidate, self.canonical)

    def assert_only(self, report, code, count=1):
        """Assert one mismatch category (and no other) was counted."""
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["mismatch_counts"][code], count)
        others = {key: value for key, value in
                  report["mismatch_counts"].items() if key != code}
        self.assertFalse(any(others.values()), others)

    def test_identical_passes_with_aggregate_report_only(self):
        """Identical records pass; the report is aggregate only."""
        report = self.compare()
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["compared_records"], 3)
        self.assertGreater(report["compared_values"], 30)
        self.assertEqual(report["max_abs_error"], 0.0)
        self.assertEqual(set(report), {
            "schema", "schema_version", "status", "compared_records",
            "compared_values", "mismatch_counts", "max_abs_error",
            "tolerance"})
        text = json.dumps(report)
        for word in ("patient_index", "concept_id", "name", "score",
                     "logit", "SYNTHETIC"):
            self.assertNotIn(word, text)

    def test_float_within_tolerance_passes(self):
        """A float difference within atol + rtol * |canonical| passes."""
        self.candidate[0]["predicted_diagnosis"]["logit"] += 5e-7
        report = self.compare()
        self.assertEqual(report["status"], "passed")
        self.assertGreater(report["max_abs_error"], 0.0)

    def test_float_beyond_tolerance_fails(self):
        """A float difference beyond the fixed tolerance fails."""
        self.candidate[1]["drivers"]["drug"][0]["score"] += 1e-3
        report = self.compare()
        self.assert_only(report, "float_mismatch")
        self.assertAlmostEqual(report["max_abs_error"], 1e-3)

    def test_record_reorder_fails(self):
        """Swapped records fail on exact values."""
        self.candidate[0], self.candidate[1] = (self.candidate[1],
                                                self.candidate[0])
        report = self.compare()
        self.assertEqual(report["status"], "failed")
        self.assertGreater(report["mismatch_counts"]["value_mismatch"], 0)

    def test_driver_reorder_fails(self):
        """A reordered driver list fails."""
        self.candidate[0]["drivers"]["condition"].reverse()
        self.assertEqual(self.compare()["status"], "failed")

    def test_missing_and_extra_fields_fail(self):
        """A missing or an extra field fails."""
        del self.candidate[0]["predicted_diagnosis"]["name"]
        self.assert_only(self.compare(), "missing_field")
        self.candidate = copy.deepcopy(self.canonical)
        self.candidate[2]["drivers"]["condition"][0]["extra"] = 1
        self.assert_only(self.compare(), "extra_field")

    def test_type_mismatch_fails(self):
        """int vs float, bool vs int and null vs int are type mismatches."""
        prediction = self.candidate[0]["predicted_diagnosis"]
        prediction["label_index"] = float(prediction["label_index"])
        self.assert_only(self.compare(), "type_mismatch")
        self.candidate = copy.deepcopy(self.canonical)
        self.candidate[1]["patient_index"] = True
        self.assert_only(self.compare(), "type_mismatch")
        self.candidate = copy.deepcopy(self.canonical)
        self.candidate[0]["predicted_diagnosis"]["concept_id"] = None
        self.assert_only(self.compare(), "type_mismatch")
        self.candidate = copy.deepcopy(self.canonical)
        self.candidate[0]["drivers"] = []
        self.assert_only(self.compare(), "type_mismatch")

    def test_non_float_value_mismatch_fails(self):
        """A changed integer or string fails exactly."""
        self.candidate[0]["drivers"]["condition"][0]["vocab_index"] += 1
        self.assert_only(self.compare(), "value_mismatch")
        self.candidate = copy.deepcopy(self.canonical)
        self.candidate[2]["predicted_diagnosis"]["name"] += " "
        self.assert_only(self.compare(), "value_mismatch")

    def test_non_finite_fails(self):
        """NaN or infinity on either side fails."""
        for value in (float("nan"), float("inf")):
            self.candidate = copy.deepcopy(self.canonical)
            self.candidate[0]["predicted_diagnosis"]["logit"] = value
            self.assert_only(self.compare(), "non_finite")
        self.candidate = copy.deepcopy(self.canonical)
        canonical = copy.deepcopy(self.canonical)
        canonical[1]["predicted_diagnosis"]["probability"] = float("nan")
        report = pmo.compare_canonical(self.candidate, canonical)
        self.assert_only(report, "non_finite")

    def test_length_mismatch_fails(self):
        """A missing driver or record fails."""
        self.candidate[0]["drivers"]["procedure"].pop()
        self.assert_only(self.compare(), "length_mismatch")
        self.candidate = copy.deepcopy(self.canonical)[:2]
        self.assert_only(self.compare(), "length_mismatch")

    def test_tolerance_cannot_change(self):
        """A widened or narrowed tolerance is refused."""
        for atol, rtol in ((1e-3, 1e-6), (1e-6, 0.0), (0.0, 0.0)):
            with self.assertRaisesRegex(pmo.Step20Error,
                                        "tolerance_changed"):
                pmo.compare_canonical(self.candidate, self.canonical,
                                      atol=atol, rtol=rtol)
        with self.assertRaisesRegex(pmo.Step20Error, "settings_changed"):
            pmo.check_fixed_settings(top_k=5)
        with self.assertRaisesRegex(pmo.Step20Error, "settings_changed"):
            pmo.check_fixed_settings(device="cpu")

    def test_canonical_prefix_shape(self):
        """The canonical sample must be exactly records 0, 1, 2."""
        pmo.canonical_prefix(self.canonical)
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "canonical_prefix_unexpected"):
            pmo.canonical_prefix(self.canonical[:2])
        swapped = [self.canonical[1], self.canonical[0], self.canonical[2]]
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "canonical_prefix_unexpected"):
            pmo.canonical_prefix(swapped)
        broken = copy.deepcopy(self.canonical)
        del broken[0]["drivers"]
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "canonical_schema_invalid"):
            pmo.canonical_prefix(broken)

    def test_record_schema(self):
        """Bridge schema, finiteness, unsigned scores and top_k are kept."""
        record = copy.deepcopy(self.canonical[0])
        self.assertIsNone(pmo.schema_problem(record))
        cases = (
            (("predicted_diagnosis", "probability"), 1.5,
             "record_non_finite"),
            (("predicted_diagnosis", "logit"), float("nan"),
             "record_non_finite"),
            (("drivers", "condition", 0, "score"), -0.1,
             "record_non_finite"),
            (("drivers", "condition", 0, "concept_id"), "1",
             "record_schema_invalid"),
            (("predicted_diagnosis", "label_index"), -1,
             "record_schema_invalid"),
        )
        for path, value, code in cases:
            item = copy.deepcopy(record)
            target = item
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            self.assertEqual(pmo.schema_problem(item), code, path)
        item = copy.deepcopy(record)
        item["drivers"]["drug"] = item["drivers"]["drug"] * 4
        self.assertEqual(pmo.schema_problem(item, top_k=10),
                         "record_schema_invalid")
        item = copy.deepcopy(record)
        item["extra"] = 1
        self.assertEqual(pmo.schema_problem(item), "record_schema_invalid")
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "record_not_serialisable"):
            pmo.normalise_record({"value": object()})


class GenerationTestCase(unittest.TestCase):
    """One load, one preprocess, one model load, direct indexing, CUDA."""

    def setUp(self):
        """Build invented fixtures."""
        self.plan, _, self.documents = fixtures()

    def test_single_load_preprocess_and_model(self):
        """The union is generated once; only pilot records are returned."""
        runtime = pmo.synthetic_runtime()
        outputs, report, info = run(self.plan, runtime, self.documents)
        calls = runtime.bridge.calls
        self.assertEqual(runtime.loads,
                         [self.plan["assets"]["graph"]["path"]])
        self.assertEqual(calls[0], "preprocess")
        self.assertEqual(calls[1], "load_model:cuda")
        self.assertEqual(calls.count("preprocess"), 1)
        self.assertEqual(calls.count("load_model:cuda"), 1)
        self.assertEqual(calls[2:], ["explain"] * 12)
        self.assertEqual([record["patient_index"] for record in outputs],
                         PILOT)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(info, {"torch": "synthetic", "cuda": "synthetic"})

    def test_cuda_required_before_any_load(self):
        """No CUDA (or a failing probe) stops the run before any load."""
        broken = pmo.synthetic_torch()

        def fail():
            """Raise like a broken CUDA probe."""
            raise RuntimeError("synthetic")
        broken.cuda.is_available = fail
        for torch in (pmo.synthetic_torch(cuda=False), broken):
            runtime = pmo.synthetic_runtime(torch=torch)
            with self.assertRaisesRegex(pmo.Step20Error,
                                        "^cuda_unavailable$"):
                run(self.plan, runtime, self.documents)
            self.assertEqual(runtime.loads, [])
            self.assertEqual(runtime.bridge.calls, [])

    def test_no_cpu_fallback(self):
        """A model left on the CPU fails; CUDA is always requested."""
        bridge = pmo.SyntheticBridge(on_cuda=False)
        runtime = pmo.synthetic_runtime(bridge=bridge)
        with self.assertRaisesRegex(pmo.Step20Error, "model_not_on_cuda"):
            run(self.plan, runtime, self.documents)
        self.assertEqual(bridge.calls, ["preprocess", "load_model:cuda"])

    def test_eval_required(self):
        """A model that stays in training mode fails."""
        class Stuck(pmo.SyntheticModel):
            """Invented model whose eval() does nothing."""

            def eval(self):
                """Stay in training mode."""
                return self
        bridge = RecordingBridge(model=Stuck())
        runtime = pmo.synthetic_runtime(bridge=bridge)
        with self.assertRaisesRegex(pmo.Step20Error, "model_not_eval"):
            run(self.plan, runtime, self.documents)

    def test_step19_parity_record_enforced(self):
        """Another digest, count or status in the Step 19 record fails."""
        cases = ((("model_input_digest",), "f" * 64,
                  "preprocess_digest_mismatch"),
                 (("counts", "graphs_filtered"), 13,
                  "preprocess_count_mismatch"),
                 (("status",), "failed", "step19_summary_unexpected"),
                 (("phase4_eligible",), True, "step19_summary_unexpected"),
                 (("graph_sha256",), "0" * 64, "step19_summary_unexpected"))
        for path, value, code in cases:
            documents = copy.deepcopy(self.documents)
            target = documents["step19_summary"]
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = value
            with self.assertRaisesRegex(pmo.Step20Error, code):
                run(self.plan, pmo.synthetic_runtime(), documents)

    def test_agreement_failure_raises_aggregate(self):
        """A canonical mismatch raises with the aggregate report only."""
        documents = copy.deepcopy(self.documents)
        documents["canonical_explanations"][2]["predicted_diagnosis"][
            "logit"] += 1e-3
        with self.assertRaises(pmo.AgreementError) as caught:
            run(self.plan, pmo.synthetic_runtime(), documents)
        self.assertEqual(str(caught.exception), "canonical_agreement_failed")
        self.assertEqual(
            caught.exception.report["mismatch_counts"]["float_mismatch"], 1)

    def test_record_index_mismatch(self):
        """A record for another patient_index fails."""
        class Shifted(pmo.SyntheticBridge):
            """Invented bridge reporting the wrong index."""

            def explain(self, model, graph, patient_index, top_k, device):
                """Return a record for patient_index + 1."""
                return pmo.synthetic_record(graph, patient_index + 1, top_k)
        runtime = pmo.synthetic_runtime(bridge=Shifted())
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "record_index_mismatch"):
            run(self.plan, runtime, self.documents)


class IsolationTestCase(unittest.TestCase):
    """Silencing, network, tracker and training/ETL/DataLoader tripwires."""

    def setUp(self):
        """Build invented fixtures."""
        self.plan, _, self.documents = fixtures()

    def capture_fds(self):
        """Point fds 1/2 at temp files; return (files, restore function)."""
        for stream in (sys.stdout, sys.stderr):
            stream.flush()
        saved = {fd: os.dup(fd) for fd in (1, 2)}
        files = {fd: tempfile.TemporaryFile() for fd in (1, 2)}
        for fd, handle in files.items():
            os.dup2(handle.fileno(), fd)

        def restore():
            """Put the original fds 1/2 back and close the copies."""
            for fd, copy_fd in saved.items():
                os.dup2(copy_fd, fd)
                os.close(copy_fd)
        return files, restore

    @staticmethod
    def read(handle):
        """Return everything written to a temp capture file."""
        handle.seek(0)
        return handle.read().decode("utf-8", "replace")

    def test_silenced_python_logging_and_fds(self):
        """Nothing escapes on any channel; state is restored afterwards."""
        root = logging.getLogger()
        buffer = io.StringIO()
        handler = logging.StreamHandler(buffer)
        root.addHandler(handler)
        self.addCleanup(root.removeHandler, handler)
        saved_disable = logging.root.manager.disable
        files, restore = self.capture_fds()
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                with pmo.silenced():
                    print(MARKER)
                    print(MARKER, file=sys.stderr)
                    logging.getLogger("synthetic").critical(MARKER)
                    os.write(1, MARKER.encode() + b"\n")
                    os.write(2, MARKER.encode() + b"\n")
                self.assertIs(sys.stdout, out)
                self.assertIs(sys.stderr, err)
                print("after-print")
                os.write(1, b"after-fd\n")
        finally:
            restore()
        texts = [out.getvalue(), err.getvalue(), buffer.getvalue(),
                 self.read(files[1]), self.read(files[2])]
        for handle in files.values():
            handle.close()
        for text in texts:
            self.assertNotIn(MARKER, text)
        self.assertIn("after-print", texts[0])
        self.assertIn("after-fd", texts[3])
        self.assertEqual(logging.root.manager.disable, saved_disable)
        self.assertIn(handler, root.handlers)

    def test_silenced_restores_on_failure(self):
        """Streams, fds, disable level and root logger return on error."""
        root = logging.getLogger()
        saved = (sys.stdout, sys.stderr, logging.root.manager.disable,
                 list(root.handlers), root.level, root.disabled)
        files, restore = self.capture_fds()
        try:
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                with pmo.silenced():
                    logging.basicConfig(stream=sys.stdout)
                    root.setLevel(logging.DEBUG)
                    root.disabled = True
                    raise RuntimeError("synthetic")
            os.write(1, b"restored-fd\n")
        finally:
            restore()
        self.assertIn("restored-fd", self.read(files[1]))
        for handle in files.values():
            handle.close()
        self.assertEqual(
            (sys.stdout, sys.stderr, logging.root.manager.disable,
             list(root.handlers), root.level, root.disabled), saved)

    def test_run_output_silenced(self):
        """Bridge prints and logs during a run never reach any channel."""
        def noisy(bridge, model):
            """Print and log the marker on every channel."""
            print(MARKER)
            print(MARKER, file=sys.stderr)
            logging.getLogger("synthetic").error(MARKER)
        runtime = pmo.synthetic_runtime(bridge=RecordingBridge(noisy))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            run(self.plan, runtime, self.documents)
        self.assertNotIn(MARKER, out.getvalue() + err.getvalue())

    def test_network_refused_and_restored(self):
        """Connections are refused inside isolation; sockets restored."""
        original = socket.create_connection
        trips = pmo.Tripwires()
        with pmo.network_blocked(trips):
            with self.assertRaisesRegex(pmo.Step20Error, "network_refused"):
                socket.create_connection(("127.0.0.1", 9))
            with self.assertRaisesRegex(pmo.Step20Error, "network_refused"):
                socket.getaddrinfo("localhost", 9)
        self.assertIs(socket.create_connection, original)

    def test_swallowed_network_attempt_still_fails(self):
        """A refused connection caught by upstream code still fails."""
        def sneaky(bridge, model):
            """Attempt a connection and swallow the refusal."""
            try:
                socket.create_connection(("127.0.0.1", 9))
            except Exception:  # noqa: BLE001 - simulating upstream code
                pass
        runtime = pmo.synthetic_runtime(bridge=RecordingBridge(sneaky))
        with self.assertRaisesRegex(pmo.Step20Error, "^network_refused$"):
            run(self.plan, runtime, self.documents)

    def test_tracker_offline_and_restored(self):
        """The tracker is offline during the run; env is restored."""
        seen = []

        def record(bridge, model):
            """Record the tracker mode seen inside the run."""
            seen.append(os.environ.get("CLEARML_OFFLINE_MODE"))
        with mock.patch.dict(os.environ, {"CLEARML_OFFLINE_MODE": "0"}):
            run(self.plan, pmo.synthetic_runtime(
                bridge=RecordingBridge(record)), self.documents)
            self.assertEqual(os.environ["CLEARML_OFFLINE_MODE"], "0")
        self.assertEqual(set(seen), {"1"})
        with mock.patch.dict(os.environ):
            os.environ.pop("CLEARML_OFFLINE_MODE", None)
            with pmo.tracker_offline():
                self.assertEqual(os.environ["CLEARML_OFFLINE_MODE"], "1")
            self.assertNotIn("CLEARML_OFFLINE_MODE", os.environ)

    def test_forbidden_imports_blocked(self):
        """Training, ETL/builder and tracker imports are refused."""
        cases = (("train", "training"), ("build_patient_graphs", "etl"),
                 ("wandb", "tracker"), ("mlflow", "tracker"))
        for name, category in cases:
            self.assertNotIn(name, sys.modules)
            trips = pmo.Tripwires()
            with pmo.imports_blocked(trips):
                with self.assertRaises(ImportError):
                    importlib.import_module(name)
            self.assertEqual(trips.hits, ["forbidden_import:%s" % category])
        self.assertFalse(any(isinstance(item, pmo._ImportBlocker)
                             for item in sys.meta_path))
        for name, category in (("src.gnn.train", "training"),
                               ("src.preprocess.build_patient_graphs",
                                "etl"),
                               ("src.etl", "etl")):
            trips = pmo.Tripwires()
            with self.assertRaises(ImportError):
                pmo._ImportBlocker(trips).find_spec(name)
            self.assertEqual(trips.hits, ["forbidden_import:%s" % category])
        self.assertIsNone(pmo.forbidden_category("json"))
        self.assertIsNone(pmo.forbidden_category("trainer"))

    def test_forbidden_preloaded_refused(self):
        """A forbidden module already imported refuses the real run."""
        self.assertEqual(
            pmo.forbidden_preloaded_problem({"json": 1, "train": 1}),
            "forbidden_module_preloaded")
        self.assertIsNone(pmo.forbidden_preloaded_problem({"json": 1}))

    def test_swallowed_training_import_still_fails(self):
        """A bridge that imports train and swallows the error fails."""
        bridge = pmo.SyntheticBridge()

        def factory(plan, bound):
            """Try the forbidden import like an optional upstream import."""
            try:
                importlib.import_module("train")
            except ImportError:
                pass
            return bridge
        runtime = pmo.synthetic_runtime(bridge=bridge)
        runtime.bridge_factory = factory
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "^forbidden_import:training$"):
            run(self.plan, runtime, self.documents)
        self.assertEqual(runtime.loads, [])

    def test_dataloader_training_and_optimizer_tripwires(self):
        """DataLoader, train(True) and optimisers are refused; eval works."""
        torch = pmo.synthetic_torch()
        trips = pmo.Tripwires()
        model = pmo.SyntheticModel()
        with pmo.torch_tripwires(torch, trips):
            with self.assertRaisesRegex(pmo.Step20Error, "dataloader_used"):
                torch.utils.data.DataLoader([])
            with self.assertRaisesRegex(pmo.Step20Error,
                                        "training_mode_used"):
                model.train()
            model.eval()
            self.assertFalse(model.training)
            with self.assertRaisesRegex(pmo.Step20Error,
                                        "optimizer_created"):
                torch.optim.Optimizer([])
        self.assertEqual(trips.hits, ["dataloader_used",
                                      "training_mode_used",
                                      "optimizer_created"])
        torch.utils.data.DataLoader([])
        torch.optim.Optimizer([])
        self.assertTrue(model.train().training)

    def test_dataloader_or_training_in_run_fails(self):
        """A bridge that builds a DataLoader or trains fails the run."""
        def loader(bridge, model):
            """Build a DataLoader inside explain()."""
            pmo.SyntheticDataLoader([])

        def trainer(bridge, model):
            """Switch the model into training mode inside explain()."""
            model.train()
        for hook, code in ((loader, "dataloader_used"),
                           (trainer, "training_mode_used")):
            runtime = pmo.synthetic_runtime(bridge=RecordingBridge(hook))
            with self.assertRaisesRegex(pmo.Step20Error, "^%s$" % code):
                run(self.plan, runtime, self.documents)


class ScratchTestCase(unittest.TestCase):
    """Base case with a workspace scratch directory and pinned invented files."""

    def setUp(self):
        """Create invented pinned files in a workspace scratch directory."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT, True),
                        "test scratch space must stay in the workspace root")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="step-20-", dir=root)
        self.addCleanup(self.remove_scratch)
        self.plan = self.build_plan()

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def put(self, role, relative, data, sized=True):
        """Write an invented pinned file and record its identity."""
        path = os.path.join(self.work, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        entry = {"path": path, "sha256": hashlib.sha256(data).hexdigest()}
        if sized:
            entry["size_bytes"] = len(data)
        self.assets[role] = entry

    def build_plan(self):
        """Return a plan pinned to invented upstream and protected files."""
        self.assets = {}
        self.put("explain_patient", "upstream\\src\\gnn\\explain_patient.py",
                 b"# synthetic bridge\n")
        self.put("model", "upstream\\src\\gnn\\model.py",
                 b"# synthetic model\n")
        self.put("graph", "upstream\\data\\graphs.pt", b"synthetic graphs")
        self.put("checkpoint", "upstream\\data\\ckpt.pt", b"synthetic ckpt")
        self.put("concept_vocab", "upstream\\data\\concepts.json", b"{}")
        self.put("label_vocab", "upstream\\data\\labels.json", b"[]")
        self.put("documentation", "upstream\\doc.md", b"# synthetic\n")
        self.put("kg_concepts", "protected\\kg.csv", b"concept_id\n1\n",
                 sized=False)
        plan = {"upstream_root": os.path.join(self.work, "upstream"),
                "assets": self.assets,
                "output_dir": os.path.join(self.work, "protected",
                                           "model-outputs", "step-20-test"),
                "protected_root": os.path.join(self.work, "protected")}
        documents = pmo.synthetic_documents(plan,
                                            pmo.synthetic_collection())
        self.put("step17_mapping", "protected\\mapping.json",
                 json.dumps(documents["step17_mapping"]).encode())
        self.put("canonical_explanations",
                 "upstream\\data\\runs\\explanations.json",
                 json.dumps(documents["canonical_explanations"]).encode())
        self.put("step19_summary", "protected\\summary.json",
                 json.dumps(documents["step19_summary"]).encode(),
                 sized=False)
        return plan

    def file_sha(self, role):
        """Return the current SHA-256 of a pinned invented file."""
        return mapping.sha256_file(self.plan["assets"][role]["path"])

    def assert_nothing_published(self):
        """Assert no final or partial output directory exists."""
        for path in (self.plan["output_dir"],
                     self.plan["output_dir"] + pmo.PARTIAL):
            self.assertFalse(os.path.lexists(path))


class PublicationTestCase(ScratchTestCase):
    """Pinned identities, mutation checks and atomic publication."""

    def test_publishes_three_files_atomically(self):
        """A passing run publishes outputs, manifest and agreement only."""
        canonical_sha = self.file_sha("canonical_explanations")
        runtime = pmo.synthetic_runtime()
        summary = pmo.prepare_outputs(self.plan, runtime)
        out = self.plan["output_dir"]
        self.assertEqual(sorted(os.listdir(out)), sorted(
            [pmo.OUTPUTS_NAME, pmo.MANIFEST_NAME, pmo.AGREEMENT_NAME]))
        self.assertFalse(os.path.lexists(out + pmo.PARTIAL))
        self.assertEqual(self.file_sha("canonical_explanations"),
                         canonical_sha)
        texts = {}
        for name in os.listdir(out):
            with open(os.path.join(out, name), "rb") as handle:
                data = handle.read()
            texts[name] = data.decode("utf-8")
            if name != pmo.MANIFEST_NAME:
                self.assertEqual(summary["digests"][name],
                                 hashlib.sha256(data).hexdigest())
        outputs = json.loads(texts[pmo.OUTPUTS_NAME])
        self.assertEqual([item["patient_index"] for item in outputs], PILOT)
        for record in outputs:
            self.assertIsNone(pmo.schema_problem(record))
            self.assertEqual(list(record), ["patient_index",
                                            "predicted_diagnosis",
                                            "drivers"])
        manifest = json.loads(texts[pmo.MANIFEST_NAME])
        self.assertEqual(
            manifest["files"][pmo.OUTPUTS_NAME]["sha256"],
            summary["digests"][pmo.OUTPUTS_NAME])
        agreement = json.loads(texts[pmo.AGREEMENT_NAME])
        self.assertEqual(agreement["status"], "passed")
        invented_ids = [str(base + position) for base in
                        (pmo.SYNTHETIC_SUBJECT_ID_BASE,
                         pmo.SYNTHETIC_PERSON_ID_BASE,
                         pmo.SYNTHETIC_VISIT_ID_BASE)
                        for position in range(16)]
        for name in (pmo.MANIFEST_NAME, pmo.AGREEMENT_NAME):
            for word in ["patient_index", "subject_id", "person_id",
                         "hadm_id", "visit_occurrence_id",
                         "SYNTHETIC"] + invented_ids:
                self.assertNotIn(word, texts[name], name)

    def test_verified_vocabularies_reach_model_load(self):
        """Only the verified, parsed vocabularies are handed to the bridge."""
        bridge = RecordingBridge()
        pmo.prepare_outputs(self.plan, pmo.synthetic_runtime(bridge=bridge))
        self.assertEqual(bridge.seen, [{"label_vocab": [],
                                        "concept_vocab": {}}])

    def test_manifest_limitations(self):
        """Phase, history, label and input limitations are explicit."""
        pmo.prepare_outputs(self.plan, pmo.synthetic_runtime())
        path = os.path.join(self.plan["output_dir"], pmo.MANIFEST_NAME)
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
        self.assertIs(manifest["phase4_eligible"], False)
        self.assertEqual(manifest["label_index_correspondence"],
                         "not_established")
        self.assertEqual(manifest["source_event_linkage"], "absent")
        self.assertIs(manifest["history_only_claim_allowed"], False)
        self.assertEqual(manifest["upstream_split_membership"], "unknown")
        self.assertIs(manifest["pre_target_only_input"], False)
        self.assertIs(manifest["derived_evidence_attached"], False)
        self.assertEqual(manifest["target_visit_inputs_remaining"], [
            "final-visit visit.x features", "final-visit procedure edges",
            "final-visit drug edges"])
        self.assertEqual(manifest["settings"]["top_k"], 10)
        self.assertEqual(manifest["settings"]["device"], "cuda")
        self.assertIs(manifest["settings"]["cpu_fallback"], False)
        self.assertEqual(manifest["settings"]["tolerance"],
                         {"atol": 1e-6, "rtol": 1e-6})
        self.assertEqual(set(manifest["assets"]), set(self.assets))
        for key in ("history", "target_visit", "events", "records"):
            self.assertNotIn(key, manifest)

    def test_no_overwrite(self):
        """An existing final or partial directory is refused."""
        pmo.prepare_outputs(self.plan, pmo.synthetic_runtime())
        runtime = pmo.synthetic_runtime()
        with self.assertRaisesRegex(pmo.Step20Error, "^output_exists$"):
            pmo.prepare_outputs(self.plan, runtime)
        self.assertEqual(runtime.loads, [])
        self.plan["output_dir"] += "-second"
        os.makedirs(self.plan["output_dir"] + pmo.PARTIAL)
        with self.assertRaisesRegex(pmo.Step20Error, "^output_exists$"):
            pmo.prepare_outputs(self.plan, pmo.synthetic_runtime())
        self.assertFalse(os.path.lexists(self.plan["output_dir"]))

    def test_agreement_failure_publishes_nothing(self):
        """A canonical disagreement publishes nothing."""
        path = self.plan["assets"]["canonical_explanations"]["path"]
        with open(path, "r", encoding="utf-8") as handle:
            canonical = json.load(handle)
        canonical[0]["drivers"]["condition"][0]["score"] += 1e-3
        data = json.dumps(canonical).encode()
        with open(path, "wb") as handle:
            handle.write(data)
        self.assets["canonical_explanations"].update(
            size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        with self.assertRaises(pmo.AgreementError):
            pmo.prepare_outputs(self.plan, pmo.synthetic_runtime())
        self.assert_nothing_published()

    def test_output_boundaries(self):
        """Upstream/canonical and unprotected outputs are refused."""
        canonical = self.plan["assets"]["canonical_explanations"]["path"]
        for target in (os.path.join(self.plan["upstream_root"], "out"),
                       os.path.dirname(canonical)):
            self.plan["output_dir"] = target
            with self.assertRaisesRegex(pmo.Step20Error,
                                        "^output_is_upstream$"):
                pmo.output_preconditions(self.plan)
        self.plan["output_dir"] = os.path.join(self.work, "elsewhere", "out")
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "^output_not_protected$"):
            pmo.output_preconditions(self.plan)

    def test_hash_mismatch_fails_before_any_load(self):
        """Every pinned role's hash is checked before deserialisation."""
        for role in sorted(self.assets):
            saved = self.assets[role]["sha256"]
            self.assets[role]["sha256"] = "0" * 64
            runtime = pmo.synthetic_runtime()
            with self.assertRaisesRegex(
                    pmo.Step20Error,
                    "^input_identity:input_hash_mismatch:%s$" % role):
                pmo.prepare_outputs(self.plan, runtime)
            self.assertEqual(runtime.loads, [], role)
            self.assets[role]["sha256"] = saved
        self.assert_nothing_published()

    def test_malformed_pin_fails_before_any_read(self):
        """A pinned SHA-256 that is not 64 hex digits refuses to start."""
        self.assertIsNone(pmo.pinned_hash_problem(self.plan))
        for bad in ("a" * 65, "a" * 63, "g" * 64, None):
            self.assets["kg_concepts"]["sha256"] = bad
            runtime = pmo.synthetic_runtime()
            with self.assertRaisesRegex(
                    pmo.Step20Error, "^pinned_hash_malformed:kg_concepts$"):
                pmo.prepare_outputs(self.plan, runtime)
            self.assertEqual(runtime.loads, [])
        self.assert_nothing_published()

    def test_size_mismatch_fails(self):
        """A pinned size mismatch fails before any load."""
        self.assets["checkpoint"]["size_bytes"] += 1
        runtime = pmo.synthetic_runtime()
        with self.assertRaisesRegex(
                pmo.Step20Error,
                "input_identity:input_size_mismatch:checkpoint"):
            pmo.prepare_outputs(self.plan, runtime)
        self.assertEqual(runtime.loads, [])

    def test_path_outside_policy_anchor_fails(self):
        """A pinned input outside the workspace is refused before any load."""
        self.assets["graph"]["path"] = "C:\\synthetic\\graphs.pt"
        runtime = pmo.synthetic_runtime()
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "input_identity:input_path_rejected"):
            pmo.prepare_outputs(self.plan, runtime)
        self.assertEqual(runtime.loads, [])

    def test_reparse_point_fails(self):
        """A pinned input reported as a reparse point is refused."""
        target = self.assets["checkpoint"]["path"]
        real_lstat = os.lstat

        def fake_lstat(path, *args, **kwargs):
            """Report the checkpoint as a reparse point."""
            info = real_lstat(path, *args, **kwargs)
            if os.path.normcase(str(path)) == os.path.normcase(target):
                return types.SimpleNamespace(
                    st_mode=info.st_mode, st_size=info.st_size,
                    st_mtime_ns=info.st_mtime_ns,
                    st_file_attributes=guard.REPARSE_POINT)
            return info
        runtime = pmo.synthetic_runtime()
        with mock.patch("os.lstat", fake_lstat):
            with self.assertRaisesRegex(
                    pmo.Step20Error, "input_identity:input_link_rejected"):
                pmo.prepare_outputs(self.plan, runtime)
        self.assertEqual(runtime.loads, [])

    def test_graph_mutated_during_load_fails(self):
        """A pinned input changed by the load fails before publication."""
        runtime = pmo.synthetic_runtime()
        path = self.assets["graph"]["path"]

        def mutating_loader(graph_path):
            """Append to the pinned graph while loading it."""
            with open(path, "ab") as handle:
                handle.write(b"changed")
            return pmo.synthetic_collection()
        runtime.graph_loader = mutating_loader
        with self.assertRaisesRegex(
                pmo.Step20Error,
                "input_identity:input_size_mismatch:graph"):
            pmo.prepare_outputs(self.plan, runtime)
        self.assertEqual(runtime.bridge.calls, [])
        self.assert_nothing_published()

    def test_same_size_restored_mtime_mutation_fails_before_load(self):
        """A same-size rewrite cannot evade the bound graph hash."""
        runtime = pmo.synthetic_runtime()
        bridge = runtime.bridge
        path = self.assets["graph"]["path"]
        before = os.stat(path)

        def mutating_factory(plan, bound):
            """Rewrite the graph, preserving its size and modification time."""
            with open(path, "r+b") as handle:
                handle.write(b"X" * before.st_size)
                handle.flush()
                os.fsync(handle.fileno())
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            after = os.stat(path)
            self.assertEqual(after.st_size, before.st_size)
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
            return bridge

        runtime.bridge_factory = mutating_factory
        with self.assertRaisesRegex(
                pmo.Step20Error,
                "^input_identity:input_hash_mismatch:graph$"):
            pmo.prepare_outputs(self.plan, runtime)
        self.assertEqual(runtime.loads, [])
        self.assert_nothing_published()

    def test_input_mutated_after_generation_fails(self):
        """A pinned input changed during generation fails afterwards."""
        path = self.assets["step17_mapping"]["path"]

        def touch(bridge, model):
            """Change the mapping's modification time on the last call."""
            if bridge.calls.count("explain") == 11:
                info = os.stat(path)
                os.utime(path, ns=(info.st_atime_ns,
                                   info.st_mtime_ns + 10 ** 9))
        runtime = pmo.synthetic_runtime(bridge=RecordingBridge(touch))
        with self.assertRaisesRegex(
                pmo.Step20Error,
                "input_identity:input_changed_during_read:step17_mapping"):
            pmo.prepare_outputs(self.plan, runtime)
        self.assert_nothing_published()

    def test_unpinned_upstream_import_fails(self):
        """Loading any upstream module but the pinned two fails."""
        helper = os.path.join(self.plan["upstream_root"], "helper.py")
        with open(helper, "w", encoding="utf-8") as handle:
            handle.write("VALUE = 1\n")
        name = "step20_synthetic_unpinned_helper"
        self.addCleanup(sys.modules.pop, name, None)
        bridge = pmo.SyntheticBridge()

        def factory(plan, bound):
            """Import an unpinned invented upstream module."""
            spec = importlib.util.spec_from_file_location(name, helper)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sys.modules[name] = module
            return bridge
        runtime = pmo.synthetic_runtime(bridge=bridge)
        runtime.bridge_factory = factory
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "^unpinned_upstream_import$"):
            pmo.prepare_outputs(self.plan, runtime)
        self.assertEqual(runtime.loads, [])
        pinned = types.ModuleType("step20_synthetic_pinned")
        pinned.__file__ = self.assets["model"]["path"]
        sys.modules.pop(name)
        sys.modules[pinned.__name__] = pinned
        self.addCleanup(sys.modules.pop, pinned.__name__, None)
        trips = pmo.Tripwires()
        pmo.check_upstream_modules(self.plan, trips)
        self.assertEqual(trips.hits, [])


class FakeScalar:
    """Invented 0-d tensor stand-in holding one Python number."""

    def __init__(self, value):
        """Keep the invented value."""
        self.value = value

    def item(self):
        """Return the invented value, like Tensor.item()."""
        return self.value


class FakeLogits:
    """Invented 1-d logits stand-in indexed like a tensor."""

    def __init__(self, values):
        """Keep the invented logit values."""
        self.values = list(values)

    def __getitem__(self, index):
        """Return one logit as a scalar stand-in."""
        return FakeScalar(self.values[index])


class FakeGraph:
    """Invented preprocessed graph recording its device moves."""

    def __init__(self, log):
        """Share the call log."""
        self.log = log

    def to(self, device):
        """Record the move and return an invented device-side graph."""
        self.log.append(("graph.to", device))
        return ("device_graph", device)


class FakeTorch:
    """Invented torch stand-in recording load, no_grad and math calls."""

    def __init__(self, log):
        """Share the call log; start outside no_grad."""
        self.log = log
        self.grad_disabled = False

    def device(self, name):
        """Return an invented device object."""
        return ("device", name)

    def load(self, source, map_location=None, weights_only=None):
        """Record the checkpoint load and return an invented checkpoint."""
        self.log.append(("torch.load", getattr(source, "name", source),
                         map_location, weights_only))
        return {"args": {"synthetic": True}, "model_state": {}}

    @contextlib.contextmanager
    def no_grad(self):
        """Mark gradients disabled inside the block."""
        self.grad_disabled = True
        try:
            yield
        finally:
            self.grad_disabled = False

    def argmax(self, logits):
        """Return the index of the largest invented logit."""
        values = logits.values
        return FakeScalar(values.index(max(values)))

    def sigmoid(self, scalar):
        """Return the logistic function of an invented scalar."""
        return FakeScalar(1.0 / (1.0 + math.exp(-scalar.value)))


class FakeModel:
    """Invented model returning fixed logits and a fixed explanation."""

    def __init__(self, torch, log, logits, explanation):
        """Keep the invented outputs and the shared log."""
        self.torch = torch
        self.log = log
        self.logits = logits
        self.explanation = explanation

    def __call__(self, graph, task=None):
        """Return invented diagnosis logits; record no_grad state."""
        self.log.append(("forward", graph, task, self.torch.grad_disabled))
        return {"diagnosis": [FakeLogits(self.logits)]}

    def explain(self, graph, label_idx=None, task=None, top_k=None):
        """Return the invented per-domain (vocab_index, score) pairs."""
        self.log.append(("explain", graph, label_idx, task, top_k,
                         self.torch.grad_disabled))
        return self.explanation


class FakeVerifiedStream:
    """Invented verified stream that gives loaders one named open handle."""

    def __init__(self, path):
        """Keep the display path used by call-flow assertions."""
        self.path = path

    def load(self, loader, *args, **kwargs):
        """Call the loader with a seekable in-memory binary handle."""
        handle = io.BytesIO(b"SYNTHETIC CHECKPOINT")
        handle.name = self.path
        return loader(handle, *args, **kwargs)


def fake_bridge_module(log, model):
    """Return an invented explain_patient module recording its calls."""
    def vocab_size(concept_vocab, domain):
        """Return an invented size for a domain."""
        log.append(("_vocab_size", id(concept_vocab), domain))
        return 100 + len(domain)

    def invert(concept_vocab, domain):
        """Return the invented inverse map of one domain."""
        log.append(("_invert", id(concept_vocab), domain))
        return {int(index): int(concept)
                for concept, index in concept_vocab[domain].items()}

    def load_concept_names(path):
        """Return invented concept names."""
        with open(path, "r", encoding="utf-8") as handle:
            if handle.read() != "concept_id,concept_name\n":
                raise AssertionError("concept helper did not receive bytes")
        log.append(("load_concept_names", path))
        return {990000101: "SYNTHETIC CONDITION A",
                990000201: "SYNTHETIC PROCEDURE A",
                990000500: "SYNTHETIC LABEL B"}

    def build_model(ckpt, num_labels, vocab_sizes, device):
        """Record the build arguments and return the invented model."""
        sizes = {name: vocab_sizes[name] for name in ("cond", "proc", "drug")}
        log.append(("build_model", ckpt, num_labels, dict(vocab_sizes),
                    device))
        if sizes != vocab_sizes:
            raise AssertionError("unexpected vocabulary-size keys")
        return model
    return types.SimpleNamespace(
        preprocess=lambda graphs: ["kept"], _vocab_size=vocab_size,
        _invert=invert, load_concept_names=load_concept_names,
        build_model=build_model)


class PinnedBridgeTestCase(unittest.TestCase):
    """The confirmed bridge entry points, wired with invented stand-ins."""

    LABEL_VOCAB = [990000400, 990000500, 990000600]
    CONCEPT_VOCAB = {"condition": {"990000101": 1, "990000102": 2},
                     "procedure": {"990000201": 1},
                     "drug": {}}
    EXPLANATION = {"logit": -7.25, "probability": 0.125,
                   "condition": [(1, 0.5), (3, 0.25)],
                   "procedure": [(1, 0.125)],
                   "drug": [(4, 0.0625)]}

    def setUp(self):
        """Build an invented plan, torch, model and bridge module."""
        self.log = []
        self.torch = FakeTorch(self.log)
        self.model = FakeModel(self.torch, self.log, [0.5, 1.5, -2.0],
                               self.EXPLANATION)
        self.plan = pmo.synthetic_plan()
        self.verified = {
            "bytes": {"kg_concepts": b"concept_id,concept_name\n"},
            "streams": {"checkpoint": FakeVerifiedStream(
                self.plan["assets"]["checkpoint"]["path"])}}
        self.bridge = pmo.PinnedBridge(
            fake_bridge_module(self.log, self.model), self.plan, self.torch,
            self.verified)
        self.vocabularies = {"label_vocab": self.LABEL_VOCAB,
                             "concept_vocab": self.CONCEPT_VOCAB}

    def test_preprocess_is_delegated(self):
        """preprocess is the pinned full-collection function."""
        self.assertEqual(self.bridge.preprocess([]), ["kept"])

    def test_load_model_call_flow(self):
        """Vocab helpers, names, torch.load and build_model are wired."""
        model = self.bridge.load_model("cuda", self.vocabularies)
        self.assertIs(model, self.model)
        vocab_id = id(self.CONCEPT_VOCAB)
        device = ("device", "cuda")
        self.assertEqual(self.log, [
            ("_vocab_size", vocab_id, "condition"),
            ("_vocab_size", vocab_id, "procedure"),
            ("_vocab_size", vocab_id, "drug"),
            ("_invert", vocab_id, "condition"),
            ("_invert", vocab_id, "procedure"),
            ("_invert", vocab_id, "drug"),
            ("load_concept_names", self.plan["assets"]["kg_concepts"]["path"]),
            ("torch.load", self.plan["assets"]["checkpoint"]["path"], device,
             False),
             ("build_model", {"args": {"synthetic": True}, "model_state": {}},
              3, {"cond": 109, "proc": 109, "drug": 104}, device)])

    def test_explain_before_load_refused(self):
        """A record cannot be produced before the model is loaded."""
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "^bridge_model_not_loaded$"):
            self.bridge.explain(self.model, FakeGraph(self.log), 5, 10,
                                "cuda")

    def test_explain_call_flow_and_exact_record(self):
        """Forward under no_grad, argmax, lookup and explain; exact record."""
        model = self.bridge.load_model("cuda", self.vocabularies)
        del self.log[:]
        record = self.bridge.explain(model, FakeGraph(self.log), 5, 10,
                                     "cuda")
        device = ("device", "cuda")
        moved = ("device_graph", device)
        self.assertEqual(self.log, [
            ("graph.to", device),
            ("forward", moved, "diagnosis", True),
            ("explain", moved, 1, "diagnosis", 10, False)])
        expected = {
            "patient_index": 5,
            "predicted_diagnosis": {
                "label_index": 1, "concept_id": 990000500,
                "name": "SYNTHETIC LABEL B", "logit": -7.25,
                "probability": 0.125},
            "drivers": {
                "condition": [
                    {"vocab_index": 1, "concept_id": 990000101,
                     "name": "SYNTHETIC CONDITION A", "score": 0.5},
                    {"vocab_index": 3, "concept_id": None, "name": "",
                     "score": 0.25}],
                "procedure": [
                    {"vocab_index": 1, "concept_id": 990000201,
                     "name": "SYNTHETIC PROCEDURE A", "score": 0.125}],
                "drug": [
                    {"vocab_index": 4, "concept_id": None, "name": "",
                     "score": 0.0625}]}}
        self.assertEqual(record, expected)
        self.assertEqual(list(record), ["patient_index",
                                        "predicted_diagnosis", "drivers"])
        self.assertEqual(list(record["predicted_diagnosis"]),
                         ["label_index", "concept_id", "name", "logit",
                          "probability"])
        self.assertEqual(list(record["drivers"]), list(pmo.DOMAINS))
        for entries in record["drivers"].values():
            for entry in entries:
                self.assertEqual(list(entry), ["vocab_index", "concept_id",
                                               "name", "score"])
        self.assertIsNone(pmo.schema_problem(pmo.normalise_record(record)))

    def test_package_keeps_returned_order_and_values(self):
        """Driver order and scores are kept exactly as returned."""
        explanation = {"condition": [(2, 0.1), (1, 0.9)], "procedure": [],
                       "drug": []}
        inverse = {"condition": {1: 990000101}, "procedure": {}, "drug": {}}
        record = pmo.package_record(7, 0, 990000400, -0.25, 0.4, explanation,
                                    inverse, {})
        self.assertEqual(
            [(item["vocab_index"], item["score"], item["name"])
             for item in record["drivers"]["condition"]],
            [(2, 0.1, ""), (1, 0.9, "")])
        self.assertEqual(record["predicted_diagnosis"]["name"], "")

    def test_unexpected_explanation_refused(self):
        """A missing domain or non-mapping explanation fails closed."""
        inverse = {domain: {} for domain in pmo.DOMAINS}
        for explanation in ({"condition": [], "procedure": []}, [], None):
            with self.assertRaisesRegex(pmo.Step20Error,
                                        "^bridge_explanation_unexpected$"):
                pmo.package_record(0, 0, 1, 0.0, 0.5, explanation, inverse,
                                   {})

    def test_generation_through_pinned_adapter(self):
        """The generation run drives the adapter for every union record."""
        plan, _, documents = fixtures()
        documents = dict(documents, **self.vocabularies)
        records = {}
        torch = pmo.synthetic_torch()
        adapter = self.bridge
        calls = []

        class Wrapper:
            """Invented bridge: real adapter plus a canonical-safe record."""

            def preprocess(self, graphs):
                """Use the invented reference preprocessing."""
                return pmo.parity.synthetic_reference_preprocess(graphs)

            def load_model(self, device, vocabularies):
                """Load through the adapter; return an invented CUDA model."""
                calls.append(vocabularies)
                adapter.load_model(device, vocabularies)
                return pmo.SyntheticModel()

            def explain(self, model, graph, patient_index, top_k, device):
                """Record the index; return the invented canonical record."""
                records[patient_index] = top_k
                return pmo.synthetic_record(graph, patient_index, top_k)
        runtime = pmo.synthetic_runtime(bridge=Wrapper(), torch=torch)
        outputs, report, _ = run(plan, runtime, documents)
        self.assertEqual(calls, [self.vocabularies])
        self.assertEqual(sorted(records), sorted(set(PILOT) | {0, 1, 2}))
        self.assertEqual(set(records.values()), {10})
        self.assertEqual(report["status"], "passed")
        self.assertEqual(len(outputs), 10)


class ModesTestCase(unittest.TestCase):
    """Default synthetic mode, the closed real gate and the pinned plan."""

    LINE = re.compile(r"^(status: passed|pilot_records: \d+|"
                      r"compared_records: \d+|compared_values: \d+|"
                      r"mismatch [a-z_]+: \d+)$")

    def forbid_protected_reads(self):
        """Make any guarded file access fail the test."""
        def refuse(*args, **kwargs):
            """Fail if a protected or pinned file is touched."""
            raise AssertionError("protected access")
        for name in ("regular_file_info", "sha256_file", "read_verified"):
            patcher = mock.patch.object(mapping, name, refuse)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_default_mode_is_synthetic_and_sanitised(self):
        """The default mode passes on invented data with sanitised lines."""
        self.forbid_protected_reads()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(pmo.main([]), 0)
        lines = out.getvalue().splitlines()
        self.assertIn("status: passed", lines)
        self.assertIn("pilot_records: 10", lines)
        for line in lines:
            self.assertRegex(line, self.LINE)

    def test_real_mode_gated_closed(self):
        """The real mode refuses before touching any file."""
        self.assertFalse(pmo.BRIDGE_INTERFACE_CONFIRMED)
        self.forbid_protected_reads()
        with self.assertRaisesRegex(pmo.Step20Error,
                                    "^bridge_interface_unconfirmed$"):
            pmo.run_real(runtime=pmo.synthetic_runtime())
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(pmo.main(["--operator-real"]), 1)
        self.assertEqual(out.getvalue().strip(),
                         "status: failed (bridge_interface_unconfirmed)")


    def test_agreement_failure_console_is_aggregate(self):
        """An agreement failure prints status and counts only."""
        report = pmo.compare_canonical([], [])
        report["status"] = "failed"
        report["mismatch_counts"]["float_mismatch"] = 2

        def failing():
            """Raise an invented agreement failure."""
            raise pmo.AgreementError(report)
        out = io.StringIO()
        with mock.patch.object(pmo, "run_synthetic", failing):
            with redirect_stdout(out):
                self.assertEqual(pmo.main([]), 1)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0],
                         "status: failed (canonical_agreement_failed)")
        self.assertIn("mismatch float_mismatch: 2", lines)

    def test_unexpected_error_message_withheld(self):
        """Unexpected exception messages never reach the console."""
        def failing():
            """Raise an error whose message must not be printed."""
            raise ValueError(MARKER)
        out = io.StringIO()
        with mock.patch.object(pmo, "run_synthetic", failing):
            with redirect_stdout(out):
                self.assertEqual(pmo.main([]), 1)
        self.assertNotIn(MARKER, out.getvalue())

    def test_real_plan_pins(self):
        """The real plan pins the approved hashes and fixed output."""
        plan = pmo.real_plan()
        expected = {
            "explain_patient": "1799F536C69D2312A4277F867F7836D7CD56DDC5"
                               "4A448F24924597199A3B87D9",
            "model": "558E1B83CCF7667896A8736B4916E2CD230A9878D83BB9DC"
                     "7F806A7ACF75CFC6",
            "checkpoint": "5785E247D9A41D1D1534474D9771265EA0918767607C"
                          "44B5DB9EA364D5326FE1",
            "graph": "A650F130E08DCFA472CD50985DFCF9D140772AEBCA40AC0F"
                     "9B62E8103049DE8C",
            "concept_vocab": "4AC38FC78A194ABFD731CC38F178A83BB1C3C677"
                             "82AA2734576FA41DBCC67A4E",
            "label_vocab": "32C1E2DFE9FC89363F56D0B9A84EDF828173EC1286"
                           "DBC597CDB8FF10E1993A57",
            "documentation": "C825B55A6F5A5A48C4F99B6511CBF88306C5CA54"
                             "D9253450DD76D07D29042E26",
            "canonical_explanations": "1D28152A4491860E0AA4BD12B0E24A6E"
                                      "EC960CAE1E1DDE24A5FDEDBBB13C41A7",
            "step17_mapping": "D12DDB031DC6F8F5E8C271AF70A90D7161073D2E"
                              "F48DFC1DE2CC3AC31DADA18D",
            "kg_concepts": "D7B75FE631E721B91A026505DE2FE61D8F3ECDAA56"
                           "06C8587332A79FDCCDD52D",
            "step19_summary": "4472843C61B5196CB3D999D71EEC2EF15FBE75AE"
                              "E7F9A27B03712BA42F470B47",
        }
        self.assertEqual({name: entry["sha256"].upper()
                          for name, entry in plan["assets"].items()},
                         expected)
        self.assertIsNone(pmo.pinned_hash_problem(plan))
        self.assertEqual(plan["assets"]["kg_concepts"]["size_bytes"],
                         3713011)
        self.assertEqual(
            plan["output_dir"],
            "G:\\Industry Dataset\\ehr-evidence-workspace\\protected-data"
            "\\model-outputs\\step-20-pilot10-v1")
        for entry in plan["assets"].values():
            self.assertTrue(guard.is_within(entry["path"],
                                            guard.POLICY_ANCHOR, True))
        self.assertEqual((pmo.ATOL, pmo.RTOL, pmo.TOP_K, pmo.DEVICE,
                          pmo.CANONICAL_PREFIX),
                         (1e-6, 1e-6, 10, "cuda", (0, 1, 2)))
        self.assertTrue(guard.is_within(
            plan["assets"]["canonical_explanations"]["path"],
            plan["upstream_root"], True))
        self.assertFalse(guard.is_within(plan["output_dir"],
                                         plan["upstream_root"]))


if __name__ == "__main__":
    unittest.main()
