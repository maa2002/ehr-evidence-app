"""Synthetic checks for ehr_evidence_app.qa.resolve.

The vocabulary is invented inline and encoded with the builder's own file
format; loading from files uses the configured workspace test temp
directory only. The real vocabulary is never read.
"""

import hashlib
import json
import os
import shutil
import tempfile
import unittest

from ehr_evidence_app.qa import resolve
from ehr_evidence_app.qa import vocabulary as build
from ehr_evidence_app.storage import policy as guard

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
STANDARD = {1: "Clinical finding", 2: "Disease", 3: "Heart failure",
            4: "Congestive heart failure",
            5: "Crimean-Congo hemorrhagic fever",
            6: "Urinary tract infectious disease",
            7: "Lower urinary tract infectious disease",
            8: "Diabetes mellitus", 9: "Type 2 diabetes mellitus",
            10: "Hypertensive heart disease", 11: "Pneumonia",
            12: "Sjögren's syndrome"}
PARENTS = {2: 1, 3: 2, 4: 3, 5: 2, 6: 2, 7: 6, 8: 2, 9: 8, 10: 2, 11: 2,
           12: 2}
ICD = {100: ("ICD10CM", "I50.9", "Heart failure, unspecified"),
       101: ("ICD10CM", "Z87.891", "Personal history of nicotine dependence"),
       102: ("ICD9CM", "V45.81", "Aortocoronary bypass status"),
       103: ("ICD10CM", "I11.9",
             "Hypertensive heart disease without heart failure"),
       104: ("ICD10CM", "I11.0",
             "Hypertensive heart disease with heart failure"),
       105: ("ICD9CM", "250.00",
             "Diabetes mellitus without mention of complication")}
SYNONYMS = [(3, "Cardiac failure"), (4, "CHF - Congestive heart failure"),
            (5, "CHF - Crimean-Congo haemorrhagic fever"),
            (6, "UTI - Urinary tract infection"),
            (7, "UTI - Lower urinary tract infection"),
            (9, "T2DM - Type 2 diabetes mellitus"),
            (11, "Lung - inflammation")]


def ancestors_of(concept):
    """Return a concept and all its invented ancestors."""
    found = [concept]
    while found[-1] in PARENTS:
        found.append(PARENTS[found[-1]])
    return found


def files():
    """Return the invented vocabulary encoded as the builder's files."""
    concepts = [(concept, "standard", "SNOMED", "Disorder", "S%d" % concept,
                 "", name) for concept, name in STANDARD.items()]
    concepts += [(concept, "icd", vocabulary, "code", code, "", name)
                 for concept, (vocabulary, code, name) in ICD.items()]
    pairs = [(ancestor, concept, level) for concept in STANDARD
             for level, ancestor in enumerate(ancestors_of(concept))]
    return {build.FILES["concepts"]: build.encode_tsv("concepts", concepts),
            build.FILES["synonyms"]: build.encode_tsv("synonyms", SYNONYMS),
            build.FILES["ancestors"]: build.encode_tsv("ancestors", pairs),
            build.FILES["icd_maps"]: build.encode_tsv("icd_maps",
                                                      [(100, 3)])}


VOCAB = resolve.parse(files())


def configured_test_temp():
    """Return the configured workspace test scratch directory."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        paths = json.load(handle)
    relative = paths["directories"]["test_scratch"]["path"]
    return os.path.normpath(os.path.join(paths["workspace_root"], relative))


class NormalisationTests(unittest.TestCase):
    """Phrases and names normalise the same way."""

    def test_normalize(self):
        """Case, accents, possessives, '&' and punctuation."""
        self.assertEqual(resolve.normalize("  Sjögren's  Syndrome "),
                         "sjogren syndrome")
        self.assertEqual(resolve.normalize("Heart-failure & COPD!"),
                         "heart failure and copd")
        self.assertEqual(resolve.normalize("Sjögren’s syndrome"),
                         "sjogren syndrome")
        self.assertEqual(resolve.normalize(None), "")

    def test_singulars(self):
        """Plural last words get singular forms; others none."""
        self.assertEqual(resolve.singulars("pneumonias"), ["pneumonia"])
        self.assertEqual(resolve.singulars("kidney stones"),
                         ["kidney ston", "kidney stone"])
        self.assertEqual(resolve.singulars("allergies"),
                         ["allergy", "allergi", "allergie"])
        for phrase in ("abscess", "gas", "", "sepsi"):
            self.assertEqual(resolve.singulars(phrase), [])

    def test_codes(self):
        """ICD-9 and ICD-10 forms with or without dots."""
        for text in ("I50.9", "i509", "428.0", "V45.81", "E11.9", "Z87.891",
                     "E888.9"):
            self.assertTrue(resolve.is_code(text), text)
        for text in ("heart", "I5", "12", "ABC.1"):
            self.assertFalse(resolve.is_code(text), text)
        self.assertEqual(resolve.code_key(" i50.9 "), "I509")


class ResolutionTests(unittest.TestCase):
    """The fixed rules, applied in order."""

    def check(self, text, status, rule=None, concepts=None, codes=None):
        """Assert one resolution."""
        result = resolve.resolve(text, VOCAB)
        self.assertEqual(result.status, status, text)
        if rule is not None:
            self.assertEqual(result.rule, rule, text)
        if concepts is not None:
            self.assertEqual(result.concepts, concepts, text)
        if codes is not None:
            self.assertEqual(result.codes, codes, text)
        return result

    def test_names_synonyms_and_acronyms(self):
        """Names first, then synonyms; acronyms resolve when unique."""
        self.check("Heart Failure", "resolved", "name", (3,))
        self.check("cardiac failure", "resolved", "synonym", (3,))
        self.check("T2DM", "resolved", "synonym", (9,))
        self.check("sjogren's syndrome", "resolved", "name", (12,))
        self.check("pneumonias", "resolved", "name", (11,))
        self.check("lung inflammation", "resolved", "synonym", (11,))
        self.check("Lung", "unknown")

    def test_ambiguity_and_nesting(self):
        """Unrelated candidates are ambiguous; nested ones merge."""
        self.check("CHF", "ambiguous", "synonym", (4, 5))
        self.check("UTI", "resolved", "synonym", (6,))

    def test_breadth(self):
        """A concept covering over a quarter of conditions is too broad."""
        self.check("disease", "too_broad", "name", (2,))
        self.check("clinical finding", "too_broad", "name", (1,))

    def test_unknown_terms_get_suggestions(self):
        """Suggestions begin with the phrase first; never too broad."""
        result = self.check("diabetes", "unknown")
        self.assertEqual(result.concepts[0], 8)
        self.assertIn(9, result.concepts)
        self.check("zzqx", "unknown", concepts=())
        self.check("   ", "unknown", concepts=())

    def test_spelling_suggestions(self):
        """A misspelt word is suggested its closest spellings, never
        resolved."""
        for text, concept in (("pnuemonia", 11), ("penumonia", 11),
                              ("congestive heart failur", 4),
                              ("hart failure", 3),
                              ("diabetis mellitus", 8)):
            result = self.check(text, "unknown")
            self.assertEqual(result.concepts[0], concept, text)
        self.check("pneumonias", "resolved", "name", (11,))
        self.assertEqual(resolve.spellings(VOCAB, "heart failure"), [])
        self.assertEqual(resolve.edit_distance("heart", "hart", 1), 1)
        self.assertEqual(resolve.edit_distance("heart", "hurts", 1), 2)
        self.assertEqual(resolve.edit_distance("a", "abcd", 1), 2)

    def test_icd_codes(self):
        """Known codes resolve as codes; unknown codes fall through."""
        self.check("I50.9", "resolved", "icd_code", (), ("I509",))
        self.check("i50", "unknown")
        self.check("Q99.9", "unknown")


class MatchTests(unittest.TestCase):
    """How a record matches a resolved phrase."""

    def test_standard_concepts(self):
        """Exact, more specific, unrelated and unresolved."""
        failure = resolve.resolve("heart failure", VOCAB)
        self.assertEqual(resolve.match(failure, VOCAB, 3), "exact")
        self.assertEqual(resolve.match(failure, VOCAB, 4), "more_specific")
        self.assertIsNone(resolve.match(failure, VOCAB, 10))
        chf = resolve.resolve("CHF", VOCAB)
        self.assertIsNone(resolve.match(chf, VOCAB, 4))

    def test_icd_codes(self):
        """Code equality, then longer codes that start with it."""
        exact = resolve.resolve("I50.9", VOCAB)
        self.assertEqual(resolve.match(exact, VOCAB, 3, "I509"), "icd_code")
        self.assertIsNone(resolve.match(exact, VOCAB, 3, "I110"))
        broader = resolve.Resolution("i50", "resolved", "icd_code",
                                     codes=("I50",))
        self.assertEqual(resolve.match(broader, VOCAB, 3, "I509"),
                         "more_specific_icd_code")

    def test_icd_descriptions_of_uncoded_records(self):
        """Whole phrases match unless negated."""
        failure = resolve.resolve("heart failure", VOCAB)
        self.assertEqual(resolve.match(failure, VOCAB, 0, "I110", 104),
                         "icd_description")
        self.assertIsNone(resolve.match(failure, VOCAB, 0, "I119", 103))
        diabetes = resolve.resolve("diabetes mellitus", VOCAB)
        self.assertEqual(resolve.match(diabetes, VOCAB, 0, "25000", 105),
                         "icd_description")
        congestive = resolve.resolve("congestive heart failure", VOCAB)
        self.assertIsNone(resolve.match(congestive, VOCAB, 0, "I110", 104))
        self.assertIsNone(resolve.match(failure, VOCAB, 0, "X", 999))
        self.assertIsNone(resolve.match(failure, VOCAB, 0, "I509", 3))

    def test_negation_forms(self):
        """Each negation form suppresses the phrase after it."""
        phrases = {("heart", "failure")}
        for text in ("no heart failure", "not heart failure",
                     "except heart failure", "w/o heart failure",
                     "without mention of heart failure"):
            self.assertFalse(resolve.contains_phrase(text, phrases), text)
        self.assertTrue(resolve.contains_phrase(
            "Heart failure, not elsewhere classified", phrases))


class LoadTests(unittest.TestCase):
    """The vocabulary loads only through its pinned manifest."""

    def setUp(self):
        """Write the invented vocabulary and a manifest to scratch."""
        root = configured_test_temp()
        if not os.path.isdir(root):
            self.skipTest("configured test temp is missing; run "
                          "scripts/setup-destinations.ps1 first")
        self.temp_root = root
        self.work = tempfile.mkdtemp(prefix="qa-resolve-", dir=root)
        self.addCleanup(self.remove_scratch)
        data = files()
        for name, content in data.items():
            with open(os.path.join(self.work, name), "wb") as handle:
                handle.write(content)
        manifest = {"schema": build.SCHEMA,
                    "files": {name: {"size_bytes": len(content),
                                     "sha256": hashlib.sha256(
                                         content).hexdigest()}
                              for name, content in data.items()}}
        content = json.dumps(manifest).encode("utf-8")
        path = os.path.join(self.work, build.MANIFEST_NAME)
        with open(path, "wb") as handle:
            handle.write(content)
        self.plan = {"directory": self.work,
                     "manifest": {"path": path, "size_bytes": len(content),
                                  "sha256": hashlib.sha256(
                                      content).hexdigest()}}

    def remove_scratch(self):
        """Delete the scratch tree only if it is strictly under the root."""
        if guard.is_within(self.work, self.temp_root, strict=True):
            shutil.rmtree(self.work, ignore_errors=True)

    def test_load_and_tampering(self):
        """A pinned load works; a changed file or manifest fails."""
        vocab = resolve.load(self.plan)
        self.assertEqual(vocab.standard_count, len(STANDARD))
        self.assertEqual(vocab.digest, self.plan["manifest"]["sha256"])
        with open(os.path.join(self.work, build.FILES["synonyms"]),
                  "ab") as handle:
            handle.write(b"99\tExtra\n")
        with self.assertRaisesRegex(resolve.ResolutionError,
                                    "input_hash_mismatch:synonyms.tsv"):
            resolve.load(self.plan)
        self.plan["manifest"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(resolve.ResolutionError,
                                    "vocabulary_manifest"):
            resolve.load(self.plan)

    def test_real_plan_is_pinned(self):
        """The real plan pins the published manifest."""
        plan = resolve.real_plan()
        self.assertEqual(plan["manifest"]["sha256"], resolve.MANIFEST_SHA256)
        self.assertTrue(plan["manifest"]["path"].endswith("manifest.json"))


if __name__ == "__main__":
    unittest.main()
