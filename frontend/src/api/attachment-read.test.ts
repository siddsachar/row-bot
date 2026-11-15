import { afterEach, expect, it, vi } from 'vitest';
import { readAttachment } from '../../../contracts/client-platform/v1/typescript/client';

const proof = {
  client_session_id: '00000000-0000-4000-8000-000000000001',
  csrf_token: 'synthetic',
};
const MB = 1024 * 1024;

afterEach(() => vi.unstubAllGlobals());

function serves(size: number) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({ ok: true, blob: async () => ({ size }) }),
  );
}

it('reads a chat file up to the server’s own 200 MB limit, such as a long recording', async () => {
  // 31 Oct: a 1080p browser recording passed 25 MB and the whole window said "out of date".
  serves(60 * MB);
  await expect(readAttachment('', proof, 'chat:recording')).resolves.toEqual({
    size: 60 * MB,
  });
  serves(200 * MB + 1);
  await expect(readAttachment('', proof, 'chat:recording')).rejects.toThrow(
    'protocol_incompatible',
  );
});
