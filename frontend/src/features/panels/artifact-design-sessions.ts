import type { ResourceView } from '../../api/types';
import type {
  DesignBrand,
  DesignControlsProps,
  DesignControlsState,
  DesignReviewState,
  DesignSection,
} from './ArtifactDesignControls';

export type DesignScope = {
  conversation_id: string;
  resource_id: string;
  binding_id: string;
  binding_revision: string;
};
export type DesignPresetIntent = {
  action: 'save' | 'delete';
  name: string;
  preset_id?: string;
};
export type DesignFormState = {
  section: DesignSection;
  state: DesignControlsState | null;
  brand: DesignBrand | null;
  styles: Record<string, string>;
  review: DesignReviewState | null;
  scope: 'page' | 'project';
  action: string;
  target: string;
  error: string;
  notice: string;
  loading: boolean;
  saving: boolean;
  file: File | null;
  presetName: string;
  presetReview: DesignPresetIntent | null;
  reload: number;
  dirtySource: string | null;
  dirtyFields: string[];
  controlPage: boolean;
  reviewPage: boolean;
};
const blank = (): DesignFormState => ({
  section: 'elements',
  state: null,
  brand: null,
  styles: {},
  review: null,
  scope: 'page',
  action: 'navigate',
  target: '',
  error: '',
  notice: '',
  loading: false,
  saving: false,
  file: null,
  presetName: '',
  presetReview: null,
  reload: 0,
  dirtySource: null,
  dirtyFields: [],
  controlPage: false,
  reviewPage: false,
});

/** Retained by the runtime owner; disposal permanently rejects late settlement. */
export class DesignFormSession {
  private value = blank();
  private listeners = new Set<() => void>();
  private dead = false;
  operation: { current: symbol | null } = { current: null };
  getSnapshot = () => this.value;
  subscribe = (listener: () => void) => {
    if (!this.dead) this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  set = <K extends keyof DesignFormState>(
    key: K,
    value:
      DesignFormState[K] | ((prior: DesignFormState[K]) => DesignFormState[K]),
  ) => {
    if (this.dead) return;
    this.value = {
      ...this.value,
      [key]: typeof value === 'function' ? value(this.value[key]) : value,
    };
    this.listeners.forEach((listener) => listener());
  };
  reset = () => {
    if (this.dead || this.operation.current) return;
    this.value = { ...blank(), reload: this.value.reload + 1 };
    this.listeners.forEach((listener) => listener());
  };
  dispose = () => {
    this.dead = true;
    this.value = blank();
    this.operation.current = null;
    this.listeners.forEach((listener) => listener());
    this.listeners.clear();
  };
  retained = () =>
    Boolean(
      this.value.dirtySource || this.value.saving || this.operation.current,
    );
  markDirty = (field: string, source: string) => {
    if (!this.value.dirtySource) this.set('dirtySource', source);
    if (!this.value.dirtyFields.includes(field))
      this.set('dirtyFields', [...this.value.dirtyFields, field]);
  };
  committed = (operation: string) => {
    const fields =
      operation === 'brand' || operation === 'preset'
        ? ['brand']
        : operation === 'style'
          ? ['styles']
          : operation === 'hotspot'
            ? ['action', 'target']
            : operation === 'asset_upload'
              ? ['file']
              : operation.startsWith('preset_')
                ? ['presetName']
                : [];
    const remaining = this.value.dirtyFields.filter(
      (field) => !fields.includes(field),
    );
    if (this.value.dirtyFields.length && !remaining.length)
      this.set('dirtySource', null);
    this.set('dirtyFields', remaining);
  };
}

export type DesignOutcome = {
  resource_id: string;
  resource_revision: string;
  operation: string;
  status: 'saved' | 'unchanged' | 'partial';
  code: string;
  asset_id?: string;
  preset_id?: string;
};
export type DesignReceipt = {
  command_id: string;
  conversation_id?: string;
  binding_id?: string;
  binding_revision?: string;
  resource_id?: string;
  status: string;
  code?: string;
  artifact_design?: DesignOutcome;
};
export type DesignCommandType =
  | 'artifact.design.control'
  | 'artifact.asset.upload'
  | 'artifact.document.import'
  | 'artifact.notes.generate'
  | 'artifact.preset.mutate';
export type DesignSessionOwner = {
  getSnapshot(): {
    identity: string;
    conversationId: string | null;
    conversationRevision: string;
    loading: boolean;
    resources: ResourceView[];
  };
  subscribe(listener: () => void): () => void;
  load(
    scope: DesignScope,
    options: Parameters<DesignControlsProps['load']>[0],
    signal: AbortSignal,
  ): Promise<DesignControlsState>;
  review(
    scope: DesignScope,
    options: Parameters<DesignControlsProps['review']>[0],
    signal: AbortSignal,
  ): Promise<DesignReviewState>;
  draftFix(
    scope: DesignScope,
    options: { finding_id: string; page_id: string; expected_revision: string },
    signal: AbortSignal,
  ): Promise<string>;
  stageUpload(
    scope: DesignScope,
    file: File,
    commandId: string,
    signal: AbortSignal,
  ): Promise<{ upload_id: string; sha256: string; size_bytes: number }>;
  importPreview?(
    scope: DesignScope,
    body: import('../../api/types').ArtifactDocumentImportPreviewRequest,
    signal: AbortSignal,
  ): Promise<import('../../api/types').ArtifactDocumentImportPreview>;
  presetReview(
    scope: DesignScope,
    options: DesignPresetIntent & {
      expected_revision: string;
      command_id: string;
    },
    signal: AbortSignal,
  ): Promise<{ nonce: string }>;
  execute(
    scope: DesignScope,
    commandId: string,
    type: DesignCommandType,
    payload: Record<string, unknown>,
    expectedRevision: string,
  ): Promise<DesignReceipt>;
  receipt(scope: DesignScope, commandId: string): Promise<DesignReceipt | null>;
};

export type DesignAttempt = {
  commandId: string;
  expectedRevision: string;
  type: DesignCommandType;
  payload: Record<string, unknown>;
  status: 'preparing' | 'pending' | 'uncertain' | 'partial' | 'rejected';
  code: string;
};
export type DesignSessionState = {
  revoked: boolean;
  attempt: DesignAttempt | null;
  recovering: boolean;
  notice: string;
};

export function createArtifactDesignSessions(owner: DesignSessionOwner) {
  const entries = new Map<string, ReturnType<typeof make>>();
  let identity = owner.getSnapshot().identity;
  let disposed = false;
  function make(scope: DesignScope) {
    const ownedIdentity = identity;
    const form = new DesignFormSession();
    const listeners = new Set<() => void>();
    const queries = new Set<AbortController>();
    let dead = false;
    let value: DesignSessionState = {
      revoked: false,
      attempt: null,
      recovering: false,
      notice: '',
    };
    const update = (patch: Partial<DesignSessionState>) => {
      if (!dead) {
        value = { ...value, ...patch };
        listeners.forEach((listener) => listener());
      }
    };
    function revoke(purge = false) {
      if (dead || (value.revoked && !purge)) return;
      queries.forEach((query) => query.abort());
      queries.clear();
      if (purge) {
        form.dispose();
        value = { revoked: true, attempt: null, recovering: false, notice: '' };
        dead = true;
      } else value = { ...value, revoked: true };
      listeners.forEach((listener) => listener());
      if (purge) listeners.clear();
    }
    function guard(requireActive = true) {
      sync();
      const current = owner.getSnapshot();
      const resource = current.resources.find(
        (item) => item.binding.binding_id === scope.binding_id,
      );
      if (
        dead ||
        disposed ||
        !ownedIdentity ||
        current.identity !== ownedIdentity ||
        value.revoked
      )
        throw new Error('authentication_required');
      if (!requireActive && current.conversationId !== scope.conversation_id)
        return;
      if (
        current.loading ||
        current.conversationId !== scope.conversation_id ||
        !resource?.available ||
        resource.binding.kind !== 'artifact' ||
        resource.binding.resource_id !== scope.resource_id ||
        resource.binding.revision !== scope.binding_revision
      )
        throw new Error('resource_binding_revoked');
    }
    async function query<T>(read: (signal: AbortSignal) => Promise<T>) {
      guard();
      const controller = new AbortController();
      queries.add(controller);
      try {
        const result = await read(controller.signal);
        guard();
        if (controller.signal.aborted) throw new Error('query_cancelled');
        return result;
      } finally {
        queries.delete(controller);
      }
    }
    function accept(
      receipt: DesignReceipt,
      attempt: DesignAttempt,
    ): DesignOutcome | null {
      if (
        receipt.command_id !== attempt.commandId ||
        (receipt.conversation_id !== undefined &&
          receipt.conversation_id !== scope.conversation_id) ||
        (receipt.binding_id !== undefined &&
          receipt.binding_id !== scope.binding_id) ||
        (receipt.binding_revision !== undefined &&
          receipt.binding_revision !== scope.binding_revision) ||
        (receipt.resource_id !== undefined &&
          receipt.resource_id !== scope.resource_id)
      )
        throw new Error('receipt_mismatch');
      const outcome = receipt.artifact_design;
      const operation =
        attempt.type === 'artifact.design.control'
          ? attempt.payload.operation
          : attempt.type === 'artifact.asset.upload'
            ? 'asset_upload'
            : attempt.type === 'artifact.document.import'
              ? 'document_import'
              : attempt.type === 'artifact.notes.generate'
                ? 'notes_generate'
                : 'preset_' + attempt.payload.action;
      if (
        outcome &&
        (outcome.resource_id !== scope.resource_id ||
          outcome.operation !== operation)
      )
        throw new Error('receipt_mismatch');
      if (
        receipt.status === 'completed' &&
        outcome &&
        outcome.resource_id === scope.resource_id &&
        outcome.operation === operation &&
        typeof outcome.resource_revision === 'string' &&
        outcome.resource_revision.length > 0 &&
        outcome.resource_revision.length <= 128 &&
        receipt.conversation_id === scope.conversation_id &&
        receipt.binding_id === scope.binding_id &&
        receipt.binding_revision === scope.binding_revision &&
        (outcome.status === 'saved' || outcome.status === 'unchanged')
      ) {
        update({ attempt: null, notice: 'Original design command confirmed.' });
        form.committed(outcome.operation);
        form.set('reload', (prior) => prior + 1);
        return outcome;
      }
      update({
        attempt: {
          ...attempt,
          status:
            receipt.status === 'rejected'
              ? 'rejected'
              : outcome?.status === 'partial'
                ? 'partial'
                : 'uncertain',
          code: receipt.code ?? outcome?.code ?? 'artifact_design_unconfirmed',
        },
      });
      return null;
    }
    async function dispatch(attempt: DesignAttempt) {
      guard();
      update({ attempt: { ...attempt, status: 'pending' } });
      try {
        const receipt = await owner.execute(
          scope,
          attempt.commandId,
          attempt.type,
          attempt.payload,
          attempt.expectedRevision,
        );
        guard(false);
        const outcome = accept(receipt, attempt);
        if (outcome) return outcome;
      } catch {
        guard(false);
        update({
          attempt: {
            ...attempt,
            status: 'uncertain',
            code: 'artifact_design_unconfirmed',
          },
        });
      }
      throw new Error('artifact_design_unconfirmed');
    }
    async function effect(
      type: DesignCommandType,
      revision: string,
      payload: Record<string, unknown>,
      prepare?: (id: string) => Promise<Record<string, unknown>>,
    ) {
      guard();
      if (value.attempt) throw new Error('original_design_command_unconfirmed');
      const attempt: DesignAttempt = {
        commandId: crypto.randomUUID(),
        expectedRevision: owner.getSnapshot().conversationRevision,
        type,
        payload: structuredClone({
          target: { kind: 'artifact', ...scope, resource_revision: revision },
          ...payload,
        }),
        status: 'preparing',
        code: '',
      };
      // conversation_id belongs to admission context, never to the strict WriteTarget.
      delete (attempt.payload.target as Record<string, unknown>)
        .conversation_id;
      update({ attempt, notice: '' });
      if (prepare) {
        try {
          attempt.payload = {
            ...attempt.payload,
            ...(await prepare(attempt.commandId)),
          };
          guard();
        } catch {
          guard(false);
          update({ attempt: null });
          throw new Error('design_preparation_unavailable');
        }
      }
      return dispatch(attempt);
    }
    const entry = {
      scope,
      form,
      getSnapshot: () => value,
      subscribe: (listener: () => void) => {
        if (!dead) listeners.add(listener);
        return () => listeners.delete(listener);
      },
      guard,
      revoke,
      retained: () =>
        Boolean(value.attempt || value.recovering || form.retained()),
      hide: () => {
        queries.forEach((query) => query.abort());
        queries.clear();
      },
      load: (options: Parameters<DesignControlsProps['load']>[0]) =>
        query((signal) => owner.load(scope, options, signal)),
      review: (options: Parameters<DesignControlsProps['review']>[0]) =>
        query((signal) => owner.review(scope, options, signal)),
      draftFix: (
        finding_id: string,
        page_id: string,
        expected_revision: string,
      ) =>
        query((signal) =>
          owner.draftFix(
            scope,
            { finding_id, page_id, expected_revision },
            signal,
          ),
        ),
      apply: (
        operation: Parameters<DesignControlsProps['apply']>[0],
        parameters: Record<string, unknown>,
        revision: string,
        page_id: string,
        element_id?: string,
      ) =>
        effect('artifact.design.control', revision, {
          operation,
          parameters,
          page_id,
          ...(element_id ? { element_id } : {}),
        }),
      upload: async (file: File, revision: string) => {
        if (
          !file.size ||
          file.size > 25 * 1024 * 1024 ||
          file.name.length > 256
        )
          throw new Error('asset_too_large');
        return effect(
          'artifact.asset.upload',
          revision,
          { filename: file.name },
          async (id) => {
            const staged = await query((signal) =>
              owner.stageUpload(scope, file, id, signal),
            );
            if (
              staged.size_bytes !== file.size ||
              !/^[a-f0-9]{64}$/.test(staged.sha256) ||
              typeof staged.upload_id !== 'string' ||
              !staged.upload_id ||
              staged.upload_id.length > 256
            )
              throw new Error('upload_identity_conflict');
            return staged;
          },
        );
      },
      prepareImport: async (file: File, revision: string) => {
        const importPreview = owner.importPreview;
        if (!importPreview) throw new Error('capability_unavailable');
        if (
          !/\.(pptx|docx)$/i.test(file.name) ||
          !file.size ||
          file.size > 25 * 1024 * 1024
        )
          throw new Error('invalid_document_import');
        const staged = await query((signal) =>
          owner.stageUpload(scope, file, crypto.randomUUID(), signal),
        );
        if (
          staged.size_bytes !== file.size ||
          !/^[a-f0-9]{64}$/.test(staged.sha256) ||
          !staged.upload_id ||
          staged.upload_id.length > 256
        )
          throw new Error('upload_identity_conflict');
        const preview = await query((signal) =>
          importPreview(
            scope,
            {
              expected_revision: revision,
              upload_id: staged.upload_id,
              filename: file.name,
              sha256: staged.sha256,
              size_bytes: staged.size_bytes,
            },
            signal,
          ),
        );
        if (
          preview.resource_id !== scope.resource_id ||
          preview.resource_revision !== revision ||
          preview.source_sha256 !== staged.sha256 ||
          preview.filename !== file.name
        )
          throw new Error('document_import_identity_conflict');
        return { staged, preview };
      },
      importDocument: (input: {
        staged: { upload_id: string; sha256: string; size_bytes: number };
        filename: string;
        revision: string;
        replace: boolean;
      }) =>
        effect('artifact.document.import', input.revision, {
          upload_id: input.staged.upload_id,
          filename: input.filename,
          sha256: input.staged.sha256,
          size_bytes: input.staged.size_bytes,
          replace: input.replace,
        }),
      generateNotes: (pageId: string, revision: string) =>
        effect('artifact.notes.generate', revision, { page_id: pageId }),
      mutatePreset: async (options: DesignPresetIntent, revision: string) => {
        const outcome = await effect(
          'artifact.preset.mutate',
          revision,
          options,
          (id) =>
            query((signal) =>
              owner.presetReview(
                scope,
                { ...options, expected_revision: revision, command_id: id },
                signal,
              ),
            ),
        );
        return {
          ...options,
          resource_id: outcome.resource_id,
          resource_revision: outcome.resource_revision,
          preset_id: outcome.preset_id ?? options.preset_id ?? '',
        };
      },
      recover: async () => {
        guard();
        const attempt = value.attempt;
        if (
          !attempt ||
          value.recovering ||
          ['pending', 'preparing'].includes(attempt.status)
        )
          return;
        update({ recovering: true });
        try {
          const receipt = await owner.receipt(scope, attempt.commandId);
          guard();
          if (receipt && accept(receipt, attempt)) return;
          if (
            receipt?.artifact_design?.status === 'partial' &&
            receipt.artifact_design.code === 'artifact_design_unconfirmed'
          )
            await dispatch(attempt);
          else if (!receipt)
            update({
              notice:
                'The original receipt is unavailable. No effect was repeated.',
            });
        } catch {
          guard();
          update({
            notice:
              'The original command remains unconfirmed. Review the current design and retained recovery files.',
          });
        } finally {
          update({ recovering: false });
        }
      },
      dismissRejection: () => {
        guard();
        if (value.attempt?.status === 'rejected') update({ attempt: null });
      },
    };
    return entry;
  }
  function sync() {
    const current = owner.getSnapshot();
    if (current.identity !== identity) {
      entries.forEach((entry) => entry.revoke(true));
      entries.clear();
      identity = current.identity;
    }
    if (current.loading || !current.conversationId) return;
    for (const entry of entries.values()) {
      if (entry.scope.conversation_id !== current.conversationId) continue;
      const resource = current.resources.find(
        (item) => item.binding.binding_id === entry.scope.binding_id,
      );
      if (
        !resource?.available ||
        resource.binding.kind !== 'artifact' ||
        resource.binding.resource_id !== entry.scope.resource_id ||
        resource.binding.revision !== entry.scope.binding_revision
      )
        entry.revoke();
    }
  }
  const unsubscribe = owner.subscribe(sync);
  return {
    hasRetained() {
      sync();
      return [...entries.values()].some((entry) => entry.retained());
    },
    get(conversation: string, resource: ResourceView) {
      sync();
      if (disposed || !identity) throw new Error('authentication_required');
      const scope = {
        conversation_id: conversation,
        resource_id: resource.binding.resource_id,
        binding_id: resource.binding.binding_id,
        binding_revision: resource.binding.revision,
      };
      const key = JSON.stringify(scope);
      const prior = entries.get(key);
      if (prior) {
        prior.guard();
        return prior;
      }
      const candidate = make(scope);
      candidate.guard();
      if (entries.size >= 8) {
        const clean = [...entries].find(([, value]) => !value.retained());
        if (!clean) {
          candidate.revoke(true);
          throw new Error('design_session_limit');
        }
        clean[1].revoke(true);
        entries.delete(clean[0]);
      }
      entries.set(key, candidate);
      return candidate;
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      unsubscribe();
      entries.forEach((entry) => entry.revoke(true));
      entries.clear();
    },
  };
}
export type ArtifactDesignSession = ReturnType<
  ReturnType<typeof createArtifactDesignSessions>['get']
>;
