import { useEffect, useState } from 'react';

/**
 * Read aloud uses the operating system's speech engine only. Voices that are
 * not `localService` (for example cloud "Natural"/"Online" voices) would send
 * message text off the device, so they are never used.
 */
function speech(): SpeechSynthesis | null {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
    ? window.speechSynthesis
    : null;
}

export function localVoices(): SpeechSynthesisVoice[] {
  const engine = speech();
  if (!engine) return [];
  try {
    return engine.getVoices().filter((voice) => voice.localService);
  } catch {
    return [];
  }
}

function pickVoice(): SpeechSynthesisVoice | null {
  const voices = localVoices();
  const language = (navigator.language || 'en').toLowerCase();
  return (
    voices.find((voice) => voice.default) ??
    voices.find((voice) => voice.lang.toLowerCase() === language) ??
    voices.find((voice) =>
      voice.lang.toLowerCase().startsWith(language.split('-')[0]),
    ) ??
    voices[0] ??
    null
  );
}

/** Whether an on-device voice exists; voices can arrive after load. */
export function useLocalSpeechAvailable(): boolean {
  const [available, setAvailable] = useState(() => localVoices().length > 0);
  useEffect(() => {
    const engine = speech();
    if (!engine) return;
    const update = () => setAvailable(localVoices().length > 0);
    update();
    engine.addEventListener?.('voiceschanged', update);
    return () => engine.removeEventListener?.('voiceschanged', update);
  }, []);
  return available;
}

/** Plain words from Markdown: code, links and markup are not read out. */
export function speakableText(markdown: string): string {
  return markdown
    .replace(/```[\s\S]*?(```|$)/g, ' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+/gm, '')
    .replace(/(\*\*|__|~~|\*|_)/g, '')
    .replace(/\|/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 12000);
}

let speaking: { owner: string; utterance: SpeechSynthesisUtterance } | null =
  null;

/** Speak text; resolves when speech ends or is cancelled. */
export function speak(owner: string, text: string, onEnd: () => void): boolean {
  const engine = speech();
  const voice = pickVoice();
  if (!engine || !voice || !text) return false;
  engine.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.voice = voice;
  utterance.lang = voice.lang;
  const finish = () => {
    if (speaking?.utterance === utterance) speaking = null;
    onEnd();
  };
  utterance.addEventListener('end', finish);
  utterance.addEventListener('error', finish);
  speaking = { owner, utterance };
  engine.speak(utterance);
  return true;
}

export function stopSpeaking(owner?: string) {
  const engine = speech();
  if (!engine || (owner && speaking?.owner !== owner)) return;
  speaking = null;
  engine.cancel();
}
