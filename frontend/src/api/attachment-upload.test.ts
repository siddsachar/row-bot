import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { File as NodeFile } from 'node:buffer';
import { webcrypto } from 'node:crypto';
import * as wire from '../../../contracts/client-platform/v1/typescript/client';
import { FixtureTransport } from './fixtures';
import { HttpTransport } from './http';

const CHUNK = 1048576;
beforeEach(() => vi.stubGlobal('crypto', webcrypto));
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it('reports the bytes sent after each uploaded chunk, for the tile’s progress (B232)', async () => {
  const size = 2 * CHUNK + 10;
  vi.spyOn(wire, 'handshake').mockResolvedValue(
    await new FixtureTransport().connect(),
  );
  vi.spyOn(wire, 'beginUpload').mockResolvedValue({
    upload_id: 'upload-1',
    size_bytes: size,
    received_bytes: 0,
    expires_in_seconds: 1800,
  });
  const chunks = vi
    .spyOn(wire, 'uploadChunk')
    .mockImplementation(async (_base, _proof, upload, offset, data) => ({
      upload_id: upload,
      size_bytes: size,
      received_bytes: offset + data.size,
      expires_in_seconds: 1800,
    }));
  vi.spyOn(wire, 'completeUpload').mockResolvedValue({
    attachment_ref: 'conversation-a:attachment',
    name: 'large.bin',
    mime_type: 'application/octet-stream',
    size_bytes: size,
    revision: '1',
  });
  const http = new HttpTransport();
  await http.connect();
  const sent: number[] = [];
  const file = new NodeFile([new Uint8Array(size)], 'large.bin');
  await http.upload('conversation-a', file as File, undefined, (value) =>
    sent.push(value),
  );
  expect(chunks).toHaveBeenCalledTimes(3);
  expect(sent).toEqual([CHUNK, 2 * CHUNK, size]);
});
