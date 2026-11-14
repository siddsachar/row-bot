import { createContext, useContext, useRef, useState } from 'react';
import {
  FolderCode,
  Link2,
  MessageCircleQuestion,
  Palette,
  Settings2,
  SquareArrowOutUpRight,
  Undo2,
} from 'lucide-react';
import type {
  EventRecord,
  ResourceView,
  SettingChange,
  TraceAppRef,
  TraceQuestionAnswer,
  TranscriptTraceGroup,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { useOverlay } from '../../ui/overlays';
import { AppLink } from '../../ui/app-link';
import { Button, Disclosure, Hint, IconButton, Input } from '../../ui/primitives';
import { useOptionalRuntime } from '../../runtime';
import ChatConnect from '../apps/ChatConnect';
import AppViewFrame from '../apps/AppViewFrame';
import { ProcessCard } from './BackgroundProcess';
import { RecordingCard } from './RecordingCard';
import { VideoEditCard } from './VideoEditCard';
import { DeliveryCard } from './DeliveryCard';
import { SandboxJobCard } from './SandboxJobCard';
import { SandboxPrepareOffer } from './SandboxPrepare';
import { PageCard } from '../pages/PageCard';
import { VideoProjectCard } from '../video/VideoProjectCard';
import { LinkedConversationCard } from './LinkedConversationCard';

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
  | {
      kind: 'video_edit';
      editId: string;
      /** Stills, not a version of a video. */ frames?: boolean;
    }
  /** Files a step sent to the person's devices or a chat app (F7). */
  | { kind: 'delivery'; deliveryId: string }
  /** Prepare sandbox, or the sandbox image's download, a step started (F18). */
  | { kind: 'sandbox_job'; jobId: string }
  /** A sandbox test run that couldn't start for want of its tools: Prepare sandbox (F18). */
  | { kind: 'sandbox_offer'; reason: string }
  /** A page a step made or changed, with Open (N5). */
  | { kind: 'page'; pageId: string }
  /** A video project a step made or changed, with Open (5.3.1 B3). */
  | { kind: 'video_project'; projectId: string }
  /** A conversation this one started or proposed, followed by its link (5.3.1 C). */
  | { kind: 'linked'; linkId: string; title: string }
  /** A setting Row-Bot changed, with Undo (its result message) and its row. */
  | {
      kind: 'setting';
      callId: string;
      messageId: string;
      change: SettingChange;
    }
  /** Row-Bot's question, answered (or skipped), collapsed to what was chosen (N4). */
  | {
      kind: 'answer';
      callId: string;
      outcome: 'answered' | 'skipped' | 'timed_out';
      answers: TraceQuestionAnswer[];
    };

type Specialization = NonNullable<
  TranscriptTraceGroup['items'][number]['specialization']
>;

export function cardOf(
  value: Specialization | null | undefined,
  callId = '',
  messageId = '',
) {
  if (!value) return null;
  if (value.kind === 'setting_change' && value.setting_change)
    return {
      kind: 'setting',
      callId,
      messageId,
      change: value.setting_change,
    } satisfies TranscriptCard;
  if (
    value.kind === 'question_answered' &&
    (value.outcome === 'answered' ||
      value.outcome === 'skipped' ||
      value.outcome === 'timed_out')
  )
    return {
      kind: 'answer',
      callId,
      outcome: value.outcome,
      answers: value.answers ?? [],
    } satisfies TranscriptCard;
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
  if (value.kind === 'page' && value.page_id)
    return { kind: 'page', pageId: value.page_id } satisfies TranscriptCard;
  if (value.kind === 'video_project' && value.project_id)
    return {
      kind: 'video_project',
      projectId: value.project_id,
    } satisfies TranscriptCard;
  if (value.kind === 'linked_conversation' && value.link_id)
    return {
      kind: 'linked',
      linkId: value.link_id,
      title: value.display_name ?? '',
    } satisfies TranscriptCard;
  if (value.kind === 'video_edit' && value.edit_id)
    return {
      kind: 'video_edit',
      editId: value.edit_id,
      ...(value.media_kind === 'frames' ? { frames: true } : {}),
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
  if (card.kind === 'setting') return `setting:${card.callId}`;
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
                : card.kind === 'answer'
                  ? `answer:${card.callId}`
                  : card.kind === 'page'
                    ? `page:${card.pageId}`
                    : card.kind === 'video_project'
                      ? `video_project:${card.projectId}`
                      : card.kind === 'linked'
                        ? `linked:${card.linkId}`
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
          : cardOf(item.specialization, item.call_id, item.result_message_id);
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
    const card = cardOf(
      record.event.payload.specialization,
      record.event.payload.tool_call_id,
    );
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

type UndoState = 'idle' | 'busy' | 'undone' | 'changed_since' | 'unavailable';

/**
 * A setting Row-Bot changed: what it is now, Undo (the person's own action:
 * nothing is asked, and it refuses if the setting changed since), and its row
 * in Settings.
 */
function SettingCard({
  card,
  conversation,
}: {
  card: Extract<TranscriptCard, { kind: 'setting' }>;
  conversation?: string | null;
}) {
  const runtime = useOptionalRuntime();
  const [state, setState] = useState<UndoState>('idle');
  const [note, setNote] = useState('');
  const { label, before, after, href } = card.change;
  const turnedOn = before === 'Off' && after === 'On';
  async function undo() {
    if (!runtime || !conversation || !card.messageId || state === 'busy')
      return;
    setState('busy');
    setNote('');
    try {
      const result = await runtime.controller.undoSettingChange(
        conversation,
        card.messageId,
      );
      setState(result?.status ?? 'unavailable');
      if (result && result.status !== 'undone') setNote(result.message);
    } catch (cause) {
      setState('idle');
      setNote(clientError(cause).message);
    }
  }
  const canUndo =
    Boolean(before && card.messageId && conversation && runtime) &&
    state !== 'undone';
  return (
    <div
      className="transcript-card"
      data-kind="setting"
      role="group"
      aria-label={state === 'undone' ? `Undone: ${label}` : `Changed ${label}`}
    >
      <Settings2 className="transcript-card-icon" aria-hidden />
      <div className="transcript-card-text">
        <span>
          {state === 'undone' ? (
            <>
              Undone: <strong>{label}</strong> is back to {before}
            </>
          ) : turnedOn ? (
            <>
              Turned on <strong>{label}</strong>
            </>
          ) : (
            <>
              Changed <strong>{label}</strong> to {after}
            </>
          )}
        </span>
      </div>
      <div className="transcript-card-actions">
        {canUndo && (
          <IconButton
            size="sm"
            label={
              state === 'changed_since'
                ? 'Changed since: open Settings to change it'
                : turnedOn
                  ? 'Turn it off again'
                  : `Undo: back to ${before}`
            }
            disabled={
              state === 'busy' ||
              state === 'changed_since' ||
              state === 'unavailable'
            }
            aria-busy={state === 'busy' || undefined}
            onClick={() => void undo()}
          >
            <Undo2 size={15} aria-hidden />
          </IconButton>
        )}
        {href && (
          <Hint label="Show in Settings">
            <AppLink
              className="button ghost icon-button icon-action icon-action-sm"
              to={href}
              aria-label="Show in Settings"
            >
              <SquareArrowOutUpRight size={15} aria-hidden />
            </AppLink>
          </Hint>
        )}
      </div>
      {note && (
        <p role="status" className="transcript-card-error">
          {note}
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

/**
 * Row-Bot's question once answered: "You chose: Beta.1 now", plain and
 * quiet, or that it was skipped or had no answer in time (N4).
 */
function AnsweredCard({
  card,
}: {
  card: Extract<TranscriptCard, { kind: 'answer' }>;
}) {
  const single = card.answers.length === 1;
  const words =
    card.outcome === 'skipped'
      ? 'You skipped the question'
      : card.outcome === 'timed_out'
        ? 'No answer in time'
        : `You chose: ${card.answers
            .map((item) =>
              single ? item.answer : `${item.header}: ${item.answer}`,
            )
            .join(' · ')}`;
  return (
    <div
      className="transcript-card question-answered"
      data-outcome={card.outcome}
      role="group"
      aria-label={words}
    >
      <MessageCircleQuestion className="transcript-card-icon" aria-hidden />
      <div className="transcript-card-text">
        {card.outcome !== 'answered' ? (
          <span>{words}</span>
        ) : (
          <span>
            You chose:{' '}
            {card.answers.map((item, index) => (
              <span
                key={`${index}:${item.header}`}
                className="question-answered-item"
              >
                {index > 0 && ' · '}
                {!single && <>{item.header}: </>}
                <strong>{item.answer}</strong>
              </span>
            ))}
          </span>
        )}
      </div>
    </div>
  );
}

export function TranscriptCards({
  cards,
  earlier = [],
  live = false,
  conversation,
}: {
  cards: TranscriptCard[];
  /** Earlier versions of this reply's newest video, folded under it. */
  earlier?: TranscriptCard[];
  live?: boolean;
  /** The chat a view belongs to; views show only where it is known. */
  conversation?: string | null;
}) {
  if (!cards.length && !earlier.length) return null;
  const show = (card: TranscriptCard) => (
    <CardView
      key={cardKey(card)}
      card={card}
      live={live}
      conversation={conversation}
    />
  );
  return (
    <div className="transcript-cards">
      {cards.map(show)}
      {earlier.length > 0 && (
        <Disclosure
          className="transcript-earlier-videos"
          summary={
            earlier.length === 1
              ? '1 earlier version'
              : `${earlier.length} earlier versions`
          }
        >
          <div className="transcript-cards">{earlier.map(show)}</div>
        </Disclosure>
      )}
    </div>
  );
}

function CardView({
  card,
  live,
  conversation,
}: {
  card: TranscriptCard;
  live: boolean;
  conversation?: string | null;
}) {
  return card.kind === 'resource' ? (
    <ResourceCard card={card} live={live} />
  ) : card.kind === 'apps' ? (
    <AppsCard card={card} />
  ) : card.kind === 'process' ? (
    <ProcessCard processId={card.processId} conversationId={conversation} />
  ) : card.kind === 'recording' ? (
    <RecordingCard
      recordingId={card.recordingId}
      conversationId={conversation}
    />
  ) : card.kind === 'delivery' ? (
    <DeliveryCard deliveryId={card.deliveryId} />
  ) : card.kind === 'sandbox_job' ? (
    <SandboxJobCard jobId={card.jobId} />
  ) : card.kind === 'sandbox_offer' ? (
    <SandboxPrepareOffer conversationId={conversation} reason={card.reason} />
  ) : card.kind === 'answer' ? (
    <AnsweredCard card={card} />
  ) : card.kind === 'setting' ? (
    <SettingCard card={card} conversation={conversation} />
  ) : card.kind === 'page' ? (
    <PageCard pageId={card.pageId} conversationId={conversation} />
  ) : card.kind === 'video_project' ? (
    <VideoProjectCard
      projectId={card.projectId}
      conversationId={conversation}
    />
  ) : card.kind === 'linked' ? (
    <LinkedConversationCard
      linkId={card.linkId}
      title={card.title}
      conversationId={conversation}
    />
  ) : card.kind === 'video_edit' ? (
    <VideoEditCard editId={card.editId} conversationId={conversation} />
  ) : card.kind === 'view' ? (
    conversation ? (
      <AppViewFrame
        conversation={conversation}
        callId={card.callId}
        app={card.app}
      />
    ) : null
  ) : (
    <ConnectCard card={card} />
  );
}
