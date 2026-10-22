import { useState } from 'react';
import { Square, Terminal } from 'lucide-react';
import type { BackgroundProcessView } from '../../api/types';
import { clientError } from '../../api/errors';
import { useOverlay } from '../../ui/overlays';
import {
  Button,
  Disclosure,
  EntityList,
  EntityRow,
  ErrorState,
  InlineEmpty,
  Skeleton,
  StatusDot,
} from '../../ui/primitives';
import {
  processLive,
  processName,
  processStatus,
  runningSeconds,
  shortAddress,
  useBackgroundProcesses,
  useNow,
  type ProcessIo,
} from '../shell/background-processes';
import {
  ProcessDetails,
  ProcessOutputDrawer,
} from '../shell/BackgroundProcess';
import {
  learningIsOn,
  trainingLive,
  useTrainingWatch,
  type LearningIO,
} from '../learning/learning-state';
import { TrainingMonitorRow } from '../learning/TrainingMonitorRow';

export type BackgroundProcessesIo = ProcessIo & {
  /** Open the conversation that started a process. */
  openConversation?: (conversationId: string) => void;
  /** Open the Personal model view (Learning, preview): its training run's row links there. */
  openLearning?: () => void;
};

/**
 * Every background process at once, in Monitor (F1, F6): what runs, in which
 * conversation, for how long, with Stop. Running ones first; finished ones
 * fold under "Recently finished". Monitor is the one place reachable from
 * every device that isn't tied to a conversation or a code folder.
 */
export default function BackgroundProcesses({
  io,
  learning = null,
}: {
  io: BackgroundProcessesIo;
  /** Learning (preview), when this build has it: the personal model's training run. */
  learning?: LearningIO | null;
}) {
  const overlay = useOverlay();
  const feed = useBackgroundProcesses(io);
  const [openId, setOpenId] = useState('');
  const [busy, setBusy] = useState<ReadonlySet<string>>(new Set());
  const [error, setError] = useState('');
  const running = feed.items.filter(processLive);
  const finished = feed.items.filter((item) => !processLive(item)).slice(0, 20);
  const now = useNow(running.length > 0);
  const opened = feed.items.find((item) => item.process_id === openId);
  // Learning (preview): the personal model's training run, one row while it runs.
  useTrainingWatch(learning);
  const training = learning?.snapshot?.training;
  const trainingRow =
    learning && training && learningIsOn(learning) && trainingLive(training)
      ? training
      : null;
  const [stoppingTraining, setStoppingTraining] = useState(false);
  const live = running.length + (trainingRow ? 1 : 0);

  async function stopTraining() {
    if (!learning || !trainingRow) return;
    setStoppingTraining(true);
    setError('');
    try {
      await learning.run('stop_training', { job_id: trainingRow.job_id ?? '' });
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setStoppingTraining(false);
    }
  }

  async function stop(views: readonly BackgroundProcessView[]) {
    const ids = views.map((view) => view.process_id);
    setBusy((current) => new Set([...current, ...ids]));
    setError('');
    try {
      const results = await Promise.allSettled(ids.map((id) => io.stop(id)));
      for (const result of results)
        if (result.status === 'fulfilled') feed.merge(result.value);
      const failed = results.find((result) => result.status === 'rejected');
      if (failed && failed.status === 'rejected')
        setError(clientError(failed.reason).message);
    } finally {
      setBusy(
        (current) => new Set([...current].filter((id) => !ids.includes(id))),
      );
      feed.refresh();
    }
  }

  function row(view: BackgroundProcessView) {
    const seconds = runningSeconds(view, feed.readAt, now);
    const status = processStatus(view, seconds);
    const live = processLive(view);
    const meta = [
      view.conversation_title || 'Untitled conversation',
      live && view.local_urls[0] ? shortAddress(view.local_urls[0]) : '',
    ]
      .filter(Boolean)
      .join(' · ');
    return (
      <EntityRow
        key={view.process_id}
        className="process-row"
        title={processName(view)}
        icon={<Terminal size={16} />}
        status={{ tone: status.tone, label: status.label }}
        meta={meta}
        action={
          live ? (
            <Button
              className="small"
              aria-label={`Stop ${processName(view)}`}
              disabled={busy.has(view.process_id) || view.status === 'stopping'}
              onClick={() => void stop([view])}
            >
              <Square size={12} aria-hidden />
              Stop
            </Button>
          ) : undefined
        }
        menuLabel={`More for ${processName(view)}`}
        menu={[
          { label: 'View output', onSelect: () => setOpenId(view.process_id) },
          ...(io.openConversation
            ? [
                {
                  label: 'Open conversation',
                  onSelect: () => io.openConversation?.(view.conversation_id),
                },
              ]
            : []),
        ]}
        details={
          <ProcessDetails view={view} conversation={view.conversation_title} />
        }
      />
    );
  }

  return (
    <section
      className="monitor-section monitor-processes"
      aria-labelledby="processes-heading"
    >
      <header className="monitor-section-head">
        <h3 id="processes-heading">Background processes</h3>
        {live > 0 && (
          <StatusDot tone="accent" pulse showLabel label={`${live} running`} />
        )}
        {live > 1 && (
          <Button
            variant="ghost"
            className="small"
            onClick={() =>
              overlay.open({
                kind: 'alert',
                title: `Stop ${live} background processes?`,
                description:
                  'Each one stops with everything it started. Row-Bot can start them again when you ask.',
                confirmLabel: 'Stop all',
                onConfirm: () => {
                  overlay.close();
                  void stop(running);
                  if (trainingRow) void stopTraining();
                },
              })
            }
          >
            Stop all
          </Button>
        )}
      </header>
      {error && <p role="alert">{error}</p>}
      {!feed.loaded ? (
        <Skeleton label="Loading background processes" />
      ) : feed.error && !feed.items.length ? (
        <ErrorState
          title="Background processes unavailable"
          action={
            <Button className="small" onClick={feed.refresh}>
              Try again
            </Button>
          }
        >
          Row-Bot couldn&apos;t read them just now.
        </ErrorState>
      ) : live ? (
        <EntityList label="Running background processes">
          {trainingRow && (
            <TrainingMonitorRow
              training={trainingRow}
              busy={stoppingTraining}
              onStop={() => void stopTraining()}
              onOpen={io.openLearning}
            />
          )}
          {running.map(row)}
        </EntityList>
      ) : (
        <InlineEmpty icon={<Terminal size={16} />}>
          Nothing is running in the background. When Row-Bot starts a server or
          a long command for you, it shows here with a Stop button.
        </InlineEmpty>
      )}
      {finished.length > 0 && (
        <Disclosure
          className="monitor-processes-finished"
          summary="Recently finished"
          meta={finished.length}
        >
          <EntityList label="Recently finished background processes">
            {finished.map(row)}
          </EntityList>
        </Disclosure>
      )}
      {opened && (
        <ProcessOutputDrawer
          view={opened}
          io={io}
          open
          onOpenChange={(open) => !open && setOpenId('')}
          onStop={() => void stop([opened])}
          stopping={busy.has(opened.process_id)}
          conversation={opened.conversation_title}
          seconds={runningSeconds(opened, feed.readAt, now)}
        />
      )}
    </section>
  );
}
