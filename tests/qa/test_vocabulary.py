"""Synthetic checks for ehr_evidence_app.qa.vocabulary.

Every Athena table is invented inline; scratch files are created only under
the configured workspace test temp directory. The Athena release itself is
never read.
"""

import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout

from ehr_evidence_app.gnn import mapping
from ehr_evidence_app.qa import vocabulary
from ehr_evidence_app.storage import policy as guard

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def rows(role):
    """Return invented rows of one Athena table."""
    return [dict(row) for row in vocabulary.SYNTHETIC_TABLES[role]]


class SelectionTests(unittest.TestCase):
    """Only the rows resolution needs are kept."""

    def test_concepts(self):
        """Standard conditions and ICD codes are kept; drugs are not."""
        standard, icd, lines = vocabulary.select_concepts(rows("concept"))
        self.assertEqual((standard, icd), ({10, 11}, {20}))
        self.assertEqual([line[:2] for line in lines],
                         [(10, "standard"), (11, "standard"), (20, "icd")])
        self.assertEqual(lines[2][4], "Q50.9")

    def test_synonyms(self):
        """English synonyms of standard conditions, once each."""
        table = rows("synonym") + [rows("synonym")[0]]
        self.assertEqual(vocabulary.select_synonyms(table, {10, 11}),
                         [(11, "Synthetic cardiac failure")])

    def test_ancestors_and_maps(self):
        """Pairs stay inside standard conditions; only valid Maps to."""
        self.assertEqual(vocabulary.select_ancestors(rows("ancestor"),
                                                     {10, 11}),
                         [(10, 10, 0), (10, 11, 1), (11, 11, 0)])
        self.assertEqual(vocabulary.select_icd_maps(rows("relationship"),
                                                    {20}, {10, 11}),
                         [(20, 11)])

    def test_cells_are_single_line(self):
        """Tabs and line breaks inside a value become single spaces."""
        self.assertEqual(vocabulary.clean(" a\tb\r\nc  d "), "a b c d")
        self.assertEqual(vocabulary.clean(None), "")

    def test_build_and_encoding(self):
        """The in-memory build yields sorted, headed TSV files."""
        files, counts = vocabulary.run_synthetic()
        self.assertEqual(counts, {"standard_conditions": 2,
                                  "icd_concepts": 1, "synonyms": 1,
                                  "ancestor_pairs": 3, "icd_maps": 1})
        lines = files["ancestors.tsv"].decode("utf-8").splitlines()
        self.assertEqual(lines[0], "ancestor_id\tdescendant_id\tmin_levels")
        self.assertEqual(lines[1:], ["10\t10\t0", "10\t11\t1", "11\t11\t0"])

    def test_plan_pins(self):
        """Four Athena inputs are pinned by size and SHA-256."""
        plan = vocabulary.real_plan()
        self.assertEqual(sorted(plan["inputs"]),
                         ["ancestor", "concept", "relationship", "synonym"])
        for entry in plan["inputs"].values():
            self.assertTrue(entry["path"].startswith(vocabulary.ATHENA_ROOT))
            self.assertRegex(entry["sha256"], "^[0-9a-f]{64}$")
            self.assertGreater(entry["size_bytes"], 0)

    def test_synthetic_mode(self):
        """The default command builds the invented vocabulary."""
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(vocabulary.main([]), 0)
        self.assertIn("RESULT: PASS", output.getvalue())


class ScratchBuildTests(unittest.TestCase):
    """Verified reading and publication over invented scratch files."""

    def setUp(self):
        """Write the invented Athena tables into a scratch directory."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        if guard.fixed_policy_problem():
            self.skipTest("the fixed layout policy does not hold here")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="qa-vocabulary-", dir=root)
        self.addCleanup(self.remove_scratch)
        athena = os.path.join(self.work, "athena")
        os.makedirs(athena)
        self.plan = {"inputs": {}, "source_dir": athena,
                     "protected_root": os.path.join(self.work,
                                                    "protected")}
        self.plan["output_dir"] = os.path.join(self.plan["protected_root"],
                                               "vocabularies", "synthetic")
        for role in vocabulary.INPUTS:
            data = vocabulary.synthetic_bytes(role)
            path = os.path.join(athena, role + ".csv")
            with open(path, "wb") as handle:
                handle.write(data)
            self.plan["inputs"][role] = {
                "path": path, "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def test_build_publishes_once(self):
        """Files and manifest are published; a rerun refuses."""
        counts, digests = vocabulary.run_real(self.plan)
        self.assertEqual(counts["ancestor_pairs"], 3)
        out = self.plan["output_dir"]
        self.assertEqual(sorted(os.listdir(out)),
                         sorted(list(vocabulary.FILES.values())
                                + [vocabulary.MANIFEST_NAME]))
        with open(os.path.join(out, vocabulary.MANIFEST_NAME), "rb") as handle:
            manifest = json.loads(handle.read())
        for name, entry in manifest["files"].items():
            self.assertEqual(mapping.sha256_file(os.path.join(out, name)),
                             entry["sha256"])
            self.assertEqual(digests[name], entry["sha256"])
        self.assertFalse(manifest["patient_bearing"])
        with self.assertRaisesRegex(vocabulary.VocabularyError,
                                    "output_exists"):
            vocabulary.run_real(self.plan)

    def test_changed_input_publishes_nothing(self):
        """A changed input fails its pin after being read once."""
        with open(self.plan["inputs"]["synonym"]["path"], "ab") as handle:
            handle.write(b"99\tExtra\t4180186\n")
        with self.assertRaisesRegex(vocabulary.VocabularyError,
                                    "input_hash_mismatch:synonym"):
            vocabulary.run_real(self.plan)
        self.assertFalse(os.path.lexists(self.plan["output_dir"]))
        self.plan["inputs"]["concept"]["path"] += ".missing"
        with self.assertRaisesRegex(vocabulary.VocabularyError,
                                    "input_path_rejected:concept"):
            vocabulary.run_real(self.plan)

    def test_inputs_outside_the_source_directory_are_refused(self):
        """Only files directly inside the named source directory pass."""
        elsewhere = os.path.join(self.work, "elsewhere")
        os.makedirs(elsewhere)
        moved = os.path.join(elsewhere, "concept.csv")
        shutil.copyfile(self.plan["inputs"]["concept"]["path"], moved)
        self.plan["inputs"]["concept"]["path"] = moved
        with self.assertRaisesRegex(vocabulary.VocabularyError,
                                    "input_path_rejected:concept"):
            vocabulary.run_real(self.plan)
        self.plan["source_dir"] = guard.RUNTIME_ROOT
        with self.assertRaisesRegex(vocabulary.VocabularyError,
                                    "input_path_rejected"):
            vocabulary.run_real(self.plan)


if __name__ == "__main__":
    unittest.main()
