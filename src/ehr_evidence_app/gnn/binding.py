#!/usr/bin/env python3
"""Run-binding verification of the inherited GNN (Phase 3 closure).

The canonical diag_kg checkpoint stores the arguments of its training run,
including the diagnosis pos_weight values the trainer computed from the
label counts of its training partition, and the checkpoint and metrics file
store the run's validation and test metrics. This module replays the pinned
upstream split over the current graph collection and checks that:

* the replayed training partition reproduces the saved pos_weight exactly;
* a batched re-evaluation of the checkpoint on the replayed validation and
  test partitions reproduces the stored metrics within a fixed tolerance;
* negative controls fail: a different seed and a reversed label order do
  not reproduce the exact-count pos_weight values, and a different seed
  does not reproduce the test metrics.

Together these bind the checkpoint, the graph labels, the label order and
the split to one another, which establishes upstream partition membership
for the development pilot. The module also measures how the single-graph
bridge prediction of each pilot case changes when the same graph is
evaluated inside a batch, because the model normalises its visit structural
encodings by batch maxima.

Every input is pinned by SHA-256 and read once; the graphs and checkpoint
are deserialised through their verified open handles. Upstream model,
bridge and trainer code runs from its verified source bytes with the
tracker offline, network access refused, tracker and ETL imports refused,
training mode and optimisers disabled, and all upstream output discarded.
Nothing is trained and no upstream file is written.

The operator-only real mode (--operator-real, GNN interpreter, CUDA)
publishes one new protected directory that is never overwritten:

  summary.json  aggregate only: statuses, counts, metric differences,
                partition digests and batch-sensitivity statistics;
  cases.json    per pilot case: upstream partition, whether the
                single-graph prediction reproduces the published label,
                and batch stability.

The default mode runs invented fixtures in memory and writes nothing.

Usage:
    python -m ehr_evidence_app.gnn.binding
    python -m ehr_evidence_app.gnn.binding --operator-real
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib
import importlib.abc
import json
import math
import os
import platform
import random
import statistics
import sys

from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.gnn import outputs as pmo
from ehr_evidence_app.gnn import provenance as amp
from ehr_evidence_app.gnn import split as usm
from ehr_evidence_app.storage import policy as guard

SCHEMA = "inherited-run-binding"
CASES_SCHEMA = "inherited-run-binding-cases"
SCHEMA_VERSION = "1.0"
OUTPUT_VERSION = "inherited-run-binding-v1"
PROTECTED_ROOT = pmo.PROTECTED_ROOT
OUTPUT_DIR = PROTECTED_ROOT + "\\verification\\" + OUTPUT_VERSION
SUMMARY_NAME = "summary.json"
CASES_NAME = "cases.json"
PARTIAL = ".partial"
VERSION_PREFIX = "run-binding-prep-"
VERSION_HEX_CHARS = 16

VERIFIED = "verified"
FAILED = "failed"
PARTITIONS = ("train", "validation", "test")
METRIC_ATOL = 1e-6
METRIC_KEYS = ("auroc_macro", "auprc_macro", "code_recall",
               "visit_precision", "loss")
POS_WEIGHT_CAPS = {"cap50": 50.0}
CONTROL_SEED_OFFSET = 1
BATCH_SIZES = (64, 128)
COMPANION_SEED = 12345
DEVICE = "cuda"
PILOT_RECORDS = pmo.PILOT_RECORDS
EXPECTED_RUN = {"task": "diagnosis", "data_source": "omop", "no_kg": False}

SOURCE_ROLES = ("model", "explain_patient", "train")
JSON_ROLES = ("metrics", "label_vocab", "concept_vocab", "mapping",
              "outputs", "parity")
STREAM_ROLES = ("graph", "checkpoint")
ARG_KEYS = ("task", "data_source", "no_kg", "seed", "val_frac",
            "test_frac", "batch_size", "pw_mode")
FORBIDDEN_IMPORTS = {
    "etl": (("build_patient_graphs", "preprocess"),
            ("src.preprocess", "src.etl")),
    "tracker": (("clearml", "wandb", "mlflow", "comet_ml", "neptune"), ()),
}

LIMITATIONS = [
    "Development pilot of ten verified cases; the batch-sensitivity "
    "statistics describe these cases only.",
    "Metric reproduction re-evaluates the checkpoint on CUDA; GPU "
    "arithmetic can differ in the last bits between runs, which the fixed "
    "tolerance absorbs.",
    "Upstream partition membership is a training-data fact for this run; it "
    "is not a claim about clinical novelty. The label vocabulary and the "
    "feature normalisation were computed over all patients.",
    "Aggregate record: no identifier, label, concept, score or per-patient "
    "value appears in summary.json.",
]


class BindingError(Exception):
    """A fail-closed precondition failure carrying a finding code only."""


# --------------------------------------------------------------------------
# Plan
# --------------------------------------------------------------------------

def real_plan():
    """Return the pinned inputs and the fixed protected output."""
    step20 = pmo.real_plan()["assets"]
    step22 = usm.real_plan()["inputs"]
    step21 = amp.real_plan()["inputs"]
    return {
        "upstream_root": pmo.UPSTREAM_ROOT,
        "assets": {
            "model": dict(step20["model"]),
            "explain_patient": dict(step20["explain_patient"]),
            "train": dict(step22["train_source"]),
            "graph": dict(step20["graph"]),
            "checkpoint": dict(step20["checkpoint"]),
            "metrics": dict(step22["metrics"]),
            "label_vocab": dict(step20["label_vocab"]),
            "concept_vocab": dict(step20["concept_vocab"]),
            "mapping": dict(step20["step17_mapping"]),
            "outputs": dict(step21["step20_outputs"]),
            "parity": dict(step20["step19_summary"]),
        },
        "output_dir": OUTPUT_DIR,
        "protected_root": PROTECTED_ROOT,
    }


def plan_problem(plan):
    """Return a finding code when the plan is not fully pinned."""
    assets = plan.get("assets") if isinstance(plan, dict) else None
    roles = SOURCE_ROLES + JSON_ROLES + STREAM_ROLES
    if not isinstance(assets, dict) or set(assets) != set(roles):
        return "plan_roles_unexpected"
    for name in roles:
        entry = assets[name]
        digest = entry.get("sha256") if isinstance(entry, dict) else None
        if (not isinstance(entry.get("path"), str)
                or not isinstance(digest, str) or len(digest) != 64
                or any(c not in "0123456789abcdefABCDEF" for c in digest)):
            return "plan_pin_invalid:%s" % name
    return None


# --------------------------------------------------------------------------
# Canonical JSON and fingerprints
# --------------------------------------------------------------------------

def canonical_json(value):
    """Return canonical UTF-8 JSON bytes (sorted, compact, no NaN)."""
    try:
        text = json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise BindingError("value_not_canonical_json") from None
    return text.encode("utf-8")


def digest(value):
    """Return the SHA-256 hex digest of a value's canonical JSON."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def encode(document):
    """Return strict, sorted JSON bytes with a final newline."""
    try:
        text = json.dumps(document, indent=2, sort_keys=True,
                          allow_nan=False)
    except (TypeError, ValueError):
        raise BindingError("record_not_serialisable") from None
    return (text + "\n").encode("utf-8")


# --------------------------------------------------------------------------
# Pure checks (no torch): split, pos_weight, metrics, batch statistics
# --------------------------------------------------------------------------

def strict_int(value, minimum=0):
    """Return True for a plain int (not bool) of at least minimum."""
    return type(value) is int and value >= minimum


def check_args(saved, metrics_args):
    """Return the run arguments after checking identity and agreement.

    The checkpoint's saved arguments decide; the metrics file's saved
    arguments must agree on every checked key when present. Parser
    defaults are never used.
    """
    if not isinstance(saved, dict):
        raise BindingError("checkpoint_args_missing")
    args = {}
    for key in ARG_KEYS:
        if key not in saved:
            raise BindingError("checkpoint_arg_missing:%s" % key)
        args[key] = saved[key]
    for key, expected in EXPECTED_RUN.items():
        if args[key] != expected or type(args[key]) is not type(expected):
            raise BindingError("run_identity_unexpected:%s" % key)
    if not strict_int(args["seed"]) or not strict_int(args["batch_size"], 1):
        raise BindingError("checkpoint_arg_invalid")
    for key in ("val_frac", "test_frac"):
        value = args[key]
        if (type(value) is not float or not math.isfinite(value)
                or not 0.0 < value < 1.0):
            raise BindingError("checkpoint_arg_invalid")
    if args["val_frac"] + args["test_frac"] >= 1.0:
        raise BindingError("checkpoint_arg_invalid")
    if args["pw_mode"] not in POS_WEIGHT_CAPS:
        raise BindingError("pos_weight_mode_unsupported")
    if metrics_args is not None:
        if not isinstance(metrics_args, dict):
            raise BindingError("metrics_args_invalid")
        for key in ARG_KEYS:
            if key in metrics_args and metrics_args[key] != args[key]:
                raise BindingError("metrics_args_disagree:%s" % key)
    return args


def replay_split(n, val_frac, test_frac, seed):
    """Replay the pinned split_graphs index logic over positions 0..n-1.

    Mirrors the pinned trainer: numpy default_rng(seed).permutation(n),
    then test, validation and training slices with floor-based sizes.
    """
    import numpy  # Imported lazily; only the split replay needs NumPy.
    order = numpy.random.default_rng(seed).permutation(n).tolist()
    n_test = int(n * test_frac)
    n_val = int(n * val_frac)
    return {"train": order[n_test + n_val:],
            "validation": order[n_test:n_test + n_val],
            "test": order[:n_test]}


def partition_digest(positions):
    """Return the SHA-256 of a partition's ascending positions."""
    return digest(sorted(positions))


def check_partitions(partitions, n, val_frac, test_frac):
    """Return aggregate partition facts; fail on any integrity problem."""
    if set(partitions) != set(PARTITIONS):
        raise BindingError("split_partitions_unexpected")
    seen = set()
    total = 0
    for name in PARTITIONS:
        items = partitions[name]
        if not all(strict_int(item) and item < n for item in items):
            raise BindingError("split_position_invalid")
        seen.update(items)
        total += len(items)
    if total != n or len(seen) != n:
        raise BindingError("split_not_a_partition")
    expected = {"test": int(n * test_frac), "validation": int(n * val_frac)}
    expected["train"] = n - expected["test"] - expected["validation"]
    counts = {name: len(partitions[name]) for name in PARTITIONS}
    if counts != expected:
        raise BindingError("split_sizes_unexpected")
    return {"counts": counts, "pairwise_disjoint": True,
            "full_coverage": True,
            "partition_sha256": {name: partition_digest(partitions[name])
                                 for name in PARTITIONS}}


def finite_list(values, name):
    """Return values as a list of finite floats, or fail closed."""
    if not isinstance(values, (list, tuple)) or not values:
        raise BindingError("%s_invalid" % name)
    result = []
    for value in values:
        if (type(value) not in (int, float) or isinstance(value, bool)
                or not math.isfinite(value)):
            raise BindingError("%s_invalid" % name)
        result.append(float(value))
    return result


def compare_pos_weight(replayed, saved, cap):
    """Compare replayed and saved pos_weight vectors element by element.

    Values below the cap encode exact label counts of the training
    partition; they are the discriminating part of the comparison.
    """
    replayed = finite_list(replayed, "pos_weight_replayed")
    saved = finite_list(saved, "pos_weight_saved")
    if len(replayed) != len(saved):
        raise BindingError("pos_weight_length_mismatch")
    exact = [index for index, value in enumerate(saved) if value < cap]
    equal = [a == b for a, b in zip(replayed, saved)]
    return {"labels": len(saved),
            "equal_values": sum(equal),
            "exact_count_values": len(exact),
            "exact_count_equal": sum(equal[index] for index in exact),
            "bitwise_equal": all(equal)}


def metric_differences(got, reference):
    """Return the maximum absolute difference per metric key."""
    if not isinstance(got, dict) or not isinstance(reference, dict):
        raise BindingError("metrics_invalid")
    result = {}
    for key in METRIC_KEYS:
        if key not in got or key not in reference:
            raise BindingError("metric_missing:%s" % key)
        a, b = got[key], reference[key]
        if isinstance(b, list):
            a = finite_list(a, "metric_%s" % key)
            b = finite_list(b, "metric_%s" % key)
            if len(a) != len(b):
                raise BindingError("metric_length_mismatch:%s" % key)
            result[key] = max(abs(x - y) for x, y in zip(a, b))
        else:
            a = finite_list([a], "metric_%s" % key)[0]
            b = finite_list([b], "metric_%s" % key)[0]
            result[key] = abs(a - b)
    return result


def within(differences, tolerance=METRIC_ATOL):
    """Return True when every metric difference is within tolerance."""
    return all(value <= tolerance for value in differences.values())


def batch_statistics(rows):
    """Return aggregate batch-sensitivity statistics over pilot rows."""
    if not rows:
        raise BindingError("batch_rows_missing")
    result = {"cases": len(rows),
              "single_graph_reproduces_published":
                  sum(bool(row["reproduces_published"]) for row in rows),
              "argmax_unchanged": {}, "selected_abs_logit_shift": {},
              "any_label_max_abs_logit_shift": {}, "selected_rank_max": {}}
    for size in BATCH_SIZES:
        key = str(size)
        cells = [row["batches"][key] for row in rows]
        selected = finite_list([cell["selected_shift"] for cell in cells],
                               "batch_selected_shift")
        largest = finite_list([cell["max_shift"] for cell in cells],
                              "batch_max_shift")
        ranks = [cell["selected_rank"] for cell in cells]
        if not all(strict_int(rank, 1) for rank in ranks):
            raise BindingError("batch_rank_invalid")
        result["argmax_unchanged"][key] = sum(
            bool(cell["argmax_unchanged"]) for cell in cells)
        result["selected_abs_logit_shift"][key] = {
            "median": statistics.median(selected), "max": max(selected)}
        result["any_label_max_abs_logit_shift"][key] = {
            "median": statistics.median(largest), "max": max(largest)}
        result["selected_rank_max"][key] = max(ranks)
    return result


def evaluate_binding(run):
    """Return (status, checks) for a completed run's comparisons."""
    checks = {
        "pos_weight_bitwise_equal": run["pos_weight"]["bitwise_equal"],
        "validation_metrics_within_tolerance":
            within(run["metrics"]["validation"]),
        "test_metrics_within_tolerance": within(run["metrics"]["test"]),
        "wrong_seed_control_failed":
            (run["controls"]["wrong_seed"]["exact_count_equal"]
             < run["pos_weight"]["exact_count_values"]
             and not within(run["metrics"]["wrong_seed_test"])),
        "reversed_label_control_failed":
            (run["controls"]["reversed_label_order"]["exact_count_equal"]
             < run["pos_weight"]["exact_count_values"]),
        "split_replay_matches_upstream": run["split_replay_matches"],
    }
    status = VERIFIED if all(checks.values()) else FAILED
    return status, checks


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def output_preconditions(plan):
    """Refuse an existing, unprotected or input-overlapping output."""
    for path in (plan["output_dir"], plan["output_dir"] + PARTIAL):
        for entry in plan["assets"].values():
            if (guard.is_within(entry["path"], path)
                    or guard.is_within(path, os.path.dirname(entry["path"]))):
                raise BindingError("output_overlaps_input")
        if os.path.lexists(path):
            raise BindingError("output_exists")
        problem = mapping.output_problem(path, plan["protected_root"])
        if problem:
            raise BindingError(problem)


def verify_inputs(plan, stack):
    """Verify every pin; parse JSON only from the verified bytes.

    Returns (held identities, parsed documents, source bytes, streams).
    """
    held, raw = [], {}
    try:
        for name in SOURCE_ROLES + JSON_ROLES:
            raw[name] = pmo.verify_read(plan["assets"][name], name, held)
        streams = {name: pmo.verify_stream(plan["assets"][name], name, held,
                                           stack)
                   for name in STREAM_ROLES}
        pmo.confirm_inputs(held)
    except pmo.Step20Error as error:
        raise BindingError(str(error)) from None
    documents = {}
    for name in JSON_ROLES:
        try:
            documents[name] = mapping.parse_json(raw[name])
        except (UnicodeDecodeError, ValueError):
            raise BindingError("input_not_json:%s" % name) from None
    sources = {name: raw[name] for name in SOURCE_ROLES}
    return held, documents, sources, streams


def select_pilot(document, plan):
    """Return the ten verified pilot selections (identity mapping rules)."""
    try:
        return pmo.select_pilot(document, {"assets": plan["assets"]})
    except pmo.Step20Error as error:
        raise BindingError(str(error)) from None


def published_labels(document, selection):
    """Return {patient_index: label_index} from the published outputs.

    The outputs must hold exactly the selected cases in ascending
    patient_index order, each a valid bridge record.
    """
    if not isinstance(document, list) or len(document) != len(selection):
        raise BindingError("outputs_record_count")
    labels = {}
    for record, item in zip(document, selection):
        if pmo.schema_problem(record):
            raise BindingError("outputs_record_invalid")
        if record["patient_index"] != item["patient_index"]:
            raise BindingError("outputs_selection_mismatch")
        labels[record["patient_index"]] = (
            record["predicted_diagnosis"]["label_index"])
    return labels


def check_parity(document, plan):
    """Return the approved preprocessing counts and model-input digest."""
    try:
        return pmo.check_step19(document, {"assets": {
            "graph": plan["assets"]["graph"],
            "explain_patient": plan["assets"]["explain_patient"],
            "model": plan["assets"]["model"]}})
    except pmo.Step20Error as error:
        raise BindingError(str(error)) from None


def metrics_reference(document):
    """Return (test metrics, saved metrics args) from the metrics file."""
    if not isinstance(document, dict):
        raise BindingError("metrics_invalid")
    for key, expected in EXPECTED_RUN.items():
        if document.get(key) != expected:
            raise BindingError("run_identity_unexpected:%s" % key)
    test = document.get("test")
    if not isinstance(test, dict):
        raise BindingError("metrics_test_missing")
    return test, document.get("args")


# --------------------------------------------------------------------------
# Isolation for upstream evaluation
# --------------------------------------------------------------------------

def forbidden_category(name):
    """Return the forbidden category of a module name, else None."""
    top = name.split(".")[0]
    for category, (tops, prefixes) in FORBIDDEN_IMPORTS.items():
        if top in tops or any(name == prefix or name.startswith(prefix + ".")
                              for prefix in prefixes):
            return category
    return None


class _ImportBlocker(importlib.abc.MetaPathFinder):
    """Meta-path finder refusing tracker and ETL/graph-builder modules."""

    def __init__(self, trips):
        """Bind the tripwire recorder."""
        self.trips = trips

    def find_spec(self, fullname, path=None, target=None):
        """Refuse a forbidden module; defer everything else."""
        category = forbidden_category(fullname)
        if category:
            self.trips.hits.append("forbidden_import:%s" % category)
            raise ImportError("import refused by the run-binding tripwire")
        return None


@contextlib.contextmanager
def imports_blocked(trips):
    """Install the forbidden-import finder for the duration of the block."""
    blocker = _ImportBlocker(trips)
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        with contextlib.suppress(ValueError):
            sys.meta_path.remove(blocker)


@contextlib.contextmanager
def evaluation_tripwires(torch, trips):
    """Refuse training mode and optimiser creation; allow evaluation loaders.

    Module.train(False), used by eval(), stays allowed. The originals are
    restored in finally.
    """
    module = torch.nn.Module
    optimizer = torch.optim.Optimizer
    saved = (module.train, optimizer.__init__)
    original_train = saved[0]

    def guarded_train(self, mode=True):
        """Refuse switching into training mode."""
        if mode:
            trips.trip("training_mode_used")
        return original_train(self, mode)

    def refuse_optimizer(self, *args, **kwargs):
        """Refuse any optimiser construction."""
        trips.trip("optimizer_created")

    module.train = guarded_train
    optimizer.__init__ = refuse_optimizer
    try:
        yield
    finally:
        module.train, optimizer.__init__ = saved


@contextlib.contextmanager
def isolated(trips):
    """Tracker offline, network refused, forbidden imports blocked, silent."""
    with pmo.tracker_offline(), pmo.network_blocked(trips), \
            imports_blocked(trips), pmo.silenced():
        yield


def load_upstream(plan, sources):
    """Execute model, bridge and trainer only from their verified bytes.

    The trainer and bridge import PatientGNN from a module named model, so
    the verified model module is registered under that name while they
    execute; sys.path and sys.modules are restored afterwards.
    """
    saved_path = list(sys.path)
    sentinel = object()
    saved_model = sys.modules.get("model", sentinel)
    try:
        model = pmo.module_from_verified_source(
            "model", plan["assets"]["model"]["path"], sources["model"])
        sys.modules["model"] = model
        bridge = pmo.module_from_verified_source(
            "run_binding_upstream_explain_patient",
            plan["assets"]["explain_patient"]["path"],
            sources["explain_patient"])
        trainer = pmo.module_from_verified_source(
            "run_binding_upstream_train", plan["assets"]["train"]["path"],
            sources["train"])
    except pmo.Step20Error as error:
        raise BindingError(str(error)) from None
    finally:
        sys.path[:] = saved_path
        if saved_model is sentinel:
            sys.modules.pop("model", None)
        else:
            sys.modules["model"] = saved_model
    return bridge, trainer


# --------------------------------------------------------------------------
# Real runtime (GNN interpreter only)
# --------------------------------------------------------------------------

class TorchRuntime:
    """Runs the upstream-dependent part of the verification on CUDA."""

    def __init__(self):
        """Import torch lazily (the application environment excludes it)."""
        self.torch = importlib.import_module("torch")

    def versions(self):
        """Return runtime versions only (no device or host names)."""
        import numpy
        versions = {"python": platform.python_version(),
                    "torch": str(getattr(self.torch, "__version__", "")),
                    "numpy": str(numpy.__version__)}
        for name in ("torch_geometric", "sklearn"):
            module = importlib.import_module(name)
            versions[name] = str(getattr(module, "__version__", ""))
        return versions

    def run(self, plan, sources, streams, documents, selection, published,
            step19, trips):
        """Return plain comparison data for the binding record."""
        torch = self.torch
        pmo.require_cuda(torch)
        device = torch.device(DEVICE)
        graphs = streams["graph"].load(pmo.torch_graph_loader)
        checkpoint = streams["checkpoint"].load(
            torch.load, map_location="cpu", weights_only=False)
        if not isinstance(graphs, list) or not isinstance(checkpoint, dict):
            raise BindingError("upstream_objects_unexpected")
        try:
            pmo.check_selection_graphs(graphs, selection)
        except pmo.Step20Error as error:
            raise BindingError(str(error)) from None
        test_reference, metrics_args = metrics_reference(
            documents["metrics"])
        args = check_args(checkpoint.get("args"), metrics_args)
        saved_pw = checkpoint["args"].get("_diag_pos_weight")
        if not isinstance(saved_pw, torch.Tensor) or saved_pw.dim() != 1:
            raise BindingError("pos_weight_saved_invalid")
        val_reference = checkpoint.get("val_metrics")
        filtered = [graph for graph in graphs
                    if hasattr(graph, "y_diagnosis")]
        n = len(filtered)
        if len(graphs) != step19["graphs_total"] or n != step19[
                "graphs_filtered"]:
            raise BindingError("graph_count_mismatch")
        bridge, trainer = load_upstream(plan, sources)
        with evaluation_tripwires(torch, trips):
            upstream = trainer.split_graphs(list(range(n)), args["val_frac"],
                                            args["test_frac"], args["seed"])
            partitions = dict(zip(PARTITIONS, (list(part)
                                               for part in upstream)))
            replayed = replay_split(n, args["val_frac"], args["test_frac"],
                                    args["seed"])
            control = replay_split(n, args["val_frac"], args["test_frac"],
                                   args["seed"] + CONTROL_SEED_OFFSET)
            cap = POS_WEIGHT_CAPS[args["pw_mode"]]
            labels = torch.stack([graph.y_diagnosis for graph in filtered])
            reverse = torch.arange(labels.shape[1] - 1, -1, -1)

            def pos_weight(positions, order=None):
                """Replay the trainer's cap-mode pos_weight on positions."""
                chosen = labels[positions]
                if order is not None:
                    chosen = chosen[:, order]
                n_pos = chosen.sum(0)
                n_neg = len(positions) - n_pos
                return (n_neg / n_pos.clamp(min=1)).clamp(max=cap)

            saved = saved_pw.tolist()
            weight = compare_pos_weight(
                pos_weight(partitions["train"]).tolist(), saved, cap)
            wrong_seed = compare_pos_weight(
                pos_weight(control["train"]).tolist(), saved, cap)
            reversed_order = compare_pos_weight(
                pos_weight(partitions["train"], reverse).tolist(), saved, cap)
            del labels
            processed = bridge.preprocess(filtered)
            try:
                pmo.check_preprocessed(graphs, processed, step19)
            except pmo.Step20Error as error:
                raise BindingError(str(error)) from None
            label_vocab = documents["label_vocab"]
            concept_vocab = documents["concept_vocab"]
            sizes = {short: bridge._vocab_size(concept_vocab, domain)
                     for short, domain in (("cond", "condition"),
                                           ("proc", "procedure"),
                                           ("drug", "drug"))}
            model = bridge.build_model(checkpoint, len(label_vocab), sizes,
                                       device)
            model.eval()
            namespace = argparse.Namespace(task="diagnosis",
                                           _diag_pos_weight=saved_pw)

            def evaluate(positions):
                """Batched evaluation exactly as the trainer's eval loop."""
                loader = trainer.DataLoader(
                    [processed[i] for i in positions],
                    batch_size=args["batch_size"] * 2, shuffle=False,
                    num_workers=0)
                result, _ = trainer.run_epoch(model, loader, None, device,
                                              namespace, train=False)
                return result

            metrics = {
                "validation": metric_differences(
                    evaluate(partitions["validation"]), val_reference),
                "test": metric_differences(evaluate(partitions["test"]),
                                           test_reference),
                "wrong_seed_test": metric_differences(
                    evaluate(control["test"]), test_reference)}
            rows = self.batch_rows(model, processed, selection, published,
                                   device)
        trips.check()
        return {"graphs_total": len(graphs), "graphs_filtered": n,
                "args": {key: args[key] for key in ARG_KEYS},
                "partitions": partitions,
                "split_replay_matches": replayed == partitions,
                "pos_weight": weight,
                "controls": {"wrong_seed": wrong_seed,
                             "reversed_label_order": reversed_order},
                "metrics": metrics, "batch_rows": rows}

    def batch_rows(self, model, processed, selection, published, device):
        """Compare single-graph and in-batch logits for each pilot case."""
        torch = self.torch
        from torch_geometric.data import Batch
        pilot = [item["patient_index"] for item in selection]
        generator = torch.Generator().manual_seed(COMPANION_SEED)
        excluded = set(pilot)
        others = [index for index in torch.randperm(
            len(processed), generator=generator).tolist()
            if index not in excluded]
        rows = []
        with torch.no_grad():
            for position, index in enumerate(pilot):
                single = model(processed[index].clone().to(device),
                               task="diagnosis")["diagnosis"][0]
                single = single.float().cpu()
                label = published[index]
                row = {"reproduces_published":
                           int(single.argmax()) == label,
                       "batches": {}}
                for size in BATCH_SIZES:
                    offset = position * size
                    members = [processed[index].clone()] + [
                        processed[other].clone()
                        for other in others[offset:offset + size - 1]]
                    batch = Batch.from_data_list(members).to(device)
                    batched = model(batch, task="diagnosis")["diagnosis"][0]
                    batched = batched.float().cpu()
                    row["batches"][str(size)] = {
                        "argmax_unchanged":
                            int(batched.argmax()) == int(single.argmax()),
                        "selected_shift":
                            abs(float(single[label] - batched[label])),
                        "max_shift": float((single - batched).abs().max()),
                        "selected_rank":
                            int((batched > batched[label]).sum()) + 1}
                rows.append(row)
        return rows


# --------------------------------------------------------------------------
# Records and publication
# --------------------------------------------------------------------------

def input_refs(plan):
    """Return path, size and SHA-256 of every pinned input."""
    refs = {}
    for name, entry in sorted(plan["assets"].items()):
        ref = {"path": entry["path"], "sha256": entry["sha256"].lower()}
        if "size_bytes" in entry:
            ref["size_bytes"] = entry["size_bytes"]
        refs[name] = ref
    return refs


def build_records(plan, run, selection, versions):
    """Return (summary, cases) documents for a completed run."""
    args = run["args"]
    split = check_partitions(run["partitions"], run["graphs_filtered"],
                             args["val_frac"], args["test_frac"])
    status, checks = evaluate_binding(run)
    membership = {}
    for name in PARTITIONS:
        for position in run["partitions"][name]:
            membership[position] = name
    cases = []
    for position, (item, row) in enumerate(zip(selection, run["batch_rows"])):
        cases.append({
            "case_position": position,
            "patient_index": item["patient_index"],
            "graph_position": item["graph_position"],
            "upstream_partition": membership[item["patient_index"]],
            "single_graph_reproduces_published":
                bool(row["reproduces_published"]),
            "batch": {key: {"argmax_unchanged":
                                bool(cell["argmax_unchanged"]),
                            "selected_label_rank": cell["selected_rank"]}
                      for key, cell in sorted(row["batches"].items())}})
    cases_document = {"schema": CASES_SCHEMA, "schema_version": SCHEMA_VERSION,
                      "version": OUTPUT_VERSION, "cases": cases,
                      "patient_bearing": True}
    pilot_counts = {name: sum(case["upstream_partition"] == name
                              for case in cases) for name in PARTITIONS}
    body = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "version": OUTPUT_VERSION, "status": status, "checks": checks,
        "inputs": input_refs(plan), "runtime": versions,
        "run_arguments": {key: args[key] for key in ARG_KEYS
                          if key != "pw_mode"},
        "pos_weight": dict(run["pos_weight"], mode=args["pw_mode"]),
        "split": dict(split, graphs_total=run["graphs_total"],
                      graphs_filtered=run["graphs_filtered"],
                      rule="pinned trainer split_graphs over bridge-filtered "
                           "positions; replayed independently with NumPy"),
        "controls": {
            "wrong_seed": dict(
                run["controls"]["wrong_seed"],
                seed_offset=CONTROL_SEED_OFFSET,
                test_metric_max_abs_difference=run["metrics"][
                    "wrong_seed_test"]),
            "reversed_label_order": dict(
                run["controls"]["reversed_label_order"])},
        "metrics": {
            "tolerance": METRIC_ATOL,
            "validation": {"reference": "checkpoint val_metrics",
                           "max_abs_difference": run["metrics"]["validation"]},
            "test": {"reference": "metrics.json test",
                     "max_abs_difference": run["metrics"]["test"]},
            "evaluation": "trainer run_epoch, batch_size x2, no shuffle, "
                          "CUDA"},
        "batch_sensitivity": dict(batch_statistics(run["batch_rows"]),
                                  batch_sizes=list(BATCH_SIZES),
                                  companion_seed=COMPANION_SEED),
        "pilot_partition_counts": pilot_counts,
        "cases_file": {"name": CASES_NAME,
                       "sha256": hashlib.sha256(
                           encode(cases_document)).hexdigest()},
        "limitations": list(LIMITATIONS),
    }
    value = digest(body)
    summary = dict(body, preparation={
        "fingerprint_sha256": value,
        "version": VERSION_PREFIX + value[:VERSION_HEX_CHARS]})
    return summary, cases_document


def remove_partial(partial):
    """Delete this run's own partial directory after a failure."""
    for name in (SUMMARY_NAME, CASES_NAME):
        target = os.path.join(partial, name)
        if os.path.lexists(target):
            os.remove(target)
    if os.path.isdir(partial):
        os.rmdir(partial)


def publish(plan, summary, cases, held):
    """Write both files through a new .partial directory and rename it.

    Every input is re-hashed after the partial write and before the rename;
    a failure removes only this run's partial directory. Returns the
    SHA-256 of each published file.
    """
    output_preconditions(plan)
    files = {SUMMARY_NAME: encode(summary), CASES_NAME: encode(cases)}
    partial = plan["output_dir"] + PARTIAL
    os.makedirs(os.path.dirname(plan["output_dir"]), exist_ok=True)
    os.mkdir(partial)
    try:
        for name, data in files.items():
            with open(os.path.join(partial, name), "xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            pmo.confirm_inputs(held, rehash=True)
        except pmo.Step20Error as error:
            raise BindingError(str(error)) from None
        try:
            os.rename(partial, plan["output_dir"])
        except OSError:
            raise BindingError("output_rename_failed") from None
    except BaseException:
        remove_partial(partial)
        raise
    return {name: hashlib.sha256(data).hexdigest()
            for name, data in files.items()}


def run_real(plan=None, runtime_factory=TorchRuntime):
    """Operator-only real run in the GNN interpreter (CUDA required)."""
    plan = plan or real_plan()
    problem = guard.fixed_policy_problem() or plan_problem(plan)
    if problem:
        raise BindingError(problem)
    output_preconditions(plan)
    runtime = runtime_factory()
    trips = pmo.Tripwires()
    with contextlib.ExitStack() as stack:
        held, documents, sources, streams = verify_inputs(plan, stack)
        selection = select_pilot(documents["mapping"], plan)
        published = published_labels(documents["outputs"], selection)
        step19 = check_parity(documents["parity"], plan)
        try:
            with isolated(trips):
                run = runtime.run(plan, sources, streams, documents,
                                  selection, published, step19, trips)
            trips.check()
        except pmo.Step20Error as error:
            raise BindingError(str(error)) from None
        versions = runtime.versions()
        summary, cases = build_records(plan, run, selection, versions)
        digests = publish(plan, summary, cases, held)
    return summary, digests


# --------------------------------------------------------------------------
# Synthetic default mode (invented fixtures only)
# --------------------------------------------------------------------------

SYNTHETIC_GRAPHS = 40
SYNTHETIC_LABELS = 6
SYNTHETIC_SEED = 7


def synthetic_labels(count=SYNTHETIC_GRAPHS, width=SYNTHETIC_LABELS):
    """Return an invented binary label matrix."""
    rng = random.Random(SYNTHETIC_SEED)
    return [[1.0 if rng.random() < 0.3 else 0.0 for _ in range(width)]
            for _ in range(count)]


def synthetic_pos_weight(labels, positions, cap, order=None):
    """Pure-Python version of the trainer's cap-mode pos_weight."""
    width = len(labels[0])
    order = list(range(width)) if order is None else order
    result = []
    for column in order:
        positive = sum(labels[row][column] for row in positions)
        negative = len(positions) - positive
        result.append(min(negative / max(positive, 1.0), cap))
    return result


def synthetic_run(tamper=False):
    """Return an invented run whose saved values do (or do not) bind."""
    labels = synthetic_labels()
    cap = POS_WEIGHT_CAPS["cap50"]
    args = {"task": "diagnosis", "data_source": "omop", "no_kg": False,
            "seed": 3, "val_frac": 0.1, "test_frac": 0.15, "batch_size": 4,
            "pw_mode": "cap50"}
    partitions = replay_split(SYNTHETIC_GRAPHS, args["val_frac"],
                              args["test_frac"], args["seed"])
    control = replay_split(SYNTHETIC_GRAPHS, args["val_frac"],
                           args["test_frac"], args["seed"] + 1)
    saved = synthetic_pos_weight(labels, partitions["train"], cap)
    if tamper:
        saved[0] += 1.0
    reverse = list(range(SYNTHETIC_LABELS - 1, -1, -1))
    reference = {"auroc_macro": 0.8, "auprc_macro": 0.3,
                 "code_recall": [0.5, 0.6, 0.7],
                 "visit_precision": [0.4, 0.5, 0.6], "loss": 1.2}
    near = dict(reference, auroc_macro=0.8 + 1e-9)
    far = dict(reference, auroc_macro=0.77)
    rows = [{"reproduces_published": True,
             "batches": {str(size): {"argmax_unchanged": index % 5 != 0,
                                     "selected_shift": 0.1 * (index + 1),
                                     "max_shift": 0.2 * (index + 1),
                                     "selected_rank": 1 + (index % 5 == 0)}
                         for size in BATCH_SIZES}}
            for index in range(3)]
    return {"graphs_total": SYNTHETIC_GRAPHS + 2,
            "graphs_filtered": SYNTHETIC_GRAPHS, "args": args,
            "partitions": partitions, "split_replay_matches": True,
            "pos_weight": compare_pos_weight(
                synthetic_pos_weight(labels, partitions["train"], cap),
                saved, cap),
            "controls": {
                "wrong_seed": compare_pos_weight(
                    synthetic_pos_weight(labels, control["train"], cap),
                    saved, cap),
                "reversed_label_order": compare_pos_weight(
                    synthetic_pos_weight(labels, partitions["train"], cap,
                                         reverse), saved, cap)},
            "metrics": {"validation": metric_differences(near, reference),
                        "test": metric_differences(near, reference),
                        "wrong_seed_test": metric_differences(far,
                                                              reference)},
            "batch_rows": rows}


def synthetic_plan():
    """Return an invented plan (paths never opened in synthetic mode)."""
    assets = {name: {"path": "W:\\synthetic\\inputs\\%s" % name,
                     "sha256": hashlib.sha256(name.encode()).hexdigest()}
              for name in SOURCE_ROLES + JSON_ROLES + STREAM_ROLES}
    return {"upstream_root": "W:\\synthetic\\upstream", "assets": assets,
            "output_dir": "W:\\synthetic\\protected\\verification\\out",
            "protected_root": "W:\\synthetic\\protected"}


def run_synthetic():
    """Build invented bound and tampered runs; return their statuses."""
    plan = synthetic_plan()
    run = synthetic_run()
    selection = [{"patient_index": index, "graph_position": index + 1}
                 for index in sorted(run["partitions"]["test"])[:3]]
    versions = {"python": platform.python_version(), "torch": "synthetic"}
    summary, cases = build_records(plan, run, selection, versions)
    tampered, _ = build_records(plan, synthetic_run(tamper=True), selection,
                                versions)
    if summary["status"] != VERIFIED or tampered["status"] != FAILED:
        raise BindingError("synthetic_expectation_failed")
    return summary, cases, tampered


# --------------------------------------------------------------------------
# Console
# --------------------------------------------------------------------------

def report(summary, digests=None):
    """Print the sanitised aggregate summary."""
    weight = summary["pos_weight"]
    print("Status             : %s" % summary["status"])
    for name, value in sorted(summary["checks"].items()):
        print("  %-38s %s" % (name, "pass" if value else "FAIL"))
    print("pos_weight equal   : %d/%d (exact-count %d/%d)" % (
        weight["equal_values"], weight["labels"],
        weight["exact_count_equal"], weight["exact_count_values"]))
    controls = summary["controls"]
    print("controls           : wrong seed exact-count %d, reversed order "
          "exact-count %d" % (controls["wrong_seed"]["exact_count_equal"],
                              controls["reversed_label_order"][
                                  "exact_count_equal"]))
    for name in ("validation", "test"):
        differences = summary["metrics"][name]["max_abs_difference"]
        print("%-19s: max |diff| %.2e" % (name + " metrics",
                                          max(differences.values())))
    batch = summary["batch_sensitivity"]
    print("single-graph labels: %d/%d reproduce the published label" % (
        batch["single_graph_reproduces_published"], batch["cases"]))
    for key in sorted(batch["argmax_unchanged"], key=int):
        print("batch %-13s: argmax unchanged %d/%d, selected rank max %d" % (
            key, batch["argmax_unchanged"][key], batch["cases"],
            batch["selected_rank_max"][key]))
    counts = summary["pilot_partition_counts"]
    print("pilot partitions   : train %d, validation %d, test %d" % (
        counts["train"], counts["validation"], counts["test"]))
    print("preparation        : %s" % summary["preparation"]["version"])
    for name, value in sorted((digests or {}).items()):
        print("%-19s: %s" % (name, value))


def main(argv=None):
    """Run the synthetic default mode or the operator-only real mode."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-real", action="store_true",
                        help="verify the real inherited run (GNN "
                             "interpreter, CUDA); writes one new protected "
                             "record")
    args = parser.parse_args(argv)
    try:
        if args.operator_real:
            summary, digests = run_real()
            report(summary, digests)
        else:
            summary, _, tampered = run_synthetic()
            report(summary)
            print("tampered control   : %s" % tampered["status"])
    except BindingError as error:
        print("FAILED: %s" % error)
        return 1
    except Exception as error:  # noqa: BLE001 - text may carry values
        print("FAILED: unexpected %s" % type(error).__name__)
        return 2
    return 0 if summary["status"] == VERIFIED else 1


if __name__ == "__main__":
    sys.exit(main())
