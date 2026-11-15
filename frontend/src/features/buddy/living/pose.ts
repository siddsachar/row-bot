import { LIMB_IDS, type BuiltRig, type LimbId, type Point } from './rig';

/**
 * One frame of Buddy's body and face. Lengths are in art units (the art is
 * 1 × 1, y down), angles in radians (positive turns clockwise on screen).
 */
export type Pose = {
  x: number;
  y: number;
  lean: number;
  squash: number;
  bodyTilt: number;
  breathe: number;
  headTilt: number;
  headX: number;
  headY: number;
  /** Head turn: +x looks toward the viewer's right, +y looks down. */
  turnX: number;
  turnY: number;
  /** Where the eyes look, −1…1 on each axis. */
  gazeX: number;
  gazeY: number;
  eyeOpen: number;
  mouth: number;
  happy: number;
  worried: number;
  focused: number;
  surprised: number;
  /** Per limb: positive raises its tip (lifts a wing or arm, perks an ear). */
  limbs: Record<LimbId, number>;
};

export function restPose(): Pose {
  return {
    x: 0,
    y: 0,
    lean: 0,
    squash: 0,
    bodyTilt: 0,
    breathe: 0,
    headTilt: 0,
    headX: 0,
    headY: 0,
    turnX: 0,
    turnY: 0,
    gazeX: 0,
    gazeY: 0,
    eyeOpen: 1,
    mouth: 0,
    happy: 0,
    worried: 0,
    focused: 0,
    surprised: 0,
    limbs: Object.fromEntries(LIMB_IDS.map((id) => [id, 0])) as Record<
      LimbId,
      number
    >,
  };
}

/** A 2D affine transform: x' = a·x + c·y + e, y' = b·x + d·y + f. */
export type Affine = [number, number, number, number, number, number];

export const identity = (): Affine => [1, 0, 0, 1, 0, 0];

export function multiply(m: Affine, n: Affine): Affine {
  return [
    m[0] * n[0] + m[2] * n[1],
    m[1] * n[0] + m[3] * n[1],
    m[0] * n[2] + m[2] * n[3],
    m[1] * n[2] + m[3] * n[3],
    m[0] * n[4] + m[2] * n[5] + m[4],
    m[1] * n[4] + m[3] * n[5] + m[5],
  ];
}

/** Rotate by angle and scale by (sx, sy) about a pivot, then translate. */
export function about(
  pivot: Point,
  angle: number,
  sx = 1,
  sy = 1,
  tx = 0,
  ty = 0,
): Affine {
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const a = cos * sx;
  const b = sin * sx;
  const c = -sin * sy;
  const d = cos * sy;
  return [
    a,
    b,
    c,
    d,
    pivot[0] + tx - (a * pivot[0] + c * pivot[1]),
    pivot[1] + ty - (b * pivot[0] + d * pivot[1]),
  ];
}

export function apply(m: Affine, p: Point): Point {
  return [m[0] * p[0] + m[2] * p[1] + m[4], m[1] * p[0] + m[3] * p[1] + m[5]];
}

/** Which way a limb turns to raise its tip: its tip must move up the screen. */
export function raiseSign(pivot: Point, tip: Point | undefined): number {
  if (!tip) return 0;
  const dx = tip[0] - pivot[0];
  const dy = tip[1] - pivot[1];
  // A tip mostly below its pivot (a tail, a leg) swings sideways instead.
  if (Math.abs(dx) < Math.abs(dy) * 0.35) return dy > 0 ? 1 : -1;
  return dx < 0 ? 1 : -1;
}

/** The root transform: hops and floats, lean, squash about the base point. */
export function rootTransform(rig: BuiltRig, pose: Pose): Affine {
  const base: Point = [(rig.bounds[0] + rig.bounds[2]) / 2, rig.spec.base];
  const stretch = Math.max(-0.45, Math.min(0.45, pose.squash));
  return about(
    base,
    pose.lean,
    1 / Math.sqrt(1 + stretch),
    1 + stretch,
    pose.x,
    pose.y,
  );
}

/** World transforms for every bone in the rig's order. */
export function boneTransforms(rig: BuiltRig, pose: Pose): Affine[] {
  const root = rootTransform(rig, pose);
  const world: Affine[] = [];
  rig.bones.forEach((bone, i) => {
    let local: Affine;
    if (bone.id === 'body')
      local = about(
        bone.pivot,
        pose.bodyTilt,
        1 + pose.breathe * 0.35,
        1 + pose.breathe,
      );
    else if (bone.id === 'head')
      local = about(bone.pivot, pose.headTilt, 1, 1, pose.headX, pose.headY);
    else
      local = about(
        bone.pivot,
        raiseSign(bone.pivot, bone.tip) * (pose.limbs[bone.id] ?? 0),
      );
    const parent = rig.parents[i];
    world.push(multiply(parent < 0 ? root : world[parent], local));
  });
  return world;
}
