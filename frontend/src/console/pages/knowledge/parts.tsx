import { useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { Plus, X } from 'lucide-react';
import { IconButton, Input, Select } from '../../../ui/primitives';
import { request } from '../../api';
import { useDirectory } from '../../directory';
import { useLoad } from '../../load';
import { Notice } from '../../page';
import type { CatalogModel } from '../../types';
import { ADD_CHAT, ADD_EMBEDDINGS } from './words';

const MODELS = '/models?tab=catalog';

/** A reason in plain words; the two that need a model link to Models. */
export function Reason({ text }: { text: string }) {
  return text === ADD_EMBEDDINGS || text === ADD_CHAT ? (
    <Link to={MODELS}>{text}</Link>
  ) : (
    <>{text}</>
  );
}

/**
 * What the company brain is missing: a default embeddings model (documents
 * are found by their words only) or a default chat model (no knowledge graph).
 * Says nothing while the catalog loads or if it can't be read.
 */
export function ModelNotices() {
  const catalog = useLoad(() =>
    request<{ models: CatalogModel[] }>('/api/admin/models').then(
      (answer) => answer.models,
    ),
  );
  if (catalog.status !== 'ready') return null;
  const has = (kind: CatalogModel['kind']) =>
    catalog.data.some((model) => model.kind === kind && model.is_default);
  return (
    <>
      {!has('embeddings') && (
        <Notice tone="warning" title="No embeddings model yet">
          <span>
            Uploaded documents wait for one, and Row-Bot finds them by their
            words only until then. <Link to={MODELS}>{ADD_EMBEDDINGS}</Link>
          </span>
        </Notice>
      )}
      {!has('chat') && (
        <Notice tone="warning" title="No chat model yet">
          <span>
            Row-Bot finds the people, teams, projects and more in documents and
            facts with one. <Link to={MODELS}>{ADD_CHAT}</Link>
          </span>
        </Notice>
      )}
    </>
  );
}

function Chips({
  label,
  items,
  nameOf,
  what,
  onRemove,
}: {
  label: string;
  items: string[];
  nameOf: (item: string) => string;
  what: string;
  onRemove: (item: string) => void;
}) {
  return (
    <ul className="console-chips" aria-label={label}>
      {items.map((item) => (
        <li key={item}>
          <span>{nameOf(item)}</span>
          <IconButton
            size="sm"
            label={`Remove ${nameOf(item)} from ${what}`}
            onClick={() => onRemove(item)}
          >
            <X size={14} aria-hidden />
          </IconButton>
        </li>
      ))}
    </ul>
  );
}

/**
 * Groups chosen from the identity provider's groups people signed in with,
 * or typed (a group nobody has signed in with yet).
 */
export function GroupPicker({
  label,
  what,
  hint,
  value,
  onChange,
}: {
  label: string;
  /** The role in the remove and add names: "readers", "owners". */
  what: string;
  hint: string;
  value: string[];
  onChange: (value: string[]) => void;
}) {
  const { groups } = useDirectory();
  const id = useId();
  const [text, setText] = useState('');
  const name = text.trim();
  function add() {
    if (!name || name.length > 256) return;
    if (!value.includes(name)) onChange([...value, name]);
    setText('');
  }
  return (
    <div className="field console-picker" role="group" aria-labelledby={id}>
      <span className="field-label" id={id}>
        {label}
      </span>
      {value.length > 0 && (
        <Chips
          label={label}
          items={value}
          nameOf={(group) => group}
          what={what}
          onRemove={(group) => onChange(value.filter((item) => item !== group))}
        />
      )}
      <div className="console-picker-add">
        <Input
          aria-label={`Add a group to ${what}`}
          placeholder="Group name"
          list={`${id}-groups`}
          maxLength={256}
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            // Enter adds the group; it doesn't send the form.
            if (event.key === 'Enter') {
              event.preventDefault();
              add();
            }
          }}
        />
        <IconButton
          label={`Add the group to ${what}`}
          disabled={!name}
          onClick={add}
        >
          <Plus size={16} aria-hidden />
        </IconButton>
      </div>
      <datalist id={`${id}-groups`}>
        {groups
          .filter((group) => !value.includes(group))
          .map((group) => (
            <option key={group} value={group} />
          ))}
      </datalist>
      <small>{hint}</small>
    </div>
  );
}

/** People who have signed in, by name. */
export function PeoplePicker({
  label,
  what,
  hint,
  value,
  onChange,
}: {
  label: string;
  what: string;
  hint: string;
  value: string[];
  onChange: (value: string[]) => void;
}) {
  const { people, nameOf } = useDirectory();
  const id = useId();
  const left = people.filter((person) => !value.includes(person.id));
  return (
    <div className="field console-picker" role="group" aria-labelledby={id}>
      <span className="field-label" id={id}>
        {label}
      </span>
      {value.length > 0 && (
        <Chips
          label={label}
          items={value}
          nameOf={nameOf}
          what={what}
          onRemove={(person) =>
            onChange(value.filter((item) => item !== person))
          }
        />
      )}
      <Select
        aria-label={`Add a person to ${what}`}
        value=""
        disabled={left.length === 0}
        onChange={(event) => {
          if (event.target.value) onChange([...value, event.target.value]);
        }}
      >
        <option value="" disabled>
          {left.length ? 'Add a person' : 'Everyone is added'}
        </option>
        {left.map((person) => (
          <option key={person.id} value={person.id}>
            {person.display_name || person.email}
          </option>
        ))}
      </Select>
      <small>{hint}</small>
    </div>
  );
}
