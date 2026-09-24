import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useState,
} from 'react';
import type { SlashCommandSpec } from '../../api/types';

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

function matches(spec: SlashCommandSpec, query: string) {
  const needle = query.toLocaleLowerCase();
  return [
    spec.token.slice(1),
    ...spec.aliases.map((alias) => alias.slice(1)),
    spec.label,
    spec.category,
    spec.description,
  ].some((value) => value.toLocaleLowerCase().includes(needle));
}

const SlashPalette = forwardRef<
  SlashPaletteHandle,
  {
    text: string;
    cursor: number;
    commands: SlashCommandSpec[];
    disabled: boolean;
    onChoose(
      command: SlashCommandSpec,
      token: { start: number; end: number },
    ): void;
  }
>(function SlashPalette({ text, cursor, commands, disabled, onChoose }, ref) {
  const token = currentToken(text, cursor);
  const query = token?.query;
  const items = useMemo(
    () =>
      query !== undefined
        ? commands.filter((command) => matches(command, query)).slice(0, 12)
        : [],
    [commands, query],
  );
  const [selected, setSelected] = useState(0);
  const [dismissed, setDismissed] = useState('');
  const identity = token ? `${token.start}:${token.end}:${token.query}` : '';
  const open = Boolean(token && identity !== dismissed);
  useEffect(() => setSelected(0), [identity]);
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
          onChoose(items[selected], token!);
          setDismissed(identity);
          return true;
        }
        return false;
      },
    }),
    [disabled, identity, items, onChoose, open, selected, token],
  );
  if (!open) return null;
  return (
    <div
      className="slash-palette surface-effect"
      role="listbox"
      aria-label="Slash commands"
    >
      {items.length ? (
        items.map((command, index) => (
          <button
            className="slash-palette-row"
            type="button"
            role="option"
            aria-selected={index === selected}
            key={command.id}
            onMouseDown={(event) => event.preventDefault()}
            onMouseEnter={() => setSelected(index)}
            onClick={() => {
              onChoose(command, token!);
              setDismissed(identity);
            }}
          >
            <span className="slash-palette-icon" aria-hidden>
              {command.icon}
            </span>
            <span className="slash-palette-copy">
              <strong>{command.token}</strong>
              <span>{command.label}</span>
              <small>{command.description}</small>
            </span>
            <span className="slash-palette-category">
              {command.category}
              {command.argument_hint ? ` · ${command.argument_hint}` : ''}
            </span>
          </button>
        ))
      ) : (
        <p role="status">No slash commands match.</p>
      )}
    </div>
  );
});

export default SlashPalette;
