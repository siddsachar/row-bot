import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { FolderGit2, LayoutTemplate, Palette, Search } from 'lucide-react';
import type { ClientController } from '../../api/controller';
import { clientError } from '../../api/errors';
import type { OutputView } from '../../api/types';
import { useRuntime } from '../../runtime';
import { absoluteTime, parseTimestamp } from '../../ui/format';
import {
  Button,
  EmptyState,
  ErrorState,
  Input,
  ListSkeleton,
  Segmented,
} from '../../ui/primitives';
import { requestPagePanel } from '../pages/open-page';
import { shortTime } from './conversation-groups';
import { Thumb } from './LibraryMedia';
import {
  OUTPUT_ICONS,
  OUTPUT_KIND_WORDS,
  outputTitle,
  versionWords,
} from './outputs';

/** What Files lists: what Row-Bot made, by kind; then the files the person added, and the working files. */
export type FileKind =
  'all' | 'design' | 'page' | 'media' | 'folder' | 'uploads' | 'working';

type MadeFile = {
  id: string;
  kind: 'design' | 'folder' | 'page' | OutputView['kind'];
  title: string;
  conversationId: string;
  conversationTitle: string;
  at?: string;
  pageId?: string;
  resourceRef?: string;
  media?: OutputView;
};

// The outputs list's own chips (W3) close the row, as in Library › Media.
const KINDS: { value: FileKind; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'design', label: 'Designs' },
  { value: 'page', label: 'Pages' },
  { value: 'media', label: 'Media' },
  { value: 'folder', label: 'Folders' },
  { value: 'uploads', label: 'Uploads' },
  { value: 'working', label: 'Working files' },
];
const WHAT: Record<MadeFile['kind'], string> = {
  design: 'Design',
  folder: 'Code folder',
  page: 'Page',
  ...OUTPUT_KIND_WORDS,
};
const ICONS = {
  design: Palette,
  folder: FolderGit2,
  page: LayoutTemplate,
  ...OUTPUT_ICONS,
} as const;

/** The newest chats with a design or code folder whose names are read. */
const RESOURCE_CHATS = 30;

const time = (value?: string) => parseTimestamp(value)?.getTime() ?? 0;
const matchesKind = (file: MadeFile, kind: FileKind) =>
  kind === 'all' ||
  file.kind === kind ||
  (kind === 'media' && Boolean(file.media));

const fromOutput = (item: OutputView): MadeFile => ({
  id: `media:${item.conversation_id}:${item.id}`,
  kind: item.kind,
  title: outputTitle(item),
  conversationId: item.conversation_id,
  conversationTitle: item.conversation_title ?? '',
  at: item.updated_at,
  media: item,
});

/**
 * The designs and code folders bound to the newest chats. Those chats are
 * read from their own listings, not the whole library; each one's folder
 * and design names take a read of its own, so they come after the rest.
 */
async function loadResources(
  controller: ClientController,
  signal: AbortSignal,
): Promise<MadeFile[]> {
  const [designs, folders] = await Promise.all([
    controller.conversationsOfGroup('artifact', RESOURCE_CHATS, signal),
    controller.conversationsOfGroup('workspace', RESOURCE_CHATS, signal),
  ]);
  const conversations = [
    ...new Map(
      [...(designs?.items ?? []), ...(folders?.items ?? [])].map((row) => [
        row.id,
        row,
      ]),
    ).values(),
  ];
  // A helper's thread shares its chat's folder; the chat lists it.
  const bound = conversations
    .filter(
      (row) =>
        !row.parent_conversation_id &&
        row.resource_bindings?.some(
          (binding) =>
            binding.kind === 'artifact' || binding.kind === 'workspace',
        ),
    )
    .sort((a, b) => time(b.updated_at) - time(a.updated_at))
    .slice(0, RESOURCE_CHATS);
  const workspaces = await Promise.allSettled(
    bound.map((row) => controller.workspaceFor(row.id, signal)),
  );
  const seen = new Set<string>();
  const resources: MadeFile[] = [];
  workspaces.forEach((result, index) => {
    if (result.status !== 'fulfilled' || !result.value) return;
    const row = bound[index];
    for (const resource of result.value.resources) {
      const kind = resource.binding.kind;
      if (
        (kind !== 'artifact' && kind !== 'workspace') ||
        !resource.available ||
        seen.has(resource.resource_ref)
      )
        continue;
      seen.add(resource.resource_ref);
      resources.push({
        id: `resource:${resource.resource_ref}`,
        kind: kind === 'artifact' ? 'design' : 'folder',
        title: resource.title,
        conversationId: row.id,
        conversationTitle: row.title,
        at: row.updated_at,
        resourceRef: resource.resource_ref,
      });
    }
  });
  return resources;
}

/** Every page, and what the chats made: the same outputs list as Library › Media. */
async function loadPagesAndMedia(
  controller: ClientController,
  signal: AbortSignal,
): Promise<MadeFile[]> {
  const [pages, media] = await Promise.all([
    controller.pages(undefined, signal),
    controller.outputs({ show: 'made' }, signal),
  ]);
  return [
    ...(pages?.items ?? []).map((page) => ({
      id: `page:${page.page_id}`,
      kind: 'page' as const,
      title: page.title,
      conversationId: page.conversation_id,
      conversationTitle: page.conversation_title ?? '',
      at: page.updated_at,
      pageId: page.page_id,
    })),
    ...(media?.items ?? []).map(fromOutput),
  ];
}

/**
 * Chats › Files on the phone (D10 A): what Row-Bot made, with search and
 * labelled chips, and the files the person added and the working files on
 * request (W3). Each opens in its chat: a design or code folder in its
 * panel, a page beside it, a picture or recording where it was shared.
 */
export default function LibraryFiles({
  initialKind = 'all',
}: {
  initialKind?: FileKind;
}) {
  const { controller } = useRuntime();
  // Each part shows as it arrives: pages and media first, then the designs
  // and folders, which take a read per chat.
  const [files, setFiles] = useState<MadeFile[] | null>(null);
  const [resources, setResources] = useState<MadeFile[] | null>(null);
  // Uploads or Working files, read when their chip is chosen.
  const [others, setOthers] = useState<{
    kind: FileKind;
    files: MadeFile[];
  } | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [kind, setKind] = useState<FileKind>(initialKind);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    setError('');
    const failed = (cause: unknown) => {
      if (!abort.signal.aborted) setError(clientError(cause).message);
    };
    loadPagesAndMedia(controller, abort.signal).then((result) => {
      if (!abort.signal.aborted) setFiles(result);
    }, failed);
    loadResources(controller, abort.signal).then((result) => {
      if (!abort.signal.aborted) setResources(result);
    }, failed);
    return () => abort.abort();
  }, [controller, attempt]);

  const other = kind === 'uploads' || kind === 'working';
  useEffect(() => {
    if (kind !== 'uploads' && kind !== 'working') return;
    const abort = new AbortController();
    controller.outputs({ show: kind }, abort.signal).then(
      (page) => {
        if (!abort.signal.aborted)
          setOthers({ kind, files: (page?.items ?? []).map(fromOutput) });
      },
      (cause) => {
        if (!abort.signal.aborted) setError(clientError(cause).message);
      },
    );
    return () => abort.abort();
  }, [controller, kind, attempt]);

  const words = query.trim().toLowerCase();
  const listed = other ? (others?.kind === kind ? others.files : null) : files;
  const shown = [...(listed ?? []), ...(other ? [] : (resources ?? []))]
    .sort((a, b) => time(b.at) - time(a.at))
    .filter(
      (file) =>
        (other || matchesKind(file, kind)) &&
        (!words ||
          `${file.title} ${file.conversationTitle}`
            .toLowerCase()
            .includes(words)),
    );
  const resourcesPending =
    !other && !resources && kind !== 'page' && kind !== 'media';
  const now = new Date();
  return (
    <section className="library-files" aria-label="Files">
      <div className="library-toolbar">
        <label className="library-search">
          <Search size={16} aria-hidden />
          <Input
            type="search"
            aria-label="Search what Row-Bot made"
            placeholder="Search what Row-Bot made"
            value={query}
            maxLength={200}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <Segmented
          size="sm"
          className="phone-chips"
          label="Kind of file"
          value={kind}
          onChange={setKind}
          options={KINDS}
        />
      </div>
      {error ? (
        <ErrorState
          title="Files didn't load"
          action={
            <Button onClick={() => setAttempt((n) => n + 1)}>Try again</Button>
          }
        >
          {error}
        </ErrorState>
      ) : !listed || (shown.length === 0 && resourcesPending) ? (
        <ListSkeleton label="Loading files" rows={9} />
      ) : shown.length === 0 ? (
        words || kind !== 'all' ? (
          <EmptyState title="Nothing matches">
            Try other words, or another kind.
          </EmptyState>
        ) : (
          <EmptyState title="Nothing made yet">
            Designs, code folders, pages, films, pictures and documents Row-Bot
            makes in your chats show here.
          </EmptyState>
        )
      ) : (
        <ul className="phone-chat-list-rows" aria-label="Files">
          {shown.map((file) => {
            const Icon = ICONS[file.kind];
            const date = shortTime(file.at, now);
            return (
              <li key={file.id} className="phone-row">
                <Link
                  className="phone-row-main"
                  to={`/conversations/${encodeURIComponent(file.conversationId)}`}
                  state={
                    file.resourceRef
                      ? { openResource: file.resourceRef }
                      : undefined
                  }
                  onClick={() => {
                    if (file.pageId)
                      requestPagePanel({
                        conversationId: file.conversationId,
                        pageId: file.pageId,
                        title: file.title,
                      });
                  }}
                >
                  <span className="phone-row-icon" aria-hidden>
                    {file.media?.kind === 'image' ? (
                      <Thumb item={file.media} />
                    ) : (
                      <Icon size={18} />
                    )}
                  </span>
                  <span className="phone-row-text">
                    <span className="phone-row-title">{file.title}</span>
                    <span className="phone-row-meta">
                      {[
                        WHAT[file.kind],
                        file.media?.role === 'draft' ? 'Draft' : '',
                        file.media ? versionWords(file.media) : '',
                        `in ${file.conversationTitle || 'a chat'}`,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                    </span>
                  </span>
                  {date && (
                    <time
                      className="phone-row-date"
                      dateTime={file.at}
                      title={absoluteTime(file.at)}
                    >
                      {date}
                    </time>
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {!error && listed && shown.length > 0 && resourcesPending && (
        <ListSkeleton label="Loading designs and folders" rows={2} />
      )}
    </section>
  );
}
