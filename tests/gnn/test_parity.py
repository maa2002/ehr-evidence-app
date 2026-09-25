"""Synthetic checks for ehr_evidence_app.gnn.parity (Step 19).

Every fixture is invented: small numpy arrays stand in for graph tensors and
stub loaders stand in for the pinned graph, bridge and training loader. torch
is never imported, no upstream module is imported and no real graph, mapping
or patient record is opened. Scratch files are created only under the
configured workspace test temp directory.
"""

import hashlib
import io
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout

import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.storage import policy as guard  # noqa: E402
from ehr_evidence_app.gnn import parity as parity  # noqa: E402

PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
VISIT = parity.VISIT
COND = (VISIT, "has_condition", "condition")
PROC = (VISIT, "has_procedure", "procedure")
DRUG = (VISIT, "has_drug", "drug")
NEXT = (VISIT, "temporal_next", VISIT)


def configured_test_temp():
    """Return the configured workspace test scratch directory (workspace-only rules)."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def store(**fields):
    """Return a simple node or edge store holding the given arrays."""
    return types.SimpleNamespace(**fields)


def edges(pairs):
    """Return an int64 edge_index array from (source, target) pairs."""
    return np.array(pairs, dtype=np.int64).reshape(-1, 2).T.copy()


def rich_graph(visits=3, offset=0.0, diagnosis=True):
    """Return an invented graph with condition, procedure, drug and
    temporal edges, including edges that leave the final visit."""
    x = (np.arange(visits * 6, dtype=np.float32).reshape(visits, 6)
         + np.float32(offset))
    final = visits - 1
    nodes = {VISIT: store(x=x),
             "condition": store(x=np.ones((2, 3), dtype=np.float32)),
             "procedure": store(x=np.ones((1, 3), dtype=np.float32)),
             "drug": store(x=np.full((1, 3), 2.0, dtype=np.float32))}
    links = {
        COND: store(edge_index=edges([(0, 0), (final, 1), (1, 1),
                                      (final, 0)]),
                    edge_attr=np.arange(8, dtype=np.float32).reshape(4, 2)),
        PROC: store(edge_index=edges([(final, 0), (0, 0)])),
        DRUG: store(edge_index=edges([(final, 0)])),
        NEXT: store(edge_index=edges([(v, v + 1) for v in range(final)])),
    }
    return parity.SyntheticGraph(nodes, links, diagnosis=diagnosis)


def collection():
    """Return a graph list whose second graph is dropped by the filter."""
    return [rich_graph(3, 0.0), rich_graph(2, 5.0, diagnosis=False),
            rich_graph(4, 1.5), rich_graph(1, 3.0)]


def multi_visit_collection():
    """Return a graph list whose kept graphs each have two or more visits,
    so per-graph (unbiased) statistics are defined."""
    return [rich_graph(3, 0.0), rich_graph(2, 5.0, diagnosis=False),
            rich_graph(4, 1.5)]


def reference(graphs):
    """Return reference-preprocessed model inputs of a graph list."""
    return parity.collection_inputs(
        parity.synthetic_reference_preprocess(graphs))


def subset_preprocess(graphs):
    """Faulty: normalise each graph with its own (subset) statistics."""
    result = []
    for graph in graphs:
        if parity.is_diagnosis_graph(graph):
            result.extend(parity.synthetic_reference_preprocess([graph]))
    return result


class OracleTestCase(unittest.TestCase):
    """Formula oracle and comparison rules on invented arrays."""

    def test_valid_full_collection_statistics(self):
        """The oracle z-scores with statistics over all filtered visit rows."""
        graphs = collection()
        oracle, counts = parity.build_oracle(graphs)
        self.assertEqual(counts["graphs_total"], 4)
        self.assertEqual(counts["graphs_filtered"], 3)
        self.assertEqual(counts["visit_rows"], 8)
        rows = np.concatenate([graphs[i][VISIT].x.astype(np.float64)
                               for i in (0, 2, 3)])
        mean = rows[:, 4:6].mean(axis=0)
        std = rows[:, 4:6].std(axis=0, ddof=parity.STD_DDOF)
        for position, index in enumerate((0, 2, 3)):
            raw = graphs[index][VISIT].x.astype(np.float64)
            value = oracle[position][("node", VISIT, "x")][0]
            np.testing.assert_allclose(value[:, 3], np.log1p(raw[:, 3]))
            np.testing.assert_allclose(value[:, 4:6],
                                       (raw[:, 4:6] - mean) / (std + 1e-6))
            np.testing.assert_array_equal(value[:, :3], raw[:, :3])
        parity.compare_to_oracle(reference(graphs), oracle)

    def test_subset_normalisation_rejected(self):
        """Per-graph statistics do not match the full-collection oracle."""
        graphs = multi_visit_collection()
        oracle, _ = parity.build_oracle(graphs)
        candidate = parity.collection_inputs(subset_preprocess(graphs))
        with self.assertRaisesRegex(parity.ParityError,
                                    "oracle_value_mismatch"):
            parity.compare_to_oracle(candidate, oracle)

    def test_subset_selected_before_statistics_rejected(self):
        """Selecting a subset before statistics changes the oracle output."""
        graphs = collection()
        oracle, _ = parity.build_oracle(graphs)
        subset = reference([graphs[0]])
        with self.assertRaisesRegex(parity.ParityError,
                                    "graph_count_mismatch"):
            parity.compare_to_oracle(subset, oracle)
        with self.assertRaisesRegex(parity.ParityError,
                                    "oracle_value_mismatch"):
            parity.compare_to_oracle(subset, oracle[:1])

    def test_double_preprocessing_rejected(self):
        """Preprocessing an already preprocessed collection fails."""
        graphs = collection()
        oracle, _ = parity.build_oracle(graphs)
        twice = parity.synthetic_reference_preprocess(
            parity.synthetic_reference_preprocess(graphs))
        with self.assertRaisesRegex(parity.ParityError,
                                    "oracle_value_mismatch|shape_or_dtype"):
            parity.compare_to_oracle(parity.collection_inputs(twice), oracle)

    def test_changed_graph_order_rejected(self):
        """A reordered output collection fails the oracle comparison."""
        graphs = collection()
        oracle, _ = parity.build_oracle(graphs)
        candidate = list(reversed(reference(graphs)))
        with self.assertRaises(parity.ParityError):
            parity.compare_to_oracle(candidate, oracle)

    def test_changed_filter_rejected(self):
        """Keeping a graph without y_diagnosis changes the collection."""
        graphs = collection()
        oracle, _ = parity.build_oracle(graphs)
        for graph in graphs:
            graph.y_diagnosis = np.zeros((1, 1), dtype=np.float32)
        with self.assertRaisesRegex(parity.ParityError,
                                    "graph_count_mismatch"):
            parity.compare_to_oracle(reference(graphs), oracle)

    def test_only_final_visit_condition_edges_removed(self):
        """Final-visit condition edge_index columns go; edge_attr and all
        other edges are kept unchanged, as in the pinned sources."""
        graph = rich_graph(3)
        oracle, counts = parity.build_oracle([graph])
        item = oracle[0]
        self.assertEqual(counts["final_visit_condition_edges_removed"], 2)
        np.testing.assert_array_equal(
            item[("edge", COND, "edge_index")][0], edges([(0, 0), (1, 1)]))
        np.testing.assert_array_equal(
            item[("edge", COND, "edge_attr")][0], graph[COND].edge_attr)
        for key in (PROC, DRUG, NEXT):
            np.testing.assert_array_equal(
                item[("edge", key, "edge_index")][0], graph[key].edge_index)
        for name in ("condition", "procedure", "drug"):
            np.testing.assert_array_equal(
                item[("node", name, "x")][0], graph[name].x)

    def test_oracle_does_not_mutate_raw(self):
        """The raw graph arrays are unchanged after the oracle runs."""
        graphs = collection()
        before = parity.digest_inputs(parity.collection_inputs(graphs))
        parity.build_oracle(graphs)
        self.assertEqual(
            parity.digest_inputs(parity.collection_inputs(graphs)), before)

    def test_absent_tensor_rejected(self):
        """A missing visit x or edge_index fails closed."""
        graph = rich_graph()
        graph[VISIT].x = None
        with self.assertRaisesRegex(parity.ParityError, "tensor_absent"):
            parity.build_oracle([graph])
        graph = rich_graph()
        del graph[PROC].edge_index
        with self.assertRaisesRegex(parity.ParityError, "tensor_absent"):
            parity.build_oracle([graph])

    def test_invalid_tensor_rejected(self):
        """Wrong types, ranks, widths or edge dtypes fail closed."""
        cases = ((VISIT, "x", [[1.0] * 6]),
                 (VISIT, "x", np.zeros(6, dtype=np.float32)),
                 (VISIT, "x", np.zeros((2, 5), dtype=np.float32)),
                 (VISIT, "x", np.zeros((2, 6), dtype=np.int64)),
                 (PROC, "edge_index", np.zeros((2, 1), dtype=np.float32)),
                 (PROC, "edge_index", np.zeros((3, 1), dtype=np.int64)))
        for name, field, value in cases:
            graph = rich_graph()
            setattr(graph[name], field, value)
            with self.assertRaisesRegex(parity.ParityError,
                                        "tensor_invalid"):
                parity.build_oracle([graph])

    def test_non_finite_rejected(self):
        """NaN or infinite inputs and non-finite oracle values fail."""
        for value in (np.nan, np.inf):
            graph = rich_graph()
            graph[VISIT].x[0, 1] = value
            with self.assertRaisesRegex(parity.ParityError,
                                        "tensor_non_finite"):
                parity.build_oracle([graph])
        graph = rich_graph()
        graph[VISIT].x[0, 3] = -2.0
        with np.errstate(invalid="ignore"):
            with self.assertRaisesRegex(parity.ParityError,
                                        "oracle_non_finite"):
                parity.build_oracle([graph])
        graphs = collection()
        oracle, _ = parity.build_oracle(graphs)
        candidate = reference(graphs)
        candidate[0][("node", VISIT, "x")][0, 0] = np.nan
        with self.assertRaisesRegex(parity.ParityError, "oracle_value"):
            parity.compare_to_oracle(candidate, oracle)

    def test_oracle_tolerance_fixed(self):
        """Values beyond 1e-6 fail, within pass and tolerances cannot widen."""
        graphs = collection()
        oracle, _ = parity.build_oracle(graphs)
        candidate = reference(graphs)
        with self.assertRaisesRegex(parity.ParityError, "tolerance_changed"):
            parity.compare_to_oracle(candidate, oracle, atol=1e-3)
        candidate[0][("node", VISIT, "x")][0, 0] += np.float32(1e-3)
        with self.assertRaisesRegex(parity.ParityError,
                                    "oracle_value_mismatch"):
            parity.compare_to_oracle(candidate, oracle)

    def test_exact_source_path_parity(self):
        """Any bit difference between source paths fails (atol=0)."""
        first = reference(collection())
        second = reference(collection())
        parity.compare_exact(parity.digest_inputs(first),
                             parity.digest_inputs(second))
        x = second[0][("node", VISIT, "x")]
        x[0, 0] = np.nextafter(x[0, 0], np.float32(np.inf))
        with self.assertRaisesRegex(parity.ParityError,
                                    "source_path_mismatch"):
            parity.compare_exact(parity.digest_inputs(first),
                                 parity.digest_inputs(second))

    def test_digest_covers_dtype_and_shape(self):
        """Same bytes with a different dtype or shape change the digest."""
        base = {("node", VISIT, "x"): np.zeros((2, 6), dtype=np.float32)}
        other = {("node", VISIT, "x"): np.zeros((3, 4), dtype=np.float32)}
        wide = {("node", VISIT, "x"): np.zeros((1, 6), dtype=np.float64)}
        digests = {parity.digest_inputs([item])
                   for item in (base, other, wide)}
        self.assertEqual(len(digests), 3)

    def test_synthetic_default_mode(self):
        """The default mode passes on invented fixtures only."""
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(parity.main([]), 0)
        self.assertIn("status: passed", output.getvalue())


class RealModeTestCase(unittest.TestCase):
    """Pinned identity, path boundaries and protected summary output."""

    def setUp(self):
        """Create synthetic pinned files in a workspace scratch directory."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT, True),
                        "test scratch space must stay in the workspace root")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="step-19-", dir=root)
        self.addCleanup(self.remove_scratch)
        self.loads = []
        self.plan = self.build_plan()

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def write(self, name, data):
        """Write a scratch file and return its path, size and SHA-256."""
        path = os.path.join(self.work, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return {"path": path, "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def build_plan(self):
        """Return a plan pinned to synthetic source and graph files."""
        sources = {}
        for name in ("train", "explain_patient", "model", "builder"):
            data = b"# synthetic %s\n" % name.encode()
            entry = self.write(name + ".py", data)
            del entry["size_bytes"]
            sources[name] = entry
        return {"graph": self.write("graphs.pt", b"synthetic graph bytes"),
                "sources": sources,
                "output_dir": os.path.join(self.work, "out"),
                "protected_root": self.work}

    def loader(self, path):
        """Record a deserialisation and return a fresh invented collection."""
        self.loads.append("graph")
        return collection()

    def bridge(self, graphs):
        """Stand-in bridge preprocessing on the full collection."""
        self.loads.append("bridge")
        return parity.synthetic_reference_preprocess(graphs)

    def train_load(self, path):
        """Stand-in training loader: fresh load and preprocessing."""
        self.loads.append("train")
        return parity.synthetic_reference_preprocess(collection())

    def test_parity_run_and_summary(self):
        """A consistent run passes and writes one aggregate summary."""
        held = parity.verify_sources(self.plan)
        summary = parity.run_parity(self.plan, self.loader, self.bridge,
                                    self.train_load, held)
        self.assertEqual(self.loads, ["graph", "bridge", "train"])
        self.assertFalse(summary["phase4_eligible"])
        self.assertEqual(summary["tolerances"]["source_path"],
                         {"atol": 0.0, "rtol": 0.0})
        parity.write_summary(self.plan, summary)
        path = os.path.join(self.plan["output_dir"], parity.OUTPUT_NAME)
        with open(path, "r", encoding="utf-8") as handle:
            written = json.load(handle)
        self.assertEqual(written["status"], "passed")
        self.assertFalse(os.path.lexists(self.plan["output_dir"]
                                         + parity.PARTIAL))
        with self.assertRaisesRegex(parity.ParityError, "output_exists"):
            parity.write_summary(self.plan, summary)

    def test_train_path_difference_rejected(self):
        """A training output within oracle tolerance but not exact fails."""
        def drifted(path):
            """Return training inputs with a one-ulp visit difference."""
            graphs = parity.synthetic_reference_preprocess(collection())
            x = graphs[0][VISIT].x
            x[0, 0] = np.nextafter(x[0, 0], np.float32(np.inf))
            return graphs
        with self.assertRaisesRegex(parity.ParityError,
                                    "source_path_mismatch"):
            parity.run_parity(self.plan, self.loader, self.bridge, drifted)

    def test_bridge_subset_rejected(self):
        """A bridge that normalises per graph fails before training loads."""
        def multi_visit_loader(path):
            """Record a deserialisation; return the multi-visit collection."""
            self.loads.append("graph")
            return multi_visit_collection()
        with self.assertRaisesRegex(parity.ParityError,
                                    "oracle_value_mismatch"):
            parity.run_parity(self.plan, multi_visit_loader,
                              subset_preprocess, self.train_load)
        self.assertEqual(self.loads, ["graph"])
        self.assertNotIn("train", self.loads)

    def test_source_hash_mismatch_rejected(self):
        """A changed pinned source fails before any deserialisation."""
        self.plan["sources"]["train"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(parity.ParityError,
                                    "source_identity:input_hash_mismatch"):
            parity.verify_sources(self.plan)

    def test_source_outside_policy_anchor_rejected(self):
        """A source path outside the workspace is refused."""
        self.plan["sources"]["model"]["path"] = "C:\\synthetic\\model.py"
        with self.assertRaisesRegex(parity.ParityError,
                                    "source_identity:input_path_rejected"):
            parity.verify_sources(self.plan)

    def test_graph_identity_mismatch_rejected(self):
        """A graph hash or size mismatch fails before the loader runs."""
        self.plan["graph"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(parity.ParityError,
                                    "graph_identity:hash_mismatch"):
            parity.run_parity(self.plan, self.loader, self.bridge,
                              self.train_load)
        self.plan["graph"]["size_bytes"] += 1
        with self.assertRaisesRegex(parity.ParityError,
                                    "graph_identity:size_mismatch"):
            parity.run_parity(self.plan, self.loader, self.bridge,
                              self.train_load)
        self.assertEqual(self.loads, [])

    def test_graph_outside_policy_anchor_rejected(self):
        """A graph path outside the workspace is refused before loading."""
        self.plan["graph"]["path"] = "G:\\synthetic\\graphs.pt"
        with self.assertRaisesRegex(parity.ParityError,
                                    "graph_identity:input_path_rejected"):
            parity.run_parity(self.plan, self.loader, self.bridge,
                              self.train_load)
        self.assertEqual(self.loads, [])

    def test_unprotected_output_rejected(self):
        """An output outside the protected root is refused."""
        self.plan["protected_root"] = os.path.join(self.work, "protected")
        with self.assertRaisesRegex(parity.ParityError,
                                    "output_not_protected"):
            parity.output_preconditions(self.plan)

    def test_network_blocked(self):
        """Socket connections are refused inside the guard and restored."""
        original = socket.create_connection
        with parity.network_blocked():
            with self.assertRaisesRegex(parity.ParityError,
                                        "network_refused"):
                socket.create_connection(("127.0.0.1", 9))
        self.assertIs(socket.create_connection, original)


class AdapterTestCase(unittest.TestCase):
    """Upstream adapter call shapes against stub modules only."""

    def test_train_loader_args_and_tuple(self):
        """The loader gets graphs_path/vocab_path and keeps the graph list."""
        calls = []
        graphs = [object(), object()]

        def load_omop_diagnosis_data(args):
            """Stub loader recording its args and returning a tuple."""
            calls.append(args)
            return graphs, 7, {"condition": 3}

        filtered = graphs[:1]
        stubs = {
            "train": types.SimpleNamespace(
                load_omop_diagnosis_data=load_omop_diagnosis_data),
            "explain_patient": types.SimpleNamespace(
                preprocess=lambda items: filtered),
        }
        original = parity.import_upstream
        parity.import_upstream = lambda plan, name: stubs[name]
        self.addCleanup(setattr, parity, "import_upstream", original)
        bridge, load = parity.upstream_adapters({})
        self.assertIs(load("W:\\synthetic\\graphs.pt"), graphs)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].graphs_path, "W:\\synthetic\\graphs.pt")
        self.assertIsNone(calls[0].vocab_path)
        self.assertIs(bridge(graphs), filtered)

    def test_upstream_output_silenced(self):
        """Upstream logging and prints never escape; state is restored."""
        marker = "SYNTHETIC_UPSTREAM_MARKER"

        def noisy(*args):
            """Log and print the synthetic marker on every channel."""
            logging.getLogger("step19_stub").error(marker)
            logging.warning(marker)
            print(marker)
            print(marker, file=sys.stderr)

        def load_omop_diagnosis_data(args):
            """Noisy stub loader returning a synthetic tuple."""
            noisy()
            return [object()], 1, {}

        def preprocess(items):
            """Noisy stub bridge returning the list."""
            noisy()
            return items

        def fake_import(plan, name):
            """Noisy stub import returning a stub module."""
            noisy()
            return types.SimpleNamespace(
                load_omop_diagnosis_data=load_omop_diagnosis_data,
                preprocess=preprocess)

        original = parity.import_upstream
        parity.import_upstream = fake_import
        self.addCleanup(setattr, parity, "import_upstream", original)
        saved_level = logging.root.manager.disable
        self.addCleanup(logging.disable, saved_level)
        logging.disable(logging.DEBUG)
        log_buffer = io.StringIO()
        handler = logging.StreamHandler(log_buffer)
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            bridge, load = parity.upstream_adapters({})
            load("W:\\synthetic\\graphs.pt")
            bridge([object()])
            self.assertIs(sys.stdout, out)
            self.assertIs(sys.stderr, err)
            print("status: passed")
        for text in (out.getvalue(), err.getvalue(), log_buffer.getvalue()):
            self.assertNotIn(marker, text)
        self.assertIn("status: passed", out.getvalue())
        self.assertEqual(logging.root.manager.disable, logging.DEBUG)

    def test_silencing_restored_on_failure(self):
        """Streams, disable level and root logger are restored on error."""
        root = logging.getLogger()
        saved = (sys.stdout, sys.stderr, logging.root.manager.disable)
        saved_root = (list(root.handlers), root.level, root.disabled)
        with self.assertRaisesRegex(RuntimeError, "synthetic"):
            with parity.upstream_silenced():
                self.assertEqual(logging.root.manager.disable,
                                 logging.CRITICAL)
                root.addHandler(logging.StreamHandler(sys.stdout))
                root.setLevel(logging.DEBUG)
                root.disabled = True
                raise RuntimeError("synthetic")
        self.assertEqual(
            (sys.stdout, sys.stderr, logging.root.manager.disable), saved)
        self.assertEqual(
            (list(root.handlers), root.level, root.disabled), saved_root)

    def isolate_root_logger(self):
        """Empty the root logger for one test and restore it afterwards."""
        root = logging.getLogger()
        saved = (list(root.handlers), root.level, root.disabled,
                 logging.root.manager.disable)

        def restore():
            """Put back the root logger state saved before the test."""
            for handler in list(root.handlers):
                if handler not in saved[0]:
                    root.removeHandler(handler)
                    handler.close()
            root.handlers[:] = saved[0]
            root.setLevel(saved[1])
            root.disabled = saved[2]
            logging.disable(saved[3])
        self.addCleanup(restore)
        root.handlers[:] = []
        root.setLevel(logging.WARNING)
        root.disabled = False
        logging.disable(logging.NOTSET)
        return root

    def test_silencing_removes_basicconfig_sink_handler(self):
        """A basicConfig handler bound to the sink is removed afterwards."""
        root = self.isolate_root_logger()
        marker = "synthetic-basicconfig-marker"
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with parity.upstream_silenced():
                logging.basicConfig(stream=sys.stdout)
                self.assertEqual(len(root.handlers), 1)
                self.assertIsInstance(root.handlers[0].stream,
                                      parity._DiscardSink)
                sink_handler = root.handlers[0]
                logging.getLogger("synthetic").critical(marker)
                print(marker)
                print(marker, file=sys.stderr)
            self.assertIs(sys.stdout, out)
            self.assertIs(sys.stderr, err)
            self.assertEqual(root.handlers, [])
            self.assertNotIn(sink_handler, root.handlers)
            self.assertEqual(logging.root.manager.disable, logging.NOTSET)
            logging.basicConfig(stream=sys.stdout, format="%(message)s")
            self.assertEqual(len(root.handlers), 1)
            self.assertIs(root.handlers[0].stream, out)
            logging.getLogger("synthetic").warning("post-context log")
            print("post-context print")
        for text in (out.getvalue(), err.getvalue()):
            self.assertNotIn(marker, text)
        self.assertIn("post-context log", out.getvalue())
        self.assertIn("post-context print", out.getvalue())


if __name__ == "__main__":
    unittest.main()
