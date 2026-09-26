"""Synthetic checks for ehr_evidence_app.qa.heldout.

Archives, patients, admissions and questions are invented; the real modes
are only checked for refusing to run without their pinned inputs.
"""

import contextlib
import io
import unittest

from ehr_evidence_app.qa import heldout, suite

VOCAB = suite.synthetic_vocabulary()


def partition(files=None, ours=heldout.SYNTHETIC_OURS):
    """Return the partition record of an invented archive."""
    archive = heldout.open_archive(heldout.archive_bytes(
        files or heldout.synthetic_files()))
    return heldout.partition_document(archive, ours, {"name": "synthetic"},
                                      {})


class ArchiveTests(unittest.TestCase):
    """The archive is used only when every member matches its checksum."""

    def test_members_are_verified(self):
        """A changed member or a missing one stops the run."""
        files = heldout.synthetic_files()
        sums = "0" * 64 + "  cohort.csv\n"
        with self.assertRaisesRegex(heldout.HeldOutError,
                                    "archive_member_mismatch"):
            heldout.open_archive(heldout.archive_bytes(files, sums))
        del files["qa_note.jsonl"]
        with self.assertRaisesRegex(heldout.HeldOutError,
                                    "archive_member_missing"):
            heldout.open_archive(heldout.archive_bytes(files))
        with self.assertRaisesRegex(heldout.HeldOutError,
                                    "archive_unexpected"):
            heldout.open_archive(b"not an archive")


class PartitionTests(unittest.TestCase):
    """The three traps of the shipped split are handled."""

    def test_partitions(self):
        """Held-out is the test split; our patients leave training."""
        document = partition()
        found = {name: entry["hadm_ids"]
                 for name, entry in document["partitions"].items()}
        self.assertEqual(found, {"held_out": [9103], "validation": [9105],
                                 "training": [9102]})
        self.assertEqual(document["excluded"], {
            "admissions_in_no_split": 1,
            "questions_of_admissions_outside_cohort": 0,
            "training_admissions_of_case_set_patients": 1,
            "validation_admissions_in_the_test_split": 1,
            "validation_admissions_of_case_set_patients": 1})
        self.assertTrue(document["patient_bearing"])

    def test_a_shared_patient_stops_the_partition(self):
        """Training and test must not share a patient."""
        cohort = heldout.SYNTHETIC_COHORT + ((902, 9107, ("test",)),)
        with self.assertRaisesRegex(heldout.HeldOutError,
                                    "split_not_patient_disjoint"):
            partition(heldout.synthetic_files(cohort=cohort))

    def test_unknown_event_types_stop_the_partition(self):
        """Every question has one of the known event types."""
        questions = ((9103, "notes", "What happened?"),)
        with self.assertRaisesRegex(heldout.HeldOutError,
                                    "event_type_unexpected"):
            partition(heldout.synthetic_files(questions=questions))


class ReadingTests(unittest.TestCase):
    """Questions are read as the pipeline would read them."""

    def test_families(self):
        """Ranking words mark the ranking templates."""
        for question, family in (
                ("What were the billed diagnoses of the patient?",
                 "diagnoses_list"),
                ("What was the first billed diagnosis?", "diagnoses_ranking"),
                ("List the top 3 diagnoses.", "diagnoses_ranking"),
                ("Which were the patient's primary diagnoses?",
                 "diagnoses_ranking")):
            self.assertEqual(heldout.family_of("diagnoses_icd", question),
                             family, question)
        self.assertEqual(heldout.family_of("labevents", "List the labs."),
                         "labevents")

    def test_readings(self):
        """Lists, resolved presence checks, clarifications, abstentions."""
        for question, found in (
                ("List the diagnoses.", "list"),
                ("Is synthetic concept 1001 recorded?", "presence"),
                ("Is zzqx recorded?", "clarification:concept_unknown"),
                ("What was the gender of the patient?",
                 "clarification:task_ambiguous"),
                ("What medications is she on?", "abstention:other_data"),
                ("Is a chest X-ray recorded?", "abstention:other_data"),
                ("Was AKI recorded after the transfer?",
                 "abstention:time_or_count")):
            self.assertEqual(heldout.reading(question, VOCAB)[0], found,
                             question)

    def test_expectations(self):
        """Lists must be read as lists; nothing else may be answered."""
        self.assertTrue(heldout.met("diagnoses_list", "list"))
        self.assertFalse(heldout.met("diagnoses_list",
                                     "clarification:task_ambiguous"))
        self.assertFalse(heldout.met("diagnoses_ranking", "list"))
        self.assertFalse(heldout.met("labevents", "presence"))
        self.assertTrue(heldout.met("labevents", "abstention:other_data"))


class RunTests(unittest.TestCase):
    """Only held-out questions are read, and only with pinned inputs."""

    def test_synthetic_run(self):
        """The training question is never read."""
        _, result = heldout.run_synthetic()
        for key, value in heldout.SYNTHETIC_EXPECTED.items():
            self.assertEqual(result[key], value, key)
        self.assertEqual(set(result["families"]), {
            "diagnoses_list", "diagnoses_ranking", "labevents",
            "prescriptions", "patient_demographics"})

    def test_unpinned_partition_is_refused(self):
        """The test needs the pinned partition record."""
        plan = dict(heldout.real_plan(), partition=None)
        with self.assertRaisesRegex(heldout.HeldOutError,
                                    "partition_not_pinned"):
            heldout.run_held_out(plan)

    def test_command(self):
        """The default mode passes; real options need --operator-real."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(heldout.main([]), 0)
            self.assertEqual(heldout.main(["--held-out"]), 2)
            self.assertEqual(heldout.main(["--operator-real"]), 2)
        self.assertIn("RESULT: PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
