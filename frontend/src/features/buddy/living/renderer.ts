import { boneTransforms, type Pose } from './pose';
import { PATCH_IDS, type BuiltRig, type PatchId } from './rig';

export type Framing = 'portrait' | 'head' | 'pet';

const MAX_BONES = 12;

const CHARACTER_VERTEX = `#version 300 es
in vec2 aPos;
in vec2 aBone;
in vec2 aWeight;
uniform vec4 uBoneM[${MAX_BONES}];
uniform vec2 uBoneT[${MAX_BONES}];
uniform vec4 uView;
out vec2 vUv;
vec2 skin(int i, vec2 p) {
  vec4 m = uBoneM[i];
  return vec2(m.x * p.x + m.z * p.y, m.y * p.x + m.w * p.y) + uBoneT[i];
}
void main() {
  vec2 p = skin(int(aBone.x), aPos) * aWeight.x + skin(int(aBone.y), aPos) * aWeight.y;
  vUv = aPos;
  vec2 n = (p - uView.xy) / (uView.zw - uView.xy);
  gl_Position = vec4(n.x * 2.0 - 1.0, 1.0 - n.y * 2.0, 0.0, 1.0);
}`;

const CHARACTER_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vUv;
uniform sampler2D uArt;
uniform sampler2D uPatches;
uniform vec4 uHead;
uniform vec2 uTurn;
uniform vec4 uBody;
uniform vec2 uBodyTurn;
uniform vec4 uEyes[3];
uniform int uEyeCount;
uniform vec2 uGaze;
uniform float uSquish;
uniform vec4 uPatchAt[6];
uniform vec4 uPatchUv[6];
uniform float uPatchW[6];
out vec4 outColor;

// Where a point on a turned sphere (an ellipse on the art) came from.
vec2 sphereTurn(vec2 uv, vec4 e, vec2 turn) {
  vec2 q = (uv - e.xy) / e.zw;
  float r = length(q);
  if (r >= 1.0) return uv;
  float ry = sqrt(max(1.0 - q.y * q.y, 1e-4));
  float rx = sqrt(max(1.0 - q.x * q.x, 1e-4));
  vec2 src = vec2(
    sin(asin(clamp(q.x / ry, -1.0, 1.0)) - turn.x) * ry,
    sin(asin(clamp(q.y / rx, -1.0, 1.0)) - turn.y) * rx);
  return e.xy + mix(q, src, smoothstep(1.0, 0.8, r)) * e.zw;
}

void main() {
  vec2 uv = sphereTurn(vUv, uBody, uBodyTurn);
  uv = sphereTurn(uv, uHead, uTurn);
  vec2 face = uv;
  for (int i = 0; i < 3; i++) {
    if (i >= uEyeCount) break;
    vec4 e = uEyes[i];
    vec2 q = (uv - e.xy) / e.zw;
    float r2 = dot(q, q);
    if (r2 < 1.0) {
      float f = (1.0 - r2) * (1.0 - r2);
      uv -= uGaze * e.zw * 0.3 * f;
    }
    // Without a blink patch the eye closes: its lids (the skin just above and
    // below it) slide toward its middle line.
    if (uSquish < 0.999 && r2 < 1.0) {
      float wx = smoothstep(1.0, 0.6, abs(q.x));
      float sy = q.y / mix(1.0, max(uSquish, 0.02), wx);
      float lid = sqrt(max(1.0 - q.x * q.x, 0.0)) * 1.08;
      sy = clamp(sy, -lid, lid);
      uv.y = e.y + sy * e.w;
    }
  }
  vec4 color = texture(uArt, uv);
  for (int i = 0; i < 6; i++) {
    float w = uPatchW[i];
    if (w <= 0.002) continue;
    vec4 at = uPatchAt[i];
    vec2 local = (face - at.xy) / at.zw;
    if (local.x < 0.0 || local.y < 0.0 || local.x > 1.0 || local.y > 1.0) continue;
    vec4 overlay = texture(uPatches, uPatchUv[i].xy + local * uPatchUv[i].zw);
    color = overlay * w + color * (1.0 - overlay.a * w);
  }
  outColor = color;
}`;

const FX_VERTEX = `#version 300 es
in vec2 aCorner;
uniform vec4 uRect;
uniform vec4 uView;
uniform float uSpin;
out vec2 vLocal;
void main() {
  vLocal = aCorner;
  float c = cos(uSpin), s = sin(uSpin);
  vec2 p = uRect.xy + vec2(c * aCorner.x - s * aCorner.y, s * aCorner.x + c * aCorner.y) * uRect.zw;
  vec2 n = (p - uView.xy) / (uView.zw - uView.xy);
  gl_Position = vec4(n.x * 2.0 - 1.0, 1.0 - n.y * 2.0, 0.0, 1.0);
}`;

const FX_FRAGMENT = `#version 300 es
precision highp float;
in vec2 vLocal;
uniform int uShape;
uniform vec4 uColor;
out vec4 outColor;
void main() {
  float r = length(vLocal);
  float a;
  if (uShape == 0) a = 1.0 - smoothstep(0.15, 1.0, r);
  else {
    float star = 1.0 - smoothstep(0.0, 0.16, min(abs(vLocal.x), abs(vLocal.y)) + r * 0.22);
    a = max(star, 1.0 - smoothstep(0.0, 0.32, r)) * (1.0 - smoothstep(0.7, 1.0, r));
  }
  outColor = vec4(uColor.rgb * uColor.a * a, uColor.a * a);
}`;

function compile(gl: WebGL2RenderingContext, vertex: string, fragment: string) {
  const program = gl.createProgram();
  for (const [type, source] of [
    [gl.VERTEX_SHADER, vertex],
    [gl.FRAGMENT_SHADER, fragment],
  ] as const) {
    const shader = gl.createShader(type)!;
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (
      !gl.getShaderParameter(shader, gl.COMPILE_STATUS) &&
      !gl.isContextLost()
    )
      throw new Error(`buddy_shader: ${gl.getShaderInfoLog(shader)}`);
    gl.attachShader(program, shader);
  }
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS) && !gl.isContextLost())
    throw new Error(`buddy_program: ${gl.getProgramInfoLog(program)}`);
  return program;
}

function uniforms(
  gl: WebGL2RenderingContext,
  program: WebGLProgram,
  names: string[],
) {
  return Object.fromEntries(
    names.map((name) => [name, gl.getUniformLocation(program, name)]),
  );
}

/** The visible window on the art (x0, y0, x1, y1) for a framing and canvas shape. */
export function viewFor(
  rig: BuiltRig,
  framing: Framing,
  width: number,
  height: number,
): [number, number, number, number] {
  const [x0, y0, x1] = rig.bounds;
  const base = rig.spec.base;
  const aspect = width > 0 && height > 0 ? width / height : 1;
  let cx = (x0 + x1) / 2;
  let size: number;
  let bottom: number;
  if (framing === 'head') {
    const head = rig.head;
    size = Math.max(head.radius[0], head.radius[1]) * 2 * 1.45;
    cx = head.center[0];
    bottom = head.center[1] + size * 0.52;
  } else {
    const tall = base - y0;
    const wide = x1 - x0;
    const headroom = framing === 'pet' ? 0.3 : 0.16;
    size = Math.max(wide * 1.16, tall * (1 + headroom) + 0.05);
    bottom = base + (framing === 'pet' ? 0.03 : 0.045);
  }
  const viewHeight = aspect >= 1 ? size : size / aspect;
  const viewWidth = viewHeight * aspect;
  return [cx - viewWidth / 2, bottom - viewHeight, cx + viewWidth / 2, bottom];
}

export type DrawOptions = {
  framing: Framing;
  shadow: number;
  sparkles: number[];
  time: number;
};

/** One living Buddy drawn into one canvas. */
export class BuddyRenderer {
  private readonly gl: WebGL2RenderingContext;
  private readonly character: WebGLProgram;
  private readonly fx: WebGLProgram;
  private readonly cu: Record<string, WebGLUniformLocation | null>;
  private readonly fu: Record<string, WebGLUniformLocation | null>;
  private readonly characterVao: WebGLVertexArrayObject;
  private readonly fxVao: WebGLVertexArrayObject;
  private readonly art: WebGLTexture;
  private readonly patches: WebGLTexture;
  private readonly count: number;
  private readonly patchSlots: { id: PatchId; at: number[]; uv: number[] }[];
  private readonly buffers: WebGLBuffer[] = [];

  constructor(
    readonly canvas: HTMLCanvasElement,
    readonly rig: BuiltRig,
    art: ImageBitmap,
    patches: ImageBitmap | null,
  ) {
    const gl = canvas.getContext('webgl2', {
      alpha: true,
      premultipliedAlpha: true,
      antialias: true,
      preserveDrawingBuffer: false,
      powerPreference: 'low-power',
    });
    if (!gl) throw new Error('buddy_webgl2_unavailable');
    this.gl = gl;
    this.character = compile(gl, CHARACTER_VERTEX, CHARACTER_FRAGMENT);
    this.fx = compile(gl, FX_VERTEX, FX_FRAGMENT);
    this.cu = uniforms(gl, this.character, [
      'uBoneM',
      'uBoneT',
      'uView',
      'uArt',
      'uPatches',
      'uHead',
      'uTurn',
      'uBody',
      'uBodyTurn',
      'uEyes',
      'uEyeCount',
      'uGaze',
      'uSquish',
      'uPatchAt',
      'uPatchUv',
      'uPatchW',
    ]);
    this.fu = uniforms(gl, this.fx, [
      'uRect',
      'uView',
      'uSpin',
      'uShape',
      'uColor',
    ]);

    const buffer = (
      data: ArrayBufferView,
      target: number = gl.ARRAY_BUFFER,
    ) => {
      const handle = gl.createBuffer()!;
      gl.bindBuffer(target, handle);
      gl.bufferData(target, data, gl.STATIC_DRAW);
      this.buffers.push(handle);
      return handle;
    };
    this.characterVao = gl.createVertexArray()!;
    gl.bindVertexArray(this.characterVao);
    const attribute = (name: string, data: Float32Array, size: number) => {
      const location = gl.getAttribLocation(this.character, name);
      buffer(data);
      gl.enableVertexAttribArray(location);
      gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0);
    };
    attribute('aPos', rig.positions, 2);
    attribute('aBone', Float32Array.from(rig.boneIndex), 2);
    attribute('aWeight', rig.boneWeight, 2);
    buffer(rig.indices, gl.ELEMENT_ARRAY_BUFFER);
    this.count = rig.indices.length;
    gl.bindVertexArray(null);

    this.fxVao = gl.createVertexArray()!;
    gl.bindVertexArray(this.fxVao);
    buffer(new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]));
    const corner = gl.getAttribLocation(this.fx, 'aCorner');
    gl.enableVertexAttribArray(corner);
    gl.vertexAttribPointer(corner, 2, gl.FLOAT, false, 0, 0);
    gl.bindVertexArray(null);

    const texture = (image: ImageBitmap | null) => {
      const handle = gl.createTexture()!;
      gl.bindTexture(gl.TEXTURE_2D, handle);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      if (image) {
        gl.texImage2D(
          gl.TEXTURE_2D,
          0,
          gl.RGBA,
          gl.RGBA,
          gl.UNSIGNED_BYTE,
          image,
        );
        gl.generateMipmap(gl.TEXTURE_2D);
        gl.texParameteri(
          gl.TEXTURE_2D,
          gl.TEXTURE_MIN_FILTER,
          gl.LINEAR_MIPMAP_LINEAR,
        );
      } else {
        gl.texImage2D(
          gl.TEXTURE_2D,
          0,
          gl.RGBA,
          1,
          1,
          0,
          gl.RGBA,
          gl.UNSIGNED_BYTE,
          new Uint8Array(4),
        );
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      }
      return handle;
    };
    this.art = texture(art);
    this.patches = texture(patches);
    const atlasWidth = patches?.width ?? 1;
    const atlasHeight = patches?.height ?? 1;
    this.patchSlots = patches
      ? PATCH_IDS.flatMap((id) => {
          const patch = rig.spec.patches[id];
          if (!patch) return [];
          const [ax, ay, aw, ah] = patch.atlas;
          return [
            {
              id,
              at: patch.at,
              uv: [
                ax / atlasWidth,
                ay / atlasHeight,
                aw / atlasWidth,
                ah / atlasHeight,
              ],
            },
          ];
        }).slice(0, 6)
      : [];
  }

  get lost() {
    return this.gl.isContextLost();
  }

  /** Size the drawing buffer to the canvas's CSS box at the device's pixel ratio. */
  private resize() {
    const ratio = Math.min(window.devicePixelRatio || 1, 3);
    const width = Math.max(1, Math.round(this.canvas.clientWidth * ratio));
    const height = Math.max(1, Math.round(this.canvas.clientHeight * ratio));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }
  }

  draw(pose: Pose, options: DrawOptions) {
    const gl = this.gl;
    if (gl.isContextLost()) return;
    this.resize();
    const { width, height } = this.canvas;
    const view = viewFor(this.rig, options.framing, width, height);
    gl.viewport(0, 0, width, height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);

    const rig = this.rig;
    const [x0, , x1] = rig.bounds;
    // The contact shadow: smaller and fainter the higher Buddy hops.
    if (options.shadow > 0) {
      const lift = Math.max(0, -pose.y);
      const fade = Math.max(0, 1 - lift * 5);
      gl.useProgram(this.fx);
      gl.bindVertexArray(this.fxVao);
      gl.uniform4f(this.fu.uView, view[0], view[1], view[2], view[3]);
      gl.uniform1f(this.fu.uSpin, 0);
      gl.uniform1i(this.fu.uShape, 0);
      const spread = (x1 - x0) * 0.36 * (0.75 + fade * 0.25);
      gl.uniform4f(
        this.fu.uRect,
        (x0 + x1) / 2 + pose.x,
        rig.spec.base + 0.008,
        spread,
        spread * 0.16,
      );
      gl.uniform4f(
        this.fu.uColor,
        0,
        0,
        0,
        options.shadow * (0.45 + fade * 0.55),
      );
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }

    gl.useProgram(this.character);
    gl.bindVertexArray(this.characterVao);
    const transforms = boneTransforms(rig, pose);
    const matrices = new Float32Array(MAX_BONES * 4);
    const translations = new Float32Array(MAX_BONES * 2);
    transforms.forEach((m, i) => {
      matrices.set([m[0], m[1], m[2], m[3]], i * 4);
      translations.set([m[4], m[5]], i * 2);
    });
    gl.uniform4fv(this.cu.uBoneM, matrices);
    gl.uniform2fv(this.cu.uBoneT, translations);
    gl.uniform4f(this.cu.uView, view[0], view[1], view[2], view[3]);
    const head = rig.head;
    gl.uniform4f(
      this.cu.uHead,
      head.center[0],
      head.center[1],
      head.radius[0] * 1.05,
      head.radius[1] * 1.05,
    );
    gl.uniform2f(this.cu.uTurn, pose.turnX, pose.turnY);
    const body = rig.bones.find((bone) => bone.id === 'body')!;
    const bodyRadius: [number, number] = [
      (x1 - x0) * 0.42,
      Math.max(0.12, rig.spec.base - body.pivot[1]) * 1.4,
    ];
    gl.uniform4f(
      this.cu.uBody,
      body.pivot[0],
      body.pivot[1],
      bodyRadius[0],
      bodyRadius[1],
    );
    gl.uniform2f(this.cu.uBodyTurn, pose.turnX * 0.3, pose.turnY * 0.15);
    const eyes = new Float32Array(12);
    rig.spec.eyes.forEach((eye, i) =>
      eyes.set(
        [eye.center[0], eye.center[1], eye.radius[0], eye.radius[1]],
        i * 4,
      ),
    );
    gl.uniform4fv(this.cu.uEyes, eyes);
    gl.uniform1i(this.cu.uEyeCount, rig.spec.eyes.length);
    gl.uniform2f(this.cu.uGaze, pose.gazeX, pose.gazeY);
    const hasBlink = this.patchSlots.some((slot) => slot.id === 'blink');
    gl.uniform1f(this.cu.uSquish, hasBlink ? 1 : pose.eyeOpen);
    const at = new Float32Array(24);
    const uv = new Float32Array(24);
    const weights = new Float32Array(6);
    const weightOf: Record<PatchId, number> = {
      blink: 1 - pose.eyeOpen,
      talk: pose.mouth,
      happy: pose.happy,
      worried: pose.worried,
      focused: pose.focused,
      surprised: pose.surprised,
    };
    // Expressions first, the blink and the mouth last, so they read on top.
    const order = [...this.patchSlots].sort(
      (a, b) =>
        Number(a.id === 'blink' || a.id === 'talk') -
        Number(b.id === 'blink' || b.id === 'talk'),
    );
    order.forEach((slot, i) => {
      at.set(slot.at, i * 4);
      uv.set(slot.uv, i * 4);
      weights[i] = Math.max(0, Math.min(1, weightOf[slot.id]));
    });
    gl.uniform4fv(this.cu.uPatchAt, at);
    gl.uniform4fv(this.cu.uPatchUv, uv);
    gl.uniform1fv(this.cu.uPatchW, weights);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, this.art);
    gl.uniform1i(this.cu.uArt, 0);
    gl.activeTexture(gl.TEXTURE1);
    gl.bindTexture(gl.TEXTURE_2D, this.patches);
    gl.uniform1i(this.cu.uPatches, 1);
    gl.drawElements(gl.TRIANGLES, this.count, gl.UNSIGNED_SHORT, 0);

    // Sparkles around the head after a big celebration.
    const live = options.sparkles.filter((start) => options.time >= start);
    if (live.length) {
      gl.useProgram(this.fx);
      gl.bindVertexArray(this.fxVao);
      gl.uniform4f(this.fu.uView, view[0], view[1], view[2], view[3]);
      gl.uniform1i(this.fu.uShape, 1);
      for (const start of live) {
        const age = options.time - start;
        const fade = Math.max(0, 1 - age / 1.4);
        for (let i = 0; i < 9; i++) {
          const angle = (i / 9) * Math.PI * 2 + 0.4;
          const reach = head.radius[0] * (1.1 + age * 1.5 + (i % 3) * 0.15);
          const size = 0.022 * (1 - age / 1.6) * (i % 2 ? 1 : 0.7);
          gl.uniform4f(
            this.fu.uRect,
            head.center[0] + pose.x + Math.cos(angle) * reach,
            head.center[1] +
              pose.y +
              Math.sin(angle) * reach * 0.85 -
              age * 0.05,
            size,
            size,
          );
          gl.uniform1f(this.fu.uSpin, age * 2 + i);
          const gold = i % 3 === 0;
          gl.uniform4f(
            this.fu.uColor,
            1,
            gold ? 0.84 : 0.97,
            gold ? 0.42 : 0.86,
            fade,
          );
          gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        }
      }
    }
    gl.bindVertexArray(null);
  }

  /**
   * Which cells of the canvas the frame just drawn covers (24×24, row-major,
   * one bit each, grown by a cell): the desktop pet's click mask. Call right
   * after draw, while the drawing buffer still holds the frame.
   */
  readMask(cells = 24): Uint8Array {
    const gl = this.gl;
    const { width, height } = this.canvas;
    const bits = new Uint8Array((cells * cells) / 8);
    if (gl.isContextLost() || !width || !height) return bits;
    const pixels = new Uint8Array(width * height * 4);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
    const covered = new Uint8Array(cells * cells);
    const step = Math.max(1, Math.floor(Math.min(width, height) / (cells * 4)));
    for (let y = 0; y < height; y += step)
      for (let x = 0; x < width; x += step)
        if (pixels[(y * width + x) * 4 + 3] > 40) {
          // readPixels rows start at the bottom.
          const row =
            cells - 1 - Math.min(cells - 1, Math.floor((y * cells) / height));
          covered[
            row * cells + Math.min(cells - 1, Math.floor((x * cells) / width))
          ] = 1;
        }
    for (let row = 0; row < cells; row++)
      for (let column = 0; column < cells; column++) {
        let near = 0;
        for (let dy = -1; dy <= 1 && !near; dy++)
          for (let dx = -1; dx <= 1 && !near; dx++) {
            const r = row + dy;
            const c = column + dx;
            if (r >= 0 && c >= 0 && r < cells && c < cells)
              near = covered[r * cells + c];
          }
        if (near) {
          const index = row * cells + column;
          bits[index >> 3] |= 0x80 >> (index & 7);
        }
      }
    return bits;
  }

  dispose() {
    const gl = this.gl;
    for (const buffer of this.buffers) gl.deleteBuffer(buffer);
    gl.deleteTexture(this.art);
    gl.deleteTexture(this.patches);
    gl.deleteVertexArray(this.characterVao);
    gl.deleteVertexArray(this.fxVao);
    gl.deleteProgram(this.character);
    gl.deleteProgram(this.fx);
    gl.getExtension('WEBGL_lose_context')?.loseContext();
  }
}
