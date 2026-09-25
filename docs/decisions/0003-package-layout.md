# 0003. Package layout and repository structure

Status: accepted (2026-09-25).

## Context

The code grew as a flat `scripts/` directory of stand-alone tools, each
adding the repository to `sys.path`, with one document per development step
and process records at the repository root. The structure made shared code
hard to reuse, hid the architecture, and would not scale to the evidence,
generation, validation, API and evaluation layers still to come. Several
historical records pin the exact bytes of source files from that layout.

## Decision

- All code lives in one importable package, `src/ehr_evidence_app`, split by
  responsibility: `storage`, `sources`, `gnn`, `tracking`, `environment` and
  `quality`. New layers (evidence, QA, generation, validation, API,
  evaluation) are added as further subpackages.
- Modules run with `python -m ehr_evidence_app.<package>.<module>` and use
  absolute imports. No module adds the repository to `sys.path`; the only
  path changes left are scoped ones, such as loading pinned upstream modules
  or running an isolated probe.
- Tests mirror the package layout under `tests/`; pytest reads `testpaths`
  and `pythonpath` from `pyproject.toml`.
- `scripts/` holds only shell entry points (`setup-destinations.ps1`).
- Documentation is organised by subsystem in `docs/`, with decisions in
  `docs/decisions/`. Process records stay outside the repository.
- The package is not installed into either environment; `src` is placed on
  `PYTHONPATH`. This keeps the hash-locked application environment exact and
  the GNN environment untouched.
- Records that pin pre-0.3.0 source files resolve those pins to a byte-exact
  archive of the previous layout in the private workspace, so the pins stay
  valid without keeping obsolete files in the repository.

## Consequences

- One import path for every tool and test; shared helpers are reused rather
  than copied.
- The repository root holds only project metadata (`README.md`,
  `CHANGELOG.md`, `pyproject.toml`, `.gitignore`, `.gitattributes`).
- Reproducing a historical record needs the private archive as well as the
  repository; this is recorded in each affected module's `real_plan()`.
