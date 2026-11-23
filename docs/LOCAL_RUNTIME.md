# On this computer: local runtimes, and Row-Bot's own llama.cpp download

Row-Bot runs models on this computer through any local runtime, with equal weight: Ollama, LM
Studio, vLLM, a llama.cpp server of the person's own, and **Download with Row-Bot (llama.cpp)**
(E1), for a person with none of them and no terminal. Setup's **On this computer** first lists the
model servers already running here, then every way as an equal row; Settings › Models › Local lists
the connected ones the same way.

**Finding servers already running.** Only when that Setup page opens (or the person chooses Check
again), Row-Bot asks this computer, 127.0.0.1 only, on each runtime's usual port with its standard
model list and a 0.8 s timeout: Ollama 11434 (`/api/tags`), LM Studio 1234, vLLM 8000, llama.cpp
8080 and 8081 (`/v1/models`). Row-Bot's own port is never asked (its default is 8080). Nothing is
polled in the background and nothing goes anywhere else. **Use** connects a found server: Ollama
through its own provider, the others as a local custom endpoint with that runtime's profile
(`application/client_local_servers.py`).

**Row-Bot's own download** fetches two things, and only after the person presses a button that
names the size:

1. one **official llama.cpp release build** for this computer (`llama-server` and its libraries);
2. one **model file** (GGUF) that fits this computer: **Qwen3.8 27B** at the quantization that fits
   the graphics card, or on a smaller computer **Qwen3.5 9B or 4B** (the generation before).

Neither ships in the installers, the update parts, `requirements.txt` or Row-Bot's Python
environment. This page is the review the AGENTS.md rule asks for a downloaded third-party program:
licence, what it does on the network, the flags Row-Bot runs it with, what is stored where, and how
to remove it. The code is `src/row_bot/local_runtime/`.

## Licences

| What | Licence | Note |
|---|---|---|
| llama.cpp (ggml-org) | MIT | Each archive carries its `LICENSE` file; Row-Bot keeps it in the build folder. |
| NVIDIA CUDA runtime (`cudart-llama-*` archives: `cudart`, `cublas`, `cublasLt`) | NVIDIA CUDA Toolkit EULA | The EULA's Attachment A lists these runtime libraries as redistributable with an application. ggml-org publishes them beside its CUDA builds; Row-Bot downloads them from that release only for a CUDA build, never bundles them. |
| Qwen3.8 27B, Qwen3.5 9B and 4B (Alibaba Qwen) | Apache-2.0 | Each model's Hugging Face card is tagged `license:apache-2.0` (`Qwen/Qwen3.8-27B` at `37561816…`). |
| The GGUF conversions (Unsloth) | Apache-2.0 (the model's) | Quantised copies of the Qwen weights; the repository is tagged `license:apache-2.0`. |

## The pinned release

**llama.cpp b12359**, the nightly build that ggml-org's first stable release, **v1.0.0**
(13 Nov 2026), names as its assets ("Nightly build: b12359" in the v1.0.0 notes; v1.0.0 itself
carries only `nightly-tag.txt`, whose content is `b12359`). Commit
`b1b770d90d94cad9e9ea5b993bdbc383a522fb28`.

The sizes and SHA-256 values below are GitHub's own asset digests, read from
`GET https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/b12359` on 16 Nov 2026 (the
`digest` field of each asset). They are pinned in `local_runtime/builds.py`; a file whose size or
SHA-256 differs is deleted and never used.

| Computer | Build | Asset | Bytes | SHA-256 |
|---|---|---|---|---|
| Windows x64 | CUDA 13 | `llama-b12359-bin-win-cuda-13.5-x64.zip` | 179,546,112 | `1de9ca1ff408d6e348ea0007c6880ad2393311a02e7799bcde26dc16daef03dd` |
| | + its CUDA runtime | `cudart-llama-bin-win-cuda-13.5-x64.zip` | 426,314,199 | `c2bf4cc202bfbaaa7a0b75f5082457c3811326947bb216059b72839d844e9cba` |
| Windows x64 | CUDA 12 | `llama-b12359-bin-win-cuda-12.4-x64.zip` | 318,976,332 | `e9c0abf442e8339c06728366c1ad2c711332f9939c0c00603fb75d85fd44c914` |
| | + its CUDA runtime | `cudart-llama-bin-win-cuda-12.4-x64.zip` | 391,443,627 | `8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6` |
| Windows x64 | Vulkan | `llama-b12359-bin-win-vulkan-x64.zip` | 38,090,643 | `e8d18c6e4210cca87e7307c2ea78363ac8d158e4e1397358ec96ab70b53a0d5e` |
| Windows x64 | CPU | `llama-b12359-bin-win-cpu-x64.zip` | 25,097,509 | `837c1655e87df3b66989f4be33a792a1399c785bf259038dc75c900096683e69` |
| macOS Apple silicon | Metal | `llama-b12359-bin-macos-arm64.tar.gz` | 16,907,811 | `82b00abc740160e1b9980e6cc79c117345fa34af058c80e6c0a69aa8ddccde75` |
| Linux x64 | CUDA 13 | `llama-b12359-bin-ubuntu-cuda-13.5-x64.tar.gz` | 178,419,847 | `9440f417615bed0e191acef03bff0249117d2be74335358265a6d28475a9baf0` |
| | + its CUDA runtime | `cudart-llama-b12359-bin-ubuntu-cuda-13.5-x64.tar.gz` | 444,078,822 | `976a9146bb8a487a6ea701224d7fb14b05e494ab3c8f5c1799ffc4e5805d640c` |
| Linux x64 | CUDA 12 | `llama-b12359-bin-ubuntu-cuda-12.8-x64.tar.gz` | 199,223,370 | `50c937bec5bbd9f74c99138034f7403283ee21f1943f2429d7b1e4c079eafc11` |
| | + its CUDA runtime | `cudart-llama-b12359-bin-ubuntu-cuda-12.8-x64.tar.gz` | 594,371,571 | `75a12d227f44a391608992329241765343bd143ee9445b90dc62e154ee726e5d` |
| Linux x64 | Vulkan | `llama-b12359-bin-ubuntu-vulkan-x64.tar.gz` | 36,153,377 | `19b60f00395a3f4954e46a5037695bc1d843404fc9d3124f82a612ecd8fbe9f0` |
| Linux x64 | CPU | `llama-b12359-bin-ubuntu-x64.tar.gz` | 23,311,084 | `0bc1f091e8916102b6d8f2cf944233a620e90a0f856c72d0ce41150c65c2e72e` |

Each asset comes from `https://github.com/ggml-org/llama.cpp/releases/download/b12359/<asset>`.
The archive layouts were read from ggml-org's `release.yml` and `release-publish.yml` at that tag
(Windows zips hold the files at their root; macOS and Linux tarballs hold one `llama-b12359/`
folder; the CUDA runtime archives are unpacked next to the binaries).

Not offered yet: Intel Macs (E9), Windows and Linux on Arm, ROCm/HIP (Vulkan covers most AMD
cards). Such a computer is told plainly that it can't run a model here and is offered providers.

**Which build.** Chosen automatically from what this computer has (`local_runtime/hardware.py`):

- an NVIDIA card whose driver is 580 or newer: CUDA 13; 525 to 579: CUDA 12 (NVIDIA's CUDA
  minor-version compatibility table: CUDA 13.x needs driver ≥ 580, CUDA 12.x ≥ 525); older: Vulkan;
- Apple silicon: Metal;
- an AMD or Intel graphics card with its own memory: Vulkan;
- otherwise: CPU.

## The pinned models

**Qwen3.8 27B** (the current generation; its smallest size is 27B; tool use, thinking, Apache-2.0)
from Unsloth's Dynamic GGUFs, repository `unsloth/Qwen3.8-27B-GGUF` at revision
`4af51b3542f775b0a8e4b310bfb3f17e01c306c9` (12 Nov 2026, after Unsloth's Dynamic v3.0 repack), in
three quantizations; and two smaller models of the generation before, **Qwen3.5 9B and 4B**
(Q4_K_M), offered only where the 27B doesn't fit and labelled "a smaller model for this computer
(older generation)". Sizes and SHA-256 are each repository's own LFS records, read from
`GET https://huggingface.co/api/models/<repo>?blobs=true` on 16 Nov 2026. Pinned in
`local_runtime/models.py`.

| Id | Repository @ revision | File | Bytes | SHA-256 |
|---|---|---|---|---|
| `qwen3.8-27b-q4` | `unsloth/Qwen3.8-27B-GGUF` @ `4af51b3542f775b0a8e4b310bfb3f17e01c306c9` | `Qwen3.8-27B-UD-Q4_K_M.gguf` | 16,564,259,744 | `1bce504dfecd84153666928a41d8a66706f0158d3a317bc5196b72819d7dc529` |
| `qwen3.8-27b-q3` | same | `Qwen3.8-27B-UD-Q3_K_XL.gguf` | 13,146,393,504 | `7b20f0679da6f5c6ec7971e241630f44fe1e123e5c99bfea90c433268b355723` |
| `qwen3.8-27b-q2` | same | `Qwen3.8-27B-UD-Q2_K_XL.gguf` | 9,948,666,784 | `18f1187f38e7994b05c08cb45c674b90b7f17523687c83a4614fe493f7ea739d` |
| `qwen3.5-9b` | `unsloth/Qwen3.5-9B-GGUF` @ `3885219b6810b007914f3a7950a8d1b469d598a5` | `Qwen3.5-9B-Q4_K_M.gguf` | 5,680,522,464 | `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8` |
| `qwen3.5-4b` | `unsloth/Qwen3.5-4B-GGUF` @ `e87f176479d0855a907a41277aca2f8ee7a09523` | `Qwen3.5-4B-Q4_K_M.gguf` | 2,740,937,888 | `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4` |

Each file comes from `https://huggingface.co/<repo>/resolve/<revision>/<file>` (which redirects
to Hugging Face's storage, `*.hf.co`). Qwen3.8 27B has the same architecture as Qwen3.5
(`qwen3_5_text`: 64 layers, 16 of them full attention), which llama.cpp b12359 runs.

**Which model.** The best that fits the graphics card's memory (the card less 512 MiB the system keeps;
Apple silicon: about 70 % of the computer's memory, which the card shares), in this order: Qwen3.8 27B
4-bit, 3-bit, 2-bit, then Qwen3.5 9B, 4B, picked at a 32K window. A model needs its file, its context
cache and working memory (`Model.need_bytes`):

- **The context cache** is 8-bit (`q8_0`, 34 bytes per 32 values) for every model: per token, the
  full-attention layers (16 for the 27B, 8 for Qwen3.5 9B and 4B) x 2 x 4 key-value heads x head size
  256, so 34,816 bytes for the 27B and 17,408 for the small ones. llama.cpp's own KV buffer lines match
  it exactly (4,352 MiB for the 27B at 128K).
- **Compute buffers** grow by 6,144 bytes a token: 5,120 on the card and 1,024 in host memory, both
  counted, since a Mac's memory is one (Qwen3.5 9B on Metal: 305, 497 and 881 MiB at 32K, 64K and 128K).
- **A fixed part**, 256 MiB per model and 768 MiB for Qwen3.5 4B. It covers the runtime itself and the
  recurrent state, less the embedding table llama.cpp keeps in host memory (682 MiB for the 27B, so
  its file overstates the card). Qwen3.5 4B's output layer is its embedding table, held in both places,
  so its weights come to 487 MiB more than its file.

These are fitted to llama.cpp b12359's buffer lines and the memory measured on an RTX 5090 and a 16 GB
M4 Mac mini (below). Every measured case is predicted at or above what it used, by 170 MiB or more.

So a 20 GB or larger card gets the 4-bit build (about 18.2 GB loaded at 32K), 16 GB the 3-bit
(14.8 GB), 12 GB the 2-bit (11.6 GB), 8 GB Qwen3.5 9B and 6 GB Qwen3.5 4B. A Mac shares that memory with macOS and its apps, so a 27B build
is recommended only on a Mac with room to spare: 24 GB gets the 2-bit, 32 GB the 3-bit, 48 GB or more the
4-bit, and a 16 GB Mac Qwen3.5 9B (a 27B build that fits can still be chosen). Without a card that fits one, a computer with 32 GB of memory is offered Qwen3.5 9B on the
processor and one with 16 GB Qwen3.5 4B; with less, none is recommended, but every model that fits
can still be chosen.

**The window it reads** (D1, `models.context_for`). Once the model is picked, it is served at the largest of
128K, 64K and 32K tokens at which it still runs entirely on the card with llama.cpp's own `--fit` margin
(1024 MiB of each card kept free) to spare, never above its native 262K. The pick comes first, so a larger
window never costs a quality level. A model that runs partly on the card or on the processor stays at
32K (a longer prompt there makes the first word too slow), and so does every model on a Mac (below). With
the published sizes:

| Card | Model | Window | Loaded |
|---|---|---|---|
| 24 GB or more | Qwen3.8 27B 4-bit | 128K | 20.7 GiB |
| 20 GB | Qwen3.8 27B 4-bit | 64K | 18.2 GiB |
| 16 GB | Qwen3.8 27B 3-bit | 32K | 13.7 GiB (64K would need 15.0 GiB plus the margin) |
| 12 GB | Qwen3.8 27B 2-bit | 32K | 10.8 GiB |
| 10 GB | Qwen3.5 9B | 128K | 8.4 GiB |
| 8 GB | Qwen3.5 9B | 32K | 6.3 GiB |
| 6 GB | Qwen3.5 4B | 32K | 4.0 GiB (64K would need 4.7 GiB plus the margin) |
| none | Qwen3.5 9B or 4B on the processor | 32K | |
| Mac, any memory | the model picked for it | 32K | |

**A Mac reads 32K**, whatever its memory holds, because its first word comes too slowly at more. On an M4 Mac
mini (16 GB, b12359 Metal), Qwen3.5 9B read a prompt at about 200 tokens a second, falling to 101 at 110K:

| Prompt | First word, cold |
|---|---|
| 12K | 58 s |
| 27K | 148 s |
| 60K | 407 s |
| 110K | 1,095 s, past the 600 s silence limit; 128K went into swap |

A follow-up turn's first word took about 0.5 s from the prompt cache. Pro and Max chips get measured before
a larger window is automatic on a Mac; a larger window will be the person's own choice (U1).

**On an RTX 5090** (32 GB, b12359 CUDA 13) every case loaded all its layers on the card, and the larger
windows are quick. A cold 110K prompt's first word took 49 s on the 27B 4-bit and 17 s on Qwen3.5 9B; the
next turn's took 0.15 to 0.27 s from the prompt cache. Memory measured vs predicted, in GiB:

| Model | 128K | 64K | 32K |
|---|---|---|---|
| Qwen3.8 27B 4-bit | 20.05 vs 20.68 | 17.63 vs 18.18 | 16.41 vs 16.93 |
| Qwen3.5 9B | 8.13 vs 8.42 | 6.75 vs 6.98 | 6.06 vs 6.26 |
| Qwen3.5 4B | | 4.54 vs 4.74 | 3.85 vs 4.02 |

Each model on the endpoint has its own window: on a 16 GB card a downloaded
Qwen3.5 4B reads 128K beside the 3-bit's 32K. Setup and Settings › Models › Local say it ("reads 64K").
The presets file, the endpoint (`save_endpoint`, one window per model and none for the whole endpoint),
`fit` and Setup all take it from `context_for`, with this computer's hardware as read once per run
(Repair reads it again).

**Thinking.** Every request says whether the model thinks (`chat_template_kwargs.enable_thinking`),
rather than leaving it to the template. Every model thinks for now, as its template does by default;
Qwen3.5 9B and 4B are to answer straight away instead once a local measurement confirms that small
models do better that way in a 32K window. A chat's
Thinking choice (On or Off, also `/reasoning on|off`) overrides that for its own requests
(`Model.thinks` in `local_runtime/models.py`).

## Network behaviour

**Row-Bot's downloads.** Only after the person presses Download (the button names the size, and
the free disk space is checked first). Only the pinned URLs above, through Row-Bot's one safe
download path (`integrations.safe.open_stream`): https only, reviewed hosts only (`github.com`,
`objects.githubusercontent.com`, `release-assets.githubusercontent.com`, `huggingface.co`,
`*.huggingface.co`, `*.hf.co`), checked addresses, redirects checked again, no credentials and
never a Hugging Face token. Downloads resume after a stop, a dropped connection or a restart.
Nothing checks for updates: a newer llama.cpp or model arrives only when a Row-Bot release moves
the pins, and then Settings › Models offers "Update (size)" and keeps working until it is pressed.

**llama.cpp itself.** llama.cpp has no telemetry, usage reporting or update check. Its only
network features are model downloads (`-hf`, `--docker-repo`, the web page's Discover), the web
page's MCP proxy, and its built-in tools and agent mode. Row-Bot never passes any of them and adds
flags that keep them off:

| Flag | Why |
|---|---|
| `--host 127.0.0.1` | Listens on this computer only (loopback). |
| `--port <free port>` | A free loopback port the system picks, never Row-Bot's own or a reserved one. |
| `--offline` | "Prevents network access": no Hugging Face downloads even if asked. |
| `--no-webui` | No web page, so no Discover downloads and no MCP proxy. |
| `--cors-origins localhost` | A web page in the person's browser can't call it. |
| `--models-preset <file>` `--models-max 1` | Router mode: the downloaded model files by their catalog id, from a presets file Row-Bot writes (each model's `model = <file>` and `ctx-size` = its window above, and the settings below), one loaded at a time; nothing is looked up by name online. |
| `LLAMA_API_KEY` (environment) | A key made at each start, in the process environment only (never on its command line or on disk), so other programs on this computer can't use the server. |

The presets file's shared settings (each is a llama-server argument of b12359; the file and line are in
`tools/server/README.md` and `common/arg.cpp` at that tag):

| Setting | Why |
|---|---|
| `jinja = true` | The model's own chat template, so its tool calls parse. |
| `parallel = 1` | One request at a time. |
| `flash-attn = on` | Flash attention (`-fa`, `common/arg.cpp:1726`); an 8-bit cache needs it (`src/llama-context.cpp:4541`). |
| `cache-type-k = q8_0`, `cache-type-v = q8_0` | The context cache in 8-bit for every model (`common/arg.cpp:2409,2422`): half of the 16-bit default, which the memory sums above count. |
| `cache-ram = 2048` | The prompt cache in the computer's memory capped at 2 GiB per loaded model, as Learning's server keeps it; llama.cpp's default is 8 GiB (`common/arg.cpp:1687`, `common/common.h:657`). |

Left at llama.cpp's defaults, on purpose: `--fit on` with `-ngl auto` (`common/common.h:494,497`) puts as
many layers on the card as fit and the rest on the processor, so a model that half-fits uses the card;
setting `-ngl` would switch that off (`common/fit.cpp:550`). Prompt caching is on by default
(`--cache-prompt`, `common/common.h:652`). `--cache-reuse` is not passed: it shifts the cache, which
Qwen3.5's architecture (multi-section positions, `src/llama-kv-cache.cpp:1245`) can't, so the server would
turn it off (`tools/server/server-context.cpp:1420`).

Its environment is built from nothing: no keys, no `HF_TOKEN`, no `LLAMA_ARG_*` settings of the
person's. `--tools`, `--agent`, `--mcp-servers-*`, `--webui-mcp-proxy`, `-hf` and `--docker-repo`
are never passed.

## When it runs

`llama-server` starts only when a chat (or the Setup test) asks for one of its models, and stops
after 30 minutes without a request and with nothing being answered, so the graphics card's memory
goes back (30 minutes is the `keep_alive` Row-Bot sends Ollama). The prompt cache lives in the server, so
a shorter stop would make any reply after a pause read the whole conversation again: two and a half
minutes for a 32K prompt on a 16 GB Mac. Row-Bot stops the process itself rather than passing llama.cpp's
`--sleep-idle-seconds`: sleeping unloads the model but keeps the process, its port and its key, while
Learning's `release_gpu` and the "running" state need it gone. One model is loaded at a time. Each start
writes the presets file and the endpoint again from the windows above, so a new card counts from the
next start after Repair or a restart of Row-Bot; Repair stops a running server whose windows changed. When a model finishes downloading,
Row-Bot checks it once on this computer with the probe it runs for any endpoint (a reply, a tool
call and a tool-result round trip), which loads it once. On Windows a kill-on-close job object holds it, so Row-Bot quitting or crashing ends it; on
macOS and Linux it runs in its own process group and is recorded (`owned_processes`), so a later
Row-Bot stops one a crash left behind. Learning's training can ask for the card
(`local_runtime.server.release_gpu()`: it stops the server at once unless a request is being
answered); a chat that is answering is never interrupted. While training holds the card, background
work doesn't start the server; the person's own chat does (Learning's lease then pauses training).

It is the custom endpoint `llamacpp` (provider id `custom_openai_llamacpp`, model refs such as
`model:custom_openai_llamacpp:qwen3.8-27b-q2`), saved with `execution_location: local` and a
loopback address (kept while the server is stopped), so every "is this model local?" check treats it
as a model on this computer, Learning's `spending.is_local_provider` included, and so do the prompt
tiers: up to 128K it gets the local middle tier, as Ollama's models do (`models.is_local_custom_endpoint`,
which counts any custom endpoint saved as local except Learning's personal models). It is still
reached over the OpenAI-compatible transport, and Settings' context control still treats its window
as the server's.

A caller outside chat that needs the server running, such as Learning's evaluation worker (another
process), calls `local_runtime.server.ensure_started(model_ref)`: it starts the server if needed,
waits for its health check and returns the loopback `base_url`; `ensure_connection(model_ref)` also
gives the key requests need. Both are idempotent. As background work they raise `card_busy` while
Learning's training holds the card, unless the caller passes `while_training=True` (Learning's own
evaluation inside its lease).

## What is stored where

All under Row-Bot's data folder, never in the app or its installer folder:

| Path | What |
|---|---|
| `runtimes/llamacpp/b12359-<build>/` | The unpacked build and `installed.json` (written last, after the binary answered `--version` here). |
| `runtimes/llamacpp/downloads/` | Partial downloads (`*.part`) while a download runs or is stopped. |
| `local_models/<id>/` | The model file and `model.json` (source, revision, licence). |
| `runtime/local-runtime-server.json` | The running server's process record (for crash cleanup). |

`runtimes` and `local_models` are left out of backups (gigabytes that can be downloaded again).

## Removing it

- **In Row-Bot:** Settings › Models › Local › Download with Row-Bot › Remove (the trash icon) stops the server and
  deletes the build, its partial downloads and every model, and says how much space it freed. One
  model can be removed on its own from the same row.
- **By hand:** quit Row-Bot, then delete `runtimes/llamacpp/` and `local_models/` in the data
  folder. Uninstalling Row-Bot leaves the data folder (by design), so these stay until deleted.

## Antivirus and App Control

A freshly downloaded `llama-server.exe` can be held by an antivirus scan or blocked by App Control.
Row-Bot runs `llama-server --version` before it publishes a build; if that fails, the install stops
with a plain line ("This computer blocked the downloaded program…") and nothing half-installed is
kept.
