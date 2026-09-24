import { expect, it, vi } from 'vitest';
import type { ResourceView } from '../../api/types';
import {
  createArtifactDesignSessions,
  DesignFormSession,
  type DesignReceipt,
  type DesignSessionOwner,
} from './artifact-design-sessions';

const resource = {
  available: true,
  resource_revision: 'r1',
  binding: {
    kind: 'artifact',
    resource_id: 'design',
    binding_id: 'binding',
    revision: 'b1',
  },
} as ResourceView;
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}
function fixture() {
  const state = {
    identity: 'instance/epoch/session',
    conversationId: 'chat',
    conversationRevision: '1',
    loading: false,
    resources: [structuredClone(resource)],
  };
  const listeners = new Set<() => void>();
  const success = (id: string, operation = 'brand'): DesignReceipt => ({
    command_id: id,
    conversation_id: 'chat',
    binding_id: 'binding',
    binding_revision: 'b1',
    resource_id: 'design',
    status: 'completed',
    artifact_design: {
      resource_id: 'design',
      resource_revision: 'r2',
      operation,
      status: 'saved',
      code: '',
    },
  });
  const owner: DesignSessionOwner = {
    getSnapshot: () => state,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    load: vi.fn(),
    review: vi.fn(),
    draftFix: vi.fn(async () => 'Local fix draft'),
    stageUpload: vi.fn(async () => ({
      upload_id: 'staged',
      sha256: 'a'.repeat(64),
      size_bytes: 3,
    })),
    importPreview: vi.fn(async (_scope, body) => ({
      resource_id: 'design',
      resource_revision: body.expected_revision,
      filename: body.filename,
      source_sha256: body.sha256,
      page_count: 1,
      pages: [{ title: 'Synthetic', has_notes: false }],
      replacing_page_count: 1,
    })),
    presetReview: vi.fn(async () => ({ nonce: 'private-review' })),
    execute: vi.fn(async (_scope, id, type, payload) =>
      success(
        id,
        type === 'artifact.design.control'
          ? String(payload.operation)
          : type === 'artifact.asset.upload'
            ? 'asset_upload'
            : type === 'artifact.document.import'
              ? 'document_import'
              : 'preset_' + payload.action,
      ),
    ),
    receipt: vi.fn(async () => null),
  };
  const sessions = createArtifactDesignSessions(owner);
  const entry = sessions.get('chat', resource);
  return {
    state,
    owner,
    sessions,
    entry,
    success,
    notify: () => listeners.forEach((listener) => listener()),
  };
}

it('reuses exact scope across remount and never dispatches on get, read or draft', async () => {
  const f = fixture();
  expect(f.sessions.hasRetained()).toBe(false);
  f.entry.form.set('dirtySource', 'original');
  expect(f.sessions.hasRetained()).toBe(true);
  f.entry.form.set('presetName', 'Retained draft');
  expect(f.sessions.get('chat', resource)).toBe(f.entry);
  expect(f.entry.form.getSnapshot().presetName).toBe('Retained draft');
  await expect(f.entry.draftFix('finding', 'page', 'r1')).resolves.toBe(
    'Local fix draft',
  );
  expect(f.owner.execute).not.toHaveBeenCalled();
  expect(f.owner.stageUpload).not.toHaveBeenCalled();
});

it('retains the original payload and top-level revision through unknown and partial recovery', async () => {
  const f = fixture();
  vi.mocked(f.owner.execute).mockRejectedValueOnce(new Error('lost response'));
  const parameters = { primary_color: '#112233' };
  await expect(
    f.entry.apply('brand', parameters, 'r1', 'page'),
  ).rejects.toThrow('unconfirmed');
  const original = structuredClone(f.entry.getSnapshot().attempt!);
  parameters.primary_color = '#445566';
  f.state.conversationRevision = '2';
  f.state.resources[0].resource_revision = 'r2';
  await expect(
    f.entry.apply('brand', parameters, 'r2', 'page'),
  ).rejects.toThrow('unconfirmed');
  await f.entry.recover();
  expect(f.owner.execute).toHaveBeenCalledTimes(1);
  vi.mocked(f.owner.receipt).mockResolvedValue({
    ...f.success(original.commandId),
    status: 'partial',
    artifact_design: {
      ...f.success(original.commandId).artifact_design!,
      status: 'partial',
      code: 'artifact_design_unconfirmed',
    },
  });
  await f.entry.recover();
  expect(f.owner.execute).toHaveBeenCalledTimes(2);
  expect(vi.mocked(f.owner.execute).mock.calls[1]).toEqual(
    vi.mocked(f.owner.execute).mock.calls[0],
  );
  expect(vi.mocked(f.owner.execute).mock.calls[1][4]).toBe('1');
  expect(f.entry.getSnapshot().attempt).toBeNull();
});

it.each(['completed', 'rejected', 'admitting'])(
  'does not resubmit an original %s receipt',
  async (status) => {
    const f = fixture();
    vi.mocked(f.owner.execute).mockRejectedValueOnce(new Error('lost'));
    await expect(f.entry.apply('brand', {}, 'r1', 'page')).rejects.toThrow();
    const id = f.entry.getSnapshot().attempt!.commandId;
    vi.mocked(f.owner.receipt).mockResolvedValue(
      status === 'completed'
        ? f.success(id)
        : { command_id: id, status, code: 'action_denied' },
    );
    await f.entry.recover();
    expect(f.owner.execute).toHaveBeenCalledTimes(1);
    if (status === 'rejected') {
      f.entry.dismissRejection();
      expect(f.entry.getSnapshot().attempt).toBeNull();
    }
  },
);

it('does not resubmit a foreign partial code or mismatched receipt', async () => {
  const f = fixture();
  vi.mocked(f.owner.execute).mockRejectedValue(new Error('lost'));
  await expect(f.entry.apply('brand', {}, 'r1', 'page')).rejects.toThrow();
  const id = f.entry.getSnapshot().attempt!.commandId;
  vi.mocked(f.owner.receipt).mockResolvedValue({
    ...f.success(id),
    artifact_design: {
      ...f.success(id).artifact_design!,
      status: 'partial',
      code: 'foreign',
    },
  });
  await f.entry.recover();
  vi.mocked(f.owner.receipt).mockResolvedValue({
    ...f.success(id),
    binding_id: 'foreign',
  });
  await f.entry.recover();
  expect(f.owner.execute).toHaveBeenCalledTimes(1);
  expect(f.entry.getSnapshot().attempt).not.toBeNull();
});

it('blocks a second effect while first execution is pending', async () => {
  const f = fixture(),
    pending = deferred<DesignReceipt>();
  vi.mocked(f.owner.execute).mockReturnValue(pending.promise);
  const first = f.entry.apply('brand', {}, 'r1', 'page');
  const id = f.entry.getSnapshot().attempt!.commandId;
  await expect(
    f.entry.upload(new File(['123'], 'a.png'), 'r1'),
  ).rejects.toThrow();
  pending.resolve(f.success(id));
  await first;
  expect(f.owner.execute).toHaveBeenCalledTimes(1);
});

it('auth purge permanently tombstones late operation and form settlement', async () => {
  const f = fixture(),
    pending = deferred<DesignReceipt>();
  vi.mocked(f.owner.execute).mockReturnValue(pending.promise);
  const first = f.entry.apply('brand', {}, 'r1', 'page');
  const caught = expect(first).rejects.toThrow('authentication_required');
  const id = f.entry.getSnapshot().attempt!.commandId;
  f.entry.form.set('presetName', 'Private draft');
  f.state.identity = '';
  f.notify();
  pending.resolve(f.success(id));
  await caught;
  f.entry.form.set('presetName', 'Late private draft');
  expect(f.entry.form.getSnapshot().presetName).toBe('');
  expect(f.entry.getSnapshot().attempt).toBeNull();
  f.state.identity = 'new-session';
  f.notify();
  expect(f.sessions.get('chat', resource)).not.toBe(f.entry);
});

it('observed binding ABA cannot resurrect an old retained session', async () => {
  const f = fixture();
  f.entry.form.set('dirtySource', 'old');
  f.state.resources = [];
  f.notify();
  f.state.resources = [structuredClone(resource)];
  f.notify();
  expect(() => f.sessions.get('chat', resource)).toThrow();
  expect(f.entry.getSnapshot().revoked).toBe(true);
});

it('rejects stale supplied scope and bounds eight retained drafts without eviction', () => {
  const f = fixture();
  expect(() =>
    f.sessions.get('chat', {
      ...resource,
      binding: { ...resource.binding, revision: 'wrong' },
    }),
  ).toThrow();
  for (let index = 0; index < 8; index++) {
    const next = {
      ...resource,
      binding: {
        ...resource.binding,
        binding_id: 'b-' + index,
        resource_id: 'd-' + index,
      },
    };
    f.state.resources.push(next);
    f.sessions.get('chat', next).form.set('dirtySource', 'retained');
  }
  const next = {
    ...resource,
    binding: { ...resource.binding, binding_id: 'overflow' },
  };
  f.state.resources.push(next);
  expect(() => f.sessions.get('chat', next)).toThrow('design_session_limit');
});

it('aborts hidden reads without treating a completed late query as current', async () => {
  const f = fixture(),
    pending = deferred<string>();
  vi.mocked(f.owner.draftFix).mockReturnValue(pending.promise);
  const read = f.entry.draftFix('finding', 'page', 'r1');
  const caught = expect(read).rejects.toThrow('query_cancelled');
  f.entry.hide();
  pending.resolve('Must not reach composer');
  await caught;
  expect(vi.mocked(f.owner.draftFix).mock.calls[0][2].aborted).toBe(true);
});

it('stages only on explicit upload and does not reload staged data after uncertain dispatch', async () => {
  const f = fixture();
  vi.mocked(f.owner.execute).mockRejectedValueOnce(new Error('lost'));
  await expect(
    f.entry.upload(new File(['123'], 'a.png'), 'r1'),
  ).rejects.toThrow();
  expect(f.owner.stageUpload).toHaveBeenCalledTimes(1);
  const attempt = f.entry.getSnapshot().attempt!;
  expect(attempt.payload).toMatchObject({
    filename: 'a.png',
    upload_id: 'staged',
    size_bytes: 3,
  });
  await f.entry.recover();
  expect(f.owner.stageUpload).toHaveBeenCalledTimes(1);
});

it('previews selected document bytes without dispatch and retains one import command after response loss', async () => {
  const f = fixture();
  const file = new File(['123'], 'synthetic.docx');
  const prepared = await f.entry.prepareImport(file, 'r1');
  expect(prepared.preview.page_count).toBe(1);
  expect(f.owner.execute).not.toHaveBeenCalled();
  vi.mocked(f.owner.execute).mockRejectedValueOnce(new Error('lost response'));
  await expect(
    f.entry.importDocument({
      staged: prepared.staged,
      filename: file.name,
      revision: 'r1',
      replace: false,
    }),
  ).rejects.toThrow('unconfirmed');
  const attempt = f.entry.getSnapshot().attempt!;
  expect(attempt.type).toBe('artifact.document.import');
  expect(attempt.payload).toMatchObject({
    upload_id: 'staged',
    replace: false,
  });
  await f.entry.recover();
  expect(f.owner.stageUpload).toHaveBeenCalledTimes(1);
  expect(f.owner.importPreview).toHaveBeenCalledTimes(1);
});

it('preserves one exact global preset nonce and ID after dispatch uncertainty', async () => {
  const f = fixture();
  vi.mocked(f.owner.execute).mockRejectedValueOnce(new Error('lost'));
  await expect(
    f.entry.mutatePreset({ action: 'save', name: 'Shared' }, 'r1'),
  ).rejects.toThrow();
  const attempt = f.entry.getSnapshot().attempt!;
  expect(attempt.payload.nonce).toBe('private-review');
  expect(f.owner.presetReview).toHaveBeenCalledTimes(1);
  await f.entry.recover();
  expect(f.owner.presetReview).toHaveBeenCalledTimes(1);
});

it('clears a failed pre-effect preparation so a new explicit attempt remains possible', async () => {
  const f = fixture();
  vi.mocked(f.owner.stageUpload).mockRejectedValueOnce(
    new Error('staging unavailable'),
  );
  await expect(
    f.entry.upload(new File(['123'], 'a.png'), 'r1'),
  ).rejects.toThrow('preparation');
  expect(f.entry.getSnapshot().attempt).toBeNull();
  expect(f.owner.execute).not.toHaveBeenCalled();
  await f.entry.upload(new File(['123'], 'a.png'), 'r1');
  expect(f.owner.execute).toHaveBeenCalledTimes(1);
});

it('explicit form reset never discards an in-flight operation and disposal cannot be undone', () => {
  const form = new DesignFormSession();
  form.set('dirtySource', 'source');
  form.operation.current = Symbol('pending');
  form.reset();
  expect(form.getSnapshot().dirtySource).toBe('source');
  form.dispose();
  form.set('dirtySource', 'late');
  expect(form.getSnapshot().dirtySource).toBeNull();
});

it('settles the original hidden conversation without leaving a permanent pending command', async () => {
  const f = fixture(),
    pending = deferred<DesignReceipt>();
  vi.mocked(f.owner.execute).mockReturnValue(pending.promise);
  const first = f.entry.apply('brand', {}, 'r1', 'page');
  const id = f.entry.getSnapshot().attempt!.commandId;
  f.state.conversationId = 'other';
  f.state.resources = [];
  f.notify();
  pending.resolve(f.success(id));
  await first;
  expect(f.entry.getSnapshot().attempt).toBeNull();
  f.state.conversationId = 'chat';
  f.state.resources = [resource];
  f.notify();
  expect(f.sessions.get('chat', resource)).toBe(f.entry);
});

it('refuses mismatched staged bytes before a command can be dispatched', async () => {
  const f = fixture();
  vi.mocked(f.owner.stageUpload).mockResolvedValue({
    upload_id: 'owned',
    size_bytes: 4,
    sha256: 'b'.repeat(64),
  });
  await expect(
    f.entry.upload(new File(['123'], 'a.png'), 'r1'),
  ).rejects.toThrow('preparation');
  expect(f.owner.execute).not.toHaveBeenCalled();
  expect(f.entry.getSnapshot().attempt).toBeNull();
});
