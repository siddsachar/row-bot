import {
  useDeferredValue,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  Bot,
  CornerDownLeft,
  FileText,
  MessageSquare,
  Search,
  Settings,
} from 'lucide-react';
import { useClientState, useRuntime } from '../../runtime';
import { Kbd } from '../../ui/primitives';
import type { ConversationView, SearchHit } from '../../api/types';
import { fuzzyScore } from './fuzzy';
import { ConversationGlyph } from './ConversationGlyph';
import { settingsLeaves } from '../settings/model';

export type PaletteGroup =
  'Conversations' | 'Messages' | 'Commands' | 'Settings' | 'Agents';

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
const SETTINGS_KEYWORDS: Record<string, string> = {
  providers: 'api key credentials ollama openai anthropic cloud connect',
  models: 'default model thinking reasoning catalog pin',
  knowledge: 'memory memories wiki graph dream cycle',
  buddy: 'companion avatar pack desktop',
  goals: 'agent profiles objectives',
  voice: 'dictation talk speech microphone tts',
  system: 'shell browser computer use tunnel remote access logging',
  tracker: 'habits tracking',
  documents: 'files upload pdf library',
  tools: 'utilities search web built-in',
  skills: 'hub install',
  accounts: 'google gmail calendar oauth subscription',
  channels: 'telegram discord slack messaging',
  utilities: 'built-in helpers',
  mcp: 'servers model context protocol connectors',
  plugins: 'extensions install',
  preferences: 'appearance theme dark light density language migration',
};

const GROUP_ORDER: readonly PaletteGroup[] = [
  'Conversations',
  'Commands',
  'Agents',
  'Settings',
  'Messages',
];

/** fuzzyMatch scores a literal substring at 1000 or more (less field weight). */
const LITERAL_SCORE = 850;

const LIMITS: Record<PaletteGroup, number> = {
  Conversations: 8,
  Messages: 6,
  Commands: 8,
  Settings: 6,
  Agents: 5,
};

/**
 * One fuzzy search across conversations (design and code threads included),
 * commands, settings pages and agents, plus a full-text search of message
 * history. Raycast-style: a single field, grouped results, keyboard first.
 */
export default function CommandPalette({
  commands,
  loadAgents,
  onOpenConversation,
  onOpenSearchHit,
  onOpenSetting,
  onStartAgent,
}: {
  commands: PaletteCommand[];
  /** Read once when the palette opens; agents join the results. */
  loadAgents?: (signal: AbortSignal) => Promise<PaletteAgent[]>;
  onOpenConversation: (conversation: ConversationView) => void;
  onOpenSearchHit: (hit: SearchHit) => void;
  onOpenSetting: (href: string) => void;
  onStartAgent?: (agent: PaletteAgent) => void;
}) {
  const state = useClientState();
  const { controller } = useRuntime();
  const [query, setQuery] = useState('');
  const deferred = useDeferredValue(query);
  const [active, setActive] = useState(0);
  const listId = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const trimmed = deferred.trim();
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
          icon: <ConversationGlyph category={row.category} />,
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
      for (const row of topLevel) {
        const score = fuzzyScore(
          trimmed,
          row.title || 'Untitled conversation',
          row.category,
        );
        if (score !== null)
          results.push({
            id: `conversation:${row.id}`,
            group: 'Conversations',
            label: row.title || 'Untitled conversation',
            icon: <ConversationGlyph category={row.category} />,
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
        const score = fuzzyScore(
          trimmed,
          leaf.label,
          `Open ${leaf.label} settings`,
          `${leaf.category} ${SETTINGS_KEYWORDS[leaf.id] ?? ''}`,
        );
        if (score !== null)
          results.push({
            id: `setting:${leaf.id}`,
            group: 'Settings',
            label: `${leaf.label} settings`,
            detail: leaf.category,
            icon: <Settings size={16} />,
            score,
            run: () => onOpenSetting(leaf.href),
          });
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
      const search = state.search;
      if (search)
        search.items.forEach((hit, index) =>
          results.push({
            id: `hit:${hit.conversation_id}:${hit.message_id ?? 'title'}`,
            group: 'Messages',
            label: hit.title || 'Untitled conversation',
            detail: plainSnippet(hit.excerpt),
            icon: <FileText size={16} />,
            score: -index,
            run: () => onOpenSearchHit(hit),
          }),
        );
    }
    // Scattered letter matches only show when nothing matches literally.
    const literal = results.some(
      (item) => item.group !== 'Messages' && item.score >= LITERAL_SCORE,
    );
    const relevant = literal
      ? results.filter(
          (item) => item.group === 'Messages' || item.score >= LITERAL_SCORE,
        )
      : results;
    const grouped = GROUP_ORDER.map((group) =>
      relevant
        .filter((item) => item.group === group)
        .sort((left, right) => right.score - left.score)
        .slice(0, LIMITS[group]),
    ).filter((group) => group.length);
    // With a query, the group holding the best match comes first, so Enter
    // runs the best result overall. Message hits always come last.
    if (trimmed)
      grouped.sort((left, right) =>
        left[0].group === 'Messages'
          ? 1
          : right[0].group === 'Messages'
            ? -1
            : right[0].score - left[0].score,
      );
    return grouped.flat();
  }, [
    agents,
    commands,
    onOpenConversation,
    onOpenSearchHit,
    onOpenSetting,
    onStartAgent,
    state.conversations,
    state.search,
    trimmed,
  ]);

  useEffect(() => setActive(0), [trimmed]);
  const current = Math.min(active, Math.max(0, items.length - 1));
  const optionId = (index: number) => `${listId}-option-${index}`;
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>(`[id="${CSS.escape(optionId(current))}"]`)
      ?.scrollIntoView?.({ block: 'nearest' });
    // optionId is derived from listId, which is stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

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
          aria-activedescendant={items.length ? optionId(current) : undefined}
          autoComplete="off"
          spellCheck={false}
          placeholder="Search conversations, commands, settings…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (event.nativeEvent.isComposing) return;
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault();
              if (!items.length) return;
              const step = event.key === 'ArrowDown' ? 1 : -1;
              setActive((current + step + items.length) % items.length);
            } else if (event.key === 'Enter') {
              event.preventDefault();
              items[current]?.run();
            }
          }}
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
        // Reachable for assistive tech; the field keeps keyboard focus.
        tabIndex={-1}
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
                  if (index !== current) setActive(index);
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
              : `No results for “${trimmed}”.`}
          </p>
        )}
      </div>
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
        <span className="command-palette-hint">
          <MessageSquare size={12} /> Type two letters to search messages
        </span>
      </footer>
    </div>
  );
}
