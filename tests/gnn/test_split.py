"""Synthetic checks for ehr_evidence_app.gnn.split (Step 22).

Every fixture is invented: an invented Step 21 sidecar built by the Step 21
builder over invented Step 17/19/20 documents, an invented train.py
stand-in, invented checkpoint and metrics argument documents and an
invented RNG provenance record. Replays run over visibly synthetic sentinel
indices with either a fixed invented generator or the installed NumPy. No
real graph, checkpoint, metrics file, mapping, sidecar or patient record is
opened. Scratch files are created only under the configured workspace test temp
directory.
"""

import copy
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.gnn import provenance as amp  # noqa: E402
from ehr_evidence_app.gnn import split as sua  # noqa: E402
from ehr_evidence_app.gnn import mapping as mapping  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402
from ehr_evidence_app.gnn import outputs as pmo  # noqa: E402

try:
    import numpy as np
except ImportError:  # pragma: no cover - app environment provides NumPy
    np = None

PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
PILOT = list(amp.SYNTHETIC_PILOT)
N = 20  # invented Step 19 graphs_filtered
FAKE_VERSION = "synthetic-numpy-0.0"
MARKER = "SYNTHETIC_STEP22_MARKER"
# Invented permutation: pilot positions first, then the rest.
ORDER = PILOT + [value for value in range(N) if value not in PILOT]
N_TEST = int(N * 0.15)
N_VAL = int(N * 0.1)


class FixedGenerator:
    """Invented generator returning a fixed permutation; records seeds."""

    seeds = []

    def __init__(self, seed):
        """Record the seed it was created with."""
        FixedGenerator.seeds.append(seed)

    def permutation(self, n):
        """Return ORDER restricted to n, then any further positions."""
        return [value for value in ORDER if value < n] + list(range(N, n))


def refuse_rng(seed):
    """Fail if a replay is attempted where evidence does not permit it."""
    raise AssertionError("replay must not run")


def fake_runtime(version=FAKE_VERSION, default_rng=FixedGenerator,
                 loader=sua.synthetic_checkpoint_loader, bit="PCG64",
                 synthetic=True):
    """Return an invented replay runtime with a fixed generator."""
    return {"checkpoint_loader": loader,
            "rng": {"numpy_version": version, "bit_generator": bit,
                    "default_rng": default_rng},
            "synthetic_evidence_allowed": synthetic}


def configured_test_temp():
    """Return the configured workspace test scratch directory (workspace-only rules)."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def config_ref():
    """Return the parsed reference of the repository config."""
    return sua.load_config(sua.read_config_bytes(sua.CONFIG_PATH))


def bundle(**changes):
    """Return an invented (plan, raw) whose RNG record names FAKE_VERSION."""
    changes.setdefault("numpy_version", FAKE_VERSION)
    return sua.synthetic_bundle(**changes)


def build(plan, raw, runtime=None):
    """Build a sidecar from invented bytes with the repository config."""
    return sua.build_sidecar(plan, raw, config_ref(),
                             runtime or fake_runtime())


def repin(plan, raw, role, data):
    """Replace one role's bytes and pin the plan to them."""
    raw[role] = data
    plan["inputs"][role]["sha256"] = hashlib.sha256(data).hexdigest()
    plan["inputs"][role].pop("size_bytes", None)


def edit_step19(plan, raw, change):
    """Change the invented Step 19 summary and re-bind Step 21 to it."""
    step19 = json.loads(raw["step19_summary"])
    change(step19)
    data = amp.encode(step19)
    step21 = json.loads(raw["step21_provenance"])
    step21["inputs"]["step19_summary"]["sha256"] = hashlib.sha256(
        data).hexdigest()
    sua.refingerprint(step21)
    repin(plan, raw, "step19_summary", data)
    repin(plan, raw, "step21_provenance", amp.encode(step21))


def set_args(key, value, sources=("checkpoint", "metrics")):
    """Return an evidence change setting (or deleting) one saved argument."""
    def change(checkpoint, metrics):
        """Apply the change to the chosen invented sources."""
        for name, document in (("checkpoint", checkpoint),
                               ("metrics", metrics)):
            if name in sources:
                if value is DELETE:
                    del document["args"][key]
                else:
                    document["args"][key] = value
    return change


DELETE = object()


def run_level(sidecar):
    """Return (status, reason codes) of the split reconstruction."""
    block = sidecar["split_reconstruction"]
    return block["status"], block["reason_codes"]


def expected_block(sidecar):
    """Return (status, counts, reason codes) of the expected counts."""
    block = sidecar["expected_configuration_counts"]
    return block["status"], block["counts"], block["reason_codes"]


FLOOR_COUNTS = {"total": N, "test": N_TEST, "validation": N_VAL,
                "train": N - N_TEST - N_VAL}


def rebind_run(plan, raw):
    """Point both invented run artifacts at the pinned checkpoint/metrics."""
    for role in ("rng_provenance", "run_graph_binding"):
        document = json.loads(raw[role])
        document["run"].update(
            checkpoint_sha256=plan["inputs"]["checkpoint"]["sha256"],
            metrics_sha256=plan["inputs"]["metrics"]["sha256"])
        repin(plan, raw, role, amp.encode(document))


class SplitReplayTestCase(unittest.TestCase):
    """The replay mirrors the pinned slice order exactly."""

    def test_source_slice_order_fixed_generator(self):
        """Test first, validation next, train remainder, from the seed."""
        FixedGenerator.seeds = []
        replay = sua.split_positions(N, 0.1, 0.15, 424242, FixedGenerator)
        self.assertEqual(FixedGenerator.seeds, [424242])
        self.assertEqual(replay["order"], ORDER)
        self.assertEqual(replay["test"], ORDER[:N_TEST])
        self.assertEqual(replay["validation"], ORDER[N_TEST:N_TEST + N_VAL])
        self.assertEqual(replay["train"], ORDER[N_TEST + N_VAL:])
        self.assertIsNone(sua.partition_problem(N, 0.1, 0.15, replay))

    @unittest.skipUnless(np is not None, "NumPy is not installed")
    def test_matches_verbatim_upstream_split_on_sentinels(self):
        """Replay equals the verbatim pinned function on sentinel graphs."""
        log = mock.Mock()  # stands in for the upstream module logger

        def split_graphs(graphs, val_frac, test_frac, seed):
            """Verbatim excerpt of the pinned upstream split function."""
            rng = np.random.default_rng(seed)
            n = len(graphs)
            idx = rng.permutation(n)
            n_test = int(n * test_frac)
            n_val = int(n * val_frac)
            test_idx = idx[:n_test]
            val_idx = idx[n_test:n_test + n_val]
            train_idx = idx[n_test + n_val:]
            train = [graphs[i] for i in train_idx]
            val = [graphs[i] for i in val_idx]
            test = [graphs[i] for i in test_idx]
            log.info("Split: train=%d  val=%d  test=%d", len(train),
                     len(val), len(test))
            return train, val, test

        for n, val_frac, test_frac, seed in ((37, 0.1, 0.15, 7),
                                             (20, 0.1, 0.15, 424242),
                                             (101, 0.2, 0.3, 0),
                                             (9, 0.0, 0.5, 13)):
            sentinels = ["SYNTHETIC-GRAPH-%04d" % value
                         for value in range(n)]
            train, val, test = split_graphs(sentinels, val_frac, test_frac,
                                            seed)
            replay = sua.split_positions(n, val_frac, test_frac, seed,
                                         np.random.default_rng)
            for name, graphs in (("train", train), ("validation", val),
                                 ("test", test)):
                self.assertEqual([sentinels[value] for value in replay[name]],
                                 graphs, (n, name))

    @unittest.skipUnless(np is not None, "NumPy is not installed")
    def test_numpy_replay_is_deterministic(self):
        """Equal seeds replay identically; another seed differs."""
        first = sua.split_positions(500, 0.1, 0.15, 99, np.random.default_rng)
        second = sua.split_positions(500, 0.1, 0.15, 99,
                                     np.random.default_rng)
        other = sua.split_positions(500, 0.1, 0.15, 98,
                                    np.random.default_rng)
        self.assertEqual(first, second)
        self.assertNotEqual(first["order"], other["order"])

    def test_disjoint_full_floor_partitions(self):
        """Partitions are pairwise disjoint, cover 0..n-1, floor counts."""
        generators = [FixedGenerator]
        if np is not None:
            generators.append(np.random.default_rng)
        for generator in generators:
            for n, val_frac, test_frac in ((20, 0.1, 0.15), (7, 0.1, 0.15),
                                           (1, 0.1, 0.15), (33, 0.25, 0.25),
                                           (10, 0.0, 0.0), (10, 0.5, 0.5)):
                replay = sua.split_positions(n, val_frac, test_frac, 5,
                                             generator)
                self.assertIsNone(sua.partition_problem(
                    n, val_frac, test_frac, replay))
                self.assertEqual(len(replay["test"]), int(n * test_frac))
                self.assertEqual(len(replay["validation"]),
                                 int(n * val_frac))
                self.assertEqual(len(replay["train"]),
                                 n - int(n * test_frac) - int(n * val_frac))
                sets = [set(replay[name]) for name in sua.PARTITIONS]
                self.assertEqual(sum(len(item) for item in sets), n)
                self.assertEqual(set().union(*sets), set(range(n)))
        replay = sua.split_positions(7, 0.1, 0.15, 5, FixedGenerator)
        self.assertEqual((len(replay["test"]), len(replay["validation"]),
                          len(replay["train"])), (1, 0, 6))

    def test_partition_problems_detected(self):
        """Non-permutations, overlaps and wrong counts are reported."""
        good = sua.split_positions(N, 0.1, 0.15, 1, FixedGenerator)
        bad = copy.deepcopy(good)
        bad["order"][0] = bad["order"][1]
        self.assertEqual(sua.partition_problem(N, 0.1, 0.15, bad),
                         "replay_not_permutation")
        bad = copy.deepcopy(good)
        bad["train"].pop()
        self.assertEqual(sua.partition_problem(N, 0.1, 0.15, bad),
                         "replay_count_mismatch")
        bad = copy.deepcopy(good)
        bad["train"][0] = bad["test"][0]
        self.assertEqual(sua.partition_problem(N, 0.1, 0.15, bad),
                         "replay_partition_overlap")
        bad = copy.deepcopy(good)
        bad["train"].reverse()
        self.assertEqual(sua.partition_problem(N, 0.1, 0.15, bad),
                         "replay_slice_order_mismatch")

    def test_nondeterministic_generator_is_unknown(self):
        """A generator that differs between replays is never verified."""
        class Drifting:
            """Invented generator whose permutation changes every call."""

            count = [0]

            def __init__(self, seed):
                """Ignore the seed."""

            def permutation(self, n):
                """Rotate the order by one more step every call."""
                Drifting.count[0] += 1
                step = Drifting.count[0] % n
                return list(range(step, n)) + list(range(step))
        findings = {}
        self.assertIsNone(sua.reconstruct(N, sua.SYNTHETIC_ARGS, Drifting,
                                          findings))
        self.assertEqual(findings, {"replay_not_deterministic": "unknown"})


class VerifiedBuildTestCase(unittest.TestCase):
    """A consistent invented chain with RNG evidence verifies every case."""

    def setUp(self):
        """Build one invented preparation."""
        FixedGenerator.seeds = []
        self.plan, self.raw = bundle()
        self.sidecar = build(self.plan, self.raw)
        self.step21 = json.loads(self.raw["step21_provenance"])

    def test_every_case_verified_from_replay(self):
        """Each case gets exactly the replayed partition of its index."""
        self.assertEqual(run_level(self.sidecar), ("verified", []))
        self.assertEqual(set(FixedGenerator.seeds), {sua.SYNTHETIC_SEED})
        expected = {}
        expected.update((value, "test") for value in ORDER[:N_TEST])
        expected.update((value, "validation")
                        for value in ORDER[N_TEST:N_TEST + N_VAL])
        expected.update((value, "train")
                        for value in ORDER[N_TEST + N_VAL:])
        cases = self.sidecar["cases"]
        self.assertEqual(len(cases), len(self.step21["records"]))
        for position, (case, record) in enumerate(
                zip(cases, self.step21["records"])):
            self.assertEqual(case["case_position"], position)
            self.assertEqual(case["step21_record_sha256"], amp.digest(record))
            self.assertEqual(case["patient_index"], record["patient_index"])
            self.assertIs(case["patient_index_verified"], True)
            self.assertEqual(case["status"], "verified")
            self.assertEqual(case["partition"],
                             expected[record["patient_index"]])
            self.assertEqual(case["reason_codes"], [])
        self.assertEqual({case["partition"] for case in cases},
                         {"train", "validation", "test"})
        self.assertEqual(self.sidecar["counts"]["verified"], 10)

    def test_global_counts_and_digests(self):
        """Counts are the floor-based configuration counts; digests fixed."""
        block = self.sidecar["split_reconstruction"]
        self.assertEqual(block["counts"], {
            "total": N, "test": N_TEST, "validation": N_VAL,
            "train": N - N_TEST - N_VAL})
        self.assertEqual(block["collection_size"], N)
        digests = block["partition_digests"]
        self.assertEqual(digests["permutation_sha256"], amp.digest(ORDER))
        self.assertEqual(digests["test_sha256"],
                         amp.digest(sorted(ORDER[:N_TEST])))
        self.assertEqual(digests["train_sha256"],
                         amp.digest(sorted(ORDER[N_TEST + N_VAL:])))
        saved = block["saved_arguments"]
        self.assertIs(saved["complete_and_agreeing"], True)
        self.assertEqual(saved["values"]["seed"], sua.SYNTHETIC_SEED)
        self.assertNotIn("graphs_path", saved["values"])
        self.assertNotIn("epochs", saved["values"])
        self.assertEqual(block["rng_provenance"]["status"], "verified")
        self.assertIs(block["pairwise_disjoint"], True)
        self.assertIs(block["full_coverage"], True)
        upstream = self.sidecar["upstream_training_input"]
        self.assertEqual((upstream["status"], upstream["reason_codes"]),
                         ("verified", []))
        self.assertEqual(upstream["graphs_path"]["form"],
                         "canonical_absolute")
        self.assertEqual(upstream["graph_byte_binding"]["status"],
                         "verified")
        self.assertEqual(self.sidecar["downstream_graph_chain"]["status"],
                         "verified")
        self.assertIs(self.sidecar["downstream_graph_chain"][
            "is_training_input_evidence"], False)
        # Expected counts agree with the verified replay counts.
        self.assertEqual(expected_block(self.sidecar),
                         ("computed", block["counts"], []))

    def test_flags_and_non_inference(self):
        """Phase 4 stays false and exposure/novelty stay not_inferred."""
        for key, value in sua.REQUIRED_FLAGS.items():
            self.assertIs(type(self.sidecar[key]), type(value), key)
            self.assertEqual(self.sidecar[key], value, key)
        for case in self.sidecar["cases"]:
            self.assertIs(case["phase4_eligible"], False)
            self.assertEqual(case["upstream_training_exposure"],
                             "not_inferred")
            self.assertEqual(case["unseen_patient_status"], "not_inferred")
        self.assertIn("gradient exposure",
                      json.dumps(self.sidecar["limitations"]))

    def test_bindings(self):
        """Step 17, 19, 21, source, graph, checkpoint and metrics bound."""
        bindings = self.sidecar["bindings"]
        for role, key in (("step21_provenance", "step21"),
                          ("step17_mapping", "step17_mapping"),
                          ("step19_summary", "step19_summary"),
                          ("train_source", "train_source"),
                          ("checkpoint", "checkpoint"),
                          ("metrics", "metrics")):
            self.assertEqual(bindings[key]["sha256"],
                             hashlib.sha256(self.raw[role]).hexdigest())
        self.assertEqual(bindings["step21"]["preparation"],
                         self.step21["preparation"])
        self.assertEqual(bindings["graph"]["sha256"],
                         self.plan["graph"]["sha256"])
        self.assertEqual(bindings["step19_summary"]["graphs_filtered"], N)
        self.assertEqual(set(self.sidecar["inputs"]), set(sua.ALL_ROLES))
        for case in self.sidecar["cases"]:
            self.assertEqual(case["evidence"]["step17_mapping_sha256"],
                             bindings["step17_mapping"]["sha256"])
            self.assertEqual(case["evidence"]["step21_provenance_sha256"],
                             bindings["step21"]["sha256"])

    def test_step21_values_not_copied(self):
        """No Step 21 identity or target field is copied into the sidecar."""
        def scalars(value):
            """Yield every scalar value nested in a JSON value."""
            if isinstance(value, dict):
                for item in value.values():
                    yield from scalars(item)
            elif isinstance(value, list):
                for item in value:
                    yield from scalars(item)
            else:
                yield value
        values = set(scalars(self.sidecar))
        for record in self.step21["records"]:
            identity = record["patient_identity"]
            self.assertNotIn(identity["person_id"], values)
            self.assertNotIn(identity["subject_id"], values)
        text = sua.encode(self.sidecar).decode("utf-8")
        for word in ("visit_occurrence_id", "person_id", "subject_id",
                     "predicted_diagnosis", "drivers"):
            self.assertNotIn(word, text)


class RunEvidenceTestCase(unittest.TestCase):
    """Missing evidence is unavailable; conflicting evidence is unknown."""

    def classify(self, runtime=None, **changes):
        """Build an invented chain with changes; return (sidecar, status)."""
        plan, raw = bundle(**changes)
        sidecar = build(plan, raw, runtime)
        return sidecar, run_level(sidecar)

    def assert_all(self, sidecar, status, code):
        """Every case has status, a null partition and the run code."""
        self.assertEqual(sidecar["split_reconstruction"]["status"], status)
        self.assertIn(code, sidecar["split_reconstruction"]["reason_codes"])
        for key in ("counts", "partition_digests", "pairwise_disjoint",
                    "full_coverage"):
            self.assertIsNone(sidecar["split_reconstruction"][key], key)
        self.assertEqual(len(sidecar["cases"]), 10)
        for case in sidecar["cases"]:
            self.assertEqual(case["status"], status)
            self.assertIsNone(case["partition"])
            self.assertIn(code, case["reason_codes"])
            self.assertIs(case["phase4_eligible"], False)
        self.assertEqual(sidecar["counts"]["verified_by_partition"],
                         {"train": 0, "validation": 0, "test": 0})

    def test_missing_saved_parameters_never_defaulted(self):
        """A missing or null saved value is unavailable; no replay runs."""
        runtime = fake_runtime(default_rng=refuse_rng)
        for key in sua.REQUIRED_ARGS:
            for source in ("checkpoint", "metrics"):
                for value in (DELETE, None):
                    sidecar, _ = self.classify(
                        runtime, evidence_change=set_args(key, value,
                                                          (source,)))
                    self.assert_all(sidecar, "unavailable",
                                    "saved_arg_missing:%s:%s"
                                    % (source, key))
                    saved = sidecar["split_reconstruction"][
                        "saved_arguments"]
                    self.assertIsNone(saved["values"])
                    self.assertIs(saved["defaults_substituted"], False)

    def test_missing_args_container_and_unsafe_load(self):
        """No args mapping, a failed safe load or no loader: unavailable."""
        def drop(checkpoint, metrics):
            """Remove the invented checkpoint args mapping."""
            del checkpoint["args"]
        sidecar, _ = self.classify(evidence_change=drop)
        self.assert_all(sidecar, "unavailable",
                        "saved_args_unavailable:checkpoint")

        def failing(data):
            """Invented safe loader that refuses the bytes."""
            raise RuntimeError(MARKER)
        sidecar, _ = self.classify(fake_runtime(loader=failing))
        self.assert_all(sidecar, "unavailable", "checkpoint_safe_load_failed")
        self.assertNotIn(MARKER, json.dumps(sidecar))
        sidecar, _ = self.classify(fake_runtime(loader=None))
        self.assert_all(sidecar, "unavailable",
                        "checkpoint_loader_unavailable")

    def test_metrics_not_json_is_unavailable(self):
        """Unparsable pinned metrics bytes leave every case unavailable."""
        plan, raw = bundle()
        repin(plan, raw, "metrics", b"SYNTHETIC not json")
        rebind_run(plan, raw)
        self.assert_all(build(plan, raw), "unavailable", "metrics_not_json")

    def test_conflicting_saved_parameters(self):
        """Checkpoint and metrics disagreeing is unknown, never verified."""
        for key, value in (("seed", 7), ("val_frac", 0.2),
                           ("test_frac", 0.1), ("graphs_path",
                                                "data/processed/other.pt")):
            sidecar, _ = self.classify(
                evidence_change=set_args(key, value, ("metrics",)))
            self.assert_all(sidecar, "unknown", "saved_arg_conflict:%s" % key)

    def test_conflict_outranks_gap(self):
        """A conflict together with a gap is unknown."""
        def change(checkpoint, metrics):
            """Remove one value and contradict another."""
            del checkpoint["args"]["seed"]
            metrics["args"]["val_frac"] = 0.3
        sidecar, (status, codes) = self.classify(evidence_change=change)
        self.assertEqual(status, "unknown")
        self.assertIn("saved_arg_missing:checkpoint:seed", codes)
        self.assertIn("saved_arg_conflict:val_frac", codes)

    def test_wrong_saved_types(self):
        """Saved values of the wrong type are conflicts."""
        for key, value in (("seed", "20260925"), ("seed", True),
                           ("val_frac", 1), ("no_kg", 0)):
            sidecar, _ = self.classify(evidence_change=set_args(key, value))
            self.assert_all(sidecar, "unknown", "saved_arg_type_invalid:"
                            "checkpoint:%s" % key)

    def test_run_identity(self):
        """Task, data source, KG flag and graph reference must match."""
        cases = (("task", "mort_read", "run_task_mismatch"),
                 ("data_source", "pyhealth", "run_data_source_mismatch"),
                 ("no_kg", True, "run_no_kg_mismatch"),
                 ("graphs_path", "data/processed/other_graphs.pt",
                  "graph_path_noncanonical"),
                 ("seed", -1, "saved_seed_invalid"),
                 ("test_frac", 0.95, "split_fraction_invalid"),
                 ("val_frac", float("inf"), "split_fraction_invalid"))
        for key, value, code in cases:
            def change(checkpoint, metrics, key=key, value=value):
                """Set one value in both sources and the metrics identity."""
                set_args(key, value)(checkpoint, metrics)
                if key in sua.METRICS_IDENTITY_KEYS:
                    metrics[key] = value
            if value == float("inf"):
                # JSON cannot carry infinity; check the rule directly.
                findings = {}
                sua.check_run_identity({"val_frac": value}, bundle()[0], N,
                                       findings)
                self.assertEqual(findings, {code: "unknown"})
                continue
            sidecar, _ = self.classify(evidence_change=change)
            self.assert_all(sidecar, "unknown", code)
            self.assertEqual(expected_block(sidecar)[:2], ("unknown", None))

    def test_metrics_run_identity(self):
        """The metrics top-level identity must be present and agree."""
        def conflict(checkpoint, metrics):
            """Contradict the metrics args at the top level."""
            metrics["task"] = "mort_read"
        sidecar, _ = self.classify(evidence_change=conflict)
        self.assert_all(sidecar, "unknown", "metrics_run_identity_conflict:"
                        "task")

        def missing(checkpoint, metrics):
            """Remove the metrics top-level KG flag."""
            del metrics["no_kg"]
        sidecar, _ = self.classify(evidence_change=missing)
        self.assert_all(sidecar, "unavailable",
                        "metrics_run_identity_missing:no_kg")

    def test_source_identity_mismatch(self):
        """Missing pinned fragments or a different source hash conflict."""
        base = sua.synthetic_train_source().decode("utf-8")
        for name, snippet in sua.SOURCE_SNIPPETS.items():
            text = base.replace(snippet, "# SYNTHETIC removed fragment")
            sidecar, _ = self.classify(train_source=text.encode("utf-8"))
            self.assert_all(sidecar, "unknown",
                            "source_%s_text_mismatch" % name)

        def other_train(step21):
            """Record a different train source hash in Step 21."""
            step21["step18_policy"]["pinned_sources"]["train"] = "0" * 64
        sidecar, _ = self.classify(step21_change=other_train)
        self.assert_all(sidecar, "unknown", "source_identity_conflict")

    def test_literal_pinned_split_excerpt_passes(self):
        """The exact pinned split function, with its log line, passes.

        The excerpt is literal text, not built from SPLIT_SOURCE, so an
        incomplete expected fragment would be detected here.
        """
        literal = "\n".join((
            "def split_graphs(graphs, val_frac, test_frac, seed):",
            "    rng = np.random.default_rng(seed)",
            "    n = len(graphs)",
            "    idx = rng.permutation(n)",
            "    n_test = int(n * test_frac)",
            "    n_val = int(n * val_frac)",
            "    test_idx = idx[:n_test]",
            "    val_idx = idx[n_test:n_test + n_val]",
            "    train_idx = idx[n_test + n_val:]",
            "    train = [graphs[i] for i in train_idx]",
            "    val = [graphs[i] for i in val_idx]",
            "    test = [graphs[i] for i in test_idx]",
            '    log.info("Split: train=%d  val=%d  test=%d", len(train), '
            "len(val), len(test))",
            "    return train, val, test"))
        others = [snippet for name, snippet in sua.SOURCE_SNIPPETS.items()
                  if name != "split"]
        text = "\n".join(["# SYNTHETIC stand-in with a literal excerpt",
                          literal] + others) + "\n"
        sidecar, (status, codes) = self.classify(
            train_source=text.encode("utf-8"))
        self.assertEqual((status, codes), ("verified", []))

        # Removing or mutating one split statement must still fail.
        for old, new in (
                ('    log.info("Split: train=%d  val=%d  test=%d", '
                 "len(train), len(val), len(test))\n", ""),
                ("val_idx = idx[n_test:n_test + n_val]",
                 "val_idx = idx[n_test + n_val:]"),
                ("test_idx = idx[:n_test]", "test_idx = idx[-n_test:]")):
            self.assertIn(old, text)
            sidecar, _ = self.classify(
                train_source=text.replace(old, new).encode("utf-8"))
            self.assert_all(sidecar, "unknown", "source_split_text_mismatch")

    def test_graph_and_checkpoint_identity_mismatch(self):
        """Pinned graph or checkpoint differing from Step 21 conflicts."""
        plan, raw = bundle()
        plan["graph"]["sha256"] = "1" * 64
        sidecar = build(plan, raw)
        self.assert_all(sidecar, "unknown", "graph_identity_conflict")
        self.assertEqual(sidecar["downstream_graph_chain"]["reason_codes"],
                         ["graph_identity_conflict"])
        self.assertEqual(expected_block(sidecar)[:2], ("unknown", None))

        def other_checkpoint(step21):
            """Record a different checkpoint hash in Step 21."""
            step21["model_versions"]["checkpoint"]["sha256"] = "2" * 64
        sidecar, _ = self.classify(step21_change=other_checkpoint)
        self.assert_all(sidecar, "unknown", "checkpoint_identity_conflict")

    def test_filter_identity_mismatch(self):
        """A different patient_index rule or filtered count conflicts."""
        def other_rule(step17):
            """Describe an unfiltered order in Step 17."""
            step17["join_rules"]["patient_index"] = "raw list position"
        sidecar, _ = self.classify(step17_change=other_rule)
        self.assert_all(sidecar, "unknown", "filter_identity_conflict")
        plan, raw = bundle()
        edit_step19(plan, raw, lambda step19: step19["counts"].update(
            graphs_filtered=22))
        self.assert_all(build(plan, raw), "unknown",
                        "filter_identity_conflict")
        plan, raw = bundle()
        edit_step19(plan, raw, lambda step19: step19.update(status="failed"))
        self.assert_all(build(plan, raw), "unknown", "step19_status_conflict")

    def test_missing_rng_provenance(self):
        """Absent or incomplete RNG evidence is unavailable.

        A present NumPy runtime is not historical evidence on its own.
        """
        runtime = fake_runtime(default_rng=refuse_rng)
        sidecar, _ = self.classify(runtime, rng_absent=True)
        self.assert_all(sidecar, "unavailable", "rng_provenance_absent")
        self.assertIsNone(sidecar["inputs"]["rng_provenance"])
        self.assertIsNone(sidecar["split_reconstruction"]["rng_provenance"][
            "recorded"])
        for key in ("numpy_version", "bit_generator", "run", "schema"):
            sidecar, _ = self.classify(
                runtime, rng_change=lambda document, key=key: document.pop(
                    key))
            self.assert_all(sidecar, "unavailable",
                            "rng_provenance_incomplete")
        sidecar, _ = self.classify(runtime, rng_change=lambda document: (
            document.update(attribution="current_environment_snapshot")))
        self.assert_all(sidecar, "unavailable", "rng_provenance_incomplete")
        plan, raw = bundle()
        repin(plan, raw, "rng_provenance", b"SYNTHETIC not json")
        self.assert_all(build(plan, raw, runtime), "unavailable",
                        "rng_provenance_incomplete")
        runtime = fake_runtime()
        runtime["rng"] = None
        sidecar, _ = self.classify(runtime)
        self.assert_all(sidecar, "unavailable",
                        "rng_replay_runtime_unavailable")

    def test_conflicting_rng_provenance(self):
        """RNG evidence for another run, generator or version is unknown."""
        runtime = fake_runtime(default_rng=refuse_rng)
        sidecar, _ = self.classify(runtime, rng_change=lambda document: (
            document["run"].update(checkpoint_sha256="3" * 64)))
        self.assert_all(sidecar, "unknown", "rng_provenance_run_conflict")
        sidecar, _ = self.classify(runtime, rng_change=lambda document: (
            document["run"].update(metrics_sha256="4" * 64)))
        self.assert_all(sidecar, "unknown", "rng_provenance_run_conflict")
        sidecar, _ = self.classify(runtime, rng_change=lambda document: (
            document.update(bit_generator="MT19937")))
        self.assert_all(sidecar, "unknown", "rng_generator_conflict")
        sidecar, _ = self.classify(fake_runtime(version="synthetic-9.9",
                                                default_rng=refuse_rng))
        self.assert_all(sidecar, "unknown", "rng_replay_runtime_mismatch")
        sidecar, _ = self.classify(fake_runtime(bit="SFC64",
                                                default_rng=refuse_rng))
        self.assert_all(sidecar, "unknown", "rng_generator_conflict")


class ExpectedCountsTestCase(unittest.TestCase):
    """Expected configuration counts are separate from membership."""

    def build(self, runtime=None, **changes):
        """Build an invented chain with changes; return the sidecar."""
        plan, raw = bundle(**changes)
        return build(plan, raw, runtime or fake_runtime(
            default_rng=refuse_rng))

    def assert_no_membership(self, sidecar):
        """No partition, integrity result or verified case exists."""
        reconstruction = sidecar["split_reconstruction"]
        self.assertNotEqual(reconstruction["status"], "verified")
        for key in ("counts", "partition_digests", "pairwise_disjoint",
                    "full_coverage"):
            self.assertIsNone(reconstruction[key], key)
        for case in sidecar["cases"]:
            self.assertNotEqual(case["status"], "verified")
            self.assertIsNone(case["partition"])
            self.assertIs(case["phase4_eligible"], False)
        self.assertEqual(sidecar["counts"]["verified"], 0)
        self.assertEqual(sidecar["counts"]["verified_by_partition"],
                         {"train": 0, "validation": 0, "test": 0})

    def test_counts_computed_without_run_evidence(self):
        """Real-plan shape: counts computed, membership unavailable."""
        sidecar = self.build(rng_absent=True, binding_absent=True)
        self.assertEqual(expected_block(sidecar),
                         ("computed", FLOOR_COUNTS, []))
        block = sidecar["expected_configuration_counts"]
        self.assertIs(block["historically_verified"], False)
        self.assertIs(block["assigns_case_partitions"], False)
        self.assertIn("not historically verified", block["interpretation"])
        self.assertEqual(block["collection"]["graphs_filtered"], N)
        self.assertEqual(sidecar["split_reconstruction"]["status"],
                         "unavailable")
        self.assertEqual(sidecar["counts"]["unavailable"], 10)
        self.assert_no_membership(sidecar)

    def test_floor_counts_from_saved_fractions(self):
        """Counts use int(n * frac) with train as the remainder."""
        def change(checkpoint, metrics):
            """Set invented fractions with fractional products."""
            for document in (checkpoint, metrics):
                document["args"].update(val_frac=0.17, test_frac=0.23)
        sidecar = self.build(evidence_change=change, rng_absent=True)
        self.assertEqual(expected_block(sidecar)[:2], ("computed", {
            "total": N, "test": 4, "validation": 3, "train": 13}))
        self.assert_no_membership(sidecar)

    def test_counts_unaffected_by_rng_or_graph_byte_gaps(self):
        """RNG and graph-byte gaps or RNG conflicts leave counts computed."""
        for changes, code in (
                ({"rng_absent": True}, "rng_provenance_absent"),
                ({"binding_absent": True}, "run_graph_binding_absent"),
                ({"rng_change": lambda document: document["run"].update(
                    checkpoint_sha256="3" * 64)},
                 "rng_provenance_run_conflict")):
            sidecar = self.build(**changes)
            self.assertEqual(expected_block(sidecar),
                             ("computed", FLOOR_COUNTS, []), code)
            self.assertIn(code, run_level(sidecar)[1])
            self.assert_no_membership(sidecar)

    def test_missing_saved_arguments_give_unavailable(self):
        """A missing saved fraction or other argument yields no counts."""
        for key in ("val_frac", "test_frac", "seed", "graphs_path"):
            for source in ("checkpoint", "metrics"):
                sidecar = self.build(evidence_change=set_args(
                    key, DELETE, (source,)), rng_absent=True)
                status, counts, codes = expected_block(sidecar)
                self.assertEqual((status, counts), ("unavailable", None))
                self.assertIn("saved_arg_missing:%s:%s" % (source, key),
                              codes)
                self.assert_no_membership(sidecar)
        findings = {}
        block = sua.expected_counts(N, {"test_frac": 0.15}, findings, {})
        self.assertEqual((block["status"], block["counts"]),
                         ("unavailable", None))
        self.assertIn("saved_fraction_unavailable", block["reason_codes"])

    def test_conflicting_or_invalid_arguments_give_unknown(self):
        """Disagreeing or invalid saved values yield unknown, no counts."""
        for change, code in (
                (set_args("val_frac", 0.2, ("metrics",)),
                 "saved_arg_conflict:val_frac"),
                (set_args("test_frac", 0.1, ("checkpoint",)),
                 "saved_arg_conflict:test_frac"),
                (set_args("seed", 7, ("metrics",)),
                 "saved_arg_conflict:seed"),
                (set_args("test_frac", 0.95), "split_fraction_invalid"),
                (set_args("val_frac", 1), "saved_arg_type_invalid:"
                                          "checkpoint:val_frac")):
            sidecar = self.build(evidence_change=change, rng_absent=True)
            status, counts, codes = expected_block(sidecar)
            self.assertEqual((status, counts), ("unknown", None), code)
            self.assertIn(code, codes)
            self.assert_no_membership(sidecar)

    def test_collection_identity_conflict_gives_unknown(self):
        """A changed filtered count or rule leaves counts unknown."""
        plan, raw = bundle(rng_absent=True)
        edit_step19(plan, raw, lambda step19: step19["counts"].update(
            graphs_filtered=22))
        sidecar = build(plan, raw, fake_runtime(default_rng=refuse_rng))
        self.assertEqual(expected_block(sidecar)[:2], ("unknown", None))
        self.assertIn("filter_identity_conflict", expected_block(sidecar)[2])

        def other_rule(step17):
            """Describe an unfiltered order in Step 17."""
            step17["join_rules"]["patient_index"] = "raw list position"
        sidecar = self.build(step17_change=other_rule)
        self.assertEqual(expected_block(sidecar)[:2], ("unknown", None))

    def test_counts_never_create_partitions(self):
        """Computed counts with downstream labels still give no partition."""
        def label(step21):
            """Label every invented Step 21 record with a partition."""
            for record in step21["records"]:
                record["partition"] = "train"
                record["upstream_split"] = "test"
        with mock.patch.object(sua, "reconstruct",
                               side_effect=AssertionError("replay")):
            sidecar = self.build(step21_change=label, binding_absent=True)
        self.assertEqual(expected_block(sidecar)[0], "computed")
        self.assert_no_membership(sidecar)
        for case in sidecar["cases"]:
            self.assertEqual(case["status"], "unavailable")
            self.assertIn("run_graph_binding_absent", case["reason_codes"])


class GraphPathTestCase(unittest.TestCase):
    """Only the exact canonical absolute graphs_path is accepted."""

    CANONICAL = sua.SYNTHETIC_GRAPHS_PATH
    ROOT = sua.SYNTHETIC_UPSTREAM_ROOT

    def form(self, value):
        """Return graph_path_form against the invented canonical path."""
        return sua.graph_path_form(value, self.CANONICAL, sua.GRAPH_REFERENCE)

    def test_exact_canonical_accepted(self):
        """The exact path, with / separators or other case, is accepted."""
        for value in (self.CANONICAL, self.CANONICAL.replace("\\", "/"),
                      self.CANONICAL.upper(), self.CANONICAL.lower()):
            self.assertEqual(self.form(value),
                             ("canonical_absolute", "verified", None), value)

    def test_wrong_root_suffix_rejected(self):
        """Another root, drive, depth or file name is noncanonical."""
        for value in (
                "W:\\other\\data\\processed\\patient_graphs.pt",
                "C:" + self.CANONICAL[2:],
                self.ROOT + "\\extra\\data\\processed\\patient_graphs.pt",
                "W:\\synthetic\\data\\processed\\patient_graphs.pt",
                self.CANONICAL + ".bak",
                self.ROOT + "\\data\\processed\\other_graphs.pt",
                "other/data/processed/patient_graphs.pt",
                "processed/patient_graphs.pt"):
            self.assertEqual(self.form(value),
                             ("noncanonical", "unknown",
                              "graph_path_noncanonical"), value)

    def test_unsafe_forms_rejected(self):
        """Traversal, dot, drive-relative, UNC, device, stream and alias."""
        for value in (
                self.ROOT + "\\data\\..\\data\\processed\\patient_graphs.pt",
                self.ROOT + "\\data\\processed\\..\\processed\\"
                            "patient_graphs.pt",
                "..\\data\\processed\\patient_graphs.pt",
                self.ROOT + "\\.\\data\\processed\\patient_graphs.pt",
                "./data/processed/patient_graphs.pt",
                "W:" + self.CANONICAL[3:],
                "\\" + self.CANONICAL[3:],
                "\\\\server\\share\\data\\processed\\patient_graphs.pt",
                "//server/share/data/processed/patient_graphs.pt",
                "\\\\?\\" + self.CANONICAL,
                "\\\\.\\" + self.CANONICAL,
                "\\\\?\\UNC\\server\\share\\patient_graphs.pt",
                self.CANONICAL + ":stream",
                self.CANONICAL + "::$DATA",
                self.CANONICAL + ".",
                self.CANONICAL + " ",
                self.CANONICAL + "\\",
                self.ROOT + ".\\data\\processed\\patient_graphs.pt",
                self.ROOT + "\\\\data\\processed\\patient_graphs.pt",
                "W:\\SYNTHE~1\\upstream\\data\\processed\\patient_graphs.pt",
                self.ROOT + "\\data\\processed\\CON",
                self.ROOT + "\\data\\processed\\nul.pt",
                self.CANONICAL + "\n",
                self.CANONICAL.replace("patient", "patient*"),
                "1:" + self.CANONICAL[2:],
                "", None, 7):
            self.assertEqual(self.form(value),
                             ("unsafe", "unknown", "graph_path_unsafe_form"),
                             repr(value))

    def test_relative_reference_needs_working_directory(self):
        """The exact relative reference is a gap, never inferred."""
        for value in ("data/processed/patient_graphs.pt",
                      "data\\processed\\patient_graphs.pt"):
            self.assertEqual(self.form(value), (
                "canonical_relative", "unavailable",
                "graph_path_working_directory_unavailable"))

    def classify(self, value):
        """Build with graphs_path set in both invented sources."""
        plan, raw = bundle(evidence_change=set_args("graphs_path", value))
        return build(plan, raw, fake_runtime(default_rng=refuse_rng))

    def test_saved_path_classification_in_sidecar(self):
        """Saved path forms set the training-input and run statuses."""
        sidecar = self.classify("W:\\other\\data\\processed\\"
                                "patient_graphs.pt")
        self.assertEqual(run_level(sidecar)[0], "unknown")
        self.assertIn("graph_path_noncanonical", run_level(sidecar)[1])
        self.assertEqual(sidecar["upstream_training_input"]["status"],
                         "unknown")
        self.assertEqual(expected_block(sidecar)[:2], ("unknown", None))
        self.assertTrue(all(case["partition"] is None
                            for case in sidecar["cases"]))
        sidecar = self.classify(self.CANONICAL + ":stream")
        self.assertIn("graph_path_unsafe_form", run_level(sidecar)[1])
        self.assertEqual(run_level(sidecar)[0], "unknown")
        sidecar = self.classify("data/processed/patient_graphs.pt")
        self.assertEqual(run_level(sidecar)[0], "unavailable")
        self.assertIn("graph_path_working_directory_unavailable",
                      run_level(sidecar)[1])
        self.assertEqual(expected_block(sidecar)[:2],
                         ("computed", FLOOR_COUNTS))
        path = sidecar["upstream_training_input"]["graphs_path"]
        self.assertEqual((path["form"], path["status"]),
                         ("canonical_relative", "unavailable"))
        self.assertEqual(path["sha256"], hashlib.sha256(
            b"data/processed/patient_graphs.pt").hexdigest())
        self.assertTrue(all(case["status"] == "unavailable"
                            and case["partition"] is None
                            for case in sidecar["cases"]))

    def test_saved_path_gap_or_conflict_mirrored(self):
        """A missing or conflicting saved path is mirrored in the section."""
        plan, raw = bundle(evidence_change=set_args("graphs_path", DELETE,
                                                    ("metrics",)))
        block = build(plan, raw)["upstream_training_input"]
        self.assertEqual(block["status"], "unavailable")
        self.assertIn("saved_graphs_path_unusable", block["reason_codes"])
        self.assertIsNone(block["graphs_path"]["form"])
        plan, raw = bundle(evidence_change=set_args(
            "graphs_path", "W:\\other\\x.pt", ("metrics",)))
        block = build(plan, raw)["upstream_training_input"]
        self.assertEqual(block["status"], "unknown")

    def test_pinned_canonical_path_checked(self):
        """A malformed pinned canonical path refuses before any read."""
        plan, _ = bundle()
        self.assertIsNone(sua.pinned_hash_problem(plan))
        for value in ("data\\processed\\patient_graphs.pt",
                      self.ROOT + "\\data\\..\\data\\processed\\"
                                  "patient_graphs.pt",
                      self.ROOT + "\\data\\processed\\other.pt",
                      "\\\\server\\share\\data\\processed\\patient_graphs.pt",
                      None):
            plan, _ = bundle()
            plan["graph"]["canonical_path"] = value
            self.assertEqual(sua.pinned_hash_problem(plan),
                             "pinned_graph_path_malformed", repr(value))


class GraphBindingTestCase(unittest.TestCase):
    """Historical graph bytes need a run-bound graph digest."""

    def classify(self, runtime=None, **changes):
        """Build an invented chain with changes; return the sidecar."""
        plan, raw = bundle(**changes)
        return build(plan, raw, runtime or fake_runtime(
            default_rng=refuse_rng))

    def assert_membership(self, sidecar, status, code):
        """Every case has status and a null partition; code is recorded."""
        self.assertEqual(run_level(sidecar)[0], status)
        self.assertIn(code, run_level(sidecar)[1])
        self.assertIn(code, sidecar["upstream_training_input"][
            "reason_codes"])
        for case in sidecar["cases"]:
            self.assertEqual(case["status"], status)
            self.assertIsNone(case["partition"])
            self.assertIn(code, case["reason_codes"])

    def test_missing_binding_is_unavailable_with_canonical_path(self):
        """A canonical path and consistent chain do not bind graph bytes."""
        sidecar = self.classify(binding_absent=True)
        self.assert_membership(sidecar, "unavailable",
                               "run_graph_binding_absent")
        upstream = sidecar["upstream_training_input"]
        self.assertEqual(upstream["graphs_path"]["status"], "verified")
        self.assertEqual(upstream["graph_byte_binding"], {
            "status": "unavailable", "recorded": None,
            "downstream_chain_used_as_training_digest": False})
        self.assertEqual(sidecar["downstream_graph_chain"]["status"],
                         "verified")
        self.assertIsNone(sidecar["inputs"]["run_graph_binding"])
        self.assertEqual(expected_block(sidecar)[0], "computed")
        self.assertEqual(sidecar["split_reconstruction"]["rng_provenance"][
            "status"], "verified")

    def test_incomplete_binding_is_unavailable(self):
        """Missing fields, another attribution or bad JSON: unavailable."""
        for change in (lambda document: document.pop("graph_sha256"),
                       lambda document: document.pop("run"),
                       lambda document: document.pop("schema"),
                       lambda document: document["run"].pop(
                           "metrics_sha256"),
                       lambda document: document.update(
                           attribution="current_environment_snapshot"),
                       lambda document: document.update(graph_sha256=None)):
            self.assert_membership(self.classify(binding_change=change),
                                   "unavailable",
                                   "run_graph_binding_incomplete")
        plan, raw = bundle()
        repin(plan, raw, "run_graph_binding", b"SYNTHETIC not json")
        self.assert_membership(build(plan, raw, fake_runtime(
            default_rng=refuse_rng)), "unavailable",
            "run_graph_binding_incomplete")

    def test_conflicting_binding_is_unknown(self):
        """A binding for another graph or run is unknown."""
        sidecar = self.classify(binding_change=lambda document: (
            document.update(graph_sha256="6" * 64)))
        self.assert_membership(sidecar, "unknown",
                               "run_graph_binding_graph_conflict")
        self.assertEqual(expected_block(sidecar)[:2], ("unknown", None))
        for key in sua.RUN_KEYS:
            sidecar = self.classify(binding_change=lambda document, key=key: (
                document["run"].update({key: "7" * 64})))
            self.assert_membership(sidecar, "unknown",
                                   "run_graph_binding_run_conflict")

    def test_synthetic_artifacts_refused_outside_synthetic_runtime(self):
        """Invented RNG and graph-binding artifacts are never original."""
        sidecar = self.classify(fake_runtime(default_rng=refuse_rng,
                                             synthetic=False))
        self.assert_membership(sidecar, "unknown",
                               "run_graph_binding_synthetic")
        self.assertIn("rng_provenance_synthetic", run_level(sidecar)[1])
        runtime = fake_runtime(default_rng=refuse_rng)
        del runtime["synthetic_evidence_allowed"]
        self.assertEqual(run_level(self.classify(runtime))[0], "unknown")


class CaseTestCase(unittest.TestCase):
    """Every Step 21 case is kept; bad links are quarantined, not dropped."""

    def cases(self, **changes):
        """Build with changes and return the case entries."""
        plan, raw = bundle(**changes)
        sidecar = build(plan, raw)
        self.assertEqual(len(sidecar["cases"]), 10)
        self.assertEqual([case["case_position"] for case in
                          sidecar["cases"]], list(range(10)))
        step21 = json.loads(raw["step21_provenance"])
        for case, record in zip(sidecar["cases"], step21["records"]):
            self.assertEqual(case["step21_record_sha256"], amp.digest(record))
            self.assertIs(case["phase4_eligible"], False)
            if case["status"] != "verified":
                self.assertIsNone(case["partition"])
        return sidecar["cases"]

    def assert_case(self, case, status, code):
        """One case has the given status, code and a null partition."""
        self.assertEqual(case["status"], status)
        self.assertIn(code, case["reason_codes"])
        self.assertIsNone(case["partition"])
        self.assertIs(case["patient_index_verified"], False)

    def edit_record(self, position, change):
        """Return a Step 21 change editing one invented record."""
        def step21_change(step21):
            """Apply change to one Step 21 record."""
            change(step21["records"][position])
        return step21_change

    def test_missing_negative_out_of_range_invalid(self):
        """Bad case indices are kept and quarantined; others verify."""
        for value, status, code in (
                (DELETE, "unavailable", "case_index_missing"),
                (None, "unavailable", "case_index_missing"),
                (-1, "unknown", "case_index_negative"),
                (N, "unknown", "case_index_out_of_range"),
                (N + 500, "unknown", "case_index_out_of_range"),
                ("3", "unknown", "case_index_invalid"),
                (True, "unknown", "case_index_invalid"),
                (3.0, "unknown", "case_index_invalid")):
            def change(record, value=value):
                """Set or remove the invented patient_index."""
                if value is DELETE:
                    del record["patient_index"]
                else:
                    record["patient_index"] = value
            cases = self.cases(step21_change=self.edit_record(2, change))
            self.assert_case(cases[2], status, code)
            for number, case in enumerate(cases):
                if number != 2:
                    self.assertEqual(case["status"], "verified")

    def test_duplicate_case_index(self):
        """Two Step 21 cases with one index are both quarantined."""
        def change(step21):
            """Give record 1 the index of record 0."""
            step21["records"][1]["patient_index"] = step21["records"][0][
                "patient_index"]
        cases = self.cases(step21_change=change)
        for case in cases[:2]:
            self.assert_case(case, "unknown", "case_index_duplicate")
            self.assertEqual(case["patient_index"], PILOT[0])
        self.assertTrue(all(case["status"] == "verified"
                            for case in cases[2:]))

    def test_non_record_case(self):
        """A non-object Step 21 record is kept as an unknown case."""
        def change(step21):
            """Replace record 4 with an invented string."""
            step21["records"][4] = "SYNTHETIC not a record"
        cases = self.cases(step21_change=change)
        self.assert_case(cases[4], "unknown", "case_record_invalid")
        self.assertIsNone(cases[4]["patient_index"])

    def test_step17_missing_duplicate_conflict(self):
        """Missing mapping is unavailable; duplicate or conflicting unknown."""
        def remove(step17):
            """Drop the invented Step 17 record for index 6."""
            step17["records"] = [record for record in step17["records"]
                                 if record["patient_index"] != 6]
        position = PILOT.index(6)
        cases = self.cases(step17_change=remove)
        self.assert_case(cases[position], "unavailable",
                         "step17_mapping_missing")
        self.assertIsNone(cases[position]["evidence"]["step17_record_sha256"])

        def duplicate(step17):
            """Repeat the invented Step 17 record for index 6."""
            step17["records"].append(copy.deepcopy(next(
                record for record in step17["records"]
                if record["patient_index"] == 6)))
        self.assert_case(self.cases(step17_change=duplicate)[position],
                         "unknown", "step17_mapping_duplicate")
        for key, value in (("graph_position", 99), ("person_id", 1),
                           ("subject_id", 2)):
            def conflict(step17, key=key, value=value):
                """Change one identity field of the Step 17 record."""
                for record in step17["records"]:
                    if record["patient_index"] == 6:
                        record[key] = value
            self.assert_case(self.cases(step17_change=conflict)[position],
                             "unknown", "step17_mapping_conflict")

        def quarantine(step17):
            """Mark the Step 17 record for index 6 as quarantined."""
            for record in step17["records"]:
                if record["patient_index"] == 6:
                    record["identity_status"] = mapping.STATUS_QUARANTINED
        self.assert_case(self.cases(step17_change=quarantine)[position],
                         "unknown", "step17_mapping_not_verified")

    def test_step17_binding_conflict(self):
        """A Step 21 record bound to another Step 17 digest is unknown."""
        cases = self.cases(step21_change=self.edit_record(
            5, lambda record: record["step17_mapping"].update(
                sha256="5" * 64)))
        self.assert_case(cases[5], "unknown", "step17_binding_conflict")


class DownstreamPartitionTestCase(unittest.TestCase):
    """Downstream partition fields are never evidence of membership."""

    def test_field_ignored_when_verified(self):
        """A contradicting downstream field does not change the replay."""
        position = len(PILOT) - 1
        self.assertIn(PILOT[position], ORDER[N_TEST + N_VAL:])

        def change(step21):
            """Label a replayed-train case as test downstream."""
            step21["records"][position]["partition"] = "test"
            step21["records"][position]["upstream_split"] = "test"
        plan, raw = bundle(step21_change=change)
        sidecar = build(plan, raw)
        case = sidecar["cases"][position]
        self.assertEqual(case["status"], "verified")
        self.assertEqual(case["partition"], "train")
        self.assertIn("downstream_partition_field_ignored",
                      case["reason_codes"])
        self.assertIs(sidecar["downstream_partition_used"], False)

    def test_field_ignored_without_evidence(self):
        """Without RNG evidence a downstream field yields no partition."""
        def change(step17):
            """Label every invented Step 17 record with a split."""
            for record in step17["records"]:
                record["split"] = "validation"
        plan, raw = bundle(step17_change=change, rng_absent=True)
        sidecar = build(plan, raw)
        for case in sidecar["cases"]:
            self.assertEqual(case["status"], "unavailable")
            self.assertIsNone(case["partition"])
            self.assertIn("downstream_partition_field_ignored",
                          case["reason_codes"])


class IntegrityTestCase(unittest.TestCase):
    """Integrity failures fail closed before any output."""

    def setUp(self):
        """Build one consistent invented preparation."""
        self.plan, self.raw = bundle()

    def expect(self, code, plan=None, raw=None):
        """Assert that building fails closed with code."""
        with self.assertRaisesRegex(sua.Step22Error, "^%s$" % re.escape(
                code)):
            build(plan or self.plan, raw or self.raw)

    def test_pinned_hash_and_size(self):
        """Every role's bytes must match its pinned SHA-256 and size."""
        for role in sua.ALL_ROLES:
            plan = copy.deepcopy(self.plan)
            plan["inputs"][role]["sha256"] = "0" * 64
            self.expect("input_identity:input_hash_mismatch:%s" % role, plan)
        self.plan["inputs"]["checkpoint"]["size_bytes"] += 1
        self.expect("input_identity:input_size_mismatch:checkpoint")

    def test_roles_and_json(self):
        """A missing role or unparsable fail-closed JSON role fails."""
        raw = dict(self.raw)
        del raw["metrics"]
        self.expect("input_roles_unexpected", raw=raw)
        for role in sua.PARSED_ROLES:
            plan, raw = bundle()
            repin(plan, raw, role, b"SYNTHETIC not json")
            self.expect("input_not_json:%s" % role, plan, raw)

    def test_step21_tampering(self):
        """A changed Step 21 without a matching fingerprint fails closed."""
        step21 = json.loads(self.raw["step21_provenance"])
        step21["records"][0]["graph_position"] += 1
        repin(self.plan, self.raw, "step21_provenance", amp.encode(step21))
        self.expect("step21_fingerprint_mismatch")
        for key, value in (("phase4_eligible", True),
                           ("upstream_split_membership", "test"),
                           ("unseen_patient_status", "unseen"),
                           ("status", "draft")):
            plan, raw = bundle(step21_change=lambda step21, key=key,
                               value=value: step21.update({key: value}))
            self.expect("step21_field:%s" % key, plan, raw)
        plan, raw = bundle(step21_change=lambda step21: step21["records"][
            0].update(phase4_eligible=True))
        self.expect("step21_phase4_flag", plan, raw)
        plan, raw = bundle(step21_change=lambda step21: step21[
            "records"].pop())
        self.expect("step21_records_invalid", plan, raw)

    def test_step21_binding(self):
        """Step 17/19 bytes other than those Step 21 bound fail closed."""
        for role in ("step17_mapping", "step19_summary"):
            plan, raw = bundle()
            document = json.loads(raw[role])
            document["synthetic_extra"] = MARKER
            repin(plan, raw, role, amp.encode(document))
            self.expect("step21_binding_mismatch:%s" % role, plan, raw)

    def test_step17_and_step19_shape(self):
        """Phase 4 flags in Step 17 or unusable Step 19 counts fail."""
        plan, raw = bundle(step17_change=lambda step17: step17["records"][
            0].update(phase4_eligible=True))
        self.expect("step17_phase4_flag", plan, raw)
        plan, raw = bundle(step17_change=lambda step17: step17.update(
            step="18"))
        self.expect("step17_unexpected", plan, raw)
        for value in (0, "20", 25, None):
            plan, raw = bundle()
            edit_step19(plan, raw, lambda step19, value=value: step19[
                "counts"].update(graphs_filtered=value))
            self.expect("step19_summary_unexpected", plan, raw)

    def test_pins_and_config(self):
        """Malformed pins, missing roles and a changed config are refused."""
        plan = copy.deepcopy(self.plan)
        plan["inputs"]["metrics"]["sha256"] = "g" * 64
        self.assertEqual(sua.pinned_hash_problem(plan),
                         "pinned_hash_malformed:metrics")
        plan = copy.deepcopy(self.plan)
        plan["inputs"]["checkpoint"] = None
        self.assertEqual(sua.pinned_hash_problem(plan),
                         "pinned_input_missing:checkpoint")
        plan = copy.deepcopy(self.plan)
        plan["graph"]["sha256"] = "short"
        self.assertEqual(sua.pinned_hash_problem(plan),
                         "pinned_hash_malformed:graph")
        plan = copy.deepcopy(self.plan)
        del plan["inputs"]["rng_provenance"]
        self.assertEqual(sua.pinned_hash_problem(plan),
                         "pinned_roles_unexpected")
        self.assertIsNone(sua.pinned_hash_problem(self.plan))
        with open(sua.CONFIG_PATH, "rb") as handle:
            data = handle.read()
        for path, value in ((("required_flags", "phase4_eligible"), True),
                            (("required_saved_arguments", "seed"), "str"),
                            (("rng_provenance", "bit_generator"), "MT19937"),
                            (("checkpoint_loading", "weights_only"), False)):
            config = json.loads(data)
            config[path[0]][path[1]] = value
            with self.assertRaisesRegex(sua.Step22Error, "^config_mismatch$"):
                sua.load_config(json.dumps(config).encode())
        with self.assertRaisesRegex(sua.Step22Error, "^config_not_json$"):
            sua.load_config(b"{")
        self.assertEqual(sua.load_config(data)["sha256"],
                         hashlib.sha256(data).hexdigest())


class FingerprintTestCase(unittest.TestCase):
    """The preparation fingerprint and digests are deterministic."""

    def test_deterministic(self):
        """Equal inputs give equal bytes and the documented fingerprint."""
        plan, raw = bundle()
        first = build(plan, raw)
        second = build(copy.deepcopy(plan), dict(raw))
        self.assertEqual(sua.encode(first), sua.encode(second))
        body = {key: value for key, value in first.items()
                if key != "preparation"}
        preparation = first["preparation"]
        self.assertEqual(preparation["fingerprint_sha256"],
                         hashlib.sha256(sua.canonical_json(body)).hexdigest())
        self.assertRegex(preparation["version"],
                         r"^step22-prep-[0-9a-f]{16}$")
        text = sua.encode(first).decode("utf-8").lower()
        for word in ("timestamp", "created", "generated_at", "hostname"):
            self.assertNotIn(word, text)

    @unittest.skipUnless(np is not None, "NumPy is not installed")
    def test_numpy_partition_digests_deterministic(self):
        """NumPy replays give equal digests; another seed differs."""
        runtime = sua.synthetic_runtime()
        plan, raw = sua.synthetic_bundle()
        first = build(plan, raw, runtime)
        again = build(plan, raw, runtime)
        self.assertEqual(first["split_reconstruction"]["status"], "verified")
        self.assertEqual(first["split_reconstruction"]["partition_digests"],
                         again["split_reconstruction"]["partition_digests"])
        plan, raw = sua.synthetic_bundle(evidence_change=set_args("seed", 11))
        other = build(plan, raw, runtime)
        self.assertNotEqual(
            first["split_reconstruction"]["partition_digests"][
                "permutation_sha256"],
            other["split_reconstruction"]["partition_digests"][
                "permutation_sha256"])

    def test_changes_are_visible(self):
        """Changed evidence, status or config changes the fingerprint."""
        versions = {}
        plan, raw = bundle()
        versions["base"] = build(plan, raw)["preparation"]["version"]
        plan, raw = bundle(evidence_change=set_args("seed", 11))
        versions["seed"] = build(plan, raw)["preparation"]["version"]
        plan, raw = bundle(rng_absent=True)
        versions["rng"] = build(plan, raw)["preparation"]["version"]
        plan, raw = bundle(evidence_change=set_args("seed", 12,
                                                    ("metrics",)))
        versions["conflict"] = build(plan, raw)["preparation"]["version"]
        plan, raw = bundle()
        ref = dict(config_ref(), sha256="a" * 64)
        versions["config"] = sua.build_sidecar(plan, raw, ref,
                                               fake_runtime())[
            "preparation"]["version"]
        self.assertEqual(len(set(versions.values())), len(versions),
                         versions)


class ScratchTestCase(unittest.TestCase):
    """Invented inputs written to a workspace scratch directory."""

    def setUp(self):
        """Write invented pinned inputs into a workspace scratch directory."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT, True),
                        "test scratch space must stay in the workspace root")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="step-22-", dir=root)
        self.addCleanup(self.remove_scratch)
        inputs = os.path.join(self.work, "protected", "inputs")
        os.makedirs(inputs)
        self.plan, self.raw = bundle(input_dir=inputs)
        for role, data in self.raw.items():
            with open(self.plan["inputs"][role]["path"], "wb") as handle:
                handle.write(data)
        self.plan["protected_root"] = os.path.join(self.work, "protected")
        self.plan["output_dir"] = os.path.join(
            self.work, "protected", "model-provenance", "step-22-test")

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def input_hashes(self):
        """Return the current SHA-256 of every invented input file."""
        return {role: mapping.sha256_file(entry["path"])
                for role, entry in self.plan["inputs"].items() if entry}

    def assert_nothing_published(self):
        """Assert no final or partial output directory exists."""
        for path in (self.plan["output_dir"],
                     self.plan["output_dir"] + sua.PARTIAL):
            self.assertFalse(os.path.lexists(path))


class PublicationTestCase(ScratchTestCase):
    """No overwrite, atomic publication and immutable inputs."""

    def test_publishes_one_file_atomically(self):
        """A passing run publishes only the sidecar; inputs unchanged."""
        before = self.input_hashes()
        result = sua.attach(self.plan, fake_runtime())
        out = self.plan["output_dir"]
        self.assertEqual(os.listdir(out), [sua.OUTPUT_NAME])
        with open(os.path.join(out, sua.OUTPUT_NAME), "rb") as handle:
            data = handle.read()
        self.assertEqual(result["sidecar_sha256"],
                         hashlib.sha256(data).hexdigest())
        self.assertEqual(json.loads(data), build(self.plan, self.raw))
        self.assertEqual(self.input_hashes(), before)
        self.assertEqual((result["cases"], result["verified"]), (10, 10))
        self.assertIs(result["phase4_eligible"], False)

    def test_absent_rng_publishes_unavailable(self):
        """Without an RNG artifact the sidecar publishes all unavailable."""
        os.remove(self.plan["inputs"]["rng_provenance"]["path"])
        self.plan["inputs"]["rng_provenance"] = None
        result = sua.attach(self.plan, fake_runtime(default_rng=refuse_rng))
        self.assertEqual((result["unavailable"], result["verified"]), (10, 0))
        self.assertEqual(result["reconstruction_status"], "unavailable")
        self.assertIn("rng_provenance_absent", result["reason_codes"])

    def test_no_overwrite(self):
        """An existing final or partial directory is refused."""
        sua.attach(self.plan, fake_runtime())
        with self.assertRaisesRegex(sua.Step22Error, "^output_exists$"):
            sua.attach(self.plan, fake_runtime())
        self.plan["output_dir"] += "-second"
        os.makedirs(self.plan["output_dir"] + sua.PARTIAL)
        with self.assertRaisesRegex(sua.Step22Error, "^output_exists$"):
            sua.attach(self.plan, fake_runtime())
        self.assertFalse(os.path.lexists(self.plan["output_dir"]))

    def test_output_boundaries(self):
        """Unprotected or input-overlapping outputs are refused."""
        self.plan["output_dir"] = os.path.join(self.work, "elsewhere", "out")
        with self.assertRaisesRegex(sua.Step22Error,
                                    "^output_not_protected$"):
            sua.attach(self.plan, fake_runtime())
        self.plan["output_dir"] = os.path.join(
            os.path.dirname(self.plan["inputs"]["step21_provenance"][
                "path"]), "membership")
        with self.assertRaisesRegex(sua.Step22Error,
                                    "^output_overlaps_input$"):
            sua.attach(self.plan, fake_runtime())
        self.plan["output_dir"] = "C:\\synthetic\\out"
        with self.assertRaises(sua.Step22Error):
            sua.attach(self.plan, fake_runtime())
        self.assert_nothing_published()

    def test_identity_mismatch_publishes_nothing(self):
        """A hash mismatch in any role fails before publication."""
        for role in sua.ALL_ROLES:
            saved = self.plan["inputs"][role]["sha256"]
            self.plan["inputs"][role]["sha256"] = "0" * 64
            with self.assertRaisesRegex(
                    sua.Step22Error,
                    "^input_identity:input_hash_mismatch:%s$" % role):
                sua.attach(self.plan, fake_runtime())
            self.plan["inputs"][role]["sha256"] = saved
        self.assert_nothing_published()

    def test_unsafe_paths_refused_before_output(self):
        """Malformed pins and paths outside the anchor publish nothing."""
        self.plan["inputs"]["metrics"]["sha256"] = "g" * 64
        with mock.patch.object(mapping, "read_verified",
                               side_effect=AssertionError("read")):
            with self.assertRaisesRegex(sua.Step22Error,
                                        "^pinned_hash_malformed:metrics$"):
                sua.attach(self.plan, fake_runtime())
        self.plan, _ = bundle(input_dir=os.path.dirname(
            self.plan["inputs"]["metrics"]["path"]))
        self.plan["protected_root"] = os.path.join(self.work, "protected")
        self.plan["output_dir"] = os.path.join(
            self.work, "protected", "model-provenance", "step-22-test")
        self.plan["inputs"]["train_source"]["path"] = (
            "C:\\synthetic\\train.py")
        with self.assertRaisesRegex(
                sua.Step22Error, "^input_identity:input_path_rejected$"):
            sua.attach(self.plan, fake_runtime())
        self.assert_nothing_published()

    def test_change_during_write_removes_partial(self):
        """An input changed while the partial is written publishes nothing."""
        path = self.plan["inputs"]["step21_provenance"]["path"]
        original = sua.encode

        def encode_then_touch(sidecar):
            """Encode, then move the invented Step 21 file's mtime."""
            data = original(sidecar)
            info = os.stat(path)
            os.utime(path, ns=(info.st_atime_ns,
                               info.st_mtime_ns + 10 ** 9))
            return data
        with mock.patch.object(sua, "encode", encode_then_touch):
            with self.assertRaisesRegex(
                    sua.Step22Error,
                    "^input_identity:input_changed_during_read:"
                    "step21_provenance$"):
                sua.attach(self.plan, fake_runtime())
        self.assert_nothing_published()

    def test_rename_failure_removes_partial(self):
        """A failed final rename leaves no final or partial path."""
        before = self.input_hashes()
        with mock.patch.object(sua.os, "rename",
                               side_effect=OSError(MARKER)):
            with self.assertRaisesRegex(sua.Step22Error,
                                        "^output_rename_failed$"):
                sua.attach(self.plan, fake_runtime())
        self.assert_nothing_published()
        self.assertEqual(self.input_hashes(), before)
        sua.attach(self.plan, fake_runtime())
        self.assertEqual(os.listdir(self.plan["output_dir"]),
                         [sua.OUTPUT_NAME])


class ModesTestCase(unittest.TestCase):
    """Default synthetic mode, the closed real gate and the pinned plan."""

    LINE = re.compile(
        r"^(status: passed|cases: \d+|verified: \d+|unknown: \d+|"
        r"unavailable: \d+|"
        r"((alternative_)?reconstruction_status|"
        r"upstream_training_input_status|downstream_graph_chain_status): "
        r"(verified|unknown|unavailable)|"
        r"(alternative_)?expected_counts_status: "
        r"(computed|unknown|unavailable)|"
        r"reason_codes: [a-z0-9_:,]+|phase4_eligible: false|"
        r"(alternative_)?preparation_version: step22-prep-[0-9a-f]{16}|"
        r"fingerprint_sha256: [0-9a-f]{64})$")

    def forbid_protected_reads(self):
        """Make any guarded file access fail the test."""
        def refuse(*args, **kwargs):
            """Fail if a protected or pinned file is touched."""
            raise AssertionError("protected access")
        for name in ("regular_file_info", "sha256_file", "read_verified"):
            patcher = mock.patch.object(mapping, name, refuse)
            patcher.start()
            self.addCleanup(patcher.stop)

    @unittest.skipUnless(np is not None, "NumPy is not installed")
    def test_default_mode_is_synthetic_and_sanitised(self):
        """The default mode passes on invented data with sanitised lines."""
        self.forbid_protected_reads()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(sua.main([]), 0)
        lines = out.getvalue().splitlines()
        for expected in ("status: passed", "cases: 10", "verified: 10",
                         "reconstruction_status: verified",
                         "expected_counts_status: computed",
                         "upstream_training_input_status: verified",
                         "downstream_graph_chain_status: verified",
                         "alternative_reconstruction_status: unavailable",
                         "alternative_expected_counts_status: computed",
                         "phase4_eligible: false"):
            self.assertIn(expected, lines)
        for line in lines:
            self.assertRegex(line, self.LINE)
        self.assertNotIn(str(sua.SYNTHETIC_SEED), out.getvalue())

    def test_real_mode_gated_closed(self):
        """The real mode refuses before touching any file."""
        self.assertFalse(sua.REAL_MODE_ENABLED)
        self.forbid_protected_reads()
        with mock.patch.object(sua, "attach",
                               side_effect=AssertionError("attach")):
            with self.assertRaisesRegex(sua.Step22Error,
                                        "^real_mode_gated_closed$"):
                sua.run_real()
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(sua.main(["--operator-real"]), 1)
        self.assertEqual(out.getvalue().strip(),
                         "status: failed (real_mode_gated_closed)")

    def test_errors_are_sanitised(self):
        """Failures print a finding code; other messages are withheld."""
        def coded():
            """Raise an invented coded failure."""
            raise sua.Step22Error("step21_fingerprint_mismatch")

        def unexpected():
            """Raise an error whose message must not be printed."""
            raise ValueError(MARKER)
        for failing, expected in (
                (coded, "status: failed (step21_fingerprint_mismatch)"),
                (unexpected, "status: failed (ValueError, message "
                             "withheld)")):
            out = io.StringIO()
            with mock.patch.object(sua, "run_synthetic", failing):
                with redirect_stdout(out):
                    self.assertEqual(sua.main([]), 1)
            self.assertEqual(out.getvalue().strip(), expected)
            self.assertNotIn(MARKER, out.getvalue())

    def test_safe_checkpoint_loader_only(self):
        """Real mode loads only with weights_only=True on CPU, no fallback."""
        calls = []

        class FakeTorch:
            """Invented torch module recording load calls."""

            @staticmethod
            def load(handle, **kwargs):
                """Record the call and refuse, as an unsafe payload would."""
                calls.append(kwargs)
                raise RuntimeError(MARKER)
        with mock.patch.dict(sys.modules, {"torch": FakeTorch}):
            runtime = sua.real_runtime()
        # The operator runtime never accepts invented evidence.
        self.assertNotIn("synthetic_evidence_allowed", runtime)
        runtime["rng"] = fake_runtime()["rng"]
        runtime["synthetic_evidence_allowed"] = True
        plan, raw = bundle()
        sidecar = build(plan, raw, runtime)
        self.assertEqual(calls, [{"weights_only": True,
                                  "map_location": "cpu"}])
        self.assertEqual(sidecar["split_reconstruction"]["status"],
                         "unavailable")
        self.assertIn("checkpoint_safe_load_failed",
                      sidecar["split_reconstruction"]["reason_codes"])
        with open(os.path.join(REPO_ROOT, "src", "ehr_evidence_app", "gnn",
                               "split.py"),
                  "r", encoding="utf-8") as handle:
            text = handle.read()
        for forbidden in ("weights_only=False", "importlib",
                          "module_from_verified_source", "import train"):
            self.assertNotIn(forbidden, text)

    def test_real_plan_pins(self):
        """The real plan pins the approved identities and fixed output."""
        plan = sua.real_plan()
        self.assertIsNone(sua.pinned_hash_problem(plan))
        pins = {name: entry["sha256"].upper()
                for name, entry in plan["inputs"].items() if entry}
        self.assertEqual(pins, {
            "step21_provenance": "132E27CDE28150550B03C26FE0C932EC824DAF8FC0"
                                 "8A207F4325D982AAA1B655",
            "step17_mapping": "D12DDB031DC6F8F5E8C271AF70A90D7161073D2EF48D"
                              "FC1DE2CC3AC31DADA18D",
            "step19_summary": "4472843C61B5196CB3D999D71EEC2EF15FBE75AEE7"
                              "F9A27B03712BA42F470B47",
            "train_source": "EB412E5BEFF75371D30DE509AD7D8605F727BF9E2581"
                            "5D15D07CDAEBEBDEA6DF",
            "checkpoint": "5785E247D9A41D1D1534474D9771265EA0918767607C44"
                          "B5DB9EA364D5326FE1",
            "metrics": "C4382CA881BBC728DBC94236C262E66001C0E97E7BCB6E1F"
                       "CBE6A0D4166B7A9D"})
        self.assertIsNone(plan["inputs"]["rng_provenance"])
        # No run-bound graph digest is pinned, so graph bytes stay
        # unavailable even though the canonical path is fixed.
        self.assertIsNone(plan["inputs"]["run_graph_binding"])
        self.assertEqual(plan["inputs"]["train_source"]["size_bytes"], 30887)
        self.assertEqual(plan["inputs"]["metrics"]["size_bytes"], 7752)
        self.assertEqual(plan["inputs"]["checkpoint"]["size_bytes"],
                         19523207)
        self.assertEqual(plan["graph"]["sha256"].upper(),
                         "A650F130E08DCFA472CD50985DFCF9D140772AEBCA40AC0F"
                         "9B62E8103049DE8C")
        upstream = "G:\\Industry Dataset\\standardized-omop-gnn-7B82\\"
        self.assertEqual(plan["graph"]["canonical_path"],
                         upstream + "data\\processed\\patient_graphs.pt")
        self.assertEqual(plan["graph"]["reference"],
                         "data\\processed\\patient_graphs.pt")
        self.assertEqual(plan["inputs"]["train_source"]["path"],
                         upstream + "src\\gnn\\train.py")
        self.assertEqual(plan["inputs"]["metrics"]["path"],
                         upstream + "data\\runs\\diag_kg\\results\\"
                                    "metrics.json")
        self.assertEqual(plan["inputs"]["checkpoint"]["path"],
                         upstream + "data\\runs\\diag_kg\\checkpoints\\"
                                    "best_model.pt")
        self.assertEqual(
            plan["inputs"]["step21_provenance"]["path"],
            "G:\\Industry Dataset\\ehr-evidence-workspace\\protected-data"
            "\\model-provenance\\step-21-pilot10-v1\\provenance.json")
        self.assertEqual(
            plan["output_dir"],
            "G:\\Industry Dataset\\ehr-evidence-workspace\\protected-data"
            "\\model-provenance\\step-22-pilot10-v2")
        self.assertEqual(sua.OUTPUT_NAME, "split-membership.json")
        for entry in plan["inputs"].values():
            if entry:
                self.assertTrue(guard.is_within(entry["path"],
                                                guard.POLICY_ANCHOR, True))
        self.assertTrue(guard.is_within(plan["output_dir"],
                                        plan["protected_root"], True))
        self.assertFalse(guard.is_within(plan["output_dir"], amp.OUTPUT_DIR))
        self.assertEqual(plan["inputs"]["checkpoint"],
                         pmo.real_plan()["assets"]["checkpoint"])


if __name__ == "__main__":
    unittest.main()
