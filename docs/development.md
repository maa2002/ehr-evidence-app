# Development

## Requirements

- Windows 11 with Windows PowerShell 5.1 (the storage policy is
  Windows-specific; destination checks fail closed elsewhere).
- Git 2.24 or later and uv.
- An existing Python 3.12.8 interpreter (not the GNN environment) as the base
  of the application environment.
- For real-data work only: the existing `omopgnn` GNN environment, the
  inherited upstream tree, and the local PostgreSQL OMOP database. None of
  these is needed for tests.

Set up the storage roots and the application environment once, as described
in [data-handling.md](data-handling.md) and
[environments.md](environments.md).

## Session set-up

Every PowerShell session starts with:

```powershell
Set-Location "G:\Industry Dataset\ehr-evidence-app"
. .\scripts\setup-destinations.ps1 -Quiet
$AppPython = Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe'
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
```

Real-data runs that load the inherited graphs or checkpoint use the GNN
interpreter instead:

```powershell
$GnnPython = 'C:\path\to\omopgnn\python.exe'   # existing omopgnn interpreter
& $GnnPython -m ehr_evidence_app.gnn.<module> --operator-real
```

Dot-sourcing keeps the process-local variables (temp, caches, pytest,
ClearML offline mode) in the session; a new terminal starts clean. Start
Python only after set-up so that the bytecode cache is routed correctly.
Never install anything into the GNN environment.

Modules run as `python -m ehr_evidence_app.<package>.<module>`. Real-data
work is always explicit:

- `gnn.parity`, `gnn.outputs`, `gnn.provenance`, `gnn.split`, `gnn.binding`
  and `gnn.admission` run invented fixtures unless given `--operator-real`;
- `gnn.mapping` is operator-only and takes no arguments;
- `gnn.baseline` needs `--output`, `gnn.assets` writes only with `--write`,
  and `sources.exports` needs `--version-label`;
- `sources.pilot` needs database credentials in the process environment;
- `tracking.connection` and `tracking.registration` contact ClearML only with
  their explicit live flags.

Writers refuse to replace an existing record. The one exception is
`sources.exports`, which re-verifies an existing version and rewrites its
manifest; never point it at a published version.

## Tests

```powershell
& $AppPython -m pytest -q
```

pytest takes `testpaths` and `pythonpath` from `pyproject.toml` and its
temp and cache locations from `PYTEST_ADDOPTS`, which set-up routes to the
runtime root. The suite uses invented fixtures only; it never opens protected
data, contacts a service or runs upstream code. Scratch files go under the
workspace `test-scratch` directory or the runtime pytest temp.

Some cases are skipped by design where the platform cannot run them, for
example real symbolic-link tests without the symlink privilege. Setup-script
tests start a child `powershell -NoProfile -NonInteractive` and skip if the
execution policy blocks scripts.

## Quality gates

Run before every commit:

```powershell
& $AppPython -m pytest -q
& $AppPython -m compileall -q src tests
& $AppPython -m ehr_evidence_app.quality.publication
git diff --check
```

When dependencies change, also run `& $AppPython -m ehr_evidence_app.environment.app`.
The publication audit scans the working tree, the index and all history; it
must pass before anything is pushed.

## Conventions

**Modules.** Standard library first; heavy dependencies (NumPy, PyTorch)
are imported inside the functions that need them, so importing the package
stays side-effect free. A module docstring states what the module does and
its usage; `main(argv)` returns the exit code (`0` pass, `1` findings or
failure, `2` operational error where the module distinguishes it).

**Failing closed.** Check every precondition before writing. Report problems
as stable finding codes; never print identifiers, row values, scores,
secrets or raw exception text.

**Inputs.** Pin every input of a real-data mode by SHA-256 (and size where
stable) in the module's `real_plan()`. Read each input once as bounded bytes
and parse only verified bytes. Pins for historical records refer to the
private workspace archive and never change.

**Outputs.** Write into a new versioned directory through a `.partial`
sibling and an atomic rename. Refuse an existing output; a correction is a
new version. Never edit a published record or a file that a record pins.

**Configuration.** Configuration may narrow a policy but never widen it;
fixed roots and allowlists live in code. When
`config/inherited_baseline.json` changes, update the pin
`APPROVED_CONFIG_SHA256` in `ehr_evidence_app.gnn.assets`.

**Tests.** Mirror the package layout (`tests/<package>/test_<module>.py`).
Use invented values; name fixture files with `synthetic` as a whole word.
Assemble secret-shaped strings from fragments at run time so that no
secret-shaped literal appears in the source.

**Text files.** UTF-8, LF line endings (enforced by `.gitattributes`); the
setup script stays ASCII.

## Version control and releases

- Commit with the repository-local author identity; do not change global Git
  configuration.
- Keep `main` releasable: all quality gates pass on every commit.
- Versions follow semantic versioning (0.y.z while interfaces are still
  changing). A release updates `version` in `pyproject.toml`,
  `__version__` in `src/ehr_evidence_app/__init__.py` and
  [CHANGELOG.md](../CHANGELOG.md), then adds an annotated tag `vX.Y.Z`.
- Protected data, generated outputs and secrets never enter Git; see
  [data-handling.md](data-handling.md#repository-content-controls).
