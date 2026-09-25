# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html); 0.y releases may
change interfaces.

## [Unreleased]

### Added

- Continuous integration on a Windows runner: compile, release-metadata
  check, test suite, synthetic module modes, publication audit and a
  clean-tree check (`.github/workflows/ci.yml`).
- Release workflow: reruns the checks at a version tag and publishes a
  GitHub release from the changelog entry (`.github/workflows/release.yml`).
- `quality.release`: checks that the tag, the package version and the
  newest changelog entry agree, and extracts the release notes.
- Dependabot updates for the pinned workflow actions.

### Changed

- Environment tests skip, instead of creating directories, when the
  project storage layout is absent.

## [0.3.0] - 2026-09-26

First tagged release: the data-handling foundation, the integration of the
inherited GNN for the development pilot, and the Phase 3 closure.

### Added

- `storage`: fixed storage roots under one policy anchor; lexical and
  on-disk destination checks; process-local destination set-up
  (`scripts/setup-destinations.ps1`) and its verifier; runtime-volume
  preflight with identity pinning and ACL checks.
- `sources`: verified staging of the legacy OMOP CSV exports; bounded,
  read-only, single-snapshot extraction of the development pilot with native
  event identifiers and coverage checks.
- `gnn`: inherited baseline identity record; asset validation; identity
  mapping between graphs and source persons and visits; preprocessing parity
  against an independent oracle; offline model outputs with canonical
  agreement; provenance and split-membership sidecars.
- `gnn.binding`: run-binding verification of the inherited model. Replays
  the pinned split, reproduces the checkpoint's saved `pos_weight` and its
  stored validation and test metrics, runs negative controls and measures
  batch sensitivity.
- `gnn.admission`: the Phase 3 closure record. Typed per-case admission,
  label-evidence layers, per-driver visit tags, the claim policy and the map
  of superseded flags (`config/phase3_closure.json`).
- `tracking`: tracker policy verifier; ClearML connection checks; controlled
  metadata-only tracking; inherited input registration; lineage schema and
  validator.
- `environment`: application environment smoke check; GNN environment
  capture with before-and-after fingerprints.
- `quality`: publication audit of the working tree, index and full history.
- Documentation by subsystem and architecture decision records.

### Changed

Relative to the untagged pre-release development tree:

- All storage moved to one permission-restricted NTFS volume; encryption at
  rest is recorded but no longer required
  ([ADR 0001](docs/decisions/0001-single-volume-storage.md)).
- Tools reorganised into the `ehr_evidence_app` package, run with
  `python -m` ([ADR 0003](docs/decisions/0003-package-layout.md)).
- Missing source-event linkage now restricts specific claims instead of
  blocking whole cases ([ADR 0002](docs/decisions/0002-phase-3-closure.md)).

### Fixed

- The asset-validation record and the inherited-input registration are no
  longer overwritten by a re-run; an identical record is left unchanged and
  a different one is refused.
- The identity mapping pins the configuration its source pilot was actually
  extracted under.
- `sources.exports` requires an explicit version label, so a default can no
  longer re-verify (and rewrite the manifest of) the published version.

[Unreleased]: https://github.com/maa2002/ehr-evidence-app/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/maa2002/ehr-evidence-app/tree/v0.3.0
