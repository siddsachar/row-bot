import { useState } from 'react';
import { Boxes, Pencil, Plus, Trash2 } from 'lucide-react';
import {
  Button,
  EmptyState,
  EntityList,
  EntityRow,
  Field,
  IconButton,
  Input,
  Segmented,
  Select,
  Toggle,
} from '../../../ui/primitives';
import { useNotify, useOverlay } from '../../../ui/overlays';
import { request } from '../../api';
import { FormDialog } from '../../form-dialog';
import { Loaded, useLoad } from '../../load';
import type { CatalogModel, Endpoint } from '../../types';
import { amount, problem } from '../../words';

const KINDS = { chat: 'Chat', embeddings: 'Embeddings' } as const;

function ModelForm({
  model,
  endpoints,
  onClose,
  onSaved,
}: {
  model: CatalogModel | null;
  endpoints: Endpoint[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [endpoint, setEndpoint] = useState(
    model?.endpoint_id ?? endpoints[0]?.id ?? '',
  );
  const [name, setName] = useState(model?.name ?? '');
  const [upstream, setUpstream] = useState(
    model && model.upstream_model !== model.name ? model.upstream_model : '',
  );
  const [kind, setKind] = useState<CatalogModel['kind']>(model?.kind ?? 'chat');
  const [input, setInput] = useState(String(model?.input_price ?? 0));
  const [output, setOutput] = useState(String(model?.output_price ?? 0));
  const [isDefault, setDefault] = useState(model?.is_default ?? false);

  async function save() {
    const fields = {
      name: name.trim(),
      upstream_model: upstream.trim() || name.trim(),
      input_price: Number(input),
      output_price: Number(output),
      is_default: isDefault,
    };
    if (model)
      await request(`/api/admin/models/${model.id}`, {
        method: 'PATCH',
        body: fields,
      });
    else
      await request('/api/admin/models', {
        method: 'POST',
        body: { ...fields, endpoint_id: endpoint, kind },
      });
    onSaved();
  }

  return (
    <FormDialog
      title={model ? `Edit ${model.name}` : 'Add a catalog model'}
      description="A model people can choose in Row-Bot. They call it by its name here; the company server sends it to the endpoint."
      submitLabel={model ? 'Save' : 'Add model'}
      onClose={onClose}
      onSubmit={save}
    >
      {model ? (
        <Field label="Endpoint">
          <Input
            readOnly
            value={
              endpoints.find((item) => item.id === model.endpoint_id)?.name ??
              ''
            }
          />
        </Field>
      ) : (
        <Field label="Endpoint">
          <Select
            required
            value={endpoint}
            onChange={(event) => setEndpoint(event.target.value)}
          >
            {endpoints.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </Select>
        </Field>
      )}
      <Field
        label="Name"
        hint="What people and the policy call it: letters, digits and . _ : / @ + -"
      >
        <Input
          required
          maxLength={128}
          value={name}
          onChange={(event) => setName(event.target.value)}
          data-initial-focus
        />
      </Field>
      <Field
        label="Upstream model"
        hint="The endpoint's own name for it, if different."
      >
        <Input
          maxLength={200}
          placeholder={name || 'Same as the name'}
          value={upstream}
          onChange={(event) => setUpstream(event.target.value)}
        />
      </Field>
      {!model && (
        <div className="field">
          <span className="field-label">Kind</span>
          <Segmented<CatalogModel['kind']>
            label="Kind"
            value={kind}
            onChange={setKind}
            options={[
              { value: 'chat', label: KINDS.chat },
              { value: 'embeddings', label: KINDS.embeddings },
            ]}
          />
        </div>
      )}
      <div className="console-form-pair">
        <Field label="Input price" hint="Per million tokens">
          <Input
            required
            type="number"
            min={0}
            step="0.000001"
            value={input}
            onChange={(event) => setInput(event.target.value)}
          />
        </Field>
        <Field label="Output price" hint="Per million tokens">
          <Input
            required
            type="number"
            min={0}
            step="0.000001"
            value={output}
            onChange={(event) => setOutput(event.target.value)}
          />
        </Field>
      </div>
      <Field
        label="Default"
        layout="row"
        hint={`The model Row-Bot picks for ${kind === 'chat' ? 'chat' : 'embeddings'} unless people choose another.`}
      >
        <Toggle
          label="Default"
          checked={isDefault}
          onChange={(event) => setDefault(event.target.checked)}
        />
      </Field>
    </FormDialog>
  );
}

export function Catalog() {
  const overlay = useOverlay();
  const notify = useNotify();
  const catalog = useLoad(() =>
    Promise.all([
      request<{ models: CatalogModel[] }>('/api/admin/models'),
      request<{ endpoints: Endpoint[] }>('/api/admin/endpoints'),
    ]).then(([{ models }, { endpoints }]) => ({ models, endpoints })),
  );
  const [editing, setEditing] = useState<CatalogModel | 'new' | null>(null);

  function remove(model: CatalogModel) {
    overlay.open({
      kind: 'alert',
      title: `Remove ${model.name}?`,
      description:
        'People can no longer choose or call it. Its past usage stays in the usage report.',
      confirmLabel: 'Remove',
      onConfirm: () => {
        request(`/api/admin/models/${model.id}`, { method: 'DELETE' }).then(
          () => {
            catalog.reload();
            notify(`${model.name} is removed`);
          },
          (error: unknown) => notify(problem(error), 'danger'),
        );
      },
    });
  }

  return (
    <section className="console-section" aria-label="Catalog">
      <Loaded load={catalog} label="Loading the catalog" what="the catalog">
        {({ models, endpoints }) => {
          const add = (
            <Button
              variant="primary"
              disabled={endpoints.length === 0}
              onClick={() => setEditing('new')}
            >
              <Plus size={16} aria-hidden />
              Add model
            </Button>
          );
          const endpointName = (id: string) =>
            endpoints.find((item) => item.id === id)?.name ?? 'an endpoint';
          return (
            <>
              {models.length === 0 ? (
                <EmptyState title="No catalog models yet" action={add}>
                  {endpoints.length === 0
                    ? 'Add an endpoint first, then the models people may choose from it.'
                    : 'Add the models people may choose. The policy decides who may use which.'}
                </EmptyState>
              ) : (
                <>
                  <div className="console-section-actions">{add}</div>
                  <EntityList label="Catalog models">
                    {models.map((model) => (
                      <EntityRow
                        key={model.id}
                        title={model.name}
                        icon={<Boxes size={18} />}
                        status={
                          model.is_default
                            ? { tone: 'accent', label: 'Default' }
                            : undefined
                        }
                        meta={[
                          KINDS[model.kind],
                          `on ${endpointName(model.endpoint_id)}`,
                          model.upstream_model !== model.name
                            ? model.upstream_model
                            : '',
                          `${amount(model.input_price)} in / ${amount(model.output_price)} out per million tokens`,
                        ]
                          .filter(Boolean)
                          .join(' · ')}
                        action={
                          <>
                            <IconButton
                              size="sm"
                              label={`Edit ${model.name}`}
                              onClick={() => setEditing(model)}
                            >
                              <Pencil size={16} aria-hidden />
                            </IconButton>
                            <IconButton
                              size="sm"
                              label={`Remove ${model.name}`}
                              onClick={() => remove(model)}
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
              {editing && (
                <ModelForm
                  model={editing === 'new' ? null : editing}
                  endpoints={endpoints}
                  onClose={() => setEditing(null)}
                  onSaved={() => {
                    catalog.reload();
                    notify('The catalog is saved');
                  }}
                />
              )}
            </>
          );
        }}
      </Loaded>
    </section>
  );
}
