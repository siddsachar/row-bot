import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { Blob as NodeBlob } from 'node:buffer';
import { webcrypto } from 'node:crypto';
import { downloadArtifactExport } from '../../../contracts/client-platform/v1/typescript/client';
import type { ArtifactExport } from './types';

const proof = {
  client_session_id: '00000000-0000-4000-8000-000000000001',
  csrf_token: 'synthetic',
};
const bytes = new TextEncoder().encode('<html>saved design</html>');
const hash = Array.from(
  new Uint8Array(await webcrypto.subtle.digest('SHA-256', bytes)),
  (byte) => byte.toString(16).padStart(2, '0'),
).join('');
const descriptor: ArtifactExport = {
  export_id: '00000000-0000-4000-8000-000000000002',
  resource_id: 'design',
  resource_revision: '1',
  format: 'html',
  pptx_mode: null,
  page_count: 1,
  filename: 'Design.html',
  media_type: 'text/html',
  size_bytes: bytes.length,
  sha256: hash,
  status: 'ready',
  warnings: [],
  expires_at: 2000000000,
};
afterEach(() => vi.unstubAllGlobals());
beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto);
  vi.stubGlobal('Blob', NodeBlob);
});

it.each([null, 'identity', 'gzip', 'br', 'deflate'])(
  'verifies decoded content independently of %s transfer encoding',
  async (encoding) => {
    const headers: Record<string, string> = {
      'Content-Length': String(
        encoding && encoding !== 'identity' ? 12 : bytes.length,
      ),
    };
    if (encoding) headers['Content-Encoding'] = encoding;
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response(bytes, { headers })),
    );
    const result = await downloadArtifactExport(
      '',
      proof,
      'chat',
      'binding',
      descriptor,
    );
    expect(await result.text()).toBe('<html>saved design</html>');
    expect(result.type).toBe('text/html');
  },
);

it.each(['overflow', 'truncated', 'digest', 'encoding', 'length'])(
  'rejects %s corruption and releases the response',
  async (reason) => {
    const cancel = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          reason === 'overflow'
            ? new Uint8Array(bytes.length + 1)
            : reason === 'truncated'
              ? bytes.slice(1)
              : bytes,
        );
        if (!['overflow', 'encoding', 'length'].includes(reason))
          controller.close();
      },
      cancel,
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(body, {
          headers: {
            'Content-Encoding': reason === 'encoding' ? 'unreviewed' : 'gzip',
            ...(reason === 'length'
              ? { 'Content-Encoding': 'identity', 'Content-Length': '1' }
              : {}),
          },
        }),
      ),
    );
    await expect(
      downloadArtifactExport('', proof, 'chat', 'binding', {
        ...descriptor,
        ...(reason === 'digest' ? { sha256: '0'.repeat(64) } : {}),
      }),
    ).rejects.toThrow('protocol_incompatible');
    expect(body.locked).toBe(false);
    if (['overflow', 'encoding', 'length'].includes(reason))
      expect(cancel).toHaveBeenCalledOnce();
  },
);
