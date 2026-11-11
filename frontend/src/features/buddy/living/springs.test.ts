import { describe, expect, it } from 'vitest';
import { Spring } from './springs';

const run = (spring: Spring, target: number, seconds: number, dt = 1 / 60) => {
  const values: number[] = [];
  for (let t = 0; t < seconds; t += dt) values.push(spring.step(target, dt));
  return values;
};

describe('Spring', () => {
  it('settles on its target', () => {
    const values = run(new Spring(0, 3, 1), 1, 3);
    expect(values.at(-1)).toBeCloseTo(1, 3);
  });

  it('never overshoots when critically damped', () => {
    const values = run(new Spring(0, 3, 1), 1, 3);
    expect(Math.max(...values)).toBeLessThanOrEqual(1.0001);
  });

  it('overshoots when underdamped', () => {
    const values = run(new Spring(0, 3, 0.5), 1, 3);
    expect(Math.max(...values)).toBeGreaterThan(1.05);
  });

  it('anticipates (moves away first) with a negative response', () => {
    const values = run(new Spring(0, 3, 1, -0.5), 1, 0.2);
    expect(Math.min(...values)).toBeLessThan(0);
  });

  it('stays bounded and still settles with long, uneven frames', () => {
    const spring = new Spring(0, 8, 0.4);
    const frames = [0.2, 0.5, 0.03, 0.25, 0.1, 0.4];
    const values = Array.from({ length: 120 }, (_, i) =>
      spring.step(1, frames[i % frames.length]),
    );
    expect(
      values.every((value) => Number.isFinite(value) && Math.abs(value) < 4),
    ).toBe(true);
    expect(values.at(-1)).toBeCloseTo(1, 2);
  });

  it('jumps without motion on reset', () => {
    const spring = new Spring(0, 3, 1);
    run(spring, 1, 0.3);
    spring.reset(0.5);
    expect(spring.velocity).toBe(0);
    expect(spring.step(0.5, 1 / 60)).toBeCloseTo(0.5, 6);
  });
});
