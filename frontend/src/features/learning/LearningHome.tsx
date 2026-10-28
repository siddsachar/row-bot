import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { BookOpen, Inbox, MoreHorizontal, Moon, Search } from 'lucide-react';
import { clientError } from '../../api/errors';
import type {
  LearningHardware,
  LearningLessonPage,
  LearningLessonView,
  LearningSnapshot,
} from '../../api/types';
import {
  Button,
  EmptyState,
  EntityList,
  EntityRow,
  InlineEmpty,
  Input,
  Menu,
  Segmented,
  StatusDot,
} from '../../ui/primitives';
import { useNotify } from '../../ui/overlays';
import { relativeTime } from '../../ui/format';
import { useDeleteLearningData } from './DeleteLearningData';
import {
  useDownloadWatch,
  useTrainingWatch,
  type LearningIO,
} from './learning-state';
import { DownloadAsk } from './LearningSetup';
import {
  DownloadProgress,
  downloadView,
  missingWords,
} from './PersonalModelDownload';
import {
  RunsInSheet,
  runsInWords,
  VersionCard,
  versionFileWords,
} from './PersonalModelVersion';
import {
  AdvancedSection,
  PersonalModelNeeds,
  UsesSection,
  VersionReview,
  VersionsSection,
} from './PersonalModelUse';
import { CardRoomNotice } from './CardRoomNotice';
import {
  ChangeNeeds,
  ChangeReview,
  ChangesView,
  UsedByItself,
} from './ChangesView';
import { MethodSheet, MethodSummary, MethodSwitched } from './HowRowBotLearns';
import { QuickQuestion } from './QuickQuestion';
import { StrongerModelNotice } from './StrongerModelNotice';
import { TrainingProgress } from './TrainingProgress';
import {
  dayWords,
  KIND_STATE_WORDS,
  kindLabel,
  LESSON_STATUS,
  memoryAdvice,
  memoryNeedWords,
  nextLookWords,
  overnightLines,
  plural,
  quietHoursWords,
  sizeWords,
  timesWords,
  tokenWords,
} from './words';
import './learning.css';

export type LearningView = 'overview' | 'changes' | 'lessons' | 'model';
export const LEARNING_VIEWS: { value: LearningView; label: string }[] = [
  { value: 'overview', label: 'Overview' },
  { value: 'changes', label: 'Changes' },
  { value: 'lessons', label: 'Lessons' },
  { value: 'model', label: 'Personal model' },
];

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="monitor-section" aria-label={title}>
      <header className="monitor-section-head">
        <h3>{title}</h3>
      </header>
      {children}
    </section>
  );
}

/** "Next look tonight at 1:00 AM · 240K of 2M tokens this month" (designs §3). */
function headerLine(snapshot: LearningSnapshot) {
  const { quiet_hours: hours, spending } = snapshot;
  const when = hours.next_look_at
    ? nextLookWords(hours.next_look_at)
    : `Quiet hours ${quietHoursWords(hours.window_start, hours.window_end)}`;
  return `${when} · ${tokenWords(spending.used_tokens)} of ${tokenWords(spending.limit_tokens)} tokens this month`;
}

/** Paused, limit reached and reading past chats: one line each, with the way out. */
function Banners({
  snapshot,
  busy,
  onResume,
  onRaiseLimit,
}: {
  snapshot: LearningSnapshot;
  busy: boolean;
  onResume: () => void;
  onRaiseLimit: () => void;
}) {
  const reading =
    snapshot.past_chats.state === 'reading' ||
    snapshot.past_chats.state === 'waiting';
  const { total, done } = snapshot.past_chats;
  return (
    <>
      {snapshot.state === 'paused' && (
        <div className="learning-banner" role="status">
          <p>Learning is paused. Nothing new is recorded, tested or trained.</p>
          <Button className="small" disabled={busy} onClick={onResume}>
            Resume
          </Button>
        </div>
      )}
      {snapshot.state === 'on' && snapshot.spending.limit_reached && (
        <div className="learning-banner" data-tone="warning" role="status">
          <p>
            {snapshot.spending.limit_tokens === 0
              ? 'Your monthly limit is 0, so Learning makes no model calls. Local training continues.'
              : `Monthly limit reached. Testing resumes on ${dayWords(snapshot.spending.resets_on)}. Local training continues.`}
          </p>
          <Button className="small" onClick={onRaiseLimit}>
            Raise limit
          </Button>
        </div>
      )}
      {snapshot.state === 'on' && reading && total > 0 && (
        <div className="learning-progress" role="status">
          <p className="home-caption">
            Reading {plural(total, 'past chat')} · {done.toLocaleString()} done
            {snapshot.past_chats.state === 'waiting'
              ? ' · waits while you chat'
              : ''}
          </p>
          <progress
            max={total}
            value={done}
            aria-label={`Reading ${plural(total, 'past chat')}`}
          />
        </div>
      )}
    </>
  );
}

function Overview({
  io,
  onReview,
  onReviewChange,
  onMethod,
}: {
  io: LearningIO;
  onReview: (version: number) => void;
  onReviewChange: (id: string) => void;
  onMethod: () => void;
}) {
  const needs = io.snapshot?.needs_you ?? [];
  const changes = io.snapshot?.changes ?? [];
  const changeNeeds = changes.some(
    (item) => item.state === 'ready' && !item.snoozed,
  );
  const testing = io.snapshot?.testing_now;
  const uses = io.snapshot?.personal_model.uses ?? [];
  const night = io.snapshot?.overnight;
  const method = io.snapshot?.method;
  const question = io.snapshot?.quick_question;
  const lines = night ? overnightLines(night) : [];
  const learning = io.snapshot?.kinds_learning ?? [];
  const kinds = [
    ...uses.map((item) => item.kind),
    ...learning
      .map((item) => item.kind)
      .filter((kind) => !uses.some((item) => item.kind === kind)),
  ];
  return (
    <div className="learning-section-list">
      {testing && (
        <div className="learning-progress" role="status">
          <p className="home-caption">
            Testing “{testing.title}” on {plural(testing.total, 'task')} ·{' '}
            {testing.done.toLocaleString()} done
          </p>
          <progress
            max={Math.max(1, testing.total)}
            value={testing.done}
            aria-label={`Testing “${testing.title}”`}
          />
        </div>
      )}
      <Section title="Needs you">
        {needs.length || changeNeeds || question ? (
          <>
            <QuickQuestion io={io} question={question} />
            <ChangeNeeds changes={changes} onReview={onReviewChange} />
            {needs.length > 0 && (
              <PersonalModelNeeds io={io} onReview={onReview} />
            )}
          </>
        ) : (
          <InlineEmpty icon={<Inbox size={15} />}>
            Nothing needs you. Changes that are ready to review show here.
          </InlineEmpty>
        )}
      </Section>
      <Section title="Overnight">
        {lines.length ? (
          <>
            <ul className="learning-overnight">
              {lines.map((line) => (
                <li key={line.text} data-tone={line.tone}>
                  {line.text}
                </li>
              ))}
            </ul>
            <MethodSwitched io={io} night={night} method={method} />
            <UsedByItself io={io} changes={changes} />
          </>
        ) : (
          <InlineEmpty icon={<Moon size={15} />}>
            Nothing has run yet. What Learning does in quiet hours shows here
            the next morning.
          </InlineEmpty>
        )}
      </Section>
      <Section title="What Row-Bot is learning">
        {kinds.length ? (
          <table className="learning-results learning-kinds">
            <caption className="visually-hidden">
              Kinds of work and where they stand
            </caption>
            <thead>
              <tr>
                <th scope="col">Kind of work</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {kinds.map((kind) => {
                const use = uses.find((item) => item.kind === kind);
                const state = learning.find((item) => item.kind === kind);
                const words = [
                  use &&
                    (use.state === 'trial'
                      ? `Trying personal model v${use.version}`
                      : `Personal model v${use.version} in use`),
                  state && KIND_STATE_WORDS[state.state],
                ].filter(Boolean);
                return (
                  <tr key={kind}>
                    <th scope="row">{use?.label ?? kindLabel(kind)}</th>
                    <td>{words.join(' · ')}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <div className="learning-empty-lead">
            <strong>Nothing learned yet.</strong>
            <p>
              Row-Bot looks for things to improve during quiet hours. The
              quickest way to help is <strong>Teach</strong> on a reply that
              went wrong.
            </p>
          </div>
        )}
      </Section>
      <Section title="How Row-Bot learns">
        <MethodSummary method={method} onDetails={onMethod} />
      </Section>
    </div>
  );
}

function lessonMeta(item: LearningLessonView): string {
  if (item.origin === 'agent_saved')
    return `Saved by Row-Bot during a chat${item.updated_at ? ` · ${relativeTime(item.updated_at)}` : ''}`;
  const kind =
    item.kind && item.kind !== 'other' ? kindLabel(item.kind) : 'Your work';
  return item.status === 'in_use'
    ? `${kind} · ${item.used ? `used ${timesWords(item.used)} this month` : 'not used yet this month'}`
    : kind;
}

function Lessons({ io }: { io: LearningIO }) {
  const [page, setPage] = useState<LearningLessonPage | null>(null);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const notify = useNotify();
  const revision = io.snapshot?.revision ?? '';
  const changeStates = (io.snapshot?.changes ?? [])
    .map((item) => item.state)
    .join();
  useEffect(() => {
    let live = true;
    io.lessons().then(
      (value) => live && setPage(value),
      (cause: unknown) => live && setError(clientError(cause).message),
    );
    return () => {
      live = false;
    };
    // Read when the view opens, and again when a change moves (a lesson used, undone or tested).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revision, changeStates]);
  async function act(
    action: 'undo_change' | 'delete_lesson' | 'test_lesson',
    item: LearningLessonView,
  ) {
    try {
      const receipt = await io.run(action, {
        change_id: action === 'undo_change' ? item.change_id : item.id,
      });
      notify(receipt.summary);
      setPage(await io.lessons());
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
    }
  }
  const shown = useMemo(() => {
    const words = query.trim().toLowerCase();
    return (page?.items ?? []).filter(
      (item) =>
        !words || `${item.title} ${item.text}`.toLowerCase().includes(words),
    );
  }, [page, query]);
  return (
    <div className="learning-section-list">
      <label className="home-search">
        <span className="visually-hidden">Search lessons</span>
        <Search size={14} aria-hidden className="settings-search-icon" />
        <Input
          type="search"
          placeholder="Search lessons"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      {error && <p role="alert">{error}</p>}
      {!page && !error && (
        <p className="home-caption" role="status">
          Loading lessons…
        </p>
      )}
      {page && page.items.length === 0 && (
        <InlineEmpty icon={<BookOpen size={15} />}>
          No lessons yet. Lessons Row-Bot learns from your work show here once
          they pass their tests and you use them.
        </InlineEmpty>
      )}
      {page && page.items.length > 0 && shown.length === 0 && (
        <InlineEmpty icon={<Search size={15} />}>
          No lessons match “{query.trim()}”.
        </InlineEmpty>
      )}
      {shown.length > 0 && (
        <EntityList label="Lessons">
          {shown.map((item) => {
            const status = LESSON_STATUS[item.status];
            const menu = [
              ...(item.status === 'in_use'
                ? [
                    {
                      label: 'Turn off',
                      onSelect: () => void act('undo_change', item),
                    },
                  ]
                : []),
              ...(item.origin === 'agent_saved' &&
              ['not_tested', 'tested_not_better'].includes(item.status)
                ? [
                    {
                      label: 'Test it',
                      onSelect: () => void act('test_lesson', item),
                    },
                  ]
                : []),
              {
                label: 'Delete',
                danger: true,
                onSelect: () => void act('delete_lesson', item),
              },
            ];
            return (
              <EntityRow
                key={item.id}
                title={item.title}
                icon={<BookOpen size={16} />}
                status={status}
                meta={lessonMeta(item)}
                action={
                  item.origin === 'agent_saved' &&
                  item.status === 'not_tested' ? (
                    <Button
                      className="small"
                      aria-label={`Test ${item.title}`}
                      onClick={() => void act('test_lesson', item)}
                    >
                      Test it
                    </Button>
                  ) : undefined
                }
                menu={menu}
                menuLabel={`More for ${item.title}`}
                details={item.text ? <p>{item.text}</p> : undefined}
              />
            );
          })}
        </EntityList>
      )}
    </div>
  );
}

/** The training engine's line in the facts: "Installed (3.4 GB)", "Downloading", "Needs an update". */
function engineFact(model: LearningSnapshot['personal_model']) {
  const download = model.download_state;
  if (download.state === 'downloading' && download.phase !== 'model')
    return download.phase === 'engine' ? 'Downloading' : 'Installing';
  if (model.engine === 'installed')
    return model.engine_bytes
      ? `Installed (${sizeWords(model.engine_bytes)})`
      : 'Installed';
  if (model.engine === 'damaged') return 'Damaged';
  if (model.engine === 'outdated') return 'Needs an update';
  return 'Not downloaded';
}

function startingModelFact(model: LearningSnapshot['personal_model']) {
  const download = model.download_state;
  if (download.state === 'downloading' && download.phase === 'model')
    return 'Downloading';
  if (model.starting_model_state === 'downloaded')
    return model.starting_model_bytes
      ? `On this computer (${sizeWords(model.starting_model_bytes)})`
      : 'On this computer';
  if (model.starting_model_state === 'damaged') return 'Damaged';
  return 'Not downloaded';
}

function PersonalModel({
  io,
  snapshot,
  onOpenSettings,
  onReview,
}: {
  io: LearningIO;
  snapshot: LearningSnapshot;
  onOpenSettings: (anchor?: string) => void;
  onReview: () => void;
}) {
  const [hardware, setHardware] = useState<LearningHardware | null>(null);
  const [askOpen, setAskOpen] = useState(false);
  const [runsInOpen, setRunsInOpen] = useState(false);
  const notify = useNotify();
  // Training progress and the download stay fresh while this view is open.
  useTrainingWatch(io);
  useDownloadWatch(io);
  const check = (refresh = false) => {
    if (refresh) setHardware(null);
    io.hardware(refresh).then(setHardware, () => undefined);
  };
  useEffect(() => {
    check();
    // The computer doesn't change while the view is open (Check again asks it again).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const model = snapshot.personal_model;
  const starting = model.starting_model;
  const settings = (
    <Button className="small" onClick={() => onOpenSettings('personal-model')}>
      Learning settings
    </Button>
  );
  const training = snapshot.training;
  const downloaded =
    model.engine === 'installed' && model.starting_model_state === 'downloaded';
  const download = downloadView(model);
  // A run (or an engine with nothing to train yet) takes the place of the setup states.
  const showTraining =
    training.state !== 'none' ||
    (downloaded &&
      ['no_data', 'planning', 'up_to_date'].includes(
        training.runner.reason ?? '',
      ));
  let state: ReactNode;
  if (showTraining) state = <TrainingProgress io={io} snapshot={snapshot} />;
  else if (!model.train_in_quiet_hours)
    state = (
      <EmptyState title="Training is off" action={settings}>
        Turn on Train during quiet hours to train a model of your own on your
        work.
      </EmptyState>
    );
  else if (model.train_where === 'hugging_face')
    state = (
      <EmptyState title="Training on Hugging Face" action={settings}>
        Your personal model will train in your own Hugging Face account, priced
        before each run. Nothing is uploaded until you agree to a run.
      </EmptyState>
    );
  else if (hardware?.state === 'old_driver')
    state = (
      <EmptyState
        title="Update your NVIDIA driver to train here"
        action={
          <Button className="small" onClick={() => check(true)}>
            Check again
          </Button>
        }
      >
        {`${hardware.gpu?.label ?? 'This graphics card'} can train a personal model, but its driver is too old. Training needs NVIDIA driver 570 or newer.`}
      </EmptyState>
    );
  else if (hardware?.state === 'low_memory')
    state = (
      <EmptyState
        title="This computer’s memory can’t hold training yet"
        action={
          <Button className="small" onClick={() => check(true)}>
            Check again
          </Button>
        }
      >
        {`Training needs about ${hardware.tier?.system_memory_gb ?? ''} GB of RAM and virtual memory, and this computer has ${hardware.memory?.limit_gb ?? ''} GB. ${memoryAdvice(hardware.memory?.system)}`}
      </EmptyState>
    );
  else if (hardware?.state === 'container')
    state = (
      <EmptyState title="Training isn’t available here" action={settings}>
        Row-Bot’s Docker image doesn’t train personal models. Run Row-Bot on the
        computer with the graphics card (Windows or Linux) to train one.
      </EmptyState>
    );
  else if (hardware && hardware.state !== 'gpu')
    state = (
      <EmptyState title="This computer can’t train a model" action={settings}>
        {hardware.gpu
          ? `${hardware.gpu.label} is too small: training needs about 8 GB. `
          : ''}
        You can train on Hugging Face instead (your account, priced before each
        run).
      </EmptyState>
    );
  else if (!download && !downloaded)
    state = (
      <EmptyState
        title="No personal model yet"
        action={
          <Button variant="primary" onClick={() => setAskOpen(true)}>
            Download…
          </Button>
        }
      >
        {`Row-Bot can train a model of your own on your work, here on this computer. First it needs ${missingWords(model)}.`}
      </EmptyState>
    );
  const mainName = snapshot.spending.model_name || 'your main model';
  // The card shows a version until its comparison starts; Versions shows it from then on.
  const comparison = model.comparison;
  const preparing =
    model.version &&
    (model.version.phase !== 'ready' ||
      !comparison ||
      comparison.state === 'waiting');
  return (
    <div className="learning-section-list">
      {model.room && (
        <CardRoomNotice io={io} room={model.room} model={starting.name} />
      )}
      {model.stronger_model && !model.room && (
        <StrongerModelNotice io={io} model={model.stronger_model} />
      )}
      {download && <DownloadProgress io={io} snapshot={snapshot} />}
      {preparing && (
        <VersionCard
          io={io}
          model={model}
          onRunsIn={() => setRunsInOpen(true)}
        />
      )}
      <UsesSection io={io} model={model} mainName={mainName} />
      <VersionsSection io={io} model={model} onReview={onReview} />
      {state}
      <dl className="learning-facts" aria-label="Personal model">
        {(downloaded || model.version) && (
          <div>
            <dt>Runs in</dt>
            <dd className="learning-fact-action">
              <span>{runsInWords(model)}</span>
              <Button
                variant="ghost"
                className="small"
                aria-label="Change where your personal model runs"
                onClick={() => setRunsInOpen(true)}
              >
                Change
              </Button>
            </dd>
          </div>
        )}
        {!!model.version?.file_bytes && (
          <div>
            <dt>Newest version</dt>
            <dd>{versionFileWords(model.version)}</dd>
          </div>
        )}
        <div>
          <dt>Starting model</dt>
          <dd>
            {starting.name} · {starting.licence} · {startingModelFact(model)}
          </dd>
        </div>
        <div>
          <dt>Training engine</dt>
          <dd>{engineFact(model)}</dd>
        </div>
        <div>
          <dt>Train during quiet hours</dt>
          <dd>{model.train_in_quiet_hours ? 'On' : 'Off'}</dd>
        </div>
        <div>
          <dt>Where to train</dt>
          <dd>
            {model.train_where === 'hugging_face'
              ? 'Hugging Face'
              : 'This computer'}
          </dd>
        </div>
        <div>
          <dt>This computer</dt>
          <dd>
            {!hardware
              ? 'Checking…'
              : hardware.gpu
                ? hardware.gpu.label
                : hardware.state === 'unknown'
                  ? 'Couldn’t check the graphics card'
                  : 'No NVIDIA graphics card'}
            {hardware?.state === 'gpu' && hardware.tier && (
              <span className="learning-muted learning-fact-note">
                {hardware.tier.summary}
                {(hardware.recommended ?? hardware.tier).system_memory_gb
                  ? ` ${memoryNeedWords((hardware.recommended ?? hardware.tier).system_memory_gb)}`
                  : ''}
              </span>
            )}
            {hardware?.state === 'low_memory' && hardware.tier && (
              <span className="learning-muted learning-fact-note">
                Its memory can’t hold training yet.
              </span>
            )}
          </dd>
        </div>
      </dl>
      {downloaded && model.advanced && (
        <AdvancedSection io={io} advanced={model.advanced} />
      )}
      <DownloadAsk
        open={askOpen}
        onOpenChange={setAskOpen}
        io={io}
        hardware={hardware}
        onAnswered={(_download, summary) => {
          setAskOpen(false);
          notify(summary);
        }}
      />
      <RunsInSheet
        open={runsInOpen}
        onOpenChange={setRunsInOpen}
        io={io}
        model={model}
      />
    </div>
  );
}

/** Home › Learning (designs/learning-ux.md §3, §5, §7): Overview, Changes, Lessons, Personal model. */
export default function LearningHome({
  io,
  view,
  onViewChange,
  onOpenSettings,
}: {
  io: LearningIO;
  view: LearningView;
  onViewChange: (view: LearningView) => void;
  onOpenSettings: (anchor?: string) => void;
}) {
  const notify = useNotify();
  const [busy, setBusy] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [changeOpen, setChangeOpen] = useState<string | null>(null);
  const [methodOpen, setMethodOpen] = useState(false);
  const deleteData = useDeleteLearningData(io);
  const snapshot = io.snapshot;
  const comparison = snapshot?.personal_model.comparison;
  async function command(action: 'pause' | 'resume') {
    setBusy(true);
    try {
      notify((await io.run(action)).summary);
    } catch (cause) {
      notify(clientError(cause).message, 'danger');
    } finally {
      setBusy(false);
    }
  }
  if (!snapshot)
    return (
      <section className="home-page learning-home" aria-label="Learning">
        {io.error ? (
          <p role="alert">{io.error}</p>
        ) : (
          <p className="home-caption" role="status">
            Loading Learning…
          </p>
        )}
      </section>
    );
  const paused = snapshot.state === 'paused';
  const reviewingChange =
    (snapshot.changes ?? []).find((item) => item.id === changeOpen) ?? null;
  return (
    <section className="home-page learning-home" aria-label="Learning">
      <header className="home-page-header">
        <h2>Learning</h2>
        <StatusDot
          tone={paused ? 'neutral' : 'success'}
          label={paused ? 'Paused' : 'On'}
          showLabel
        />
        <p className="home-caption">{headerLine(snapshot)}</p>
        <span className="home-page-header-spacer" />
        <Menu
          label="More for Learning"
          iconOnly
          variant="ghost"
          className="icon-action"
          actions={[
            paused
              ? { label: 'Resume', onSelect: () => void command('resume') }
              : { label: 'Pause', onSelect: () => void command('pause') },
            { label: 'Learning settings', onSelect: () => onOpenSettings() },
            {
              label: 'Delete learning data…',
              danger: true,
              disabled: !snapshot.data.exists,
              onSelect: (opener) => deleteData(opener),
            },
          ]}
        >
          <MoreHorizontal size={16} aria-hidden />
        </Menu>
      </header>
      <Segmented
        label="Learning view"
        value={view}
        onChange={onViewChange}
        options={LEARNING_VIEWS}
        className="learning-views"
      />
      <Banners
        snapshot={snapshot}
        busy={busy}
        onResume={() => void command('resume')}
        onRaiseLimit={() => onOpenSettings('monthly_token_limit')}
      />
      {view === 'overview' && (
        <Overview
          io={io}
          onReview={() => setReviewing(true)}
          onReviewChange={setChangeOpen}
          onMethod={() => setMethodOpen(true)}
        />
      )}
      {view === 'changes' && (
        <ChangesView io={io} snapshot={snapshot} onOpen={setChangeOpen} />
      )}
      {view === 'lessons' && <Lessons io={io} />}
      {view === 'model' && (
        <PersonalModel
          io={io}
          snapshot={snapshot}
          onOpenSettings={onOpenSettings}
          onReview={() => setReviewing(true)}
        />
      )}
      {reviewingChange && (
        <ChangeReview
          io={io}
          change={reviewingChange}
          open
          onOpenChange={(next) => !next && setChangeOpen(null)}
        />
      )}
      {snapshot.method && (
        <MethodSheet
          io={io}
          method={snapshot.method}
          open={methodOpen}
          onOpenChange={setMethodOpen}
        />
      )}
      {comparison?.state === 'done' && (
        <VersionReview
          open={reviewing}
          onOpenChange={setReviewing}
          io={io}
          model={snapshot.personal_model}
          comparison={comparison}
        />
      )}
    </section>
  );
}
