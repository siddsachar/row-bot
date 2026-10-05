/**
 * Identifiers and digests that work on every page Row-Bot is opened from.
 *
 * Browsers expose `crypto.randomUUID` and `crypto.subtle` only to secure
 * pages: HTTPS, `localhost` and `127.0.0.1`. A computer that opens Row-Bot at
 * a plain `http://` network address gets neither, while
 * `crypto.getRandomValues` is available everywhere.
 */
type PageCrypto = Pick<Crypto, 'getRandomValues'> &
  Partial<Pick<Crypto, 'randomUUID' | 'subtle'>>;

/**
 * Give a page without `crypto.randomUUID` the standard one, built from
 * `getRandomValues`. Each page entry point does this before anything else
 * runs (see `./random-uuid`); a page that has the browser's own keeps it.
 */
export function provideRandomUUID(
  target: PageCrypto = globalThis.crypto,
): void {
  if (typeof target.randomUUID === 'function') return;
  const randomUUID = (): ReturnType<Crypto['randomUUID']> => {
    const bytes = target.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) =>
      byte.toString(16).padStart(2, '0'),
    ).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  };
  Object.defineProperty(target, 'randomUUID', {
    configurable: true,
    writable: true,
    value: randomUUID,
  });
}

/**
 * The SHA-256 digest of `data` as 64 lowercase hex characters. Use this
 * instead of `crypto.subtle`, which a plain-HTTP network page does not have.
 */
export async function sha256Hex(data: BufferSource): Promise<string> {
  const subtle = (globalThis.crypto as PageCrypto).subtle;
  if (!subtle)
    return sha256Fallback(
      ArrayBuffer.isView(data)
        ? new Uint8Array(data.buffer, data.byteOffset, data.byteLength)
        : new Uint8Array(data),
    );
  const digest = await subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, '0'),
  ).join('');
}

// FIPS 180-4 round constants and initial hash values.
const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
  0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
  0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
  0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
  0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
  0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
  0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
  0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
  0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);
const INITIAL = [
  0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c,
  0x1f83d9ab, 0x5be0cd19,
];

function compress(
  state: Uint32Array,
  w: Uint32Array,
  view: DataView,
  offset: number,
): void {
  for (let i = 0; i < 16; i++) w[i] = view.getUint32(offset + i * 4);
  for (let i = 16; i < 64; i++) {
    const x = w[i - 15];
    const y = w[i - 2];
    const s0 = ((x >>> 7) | (x << 25)) ^ ((x >>> 18) | (x << 14)) ^ (x >>> 3);
    const s1 = ((y >>> 17) | (y << 15)) ^ ((y >>> 19) | (y << 13)) ^ (y >>> 10);
    w[i] = w[i - 16] + s0 + w[i - 7] + s1;
  }
  let a = state[0];
  let b = state[1];
  let c = state[2];
  let d = state[3];
  let e = state[4];
  let f = state[5];
  let g = state[6];
  let h = state[7];
  for (let i = 0; i < 64; i++) {
    const s1 =
      ((e >>> 6) | (e << 26)) ^
      ((e >>> 11) | (e << 21)) ^
      ((e >>> 25) | (e << 7));
    const t1 = (h + s1 + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0;
    const s0 =
      ((a >>> 2) | (a << 30)) ^
      ((a >>> 13) | (a << 19)) ^
      ((a >>> 22) | (a << 10));
    const t2 = (s0 + ((a & b) ^ (a & c) ^ (b & c))) | 0;
    h = g;
    g = f;
    f = e;
    e = (d + t1) | 0;
    d = c;
    c = b;
    b = a;
    a = (t1 + t2) | 0;
  }
  state[0] += a;
  state[1] += b;
  state[2] += c;
  state[3] += d;
  state[4] += e;
  state[5] += f;
  state[6] += g;
  state[7] += h;
}

/** Plain SHA-256 for pages without `crypto.subtle`; reads `bytes` without copying it. */
function sha256Fallback(bytes: Uint8Array): string {
  const state = new Uint32Array(INITIAL);
  const w = new Uint32Array(64);
  const whole = bytes.length - (bytes.length % 64);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let offset = 0; offset < whole; offset += 64)
    compress(state, w, view, offset);
  // The rest of the message, a 1 bit, zeros and the bit length fill one or
  // two final blocks.
  const rest = bytes.length - whole;
  const tail = new Uint8Array(rest + 9 > 64 ? 128 : 64);
  tail.set(bytes.subarray(whole));
  tail[rest] = 0x80;
  const tailView = new DataView(tail.buffer);
  const bits = bytes.length * 8;
  tailView.setUint32(tail.length - 8, Math.floor(bits / 0x100000000));
  tailView.setUint32(tail.length - 4, bits >>> 0);
  for (let offset = 0; offset < tail.length; offset += 64)
    compress(state, w, tailView, offset);
  return Array.from(state, (word) => word.toString(16).padStart(8, '0')).join(
    '',
  );
}
