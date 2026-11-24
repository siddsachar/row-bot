import { useEffect, useRef, useState, type DragEvent } from 'react';
import { FileText, FileUp, Trash2, Upload, X } from 'lucide-react';
import {
  Button,
  EntityList,
  EntityRow,
  IconButton,
} from '../../../ui/primitives';
import { When } from '../../../ui/When';
import { absoluteTime } from '../../../ui/format';
import { useNotify, useOverlay } from '../../../ui/overlays';
import { ApiError, request, upload } from '../../api';
import { useDirectory } from '../../directory';
import { Loaded, useLoad } from '../../load';
import { Notice } from '../../page';
import type { BrainDocument, Space } from '../../types';
import { count, problem } from '../../words';
import { Reason } from './parts';
import {
  accepted,
  ACCEPTED,
  ACCEPTED_WORDS,
  DOCUMENTS_POLL_MS,
  documentStatus,
  megabytes,
  size,
} from './words';

type Refusal = { reason: string };

const ONLY =
  'only text, Markdown and Word files (.txt, .md, .docx) can be added.';
const tooLarge = (name: string, bytes: number) =>
  `${name} is larger than ${megabytes(bytes)}, the most the server takes.`;

/** Why the server didn't take a file, in plain words. */
function refusal(file: File, error: unknown, maxBytes: number): string {
  if (error instanceof ApiError) {
    if (error.code === 'unsupported_type')
      return `${file.name} can't be added: ${ONLY}`;
    const max = error.body.max_bytes;
    if (error.code === 'file_too_large' || error.status === 413)
      return tooLarge(file.name, typeof max === 'number' ? max : maxBytes);
    if (error.code === 'too_many_uploads')
      return `The server is busy with other uploads. Try ${file.name} again in a moment.`;
  }
  return `${file.name}: ${problem(error)}`;
}

/** The refusal the server would give, found before sending the file. */
function precheck(file: File, maxBytes: number): string | null {
  if (!accepted(file.name)) return `${file.name} can't be added: ${ONLY}`;
  if (file.size > maxBytes) return tooLarge(file.name, maxBytes);
  return null;
}

const titleOf = (item: BrainDocument) => item.title || item.file_name;

const hasFiles = (event: DragEvent) =>
  Array.from(event.dataTransfer.types).includes('Files');

/**
 * A space's documents: an upload (a button or a drop), each document's
 * status in words, and replace and delete. While any document is being read
 * the list reads itself again every few seconds, only while the page is seen.
 */
export function Documents({
  space,
  maxBytes,
}: {
  space: Space;
  maxBytes: number;
}) {
  const { nameOf } = useDirectory();
  const overlay = useOverlay();
  const notify = useNotify();
  const path = `/api/brain/spaces/${space.id}/documents`;
  const documents = useLoad(
    () =>
      request<{ documents: BrainDocument[] }>(path).then(
        (answer) => answer.documents,
      ),
    path,
  );
  const update = documents.update;
  const [uploads, setUploads] = useState<{ key: number; name: string }[]>([]);
  const [refused, setRefused] = useState<Refusal[]>([]);
  const [dragging, setDragging] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const replacer = useRef<HTMLInputElement>(null);
  const replacing = useRef<BrainDocument | null>(null);
  const uploaded = useRef(0);

  const working =
    documents.status === 'ready' &&
    documents.data.some((item) => documentStatus(item).working);
  useEffect(() => {
    if (!working) return;
    let reading = false;
    const timer = setInterval(() => {
      // Polite: not while the page is hidden, never two reads at once.
      if (document.hidden || reading) return;
      reading = true;
      request<{ documents: BrainDocument[] }>(path)
        .then(
          (answer) => update(() => answer.documents),
          () => undefined,
        )
        .finally(() => {
          reading = false;
        });
    }, DOCUMENTS_POLL_MS);
    return () => clearInterval(timer);
  }, [working, path, update]);

  async function add(files: File[]) {
    const refusals: Refusal[] = [];
    const sending: { key: number; file: File }[] = [];
    for (const file of files) {
      const reason = precheck(file, maxBytes);
      if (reason) refusals.push({ reason });
      else sending.push({ key: ++uploaded.current, file });
    }
    setRefused(refusals);
    setUploads((current) => [
      ...current,
      ...sending.map(({ key, file }) => ({ key, name: file.name })),
    ]);
    // One at a time: the server reads only a few uploads at once.
    for (const { key, file } of sending) {
      try {
        const added = await upload<BrainDocument>(path, 'POST', file);
        update((list) => [added, ...list]);
      } catch (error) {
        setRefused((current) => [
          ...current,
          { reason: refusal(file, error, maxBytes) },
        ]);
      } finally {
        setUploads((current) => current.filter((item) => item.key !== key));
      }
    }
  }

  function replace(target: BrainDocument, file: File) {
    const early = precheck(file, maxBytes);
    if (early) {
      setRefused([{ reason: early }]);
      return;
    }
    overlay.open({
      kind: 'alert',
      title: `Replace ${titleOf(target)} with ${file.name}?`,
      description:
        'Its old text stops being found at once. Row-Bot reads the new file, and its knowledge, again.',
      confirmLabel: 'Replace',
      onConfirm: () => {
        setRefused([]);
        const at = `/api/brain/documents/${target.id}`;
        upload<BrainDocument>(at, 'PUT', file).then(
          (replaced) => {
            update((list) =>
              list.map((item) => (item.id === replaced.id ? replaced : item)),
            );
            notify(`${titleOf(target)} is replaced`);
          },
          (error: unknown) =>
            setRefused([{ reason: refusal(file, error, maxBytes) }]),
        );
      },
    });
  }

  function remove(target: BrainDocument) {
    overlay.open({
      kind: 'alert',
      title: `Delete ${titleOf(target)}?`,
      description:
        'Its text and its knowledge leave the company brain for good, and facts written from it lose their source. This can’t be undone.',
      confirmLabel: 'Delete',
      onConfirm: () => {
        request(`/api/brain/documents/${target.id}`, {
          method: 'DELETE',
        }).then(
          () => {
            update((list) => list.filter((item) => item.id !== target.id));
            notify(`${titleOf(target)} is deleted`);
          },
          (error: unknown) => notify(problem(error), 'danger'),
        );
      },
    });
  }

  const ready = documents.status === 'ready';
  const empty = ready && documents.data.length === 0 && uploads.length === 0;
  return (
    <section
      className="console-section"
      aria-label="Documents"
      onDragEnter={(event) => {
        if (!ready || !hasFiles(event)) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragOver={(event) => {
        if (!ready || !hasFiles(event)) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'copy';
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node))
          setDragging(false);
      }}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        if (ready) void add(Array.from(event.dataTransfer.files));
      }}
    >
      {ready && (
        <div
          className="console-dropzone"
          data-dragging={dragging ? 'true' : undefined}
        >
          <FileUp size={20} aria-hidden />
          <div className="console-dropzone-text">
            <strong>
              {empty
                ? 'No documents yet. Upload a file, or drop it here.'
                : 'Drop files here to add them to this space.'}
            </strong>
            <small>
              {ACCEPTED_WORDS}, up to {megabytes(maxBytes)} each.
            </small>
          </div>
          <Button variant="primary" onClick={() => picker.current?.click()}>
            <Upload size={16} aria-hidden />
            Upload
          </Button>
          <input
            ref={picker}
            type="file"
            multiple
            accept={ACCEPTED.join(',')}
            className="visually-hidden"
            tabIndex={-1}
            aria-label="Choose files to upload"
            onChange={(event) => {
              void add(Array.from(event.target.files ?? []));
              event.target.value = '';
            }}
          />
          <input
            ref={replacer}
            type="file"
            accept={ACCEPTED.join(',')}
            className="visually-hidden"
            tabIndex={-1}
            aria-label="Choose the new file"
            onChange={(event) => {
              const file = event.target.files?.[0];
              const target = replacing.current;
              event.target.value = '';
              if (file && target) replace(target, file);
            }}
          />
        </div>
      )}
      {refused.length > 0 && (
        <Notice
          tone="warning"
          title={
            refused.length === 1
              ? 'A file wasn’t added'
              : `${count(refused.length)} files weren’t added`
          }
          action={
            <IconButton
              size="sm"
              label="Dismiss"
              onClick={() => setRefused([])}
            >
              <X size={16} aria-hidden />
            </IconButton>
          }
        >
          <ul>
            {refused.map((item, index) => (
              <li key={index}>{item.reason}</li>
            ))}
          </ul>
        </Notice>
      )}
      <Loaded load={documents} label="Loading documents" what="the documents">
        {(list) =>
          list.length + uploads.length === 0 ? null : (
            <EntityList label="Documents">
              {uploads.map((item) => (
                <EntityRow
                  key={`upload-${item.key}`}
                  title={item.name}
                  icon={<FileUp size={18} />}
                  status={{ tone: 'info', label: 'Uploading' }}
                />
              ))}
              {list.map((item) => {
                const status = documentStatus(item);
                return (
                  <EntityRow
                    key={item.id}
                    title={titleOf(item)}
                    icon={<FileText size={18} />}
                    status={{ tone: status.tone, label: status.label }}
                    meta={
                      <>
                        {`${item.file_name} · ${size(item.byte_count)} · ${nameOf(item.uploaded_by)} · `}
                        <When value={item.uploaded_at} />
                        {status.reason && (
                          <span className="console-reason">
                            <Reason text={status.reason} />
                            {status.label === 'Waiting' &&
                              ' Row-Bot finds it by its words until then.'}
                          </span>
                        )}
                        {status.graph && (
                          <span className="console-reason">
                            {status.graph.lead && `${status.graph.lead} `}
                            <Reason text={status.graph.reason} />
                          </span>
                        )}
                      </>
                    }
                    details={
                      <dl className="console-details">
                        <div>
                          <dt>File</dt>
                          <dd>{item.file_name}</dd>
                        </div>
                        <div>
                          <dt>Uploaded</dt>
                          <dd>
                            {`${absoluteTime(item.uploaded_at)} by ${nameOf(item.uploaded_by)}`}
                          </dd>
                        </div>
                        <div>
                          <dt>Version</dt>
                          <dd>{item.version}</dd>
                        </div>
                        {item.processed_at && (
                          <div>
                            <dt>Read</dt>
                            <dd>
                              {`${absoluteTime(item.processed_at)}, in ${count(item.chunk_count)} parts`}
                            </dd>
                          </div>
                        )}
                      </dl>
                    }
                    action={
                      <>
                        <IconButton
                          size="sm"
                          label={`Replace ${titleOf(item)}`}
                          onClick={() => {
                            replacing.current = item;
                            replacer.current?.click();
                          }}
                        >
                          <FileUp size={16} aria-hidden />
                        </IconButton>
                        <IconButton
                          size="sm"
                          label={`Delete ${titleOf(item)}`}
                          onClick={() => remove(item)}
                        >
                          <Trash2 size={16} aria-hidden />
                        </IconButton>
                      </>
                    }
                  />
                );
              })}
            </EntityList>
          )
        }
      </Loaded>
    </section>
  );
}
