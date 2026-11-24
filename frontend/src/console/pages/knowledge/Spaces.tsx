import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { FolderOpen, Library, Pencil, Plus, Trash2 } from 'lucide-react';
import {
  Button,
  EntityList,
  EntityRow,
  Field,
  IconButton,
  Input,
} from '../../../ui/primitives';
import { useNotify, useOverlay } from '../../../ui/overlays';
import { request } from '../../api';
import { useDirectory } from '../../directory';
import { FormDialog } from '../../form-dialog';
import { Loaded, useLoad } from '../../load';
import type { BrainDocument, Space, SpaceList } from '../../types';
import { problem } from '../../words';
import { GroupPicker, PeoplePicker } from './parts';
import { plural, same, spaceSummary } from './words';

type Listed = Space & { documents: number };

/** Add a space, or change its name and who reads and owns it. */
function SpaceForm({
  space,
  onClose,
  onSaved,
}: {
  space: Space | null;
  onClose: () => void;
  onSaved: (space: Space) => void;
}) {
  const [name, setName] = useState(space?.name ?? '');
  const [readers, setReaders] = useState(space?.reader_groups ?? []);
  const [ownerGroups, setOwnerGroups] = useState(space?.owner_groups ?? []);
  const [owners, setOwners] = useState(space?.owner_principals ?? []);
  const company = space?.kind === 'company';

  async function save() {
    const fields = {
      name: name.trim(),
      reader_groups: readers,
      owner_groups: ownerGroups,
      owner_principals: owners,
    };
    if (!space) {
      onSaved(
        await request<Space>('/api/admin/brain/spaces', {
          method: 'POST',
          body: fields,
        }),
      );
      return;
    }
    // Only what changed, so the audit log names just that.
    const changes: Partial<typeof fields> = {};
    if (fields.name !== space.name) changes.name = fields.name;
    if (!company && !same(readers, space.reader_groups))
      changes.reader_groups = readers;
    if (!same(ownerGroups, space.owner_groups))
      changes.owner_groups = ownerGroups;
    if (!same(owners, space.owner_principals))
      changes.owner_principals = owners;
    onSaved(
      await request<Space>(`/api/admin/brain/spaces/${space.id}`, {
        method: 'PATCH',
        body: changes,
      }),
    );
  }

  return (
    <FormDialog
      title={space ? `Edit ${space.name}` : 'Add a space'}
      description={
        company
          ? 'Everyone signed in reads the company space. Choose who may add documents and facts to it.'
          : 'A space holds company documents and facts. Only its readers and owners find what is in it.'
      }
      submitLabel={space ? 'Save' : 'Add space'}
      onClose={onClose}
      onSubmit={save}
    >
      <Field label="Name">
        <Input
          required
          maxLength={160}
          value={name}
          onChange={(event) => setName(event.target.value)}
          data-initial-focus
        />
      </Field>
      {!company && (
        <GroupPicker
          label="Readers"
          what="readers"
          hint="Groups whose people find what is in this space. Its owners read it too."
          value={readers}
          onChange={setReaders}
        />
      )}
      <GroupPicker
        label="Owner groups"
        what="owner groups"
        hint="Groups whose people may upload documents, write facts and curate its knowledge."
        value={ownerGroups}
        onChange={setOwnerGroups}
      />
      <PeoplePicker
        label="Owners"
        what="owners"
        hint="People who may do the same. Admins always can."
        value={owners}
        onChange={setOwners}
      />
    </FormDialog>
  );
}

async function loadSpaces(): Promise<Listed[]> {
  const { spaces } = await request<SpaceList>('/api/admin/brain/spaces');
  const counts = await Promise.all(
    spaces.map((space) =>
      request<{ documents: BrainDocument[] }>(
        `/api/brain/spaces/${space.id}/documents`,
      ).then((answer) => answer.documents.length),
    ),
  );
  return spaces.map((space, index) => ({
    ...space,
    documents: counts[index],
  }));
}

/** Every space: who reads and owns it and how many documents it holds. */
export function Spaces() {
  const { nameOf } = useDirectory();
  const navigate = useNavigate();
  const overlay = useOverlay();
  const notify = useNotify();
  const spaces = useLoad(loadSpaces);
  const [editing, setEditing] = useState<Space | 'new' | null>(null);

  function remove(space: Listed) {
    overlay.open({
      kind: 'alert',
      title: `Delete ${space.name}?`,
      description: `Its ${plural(space.documents, 'document', 'documents')}, its facts and its knowledge are deleted for good, for everyone. This can't be undone.`,
      confirmLabel: 'Delete',
      onConfirm: () => {
        request(`/api/admin/brain/spaces/${space.id}`, {
          method: 'DELETE',
        }).then(
          () => {
            spaces.update((list) =>
              list.filter((item) => item.id !== space.id),
            );
            notify(`${space.name} is deleted`);
          },
          (error: unknown) => notify(problem(error), 'danger'),
        );
      },
    });
  }

  const add = (
    <Button variant="primary" onClick={() => setEditing('new')}>
      <Plus size={16} aria-hidden />
      Add space
    </Button>
  );
  return (
    <section className="console-section" aria-label="Spaces">
      {/* The company space is always there: the list is never empty. */}
      <Loaded load={spaces} label="Loading spaces" what="the spaces">
        {(list) => (
          <>
            <div className="console-section-actions">{add}</div>
            <EntityList label="Spaces">
              {list.map((space) => (
                <EntityRow
                  key={space.id}
                  title={space.name}
                  icon={<Library size={18} />}
                  meta={`${spaceSummary(space, nameOf)} · ${
                    space.documents
                      ? plural(space.documents, 'document', 'documents')
                      : 'No documents'
                  }`}
                  action={
                    <>
                      <IconButton
                        size="sm"
                        label={`Open ${space.name}`}
                        onClick={() => navigate(`/knowledge?space=${space.id}`)}
                      >
                        <FolderOpen size={16} aria-hidden />
                      </IconButton>
                      <IconButton
                        size="sm"
                        label={`Edit ${space.name}`}
                        onClick={() => setEditing(space)}
                      >
                        <Pencil size={16} aria-hidden />
                      </IconButton>
                      <IconButton
                        size="sm"
                        label={
                          space.kind === 'company'
                            ? "The company space can't be deleted"
                            : `Delete ${space.name}`
                        }
                        aria-disabled={space.kind === 'company' || undefined}
                        onClick={() => {
                          if (space.kind !== 'company') remove(space);
                        }}
                      >
                        <Trash2 size={16} aria-hidden />
                      </IconButton>
                    </>
                  }
                />
              ))}
            </EntityList>
          </>
        )}
      </Loaded>
      {editing && (
        <SpaceForm
          space={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={(saved) => {
            spaces.update((list) =>
              editing === 'new'
                ? [...list, { ...saved, documents: 0 }]
                : list.map((item) =>
                    item.id === saved.id
                      ? { ...saved, documents: item.documents }
                      : item,
                  ),
            );
            notify(`${saved.name} is saved`);
          }}
        />
      )}
    </section>
  );
}
