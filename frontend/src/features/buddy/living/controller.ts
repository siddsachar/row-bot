import {
  GESTURES,
  sampleTrack,
  type GestureChannel,
  type GestureName,
} from './gestures';
import { restPose, type Pose } from './pose';
import { LIMB_IDS, type Family, type LimbId, type RigSpec } from './rig';
import { Spring } from './springs';

export type Intensity = 'quiet' | 'normal' | 'expressive';

export type ControllerInput = {
  /** The conversation's activity (snapshot.activity), or 'idle'. */
  activity: string;
  /** BuddyBrain's animation name (snapshot.status.animation). */
  animation: string;
  eventId: number;
  mood: string;
  energy: number;
  focus: number;
  alert: number;
  intensity: Intensity;
  reducedMotion: boolean;
  /** The cursor relative to Buddy's head, in head-heights; null if unknown. */
  pointer: { x: number; y: number } | null;
  /** Seconds since the cursor last moved. */
  pointerAge: number;
  /** Speech loudness, 0–1, while Buddy's voice plays. */
  voice: number;
  /** Which side Buddy's work is on: +1 right, −1 left. */
  workSide: 1 | -1;
};

type Expression = Partial<
  Record<'happy' | 'worried' | 'focused' | 'surprised', number>
>;
type GazeMode = 'viewer' | 'wander' | 'side' | 'up' | 'down' | 'read';
type Mode = {
  gaze: GazeMode;
  expression?: Expression;
  enter?: GestureName;
  /** Replay the entry gesture when the same behaviour gets a new event. */
  repeat?: boolean;
  loop?: { gesture: GestureName; every: [number, number] };
  /** Seconds after which the behaviour relaxes into idle, keeping a trace of its face. */
  settle?: number;
  turnX?: number;
  turnY?: number;
  headTilt?: number;
  lean?: number;
  squash?: number;
  limbs?: number;
  bob?: number;
  eyeOpen?: number;
  fidgets?: boolean;
  sleep?: boolean;
  /** Draw at least this often while in the behaviour. */
  fps?: number;
};

const MODES: Record<string, Mode> = {
  idle_breathe: { gaze: 'wander', fidgets: true },
  wake: {
    gaze: 'viewer',
    enter: 'stretch',
    expression: { happy: 0.25 },
    settle: 3,
    fidgets: true,
  },
  lean_in: {
    gaze: 'viewer',
    enter: 'perk',
    expression: { focused: 0.55 },
    headTilt: 0.04,
    squash: -0.02,
    bob: 0.6,
    fps: 30,
  },
  think_loop: {
    gaze: 'up',
    expression: { focused: 0.55 },
    turnX: -0.16,
    turnY: -0.1,
    headTilt: -0.07,
    loop: { gesture: 'wiggle', every: [3.5, 6] },
    bob: 0.5,
    fps: 30,
  },
  type_follow: {
    gaze: 'read',
    expression: { focused: 0.45, happy: 0.1 },
    turnY: 0.06,
    loop: { gesture: 'typeBob', every: [0.45, 0.85] },
    bob: 0.4,
    fps: 30,
  },
  tool_peek: {
    gaze: 'side',
    enter: 'peek',
    expression: { focused: 0.6 },
    turnX: 0.14,
    lean: 0.035,
    limbs: 0.15,
    loop: { gesture: 'stepNod', every: [1.8, 3.2] },
    fps: 30,
  },
  nod: {
    gaze: 'viewer',
    enter: 'nod',
    repeat: true,
    expression: { happy: 0.3 },
    settle: 2.5,
    fidgets: true,
  },
  tap_glass: {
    gaze: 'viewer',
    enter: 'perk',
    expression: { surprised: 0.35 },
    limbs: 0.1,
    loop: { gesture: 'tap', every: [1.9, 2.7] },
    fps: 30,
  },
  pause: {
    gaze: 'down',
    enter: 'sigh',
    repeat: true,
    bob: 0.6,
    settle: 3,
    fidgets: true,
  },
  celebrate_small: {
    gaze: 'viewer',
    enter: 'hop',
    repeat: true,
    expression: { happy: 0.55 },
    settle: 3,
    fidgets: true,
  },
  celebrate_big: {
    gaze: 'viewer',
    enter: 'doubleHop',
    repeat: true,
    expression: { happy: 0.65 },
    settle: 4,
    fidgets: true,
  },
  worry: {
    gaze: 'viewer',
    enter: 'worry',
    repeat: true,
    expression: { worried: 0.55 },
    limbs: -0.15,
    bob: 0.5,
    settle: 5,
  },
  pack_bag: {
    gaze: 'wander',
    enter: 'perk',
    expression: { happy: 0.3 },
    loop: { gesture: 'lookAround', every: [5, 8] },
    fps: 30,
  },
  step_check: {
    gaze: 'side',
    expression: { focused: 0.45 },
    turnX: 0.12,
    loop: { gesture: 'stepNod', every: [1.4, 2.4] },
    fps: 30,
  },
  // Proactive: something for you. A calm, held offer (one perk, then only
  // breathing, blinks and a slight head tilt), never a repeated demand.
  glance: {
    gaze: 'viewer',
    enter: 'perk',
    expression: { happy: 0.18, surprised: 0.12 },
    headTilt: 0.07,
    limbs: 0.12,
    fidgets: true,
  },
  listen: {
    gaze: 'viewer',
    enter: 'perk',
    expression: { surprised: 0.25 },
    headTilt: 0.12,
    limbs: 0.2,
    fps: 30,
  },
  ping: {
    gaze: 'viewer',
    enter: 'perk',
    repeat: true,
    expression: { surprised: 0.25 },
    settle: 2.5,
    fidgets: true,
  },
  sleep: { gaze: 'down', sleep: true },
};

const ACTIVITY_BEHAVIOUR: Record<string, string> = {
  thinking: 'think_loop',
  streaming: 'type_follow',
  tool: 'tool_peek',
  approval: 'tap_glass',
  stopping: 'pause',
  completed: 'celebrate_small',
  stopped: 'pause',
  error: 'worry',
  disconnected: 'pause',
};

const MOOD_FACE: Record<string, Expression & { eyeOpen?: number }> = {
  curious: { surprised: 0.08 },
  focused: { focused: 0.3 },
  excited: { happy: 0.3, surprised: 0.1 },
  concerned: { worried: 0.35 },
  proud: { happy: 0.35 },
  sleepy: { eyeOpen: 0.7 },
};

/** Long idle before Buddy naps (seconds). */
export const SLEEP_AFTER = 600;

export function behaviourFor(activity: string, animation: string): string {
  return (
    ACTIVITY_BEHAVIOUR[activity] ??
    (MODES[animation] ? animation : 'idle_breathe')
  );
}

/** A small seeded random source, so motion is repeatable under test. */
function random(seed: number) {
  let state = seed >>> 0 || 1;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Active = { name: GestureName; start: number; fadeFrom: number | null };

const MOVEMENT: ReadonlySet<GestureChannel> = new Set([
  'x',
  'y',
  'lean',
  'squash',
  'bodyTilt',
  'headTilt',
  'headY',
  'turnX',
  'turnY',
  'limbs',
  'wings',
]);
const FADE = 0.16;
const MAX_TURN = 0.26;
const RAISED: ReadonlySet<LimbId> = new Set([
  'arm_l',
  'arm_r',
  'wing_l',
  'wing_r',
  'ear_l',
  'ear_r',
]);
const clamp = (value: number, low: number, high: number) =>
  Math.max(low, Math.min(high, value));

export type Frame = {
  pose: Pose;
  /** Start times (controller seconds) of sparkle bursts still showing. */
  sparkles: number[];
  /** How often this instance wants to be drawn now. */
  fps: number;
  time: number;
};

export class MotionController {
  private time = 0;
  private readonly next: () => number;
  private readonly family: Family;
  private readonly limbs: LimbId[];
  private readonly personality: RigSpec['personality'];
  private readonly springs: Record<string, Spring>;
  private readonly limbSprings: Record<string, Spring>;
  private behaviour = 'idle_breathe';
  private behaviourSince = 0;
  private eventId = -1;
  private active: Active[] = [];
  private sparkles: number[] = [];
  private nextBlink = 2;
  private nextLoop = Infinity;
  private nextFidget = 12;
  private nextSaccade = 1;
  private saccade = { x: 0, y: 0 };
  private idleSince = 0;
  private sleeping = false;
  private started = false;

  constructor(spec: RigSpec, seed = 1) {
    this.next = random(seed);
    this.family = spec.family;
    this.limbs = spec.bones
      .map((bone) => bone.id)
      .filter((id): id is LimbId =>
        (LIMB_IDS as readonly string[]).includes(id),
      );
    this.personality = spec.personality;
    const bounce = spec.personality.bounce;
    const springy = 0.82 - bounce * 0.3;
    const s = (f: number, zeta: number, r = 0) => new Spring(0, f, zeta, r);
    this.springs = {
      x: s(2.2, 0.75),
      y: s(2.2, springy),
      lean: s(1.8, 0.75, -0.15),
      squash: s(3, springy),
      bodyTilt: s(1.6, 0.8),
      headTilt: s(2.2, 0.7),
      headX: s(3, 0.85),
      headY: s(3, 0.85),
      turnX: s(2.4, 0.85),
      turnY: s(2.4, 0.85),
      gazeX: s(7, 1),
      gazeY: s(7, 1),
      eyeOpen: new Spring(1, 5, 1),
      mouth: s(14, 1),
      happy: s(2.5, 1),
      worried: s(2.5, 1),
      focused: s(2.5, 1),
      surprised: s(3, 1),
    };
    this.limbSprings = Object.fromEntries(
      LIMB_IDS.map((id) => [id, s(2.4, springy - 0.1)]),
    );
  }

  /** Play a one-shot gesture now (a click's boop, a landing). */
  trigger(name: GestureName) {
    this.play(name);
    this.wake();
  }

  private wake() {
    this.idleSince = this.time;
    if (this.sleeping) {
      this.sleeping = false;
      this.play('stretch');
    }
  }

  private play(name: GestureName) {
    const gesture = GESTURES[name];
    if (
      this.active.some(
        (entry) => entry.name === name && entry.fadeFrom === null,
      )
    )
      return;
    const channels = Object.keys(gesture.tracks);
    for (const entry of this.active) {
      const other = GESTURES[entry.name];
      if (
        entry.fadeFrom === null &&
        other.priority <= gesture.priority &&
        name !== 'blink' &&
        entry.name !== 'blink' &&
        Object.keys(other.tracks).some((channel) => channels.includes(channel))
      )
        entry.fadeFrom = this.time;
    }
    this.active.push({ name, start: this.time, fadeFrom: null });
    if (gesture.sparkle !== undefined)
      this.sparkles.push(this.time + gesture.sparkle * gesture.duration);
  }

  private between([low, high]: [number, number]) {
    return low + (high - low) * this.next();
  }

  update(dt: number, input: ControllerInput): Frame {
    const step = clamp(dt, 0, 0.1);
    this.time += step;
    const now = this.time;
    const quiet = input.intensity === 'quiet';
    const movement = input.reducedMotion
      ? 0
      : quiet
        ? 0.35
        : input.intensity === 'expressive'
          ? 1.3
          : 1;
    const tempo =
      (0.75 + clamp(input.energy, 0, 100) / 200) *
      (0.85 + this.personality.tempo * 0.3);

    // Behaviour changes start their entry gesture once.
    const behaviour = behaviourFor(input.activity, input.animation);
    const mode = MODES[behaviour];
    const changed = behaviour !== this.behaviour;
    const repeated =
      !changed && mode.repeat && input.eventId !== this.eventId && this.started;
    if (changed || repeated) {
      this.behaviour = behaviour;
      this.behaviourSince = now;
      if (behaviour !== 'idle_breathe' && behaviour !== 'sleep') this.wake();
      if (mode.enter && this.started) this.play(mode.enter);
      this.nextLoop = mode.loop
        ? now + this.between(mode.loop.every) * 0.5
        : Infinity;
    }
    this.eventId = input.eventId;
    this.started = true;
    const settled =
      mode.settle !== undefined && now - this.behaviourSince > mode.settle;
    const resting = settled || behaviour === 'idle_breathe';
    const shape: Mode = settled
      ? { ...MODES.idle_breathe, expression: scaled(mode.expression, 0.35) }
      : mode;

    // Napping after a long idle; anything else wakes Buddy.
    if (!resting && behaviour !== 'sleep') this.idleSince = now;
    if (!this.sleeping && resting && now - this.idleSince > SLEEP_AFTER)
      this.sleeping = true;
    const asleep = this.sleeping || !!shape.sleep;

    if (shape.loop && now >= this.nextLoop) {
      this.play(shape.loop.gesture);
      this.nextLoop = now + this.between(shape.loop.every) / tempo;
    }
    if (!asleep && now >= this.nextBlink) {
      if (!this.active.some((entry) => GESTURES[entry.name].travels)) {
        this.play('blink');
        if (this.next() < 0.18) this.nextBlink = now + 0.28;
        else
          this.nextBlink =
            now + clamp(-Math.log(1 - this.next()) * 3.6, 1.4, 8);
      } else this.nextBlink = now + 0.4;
    }
    if (shape.fidgets && !asleep && now >= this.nextFidget) {
      const roll = this.next();
      const curious = this.personality.curiosity;
      this.play(
        roll < 0.3 + curious * 0.3
          ? 'lookAround'
          : input.energy < 40 && roll > 0.85
            ? 'yawn'
            : roll > 0.8
              ? 'stretch'
              : 'wiggle',
      );
      this.nextFidget =
        now + (9 + this.next() * 14) * (1.3 - curious * 0.6) * (quiet ? 2 : 1);
    }

    // Where the eyes go.
    const side = input.workSide;
    const pointerFresh = input.pointer !== null && input.pointerAge < 2.5;
    if (now >= this.nextSaccade) {
      const reach =
        shape.gaze === 'wander'
          ? 0.55 * (0.6 + this.personality.curiosity * 0.6)
          : 0.12;
      this.saccade =
        this.next() < 0.3
          ? { x: 0, y: 0 }
          : {
              x: (this.next() * 2 - 1) * reach,
              y: (this.next() * 2 - 1) * reach * 0.6,
            };
      this.nextSaccade = now + 0.7 + this.next() * 2.2;
    }
    let gazeX = this.saccade.x;
    let gazeY = this.saccade.y;
    if (shape.gaze === 'side')
      [gazeX, gazeY] = [0.72 * side + gazeX * 0.3, 0.12];
    if (shape.gaze === 'up') [gazeX, gazeY] = [-0.45 * side, -0.7];
    if (shape.gaze === 'down') [gazeX, gazeY] = [0, 0.65];
    if (shape.gaze === 'read')
      [gazeX, gazeY] = [((now * 0.8 * tempo) % 1) * 1.1 - 0.55, 0.28];
    if (pointerFresh) {
      const p = input.pointer!;
      const follow =
        shape.gaze === 'viewer' || shape.gaze === 'wander' ? 1 : 0.3;
      const px = clamp(p.x * 1.1, -1, 1);
      const py = clamp(p.y * 1.1, -1, 1);
      gazeX = gazeX * (1 - follow) + px * follow;
      gazeY = gazeY * (1 - follow) + py * follow;
    } else if (shape.gaze === 'viewer')
      [gazeX, gazeY] = [this.saccade.x * 0.25, this.saccade.y * 0.25];
    if (asleep) [gazeX, gazeY] = [0, 0.4];

    // Face: behaviour, mood, focus and alert.
    const face: Required<Expression> & { eyeOpen: number } = {
      happy: 0,
      worried: 0,
      focused: 0,
      surprised: 0,
      eyeOpen: shape.eyeOpen ?? 1,
      ...MOOD_FACE[input.mood],
    };
    for (const [key, value] of Object.entries(shape.expression ?? {}))
      face[key as keyof Expression] = Math.max(
        face[key as keyof Expression],
        value ?? 0,
      );
    if (!resting) face.focused += (clamp(input.focus, 0, 100) / 100) * 0.2;
    face.surprised += (clamp(input.alert, 0, 100) / 100) * 0.15;
    if (asleep) face.eyeOpen = 0;

    const breathePeriod = (asleep ? 5.2 : 3.6) / tempo;
    const breath = Math.sin((now / breathePeriod) * Math.PI * 2);
    const energyAmp = 0.6 + (clamp(input.energy, 0, 100) / 100) * 0.6;
    const floats = this.family === 'floater' || this.family === 'winged';
    const bob =
      (shape.bob ?? 1) *
      energyAmp *
      (floats ? 0.014 : 0.004) *
      (asleep ? 0.4 : 1);
    const t = this.springs;
    const target = {
      x: 0,
      y:
        (floats ? Math.sin((now / 2.9) * Math.PI * 2) * bob : 0) * movement +
        (asleep ? 0.01 : 0),
      lean: (shape.lean ?? 0) * side * movement,
      squash:
        ((shape.squash ?? 0) +
          (clamp(input.alert, 0, 100) / 100) * 0.03 +
          (asleep ? -0.05 : 0)) *
        movement,
      bodyTilt:
        (shape.gaze === 'up' ? Math.sin(now * 0.9) * 0.025 : 0) * movement,
      headTilt: ((shape.headTilt ?? 0) * side + (asleep ? 0.09 : 0)) * movement,
      headX: 0,
      headY: (asleep ? 0.012 : 0) * movement,
      turnX: ((shape.turnX ?? 0) * side + gazeX * 0.2) * movement,
      turnY: ((shape.turnY ?? 0) + gazeY * 0.12) * movement,
      gazeX,
      gazeY,
      eyeOpen: face.eyeOpen,
      mouth: input.voice > 0.02 ? clamp(input.voice * 1.4, 0, 1) : 0,
      happy: clamp(face.happy, 0, 1),
      worried: clamp(face.worried, 0, 1),
      focused: clamp(face.focused, 0, 1),
      surprised: clamp(face.surprised, 0, 1),
    };
    const pose = restPose();
    for (const [key, spring] of Object.entries(t)) {
      const value = spring.step(target[key as keyof typeof target], step);
      (pose as unknown as Record<string, number>)[key] = value;
    }
    if (input.reducedMotion)
      for (const key of [
        'x',
        'y',
        'lean',
        'squash',
        'bodyTilt',
        'headTilt',
        'headX',
        'headY',
        'turnX',
        'turnY',
      ])
        (pose as unknown as Record<string, number>)[key] = 0;
    pose.breathe =
      (asleep ? 0.022 : 0.013) *
      energyAmp *
      (breath * 0.5 + 0.5) *
      (input.reducedMotion ? 0 : 1);
    for (const id of LIMB_IDS) {
      let limb = (shape.limbs ?? 0) + (asleep ? -0.15 : 0);
      if (id === 'tail') limb += Math.sin(now * 1.7 * tempo) * 0.14;
      if ((id === 'wing_l' || id === 'wing_r') && this.family === 'winged')
        limb += Math.sin(now * Math.PI * 2 * 1.1 * tempo) * 0.07 + 0.02;
      if (id === 'ear_l' || id === 'ear_r')
        limb += face.surprised * 0.25 - face.worried * 0.3;
      pose.limbs[id] = this.limbSprings[id].step(limb * movement, step);
    }

    // Gestures on top.
    this.active = this.active.filter((entry) => {
      const gesture = GESTURES[entry.name];
      const u = (now - entry.start) / gesture.duration;
      const weight =
        entry.fadeFrom === null ? 1 : 1 - (now - entry.fadeFrom) / FADE;
      if (u >= 1 || weight <= 0) return false;
      for (const [channel, values] of Object.entries(gesture.tracks) as [
        GestureChannel,
        number[],
      ][]) {
        let value = sampleTrack(gesture.keys, values, u) * weight;
        if (MOVEMENT.has(channel))
          value *=
            movement *
            (gesture.travels ? 0.6 + this.personality.bounce * 0.8 : 1);
        if (channel === 'limbs' || channel === 'wings') {
          // Tails, legs and tops have their own motion; gestures lift arms, wings and ears.
          for (const id of this.limbs)
            if (
              channel === 'limbs'
                ? RAISED.has(id)
                : id === 'wing_l' || id === 'wing_r'
            )
              pose.limbs[id] += value;
        } else (pose as unknown as Record<string, number>)[channel] += value;
      }
      return true;
    });
    pose.eyeOpen = clamp(pose.eyeOpen, 0, 1);
    pose.mouth = clamp(pose.mouth, 0, 1);
    for (const key of ['happy', 'worried', 'focused', 'surprised'] as const)
      pose[key] = clamp(pose[key], 0, 1);
    // Larger turns smear the trailing edge of a painted head.
    pose.turnX = clamp(pose.turnX, -MAX_TURN, MAX_TURN);
    pose.turnY = clamp(pose.turnY, -MAX_TURN * 0.8, MAX_TURN * 0.8);
    pose.gazeX = clamp(pose.gazeX, -1, 1);
    pose.gazeY = clamp(pose.gazeY, -1, 1);

    this.sparkles = this.sparkles.filter((start) => now - start < 1.4);
    const busy =
      this.active.some(
        (entry) => entry.name !== 'blink' || !input.reducedMotion,
      ) ||
      Math.abs(t.y.velocity) +
        Math.abs(t.turnX.velocity) +
        Math.abs(t.gazeX.velocity) >
        0.05 ||
      input.pointerAge < 1;
    const fps = input.reducedMotion
      ? 15
      : busy
        ? 60
        : Math.max(shape.fps ?? 15, floats ? 24 : 15);
    return { pose, sparkles: this.sparkles.slice(), fps, time: now };
  }
}

function scaled(
  expression: Expression | undefined,
  factor: number,
): Expression {
  return Object.fromEntries(
    Object.entries(expression ?? {}).map(([key, value]) => [
      key,
      (value ?? 0) * factor,
    ]),
  );
}
