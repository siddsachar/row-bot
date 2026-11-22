/**
 * Gemini Live as a Live voice front: maps Gemini's Live API messages onto the
 * same whitelisted events the OpenAI-schema runtime emits, and answers the
 * consult call with a toolResponse. The consult is asynchronous
 * (NON_BLOCKING), so Gemini keeps talking while the agent works.
 */
import {
  RUN_EVENT_INSTRUCTIONS,
  type RealtimeLink,
  type RealtimeMetadata,
  type RealtimeRuntime,
} from '../../../src/row_bot/voice/realtime_runtime.js';

const CONSULT = 'row_bot_agent_consult';
export const GEMINI_INPUT_RATE = 16_000;
export const GEMINI_OUTPUT_RATE = 24_000;

export function geminiAudioFrame(base64: string) {
  return {
    realtimeInput: {
      audio: { data: base64, mimeType: `audio/pcm;rate=${GEMINI_INPUT_RATE}` },
    },
  };
}

type Turn = { id: string; origin: 'front' | 'row_bot'; text: string };

export async function startGeminiRuntime(options: {
  sessionId: number;
  threadId: string;
  generationId: string;
  current(): boolean;
  emit(event: Record<string, unknown>): void;
  socket(): Promise<RealtimeLink>;
  onRuntime(runtime: RealtimeRuntime): void;
}): Promise<RealtimeRuntime> {
  let generation = options.generationId;
  let link: RealtimeLink | null = null;
  let stopped = false;
  let counter = 0;
  let heard = '';
  let heardItem = '';
  let turn: Turn | null = null;
  let rowBotNext = false;
  const names = new Map<string, string>();
  const isCurrent = () => !stopped && options.current();

  function emit(type: string, detail: Record<string, unknown> = {}) {
    if (!isCurrent()) return;
    options.emit({
      type,
      ...detail,
      thread_id: options.threadId,
      generation_id: generation,
      session_id: options.sessionId,
    });
  }
  function send(message: unknown): boolean {
    if (!link || link.dc.readyState !== 'open') return false;
    const encoded = JSON.stringify(message);
    if (encoded.length > 32_768) return false;
    link.dc.send(encoded);
    return true;
  }
  // Gemini sends no VAD events: the person's words end when the model answers or calls a tool.
  function endHeard() {
    if (!heard) return;
    emit('speech_stopped');
    emit('transcript_final', {
      text: heard.slice(0, 4000),
      item_id: heardItem,
    });
    heard = '';
  }
  function startTurn(): Turn {
    if (!turn) {
      endHeard();
      turn = {
        id: `gemini-${++counter}`,
        origin: rowBotNext ? 'row_bot' : 'front',
        text: '',
      };
      rowBotNext = false;
      emit('output_started', { response_id: turn.id, origin: turn.origin });
    }
    return turn;
  }
  function endTurn(kind: 'response_done' | 'response_cancelled') {
    const ended = turn;
    turn = null;
    if (!ended) return;
    if (ended.text)
      emit('assistant_transcript_final', {
        response_id: ended.id,
        origin: ended.origin,
        text: ended.text.slice(0, 4000),
      });
    emit(kind, { response_id: ended.id, origin: ended.origin });
  }

  function onMessage(event: { data: unknown }) {
    if (!isCurrent() || typeof event.data !== 'string') return;
    if (event.data.length > 1_048_576) return void runtime.stop();
    let message: Record<string, unknown>;
    try {
      message = JSON.parse(event.data);
    } catch {
      return;
    }
    const content = (message.serverContent || {}) as Record<string, unknown>;
    const input = content.inputTranscription as { text?: string } | undefined;
    if (input?.text) {
      if (!heard) {
        heardItem = `heard-${++counter}`;
        emit('speech_started');
      }
      heard = (heard + input.text).slice(0, 4000);
    }
    if (content.interrupted) {
      // The person spoke over the reply: stop what is still queued to play.
      link?.audio.flush();
      emit('barge_in_cancelled', { response_id: turn?.id ?? '' });
      endTurn('response_cancelled');
    }
    const model = content.modelTurn as
      { parts?: { inlineData?: { data?: string } }[] } | undefined;
    for (const part of model?.parts ?? []) {
      if (part.inlineData?.data) {
        startTurn();
        link?.audio.play(part.inlineData.data);
      }
    }
    const output = content.outputTranscription as { text?: string } | undefined;
    if (output?.text) startTurn().text += output.text;
    const call = message.toolCall as
      | { functionCalls?: { id?: string; name?: string; args?: unknown }[] }
      | undefined;
    for (const item of call?.functionCalls ?? []) {
      const id = String(item.id || '');
      if (!id || id.length > 128 || names.size >= 200) continue;
      names.set(id, String(item.name || ''));
      endHeard();
      emit('function_call_ready', {
        call_id: id,
        name: String(item.name || ''),
        arguments: JSON.stringify(item.args ?? {}).slice(0, 8192),
        response_id: turn?.id ?? '',
      });
    }
    if (content.turnComplete) endTurn('response_done');
  }

  const runtime: RealtimeRuntime = {
    session: null,
    async stop() {
      stopped = true;
      const closing = link;
      link = null;
      runtime.session = null;
      if (!closing) return;
      try {
        closing.dc.close();
      } catch {
        /* closed */
      }
      closing.stream?.getTracks().forEach((track) => track.stop());
      closing.audio.stop();
    },
    sendFunctionOutput(
      callId: string,
      output: string,
      meta?: RealtimeMetadata,
    ) {
      const name = names.get(callId);
      if (!isCurrent() || !name) return false;
      if (meta?.generation_id !== undefined) generation = meta.generation_id;
      let response: Record<string, unknown>;
      try {
        const parsed = JSON.parse(output);
        response =
          parsed && typeof parsed === 'object' && !Array.isArray(parsed)
            ? parsed
            : { output };
      } catch {
        response = { output };
      }
      if (name === CONSULT) {
        // The agent's answer, said now; a silent result is only kept.
        response = {
          ...response,
          scheduling: meta?.silent ? 'SILENT' : 'INTERRUPT',
        };
        if (!meta?.silent) rowBotNext = true;
      }
      const sent = send({
        toolResponse: { functionResponses: [{ id: callId, name, response }] },
      });
      if (sent && name === CONSULT && !meta?.silent)
        emit('provider_response_create_sent', { origin: 'row_bot' });
      return sent;
    },
    sendRunEvent(text: string, meta?: RealtimeMetadata) {
      const clean = String(text || '').trim();
      if (!isCurrent() || !clean || clean.length > 8192) return false;
      const answer = meta?.origin === 'final';
      // A status waits for nothing: it is dropped while the front is speaking.
      if (!answer && turn) return true;
      if (meta?.generation_id !== undefined) generation = meta.generation_id;
      rowBotNext = true;
      const prompt =
        (answer
          ? RUN_EVENT_INSTRUCTIONS.answer
          : RUN_EVENT_INSTRUCTIONS.status) + clean;
      const sent = send({
        clientContent: {
          turns: [{ role: 'user', parts: [{ text: prompt }] }],
          turnComplete: true,
        },
      });
      if (sent) emit('provider_response_create_sent', { origin: 'row_bot' });
      return sent;
    },
    setGeneration(session, thread, previous, next) {
      if (
        !isCurrent() ||
        session !== options.sessionId ||
        thread !== options.threadId ||
        previous !== generation
      )
        return false;
      generation = next;
      return true;
    },
    clearGeneration(session, thread, previous) {
      if (
        !isCurrent() ||
        session !== options.sessionId ||
        thread !== options.threadId ||
        previous !== generation
      )
        return false;
      generation = '';
      return true;
    },
    cancelActiveOutput() {
      link?.audio.flush();
      return true;
    },
  };
  options.onRuntime(runtime);
  try {
    emit('connecting');
    const opened = await options.socket();
    if (!isCurrent()) {
      opened.dc.close();
      opened.audio.stop();
      return runtime;
    }
    link = opened;
    runtime.session = opened;
    opened.dc.addEventListener('message', onMessage);
    opened.dc.addEventListener('close', () => emit('disconnected'));
    opened.dc.addEventListener('error', () =>
      emit('fatal_error', { message: 'Gemini Live socket error' }),
    );
    emit('connected');
  } catch (error) {
    emit('fatal_error', {
      message: String((error as Error)?.message || error),
    });
    await runtime.stop();
  }
  return runtime;
}
