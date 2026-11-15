# Learning's training engine: downloads and usage reporting

Learning (preview) can train a personal model on this computer. Training runs in a separately
installed **training engine** (CUDA PyTorch, Unsloth, TRL, PEFT and their dependencies, plus
llama.cpp's own tools that export a trained version to a GGUF file), never in
Row-Bot's own environment, the `all` extra, `requirements.txt` or the installers. This page records,
for the AGENTS.md rule on third-party telemetry, what the engine's libraries would send on their
own, how Row-Bot switches each one off, and how that is tested. Row-Bot itself adds no telemetry.

## What is downloaded, and from where

Nothing downloads until the person agrees to the download ask ("Download the training engine and
Qwen3.5 2B?"), which shows the sizes, the free space, where it comes from and one line about usage
reporting:

> From PyPI, PyTorch, Hugging Face and GitHub (llama.cpp). The training engine's own usage reporting is switched off.

| What | From | Size | Pinned by |
|---|---|---|---|
| The engine's wheels (85 on Windows, 101 on Linux) | `files.pythonhosted.org`, `download.pytorch.org` (redirects to `download-r2.pytorch.org`) | 2.2 GB on Windows (CUDA 13.0), 3.0 GB on Linux | SHA-256 per wheel in `src/row_bot/learning_engine/uv.lock` |
| uv 0.12.22 (installs the wheels offline) | `files.pythonhosted.org` | about 20 MB | the same lock (`bootstrap` group) |
| CPython 3.12.15 (the engine's own interpreter) | uv's managed download (python-build-standalone) | about 30 MB | uv's own pinned hashes |
| llama.cpp b11669's CPU build (`llama-quantize` and its libraries) | `github.com` release assets (redirect to `objects.githubusercontent.com` or `release-assets.githubusercontent.com`) | 19.5 MB on Windows, 17.9 MB on Linux | SHA-256 and size in `learning/training/lockfile.py` (`LLAMA_CPP_TOOLS`) |
| llama.cpp b11669's source archive, of which only its converter (`convert_hf_to_gguf.py`, `conversion/`, `gguf-py/`) is unpacked | `github.com` (redirects to `codeload.github.com`) | 40 MB | SHA-256 and size in `lockfile.py` (`LLAMA_CPP_SOURCE`) |
| Qwen3.5 2B at revision `15852e8c…` | `huggingface.co` (redirects to its CDN, `*.hf.co`) | 4.6 GB | size and SHA-256 per file in `learning/training/weights.py` |

Every request goes through Row-Bot's safe download path (`integrations.safe.open_stream`): https
only, reviewed hosts only, checked addresses, no credentials sent, never a Hugging Face token. A
file is used only when its size and SHA-256 match its pin; uv checks every wheel's hash again.

## What each library would send, and how it is switched off

Read in the installed source of each version in the lock (L0d, 16 Oct 2026). The engine's worker
and its check set every switch below in the process environment before anything is imported
(`learning/training/worker.py` `TELEMETRY_ENV`, `learning_engine/entry.py` `SWITCHES`).

| Library | What it would send | Switched off by |
|---|---|---|
| Unsloth 2026.10.6 | `get_statistics()` on every model load: downloads of `unslothai/<environment>`, `unslothai/vram-<N>`, `unslothai/<GPU count>` from the Hub (the download counters reveal the environment, the memory bucket and the GPU count), after a socket to `8.8.8.8:53` | `UNSLOTH_DISABLE_STATISTICS=1`, `HF_HUB_OFFLINE=1`, models loaded from a local folder |
| Unsloth | a refresh of its model-name table from `raw.githubusercontent.com` | offline, local folders |
| Unsloth / unsloth_zoo | GGUF export clones and builds llama.cpp and runs `pip install`; `unsloth install-kernels` | `UNSLOTH_AUTO_INSTALL=0`; Row-Bot never calls those paths (it exports with llama.cpp's own tools) |
| llama.cpp's converter (`gguf-py`) and `llama-quantize` | nothing: they read the merged model from disk and write GGUF files (O26) | the converter runs in the engine's own `export` mode under the network guard; `llama-quantize` is a local process the export starts |
| Unsloth | turns on PyTorch's remote autotune cache (Redis), used only if `redis` is installed | `redis` is not in the lock (a test checks) |
| TRL 1.15.0 | every trainer sends `trl/SFTTrainer` usage (model architecture, PEFT, dataset source, device, **GPU name**) to `huggingface.co/api/telemetry` | `HF_HUB_DISABLE_TELEMETRY=1`, `DO_NOT_TRACK=1`, `DISABLE_TELEMETRY=1`, `HF_HUB_OFFLINE=1`, and `report_to="none"` on every trainer |
| huggingface_hub | usage telemetry, an update check, a stored token attached to requests | `HF_HUB_DISABLE_TELEMETRY=1`, `HF_HUB_DISABLE_UPDATE_CHECK=1`, `HF_HUB_DISABLE_IMPLICIT_TOKEN=1`, `HF_HUB_OFFLINE=1`; its Rust downloaders (`hf_transfer`, `hf_xet`) never run offline (`HF_HUB_ENABLE_HF_TRANSFER=0`) |
| transformers 5.17.0 | Hub requests (a user agent saying telemetry is off when off); compiled kernels from the Hub if `kernels` is installed | `TRANSFORMERS_OFFLINE=1`, `USE_HUB_KERNELS=NO`; `kernels` is not in the lock |
| datasets | Hub access for named datasets | `HF_DATASETS_OFFLINE=1`; Row-Bot builds datasets from its own files |
| wandb, trackio, comet, mlflow | experiment tracking | none is in the lock (a test checks); `WANDB_MODE=disabled`, `WANDB_DISABLED=true`, `TRACKIO_DISABLED=1`, `COMET_MODE=DISABLED`, `report_to="none"` |
| xformers | nothing, but its PyPI build is for another torch | left out of the lock (an override) |
| accelerate, peft, bitsandbytes, torchao, torch, triton-windows | nothing found (local subprocesses only: `ptxas`, `tcc`, `nvidia-smi`) | — |

`opentelemetry-api` appears in the lock only as a dependency of macOS-only packages (mlx-vlm via
FastAPI); it is never installed on Windows or Linux, and the API alone sends nothing.

## Belt and braces: the engine's network guard

Training never needs the network once the files are on disk. Every engine process (`entry.py`
`check`, `train`, `fit`, `export`) installs a Python audit hook before any library is imported that
refuses any connection, name lookup or datagram that isn't this computer and counts the refusals.
A library that ignored the switches fails instead of phoning home. The engine also gets a minimal
environment: no Row-Bot data folder, keys or settings, its own home and temp folders, and a
neutral user name.

## Serving a version

A trained version is registered in a runtime on this computer only: Ollama (`ollama create` from the
version's GGUF, on loopback; nothing leaves the computer) or a server the person starts (LM Studio,
llama.cpp's server with `--offline --no-webui --host 127.0.0.1`, vLLM), which Row-Bot reaches on
`127.0.0.1` only. Row-Bot's own checks (its readiness probe and one Agent turn with the calculator)
run against that local server.

## Training on Hugging Face

A person who chooses **Where to train: Hugging Face** trains in their **own** Hugging Face account; the model
then runs on their computer, as above. What leaves the computer, and when:

- **Sign in with Hugging Face** (OAuth, device code): Row-Bot registers itself once as a public client of
  Hugging Face (no secret), asks for a code with the scopes `openid profile jobs contribute-repos read-billing`,
  and the person authorizes on Hugging Face's own page. The tokens stay in the system keychain only; Sign out
  removes them.
- **The price list** (`GET https://huggingface.co/api/jobs/hardware`, no sign-in, cached for hours) once the
  person has chosen Hugging Face, for the quote.
- **The model's tokenizer** (its small pinned files, about 25 MB, never its weights), downloaded once and
  checked against its pins, to count each training row's tokens exactly before anything is uploaded.
- **A run, only after the person agrees to its quote** (the first run; later runs start in quiet hours only
  within the monthly cap they chose): a private dataset repository holding the training rows the run uses
  (the serving shape, the same rows a run here would use; never chat databases, memories, files or anything
  else) and Row-Bot's own engine files; a private model repository for the results; one job whose only
  secret is the person's token. Both repositories are deleted once the trained model is downloaded and
  checked, or when the run is cancelled or fails; Learning says so.
- **Hugging Face's own library** (`huggingface_hub`, part of Row-Bot's full install) makes these calls with
  `HF_HUB_DISABLE_TELEMETRY=1`, `HF_HUB_DISABLE_IMPLICIT_TOKEN=1` (it never uses a token found on the
  computer) and `DO_NOT_TRACK=1`. Inside the job the same switches as the engine's are set for every
  process, the training process runs under the engine's network guard (no network at all while it trains),
  and the job reports to Row-Bot only through its own log lines (stages, step counts, losses and times).

Row-Bot sends Hugging Face nothing else, and adds no telemetry of its own.

## How it is tested

- `tests/subsystem/learning/test_learning_engine_lock.py`: no build of the engine (CUDA 13.0 and
  12.8, Windows and Linux) installs wandb, kernels, redis, trackio, xformers, comet or mlflow, and
  the lock names none of them; the engine is not in Row-Bot's own lock, requirements or extras.
- `tests/subsystem/learning/test_learning_engine.py`: the engine's check sees every switch already
  set when the first library is imported; its network guard refuses a library's lookup and
  connection to the Hub; uv runs with no configuration, no token and Row-Bot's isolated
  environment.
- `tests/subsystem/learning/test_learning_training_worker.py` (L2a): the worker's environment.
- `tests/e2e/learning/test_learning_engine_e2e.py` (opt-in): the real engine installed and checked
  with the process tree's connections sampled: zero connections that weren't this computer.
- `tests/subsystem/learning/test_learning_export.py` and `test_learning_serving.py` (L2c): the export
  runs in the engine's clean environment with no Row-Bot secrets; Ollama gets no keys; servers are
  asked for their models only on this computer.
- `tests/e2e/learning/test_learning_personal_model_e2e.py` (opt-in, L2c): real training, export and
  serving with the process tree's connections sampled: none that weren't this computer.
- `tests/subsystem/learning/test_learning_hugging_face.py`, `test_learning_hugging_face_api.py` and
  `test_learning_hugging_face_auth.py` (C1, a fake Hub): only the rows and the engine's files are uploaded,
  to a private dataset; the token is a job secret only and never in a file, log or snapshot; every OAuth
  request goes to `huggingface.co`; the uploads are deleted after; a submission without an answer is found
  by its label and never sent twice.
