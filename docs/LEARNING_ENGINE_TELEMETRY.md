# Learning's training engine: downloads and usage reporting

Learning (preview) can train a personal model on this computer. Training runs in a separately
installed **training engine** (CUDA PyTorch, Unsloth, TRL, PEFT and their dependencies, plus
llama.cpp's own tools that export a trained version to a GGUF file, and llama.cpp's server that
runs personal models), never in
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
| llama.cpp b11669's CUDA build (`llama-server`) and the CUDA runtime it needs, for Row-Bot's own model server (CUDA 13.4 with a driver of 580 or newer; 12.4 on Windows and 12.8 on Linux with 570-579) | `github.com` release assets | 578 MB on Windows (CUDA 13.4), 594 MB on Linux; 658 and 767 MB for CUDA 12 | SHA-256 and size in `lockfile.py` (`LLAMA_CPP_SERVER`) |
| The starting model the person chose: Qwen3.5 2B (`15852e8c…`), 9B (`c2022362…`) or Qwen3.8 27B (`1d4bf0f2…`), as the card's tier allows; the 27B also downloads Unsloth's pre-quantized 4-bit checkpoint it trains from (`unsloth/Qwen3.8-27B-unsloth-bnb-4bit`, `8aa5f05d…`, with Qwen's own tokenizer and chat template) | `huggingface.co` (redirects to its CDN, `*.hf.co`) | 4.6, 19.3 or 78.0 GB (55.6 + 22.4) | size and SHA-256 per file in `learning/training/weights.py` |

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

A trained version runs on this computer only. By default it runs in **Row-Bot's own model server**
(`learning/training/llama_server.py`, O75): llama.cpp's `llama-server` from the download above,
which Row-Bot starts when a request needs one of its models and stops when they are idle. It is
started with:

- `--host 127.0.0.1` and a free port: nothing outside this computer can reach it;
- `--offline`: it never downloads (no `-hf`, `-mu` or model URLs are ever passed), and its model
  cache is an empty folder of its own (`LLAMA_CACHE`), so it lists only the files Row-Bot gives it;
- `--no-webui`, and none of its own tools, agent or MCP proxy (`--tools`, `--agent`,
  `--mcp-servers-*`, `--ui-mcp-proxy` are off by default and never passed);
- an API key Row-Bot makes at each start, in its environment only (never on its command line or on
  disk); the model processes it starts inherit it, and every request without it is refused;
- an environment with nothing of the person's: no keys, no `LLAMA_*` settings that could switch its
  tools or downloads on, its own home and temp folders.

llama.cpp's server has no telemetry or update check of its own; its only network use is the
downloads and the web page's proxy above, none of which is switched on. Measured on an RTX 5090 (L2x): while it
served two personal models through Row-Bot's own checks (its probe and an Agent turn each), its
process tree (the server and the model processes it started, six in all) listened only on
`127.0.0.1` and had no connection to anything but this computer (469 sockets seen in 223 samples,
one every 0.2 s).

Otherwise a version is registered in Ollama (`ollama create` from the version's GGUF, on loopback;
nothing leaves the computer) or in a server the person starts (LM Studio, llama.cpp's server with
`--offline --no-webui --host 127.0.0.1`, vLLM), which Row-Bot reaches on `127.0.0.1` only. Row-Bot's
own checks (its readiness probe and one Agent turn with the calculator) run against that local
server.

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
- `tests/subsystem/learning/test_learning_llama_server.py` (L2x): Row-Bot's own model server starts
  on loopback with `--offline --no-webui` and none of its tools, its key only in its environment and
  refused without it, nothing of the person's environment; it and its model processes end on Stop, on
  quit and after a crash. `test_learning_server_files.py`: its pinned download.
