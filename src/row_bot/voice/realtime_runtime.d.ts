export type RealtimeMetadata = {
  thread_id?: string;
  generation_id?: string;
  origin?: string;
  silent?: boolean;
};
/** Audio a client plays itself (a socket front): base64 PCM16 chunks, cleared on barge-in. */
export type RealtimePlayer = {
  muted: boolean;
  play(base64: string): void;
  flush(): void;
  stop(): void;
};
/** The event channel: a WebRTC data channel, or a socket with the same surface. */
export type RealtimeChannel = {
  readonly readyState: string;
  send(data: string): void;
  close(): void;
  addEventListener(type: 'message', listener: (event: { data: unknown }) => void): void;
  addEventListener(type: 'open' | 'close' | 'error', listener: () => void): void;
};
export type RealtimeLink = {
  dc: RealtimeChannel;
  stream?: MediaStream;
  audio: RealtimePlayer;
};
export type RealtimeRuntime = {
  stop(): Promise<void>;
  sendFunctionOutput(callId: string, output: string, metadata?: RealtimeMetadata): boolean;
  sendRunEvent(text: string, metadata?: RealtimeMetadata): boolean;
  clearGeneration(sessionId: number, threadId: string, generationId: string): boolean;
  setGeneration(sessionId: number, threadId: string, previous: string, next: string): boolean;
  cancelActiveOutput(reason: string): boolean;
  session:
    | { pc: RTCPeerConnection; dc?: RTCDataChannel; stream?: MediaStream; audio: HTMLAudioElement }
    | RealtimeLink
    | null;
};
export type RealtimeOptions = {
  sessionId: number;
  threadId?: string;
  generationId?: string;
  current?(): boolean;
  emit(event: Record<string, unknown>): void;
  exchangeManaged?: boolean;
  bootstrap(): Promise<{ value: string | null }>;
  exchange(sdp: string, ephemeralKey: string): Promise<string>;
  /** A socket front: opens the microphone, player and socket, and has sent its setup. */
  socket?(): Promise<RealtimeLink>;
  onRuntime?(runtime: RealtimeRuntime): void;
};
export const RUN_EVENT_INSTRUCTIONS: { answer: string; status: string };
export function startRealtimeRuntime(options: RealtimeOptions): Promise<RealtimeRuntime | undefined>;
