import { createContext, useContext, useRef, useState } from 'react';
import { FolderCode, Link2, Palette } from 'lucide-react';
import type {
  EventRecord,
  ResourceView,
  TranscriptTraceGroup,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, Input } from '../../ui/primitives';

/**
 * Cards the assistant leaves in a turn (decision 12): a design or code folder
 * it created, with Open · Rename · Undo, and a Connect card for an account or
 * channel the work needs. Each comes from its tool's reviewed specialization,
 * live from `tool.activity` and settled from the turn's traces.
 */
export type TranscriptCard =
  | {
      kind: 'resource';
      resourceKind: 'design' | 'code';
      name: string;
      bindingId: string;
      resourceId: string;
    }
  | {
      kind: 'connect';
      target: string;
      label: string;
      page: 'accounts' | 'channels';
    };

type Specialization = NonNullable<
  TranscriptTraceGroup['items'][number]['specialization']
>;

export function cardOf(value: Specialization | null | undefined) {
  if (!value) return null;
  if (
    value.kind === 'resource_created' &&
    (value.resource_kind === 'design' || value.resource_kind === 'code') &&
    value.binding_id
  )
    return {
      kind: 'resource',
      resourceKind: value.resource_kind,
      name: value.display_name ?? '',
      bindingId: value.binding_id,
      resourceId: value.resource_id ?? '',
    } satisfies TranscriptCard;
  if (
    value.kind === 'setup_needed' &&
    (value.settings_page === 'accounts' || value.settings_page === 'channels')
  )
    return {
      kind: 'connect',
      target: value.setup_target ?? '',
      label: value.display_name ?? '',
      page: value.settings_page,
    } satisfies TranscriptCard;
  return null;
}

export function cardKey(card: TranscriptCard) {
  return card.kind === 'resource'
    ? `resource:${card.bindingId}`
    : `connect:${card.target}`;
}

export function tracedCards(groups: TranscriptTraceGroup[]): TranscriptCard[] {
  const seen = new Set<string>();
  const cards: TranscriptCard[] = [];
  for (const group of groups)
    for (const item of group.items) {
      const card = cardOf(item.specialization);
      if (!card || seen.has(cardKey(card))) continue;
      seen.add(cardKey(card));
      cards.push(card);
    }
  return cards;
}

/** Cards announced live that no settled row shows yet. */
export function liveCards(
  activity: readonly EventRecord[],
  settled: ReadonlySet<string>,
): TranscriptCard[] {
  const seen = new Set<string>();
  const cards: TranscriptCard[] = [];
  for (const record of activity) {
    if (record.event.type !== 'tool.activity') continue;
    const card = cardOf(record.event.payload.specialization);
    if (!card) continue;
    const key = cardKey(card);
    if (settled.has(key) || seen.has(key)) continue;
    seen.add(key);
    cards.push(card);
  }
  return cards;
}

export type CardActions = {
  resource: (bindingId: string) => ResourceView | undefined;
  open: (resource: ResourceView) => void;
  rename: (bindingId: string, name: string) => Promise<void>;
  undo: (bindingId: string) => Promise<void>;
  connect: (page: 'accounts' | 'channels') => void;
};

export const CardActionsContext = createContext<CardActions | null>(null);

const NOUN = { design: 'design', code: 'code folder' } as const;

function ResourceCard({
  card,
  live,
}: {
  card: Extract<TranscriptCard, { kind: 'resource' }>;
  live: boolean;
}) {
  const actions = useContext(CardActionsContext);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const committed = useRef(false);
  const resource = actions?.resource(card.bindingId);
  const title = resource?.title || card.name;
  const noun = NOUN[card.resourceKind];
  const Icon = card.resourceKind === 'design' ? Palette : FolderCode;
  async function run(work: () => Promise<void>) {
    setBusy(true);
    try {
      await work();
      setError('');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy(false);
    }
  }
  function commitRename() {
    if (committed.current) return;
    committed.current = true;
    const next = name.trim();
    setRenaming(false);
    if (!next || next === title || !actions) return;
    void run(() => actions.rename(card.bindingId, next));
  }
  // A live card can arrive before the conversation's resources are read
  // again; a settled one without its binding was undone or removed.
  if (!resource && !live)
    return (
      <div
        className="transcript-card"
        data-kind="resource"
        data-state="removed"
        role="group"
        aria-label={`Removed ${noun} ${card.name}`}
      >
        <Icon className="transcript-card-icon" aria-hidden />
        <div className="transcript-card-text">
          <span>
            Removed {noun} <strong>{card.name}</strong>
          </span>
        </div>
      </div>
    );
  return (
    <div
      className="transcript-card"
      data-kind="resource"
      role="group"
      aria-label={`Created ${noun} ${title}`}
    >
      <Icon className="transcript-card-icon" aria-hidden />
      <div className="transcript-card-text">
        {renaming ? (
          <Input
            autoFocus
            aria-label={`Name of the ${noun}`}
            maxLength={120}
            value={name}
            onChange={(event) => setName(event.target.value)}
            onBlur={commitRename}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                commitRename();
              } else if (event.key === 'Escape') {
                event.preventDefault();
                committed.current = true;
                setRenaming(false);
              }
            }}
          />
        ) : (
          <span>
            Created {noun} <strong>{title}</strong>
          </span>
        )}
      </div>
      <div className="transcript-card-actions">
        <Button
          disabled={busy || !resource?.available}
          onClick={() => resource && actions?.open(resource)}
        >
          Open
        </Button>
        <Button
          variant="ghost"
          disabled={busy || renaming || !resource}
          onClick={() => {
            committed.current = false;
            setName(title);
            setRenaming(true);
          }}
        >
          Rename
        </Button>
        <Button
          variant="ghost"
          disabled={busy || !resource}
          title={
            card.resourceKind === 'design'
              ? 'Delete this design'
              : 'Delete this code folder and its files'
          }
          onClick={() =>
            actions && void run(() => actions.undo(card.bindingId))
          }
        >
          Undo
        </Button>
      </div>
      {error && (
        <p role="alert" className="transcript-card-error">
          {error}
        </p>
      )}
    </div>
  );
}

function ConnectCard({
  card,
}: {
  card: Extract<TranscriptCard, { kind: 'connect' }>;
}) {
  const actions = useContext(CardActionsContext);
  return (
    <div
      className="transcript-card"
      data-kind="connect"
      role="group"
      aria-label={`Connect ${card.label}`}
    >
      <Link2 className="transcript-card-icon" aria-hidden />
      <div className="transcript-card-text">
        <span>
          Row-Bot needs <strong>{card.label}</strong> for this.
        </span>
      </div>
      <div className="transcript-card-actions">
        <Button variant="primary" onClick={() => actions?.connect(card.page)}>
          Connect {card.label}
        </Button>
      </div>
    </div>
  );
}

export function TranscriptCards({
  cards,
  live = false,
}: {
  cards: TranscriptCard[];
  live?: boolean;
}) {
  if (!cards.length) return null;
  return (
    <div className="transcript-cards">
      {cards.map((card) =>
        card.kind === 'resource' ? (
          <ResourceCard key={cardKey(card)} card={card} live={live} />
        ) : (
          <ConnectCard key={cardKey(card)} card={card} />
        ),
      )}
    </div>
  );
}
