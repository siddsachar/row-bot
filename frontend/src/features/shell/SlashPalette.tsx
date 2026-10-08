import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useId,
  useRef,
  useState,
  type RefObject,
} from 'react';
import type { SlashCommandSpec } from '../../api/types';
import {
  BadgeCheck,
  Bot,
  CircleHelp,
  CircleMinus,
  CircleStop,
  Code2,
  Download,
  Flag,
  HeartPulse,
  MessageSquarePlus,
  RotateCcw,
  Sparkles,
  UserRound,
  Wrench,
  Brain,
  type LucideIcon,
} from 'lucide-react';

const commandIcons: Record<string, LucideIcon> = {
  auto_fix_high: Sparkles,
  restart_alt: RotateCcw,
  remove_circle: CircleMinus,
  add_comment: MessageSquarePlus,
  stop_circle: CircleStop,
  psychology: Brain,
  badge: BadgeCheck,
  person_pin: UserRound,
  hub: Bot,
  flag: Flag,
  monitor_heart: HeartPulse,
  construction: Wrench,
  download: Download,
  help: CircleHelp,
  code: Code2,
};

export type SlashPaletteHandle = {
  key(event: React.KeyboardEvent<HTMLTextAreaElement>): boolean;
};

function currentToken(text: string, cursor: number) {
  const safe = Math.max(0, Math.min(cursor, text.length));
  let start = safe;
  while (start > 0 && !/\s/.test(text[start - 1])) start -= 1;
  let end = safe;
  while (end < text.length && !/\s/.test(text[end])) end += 1;
  const token = text.slice(start, end);
  return token.startsWith('/') ? { start, end, query: token.slice(1) } : null;
}

/** Whether a word of the text starts with the needle ("the" is not "he"). */
function wordStarts(text: string, needle: string) {
  return text
    .toLocaleLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .some((word) => word.startsWith(needle));
}

/**
 * How well a command matches what follows "/", lower first: its own name,
 * then its label, then a word of its category or description; null when it
 * does not match. So "/he" leads with /help, not every "the".
 */
function rank(spec: SlashCommandSpec, query: string): number | null {
  const needle = query.toLocaleLowerCase();
  if (!needle) return 0;
  const names = [spec.token, ...spec.aliases].map((name) =>
    name.slice(1).toLocaleLowerCase(),
  );
  const label = spec.label.toLocaleLowerCase();
  if (names[0] === needle) return 0;
  if (names.includes(needle)) return 1;
  if (names.some((name) => name.startsWith(needle))) return 2;
  if (label.startsWith(needle)) return 3;
  if (wordStarts(label, needle)) return 4;
  if (names.some((name) => name.includes(needle)) || label.includes(needle))
    return 5;
  if (wordStarts(spec.category, needle)) return 6;
  if (wordStarts(spec.description, needle)) return 7;
  return null;
}

const SlashPalette = forwardRef<
  SlashPaletteHandle,
  {
    text: string;
    cursor: number;
    commands: SlashCommandSpec[];
    disabled: boolean;
    inputRef?: RefObject<HTMLTextAreaElement | null>;
    onChoose(
      command: SlashCommandSpec,
      token: { start: number; end: number },
    ): void;
  }
>(function SlashPalette(
  { text, cursor, commands, disabled, inputRef, onChoose },
  ref,
) {
  const listboxId = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const token = currentToken(text, cursor);
  const query = token?.query;
  // Matches are grouped by category; keyboard order follows the visual order.
  const groups = useMemo(() => {
    if (query === undefined) return [];
    const order: string[] = [];
    const byCategory = new Map<string, SlashCommandSpec[]>();
    const ranked = commands
      .map((command, index) => ({ command, index, rank: rank(command, query) }))
      .filter((item) => item.rank !== null)
      .sort(
        (left, right) => left.rank! - right.rank! || left.index - right.index,
      )
      .slice(0, 12);
    for (const { command } of ranked) {
      const category = command.category || 'Commands';
      if (!byCategory.has(category)) {
        byCategory.set(category, []);
        order.push(category);
      }
      byCategory.get(category)!.push(command);
    }
    return order.map((category) => ({
      category,
      items: byCategory.get(category)!,
    }));
  }, [commands, query]);
  const items = useMemo(() => groups.flatMap((group) => group.items), [groups]);
  const [selected, setSelected] = useState(0);
  const [dismissed, setDismissed] = useState('');
  // One popover at a time: the palette belongs to the focused composer, so it
  // steps aside while focus is in another control such as a composer menu.
  const [composerBlurred, setComposerBlurred] = useState(false);
  const identity = token ? `${token.start}:${token.end}:${token.query}` : '';
  const open = Boolean(
    token && identity !== dismissed && !disabled && !composerBlurred,
  );
  // Once the token is gone, typing it again is a new request.
  useEffect(() => {
    if (!identity) setDismissed('');
  }, [identity]);
  useEffect(() => {
    const input = inputRef?.current;
    if (!input) return;
    const blur = (event: FocusEvent) => {
      if (
        event.relatedTarget instanceof Node &&
        listRef.current?.contains(event.relatedTarget)
      )
        return;
      setComposerBlurred(true);
    };
    const focus = () => setComposerBlurred(false);
    input.addEventListener('blur', blur);
    input.addEventListener('focus', focus);
    return () => {
      input.removeEventListener('blur', blur);
      input.removeEventListener('focus', focus);
    };
  }, [inputRef]);
  useEffect(() => setSelected(0), [identity]);
  useEffect(() => {
    const input = inputRef?.current;
    if (!input) return;
    if (open && !disabled && items.length) {
      input.setAttribute('aria-controls', listboxId);
      input.setAttribute('aria-expanded', 'true');
      input.setAttribute(
        'aria-activedescendant',
        `${listboxId}-${Math.min(selected, items.length - 1)}`,
      );
    } else {
      input.removeAttribute('aria-controls');
      input.removeAttribute('aria-expanded');
      input.removeAttribute('aria-activedescendant');
    }
    return () => {
      input.removeAttribute('aria-controls');
      input.removeAttribute('aria-expanded');
      input.removeAttribute('aria-activedescendant');
    };
  }, [disabled, inputRef, items.length, listboxId, open, selected]);
  useEffect(() => {
    const active = listRef.current?.querySelector<HTMLElement>(
      '[aria-selected="true"]',
    );
    active?.scrollIntoView?.({ block: 'nearest' });
  }, [open, selected]);
  useImperativeHandle(
    ref,
    () => ({
      key(event) {
        if (!open || disabled) return false;
        if (event.key === 'Escape') {
          setDismissed(identity);
          return true;
        }
        if (!items.length) return false;
        if (event.key === 'ArrowDown') {
          setSelected((value) => (value + 1) % items.length);
          return true;
        }
        if (event.key === 'ArrowUp') {
          setSelected((value) => (value - 1 + items.length) % items.length);
          return true;
        }
        if (event.key === 'Enter' || event.key === 'Tab') {
          onChoose(items[Math.min(selected, items.length - 1)], token!);
          setDismissed(identity);
          return true;
        }
        return false;
      },
    }),
    [disabled, identity, items, onChoose, open, selected, token],
  );
  if (!open) return null;
  let position = -1;
  return (
    <div
      className="slash-palette surface-effect"
      id={listboxId}
      ref={listRef}
      role="listbox"
      aria-label="Slash commands"
      onMouseDown={(event) => event.preventDefault()}
    >
      {items.length ? (
        groups.map((group, groupIndex) => (
          <div
            key={group.category}
            role="group"
            aria-labelledby={`${listboxId}-group-${groupIndex}`}
            className="slash-palette-group"
          >
            <div
              id={`${listboxId}-group-${groupIndex}`}
              className="slash-palette-group-label"
              role="presentation"
            >
              {group.category}
            </div>
            {group.items.map((command) => {
              const index = ++position;
              const Icon = Object.hasOwn(commandIcons, command.icon)
                ? commandIcons[command.icon]
                : Sparkles;
              return (
                <button
                  id={`${listboxId}-${index}`}
                  className="slash-palette-row"
                  type="button"
                  role="option"
                  aria-selected={index === selected}
                  aria-label={`${command.token} ${command.label}`}
                  aria-description={command.description}
                  key={command.id}
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setSelected(index)}
                  onFocus={() => setSelected(index)}
                  onClick={() => {
                    onChoose(command, token!);
                    setDismissed(identity);
                  }}
                >
                  <span className="slash-palette-icon" aria-hidden>
                    <Icon size={16} strokeWidth={1.8} />
                  </span>
                  {/* The usage ("/goal objective") and the label are
                      kept apart (U19). */}
                  <span className="slash-palette-token">
                    {command.token}
                    {command.argument_hint ? (
                      <span className="slash-palette-argument">
                        {' '}
                        {command.argument_hint}
                      </span>
                    ) : null}
                  </span>
                  <span className="slash-palette-label">{command.label}</span>
                  <span className="slash-palette-description" aria-hidden>
                    {command.description}
                  </span>
                </button>
              );
            })}
          </div>
        ))
      ) : (
        <p role="status" className="slash-palette-empty">
          No slash commands match.
        </p>
      )}
      <div className="slash-palette-footer" aria-hidden>
        <span>
          <kbd>↑</kbd>
          <kbd>↓</kbd> move
        </span>
        <span>
          <kbd>↵</kbd> choose
        </span>
        <span>
          <kbd>esc</kbd> close
        </span>
      </div>
    </div>
  );
});

export default SlashPalette;
