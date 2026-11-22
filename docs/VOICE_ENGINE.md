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
4. The selected speech-to-text engine (faster-whisper or SenseVoice, behind
   the `StreamingSpeechToText` seam in `voice/streaming_base.py`) writes the
   turn down. The transcript goes through `ClientVoiceAdmission`: a new turn
   when nothing runs, otherwise the active-run control (steer, follow-up,
   stop or status).
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
- **Approvals (VD4).** An `interrupt` becomes a bar notice and a spoken
  summary in the tool's own words. A spoken yes or no counts only when it is a
  short plain answer, began (in audio time) after the summary finished playing
  and while Row-Bot was silent, and the approval system's classification lets
  voice answer it (`voice/approvals.py`: the "Allow in this chat" kinds, one
  action, never a command, an app's tool, the computer, money, a setting, a
  question or setup card). The host then checks that it is the conversation's
  only waiting approval and sends the card's own `approval.resolve` command.
  Everything else says "This one needs a tap."

Latency marks per turn (end of speech, turn end, transcript, admission, first
token, first sentence, first audio sent, first audio played) are kept as
durations only; the bar's timing line and `row_bot.voice.engine.latency`
show them.

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
