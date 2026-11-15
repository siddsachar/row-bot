/**
 * Authored one-shot motions. Each track is a list of additive offsets at the
 * gesture's keys (fractions of its duration), interpolated smoothly; every
 * track starts and ends at 0, so a gesture blends onto any pose. Lengths are
 * in art units (negative y is up), angles in radians.
 */
export type GestureChannel =
  | 'x'
  | 'y'
  | 'lean'
  | 'squash'
  | 'bodyTilt'
  | 'headTilt'
  | 'headY'
  | 'turnX'
  | 'turnY'
  | 'gazeX'
  | 'gazeY'
  | 'eyeOpen'
  | 'mouth'
  | 'happy'
  | 'worried'
  | 'focused'
  | 'surprised'
  /** Every limb's raise. */
  | 'limbs'
  /** Wings only, on top of limbs (a flap). */
  | 'wings';

export type Gesture = {
  duration: number;
  /** A higher priority gesture fades out lower ones on the same channels. */
  priority: number;
  keys: number[];
  tracks: Partial<Record<GestureChannel, number[]>>;
  /** Moves the whole body (suppressed under reduced motion, quiet hops become perks). */
  travels?: boolean;
  /** Fraction of the duration at which sparkles burst. */
  sparkle?: number;
};

const g = (
  duration: number,
  priority: number,
  keys: number[],
  tracks: Gesture['tracks'],
  extra: Partial<Gesture> = {},
): Gesture => ({ duration, priority, keys, tracks, ...extra });

export const GESTURES = {
  blink: g(0.17, 1, [0, 0.4, 0.6, 1], { eyeOpen: [0, -1, -1, 0] }),
  hop: g(
    0.72,
    3,
    [0, 0.16, 0.3, 0.5, 0.7, 0.84, 1],
    {
      squash: [0, -0.12, 0.1, 0.03, -0.11, 0.03, 0],
      y: [0, 0.006, -0.065, -0.09, 0, 0, 0],
      happy: [0, 0.3, 0.7, 0.8, 0.7, 0.4, 0],
      limbs: [0, -0.1, 0.55, 0.65, 0.25, 0.1, 0],
      wings: [0, -0.2, 0.5, -0.3, 0.35, 0, 0],
    },
    { travels: true },
  ),
  doubleHop: g(
    1.5,
    3,
    [0, 0.08, 0.16, 0.26, 0.36, 0.44, 0.52, 0.64, 0.74, 0.86, 1],
    {
      squash: [0, -0.12, 0.11, -0.1, 0.03, -0.12, 0.12, 0.03, -0.11, 0.03, 0],
      y: [0, 0.006, -0.075, 0, -0.012, 0.006, -0.1, -0.07, 0, 0, 0],
      lean: [0, 0, -0.08, 0, 0.04, 0, 0.1, 0.06, -0.04, 0.02, 0],
      happy: [0, 0.4, 0.8, 0.8, 0.8, 0.8, 0.9, 0.9, 0.8, 0.5, 0],
      limbs: [0, -0.1, 0.7, 0.2, 0.3, -0.1, 0.8, 0.7, 0.3, 0.1, 0],
      wings: [0, -0.2, 0.5, -0.3, 0.2, -0.2, 0.6, -0.3, 0.3, 0, 0],
    },
    { travels: true, sparkle: 0.42 },
  ),
  nod: g(0.6, 2, [0, 0.3, 0.55, 0.78, 1], {
    turnY: [0, 0.28, -0.06, 0.12, 0],
    headY: [0, 0.012, -0.002, 0.005, 0],
    happy: [0, 0.25, 0.3, 0.2, 0],
  }),
  stepNod: g(0.5, 1, [0, 0.35, 0.7, 1], {
    turnY: [0, 0.16, -0.03, 0],
    headY: [0, 0.006, 0, 0],
  }),
  typeBob: g(0.36, 1, [0, 0.5, 1], {
    headY: [0, 0.005, 0],
    turnY: [0, 0.06, 0],
  }),
  lookAround: g(2.6, 1, [0, 0.14, 0.4, 0.56, 0.82, 1], {
    turnX: [0, -0.32, -0.3, 0.3, 0.32, 0],
    gazeX: [0, -0.85, -0.8, 0.8, 0.85, 0],
    headTilt: [0, -0.04, -0.04, 0.04, 0.04, 0],
  }),
  stretch: g(1.5, 2, [0, 0.14, 0.4, 0.68, 0.84, 1], {
    squash: [0, -0.08, 0.2, 0.2, -0.06, 0],
    limbs: [0, -0.05, 0.75, 0.75, 0.1, 0],
    eyeOpen: [0, -0.4, -0.85, -0.85, 0.1, 0],
    happy: [0, 0, 0.3, 0.4, 0.3, 0],
  }),
  yawn: g(1.8, 2, [0, 0.2, 0.45, 0.75, 1], {
    mouth: [0, 0.6, 0.95, 0.4, 0],
    eyeOpen: [0, -0.4, -0.75, -0.5, 0],
    squash: [0, 0.05, 0.12, 0.02, 0],
    headTilt: [0, -0.06, -0.1, -0.04, 0],
  }),
  wiggle: g(0.9, 1, [0, 0.2, 0.4, 0.6, 0.8, 1], {
    bodyTilt: [0, 0.08, -0.08, 0.06, -0.04, 0],
    headTilt: [0, -0.04, 0.05, -0.03, 0.02, 0],
    wings: [0, 0.3, -0.2, 0.25, -0.1, 0],
  }),
  boop: g(0.55, 3, [0, 0.18, 0.4, 0.7, 1], {
    squash: [0, -0.2, 0.1, -0.04, 0],
    happy: [0, 0.7, 0.9, 0.6, 0],
    eyeOpen: [0, -0.75, -0.6, -0.2, 0],
    limbs: [0, -0.15, 0.3, 0.1, 0],
  }),
  worry: g(1.1, 2, [0, 0.12, 0.26, 0.4, 0.55, 0.75, 1], {
    x: [0, 0.012, -0.012, 0.01, -0.006, 0, 0],
    squash: [0, -0.1, -0.1, -0.09, -0.08, -0.05, 0],
    worried: [0, 0.7, 0.8, 0.8, 0.8, 0.6, 0],
    gazeY: [0, 0.6, 0.6, 0.5, 0.4, 0.2, 0],
    limbs: [0, -0.3, -0.35, -0.35, -0.3, -0.2, 0],
  }),
  sigh: g(1.1, 2, [0, 0.25, 0.55, 0.8, 1], {
    squash: [0, 0.06, -0.1, -0.04, 0],
    y: [0, -0.005, 0.008, 0.002, 0],
    eyeOpen: [0, -0.2, -0.4, -0.15, 0],
    limbs: [0, 0.1, -0.25, -0.1, 0],
  }),
  perk: g(0.5, 2, [0, 0.3, 0.6, 1], {
    squash: [0, -0.07, 0.11, 0],
    y: [0, 0, -0.035, 0],
    surprised: [0, 0.45, 0.5, 0],
    limbs: [0, 0.1, 0.35, 0],
  }),
  tap: g(0.85, 1, [0, 0.22, 0.45, 0.68, 1], {
    y: [0, -0.03, 0, -0.016, 0],
    squash: [0, 0.07, -0.07, 0.03, 0],
    limbs: [0, 0.45, 0.05, 0.35, 0],
    surprised: [0, 0.2, 0.1, 0.15, 0],
  }),
  peek: g(0.7, 2, [0, 0.3, 0.65, 1], {
    turnX: [0, 0.2, 0.08, 0],
    squash: [0, -0.05, 0.06, 0],
    limbs: [0, 0.3, 0.2, 0],
    surprised: [0, 0.3, 0.1, 0],
  }),
  land: g(0.5, 3, [0, 0.2, 0.5, 0.75, 1], {
    squash: [0, -0.26, 0.1, -0.04, 0],
    limbs: [0, 0.3, -0.1, 0.05, 0],
  }),
} satisfies Record<string, Gesture>;

export type GestureName = keyof typeof GESTURES;

/** Smooth (Catmull–Rom) interpolation of a track at fraction u. */
export function sampleTrack(
  keys: number[],
  values: number[],
  u: number,
): number {
  if (u <= 0 || u >= 1) return 0;
  let i = 0;
  while (i < keys.length - 2 && u > keys[i + 1]) i++;
  const k0 = keys[i];
  const k1 = keys[i + 1];
  const t = k1 > k0 ? (u - k0) / (k1 - k0) : 0;
  const p0 = values[Math.max(0, i - 1)];
  const p1 = values[i];
  const p2 = values[i + 1];
  const p3 = values[Math.min(values.length - 1, i + 2)];
  const t2 = t * t;
  const t3 = t2 * t;
  return (
    0.5 *
    (2 * p1 +
      (-p0 + p2) * t +
      (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2 +
      (-p0 + 3 * p1 - 3 * p2 + p3) * t3)
  );
}
