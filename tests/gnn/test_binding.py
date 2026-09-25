"""Synthetic checks for ehr_evidence_app.gnn.binding (Phase 3 closure).

Every fixture is invented: invented label matrices, split parameters,
metrics, identity mapping, parity summary and bridge outputs. No real
graph, checkpoint, mapping, output or patient record is opened, and torch
is never imported; the real runtime is replaced by an in-memory fake.
Scratch files are created only under the configured workspace test temp
directory.
"""

import copy
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.gnn import binding  # noqa: E402
from ehr_evidence_app.gnn import mapping  # noqa: E402
from ehr_evidence_app.gnn import outputs as pmo  # noqa: E402
from ehr_evidence_app.gnn import provenance as amp  # noqa: E402
from ehr_evidence_app.gnn import split as usm  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402

PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
PILOT = sorted(amp.SYNTHETIC_PILOT)
REFERENCE = {"auroc_macro": 0.8, "auprc_macro": 0.3,
             "code_recall": [0.5, 0.6, 0.7],
             "visit_precision": [0.4, 0.5, 0.6], "loss": 1.2}


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def pinned_split(n, val_frac, test_frac, seed):
    """Run the pinned upstream split_graphs source over positions 0..n-1."""
    import numpy
    namespace = {"np": numpy,
                 "log": types.SimpleNamespace(info=lambda *a, **k: None)}
    exec(usm.SPLIT_SOURCE, namespace)  # noqa: S102 - pinned invented copy
    parts = namespace["split_graphs"](list(range(n)), val_frac, test_frac,
                                      seed)
    return dict(zip(binding.PARTITIONS, parts))


def good_args(**overrides):
    """Return invented saved checkpoint arguments."""
    args = {"task": "diagnosis", "data_source": "omop", "no_kg": False,
            "seed": 3, "val_frac": 0.1, "test_frac": 0.15, "batch_size": 4,
            "pw_mode": "cap50"}
    args.update(overrides)
    return args


def batch_rows(count, changed=(0,)):
    """Return invented per-case batch rows; listed cases change argmax."""
    return [{"reproduces_published": True,
             "batches": {str(size): {
                 "argmax_unchanged": index not in changed,
                 "selected_shift": 0.1 * (index + 1),
                 "max_shift": 0.3 * (index + 1),
                 "selected_rank": 2 if index in changed else 1}
                 for size in binding.BATCH_SIZES}}
            for index in range(count)]


def invented_run(n=20, rows=None, tamper=False):
    """Return an invented run over n graphs with the pilot selection."""
    labels = binding.synthetic_labels(count=n)
    args = good_args()
    parts = binding.replay_split(n, args["val_frac"], args["test_frac"],
                                 args["seed"])
    control = binding.replay_split(n, args["val_frac"], args["test_frac"],
                                   args["seed"] + 1)
    cap = binding.POS_WEIGHT_CAPS["cap50"]
    replayed = binding.synthetic_pos_weight(labels, parts["train"], cap)
    saved = list(replayed)
    if tamper:
        saved[1] += 0.5
    reverse = list(range(binding.SYNTHETIC_LABELS - 1, -1, -1))
    near = dict(REFERENCE, loss=1.2 + 5e-9)
    far = dict(REFERENCE, auroc_macro=0.75)
    return {"graphs_total": n + 4, "graphs_filtered": n, "args": args,
            "partitions": parts, "split_replay_matches": True,
            "pos_weight": binding.compare_pos_weight(replayed, saved, cap),
            "controls": {
                "wrong_seed": binding.compare_pos_weight(
                    binding.synthetic_pos_weight(labels, control["train"],
                                                 cap), saved, cap),
                "reversed_label_order": binding.compare_pos_weight(
                    binding.synthetic_pos_weight(labels, parts["train"], cap,
                                                 reverse), saved, cap)},
            "metrics": {
                "validation": binding.metric_differences(near, REFERENCE),
                "test": binding.metric_differences(near, REFERENCE),
                "wrong_seed_test": binding.metric_differences(far,
                                                              REFERENCE)},
            "batch_rows": rows if rows is not None else batch_rows(
                len(PILOT))}


def selection():
    """Return the invented pilot selection sorted by patient_index."""
    return [{"patient_index": index, "graph_position": index + 1,
             "subject_id": 9900000 + index} for index in PILOT]


class PureCheckTests(unittest.TestCase):
    """Split, pos_weight, metrics, batch statistics and status rules."""

    def test_replay_matches_the_pinned_split_source(self):
        """The NumPy replay equals the pinned split_graphs on positions."""
        for n, seed in ((20, 3), (97, 42), (1000, 7)):
            with self.subTest(n=n, seed=seed):
                self.assertEqual(binding.replay_split(n, 0.1, 0.15, seed),
                                 pinned_split(n, 0.1, 0.15, seed))

    def test_partitions_are_checked(self):
        """Overlap, gaps, foreign positions and wrong sizes fail closed."""
        parts = binding.replay_split(20, 0.1, 0.15, 3)
        facts = binding.check_partitions(parts, 20, 0.1, 0.15)
        self.assertEqual(facts["counts"],
                         {"train": 15, "validation": 2, "test": 3})
        self.assertEqual(facts["partition_sha256"]["test"],
                         binding.digest(sorted(parts["test"])))
        broken = copy.deepcopy(parts)
        broken["test"][0] = broken["train"][0]
        with self.assertRaisesRegex(binding.BindingError, "not_a_partition"):
            binding.check_partitions(broken, 20, 0.1, 0.15)
        moved = copy.deepcopy(parts)
        moved["validation"].append(moved["train"].pop())
        with self.assertRaisesRegex(binding.BindingError, "sizes"):
            binding.check_partitions(moved, 20, 0.1, 0.15)
        foreign = copy.deepcopy(parts)
        foreign["test"][0] = 20
        with self.assertRaisesRegex(binding.BindingError, "position"):
            binding.check_partitions(foreign, 20, 0.1, 0.15)
        with self.assertRaisesRegex(binding.BindingError, "unexpected"):
            binding.check_partitions({"train": []}, 20, 0.1, 0.15)

    def test_pos_weight_comparison(self):
        """Exact-count values are those below the cap."""
        saved = [50.0, 3.5, 50.0, 12.25]
        same = binding.compare_pos_weight(list(saved), saved, 50.0)
        self.assertEqual(same, {"labels": 4, "equal_values": 4,
                                "exact_count_values": 2,
                                "exact_count_equal": 2,
                                "bitwise_equal": True})
        other = binding.compare_pos_weight([50.0, 3.5, 50.0, 12.5], saved,
                                           50.0)
        self.assertEqual((other["equal_values"], other["exact_count_equal"],
                          other["bitwise_equal"]), (3, 1, False))
        for bad in ([], [1.0, float("nan"), 2.0, 3.0], [1.0, 2.0],
                    [True, 1.0, 2.0, 3.0], "1234"):
            with self.subTest(bad=bad):
                with self.assertRaises(binding.BindingError):
                    binding.compare_pos_weight(bad, saved, 50.0)

    def test_metric_differences(self):
        """Scalars and lists compare by maximum absolute difference."""
        got = dict(REFERENCE, auroc_macro=0.8 + 2e-7,
                   code_recall=[0.5, 0.6, 0.7 + 3e-7])
        result = binding.metric_differences(got, REFERENCE)
        self.assertAlmostEqual(result["auroc_macro"], 2e-7)
        self.assertAlmostEqual(result["code_recall"], 3e-7)
        self.assertTrue(binding.within(result))
        self.assertFalse(binding.within(dict(result, loss=2e-6)))
        for key in ("loss", "visit_precision"):
            missing = dict(REFERENCE)
            del missing[key]
            with self.assertRaisesRegex(binding.BindingError, key):
                binding.metric_differences(missing, REFERENCE)
        with self.assertRaisesRegex(binding.BindingError, "length"):
            binding.metric_differences(dict(REFERENCE, code_recall=[0.5]),
                                       REFERENCE)
        with self.assertRaises(binding.BindingError):
            binding.metric_differences(dict(REFERENCE,
                                            loss=float("inf")), REFERENCE)

    def test_saved_arguments_are_checked(self):
        """Missing, wrong-run, invalid and disagreeing arguments fail."""
        self.assertEqual(binding.check_args(good_args(), None)["seed"], 3)
        self.assertEqual(binding.check_args(good_args(), good_args())[
            "pw_mode"], "cap50")
        cases = (
            ({key: value for key, value in good_args().items()
              if key != "seed"}, None, "arg_missing:seed"),
            (good_args(task="mort_read"), None, "identity"),
            (good_args(no_kg=0), None, "identity"),
            (good_args(seed=True), None, "invalid"),
            (good_args(val_frac=0.0), None, "invalid"),
            (good_args(val_frac=0.5, test_frac=0.5), None, "invalid"),
            (good_args(batch_size=0), None, "invalid"),
            (good_args(pw_mode="sqrt5"), None, "unsupported"),
            (good_args(), good_args(seed=4), "disagree:seed"),
            (good_args(), ["not", "a", "mapping"], "metrics_args_invalid"),
            ("not a mapping", None, "args_missing"))
        for saved, metrics_args, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(binding.BindingError, code):
                    binding.check_args(saved, metrics_args)

    def test_batch_statistics(self):
        """Counts, medians, maxima and ranks are aggregated per size."""
        stats = binding.batch_statistics(batch_rows(4, changed=(1, 3)))
        self.assertEqual(stats["cases"], 4)
        self.assertEqual(stats["argmax_unchanged"], {"64": 2, "128": 2})
        self.assertEqual(stats["selected_rank_max"], {"64": 2, "128": 2})
        self.assertAlmostEqual(stats["selected_abs_logit_shift"]["64"]["max"],
                               0.4)
        self.assertAlmostEqual(
            stats["selected_abs_logit_shift"]["64"]["median"], 0.25)
        with self.assertRaises(binding.BindingError):
            binding.batch_statistics([])
        rows = batch_rows(2)
        rows[0]["batches"]["64"]["selected_rank"] = 0
        with self.assertRaisesRegex(binding.BindingError, "rank"):
            binding.batch_statistics(rows)

    def test_binding_status_needs_every_check(self):
        """A bound run verifies; any failed check or control fails it."""
        status, checks = binding.evaluate_binding(invented_run())
        self.assertEqual(status, binding.VERIFIED)
        self.assertTrue(all(checks.values()))
        tampered, _ = binding.evaluate_binding(invented_run(tamper=True))
        self.assertEqual(tampered, binding.FAILED)
        variants = []
        run = invented_run()
        run["metrics"]["test"]["auroc_macro"] = 1e-3
        variants.append(("test_metrics_within_tolerance", run))
        run = invented_run()
        run["metrics"]["validation"]["loss"] = 1e-3
        variants.append(("validation_metrics_within_tolerance", run))
        run = invented_run()
        run["metrics"]["wrong_seed_test"] = dict.fromkeys(
            binding.METRIC_KEYS, 0.0)
        variants.append(("wrong_seed_control_failed", run))
        run = invented_run()
        run["controls"]["reversed_label_order"] = dict(run["pos_weight"])
        variants.append(("reversed_label_control_failed", run))
        run = invented_run()
        run["split_replay_matches"] = False
        variants.append(("split_replay_matches_upstream", run))
        for check, run in variants:
            with self.subTest(check=check):
                status, checks = binding.evaluate_binding(run)
                self.assertEqual(status, binding.FAILED)
                self.assertFalse(checks[check])


class RecordTests(unittest.TestCase):
    """Summary and cases documents."""

    def test_records_are_aggregate_and_bound_to_cases(self):
        """The summary holds no case identifiers and hashes the cases file."""
        plan = binding.synthetic_plan()
        summary, cases = binding.build_records(
            plan, invented_run(rows=batch_rows(len(PILOT), changed=(0, 5))),
            selection(), {"python": "synthetic"})
        self.assertEqual(summary["status"], binding.VERIFIED)
        self.assertEqual(summary["cases_file"]["sha256"],
                         hashlib.sha256(binding.encode(cases)).hexdigest())
        self.assertEqual(sum(summary["pilot_partition_counts"].values()),
                         len(PILOT))
        self.assertEqual(summary["batch_sensitivity"]["argmax_unchanged"],
                         {"64": 8, "128": 8})
        text = json.dumps(summary)
        self.assertNotIn("patient_index", text)
        self.assertNotIn("graph_position", text)
        membership = {}
        for name, positions in invented_run()["partitions"].items():
            for position in positions:
                membership[position] = name
        self.assertEqual([case["patient_index"] for case in cases["cases"]],
                         PILOT)
        for case in cases["cases"]:
            self.assertEqual(case["upstream_partition"],
                             membership[case["patient_index"]])
            self.assertEqual(set(case["batch"]), {"64", "128"})
        self.assertFalse(cases["cases"][0]["batch"]["64"]["argmax_unchanged"])
        body = {key: value for key, value in summary.items()
                if key != "preparation"}
        self.assertEqual(summary["preparation"]["fingerprint_sha256"],
                         binding.digest(body))
        again, _ = binding.build_records(
            plan, invented_run(rows=batch_rows(len(PILOT), changed=(0, 5))),
            selection(), {"python": "synthetic"})
        self.assertEqual(again, summary)

    def test_synthetic_mode(self):
        """The default mode verifies the bound run and rejects the tamper."""
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(binding.main([]), 0)
        text = stream.getvalue()
        self.assertIn("Status             : verified", text)
        self.assertIn("tampered control   : failed", text)

    def test_real_plan_is_pinned_and_protected(self):
        """Every real role is pinned and the output stays protected."""
        plan = binding.real_plan()
        self.assertIsNone(binding.plan_problem(plan))
        self.assertTrue(guard.is_within(plan["output_dir"],
                                        plan["protected_root"], True))
        self.assertTrue(plan["output_dir"].endswith(
            "\\verification\\inherited-run-binding-v1"))
        self.assertEqual(plan["assets"]["graph"]["sha256"],
                         pmo.real_plan()["assets"]["graph"]["sha256"])
        broken = copy.deepcopy(plan)
        broken["assets"]["metrics"]["sha256"] = "0" * 63
        self.assertEqual(binding.plan_problem(broken),
                         "plan_pin_invalid:metrics")
        del broken["assets"]["metrics"]
        self.assertEqual(binding.plan_problem(broken),
                         "plan_roles_unexpected")


class IsolationTests(unittest.TestCase):
    """Import blocking, evaluation tripwires and verified upstream loading."""

    def test_forbidden_imports_are_refused(self):
        """Tracker and ETL modules are refused; others are allowed."""
        self.assertEqual(binding.forbidden_category("clearml.task"),
                         "tracker")
        self.assertEqual(binding.forbidden_category("src.preprocess.x"),
                         "etl")
        self.assertIsNone(binding.forbidden_category("sklearn.metrics"))
        self.assertIsNone(binding.forbidden_category("train"))
        trips = pmo.Tripwires()
        name = "wandb"
        saved = sys.modules.pop(name, None)
        try:
            with binding.imports_blocked(trips):
                with self.assertRaises(ImportError):
                    __import__(name)
        finally:
            if saved is not None:
                sys.modules[name] = saved
        self.assertEqual(trips.hits, ["forbidden_import:tracker"])

    def test_training_mode_and_optimisers_trip(self):
        """train(True) and optimiser creation trip; eval stays allowed."""
        class Module:
            """Invented stand-in for torch.nn.Module."""

            def train(self, mode=True):
                """Record the requested mode."""
                self.mode = mode
                return self

        class Optimizer:
            """Invented stand-in for torch.optim.Optimizer."""

            def __init__(self, *args, **kwargs):
                """Accept anything."""

        fake = types.SimpleNamespace(nn=types.SimpleNamespace(Module=Module),
                                     optim=types.SimpleNamespace(
                                         Optimizer=Optimizer))
        original = (Module.train, Optimizer.__init__)
        trips = pmo.Tripwires()
        with binding.evaluation_tripwires(fake, trips):
            self.assertFalse(Module().train(False).mode)
            with self.assertRaises(pmo.Step20Error):
                Module().train(True)
            with self.assertRaises(pmo.Step20Error):
                Optimizer()
        self.assertEqual(trips.hits, ["training_mode_used",
                                      "optimizer_created"])
        self.assertEqual((Module.train, Optimizer.__init__), original)

    def test_upstream_modules_load_from_verified_bytes(self):
        """model is visible to the bridge and trainer, then unregistered."""
        plan = binding.synthetic_plan()
        sources = {
            "model": b"class PatientGNN:\n    marker = 'model'\n",
            "explain_patient": b"from model import PatientGNN\n"
                               b"LOADED = PatientGNN.marker\n",
            "train": b"from model import PatientGNN\n"
                     b"def split_graphs(*args):\n    return args\n"}
        path_before = list(sys.path)
        previous = sys.modules.get("model")
        bridge, trainer = binding.load_upstream(plan, sources)
        self.assertEqual(bridge.LOADED, "model")
        self.assertTrue(callable(trainer.split_graphs))
        self.assertEqual(sys.path, path_before)
        self.assertIs(sys.modules.get("model"), previous)
        with self.assertRaises(binding.BindingError):
            binding.load_upstream(plan, dict(sources, train=b"def (:\n"))


class FakeRuntime:
    """In-memory stand-in for the torch runtime."""

    rows = None
    seen = None

    def __init__(self):
        """Nothing to import."""

    def versions(self):
        """Return invented runtime versions."""
        return {"python": "synthetic", "torch": "synthetic"}

    def run(self, plan, sources, streams, documents, selection, published,
            step19, trips):
        """Return an invented run for the pinned selection."""
        FakeRuntime.seen = {"sources": sorted(sources),
                            "streams": sorted(streams),
                            "selection": [item["patient_index"]
                                          for item in selection],
                            "published": dict(published),
                            "step19": dict(step19)}
        for stream in streams.values():
            stream.verify()
        return invented_run(n=step19["graphs_filtered"], rows=self.rows)


class ScratchRunTests(unittest.TestCase):
    """End-to-end real-mode orchestration over invented scratch inputs."""

    def setUp(self):
        """Write invented pinned inputs into a workspace scratch tree."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT, True),
                        "test scratch space must stay in the workspace root")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="run-binding-", dir=root)
        self.addCleanup(self.remove_scratch)
        inputs = os.path.join(self.work, "protected", "inputs")
        os.makedirs(inputs)
        step20_plan = amp.synthetic_step20_plan()
        documents = amp.synthetic_documents(step20_plan)
        raw = {
            "model": b"# invented model source\n",
            "explain_patient": b"# invented bridge source\n",
            "train": b"# invented trainer source\n",
            "metrics": json.dumps({"task": "diagnosis", "data_source": "omop",
                                   "no_kg": False, "test": REFERENCE,
                                   "args": good_args()}).encode(),
            "label_vocab": json.dumps(list(range(6))).encode(),
            "concept_vocab": json.dumps({"condition": {}, "procedure": {},
                                         "drug": {}}).encode(),
            "mapping": json.dumps(documents["step17_mapping"]).encode(),
            "outputs": json.dumps(documents["step20_outputs"]).encode(),
            "parity": json.dumps(documents["step19_summary"]).encode(),
            "graph": b"invented graph bytes",
            "checkpoint": b"invented checkpoint bytes"}
        assets = {}
        for role, data in raw.items():
            path = os.path.join(inputs, role + ".bin")
            with open(path, "wb") as handle:
                handle.write(data)
            assets[role] = {"path": path,
                            "sha256": hashlib.sha256(data).hexdigest()}
        self.raw = raw
        self.plan = {"upstream_root": os.path.join(self.work, "upstream"),
                     "assets": assets,
                     "output_dir": os.path.join(self.work, "protected",
                                                "verification", "out"),
                     "protected_root": os.path.join(self.work, "protected")}
        FakeRuntime.rows = None

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def repin_documents(self):
        """Point the invented documents at the scratch identities."""
        mapping_document = json.loads(self.raw["mapping"])
        parity_document = json.loads(self.raw["parity"])
        assets = self.plan["assets"]
        mapping_document["inputs"]["graph"]["sha256"] = assets["graph"][
            "sha256"]
        mapping_document["inputs"]["bridge"]["sha256"] = assets[
            "explain_patient"]["sha256"]
        parity_document["graph_sha256"] = assets["graph"]["sha256"]
        parity_document["source_sha256"] = {
            "explain_patient": assets["explain_patient"]["sha256"],
            "model": assets["model"]["sha256"]}
        for role, document in (("mapping", mapping_document),
                               ("parity", parity_document)):
            data = json.dumps(document).encode()
            with open(assets[role]["path"], "wb") as handle:
                handle.write(data)
            assets[role]["sha256"] = hashlib.sha256(data).hexdigest()

    def test_publishes_summary_and_cases_once(self):
        """A bound run publishes two files; a rerun refuses to overwrite."""
        self.repin_documents()
        before = {role: mapping.sha256_file(entry["path"])
                  for role, entry in self.plan["assets"].items()}
        summary, digests = binding.run_real(self.plan,
                                            runtime_factory=FakeRuntime)
        out = self.plan["output_dir"]
        self.assertEqual(sorted(os.listdir(out)),
                         [binding.CASES_NAME, binding.SUMMARY_NAME])
        self.assertFalse(os.path.lexists(out + binding.PARTIAL))
        for name, value in digests.items():
            self.assertEqual(mapping.sha256_file(os.path.join(out, name)),
                             value)
        self.assertEqual(summary["status"], binding.VERIFIED)
        with open(os.path.join(out, binding.CASES_NAME), "rb") as handle:
            cases = json.loads(handle.read())
        self.assertEqual([case["patient_index"] for case in cases["cases"]],
                         PILOT)
        self.assertEqual(summary["cases_file"]["sha256"],
                         digests[binding.CASES_NAME])
        self.assertEqual(FakeRuntime.seen["selection"], PILOT)
        self.assertEqual(sorted(FakeRuntime.seen["published"]), PILOT)
        self.assertEqual(FakeRuntime.seen["streams"], ["checkpoint", "graph"])
        after = {role: mapping.sha256_file(entry["path"])
                 for role, entry in self.plan["assets"].items()}
        self.assertEqual(before, after)
        with self.assertRaisesRegex(binding.BindingError, "output_exists"):
            binding.run_real(self.plan, runtime_factory=FakeRuntime)

    def test_changed_input_publishes_nothing(self):
        """A pin mismatch fails before the runtime runs."""
        self.repin_documents()
        with open(self.plan["assets"]["metrics"]["path"], "ab") as handle:
            handle.write(b" ")
        FakeRuntime.seen = None
        with self.assertRaisesRegex(binding.BindingError,
                                    "input_hash_mismatch:metrics"):
            binding.run_real(self.plan, runtime_factory=FakeRuntime)
        self.assertIsNone(FakeRuntime.seen)
        for path in (self.plan["output_dir"],
                     self.plan["output_dir"] + binding.PARTIAL):
            self.assertFalse(os.path.lexists(path))

    def test_unbound_mapping_identity_is_refused(self):
        """A mapping built against another graph fails closed."""
        with self.assertRaisesRegex(binding.BindingError,
                                    "mapping_pinned_identity_mismatch"):
            binding.run_real(self.plan, runtime_factory=FakeRuntime)

    def test_output_boundaries(self):
        """Existing, unprotected and input-overlapping outputs are refused."""
        self.repin_documents()
        os.makedirs(self.plan["output_dir"] + binding.PARTIAL)
        with self.assertRaisesRegex(binding.BindingError, "output_exists"):
            binding.run_real(self.plan, runtime_factory=FakeRuntime)
        os.rmdir(self.plan["output_dir"] + binding.PARTIAL)
        inside = copy.deepcopy(self.plan)
        inside["output_dir"] = os.path.join(self.work, "protected", "inputs",
                                            "out")
        with self.assertRaisesRegex(binding.BindingError, "overlaps_input"):
            binding.run_real(inside, runtime_factory=FakeRuntime)
        outside = copy.deepcopy(self.plan)
        outside["output_dir"] = os.path.join(self.work, "elsewhere")
        with self.assertRaisesRegex(binding.BindingError, "not_protected"):
            binding.run_real(outside, runtime_factory=FakeRuntime)


if __name__ == "__main__":
    unittest.main()
