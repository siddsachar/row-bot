import { afterEach, describe, expect, it, vi } from 'vitest';
import { webcrypto } from 'node:crypto';
import { provideRandomUUID, sha256Hex } from './crypto';

const UUID_V4 =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

/** What a page at a plain http:// network address gets from the browser. */
function plainHttpPage() {
  const page = {
    getRandomValues: (array: Uint8Array<ArrayBuffer>) =>
      webcrypto.getRandomValues(array),
  };
  vi.stubGlobal('crypto', page);
  return page;
}

async function nativeHex(bytes: Uint8Array<ArrayBuffer>): Promise<string> {
  return Array.from(
    new Uint8Array(await webcrypto.subtle.digest('SHA-256', bytes)),
    (byte) => byte.toString(16).padStart(2, '0'),
  ).join('');
}

afterEach(() => vi.unstubAllGlobals());

describe('crypto.randomUUID', () => {
  it('is given to a plain-HTTP page by the entry point import', async () => {
    plainHttpPage();
    vi.resetModules();
    await import('./random-uuid');
    const ids = Array.from({ length: 64 }, () => crypto.randomUUID());
    for (const id of ids) expect(id).toMatch(UUID_V4);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("keeps the browser's own on a secure page", () => {
    const own = () => '00000000-0000-4000-8000-000000000001' as const;
    const page = { ...plainHttpPage(), randomUUID: own };
    provideRandomUUID(page as unknown as Crypto);
    expect(page.randomUUID).toBe(own);
  });
});

describe('sha256Hex', () => {
  it('matches the published SHA-256 test vectors on a plain-HTTP page', async () => {
    plainHttpPage();
    const text = (value: string) => new TextEncoder().encode(value);
    expect(await sha256Hex(text(''))).toBe(
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    );
    expect(await sha256Hex(text('abc'))).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
    expect(
      await sha256Hex(
        text('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq'),
      ),
    ).toBe('248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1');
  });

  it('agrees with crypto.subtle across block boundaries and buffer views', async () => {
    const source = Uint8Array.from(
      { length: 1_000_004 },
      (_, index) => (index * 2654435761) >>> 24,
    );
    // Every length through the 55/56/64-byte padding edges, then a large file.
    const lengths = Array.from({ length: 140 }, (_, index) => index);
    lengths.push(1_000_003);
    plainHttpPage();
    for (const length of lengths) {
      // Offset views check that only the viewed bytes are hashed.
      const view = source.subarray(1, 1 + length);
      expect(await sha256Hex(view)).toBe(await nativeHex(view));
      expect(await sha256Hex(view.slice().buffer)).toBe(await nativeHex(view));
    }
  });

  it('uses crypto.subtle on a secure page', async () => {
    const digest = vi.fn(async () => new Uint8Array(32).fill(0xab).buffer);
    vi.stubGlobal('crypto', { subtle: { digest } });
    expect(await sha256Hex(new Uint8Array([1, 2, 3]))).toBe('ab'.repeat(32));
    expect(digest).toHaveBeenCalledWith('SHA-256', new Uint8Array([1, 2, 3]));
  });
});
