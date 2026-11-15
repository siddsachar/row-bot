const MAX_SUBSTEP = 1 / 120;

/**
 * Second-order dynamics: every pose channel follows its target through a
 * spring, so a target may change on any frame without the pose snapping.
 *
 * f is the natural frequency (Hz), zeta the damping (1 = critically damped,
 * below 1 overshoots), r the initial response (0 eases in, 1 is immediate,
 * above 1 overshoots at the start, below 0 anticipates by moving away first).
 */
export class Spring {
  value: number;
  velocity = 0;
  private previousTarget: number;
  private k1: number;
  private k2: number;
  private k3: number;

  constructor(initial: number, f: number, zeta = 1, r = 0) {
    this.value = initial;
    this.previousTarget = initial;
    this.k1 = zeta / (Math.PI * f);
    this.k2 = 1 / (2 * Math.PI * f) ** 2;
    this.k3 = (r * zeta) / (2 * Math.PI * f);
  }

  /** Advance by dt seconds toward target; returns the new value. */
  step(target: number, dt: number): number {
    if (!(dt > 0)) return this.value;
    const total = Math.min(dt, 0.1);
    const targetVelocity = (target - this.previousTarget) / total;
    this.previousTarget = target;
    // Fixed small sub-steps: varying the step (and the stiffness that keeps a
    // long step stable) from frame to frame would pump energy into the spring.
    const count = Math.ceil(total / MAX_SUBSTEP);
    const h = total / count;
    const k2 = Math.max(this.k2, (h * h) / 2 + (h * this.k1) / 2, h * this.k1);
    for (let i = 0; i < count; i++) {
      this.value += h * this.velocity;
      this.velocity +=
        (h *
          (target +
            this.k3 * targetVelocity -
            this.value -
            this.k1 * this.velocity)) /
        k2;
    }
    return this.value;
  }

  /** Jump to a value with no motion (first frame, reduced motion). */
  reset(value: number) {
    this.value = value;
    this.previousTarget = value;
    this.velocity = 0;
  }
}
