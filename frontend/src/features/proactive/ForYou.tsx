import { useEffect, useState, type CSSProperties, type ReactNode } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Check, ChevronRight, MoreHorizontal } from 'lucide-react';
import type { ProactiveMomentView } from '../../api/types';
import { relativeTime } from '../../ui/format';
import {
  IconButton,
  Menu,
  Skeleton,
  type MenuAction,
} from '../../ui/primitives';
import { useMomentActions } from './moment-actions';
import { introduced, ProactiveIntro } from './ProactiveIntro';
import { useMinute, useProactive, type ProactiveIO } from './proactive-state';
import {
  KIND_TILES,
  actionIcon,
  briefLines,
  currentBrief,
  groupMoments,
  momentTime,
  preparedWords,
} from './words';
import './proactive.css';

/** "in 8 minutes" (to the meeting, or when it's due), or how long it has waited. */
function timeWords(moment: ProactiveMomentView, now: Date): string {
  const due = momentTime(moment);
  if (due && due.getTime() > now.getTime()) return relativeTime(due, now);
  const at = moment.shown_at || moment.created_at;
  return at ? relativeTime(at, now) : '';
}

/**
 * One moment as a Home row: its kind's tile, the line, what was prepared
 * and the time. The row does the primary action (or explains itself); the
 * actions and "…" (Not now, Less like this, Why this?, Done) sit beside it.
 * On a narrow Home the time and what was prepared go under the title, and
 * the actions under them, so the title keeps the row's width.
 */
export function MomentRow({
  moment,
  now,
  onAction,
  onWhy,
  menu,
}: {
  moment: ProactiveMomentView;
  now: Date;
  onAction(action: ProactiveMomentView['actions'][number]): void;
  onWhy(): void;
  menu: MenuAction[];
}) {
  const tile = KIND_TILES[moment.kind];
  const [first] = moment.actions;
  const prepared = preparedWords(moment);
  const time = timeWords(moment, now);
  return (
    <li
      className="overview-row proactive-moment"
      data-urgency={moment.urgency}
      data-moment={moment.moment_id}
    >
      <button
        type="button"
        className="overview-row-button"
        aria-label={[
          moment.title,
          time,
          prepared,
          first ? first.label : 'Why this?',
        ]
          .filter(Boolean)
          .join('. ')}
        onClick={() => (first ? onAction(first) : onWhy())}
      >
        <span className="overview-tile" data-tone={tile.tone} aria-hidden>
          {tile.icon}
        </span>
        <span className="overview-row-text">
          <span className="overview-row-title">{moment.title}</span>
          {(prepared || time) && (
            <span className="overview-row-meta">
              {prepared}
              {time && (
                <span className="proactive-moment-when" aria-hidden>
                  {prepared ? ' · ' : ''}
                  {time}
                </span>
              )}
            </span>
          )}
        </span>
        <span className="overview-row-time" aria-hidden>
          {time}
        </span>
        <ChevronRight className="overview-row-chevron" size={15} aria-hidden />
      </button>
      <div className="overview-row-actions proactive-row-actions">
        {moment.actions.slice(0, 2).map((action) => (
          <IconButton
            key={action.id}
            size="sm"
            label={action.label}
            onClick={() => onAction(action)}
          >
            {actionIcon(action.id, 15)}
          </IconButton>
        ))}
        <Menu
          label={`More for ${moment.title}`}
          hint="More"
          iconOnly
          variant="ghost"
          className="icon-action icon-action-sm"
          actions={menu}
        >
          <MoreHorizontal size={15} aria-hidden />
        </Menu>
      </div>
    </li>
  );
}

/**
 * Home › For you, in place of Today while Proactive is on: the morning brief
 * first, expanded, then Now / Later today / Ready when you are, ending with
 * "That's everything for today." Today's fact rows (an agent at work, the
 * next workflow) join Later today; its memories line is the footer. Expired
 * moments leave quietly; with nothing at all, it doesn't render.
 */
export default function ForYou({
  io,
  now: fixedNow,
  order = 3,
  today = [],
  footer,
}: {
  io?: ProactiveIO | null;
  now?: Date;
  /** Its place in Overview's entrance. */
  order?: number;
  /** Today's plain fact rows, shown under Later today. */
  today?: ReactNode[];
  /** One quiet line under the closing line ("12 memories saved this week"). */
  footer?: string;
}) {
  const { store, view } = useProactive(io);
  const now = useMinute(fixedNow);
  const navigate = useNavigate();
  const [search, setSearch] = useSearchParams();
  const [dismissedIntro, setDismissedIntro] = useState(false);
  const actions = useMomentActions(store, {
    navigate: (to) => navigate(to),
  });
  useEffect(() => store?.watchDrafts(), [store]);
  // A draft or a moment's Why this, opened from Buddy's bubble, the strip or
  // the desktop pet, lands here (`?draft=`, `?why=`).
  const draftParam = search.get('draft');
  const whyParam = search.get('why');
  const openDrafts = actions.drafts;
  const openWhy = actions.why;
  useEffect(() => {
    if ((!draftParam && !whyParam) || !store) return;
    if (draftParam) openDrafts(draftParam === 'all' ? null : draftParam);
    if (whyParam) {
      const listed = store
        .getView()
        .moments?.find((moment) => moment.moment_id === whyParam);
      if (listed) openWhy(listed);
      else
        void store.io
          .moment(whyParam)
          .then((detail) => openWhy(detail.moment))
          .catch(() => undefined);
    }
    const next = new URLSearchParams(search);
    next.delete('draft');
    next.delete('why');
    setSearch(next, { replace: true });
    // Only a new ?draft= or ?why= opens a sheet.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftParam, whyParam, store]);
  const serverIntroduced = view.settings?.introduced;
  useEffect(() => {
    // Answered on this device before the server kept it: tell the server once.
    if (store && serverIntroduced === false && introduced())
      void store
        .settingsChanged(() => store.io.changeSettings({ introduced: true }))
        .catch(() => undefined);
  }, [store, serverIntroduced]);
  const style = { '--overview-order': order } as CSSProperties;
  if (!store || view.unavailable) return <>{actions.sheets}</>;
  if (!view.settings || !view.moments)
    return view.error ? (
      <section className="overview-section overview-cell" style={style}>
        <p className="overview-error" role="status">
          Couldn’t read For you just now. Monitor may say more.
        </p>
      </section>
    ) : (
      <section
        className="overview-section overview-cell"
        style={style}
        aria-labelledby="overview-for-you"
        aria-busy="true"
      >
        <header className="overview-section-title">
          <h3 id="overview-for-you">For you</h3>
        </header>
        <Skeleton label="Reading For you" />
      </section>
    );
  const settings = view.settings;
  // Once per person: the server keeps whether it was answered (any device).
  const intro = !dismissedIntro && !settings.introduced && !introduced();
  if (settings.level === 'off' && !intro) return <>{actions.sheets}</>;
  const brief = currentBrief(view.moments, now);
  const groups = groupMoments(view.moments, now).map((group) => ({
    ...group,
    // Moments carried in the brief show there, unless they are Now.
    moments:
      brief && group.id !== 'now'
        ? group.moments.filter((moment) => moment.surface !== 'brief')
        : group.moments,
  }));
  const later = groups.find((group) => group.id === 'later');
  const shown = groups.filter(
    (group) => group.moments.length || (group.id === 'later' && today.length),
  );
  if (!later && today.length)
    shown.push({ id: 'later', title: 'Later today', moments: [] });
  const drafts = view.drafts?.length ?? 0;
  const empty = !brief && !shown.length;
  if (empty && !intro) return <>{actions.sheets}</>;
  const lines = brief ? briefLines(brief.body) : [];
  const ordered = ['now', 'later', 'ready']
    .map((id) => shown.find((group) => group.id === id))
    .filter((group): group is (typeof shown)[number] => Boolean(group));
  return (
    <section
      id="for-you"
      className="overview-section overview-cell proactive-for-you"
      style={style}
      aria-labelledby="overview-for-you"
    >
      <header className="overview-section-title">
        <h3 id="overview-for-you">For you</h3>
        {drafts > 0 && (
          <button
            type="button"
            className="overview-section-link"
            onClick={() => openDrafts(null)}
          >
            Drafts <span className="proactive-count">{drafts}</span>
            <ChevronRight size={13} aria-hidden />
          </button>
        )}
      </header>
      {intro && (
        <ProactiveIntro
          store={store}
          settings={settings}
          onDone={() => setDismissedIntro(true)}
        />
      )}
      {brief && (
        <div
          className="proactive-brief"
          role="region"
          aria-labelledby={`brief-${brief.moment_id}`}
        >
          <div className="proactive-brief-head">
            <span className="overview-tile" data-tone="accent" aria-hidden>
              {KIND_TILES.brief.icon}
            </span>
            <h4 id={`brief-${brief.moment_id}`}>{brief.title}</h4>
            <Menu
              label={`More for ${brief.title}`}
              hint="More"
              iconOnly
              variant="ghost"
              className="icon-action icon-action-sm"
              actions={actions.menu(brief)}
            >
              <MoreHorizontal size={15} aria-hidden />
            </Menu>
          </div>
          {lines.length > 0 && (
            <ul className="proactive-brief-lines">
              {lines.map((line, index) => (
                <li key={index}>{line}</li>
              ))}
            </ul>
          )}
          <p className="proactive-brief-end">That’s everything for today.</p>
        </div>
      )}
      {ordered.map((group) => (
        <div
          key={group.id}
          className="proactive-group"
          data-group={group.id}
          role="group"
          aria-labelledby={`for-you-${group.id}`}
        >
          <h4 id={`for-you-${group.id}`} className="proactive-group-title">
            {group.title}
          </h4>
          <ul className="overview-rows">
            {group.moments.map((moment) => (
              <MomentRow
                key={moment.moment_id}
                moment={moment}
                now={now}
                onAction={(action) => actions.perform(moment, action)}
                onWhy={() => actions.why(moment)}
                menu={actions.menu(moment)}
              />
            ))}
            {group.id === 'later' && today}
          </ul>
        </div>
      ))}
      {!empty && (
        <p className="proactive-closing">
          <Check size={14} aria-hidden /> That’s everything for today.
        </p>
      )}
      {footer && <p className="proactive-footer">{footer}</p>}
      {view.error && (
        <p className="overview-error" role="status">
          Couldn’t read For you just now. Monitor may say more.
        </p>
      )}
      {actions.sheets}
    </section>
  );
}
