# Environments

The project uses two Python environments that are never mixed:

| Environment | Purpose | Location | Managed by this project |
| --- | --- | --- | --- |
| Application | Tests, tooling, tracking, and later the API, interface and LLM layers | `UV_PROJECT_ENVIRONMENT` under the runtime root | yes, from a hashed lock |
| GNN (`omopgnn`) | Real-data runs that load the inherited graphs or checkpoint | existing conda environment on C: | no: read-only, never modified |

Command examples assume the session set-up in
[development.md](development.md#session-set-up).

## Application environment

- Python 3.12.8 virtual environment created with uv from an existing,
  non-GNN Python 3.12.8 interpreter (uv-managed Python downloads disabled).
- `pyproject.toml` declares the direct dependencies: FastAPI, Uvicorn,
  Pydantic 2, Streamlit and the ClearML SDK, plus pytest in the `test` extra.
- `requirements/app.lock.txt` pins 56 packages exactly, with SHA-256 hashes,
  for Windows x86-64 and Python 3.12.8 (lock SHA-256
  `9386863C9763E589C8F69DE9CE458A67867FDCB9F587651625E84FAEADB0BCC9`).
- PyTorch, PyG, CUDA packages, model weights and LLM runtimes are excluded;
  the LLM runtime is added with the generation layer.
- The application package is imported from `src` (`PYTHONPATH` or pytest's
  `pythonpath`); it is not installed into the environment.

| Direct package | Locked version |
| --- | --- |
| FastAPI | 0.141.1 |
| Uvicorn | 0.53.0 |
| Pydantic | 2.13.5 |
| Streamlit | 1.64.0 |
| ClearML SDK | 2.1.12 |
| pytest | 9.1.1 |

### Create or recreate

Never copy an environment; recreate it from the lock:

```powershell
Set-Location "G:\Industry Dataset\ehr-evidence-app"
. .\scripts\setup-destinations.ps1 -Quiet
$AppBasePython = '<existing non-GNN Python 3.12.8 executable>'
$env:UV_PYTHON_DOWNLOADS = 'never'
uv venv --no-project --python $AppBasePython --no-managed-python --no-python-downloads $env:UV_PROJECT_ENVIRONMENT
$AppPython = Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe'
uv pip sync --python $AppPython --require-hashes --strict --default-index https://pypi.org/simple requirements\app.lock.txt
```

To change dependencies, edit `pyproject.toml`, regenerate the lock and
review the diff:

```powershell
uv pip compile pyproject.toml --extra test --python-version 3.12.8 --python-platform x86_64-pc-windows-msvc --generate-hashes --default-index https://pypi.org/simple -o requirements\app.lock.txt
```

### Verify

`ehr_evidence_app.environment.app` checks the dependency declaration, the
exact hashed lock, the virtual environment (Python 3.12.8, no system
site-packages) and then runs an isolated, CPU-only import probe (`-I -B`,
minimal environment, ClearML forced offline with a non-existent
configuration). The probe must import the application package from `src`
and every locked dependency at its pinned version, and must find no GNN, GPU
or LLM module and no upstream directory on `sys.path`.

```powershell
& $AppPython -m ehr_evidence_app.environment.app
```

It prints the observed versions and `RESULT: PASS` or `RESULT: FAIL`.

## GNN environment

The inherited model runs only in the existing `omopgnn` conda environment:
Python 3.12.8, PyTorch 2.11.0+cu128, PyG 2.8.0.post1 and NumPy 1.26.4
(CUDA required for model outputs). It is treated as an external,
fingerprinted dependency: nothing is installed, upgraded or removed in it,
and its base installation stays where it is.

`ehr_evidence_app.environment.gnn` records its identity without changing
it. The interpreter is launched once with `-I -S -B` and a minimal
environment; a fixed probe reads installed distribution versions from
package metadata and imports only `torch`, `torch_geometric` and `numpy`.
The whole environment tree is fingerprinted before and after the probe
(names, types, sizes, times, reparse attributes and file contents); any
change blocks the manifest. The interpreter path is never printed or saved.
The manifest is `outputs\environments\gnn-environment.json`; an identical
manifest is left unchanged and a different one is never overwritten.

Real-data modes that load the graphs or checkpoint (`gnn.mapping`,
`gnn.parity --operator-real` and the model-output modules) run in this
interpreter with the application package on `PYTHONPATH`:

```powershell
$GnnPython = 'C:\path\to\omopgnn\python.exe'   # existing omopgnn interpreter
& $AppPython -m ehr_evidence_app.environment.gnn --python $GnnPython
& $GnnPython -m ehr_evidence_app.gnn.parity --operator-real
```

The capture uses only the standard library; run it from the application
interpreter so that the inspected environment is not the one executing the
tool. `--python` names the environment to inspect. The GNN real-data modes
refuse an existing versioned output before loading anything, so re-running
one never alters a published record.
