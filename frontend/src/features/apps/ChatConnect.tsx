import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useRuntime } from '../../runtime';
import type { IntegrationDetail, TraceAppRef } from '../../api/types';
import { Button, StatusDot } from '../../ui/primitives';
import { ConsentSheet, PlanProgress, usePlan } from './SetupFlow';
import { AppIcon, idPath, Publisher, statusOf } from './parts';

/**
 * The Connect card the agent leaves in a chat: apps from Row-Bot's own
 * catalog, each shown as the catalog knows it (never as the chat described
 * it). Connecting runs that app's normal consent sheet and plan right here;
 * nothing is installed until the person agrees. Once an app is ready,
 * Continue sends the request on in this chat.
 */
export default function ChatConnect({
  apps,
  onContinue,
}: {
  apps: TraceAppRef[];
  onContinue(): void;
}) {
  return (
    <div
      className="transcript-card chat-connect"
      data-kind="apps"
      role="group"
      aria-label="Apps to connect"
    >
      <span className="chat-connect-intro">
        Row-Bot can do this with{' '}
        {apps.length > 1 ? 'one of these apps' : 'an app'}.
      </span>
      <ul className="chat-connect-apps">
        {apps.map((app) => (
          <SuggestedApp key={app.item_id} app={app} onContinue={onContinue} />
        ))}
      </ul>
    </div>
  );
}

function SuggestedApp({
  app,
  onContinue,
}: {
  app: TraceAppRef;
  onContinue(): void;
}) {
  const { controller } = useRuntime();
  const [itemId, setItemId] = useState(app.item_id);
  const [detail, setDetail] = useState<IntegrationDetail | null>(null);
  const load = useCallback(
    (signal?: AbortSignal) =>
      controller
        .integrationDetail({ item_id: itemId, revision: '' }, signal)
        .then(setDetail, () => undefined),
    [controller, itemId],
  );
  useEffect(() => {
    const abort = new AbortController();
    void load(abort.signal);
    return () => abort.abort();
  }, [load]);
  const entry = detail?.entry;
  const name = entry ? entry.app?.name || entry.name : app.name;
  const control = usePlan({ itemId, revision: '', name }, (plan) => {
    // Set up from the catalog: follow the app to what it became.
    if (plan.installed_id && plan.installed_id !== itemId)
      setItemId(plan.installed_id);
    else void load();
  });
  const status = entry ? statusOf(entry) : null;
  const ready = entry?.lifecycle === 'installed' && entry.readiness === 'ready';
  const action = entry?.next_action;
  // Offered for a change, ready but it only looks things up: the card allows changes (a sign-in made for
  // reads signs in once more). Offered to connect or turn on, a ready app continues the request.
  const access = ready && app.allow_changes ? detail?.about.access : null;
  const readsOnly =
    access &&
    (access.limited ||
      (access.preset === 'read_only' &&
        access.tools.some((tool) => tool.effect !== 'read_only')));
  const label = `${name}${status ? `, ${status[1]}` : ''}`;
  return (
    <li className="chat-connect-app" aria-label={label}>
      <div className="chat-connect-row">
        <AppIcon icon={entry?.icon ?? app.icon} size={32} />
        <span className="chat-connect-text">
          <strong>{name}</strong>
          <span className="app-card-meta">
            {entry && <Publisher entry={entry} />}
            {status && (
              <StatusDot tone={status[0]} label={status[1]} showLabel />
            )}
          </span>
        </span>
        <span className="transcript-card-actions">
          <Link className="button ghost" to={idPath('app', itemId)}>
            Details
          </Link>
          {readsOnly ? (
            <Button
              variant="primary"
              disabled={control.busy || Boolean(control.plan)}
              onClick={() =>
                void control.apply('access', {
                  preset: 'ask',
                  tools_digest: access.tools_digest,
                  overrides: {},
                })
              }
            >
              Allow changes
            </Button>
          ) : ready ? (
            <Button variant="primary" onClick={() => onContinue()}>
              Continue
            </Button>
          ) : action &&
            entry?.kind === 'builtin' &&
            !['none', 'try'].includes(action.kind) ? (
            // Part of Row-Bot: it is set up in its own settings.
            <Link
              className="button primary"
              to={`${idPath('app', itemId)}&edit=1`}
            >
              {action.label}
            </Link>
          ) : action &&
            !['none', 'try'].includes(action.kind) &&
            !control.plan ? (
            <Button
              variant="primary"
              disabled={control.busy}
              onClick={() => void control.review('', action.label)}
            >
              {action.label}
            </Button>
          ) : null}
        </span>
      </div>
      {control.error && <p role="alert">{control.error}</p>}
      <PlanProgress control={control} name={name} />
      <ConsentSheet control={control} name={name} cleanupOffered={false} />
    </li>
  );
}
