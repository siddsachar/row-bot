import { useEffect, useState } from 'react';
import {
  ExternalLink,
  FlaskConical,
  Play,
  RotateCcw,
  Square,
  Trash2,
} from 'lucide-react';
import { clientError } from '../../api/errors';
import type { RowBotSandboxPage, RowBotSandboxView } from '../../api/types';

type SandboxPack = RowBotSandboxView['pack'];
import { useRuntime } from '../../runtime';
import {
  Button,
  Field,
  IconButton,
  Select,
  Skeleton,
  StatusDot,
} from '../../ui/primitives';

const STATUS: Record<
  RowBotSandboxView['status'],
  { label: string; tone: 'neutral' | 'info' | 'success' | 'danger' }
> = {
  stopped: { label: 'Stopped', tone: 'neutral' },
  starting: { label: 'Starting', tone: 'info' },
  running: { label: 'Running', tone: 'success' },
  failed: { label: 'Didn’t start', tone: 'danger' },
};

/**
 * Sandboxes (5.3.1 B4) in Settings › Data: a second Row-Bot on its own data
 * folder, port and workspace, empty or with a made-up data pack, for trying a
 * workflow or skill before it touches real accounts, filming demos, or showing
 * Row-Bot without your data. Open signs the person's browser in with a
 * one-time link; Reset brings it back to its pack.
 */
export function SandboxSettings() {
  const { controller, platform } = useRuntime();
  const [page, setPage] = useState<RowBotSandboxPage | null>(null);
  const [name, setName] = useState('');
  const [pack, setPack] = useState<SandboxPack>('shop');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [confirming, setConfirming] = useState('');

  const starting = page?.items.some((item) => item.status === 'starting');
  useEffect(() => {
    const abort = new AbortController();
    let timer = 0;
    const read = () =>
      controller.rowBotSandboxes(abort.signal).then(
        (next) => {
          if (!next) return;
          setPage(next);
          if (next.items.some((item) => item.status === 'starting'))
            timer = window.setTimeout(read, 1500);
        },
        (cause) => {
          if (!abort.signal.aborted) setError(clientError(cause).message);
        },
      );
    void read();
    return () => {
      abort.abort();
      window.clearTimeout(timer);
    };
  }, [controller, starting]);

  async function run(key: string, work: () => Promise<unknown>) {
    setBusy(key);
    setError('');
    try {
      await work();
      const next = await controller.rowBotSandboxes();
      if (next) setPage(next);
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setBusy('');
      setConfirming('');
    }
  }

  async function open(sandbox: RowBotSandboxView) {
    await run(`open:${sandbox.sandbox_id}`, async () => {
      const link = await controller.rowBotSandboxLink(sandbox.sandbox_id);
      if (link) await platform.openExternal(link.url);
    });
  }

  if (!page)
    return error ? (
      <p role="alert" className="settings-error">
        {error}
      </p>
    ) : (
      <Skeleton label="Checking sandboxes" />
    );
  if (!page.available) return null;
  return (
    <section
      className="settings-snapshot-section stack sandbox-settings"
      aria-labelledby="settings-snapshot-sandboxes"
      data-setting-anchor="sandboxes"
    >
      <header className="settings-snapshot-heading">
        <FlaskConical size={18} aria-hidden />
        <div>
          <h3 id="settings-snapshot-sandboxes">Sandboxes</h3>
          <p>
            A second Row-Bot on made-up data, with its own chats, settings and
            sign-ins: try a workflow before it touches real accounts, film a
            demo, or show Row-Bot to someone. It can’t see your own Row-Bot or
            its files, and it stops when Row-Bot quits.
          </p>
        </div>
      </header>
      {page.items.length > 0 && (
        <ul className="sandbox-list" aria-label="Sandboxes">
          {page.items.map((sandbox) => {
            const status = STATUS[sandbox.status];
            const working = busy.endsWith(sandbox.sandbox_id);
            return (
              <li key={sandbox.sandbox_id} className="sandbox-row">
                <span className="sandbox-name">
                  <strong>{sandbox.name}</strong>
                  <small>{sandbox.pack_name}</small>
                </span>
                <StatusDot
                  tone={status.tone}
                  pulse={sandbox.status === 'starting'}
                  label={status.label}
                  showLabel
                />
                <span className="sandbox-actions">
                  {sandbox.status === 'running' ? (
                    <>
                      <IconButton
                        size="sm"
                        label={`Open ${sandbox.name}`}
                        disabled={working}
                        onClick={() => void open(sandbox)}
                      >
                        <ExternalLink size={16} aria-hidden />
                      </IconButton>
                      <IconButton
                        size="sm"
                        label={`Stop ${sandbox.name}`}
                        disabled={working}
                        onClick={() =>
                          void run(`stop:${sandbox.sandbox_id}`, () =>
                            controller.actOnRowBotSandbox(
                              sandbox.sandbox_id,
                              'stop',
                            ),
                          )
                        }
                      >
                        <Square size={16} aria-hidden />
                      </IconButton>
                    </>
                  ) : (
                    <IconButton
                      size="sm"
                      label={`Start ${sandbox.name}`}
                      disabled={working || sandbox.status === 'starting'}
                      onClick={() =>
                        void run(`start:${sandbox.sandbox_id}`, () =>
                          controller.actOnRowBotSandbox(
                            sandbox.sandbox_id,
                            'start',
                          ),
                        )
                      }
                    >
                      <Play size={16} aria-hidden />
                    </IconButton>
                  )}
                  {confirming === `reset:${sandbox.sandbox_id}` ||
                  confirming === `delete:${sandbox.sandbox_id}` ? (
                    <span
                      className="sandbox-confirm"
                      role="group"
                      aria-label="Confirm"
                    >
                      <span>
                        {confirming.startsWith('reset')
                          ? 'Back to its pack? Its chats and changes go.'
                          : 'Delete this sandbox and everything in it?'}
                      </span>
                      <Button
                        variant={
                          confirming.startsWith('reset')
                            ? 'secondary'
                            : 'danger'
                        }
                        disabled={working}
                        onClick={() =>
                          void run(confirming, () =>
                            confirming.startsWith('reset')
                              ? controller.actOnRowBotSandbox(
                                  sandbox.sandbox_id,
                                  'reset',
                                )
                              : controller.deleteRowBotSandbox(
                                  sandbox.sandbox_id,
                                ),
                          )
                        }
                      >
                        {confirming.startsWith('reset') ? 'Reset' : 'Delete'}
                      </Button>
                      <Button variant="ghost" onClick={() => setConfirming('')}>
                        Cancel
                      </Button>
                    </span>
                  ) : (
                    <>
                      <IconButton
                        size="sm"
                        label={`Reset ${sandbox.name}`}
                        disabled={working}
                        onClick={() =>
                          setConfirming(`reset:${sandbox.sandbox_id}`)
                        }
                      >
                        <RotateCcw size={16} aria-hidden />
                      </IconButton>
                      <IconButton
                        size="sm"
                        label={`Delete ${sandbox.name}`}
                        disabled={working}
                        onClick={() =>
                          setConfirming(`delete:${sandbox.sandbox_id}`)
                        }
                      >
                        <Trash2 size={16} aria-hidden />
                      </IconButton>
                    </>
                  )}
                </span>
                {sandbox.problem && (
                  <small className="sandbox-problem">{sandbox.problem}</small>
                )}
                {!sandbox.problem &&
                  sandbox.needs_model &&
                  sandbox.status === 'running' && (
                    <small className="sandbox-hint">
                      No model yet: open it and sign in to a provider once, so
                      its chats can answer.
                    </small>
                  )}
              </li>
            );
          })}
        </ul>
      )}
      {page.items.length < 5 && (
        <div className="sandbox-new" role="group" aria-label="New sandbox">
          <Field label="Name" layout="row">
            <input
              className="input"
              aria-label="Name"
              placeholder="Demo shop"
              maxLength={60}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </Field>
          <Field label="Starts with" layout="row">
            <Select
              aria-label="Starts with"
              value={pack}
              onChange={(event) => setPack(event.target.value as SandboxPack)}
            >
              {page.packs.map((item) => (
                <option key={item.pack} value={item.pack}>
                  {item.name}
                </option>
              ))}
            </Select>
          </Field>
          <Button
            disabled={busy === 'create'}
            onClick={() =>
              void run('create', async () => {
                await controller.createRowBotSandbox({ name, pack });
                setName('');
              })
            }
          >
            New sandbox
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="settings-error">
          {error}
        </p>
      )}
    </section>
  );
}
