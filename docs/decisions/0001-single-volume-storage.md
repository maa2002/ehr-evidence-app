# 0001. Single-volume storage on permission-restricted NTFS

Status: accepted (2026-09-25).

## Context

The project first kept its repository, private workspace and protected data
on an external USB hard disk formatted exFAT, with disposable runtime files on
an internal NVMe SSD (G:). The runtime root was barred from holding protected
data unless full-volume BitLocker encryption could be shown. That split had
costs and no security benefit:

- exFAT has no file permissions, so the "authoritative" location had no
  access control at all, and link and junction tests could not run there;
- the external disk produced transient rename failures and slow small-file
  operations;
- the host runs Windows 11 Home, which has no BitLocker, so the encryption
  condition could never be met, and the external disk was not encrypted
  either.

The project owner confirmed that no data-use or institutional requirement
for encryption at rest applies to this academic project, and that
permission-restricted storage is sufficient.

## Decision

- All project storage lives under one policy anchor, `G:\Industry Dataset`,
  on the internal NTFS SSD: the repository, the private workspace
  (authoritative) and the runtime root (disposable).
- The anchor's access-control list grants access only to the owning user,
  Administrators and SYSTEM; everything below inherits it.
- The protected-data gate equals the runtime-volume gate (identity, health,
  NTFS, free space, path safety, restrictive ACLs). Drive encryption is
  recorded for information and is not required.
- Raw MIMIC-IV files and the Athena vocabularies stay on the external disk as
  a cold archive; the PostgreSQL database and the GNN environment stay on C:.
- The move was a verified copy: every file's SHA-256 matched its source
  (565 files, 0 mismatches). Historical records keep the paths they were
  written with; content identity carries across by SHA-256.

## Consequences

- Protected data now sits on a volume with enforced access control.
- The fixed roots in `ehr_evidence_app.storage.policy` and the setup script
  name the new anchor; configuration can choose locations only inside it.
- Records written before the move contain the previous absolute paths.
  Consumers verify inputs by SHA-256, not by recorded path.
- Anyone with administrative access to the machine can read the data; this
  is accepted for a single-user academic workstation.
