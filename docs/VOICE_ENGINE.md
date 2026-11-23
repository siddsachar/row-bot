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
durations only; the bar's details (**Show details**) and
`row_bot.voice.engine.latency` show them.

## Live voice (V4)

A chat's Talk menu also lists the Live voice fronts (`voice/live_voice.py`):
OpenAI Realtime, Gemini Live and Grok Voice, each with the person's own key from
Providers. Before a provider's first use in a browser the client shows what goes
to it (the same list as Settings: voice, recent messages, the agent profile's
name, a few memories unless incognito, and for Gemini Live what is shared).
`POST /api/v1/conversations/{id}/voice/realtime` takes the lease and returns a
ticket for `WS /api/v1/voice/live`, which carries the session's events.

- **Gemini Live and Grok Voice are relayed by the host**: the provider socket is
  opened from Row-Bot with the key, and the audio rides the page's socket
  (16 kHz up, 24 kHz down) through the same capture and playback as Row-Bot
  Voice. No provider token reaches the browser; the page's CSP keeps
  `connect-src 'self'`.
- **OpenAI Realtime keeps its WebRTC call for the media.** The host mints the
  short-lived client secret and exchanges the SDP
  (`.../voice/realtime/{lease}/exchange`); the browser's `RTCPeerConnection`
  then carries audio directly to and from OpenAI, and the call's events go up
  the socket.
- **The front talks; Row-Bot works.** The front's instructions
  (`voice/live_front.py`) and a bounded context block (12 recent messages,
  5 memories) go to the provider; anything beyond small talk calls
  `row_bot_agent_consult`, which runs the chat's own agent with its model,
  tools and approvals. Progress lines ("Working on: …") and the answer go back
  to the front to say. The front's own replies are saved as assistant messages
  with `additional_kwargs={"voice_front": True}` (Learning keys on it: never
  rename it).
- Live voice is always hands-free (no push to talk); only Gemini Live takes
  shared stills.
- OpenAI Realtime's model and voice are `realtime_model` and `realtime_voice`
  (Settings › Voice › Live voice). "Listen with" offers only speech-to-text
  (local or the V5 cloud services). An older file with
  `talk_provider: "openai_realtime"` reads as `local`, and a `gpt-realtime…`
  talk model moves to `realtime_model` (`voice/runtime.py`
  `retire_realtime_listen`); the next save of any voice setting rewrites it.

## Voice-note replies in chat apps (V9)

On Telegram and WhatsApp, a voice note in is transcribed with Dictation's
local engine in the saved language (`speech_to_text_provider("voice_note")`).
With the channel's **Reply to voice notes with a voice note** on (the default)
and a voice that can speak, the turn runs with `voice_mode` for that turn only
and the answer goes back as text, then as an OGG/Opus voice note made by the
chosen speech output (`channels/voice_replies.py`; Kokoro, or the cloud voice
with its key). Code, tables and links are left out of the note; an answer over
the platform's size limit, or in a language the voice can't speak, goes as text
alone.

## Not shipped

- **Wake word** (V10): not merged; there is no always-listening mode.
- **Japanese and Mandarin speech** need misaki, which Row-Bot does not ship.
- **The macOS push-to-talk hotkey** and **OpenAI Realtime sharing** are
  follow-ups.

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
with (`talk_model`). Remove deletes the folder
and puts Talk back on Whisper if it used it. The language reported with each
transcript is the model's own (`TranscriptUpdate.language`); a model that
detects languages would report what it heard; see Languages (V8) below.

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
  Silero VAD, Smart Turn, the wake word): telemetry built in.** Its `onnxruntime.dll` and
  `onnxruntime_pybind11_state.pyd` both contain the provider GUID and the
  group GUID `{4f50731a-89cf-4782-b3e0-dce8c90476ba}` (the same group as in
  Microsoft's ONNX Runtime inside `moonshine-voice`), plus the event names.
  **Switched off:** `row_bot/onnxruntime_telemetry.switch_off()` calls
  `onnxruntime.disable_telemetry_events()` ("Disables platform-specific
  telemetry collection", ONNX Runtime Python API) once per process, before
  any session: Kokoro's load (`tts.py`), Whisper's load (`voice/__init__.py`,
  whose voice filter is an ONNX Runtime session), Pipecat's analyzers
  (`pipecat_boundary.prepare`) and the wake word's model (`voice/wake.py`).
  A PR-lane test checks the order on every path. Every event function returns early while `enabled_` is false.
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

## Languages (V8)

Settings › Voice › Language (`speech_language` in `voice_runtime_settings.json`:
`auto`, the default, or a code from `voice/languages.py`) applies to Talk,
Dictate, chat-app voice notes and narration. `providers.speech_to_text_provider`
builds each engine with it (None to detect), and a Talk session opens every
utterance with it through `StreamingSpeechToText.open(language=...)`. No path
hard-codes English any more.

- **Whisper** takes the language or detects it (one extra decoder step on the
  first 30 s window; the encoder output is reused). Detection is trusted only
  at a language probability of 0.5 or more on 1.5 s of audio or more; a
  shorter clip skips detection and an unsure one is read again (detection
  comes before any decoding, so only the features and encoder repeat) in the
  last language heard clearly, else the interface's (English: the client has
  no translations). Same engine, only the language is chosen.
  SenseVoice gives no confidence, so only the short-clip rule applies to it. **SenseVoice** is asked for
  Chinese, English, Japanese or Korean when that is the setting, else `auto`,
  and reports the language tag it heard. **Moonshine Base** and **Nemotron**
  are English only and report `en`.
- A provider reports the language with each transcript through
  `transcribe_heard(audio, language=...)` (`provider_base.LanguageSpeechToText`);
  one without it (a cloud adapter) is asked through `transcribe_bytes` and
  reports none. The final caption carries it (`language`), the bar's details
  show it, and it is the hint for the voice that answers.
- **No silent fallback.** An English-only Talk engine with another language
  chosen keeps listening in English; Settings says so (the snapshot's
  `voice.language.notices`) and offers Whisper. With Detect automatically, the
  last ten detected languages (in memory only) raise the same notice once
  another language was heard three times.
- **Speaking it.** Kokoro picks a voice per sentence
  (`languages.kokoro_voice_for`): the sentence's own language when it is clear
  (a script check, then common words, which needs two marker words, or one in
  three words or fewer, and must beat every other language), else the
  language the person was heard speaking when Kokoro speaks it, else the
  person's own voice's language. The person's voice speaks its own language;
  Spanish, French, Hindi, Italian and Brazilian Portuguese get the voice
  picked in Settings or a preset. Kokoro's Japanese and Mandarin voices need
  the misaki G2P package, which Row-Bot does not ship, so those (and every
  other language) are not spoken: `SynthesizedSpeech.unspoken` names the
  language, the session sends the caption with `spoken: false` and an
  `unspoken` event, Read aloud answers `voice_language_unspoken`, and a
  chat-app reply goes as text. A hint alone never silences a sentence.
- **Cost.** Guessing a sentence's language takes about 30 µs (on a processor
  at 100% load); nothing is loaded and nothing is kept but the ten codes.
- No multilingual streaming model is offered: sherpa-onnx's streaming models
  are each one language (or Chinese with English). Qwen3-TTS is a follow-up.

## Cloud speech parts (V5)

Optional, off by default, with the person's own keys (`voice/cloud_speech.py` holds the
options, their published prices with the date checked, and where each key lives). Each runs
behind the same seams as the local engines, connected from the host with plain `websockets`
and `httpx`; no vendor SDK, so there is no new dependency and no third-party telemetry.

| Listen with | Protocol | Price (23 Nov 2026) |
|---|---|---|
| Deepgram Nova-3 | `wss://api.deepgram.com/v1/listen`, `Authorization: Token`, binary PCM, `Finalize` | $0.0048/min ($0.0058 multilingual), promotional |
| AssemblyAI STT 3.7 Realtime | `wss://streaming.assemblyai.com/v3/ws`, `Authorization`, binary PCM, `Terminate` | $0.30 per session hour |
| OpenAI `gpt-live-transcribe` | `wss://api.openai.com/v1/realtime?intent=transcription`, a `transcription` session, 24 kHz PCM, commit | $0.017/min |
| Gemini `gemini-3.5-transcribe-live` | the Live API socket, `x-goog-api-key`, manual activity start and end | about $0.009/min |

| Speak with | Request | Price (23 Nov 2026) |
|---|---|---|
| ElevenLabs `eleven_flash_v2_5` | `POST /v1/text-to-speech/{voice}/stream?output_format=pcm_24000`, `xi-api-key` | $0.04 per 1,000 characters |
| Cartesia `sonic-3.6` | `POST /tts/bytes`, raw `pcm_s16le` 24 kHz, `Cartesia-Version: 2026-08-14` | about $0.05 per 1,000 characters |
| Gemini `gemini-3.8-flash-tts` | `POST /v1beta/interactions`, streamed, voice in `speech_config`, style annotation | $9 per 1M audio tokens out (25 a second), $18 from 1 Jan 2027 |

- **Listening.** One utterance is one socket (`cloud_stt.py`): it opens when the person starts
  speaking, carries only that utterance's audio in 100 ms pieces from a thread, and closes when
  the final transcript arrives, or at once when the turn is dropped (a barge-in that was not a
  turn, mute). Partials are what the provider has said so far, so `live_partials` is true and
  the bar's captions are live. A session that is open but quiet sends nothing and costs
  nothing. A failure reaches the bar as `cloud_speech_failed`; only the exception's type is
  logged. `open(language=...)` passes a language (a code, or None to detect) to each provider.
- **Speaking.** Each sentence or clause is one HTTP request on a kept-alive client
  (`cloud_tts.py`). The pieces stream back as they are made: `SentenceSpeech.stream` lets the
  engine send the first piece before the sentence is finished, and closing the iterator at a
  barge-in closes the request. Saved pronunciations (`sound.respelled`) apply. Read aloud and
  voice-note replies use the same chosen output, as a WAV.
- **Voices (VD11).** Presets per provider (Kokoro's stay the default). "Describe a voice":
  Gemini's style annotation from the person's description, sent with each sentence; ElevenLabs'
  voice design (`/v1/text-to-voice/design`, then `/v1/text-to-voice` with the first preview),
  a reviewed Settings action that saves the new voice in the person's account. Only words are
  sent: no recording, and no route takes one.
- **Keys.** OpenAI and Google use their keys in Providers. Deepgram, AssemblyAI, ElevenLabs and
  Cartesia keys are Voice settings stored in Row-Bot's key store like the other keys, written
  only, never returned, kept out of programs Row-Bot starts. Readiness is whether a key is
  saved, with no network call.
- **Languages (V8).** The saved language reaches each listener (`open(language=...)`; None detects,
  and the language a provider reports comes back with the transcript, so `transcribe_heard` reports
  it too). A cloud voice gets the language hint per sentence: the text's own language when clear,
  else the one last heard. It is passed to ElevenLabs (`language_code`) and Cartesia (`language`);
  Gemini tells it itself. A language the voice's model does not list (ElevenLabs Flash v2.5's 32,
  Sonic 3.6's 44, Gemini 3.8 Flash TTS's 130-plus) is `unspoken`, exactly as for Kokoro, and
  Settings › Voice › Language lists the chosen voice's languages.
- **No silent switching.** A chosen cloud part without its key is replaced by the local part
  (Whisper or the downloaded model; Kokoro), never by another company: Settings says
  "Needs your … key", and the session's `ready` event carries `fallbacks`, which the bar says.
- **OpenAI text-to-speech is not offered.** OpenAI shuts down `tts-1`, `tts-1-hd` and
  `gpt-4o-mini-tts` on 6 Jan 2027; its named replacement is a speech-to-speech model on the
  Realtime API, not a speech endpoint.
- **Latency with fakes.** With a provider that answers at once, the engine adds nothing beyond
  the provider's own time: the first piece of a sentence is sent the moment it arrives, before
  the rest is made. Real first-audio times need real keys (a hand check).

## Presence and control (V7)

- **Devices.** `features/voice/devices.ts` keeps the chosen microphone and speaker per browser
  (`localStorage`, '' for the system default; never sent to the host) and lists devices by the
  names the operating system gives them (`enumerateDevices`, without Chromium's `default` and
  `communications` stand-ins). The microphone opens with `deviceId: {ideal}`, so a missing one
  gives the default at once, and `getSettings().deviceId` tells the bar. A track the browser ends
  (the device went away) reopens the microphone straight away; `devicechange` switches back when
  the chosen one returns, and listens again when a microphone appears after none was left. The
  speaker uses `AudioContext.setSinkId` (Chromium engines); where it is missing (WebKit, Firefox)
  no speaker choice is shown. OpenAI Realtime's call gets the same constraints and its `<audio>`
  element's `setSinkId`; a change mid-call replaces the sender's track. Dictation opens the chosen
  microphone too.
- **Push to talk.** With push to talk on, Space held in the window (not in a text field, a button,
  a menu or a dialog) sends `hold`. In the Windows app the person may choose a hotkey
  (`row_bot/voice_hotkey.py`, off by default, kept per computer): the page arms it through the
  native bridge's `voice_hotkey` operation only while a push-to-talk session is open, and disarms it
  when the session ends; the window loading or closing disarms it too. It is `RegisterHotKey` for
  exactly that combination on its own thread (no keyboard hook, so no other keystroke is seen),
  then `GetAsyncKeyState` of that one key every 20 ms until it is released; presses reach the page
  as `row-bot-voice-hotkey` events. Checked here with a synthetic F13: press heard in 34 ms,
  release within one poll. **macOS is not offered**: Carbon's `RegisterEventHotKey` needs no
  Accessibility or Input Monitoring permission (unlike an event tap), but needs the window
  process's main run loop and a Mac to check it on (since macOS 15 it also refuses hotkeys whose
  only modifiers are Option or Shift); a follow-up.
- **Media Session.** While voice is open, `navigator.mediaSession` (where it exists) shows
  "Row-Bot · Listening" (or what voice is doing); play and pause unmute and mute, stop and hang up
  end voice, and `setMicrophoneActive` follows mute. Whether the lock screen or a headset shows
  them is the system's choice (an audible media element usually decides it).
- **Phones.** The wake lock keeps the screen on while voice is open. The bar says once
  (dismissible, remembered per browser) that an iPhone stops listening when the screen locks or
  the app is backgrounded (a Home Screen web app included) and that Android stops capture with the
  screen off; Settings › Voice says it too. Background listening would need a native app.
- **Buddy.** Both voice paths set Buddy's listening state and lip-sync level; Live voice's OpenAI
  call, which plays its own speech, has its remote stream read by an analyser. The desktop pet
  is its own window, so the main window shares the two values on a same-origin
  `BroadcastChannel` (`row-bot-buddy-voice`, about 25 a second while speaking, twice a second
  while listening; the pet drops "listening" after 2 s without word).

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
profile switch drops the headset to call quality and a lower sample rate when
its microphone opens); the phone's lock screen (an installed iPhone web app
stops listening when locked) and Media Session controls; Android with the
screen off; the Windows push-to-talk hotkey on a real keyboard with another
app in front; Live voice and the cloud speech parts with real keys (first
audio, captions, barge-in), and OpenAI Realtime's direct WebRTC call from a
phone over the remote-access tunnel.

## The wake word (V10)

"Hey Row-Bot" is opt-in, desktop only and off by default: Settings › Voice ›
Wake word (`runtime.wake_word`, not changeable from a chat). The host never
opens a microphone. The app window on the computer running Row-Bot captures
with Row-Bot Voice's worklet and, while the person has it on, streams 16 kHz
frames over the wake mode of `WS /api/v1/voice/stream` (a single-use ticket
from `POST /api/v1/voice/wake`, which only this computer may call, over
loopback). The host runs `row_bot.voice.wake` and sends `wake` when it hears
the phrase; the window then closes its wake microphone and starts an ordinary
Row-Bot Voice session, in the open chat or in a new one as Home's Talk does.
Audio is kept only until its 80 ms step is full, never written anywhere and
never sent on.

Guards: a score threshold (0.5, calibrated in training to a target false
accept rate), a 2 s refractory period after a detection, and silence while any
voice session or dictation holds the microphone's lease (the window closes its
wake microphone; the host drops frames as well). Closing the window ends
listening. A hidden window may have its microphone muted by the system (WebKit
on macOS); the window watches the track's mute state and its mark says
"Wake word paused".

### The model and its licence

`src/row_bot/voice/hey_row_bot.onnx` is a LiveKit WakeWord v2 streaming model
(a 1280-sample `chunk` and its state tensors in; score and next state out),
run by Row-Bot's own 60-line runtime on onnxruntime (already a dependency),
one thread, CPU only. Neither `livekit-wakeword` nor `openwakeword` is a
dependency. openWakeWord was ruled out: its pre-trained models and its
negative feature set are CC BY-NC-SA, and its package requires `tflite-runtime`
on Linux, which has no wheel for Python 3.12 or 3.13.

The model starts from LiveKit's pretrained encoders (Apache-2.0 per LiveKit's
NOTICE) and was fine-tuned only on permissive or generated data: synthetic
speech from Kokoro v1.0 (Apache-2.0; 44 of its 54 voices and blends of them)
and Piper's LibriTTS-R generator (MIT code, CC BY 4.0 voice; 800 of its 904
speakers and blends), LibriSpeech's dev sets as background speech
(CC BY 4.0), MS-SNSD's Freesound noise clips (CC0; its DEMAND recordings,
CC BY-SA, are left out), and generated coloured noise and rooms. No Common
Voice, MUSAN, ACAV100M, AudioSet, FMA, MIT room recordings or other
non-commercial data. The model (7.4 MB, SHA-256 `7f5a7079…a0296b`) is
Apache-2.0, credited in NOTICE.

### How well it hears (23 Nov 2026)

Measured with `scripts/wake_word/evaluate.py` through Row-Bot's own runtime,
on audio it never trained on: LibriSpeech test-clean and test-other (10.75 h of
real read speech) and MS-SNSD's CC0 test noise; the phrase and near misses
said by the held-out voices (10 Kokoro voices and Piper speakers 800-903, 1,200
clips each), each after a second of real speech.

| Threshold | False accepts per hour, speech | at 10 dB noise | Near misses woken | Recall, clean | at 10 dB noise |
|---|---|---|---|---|---|
| 0.3 | 0.09 | 1.30 | 27 / 1,200 | 99.3% | 98.2% |
| **0.5 (shipped)** | **0.00** | **0.28** | **17 / 1,200** | **98.3%** | **95.8%** |
| 0.7 | 0.00 | 0.00 | 9 / 1,200 | 95.3% | 87.2% |

It wakes about when the phrase ends (median 40 ms before the measured end,
90th percentile 90 ms after; one 80 ms step at most adds to that). Each step
costs 0.7 ms on one CPU thread (under 1% of a core) and the model adds about
40 MB to the host. Caveats: recall is on synthetic voices, so real voices,
accents, far-field rooms, music and TV may do worse; there is no music in the
training data; and FLEURS and Common Voice were not used, so non-native and
non-English speech is less covered. Said on their own, only "hey rowboat"
(3 of 40) and "hey, row it" (1 of 20) of the near misses woke it.

"Row-Bot" and "robot" sound the same, so "hey robot" wakes it too; the near
misses it is taught to reject include "robot" alone, "hey Rob", "row boat"
and "hey Siri".

### Reproducing the training

The scripts are in `scripts/wake_word/`; keep every folder and cache on a
drive with room (about 5 GB of downloads).

1. `python scripts/wake_word/sources.py <src> --yes --noise` downloads the
   pinned inputs (sizes and hashes checked).
2. `python scripts/wake_word/speech.py <src> <clips>` (Row-Bot's environment,
   CPU) makes the synthetic speech; a fifth of the Kokoro voices is held out.
3. In a throwaway environment (`uv venv` plus `livekit-wakeword[train]==2.1.3`,
   `piper-sample-generator==3.2.0` with that repository's `piper_train` on the
   path, and torch): `python scripts/wake_word/piper_voices.py <src> <clips>`
   adds Piper's voices (CPU with `CUDA_VISIBLE_DEVICES=-1`), then
   `LIVEKIT_WAKEWORD_ENCODERS=<src>/encoders HF_HUB_OFFLINE=1
   python scripts/wake_word/train.py <src> <clips> <out>/hey_row_bot.onnx --threads 16`
   runs LiveKit's training loop with every data source replaced by the local
   ones. The shipped model took 6,000 steps in 85 minutes on 16 CPU threads
   (no GPU); a GPU is faster.
4. `python scripts/wake_word/evaluate.py <out>/hey_row_bot.onnx <src> <clips>`
   (Row-Bot's environment) reports false accepts per hour on held-out real
   speech (clean and noisy), recall on the held-out voices, detection latency,
   CPU and memory.
