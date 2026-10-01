/**
 * Each of the glide's two easing stages closes about 63% of what is left in
 * this time: the drawn graph starts from rest, is quickest after about this
 * long and comes to rest some seconds later, without overshooting.
 */
export const SETTLE_EASE_MS = 600;

/**
 * Eases points (x, y pairs) toward a target that may keep moving, like the
 * knowledge graph's layout while it is worked out (B250). Two exponential
 * stages in a row act as a critically damped spring: smooth from the first
 * frame, frame-rate independent, and never past the target.
 */
export class Glide {
  readonly at: Float64Array;
  readonly target: Float64Array;
  private readonly middle: Float64Array;

  constructor(
    start: ArrayLike<number>,
    target: ArrayLike<number> = start,
    private readonly ease = SETTLE_EASE_MS,
  ) {
    this.at = Float64Array.from(start);
    this.middle = Float64Array.from(start);
    this.target = Float64Array.from(target);
  }

  aim(target: ArrayLike<number>) {
    this.target.set(target);
  }

  step(ms: number) {
    const share = 1 - Math.exp(-ms / this.ease);
    const { at, middle, target } = this;
    for (let index = 0; index < at.length; index += 1) {
      middle[index] += (target[index] - middle[index]) * share;
      at[index] += (middle[index] - at[index]) * share;
    }
  }

  /** The largest distance left along either axis, momentum included. */
  gap() {
    const { at, middle, target } = this;
    let largest = 0;
    for (let index = 0; index < at.length; index += 1)
      largest = Math.max(
        largest,
        Math.abs(target[index] - at[index]),
        Math.abs(target[index] - middle[index]),
      );
    return largest;
  }

  land() {
    this.at.set(this.target);
    this.middle.set(this.target);
  }
}

/** The box around x, y pairs: min x, min y, max x, max y. */
export function bounds(points: ArrayLike<number>) {
  const box = new Float64Array([Infinity, Infinity, -Infinity, -Infinity]);
  for (let index = 0; index < points.length; index += 2) {
    box[0] = Math.min(box[0], points[index]);
    box[1] = Math.min(box[1], points[index + 1]);
    box[2] = Math.max(box[2], points[index]);
    box[3] = Math.max(box[3], points[index + 1]);
  }
  return box;
}
