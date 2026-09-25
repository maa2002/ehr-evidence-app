#!/usr/bin/env python3
"""Security preflight for the project storage volume.

Standard library only; Windows only. Volume, disk, encryption and ACL facts
are read through read-only PowerShell queries (Get-Volume, Get-Partition,
Get-Disk, Get-PhysicalDisk, Get-BitLockerVolume and the directory security
descriptor); nothing is changed by the default run.

Two gates are evaluated:

  runtime gate         exact expected disk model/bus/media, fixed NTFS
                       volume, healthy volume and disk, minimum free space,
                       a pinned volume identity digest (when a prior record
                       exists), plain path components without reparse points
                       or .git, and restrictive effective ACLs on the project
                       anchor and the runtime root. runtime_gate_checks()
                       re-runs it read-only, with the pin required, before
                       ordinary setup/verifier writes.
  protected-data gate  the runtime gate. Protected data is kept on the
                       permission-restricted NTFS volume; everything under
                       the anchor inherits its restrictive DACL. BitLocker
                       state is recorded for information but is not required
                       by the project's data-handling policy.

--create-root creates only the missing components of the runtime root, one
level at a time, and only after the identity, volume, health, space and
path checks pass and every pre-existing component has the restrictive ACL.
Each created directory immediately receives a protected DACL (inheritance
removed) that grants full control to SYSTEM, Administrators and the current
user only, and is then verified. On any failure, the components created by
that call are removed again, in reverse order, when each is still an empty
plain directory. Pre-existing directories are never re-permissioned or
removed.

A sanitised record is written to the workspace verification directory
unless --no-record is given. It holds no account name, SID, unit serial, raw
volume or partition GUID, credential or patient content; the volume is
pinned by a versioned SHA-256 identity digest instead. The record also
carries the placement manifest that distinguishes authoritative workspace
assets from disposable runtime/cache destinations.

Usage:
    python -m ehr_evidence_app.storage.runtime [--create-root] [--no-record]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import stat
import subprocess
import sys

from ehr_evidence_app.storage import policy as guard

REPO_ROOT = guard.REPO_ROOT
PATHS_CONFIG = os.path.join(REPO_ROOT, "config", "paths.json")
RECORD_PATH = (guard.PRIVATE_ROOT
               + "\\outputs\\verification\\runtime-storage-preflight.json")
RUNTIME_PARENT = guard.RUNTIME_ROOT.rsplit("\\", 1)[0]
RUNTIME_DIRECTORIES = (("runtime-parent", RUNTIME_PARENT),
                       ("runtime-root", guard.RUNTIME_ROOT))
DRIVE_LETTER = guard.RUNTIME_ROOT[0]

EXPECTED_DISK_MODEL = "CT1000T500SSD8"
EXPECTED_BUS = "NVMe"
EXPECTED_MEDIA = "SSD"
EXPECTED_FILE_SYSTEM = "NTFS"
EXPECTED_DRIVE_TYPE = "Fixed"
HEALTHY = "Healthy"
MIN_FREE_BYTES = 10 * 1024 ** 3
QUERY_TIMEOUT_SECONDS = 120

# Versioned identity digest: SHA-256 over a fixed prefix line and the
# canonical fields below as "name=value" lines joined by "\n". Mirrored in
# setup-destinations.ps1. Raw GUIDs are used only in memory.
IDENTITY_VERSION = "v1"
IDENTITY_PREFIX = "ehr-runtime-volume-identity:" + IDENTITY_VERSION
IDENTITY_FIELDS = ("disk_model", "bus_type", "size_bytes", "partition_guid",
                   "volume_id")
RAW_IDENTIFIERS = ("volume_id", "partition_guid")

SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"
FULL_CONTROL = 0x1F01FF
INHERIT_BOTH = "ContainerInherit, ObjectInherit"
# Get-BitLockerVolume EncryptionMethod values that name a real method.
KNOWN_ENCRYPTION_METHODS = frozenset([
    "Aes128", "Aes256", "Aes128Diffuser", "Aes256Diffuser", "XtsAes128",
    "XtsAes256", "Hardware"])

VOLUME_QUERY = r"""
$ErrorActionPreference = 'Stop'
$letter = '%s'
$v = Get-Volume -DriveLetter $letter
$p = Get-Partition -DriveLetter $letter
$d = Get-Disk -Number $p.DiskNumber
$pd = @(Get-PhysicalDisk |
    Where-Object { [string]$_.DeviceId -eq [string]$p.DiskNumber })
$bitlocker = [ordered]@{ available = $false; protection_status = $null;
    volume_status = $null; encryption_percentage = $null;
    encryption_method = $null }
try {
    $bl = Get-BitLockerVolume -MountPoint ($letter + ':') -ErrorAction Stop
    $bitlocker.available = $true
    $bitlocker.protection_status = [string]$bl.ProtectionStatus
    $bitlocker.volume_status = [string]$bl.VolumeStatus
    $bitlocker.encryption_percentage = [double]$bl.EncryptionPercentage
    $bitlocker.encryption_method = [string]$bl.EncryptionMethod
} catch { $bitlocker.available = $false }
$media = 'unknown'
if ($pd.Count -eq 1) { $media = [string]$pd[0].MediaType }
[ordered]@{
    file_system = [string]$v.FileSystem
    drive_type = [string]$v.DriveType
    volume_health = [string]$v.HealthStatus
    volume_operational = [string]$v.OperationalStatus
    size_bytes = [int64]$v.Size
    free_bytes = [int64]$v.SizeRemaining
    volume_id = [string]$v.UniqueId
    partition_guid = [string]$p.Guid
    disk_model = [string]$d.FriendlyName
    bus_type = [string]$d.BusType
    disk_health = [string]$d.HealthStatus
    disk_operational = [string]$d.OperationalStatus
    disk_is_boot = [bool]$d.IsBoot
    disk_is_system = [bool]$d.IsSystem
    media_type = $media
    bitlocker = $bitlocker
} | ConvertTo-Json -Compress
"""

ACL_QUERY = r"""
$ErrorActionPreference = 'Stop'
$acl = New-Object System.Security.AccessControl.DirectorySecurity(
    '%s', 'Access, Owner')
$sid = [System.Security.Principal.SecurityIdentifier]
$owner = $acl.GetOwner($sid).Value
$rules = @(foreach ($rule in $acl.GetAccessRules($true, $true, $sid)) {
    [ordered]@{
        sid = $rule.IdentityReference.Value
        rights = [int64]$rule.FileSystemRights
        type = [string]$rule.AccessControlType
        inherited = [bool]$rule.IsInherited
        inheritance = [string]$rule.InheritanceFlags
        propagation = [string]$rule.PropagationFlags
    }
})
[ordered]@{ protected = [bool]$acl.AreAccessRulesProtected; owner = $owner;
            rules = $rules } | ConvertTo-Json -Compress -Depth 4
"""

USER_SID_QUERY = ("[System.Security.Principal.WindowsIdentity]::GetCurrent()"
                  ".User.Value")


class PreflightError(Exception):
    """A failure that stops the preflight before it completes."""


def ps_literal(text):
    """Return text as the body of a PowerShell single-quoted literal."""
    return text.replace("'", "''")


def run_powershell(script):
    """Run one read-only PowerShell script and return its stdout text.

    Windows PowerShell 5.1 is started without an inherited PSModulePath, so
    a PowerShell 7 module path cannot stop its own modules (Storage,
    BitLocker) from loading.
    """
    powershell = os.path.join(os.environ.get("SYSTEMROOT", "C:\\Windows"),
                              "System32", "WindowsPowerShell", "v1.0",
                              "powershell.exe")
    env = {name: value for name, value in os.environ.items()
           if name.upper() != "PSMODULEPATH"}
    try:
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command",
             "& { " + script + " }"],
            shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=QUERY_TIMEOUT_SECONDS,
            check=False, env=env)
    except (OSError, subprocess.SubprocessError) as error:
        raise PreflightError("PowerShell query could not run (%s)"
                             % type(error).__name__)
    if completed.returncode != 0:
        raise PreflightError("PowerShell query failed with exit code %d"
                             % completed.returncode)
    return completed.stdout.decode("utf-8", errors="replace").strip()


def collect_volume_facts():
    """Return the runtime drive's volume, disk and encryption facts."""
    text = run_powershell(VOLUME_QUERY % ps_literal(DRIVE_LETTER))
    try:
        facts = json.loads(text)
    except ValueError:
        raise PreflightError("volume query did not return JSON")
    if not isinstance(facts, dict):
        raise PreflightError("volume query did not return an object")
    return facts


def collect_acl(path):
    """Return the ACL facts of one existing directory (SIDs, not names)."""
    try:
        acl = json.loads(run_powershell(ACL_QUERY % ps_literal(path)))
    except ValueError:
        raise PreflightError("ACL query did not return JSON")
    rules = acl.get("rules")
    if isinstance(rules, dict):
        acl["rules"] = [rules]
    elif rules is None:
        acl["rules"] = []
    return acl


def current_user_sid():
    """Return the current user's SID for the restrictive grant."""
    sid = run_powershell(USER_SID_QUERY)
    if not sid.startswith("S-1-5-21-"):
        raise PreflightError("the current user SID could not be determined")
    return sid


def identity_digest(facts):
    """Return the versioned SHA-256 identity digest, or None if incomplete.

    Every canonical field must be present and non-empty; the digest is
    "v1:" followed by 64 lower-case hexadecimal digits.
    """
    lines = [IDENTITY_PREFIX]
    for name in IDENTITY_FIELDS:
        value = facts.get(name)
        if value is None or isinstance(value, bool) or value == "":
            return None
        lines.append("%s=%s" % (name, value))
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return "%s:%s" % (IDENTITY_VERSION, digest)


def reduced_bitlocker(facts):
    """Return only the BitLocker fields that the protected gate evaluates."""
    raw = facts.get("bitlocker")
    raw = raw if isinstance(raw, dict) else {}
    return {"available": raw.get("available") is True,
            "protection_status": raw.get("protection_status"),
            "volume_status": raw.get("volume_status"),
            "encryption_percentage": raw.get("encryption_percentage"),
            "encryption_method": raw.get("encryption_method")}


def encryption_status(facts):
    """Return "complete", "incomplete" or "unknown" BitLocker encryption.

    "complete" requires current Get-BitLockerVolume evidence of
    ProtectionStatus On, VolumeStatus FullyEncrypted, EncryptionPercentage
    100 and a known encryption method. An unavailable cmdlet is "unknown";
    any other reported state is "incomplete". No other source counts.
    """
    bitlocker = reduced_bitlocker(facts)
    if not bitlocker["available"]:
        return "unknown"
    percentage = bitlocker["encryption_percentage"]
    if (bitlocker["protection_status"] == "On"
            and bitlocker["volume_status"] == "FullyEncrypted"
            and isinstance(percentage, (int, float))
            and not isinstance(percentage, bool) and percentage == 100
            and bitlocker["encryption_method"] in KNOWN_ENCRYPTION_METHODS):
        return "complete"
    return "incomplete"


def volume_checks(facts, pinned_digest=None):
    """Return (name, passed, detail) checks for the volume facts.

    The identity check compares the current identity digest with the pinned
    one; with no pin it only requires a complete digest. No raw identifier
    appears in any detail.
    """
    checks = [
        ("disk-model", facts.get("disk_model") == EXPECTED_DISK_MODEL,
         "disk model %s (expected %s)" % (facts.get("disk_model"),
                                          EXPECTED_DISK_MODEL)),
        ("bus-type", facts.get("bus_type") == EXPECTED_BUS,
         "bus %s (expected %s)" % (facts.get("bus_type"), EXPECTED_BUS)),
        ("media-type", facts.get("media_type") == EXPECTED_MEDIA,
         "media %s (expected %s)" % (facts.get("media_type"),
                                     EXPECTED_MEDIA)),
        ("drive-type", facts.get("drive_type") == EXPECTED_DRIVE_TYPE,
         "drive type %s (expected %s)" % (facts.get("drive_type"),
                                          EXPECTED_DRIVE_TYPE)),
        ("file-system", facts.get("file_system") == EXPECTED_FILE_SYSTEM,
         "file system %s (expected %s)" % (facts.get("file_system"),
                                           EXPECTED_FILE_SYSTEM)),
        ("volume-health", facts.get("volume_health") == HEALTHY
         and facts.get("volume_operational") == "OK",
         "volume %s/%s" % (facts.get("volume_health"),
                           facts.get("volume_operational"))),
        ("disk-health", facts.get("disk_health") == HEALTHY
         and facts.get("disk_operational") == "Online",
         "disk %s/%s" % (facts.get("disk_health"),
                         facts.get("disk_operational"))),
    ]
    free = facts.get("free_bytes")
    enough = (isinstance(free, int) and not isinstance(free, bool)
              and free >= MIN_FREE_BYTES)
    checks.append(("free-space", enough, "%s bytes free (minimum %d)"
                   % (free, MIN_FREE_BYTES)))
    digest = identity_digest(facts)
    if pinned_digest is None:
        checks.append(("volume-identity", digest is not None,
                       "identity digest %s" % (
                           "recorded for later runs" if digest
                           else "could not be derived")))
    else:
        matched = digest is not None and digest == pinned_digest
        checks.append(("volume-identity", matched,
                       "identity digest %s the pinned digest"
                       % ("matches" if matched else "DIFFERS from")))
    return checks


def acl_problems(acl, user_sid):
    """Return why a directory ACL is not the restrictive expected DACL.

    Expected: inheritance disabled (protected), no inherited or deny rule,
    allow rules only for SYSTEM, Administrators and the current user, and
    full control inherited to children for each of the three. The owner
    must be one of the same three principals.
    """
    allowed = {SYSTEM_SID, ADMINISTRATORS_SID, user_sid}
    problems = []
    if acl.get("protected") is not True:
        problems.append("inheritance is not disabled")
    if acl.get("owner") not in allowed:
        problems.append("the owner is not SYSTEM, Administrators or the "
                        "current user")
    granted = set()
    for rule in acl.get("rules") or []:
        if rule.get("inherited"):
            problems.append("an inherited rule is present")
        if rule.get("type") != "Allow":
            problems.append("a non-allow rule is present")
        if rule.get("sid") not in allowed:
            problems.append("a rule grants access to another principal")
            continue
        if (rule.get("rights") == FULL_CONTROL
                and rule.get("inheritance") == INHERIT_BOTH
                and rule.get("propagation") == "None"):
            granted.add(rule.get("sid"))
    for sid, label in ((SYSTEM_SID, "SYSTEM"),
                       (ADMINISTRATORS_SID, "Administrators"),
                       (user_sid, "the current user")):
        if sid not in granted:
            problems.append("%s lacks inherited full control" % label)
    return sorted(set(problems))


def acl_summary(acl, user_sid):
    """Return a sanitised ACL description with principal labels, no SIDs."""
    labels = {SYSTEM_SID: "SYSTEM", ADMINISTRATORS_SID: "Administrators",
              user_sid: "current-user"}
    return {
        "inheritance_disabled": acl.get("protected") is True,
        "owner": labels.get(acl.get("owner"), "other"),
        "rules": sorted(
            "%s:%s:%s:%s" % (labels.get(rule.get("sid"), "other"),
                             rule.get("type"),
                             "FullControl" if rule.get("rights")
                             == FULL_CONTROL else rule.get("rights"),
                             rule.get("inheritance"))
            for rule in acl.get("rules") or []),
    }


def restrict_command(path, user_sid):
    """Return the icacls command that applies the restrictive DACL."""
    grants = []
    for sid in (SYSTEM_SID, ADMINISTRATORS_SID, user_sid):
        grants += ["/grant:r", "*%s:(OI)(CI)F" % sid]
    return ["icacls", path, "/inheritance:r"] + grants + ["/Q"]


def path_checks():
    """Return (name, passed, detail) checks for the runtime root path."""
    problem = guard.fixed_policy_problem() or guard.path_problem(
        guard.RUNTIME_ROOT)
    checks = [("path-components", problem is None,
               problem or "plain absolute path components")]
    problem = guard.filesystem_problem(guard.RUNTIME_ROOT, "directory")
    checks.append(("path-no-reparse", problem is None,
                   problem or "existing components are plain directories "
                   "without reparse points or .git"))
    checks.append(("volume-api", guard.runtime_volume_problem() is None,
                   guard.runtime_volume_problem()
                   or "fixed NTFS volume by the Windows volume API"))
    return checks


def is_plain_directory(path):
    """Return True for an existing directory that is not a reparse point."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not guard.is_link_like(info)


def remove_created(created, approved):
    """Remove directories created by create_root, newest first.

    Each path is removed only when it is exactly one of the approved paths
    passed to create_root, is still a plain non-reparse directory and is
    empty. Returns the paths that could not be removed safely.
    """
    left = []
    for path in reversed(created):
        try:
            safe = (path in approved and is_plain_directory(path)
                    and not os.listdir(path))
            if safe:
                os.rmdir(path)
                continue
        except OSError:
            pass
        left.append(path)
    return left


def create_root(user_sid, run=subprocess.run,
                paths=(RUNTIME_PARENT, guard.RUNTIME_ROOT),
                acl_problems_of=None):
    """Create missing runtime-root components with a restrictive DACL.

    paths are the approved components, outermost first. A pre-existing
    component must be a plain directory whose ACL is already restrictive
    before any child is created; it is never changed or removed. Each
    missing component is created with os.mkdir, restricted at once with
    icacls and verified (plain directory and restrictive ACL). On any
    failure, the components created by this call are removed again by
    remove_created, and PreflightError is raised. Returns the paths created.
    """
    if acl_problems_of is None:
        def acl_problems_of(path):
            """Return the ACL problems of one existing directory."""
            return acl_problems(collect_acl(path), user_sid)
    created = []
    try:
        for path in paths:
            if os.path.lexists(path):
                problem = guard.filesystem_problem(path, "directory")
                if problem:
                    raise PreflightError(problem)
                problems = acl_problems_of(path)
                if problems:
                    raise PreflightError("existing %s has a weak ACL: %s"
                                         % (path, "; ".join(problems)))
                continue
            os.mkdir(path)
            created.append(path)
            completed = run(restrict_command(path, user_sid), shell=False,
                            stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=60,
                            check=False)
            if completed.returncode != 0:
                raise PreflightError("icacls could not restrict %s" % path)
            problem = guard.filesystem_problem(path, "directory")
            if problem:
                raise PreflightError(problem)
            problems = acl_problems_of(path)
            if problems:
                raise PreflightError("%s ACL verification failed: %s"
                                     % (path, "; ".join(problems)))
    except BaseException as error:
        left = remove_created(created, tuple(paths))
        if left:
            raise PreflightError("%s; created directories left in place "
                                 "(not empty or not plain): %s"
                                 % (error, ", ".join(left)))
        raise
    return created


def placement_manifest(paths_config=PATHS_CONFIG):
    """Return the placement of every configured directory key by tier."""
    with open(paths_config, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    placement = {"authoritative": [], "runtime": []}
    for key, entry in sorted(config["directories"].items()):
        path, root = guard.configured_directory_path(key, entry)
        tier = "runtime" if root == guard.RUNTIME_ROOT else "authoritative"
        placement[tier].append({"key": key, "path": path})
    placement["sources_outside_configuration"] = [
        "raw MIMIC-IV and Athena vocabularies (cold archive, external disk)",
        "PostgreSQL service and data (C:)", "legacy OMOP exports (C:)",
        "staged source exports and source pilot (workspace)",
        "inherited GNN tree (policy anchor)",
        "Git worktree (policy anchor)"]
    return placement


def read_pinned_digest(path=RECORD_PATH):
    """Return the pinned identity digest from the preflight record, or None.

    A record written before the digest was introduced (raw volume fields)
    is converted in memory to the same digest, so the pin carries over.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            record = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    value = record.get("identity_digest")
    if isinstance(value, str) and value.startswith(IDENTITY_VERSION + ":"):
        return value
    volume = record.get("volume")
    if isinstance(volume, dict):
        return identity_digest(volume)
    return None


def collect_runtime_acls():
    """Return (label, ACL facts or None) for both runtime levels."""
    return [(label, collect_acl(path) if is_plain_directory(path) else None)
            for label, path in RUNTIME_DIRECTORIES]


def acl_checks(acls, user_sid):
    """Return (name, passed, detail) checks for collected runtime ACLs."""
    checks = []
    for label, acl in acls:
        if acl is None:
            problems = ["directory is missing or not a plain directory"]
        else:
            problems = acl_problems(acl, user_sid)
        checks.append(("acl:%s" % label, not problems,
                       "; ".join(problems) or "restrictive protected DACL"))
    return checks


def runtime_gate_checks():
    """Return the current read-only runtime gate as (name, passed, detail).

    Used before ordinary setup and verifier writes: pinned identity digest
    (required), identity, health, fixed NTFS media, free space, path and
    reparse checks, and restrictive ACLs on both runtime levels. Nothing is
    written; no raw identifier is returned.
    """
    facts = collect_volume_facts()
    pinned = read_pinned_digest()
    checks = volume_checks(facts, pinned)
    if pinned is None:
        checks.append(("volume-pin", False,
                       "no pinned identity digest; run "
                       "ehr_evidence_app.storage.runtime first"))
    checks += path_checks()
    checks += acl_checks(collect_runtime_acls(), current_user_sid())
    return checks


def evaluate(facts, acl_results, pinned_digest, extra_checks):
    """Return (checks, runtime_pass, protected_pass, encryption)."""
    checks = [{"name": name, "status": "pass" if passed else "FAIL",
               "detail": detail}
              for name, passed, detail in
              volume_checks(facts, pinned_digest) + extra_checks
              + acl_results]
    runtime_pass = all(check["status"] == "pass" for check in checks)
    encryption = encryption_status(facts)
    checks.append({"name": "encryption", "status": "info",
                   "detail": "BitLocker encryption %s (not required)"
                   % encryption})
    return checks, runtime_pass, runtime_pass, encryption


def build_record(facts, acls, user_sid, checks, runtime_pass,
                 protected_pass, encryption, created, stamp):
    """Return the sanitised record: no raw identifier, SID or serial."""
    return {
        "schema_version": "1.2",
        "purpose": "Project storage volume security preflight and placement",
        "generated_utc": stamp,
        "runtime_root": guard.RUNTIME_ROOT,
        "created_this_run": created,
        "identity_digest": identity_digest(facts),
        "identity_digest_scheme": (
            "v1: SHA-256 over a fixed prefix and the disk model, bus type, "
            "volume size and the partition and volume identifiers; the raw "
            "identifiers are not stored"),
        "volume": {key: facts.get(key) for key in (
            "file_system", "drive_type", "volume_health",
            "volume_operational", "size_bytes", "free_bytes",
            "disk_model", "bus_type", "disk_health", "disk_operational",
            "disk_is_boot", "disk_is_system", "media_type")},
        "bitlocker": reduced_bitlocker(facts),
        "acls": {label: (None if acl is None else acl_summary(acl, user_sid))
                 for label, acl in acls},
        "checks": checks,
        "encryption_status": encryption,
        "runtime_gate": "PASS" if runtime_pass else "FAIL",
        "protected_data_gate": "PASS" if protected_pass else "FAIL",
        "protected_data_gate_rule": (
            "Patient-bearing files may be placed under the policy anchor "
            "when the runtime gate is PASS: the expected fixed, healthy NTFS "
            "volume with the pinned identity and restrictive ACLs. "
            "Encryption is recorded for information and is not required."),
        "placement": placement_manifest(),
    }


def build_parser():
    """Build the command-line parser (no option can relax the policy)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--create-root", action="store_true",
                        help="create the missing runtime root components "
                        "with a restrictive DACL after the checks pass")
    parser.add_argument("--no-record", action="store_true",
                        help="print results without writing the record")
    return parser


def run(argv=None):
    """Run the preflight and return a process exit code."""
    args = build_parser().parse_args(argv)
    if os.name != "nt":
        raise PreflightError("the runtime storage preflight supports "
                             "Windows only")
    problem = guard.destination_problem(RECORD_PATH, "file")
    if problem:
        raise PreflightError("record path: %s" % problem)
    facts = collect_volume_facts()
    pinned = read_pinned_digest()
    extra = path_checks()
    user_sid = current_user_sid()
    prerequisites = volume_checks(facts, pinned) + extra
    created = []
    if args.create_root:
        failed = [name for name, passed, _ in prerequisites if not passed]
        if failed:
            raise PreflightError("root not created; failed checks: %s"
                                 % ", ".join(failed))
        created = create_root(user_sid)
        extra = path_checks()
    acls = collect_runtime_acls()
    checks, runtime_pass, protected_pass, encryption = evaluate(
        facts, acl_checks(acls, user_sid), pinned, extra)
    stamp = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")
    record = build_record(facts, acls, user_sid, checks, runtime_pass,
                          protected_pass, encryption, created, stamp)
    for check in checks:
        print("[%-4s] %-18s %s" % (check["status"], check["name"],
                                   check["detail"]))
    print("Created        : %s" % (", ".join(created) or "nothing"))
    print("RUNTIME GATE   : %s" % record["runtime_gate"])
    print("PROTECTED GATE : %s (encryption %s)"
          % (record["protected_data_gate"], encryption))
    if not args.no_record:
        with open(RECORD_PATH, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
            handle.write("\n")
        print("Record         : %s" % RECORD_PATH)
    return 0 if runtime_pass else 1


def main(argv=None):
    """Run the preflight, reporting a stopping failure without local detail."""
    try:
        return run(argv)
    except PreflightError as error:
        print("FAILED: %s" % error)
    except Exception as error:  # sanitised: text may name local paths
        print("FAILED: unexpected %s" % type(error).__name__)
    print("RUNTIME GATE   : FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
