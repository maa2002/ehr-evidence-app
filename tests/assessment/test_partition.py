"""Synthetic checks for ehr_evidence_app.assessment.partition.

The case sets are invented; publishing is checked in the configured
workspace test scratch directory, and skipped where it is missing.
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from ehr_evidence_app.assessment import partition
from ehr_evidence_app.evidence import readers
from ehr_evidence_app.storage import policy as guard

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
DIGESTS = {label: {"closure_sha256": "0" * 64, "mapping_sha256": "0" * 64}
           for label in ("pilot-10-v1", "dev-30-v1", "eval-100-v1")}


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


def changed(label, change):
    """Return the invented case sets with one case set's Members changed
    by change(members)."""
    case_sets = partition.synthetic_case_sets()
    source, members = case_sets[label]
    case_sets[label] = (source, change(members))
    return case_sets


class PartitionTests(unittest.TestCase):
    """Case sets, persons and upstream partitions."""

    def test_partition_of(self):
        """The pilot and dev-30-v1 are development, eval-100-v1 final, and
        nothing else has a partition."""
        self.assertEqual(partition.partition_of(readers.PILOT_LABEL),
                         "development")
        self.assertEqual(partition.partition_of("dev-30-v1"), "development")
        self.assertEqual(partition.partition_of("eval-100-v1"), "final")
        with self.assertRaisesRegex(partition.PartitionError,
                                    "case_set_unknown"):
            partition.partition_of("dev-31-v1")

    def test_drawn_from(self):
        """The registry's case sets were drawn from their upstream
        partitions; the pilot from none."""
        self.assertIsNone(partition.drawn_from(readers.PILOT_LABEL))
        self.assertEqual(partition.drawn_from("dev-30-v1"), "validation")
        self.assertEqual(partition.drawn_from("eval-100-v1"), "test")

    def test_person_in_both_is_refused(self):
        """A development person among the final cases stops the record,
        whichever case set holds them."""
        person = partition.synthetic_case_sets()[readers.PILOT_LABEL][1][0]
        case_sets = changed("eval-100-v1", lambda members: members + (
            members[0]._replace(case_number=50,
                                subject_id=person.subject_id),))
        with self.assertRaisesRegex(partition.PartitionError,
                                    "person_in_both"):
            partition.partition_document(case_sets, DIGESTS)

    def test_other_upstream_partition_is_refused(self):
        """A case of another upstream partition in a drawn case set stops
        the record; the pilot, drawn from none, may mix them."""
        case_sets = changed("eval-100-v1", lambda members: members[:1] + (
            members[1]._replace(upstream_partition="validation"),)
            + members[2:])
        with self.assertRaisesRegex(partition.PartitionError,
                                    "partition_mismatch"):
            partition.check(case_sets)
        self.assertIsNone(partition.refused(
            partition.synthetic_case_sets()))

    def test_repeated_case_is_refused(self):
        """A case number twice in a case set stops the record."""
        case_sets = changed("dev-30-v1", lambda members: members
                            + members[:1])
        with self.assertRaisesRegex(partition.PartitionError,
                                    "case_repeated"):
            partition.check(case_sets)

    def test_document(self):
        """The record keeps every case with its upstream partition and
        flags only test-partition cases as unseen by the GNN; its summary
        counts persons and unseen cases."""
        document = partition.partition_document(
            partition.synthetic_case_sets(), DIGESTS)
        self.assertEqual(document["version"], "downstream-partition-v1")
        self.assertEqual(document["partitions"], {
            "development": ["pilot-10-v1", "dev-30-v1"],
            "final": ["eval-100-v1"]})
        pilot = document["case_sets"]["pilot-10-v1"]
        self.assertIsNone(pilot["drawn_from"])
        self.assertEqual([(case["upstream_partition"], case["unseen_by_gnn"])
                          for case in pilot["cases"]],
                         [("train", False), ("test", True)])
        final = document["summary"]["case_sets"]["eval-100-v1"]
        self.assertEqual((final["cases"], final["admitted"],
                          final["unseen_by_gnn"],
                          final["admitted_unseen_by_gnn"]), (4, 3, 4, 3))
        self.assertEqual(document["summary"]["persons"], {
            "development": 5, "final": 4, "in_both": 0})
        self.assertEqual(partition.record_bytes(document),
                         partition.record_bytes(
                             partition.partition_document(
                                 partition.synthetic_case_sets(), DIGESTS)))

    def test_report_holds_counts_only(self):
        """The printed report names case sets and counts, never a person
        or a subject."""
        found, problems = partition.run_synthetic()
        self.assertEqual(problems, [])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            partition.report(found)
        for case_set in partition.synthetic_case_sets().values():
            for member in case_set[1]:
                self.assertNotIn(str(member.subject_id), out.getvalue())
                self.assertNotIn(str(member.person_id), out.getvalue())

    def test_main_default_mode(self):
        """The default mode passes."""
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(partition.main([]), 0)
        self.assertIn("RESULT: PASS", out.getvalue())


class PublishTests(unittest.TestCase):
    """The record is written once, beneath the protected root."""

    def setUp(self):
        """Make a scratch protected root in the workspace test temp."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.assertTrue(guard.is_within(root, guard.PRIVATE_ROOT, True),
                        "test scratch space must stay in the workspace root")
        if guard.fixed_policy_problem():
            self.skipTest("the fixed layout policy does not hold here")
        self.root = root
        self.work = tempfile.mkdtemp(prefix="assessment-partition-",
                                     dir=root)
        self.addCleanup(self.remove_scratch)
        protected = os.path.join(self.work, "protected")
        self.plan = {"output_dir": os.path.join(protected, "assessment",
                                                partition.VERSION),
                     "protected_root": protected}

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def test_published_once(self):
        """The record is written whole and pinned by its bytes; a second
        run is refused."""
        case_sets = partition.synthetic_case_sets()
        with mock.patch.object(partition, "read_case_sets",
                               return_value=(case_sets, DIGESTS)):
            document, pin = partition.run_partition(self.plan)
            with self.assertRaisesRegex(partition.PartitionError,
                                        "output_exists"):
                partition.run_partition(self.plan)
        path = os.path.join(self.plan["output_dir"], partition.RECORD_NAME)
        with open(path, "rb") as handle:
            data = handle.read()
        self.assertEqual(pin, (len(data), hashlib.sha256(data).hexdigest()))
        self.assertEqual(json.loads(data), document)
        self.assertFalse(os.path.lexists(self.plan["output_dir"]
                                         + partition.PARTIAL))
        pinned = dict(self.plan, record={"path": path, "size_bytes": pin[0],
                                         "sha256": pin[1]})
        self.assertEqual(partition.read_record(pinned), document)
        pinned["record"] = dict(pinned["record"], sha256="0" * 64)
        with self.assertRaisesRegex(partition.PartitionError,
                                    "record_pin_mismatch"):
            partition.read_record(pinned)
        with self.assertRaisesRegex(partition.PartitionError,
                                    "record_not_pinned"):
            partition.read_record(dict(self.plan, record=None))

    def test_unprotected_output_is_refused(self):
        """An output outside the protected root is refused before anything
        is read."""
        plan = dict(self.plan, output_dir=os.path.join(self.work, "open"))
        with mock.patch.object(partition, "read_case_sets") as read:
            with self.assertRaisesRegex(partition.PartitionError,
                                        "output_not_protected"):
                partition.run_partition(plan)
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
