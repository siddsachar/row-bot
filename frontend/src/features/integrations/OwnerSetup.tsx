import { useEffect, useRef, useState } from 'react';
import { useRuntime } from '../../runtime';
import type {
  IntegrationItem,
  SkillDetail,
  PluginDetail,
  SkillHubPreview,
} from '../../api/types';
import { clientError } from '../../api/errors';
import { Button, Field, Input, Select, Toggle } from '../../ui/primitives';
import { ModalTask } from '../../ui/overlays';
import McpSetup from './McpSetup';
import { useSetupOperations } from './setup-operations';

type Props = {
  item: IntegrationItem;
  onChanged: (removed?: boolean) => Promise<void>;
  onAdvanced: () => void;
};

export function SkillReview({ preview }: { preview: SkillHubPreview }) {
  return (
    <section className="stack" aria-label="Review complete skill">
      <p>
        Version: {preview.version || preview.content_hash} · Publisher:{' '}
        {preview.entry.author || 'Not supplied'}
      </p>
      <p>
        Activation, required tools and accounts are described in the full
        instructions below. Adding copies files; scripts are not executed.
      </p>
      {preview.requirements?.map((requirement, i) => (
        <p key={i}>{requirement}</p>
      ))}
      <a href="/app-v2/settings/integrations?type=mcp&tab=my">
        Set up required apps &amp; tools
      </a>
      {preview.review_files?.length ? (
        preview.review_files.map((file) => (
          <details key={file.path} open={file.path.endsWith('SKILL.md')}>
            <summary>
              {file.path}
              {file.executable ? ' · Executable script' : ''}
            </summary>
            <p>
              {file.size_bytes} bytes · SHA-256 {file.sha256}
            </p>
            {file.text !== null ? (
              <pre className="text-preview">{file.text}</pre>
            ) : (
              <p>{file.unavailable_reason}</p>
            )}
          </details>
        ))
      ) : (
        <pre className="text-preview">{preview.primary_text}</pre>
      )}
      <details>
        <summary>Scanner findings and upstream provenance</summary>
        <p>
          Scanner findings and upstream audits are evidence, not a safety
          guarantee.
        </p>
        {preview.scan.findings.map((finding, i) => (
          <p key={i}>
            {finding.severity}: {finding.path} {finding.message}
          </p>
        ))}
        {preview.provenance?.map((value, i) => (
          <p key={i}>{value}</p>
        ))}
        <p>
          {preview.entry.source} · {preview.entry.url}
        </p>
        <code>{preview.content_hash}</code>
      </details>
    </section>
  );
}

export function SkillSetup({ item, onChanged, onAdvanced }: Props) {
  const { controller } = useRuntime();
  const operations = useSetupOperations(item);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [message, setMessage] = useState('');
  const [removeReview, setRemoveReview] = useState<{
    lines: string[];
    apply: () => Promise<void>;
  } | null>(null);
  const [, setBusy] = useState(false);
  const scope = `integration-skill:${item.id}`;
  const [pending, setPending] = useState(() => operations.read(scope));
  useEffect(() => {
    const abort = new AbortController();
    void controller
      .skill(item.owner_ref, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setDetail(value);
      })
      .catch((error) => {
        if (!abort.signal.aborted) setMessage(clientError(error).message);
      });
    return () => abort.abort();
  }, [controller, item.owner_ref]);
  const run = async (action: () => Promise<void>, recovery = false) => {
    setBusy(true);
    try {
      await operations.run(action, recovery);
    } catch (error) {
      setMessage(clientError(error).message);
    } finally {
      setBusy(false);
    }
  };
  const settle = async (id: string) => {
    const result = await controller.skillReceipt(id);
    const rejected = result.status === 'rejected';
    if (rejected || result.status === 'completed') {
      setMessage(
        rejected
          ? 'Skill change rejected. Review a new change when ready.'
          : 'Skill change saved.',
      );
      operations.retain(scope, '');
      setPending('');
      const removed = !rejected && result.action === 'skill.delete';
      if (!removed) setDetail(await controller.skill(item.owner_ref));
      await onChanged(removed);
    } else
      setMessage(
        'Original change is unconfirmed. Check it before making another change.',
      );
  };
  const preference = async (
    name: 'availability' | 'pin_defaults',
    value: boolean,
  ) => {
    if (!detail) return;
    const payload = {
      revision: detail.library_revision,
      name: item.owner_ref,
      preference: name,
      value,
    };
    const reviewed = await controller.reviewSkill('skill.preference', payload);
    const id = crypto.randomUUID();
    operations.retain(scope, id);
    setPending(id);
    await controller.executeSkill({
      command_id: id,
      type: 'skill.preference',
      payload: { ...payload, review_id: reviewed.review_id },
    });
    await settle(id);
  };
  const remove = async () => {
    if (!detail) return;
    const payload = {
      revision: detail.library_revision,
      name: item.owner_ref,
      skill_revision: detail.skill.revision,
    };
    const reviewed = await controller.reviewSkill('skill.delete', payload);
    setRemoveReview({
      lines: [
        `Skill: ${reviewed.target}`,
        `Current revision: ${reviewed.before_revision || reviewed.revision}`,
      ],
      apply: async () => {
        const id = crypto.randomUUID();
        operations.retain(scope, id);
        setPending(id);
        await controller.executeSkill({
          command_id: id,
          type: 'skill.delete',
          payload: { ...payload, review_id: reviewed.review_id },
        });
        const result = await controller.skillReceipt(id);
        if (result.status === 'completed' || result.status === 'rejected') {
          operations.retain(scope, '');
          setPending('');
          setMessage(
            result.status === 'rejected'
              ? 'Removal rejected. The current skill remains.'
              : 'Skill removed. Previous bytes are retained for recovery.',
          );
          await onChanged(result.status === 'completed');
        }
      },
    });
  };
  return (
    <section className="stack" aria-label="Skill availability">
      {message && <p role="status">{message}</p>}
      {pending && (
        <Button
          disabled={operations.busy}
          onClick={() => void run(() => settle(pending), true)}
        >
          Check original skill change
        </Button>
      )}
      {detail && (
        <>
          <p>
            Activation:{' '}
            {Object.entries(detail.skill.activation)
              .map(([key, values]) => `${key}: ${values.join(', ')}`)
              .join('; ') || 'Selected by the agent when relevant.'}
          </p>
          <pre className="text-preview">{detail.skill.instructions}</pre>
          <Button
            disabled={operations.blocked}
            onClick={() =>
              void run(() =>
                preference('availability', !detail.skill.available),
              )
            }
          >
            {detail.skill.available
              ? 'Turn off skill'
              : 'Make available in chats'}
          </Button>
          <Button
            disabled={operations.blocked}
            aria-pressed={detail.skill.pinned}
            onClick={() =>
              void run(() => preference('pin_defaults', !detail.skill.pinned))
            }
          >
            {detail.skill.pinned ? 'Unpin default' : 'Pin for new work'}
          </Button>
        </>
      )}
      {detail?.skill.editable &&
        ['local', 'user', 'custom'].includes(item.source) && (
          <Button
            variant="danger"
            disabled={operations.blocked}
            onClick={() => void run(remove)}
          >
            Remove skill
          </Button>
        )}
      <ModalTask
        open={Boolean(removeReview)}
        onOpenChange={(open) => {
          if (!open) setRemoveReview(null);
        }}
        title="Remove skill"
        description="Remove only this local skill. Previous bytes are retained for recovery; other skills and account credentials remain."
      >
        <ul>
          {removeReview?.lines.map((line, index) => (
            <li key={index}>{line}</li>
          ))}
        </ul>
        <Button onClick={() => setRemoveReview(null)}>Cancel</Button>
        <Button
          disabled={operations.blocked}
          onClick={() =>
            void run(async () => {
              const review = removeReview;
              setRemoveReview(null);
              await review?.apply();
            })
          }
        >
          Confirm removal
        </Button>
      </ModalTask>
      <p>
        Off keeps the skill files and preferences. It prevents future selection;
        instructions already loaded in running work are not erased. Availability
        follows the skill owner and the profile selected for a chat. Setup does
        not select or change a chat profile.
      </p>
      <a href="/app-v2/settings/profiles">Review profile restrictions</a>
      <a href="/app-v2/settings/integrations?type=mcp&tab=my">
        Set up required apps &amp; tools
      </a>
      <Button disabled={operations.blocked} onClick={onAdvanced}>
        Edit, update and maintain this skill
      </Button>
    </section>
  );
}

export function PluginSetup({ item, onChanged, onAdvanced }: Props) {
  const { controller } = useRuntime();
  const operations = useSetupOperations(item);
  const [detail, setDetail] = useState<PluginDetail | null>(null);
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [childId, setChildId] = useState('');
  const childHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (childId) childHeading.current?.focus();
  }, [childId]);
  const [review, setReview] = useState<{
    lines: string[];
    apply: () => Promise<void>;
  } | null>(null);
  const scope = `integration-plugin:${item.id}`;
  const [pending, setPending] = useState(() => operations.read(scope));
  const refresh = async () => {
    setDetail(await controller.plugin(item.owner_ref));
    await onChanged();
  };
  useEffect(() => {
    const abort = new AbortController();
    void controller
      .plugin(item.owner_ref, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setDetail(value);
      })
      .catch((error) => {
        if (!abort.signal.aborted) setMessage(clientError(error).message);
      });
    return () => abort.abort();
  }, [controller, item.owner_ref]);
  const run = async (action: () => Promise<void>, recovery = false) => {
    setBusy(true);
    setMessage('');
    try {
      await operations.run(action, recovery);
    } catch (error) {
      setMessage(clientError(error).message);
    } finally {
      setBusy(false);
    }
  };
  const settle = async (id: string) => {
    const result = await controller.pluginReceipt(item.owner_ref, id);
    if (['completed', 'rejected'].includes(result.status)) {
      operations.retain(scope, '');
      setPending('');
      await refresh();
    } else
      setMessage(
        'Original package change is unconfirmed. Check it before making another change.',
      );
  };
  const action = async (
    type:
      'plugin.configure' | 'plugin.test' | 'plugin.enable' | 'plugin.disable',
  ) => {
    if (!detail) return;
    const payload = {
      plugin_id: item.owner_ref,
      revision: detail.revision,
      ...(type === 'plugin.configure' ? { settings, secrets } : {}),
    };
    const result = await controller.reviewPlugin(item.owner_ref, type, payload);
    setReview({
      lines: result.disclosures,
      apply: async () => {
        const id = crypto.randomUUID();
        operations.retain(scope, id);
        setPending(id);
        await controller.executePlugin(item.owner_ref, {
          command_id: id,
          type,
          payload: {
            ...payload,
            action_digest: result.action_digest,
            review_id: result.review_id,
          },
        });
        setSecrets({});
        await settle(id);
      },
    });
  };
  const nextChild = item.children.find(
    (child) => child.required !== false && child.status !== 'ready',
  );
  const child = item.children.find((row) => row.id === childId);
  return (
    <section className="stack" aria-label="Package setup checklist">
      {message && <p role="status">{message}</p>}
      {pending && (
        <Button
          disabled={operations.busy}
          onClick={() => void run(() => settle(pending), true)}
        >
          Check original package change
        </Button>
      )}
      {detail && (
        <>
          {detail.enabled && (
            <>
              <Button
                disabled={operations.blocked}
                onClick={() => void run(() => action('plugin.disable'))}
              >
                Turn off package
              </Button>
              <p>
                Off withdraws this package's tools and included
                skills/connections. Configuration, permissions, data and
                protected credentials remain. Work already in progress is
                governed by its runtime owner; this action does not replay work.
              </p>
            </>
          )}
          <details>
            <summary>Package setup instructions</summary>
            <pre className="text-preview">
              {detail.guide || 'No additional instructions supplied.'}
            </pre>
            <p>
              Package permissions:{' '}
              {detail.permissions.join(', ') || 'None declared'}
            </p>
          </details>
          {detail.sign_in?.map((account, i) => (
            <p key={i}>
              Account requirement: {account.label} ({account.kind}). Use the
              package's declared settings; a package label alone does not
              establish supported OAuth.
            </p>
          ))}
          {(detail.settings.length > 0 ||
            detail.secrets.length > 0 ||
            detail.health.status !== 'passed' ||
            !detail.enabled) && (
            <fieldset disabled={operations.blocked} className="stack">
              <legend>Package requirements</legend>
              {detail.settings.map((field) => (
                <Field
                  key={field.name}
                  label={`${field.label}${field.required ? ' (required)' : ' (optional)'}`}
                >
                  {field.type === 'boolean' ? (
                    <Toggle
                      label={field.label}
                      checked={Boolean(settings[field.name] ?? field.value)}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          [field.name]: e.target.checked,
                        })
                      }
                    />
                  ) : field.options.length ? (
                    <Select
                      value={String(settings[field.name] ?? field.value ?? '')}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          [field.name]: e.target.value,
                        })
                      }
                    >
                      {field.options.map((value) => (
                        <option key={value}>{value}</option>
                      ))}
                    </Select>
                  ) : (
                    <Input
                      type={
                        ['integer', 'number'].includes(field.type)
                          ? 'number'
                          : 'text'
                      }
                      value={String(settings[field.name] ?? field.value ?? '')}
                      onChange={(e) =>
                        setSettings({
                          ...settings,
                          [field.name]: ['integer', 'number'].includes(
                            field.type,
                          )
                            ? Number(e.target.value)
                            : e.target.value,
                        })
                      }
                    />
                  )}
                </Field>
              ))}
              {detail.secrets.map((field) => (
                <Field
                  key={field.name}
                  label={`${field.label}${field.required ? ' (required)' : ' (optional)'}${field.configured ? ' · saved' : ''}`}
                >
                  <Input
                    type="password"
                    autoComplete="off"
                    value={secrets[field.name] ?? ''}
                    onChange={(e) =>
                      setSecrets({ ...secrets, [field.name]: e.target.value })
                    }
                  />
                </Field>
              ))}
              {(detail.settings.length > 0 || detail.secrets.length > 0) && (
                <Button
                  onClick={() => void run(() => action('plugin.configure'))}
                >
                  Review package settings
                </Button>
              )}
              {detail.health.status !== 'passed' && (
                <Button
                  disabled={!detail.capabilities.test?.available}
                  onClick={() => void run(() => action('plugin.test'))}
                >
                  Run local package checks
                </Button>
              )}
              {!detail.enabled && detail.health.status === 'passed' && (
                <Button
                  disabled={!detail.capabilities.enable?.available}
                  onClick={() => void run(() => action('plugin.enable'))}
                >
                  Turn on package
                </Button>
              )}
            </fieldset>
          )}
        </>
      )}
      <h3>Included capabilities</h3>
      <p>
        Required capabilities block Ready. Only explicitly optional capabilities
        may be skipped. Child configuration remains owned by this package.
      </p>
      <ul>
        {item.children.map((row) => (
          <li key={row.id}>
            {row.name} · {row.required === false ? 'Optional' : 'Required'} ·{' '}
            {row.status === 'ready' ? 'Usable' : 'Not ready'}
            {row.kind === 'mcp' && (
              <Button onClick={() => setChildId(row.id)}>
                Set up {row.name}
              </Button>
            )}
          </li>
        ))}
      </ul>
      {nextChild?.kind === 'mcp' && !child && (
        <Button variant="primary" onClick={() => setChildId(nextChild.id)}>
          Continue setup: {nextChild.name}
        </Button>
      )}
      {child?.kind === 'mcp' && (
        <section className="stack">
          <h4 tabIndex={-1} ref={childHeading}>
            Set up {child.name}
          </h4>
          <McpSetup key={child.id} item={child} onChanged={onChanged} />
        </section>
      )}
      <Button disabled={operations.blocked} onClick={onAdvanced}>
        Advanced package configuration
      </Button>
      <ModalTask
        open={Boolean(review)}
        onOpenChange={(open) => {
          if (!open && !busy) setReview(null);
        }}
        title="Review package setup"
        description="The existing package owner applies this change."
        ariaLabel="Review package setup"
      >
        <ul>
          {review?.lines.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
        <Button
          disabled={operations.blocked}
          onClick={() =>
            void run(async () => {
              const current = review;
              setReview(null);
              await current?.apply();
            })
          }
        >
          Confirm
        </Button>
      </ModalTask>
    </section>
  );
}
