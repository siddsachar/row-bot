import { useState } from 'react';
import { Input, Segmented, Toggle } from '../../../ui/primitives';
import { CHOICES, entries, SPEC, type Value } from '../../policy-model';

/** A list setting as comma-separated names ("shell, browser"). */
export function ListInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
}) {
  // The text as typed: a trailing comma stays while the list is parsed.
  const [text, setText] = useState(value.join(', '));
  return (
    <Input
      aria-label={label}
      value={text}
      placeholder={placeholder ?? 'None'}
      spellCheck={false}
      autoCapitalize="off"
      onChange={(event) => {
        setText(event.target.value);
        onChange(entries(event.target.value));
      }}
    />
  );
}

/** The one control a company policy setting gets, by its kind. */
export function SettingControl({
  settingKey,
  label,
  value,
  onChange,
  choices,
}: {
  settingKey: string;
  label: string;
  value: Value;
  onChange: (value: Value) => void;
  /** The choices to offer, when not all of them (a group's override). */
  choices?: string[];
}) {
  const spec = SPEC[settingKey];
  if (spec.kind === 'bool')
    return (
      <Toggle
        label={label}
        checked={value === true}
        onChange={(event) => onChange(event.target.checked)}
      />
    );
  if (spec.kind === 'enum')
    return (
      <Segmented
        label={label}
        size="sm"
        value={String(value)}
        onChange={onChange}
        options={(choices ?? spec.choices).map((choice) => ({
          value: choice,
          label: CHOICES[choice] ?? choice,
        }))}
      />
    );
  if (spec.kind === 'int')
    return (
      <Input
        aria-label={label}
        className="console-number"
        type="number"
        min={0}
        max={spec.maximum}
        value={String(value)}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    );
  return (
    <ListInput label={label} value={value as string[]} onChange={onChange} />
  );
}
