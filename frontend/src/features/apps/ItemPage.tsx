import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft, MoreHorizontal } from 'lucide-react';
import { useRuntime } from '../../runtime';
import { clientError } from '../../api/errors';
import type {
  IntegrationDetail,
  IntegrationEntry,
  IntegrationWay,
} from '../../api/types';
import {
  Button,
  Disclosure,
  EmptyState,
  ErrorState,
  Menu,
  Skeleton,
  StatusDot,
  type MenuAction,
} from '../../ui/primitives';
import { SettingsGroup, StatusLine } from '../settings/anatomy';
import { useWorkspaceActions } from '../shell/workspace-actions';
import AccessSheet, { ToolGroups } from './AccessSheet';
import { ConsentSheet, hostOf, PlanProgress, usePlan } from './SetupFlow';
import {
  AppIcon,
  appCatalog,
  idPath,
  ItemCard,
  Publisher,
  statusOf,
  useAppCatalog,
} from './parts';

const WAYS: Record<string, string> = {
  hosted_sign_in: 'Hosted · Sign-in',
  api_key: 'Hosted · API key',
  hosted: 'Hosted',
  local: 'Runs on this computer',
};

function WayText({ way }: { way: IntegrationWay }) {
  return (
    <span className="app-way-text">
      <strong>{way.name}</strong>
      <small>
        {[
          WAYS[way.method] ?? '',
          way.verified ? `by ${way.publisher}` : way.publisher,
          way.recommended ? 'Recommended' : '',
          way.supported ? '' : 'Not available yet',
        ]
          .filter(Boolean)
          .join(' · ')}
      </small>
    </span>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** The chat command for a skill, as the composer names it. */
export function slashOf(name: string) {
  return (
    '/' +
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-|-$/g, '')
  );
}

/** `/settings/apps/notion` names an app: open what is installed, else its best catalog entry. */
function useItemId(kind: 'app' | 'skill', param: string) {
  const { controller } = useRuntime();
  const [found, setFound] = useState(() =>
    param.includes(':') ? param : kind === 'skill' ? `skill:${param}` : '',
  );
  const [missing, setMissing] = useState(false);
  useEffect(() => {
    if (param.includes(':') || kind === 'skill') return;
    let alive = true;
    void (async () => {
      const app = (await appCatalog(controller)).get(param);
      const installed = await controller.integrationItems({
        scope: 'installed',
        kind: 'app',
      });
      const pick = (items: IntegrationEntry[]) =>
        items.find((item) => item.app?.id === param);
      let entry = pick(installed.items);
      let revision = '';
      if (!entry && app) {
        const page = await controller.integrationItems({
          scope: 'catalog',
          kind: 'app',
          query: app.name,
        });
        entry = pick(page.items);
        revision = page.revision;
      }
      if (!alive) return;
      if (entry) setFound(entry.id + (revision ? `\n${revision}` : ''));
      else setMissing(true);
    })().catch(() => alive && setMissing(true));
    return () => {
      alive = false;
    };
  }, [controller, kind, param]);
  return { found, missing };
}

export default function ItemPage({
  kind,
  param,
}: {
  kind: 'app' | 'skill';
  param: string;
}) {
  const { found, missing } = useItemId(kind, param);
  const [search] = useSearchParams();
  const [itemId, resolvedRevision = ''] = found.split('\n');
  const back = kind === 'skill' ? '/settings/skills' : '/settings/apps';
  if (missing)
    return (
      <EmptyState
        title="This app isn't available"
        action={
          <Link className="button" to={back}>
            Browse apps
          </Link>
        }
      >
        Search for it by name, or add it from a link.
      </EmptyState>
    );
  if (!itemId) return <Skeleton label="Opening app" />;
  return (
    <Detail
      key={itemId}
      kind={kind}
      itemId={itemId}
      revision={search.get('r') ?? resolvedRevision}
      back={back}
    />
  );
}

function Detail({
  kind,
  itemId,
  revision,
  back,
}: {
  kind: 'app' | 'skill';
  itemId: string;
  revision: string;
  back: string;
}) {
  const { controller } = useRuntime();
  const navigate = useNavigate();
  const workspace = useWorkspaceActions();
  const catalog = useAppCatalog();
  const heading = useRef<HTMLHeadingElement>(null);
  const [detail, setDetail] = useState<IntegrationDetail | null>(null);
  const [error, setError] = useState('');
  const [changing, setChanging] = useState(false);
  const [settling, setSettling] = useState(false);
  const load = useCallback(
    (signal?: AbortSignal) =>
      controller.integrationDetail({ item_id: itemId, revision }, signal).then(
        (value) => {
          setDetail(value);
          setError('');
        },
        (cause) => {
          if (!signal?.aborted) setError(clientError(cause).message);
        },
      ),
    [controller, itemId, revision],
  );
  useEffect(() => {
    const abort = new AbortController();
    void load(abort.signal);
    return () => abort.abort();
  }, [load]);
  const settle = async () => {
    setSettling(true);
    try {
      setDetail(await controller.settleIntegration({ item_id: itemId }));
      setError('');
    } catch (cause) {
      setError(clientError(cause).message);
    } finally {
      setSettling(false);
    }
  };
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, [Boolean(detail)]); // eslint-disable-line react-hooks/exhaustive-deps
  const entry = detail?.entry;
  const name = entry ? entry.app?.name || entry.name : '';
  const control = usePlan({ itemId, revision, name }, (plan) => {
    // Set up from a catalog entry: follow it to the installed item; removed: back to the library.
    if (plan.state === 'completed' && plan.intent === 'remove')
      navigate(back, { replace: true });
    else if (
      ['completed', 'cancelled'].includes(plan.state) &&
      plan.installed_id &&
      plan.installed_id !== itemId
    )
      navigate(idPath(kind, plan.installed_id), {
        replace: true,
      });
    else void load();
  });
  const openPlan = detail?.plan?.plan_id ? detail.plan : null;
  useEffect(() => {
    // An unfinished plan for this item (from another visit or device) shows its progress.
    if (openPlan && control.plan?.plan_id !== openPlan.plan_id && !control.plan)
      void control.review(openPlan.intent);
  }, [openPlan?.plan_id]); // eslint-disable-line react-hooks/exhaustive-deps
  if (error && !detail)
    return (
      <ErrorState
        title="Couldn't open this"
        action={<Button onClick={() => void load()}>Retry</Button>}
      >
        {error}
      </ErrorState>
    );
  if (!detail || !entry) return <Skeleton label="Opening details" />;
  const about = detail.about;
  const app = entry.app ? catalog.get(entry.app.id) : undefined;
  const status = statusOf(entry);
  const action = entry.next_action;
  const recovering = entry.blockers.some((b) =>
    [
      'change_unconfirmed',
      'configuration_recovery',
      'change_in_progress',
    ].includes(b.code),
  );
  const tryIt = () => {
    const prompt =
      entry.kind === 'skill'
        ? `${slashOf(entry.name)} `
        : `${app?.example_prompts[0] ?? `Use ${name} to `}`;
    workspace?.newChat?.(prompt);
  };
  const start = () => {
    if (action.kind === 'try') tryIt();
    else if (action.kind === 'delete_data') void control.review('remove');
    else void control.review('', action.label);
  };
  const plannable =
    !detail.plan || detail.plan.supported || detail.plan.plan_id;
  const primary =
    action.kind !== 'none' && plannable && !recovering && !control.plan ? (
      <Button
        variant="primary"
        aria-disabled={control.busy}
        onClick={() => !control.busy && start()}
      >
        {action.label}
      </Button>
    ) : null;
  const menu: MenuAction[] = [];
  if (about.actions.includes('turn_off'))
    menu.push({
      label: 'Turn off',
      onSelect: () => void control.review('turn_off'),
    });
  if (about.actions.includes('update'))
    menu.push({
      label: 'Check for updates',
      onSelect: () => void control.review('update'),
    });
  if (entry.installed && (entry.kind !== 'mcp' || !entry.parent_id))
    menu.push({
      label: 'Advanced settings',
      onSelect: () => {
        const path = idPath(kind, entry.id);
        navigate(`${path}${path.includes('?') ? '&' : '?'}edit=1`);
      },
    });
  if (about.actions.includes('remove'))
    menu.push({
      label: 'Remove…',
      danger: true,
      onSelect: () => void control.review('remove'),
    });
  const files = about.files;
  const scripts = files.filter((file) => file.executable).length;
  return (
    <article className="app-detail stack" aria-labelledby="app-detail-title">
      <Link className="settings-link app-back" to={back}>
        <ArrowLeft size={14} aria-hidden />{' '}
        {kind === 'skill' ? 'Skills' : 'Apps'}
      </Link>
      <header className="app-detail-header">
        <AppIcon icon={entry.icon} size={56} />
        <div className="app-detail-title">
          <h3 id="app-detail-title" ref={heading} tabIndex={-1}>
            {name}
          </h3>
          <span className="app-card-meta">
            <Publisher entry={entry} />
            {status && (
              <StatusDot tone={status[0]} label={status[1]} showLabel />
            )}
          </span>
        </div>
        <div className="app-detail-actions">
          {primary}
          {menu.length > 0 && (
            <Menu
              label={`More for ${name}`}
              actions={menu}
              iconOnly
              variant="ghost"
            >
              <MoreHorizontal size={18} aria-hidden />
            </Menu>
          )}
        </div>
      </header>
      {about.package && (
        <p className="settings-help">
          Installed as part of{' '}
          <Link to={idPath('app', entry.parent_id ?? '')}>{about.package}</Link>
          .
        </p>
      )}
      {recovering && !control.plan && (
        <div className="app-banner" role="status">
          <span>Finishing your last change…</span>
          <Button disabled={settling} onClick={() => void settle()}>
            Retry
          </Button>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
      {detail.plan && !detail.plan.supported && !detail.plan.plan_id && (
        <p className="settings-help" role="status">
          {detail.plan.unsupported_reason}
        </p>
      )}
      {control.error && <p role="alert">{control.error}</p>}
      {control.notice && (
        <p className="settings-help" role="status">
          {control.notice}
        </p>
      )}
      <PlanProgress control={control} name={name} />
      <SettingsGroup title="Overview">
        <div className="app-section">
          <p>{app?.summary || entry.description}</p>
          {entry.kind === 'skill' ? (
            <p>
              Use it in chat with <code>{slashOf(entry.name)}</code>
              {entry.installed ? '' : ' once added'}.
            </p>
          ) : app?.example_prompts.length ? (
            <ul className="app-prompts" aria-label="Things to ask">
              {app.example_prompts.slice(0, 3).map((prompt) => (
                <li key={prompt}>“{prompt}”</li>
              ))}
            </ul>
          ) : null}
          <StatusLine>
            {entry.kind === 'skill'
              ? 'Instructions stay on this computer.'
              : about.destination
                ? `What you ask goes to ${hostOf(about.destination)}.`
                : about.runs_locally
                  ? 'Runs on this computer.'
                  : 'Hosted by its publisher.'}
          </StatusLine>
        </div>
      </SettingsGroup>
      {about.access && (
        <SettingsGroup title="Access">
          <div className="app-section">
            <StatusLine
              action={
                <Button
                  className="settings-link"
                  onClick={() => setChanging(true)}
                >
                  Choose
                </Button>
              }
            >
              {about.access.tools.filter((tool) => tool.state !== 'off').length}{' '}
              of {about.access.tools.length} actions on ·{' '}
              {
                {
                  read_only: 'Read only',
                  ask: 'Ask before changes',
                  full: 'Full access',
                  custom: 'Custom',
                }[about.access.preset]
              }
            </StatusLine>
            {about.access.note && (
              <p className="settings-help">{about.access.note}</p>
            )}
            <ToolGroups tools={about.access.tools} />
          </div>
          <AccessSheet
            open={changing}
            name={name}
            access={about.access}
            change
            busy={control.busy}
            onCancel={() => setChanging(false)}
            onAllow={(choice) => {
              setChanging(false);
              void control.apply('access', choice);
            }}
          />
        </SettingsGroup>
      )}
      {entry.kind === 'skill' && entry.installed && (
        <SettingsGroup title="Settings">
          <div className="app-section">
            <p>
              {entry.lifecycle === 'off'
                ? 'Turned off: chats don’t use it.'
                : 'Available in every chat without a profile.'}{' '}
              {about.profiles.length
                ? `Also in these agent profiles: ${about.profiles.join(', ')}.`
                : 'No agent profile includes it yet.'}
            </p>
            {files.length > 0 && (
              <Disclosure
                summary="What's inside"
                meta={`${files.length} files${scripts ? ` · ${scripts} script${scripts > 1 ? 's' : ''}` : ''}`}
              >
                {scripts > 0 && (
                  <p className="settings-help">
                    Scripts never run when you add a skill. If Row-Bot runs one
                    later, your approval rules apply.
                  </p>
                )}
                <ul className="app-files">
                  {files.map((file) => (
                    <li key={file.path}>
                      <code>{file.path}</code>
                      {file.executable && (
                        <span className="app-chip">Script</span>
                      )}
                    </li>
                  ))}
                </ul>
              </Disclosure>
            )}
          </div>
        </SettingsGroup>
      )}
      {entry.kind !== 'skill' && entry.installed && (
        <SettingsGroup title="Settings">
          <dl className="app-facts-list">
            {entry.account_label && (
              <Fact label="Account">{entry.account_label}</Fact>
            )}
            {(about.signs_in || about.saved_key) && (
              <Fact label={about.signs_in ? 'Sign-in' : 'Key'}>
                {about.signed_in
                  ? 'Saved in your system keychain'
                  : 'Not added yet'}
              </Fact>
            )}
            {about.requirements.map((need) => (
              <Fact key={need.label} label={need.label}>
                {need.available ? 'Ready' : 'Needed'}
              </Fact>
            ))}
            <Fact label="Runs">
              {about.runs_locally
                ? 'On this computer'
                : 'Hosted by its publisher'}
            </Fact>
          </dl>
        </SettingsGroup>
      )}
      {(about.ways ?? []).length > 1 && (
        <SettingsGroup title="Ways to connect">
          <ul className="app-ways">
            {(about.ways ?? []).map((way) => (
              <li key={way.id}>
                {way.id === entry.id ? (
                  <span className="app-way" aria-current="true">
                    <WayText way={way} />
                    <span className="app-chip">This one</span>
                  </span>
                ) : (
                  <Link className="app-way" to={idPath('app', way.id)}>
                    <WayText way={way} />
                  </Link>
                )}
              </li>
            ))}
          </ul>
        </SettingsGroup>
      )}
      {entry.children.length > 0 && (
        <SettingsGroup title="Included">
          <ul className="app-grid">
            {entry.children.map((child) => (
              <ItemCard key={child.id} entry={child} />
            ))}
          </ul>
        </SettingsGroup>
      )}
      <Disclosure summary="Details" className="app-details">
        <dl className="app-facts-list">
          <Fact label="Source">
            {about.source_url ? (
              <a href={about.source_url} target="_blank" rel="noreferrer">
                {entry.source}
              </a>
            ) : (
              entry.source
            )}
          </Fact>
          {entry.version && <Fact label="Version">{entry.version}</Fact>}
          {about.license && <Fact label="Licence">{about.license}</Fact>}
          {entry.attributions.length > 1 && (
            <Fact label="Also listed in">
              {entry.attributions
                .slice(1)
                .map((a) => a.source)
                .join(', ')}
            </Fact>
          )}
          <Fact label="Identifier">
            <code>{about.identifier}</code>
            {about.pin && <code> @ {about.pin}</code>}
          </Fact>
        </dl>
      </Disclosure>
      <ConsentSheet
        control={control}
        name={name}
        cleanupOffered={
          (entry.kind === 'plugin' && entry.lifecycle !== 'data_retained') ||
          (entry.kind === 'mcp' && about.signed_in)
        }
      />
    </article>
  );
}
