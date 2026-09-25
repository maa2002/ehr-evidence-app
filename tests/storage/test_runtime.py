"""Synthetic checks for ehr_evidence_app.storage.runtime (storage placement).

Volume, disk, encryption and ACL facts are invented; no PowerShell query,
icacls call or permission change is made. Directory-creation tests run only
beneath a scratch directory under the configured test temp on the
runtime root, with icacls and ACL reads replaced by stubs. Windows only.
"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

from ehr_evidence_app.storage import runtime as storage  # noqa: E402
from ehr_evidence_app.storage import policy as guard  # noqa: E402

PATHS_PATH = os.path.join(REPO_ROOT, "config", "paths.json")
USER = "S-1-5-21-1-2-3-1001"
OTHER = "S-1-5-21-1-2-3-1002"
VOLUME_ID = "\\\\?\\Volume{00000000-0000-0000-0000-000000000001}\\"
PARTITION_GUID = "{00000000-0000-0000-0000-000000000002}"
COMPLETE_BITLOCKER = {"available": True, "protection_status": "On",
                      "volume_status": "FullyEncrypted",
                      "encryption_percentage": 100,
                      "encryption_method": "XtsAes256"}


def good_facts(**overrides):
    """Return passing synthetic volume facts with optional overrides."""
    facts = {"file_system": "NTFS", "drive_type": "Fixed",
             "volume_health": "Healthy", "volume_operational": "OK",
             "size_bytes": 10 ** 12, "free_bytes": 5 * 10 ** 11,
             "volume_id": VOLUME_ID, "partition_guid": PARTITION_GUID,
             "disk_model": storage.EXPECTED_DISK_MODEL, "bus_type": "NVMe",
             "disk_health": "Healthy", "disk_operational": "Online",
             "disk_is_boot": False, "disk_is_system": False,
             "media_type": "SSD",
             "bitlocker": {"available": False, "protection_status": None,
                           "volume_status": None,
                           "encryption_percentage": None,
                           "encryption_method": None}}
    facts.update(overrides)
    return facts


def pin(facts=None):
    """Return the identity digest of the (default) synthetic facts."""
    return storage.identity_digest(good_facts() if facts is None else facts)


def rule(sid, rights=storage.FULL_CONTROL, **overrides):
    """Return one synthetic ACL rule."""
    entry = {"sid": sid, "rights": rights, "type": "Allow",
             "inherited": False, "inheritance": storage.INHERIT_BOTH,
             "propagation": "None"}
    entry.update(overrides)
    return entry


def good_acl(**overrides):
    """Return the expected restrictive ACL with optional overrides."""
    acl = {"protected": True, "owner": USER,
           "rules": [rule(storage.SYSTEM_SID),
                     rule(storage.ADMINISTRATORS_SID), rule(USER)]}
    acl.update(overrides)
    return acl


def failed(checks):
    """Return the names of failing (name, passed, detail) checks."""
    return {name for name, passed, _ in checks if not passed}


def runtime_scratch(test, prefix):
    """Create a scratch directory under the configured runtime test temp."""
    with open(PATHS_PATH, "r", encoding="utf-8") as handle:
        entry = json.load(handle)["directories"]["pytest_temp"]
    base = guard.configured_directory_path("pytest_temp", entry)[0]
    if not os.path.isdir(base):
        test.skipTest("runtime test temp is missing; run "
                      "scripts/setup-destinations.ps1 first")
    scratch = tempfile.mkdtemp(prefix=prefix, dir=base)
    test.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
    return scratch


class VolumeTests(unittest.TestCase):
    """Identity, file-system, health and space checks."""

    def test_expected_volume_passes(self):
        """The exact expected fixed NTFS NVMe SSD passes every check."""
        self.assertEqual(failed(storage.volume_checks(good_facts())), set())
        self.assertEqual(failed(storage.volume_checks(
            good_facts(), pin())), set())

    def test_each_deviation_fails_its_check(self):
        """Wrong identity, format, health, space or volume id fails."""
        for overrides, expected in (
                ({"disk_model": "USB Flash"}, "disk-model"),
                ({"bus_type": "USB"}, "bus-type"),
                ({"media_type": "HDD"}, "media-type"),
                ({"drive_type": "Removable"}, "drive-type"),
                ({"file_system": "exFAT"}, "file-system"),
                ({"volume_health": "Warning"}, "volume-health"),
                ({"volume_operational": "Degraded"}, "volume-health"),
                ({"disk_health": "Unhealthy"}, "disk-health"),
                ({"disk_operational": "Offline"}, "disk-health"),
                ({"free_bytes": storage.MIN_FREE_BYTES - 1}, "free-space"),
                ({"free_bytes": None}, "free-space"),
                ({"free_bytes": True}, "free-space"),
                ({"volume_id": ""}, "volume-identity")):
            with self.subTest(overrides=overrides):
                self.assertEqual(failed(storage.volume_checks(
                    good_facts(**overrides))), {expected})

    def test_pinned_identity_digest_must_match(self):
        """Another volume, partition or size than the pin fails identity."""
        for overrides in ({"volume_id": "\\\\?\\Volume{other}\\"},
                          {"partition_guid": "{other}"},
                          {"size_bytes": 10 ** 12 + 1}, {"volume_id": ""}):
            with self.subTest(overrides=overrides):
                checks = storage.volume_checks(good_facts(**overrides),
                                               pin())
                self.assertEqual(failed(checks), {"volume-identity"})
                self.assertNotIn(VOLUME_ID, json.dumps(checks))
                self.assertNotIn(PARTITION_GUID, json.dumps(checks))


class IdentityDigestTests(unittest.TestCase):
    """Versioned, deterministic identity digests and pin loading."""

    def test_digest_is_versioned_deterministic_and_canonical(self):
        """The digest is v1 SHA-256 over the fixed canonical text."""
        text = "\n".join([
            "ehr-runtime-volume-identity:v1",
            "disk_model=%s" % storage.EXPECTED_DISK_MODEL, "bus_type=NVMe",
            "size_bytes=%d" % 10 ** 12, "partition_guid=%s" % PARTITION_GUID,
            "volume_id=%s" % VOLUME_ID])
        expected = "v1:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.assertEqual(storage.identity_digest(good_facts()), expected)
        self.assertEqual(storage.identity_digest(good_facts()),
                         storage.identity_digest(dict(good_facts())))
        # Health, space and encryption are not part of the identity.
        self.assertEqual(storage.identity_digest(good_facts(
            free_bytes=1, volume_health="Warning",
            bitlocker=COMPLETE_BITLOCKER)), expected)

    def test_each_identity_field_changes_the_digest(self):
        """Every canonical field is bound; missing values give None."""
        for name, value in (("disk_model", "other"), ("bus_type", "SATA"),
                            ("size_bytes", 1), ("partition_guid", "{x}"),
                            ("volume_id", "\\\\?\\Volume{x}\\")):
            self.assertNotEqual(storage.identity_digest(
                good_facts(**{name: value})), pin(), name)
            for missing in (None, "", True):
                self.assertIsNone(storage.identity_digest(
                    good_facts(**{name: missing})), (name, missing))

    def test_pin_is_read_from_new_and_legacy_records(self):
        """A digest record, a legacy raw record and bad records are read."""
        scratch = runtime_scratch(self, "identity-")
        path = os.path.join(scratch, "record.json")
        for record, expected in (
                ({"identity_digest": pin()}, pin()),
                ({"volume": good_facts()}, pin()),
                ({"identity_digest": "v2:abc", "volume": good_facts()},
                 pin()),
                ({"identity_digest": "v2:abc"}, None),
                ({"volume": {"disk_model": "x"}}, None),
                ([], None)):
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(record, handle)
            self.assertEqual(storage.read_pinned_digest(path), expected,
                             record)
        self.assertIsNone(storage.read_pinned_digest(
            os.path.join(scratch, "absent.json")))


class EncryptionTests(unittest.TestCase):
    """Only complete Get-BitLockerVolume evidence counts as encrypted."""

    def test_complete_encryption_requires_all_four_fields(self):
        """On, FullyEncrypted, 100 percent and a known method."""
        self.assertEqual(storage.encryption_status(
            good_facts(bitlocker=COMPLETE_BITLOCKER)), "complete")
        for method in sorted(storage.KNOWN_ENCRYPTION_METHODS):
            self.assertEqual(storage.encryption_status(good_facts(
                bitlocker=dict(COMPLETE_BITLOCKER,
                               encryption_method=method))), "complete")
        for change in ({"protection_status": "Off"},
                       {"protection_status": "Unknown"},
                       {"volume_status": "EncryptionInProgress"},
                       {"volume_status": "FullyDecrypted"},
                       {"encryption_percentage": 99.9},
                       {"encryption_percentage": None},
                       {"encryption_percentage": True},
                       {"encryption_percentage": "100"},
                       {"encryption_method": "None"},
                       {"encryption_method": ""},
                       {"encryption_method": None},
                       {"encryption_method": "Unknown"}):
            with self.subTest(change=change):
                self.assertEqual(storage.encryption_status(good_facts(
                    bitlocker=dict(COMPLETE_BITLOCKER, **change))),
                    "incomplete")

    def test_unavailable_or_shell_only_evidence_is_unknown(self):
        """No cmdlet evidence, or a Shell value alone, is never complete."""
        for facts in (good_facts(),
                      good_facts(bitlocker=dict(COMPLETE_BITLOCKER,
                                                available=False)),
                      good_facts(bitlocker=None),
                      good_facts(bitlocker_shell=1),
                      good_facts(bitlocker_cmdlet="On", bitlocker_shell=1)):
            self.assertEqual(storage.encryption_status(facts), "unknown")

    def test_reduced_status_keeps_only_evaluated_fields(self):
        """Only the four evaluated fields and availability are captured."""
        reduced = storage.reduced_bitlocker(good_facts(bitlocker=dict(
            COMPLETE_BITLOCKER, recovery_password="synthetic",
            key_protector=["synthetic"])))
        self.assertEqual(reduced, COMPLETE_BITLOCKER)


class AclTests(unittest.TestCase):
    """Restrictive DACL evaluation and sanitised summaries."""

    def test_expected_acl_passes(self):
        """Protected DACL for SYSTEM, Administrators and the user passes."""
        self.assertEqual(storage.acl_problems(good_acl(), USER), [])
        self.assertEqual(storage.acl_problems(
            good_acl(owner=storage.ADMINISTRATORS_SID), USER), [])

    def test_weaker_acls_fail(self):
        """Inheritance, other principals, deny and partial grants fail."""
        system, admins = storage.SYSTEM_SID, storage.ADMINISTRATORS_SID
        for acl, expected in (
                (good_acl(protected=False), "inheritance is not disabled"),
                (good_acl(owner=OTHER), "owner"),
                (good_acl(rules=good_acl()["rules"] + [rule("S-1-5-11")]),
                 "another principal"),
                (good_acl(rules=good_acl()["rules"] + [rule("S-1-1-0")]),
                 "another principal"),
                (good_acl(rules=good_acl()["rules"] + [rule(OTHER)]),
                 "another principal"),
                (good_acl(rules=good_acl()["rules"]
                          + [rule(USER, type="Deny")]), "non-allow"),
                (good_acl(rules=good_acl()["rules"]
                          + [rule(USER, inherited=True)]), "inherited rule"),
                (good_acl(rules=[rule(system), rule(admins)]),
                 "the current user lacks"),
                (good_acl(rules=[rule(system), rule(admins),
                                 rule(USER, rights=0x1301BF)]),
                 "the current user lacks"),
                (good_acl(rules=[rule(system), rule(USER),
                                 rule(admins, inheritance="None")]),
                 "Administrators lacks"),
                (good_acl(rules=[rule(admins), rule(USER)]),
                 "SYSTEM lacks")):
            problems = storage.acl_problems(acl, USER)
            self.assertTrue(any(expected in problem for problem in problems),
                            (expected, problems))

    def test_summary_names_no_account_or_sid(self):
        """The recorded summary uses principal labels, never SIDs."""
        summary = storage.acl_summary(
            good_acl(rules=good_acl()["rules"] + [rule(OTHER)]), USER)
        text = json.dumps(summary)
        self.assertNotIn("S-1-", text)
        self.assertEqual(summary["owner"], "current-user")
        self.assertIn("other:Allow:FullControl:ContainerInherit, "
                      "ObjectInherit", summary["rules"])

    def test_restrict_command_removes_inheritance_and_grants_three(self):
        """icacls removes inheritance and grants only the three SIDs."""
        command = storage.restrict_command("G:\\synthetic", USER)
        self.assertEqual(command[:3], ["icacls", "G:\\synthetic",
                                       "/inheritance:r"])
        grants = [command[index + 1] for index, part in enumerate(command)
                  if part == "/grant:r"]
        self.assertEqual(grants, ["*%s:(OI)(CI)F" % storage.SYSTEM_SID,
                                  "*%s:(OI)(CI)F" % storage.ADMINISTRATORS_SID,
                                  "*%s:(OI)(CI)F" % USER])


class GateTests(unittest.TestCase):
    """Runtime and protected-data gate outcomes and the record."""

    def evaluate(self, facts, acls=None, extra=(), pinned=None):
        """Evaluate synthetic facts with passing ACLs by default."""
        if acls is None:
            acls = [("runtime-parent", good_acl()),
                    ("runtime-root", good_acl())]
        return storage.evaluate(facts, storage.acl_checks(acls, USER),
                                pinned, list(extra))

    def test_encryption_is_reported_but_not_required(self):
        """Unknown encryption is recorded as information; gates still pass."""
        checks, runtime, protected, encryption = self.evaluate(good_facts())
        self.assertTrue(runtime)
        self.assertTrue(protected)
        self.assertEqual(encryption, "unknown")
        self.assertEqual([c["name"] for c in checks
                          if c["status"] != "pass"], ["encryption"])
        self.assertEqual([c["status"] for c in checks
                          if c["name"] == "encryption"], ["info"])

    def test_protected_gate_follows_the_runtime_gate(self):
        """Complete, partial or missing encryption never changes the gates."""
        for facts in (good_facts(bitlocker=COMPLETE_BITLOCKER),
                      good_facts(bitlocker_shell=1),
                      good_facts(bitlocker=dict(
                          COMPLETE_BITLOCKER, encryption_percentage=50,
                          volume_status="EncryptionInProgress"))):
            _, runtime, protected, _ = self.evaluate(facts)
            self.assertTrue(runtime)
            self.assertEqual(protected, runtime)

    def test_any_runtime_failure_closes_both_gates(self):
        """Missing root, weak ACL, drift or a failed path check fails."""
        complete = good_facts(bitlocker=COMPLETE_BITLOCKER)
        for facts, acls, extra, pinned in (
                (complete, [("runtime-parent", good_acl()),
                            ("runtime-root", None)], (), None),
                (complete, [("runtime-parent", good_acl(protected=False)),
                            ("runtime-root", good_acl())], (), None),
                (dict(complete, file_system="exFAT"), None, (), None),
                (complete, None, [("path-no-reparse", False, "junction")],
                 None),
                (dict(complete, volume_id="\\\\?\\Volume{x}\\"), None, (),
                 pin())):
            _, runtime, protected, _ = self.evaluate(facts, acls, extra,
                                                     pinned)
            self.assertFalse(runtime)
            self.assertFalse(protected)

    def test_record_holds_digest_but_no_raw_identifier(self):
        """The record stores the digest and reduced BitLocker status only."""
        facts = good_facts(bitlocker=dict(COMPLETE_BITLOCKER,
                                          recovery_password="synthetic"))
        acls = [("runtime-parent", good_acl()), ("runtime-root", good_acl())]
        checks, runtime, protected, encryption = self.evaluate(facts, acls,
                                                               (), pin())
        record = storage.build_record(facts, acls, USER, checks, runtime,
                                      protected, encryption, [], "stamp")
        text = json.dumps(record)
        self.assertEqual(record["identity_digest"], pin())
        for raw in (VOLUME_ID, PARTITION_GUID, "00000000-0000", "volume_id",
                    "partition_guid", "S-1-5", "synthetic", "serial"):
            self.assertNotIn(raw, text)
        self.assertEqual(record["bitlocker"], COMPLETE_BITLOCKER)
        self.assertEqual(record["protected_data_gate"], "PASS")


class RuntimeGateTests(unittest.TestCase):
    """The read-only gate used before setup/verifier writes."""

    def run_gate(self, facts=None, pinned="default", acls=None):
        """Run runtime_gate_checks with every query replaced by stubs."""
        facts = good_facts() if facts is None else facts
        pinned = pin() if pinned == "default" else pinned
        acls = ([("runtime-parent", good_acl()),
                 ("runtime-root", good_acl())] if acls is None else acls)
        with mock.patch.object(storage, "collect_volume_facts",
                               return_value=facts), \
                mock.patch.object(storage, "read_pinned_digest",
                                  return_value=pinned), \
                mock.patch.object(storage, "current_user_sid",
                                  return_value=USER), \
                mock.patch.object(storage, "collect_runtime_acls",
                                  return_value=acls), \
                mock.patch.object(storage, "path_checks", return_value=[
                    ("path-no-reparse", True, "synthetic")]):
            return storage.runtime_gate_checks()

    def test_current_matching_state_passes(self):
        """A pinned, healthy, restrictive runtime root passes."""
        self.assertEqual(failed(self.run_gate()), set())

    def test_drift_fails_the_gate(self):
        """Identity, pin, health and ACL drift each fail."""
        for kwargs, expected in (
                ({"facts": good_facts(volume_id="\\\\?\\Volume{x}\\")},
                 {"volume-identity"}),
                ({"pinned": None}, {"volume-pin"}),
                ({"facts": good_facts(disk_health="Warning")},
                 {"disk-health"}),
                ({"acls": [("runtime-parent", good_acl()),
                           ("runtime-root", good_acl(
                               rules=good_acl()["rules"]
                               + [rule("S-1-5-11")]))]},
                 {"acl:runtime-root"}),
                ({"acls": [("runtime-parent", good_acl(protected=False)),
                           ("runtime-root", good_acl())]},
                 {"acl:runtime-parent"}),
                ({"acls": [("runtime-parent", good_acl()),
                           ("runtime-root", None)]}, {"acl:runtime-root"})):
            with self.subTest(expected=expected):
                checks = self.run_gate(**kwargs)
                self.assertEqual(failed(checks), expected)
                self.assertNotIn(VOLUME_ID, json.dumps(checks))


class CreateRootTests(unittest.TestCase):
    """Directory creation and rollback beneath a runtime scratch directory."""

    def setUp(self):
        """Create a scratch directory under the runtime test temp."""
        self.scratch = runtime_scratch(self, "runtime-storage-")
        self.parent = os.path.join(self.scratch, "parent")
        self.root = os.path.join(self.parent, "root")
        self.calls = []

    def fake_run(self, returncode=0, write=None):
        """Return an icacls stub that records calls and may write a file."""

        def run(command, **kwargs):
            """Record the command, optionally write, return the code."""
            self.calls.append(command[1])
            if write is not None and command[1] == write:
                with open(os.path.join(write, "occupant.txt"), "w",
                          encoding="utf-8") as handle:
                    handle.write("synthetic\n")
            return SimpleNamespace(returncode=returncode)

        return run

    def create(self, run, acl_problems_of=lambda path: []):
        """Run create_root on the scratch parent and root."""
        return storage.create_root(USER, run=run,
                                   paths=(self.parent, self.root),
                                   acl_problems_of=acl_problems_of)

    def test_missing_components_are_created_and_restricted(self):
        """Each missing level is created, restricted and verified once."""
        self.assertEqual(self.create(self.fake_run()),
                         [self.parent, self.root])
        self.assertEqual(self.calls, [self.parent, self.root])
        self.assertTrue(os.path.isdir(self.root))
        self.assertEqual(self.create(self.fake_run()), [])
        self.assertEqual(len(self.calls), 2)

    def test_icacls_failure_removes_every_created_level(self):
        """A failed restriction leaves no newly created path behind."""
        with self.assertRaises(storage.PreflightError):
            self.create(self.fake_run(returncode=5))
        self.assertFalse(os.path.lexists(self.parent))
        self.assertEqual(os.listdir(self.scratch), [])

    def test_acl_verification_failure_removes_created_levels(self):
        """A weak ACL after icacls removes the new levels, newest first."""
        def weak_root(path):
            """Report a weak ACL for the new root only."""
            return ["inheritance is not disabled"] if path == self.root \
                else []

        with self.assertRaises(storage.PreflightError) as caught:
            self.create(self.fake_run(), acl_problems_of=weak_root)
        self.assertIn("ACL verification failed", str(caught.exception))
        self.assertFalse(os.path.lexists(self.parent))

    def test_pre_existing_parent_is_kept_on_failure(self):
        """Only the level created by the call is removed."""
        os.mkdir(self.parent)
        with open(os.path.join(self.parent, "prior.txt"), "w",
                  encoding="utf-8") as handle:
            handle.write("prior\n")
        with self.assertRaises(storage.PreflightError):
            self.create(self.fake_run(returncode=5))
        self.assertTrue(os.path.isdir(self.parent))
        self.assertEqual(os.listdir(self.parent), ["prior.txt"])

    def test_weak_pre_existing_parent_blocks_child_creation(self):
        """A pre-existing parent with a weak ACL is refused unchanged."""
        os.mkdir(self.parent)
        with self.assertRaises(storage.PreflightError) as caught:
            self.create(self.fake_run(), acl_problems_of=lambda path: [
                "a rule grants access to another principal"])
        self.assertIn("weak ACL", str(caught.exception))
        self.assertEqual(self.calls, [])
        self.assertTrue(os.path.isdir(self.parent))
        self.assertFalse(os.path.lexists(self.root))

    def test_non_empty_created_level_is_never_removed(self):
        """A created level that gained content is reported, not removed."""
        with self.assertRaises(storage.PreflightError) as caught:
            self.create(self.fake_run(returncode=5, write=self.parent))
        self.assertIn("left in place", str(caught.exception))
        self.assertTrue(os.path.isfile(os.path.join(self.parent,
                                                    "occupant.txt")))

    def test_existing_file_component_is_refused(self):
        """An existing non-directory component is refused, not replaced."""
        with open(self.parent, "w", encoding="utf-8") as handle:
            handle.write("synthetic\n")
        with self.assertRaises(storage.PreflightError):
            self.create(mock.Mock())
        self.assertTrue(os.path.isfile(self.parent))

    def test_cleanup_refuses_unapproved_and_linked_paths(self):
        """Only exact approved, plain, empty directories are removed."""
        other = os.path.join(self.scratch, "other")
        os.mkdir(other)
        self.assertEqual(storage.remove_created([other], (self.parent,)),
                         [other])
        self.assertTrue(os.path.isdir(other))
        link = os.path.join(self.scratch, "junction")
        try:
            import _winapi
            _winapi.CreateJunction(other, link)
        except (ImportError, AttributeError, OSError) as error:
            self.skipTest("junctions are unavailable: %s"
                          % type(error).__name__)
        self.addCleanup(lambda: os.path.lexists(link) and os.rmdir(link))
        self.assertEqual(storage.remove_created([link], (link,)), [link])
        self.assertTrue(os.path.lexists(link))
        self.assertTrue(os.path.isdir(other))


class RunTests(unittest.TestCase):
    """The full preflight with every query mocked; nothing is created."""

    def test_create_root_refused_when_prerequisites_fail(self):
        """A failed identity check stops before any directory is created."""
        patches = (
            mock.patch.object(storage, "collect_volume_facts",
                              return_value=good_facts(file_system="exFAT")),
            mock.patch.object(storage, "read_pinned_digest",
                              return_value=None),
            mock.patch.object(storage, "current_user_sid", return_value=USER),
            mock.patch.object(storage, "create_root"),
            mock.patch("builtins.print"))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.assertEqual(storage.main(["--create-root", "--no-record"]), 1)
        storage.create_root.assert_not_called()

    def test_parser_has_no_policy_override(self):
        """Only --create-root and --no-record are offered."""
        options = {option for action in storage.build_parser()._actions
                   for option in action.option_strings}
        self.assertEqual(options, {"-h", "--help", "--create-root",
                                   "--no-record"})

    def test_placement_manifest_separates_the_tiers(self):
        """Runtime keys sit in the runtime root; the rest in the workspace."""
        placement = storage.placement_manifest()
        runtime = {entry["key"] for entry in placement["runtime"]}
        authoritative = {entry["key"]
                         for entry in placement["authoritative"]}
        self.assertEqual(runtime, set(guard.RUNTIME_DIRECTORY_KEYS))
        for key in ("workspace_root", "prepared_data", "outputs", "logs",
                    "verification", "clearml_local"):
            self.assertIn(key, authoritative)
        for entry in placement["runtime"]:
            self.assertTrue(guard.is_within(entry["path"],
                                            guard.RUNTIME_ROOT))
        for entry in placement["authoritative"]:
            self.assertTrue(guard.is_within(entry["path"],
                                            guard.PRIVATE_ROOT))


if __name__ == "__main__":
    unittest.main()
