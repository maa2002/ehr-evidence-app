"""Synthetic checks for ehr_evidence_app.gnn.linkage (pure core).

The case, graph, published record, vocabulary and snapshot are the
module's invented fixtures, changed inline; torch is never imported. The
real-mode orchestration is exercised over invented scratch files by
tests/evidence/test_bundles.py.
"""

import copy
import hashlib
import io
import unittest
from contextlib import redirect_stdout

from ehr_evidence_app.gnn import binding, linkage
from ehr_evidence_app.gnn import provenance as amp


def fixture():
    """Return fresh invented (plan, case, graph, record, inverse, snapshot)."""
    return copy.deepcopy(linkage.synthetic_fixture())


def drivers(entry, domain):
    """Return the linkage entries of one domain's drivers."""
    return entry["domains"][domain]["drivers"]


class LinkCaseTests(unittest.TestCase):
    """Each driver's graph edges are linked to the events behind them."""

    def test_verified_case(self):
        """Every edge has events; visits carry positions, roles and ids."""
        plan, case, graph, record, inverse, snapshot = fixture()
        entry = linkage.link_case(case, graph, record, inverse, snapshot)
        first = drivers(entry, "condition")[0]
        self.assertEqual(first["link_status"], "verified")
        self.assertEqual([(visit["position"], visit["role"],
                           visit["event_ids"]) for visit in first["visits"]],
                         [(0, "pre_target", [1]), (2, "final_admission",
                                                   [2])])
        drug = drivers(entry, "drug")[0]
        self.assertEqual(drug["visits"][0]["event_ids"], [5, 6])
        for domain, edges in (("condition", 3), ("procedure", 1),
                              ("drug", 1)):
            block = entry["domains"][domain]
            self.assertEqual((block["edges"], block["edges_verified"],
                              block["in_vocabulary_events_without_edge"]),
                             (edges, edges, 0))
        self.assertEqual(entry["output_record_sha256"],
                         case.output_record_sha256)

    def test_partial_and_unlinked_drivers(self):
        """An edge without events makes a driver partial or unlinked."""
        plan, case, graph, record, inverse, snapshot = fixture()
        graph["drug"]["edges"].append((0, 0))
        entry = linkage.link_case(case, graph, record, inverse, snapshot)
        self.assertEqual(drivers(entry, "drug")[0]["link_status"], "partial")
        self.assertEqual((entry["domains"]["drug"]["edges"],
                          entry["domains"]["drug"]["edges_verified"]), (2, 1))
        graph["drug"]["edges"] = [(0, 0)]
        entry = linkage.link_case(case, graph, record, inverse, snapshot)
        self.assertEqual(drivers(entry, "drug")[0]["link_status"], "unlinked")
        self.assertEqual(
            entry["domains"]["drug"]["in_vocabulary_events_without_edge"], 1)

    def test_driver_without_edges_or_node(self):
        """A node without edges, or a driver outside the graph, is marked."""
        plan, case, graph, record, inverse, snapshot = fixture()
        graph["drug"]["edges"] = []
        entry = linkage.link_case(case, graph, record, inverse, snapshot)
        self.assertEqual(drivers(entry, "drug")[0]["link_status"],
                         "no_edges")
        plan, case, graph, record, inverse, snapshot = fixture()
        inverse["drug"][1] = 3002
        record["drivers"]["drug"].append({"vocab_index": 1,
                                          "concept_id": 3002})
        entry = linkage.link_case(case, graph, record, inverse, snapshot)
        outside = drivers(entry, "drug")[1]
        self.assertEqual((outside["node_found"], outside["link_status"],
                          outside["visits"]),
                         (False, "not_a_patient_node", []))

    def test_repeated_edges_collapse(self):
        """A repeated visit-concept edge counts once."""
        plan, case, graph, record, inverse, snapshot = fixture()
        graph["drug"]["edges"].append((2, 0))
        entry = linkage.link_case(case, graph, record, inverse, snapshot)
        self.assertEqual(entry["domains"]["drug"]["edges"], 1)

    def test_other_people_are_ignored(self):
        """Events of another person never link."""
        plan, case, graph, record, inverse, snapshot = fixture()
        index = linkage.event_index(snapshot, 78)
        self.assertEqual(index, {})
        index = linkage.event_index(snapshot, 77)
        self.assertEqual(index[("drug", 803, 3001)], (5, 6))

    def test_invalid_graphs_fail_closed(self):
        """Mismatched counts, edges, vocabularies and decodes are refused."""
        def changed(change):
            """Return fixture parts after one change."""
            parts = fixture()
            change(*parts)
            return parts

        cases = (
            (lambda p, c, g, r, i, s: g.update(visit_count=2),
             "graph_visit_count_mismatch"),
            (lambda p, c, g, r, i, s: g["drug"]["edges"].append((3, 0)),
             "graph_edge_invalid"),
            (lambda p, c, g, r, i, s: g["drug"]["edges"].append((0, 4)),
             "graph_edge_invalid"),
            (lambda p, c, g, r, i, s: (g["drug"]["node_vocab"].append(7),
                                       g["drug"]["edges"].append((0, 1))),
             "graph_concept_outside_vocabulary"),
            (lambda p, c, g, r, i, s: g["drug"]["node_vocab"].append(0),
             "graph_concept_node_repeated"),
            (lambda p, c, g, r, i, s: r["drivers"]["drug"][0].update(
                concept_id=3999), "driver_decode_mismatch"))
        for change, code in cases:
            plan, case, graph, record, inverse, snapshot = changed(change)
            with self.assertRaisesRegex(linkage.LinkageError, code):
                linkage.link_case(case, graph, record, inverse, snapshot)


class HelperTests(unittest.TestCase):
    """Vocabulary inversion, statuses and the record."""

    def test_inverse_vocabulary(self):
        """Indices map back to concepts; malformed tables fail."""
        self.assertEqual(linkage.inverse_vocabulary(
            {"condition": {"1001": 0, "1002": "1"}}),
            {"condition": {0: 1001, 1: 1002}, "procedure": {}, "drug": {}})
        for table in ({"drug": {"x": 0}}, {"drug": {"1": "01"}},
                      {"drug": {"1": 0, "2": 0}}):
            with self.assertRaisesRegex(linkage.LinkageError,
                                        "concept_vocab_invalid"):
                linkage.inverse_vocabulary(table)

    def test_link_status(self):
        """Statuses follow the share of visits with events."""
        linked, empty = {"event_ids": [1]}, {"event_ids": []}
        self.assertEqual(linkage.link_status([]), "no_edges")
        self.assertEqual(linkage.link_status([linked, linked]), "verified")
        self.assertEqual(linkage.link_status([linked, empty]), "partial")
        self.assertEqual(linkage.link_status([empty]), "unlinked")

    def test_record_and_summary(self):
        """The record is fingerprinted; the summary pins its bytes."""
        plan, case, graph, record, inverse, snapshot = fixture()
        entry = linkage.link_case(case, graph, record, inverse, snapshot)
        linked, summary = linkage.build_record(plan, [entry], {"x": "y"})
        body = {key: value for key, value in linked.items()
                if key != "preparation"}
        self.assertEqual(linked["preparation"]["fingerprint_sha256"],
                         amp.digest(body))
        self.assertTrue(linked["preparation"]["version"].startswith(
            linkage.VERSION_PREFIX))
        self.assertEqual(summary["linkage_file"]["sha256"],
                         hashlib.sha256(binding.encode(linked)).hexdigest())
        self.assertNotIn("cases", summary)
        self.assertEqual((linked["status"], summary["counts"]["cases"]),
                         ("verified", 1))
        graph["drug"]["edges"] = []
        partial = linkage.link_case(case, graph, record, inverse, snapshot)
        counts = linkage.aggregate([entry, partial])
        self.assertEqual(counts["domains"]["drug"]["drivers"]["no_edges"], 1)
        self.assertEqual(linkage.overall_status(counts), "partial")

    def test_synthetic_mode(self):
        """The default mode verifies, and its broken control is partial."""
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(linkage.main([]), 0)
        text = output.getvalue()
        self.assertIn("Status             : verified", text)
        self.assertIn("broken control     : partial", text)


if __name__ == "__main__":
    unittest.main()
