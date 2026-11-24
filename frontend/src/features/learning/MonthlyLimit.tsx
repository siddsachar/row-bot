import { useEffect, useId, useRef, useState } from 'react';
import { Input, Select } from '../../ui/primitives';
import {
  CUSTOM_LIMIT_MILLIONS,
  customLimit,
  limitChoice,
  limitOptions,
  LIMIT_CHOICES,
  millionsText,
} from './words';

const CUSTOM_HINT = `Enter ${CUSTOM_LIMIT_MILLIONS.min} to ${CUSTOM_LIMIT_MILLIONS.max.toLocaleString()} million tokens, like 75 or 2.5.`;

function amountText(limit: number | null): string {
  return limit !== null && limit > 0 ? millionsText(limit) : '';
}

/**
 * Learning's monthly limit (Settings › Learning and the setup sheet): a fixed choice, a custom amount in millions
 * of tokens, No limit (null) or No model calls (0). It answers only finished values: a choice at once, a custom
 * amount when the field is left or Enter is pressed, and only once it is in bounds. A labelled row hands it the
 * menu's ``id`` and description.
 */
export function MonthlyLimitPicker({
  value,
  onChange,
  disabled = false,
  id,
  'aria-describedby': describedBy,
}: {
  value: number | null;
  onChange: (limit: number | null) => void;
  disabled?: boolean;
  id?: string;
  'aria-describedby'?: string;
}) {
  const errorId = useId();
  const field = useRef<HTMLInputElement>(null);
  const focusField = useRef(false);
  const [custom, setCustom] = useState(() => limitChoice(value) === 'custom');
  const [text, setText] = useState(() => amountText(value));
  const [error, setError] = useState('');
  // A saved value that changes (a save here, an Undo, another device) shows as saved.
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    setCustom(limitChoice(value) === 'custom');
    setText(amountText(value));
    setError('');
  }
  useEffect(() => {
    if (!custom || !focusField.current) return;
    focusField.current = false;
    field.current?.focus();
    field.current?.select();
  }, [custom]);

  function choose(choice: string) {
    setError('');
    if (choice === 'custom') {
      focusField.current = true;
      setCustom(true);
      setText(amountText(value));
      return;
    }
    setCustom(false);
    onChange(choice === 'none' ? null : Number(choice));
  }
  function leave() {
    setCustom(limitChoice(value) === 'custom');
    setText(amountText(value));
    setError('');
  }
  function commit() {
    if (!text.trim()) {
      // Nothing typed: back to the saved choice, nothing sent.
      leave();
      return;
    }
    const tokens = customLimit(text);
    if (tokens === null) {
      setError(CUSTOM_HINT);
      return;
    }
    setError('');
    setText(millionsText(tokens));
    // An amount that is one of the choices shows as that choice.
    if (LIMIT_CHOICES.includes(tokens)) setCustom(false);
    if (tokens !== value) onChange(tokens);
  }

  return (
    <span className="learning-limit-picker">
      <Select
        id={id}
        aria-describedby={describedBy}
        value={custom ? 'custom' : limitChoice(value)}
        disabled={disabled}
        onChange={(event) => choose(event.target.value)}
      >
        {limitOptions().map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </Select>
      {custom && (
        <span className="learning-limit-custom">
          <Input
            ref={field}
            type="text"
            inputMode="decimal"
            autoComplete="off"
            aria-label="Custom monthly limit, in millions of tokens"
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            placeholder="75"
            value={text}
            disabled={disabled}
            onChange={(event) => {
              setText(event.target.value);
              setError('');
            }}
            onBlur={commit}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                commit();
              } else if (
                event.key === 'Escape' &&
                (error || text !== amountText(value))
              ) {
                event.preventDefault();
                event.stopPropagation();
                leave();
              }
            }}
          />
          <span className="learning-limit-unit" aria-hidden>
            M tokens
          </span>
        </span>
      )}
      {error && (
        <span id={errorId} role="alert" className="learning-limit-error">
          {error}
        </span>
      )}
    </span>
  );
}
