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

## Session Set-Up

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

- `gnn.parity`, `gnn.outputs`, `gnn.provenance`, `gnn.split`, `gnn.binding`,
  `gnn.admission`, `gnn.linkage` and `evidence.bundles` run invented
  fixtures unless given `--operator-real`;
- `evidence.contracts` checks the committed limits and schemas, and
  regenerates the schemas only with `--write-schemas`;
- `casesets.selection` and `casesets.prepare` run invented fixtures unless
  given `--operator-real` together with `--case-set`; `gnn.linkage` and
  `evidence.bundles` take `--case-set` with `--operator-real` to run a
  registered case set instead of the pilot;
- `gnn.mapping` is operator-only and takes no arguments;
- `gnn.baseline` needs `--output`, `gnn.assets` writes only with `--write`,
  and `sources.exports` needs `--version-label`;
- `sources.pilot` needs database credentials in the process environment;
- `api.app --serve` and `api.launch` serve the invented case set unless
  given `--operator-real`, and only on 127.0.0.1 with the launch token of
  their environment;
- `tracking.connection`, `tracking.registration`, `tracking.aggregates`,
  `tracking.measurements` and `tracking.models` contact ClearML only with
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

## Quality Gates

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

## Continuous Integration

`.github/workflows/ci.yml` runs on every push to `main`, on pull requests
and on demand, on a Windows runner with Python 3.12.8 and the hashed lock:

1. compile `src` and `tests` with warnings as errors;
2. check release metadata (`ehr_evidence_app.quality.release`);
3. run the test suite, printing every skip reason;
4. run each module's synthetic default mode;
5. run the publication audit over the full history;
6. confirm that the checks created or changed no file.

The runner has no project storage layout, so tests that need the workspace
or runtime scratch directories skip with a stated reason. CI therefore covers
the portable part of the suite; the full suite runs locally after set-up and
remains the gate for commits. CI never reads protected data, contacts a
tracker or uses repository secrets. Actions are pinned by commit SHA and kept
current by Dependabot (`.github/dependabot.yml`).

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

## Reconstruction and Restart

The application is rebuilt from a clean clone of the repository, the
pinned assets in the workspace and the hashed locks, without rerunning an
extraction or a training (work item 63):

1. Clone the repository into its configured location
   (`source_repository` in `config/paths.json`) and check out a release
   tag.
2. Recreate or check the environments from their locks
   ([environments.md](environments.md)) and run the session set-up above.
3. Check the environment, the storage and the tracker's configuration,
   then run the tests:

   ```powershell
   & $AppPython -m ehr_evidence_app.environment.app
   & $AppPython -m ehr_evidence_app.storage.runtime
   & $AppPython -m ehr_evidence_app.storage.destinations
   & $AppPython -m ehr_evidence_app.quality.publication
   & $AppPython -m ehr_evidence_app.tracking.policy
   & $AppPython -m ehr_evidence_app.tracking.connection
   & $AppPython -m pytest -q
   ```

4. Start the application on the development case sets
   (`api.launch --operator-real`, [interface.md](interface.md)) and use
   both workflows; stop it, start it again and use them again.

The check was made on 2026-10-05 at release 0.12.0. The working checkout
was set aside by renaming it, and a fresh clone from GitHub took its place
until the check ended:

| Step | Observed |
| :--- | :--- |
| Environment | The Application Environment Matched Its Hashed Lock; the Import Probe Passed |
| Storage | The Runtime Root and the Destinations Checked: 177 of 177 Checks. The Bytecode Probe Failed at First Under a Shell That Set `PYTHONDONTWRITEBYTECODE`; Without It, It Passed |
| Publication Audit | No Secret or Data File in the Tree or Its History |
| Tracker Configuration | The Policy and the Connection's Declaration Checked Offline |
| Tests | 1,656 Passed, 5 Skipped (Symbolic Links Unavailable to the Account, and an Interpreter Layout), None Failed |
| Both Workflows | Lists, Presence Checks, Questions About the Whole Record and the Explanation for the First Three Admitted Cases of `dev-30-v1`, 24 Requests With the Model, Answered Without a Problem, Each Writing Its Runtime Record |
| Restart | The API Stopped and Started Again; the Same 24 Requests Gave the Same Outcomes, Writers and Texts, Compared by Digest |
| Pinned Assets | The Workspace's Protected Data and Outputs Were Listed Before and After: Nothing Added, Removed or Rebuilt; Only the Set-Up's Markers and the Checks' Own Reports Were Rewritten |

**Seeds.** The steps that draw or resample use fixed seeds, so they give
the same result when run again:

| Step | Seed |
| :--- | :--- |
| The Inherited GNN's Split of Patients (Recovered, Not Rerun) | 7 |
| The Draw of `dev-30-v1` | 20260926 |
| The Draw of `eval-100-v1` | 20260927 |
| The Model's Decoding (`config/generation.json`) | 20260927 |
| The Paired Bootstrap of the Statistics (10,000 Replicates) | 20260928 |
| The Rating Model's Settings, the Order of the Texts It Reads, and the Owner's Sample | 20261003 |

## Version Control and Releases

- Commit with the repository-local author identity; do not change global Git
  configuration.
- Keep `main` releasable: all quality gates pass on every commit.
- Versions follow semantic versioning (0.y.z while interfaces are still
  changing). Record changes under `Unreleased` in
  [CHANGELOG.md](../CHANGELOG.md) as they land.
- To release, rename the `Unreleased` section to `[X.Y.Z] - YYYY-MM-DD` and
  add its link reference, set `version` in `pyproject.toml` and
  `__version__` in `src/ehr_evidence_app/__init__.py`, and check the result:

  ```powershell
  & $AppPython -m ehr_evidence_app.quality.release --tag vX.Y.Z
  ```

  Then commit, add an annotated tag `vX.Y.Z` and push the commit and the tag.
  `.github/workflows/release.yml` reruns every CI check at the tag, confirms
  that the tag, both versions and the newest changelog entry agree, and
  publishes a GitHub release whose notes are that entry. It can also be run
  by hand for an existing tag.
- Protected data, generated outputs and secrets never enter Git; see
  [data-handling.md](data-handling.md#repository-content-controls).
