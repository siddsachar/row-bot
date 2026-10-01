import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react';
import {
  Bot,
  CornerDownLeft,
  FileText,
  MessageSquare,
  Monitor,
  Moon,
  Plug,
  Search,
  Settings,
  Sun,
  ToggleLeft,
  ToggleRight,
  Workflow,
} from 'lucide-react';
import { useClientState, useRuntime } from '../../runtime';
import { Kbd } from '../../ui/primitives';
import { useOptionalTheme } from '../../ui/theme';
import type { Appearance } from '../../ui/theme-model';
import type { ConversationView, SearchHit } from '../../api/types';
import { fuzzyScore } from './fuzzy';
import { ConversationGlyph } from './ConversationGlyph';
import {
  conversationKinds,
  type ConversationKind,
} from './conversation-groups';
import { PALETTE_INTENTS, intentScore, switchRequest } from './palette-intents';
import type { PaletteSwitchAction } from './palette-switches';

/** Type words a query can use to find threads ("design", "code"). */
const KIND_WORDS: Record<ConversationKind, string> = {
  designer: 'design designs',
  code: 'code coding workspace',
  workflow: 'workflow workflows',
};
import {
  settingsKeywords,
  settingsLeaves,
  settingsRedirects,
  settingsRowHref,
  settingsRows,
} from '../settings/model';

export type PaletteGroup =
  | 'Actions'
  | 'Conversations'
  | 'Messages'
  | 'Commands'
  | 'Settings'
  | 'Agents'
  | 'Workflows';

export type PaletteCommand = {
  id: string;
  label: string;
  keywords?: string;
  icon?: ReactNode;
  shortcut?: string;
  run: () => void;
};

export type PaletteAgent = { id: string; label: string; description?: string };

type Item = {
  id: string;
  group: PaletteGroup;
  label: string;
  detail?: string;
  icon: ReactNode;
  shortcut?: string;
  score: number;
  /** Where a navigation result opens; one result per place. */
  href?: string;
  run: () => void;
};

/** A one-line snippet: Markdown markers and line breaks removed. */
export function plainSnippet(text: string): string {
  return text
    .replace(/```[a-z]*|`/gi, '')
    .replace(/(\*\*|__|~~)/g, '')
    .replace(/^\s*(#{1,6}|>|[-*+]|\d+\.)\s+/gm, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Words people search for that are not in a settings page's name. */
/** Former page names still find their page ("Open Utilities settings"). */
const LEGACY_SETTING_NAMES: Record<string, string> = {
  knowledge: 'Knowledge',
  tools: 'Utilities',
  profiles: 'Profiles',
  data: 'Migration',
};

const GROUP_ORDER: readonly PaletteGroup[] = [
  'Conversations',
  'Actions',
  'Commands',
  'Agents',
  'Workflows',
  'Settings',
  'Messages',
];

/** fuzzyMatch scores a literal substring at 1000 or more (less field weight). */
const LITERAL_SCORE = 850;

const LIMITS: Record<PaletteGroup, number> = {
  Actions: 5,
  Conversations: 8,
  Messages: 6,
  Commands: 8,
  Settings: 6,
  Agents: 5,
  Workflows: 5,
};

/** The device's appearance as ⌘K actions ("dark mode"). */
const APPEARANCES: {
  value: Appearance;
  label: string;
  name: string;
  icon: ReactNode;
  phrases: readonly string[];
}[] = [
  {
    value: 'dark',
    label: 'Use dark appearance',
    name: 'Dark',
    icon: <Moon size={16} />,
    phrases: ['dark mode', 'dark theme', 'night mode', 'choose dark mode'],
  },
  {
    value: 'light',
    label: 'Use light appearance',
    name: 'Light',
    icon: <Sun size={16} />,
    phrases: ['light mode', 'light theme', 'day mode', 'choose light mode'],
  },
  {
    value: 'system',
    label: 'Follow the system appearance',
    name: 'System',
    icon: <Monitor size={16} />,
    phrases: [
      'system appearance',
      'system theme',
      'follow system',
      'auto theme',
    ],
  },
];

/**
 * One search across conversations (design and code threads included),
 * commands, settings, agents and what people mean (intents, settings
 * switches with their state, appearance), plus a full-text search of message
 * history. Raycast-style: a single field, grouped results, keyboard first.
 * Enter runs a result only when it matches what was typed; one that only
 * matches scattered letters must be chosen with the arrow keys (U25).
 */
export default function CommandPalette({
  commands,
  loadAgents,
  loadWorkflows,
  loadSwitches,
  onOpenConversation,
  onOpenSearchHit,
  onOpenSetting,
  onStartAgent,
  onOpenWorkflow,
  onClose,
}: {
  commands: PaletteCommand[];
  /** Read once when the palette opens; agents join the results. */
  loadAgents?: (signal: AbortSignal) => Promise<PaletteAgent[]>;
  /** Read once when the palette opens; saved workflows join the results. */
  loadWorkflows?: (signal: AbortSignal) => Promise<PaletteAgent[]>;
  /** Read on the first letters typed; settings switches with their state. */
  loadSwitches?: (signal: AbortSignal) => Promise<PaletteSwitchAction[]>;
  onOpenConversation: (conversation: ConversationView) => void;
  onOpenSearchHit: (hit: SearchHit) => void;
  onOpenSetting: (href: string) => void;
  onStartAgent?: (agent: PaletteAgent) => void;
  /** Opens the workflow's runs, where Run now starts it. */
  onOpenWorkflow?: (workflow: PaletteAgent) => void;
  /** After an action done in place (an appearance change). */
  onClose?: () => void;
}) {
  const state = useClientState();
  const { controller } = useRuntime();
  const theme = useOptionalTheme();
  const [query, setQuery] = useState('');
  // The result Enter runs when it was chosen with the arrows or the pointer;
  // null follows the best result.
  const [chosen, setChosen] = useState<number | null>(null);
  const listId = useId();
  const listRef = useRef<HTMLDivElement>(null);
  // Results follow the typed query synchronously so Enter never runs a
  // result computed for an earlier query.
  const trimmed = query.trim();
  const [agents, setAgents] = useState<PaletteAgent[]>([]);
  useEffect(() => {
    if (!loadAgents) return;
    const abort = new AbortController();
    loadAgents(abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setAgents(value);
      })
      .catch(() => undefined);
    return () => abort.abort();
  }, [loadAgents]);
  const [workflows, setWorkflows] = useState<PaletteAgent[]>([]);
  useEffect(() => {
    if (!loadWorkflows) return;
    const abort = new AbortController();
    loadWorkflows(abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setWorkflows(value);
      })
      .catch(() => undefined);
    return () => abort.abort();
  }, [loadWorkflows]);
  const [switches, setSwitches] = useState<PaletteSwitchAction[] | null>(null);
  const typing = trimmed.length > 0;
  useEffect(() => {
    if (!loadSwitches || !typing) return;
    const abort = new AbortController();
    loadSwitches(abort.signal)
      .then(
        (value) => !abort.signal.aborted && setSwitches(value),
        () => !abort.signal.aborted && setSwitches([]),
      )
      .catch(() => undefined);
    return () => abort.abort();
  }, [loadSwitches, typing]);
  // Settings are still being read: say so rather than "No results".
  const readingSettings = Boolean(loadSwitches) && typing && switches === null;

  // Full-text history search runs after a short pause in typing.
  useEffect(() => {
    if (trimmed.length < 2) {
      void controller.searchLibrary('');
      return;
    }
    const timer = window.setTimeout(
      () => void controller.searchLibrary(trimmed).catch(() => undefined),
      220,
    );
    return () => window.clearTimeout(timer);
  }, [controller, trimmed]);
  useEffect(() => () => void controller.searchLibrary(''), [controller]);

  const appearance = theme?.preference.appearance;
  const update = theme?.update;
  const items = useMemo(() => {
    const results: Item[] = [];
    const topLevel = state.conversations.filter(
      (row) => !row.parent_conversation_id,
    );
    if (!trimmed) {
      topLevel.slice(0, 5).forEach((row, index) =>
        results.push({
          id: `conversation:${row.id}`,
          group: 'Conversations',
          label: row.title || 'Untitled conversation',
          icon: <ConversationGlyph row={row} />,
          score: -index,
          run: () => onOpenConversation(row),
        }),
      );
      commands.forEach((command, index) =>
        results.push({
          id: `command:${command.id}`,
          group: 'Commands',
          label: command.label,
          icon: command.icon ?? <CornerDownLeft size={16} />,
          shortcut: command.shortcut,
          score: -index,
          run: command.run,
        }),
      );
    } else {
      // What people mean: "connect a model", "phone", "connect telegram".
      for (const intent of PALETTE_INTENTS) {
        const literal = fuzzyScore(trimmed, intent.label);
        const score = Math.max(
          intentScore(trimmed, intent.phrases) ?? -Infinity,
          literal !== null && literal >= LITERAL_SCORE ? literal : -Infinity,
        );
        if (score > -Infinity)
          results.push({
            id: `intent:${intent.id}`,
            group: 'Actions',
            label: intent.label,
            detail: intent.detail,
            icon: intent.id.startsWith('connect-') ? (
              <Plug size={16} />
            ) : (
              <Settings size={16} />
            ),
            score,
            href: intent.href,
            run: () => onOpenSetting(intent.href),
          });
      }
      // Settings switches with their state: "turn on developer tools".
      const asked = switchRequest(trimmed);
      for (const option of switches ?? []) {
        const score = intentScore(asked.rest, [
          option.label,
          ...option.phrases,
        ]);
        if (score === null) continue;
        const already = asked.want === option.on;
        const next = asked.want ?? !option.on;
        results.push({
          id: `switch:${option.id}`,
          group: 'Actions',
          label: already
            ? `${option.label} ${option.verb} already ${option.on ? 'on' : 'off'}`
            : `Turn ${next ? 'on' : 'off'} ${option.label}`,
          detail: already
            ? 'Open its setting'
            : `Now ${option.on ? 'on' : 'off'}`,
          icon: option.on ? (
            <ToggleRight size={16} />
          ) : (
            <ToggleLeft size={16} />
          ),
          // Saying on or off names it more surely than its name alone.
          score: score + (asked.want === null ? 0 : 100),
          run: already
            ? () => onOpenSetting(option.href)
            : () => option.set(next),
        });
      }
      // This device's appearance: "dark mode".
      if (appearance && update)
        for (const option of APPEARANCES) {
          const score = intentScore(trimmed, option.phrases);
          if (score === null) continue;
          const current = APPEARANCES.find((item) => item.value === appearance);
          const inUse = option.value === appearance;
          results.push({
            id: `appearance:${option.value}`,
            group: 'Actions',
            label: inUse ? `${option.name} appearance is in use` : option.label,
            detail: `Now ${current?.name ?? 'System'}`,
            icon: option.icon,
            score,
            run: inUse
              ? () => onOpenSetting('/settings/appearance#theme')
              : () => {
                  update({ appearance: option.value });
                  onClose?.();
                },
          });
        }
      for (const row of topLevel) {
        const score = fuzzyScore(
          trimmed,
          row.title || 'Untitled conversation',
          conversationKinds(row)
            .map((kind) => KIND_WORDS[kind])
            .join(' '),
        );
        if (score !== null)
          results.push({
            id: `conversation:${row.id}`,
            group: 'Conversations',
            label: row.title || 'Untitled conversation',
            icon: <ConversationGlyph row={row} />,
            // An equally good command or setting wins the Enter key.
            score: score - 5,
            run: () => onOpenConversation(row),
          });
      }
      for (const command of commands) {
        const score = fuzzyScore(trimmed, command.label, command.keywords);
        if (score !== null)
          results.push({
            id: `command:${command.id}`,
            group: 'Commands',
            label: command.label,
            icon: command.icon ?? <CornerDownLeft size={16} />,
            shortcut: command.shortcut,
            score,
            run: command.run,
          });
      }
      for (const leaf of settingsLeaves) {
        const legacy = LEGACY_SETTING_NAMES[leaf.id];
        const aliases = Object.entries(settingsRedirects)
          .filter(([, target]) => target.leaf === leaf.id)
          .map(([alias]) => alias)
          .join(' ');
        const score = fuzzyScore(
          trimmed,
          leaf.label,
          `Open ${leaf.label} settings`,
          legacy ? `Open ${legacy} settings` : undefined,
          `${leaf.category} ${settingsKeywords[leaf.id]} ${aliases}`,
        );
        if (score !== null)
          results.push({
            id: `setting:${leaf.id}`,
            group: 'Settings',
            label: `${leaf.label} settings`,
            detail: leaf.category,
            icon: <Settings size={16} />,
            score,
            href: leaf.href,
            run: () => onOpenSetting(leaf.href),
          });
      }
      // Individual settings rows, below pages that match as well.
      for (const row of settingsRows) {
        const score = fuzzyScore(trimmed, row.label, row.keywords);
        if (score !== null && score >= LITERAL_SCORE) {
          const page = settingsLeaves.find((leaf) => leaf.id === row.leaf);
          results.push({
            id: `setting-row:${row.leaf}:${row.anchor}`,
            group: 'Settings',
            label: row.label,
            detail: page ? `${page.label} settings` : 'Settings',
            icon: <Settings size={16} />,
            score: score - 60,
            href: settingsRowHref(row),
            run: () => onOpenSetting(settingsRowHref(row)),
          });
        }
      }
      if (onStartAgent)
        for (const agent of agents) {
          const score = fuzzyScore(trimmed, agent.label, agent.description);
          if (score !== null)
            results.push({
              id: `agent:${agent.id}`,
              group: 'Agents',
              label: `Chat with ${agent.label}`,
              detail: agent.description,
              icon: <Bot size={16} />,
              score,
              run: () => onStartAgent(agent),
            });
        }
      if (onOpenWorkflow)
        for (const workflow of workflows) {
          const score = fuzzyScore(
            trimmed,
            workflow.label,
            `workflow run launch ${workflow.description ?? ''}`,
          );
          if (score !== null)
            results.push({
              id: `workflow:${workflow.id}`,
              group: 'Workflows',
              label: `Run ${workflow.label}…`,
              detail: workflow.description,
              icon: <Workflow size={16} />,
              score,
              run: () => onOpenWorkflow(workflow),
            });
        }
      const search = state.search;
      // The same words in several messages of one conversation (a prompt
      // sent again) show once: their rows would look identical.
      const shown = new Set<string>();
      search?.items.forEach((hit, index) => {
        const detail = plainSnippet(hit.excerpt);
        const key = `${hit.conversation_id}\n${detail}`;
        if (shown.has(key)) return;
        shown.add(key);
        results.push({
          id: `hit:${hit.conversation_id}:${hit.message_id ?? 'title'}`,
          group: 'Messages',
          label: hit.title || 'Untitled conversation',
          detail,
          icon: <FileText size={16} />,
          score: -index,
          run: () => onOpenSearchHit(hit),
        });
      });
    }
    // One result per place: the best way there ("Connect a phone or
    // computer" rather than the same row twice).
    const best = new Map<string, number>();
    for (const item of results)
      if (item.href)
        best.set(
          item.href,
          Math.max(best.get(item.href) ?? -Infinity, item.score),
        );
    const unique = results.filter(
      (item, index) =>
        !item.href ||
        (item.score === best.get(item.href) &&
          results.findIndex(
            (other) => other.href === item.href && other.score === item.score,
          ) === index),
    );
    // Scattered letter matches only show when nothing matches literally.
    const literal = unique.some(
      (item) => item.group !== 'Messages' && item.score >= LITERAL_SCORE,
    );
    const relevant = literal
      ? unique.filter(
          (item) => item.group === 'Messages' || item.score >= LITERAL_SCORE,
        )
      : unique;
    const grouped = GROUP_ORDER.map((group) =>
      relevant
        .filter((item) => item.group === group)
        .sort((left, right) => right.score - left.score)
        .slice(0, LIMITS[group]),
    ).filter((group) => group.length);
    // With a query, the group holding the best match comes first, so Enter
    // runs the best result overall. Message hits come last after literal
    // matches, and first above scattered ones (U25).
    if (trimmed)
      grouped.sort((left, right) => {
        const messages =
          Number(left[0].group === 'Messages') -
          Number(right[0].group === 'Messages');
        if (messages) return literal ? messages : -messages;
        return right[0].score - left[0].score;
      });
    return grouped.flat();
  }, [
    agents,
    appearance,
    commands,
    onClose,
    onOpenConversation,
    onOpenSearchHit,
    onOpenSetting,
    onStartAgent,
    onOpenWorkflow,
    state.conversations,
    state.search,
    switches,
    trimmed,
    update,
    workflows,
  ]);

  useEffect(() => setChosen(null), [trimmed]);
  // Enter runs the best result only when it matches what was typed (or is
  // a message hit); a scattered match must be chosen first.
  const top = items[0];
  const automatic =
    top && (!trimmed || top.group === 'Messages' || top.score >= LITERAL_SCORE)
      ? 0
      : -1;
  const current =
    chosen === null ? automatic : Math.min(chosen, items.length - 1);
  const optionId = (index: number) => `${listId}-option-${index}`;
  useEffect(() => {
    if (current < 0) return;
    listRef.current
      ?.querySelector<HTMLElement>(`[id="${CSS.escape(optionId(current))}"]`)
      ?.scrollIntoView?.({ block: 'nearest' });
    // optionId is derived from listId, which is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  function navigate(event: ReactKeyboardEvent) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (!items.length) return;
      const step = event.key === 'ArrowDown' ? 1 : -1;
      setChosen(
        current < 0
          ? step > 0
            ? 0
            : items.length - 1
          : (current + step + items.length) % items.length,
      );
    } else if (event.key === 'Enter') {
      event.preventDefault();
      if (current >= 0) items[current]?.run();
    }
  }

  const groups: {
    group: PaletteGroup;
    entries: { item: Item; index: number }[];
  }[] = [];
  items.forEach((item, index) => {
    const last = groups.at(-1);
    if (last?.group === item.group) last.entries.push({ item, index });
    else groups.push({ group: item.group, entries: [{ item, index }] });
  });

  return (
    <div className="command-palette" aria-label="Command palette">
      <div className="command-palette-field">
        <Search size={18} aria-hidden />
        <input
          data-initial-focus
          className="command-palette-input"
          type="search"
          aria-label="Find a workspace command"
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={current >= 0 ? optionId(current) : undefined}
          autoComplete="off"
          spellCheck={false}
          placeholder="Search conversations, commands, settings…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={navigate}
        />
        {state.searching && (
          <span className="command-palette-busy" role="status">
            Searching history…
          </span>
        )}
      </div>
      <div
        className="command-palette-results"
        id={listId}
        role="listbox"
        aria-label="Results"
        // Keyboard reachable (the list scrolls); arrows work here as well.
        tabIndex={0}
        aria-activedescendant={current >= 0 ? optionId(current) : undefined}
        onKeyDown={navigate}
        ref={listRef}
      >
        {groups.map(({ group, entries }) => (
          <div
            className="command-palette-group"
            role="group"
            aria-label={trimmed || group !== 'Conversations' ? group : 'Recent'}
            key={group}
          >
            <div className="command-palette-heading" aria-hidden>
              {trimmed || group !== 'Conversations' ? group : 'Recent'}
            </div>
            {entries.map(({ item, index }) => (
              <div
                key={item.id}
                id={optionId(index)}
                role="option"
                aria-selected={index === current}
                className="command-palette-option"
                onPointerMove={() => {
                  if (index !== current) setChosen(index);
                }}
                onPointerDown={(event) => event.preventDefault()}
                onClick={() => item.run()}
              >
                <span className="command-palette-icon" aria-hidden>
                  {item.icon}
                </span>
                <span className="command-palette-text">
                  <span className="command-palette-label">{item.label}</span>
                  {item.detail && (
                    <span className="command-palette-detail">
                      {item.detail}
                    </span>
                  )}
                </span>
                {item.shortcut && (
                  <span className="command-palette-kbd" aria-hidden>
                    <Kbd keys={item.shortcut} />
                  </span>
                )}
              </div>
            ))}
          </div>
        ))}
        {!items.length && (
          <p className="command-palette-empty" role="status">
            {state.searching
              ? 'Searching history…'
              : readingSettings
                ? 'Reading your settings…'
                : `No results for “${trimmed}”.`}
          </p>
        )}
      </div>
      {items.length > 0 && current < 0 && (
        <p className="command-palette-note" role="status">
          {readingSettings
            ? 'Reading your settings…'
            : 'Nothing matches exactly. Choose a result with the arrow keys.'}
        </p>
      )}
      <footer className="command-palette-footer" aria-hidden>
        <span>
          <Kbd keys="ArrowUp" />
          <Kbd keys="ArrowDown" /> Navigate
        </span>
        <span>
          <Kbd keys="Enter" /> Open
        </span>
        <span>
          <Kbd keys="Escape" /> Close
        </span>
        {trimmed.length < 2 && (
          <span className="command-palette-hint">
            <MessageSquare size={12} /> Type two letters to search messages
          </span>
        )}
      </footer>
    </div>
  );
}
