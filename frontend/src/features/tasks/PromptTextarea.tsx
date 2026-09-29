import {
  useId,
  useState,
  type KeyboardEvent,
  type SyntheticEvent,
  type TextareaHTMLAttributes,
} from 'react';

/** A `{{token}}` a workflow prompt can use, with what it stands for. */
export type PromptVariable = { token: string; label: string };

/** Filled in when the workflow runs (`tasks.expand_template_vars`). */
export const RUN_VARIABLES: readonly PromptVariable[] = [
  { token: 'date', label: "Today's date" },
  { token: 'day', label: 'Day of the week' },
  { token: 'time', label: 'Time of the run' },
  { token: 'month', label: 'Month' },
  { token: 'year', label: 'Year' },
  { token: 'prev_output', label: "The previous step's result" },
];

// `{{` and a partial name right before the caret, not yet closed.
const OPEN = /\{\{\s*([\w.-]*)$/;

type Props = Omit<
  TextareaHTMLAttributes<HTMLTextAreaElement>,
  'value' | 'onChange'
> & {
  value: string;
  onChange: (value: string) => void;
  /** More variables for this prompt, e.g. earlier steps' results. */
  variables?: readonly PromptVariable[];
};

/**
 * A prompt field that suggests variables after `{{` (parity row 19):
 * arrows move, Enter or Tab inserts `{{token}}`, Escape closes.
 */
export default function PromptTextarea({
  value,
  onChange,
  variables = [],
  readOnly,
  onKeyDown,
  ...rest
}: Props) {
  const id = useId();
  const [query, setQuery] = useState<{ text: string; at: number } | null>(null);
  const [active, setActive] = useState(0);
  const all = [...RUN_VARIABLES, ...variables];
  const matches = query
    ? all.filter(
        (item) =>
          item.token.toLowerCase().includes(query.text.toLowerCase()) ||
          item.label.toLowerCase().includes(query.text.toLowerCase()),
      )
    : [];
  const open = !readOnly && matches.length > 0;

  function track(
    element: HTMLTextAreaElement,
    text = element.value,
    caret = element.selectionStart ?? text.length,
  ) {
    const match = OPEN.exec(text.slice(0, caret));
    setQuery(match ? { text: match[1], at: caret - match[0].length } : null);
    setActive(0);
  }

  function insert(item: PromptVariable) {
    if (!query) return;
    const end = query.at + value.slice(query.at).search(/[^{\s\w.-]|$/);
    const next = `${value.slice(0, query.at)}{{${item.token}}}${value.slice(end)}`;
    setQuery(null);
    onChange(next);
  }

  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (open) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        const step = event.key === 'ArrowDown' ? 1 : -1;
        setActive(
          (current) => (current + step + matches.length) % matches.length,
        );
        return;
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault();
        insert(matches[Math.min(active, matches.length - 1)]);
        return;
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        setQuery(null);
        return;
      }
    }
    onKeyDown?.(event);
  }

  const reselect = (event: SyntheticEvent<HTMLTextAreaElement>) => {
    if (!readOnly) track(event.currentTarget);
  };

  if (readOnly)
    return (
      <textarea
        {...rest}
        readOnly
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
      />
    );
  return (
    <span className="prompt-field">
      <textarea
        {...rest}
        role="combobox"
        aria-autocomplete="list"
        aria-expanded={open}
        aria-controls={open ? `${id}-list` : undefined}
        aria-activedescendant={open ? `${id}-${active}` : undefined}
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
          track(event.target, event.target.value);
        }}
        onSelect={reselect}
        onClick={reselect}
        onBlur={() => setQuery(null)}
        onKeyDown={keyDown}
      />
      {open && (
        <span
          id={`${id}-list`}
          role="listbox"
          aria-label="Variables"
          className="prompt-variables surface-effect"
        >
          {matches.map((item, index) => (
            <span
              key={item.token}
              id={`${id}-${index}`}
              role="option"
              aria-selected={index === active}
              className="prompt-variable"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => insert(item)}
            >
              <code>{`{{${item.token}}}`}</code>
              <small>{item.label}</small>
            </span>
          ))}
        </span>
      )}
    </span>
  );
}
