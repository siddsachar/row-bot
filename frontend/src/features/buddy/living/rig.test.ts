import { describe, expect, it } from 'vitest';
import { boneTransforms, apply, restPose } from './pose';
import { buildRig, parseRig, type Mask } from './rig';

/** A synthetic character: a round head on a round body with one wing. */
function mask(): Mask {
  const width = 128;
  const height = 128;
  const alpha = new Uint8Array(width * height);
  const disc = (cx: number, cy: number, r: number) => {
    for (let y = 0; y < height; y++)
      for (let x = 0; x < width; x++)
        if ((x / width - cx) ** 2 + (y / height - cy) ** 2 < r * r)
          alpha[y * width + x] = 255;
  };
  disc(0.5, 0.4, 0.22);
  disc(0.5, 0.72, 0.18);
  disc(0.25, 0.66, 0.08);
  return { width, height, alpha };
}

const spec = parseRig({
  family: 'winged',
  base: 0.9,
  bones: [
    { id: 'body', pivot: [0.5, 0.72] },
    {
      id: 'head',
      parent: 'body',
      pivot: [0.5, 0.58],
      center: [0.5, 0.4],
      radius: [0.22, 0.22],
    },
    { id: 'wing_l', parent: 'body', pivot: [0.36, 0.66], tip: [0.18, 0.66] },
  ],
  eyes: [{ center: [0.44, 0.4], radius: [0.04, 0.05] }],
});

describe('parseRig', () => {
  it('keeps valid hints and drops malformed ones', () => {
    const rig = parseRig({
      family: 'dragon',
      base: 7,
      bones: [
        { id: 'head', pivot: [0.5, 0.4] },
        { id: 'tentacle', pivot: [0.5, 0.5] },
        { id: 'wing_l', parent: 'nobody', pivot: [0.3, 'x'] },
        { id: 'arm_r', parent: 'nobody', pivot: [0.7, 0.6], tip: [0.9, 0.6] },
      ],
      eyes: [
        { center: [0.4, 0.4], radius: [0, 0.1] },
        { center: [0.6, 0.4], radius: [0.05, 0.05] },
      ],
      mouth: 'smile',
      patches: {
        blink: { atlas: [0, 0, 10, 10], at: [0.3, 0.3, 0.2, 0.1] },
        talk: { atlas: [-1, 0, 1, 1], at: [0, 0, 1, 1] },
      },
      personality: { bounce: 4 },
    });
    expect(rig.family).toBe('free');
    expect(rig.base).toBe(1);
    expect(rig.bones.map((bone) => bone.id).sort()).toEqual([
      'arm_r',
      'body',
      'head',
    ]);
    expect(rig.bones.find((bone) => bone.id === 'arm_r')!.parent).toBe('body');
    expect(rig.eyes).toHaveLength(1);
    expect(rig.mouth).toBeNull();
    expect(Object.keys(rig.patches)).toEqual(['blink']);
    expect(rig.personality.bounce).toBe(1);
  });

  it('gives an empty or broken file a body and a head', () => {
    for (const raw of [null, 'nonsense', { bones: 'x' }]) {
      const rig = parseRig(raw);
      expect(rig.bones.map((bone) => bone.id)).toEqual(['body', 'head']);
    }
  });
});

describe('buildRig', () => {
  const rig = buildRig(spec, mask());

  it('covers the silhouette with a mesh', () => {
    expect(rig.indices.length % 3).toBe(0);
    expect(rig.positions.length / 2).toBeGreaterThan(200);
    const [x0, y0, x1, y1] = rig.bounds;
    expect(x0).toBeLessThan(0.2);
    expect(x1).toBeGreaterThan(0.65);
    expect(y0).toBeLessThan(0.2);
    expect(y1).toBeGreaterThan(0.85);
  });

  it('weights each vertex to at most two bones, summing to one', () => {
    for (let v = 0; v < rig.positions.length / 2; v++) {
      const sum = rig.boneWeight[v * 2] + rig.boneWeight[v * 2 + 1];
      expect(sum).toBeCloseTo(1, 5);
    }
  });

  it('gives the head its face and the wing its tip', () => {
    const owner = (x: number, y: number) => {
      let best = -1;
      let distance = Infinity;
      for (let v = 0; v < rig.positions.length / 2; v++) {
        const d =
          (rig.positions[v * 2] - x) ** 2 + (rig.positions[v * 2 + 1] - y) ** 2;
        if (d < distance) [best, distance] = [v, d];
      }
      return rig.bones[rig.boneIndex[best * 2]].id;
    };
    expect(owner(0.5, 0.35)).toBe('head');
    expect(owner(0.2, 0.66)).toBe('wing_l');
    expect(owner(0.5, 0.78)).toBe('body');
  });

  it('is deterministic', () => {
    const again = buildRig(spec, mask());
    expect(Array.from(again.boneWeight)).toEqual(Array.from(rig.boneWeight));
    expect(Array.from(again.indices)).toEqual(Array.from(rig.indices));
  });

  it('lifts a raised wing tip and moves a hop up the screen', () => {
    const pose = restPose();
    pose.limbs.wing_l = 0.5;
    pose.y = -0.1;
    const transforms = boneTransforms(rig, pose);
    const wing = rig.bones.findIndex((bone) => bone.id === 'wing_l');
    const body = rig.bones.findIndex((bone) => bone.id === 'body');
    expect(apply(transforms[wing], [0.18, 0.66])[1]).toBeLessThan(
      0.66 - 0.1 - 0.02,
    );
    expect(apply(transforms[body], [0.5, 0.72])[1]).toBeCloseTo(0.62, 5);
  });
});
