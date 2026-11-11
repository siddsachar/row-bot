/**
 * Speech loudness for lip sync. The talk controls set it from their analyser
 * while Buddy's voice plays; living Buddies read it every frame. A level not
 * refreshed for a quarter second counts as silence.
 */
let level = 0;
let updated = -Infinity;

export function setBuddyVoiceLevel(value: number) {
  level = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0;
  updated = performance.now();
}

export function buddyVoiceLevel(now = performance.now()): number {
  return now - updated < 250 ? level : 0;
}
