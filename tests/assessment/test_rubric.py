"""Synthetic checks for ehr_evidence_app.assessment.rubric.

No rater runs: a stand-in rater and a stub server stand for it, and the
texts are invented.
"""

import csv
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from ehr_evidence_app.assessment import references, report, rubric
from ehr_evidence_app.generation import shape
from ehr_evidence_app.validation import language
from tests.assessment.test_report import configured_test_temp


def invented_reference(context, template):
    """Write lists with a record the bundle does not hold in their first
    item, and every other request correctly."""
    document = shape.document_of(template)
    if context.task == "condition_list" and template.outcome == "answer":
        item = document["items"][0]
        document["items"][0] = ["E999", ["E999"] + item[1][1:], item[2],
                                item[3]]
    return json.dumps(document)


class StubServer:
    """A stand-in for the rating server: it records each request and
    replies with a fixed content."""

    def __init__(self, content):
        """Hold the reply."""
        self.content = content
        self.requests = []

    def __call__(self, request, timeout=None):
        """Record the request and reply."""
        self.requests.append((request.full_url,
                              json.loads(request.data.decode("utf-8"))))
        if request.full_url.endswith("/api/show"):
            body = {"details": {"family": "invented"}}
        else:
            body = {"message": {"content": self.content}}
        return io.BytesIO(json.dumps(body).encode("utf-8"))


class FormTests(unittest.TestCase):
    """The planted faults and controls."""

    def test_named_and_paraphrased_forms(self):
        """Each class's named form breaks the runtime's wording rules and
        its paraphrase does not; the controls break none. So in every
        set of wordings."""
        for faults, controls in rubric.WORDINGS.values():
            for kind, outcome in (("explanation", "explanation"),
                                  ("answer", "answer")):
                for name, (named, paraphrase) in faults[kind].items():
                    with self.subTest(kind=kind, fault=name):
                        self.assertTrue(language.findings(named, outcome))
                        self.assertEqual(language.findings(paraphrase,
                                                           outcome), [])
                self.assertEqual(language.findings(controls[kind],
                                                   outcome), [])

    def test_sets_apart(self):
        """Each set's wordings are its own, with the same classes; the
        reported set is not one the instructions are revised on, and the
        phrases that carry its paraphrases are not in the instructions."""
        markers = ("sprang from", "sent the prediction up", "brought another",
                   "outweighs", "all there is", "afflict", "how near",
                   "thin ice", "sensible next step", "sure thing")
        self.assertNotIn(rubric.REPORTED, rubric.REVISION)
        seen = {"explanation": set(), "answer": set()}
        for name, (faults, _) in rubric.WORDINGS.items():
            self.assertEqual(set(faults), set(seen))
            for kind in seen:
                self.assertEqual(set(faults[kind]),
                                 set(rubric.FAULTS[kind]))
                sentences = {sentence for forms in faults[kind].values()
                             for sentence in forms}
                self.assertFalse(sentences & seen[kind], name)
                seen[kind] |= sentences
        reported, _ = rubric.WORDINGS[rubric.REPORTED]
        for kind in seen:
            for forms in reported[kind].values():
                self.assertTrue(any(marker in forms[1].lower()
                                    for marker in markers), forms[1])
        for marker in markers:
            self.assertNotIn(marker, rubric.CRITERIA.lower())

    def test_planted_variants(self):
        """A text gives its clean form and, for each set of wordings, a
        control and both forms of every class, each with its expected
        mark."""
        found = rubric.planted("Diabetes: 2 records.", "answer")
        self.assertEqual(len(found), 2 + 2 * len(rubric.FAULTS["answer"]))
        self.assertEqual([item[2] for item in found[:2]], ["pass", "pass"])
        self.assertEqual({item[2] for item in found[2:]}, {"fail"})
        both = rubric.planted("Diabetes: 2 records.", "answer",
                              rubric.REVISION)
        self.assertEqual(len(both), 1 + 2 * (1 + 2 * len(
            rubric.FAULTS["answer"])))
        self.assertEqual(sum(item[0] == "control" for item in both), 2)

    def test_check_counts(self):
        """A rater that catches only the named forms is counted right on
        them and on the clean text and control, and wrong on every
        paraphrase."""
        named = [forms[0] for forms in rubric.FAULTS["answer"].values()]
        found = rubric.check_rater([("answer", "Diabetes: 2 records.")],
                                   rubric.StandIn(named))
        for (kind, name, form), (right, wrong, none) in found.items():
            if form == "paraphrase":
                self.assertEqual((right, wrong), (0, 1))
            else:
                self.assertEqual((right, wrong), (1, 0))
            self.assertEqual(none, 0)


class ReportedCheckTests(unittest.TestCase):
    """The reported check: the set kept for it alone, the frozen
    instructions, and once for them."""

    def test_wordings_are_named_sets(self):
        """Unknown sets, and the reported set beside another, are
        refused before anything is rated."""
        for wordings in ((), ("fourth",), (rubric.REPORTED, "first")):
            with self.assertRaisesRegex(rubric.RubricError,
                                        "wordings_unknown"):
                rubric.run_check("dev-30-v1", 1, None, keep=False,
                                 wordings=wordings)

    def test_reported_check_needs_frozen_instructions(self):
        """Instructions other than the frozen ones are not reported."""
        with mock.patch.object(rubric, "CRITERIA_SHA256", "0" * 64):
            with self.assertRaisesRegex(rubric.RubricError,
                                        "instructions_not_frozen"):
                rubric.run_check("dev-30-v1", 1, None,
                                 wordings=(rubric.REPORTED,))
        with self.assertRaisesRegex(rubric.RubricError,
                                    "reported_check_kept"):
            rubric.run_check("dev-30-v1", 1, None, keep=False,
                             wordings=(rubric.REPORTED,))

    def test_reported_once(self):
        """A record of a reported check of the same instructions stops
        another; one of other instructions, or of revision sets, does
        not."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "setup-destinations first")
        directory = tempfile.mkdtemp(prefix="rater-checks-", dir=root)
        try:
            def record(name, wordings, digest):
                with open(os.path.join(directory, name), "w",
                          encoding="utf-8") as handle:
                    json.dump({"wordings": wordings, "rater": {
                        "criteria_sha256": digest}}, handle)
            digest = rubric.CRITERIA_SHA256
            record("a.json", list(rubric.REVISION), digest)
            record("b.json", [rubric.REPORTED], "1" * 64)
            self.assertFalse(rubric.reported_before(directory, digest))
            record("c.json", [rubric.REPORTED], digest)
            self.assertTrue(rubric.reported_before(directory, digest))
        finally:
            shutil.rmtree(directory)


class RaterTests(unittest.TestCase):
    """The rating model's client."""

    def test_reply_is_read(self):
        """The first word is the mark, after any thinking; anything else
        is no mark."""
        self.assertEqual(rubric.mark_of("FAIL"), "fail")
        self.assertEqual(rubric.mark_of(" pass."), "pass")
        self.assertEqual(rubric.mark_of("<think>x</think> Fail"), "fail")
        self.assertIsNone(rubric.mark_of("It passes."))
        self.assertIsNone(rubric.mark_of(None))

    def test_workstation_only(self):
        """Texts hold patient records: only an http address on this
        workstation is accepted."""
        for endpoint in ("http://example.org:11434", "https://127.0.0.1",
                         "ftp://localhost"):
            with self.assertRaisesRegex(rubric.RubricError,
                                        "rater_not_local"):
                rubric.Rater(endpoint, "rater")
        self.assertEqual(rubric.Rater("http://127.0.0.1:11434/",
                                      "rater").endpoint,
                         "http://127.0.0.1:11434")

    def test_one_text_with_the_criteria(self):
        """The rater gets the criteria and one text, with fixed settings,
        and nothing else; its account of itself names its instructions."""
        server = StubServer("PASS")
        rater = rubric.Rater("http://localhost:11434", "rater",
                             opener=server)
        self.assertEqual(rater.rate("A text."), "pass")
        url, payload = server.requests[0]
        self.assertTrue(url.endswith("/api/chat"))
        self.assertEqual(payload["messages"],
                         [{"role": "system", "content": rubric.CRITERIA},
                          {"role": "user", "content": "A text."}])
        self.assertEqual(payload["options"], {"temperature": 0,
                                              "seed": rubric.SEED})
        described = rater.describe()
        self.assertEqual(described["model"], "rater")
        self.assertEqual(described["criteria_sha256"],
                         rubric.CRITERIA_SHA256)
        self.assertEqual(described["rubric"], rubric.RUBRIC_VERSION)

    def test_instructions_frozen(self):
        """The instructions are the frozen ones: changing them needs a new
        version, digest and check."""
        self.assertEqual(hashlib.sha256(
            rubric.CRITERIA.encode("utf-8")).hexdigest(),
            rubric.CRITERIA_SHA256)

    def test_a_slow_reply_is_a_timeout(self):
        """A reply that takes too long is told from a rater that cannot be
        reached."""
        import urllib.error

        def slow(request, timeout=None):
            raise TimeoutError("timed out")

        def refused(request, timeout=None):
            raise urllib.error.URLError(ConnectionRefusedError())

        def connect_slow(request, timeout=None):
            raise urllib.error.URLError(TimeoutError())
        for opener, code in ((slow, "rater_timed_out"),
                             (connect_slow, "rater_timed_out"),
                             (refused, "rater_unreachable")):
            rater = rubric.Rater("http://127.0.0.1:11434", "rater",
                                 opener=opener)
            with self.assertRaisesRegex(rubric.RubricError, code):
                rater.rate("A text.")

    def test_shuffled_once_each(self):
        """Every text is rated once, in an order shuffled with the fixed
        seed."""
        seen = []

        class Recorder:
            def rate(self, text):
                seen.append(text)
                return "pass"
        texts = [("k%d" % number, "text %d" % number)
                 for number in range(10)]
        marks = rubric.rate_all(texts, Recorder())
        self.assertEqual(sorted(seen), sorted(text for _, text in texts))
        self.assertNotEqual(seen, [text for _, text in sorted(texts)])
        self.assertEqual(set(marks), {key for key, _ in texts})


class OwnerTests(unittest.TestCase):
    """The owner's check."""

    def test_sample(self):
        """Up to 15 texts the rater failed and the rest passed, 30 in all;
        with fewer fails, every fail is taken."""
        marks = {"t%02d" % number: "fail" if number < 5 else "pass"
                 for number in range(60)}
        sample = rubric.owner_sample(marks)
        self.assertEqual(len(sample), 30)
        self.assertEqual(sum(marks[key] == "fail" for key in sample), 5)
        self.assertEqual(sample, rubric.owner_sample(marks))

    def test_agreement(self):
        """Agreement counts the owner's marks under each rater mark."""
        marks = {"a": "fail", "b": "fail", "c": "pass"}
        self.assertEqual(rubric.agreement(marks, {"a": "fail", "b": "pass",
                                                  "c": "pass"}),
                         {"fail": {"fail": 1, "pass": 1},
                          "pass": {"pass": 1}})


class Alternate:
    """A stand-in rater that fails about a third of the texts, the same
    ones each time."""

    def rate(self, text):
        """Return a mark fixed by the text's digest."""
        return "fail" if hashlib.sha256(text.encode("utf-8")).digest()[
            0] % 3 == 0 else "pass"


def invented_rating():
    """Return (rated, marks, document, sample) of an invented run."""
    return rubric.rate_records(
        report.synthetic_records(invented_reference),
        references.synthetic_sources(), report.synthetic_bundles(),
        references.configured_limit(), Alternate())


class RunRatingTests(unittest.TestCase):
    """Rating a finished run, and the owner's sheet."""

    def test_every_text_marked_and_sampled(self):
        """Each text of each condition is marked under its own key, and
        the sample holds up to 15 the rater failed, 30 in all."""
        rated, marks, document, sample = invented_rating()
        self.assertEqual(set(marks), {rubric.text_key(item)
                                      for item in rated})
        self.assertEqual(len(marks), len(rated))
        self.assertTrue(set(sample) <= set(marks))
        self.assertLessEqual(len(sample), rubric.SAMPLE)
        self.assertLessEqual(sum(marks[key] == "fail" for key in sample),
                             rubric.SAMPLE_FAILED)
        self.assertEqual(set(document), {item.kind for item in rated})

    def test_sheet_shows_texts_only(self):
        """The owner's sheet holds the sampled texts in the sample's order,
        numbered, with an empty mark: neither the rater's mark nor the
        condition."""
        rated, marks, _, sample = invented_rating()
        texts = {rubric.text_key(item): item.text for item in rated}
        reader = csv.reader(io.StringIO(rubric.sheet_bytes(
            rated, sample).decode("utf-8-sig"), newline=""))
        rows = list(reader)
        self.assertEqual(tuple(rows[0]), rubric.SHEET_COLUMNS)
        self.assertEqual([row[0] for row in rows[1:]],
                         [str(number) for number in
                          range(1, len(sample) + 1)])
        self.assertEqual([row[1] for row in rows[1:]],
                         [texts[key] for key in sample])
        self.assertEqual({row[2] for row in rows[1:]}, {""})

    def test_unfrozen_instructions_are_not_used(self):
        """A run is rated with the frozen instructions only."""
        with mock.patch.object(rubric, "CRITERIA_SHA256", "0" * 64):
            with self.assertRaisesRegex(rubric.RubricError,
                                        "instructions_not_frozen"):
                rubric.rate_run("dev-30-v1-invented", Alternate())

    def test_final_run_rated_with_the_final_bundles(self):
        """A run of the final case set is rated under its freeze, by its
        rater, with the final case set's bundles."""
        from ehr_evidence_app.assessment import freeze
        read = []

        def bundles(case_set, final=False):
            """Note which bundles are read."""
            read.append((case_set, final))

        rater = mock.Mock()
        rater.describe.return_value = {}
        configuration = {"case_set": "eval-100-v1",
                         "freeze_sha256": "f" * 64}
        absent = os.path.join(tempfile.gettempdir(), "no-ratings-here")
        with mock.patch.object(rubric, "ratings_directory",
                               return_value=absent), \
                mock.patch.object(report, "read_run", return_value=(
                    configuration, {}, [])), \
                mock.patch.object(freeze, "allowed", return_value=(
                    {"rater": {}}, "f" * 64)), \
                mock.patch.object(rubric, "frozen_rater") as named, \
                mock.patch.object(references.Sources, "read"), \
                mock.patch.object(report.Bundles, "read",
                                  side_effect=bundles), \
                mock.patch.object(rubric, "rate_records",
                                  return_value=([], {}, {}, [])), \
                mock.patch.object(rubric, "write_ratings"):
            rubric.rate_run("run", rater)
        self.assertEqual(read, [("eval-100-v1", True)])
        named.assert_called_once_with({"rater": {}}, rater)

    def test_owners_marks_read_back_once(self):
        """The ratings and the sheet are written once; the owner's marks
        are read back against the rater's, by kind and in all, into an
        agreement written once; a changed sheet is refused."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "setup-destinations first")
        protected = tempfile.mkdtemp(prefix="ratings-", dir=root)
        try:
            rated, marks, document, sample = invented_rating()
            directory = rubric.ratings_directory("dev-run", protected)
            rubric.write_ratings(directory, "dev-run", {"model": "stand-in"},
                                 rated, marks, document, sample)
            with self.assertRaisesRegex(rubric.RubricError,
                                        "ratings_exist"):
                rubric.write_ratings(directory, "dev-run", {}, rated, marks,
                                     document, sample)
            path = os.path.join(directory, rubric.SHEET_NAME)
            with open(path, encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))
            # The owner agrees with the rater but on the first text, and
            # leaves the last unmarked.
            for number, row in enumerate(rows[1:], 1):
                mark = marks[sample[number - 1]]
                row[2] = ("" if number == len(sample) else mark
                          if number > 1 else
                          "fail" if mark == "pass" else "pass")
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                csv.writer(handle, lineterminator="\r\n").writerows(rows)
            found = rubric.read_owner("dev-run", protected)
            self.assertEqual((found["marked"], found["unmarked"]),
                             (len(sample) - 1, 1))
            agreed = sum(count for mark, owners in found["all"].items()
                         for owner, count in owners.items()
                         if owner == mark)
            self.assertEqual(agreed, len(sample) - 2)
            with self.assertRaisesRegex(rubric.RubricError,
                                        "output_exists"):
                rubric.read_owner("dev-run", protected)
            with self.assertRaisesRegex(rubric.RubricError,
                                        "sheet_changed"):
                rubric.owner_marks(rubric.sheet_bytes(rated, sample[1:]),
                                   sample)
        finally:
            shutil.rmtree(protected)


class RunTests(unittest.TestCase):
    """The rubric of an invented run."""

    def test_withheld_texts_are_not_rated(self):
        """Lists citing a record the bundle does not hold fail identity,
        evidence and history in the baseline; the gate withheld them in
        the controlled condition, so those requests are rated in one
        condition only and left out of the comparison."""
        records = report.synthetic_records(invented_reference)
        rated = rubric.run_texts(records, references.synthetic_sources(),
                                 report.synthetic_bundles(),
                                 references.configured_limit())
        failing = [item for item in rated if not item.identity]
        self.assertTrue(failing)
        self.assertEqual({item.condition for item in failing}, {"baseline"})
        marks = {(item.request_id, item.condition): "pass" for item in rated}
        document = rubric.rubric_document(rated, marks)
        answers = document["answer"]
        self.assertEqual(answers["rated_in_one_condition_only"],
                         len(failing))
        self.assertEqual(answers["controlled"]["all_four"],
                         answers["controlled"]["texts"])
        self.assertLess(answers["baseline"]["all_four"],
                        answers["baseline"]["texts"])

    def test_requests_sharing_an_identifier_are_rated_apart(self):
        """Each record's texts are rated under its own key, though its
        request shares an identifier with another's."""
        records = report.synthetic_records(invented_reference)
        for number, record in enumerate(records, 1):
            record["number"] = number
        records[1]["request"]["request_id"] = records[0]["request"][
            "request_id"]
        rated = rubric.run_texts(records, references.synthetic_sources(),
                                 report.synthetic_bundles(),
                                 references.configured_limit())
        keys = [(item.request_id, item.condition) for item in rated]
        self.assertEqual(len(keys), len(set(keys)))

    def test_default_mode_passes(self):
        """The module's own check finds nothing."""
        self.assertEqual(rubric.run_synthetic(), [])


if __name__ == "__main__":
    unittest.main()
