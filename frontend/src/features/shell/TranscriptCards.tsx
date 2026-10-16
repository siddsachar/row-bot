import { createContext, useContext, useRef, useState } from 'react';
import { FolderCode, Link2, Palette } from 'lucide-react';
import type {
  EventRecord,
  ResourceView,
  TraceAppRef,
  TranscriptTraceGroup,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useOverlay } from '../../ui/overlays';
import { Button, Input } from '../../ui/primitives';
import ChatConnect from '../apps/ChatConnect';
import AppViewFrame from '../apps/AppViewFrame';
import { ProcessCard } from './BackgroundProcess';
import { RecordingCard } from './RecordingCard';
import { VideoEditCard } from './VideoEditCard';
import { DeliveryCard } from './DeliveryCard';
import { SandboxJobCard } from './SandboxJobCard';
import { SandboxPrepareOffer } from './SandboxPrepare';

/**
 * Cards the assistant leaves in a turn (decision 12): a design or code folder
 * it created, with Open · Rename · Undo, a code folder the person had or
 * cloned (B277), with Open · Undo (it only leaves this conversation), a
 * Connect card for apps from Row-Bot's catalog the work needs (or, in chats
 * from before apps, an account or channel), and a background process a step
 * started, followed live (F1, F6). Each comes from its tool's reviewed
 * specialization, live from `tool.activity` and settled from the turn's
 * traces.
 */
export type TranscriptCard =
  | {
      kind: 'resource';
      resourceKind: 'design' | 'code';
      name: string;
      bindingId: string;
      resourceId: string;
      /** Brought in rather than created: Undo removes it from the chat. */
      bound?: boolean;
    }
  | {
      kind: 'connect';
      target: string;
      label: string;
      page: 'accounts' | 'channels';
    }
  | { kind: 'apps'; apps: TraceAppRef[] }
  /** A finished step whose app shows a view (MCP Apps). */
  | { kind: 'view'; callId: string; app: TraceAppRef }
  /** A server, watcher or long command a step left running in the background. */
  | { kind: 'process'; processId: string }
  /** A recording of Row-Bot's browser or the screen a step started or stopped (F2). */
  | { kind: 'recording'; recordingId: string }
  /** A video edit a step started, followed or stopped (F3). */
  | { kind: 'video_edit'; editId: string }
  /** Files a step sent to the person's devices or a chat app (F7). */
  | { kind: 'delivery'; deliveryId: string }
  /** Prepare sandbox, or the sandbox image's download, a step started (F18). */
  | { kind: 'sandbox_job'; jobId: string }
  /** A sandbox test run that couldn't start for want of its tools: Prepare sandbox (F18). */
  | { kind: 'sandbox_offer'; reason: string };

type Specialization = NonNullable<
  TranscriptTraceGroup['items'][number]['specialization']
>;

export function cardOf(value: Specialization | null | undefined) {
  if (!value) return null;
  if (
    (value.kind === 'resource_created' || value.kind === 'resource_bound') &&
    (value.resource_kind === 'design' || value.resource_kind === 'code') &&
    value.binding_id
  )
    return {
      kind: 'resource',
      resourceKind: value.resource_kind,
      name: value.display_name ?? '',
      bindingId: value.binding_id,
      resourceId: value.resource_id ?? '',
      ...(value.kind === 'resource_bound' ? { bound: true } : {}),
    } satisfies TranscriptCard;
  if (value.kind === 'connect_apps' && value.apps?.length)
    return { kind: 'apps', apps: value.apps } satisfies TranscriptCard;
  if (value.kind === 'background_process' && value.process_id)
    return {
      kind: 'process',
      processId: value.process_id,
    } satisfies TranscriptCard;
  if (value.kind === 'recording' && value.recording_id)
    return {
      kind: 'recording',
      recordingId: value.recording_id,
    } satisfies TranscriptCard;
  if (value.kind === 'delivery' && value.delivery_id)
    return {
      kind: 'delivery',
      deliveryId: value.delivery_id,
    } satisfies TranscriptCard;
  if (value.kind === 'sandbox_job' && value.job_id)
    return {
      kind: 'sandbox_job',
      jobId: value.job_id,
    } satisfies TranscriptCard;
  if (value.kind === 'sandbox_unprepared')
    return {
      kind: 'sandbox_offer',
      reason: value.error_code ?? '',
    } satisfies TranscriptCard;
  if (value.kind === 'video_edit' && value.edit_id)
    return {
      kind: 'video_edit',
      editId: value.edit_id,
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
    : card.kind === 'apps'
      ? `apps:${card.apps.map((app) => app.item_id).join(',')}`
      : card.kind === 'view'
        ? `view:${card.callId}`
        : card.kind === 'process'
          ? `process:${card.processId}`
          : card.kind === 'recording'
            ? `recording:${card.recordingId}`
            : card.kind === 'video_edit'
              ? `video_edit:${card.editId}`
              : card.kind === 'delivery'
                ? `delivery:${card.deliveryId}`
                : card.kind === 'sandbox_job'
                  ? `sandbox_job:${card.jobId}`
                  : card.kind === 'sandbox_offer'
                    ? 'sandbox_offer'
                    : `connect:${card.target}`;
}

export function tracedCards(groups: TranscriptTraceGroup[]): TranscriptCard[] {
  const seen = new Set<string>();
  const cards: TranscriptCard[] = [];
  for (const group of groups)
    for (const item of group.items) {
      const card: TranscriptCard | null =
        item.app?.view && item.status === 'succeeded'
          ? { kind: 'view', callId: item.call_id, app: item.app }
          : cardOf(item.specialization);
      if (!card || seen.has(cardKey(card))) continue;
      seen.add(cardKey(card));
      cards.push(card);
    }
  // A step that went on to prepare the sandbox needs no offer to (F18).
  return cards.some((card) => card.kind === 'sandbox_job')
    ? cards.filter((card) => card.kind !== 'sandbox_offer')
    : cards;
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
  /** Takes a folder the person had out of this conversation; files stay. */
  remove: (bindingId: string) => Promise<void>;
  /** Opens that connection's app page (cards from chats before apps). */
  connect: (page: 'accounts' | 'channels', target: string) => void;
  /** Sends the request on in this chat once a suggested app is ready. */
  continueWith: () => void;
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
  const overlay = useOverlay();
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const committed = useRef(false);
  const resource = actions?.resource(card.bindingId);
  const title = resource?.title || card.name;
  // A removed card keeps the last name the person saw (after a Rename too).
  const lastTitle = useRef(card.name);
  if (resource?.title) lastTitle.current = resource.title;
  const noun = NOUN[card.resourceKind];
  const Icon = card.resourceKind === 'design' ? Palette : FolderCode;
  const [verb, gone] = card.bound
    ? ['Using', 'No longer using']
    : ['Created', 'Removed'];
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
        aria-label={`${gone} ${noun} ${lastTitle.current}`}
      >
        <Icon className="transcript-card-icon" aria-hidden />
        <div className="transcript-card-text">
          <span>
            {gone} {noun} <strong>{lastTitle.current}</strong>
          </span>
        </div>
      </div>
    );
  return (
    <div
      className="transcript-card"
      data-kind="resource"
      role="group"
      aria-label={`${verb} ${noun} ${title}`}
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
            {verb} {noun} <strong>{title}</strong>
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
        {!card.bound && (
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
        )}
        <Button
          variant="ghost"
          disabled={busy || !resource}
          title={
            card.bound
              ? 'Stop using this folder in this conversation; its files stay'
              : card.resourceKind === 'design'
                ? 'Delete this design'
                : 'Delete this code folder while it is still empty; files added to it are never deleted'
          }
          onClick={(event) => {
            if (!actions) return;
            const undo = () =>
              void run(() =>
                (card.bound ? actions.remove : actions.undo)(card.bindingId),
              );
            // Undo deletes a design with whatever was made in it since, so it
            // asks first, as Delete design does.
            if (card.bound || card.resourceKind !== 'design') undo();
            else
              overlay.open({
                kind: 'alert',
                returnFocusTo: event.currentTarget,
                title: 'Delete design?',
                description: `“${title}” will be deleted for good, with its pages, saved versions and files, including changes made since it was created.`,
                confirmLabel: 'Delete design',
                onConfirm: undo,
              });
          }}
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
        <Button
          variant="primary"
          onClick={() => actions?.connect(card.page, card.target)}
        >
          Connect {card.label}
        </Button>
      </div>
    </div>
  );
}

function AppsCard({
  card,
}: {
  card: Extract<TranscriptCard, { kind: 'apps' }>;
}) {
  const actions = useContext(CardActionsContext);
  return (
    <ChatConnect apps={card.apps} onContinue={() => actions?.continueWith()} />
  );
}

export function TranscriptCards({
  cards,
  live = false,
  conversation,
}: {
  cards: TranscriptCard[];
  live?: boolean;
  /** The chat a view belongs to; views show only where it is known. */
  conversation?: string | null;
}) {
  if (!cards.length) return null;
  return (
    <div className="transcript-cards">
      {cards.map((card) =>
        card.kind === 'resource' ? (
          <ResourceCard key={cardKey(card)} card={card} live={live} />
        ) : card.kind === 'apps' ? (
          <AppsCard key={cardKey(card)} card={card} />
        ) : card.kind === 'process' ? (
          <ProcessCard
            key={cardKey(card)}
            processId={card.processId}
            conversationId={conversation}
          />
        ) : card.kind === 'recording' ? (
          <RecordingCard
            key={cardKey(card)}
            recordingId={card.recordingId}
            conversationId={conversation}
          />
        ) : card.kind === 'delivery' ? (
          <DeliveryCard key={cardKey(card)} deliveryId={card.deliveryId} />
        ) : card.kind === 'sandbox_job' ? (
          <SandboxJobCard key={cardKey(card)} jobId={card.jobId} />
        ) : card.kind === 'sandbox_offer' ? (
          <SandboxPrepareOffer
            key={cardKey(card)}
            conversationId={conversation}
            reason={card.reason}
          />
        ) : card.kind === 'video_edit' ? (
          <VideoEditCard
            key={cardKey(card)}
            editId={card.editId}
            conversationId={conversation}
          />
        ) : card.kind === 'view' ? (
          conversation ? (
            <AppViewFrame
              key={cardKey(card)}
              conversation={conversation}
              callId={card.callId}
              app={card.app}
            />
          ) : null
        ) : (
          <ConnectCard key={cardKey(card)} card={card} />
        ),
      )}
    </div>
  );
}
