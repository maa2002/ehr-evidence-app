"""Checks for ehr_evidence_app.evidence.policy against the committed file.

Only the committed config/evidence_policy.json and invented byte strings
are read.
"""

import hashlib
import json
import os
import unittest

from ehr_evidence_app.evidence import policy
from ehr_evidence_app.gnn import admission


def committed():
    """Return the committed policy bytes."""
    with open(policy.CONFIG_PATH, "rb") as handle:
        return handle.read()


class PolicyTests(unittest.TestCase):
    """The committed policy mirrors the implemented values exactly."""

    def test_committed_policy_loads(self):
        """The file loads and its digest is the digest of its bytes."""
        config, digest = policy.load()
        self.assertEqual(digest, hashlib.sha256(committed()).hexdigest())
        self.assertEqual(config["policy_version"], policy.POLICY_VERSION)
        self.assertEqual(policy.CONFIG_PATH,
                         os.path.join(policy.REPO_ROOT, "config",
                                      "evidence_policy.json"))

    def test_policy_extends_the_closure_policy(self):
        """Two statements are added and one ban is narrowed; nothing else."""
        base = admission.CONFIG["claim_policy"]
        claims = policy.CONFIG["claim_policy"]
        self.assertEqual(claims["allowed"][:len(base["allowed"])],
                         list(base["allowed"]))
        self.assertEqual(claims["allowed"][len(base["allowed"]):],
                         policy.ADDED)
        self.assertEqual(len(claims["prohibited"]), len(base["prohibited"]))
        changed = [(old, new) for old, new in zip(base["prohibited"],
                                                  claims["prohibited"])
                   if old != new]
        self.assertEqual(changed, [(policy.REPLACED, policy.REPLACEMENT)])
        self.assertIn("attribution_tied_to_date_visit_or_recurrence",
                      claims["prohibited"])
        self.assertEqual(policy.CONFIG["base"]["record"],
                         admission.OUTPUT_VERSION)

    def test_changed_policy_is_refused(self):
        """Any changed value, non-JSON or non-object bytes fail closed."""
        config = json.loads(committed())
        widened = json.loads(committed())
        widened["claim_policy"]["prohibited"].pop()
        relabelled = dict(config, policy_version="evidence-policy-v2")
        for data in (json.dumps(widened).encode("utf-8"),
                     json.dumps(relabelled).encode("utf-8"),
                     b"[]", b"{", b"\xff"):
            with self.assertRaises(policy.PolicyError):
                policy.parse(data)
        extra = dict(config, note="extra keys outside the policy are kept")
        self.assertEqual(policy.parse(json.dumps(extra).encode("utf-8"))[0],
                         extra)

    def test_missing_policy_is_refused(self):
        """An unreadable policy file fails closed."""
        with self.assertRaises(policy.PolicyError):
            policy.load(os.path.join(policy.REPO_ROOT, "config",
                                     "missing-policy.json"))


if __name__ == "__main__":
    unittest.main()
