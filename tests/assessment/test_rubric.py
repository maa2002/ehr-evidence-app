"""Synthetic checks for ehr_evidence_app.assessment.rubric.

No rater runs: a stand-in rater and a stub server stand for it, and the
texts are invented.
"""

import io
import json
import unittest

from ehr_evidence_app.assessment import references, report, rubric
from ehr_evidence_app.generation import shape
from ehr_evidence_app.validation import language


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
        its paraphrase does not; the controls break none."""
        for kind, outcome in (("explanation", "explanation"),
                              ("answer", "answer")):
            for name, (named, paraphrase) in rubric.FAULTS[kind].items():
                with self.subTest(kind=kind, fault=name):
                    self.assertTrue(language.findings(named, outcome))
                    self.assertEqual(language.findings(paraphrase, outcome),
                                     [])
            self.assertEqual(language.findings(rubric.CONTROLS[kind],
                                               outcome), [])

    def test_planted_variants(self):
        """A text gives its clean form, a control and both forms of every
        class, each with its expected mark."""
        found = rubric.planted("Diabetes: 2 records.", "answer")
        self.assertEqual(len(found), 2 + 2 * len(rubric.FAULTS["answer"]))
        self.assertEqual([item[2] for item in found[:2]], ["pass", "pass"])
        self.assertEqual({item[2] for item in found[2:]}, {"fail"})

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
        self.assertEqual(len(described["criteria_sha256"]), 64)

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

    def test_default_mode_passes(self):
        """The module's own check finds nothing."""
        self.assertEqual(rubric.run_synthetic(), [])


if __name__ == "__main__":
    unittest.main()
