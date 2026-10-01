import { describe, expect, it } from 'vitest';
import { bounds, Glide, SETTLE_EASE_MS } from './knowledge-motion';

const FRAME = 1000 / 60;

/** Distance from each point to the target, the largest of them. */
const gap = (glide: Glide) => {
  let largest = 0;
  for (let index = 0; index < glide.at.length; index += 2)
    largest = Math.max(
      largest,
      Math.hypot(
        glide.target[index] - glide.at[index],
        glide.target[index + 1] - glide.at[index + 1],
      ),
    );
  return largest;
};

/** Frames until every point is within `share` of the first gap. */
function framesToRest(glide: Glide, share: number, frame = FRAME) {
  const first = gap(glide);
  let frames = 0;
  while (gap(glide) > first * share && frames < 10_000) {
    glide.step(frame);
    frames += 1;
  }
  return frames;
}

describe('Glide', () => {
  it('moves gradually from rest: no frame jumps, the start is gentle', () => {
    const glide = new Glide([0, 0, 10, 10], [600, 0, 10, 610]);
    const moves: number[] = [];
    for (let frame = 0; frame < 300; frame += 1) {
      const before = Array.from(glide.at);
      glide.step(FRAME);
      moves.push(Math.hypot(glide.at[0] - before[0], glide.at[1] - before[1]));
    }
    // A 600-unit trip never moves more than 1.5% of it in one frame.
    expect(Math.max(...moves)).toBeLessThan(9);
    // It speeds up from rest, then slows into place.
    const fastest = moves.indexOf(Math.max(...moves));
    expect(moves[0]).toBeLessThan(moves[fastest] / 5);
    expect(fastest * FRAME).toBeGreaterThan(SETTLE_EASE_MS * 0.7);
    expect(moves.at(-1)!).toBeLessThan(moves[fastest] / 20);
  });

  it('approaches a still target without ever overshooting it', () => {
    const glide = new Glide([0, 0, -50, 20], [300, -120, 40, 20]);
    let previous = gap(glide);
    for (let frame = 0; frame < 600; frame += 1) {
      glide.step(FRAME);
      const now = gap(glide);
      expect(now).toBeLessThanOrEqual(previous);
      previous = now;
      expect(glide.at[0]).toBeLessThanOrEqual(300);
      expect(glide.at[1]).toBeGreaterThanOrEqual(-120);
      expect(glide.at[2]).toBeLessThanOrEqual(40);
    }
  });

  it('comes to rest in a few seconds, at any frame rate', () => {
    const at60 = framesToRest(new Glide([0, 0], [500, 500]), 1e-3);
    expect(at60 * FRAME).toBeGreaterThan(3000);
    expect(at60 * FRAME).toBeLessThan(6000);
    // Most of the trip shows: a fifth is still left after a second.
    const second = new Glide([0, 0], [500, 500]);
    for (let frame = 0; frame < 60; frame += 1) second.step(FRAME);
    expect(gap(second)).toBeGreaterThan(gap(new Glide([0, 0], [500, 500])) / 5);
    // A slow device's long frames cover the same ground in the same time.
    const at20 = framesToRest(new Glide([0, 0], [500, 500]), 1e-3, 50);
    expect(Math.abs(at20 * 50 - at60 * FRAME)).toBeLessThan(400);
  });

  it('follows a target that moves on, and lands exactly on it', () => {
    const glide = new Glide([0, 0], [100, 0]);
    for (let frame = 0; frame < 30; frame += 1) glide.step(FRAME);
    glide.aim([100, 80]);
    for (let frame = 0; frame < 30; frame += 1) glide.step(FRAME);
    expect(glide.at[0]).toBeGreaterThan(0);
    expect(glide.at[1]).toBeGreaterThan(0);
    expect(glide.gap()).toBeGreaterThan(1);

    const final = new Float32Array([100.123, 79.987]);
    glide.aim(final);
    glide.land();
    expect(Array.from(glide.at)).toEqual(Array.from(final));
    expect(glide.gap()).toBe(0);
    // Landed, a step changes nothing.
    glide.step(FRAME);
    expect(Array.from(glide.at)).toEqual(Array.from(final));
  });

  it('reports the largest gap left, whichever way it points', () => {
    const glide = new Glide([0, 0, 5, 5], [3, -4, 5, -2]);
    expect(glide.gap()).toBe(7);
  });
});

describe('bounds', () => {
  it('boxes x, y pairs as min x, min y, max x, max y', () => {
    expect(Array.from(bounds([3, -1, -2, 4, 0, 0]))).toEqual([-2, -1, 3, 4]);
    expect(Array.from(bounds([7, 8]))).toEqual([7, 8, 7, 8]);
  });
});
