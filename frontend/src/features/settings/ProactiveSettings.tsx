import { useEffect, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  Ban,
  Bell,
  Clock,
  House,
  Pencil,
  Smartphone,
  Smile,
  Trash2,
  UserRoundX,
  X,
} from 'lucide-react';
import type {
  ProactiveActivityEntry,
  ProactiveMomentView,
  ProactiveRuleView,
  ProactiveRuleWrite,
  ProactiveSettingsChange,
  ProactiveSettingsView,
  TaskDeliveryChannel,
} from '../../api/types';
import { clientError } from '../../api/errors';
import {
  Button,
  ErrorState,
  IconButton,
  Input,
  Segmented,
  Select,
  Skeleton,
  Toggle,
} from '../../ui/primitives';
import { useNotify } from '../../ui/overlays';
import {
  SettingsGroup,
  SettingsItem,
  SettingsStatus,
  StatusLine,
} from './anatomy';
import { useWorkspaceActions } from '../shell/workspace-actions';
import {
  useProactive,
  type ProactiveIO,
  type ProactiveStore,
} from '../proactive/proactive-state';
import {
  EXAMPLE_DAY,
  KIND_TILES,
  KIND_WORDS,
  LEVEL_LINES,
  PRIVACY_LINE,
  hourWords,
  isLive,
  type MomentKind,
  type ProactiveLevel,
} from '../proactive/words';
import { When } from '../../ui/When';
import { parseTimestamp } from '../../ui/format';
import '../proactive/proactive.css';

/** Quarter hours for the quiet-hours selects. */
const QUARTERS = Array.from({ length: 96 }, (_, index) => {
  const hour = String(Math.floor(index / 4)).padStart(2, '0');
  const minute = String((index % 4) * 15).padStart(2, '0');
  return `${hour}:${minute}`;
});

function TimeSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange(value: string): void;
}) {
  const options = QUARTERS.includes(value) ? QUARTERS : [value, ...QUARTERS];
  return (
    <Select
      aria-label={label}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    >
      {options.map((time) => (
        <option key={time} value={time}>
          {time}
        </option>
      ))}
    </Select>
  );
}

const WHERE: Record<
  'bubble' | 'home' | 'phone',
  { icon: ReactNode; words: string }
> = {
  bubble: { icon: <Smile size={15} aria-hidden />, words: 'Buddy’s bubble' },
  home: { icon: <House size={15} aria-hidden />, words: 'Home only' },
  phone: { icon: <Smartphone size={15} aria-hidden />, words: 'Your phone' },
};

/** "An example day at Balanced": what each level would bring up, and where. */
function LevelExample({ level }: { level: ProactiveLevel }) {
  const day = EXAMPLE_DAY[level];
  const name = level[0].toUpperCase() + level.slice(1);
  return (
    <div className="proactive-example" aria-live="polite">
      <p className="proactive-example-head">
        {level === 'off' ? 'At Off' : `An example day at ${name}`}
      </p>
      {day.length ? (
        <ul>
          {day.map((row) => (
            <li key={`${row.time}${row.line}`}>
              <span className="proactive-example-icon">
                {WHERE[row.where].icon}
              </span>
              <span className="proactive-example-time">{row.time}</span>
              <span className="proactive-example-line">{row.line}</span>
              <span className="proactive-example-where">
                {WHERE[row.where].words}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="proactive-quiet-line">
          Nothing. Row-Bot wouldn’t check anything. Home shows Today as before.
        </p>
      )}
    </div>
  );
}

function ruleIcon(rule: ProactiveRuleView) {
  if (rule.type === 'never_about') return <UserRoundX size={15} aria-hidden />;
  if (rule.type === 'not_at_time') return <Clock size={15} aria-hidden />;
  return <Ban size={15} aria-hidden />;
}

function ruleWrite(rule: ProactiveRuleView): ProactiveRuleWrite {
  return {
    type: rule.type,
    kind: rule.kind,
    topic: rule.topic,
    hour: rule.hour,
  };
}

/** One rule being edited in place: the person, the kind or the hour. */
function RuleEditor({
  rule,
  kinds,
  onSave,
  onCancel,
}: {
  rule: ProactiveRuleView;
  kinds: ProactiveSettingsView['kinds'];
  onSave(write: ProactiveRuleWrite): Promise<void>;
  onCancel(): void;
}) {
  const [write, setWrite] = useState(ruleWrite(rule));
  const [error, setError] = useState('');
  return (
    <form
      className="proactive-rule-editor"
      onSubmit={(event) => {
        event.preventDefault();
        onSave(write).catch((cause) => setError(clientError(cause).message));
      }}
    >
      {rule.type === 'never_about' ? (
        <Input
          aria-label="About"
          value={write.topic ?? ''}
          maxLength={120}
          onChange={(event) =>
            setWrite({ ...write, topic: event.target.value })
          }
        />
      ) : (
        <Select
          aria-label="Kind"
          value={write.kind ?? ''}
          onChange={(event) =>
            setWrite({ ...write, kind: event.target.value as MomentKind })
          }
        >
          {kinds.map((kind) => (
            <option key={kind.id} value={kind.id}>
              {kind.label}
            </option>
          ))}
        </Select>
      )}
      {rule.type === 'not_at_time' && (
        <Select
          aria-label="Around"
          value={String(write.hour ?? 0)}
          onChange={(event) =>
            setWrite({ ...write, hour: Number(event.target.value) })
          }
        >
          {Array.from({ length: 24 }, (_, hour) => (
            <option key={hour} value={hour}>
              {hourWords(hour)}
            </option>
          ))}
        </Select>
      )}
      <Button variant="ghost" className="small" onClick={onCancel}>
        Cancel
      </Button>
      <Button type="submit" variant="primary" className="small">
        Save
      </Button>
      {error && (
        <p className="proactive-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

type Filter = 'all' | 'shown' | 'silent' | 'planned';

/** The day an entry belongs to, in words: Today, Yesterday, or the date. */
function dayWords(at: string, now: Date) {
  const date = parseTimestamp(at);
  if (!date) return '';
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (date.toDateString() === now.toDateString()) return 'Today';
  if (date.toDateString() === yesterday.toDateString()) return 'Yesterday';
  return date.toLocaleDateString(undefined, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
  });
}

/**
 * The Activity log: every check, silent ones too, newest first, by day; the
 * planned moments (waiting for a break) with Take over and Cancel.
 */
function Activity({
  store,
  planned,
}: {
  store: ProactiveStore;
  planned: ProactiveMomentView[];
}) {
  const notify = useNotify();
  const workspace = useWorkspaceActions();
  const [filter, setFilter] = useState<Filter>('all');
  const [items, setItems] = useState<ProactiveActivityEntry[] | null>(null);
  const [next, setNext] = useState<number | null>(null);
  const [error, setError] = useState('');
  const { view } = useProactive(store.io);
  // Re-read the newest page whenever Proactive changes.
  const revision = view.moments;
  useEffect(() => {
    let active = true;
    store.io
      .activity(undefined, 20)
      .then((page) => {
        if (!active) return;
        setItems(page.items);
        setNext(page.next_before);
        setError('');
      })
      .catch((cause) => {
        if (active) setError(clientError(cause).message);
      });
    return () => {
      active = false;
    };
  }, [store, revision]);
  async function earlier() {
    if (next === null) return;
    const page = await store.io.activity(next, 20);
    setItems((current) => [...(current ?? []), ...page.items]);
    setNext(page.next_before);
  }
  const now = new Date();
  const shown =
    filter === 'planned'
      ? []
      : (items ?? []).filter(
          (item) =>
            filter === 'all' ||
            (filter === 'shown' && item.kind === 'moment') ||
            (filter === 'silent' && item.kind === 'pass'),
        );
  const days: { day: string; entries: ProactiveActivityEntry[] }[] = [];
  for (const entry of shown) {
    const day = dayWords(entry.at, now);
    const last = days[days.length - 1];
    if (last?.day === day) last.entries.push(entry);
    else days.push({ day, entries: [entry] });
  }
  const cancel = (moment: ProactiveMomentView) =>
    store
      .feedback(moment.moment_id, { action: 'dismiss' })
      .then(() => notify('Planned moment cancelled'))
      .catch((cause) => notify(clientError(cause).message, 'warning'));
  return (
    <SettingsGroup
      title="Activity"
      anchor="activity"
      note="Every check, silent ones too. Kept 30 days on this computer."
      meta={
        <Segmented
          label="Show"
          size="sm"
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: 'All' },
            { value: 'shown', label: 'Shown' },
            { value: 'silent', label: 'Silent' },
            { value: 'planned', label: 'Planned' },
          ]}
        />
      }
    >
      {error && (
        <p className="proactive-error" role="alert">
          {error}
        </p>
      )}
      {(filter === 'all' || filter === 'planned') && planned.length > 0 && (
        <div className="proactive-activity-day">
          <h4>Planned</h4>
          <ul className="proactive-activity">
            {planned.map((moment) => (
              <li key={moment.moment_id}>
                <span className="proactive-activity-time">
                  {moment.due_at ? (
                    <When value={moment.due_at} fallback="" />
                  ) : (
                    '—'
                  )}
                </span>
                <span className="proactive-activity-text">
                  {moment.title} · planned
                </span>
                <span className="proactive-activity-actions">
                  {workspace?.newChat && (
                    <Button
                      variant="ghost"
                      className="small"
                      onClick={() => {
                        workspace.newChat?.(
                          `Help me with this now: ${moment.title}${moment.body ? `\n\n${moment.body}` : ''}`,
                        );
                        void cancel(moment);
                      }}
                    >
                      Take over
                    </Button>
                  )}
                  <IconButton
                    size="sm"
                    label="Cancel"
                    onClick={() => void cancel(moment)}
                  >
                    <X size={15} aria-hidden />
                  </IconButton>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {items === null && !error && <Skeleton label="Reading activity" />}
      {days.map(({ day, entries }) => (
        <div key={day} className="proactive-activity-day">
          <h4>{day}</h4>
          <ul className="proactive-activity">
            {entries.map((entry) => (
              <li key={entry.entry_id} data-kind={entry.kind}>
                <span className="proactive-activity-time">
                  {parseTimestamp(entry.at)?.toLocaleTimeString(undefined, {
                    hour: 'numeric',
                    minute: '2-digit',
                  })}
                </span>
                <span className="proactive-activity-text">{entry.summary}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {items !== null &&
        !shown.length &&
        !(filter !== 'shown' && filter !== 'silent' && planned.length) && (
          <p className="proactive-quiet-line">
            No checks yet. Row-Bot’s first check runs a few minutes after you
            turn Proactive on.
          </p>
        )}
      {next !== null && filter !== 'planned' && (
        <Button
          variant="ghost"
          className="small"
          onClick={() => void earlier()}
        >
          Show earlier
        </Button>
      )}
    </SettingsGroup>
  );
}

/**
 * Settings › Proactive: how much (the level, with an example day), when
 * (quiet hours, desktop activity), sources, what to bring up, away from
 * your desk, your rules and the Activity log.
 */
export default function ProactiveSettings({ io }: { io?: ProactiveIO | null }) {
  const { store, view } = useProactive(io);
  const notify = useNotify();
  const [editing, setEditing] = useState('');
  const [channels, setChannels] = useState<TaskDeliveryChannel[]>([]);
  useEffect(() => {
    let active = true;
    store?.io
      .channels?.()
      .then((list) => {
        if (active) setChannels(list);
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [store]);
  if (!store || view.unavailable)
    return (
      <ErrorState title="Proactive isn’t available">
        This Row-Bot server doesn’t have Proactive yet. Update it to use this
        page.
      </ErrorState>
    );
  const settings = view.settings;
  if (!settings)
    return view.error ? (
      <ErrorState
        title="Couldn’t read Proactive settings."
        action={
          <Button variant="ghost" onClick={() => void store.reload()}>
            Try again
          </Button>
        }
      >
        {view.error}
      </ErrorState>
    ) : (
      <Skeleton label="Loading Proactive settings" />
    );
  const off = settings.level === 'off';
  const change = (
    patch: ProactiveSettingsChange,
    message?: string,
    undo?: ProactiveSettingsChange,
  ) =>
    store
      .settingsChanged(() => store.io.changeSettings(patch))
      .then(() => {
        if (message)
          notify(
            message,
            undefined,
            undo && {
              label: 'Undo',
              onAction: () =>
                void store.settingsChanged(() => store.io.changeSettings(undo)),
            },
          );
      })
      .catch(() => notify('Couldn’t save. Try again.', 'danger'));
  const levelName = (id: ProactiveLevel) =>
    settings.levels.find((item) => item.id === id)?.label ?? id;
  const planned = (view.moments ?? []).filter(
    (moment) => moment.state === 'pending' && isLive(moment, new Date()),
  );
  const urgent = settings.kinds
    .filter((kind) => kind.urgent)
    .map((kind) => kind.id);
  const offNote = off ? 'Proactive is off. Nothing is checked.' : undefined;
  return (
    <div className="proactive-settings">
      <SettingsStatus
        tone={off ? 'neutral' : 'accent'}
        more={
          off ? ['nothing is checked'] : [LEVEL_LINES[settings.level].daily]
        }
      >
        {levelName(settings.level)}
      </SettingsStatus>
      <SettingsGroup title="How much" anchor="level">
        <SettingsItem
          label="Proactive level"
          help={LEVEL_LINES[settings.level].line}
          status={
            LEVEL_LINES[settings.level].daily && (
              <StatusLine>{LEVEL_LINES[settings.level].daily}</StatusLine>
            )
          }
          bind={false}
          layout="stacked"
          control={
            <Segmented
              label="Proactive level"
              value={settings.level}
              onChange={(level) =>
                void change({ level }, `Proactive set to ${levelName(level)}`, {
                  level: settings.level,
                })
              }
              options={settings.levels.map((item) => ({
                value: item.id,
                label: item.label,
              }))}
            />
          }
        >
          <LevelExample level={settings.level} />
        </SettingsItem>
      </SettingsGroup>
      <div
        data-off={off ? 'true' : undefined}
        className="proactive-settings-rest"
      >
        <SettingsGroup title="When" anchor="when" note={offNote}>
          <SettingsItem
            anchor="quiet-hours"
            label="Quiet hours"
            help="Nothing reaches you in these hours. Focus and Do Not Disturb are always respected."
            bind={false}
            control={
              <span className="proactive-hours">
                <TimeSelect
                  label="Quiet hours start"
                  value={settings.quiet_start}
                  onChange={(quiet_start) => void change({ quiet_start })}
                />
                <span aria-hidden>to</span>
                <TimeSelect
                  label="Quiet hours end"
                  value={settings.quiet_end}
                  onChange={(quiet_end) => void change({ quiet_end })}
                />
              </span>
            }
          />
          <SettingsItem
            anchor="desktop-context"
            label="Desktop context for timing"
            help={settings.desktop_context_privacy}
            layout="inline"
            control={
              <Toggle
                label="Desktop context for timing"
                checked={settings.desktop_context}
                onChange={(event) =>
                  void change({ desktop_context: event.target.checked })
                }
              />
            }
          />
        </SettingsGroup>
        <SettingsGroup
          title="Sources"
          anchor="sources"
          note="What Row-Bot may look at. Unconnected apps are skipped."
        >
          {settings.sources.map((source) => (
            <SettingsItem
              key={source.id}
              label={source.label}
              help={source.privacy}
              layout="inline"
              control={
                <Toggle
                  label={source.label}
                  checked={source.on}
                  onChange={(event) =>
                    void change({
                      sources: { [source.id]: event.target.checked },
                    })
                  }
                />
              }
            />
          ))}
          <p className="proactive-privacy proactive-privacy-row">
            {PRIVACY_LINE}
          </p>
        </SettingsGroup>
        <SettingsGroup title="What to bring up" anchor="kinds">
          {settings.kinds.map((kind) => (
            <SettingsItem
              key={kind.id}
              icon={KIND_TILES[kind.id].icon}
              tone="neutral"
              label={kind.label}
              layout="inline"
              status={
                kind.quieted && (
                  <StatusLine
                    tone="neutral"
                    action={
                      <button
                        type="button"
                        className="proactive-text-link"
                        onClick={() =>
                          void store
                            .settingsChanged(() =>
                              store.io.changeSettings({
                                kinds: { [kind.id]: false },
                              }),
                            )
                            .then(() =>
                              change(
                                { kinds: { [kind.id]: true } },
                                `${kind.label} can speak again`,
                              ),
                            )
                        }
                      >
                        Bring back
                      </button>
                    }
                  >
                    Quieted: you left the last few alone
                  </StatusLine>
                )
              }
              control={
                <Toggle
                  label={kind.label}
                  checked={kind.on}
                  onChange={(event) =>
                    void change({ kinds: { [kind.id]: event.target.checked } })
                  }
                />
              }
            />
          ))}
        </SettingsGroup>
        <SettingsGroup title="Away from your desk" anchor="urgent">
          <SettingsItem
            icon={<Smartphone size={16} aria-hidden />}
            tone="neutral"
            label="Phone notifications"
            help="Only when this computer is idle or locked, and only for what can’t wait. What they show is up to Show details in notifications, in Devices & remote access."
            trailing={
              <Link
                className="proactive-text-link"
                to="/settings/access#devices"
              >
                Devices
              </Link>
            }
          />
          <SettingsItem
            label="Urgent in chat apps"
            help={
              channels.length
                ? 'Only these kinds, only when you’re away. The message goes through that app’s servers: one line, no excerpt.'
                : 'Connect a chat app (Telegram, Slack…) in Apps to hear urgent things there when you’re away.'
            }
            control={
              <Select
                value={settings.channel}
                disabled={!channels.length}
                onChange={(event) =>
                  void change({ channel: event.target.value })
                }
              >
                <option value="">None</option>
                {channels.map((channel) => (
                  <option key={channel.id} value={channel.id}>
                    {channel.label}
                  </option>
                ))}
              </Select>
            }
          >
            {settings.channel && (
              <fieldset className="proactive-urgent-kinds">
                <legend className="visually-hidden">Urgent kinds</legend>
                {settings.kinds
                  .filter(
                    (kind) =>
                      kind.id !== 'brief' && kind.id !== 'workflow_offer',
                  )
                  .map((kind) => (
                    <label key={kind.id} className="proactive-check">
                      <input
                        type="checkbox"
                        checked={kind.urgent}
                        onChange={(event) =>
                          void change({
                            urgent_kinds: event.target.checked
                              ? [...urgent, kind.id]
                              : urgent.filter((id) => id !== kind.id),
                          })
                        }
                      />
                      {KIND_WORDS[kind.id]}
                    </label>
                  ))}
              </fieldset>
            )}
          </SettingsItem>
        </SettingsGroup>
        <SettingsGroup
          title="Your rules"
          anchor="rules"
          meta={
            settings.rules.length ? <span>{settings.rules.length}</span> : null
          }
        >
          {settings.rules.length ? (
            <ul className="proactive-rules" aria-label="Your rules">
              {settings.rules.map((rule) => (
                <li key={rule.rule_id}>
                  <span className="proactive-rule-icon">{ruleIcon(rule)}</span>
                  {editing === rule.rule_id ? (
                    <RuleEditor
                      rule={rule}
                      kinds={settings.kinds}
                      onCancel={() => setEditing('')}
                      onSave={async (write) => {
                        await store.settingsChanged(() =>
                          store.io.changeRule(rule.rule_id, write),
                        );
                        setEditing('');
                        notify('Rule saved');
                      }}
                    />
                  ) : (
                    <>
                      <span className="proactive-rule-words">{rule.words}</span>
                      <IconButton
                        size="sm"
                        label={`Edit: ${rule.words}`}
                        onClick={() => setEditing(rule.rule_id)}
                      >
                        <Pencil size={15} aria-hidden />
                      </IconButton>
                      <IconButton
                        size="sm"
                        label={`Delete: ${rule.words}`}
                        onClick={() =>
                          void store
                            .settingsChanged(() =>
                              store.io.deleteRule(rule.rule_id),
                            )
                            .then(() =>
                              notify('Rule deleted', undefined, {
                                label: 'Undo',
                                onAction: () =>
                                  void store.settingsChanged(() =>
                                    store.io.addRule(ruleWrite(rule)),
                                  ),
                              }),
                            )
                            .catch(() =>
                              notify('Couldn’t save. Try again.', 'danger'),
                            )
                        }
                      >
                        <Trash2 size={15} aria-hidden />
                      </IconButton>
                    </>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="proactive-quiet-line">
              No rules yet. Choose Less like this on anything Row-Bot brings up,
              and the rule shows here.
            </p>
          )}
        </SettingsGroup>
      </div>
      <Activity store={store} planned={planned} />
      <p className="proactive-footnote">
        <Bell size={13} aria-hidden /> Nothing proactive is ever sent or changed
        without you: every side effect goes through your approval level.
      </p>
    </div>
  );
}
