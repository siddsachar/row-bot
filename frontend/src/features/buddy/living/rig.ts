/**
 * The living Buddy rig: a pack's rig.json (hints in normalised art
 * coordinates, y down) plus the mesh and skin weights built from the art's
 * alpha. Building is deterministic, so the same art and hints always give
 * the same rig.
 */

export const LIMB_IDS = [
  'arm_l',
  'arm_r',
  'wing_l',
  'wing_r',
  'leg_l',
  'leg_r',
  'ear_l',
  'ear_r',
  'tail',
  'top',
] as const;
export type LimbId = (typeof LIMB_IDS)[number];
export type BoneId = 'body' | 'head' | LimbId;
export const PATCH_IDS = [
  'blink',
  'talk',
  'happy',
  'worried',
  'focused',
  'surprised',
] as const;
export type PatchId = (typeof PATCH_IDS)[number];
export type Family = 'floater' | 'biped' | 'winged' | 'free';

export type Point = [number, number];
export type Ellipse = { center: Point; radius: Point };
export type RigBone = {
  id: BoneId;
  parent: BoneId | null;
  pivot: Point;
  tip?: Point;
  /** The head's ellipse: its centre (else the pivot) and radii. */
  center?: Point;
  radius?: Point;
};
export type RigPatch = {
  /** Pixel rect in the patch atlas: x, y, width, height. */
  atlas: [number, number, number, number];
  /** Normalised rect on the art: x, y, width, height. */
  at: [number, number, number, number];
};
export type RigSpec = {
  family: Family;
  base: number;
  bones: RigBone[];
  eyes: Ellipse[];
  mouth: Ellipse | null;
  patches: Partial<Record<PatchId, RigPatch>>;
  personality: { bounce: number; tempo: number; curiosity: number };
};

const BONE_IDS = new Set<string>(['body', 'head', ...LIMB_IDS]);
const unit = (value: unknown) =>
  typeof value === 'number' &&
  Number.isFinite(value) &&
  value >= -0.5 &&
  value <= 1.5;
const clamp01 = (value: unknown, fallback: number) =>
  typeof value === 'number' && Number.isFinite(value)
    ? Math.max(0, Math.min(1, value))
    : fallback;

function point(value: unknown): Point | null {
  return Array.isArray(value) &&
    value.length === 2 &&
    unit(value[0]) &&
    unit(value[1])
    ? [value[0], value[1]]
    : null;
}

function ellipse(value: unknown): Ellipse | null {
  if (!value || typeof value !== 'object') return null;
  const { center, radius } = value as Record<string, unknown>;
  const c = point(center);
  const r = point(radius);
  return c && r && r[0] > 0 && r[1] > 0 ? { center: c, radius: r } : null;
}

/** Parse rig.json defensively; anything malformed is dropped, not trusted. */
export function parseRig(raw: unknown): RigSpec {
  const source = (raw && typeof raw === 'object' ? raw : {}) as Record<
    string,
    unknown
  >;
  const family = (['floater', 'biped', 'winged', 'free'] as const).includes(
    source.family as Family,
  )
    ? (source.family as Family)
    : 'free';
  const bones: RigBone[] = [];
  const seen = new Set<string>();
  for (const entry of Array.isArray(source.bones)
    ? source.bones.slice(0, 12)
    : []) {
    if (!entry || typeof entry !== 'object') continue;
    const bone = entry as Record<string, unknown>;
    const id = String(bone.id ?? '');
    const pivot = point(bone.pivot);
    if (!BONE_IDS.has(id) || seen.has(id) || !pivot) continue;
    const parent =
      typeof bone.parent === 'string' && BONE_IDS.has(bone.parent)
        ? (bone.parent as BoneId)
        : null;
    seen.add(id);
    bones.push({
      id: id as BoneId,
      parent: id === 'body' ? null : parent,
      pivot,
      ...(point(bone.tip) ? { tip: point(bone.tip)! } : {}),
      ...(point(bone.center) ? { center: point(bone.center)! } : {}),
      ...(point(bone.radius) ? { radius: point(bone.radius)! } : {}),
    });
  }
  // Every rig has a body and a head; parents must exist (else: the body).
  if (!seen.has('body'))
    bones.unshift({ id: 'body', parent: null, pivot: [0.5, 0.72] });
  if (!seen.has('head'))
    bones.push({
      id: 'head',
      parent: 'body',
      pivot: [0.5, 0.5],
      radius: [0.24, 0.22],
    });
  const ids = new Set(bones.map((bone) => bone.id));
  for (const bone of bones)
    if (
      bone.id !== 'body' &&
      (!bone.parent || !ids.has(bone.parent) || bone.parent === bone.id)
    )
      bone.parent = 'body';
  const patches: Partial<Record<PatchId, RigPatch>> = {};
  const rawPatches = (
    source.patches && typeof source.patches === 'object' ? source.patches : {}
  ) as Record<string, unknown>;
  for (const id of PATCH_IDS) {
    const entry = rawPatches[id] as Record<string, unknown> | undefined;
    if (!entry) continue;
    const atlas = entry.atlas;
    const at = entry.at;
    if (
      Array.isArray(atlas) &&
      atlas.length === 4 &&
      atlas.every(
        (value) => Number.isInteger(value) && value >= 0 && value <= 4096,
      ) &&
      Array.isArray(at) &&
      at.length === 4 &&
      at.every(unit)
    )
      patches[id] = {
        atlas: atlas as RigPatch['atlas'],
        at: at as RigPatch['at'],
      };
  }
  const personality = (
    source.personality && typeof source.personality === 'object'
      ? source.personality
      : {}
  ) as Record<string, unknown>;
  return {
    family,
    base: clamp01(source.base, 0.92),
    bones,
    eyes: (Array.isArray(source.eyes) ? source.eyes : [])
      .map(ellipse)
      .filter((value): value is Ellipse => !!value)
      .slice(0, 3),
    mouth: ellipse(source.mouth),
    patches,
    personality: {
      bounce: clamp01(personality.bounce, 0.5),
      tempo: clamp01(personality.tempo, 0.5),
      curiosity: clamp01(personality.curiosity, 0.5),
    },
  };
}

export type Mask = { width: number; height: number; alpha: Uint8Array };

export type BuiltRig = {
  spec: RigSpec;
  /** Bones in parent-first order; index 0 is the body. */
  bones: RigBone[];
  parents: Int8Array;
  /** Rest positions (normalised), also the texture coordinates. */
  positions: Float32Array;
  indices: Uint16Array;
  /** Two bone indices and two weights per vertex. */
  boneIndex: Uint8Array;
  boneWeight: Float32Array;
  /** The silhouette's bounds: x0, y0, x1, y1 (normalised). */
  bounds: [number, number, number, number];
  head: Ellipse;
};

const GRID = 40;
const OUTSIDE_COST = 6;

/** Order bones so a parent always precedes its children. */
function ordered(bones: RigBone[]): RigBone[] {
  const result: RigBone[] = [];
  const placed = new Set<string>();
  let remaining = bones.slice();
  while (remaining.length) {
    const next = remaining.filter(
      (bone) => !bone.parent || placed.has(bone.parent),
    );
    if (!next.length) break;
    for (const bone of next) {
      result.push(bone);
      placed.add(bone.id);
    }
    remaining = remaining.filter((bone) => !placed.has(bone.id));
  }
  return result;
}

function silhouette(mask: Mask) {
  const { width, height, alpha } = mask;
  let x0 = width;
  let y0 = height;
  let x1 = -1;
  let y1 = -1;
  for (let y = 0; y < height; y++)
    for (let x = 0; x < width; x++)
      if (alpha[y * width + x] > 24) {
        if (x < x0) x0 = x;
        if (x > x1) x1 = x;
        if (y < y0) y0 = y;
        if (y > y1) y1 = y;
      }
  if (x1 < 0) return [0.2, 0.2, 0.8, 0.9] as [number, number, number, number];
  return [x0 / width, y0 / height, (x1 + 1) / width, (y1 + 1) / height] as [
    number,
    number,
    number,
    number,
  ];
}

/** Multi-source Dijkstra over the mask grid; leaving the silhouette costs more. */
function geodesic(mask: Mask, seeds: number[]): Float64Array {
  const { width, height, alpha } = mask;
  const count = width * height;
  // Float64: keys are compared with stored distances, and float32 rounding
  // would re-expand nodes without end.
  const distance = new Float64Array(count).fill(Infinity);
  const heap: number[] = [];
  const keys: number[] = [];
  const push = (node: number, key: number) => {
    heap.push(node);
    keys.push(key);
    let i = heap.length - 1;
    while (i > 0) {
      const parent = (i - 1) >> 1;
      if (keys[parent] <= keys[i]) break;
      [heap[parent], heap[i]] = [heap[i], heap[parent]];
      [keys[parent], keys[i]] = [keys[i], keys[parent]];
      i = parent;
    }
  };
  const pop = () => {
    const node = heap[0];
    const key = keys[0];
    const lastNode = heap.pop()!;
    const lastKey = keys.pop()!;
    if (heap.length) {
      heap[0] = lastNode;
      keys[0] = lastKey;
      let i = 0;
      for (;;) {
        const left = i * 2 + 1;
        const right = left + 1;
        let smallest = i;
        if (left < heap.length && keys[left] < keys[smallest]) smallest = left;
        if (right < heap.length && keys[right] < keys[smallest])
          smallest = right;
        if (smallest === i) break;
        [heap[smallest], heap[i]] = [heap[i], heap[smallest]];
        [keys[smallest], keys[i]] = [keys[i], keys[smallest]];
        i = smallest;
      }
    }
    return [node, key] as const;
  };
  for (const seed of seeds) {
    distance[seed] = 0;
    push(seed, 0);
  }
  const steps = [
    [1, 0, 1],
    [-1, 0, 1],
    [0, 1, 1],
    [0, -1, 1],
    [1, 1, Math.SQRT2],
    [-1, 1, Math.SQRT2],
    [1, -1, Math.SQRT2],
    [-1, -1, Math.SQRT2],
  ] as const;
  while (heap.length) {
    const [node, key] = pop();
    if (key > distance[node]) continue;
    const x = node % width;
    const y = (node - x) / width;
    for (const [dx, dy, length] of steps) {
      const nx = x + dx;
      const ny = y + dy;
      if (nx < 0 || ny < 0 || nx >= width || ny >= height) continue;
      const next = ny * width + nx;
      const cost = length * (alpha[next] > 24 ? 1 : OUTSIDE_COST);
      const candidate = key + cost;
      if (candidate < distance[next]) {
        distance[next] = candidate;
        push(next, candidate);
      }
    }
  }
  return distance;
}

function segmentSeeds(mask: Mask, from: Point, to: Point): number[] {
  const { width, height } = mask;
  const seeds = new Set<number>();
  const samples = Math.max(
    2,
    Math.ceil(
      Math.hypot((to[0] - from[0]) * width, (to[1] - from[1]) * height),
    ),
  );
  for (let i = 0; i <= samples; i++) {
    const t = i / samples;
    const x = Math.round((from[0] + (to[0] - from[0]) * t) * (width - 1));
    const y = Math.round((from[1] + (to[1] - from[1]) * t) * (height - 1));
    if (x >= 0 && y >= 0 && x < width && y < height) seeds.add(y * width + x);
  }
  return [...seeds];
}

function ellipseSeeds(mask: Mask, shape: Ellipse, scale: number): number[] {
  const { width, height } = mask;
  const seeds: number[] = [];
  for (let y = 0; y < height; y++)
    for (let x = 0; x < width; x++) {
      const nx =
        ((x + 0.5) / width - shape.center[0]) / (shape.radius[0] * scale);
      const ny =
        ((y + 0.5) / height - shape.center[1]) / (shape.radius[1] * scale);
      if (nx * nx + ny * ny <= 1) seeds.push(y * width + x);
    }
  return seeds;
}

export function headEllipse(spec: RigSpec): Ellipse {
  const head = spec.bones.find((bone) => bone.id === 'head');
  return {
    center: head?.center ?? head?.pivot ?? [0.5, 0.45],
    radius: head?.radius ?? [0.24, 0.22],
  };
}

/** Build the deformable mesh and skin weights for a rig over its art's alpha. */
export function buildRig(spec: RigSpec, mask: Mask): BuiltRig {
  const bones = ordered(spec.bones);
  const index = new Map(bones.map((bone, i) => [bone.id, i]));
  const parents = new Int8Array(
    bones.map((bone) => (bone.parent ? index.get(bone.parent)! : -1)),
  );
  const head = headEllipse(spec);
  const { width, height, alpha } = mask;

  // Seeds per bone: the head is its ellipse, a limb its pivot→tip segment
  // (from a little past the pivot, so the joint blends), the body its torso
  // from its pivot up to the neck.
  const neck = bones.find((bone) => bone.id === 'head')!.pivot;
  const distances = bones.map((bone) => {
    if (bone.id === 'head')
      return geodesic(mask, ellipseSeeds(mask, head, 0.9));
    if (bone.id === 'body')
      return geodesic(mask, [
        ...segmentSeeds(mask, bone.pivot, neck),
        ...ellipseSeeds(mask, { center: bone.pivot, radius: [0.05, 0.04] }, 1),
      ]);
    const tip = bone.tip ?? bone.pivot;
    const start: Point = [
      bone.pivot[0] + (tip[0] - bone.pivot[0]) * 0.2,
      bone.pivot[1] + (tip[1] - bone.pivot[1]) * 0.2,
    ];
    return geodesic(mask, segmentSeeds(mask, start, tip));
  });

  // Active cells: any silhouette pixel within the cell grown by one cell.
  const summed = new Uint32Array((width + 1) * (height + 1));
  for (let y = 0; y < height; y++) {
    let row = 0;
    for (let x = 0; x < width; x++) {
      row += alpha[y * width + x] > 8 ? 1 : 0;
      summed[(y + 1) * (width + 1) + x + 1] =
        summed[y * (width + 1) + x + 1] + row;
    }
  }
  const covered = (x0: number, y0: number, x1: number, y1: number) => {
    const ax = Math.max(0, Math.floor(x0 * width));
    const ay = Math.max(0, Math.floor(y0 * height));
    const bx = Math.min(width, Math.ceil(x1 * width));
    const by = Math.min(height, Math.ceil(y1 * height));
    if (bx <= ax || by <= ay) return false;
    const w = width + 1;
    return (
      summed[by * w + bx] -
        summed[ay * w + bx] -
        summed[by * w + ax] +
        summed[ay * w + ax] >
      0
    );
  };
  const cell = 1 / GRID;
  const vertexOf = new Int32Array((GRID + 1) * (GRID + 1)).fill(-1);
  const positions: number[] = [];
  const triangles: number[] = [];
  const vertex = (gx: number, gy: number) => {
    const key = gy * (GRID + 1) + gx;
    if (vertexOf[key] < 0) {
      vertexOf[key] = positions.length / 2;
      positions.push(gx * cell, gy * cell);
    }
    return vertexOf[key];
  };
  for (let gy = 0; gy < GRID; gy++)
    for (let gx = 0; gx < GRID; gx++) {
      if (
        !covered(
          (gx - 1) * cell,
          (gy - 1) * cell,
          (gx + 2) * cell,
          (gy + 2) * cell,
        )
      )
        continue;
      const a = vertex(gx, gy);
      const b = vertex(gx + 1, gy);
      const c = vertex(gx, gy + 1);
      const d = vertex(gx + 1, gy + 1);
      triangles.push(a, b, c, b, d, c);
    }

  const vertices = positions.length / 2;
  const boneIndex = new Uint8Array(vertices * 2);
  const boneWeight = new Float32Array(vertices * 2);
  const sigma = 0.07 * width;
  for (let v = 0; v < vertices; v++) {
    const x = Math.min(
      width - 1,
      Math.max(0, Math.round(positions[v * 2] * width - 0.5)),
    );
    const y = Math.min(
      height - 1,
      Math.max(0, Math.round(positions[v * 2 + 1] * height - 0.5)),
    );
    const pixel = y * width + x;
    let first = 0;
    let second = 0;
    let firstWeight = -1;
    let secondWeight = -1;
    for (let b = 0; b < bones.length; b++) {
      const d = distances[b][pixel];
      const weight = Number.isFinite(d)
        ? Math.exp(-((d / sigma) ** 2)) + 1e-6
        : 1e-6;
      if (weight > firstWeight) {
        second = first;
        secondWeight = firstWeight;
        first = b;
        firstWeight = weight;
      } else if (weight > secondWeight) {
        second = b;
        secondWeight = weight;
      }
    }
    const total = firstWeight + Math.max(0, secondWeight);
    boneIndex[v * 2] = first;
    boneIndex[v * 2 + 1] = second;
    boneWeight[v * 2] = firstWeight / total;
    boneWeight[v * 2 + 1] = Math.max(0, secondWeight) / total;
  }
  return {
    spec,
    bones,
    parents,
    positions: new Float32Array(positions),
    indices: new Uint16Array(triangles),
    boneIndex,
    boneWeight,
    bounds: silhouette(mask),
    head,
  };
}
