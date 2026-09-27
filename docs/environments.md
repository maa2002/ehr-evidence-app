# Environments

The project uses three Python environments that are never mixed:

| Environment | Purpose | Location | Managed by this project |
| --- | --- | --- | --- |
| Application | Tests, tooling, tracking, the generation adapter, and later the API and interface | `UV_PROJECT_ENVIRONMENT` under the runtime root | yes, from a hashed lock |
| Model | The language model's worker (`generation.worker`): PyTorch with CUDA, Transformers, the schema-constrained decoders | `environments\uv\ehr-evidence-llm` under the runtime root, with a uv-managed interpreter under `python` | yes, from a hashed lock |
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
  the language model runs in the model environment, reached through
  `generation.runtime` over the worker's standard input and output.
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

## Model environment

The language model's worker runs in its own environment on G:, with its
own interpreter, so that PyTorch and CUDA never enter the application
environment ([generation.md](generation.md)):

- a uv-managed CPython 3.12.13 installed under the runtime root
  (`UV_PYTHON_INSTALL_DIR`), with no executables placed on the user's path
  and no registry entries;
- `requirements/llm.txt` names the direct dependencies, and
  `requirements/llm.lock.txt` pins 45 packages exactly, with SHA-256
  hashes, for Windows x86-64 and Python 3.12.13 (lock SHA-256
  `0981449E80B35299BFD4594AC7727104F3259F7CA47C1F107407A9A3C5862AD4`);
- CUDA's compute cache is routed to the runtime root (`CUDA_CACHE_PATH`),
  and the worker runs with Hugging Face offline, without telemetry, and
  with every cache, home and temporary directory under the runtime root.

| Direct package | Locked version |
| --- | --- |
| PyTorch (CUDA 13.0) | 2.14.0+cu130 |
| Transformers | 5.17.0 |
| Accelerate | 1.15.0 |
| safetensors | 0.8.0 |
| tokenizers | 0.23.2 |
| xgrammar | 0.2.8 |
| llguidance | 1.8.0 |
| Jinja2 | 3.1.6 |
| bitsandbytes (for the 4-bit NF4 precision) | 0.50.2 |

Create or recreate it from the lock; the Python download is the only
network access besides the packages:

```powershell
Set-Location "G:\Industry Dataset\ehr-evidence-app"
. .\scripts\setup-destinations.ps1 -Quiet
uv python install 3.12.13 --install-dir $env:UV_PYTHON_INSTALL_DIR --no-bin --no-registry
$ModelBasePython = Join-Path $env:UV_PYTHON_INSTALL_DIR 'cpython-3.12.13-windows-x86_64-none\python.exe'
$ModelEnv = 'G:\Industry Dataset\ehr-evidence-runtime\environments\uv\ehr-evidence-llm'
uv venv $ModelEnv --python $ModelBasePython
uv pip sync --python (Join-Path $ModelEnv 'Scripts\python.exe') --require-hashes --strict --torch-backend cu130 --default-index https://pypi.org/simple requirements\llm.lock.txt
```

To change it, edit `requirements/llm.txt`, regenerate the lock and review
the diff:

```powershell
uv pip compile requirements\llm.txt --python-version 3.12.13 --python-platform x86_64-pc-windows-msvc --torch-backend cu130 --generate-hashes --default-index https://pypi.org/simple -o requirements\llm.lock.txt
```

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
