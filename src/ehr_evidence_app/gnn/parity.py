#!/usr/bin/env python3
"""Full-collection diagnosis preprocessing parity check (Step 19).

Checks that the pinned training loader (train.load_omop_diagnosis_data) and
the pinned explanation bridge (explain_patient.preprocess) give exactly the
same model-input graphs, and that both match an independent formula oracle
derived from the raw graph list:

* keep graphs that carry y_diagnosis, in their stored order;
* compute statistics over every visit row of the full filtered collection
  (never a subset);
* apply log1p once to visit x column 3 and z-score visit x columns 4-5 with
  (value - mean) / (std + 1e-6);
* remove only has_condition edge_index columns whose source is the final
  visit node (edge_attr is not filtered, as in the pinned sources);
* leave every other model input unchanged.

The pinned model.py consumes node x and edge edge_index only; it does not
consume edge_attr. When present, edge_attr is still compared and digested,
but only as an extra structural invariant that must stay unchanged, not as
a model input.

Tolerances are fixed: the bridge/loader source-path parity is exact
(atol=0, rtol=0, compared as a digest of the ordered serialisation of every
compared tensor with shape and dtype); the formula oracle uses
atol=1e-6, rtol=1e-6. Non-finite values always fail. Model outputs belong to
Step 20: no checkpoint is loaded and no inference or attribution runs.

The default mode runs a small invented fixture only and loads no patient
data. The operator-only mode (--operator-real) verifies the pinned
source and graph identities and the storage path boundaries before any
deserialisation, loads one collection at a time and writes only an
aggregate sanitised summary, atomically and never overwriting, beneath the
protected workspace. Upstream imports and loader calls run with logging
disabled and Python stdout/stderr sent to a discarding sink, so upstream
path and statistics logging never reaches the console; root logging
handlers, level and disabled state are restored afterwards. Console output
and the summary carry status, counts, digests and finding codes only.
phase4_eligible is always false.

Usage:
    python -m ehr_evidence_app.gnn.parity
    python -m ehr_evidence_app.gnn.parity --operator-real
"""

from __future__ import annotations

import argparse
import contextlib
import gc
import hashlib
import importlib.util
import json
import logging
import os
import socket
import sys
import types

import numpy as np

from ehr_evidence_app.gnn import mapping as mapping
from ehr_evidence_app.storage import policy as guard

VERSION = "19.0"
SCHEMA = "preprocessing-parity-summary"
SCHEMA_VERSION = "1.0"
UPSTREAM_ROOT = mapping.UPSTREAM_ROOT
OUTPUT_DIR = (guard.PRIVATE_ROOT + "\\protected-data\\verification\\"
              "step-19-preprocessing-parity")
OUTPUT_NAME = "summary.json"
PARTIAL = ".partial"

EXACT_ATOL = 0.0
EXACT_RTOL = 0.0
ORACLE_ATOL = 1e-6
ORACLE_RTOL = 1e-6
STD_EPSILON = 1e-6
# Unbiased standard deviation: the pinned source calls torch .std() with no
# override, whose default is Bessel-corrected.
STD_DDOF = 1
VISIT = "visit"
LOG_COLUMN = 3
ZSCORE_COLUMNS = (4, 5)
CONDITION_RELATION = "has_condition"
NODE_FIELDS = ("x",)
EDGE_FIELDS = ("edge_index", "edge_attr")
REQUIRED_FIELDS = ("x", "edge_index")


class ParityError(Exception):
    """A fail-closed parity or precondition failure carrying a code only."""


def real_plan():
    """Return the pinned upstream sources, graph and protected output."""
    gnn = UPSTREAM_ROOT + "\\src\\gnn\\"
    return {
        "graph": mapping.real_plan()["graph"],
        "sources": {
            "train": {"path": gnn + "train.py",
                      "sha256": "eb412e5beff75371d30de509ad7d8605"
                                "f727bf9e25815d15d07cdaebebdea6df"},
            "explain_patient": {"path": gnn + "explain_patient.py",
                                "sha256": "1799f536c69d2312a4277f867f7836d7"
                                          "cd56ddc54a448f24924597199a3b87d9"},
            "model": {"path": gnn + "model.py",
                      "sha256": "558e1b83ccf7667896a8736b4916e2cd"
                                "230a9878d83bb9dc7f806a7acf75cfc6"},
            "builder": {"path": UPSTREAM_ROOT + "\\src\\preprocess\\"
                                "build_patient_graphs.py",
                        "sha256": "175aaf7335b72c41ecc262d88203e800"
                                  "0323fe60451c92e419effd47111d1f70"},
        },
        "output_dir": OUTPUT_DIR,
        "protected_root": guard.PRIVATE_ROOT + "\\protected-data",
    }


# --------------------------------------------------------------------------
# Model-consumed tensors
# --------------------------------------------------------------------------

def to_array(value):
    """Return a numpy view of a tensor or array, None when absent.

    Anything that is neither a numpy array nor a tensor with detach() is
    rejected as an invalid model input.
    """
    if value is None:
        return None
    if hasattr(value, "detach"):
        return value.detach().cpu().numpy()
    if isinstance(value, np.ndarray):
        return value
    raise ParityError("tensor_invalid")


def check_array(array, field):
    """Fail closed on a malformed or non-finite model-input array."""
    if array.ndim != 2:
        raise ParityError("tensor_invalid")
    if field == "edge_index":
        if array.shape[0] != 2 or not np.issubdtype(array.dtype, np.integer):
            raise ParityError("tensor_invalid")
        return
    if not np.issubdtype(array.dtype, np.number) or np.issubdtype(
            array.dtype, np.complexfloating):
        raise ParityError("tensor_invalid")
    if not np.all(np.isfinite(array)):
        raise ParityError("tensor_non_finite")


def model_inputs(graph):
    """Return the ordered {key: array} compared tensors of one graph.

    Keys are ("node", type, field) and ("edge", type, field) in sorted type
    order. Every node type must carry x and every edge type edge_index;
    these are what model.py consumes. edge_attr is not a model input; it is
    included when present only as an extra unchanged structural invariant.
    """
    try:
        node_types = sorted(graph.node_types)
        edge_types = sorted(tuple(item) for item in graph.edge_types)
    except (AttributeError, TypeError):
        raise ParityError("graph_invalid") from None
    if VISIT not in node_types:
        raise ParityError("tensor_absent")
    inputs = {}
    for kind, types, fields in (("node", node_types, NODE_FIELDS),
                                ("edge", edge_types, EDGE_FIELDS)):
        for name in types:
            store = graph[name]
            for field in fields:
                array = to_array(getattr(store, field, None))
                if array is None:
                    if field in REQUIRED_FIELDS:
                        raise ParityError("tensor_absent")
                    continue
                check_array(array, field)
                inputs[(kind, name, field)] = array
    visit = inputs[("node", VISIT, "x")]
    if visit.shape[1] <= max(ZSCORE_COLUMNS) or not np.issubdtype(
            visit.dtype, np.floating):
        raise ParityError("tensor_invalid")
    return inputs


def collection_inputs(graphs):
    """Return model inputs for every graph of a list, in list order."""
    if not isinstance(graphs, (list, tuple)):
        raise ParityError("graph_collection_not_list")
    return [model_inputs(graph) for graph in graphs]


def digest_inputs(collection):
    """Return a SHA-256 over an ordered serialisation of model inputs.

    Each tensor contributes its key, little-endian dtype, shape and
    C-ordered bytes, so equal digests mean exactly equal tensors
    (atol=0, rtol=0).
    """
    digest = hashlib.sha256()
    digest.update(b"graphs:%d;" % len(collection))
    for inputs in collection:
        digest.update(b"graph:%d;" % len(inputs))
        for key, array in inputs.items():
            data = np.ascontiguousarray(
                array, dtype=array.dtype.newbyteorder("<"))
            header = json.dumps([list(key), data.dtype.str,
                                 list(data.shape)])
            digest.update(header.encode("utf-8") + b";")
            digest.update(data.tobytes())
    return digest.hexdigest()


def is_diagnosis_graph(graph):
    """Mirror the bridge filter: the graph has a y_diagnosis attribute."""
    return hasattr(graph, "y_diagnosis")


# --------------------------------------------------------------------------
# Independent formula oracle
# --------------------------------------------------------------------------

def visit_statistics(collection):
    """Return (mean, std) of z-score columns over all visit rows.

    Every visit row of every graph in the full filtered collection is used.
    """
    columns = list(ZSCORE_COLUMNS)
    rows = [inputs[("node", VISIT, "x")][:, columns].astype(np.float64)
            for inputs in collection]
    stacked = np.concatenate(rows, axis=0)
    if stacked.shape[0] <= STD_DDOF:
        raise ParityError("visit_rows_insufficient")
    mean = stacked.mean(axis=0)
    std = stacked.std(axis=0, ddof=STD_DDOF)
    if not (np.all(np.isfinite(mean)) and np.all(np.isfinite(std))):
        raise ParityError("statistics_non_finite")
    return mean, std


def oracle_graph(inputs, mean, std):
    """Return new oracle model inputs for one graph; inputs are unchanged.

    Visit x is copied as float64 with log1p on column 3 and the z-score on
    columns 4-5; has_condition edge_index columns leaving the final visit
    are removed. As in the pinned sources, edge_attr (not a model input,
    kept as a structural invariant) is not filtered. Every
    other array is copied unchanged. Dtypes of the raw arrays are recorded
    for the candidate comparison.
    """
    result = {}
    final = inputs[("node", VISIT, "x")].shape[0] - 1
    keeps = {}
    for kind, name, field in inputs:
        if kind == "edge" and name[1] == CONDITION_RELATION:
            if name[0] != VISIT:
                raise ParityError("condition_edge_orientation_unexpected")
            if name not in keeps:
                index = inputs[("edge", name, "edge_index")]
                keeps[name] = index[0] != final
    for key, array in inputs.items():
        kind, name, field = key
        value = array.astype(np.float64) if np.issubdtype(
            array.dtype, np.floating) else array.copy()
        if key == ("node", VISIT, "x"):
            value[:, LOG_COLUMN] = np.log1p(value[:, LOG_COLUMN])
            columns = list(ZSCORE_COLUMNS)
            value[:, columns] = (value[:, columns] - mean) / (
                std + STD_EPSILON)
        elif kind == "edge" and name in keeps and field == "edge_index":
            value = value[:, keeps[name]]
        if value.dtype.kind == "f" and not np.all(np.isfinite(value)):
            raise ParityError("oracle_non_finite")
        result[key] = (value, array.dtype)
    return result


def build_oracle(graphs):
    """Return (oracle, counts) for a raw full graph list without mutation.

    The y_diagnosis filter keeps stored order; statistics come from the
    whole filtered collection before any per-graph work. The raw inputs are
    digested before and after, and any change fails closed.
    """
    if not isinstance(graphs, (list, tuple)):
        raise ParityError("graph_collection_not_list")
    filtered = [graph for graph in graphs if is_diagnosis_graph(graph)]
    if not filtered:
        raise ParityError("no_diagnosis_graphs")
    collection = collection_inputs(filtered)
    before = digest_inputs(collection)
    mean, std = visit_statistics(collection)
    oracle = [oracle_graph(inputs, mean, std) for inputs in collection]
    if digest_inputs(collection_inputs(filtered)) != before:
        raise ParityError("raw_mutated_by_oracle")
    removed = sum(
        int(inputs[key].shape[1] - oracle_item[key][0].shape[1])
        for inputs, oracle_item in zip(collection, oracle)
        for key in inputs
        if key[0] == "edge" and key[2] == "edge_index")
    counts = {"graphs_total": len(graphs),
              "graphs_filtered": len(filtered),
              "visit_rows": int(sum(item[("node", VISIT, "x")].shape[0]
                                    for item in collection)),
              "final_visit_condition_edges_removed": removed}
    return oracle, counts


def compare_to_oracle(collection, oracle, atol=ORACLE_ATOL,
                      rtol=ORACLE_RTOL):
    """Fail closed unless candidate inputs match the oracle within tolerance.

    Graph count, input keys, shapes and dtypes must match exactly; integer
    arrays must be equal and floating arrays close within (atol, rtol).
    """
    if atol != ORACLE_ATOL or rtol != ORACLE_RTOL:
        raise ParityError("tolerance_changed")
    if len(collection) != len(oracle):
        raise ParityError("graph_count_mismatch")
    for inputs, expected in zip(collection, oracle):
        if list(inputs) != list(expected):
            raise ParityError("input_keys_mismatch")
        for key, array in inputs.items():
            value, dtype = expected[key]
            if array.shape != value.shape or array.dtype != dtype:
                raise ParityError("shape_or_dtype_mismatch")
            if np.issubdtype(array.dtype, np.integer):
                if not np.array_equal(array, value):
                    raise ParityError("oracle_value_mismatch")
            elif not np.allclose(array.astype(np.float64), value, atol=atol,
                                 rtol=rtol, equal_nan=False):
                raise ParityError("oracle_value_mismatch")


def compare_exact(first, second):
    """Fail closed unless two source-path digests are identical (atol=0)."""
    if EXACT_ATOL != 0.0 or EXACT_RTOL != 0.0:
        raise ParityError("tolerance_changed")
    if first != second:
        raise ParityError("source_path_mismatch")


# --------------------------------------------------------------------------
# Pinned identities, upstream adapters and output
# --------------------------------------------------------------------------

def verify_sources(plan):
    """Check each pinned source's path, link safety and SHA-256.

    Returns the held (path, signature, name) records for re-checking.
    """
    held = []
    for name, entry in sorted(plan["sources"].items()):
        try:
            mapping.verify_small_input(entry, name, held)
        except mapping.MappingError as error:
            raise ParityError("source_identity:%s" % error) from None
    return held


def verify_graph(entry):
    """Check the pinned graph's path, size and SHA-256; return signature.

    Nothing is deserialised here.
    """
    try:
        info = mapping.regular_file_info(entry["path"])
        if info.st_size != entry["size_bytes"]:
            raise ParityError("graph_identity:size_mismatch")
        if mapping.sha256_file(entry["path"]) != entry["sha256"].lower():
            raise ParityError("graph_identity:hash_mismatch")
        after = mapping.regular_file_info(entry["path"])
    except mapping.MappingError as error:
        raise ParityError("graph_identity:%s" % error) from None
    signature = (info.st_size, info.st_mtime_ns)
    if (after.st_size, after.st_mtime_ns) != signature:
        raise ParityError("graph_changed_during_hash")
    return signature


def confirm_graph(entry, signature):
    """Fail closed when the pinned graph changed after verification."""
    try:
        info = mapping.regular_file_info(entry["path"])
    except mapping.MappingError as error:
        raise ParityError("graph_identity:%s" % error) from None
    if (info.st_size, info.st_mtime_ns) != signature:
        raise ParityError("graph_changed_during_load")


def output_preconditions(plan):
    """Refuse an existing or unprotected summary or partial path."""
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        if os.path.lexists(path):
            raise ParityError("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise ParityError(problem)


def write_summary(plan, summary):
    """Write the aggregate summary atomically; never overwrite.

    The summary is written into a new partial directory, which is then
    renamed to the final directory (rename fails if the target exists).
    """
    output_preconditions(plan)
    partial = plan["output_dir"] + PARTIAL
    os.makedirs(partial)
    with open(os.path.join(partial, OUTPUT_NAME), "x",
              encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.rename(partial, plan["output_dir"])


@contextlib.contextmanager
def network_blocked():
    """Refuse socket connections and name lookups inside the block."""
    def refuse(*args, **kwargs):
        """Raise instead of opening a network connection."""
        raise ParityError("network_refused")
    saved = (socket.socket.connect, socket.socket.connect_ex,
             socket.create_connection, socket.getaddrinfo)
    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse
    socket.create_connection = refuse
    socket.getaddrinfo = refuse
    try:
        yield
    finally:
        (socket.socket.connect, socket.socket.connect_ex,
         socket.create_connection, socket.getaddrinfo) = saved


class _DiscardSink:
    """Internal text stream that accepts and drops everything written."""

    def write(self, text):
        """Discard the text and report it as written."""
        return len(text)

    def flush(self):
        """Nothing is buffered, so there is nothing to flush."""


@contextlib.contextmanager
def upstream_silenced():
    """Disable logging and sink Python stdout/stderr inside the block.

    Upstream code logs graph paths and aggregate normalisation statistics;
    none of it may reach the console. In finally, even on failure, the
    prior streams, logging-disable level and root logger state are
    restored: any root handler created inside the block (for example a
    basicConfig handler bound to the discarding sink) is removed and
    closed, and the original handlers, level and disabled flag return.
    """
    root = logging.getLogger()
    saved_level = logging.root.manager.disable
    saved_streams = (sys.stdout, sys.stderr)
    saved_handlers = list(root.handlers)
    saved_root = (root.level, root.disabled)
    sink = _DiscardSink()
    logging.disable(logging.CRITICAL)
    sys.stdout = sys.stderr = sink
    try:
        yield
    finally:
        sys.stdout, sys.stderr = saved_streams
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        root.handlers[:] = saved_handlers
        root.setLevel(saved_root[0])
        root.disabled = saved_root[1]
        logging.disable(saved_level)


def import_upstream(plan, name):
    """Import one verified pinned upstream module from its fixed path."""
    for path in (UPSTREAM_ROOT, UPSTREAM_ROOT + "\\src\\gnn"):
        if path not in sys.path:
            sys.path.insert(0, path)
    spec = importlib.util.spec_from_file_location(
        "step19_upstream_" + name, plan["sources"][name]["path"])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def torch_loader(path):
    """Deserialise the pinned graph list once on CPU (omopgnn only).

    Any library output during loading is silenced.
    """
    with upstream_silenced():
        import torch  # Imported lazily: the app environment excludes torch.
        return torch.load(path, map_location="cpu", weights_only=False)


def upstream_adapters(plan):
    """Return (bridge_preprocess, train_load) calling the pinned modules.

    The pinned load_omop_diagnosis_data(args) reads args.graphs_path and
    args.vocab_path and returns (graphs, num_labels, vocab_sizes); the
    loader adapter passes an args-like object and keeps the graph list.
    The pinned preprocess(graphs) filters, mutates the kept graphs and
    returns the filtered list, which the bridge adapter returns; any other
    result fails closed in the comparison. Imports and calls are silenced.
    """
    with upstream_silenced():
        explain = import_upstream(plan, "explain_patient")
        train = import_upstream(plan, "train")

    def bridge(graphs):
        """Run the bridge preprocessing on the full graph list."""
        with upstream_silenced():
            return explain.preprocess(graphs)

    def load(path):
        """Run the training loader on a fresh load of the pinned graph."""
        args = types.SimpleNamespace(graphs_path=path, vocab_path=None)
        with upstream_silenced():
            result = train.load_omop_diagnosis_data(args)
        if isinstance(result, tuple) and result and isinstance(
                result[0], list):
            result = result[0]
        return result
    return bridge, load


def run_parity(plan, graph_loader, bridge, train_load, held=()):
    """Run the full parity sequence and return the aggregate summary.

    Order: raw full load, oracle, bridge comparison, release of the first
    collection, fresh training-loader load, comparison and exact digest
    parity. Only one graph collection is held at a time.
    """
    signature = verify_graph(plan["graph"])
    graphs = graph_loader(plan["graph"]["path"])
    confirm_graph(plan["graph"], signature)
    oracle, counts = build_oracle(graphs)
    bridged = bridge(graphs)
    bridge_inputs = collection_inputs(bridged)
    compare_to_oracle(bridge_inputs, oracle)
    bridge_digest = digest_inputs(bridge_inputs)
    del graphs, bridged, bridge_inputs
    gc.collect()
    signature = verify_graph(plan["graph"])
    trained = train_load(plan["graph"]["path"])
    confirm_graph(plan["graph"], signature)
    train_inputs = collection_inputs(trained)
    compare_to_oracle(train_inputs, oracle)
    train_digest = digest_inputs(train_inputs)
    compare_exact(bridge_digest, train_digest)
    del trained, train_inputs, oracle
    gc.collect()
    if held:
        try:
            mapping.confirm_unchanged(held)
        except mapping.MappingError as error:
            raise ParityError("source_identity:%s" % error) from None
    return {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "version": VERSION, "status": "passed",
        "phase4_eligible": False,
        "graph_sha256": plan["graph"]["sha256"].lower(),
        "source_sha256": {name: entry["sha256"].lower()
                          for name, entry in sorted(plan["sources"].items())},
        "counts": counts,
        "model_input_digest": train_digest,
        "tolerances": {"source_path": {"atol": EXACT_ATOL,
                                       "rtol": EXACT_RTOL},
                       "oracle": {"atol": ORACLE_ATOL,
                                  "rtol": ORACLE_RTOL}},
        "checks": ["pinned_identity", "full_collection_statistics",
                   "oracle_unmutated_raw", "bridge_vs_oracle",
                   "train_vs_oracle", "bridge_vs_train_exact"],
    }


def run_real(plan=None):
    """Operator-only real run; returns the summary written to the workspace."""
    plan = plan or real_plan()
    problem = guard.fixed_policy_problem()
    if problem:
        raise ParityError("fixed_policy_rejected")
    output_preconditions(plan)
    held = verify_sources(plan)
    os.environ["CLEARML_OFFLINE_MODE"] = "1"
    with network_blocked():
        bridge, train_load = upstream_adapters(plan)
        summary = run_parity(plan, torch_loader, bridge, train_load, held)
    write_summary(plan, summary)
    return summary


# --------------------------------------------------------------------------
# Synthetic default mode
# --------------------------------------------------------------------------

class SyntheticGraph:
    """Invented heterogeneous graph exposing the fields the check reads."""

    def __init__(self, stores, edges, diagnosis=True):
        """Store node and edge stores as simple namespaces."""
        self.stores = dict(stores)
        self.stores.update(edges)
        self.node_types = list(stores)
        self.edge_types = list(edges)
        if diagnosis:
            self.y_diagnosis = np.zeros((1, 1), dtype=np.float32)

    def __getitem__(self, key):
        """Return the store for a node or edge type."""
        return self.stores[key]


def synthetic_reference_preprocess(graphs):
    """Reference preprocessing for invented fixtures (not upstream code)."""
    import types
    filtered = [graph for graph in graphs if is_diagnosis_graph(graph)]
    oracle, _ = build_oracle(filtered)
    result = []
    for graph, expected in zip(filtered, oracle):
        stores = {}
        for key, (value, dtype) in expected.items():
            name = key[1]
            store = stores.setdefault(name, types.SimpleNamespace())
            setattr(store, key[2], value.astype(dtype))
        nodes = {name: stores[name] for name in graph.node_types}
        edges = {name: stores[name] for name in graph.edge_types}
        result.append(SyntheticGraph(nodes, edges))
    return result


def synthetic_graphs():
    """Return a small invented graph list (no patient content)."""
    import types
    graphs = []
    for index, visits in enumerate((3, 2, 4)):
        x = np.arange(visits * 6, dtype=np.float32).reshape(visits, 6) + index
        cond = np.array([[v for v in range(visits)], [0] * visits],
                        dtype=np.int64)
        proc = np.array([[visits - 1], [0]], dtype=np.int64)
        nodes = {VISIT: types.SimpleNamespace(x=x),
                 "condition": types.SimpleNamespace(
                     x=np.ones((1, 2), dtype=np.float32)),
                 "procedure": types.SimpleNamespace(
                     x=np.ones((1, 2), dtype=np.float32))}
        edges = {(VISIT, CONDITION_RELATION, "condition"):
                 types.SimpleNamespace(edge_index=cond),
                 (VISIT, "has_procedure", "procedure"):
                 types.SimpleNamespace(edge_index=proc)}
        graphs.append(SyntheticGraph(nodes, edges, diagnosis=index != 1))
    return graphs


def run_synthetic():
    """Run the parity comparisons on invented fixtures; return a summary."""
    graphs = synthetic_graphs()
    oracle, counts = build_oracle(graphs)
    first = collection_inputs(synthetic_reference_preprocess(graphs))
    second = collection_inputs(synthetic_reference_preprocess(
        synthetic_graphs()))
    compare_to_oracle(first, oracle)
    compare_to_oracle(second, oracle)
    compare_exact(digest_inputs(first), digest_inputs(second))
    return {"status": "passed", "mode": "synthetic", "counts": counts,
            "phase4_eligible": False}


def main(argv=None):
    """Run synthetic mode by default or the explicit operator-real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="operator only: verify the pinned real graph")
    args = parser.parse_args(argv)
    try:
        summary = run_real() if args.operator_real else run_synthetic()
    except (ParityError, mapping.MappingError) as error:
        print("status: failed (%s)" % error)
        return 1
    print("status: %s" % summary["status"])
    for name, value in sorted(summary["counts"].items()):
        print("%s: %d" % (name, value))
    if args.operator_real:
        print("summary written to the protected verification directory")
    return 0


if __name__ == "__main__":
    sys.exit(main())
