import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { clientError } from '../../api/errors';
import type {
  WorkflowTemplate,
  WorkflowTemplateCreated,
  WorkflowTemplateList,
} from '../../api/types';
import { Button } from '../../ui/primitives';
import { AppIcon } from '../apps/parts';

/**
 * "Start from a template": workflows that use apps. Each is created switched off and scheduled
 * (never a webhook), its step limited to its app; a template whose app isn't connected offers
 * Connect instead.
 */
export default function AppTemplates({
  load,
  create,
  onCreated,
}: {
  load?: (signal?: AbortSignal) => Promise<WorkflowTemplateList>;
  create?: (template: string) => Promise<WorkflowTemplateCreated>;
  onCreated: (id: string, name: string) => void;
}) {
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  useEffect(() => {
    if (!load) return;
    const abort = new AbortController();
    load(abort.signal).then(
      (list) => !abort.signal.aborted && setTemplates(list.items),
      () => undefined,
    );
    return () => abort.abort();
  }, [load]);
  if (!templates.length || !create) return null;
  const start = async (template: WorkflowTemplate) => {
    setBusy(template.id);
    setMessage('');
    try {
      const created = await create(template.id);
      setMessage(
        `${created.name} added, switched off. Turn it on when it suits you.`,
      );
      onCreated(created.task_id, created.name);
    } catch (cause) {
      setMessage(clientError(cause).message);
    } finally {
      setBusy('');
    }
  };
  return (
    <section className="workflow-templates" aria-label="Start from a template">
      <h2>Start from a template</h2>
      <ul className="workflow-template-grid">
        {templates.map((template) => {
          const missing = template.apps.filter((app) => !app.connected);
          return (
            <li key={template.id} className="workflow-template">
              <div className="workflow-template-head">
                {template.apps.map((app) => (
                  <AppIcon key={app.app_id} icon={app.icon} size={20} />
                ))}
                <strong>{template.name}</strong>
              </div>
              <p>{template.description}</p>
              <small>{template.schedule_label} · starts off</small>
              <div className="button-row">
                {missing.length ? (
                  missing.map((app) => (
                    <Link
                      key={app.app_id}
                      className="button small"
                      to={`/settings/apps/${encodeURIComponent(app.app_id)}`}
                    >
                      Connect {app.name}
                    </Link>
                  ))
                ) : (
                  <Button
                    className="small"
                    disabled={Boolean(busy)}
                    onClick={() => void start(template)}
                    aria-label={`Use template: ${template.name}`}
                  >
                    Use template
                  </Button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      {message && (
        <p className="home-caption" role="status">
          {message}
        </p>
      )}
    </section>
  );
}
