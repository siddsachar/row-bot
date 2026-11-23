import { useState } from 'react';
import { Pencil, Plus, Server, Trash2 } from 'lucide-react';
import {
  Button,
  EmptyState,
  EntityList,
  EntityRow,
  Field,
  IconButton,
  Input,
  Segmented,
  Toggle,
} from '../../../ui/primitives';
import { useNotify, useOverlay } from '../../../ui/overlays';
import { request } from '../../api';
import { FormDialog } from '../../form-dialog';
import { Loaded, useLoad } from '../../load';
import { Notice } from '../../page';
import type { Endpoint } from '../../types';
import { problem } from '../../words';

export const API_NAMES = {
  openai: 'OpenAI-compatible',
  anthropic: 'Anthropic',
} as const;

function originOf(url: string) {
  try {
    return new URL(url).origin;
  } catch {
    return url;
  }
}

function EndpointForm({
  endpoint,
  onClose,
  onSaved,
}: {
  endpoint: Endpoint | null;
  onClose: () => void;
  onSaved: (endpoint: Endpoint) => void;
}) {
  const [name, setName] = useState(endpoint?.name ?? '');
  const [api, setApi] = useState<Endpoint['api']>(endpoint?.api ?? 'openai');
  const [baseUrl, setBaseUrl] = useState(endpoint?.base_url ?? '');
  const [key, setKey] = useState('');
  const [timeout, setTimeoutSeconds] = useState(
    String(endpoint?.timeout_seconds ?? 60),
  );
  const [enabled, setEnabled] = useState(endpoint?.enabled ?? true);
  // The server sends the key only to the host it was entered for.
  const dropsKey =
    endpoint?.has_key &&
    !key &&
    baseUrl.trim() !== '' &&
    originOf(baseUrl.trim()) !== originOf(endpoint.base_url);

  async function save() {
    const fields = {
      name: name.trim(),
      base_url: baseUrl.trim(),
      timeout_seconds: Number(timeout),
      enabled,
      ...(key.trim() ? { api_key: key.trim() } : {}),
    };
    onSaved(
      endpoint
        ? await request<Endpoint>(`/api/admin/endpoints/${endpoint.id}`, {
            method: 'PATCH',
            body: fields,
          })
        : await request<Endpoint>('/api/admin/endpoints', {
            method: 'POST',
            body: { ...fields, api },
          }),
    );
  }

  return (
    <FormDialog
      title={endpoint ? `Edit ${endpoint.name}` : 'Add a model endpoint'}
      description={
        endpoint
          ? `An ${API_NAMES[endpoint.api]} endpoint. Its key never leaves the company server.`
          : 'A server that runs models for the company: an OpenAI-compatible server (vLLM, Ollama, llama.cpp, LM Studio, OpenRouter) or Anthropic.'
      }
      submitLabel={endpoint ? 'Save' : 'Add endpoint'}
      onClose={onClose}
      onSubmit={save}
    >
      <Field label="Name">
        <Input
          required
          maxLength={100}
          value={name}
          onChange={(event) => setName(event.target.value)}
          data-initial-focus
        />
      </Field>
      {!endpoint && (
        <div className="field">
          <span className="field-label">Type</span>
          <Segmented<Endpoint['api']>
            label="Type"
            value={api}
            onChange={setApi}
            options={[
              { value: 'openai', label: API_NAMES.openai },
              { value: 'anthropic', label: API_NAMES.anthropic },
            ]}
          />
        </div>
      )}
      <Field
        label="Base URL"
        hint={
          api === 'anthropic'
            ? 'Usually https://api.anthropic.com'
            : 'The address its SDK uses, such as http://gpu-box:8000/v1'
        }
      >
        <Input
          required
          type="url"
          value={baseUrl}
          onChange={(event) => setBaseUrl(event.target.value)}
        />
      </Field>
      <Field
        label="API key"
        hint={
          endpoint?.has_key
            ? 'Key saved. Leave this empty to keep it, or enter a new key to replace it.'
            : 'Stored encrypted on the server and never shown again.'
        }
      >
        <Input
          type="password"
          autoComplete="new-password"
          placeholder={endpoint?.has_key ? 'Key saved' : 'Optional'}
          value={key}
          onChange={(event) => setKey(event.target.value)}
        />
      </Field>
      {dropsKey && (
        <Notice tone="warning" title="The saved key won't be kept">
          It was entered for {originOf(endpoint.base_url)}. Enter it again to
          use it with the new address.
        </Notice>
      )}
      <Field label="Timeout (seconds)">
        <Input
          required
          type="number"
          min={1}
          max={600}
          value={timeout}
          onChange={(event) => setTimeoutSeconds(event.target.value)}
        />
      </Field>
      <Field label="Enabled" layout="row" hint="People can use its models.">
        <Toggle
          label="Enabled"
          checked={enabled}
          onChange={(event) => setEnabled(event.target.checked)}
        />
      </Field>
    </FormDialog>
  );
}

export function Endpoints() {
  const overlay = useOverlay();
  const notify = useNotify();
  const endpoints = useLoad(() =>
    request<{ endpoints: Endpoint[] }>('/api/admin/endpoints').then(
      (answer) => answer.endpoints,
    ),
  );
  const [editing, setEditing] = useState<Endpoint | 'new' | null>(null);

  function saved(endpoint: Endpoint) {
    endpoints.update((list) =>
      list.some((item) => item.id === endpoint.id)
        ? list.map((item) => (item.id === endpoint.id ? endpoint : item))
        : [...list, endpoint].sort((a, b) => a.name.localeCompare(b.name)),
    );
    notify(`${endpoint.name} is saved`);
  }

  function remove(endpoint: Endpoint) {
    overlay.open({
      kind: 'alert',
      title: `Remove ${endpoint.name}?`,
      description:
        'Its saved key is deleted with it. An endpoint that still has catalog models can’t be removed.',
      confirmLabel: 'Remove',
      onConfirm: () => {
        request(`/api/admin/endpoints/${endpoint.id}`, {
          method: 'DELETE',
        }).then(
          () => {
            endpoints.update((list) =>
              list.filter((item) => item.id !== endpoint.id),
            );
            notify(`${endpoint.name} is removed`);
          },
          (error: unknown) => notify(problem(error), 'danger'),
        );
      },
    });
  }

  const add = (
    <Button variant="primary" onClick={() => setEditing('new')}>
      <Plus size={16} aria-hidden />
      Add endpoint
    </Button>
  );
  return (
    <section className="console-section" aria-label="Endpoints">
      <Loaded load={endpoints} label="Loading endpoints" what="the endpoints">
        {(list) =>
          list.length === 0 ? (
            <EmptyState title="No model endpoints yet" action={add}>
              Add the servers that run models for your company. Their keys stay
              on the company server and never reach a laptop.
            </EmptyState>
          ) : (
            <>
              <div className="console-section-actions">{add}</div>
              <EntityList label="Endpoints">
                {list.map((endpoint) => (
                  <EntityRow
                    key={endpoint.id}
                    title={endpoint.name}
                    icon={<Server size={18} />}
                    status={
                      endpoint.enabled
                        ? undefined
                        : { tone: 'neutral', label: 'Off' }
                    }
                    meta={[
                      API_NAMES[endpoint.api],
                      endpoint.base_url,
                      endpoint.has_key ? 'Key saved' : 'No key',
                    ].join(' · ')}
                    action={
                      <>
                        <IconButton
                          size="sm"
                          label={`Edit ${endpoint.name}`}
                          onClick={() => setEditing(endpoint)}
                        >
                          <Pencil size={16} aria-hidden />
                        </IconButton>
                        <IconButton
                          size="sm"
                          label={`Remove ${endpoint.name}`}
                          onClick={() => remove(endpoint)}
                        >
                          <Trash2 size={16} aria-hidden />
                        </IconButton>
                      </>
                    }
                  />
                ))}
              </EntityList>
            </>
          )
        }
      </Loaded>
      {editing && (
        <EndpointForm
          endpoint={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={saved}
        />
      )}
    </section>
  );
}
