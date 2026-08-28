# Local Qwen3 probe setup

M7C-A is a validation next-action feasibility check, not a live agent or a training run. It feeds
each model the correct scripted history before one action, parses the prediction, and compares it
with the registered action. The predicted action is never passed to a Workspace, Incident, or
DataOps tool.

Use a separate model environment. Python 3.11 is preferred, but it was not installed on the
reviewed laptop; the recorded smoke therefore used the Python 3.12.13 fallback. Torch and
Transformers are deliberately absent from the core package. The reviewed dependency stack is
PyTorch 2.7.1 with the official CUDA 12.6 wheel and Transformers 4.53.3.

The branch below is executable rather than silently claiming that 3.11 was used. Set
`M7CA_PYTHON312` to a Python 3.12.13 executable only when the preferred launcher check fails:

```powershell
$modelVenv = Join-Path ([System.IO.Path]::GetTempPath()) "all-m7ca-model-venv"
py -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11)"
if ($LASTEXITCODE -eq 0) {
    py -3.11 -m venv $modelVenv
} else {
    if ([string]::IsNullOrWhiteSpace($env:M7CA_PYTHON312)) {
        throw "Set M7CA_PYTHON312 to a Python 3.12.13 executable."
    }
    & $env:M7CA_PYTHON312 -c "import sys; assert sys.version_info[:3] == (3, 12, 13)"
    if ($LASTEXITCODE -ne 0) { throw "Python 3.12.13 fallback identity failed." }
    & $env:M7CA_PYTHON312 -m venv $modelVenv
}
if ($LASTEXITCODE -ne 0) { throw "Model environment creation failed." }
$modelPython = Join-Path $modelVenv "Scripts/python.exe"
& $modelPython -m pip install --upgrade pip
& $modelPython -m pip install -r requirements-model-probe.txt
& $modelPython -m pip install --no-deps dist/agent_learning_loop-0.1.0.dev0-py3-none-any.whl
```

The requirements file uses the CUDA 12.6 PyTorch index and pins both direct dependencies. Do not
replace this with a CPU wheel, quantized model, device map, or offload path to make a failed run
appear successful.

## Locked snapshots

Put both Hugging Face caches and probe output in the system temporary directory. These exact
revisions and their Apache-2.0 `LICENSE` file are part of the contract:

```powershell
$env:HF_HOME = Join-Path ([System.IO.Path]::GetTempPath()) "all-m7ca-hf-cache"
$env:HF_XET_CACHE = Join-Path ([System.IO.Path]::GetTempPath()) "all-m7ca-xet-cache"
$env:HF_HUB_DISABLE_TELEMETRY = "1"

& $modelPython -c "from huggingface_hub import snapshot_download; print(snapshot_download('Qwen/Qwen3-0.6B', revision='c1899de289a04d12100db370d81485cdf75e47ca', cache_dir=r'$env:HF_HOME'))"
& $modelPython -c "from huggingface_hub import snapshot_download; print(snapshot_download('Qwen/Qwen3-1.7B', revision='70d244cc86ccca08cf5af4e1e306ecf908b1ad5e', cache_dir=r'$env:HF_HOME'))"

$downloadBytes = (Get-ChildItem -LiteralPath $env:HF_HOME -File -Recurse | Measure-Object Length -Sum).Sum
if ($downloadBytes -gt 8GB) { throw "M7C-A download cap exceeded" }
```

`snapshot_download` returns a `snapshots/<40-hex-revision>` directory. `run-model-probe` rejects a
different directory name, license hash, model ID, revision, chat-template hash, generation config,
seed, non-CUDA device, or input above 4,096 tokens. Inference sets Hugging Face and Transformers to
offline mode before loading local files.

## Validation-only smoke

First create the six-task validation source Eval in a temporary directory. This selection contains
two validation tasks per environment; it does not run or copy test-task instructions, actions,
results, or scope.

```powershell
$sourceEval = Join-Path ([System.IO.Path]::GetTempPath()) "all-m7ca-source-eval"
& $modelPython -m agent_learning_loop run-eval `
  --suite system-correctness `
  --split validation `
  --source-commit 65d6c441f4e2be1e2dce3e363bc87f593aab221a `
  --output-dir $sourceEval
```

Run Qwen3-0.6B first in its own process. After that process exits, run Qwen3-1.7B so the first model
and CUDA allocations are gone. Replace each placeholder below only with the corresponding locked
snapshot path returned above:

```powershell
$smallBundle = Join-Path ([System.IO.Path]::GetTempPath()) "all-m7ca-qwen3-0.6b"
& $modelPython -m agent_learning_loop run-model-probe `
  --eval-bundle $sourceEval `
  --output-dir $smallBundle `
  --backend qwen3 `
  --model-id Qwen/Qwen3-0.6B `
  --snapshot-dir <locked-0.6B-snapshot>
& $modelPython -m agent_learning_loop validate-model-probe `
  --bundle $smallBundle --eval-bundle $sourceEval

$largeBundle = Join-Path ([System.IO.Path]::GetTempPath()) "all-m7ca-qwen3-1.7b"
& $modelPython -m agent_learning_loop run-model-probe `
  --eval-bundle $sourceEval `
  --output-dir $largeBundle `
  --backend qwen3 `
  --model-id Qwen/Qwen3-1.7B `
  --snapshot-dir <locked-1.7B-snapshot>
& $modelPython -m agent_learning_loop validate-model-probe `
  --bundle $largeBundle --eval-bundle $sourceEval
```

The fixed real smoke uses seed 17 and three pre-registered validation tasks:
`workspace.normalize-checklist`, `incident.isolate-inventory-config-change`, and
`dataops.atomic-parent-child-migration`. Qwen3-0.6B must produce a complete, normally validated
bundle even if its action accuracy is low. A real Qwen3-1.7B CUDA OOM may produce a validated
`capacity_blocked` bundle with zero actions; it must not silently fall back to CPU, quantization, or
offload. That status is valid only if OOM occurs before the first successful generation. OOM after
any generated prefix fails the run and removes the partial output instead of rewriting it as zero
actions.

Completed Qwen records are checked without importing model libraries: finish reason must agree
with token counts and the fixed generation limit, Torch must be exactly `2.7.1+cu126`,
Transformers must be exactly `4.53.3`, CUDA/runtime fields cannot use fake or CPU sentinels, and
VRAM counters must be internally possible. Capacity-only evidence also requires positive total
VRAM, `allocated <= reserved <= total`, and `free + reserved <= total`. These checks catch
contradictory self-reports; they are not a cryptographic hardware attestation.

Reference exclusion compares parsed JSON semantics, not one minified byte spelling. Canonical,
pretty-printed, and reordered action/tool-call objects that identify the current reference are all
rejected, while ordinary instruction prose is left alone.

Delete the temporary model environment, Hugging Face cache, source Eval, and probe bundles after
recording aggregate counts, versions, error categories, VRAM measurements, and directory hashes.
Raw prompts, generations, model weights, cache paths, and machine-specific paths are not project
artifacts.
