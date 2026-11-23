/**
 * Row-Bot Voice's microphone capture, in the audio thread.
 *
 * Takes the (echo-cancelled) microphone at the context's rate, averages it
 * down to 16 kHz mono, and posts 20 ms frames of signed 16-bit PCM with their
 * loudness. It keeps nothing beyond the frame it is filling.
 */
declare const sampleRate: number;
declare class AudioWorkletProcessor {
  readonly port: MessagePort;
}
declare function registerProcessor(
  name: string,
  processor: new () => AudioWorkletProcessor,
): void;

const TARGET_RATE = 16000;
const FRAME_SAMPLES = 320;

class RowBotCapture extends AudioWorkletProcessor {
  private ratio = sampleRate / TARGET_RATE;
  private frame = new Int16Array(FRAME_SAMPLES);
  private filled = 0;
  private sum = 0;
  private count = 0;
  private phase = 0;
  private energy = 0;

  private emit(value: number) {
    const clamped = Math.max(-1, Math.min(1, value));
    this.frame[this.filled++] = clamped < 0 ? clamped * 32768 : clamped * 32767;
    this.energy += clamped * clamped;
    if (this.filled === FRAME_SAMPLES) {
      const level = Math.sqrt(this.energy / FRAME_SAMPLES);
      const buffer = this.frame.buffer;
      this.port.postMessage({ pcm: buffer, level }, [buffer]);
      this.frame = new Int16Array(FRAME_SAMPLES);
      this.filled = 0;
      this.energy = 0;
    }
  }

  process(inputs: Float32Array[][]): boolean {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    if (this.ratio <= 1.0001) {
      for (let i = 0; i < channel.length; i++) this.emit(channel[i]);
      return true;
    }
    // A box filter over each output sample's span: enough for speech.
    for (let i = 0; i < channel.length; i++) {
      this.sum += channel[i];
      this.count++;
      this.phase += 1;
      if (this.phase >= this.ratio) {
        this.emit(this.sum / this.count);
        this.sum = 0;
        this.count = 0;
        this.phase -= this.ratio;
      }
    }
    return true;
  }
}

registerProcessor('row-bot-capture', RowBotCapture);
