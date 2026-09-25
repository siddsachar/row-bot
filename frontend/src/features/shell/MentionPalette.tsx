import {
  forwardRef,
  useEffect,
  useId,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from 'react';
import type { SlashPaletteHandle } from './SlashPalette';

export type MentionItem = {
  id: string;
  group: string;
  label: string;
  description?: string;
  icon: ReactNode;
  /** Marks the current choice (the chat's profile or write target). */
  current?: boolean;
  onChoose(): void;
};

function currentToken(text: string, cursor: number) {
  const safe = Math.max(0, Math.min(cursor, text.length));
  let start = safe;
  while (start > 0 && !/\s/.test(text[start - 1])) start -= 1;
  let end = safe;
  while (end < text.length && !/\s/.test(text[end])) end += 1;
  const token = text.slice(start, end);
  return token.startsWith('@') ? { start, end, query: token.slice(1) } : null;
}

/**
 * "@" in the composer: agents, this chat's resources and files. Choosing one
 * removes the "@…" token and applies the choice (agent profile, write target
 * or file picker), so mentions are a keyboard path to existing controls.
 */
const MentionPalette = forwardRef<
  SlashPaletteHandle,
  {
    text: string;
    cursor: number;
    items: MentionItem[];
    disabled: boolean;
    inputRef?: RefObject<HTMLTextAreaElement | null>;
    /** Remove the "@…" token from the draft. */
    onConsume(token: { start: number; end: number }): void;
  }
>(function MentionPalette(
  { text, cursor, items, disabled, inputRef, onConsume },
  ref,
) {
  const listboxId = useId();
  const listRef = useRef<HTMLDivElement>(null);
  const token = currentToken(text, cursor);
  const query = token?.query.toLocaleLowerCase();
  const groups = useMemo(() => {
    if (query === undefined) return [];
    const order: string[] = [];
    const byGroup = new Map<string, MentionItem[]>();
    for (const item of items) {
      if (
        query &&
        !`${item.label} ${item.group} ${item.description ?? ''}`
          .toLocaleLowerCase()
          .includes(query)
      )
        continue;
      if (!byGroup.has(item.group)) {
        byGroup.set(item.group, []);
        order.push(item.group);
      }
      byGroup.get(item.group)!.push(item);
    }
    return order.map((group) => ({ group, items: byGroup.get(group)! }));
  }, [items, query]);
  const flat = useMemo(() => groups.flatMap((group) => group.items), [groups]);
  const [selected, setSelected] = useState(0);
  const [dismissed, setDismissed] = useState('');
  const [blurred, setBlurred] = useState(false);
  const identity = token ? `${token.start}:${token.end}:${token.query}` : '';
  const open = Boolean(
    token && identity !== dismissed && !disabled && !blurred,
  );
  useEffect(() => setSelected(0), [identity]);
  useEffect(() => {
    const input = inputRef?.current;
    if (!input) return;
    const blur = (event: FocusEvent) => {
      if (
        event.relatedTarget instanceof Node &&
        listRef.current?.contains(event.relatedTarget)
      )
        return;
      setBlurred(true);
    };
    const focus = () => setBlurred(false);
    input.addEventListener('blur', blur);
    input.addEventListener('focus', focus);
    return () => {
      input.removeEventListener('blur', blur);
      input.removeEventListener('focus', focus);
    };
  }, [inputRef]);
  useEffect(() => {
    const input = inputRef?.current;
    if (!input || !open || !flat.length) return;
    input.setAttribute('aria-controls', listboxId);
    input.setAttribute('aria-expanded', 'true');
    input.setAttribute(
      'aria-activedescendant',
      `${listboxId}-${Math.min(selected, flat.length - 1)}`,
    );
    return () => {
      input.removeAttribute('aria-controls');
      input.removeAttribute('aria-expanded');
      input.removeAttribute('aria-activedescendant');
    };
  }, [flat.length, inputRef, listboxId, open, selected]);
  useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>('[aria-selected="true"]')
      ?.scrollIntoView?.({ block: 'nearest' });
  }, [open, selected]);
  const choose = (item: MentionItem) => {
    if (!token) return;
    onConsume(token);
    setDismissed(identity);
    item.onChoose();
  };
  useImperativeHandle(
    ref,
    () => ({
      key(event) {
        if (!open) return false;
        if (event.key === 'Escape') {
          setDismissed(identity);
          return true;
        }
        if (!flat.length) return false;
        if (event.key === 'ArrowDown') {
          setSelected((value) => (value + 1) % flat.length);
          return true;
        }
        if (event.key === 'ArrowUp') {
          setSelected((value) => (value - 1 + flat.length) % flat.length);
          return true;
        }
        if (event.key === 'Enter' || event.key === 'Tab') {
          choose(flat[Math.min(selected, flat.length - 1)]);
          return true;
        }
        return false;
      },
    }),
    // choose closes over token/identity, which these values track.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [flat, identity, open, selected],
  );
  if (!open) return null;
  let position = -1;
  return (
    <div
      className="slash-palette mention-palette surface-effect"
      id={listboxId}
      ref={listRef}
      role="listbox"
      aria-label="Mentions"
      onMouseDown={(event) => event.preventDefault()}
    >
      {flat.length ? (
        groups.map((group, groupIndex) => (
          <div
            key={group.group}
            role="group"
            aria-labelledby={`${listboxId}-group-${groupIndex}`}
            className="slash-palette-group"
          >
            <div
              id={`${listboxId}-group-${groupIndex}`}
              className="slash-palette-group-label"
              role="presentation"
            >
              {group.group}
            </div>
            {group.items.map((item) => {
              const index = ++position;
              return (
                <button
                  id={`${listboxId}-${index}`}
                  className="slash-palette-row mention-palette-row"
                  type="button"
                  role="option"
                  aria-selected={index === selected}
                  aria-current={item.current || undefined}
                  key={item.id}
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setSelected(index)}
                  onClick={() => choose(item)}
                >
                  <span className="slash-palette-icon" aria-hidden>
                    {item.icon}
                  </span>
                  <span className="slash-palette-label mention-palette-label">
                    {item.label}
                  </span>
                  <span className="slash-palette-description">
                    {item.current ? 'Current · ' : ''}
                    {item.description}
                  </span>
                </button>
              );
            })}
          </div>
        ))
      ) : (
        <p role="status" className="slash-palette-empty">
          Nothing matches.
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

export default MentionPalette;
