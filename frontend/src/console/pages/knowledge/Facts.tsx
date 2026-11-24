import { useState } from 'react';
import { Archive, Lightbulb, Pencil, Plus } from 'lucide-react';
import {
  Button,
  EmptyState,
  EntityList,
  EntityRow,
  Field,
  IconButton,
  InlineEmpty,
  Segmented,
  Select,
} from '../../../ui/primitives';
import { When } from '../../../ui/When';
import { useNotify, useOverlay } from '../../../ui/overlays';
import { request } from '../../api';
import { useDirectory } from '../../directory';
import { FormDialog } from '../../form-dialog';
import { Loaded, useLoad } from '../../load';
import type { BrainDocument, Fact, Space } from '../../types';
import { problem } from '../../words';

type Shown = 'in_force' | 'retired';

/** A fact's first words, to name its actions. */
const short = (text: string) =>
  text.length > 48 ? `${text.slice(0, 47).trimEnd()}…` : text;

function FactForm({
  space,
  fact,
  documents,
  onClose,
  onSaved,
}: {
  space: Space;
  fact: Fact | null;
  documents: BrainDocument[];
  onClose: () => void;
  onSaved: (fact: Fact) => void;
}) {
  const [text, setText] = useState(fact?.text ?? '');
  const [source, setSource] = useState(fact?.source_document_id ?? '');

  async function save() {
    const written = text.trim();
    const from = source || null;
    if (!fact) {
      onSaved(
        await request<Fact>(`/api/brain/spaces/${space.id}/facts`, {
          method: 'POST',
          body: from
            ? { text: written, source_document_id: from }
            : { text: written },
        }),
      );
      return;
    }
    const changes: { text?: string; source_document_id?: string | null } = {};
    if (written !== fact.text) changes.text = written;
    if (from !== fact.source_document_id) changes.source_document_id = from;
    onSaved(
      await request<Fact>(`/api/brain/facts/${fact.id}`, {
        method: 'PATCH',
        body: changes,
      }),
    );
  }

  return (
    <FormDialog
      title={fact ? 'Edit fact' : `Add a fact to ${space.name}`}
      description="One thing everyone who reads this space should know, in a sentence or two. Row-Bot finds facts the way it finds documents."
      submitLabel={fact ? 'Save' : 'Add fact'}
      onClose={onClose}
      onSubmit={save}
    >
      <Field label="Fact" hint="Up to 2,000 characters.">
        <textarea
          className="input console-textarea"
          required
          maxLength={2000}
          rows={4}
          value={text}
          onChange={(event) => setText(event.target.value)}
          data-initial-focus
        />
      </Field>
      <Field
        label="Source"
        hint="The document it comes from, if any: answers cite it."
      >
        <Select
          value={source}
          onChange={(event) => setSource(event.target.value)}
        >
          <option value="">No source</option>
          {documents.map((document) => (
            <option key={document.id} value={document.id}>
              {document.title || document.file_name}
            </option>
          ))}
        </Select>
      </Field>
    </FormDialog>
  );
}

/** A space's facts: add, edit and retire. Retired facts stay listed apart. */
export function Facts({ space }: { space: Space }) {
  const { nameOf } = useDirectory();
  const overlay = useOverlay();
  const notify = useNotify();
  const facts = useLoad(
    () =>
      Promise.all([
        request<{ facts: Fact[] }>(
          `/api/brain/spaces/${space.id}/facts?retired=true`,
        ),
        request<{ documents: BrainDocument[] }>(
          `/api/brain/spaces/${space.id}/documents`,
        ),
      ]).then(([{ facts: list }, { documents }]) => ({ list, documents })),
    space.id,
  );
  const [shown, setShown] = useState<Shown>('in_force');
  const [editing, setEditing] = useState<Fact | 'new' | null>(null);

  function retire(fact: Fact) {
    overlay.open({
      kind: 'alert',
      title: 'Retire this fact?',
      description:
        'Row-Bot stops using it in answers, and its knowledge goes. It stays listed under Retired, but it can’t be brought back: write it again if you need it.',
      confirmLabel: 'Retire',
      onConfirm: () => {
        request<Fact>(`/api/brain/facts/${fact.id}/retire`, {
          method: 'POST',
        }).then(
          (retired) => {
            facts.update((data) => ({
              ...data,
              list: data.list.map((item) =>
                item.id === retired.id ? retired : item,
              ),
            }));
            notify('The fact is retired');
          },
          (error: unknown) => notify(problem(error), 'danger'),
        );
      },
    });
  }

  const add = (
    <Button variant="primary" onClick={() => setEditing('new')}>
      <Plus size={16} aria-hidden />
      Add fact
    </Button>
  );
  return (
    <section className="console-section" aria-label="Facts">
      <Loaded load={facts} label="Loading facts" what="the facts">
        {({ list, documents }) => {
          const titleOf = (id: string | null) => {
            const found = documents.find((document) => document.id === id);
            return found ? found.title || found.file_name : null;
          };
          const showing = list.filter(
            (fact) => (fact.retired_at === null) === (shown === 'in_force'),
          );
          if (list.length === 0)
            return (
              <EmptyState title="No facts yet" action={add}>
                Write down what everyone who reads this space should know, such
                as “Expense claims are due by the 5th of each month.”
              </EmptyState>
            );
          return (
            <>
              <div className="console-filters console-spread">
                <Segmented<Shown>
                  label="Show"
                  value={shown}
                  onChange={setShown}
                  options={[
                    { value: 'in_force', label: 'In force' },
                    { value: 'retired', label: 'Retired' },
                  ]}
                />
                {add}
              </div>
              {showing.length === 0 ? (
                <InlineEmpty icon={<Lightbulb size={16} />}>
                  {shown === 'retired'
                    ? 'No retired facts.'
                    : 'Every fact here is retired.'}
                </InlineEmpty>
              ) : (
                <EntityList label="Facts">
                  {showing.map((fact) => {
                    const source = titleOf(fact.source_document_id);
                    return (
                      <EntityRow
                        key={fact.id}
                        title={fact.text}
                        icon={<Lightbulb size={18} />}
                        status={
                          fact.retired_at
                            ? { tone: 'neutral', label: 'Retired' }
                            : undefined
                        }
                        meta={
                          <>
                            {`Written by ${nameOf(fact.written_by)} · `}
                            <When value={fact.updated_at} />
                            {source && ` · From ${source}`}
                          </>
                        }
                        action={
                          fact.retired_at ? undefined : (
                            <>
                              <IconButton
                                size="sm"
                                label={`Edit “${short(fact.text)}”`}
                                onClick={() => setEditing(fact)}
                              >
                                <Pencil size={16} aria-hidden />
                              </IconButton>
                              <IconButton
                                size="sm"
                                label={`Retire “${short(fact.text)}”`}
                                onClick={() => retire(fact)}
                              >
                                <Archive size={16} aria-hidden />
                              </IconButton>
                            </>
                          )
                        }
                      />
                    );
                  })}
                </EntityList>
              )}
            </>
          );
        }}
      </Loaded>
      {editing && facts.status === 'ready' && (
        <FactForm
          space={space}
          fact={editing === 'new' ? null : editing}
          documents={facts.data.documents}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            facts.update((data) => ({
              ...data,
              list:
                editing === 'new'
                  ? [saved, ...data.list]
                  : data.list.map((item) =>
                      item.id === saved.id ? saved : item,
                    ),
            }));
            setShown('in_force');
            notify('The fact is saved');
          }}
        />
      )}
    </section>
  );
}
