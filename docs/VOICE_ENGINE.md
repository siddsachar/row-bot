# Row-Bot Voice: the duplex voice engine

Row-Bot Voice is the default way to talk to Row-Bot: a streaming, full-duplex
conversation around the same agent as typed chat (same tools, memory,
approvals and transcript). This note is for developers and reviewers. The
person-facing docs are in `docs-site/docs/voice-and-buddy/`.

## How a turn flows

1. The chat's Talk button calls `POST /api/v1/conversations/{id}/voice/talk`.
   That takes the coordinator's exclusive voice lease (mode `talk`, transport
   `duplex`), freezes the chat's model, approval level and write targets exactly
   as typed messages do, and returns a single-use ticket (30 seconds).
2. The browser opens `WS /api/v1/voice/stream` on Row-Bot's own origin and sends
   `{"type": "hello", "ticket": ...}` first. The access middleware has already
   authenticated the socket, checked its origin, and closes it when the
   device's sign-in ends; the ticket binds it to the client session,
   conversation and lease, once. Every five seconds the socket re-checks the
   client session, the conversation and the lease.
3. The browser captures with `echoCancellation`, `noiseSuppression` and
   `autoGainControl`, and an AudioWorklet sends 20 ms frames of 16 kHz mono
   PCM. The host (`src/row_bot/voice/engine/`) runs Silero VAD on 32 ms windows
   and asks Smart Turn v3 at each 200 ms pause whether the thought is complete;
   the eagerness setting (When you pause) sets Smart Turn's threshold and the
   silence that ends a turn anyway.
4. The selected speech-to-text engine (a downloaded streaming model, or
   faster-whisper or SenseVoice, behind the `StreamingSpeechToText` seam in
   `voice/streaming_base.py`) writes the turn down. A streaming model's words
   so far are read every 100 ms of audio while the person speaks and sent as
   the bar's live caption; over Row-Bot's own voice they are evidence for a
   barge-in instead, never a caption. The transcript goes through
   `ClientVoiceAdmission`: a new turn when nothing runs, otherwise the
   active-run control (steer, follow-up, stop or status).
5. The run's token events reach the session through
   `ClientPlatformService.voice_listeners`. A sentence aggregator speaks the
   first clause early, never speaks code, and never caps the answer. Each
   piece is synthesised through the selected speech-output provider (Kokoro),
   so saved pronunciations apply, and sent down the socket as 24 kHz PCM.
6. Talking over Row-Bot ducks its voice at once. About 200 ms of sustained
   speech with words that are not a backchannel ("mhm", "okay") or its own
   voice heard back (or half a second of speech, when words are slower to make out) is a barge-in: the client flushes its
   playback, pending speech is cancelled and the rest of that answer stays on
   screen only.

## While it works (V3)

- **Spoken progress.** A run's `tool_call` events reach the session through the
  same listener. The first tool start of a burst (tool starts with no answer
  text between them) speaks one templated line from the tool's own name
  (`voice/engine/spoken.py`, "Checking your calendar…"; quick steps say
  nothing), only when nobody is talking, nothing is playing or queued, and
  nothing was said in the last 4 seconds. A tool still running 2 seconds in,
  with nothing said since the burst began, gets one "Still working on it…".
  No model call; the lines are never saved or sent to the model.
- **Status and follow-ups.** "How's it going?" during a run is answered from
  the run's current tool ("I'm still searching the web."); the run goes on.
  Other words steer it ("Got it, I'll do that next.").
- **Later answers.** A parent pass that runs in a later orchestration wake
  (helpers finished after the turn ended) calls
  `ClientPlatformService.voice_later_answer`; an open, listening session says
  it at its next pause. With no session open (or a paused one) nothing is said.
- **Approvals (VD4).** An `interrupt` becomes a bar notice with the card's own
  fields. The client words the title exactly as the card does and sends it
  back; the spoken summary says it, cleaned so no symbol is said (the tool's
  label after 1 s without one). A spoken yes or no counts only when it is a
  short plain answer, began (in audio time) after the summary finished playing
  and while Row-Bot was silent, and the approval system's classification lets
  voice answer it (`voice/approvals.py`: the "Allow in this chat" kinds, one
  action, never a command, an app's tool, the computer, money, a setting, a
  question or setup card). The host then checks that it is the conversation's
  only waiting approval and sends the card's own `approval.resolve` command.
  Everything else says "This one needs a tap on the screen."

Latency marks per turn (end of speech, turn end, transcript, admission, first
token, first sentence, first audio sent, first audio played) are kept as
durations only; the bar's timing line and `row_bot.voice.engine.latency`
show them.

## Sharing the camera or screen

The voice bar can share a camera (phones switch front and back) or, where
`getDisplayMedia` exists, a screen or window. The browser captures nothing
before the person shares and never streams video to the host. `share`
(`camera`, `screen` or null) tells the session; while it lasts, the end of
every spoken turn sends `look`, and the browser answers with one still, scaled
to at most 768 px on its long side, as JPEG: a `still` message with its size,
then the still as the next binary message. The host checks it
(`voice/engine/stills.py`: JPEG or PNG by its bytes, at most 1 MiB and 768 px),
waits up to 1.5 s for it alongside speech-to-text, and attaches it to that
turn only: `ClientVoiceAdmission.submit` registers it like a file the person
attached ("Live camera.jpg" or "Shared screen.jpg") and submits its reference,
so the chat shows it in the person's message and the agent reads it through
the normal image path (the Vision model describes it). A still lives only in
the session's memory until then; stopping sharing, a pause and the end of the
session drop it. A turn that steers a running answer carries words only. When
the frozen model is known not to see images (`workspace_setup.sees_images`),
`ready` says `sees_images: false`, the bar says so and the host refuses `share`.
Sharing is independent of mute.

Live voice: Gemini Live takes images in its session, so its relay shows it the
stills (`realtimeInput.video`), which the browser sends about every two seconds
while sharing and the host forwards at most once a second. OpenAI Realtime
(its images would go over the browser's data channel, which needs its own
size handling) and Grok Voice (no image input) offer no sharing.

## Pipecat review outcome (22 Nov 2026)

Row-Bot depends on `pipecat-ai>=1.16,<1.17` in the `voice` extra, core only:
not `[webrtc]`, not `opencv-python-headless`, and no aiortc. Only
`row_bot/voice/engine/pipecat_boundary.py` imports it, and only when a voice
session starts, so app start-up never loads it.

- **No telemetry by default.** Pipecat's core makes no network calls.
  Tracing and Sentry are opt-in and their SDKs are not installed; Row-Bot
  never enables them. Pipecat's runner, CLI, transports, cloud services and
  its own Kokoro and Whisper services (which download models) are never
  imported; a test checks that building the analyzers loads none of them.
- **Models are bundled.** Silero VAD (2.3 MB) and Smart Turn v3.2 (8.7 MB)
  ship inside the wheel and run on onnxruntime. Nothing is downloaded.
- **Environment scrub.** Before any analyzer is built, `PIPECAT_SETUP_FILES`
  (which makes Pipecat execute arbitrary Python files) and
  `PIPECAT_LOG_LLM_CONTEXT` are removed, and `PIPECAT_SMART_TURN_LOG_DATA` is
  forced to `0` (it writes the person's raw audio as WAV files).
- **Logging bridge.** `LOGURU_AUTOINIT=False` removes loguru's own stderr
  sink; one sink forwards to stdlib logging (`row_bot.voice.pipecat`) at INFO
  and above only. Pipecat logs transcripts and synthesised text at DEBUG,
  which never reaches Row-Bot's logs.
- **Lock changes.** Pipecat needs `protobuf<7` and `soundfile<0.14`: the lock
  moved protobuf 7.35.1 to 6.33.6 and soundfile 0.14.0 to 0.13.1, and nothing
  else moved. Every other requirer accepts both.
- **Licences.** pipecat-ai and pipecat-resampler are BSD-2-Clause; num2words
  is LGPL-2.1, pure Python, installed as its own package and used
  unmodified. See `NOTICE`.

## Streaming speech-to-text, picked for the computer (V1b, VD3)

Settings › Voice › Talk lists a small pinned catalog
(`voice/speech_models.py`) and recommends one for this computer from what
`local_runtime/hardware.py` reads (no network): **Nemotron Speech Streaming**
on an NVIDIA card the speech runtime can use, **Moonshine Base** everywhere
else, a Mac included. Whisper stays a choice, and Dictate keeps using it.

| Model | Download | On disk | Language | Licence | Runs as |
|---|---|---|---|---|---|
| Moonshine Base (`moonshine-base-en`) | 111.3 MB | 141.3 MB | English | MIT (Useful Sensors) | whole stretches, re-read for each partial; committed at the quietest 100 ms after 20 s |
| Nemotron Speech Streaming 0.6B, 160 ms chunks, int8 (`nemotron-streaming-en`) | 463.9 MB | 661.9 MB | English | NVIDIA Open Model License | a streaming transducer: decoded as the person speaks |

Each is one archive from sherpa-onnx's official GitHub releases
(`github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/...`) with its
exact size and SHA-256 (GitHub's own asset digest, checked again on download
here). **Nothing downloads until the person presses Download**: the button
names the size and the request carries it; a different size now is refused.
The archive goes into `<data>/models/speech/downloads/` through Row-Bot's one
safe download path (`local_runtime/fetch.py`: https, reviewed hosts, resume
after Stop), is checked against its pin, and only the model's listed files are
copied out (no other entry, no link, no path outside) into a staging folder
that becomes `<data>/models/speech/<id>/` after `model.json` is written last.
The archive is then deleted. A finished download becomes what Talk listens
with (`talk_model`), unless Talk is set to Realtime. Remove deletes the folder
and puts Talk back on Whisper if it used it. The language reported with each
transcript is the model's own (`TranscriptUpdate.language`); a model that
detects languages would report what it heard (V8 adds language settings).

### Runtime review: sherpa-onnx 2.0.1

One runtime for every family, rather than one per model:
`sherpa-onnx>=2.0.1,<2.1` (and `sherpa-onnx-core`, its shared libraries) in
the `voice` extra, locked at 2.0.1 (26 Oct 2026; 2.0.3 came out the day this
was written and was not taken).

- **Telemetry and network: none.** The wheels for Windows, macOS arm64 and
  Linux x86_64 were unzipped and scanned without importing them. Every URL in
  the Python files and native libraries is documentation or an error message
  (model pages, export scripts, papers). The Windows libraries import no
  networking API (no WinHTTP, WinINet, sockets or curl); the Linux wheel's
  only socket strings are in its bundled ALSA library (local sound devices,
  which Row-Bot never opens). sherpa-onnx has no model downloader: it reads
  the paths it is given.
- **Bundled ONNX Runtime 1.28.2.** The core wheel ships its own ONNX Runtime
  library (Windows also copies it into the environment's `Scripts/`). It
  coexists with the locked `onnxruntime` 1.27 that Silero, Smart Turn and
  Kokoro use: the Python package's module is self-contained, each loads its
  own library, and both orders of import were checked running a session in
  the same process. Its telemetry is covered below: it is not built as a
  telemetry provider.
- **Licence:** Apache-2.0 (sherpa-onnx), MIT (ONNX Runtime). See `NOTICE`.
- **Wheels:** CPython 3.13 for Windows x64, macOS arm64 and x86_64
  (`macosx_10_15`, and universal2), Linux x86_64 and aarch64 (the arm64
  container). Size: about 2.2 MB plus 9.5-16.7 MB for the core libraries per
  platform (about 25-45 MB installed).
- **Lock fit:** no dependencies beyond its own core; `uv lock` added the two
  packages and moved nothing else; `verify_runtime_dependencies.py all`
  imports it.
- **Graphics cards:** the PyPI build is processor-only (CUDA builds are on
  the project's own index, not in the lock). So on an NVIDIA computer the
  recommendation is Moonshine, with that reason shown; Nemotron is still
  offered and runs on the processor. `speech_models.cuda_runtime()` detects a
  CUDA build if one is ever installed, and Nemotron then runs on the card.
- **Compared:** Moonshine's own package (`moonshine-voice` 0.1.6, MIT) has the
  newer streaming Moonshine v2 Small and Medium, but brings PortAudio
  (`sounddevice`), its own downloader (`download.moonshine.ai`, `requests`), a
  second bundled ONNX Runtime, and wheels only for macOS 15+ on arm64 (no
  Intel Mac). Driving these models on the locked `onnxruntime` directly would
  mean re-implementing each family's decoder (transducer search, Moonshine's
  cached decoding) in Python. sherpa-onnx runs Moonshine Base and Tiny (in
  Moonshine v2's runtime format) but not v2's streaming Small and Medium;
  those, and Parakeet (offline in sherpa-onnx), are left for later.

### ONNX Runtime telemetry (reviewed 22 Nov 2026)

Two copies of ONNX Runtime run in a voice session. On macOS and Linux
neither has telemetry (it is Windows-only TraceLogging; the macOS and Linux
libraries contain no provider). On Windows:

- **What the source does.** ONNX Runtime's Windows platform layer
  (`onnxruntime/core/platform/windows/telemetry.cc`, checked in a vendored
  1.27.1 copy because `github.com/microsoft/onnxruntime` answered 404 on the
  day of review) defines the TraceLogging provider `Microsoft.ML.ONNXRuntime`
  `{3a26b1ff-7484-7484-7484-15261f42614d}` with
  `TraceLoggingOptionMicrosoftTelemetry()`. Its events are on by default
  (`WindowsTelemetry::enabled_ = true`) and carry
  `MICROSOFT_KEYWORD_MEASURES` and privacy tags: `ProcessInfo` (runtime
  version, debugger attached, redistributable or inbox, service names of the
  process), `SessionCreation` (model producer, version, domain, graph name,
  graph and weight hashes, model metadata, model file name, execution
  providers, hardware vendor ids), `RuntimeError`, `RuntimePerf` (run counts
  and durations), driver and execution-provider events.
- **When it is telemetry.** In the public source `TraceLoggingConfig.h`
  defines `TraceLoggingOptionMicrosoftTelemetry()` as an all-zero group:
  "providers using this configuration file will not join any ETW Provider
  Groups and will not be given any special treatment by group-sensitive ETW
  listeners". Microsoft's own release pipeline
  (`tools/ci_build/github/windows/set_telemetry_var.ps1`) writes a private
  group GUID into `TraceLoggingConfigPrivate.h` and builds with
  `--use_telemetry`. A provider group's meaning is decided by the ETW
  controllers that subscribe to it (Microsoft Learn, TraceLoggingOptionGroup);
  the Microsoft telemetry group is what Windows' diagnostic data service
  listens to, so events reach Microsoft only from an official build, and only
  as far as the person's Windows diagnostic data settings send them.
- **The PyPI `onnxruntime` 1.27 (Kokoro, faster-whisper's voice filter,
  Silero VAD, Smart Turn): telemetry built in.** Its `onnxruntime.dll` and
  `onnxruntime_pybind11_state.pyd` both contain the provider GUID and the
  group GUID `{4f50731a-89cf-4782-b3e0-dce8c90476ba}` (the same group as in
  Microsoft's ONNX Runtime inside `moonshine-voice`), plus the event names.
  **Switched off:** `row_bot/onnxruntime_telemetry.switch_off()` calls
  `onnxruntime.disable_telemetry_events()` ("Disables platform-specific
  telemetry collection", ONNX Runtime Python API) once per process, before
  any session: Kokoro's load (`tts.py`), Whisper's load (`voice/__init__.py`,
  whose voice filter is an ONNX Runtime session) and Pipecat's analyzers
  (`pipecat_boundary.prepare`). A PR-lane test checks the order on all three
  paths. Every event function returns early while `enabled_` is false.
  **What is left:** ONNX Runtime's ETW enable callback also writes
  `enabled_` (`ORT_TL_EtwEnableCallback` sets it to whether a controller
  enables the provider), so if a Windows component enables or re-enables the
  provider later in the process's life, events can resume. Row-Bot's call
  comes after the provider registers, so it wins at start; a later re-enable
  is outside Row-Bot's control. This was not observed with an ETW trace here
  (that needs an elevated session): a hand check.
- **sherpa-onnx's bundled ONNX Runtime 1.28.2: not a telemetry build.** It
  is built by sherpa-onnx's author from the public source
  (`csukuangfj/onnxruntime-libs`, `.github/workflows/windows-x64.yaml`:
  `build.py` without `--use_telemetry`). Its `onnxruntime.dll` contains the
  provider GUID and the event names but **not** the telemetry group GUID, so
  the provider joins no group and no diagnostic data listener collects it.
  Its events go only to a local ETW session that names this provider GUID
  (a developer's trace tool, run by the person). They cannot be switched off:
  sherpa-onnx's source never calls `DisableTelemetryEvents` on its
  `Ort::Env` and exposes no way to, and Row-Bot's Python call reaches only the
  PyPI copy (the two libraries are separate). Linux and macOS copies have no
  provider at all.

### Measured on this computer (22 Nov 2026)

Intel Core Ultra 9 285K, 64 GB, RTX 5090 (busy with another job, and the
processor at 100% from a concurrent research run throughout). The real engine
(Silero VAD, Smart Turn, each speech model) fed 16 real speech turns of 2 to
6.6 s in real time, 20 ms frames, with an agent and a voice that answer at
once, so the numbers are the engine's own; models in a scratch profile.

Transcription is the engine's own mark from the turn's end to the final
transcript; the rest of voice-to-voice (endpointing, the agent, speech) does
not depend on the speech model. Three runs:

| Model | Transcription p50 / p90 | First live caption | Memory loaded | Warm-up (first session) |
|---|---|---|---|---|
| Whisper base (segmented) | 432 / 448 ms, 602 / 848 ms (third run void: the machine stalled, 4.7 s) | none | (V1) | 4.7-8.7 s |
| Moonshine Base | 160 / 176 ms, then 0 / 1 ms and 1 / 132 ms once the final reuses the last partial | about 0.65 s after speech starts | +187 MiB | 0.6-0.9 s |
| Nemotron Speech Streaming (processor) | 252 / 315, 475 / 5544, 169 / 391 ms | about 1.1 s after speech starts | +746 MiB | 2.2-5.4 s |

Moonshine Base takes transcription from about half a second to almost
nothing: with V1's measured voice-to-voice of 1.29 s p50 (Whisper base,
587 ms transcription) that is about 0.7-0.85 s. Nemotron on the processor
keeps up only while the processor has room (one run's p90 of 5.5 s came from
falling behind under the 100% load); on a graphics card it would not compete
for the processor, which is why the pick needs a runtime that uses the card.
Raw decode speed, outside the engine: Nemotron RTF 0.24 with 4 threads (0.36
with 2), final 120 ms after the last word; Moonshine Base reads 4 s of speech
in 55-77 ms and 8 s in 120-164 ms.

## Transport: why a WebSocket, not WebRTC

aiortc never offers loopback candidates (it uses LAN, virtual or link-local
addresses, binds UDP on them, may raise a Windows Firewall prompt and can fail
offline), and without TURN a phone over the remote-access tunnel cannot reach
it. Pipecat's browser client pulls in Daily's SDK, which has a hard-coded
Sentry DSN and loads remote code. So Row-Bot Voice uses one WebSocket on
Row-Bot's own authenticated origin, which every remote-access path (Tailscale
Serve, ngrok, a reverse proxy such as the Caddy example) carries, and its own
small client in `frontend/src/features/voice/`. The server runs uvicorn with
its `websockets` protocol for this one route.

## Echo cancellation

The browser's echo canceller only removes what it can hear. Chromium engines
(Chrome, Edge, the Windows app's WebView2, Chrome on Android) cancel the
page's own WebAudio output. On WebKit (Safari, the macOS app's WKWebView,
iPhone) the client plays through a local loopback `RTCPeerConnection` pair
inside the page (no ICE servers, no network), the known fix that lets
WebKit's canceller hear the voice. If that pair cannot connect (a machine
with no network interface at all), the voice plays directly.

What was verified: the capture constraints, the worklet's framing, playback,
ducking and flushing in headless Chromium with a synthetic microphone.
What needs a hand check on real devices: echo on open speakers in WebView2,
WKWebView, iPhone Safari and Android Chrome; Bluetooth headsets (the HFP
profile switch); and the phone's lock screen (an installed iPhone web app
stops listening when locked).
