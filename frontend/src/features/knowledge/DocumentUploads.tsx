import { useId, useSyncExternalStore } from 'react';
import { Button, ErrorState } from '../../ui/primitives';

export type DocumentUploadFile = { name: string; size_bytes: number };
export type DocumentUploadReview = {
  review_id: string;
  action: 'document.upload';
  files: DocumentUploadFile[];
  file_count: number;
  total_bytes: number;
  intent_digest: string;
  processing: 'paused';
  provider_work: false;
};
export type DocumentUploadCommand = {
  command_id: string;
  type: 'document.upload';
  payload: { files: DocumentUploadFile[]; review_id: string };
};
export type DocumentUploadReceipt = {
  command_id: string;
  status: 'completed' | 'partial' | 'rejected';
  code?: string;
  batch_id?: string;
  processing?: 'paused';
  files?: (DocumentUploadFile & {
    id: string;
    status: 'queued' | 'skipped_duplicate';
  })[];
};
export type DocumentUploadTransport = {
  review(files: DocumentUploadFile[]): Promise<DocumentUploadReview>;
  upload(
    command: DocumentUploadCommand,
    files: readonly File[],
  ): Promise<DocumentUploadReceipt>;
  receipt(commandId: string): Promise<DocumentUploadReceipt>;
};
type State = {
  files: readonly File[];
  review: DocumentUploadReview | null;
  receipt: DocumentUploadReceipt | null;
  busy: boolean;
  pending: boolean;
  revoked: boolean;
  error: string;
};
const maximum = 256 * 1024 * 1024;
function metadata(files: readonly File[]): DocumentUploadFile[] {
  if (
    !files.length ||
    files.length > 50 ||
    files.some(
      (file) =>
        !file.name ||
        file.name.length > 256 ||
        /[\\/]/.test(file.name) ||
        Array.from(file.name).some(
          (character) => character.charCodeAt(0) < 32,
        ) ||
        !/\.(pdf|docx?|txt|md|html?|epub)$/i.test(file.name) ||
        !Number.isSafeInteger(file.size) ||
        file.size <= 0 ||
        file.size > maximum,
    )
  )
    throw new Error('invalid_document_upload');
  return files.map((file) => ({ name: file.name, size_bytes: file.size }));
}

/** Root retains this session and original File references until auth loss. */
export function createDocumentUploadsSession(
  transport: DocumentUploadTransport,
  guard: () => void,
  newId: () => string = () => crypto.randomUUID(),
) {
  let state: State = {
    files: [],
    review: null,
    receipt: null,
    busy: false,
    pending: false,
    revoked: false,
    error: '',
  };
  type Attempt = {
    command: DocumentUploadCommand;
    review: DocumentUploadReview;
    files: readonly File[];
  };
  // Retain the current exact original; settled history lives in server receipts.
  // Never keep unreachable historical payloads or File references here.
  let attempt: Attempt | null = null;
  let epoch = 0;
  let operation: Promise<unknown> | null = null;
  const listeners = new Set<() => void>();
  const emit = (patch: Partial<State>) => {
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  function authority(ticket = epoch) {
    if (ticket !== epoch || state.revoked)
      throw new Error('authentication_required');
    guard();
  }
  async function read<T>(callback: () => Promise<T>) {
    const ticket = epoch;
    authority(ticket);
    const result = await callback();
    authority(ticket);
    return result;
  }
  async function run<T>(callback: () => Promise<T>, recovery = false) {
    authority();
    if (operation || (state.pending && !recovery))
      throw new Error('document_upload_pending');
    const ticket = epoch;
    emit({ busy: true, error: '' });
    const promise = Promise.resolve().then(callback);
    operation = promise;
    try {
      return await promise;
    } catch (error) {
      if (ticket === epoch)
        emit({
          error:
            'The upload could not be confirmed. The selected files and original command are retained; refresh that command before taking another action.',
        });
      throw error;
    } finally {
      if (operation === promise) operation = null;
      if (ticket === epoch) emit({ busy: false });
    }
  }
  function accept(receipt: DocumentUploadReceipt) {
    if (
      !attempt ||
      receipt.command_id !== attempt.command.command_id ||
      !['completed', 'partial', 'rejected'].includes(receipt.status)
    )
      throw new Error('document_upload_receipt_mismatch');
    if (receipt.status === 'completed') {
      if (
        receipt.batch_id !==
          `client_${attempt.command.command_id.replaceAll('-', '')}` ||
        receipt.processing !== 'paused' ||
        !receipt.files ||
        receipt.files.length !== attempt.command.payload.files.length ||
        receipt.files.some(
          (file, index) =>
            !/^upload_[a-f0-9]{32}$/.test(file.id) ||
            !['queued', 'skipped_duplicate'].includes(file.status) ||
            file.name !== attempt!.command.payload.files[index].name ||
            file.size_bytes !==
              attempt!.command.payload.files[index].size_bytes,
        ) ||
        new Set(receipt.files.map((file) => file.id)).size !==
          receipt.files.length
      )
        throw new Error('document_upload_receipt_mismatch');
    }
    emit({
      receipt,
      pending: receipt.status === 'partial',
      review: receipt.status === 'partial' ? state.review : null,
    });
  }
  return {
    getSnapshot: () => state,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    isPending: () => state.busy || state.pending,
    select(files: readonly File[]) {
      authority();
      if (operation || state.pending)
        throw new Error('document_upload_pending');
      try {
        metadata(files);
        emit({
          files: Object.freeze([...files]),
          review: null,
          receipt: null,
          error: '',
        });
      } catch (error) {
        emit({
          error:
            'Choose up to 50 supported nonempty files, each no larger than 256 MiB. Existing selections are retained.',
        });
        throw error;
      }
    },
    review: () =>
      run(async () => {
        const files = metadata(state.files);
        const review = await read(() => transport.review(files));
        if (
          review.action !== 'document.upload' ||
          !review.review_id ||
          !review.intent_digest ||
          review.processing !== 'paused' ||
          review.provider_work !== false ||
          review.file_count !== files.length ||
          review.total_bytes !==
            files.reduce((total, file) => total + file.size_bytes, 0) ||
          JSON.stringify(review.files) !== JSON.stringify(files)
        )
          throw new Error('document_upload_review_mismatch');
        emit({ review: structuredClone(review), receipt: null });
      }),
    confirm: () =>
      run(async () => {
        if (
          !state.review ||
          JSON.stringify(metadata(state.files)) !==
            JSON.stringify(state.review.files)
        )
          throw new Error('document_upload_review_required');
        const command: DocumentUploadCommand = {
          command_id: newId(),
          type: 'document.upload',
          payload: {
            files: structuredClone(state.review.files),
            review_id: state.review.review_id,
          },
        };
        attempt = {
          command,
          review: structuredClone(state.review),
          files: state.files,
        };
        emit({ pending: true });
        accept(
          await read(() =>
            transport.upload(structuredClone(command), attempt!.files),
          ),
        );
      }),
    async start() {
      await this.review();
      await this.confirm();
    },
    refresh: () =>
      run(async () => {
        if (!attempt) throw new Error('document_upload_command_required');
        accept(
          await read(() => transport.receipt(attempt!.command.command_id)),
        );
      }, true),
    purge() {
      epoch += 1;
      attempt = null;
      emit({
        files: [],
        review: null,
        receipt: null,
        busy: false,
        pending: false,
        revoked: true,
        error: '',
      });
    },
  };
}
export type DocumentUploadsSession = ReturnType<
  typeof createDocumentUploadsSession
>;

export function DocumentUploads({
  session,
}: {
  session: DocumentUploadsSession;
}) {
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const inputId = useId();
  const disabled = state.busy || state.pending || state.revoked;
  const invoke = (callback: () => Promise<unknown>) => {
    void callback().catch(() => undefined);
  };
  return (
    <section aria-label="Document uploads">
      <h3>Upload documents</h3>
      <p>
        PDF, DOC, DOCX, TXT, MD, HTML, HTM and EPUB. Up to 256 MiB per file.
      </p>
      <p>
        Files are staged in a paused batch. Processing and provider use require
        a separate action.
      </p>
      <label htmlFor={inputId}>Choose documents</label>
      <input
        id={inputId}
        type="file"
        multiple
        accept=".pdf,.doc,.docx,.txt,.md,.html,.htm,.epub"
        disabled={disabled}
        onChange={(event) => {
          try {
            session.select(Array.from(event.target.files ?? []));
          } catch {
            /* Session exposes the recoverable error. */
          }
        }}
      />
      {state.files.map((file, index) => (
        <p key={`${index}:${file.name}`}>
          {file.name} · {file.size} bytes
        </p>
      ))}
      {state.error && (
        <ErrorState title="Upload needs attention">{state.error}</ErrorState>
      )}
      <Button
        disabled={disabled || !state.files.length}
        onClick={() => invoke(() => session.start())}
      >
        Upload selected
      </Button>
      {state.pending && (
        <Button
          disabled={state.busy || state.revoked}
          onClick={() => invoke(session.refresh)}
        >
          Refresh original upload
        </Button>
      )}
      {state.receipt && (
        <p role="status">
          {state.receipt.status === 'completed'
            ? 'Upload staged. The batch is paused.'
            : state.receipt.status === 'partial'
              ? 'Upload outcome is uncertain. Retained bytes may exist; do not repeat the upload.'
              : 'Upload was rejected.'}
        </p>
      )}
      {state.receipt?.files?.map((file) => (
        <p key={file.id}>
          {file.name} ·{' '}
          {file.status === 'skipped_duplicate'
            ? 'Duplicate detected; retained copy'
            : 'Staged for processing'}
        </p>
      ))}
    </section>
  );
}
