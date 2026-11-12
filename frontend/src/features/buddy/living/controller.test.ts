import { describe, expect, it } from 'vitest';
import {
  MotionController,
  SLEEP_AFTER,
  behaviourFor,
  type ControllerInput,
  type Frame,
} from './controller';
import { parseRig } from './rig';

const spec = parseRig({
  family: 'winged',
  bones: [
    { id: 'body', pivot: [0.5, 0.7] },
    {
      id: 'head',
      parent: 'body',
      pivot: [0.5, 0.58],
      center: [0.5, 0.4],
      radius: [0.24, 0.22],
    },
    { id: 'wing_l', parent: 'body', pivot: [0.36, 0.64], tip: [0.15, 0.7] },
    { id: 'wing_r', parent: 'body', pivot: [0.64, 0.64], tip: [0.85, 0.7] },
  ],
  eyes: [{ center: [0.45, 0.42], radius: [0.05, 0.06] }],
  personality: { bounce: 0.5, tempo: 0.5, curiosity: 0.5 },
});

const input = (changes: Partial<ControllerInput> = {}): ControllerInput => ({
  activity: 'idle',
  animation: 'idle_breathe',
  eventId: 1,
  mood: 'curious',
  energy: 70,
  focus: 30,
  alert: 0,
  intensity: 'normal',
  reducedMotion: false,
  pointer: null,
  pointerAge: Infinity,
  voice: 0,
  workSide: 1,
  ...changes,
});

/** Step a controller for some seconds at 60 fps, collecting every frame. */
function run(
  controller: MotionController,
  seconds: number,
  changes: Partial<ControllerInput> = {},
) {
  const frames: Frame[] = [];
  for (let i = 0; i < Math.round(seconds * 60); i++)
    frames.push(controller.update(1 / 60, input(changes)));
  return frames;
}

describe('behaviourFor', () => {
  it("prefers the conversation's activity, then the brain's animation", () => {
    expect(behaviourFor('approval', 'idle_breathe')).toBe('tap_glass');
    expect(behaviourFor('completed', 'think_loop')).toBe('celebrate_small');
    expect(behaviourFor('idle', 'pack_bag')).toBe('pack_bag');
    expect(behaviourFor('idle', 'something_new')).toBe('idle_breathe');
  });
});

describe('MotionController', () => {
  it('hops when work completes, then settles', () => {
    const controller = new MotionController(spec, 7);
    run(controller, 2);
    const hop = run(controller, 1.2, { activity: 'completed', eventId: 2 });
    expect(Math.min(...hop.map((frame) => frame.pose.y))).toBeLessThan(-0.04);
    expect(Math.max(...hop.map((frame) => frame.pose.happy))).toBeGreaterThan(
      0.5,
    );
    expect(Math.max(...hop.map((frame) => frame.fps))).toBe(60);
    const after = run(controller, 3, { activity: 'completed', eventId: 2 });
    expect(Math.abs(after.at(-1)!.pose.y)).toBeLessThan(0.03);
  });

  it('never jumps when work is interrupted mid-gesture', () => {
    const controller = new MotionController(spec, 3);
    run(controller, 1);
    const sequence = [
      ...run(controller, 0.35, { activity: 'tool', eventId: 2 }),
      ...run(controller, 0.3, { activity: 'approval', eventId: 3 }),
      ...run(controller, 0.25, { activity: 'stopping', eventId: 4 }),
      ...run(controller, 0.4, { activity: 'completed', eventId: 5 }),
      ...run(controller, 0.2, { activity: 'error', eventId: 6 }),
    ];
    const channels = [
      'x',
      'y',
      'lean',
      'squash',
      'headTilt',
      'turnX',
      'turnY',
    ] as const;
    for (let i = 1; i < sequence.length; i++)
      for (const channel of channels)
        expect(
          Math.abs(sequence[i].pose[channel] - sequence[i - 1].pose[channel]),
        ).toBeLessThan(0.05);
  });

  it('blinks on its own while idle', () => {
    const frames = run(new MotionController(spec, 11), 12);
    expect(Math.min(...frames.map((frame) => frame.pose.eyeOpen))).toBeLessThan(
      0.2,
    );
  });

  it('naps after a long idle and wakes for work', () => {
    const controller = new MotionController(spec, 5);
    for (let t = 0; t < SLEEP_AFTER + 5; t += 0.1)
      controller.update(0.1, input());
    const asleep = run(controller, 2);
    expect(asleep.at(-1)!.pose.eyeOpen).toBeLessThan(0.1);
    expect(asleep.at(-1)!.fps).toBeLessThanOrEqual(24);
    const awake = run(controller, 2.5, { activity: 'thinking', eventId: 2 });
    expect(awake.at(-1)!.pose.eyeOpen).toBeGreaterThan(0.6);
  });

  it('looks toward the cursor', () => {
    const controller = new MotionController(spec, 2);
    const frames = run(controller, 1, {
      pointer: { x: 1.2, y: -0.2 },
      pointerAge: 0.1,
    });
    expect(frames.at(-1)!.pose.gazeX).toBeGreaterThan(0.6);
    expect(frames.at(-1)!.pose.turnX).toBeGreaterThan(0.05);
  });

  it('opens its mouth with its voice', () => {
    const controller = new MotionController(spec, 2);
    expect(
      run(controller, 0.3, { voice: 0.6 }).at(-1)!.pose.mouth,
    ).toBeGreaterThan(0.5);
    expect(run(controller, 0.4, { voice: 0 }).at(-1)!.pose.mouth).toBeLessThan(
      0.1,
    );
  });

  it('keeps still under reduced motion but still shows how it feels', () => {
    const controller = new MotionController(spec, 9);
    run(controller, 1, { reducedMotion: true });
    const frames = run(controller, 1.5, {
      activity: 'completed',
      eventId: 2,
      reducedMotion: true,
    });
    for (const frame of frames) {
      expect(frame.pose.y).toBe(0);
      expect(frame.pose.squash).toBe(0);
      expect(frame.pose.breathe).toBe(0);
      expect(frame.pose.limbs.wing_l).toBeCloseTo(0, 6);
      expect(frame.fps).toBe(15);
    }
    expect(
      Math.max(...frames.map((frame) => frame.pose.happy)),
    ).toBeGreaterThan(0.4);
  });

  it('moves less on quiet and more on expressive', () => {
    const peak = (intensity: ControllerInput['intensity']) => {
      const controller = new MotionController(spec, 4);
      run(controller, 1, { intensity });
      const frames = run(controller, 1.2, {
        activity: 'completed',
        eventId: 2,
        intensity,
      });
      return -Math.min(...frames.map((frame) => frame.pose.y));
    };
    expect(peak('quiet')).toBeLessThan(peak('normal'));
    expect(peak('expressive')).toBeGreaterThan(peak('normal'));
  });

  it('replays a one-shot behaviour for a new event, not for the same one', () => {
    const controller = new MotionController(spec, 6);
    run(controller, 1);
    run(controller, 3, { activity: 'completed', eventId: 2 });
    const same = run(controller, 0.8, { activity: 'completed', eventId: 2 });
    expect(Math.min(...same.map((frame) => frame.pose.y))).toBeGreaterThan(
      -0.03,
    );
    const again = run(controller, 0.8, { activity: 'completed', eventId: 3 });
    expect(Math.min(...again.map((frame) => frame.pose.y))).toBeLessThan(-0.04);
  });

  it('is repeatable for the same seed', () => {
    const a = run(new MotionController(spec, 42), 5).map(
      (frame) => frame.pose.gazeX,
    );
    const b = run(new MotionController(spec, 42), 5).map(
      (frame) => frame.pose.gazeX,
    );
    expect(a).toEqual(b);
  });
});
