import { useMemo, useState, type ReactNode } from 'react';
import {
  Film,
  Image as ImageGlyph,
  LayoutTemplate,
  MoreHorizontal,
  MousePointerClick,
  Music,
  Palette,
  Search,
} from 'lucide-react';
import { humanizeToken } from '../../ui/format';
import { Button, Input, Menu, type MenuAction } from '../../ui/primitives';
import type {
  DesignControlItem,
  DesignControlsProps,
  DesignSection,
} from './ArtifactDesignControls';
import { useDesignCatalog } from './artifact-design-catalog';
import { useThumbnails } from './ArtifactLogoPicker';

type Shelf = Extract<
  DesignSection,
  'blocks' | 'assets' | 'presets' | 'interactions'
>;

const SHELVES: { id: Shelf; label: string }[] = [
  { id: 'blocks', label: 'Blocks' },
  { id: 'assets', label: 'Your images' },
  { id: 'presets', label: 'Styles' },
  { id: 'interactions', label: 'Interactions' },
];
const ASSET_WORDS: Record<string, string> = {
  image: 'Image',
  video: 'Video',
  audio: 'Sound',
};

export type DesignLibraryProps = {
  /** Blocks exist only for decks and web pages. */
  blocks: boolean;
  load: DesignControlsProps['load'];
  thumbnail: DesignControlsProps['thumbnail'];
  pageId: string;
  resourceRevision: string;
  busy: boolean;
  onInsertBlock: (id: string) => void;
  onInsertAsset: (id: string) => void;
  onRemoveAsset: (id: string) => void;
  onForgetAsset: (id: string) => void;
  onApplyPreset: (id: string) => void;
  /** Saved styles can be replaced or deleted when presets are editable. */
  presetActions?: (item: DesignControlItem) => MenuAction[];
  /** Adding an image (shown with Your images). */
  uploader: ReactNode;
  /** Saving the brand as a style (shown with Styles). */
  presetForm: ReactNode;
};

/**
 * The Design library (B247): one search over blocks, the design's images,
 * saved styles and interactions, shown as a grid of tiles. A tile's click
 * inserts or applies it; more actions sit behind its ⋯.
 */
export default function ArtifactDesignLibrary(props: DesignLibraryProps) {
  const { load, pageId, resourceRevision, busy } = props;
  const [shelf, setShelf] = useState<Shelf | 'all'>('all');
  const [query, setQuery] = useState('');
  const catalogs = {
    blocks: useDesignCatalog(
      load,
      pageId,
      'blocks',
      props.blocks,
      resourceRevision,
    ),
    assets: useDesignCatalog(load, pageId, 'assets', true, resourceRevision),
    presets: useDesignCatalog(load, pageId, 'presets', true, resourceRevision),
    interactions: useDesignCatalog(
      load,
      pageId,
      'interactions',
      true,
      resourceRevision,
    ),
  };
  const shelves = SHELVES.filter(
    (item) => item.id !== 'blocks' || props.blocks,
  );
  const images = useMemo(
    () => (catalogs.assets.items ?? []).filter((item) => item.kind === 'image'),
    [catalogs.assets.items],
  );
  const urls = useThumbnails(images, props.thumbnail);
  const terms = query.toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const shown = shelves
    .filter((item) => shelf === 'all' || shelf === item.id)
    .flatMap((item) =>
      (catalogs[item.id].items ?? []).map((entry) => ({
        shelf: item.id,
        entry,
      })),
    )
    .filter(({ entry }) => {
      const words =
        `${entry.label} ${entry.kind} ${entry.detail}`.toLocaleLowerCase();
      return terms.every((term) => words.includes(term));
    });
  const reading = shelves.some(
    (item) =>
      (shelf === 'all' || shelf === item.id) &&
      catalogs[item.id].items === null &&
      !catalogs[item.id].failed,
  );
  const failed = shelves.filter(
    (item) =>
      (shelf === 'all' || shelf === item.id) && catalogs[item.id].failed,
  );

  function tile(kind: Shelf, item: DesignControlItem) {
    const preview =
      kind === 'blocks' ? (
        <LayoutTemplate size={22} aria-hidden />
      ) : kind === 'assets' ? (
        urls[item.id] ? (
          <img src={urls[item.id]} alt="" draggable={false} />
        ) : item.kind === 'video' ? (
          <Film size={22} aria-hidden />
        ) : item.kind === 'audio' ? (
          <Music size={22} aria-hidden />
        ) : (
          <ImageGlyph size={22} aria-hidden />
        )
      ) : kind === 'presets' ? (
        <Palette size={22} aria-hidden />
      ) : (
        <MousePointerClick size={22} aria-hidden />
      );
    const name =
      kind === 'interactions' ? humanizeToken(item.label) : item.label;
    const detail =
      kind === 'assets'
        ? (ASSET_WORDS[item.kind] ?? humanizeToken(item.kind))
        : kind === 'presets'
          ? 'Saved style'
          : item.detail;
    const body = (
      <>
        <span className="design-tile-preview">{preview}</span>
        <span className="design-tile-name">{name}</span>
        {detail && <span className="design-tile-detail">{detail}</span>}
      </>
    );
    const action =
      kind === 'blocks'
        ? {
            label: `Insert ${item.label}`,
            run: () => props.onInsertBlock(item.id),
          }
        : kind === 'assets'
          ? {
              label: `Insert ${item.label}`,
              run: () => props.onInsertAsset(item.id),
            }
          : kind === 'presets'
            ? {
                label: `Apply ${item.label}`,
                run: () => props.onApplyPreset(item.id),
              }
            : null;
    const more: MenuAction[] =
      kind === 'assets'
        ? [
            {
              label: `Remove ${item.label} from page`,
              disabled: busy,
              onSelect: () => props.onRemoveAsset(item.id),
            },
            {
              label: `Remove ${item.label} from list`,
              disabled: busy,
              onSelect: () => props.onForgetAsset(item.id),
            },
          ]
        : kind === 'presets'
          ? (props.presetActions?.(item) ?? [])
          : [];
    return (
      <li key={`${kind}:${item.id}`} className="design-tile">
        {action ? (
          <button
            type="button"
            className="design-tile-main"
            aria-label={action.label}
            disabled={busy || !item.available}
            onClick={action.run}
          >
            {body}
          </button>
        ) : (
          <span className="design-tile-main">{body}</span>
        )}
        {more.length > 0 && (
          <Menu
            label={`More actions for ${item.label}`}
            iconOnly
            variant="ghost"
            className="design-tile-more"
            actions={more}
          >
            <MoreHorizontal size={15} aria-hidden />
          </Menu>
        )}
      </li>
    );
  }

  return (
    <div className="design-library">
      <label className="design-library-search">
        <Search size={14} aria-hidden />
        <Input
          type="search"
          aria-label="Search the library"
          placeholder="Search blocks, images and styles"
          maxLength={128}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      <div className="design-chips" role="group" aria-label="Show">
        {[{ id: 'all' as const, label: 'All' }, ...shelves].map((item) => (
          <button
            key={item.id}
            type="button"
            className="design-chip"
            aria-pressed={shelf === item.id}
            onClick={() => setShelf(item.id)}
          >
            {item.label}
            {item.id === 'assets' && catalogs.assets.items?.length ? (
              <small aria-hidden>{catalogs.assets.items.length}</small>
            ) : null}
          </button>
        ))}
      </div>
      {shelf === 'assets' && props.uploader}
      {shelf === 'presets' && props.presetForm}
      {shown.length > 0 && (
        <ul className="design-tiles" aria-label="Library items">
          {shown.map(({ shelf: kind, entry }) => tile(kind, entry))}
        </ul>
      )}
      {!shown.length && !reading && !failed.length && (
        <p className="muted design-library-empty">
          {terms.length
            ? `Nothing matches “${query.trim()}”.`
            : shelf === 'assets'
              ? 'No images in this design yet.'
              : 'Nothing here yet.'}
        </p>
      )}
      {reading && (
        <p className="muted design-library-empty" role="status">
          Reading the library…
        </p>
      )}
      {failed.length > 0 && (
        <p className="muted design-library-empty">
          Part of the library couldn&apos;t be read.{' '}
          <Button
            variant="ghost"
            onClick={() => failed.forEach((item) => catalogs[item.id].retry())}
          >
            Try again
          </Button>
        </p>
      )}
    </div>
  );
}
