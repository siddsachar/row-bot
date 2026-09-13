export type RealtimeMetadata = {
  thread_id?: string;
  generation_id?: string;
  origin?: string;
  silent?: boolean;
};
export type RealtimeRuntime = {
  stop(): Promise<void>;
  sendFunctionOutput(callId: string, output: string, metadata?: RealtimeMetadata): boolean;
  sendRunEvent(text: string, metadata?: RealtimeMetadata): boolean;
  clearGeneration(sessionId: number, threadId: string, generationId: string): boolean;
  setGeneration(sessionId: number, threadId: string, previous: string, next: string): boolean;
  cancelActiveOutput(reason: string): boolean;
  session: { pc: RTCPeerConnection; dc?: RTCDataChannel; stream?: MediaStream; audio: HTMLAudioElement } | null;
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
  onRuntime?(runtime: RealtimeRuntime): void;
};
export function startRealtimeRuntime(options: RealtimeOptions): Promise<RealtimeRuntime | undefined>;
